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


PROJECT_TEMPLATE: dict[str, Any] = {
    "board": {},
    "boards": [],
    "meta": {"filename": "board.kicad_pro", "version": 1},
    "net_settings": {"classes": [], "meta": {"version": 5}, "net_colors": {},
                     "netclass_assignments": {}, "netclass_patterns": []},
    "schematic": {},
}


def _project_path(ws) -> Path:
    candidates = sorted(Path(ws.path).glob("*.kicad_pro"))
    return candidates[0] if candidates else Path(ws.path) / "board.kicad_pro"


def _write_project(ws, rules: dict[str, Any]) -> Path:
    """Mirror the net classes into the KiCad project so KiCad's DRC uses the same clearance.

    Netclass clearance *is* enforced by KiCad DRC (track width is only a default; the harness
    check covers that). Existing project data is preserved; only net_settings changes.
    """
    path = _project_path(ws)
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        project = json.loads(json.dumps(PROJECT_TEMPLATE))
    net = project.setdefault("net_settings", {})
    existing = {c.get("name"): c for c in net.get("classes", []) if isinstance(c, dict)}
    default = existing.get("Default") or {
        "name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
        "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
        "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
        "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
        "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6,
        "priority": 2147483647, "tuning_profile": "",
    }
    classes = [default]
    patterns: list[dict[str, str]] = []
    priority = 1
    for name, spec in (rules.get("netclasses") or {}).items():
        if name == "Default" or not spec.get("explicit"):
            continue
        entry = dict(default)
        entry.update({
            "name": name,
            "clearance": float(spec.get("clearance") or 0.2),
            "track_width": float(spec.get("width") or 0.2),
            "diff_pair_width": float(spec.get("width") or 0.2),
            "diff_pair_gap": float(spec.get("clearance") or 0.2),
            "priority": priority,
        })
        priority += 1
        classes.append(entry)
        for pattern in spec.get("nets") or []:
            patterns.append({"netclass": name, "pattern": pattern})
    net["classes"] = classes
    net["netclass_patterns"] = patterns
    # KiCad has no per-class track-width DRC, so also lower the board's minimums to the weakest
    # declared class: a track thinner than every declared class is then a KiCad error too.
    widths = [float(spec.get("width")) for name, spec in (rules.get("netclasses") or {}).items()
              if name != "Default" and spec.get("explicit") and spec.get("width")]
    clearances = [float(spec.get("clearance")) for name, spec in (rules.get("netclasses") or {}).items()
                  if name != "Default" and spec.get("explicit") and spec.get("clearance")]
    design = project.setdefault("board", {}).setdefault("design_settings", {})
    board_rules = design.setdefault("rules", {})
    if widths:
        board_rules["min_track_width"] = max(float(board_rules.get("min_track_width") or 0),
                                             min(widths))
    if clearances:
        board_rules["min_clearance"] = max(float(board_rules.get("min_clearance") or 0),
                                           min(clearances))
    net.setdefault("meta", {"version": 5})
    net.setdefault("net_colors", {})
    net.setdefault("netclass_assignments", {})
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def save(ws, rules: dict[str, Any]) -> dict[str, Any]:
    """Atomic, unique-temp write: two tools may save at once (they did, and raced).

    The same net classes are mirrored into the KiCad project (``*.kicad_pro``)."""
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
    try:
        _write_project(ws, rules)
    except Exception:
        pass  # rules still apply in the harness even if the project cannot be written
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
