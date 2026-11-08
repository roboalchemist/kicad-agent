"""kicad-cli wrappers: DRC, board-area SVG export, 3D render, stats (DESIGN.md, "kicad_cli.py").

kicad-cli writes a ``<board>.kicad_prl`` next to the board it opens; these wrappers delete it
again when it wasn't there before, so they leave no side files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import config as config_mod
from ..config import Config
from .client import EngineError

# Pinned so SVG colours don't depend on the user's PCB editor theme.
SVG_THEME = "_builtin_default"
# KiCad Default (the dark PCB theme) ``board.background``: rgb(0,16,35) in the built-in theme.
# The same value is the viewer canvas and the 2D render composite (see web/NOTES.md).
SVG_BACKGROUND = "#001023"
# kicad-cli plots drill holes as an opaque white group, which reads as "page" on a white sheet.
# On the dark canvas they must punch through to the background instead.
_HOLE_GROUP = re.compile(r'(<g style=")fill:#FFFFFF(; fill-opacity:1\.0000; stroke:none;">)')
# In that theme F.Mask is #D864FF and B.Mask #02FFEE, filled at 0.4 opacity. Oval and round pad
# openings are plotted as opaque strokes instead, so export_svg gives those strokes 0.4 too.
_MASK_STROKE = re.compile(r"(stroke:#(?:D864FF|02FFEE);\s*stroke-width:[0-9.]+;\s*stroke-opacity:)1\b")


def _config(config: Config | None) -> Config:
    return config or config_mod.get()


def _cli_path(config: Config) -> Path:
    cli = Path(config.kicad_cli)
    if not cli.exists():
        raise EngineError("KiCadUnavailable", f"kicad-cli not found at {cli} (set KICAD_CLI)")
    return cli


def _board_path(board: str | os.PathLike[str]) -> Path:
    path = Path(board).expanduser().resolve()
    if not path.is_file():
        raise EngineError("NotFound", f"Board file not found: {path}")
    return path


def run(args: Sequence[str], *, board: Path | None = None, config: Config | None = None,
        timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """Run ``kicad-cli <args>``; raise ``EngineError("KiCadCliError")`` on a non-zero exit.

    With ``board``, a ``.kicad_prl`` that kicad-cli creates beside it is removed afterwards.
    """
    cfg = _config(config)
    cmd = [str(_cli_path(cfg)), *[str(a) for a in args]]
    prl = board.with_suffix(".kicad_prl") if board is not None else None
    prl_existed = prl.exists() if prl is not None else True
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise EngineError("Timeout", f"kicad-cli {' '.join(args[:2])} took longer than {timeout:.0f}s") from exc
    finally:
        if prl is not None and not prl_existed:
            try:
                prl.unlink()
            except FileNotFoundError:
                pass
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-6:])
        raise EngineError("KiCadCliError", f"kicad-cli {' '.join(args[:2])} exited {proc.returncode}: {tail}")
    return proc


# ----------------------------------------------------------------------------------- DRC


def _drc_item(item: dict[str, Any]) -> dict[str, Any]:
    pos = item.get("pos") or {}
    return {"uuid": item.get("uuid"), "description": item.get("description", ""),
            "x": pos.get("x"), "y": pos.get("y")}


def _drc_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {"type": entry.get("type", ""), "severity": entry.get("severity", ""),
            "description": entry.get("description", ""),
            "items": [_drc_item(i) for i in entry.get("items", [])]}


def normalize_drc(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn kicad-cli's DRC JSON (``--units mm``) into the harness shape.

    ``violations`` holds DRC and schematic-parity violations, ``unconnected`` the unrouted
    connections. ``counts.errors``/``warnings`` count violations by severity (excluded ones count
    as neither); ``counts.unconnected`` counts unconnected items.
    """
    units = raw.get("coordinate_units", "mm")
    if units != "mm":
        raise EngineError("KiCadCliError", f"expected DRC coordinates in mm, got {units!r}")
    violations = [_drc_entry(v) for v in raw.get("violations", [])]
    violations += [_drc_entry(v) for v in raw.get("schematic_parity", [])]
    unconnected = [_drc_entry(v) for v in raw.get("unconnected_items", [])]
    return {
        "counts": {
            "errors": sum(1 for v in violations if v["severity"] == "error"),
            "warnings": sum(1 for v in violations if v["severity"] == "warning"),
            "unconnected": len(unconnected),
        },
        "violations": violations,
        "unconnected": unconnected,
    }


