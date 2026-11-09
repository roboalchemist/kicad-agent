"""``Engine``: spawns ``worker.py`` under KiCad's Python and talks JSON lines to it.

See DESIGN.md, "Engine". One worker process is kept alive and restarted when it dies; calls are
serialized with a lock, so an ``Engine`` can be shared between threads.
"""

from __future__ import annotations

import atexit
import collections
import json
import logging
import os
import queue
import subprocess
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import config as config_mod
from ..config import Config

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("worker.py")
DEFAULT_TIMEOUT_S = 300.0

_ENGINES: "weakref.WeakSet[Engine]" = weakref.WeakSet()


@atexit.register
def _close_all() -> None:
    for engine in list(_ENGINES):
        engine.close()


class EngineError(Exception):
    """An engine failure with a machine-readable ``type`` (e.g. ``NotFound``) and a message."""

    def __init__(self, type: str, message: str = "") -> None:
        super().__init__(f"{type}: {message}" if message else type)
        self.type = type
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """JSON-friendly form, as the worker reports errors."""
        return {"type": self.type, "message": self.message}


def _board_arg(board: str | os.PathLike[str]) -> str:
    return str(Path(board).expanduser().resolve())


class Engine:
    """Client for the pcbnew worker. ``engine.call(method, board=path, **params)`` returns the
    worker's result or raises ``EngineError``; typed wrappers cover every worker method."""

    def __init__(self, config: Config | None = None, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.config = config or config_mod.get()
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._replies: queue.Queue[bytes | None] | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=200)
        self._next_id = 0
        self.restarts = 0
        _ENGINES.add(self)

    # ------------------------------------------------------------------ process management

    def _spawn(self) -> None:
        python = Path(self.config.kicad_python)
        if not python.exists():
            raise EngineError("KiCadUnavailable", f"KiCad's Python not found at {python} (set KICAD_PYTHON)")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
        env["PYTHONUNBUFFERED"] = "1"
        self._stderr_tail.clear()
        self._proc = subprocess.Popen(
            [str(python), "-s", "-u", str(WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(WORKER.parent),
            bufsize=0,
        )
        self._replies = queue.Queue()
        threading.Thread(target=self._read_stdout, args=(self._proc, self._replies), daemon=True,
                         name="engine-stdout").start()
        threading.Thread(target=self._read_stderr, args=(self._proc,), daemon=True, name="engine-stderr").start()
        log.debug("engine worker started (pid %s)", self._proc.pid)

    @staticmethod
    def _read_stdout(proc: subprocess.Popen[bytes], replies: queue.Queue[bytes | None]) -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, b""):
            replies.put(line)
        replies.put(None)

    def _read_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._stderr_tail.append(line)
                log.debug("worker: %s", line)

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def close(self) -> None:
        """Stop the worker process (a later call starts a new one)."""
        with self._lock:
            self._kill()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def pid(self) -> int | None:
        """PID of the running worker, if any."""
        return self._proc.pid if self._alive() else None

    def stderr_tail(self, n: int = 20) -> list[str]:
        """The worker's last ``n`` stderr lines (pcbnew warnings, tracebacks)."""
        return list(self._stderr_tail)[-n:]

    # ------------------------------------------------------------------ calls

    def call(self, method: str, board: str | os.PathLike[str] | None = None, *,
             timeout: float | None = None, **params: Any) -> Any:
        """Run ``method`` in the worker and return its result.

        Raises ``EngineError`` with the worker's error type, ``WorkerDied`` if the process exits
        mid-call (it is restarted on the next call), or ``Timeout``.
        """
        if board is not None:
            params["board"] = _board_arg(board)
        with self._lock:
            if not self._alive():
                if self._proc is not None:
                    self.restarts += 1
                    log.warning("engine worker died; restarting")
                self._kill()
                self._spawn()
            assert self._proc is not None and self._proc.stdin is not None and self._replies is not None
            self._next_id += 1
            rid = self._next_id
            line = json.dumps({"id": rid, "method": method, "params": params}, separators=(",", ":")) + "\n"
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._kill()
                raise EngineError("WorkerDied", f"could not send {method}: {exc}. {self._tail()}") from exc
            deadline = time.monotonic() + (timeout or self.timeout_s)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill()
                    raise EngineError("Timeout", f"{method} took longer than {timeout or self.timeout_s:.0f}s")
                try:
                    raw = self._replies.get(timeout=remaining)
                except queue.Empty:
                    continue
                if raw is None:
                    code = self._proc.wait(timeout=5) if self._proc else None
                    self._kill()
                    raise EngineError("WorkerDied", f"worker exited (code {code}) during {method}. {self._tail()}")
                try:
                    reply = json.loads(raw)
                except ValueError:
                    log.warning("engine worker wrote a non-JSON line: %r", raw[:200])
                    continue
                if reply.get("id") != rid:
                    log.warning("engine reply id %r != %r; skipping", reply.get("id"), rid)
                    continue
                if "error" in reply:
                    err = reply["error"] or {}
                    raise EngineError(str(err.get("type", "EngineError")), str(err.get("message", "")))
                return reply.get("result")

    def _tail(self) -> str:
        tail = self.stderr_tail(8)
        return ("stderr: " + " | ".join(tail)) if tail else ""

    # ------------------------------------------------------------------ typed wrappers

    def ping(self) -> dict:
        """``{"pcbnew": version}``; starts the worker if needed."""
        return self.call("ping")

    def index(self, board: str | os.PathLike[str]) -> dict:
        """The pick index of ``board`` (DESIGN.md, "Pick index")."""
        return self.call("index", board)

    def normalize(self, board: str | os.PathLike[str]) -> dict:
        """Replace duplicated UUIDs and save in KiCad 10 format. ``{"replaced": n}``."""
        return self.call("normalize", board)

    def move_footprint(self, board: str | os.PathLike[str], ref: str, x: float, y: float,
                       rotation: float | None = None) -> dict:
        """Move ``ref``'s anchor to (x, y), optionally setting its absolute rotation."""
        return self.call("move_footprint", board, ref=ref, x=x, y=y, rotation=rotation)

    def rotate_footprint(self, board: str | os.PathLike[str], ref: str, angle: float) -> dict:
        """Rotate ``ref`` by ``angle`` degrees about its anchor (CCW positive)."""
        return self.call("rotate_footprint", board, ref=ref, angle=angle)

    def flip_footprint(self, board: str | os.PathLike[str], ref: str) -> dict:
        """Move ``ref`` to the other side of the board, in place."""
        return self.call("flip_footprint", board, ref=ref)

    def move_reference_text(self, board: str | os.PathLike[str], ref: str, x: float, y: float) -> dict:
        """Move ``ref``'s reference designator text to (x, y) in board coordinates."""
        return self.call("move_reference_text", board, ref=ref, x=x, y=y)

    def set_track_width(self, board: str | os.PathLike[str], nets: Sequence[str],
                        width: float) -> dict:
        return self.call("set_track_width", board, nets=list(nets), width=float(width))

    def delete_tracks(self, board: str | os.PathLike[str], nets: Sequence[str] | str | None = None,
                      uuids: Sequence[str] | None = None) -> dict:
        """Delete tracks/vias on ``nets`` and/or by ``uuids``; with neither, all of them."""
        return self.call("delete_tracks", board, nets=_listify(nets), uuids=_listify(uuids))

    def delete_items(self, board: str | os.PathLike[str], uuids: Iterable[str]) -> dict:
        """Delete any board items by UUID."""
        return self.call("delete_items", board, uuids=list(uuids))

    def move_reference_texts(self, board: str | os.PathLike[str],
                             moves: Sequence[dict[str, Any]]) -> dict:
        return self.call("move_reference_texts", board, moves=[dict(item) for item in moves])

    def add_track(self, board: str | os.PathLike[str], net: str, points: Sequence[Sequence[float]],
                  width: float = 0.25, layer: str = "F.Cu") -> dict:
        """Add one segment per consecutive pair of ``points``. ``{"uuids": [...]}``."""
        return self.call("add_track", board, net=net, points=[list(p) for p in points], width=width, layer=layer)

    def add_via(self, board: str | os.PathLike[str], x: float, y: float, net: str,
                size: float = 0.6, drill: float = 0.3, via_type: str = "through",
                layers: Sequence[str] | None = None) -> dict:
        """Add a via (through/blind/buried/micro). ``{"uuid"}``."""
        return self.call("add_via", board, x=x, y=y, net=net, size=size, drill=drill,
                         via_type=via_type, layers=list(layers) if layers else None)

    def set_outline(self, board: str | os.PathLike[str], width: float, height: float, x: float = 0,
                    y: float = 0, corner_radius: float = 0) -> dict:
        """Replace the Edge.Cuts shapes with a (rounded) rectangle. ``{"bbox": [x, y, w, h]}``."""
        return self.call("set_outline", board, width=width, height=height, x=x, y=y, corner_radius=corner_radius)

    def add_cutout(self, board: str | os.PathLike[str], x: float, y: float,
                   diameter: float) -> dict:
        return self.call("add_cutout", board, x=x, y=y, diameter=diameter)

    def add_keepout(self, board: str | os.PathLike[str], polygon: Sequence[Sequence[float]],
                    layers: Sequence[str] | None = None, no_tracks: bool = True,
                    no_vias: bool = True, no_pours: bool = True,
                    no_pads: bool = False) -> dict:
        return self.call("add_keepout", board, polygon=[list(p) for p in polygon],
                         layers=list(layers) if layers else None, no_tracks=no_tracks,
                         no_vias=no_vias, no_pours=no_pours, no_pads=no_pads)

    def set_copper_layers(self, board: str | os.PathLike[str], layers: int) -> dict:
        return self.call("set_copper_layers", board, layers=int(layers))

    def set_outline_polygon(self, board: str | os.PathLike[str],
                            points: Sequence[Sequence[float]]) -> dict:
        return self.call("set_outline_polygon", board, points=[list(p) for p in points])

    def add_zone(self, board: str | os.PathLike[str], net: str, layer: str,
                 polygon: Sequence[Sequence[float]] | None = None, clearance: float = 0.3,
                 min_width: float = 0.25, margin: float = 0.5,
                 thermal: bool = True) -> dict:
        """Add and fill a copper zone (default outline: board outline inset by ``margin``)."""
        return self.call("add_zone", board, net=net, layer=layer,
                         polygon=None if polygon is None else [list(p) for p in polygon],
                         clearance=clearance, min_width=min_width, margin=margin,
                         thermal=thermal)

    def refill_zones(self, board: str | os.PathLike[str]) -> dict:
        """Refill all zones. ``{"zones": n}``."""
        return self.call("refill_zones", board)

    def create_board(self, board: str | os.PathLike[str], width: float = 50, height: float = 50) -> dict:
        """Start an empty two-layer board with a rectangular outline. Raises if one exists."""
        return self.call("create_board", board, width=width, height=height)

    def add_footprint(self, board: str | os.PathLike[str], footprint: str, ref: str, value: str, x: float,
                      y: float, rotation: float = 0, side: str = "front",
                      footprint_dirs: Sequence[str | os.PathLike[str]] | None = None) -> dict:
        """Place ``footprint`` (``Lib:Name`` or bare name) searched in ``footprint_dirs``
        (default: ``config.footprint_dirs``). ``{"uuid", "ref", "pads": [...]}``."""
        dirs = footprint_dirs if footprint_dirs is not None else (self.config.footprint_dirs or [])
        return self.call("add_footprint", board, footprint=footprint, ref=ref, value=value, x=x, y=y,
                         rotation=rotation, side=side, footprint_dirs=[str(d) for d in dirs])

    def remove_footprint(self, board: str | os.PathLike[str], ref: str) -> dict:
        """Delete footprint ``ref``. ``{"ref", "uuid"}``."""
        return self.call("remove_footprint", board, ref=ref)

    def set_pad_net(self, board: str | os.PathLike[str], ref: str, pad: str | int, net: str) -> dict:
        """Put pad ``ref.pad`` on ``net`` (created if new)."""
        return self.call("set_pad_net", board, ref=ref, pad=str(pad), net=net)

    def export_dsn(self, board: str | os.PathLike[str], path: str | os.PathLike[str]) -> dict:
        """Export a Specctra DSN to ``path``."""
        return self.call("export_dsn", board, path=str(Path(path).expanduser().resolve()))

    def import_ses(self, board: str | os.PathLike[str], path: str | os.PathLike[str]) -> dict:
        """Import a Specctra session and save. ``{"tracks": n, "vias": n}``."""
        return self.call("import_ses", board, path=str(Path(path).expanduser().resolve()))


def _listify(value: Sequence[str] | str | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)
