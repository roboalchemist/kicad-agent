"""Goal spec validation and evaluation, on synthetic data (no KiCad needed)."""

import pytest

from kicad_harness.agent import goal as g
from kicad_harness.agent.state import DesignState

INDEX = {
    "board": {"bbox": [0, 0, 25, 20], "edge_bbox": [0, 0, 25, 20], "layers": ["F.Cu", "B.Cu"], "thickness": 1.6},
    "nets": ["", "GND", "VCC"],
    "footprints": [
        {"uuid": "fp-u1", "ref": "U1", "value": "NE555", "footprint": "SOIC-8", "x": 9, "y": 10, "rotation": 0,
         "side": "front", "bbox": [6, 6.5, 6, 7], "courtyard": None, "ref_text": None},
        {"uuid": "fp-d1", "ref": "D1", "value": "LED", "footprint": "LED0805", "x": 19, "y": 16, "rotation": 90,
         "side": "front", "bbox": [18.4, 14.4, 1.2, 3.2], "courtyard": None, "ref_text": None},
    ],
    "pads": [
        {"uuid": "p1", "ref": "D1", "pad": "1", "net": "GND", "x": 19, "y": 17.1, "w": 1.0, "h": 1.25,
         "rotation": 90, "shape": "rect", "layers": ["F.Cu"], "through": False, "drill": 0,
         "polygon": [[18.375, 16.6], [19.625, 16.6], [19.625, 17.6], [18.375, 17.6]]},
        {"uuid": "p2", "ref": "U1", "pad": "1", "net": "GND", "x": 7.095, "y": 12.57, "w": 0.6, "h": 2.0,
         "rotation": 0, "shape": "oval", "layers": ["F.Cu"], "through": False, "drill": 0, "polygon": []},
    ],
    "tracks": [],
    "vias": [],
    "zones": [],
}
DRC = {"counts": {"errors": 0, "warnings": 3, "unconnected": 1}, "violations": [], "unconnected": []}
THREADS = [
    {"number": 1, "status": "open", "severity": "must_fix"},
    {"number": 2, "status": "resolved", "severity": "must_fix"},
    {"number": 3, "status": "open", "severity": "suggestion"},
    {"number": 4, "status": "reopened", "severity": "must_fix"},
]


def fake_measure(index, a, b):
    return {"distance": {"U1|D1": 7.5, "C3|U1.8": 2.4}[f"{a}|{b}"]}


def crit(**kw):
    return {"id": kw.pop("id", "c"), **kw}


@pytest.mark.parametrize(
    "criterion, passed, value",
    [
        (crit(type="drc_errors", max=0), True, 0),
        (crit(type="drc_warnings", max=2), False, 3),
        (crit(type="unconnected", max=0), False, 1),
        (crit(type="board_size", max_w=40, max_h=60), True, [25, 20]),
        (crit(type="board_size", max_w=20, max_h=60), False, [25, 20]),
        (crit(type="edge_clearance", min_mm=2.0), True, 2.4),
        (crit(type="edge_clearance", min_mm=3.0), False, 2.4),
        (crit(type="max_distance", a="C3", b="U1.8", max_mm=3), True, 2.4),
        (crit(type="min_distance", a="U1", b="D1", min_mm=10), False, 7.5),
        (crit(type="within_region", ref="D1", region=[15, 10, 25, 20]), True, None),
        (crit(type="within_region", ref="D1", region=[0, 0, 10, 10]), False, None),
        (crit(type="open_threads", severities=["must_fix"], max=0), False, 2),
        (crit(type="open_threads", max=3), True, 3),
    ],
)
def test_criteria(criterion, passed, value):
    result = g.evaluate_criterion(criterion, INDEX, DRC, THREADS, fake_measure)
    assert result["passed"] is passed, result
    if value is not None:
        assert result["value"] == pytest.approx(value)
    assert result["detail"]


def test_evaluate_and_failing_summary():
    goal = {"criteria": [crit(id="drc", type="drc_errors", max=0), crit(id="routed", type="unconnected", max=0)]}
    evaluation = g.evaluate(goal, INDEX, DRC, THREADS, fake_measure)
    assert evaluation["met"] is False
    assert [c["passed"] for c in evaluation["criteria"]] == [True, False]
    assert "[routing]" in g.failing_summary(evaluation)


def test_broken_criterion_fails_without_hiding_others():
    goal = {"criteria": [crit(id="bad", type="within_region", ref="D1", region="nope"),
                         crit(id="drc", type="drc_errors", max=0)]}
    evaluation = g.evaluate(goal, INDEX, DRC, THREADS, fake_measure)
    by_id = {c["id"]: c for c in evaluation["criteria"]}
    assert by_id["bad"]["passed"] is False
    assert "could not evaluate" in by_id["bad"]["detail"]
    assert by_id["drc"]["passed"] is True


def test_validate_goal():
    assert g.validate_goal(g.default_goal())
    with pytest.raises(ValueError, match="unknown type"):
        g.validate_goal({"criteria": [{"id": "x", "type": "nope"}]})
    with pytest.raises(ValueError, match="needs 'max'"):
        g.validate_goal({"criteria": [{"id": "x", "type": "drc_errors"}]})
    with pytest.raises(ValueError, match="duplicate id"):
        g.validate_goal({"criteria": [{"id": "x", "type": "drc_errors", "max": 0}] * 2})


def test_describe_lists_every_criterion():
    text = g.describe(g.default_goal())
    assert "[drc] DRC errors <= 0" in text
    assert "[review] open review threads (must_fix) <= 0" in text


def test_state_ignores_unknown_frontend_fields():
    state = DesignState.model_validate({"design": "blinker", "loop": True, "somethingNew": 1})
    assert state.design == "blinker" and state.loop is True
