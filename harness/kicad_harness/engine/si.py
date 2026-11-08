"""Via-stub awareness for high-speed nets.

A through via's barrel spans the whole board; any part below the last layer the signal actually
uses is an un-terminated stub that resonates. The stackup gives the dielectric thickness, the
index gives each via's type and each net's routed layers, so the stub can be estimated without a
field solver. Rule: ``pcb-high-speed#Via Stubs & Back-Drilling`` (<15 mil for >1 GHz).
"""

from __future__ import annotations

from typing import Any

from . import design_rules

LIMIT_MM = 0.381  # 15 mil
RULE = "pcb-high-speed#Via Stubs & Back-Drilling"


def _copper(index: dict[str, Any]) -> list[str]:
    return [str(layer) for layer in (index.get("board", {}).get("layers") or [])
            if str(layer).endswith(".Cu")]


def report(index: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    copper = _copper(index)
    stackup = rules.get("stackup") or {}
    thickness = float(stackup.get("thickness") or 1.6)
    limit = float(stackup.get("max_stub_mm") or LIMIT_MM)
    position = {layer: i for i, layer in enumerate(copper)}
    split = max(1, len(copper) - 1)

    used: dict[str, set[str]] = {}
    for track in index.get("tracks", []):
        net = str(track.get("net") or "")
        used.setdefault(net, set()).add(str(track.get("layer") or ""))

    entries: list[dict[str, Any]] = []
    for via in index.get("vias", []):
        net = str(via.get("net") or "")
        kind = str(via.get("type") or "through")
        stub = 0.0
        if kind == "through" and len(copper) > 2:
            layers = [layer for layer in used.get(net, set()) if layer in position]
            lowest = max((position[layer] for layer in layers), default=None)
            if lowest is not None:
                stub = thickness * (len(copper) - 1 - lowest) / split
        name, spec = design_rules.class_for(rules, net)
        high_speed = spec.get("impedance") is not None
        entries.append({
            "uuid": via.get("uuid"),
            "net": net,
            "type": kind,
            "stub_mm": round(stub, 3),
            "class": name,
            "high_speed": high_speed,
        })
    over = [entry for entry in entries
            if entry["stub_mm"] > limit + 1e-9 and entry["high_speed"]]
    per_net: dict[str, float] = {}
    for entry in entries:
        per_net[entry["net"]] = max(per_net.get(entry["net"], 0.0), entry["stub_mm"])
    return {
        "ok": not over,
        "limit_mm": limit,
        "max_mm": round(max((entry["stub_mm"] for entry in entries), default=0.0), 3),
        "count": len(entries),
        "over": over,
        "net_stub_mm": {net: round(value, 3) for net, value in per_net.items()},
        "rule": RULE,
    }
