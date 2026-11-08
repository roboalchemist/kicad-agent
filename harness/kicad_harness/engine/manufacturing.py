"""Manufacturing package: the files a fab and an assembler actually need.

``check`` says whether the board is correct; this says whether it can be ordered. The package is
written to ``<workspace>/manufacturing`` and contains Gerbers for the production layers, an
Excellon drill file, a pick-and-place CSV and a BOM CSV built from the schematic's own part
numbers, plus a manifest with the board hash so a package can be matched to a revision.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from . import kicad_cli
from .schematic import Schematic

GERBER_LAYERS = [
    "F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.Silkscreen", "B.Silkscreen", "Edge.Cuts",
]


def _alternates(ws) -> dict[str, list[str]]:
    path = Path(ws.path) / "bom_alternates.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


def _bom_rows(schematic_path: Path, alternates: dict[str, list[str]] | None = None) -> list[dict[str, str]]:
    alternates = alternates or {}
    doc = Schematic.load(schematic_path)
    groups: dict[tuple[str, str, str], list[str]] = {}
    for placement in doc.placements():
        if placement.reference.startswith("#"):
            continue
        lcsc = (placement.properties or {}).get("LCSC", "")
        key = (placement.value, placement.footprint, lcsc)
        groups.setdefault(key, []).append(placement.reference)
    rows = []
    for (value, footprint, lcsc), refs in sorted(groups.items(), key=lambda item: item[0]):
        alts = sorted({alt for ref in refs for alt in alternates.get(ref, [])})
        rows.append({
            "value": value,
            "footprint": footprint,
            "lcsc": lcsc,
            "qty": str(len(refs)),
            "refs": " ".join(sorted(refs)),
            "alternates": " ".join(alts),
        })
    return rows


def export(ws, config=None) -> dict[str, Any]:
    """Build the manufacturing package for the working board; returns its manifest."""
    out = Path(ws.path) / "manufacturing"
    out.mkdir(parents=True, exist_ok=True)
    board = ws.board_path
    kicad_cli.export_gerbers(board, out, layers=GERBER_LAYERS, config=config)
    kicad_cli.export_drill(board, out, config=config)
    kicad_cli.export_pos(board, out / "positions.csv", config=config)

    if ws.schematic_path.is_file():
        rows = _bom_rows(ws.schematic_path, _alternates(ws))
        with (out / "bom.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["value", "footprint", "lcsc", "qty", "refs",
                                                        "alternates"])
            writer.writeheader()
            writer.writerows(rows)

    files = sorted(path.name for path in out.iterdir() if path.is_file())
    manifest = {
        "generated_at": int(time.time()),
        "board_hash": _board_hash(board),
        "gerber_layers": GERBER_LAYERS,
        "files": files,
        "directory": str(out),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["files"] = sorted(path.name for path in out.iterdir() if path.is_file())
    return manifest


def status(ws) -> dict[str, Any]:
    """Whether a package exists and whether it is older than the board."""
    out = Path(ws.path) / "manufacturing"
    if not out.is_dir():
        return {"present": False, "files": [], "stale": True}
    files = sorted(path.name for path in out.iterdir() if path.is_file())
    present = bool(files)
    stale = False
    if present and ws.board_path.is_file():
        newest = max((out / name).stat().st_mtime for name in files)
        stale = ws.board_path.stat().st_mtime > newest + 1e-6
    return {"present": present, "files": files, "stale": stale}


def _board_hash(board: Path) -> str:
    import hashlib

    return hashlib.sha256(board.read_bytes()).hexdigest()[:16] if board.is_file() else ""
