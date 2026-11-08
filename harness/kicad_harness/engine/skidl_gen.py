"""Generate a schematic from a netlist description with skidl. See DESIGN.md, "Schematic".

Two engines can produce ``schematic.kicad_sch``:

- ``engine/schematic.py`` — the agent places symbols and wires them itself. Precise, and the
  file is exactly what the agent drew.
- this module — the agent (or a user) describes parts and nets, and skidl lays the circuit out
  and writes the file. Faster for a whole board idea, and it does the plumbing.

Both end at the same place: a ``.kicad_sch`` that ``kicad-cli`` renders and ERCs
(``engine/kicad_cli.py``).

skidl imports KiCad's libraries from ``KICAD<n>_SYMBOL_DIR``/``KICAD<n>_FOOTPRINT_DIR`` at
import time, so those are set by ``_env()`` before the package is imported. skidl also always
writes ``schematic.kicad_sch`` into the current directory and drops ``skidl.log``,
``skidl.erc`` and ``schematic-erc.rpt`` beside it, so generation runs in a scratch directory
and the single file we want is moved into place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import EngineError

SCHEMATIC_NAME = "schematic.kicad_sch"


@dataclass
class PartSpec:
    """One component: a KiCad symbol and the footprint the board phase should place."""

    ref: str
    library: str
    symbol: str
    value: str = ""
    footprint: str = ""
    lcsc: str = ""          # supplier part number, recorded on the symbol for the BOM
    description: str = ""


@dataclass
class NetSpec:
    """A net and the pins on it, as ``("R1", "1")`` pairs."""

    name: str
    pins: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class GenerationResult:
    """What a generation produced: the schematic path, and what skidl complained about."""

    path: Path
    log: str = ""
    warnings: list[str] = field(default_factory=list)


def available() -> bool:
    """Whether skidl can be imported with the current interpreter."""
    try:
        import skidl  # noqa: F401
    except Exception:  # pragma: no cover - depends on the install
        return False
    return True


def validate(spec: dict[str, Any]) -> tuple[list[PartSpec], list[NetSpec]]:
    """Check a spec from the agent and return ``(parts, nets)``.

    The spec is ``{"parts": [...], "nets": [...]}``; see ``PartSpec``/``NetSpec``. Raises
    ``ValueError`` naming the first problem, because the agent reads these messages.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object with 'parts' and 'nets'")
    raw_parts = spec.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("spec needs a non-empty 'parts' list")
    parts: list[PartSpec] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_parts):
        if not isinstance(raw, dict):
            raise ValueError(f"parts[{i}] must be an object")
        ref = str(raw.get("ref", "")).strip()
        library = str(raw.get("library", "")).strip()
        symbol = str(raw.get("symbol", "")).strip()
        if not ref or not library or not symbol:
            raise ValueError(f"parts[{i}] needs ref, library and symbol")
        if ref in seen:
            raise ValueError(f"duplicate reference {ref!r}")
        seen.add(ref)
        parts.append(
            PartSpec(
                ref=ref,
                library=library,
                symbol=symbol,
                value=str(raw.get("value", "") or ""),
                footprint=str(raw.get("footprint", "") or ""),
                lcsc=str(raw.get("lcsc", "") or ""),
                description=str(raw.get("description", "") or ""),
            )
        )

    raw_nets = spec.get("nets", [])
    if not isinstance(raw_nets, list):
        raise ValueError("'nets' must be a list")
    known = {p.ref for p in parts}
    nets: list[NetSpec] = []
    for i, raw in enumerate(raw_nets):
        if not isinstance(raw, dict):
            raise ValueError(f"nets[{i}] must be an object")
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"nets[{i}] needs a name")
        pins: list[tuple[str, str]] = []
        for pin in raw.get("pins", []):
            text = str(pin)
            if "." not in text:
                raise ValueError(f"nets[{i}].pins: {text!r} should be 'REF.PIN', e.g. 'R1.2'")
            ref, number = text.rsplit(".", 1)
            if ref not in known:
                raise ValueError(f"net {name!r} refers to unknown part {ref!r}")
            pins.append((ref, number))
        nets.append(NetSpec(name=name, pins=pins))
    return parts, nets


