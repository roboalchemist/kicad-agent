"""Board-level design rules the tools and the check share: net classes and stackup.

KiCad stores these in the project file; the harness keeps its own small, explicit rule set in the
workspace (``design_rules.json``) so the agent can declare intent and ``check`` can hold the board
to it without guessing. The rules are deliberately operational, not a full DRC engine:

- a net class gives the width (and clearance/impedance target) new tracks use, and ``check``
  reports any track narrower than its class;
- a net class may declare the current it carries (``current_a``); the width that current needs
  is derived from the stackup's copper weight and temperature rise, and ``check`` reports any
  track that cannot carry it;
- a declared class that matches no net on the board is reported, so a typo can never make a
  rule quietly apply to nothing;
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
    # KiCad has no per-class track-width DRC, so the board minimums mirror the weakest declared
    # class: a track thinner than every declared class is then a KiCad error too. The declaration
    # is the source of truth, so changing a class updates these rather than only raising them.
    widths = [float(spec.get("width")) for name, spec in (rules.get("netclasses") or {}).items()
              if name != "Default" and spec.get("explicit") and spec.get("width")]
    clearances = [float(spec.get("clearance")) for name, spec in (rules.get("netclasses") or {}).items()
                  if name != "Default" and spec.get("explicit") and spec.get("clearance")]
    design = project.setdefault("board", {}).setdefault("design_settings", {})
    board_rules = design.setdefault("rules", {})
    if widths:
        board_rules["min_track_width"] = min(widths)
    if clearances:
        board_rules["min_clearance"] = min(clearances)
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


def normalize_net(name: str) -> str:
    """Kicad reports net names with a leading ``/`` in some places and without it in others.

    The agent writes "+5V" the way the schematic shows it; the pick index and the DSN report
    "/+5V" (or the reverse, depending on the exporter). A class pattern must match both, or the
    declaration silently applies to nothing and the board keeps its old widths.
    """
    return str(name or "").lstrip("/")


def _matches(pattern: str, net: str) -> bool:
    pattern, net = normalize_net(pattern), normalize_net(net)
    if pattern in ("*", ""):
        return True
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), net) is not None


def pattern_rank(pattern: str) -> tuple[int, int]:
    """Sort key: exact patterns first, then prefixes by length, then the universal ``*``.

    Declaration order must not decide which class wins: a board that declares POWER (+5V) and
    then a catch-all class would otherwise route +5V at the catch-all width. This is also how
    KiCad resolves netclass patterns (a specific pattern beats a wildcard).
    """
    text = normalize_net(pattern)
    if "*" not in text:
        return (0, -len(text))
    if text == "*":
        return (2, 0)
    return (1, -len(text.split("*", 1)[0]))


def class_for(rules: dict[str, Any], net: str) -> tuple[str, dict[str, Any]]:
    """The most specific net class whose pattern matches ``net`` (Default last)."""
    classes = rules.get("netclasses") or {}
    pairs = [
        (pattern, name, spec)
        for name, spec in classes.items()
        if name != "Default"
        for pattern in spec.get("nets") or []
    ]
    for pattern, name, spec in sorted(pairs, key=lambda item: pattern_rank(item[0])):
        if _matches(pattern, net):
            return name, spec
    return "Default", classes.get("Default") or DEFAULT["netclasses"]["Default"]


def _microstrip_z0(width_mm: float, er: float, height_mm: float) -> float:
    """IPC-2141 microstrip approximation; good enough to flag a clearly wrong width."""
    return 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * height_mm / (0.8 * width_mm + 0.035))


def required_trace_width(current_a: float, copper_oz: float = 1.0, temp_rise_c: float = 20.0,
                         internal: bool = False) -> float:
    """The trace width (mm) that carries ``current_a`` at ``temp_rise_c`` (IPC-2221).

    I = k * dT^0.44 * A^0.725 with A in square mils; external k = 0.048, internal k = 0.024,
    1 oz of copper is 1.378 mil thick. This is the chart in curve form, good enough to hold a
    board to "traces meet their power needs"; it is not a thermal simulation.
    """
    current = float(current_a)
    if current <= 0:
        raise ValueError("current must be positive")
    k = 0.024 if internal else 0.048
    area_mil2 = (current / (k * float(temp_rise_c) ** 0.44)) ** (1 / 0.725)
    thickness_mil = max(float(copper_oz), 0.5) * 1.378
    return round(area_mil2 / thickness_mil * 0.0254, 3)


def parse_amps(value: str) -> float | None:
    """Current encoded in a part value ("2A", "500 mA", "1.5A polyfuse"), or None."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(mA|A)\b", str(value or ""), re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number / 1000.0 if match.group(2).lower() == "ma" else number


