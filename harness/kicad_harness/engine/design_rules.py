"""Board-level design rules the tools and the check share: net classes and stackup.

KiCad stores these in the project file; the harness keeps its own small, explicit rule set in the
workspace (``design_rules.json``) so the agent can declare intent and ``check`` can hold the board
to it without guessing. The rules are deliberately operational, not a full DRC engine:

- a net class gives the width (and clearance/impedance target) new tracks use, and ``check``
  reports any track narrower than its class;
- a stackup declares the layer count/material/thickness and is compared with the board's copper
  layers.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

FILE = "design_rules.json"

DEFAULT: dict[str, Any] = {
    "netclasses": {
        "Default": {"nets": ["*"], "width": 0.25, "clearance": 0.2, "impedance": None,
                "explicit": False},
    },
    "stackup": None,
}


def _path(ws) -> Path:
    return Path(ws.path) / FILE


def load(ws) -> dict[str, Any]:
    path = _path(ws)
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return json.loads(json.dumps(DEFAULT))
    data.setdefault("netclasses", {})
    data.setdefault("stackup", None)
    if "Default" not in data["netclasses"]:
        data["netclasses"].update(json.loads(json.dumps(DEFAULT["netclasses"])))
    return data


def save(ws, rules: dict[str, Any]) -> dict[str, Any]:
    """Atomic, unique-temp write: two tools may save at once (they did, and raced)."""
    path = _path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".design_rules.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(rules, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return rules


def _matches(pattern: str, net: str) -> bool:
    if pattern in ("*", ""):
        return True
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), net or "") is not None


def class_for(rules: dict[str, Any], net: str) -> tuple[str, dict[str, Any]]:
    """The first net class whose pattern matches ``net`` (Default last)."""
    classes = rules.get("netclasses") or {}
    for name, spec in classes.items():
        if name == "Default":
            continue
        if any(_matches(pattern, net) for pattern in spec.get("nets") or []):
            return name, spec
    return "Default", classes.get("Default") or DEFAULT["netclasses"]["Default"]


def _microstrip_z0(width_mm: float, er: float, height_mm: float) -> float:
    """IPC-2141 microstrip approximation; good enough to flag a clearly wrong width."""
    return 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * height_mm / (0.8 * width_mm + 0.035))


def violations(index: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Tracks narrower than their class, and a stackup that disagrees with the copper layers."""
    out: list[dict[str, Any]] = []
    for track in index.get("tracks", []):
        name, spec = class_for(rules, track.get("net") or "")
        if not spec.get("explicit"):
            continue  # the implicit Default class is not a rule the board must meet
        width = float(track.get("width") or 0)
        wanted = float(spec.get("width") or 0)
        if wanted and width + 1e-6 < wanted:
            out.append({
                "type": "trace_width",
                "net": track.get("net"),
                "class": name,
                "detail": f"{track.get('net') or 'track'} is {width:.2f} mm, class {name} asks {wanted:.2f} mm",
                "uuid": track.get("uuid"),
            })
    stackup = rules.get("stackup")
    if stackup:
        er = float(stackup.get("er") or 4.4)
        height = float(stackup.get("dielectric_height") or 0.2)
        for track in index.get("tracks", []):
            name, spec = class_for(rules, track.get("net") or "")
            target = spec.get("impedance")
            width = float(track.get("width") or 0)
            if not target or not width:
                continue
            z0 = _microstrip_z0(width, er, height)
            if abs(z0 - float(target)) / float(target) > 0.15:
                out.append({
                    "type": "impedance",
                    "net": track.get("net"),
                    "class": name,
                    "detail": f"{track.get('net') or 'track'} is {width:.2f} mm (~{z0:.0f} ohm), "
                              f"class {name} targets {float(target):.0f} ohm",
                    "uuid": track.get("uuid"),
                })
                break  # one example per class is enough
        copper = [layer for layer in (index.get("board", {}).get("layers") or [])
                  if str(layer).endswith(".Cu")]
        declared = int(stackup.get("layers") or 0)
        if declared and copper and len(copper) != declared:
            out.append({
                "type": "stackup",
                "detail": f"stackup declares {declared} copper layers, board has {len(copper)} ({', '.join(copper)})",
            })
    return out