def generate(
    parts: list[PartSpec],
    nets: list[NetSpec],
    out: str | Path,
    *,
    symbol_dirs: list[Path] | None = None,
    footprint_dirs: list[Path] | None = None,
    timeout: float = 180,
) -> GenerationResult:
    """Write a schematic for ``parts``/``nets`` to ``out`` using skidl.

    Runs in a subprocess so skidl's import-time environment and global state can't leak into
    the harness process. Raises ``EngineError("SchematicGeneration")`` when skidl fails.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which(sys.executable) is None:
        raise EngineError("SchematicGeneration", f"no python interpreter at {sys.executable}")

    payload = {
        "parts": [p.__dict__ for p in parts],
        "nets": [n.__dict__ for n in nets],
    }
    env = _env(symbol_dirs, footprint_dirs)
    # skidl's auto-router fails intermittently ("RoutingFailure") on netlists it routes fine on
    # the next try, so a long goal loop must not treat one failure as permanent.
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            result = _run_once(payload, out, env, timeout)
        except EngineError as exc:
            last_error = exc
            if "RoutingFailure" not in str(exc) or attempt == 7:
                raise
            # Its placement/routing pass is stochastic: a short pause then a fresh attempt
            # usually succeeds, and a long goal loop must not surface that as a hard failure.
            time.sleep(0.4 * (attempt + 1))
            continue
        # The router can also *succeed* while drawing a wire that shorts two nets (seen as two
        # PWR_FLAGs on one net). Verify the netlist really matches before accepting the sheet.
        if _netlist_matches(out, nets):
            return result
        last_error = EngineError(
            "SchematicGeneration",
            "skidl drew a schematic whose nets do not match the requested connections "
            f"(attempt {attempt + 1}/8); retrying",
        )
        time.sleep(0.3 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _netlist_matches(out: Path, nets: list[NetSpec]) -> bool:
    """Whether every requested net is intact and no two requested nets were merged."""
    try:
        from .schematic import Schematic

        doc = Schematic.load(out)
        by_pin: dict[tuple[str, str], str] = {}
        for name, members in doc.nets().items():
            for member in members:
                by_pin[(member["ref"], str(member["pin"]))] = name
        groups: list[str] = []
        for net in nets:
            names = {by_pin.get((ref, str(number))) for ref, number in net.pins}
            if not names or None in names or len(names) != 1:
                return False
            groups.append(next(iter(names)))
        if len(set(groups)) != len(groups):
            return False
        # Each net's PWR_FLAG must sit on that net. skidl sometimes wires a later flag to an
        # earlier net, which leaves the component netlist intact but ERC with two power outputs
        # on one net (and the other undriven).
        from .layout import _alias_nets, _power_nets

        power = _power_nets(doc, _alias_nets(doc))
        flags = sorted((p for p in doc.placements() if p.reference.startswith("#FLG")),
                       key=lambda p: p.reference)
        if len(flags) != len(nets):
            return False
        for flag in flags:
            try:
                index = int(flag.reference[4:]) - 1
            except ValueError:
                return False
            if index < 0 or index >= len(nets):
                return False
            net = doc.pin_net(flag, "1") or power.get((flag.reference, "1"))
            if net is None or str(net).lstrip("/") != nets[index].name:
                return False
        return True
    except Exception:
        return False


def _run_once(payload: dict[str, Any], out: Path, env: dict[str, str], timeout: float) -> GenerationResult:
    with tempfile.TemporaryDirectory(prefix="harness-skidl-") as tmp:
        tmpdir = Path(tmp)
        script = tmpdir / "generate.py"
        script.write_text(_SCRIPT, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(tmpdir),
            env=env,
            input=_json(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        produced = tmpdir / SCHEMATIC_NAME
        log_path = tmpdir / "skidl.log"
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        if proc.returncode != 0 or not produced.is_file():
            raise EngineError("SchematicGeneration", _explain(proc, log))
        tmp_out = out.with_suffix(out.suffix + ".tmp")
        shutil.copyfile(produced, tmp_out)
        tmp_out.replace(out)
    warnings = [line for line in log.splitlines() if "WARNING" in line or "ERROR" in line]
    return GenerationResult(path=out, log=log, warnings=warnings)


def _explain(proc: subprocess.CompletedProcess[str], log: str) -> str:
    """A short, actionable reason skidl failed.

    The agent reads this, so lead with the actual complaint (a missing part, a routing
    failure) rather than the last frames of a traceback.
    """
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}\n{log}"
    for line in output.splitlines():
        text = line.strip()
        if any(text.startswith(k) for k in ("ValueError:", "KeyError:", "FileNotFoundError:", "RoutingFailure")):
            return f"skidl could not generate the schematic: {text[:400]}"
        if text.startswith("ERROR:"):
            return f"skidl could not generate the schematic: {text[:400]}"
    tail = [line.strip() for line in output.splitlines() if line.strip()][-4:]
    return "skidl could not generate the schematic: " + (" | ".join(tail) or "no output")


def suggest_symbols(library: str, name: str, limit: int = 12) -> list[str]:
    """Symbols in ``library`` whose name is close to ``name``, for a useful error message."""
    import difflib

    from .schematic import read_library

    for directory in _default_symbol_dirs():
        path = Path(directory) / f"{library}.kicad_sym"
        if path.is_file():
            try:
                names = list(read_library(path))
            except Exception:
                return []
            return difflib.get_close_matches(name, names, n=limit, cutoff=0.5) or sorted(names)[:limit]
    return []


def _env(symbol_dirs: list[Path] | None, footprint_dirs: list[Path] | None) -> dict[str, str]:
    env = dict(os.environ)
    symbols = [p for p in (symbol_dirs or _default_symbol_dirs()) if Path(p).is_dir()]
    footprints = [p for p in (footprint_dirs or []) if Path(p).is_dir()]
    for version in (6, 7, 8, 9, 10):
        if symbols:
            env[f"KICAD{version}_SYMBOL_DIR"] = str(symbols[0])
        if footprints:
            env[f"KICAD{version}_FOOTPRINT_DIR"] = str(footprints[0])
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _default_symbol_dirs() -> list[Path]:
    """Where KiCad keeps its symbol library, for the usual Linux and macOS installs."""
    return [
        Path("/usr/share/kicad/symbols"),
        Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
    ]


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload)


# Runs as its own process; reads a JSON spec on stdin and writes schematic.kicad_sch in cwd.
# Power nets (and any net that is only ever a power-input) need a PWR_FLAG, otherwise ERC
# reports "power pin not driven" — the same thing eeschema asks for when you draw a supply.
_SCRIPT = r'''
import json
import sys

spec = json.load(sys.stdin)
from skidl import KICAD10, Net, Part, generate_schematic  # noqa: E402

parts = {}
for item in spec["parts"]:
    kwargs = {}
    if item.get("value"):
        kwargs["value"] = item["value"]
    if item.get("footprint"):
        kwargs["footprint"] = item["footprint"]
    parts[item["ref"]] = Part(item["library"], item["symbol"], **kwargs)

nets = {}
for item in spec["nets"]:
    net = Net(item["name"])
    for ref, pin in item["pins"]:
        part = parts[ref]
        net += part[int(pin)] if pin.isdigit() else part[pin]
    nets[item["name"]] = net

for index, (name, net) in enumerate(list(nets.items()), start=1):
    if not net.get_pins():
        continue
    flag = Part("power", "PWR_FLAG", value="PWR_FLAG")
    # Deterministic references: hash() is randomised per process, and the resulting flag
    # placement order occasionally made skidl draw a wire that shorted two nets.
    flag.ref = "#FLG%d" % index
    flag[1] += net

generate_schematic(tool=KICAD10)
sys.exit(0)
'''