def estimated_amps(spec: dict[str, Any]) -> float | None:
    """The amperage a class's estimate works out to: loads summed, capped by any protection.

    Estimate entries are ``{"refs": [...], "role": str, "amps": float, "kind": "load"|"cap",
    "count": N}``. ``amps`` is per unit and ``count`` (default 1) is how many of them there are,
    so five 0.08 A relay coils are one entry with amps 0.08, count 5. Loads add; a "cap" entry is
    an upstream protective device or source limit, and what flows can never exceed it.
    """
    entries = spec.get("estimate") or []
    if not entries:
        return None
    loads = [float(e.get("amps") or 0) * max(1, int(e.get("count") or 1))
             for e in entries if e.get("kind", "load") != "cap"]
    caps = [float(e.get("amps") or 0) for e in entries if e.get("kind") == "cap"]
    total = sum(amps for amps in loads if amps > 0)
    if caps:
        limit = min(amps for amps in caps if amps > 0)
        total = min(total, limit) if loads else limit
    return round(total, 3)


def class_required_width(spec: dict[str, Any], rules: dict[str, Any] | None = None) -> float | None:
    """The width a class demands: explicit, or derived from the current its estimate supports."""
    if spec.get("required_width"):
        return float(spec["required_width"])
    current = spec.get("current_a")
    estimated = estimated_amps(spec)
    if not current and not estimated:
        return None
    stackup = (rules or {}).get("stackup") or {}
    amps = max(float(current or 0), float(estimated or 0))
    return max(
        float(spec.get("width") or 0),
        required_trace_width(amps, float(stackup.get("copper_weight") or 1.0),
                             float(stackup.get("temp_rise_c") or 20.0)),
    )


def violations(index: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Tracks narrower than their class or their declared current, an unused class, and a
    stackup that disagrees with the copper layers."""
    out: list[dict[str, Any]] = []
    matched: set[str] = set()
    for track in index.get("tracks", []):
        name, spec = class_for(rules, track.get("net") or "")
        if name != "Default":
            matched.add(name)
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
        needed = class_required_width(spec, rules)
        if needed and width + 1e-6 < needed:
            out.append({
                "type": "current_capacity",
                "net": track.get("net"),
                "class": name,
                "detail": (f"{track.get('net') or 'track'} is {width:.2f} mm but carries "
                           f"{float(spec.get('current_a')):g} A, which needs {needed:.2f} mm "
                           f"at {float((rules.get('stackup') or {}).get('temp_rise_c') or 20.0):g}°C rise"),
                "uuid": track.get("uuid"),
            })
    for name, spec in (rules.get("netclasses") or {}).items():
        if name == "Default" or not spec.get("explicit") or name in matched:
            continue
        if index.get("tracks") or index.get("pads"):
            out.append({
                "type": "class_coverage",
                "class": name,
                "detail": (f"class {name} ({spec.get('width')} mm, nets {spec.get('nets')}) "
                           "matches no net on the board — the declaration applies to nothing"),
            })

    # A current that nobody estimated is a guess. Power classes must justify their amperage with
    # entries citing real parts, and the estimate must not contradict what it cites.
    board_refs = {str(f.get("ref") or "").upper() for f in index.get("footprints", [])}
    for name, spec in (rules.get("netclasses") or {}).items():
        if not spec.get("explicit") or not spec.get("current_a"):
            continue
        estimate = spec.get("estimate") or []
        if not estimate:
            out.append({
                "type": "power_estimate_missing",
                "class": name,
                "detail": (f"class {name} declares {spec.get('current_a'):g} A with no estimate: "
                           "add estimate entries citing the parts that set that current "
                           "(fuse rating, source limit, or the sum of the loads)"),
            })
            continue
        loads = [float(e.get("amps") or 0) for e in estimate if e.get("kind", "load") != "cap"]
        caps = [e for e in estimate if e.get("kind") == "cap"]
        for entry in estimate:
            for ref in entry.get("refs") or []:
                if ref and str(ref).upper() not in board_refs:
                    out.append({
                        "type": "power_estimate_ref_unknown",
                        "class": name,
                        "detail": (f"class {name}'s estimate cites {ref!r}, which is not a part "
                                   "on the board"),
                    })
        for cap in caps:
            for ref in cap.get("refs") or []:
                part = next((f for f in index.get("footprints", [])
                             if str(f.get("ref") or "").upper() == str(ref).upper()), None)
                rated = parse_amps((part or {}).get("value"))
                if rated is not None and float(cap.get("amps") or 0) > rated * 1.01:
                    out.append({
                        "type": "power_cap_overrated",
                        "class": name,
                        "detail": (f"class {name} estimates {float(cap.get('amps')):g} A through "
                                   f"{ref}, but {ref} is rated {rated:g} A ({part.get('value')})"),
                    })
        load_sum = sum(amps for amps in loads if amps > 0)
        cap_min = min((float(e.get("amps") or 0) for e in caps), default=None)
        if cap_min is not None and load_sum > cap_min * 1.01:
            out.append({
                "type": "power_cap_exceeded",
                "class": name,
                "detail": (f"class {name}'s loads sum to {load_sum:g} A but its protective device "
                           f"allows {cap_min:g} A"),
            })
        estimated = estimated_amps(spec)
        if estimated and float(spec["current_a"]) + 1e-6 < estimated:
            out.append({
                "type": "power_estimate_underdeclared",
                "class": name,
                "detail": (f"class {name} declares {float(spec['current_a']):g} A but its own "
                           f"estimate works out to {estimated:g} A — size the trace for the "
                           "estimate"),
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
