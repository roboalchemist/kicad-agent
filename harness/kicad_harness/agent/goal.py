"""Goal specs and their evaluation. See DESIGN.md, "Goal"."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

DEFAULT_GOAL: dict[str, Any] = {
    "summary": "Keep the board manufacturable: no DRC errors, every net routed, and no open must-fix review comments.",
    "criteria": [
        {"id": "drc", "type": "drc_errors", "max": 0},
        {"id": "routed", "type": "unconnected", "max": 0},
        {"id": "review", "type": "open_threads", "severities": ["must_fix"], "max": 0},
    ],
    "budget": {"max_requests": 120, "max_minutes": 30},
}

OPEN_STATUSES = {"open", "reopened"}

# type -> (required params, description template)
CRITERIA: dict[str, tuple[tuple[str, ...], str]] = {
    "drc_errors": (("max",), "DRC errors <= {max}"),
    "drc_warnings": (("max",), "DRC warnings <= {max}"),
    "unconnected": (("max",), "unconnected items <= {max}"),
    "board_size": (("max_w", "max_h"), "board outline within {max_w} x {max_h} mm"),
    "edge_clearance": (("min_mm",), "every pad at least {min_mm} mm from the board edge"),
    "max_distance": (("a", "b", "max_mm"), "{a} within {max_mm} mm of {b}"),
    "min_distance": (("a", "b", "min_mm"), "{a} at least {min_mm} mm from {b}"),
    "within_region": (("ref", "region"), "{ref} entirely inside region {region}"),
    "open_threads": (("max",), "open review threads ({severity_text}) <= {max}"),
    "erc_errors": (("max",), "ERC errors <= {max}"),
    "erc_warnings": (("max",), "ERC warnings <= {max}"),
    "board_laid_out": (("min",), "board laid out from the schematic"),
    "unconnected_pins": (("max",), "unconnected schematic pins <= {max}"),
    "design_sync": (("max_gaps",), "BOM, schematic and board agree (gaps <= {max_gaps})"),
    "requirements": (("max_short",), "brief requirements met (shortfalls <= {max_short})"),
    "schematic_parity": (("max_missing",), "schematic and board describe the same parts (missing <= {max_missing})"),
}

Measure = Callable[[dict, str, str], dict]


def with_board_state(drc: dict | None, board_exists: bool) -> dict:
    """Add the facts the board-phase criteria need but DRC does not report.

    ``board_laid_out`` used to be supplied only by the schematic-phase evaluator, so a goal that
    required a board stayed unmet for ever once the board existed — in four separate call sites.
    Every evaluator now goes through here.
    """
    return {**(drc or {}), "board_laid_out": bool(board_exists)}


def default_goal() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_GOAL)


def validate_goal(goal: dict[str, Any]) -> dict[str, Any]:
    """Check a goal spec and return it. Raises ValueError with every problem found."""
    problems: list[str] = []
    if not isinstance(goal, dict):
        raise ValueError("goal must be an object")
    criteria = goal.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("goal.criteria must be a non-empty list")
        criteria = []
    seen: set[str] = set()
    for i, c in enumerate(criteria):
        where = f"criteria[{i}]"
        if not isinstance(c, dict):
            problems.append(f"{where} must be an object")
            continue
        cid, ctype = c.get("id"), c.get("type")
        if not cid:
            problems.append(f"{where} needs an id")
        elif cid in seen:
            problems.append(f"{where}: duplicate id {cid!r}")
        seen.add(cid or "")
        if ctype not in CRITERIA:
            problems.append(f"{where}: unknown type {ctype!r} (known: {', '.join(sorted(CRITERIA))})")
            continue
        for param in CRITERIA[ctype][0]:
            if param not in c:
                problems.append(f"{where} ({ctype}) needs {param!r}")
        if ctype == "within_region" and "region" in c:
            region = c["region"]
            if not (isinstance(region, list) and len(region) == 4):
                problems.append(f"{where}: region must be [x1, y1, x2, y2]")
    budget = goal.get("budget", {})
    if budget and not isinstance(budget, dict):
        problems.append("goal.budget must be an object")
    if problems:
        raise ValueError("; ".join(problems))
    return goal


def describe_criterion(c: dict[str, Any]) -> str:
    params = dict(c)
    severities = c.get("severities")
    params["severity_text"] = ", ".join(severities) if severities else "any severity"
    template = CRITERIA.get(c.get("type", ""), ((), "{type}"))[1]
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return str(c.get("type"))


def describe(goal: dict[str, Any]) -> str:
    """Human-readable goal for prompts and the CLI."""
    lines = []
    if goal.get("summary"):
        lines.append(goal["summary"])
    lines.append("Acceptance criteria:")
    for c in goal.get("criteria", []):
        lines.append(f"- [{c.get('id')}] {describe_criterion(c)}")
    return "\n".join(lines)


def _edge_clearance(index: dict) -> tuple[float | None, str]:
    ex, ey, ew, eh = index["board"]["edge_bbox"]
    best: tuple[float | None, str] = (None, "")
    for pad in index.get("pads", []):
        points = pad.get("polygon") or []
        if not points:
            hw, hh = pad.get("w", 0) / 2, pad.get("h", 0) / 2
            points = [[pad["x"] + dx, pad["y"] + dy] for dx in (-hw, hw) for dy in (-hh, hh)]
        for px, py in points:
            d = min(px - ex, ex + ew - px, py - ey, ey + eh - py)
            if best[0] is None or d < best[0]:
                best = (d, f"{pad.get('ref')}.{pad.get('pad')}")
    return best


def _footprint(index: dict, ref: str) -> dict | None:
    for fp in index.get("footprints", []):
        if fp.get("ref") == ref:
            return fp
    return None


def _default_measure(index: dict, a: str, b: str) -> dict:
    from kicad_harness.engine.geometry import measure

    return measure(index, a, b)


def evaluate_criterion(
    c: dict[str, Any], index: dict, drc: dict, threads: list[dict], measure: Measure | None = None
) -> dict[str, Any]:
    """Evaluate one criterion. Returns {id, type, passed, value, detail}."""
    ctype = c["type"]
    result: dict[str, Any] = {"id": c["id"], "type": ctype, "passed": False, "value": None, "detail": ""}
    counts = (drc or {}).get("counts", {})
    try:
        if ctype in ("drc_errors", "drc_warnings", "unconnected"):
            key = {"drc_errors": "errors", "drc_warnings": "warnings", "unconnected": "unconnected"}[ctype]
            value = int(counts.get(key, 0))
            result.update(value=value, passed=value <= c["max"], detail=f"{value} {key} (max {c['max']})")
        elif ctype == "board_size":
            _, _, w, h = index["board"]["edge_bbox"]
            ok = w <= c["max_w"] + 1e-6 and h <= c["max_h"] + 1e-6
            result.update(value=[round(w, 3), round(h, 3)], passed=ok,
                          detail=f"board is {w:.2f} x {h:.2f} mm (max {c['max_w']} x {c['max_h']})")
        elif ctype == "edge_clearance":
            value, where = _edge_clearance(index)
            if value is None:
                result.update(passed=True, detail="no pads")
            else:
                result.update(value=round(value, 3), passed=value >= c["min_mm"] - 1e-6,
                              detail=f"closest pad to the edge is {where} at {value:.2f} mm (min {c['min_mm']})")
        elif ctype in ("max_distance", "min_distance"):
            m = (measure or _default_measure)(index, c["a"], c["b"])
            d = float(m["distance"])
            if ctype == "max_distance":
                ok, limit = d <= c["max_mm"] + 1e-6, f"max {c['max_mm']}"
            else:
                ok, limit = d >= c["min_mm"] - 1e-6, f"min {c['min_mm']}"
            result.update(value=round(d, 3), passed=ok, detail=f"{c['a']} to {c['b']} is {d:.2f} mm ({limit})")
        elif ctype == "within_region":
            fp = _footprint(index, c["ref"])
            x1, y1, x2, y2 = c["region"]
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)
            if fp is None:
                result.update(detail=f"no footprint {c['ref']}")
            else:
                bx, by, bw, bh = fp["bbox"]
                ok = bx >= x1 - 1e-6 and by >= y1 - 1e-6 and bx + bw <= x2 + 1e-6 and by + bh <= y2 + 1e-6
                result.update(value=[round(bx, 3), round(by, 3), round(bx + bw, 3), round(by + bh, 3)], passed=ok,
                              detail=f"{c['ref']} spans ({bx:.2f}, {by:.2f})-({bx + bw:.2f}, {by + bh:.2f}); "
                                     f"region ({x1}, {y1})-({x2}, {y2})")
        elif ctype in ("erc_errors", "erc_warnings"):
            # ERC comes from the schematic; the caller passes it as drc= with an "erc" marker.
            erc_counts = (drc or {}).get("erc_counts", {})
            key = "errors" if ctype == "erc_errors" else "warnings"
            value = int(erc_counts.get(key, 0))
            result.update(value=value, passed=value <= c["max"], detail=f"{value} ERC {key} (max {c['max']})")
        elif ctype == "design_sync":
            gaps_ = list((drc or {}).get("sync_gaps") or [])
            allowed = int(c.get("max_gaps", 0))
            result.update(value=gaps_, passed=len(gaps_) <= allowed,
                          detail=("; ".join(gaps_[:6]) or "BOM, schematic and board agree")
                          + (f" ({len(gaps_)} gap(s), max {allowed})" if gaps_ else ""))
        elif ctype == "requirements":
            gaps_ = list((drc or {}).get("requirement_gaps") or [])
            allowed = int(c.get("max_short", 0))
            result.update(value=gaps_, passed=len(gaps_) <= allowed,
                          detail=("; ".join(gaps_) or "every stated requirement is present")
                          + (f" (max {allowed})" if gaps_ else ""))
        elif ctype == "schematic_parity":
            missing_sch = list((drc or {}).get("schematic_missing") or [])
            missing_brd = list((drc or {}).get("board_missing") or [])
            allowed = int(c.get("max_missing", 0))
            total = len(missing_sch) + len(missing_brd)
            result.update(
                value={"missing_from_schematic": missing_sch, "missing_from_board": missing_brd},
                passed=total <= allowed,
                detail=(
                    f"{len(missing_sch)} board parts absent from the schematic"
                    f"{' (' + ', '.join(missing_sch[:8]) + ')' if missing_sch else ''}, "
                    f"{len(missing_brd)} schematic parts absent from the board"
                    f"{' (' + ', '.join(missing_brd[:8]) + ')' if missing_brd else ''} (max {allowed})"
                ),
            )
        elif ctype == "unconnected_pins":
            value = int((drc or {}).get("unconnected_pins", 0))
            result.update(value=value, passed=value <= c["max"],
                          detail=f"{value} unconnected pins (max {c['max']})")
        elif ctype == "board_laid_out":
            laid_out = 1 if (drc or {}).get("board_laid_out") else 0
            result.update(value=laid_out, passed=laid_out >= int(c.get("min", 1)),
                          detail="board laid out from the schematic" if laid_out else "no board yet")
        elif ctype == "open_threads":
            severities = set(c.get("severities") or [])
            open_threads = [
                t for t in threads
                if t.get("status") in OPEN_STATUSES and (not severities or t.get("severity") in severities)
            ]
            numbers = ", ".join(f"#{t.get('number')}" for t in open_threads)
            result.update(value=len(open_threads), passed=len(open_threads) <= c["max"],
                          detail=f"{len(open_threads)} open ({numbers or 'none'}; max {c['max']})")
        else:
            result.update(detail=f"unknown criterion type {ctype!r}")
    except Exception as exc:  # a broken criterion must not hide the others
        result.update(passed=False, detail=f"could not evaluate: {exc}")
    return result


def mandatory_criteria(goal: dict[str, Any], drc: dict | None) -> list[dict[str, Any]]:
    """Criteria that always apply, even if the agent's own goal omits them.

    The agent writes its own goal, so without a floor it can pass by measuring only what it
    happened to finish. Schematic/board parity is cheap, mechanical, and catches a design that
    simply omits parts the brief asked for.
    """
    have = {c.get("type") for c in goal.get("criteria", [])}
    extra: list[dict[str, Any]] = []
    if "sync_gaps" in (drc or {}) and "design_sync" not in have:
        extra.append({"id": "sync", "type": "design_sync", "max_gaps": 0})
    if (drc or {}).get("requirements") and "requirements" not in have:
        extra.append({"id": "brief", "type": "requirements", "max_short": 0})
    if "schematic_missing" in (drc or {}) or "board_missing" in (drc or {}):
        if "schematic_parity" not in have:
            extra.append({"id": "parity", "type": "schematic_parity", "max_missing": 0})
    return extra


def evaluate(
    goal: dict[str, Any], index: dict, drc: dict, threads: list[dict], measure: Measure | None = None
) -> dict[str, Any]:
    """Evaluate every criterion. Returns {met, criteria: [...]}."""
    criteria = list(goal.get("criteria", [])) + mandatory_criteria(goal, drc)
    results = [evaluate_criterion(c, index, drc, threads, measure) for c in criteria]
    return {"met": bool(results) and all(r["passed"] for r in results), "criteria": results}


def failing_summary(evaluation: dict[str, Any]) -> str:
    failing = [c for c in evaluation.get("criteria", []) if not c.get("passed")]
    return "; ".join(f"[{c['id']}] {c['detail']}" for c in failing) or "none"