def drc(board: str | os.PathLike[str], *, config: Config | None = None, timeout: float = 300) -> dict[str, Any]:
    """Run ``kicad-cli pcb drc --format json --severity-all --units mm`` and normalize the report.

    Zones are checked as saved (no refill), so the report describes the file itself.
    """
    path = _board_path(board)
    with tempfile.TemporaryDirectory(prefix="harness-drc-") as tmp:
        out = Path(tmp) / "drc.json"
        run(["pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out), str(path)],
            board=path, config=config, timeout=timeout)
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngineError("KiCadCliError", f"kicad-cli wrote no readable DRC report: {exc}") from exc
    return normalize_drc(raw)


# ----------------------------------------------------------------------------------- ERC (schematic)


def _erc_item(item: dict[str, Any]) -> dict[str, Any]:
    """One ERC violation, reduced to the fields the agent and the UI need."""
    return {
        "type": str(item.get("type", "")),
        "severity": str(item.get("severity", "")),
        "description": str(item.get("description", "")),
        "items": [
            {
                "description": str(i.get("description", "")),
                "pos": [round(float(i["pos"]["x"]), 3), round(float(i["pos"]["y"]), 3)]
                if isinstance(i.get("pos"), dict) and "x" in i["pos"] and "y" in i["pos"]
                else None,
            }
            for i in item.get("items", [])
            if isinstance(i, dict)
        ],
    }


def normalize_erc(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn kicad-cli's ERC JSON into ``{counts, violations}``.

    ``counts.errors``/``warnings`` come from the sheets' violation lists, which is the only
    place kicad-cli records them.
    """
    violations: list[dict[str, Any]] = []
    for sheet in raw.get("sheets", []):
        for item in sheet.get("violations", []):
            violations.append(_erc_item(item))
    if not violations:
        violations = [_erc_item(v) for v in raw.get("violations", [])]
    return {
        "counts": {
            "errors": sum(1 for v in violations if v["severity"] == "error"),
            "warnings": sum(1 for v in violations if v["severity"] == "warning"),
        },
        "violations": violations,
    }


def erc(schematic: str | os.PathLike[str], *, config: Config | None = None,
        timeout: float = 300) -> dict[str, Any]:
    """Run ``kicad-cli sch erc --format json --severity-all`` and normalize the report."""
    path = _schematic_path(schematic)
    with tempfile.TemporaryDirectory(prefix="harness-erc-") as tmp:
        out = Path(tmp) / "erc.json"
        run(["sch", "erc", "--format", "json", "--units", "mm", "--severity-all", "-o", str(out), str(path)],
            config=config, timeout=timeout)
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngineError("KiCadCliError", f"kicad-cli wrote no readable ERC report: {exc}") from exc
    return normalize_erc(raw)


def export_sch_svg(schematic: str | os.PathLike[str], out_dir: str | os.PathLike[str], *,
                   config: Config | None = None, timeout: float = 300) -> list[Path]:
    """Plot every sheet of a schematic to SVG in ``out_dir``; returns the files, first sheet first."""
    path = _schematic_path(schematic)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    run(["sch", "export", "svg", "-o", str(target), str(path)], config=config, timeout=timeout)
    return sorted(target.glob("*.svg"))


def export_netlist(schematic: str | os.PathLike[str], out: str | os.PathLike[str], *,
                   config: Config | None = None, timeout: float = 300) -> Path:
    """Export KiCad's own netlist for ``schematic``; returns the file path.

    This is the authority on connectivity: it applies KiCad's rules for pins, wires, labels
    and power symbols exactly as eeschema does.
    """
    path = _schematic_path(schematic)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["sch", "export", "netlist", "-o", str(target), str(path)], config=config, timeout=timeout)
    return target


def parse_netlist(text: str) -> dict[str, Any]:
    """Read a KiCad netlist into ``{"components": [...], "nets": {name: [(ref, pin)]}}``.

    Components carry ``ref``, ``value`` and ``footprint``; nets map a name to its
    ``(reference, pin)`` nodes. Malformed input raises ``EngineError("KiCadCliError")``.
    """
    from . import schematic as schema_mod

    try:
        tree = schema_mod.parse(text)
    except Exception as exc:  # the netlist is KiCad's own format, but be defensive
        raise EngineError("KiCadCliError", f"unreadable netlist: {exc}") from exc

    find_all, find_one = schema_mod.find_all, schema_mod.find_one
    components: list[dict[str, Any]] = []
    comps = find_one(tree, "components")
    if comps is not None:
        for comp in find_all(comps, "comp"):
            ref = _text(find_one(comp, "ref"))
            if not ref:
                continue
            components.append(
                {
                    "ref": ref,
                    "value": _text(find_one(comp, "value")),
                    "footprint": _text(find_one(comp, "footprint")),
                }
            )

    nets: dict[str, list[tuple[str, str]]] = {}
    nets_node = find_one(tree, "nets")
    if nets_node is not None:
        for net in find_all(nets_node, "net"):
            name = _text(find_one(net, "name"))
            nodes = []
            for node in find_all(net, "node"):
                ref = _text(find_one(node, "ref"))
                pin = _text(find_one(node, "pin"))
                if ref and pin:
                    nodes.append((ref, pin))
            if name:
                nets[name] = nodes
    return {"components": components, "nets": nets}


def _schematic_path(schematic: str | os.PathLike[str]) -> Path:
    path = Path(schematic).expanduser().resolve()
    if not path.is_file():
        raise EngineError("NotFound", f"Schematic file not found: {path}")
    return path


def _text(node: list | None) -> str:
    """The scalar payload of ``(name "value")``, or ``""``."""
    if not node or len(node) < 2:
        return ""
    value = node[1]
    return "" if isinstance(value, list) else str(value).strip()


# ----------------------------------------------------------------------------------- SVG


# What the interactive 3D viewer needs: board body, copper, parts outlines and silkscreen.
GLB_FLAGS = ("--include-tracks", "--include-pads", "--include-zones", "--include-silkscreen",
             "--include-soldermask",
             # Vias are copper + a real hole: cut the hole in the board body so the model shows it
             # at the via's actual position/diameter (do NOT --fill-all-vias).
             "--cut-vias-in-body")
GLB_MEDIA_TYPE = "model/gltf-binary"


def export_glb(board: str | os.PathLike[str], out: str | os.PathLike[str], *, config: Config | None = None,
               timeout: float = 300) -> Path:
    """Export the board as a binary glTF (GLB) for the interactive 3D viewer.

    Includes tracks, pads, zones, silkscreen and soldermask so the model looks like the board
    rather than a bare substrate. Units are millimetres, Y is up (KiCad's exporter).
    """
    path = _board_path(board)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "export", "glb", *GLB_FLAGS, "-o", str(out_path)]
    run([*args, str(path)], board=path, config=config, timeout=timeout)
    if not out_path.is_file() or out_path.stat().st_size < 1024:
        raise EngineError("KiCadCliError", f"kicad-cli wrote no usable GLB to {out_path}")
    return out_path


def export_svg(board: str | os.PathLike[str], layers: Sequence[str] | str, out: str | os.PathLike[str],
               mirror: bool = False, *, config: Config | None = None, timeout: float = 120) -> Path:
    """Plot ``layers`` (in the given order, later layers on top) to one board-area SVG.

    Units are mm. The SVG origin is the top-left of the board outline geometry (the index's
    ``edge_bbox``, which excludes the Edge.Cuts stroke), so ``svg = (x - ex, y - ey)``. With
    ``mirror``, ``svg_x = page_w - (x - ex)`` where ``page_w`` is the viewBox width (KiCad rounds
    the page size down to whole mils, so it can be a few µm smaller than the outline).
    Colours come from KiCad's built-in theme; solder-mask strokes are made as translucent as
    mask fills.
    """
    path = _board_path(board)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    layer_arg = layers if isinstance(layers, str) else ",".join(layers)
    args = ["pcb", "export", "svg", "--layers", layer_arg, "--page-size-mode", "2", "--exclude-drawing-sheet",
            "--mode-single", "--theme", SVG_THEME, "-o", str(out_path)]
    if mirror:
        args.append("--mirror")
    run([*args, str(path)], board=path, config=config, timeout=timeout)
    if not out_path.is_file():
        raise EngineError("KiCadCliError", f"kicad-cli wrote no SVG to {out_path}")
    text = out_path.read_text(encoding="utf-8")
    fixed = _MASK_STROKE.sub(r"\g<1>0.4", text)
    fixed = _HOLE_GROUP.sub(rf"\g<1>fill:{SVG_BACKGROUND}\g<2>", fixed)
    if fixed != text:
        out_path.write_text(fixed, encoding="utf-8")
    return out_path


# ----------------------------------------------------------------------------------- 3D render


def _triple(value: Iterable[float], name: str) -> str:
    vals = [float(v) for v in value]
    if len(vals) != 3:
        raise EngineError("InvalidParams", f"{name} must have 3 values, got {vals}")
    return ",".join(f"{v:.4f}" for v in vals)


def render_3d(board: str | os.PathLike[str], out: str | os.PathLike[str], side: str = "top",
              rotate: Iterable[float] | None = None, zoom: float = 1, pivot: Iterable[float] | None = None,
              width: int = 1600, height: int = 900, quality: str = "basic", *, background: str | None = None,
              config: Config | None = None, timeout: float = 300) -> Path:
    """Render the board with ``kicad-cli pcb render`` (orthographic) to a PNG.

    ``pivot`` is in cm relative to the board centre with +Y up; ``rotate`` is X,Y,Z degrees.
    kicad-cli makes the image a little smaller than ``width`` x ``height`` (8-pixel raytracer
    blocks); ``render.render_3d_view`` compensates for that. ``--pan`` is never used.
    """
    path = _board_path(board)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["pcb", "render", "-o", str(out_path), "-w", str(int(width)), "-h", str(int(height)),
            "--side", side, "--quality", quality, "--zoom", f"{float(zoom):.6f}"]
    if pivot is not None:
        args += ["--pivot", _triple(pivot, "pivot")]
    if rotate is not None:
        args += ["--rotate", _triple(rotate, "rotate")]
    if background:
        args += ["--background", background]
    run([*args, str(path)], board=path, config=config, timeout=timeout)
    if not out_path.is_file():
        raise EngineError("KiCadCliError", f"kicad-cli wrote no render to {out_path}")
    return out_path


# ----------------------------------------------------------------------------------- stats

_QUANTITY = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(mm²|mm\^2|mm|)\s*$")


def _numbers(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    if isinstance(value, str):
        m = _QUANTITY.match(value)
        if m:
            return float(m.group(1))
    return value


def stats(board: str | os.PathLike[str], *, config: Config | None = None, timeout: float = 120) -> dict[str, Any]:
    """Board statistics from ``kicad-cli pcb export stats --format json --units mm``.

    Same structure as kicad-cli's JSON, except quantities such as ``"25.0000 mm"`` or
    ``"500.00 mm²"`` become floats (mm, mm²).
    """
    path = _board_path(board)
    with tempfile.TemporaryDirectory(prefix="harness-stats-") as tmp:
        out = Path(tmp) / "stats.json"
        run(["pcb", "export", "stats", "--format", "json", "--units", "mm", "-o", str(out), str(path)],
            board=path, config=config, timeout=timeout)
        try:
            raw = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngineError("KiCadCliError", f"kicad-cli wrote no readable stats: {exc}") from exc
    return _numbers(raw)

def export_gerbers(board: str | os.PathLike[str], out_dir: str | os.PathLike[str],
                   layers: Sequence[str] | None = None, *, config: Config | None = None,
                   timeout: float = 300) -> list[Path]:
    """Production Gerbers (copper, mask, silk, outline) into ``out_dir``."""
    path = _board_path(board)
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    chosen = list(layers or ["F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.Silkscreen",
                             "B.Silkscreen", "Edge.Cuts"])
    run(["pcb", "export", "gerbers", "--layers", ",".join(chosen), "-o", str(out), str(path)],
        board=path, config=config, timeout=timeout)
    return sorted(p for p in out.iterdir() if p.is_file())


def export_drill(board: str | os.PathLike[str], out_dir: str | os.PathLike[str], *,
                 config: Config | None = None, timeout: float = 300) -> list[Path]:
    """Excellon drill files into ``out_dir``."""
    path = _board_path(board)
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "drill", "-o", str(out), str(path)],
        board=path, config=config, timeout=timeout)
    return sorted(p for p in out.iterdir() if p.is_file())


def export_pos(board: str | os.PathLike[str], out: str | os.PathLike[str], *,
               config: Config | None = None, timeout: float = 300) -> Path:
    """Pick-and-place CSV (mm) at ``out``."""
    path = _board_path(board)
    target = Path(out).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["pcb", "export", "pos", "-o", str(target), "--format", "csv", "--units", "mm", str(path)],
        board=path, config=config, timeout=timeout)
    if not target.is_file():
        raise EngineError("KiCadCliError", f"kicad-cli wrote no position file to {target}")
    return target

