"""Component heights and the enclosure: the mechanical fit check.

KiCad footprints carry no reliable height (3D models are optional and their offsets are not a
part dimension), so heights are declared per reference from the datasheet. With an enclosure
height declared, ``check`` reports every part that is too tall and every part whose height was
never measured. Rule: ``pcb-mechanical#Component Height & Enclosure`` (PCB-to-lid 5-10 mm).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

FILE = "mechanical.json"
DEFAULT_CLEARANCE = 5.0
RULE = "pcb-mechanical#Component Height & Enclosure"


def _path(ws) -> Path:
    return Path(ws.path) / FILE


def load(ws) -> dict[str, Any]:
    path = _path(ws)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data.setdefault("parts", {})
    data.setdefault("enclosure", None)
    return data


def save(ws, data: dict[str, Any]) -> dict[str, Any]:
    path = _path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mechanical.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return data


def set_part(ws, ref: str, height_mm: float, source: str | None = None) -> dict[str, Any]:
    data = load(ws)
    entry: dict[str, Any] = {"height_mm": round(float(height_mm), 3)}
    if source:
        entry["source"] = source
    data["parts"][ref] = entry
    save(ws, data)
    return {"ref": ref, **entry}


def set_enclosure(ws, height_mm: float, clearance_mm: float = DEFAULT_CLEARANCE) -> dict[str, Any]:
    data = load(ws)
    data["enclosure"] = {"height_mm": round(float(height_mm), 3),
                         "clearance_mm": round(float(clearance_mm), 3)}
    save(ws, data)
    return dict(data["enclosure"])


def report(index: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Fit against the declared enclosure: over-height parts, and parts never measured."""
    parts = data.get("parts") or {}
    enclosure = data.get("enclosure") or None
    refs = sorted({str(fp.get("ref")) for fp in index.get("footprints", [])
                   if fp.get("ref") and not str(fp.get("ref")).startswith("#")})
    measured = {ref: float(parts[ref]["height_mm"]) for ref in refs
                if ref in parts and parts[ref].get("height_mm") is not None}
    missing = [ref for ref in refs if ref not in measured]
    allowed = None
    if enclosure:
        allowed = round(float(enclosure.get("height_mm") or 0)
                        - float(enclosure.get("clearance_mm") or DEFAULT_CLEARANCE), 3)
    over = []
    if allowed is not None:
        over = [{"ref": ref, "height_mm": value, "allowed_mm": allowed}
                for ref, value in sorted(measured.items()) if value > allowed + 1e-9]
    declared = enclosure is not None
    return {
        "ok": bool(declared and not over and not missing),
        "enclosure_mm": enclosure.get("height_mm") if enclosure else None,
        "clearance_mm": enclosure.get("clearance_mm") if enclosure else None,
        "allowed_mm": allowed,
        "tallest_mm": round(max(measured.values()), 3) if measured else None,
        "measured": len(measured),
        "missing": missing,
        "over": over,
        "rule": RULE,
    }
