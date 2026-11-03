"""Adversarial verification: the brief must actually be delivered."""

from __future__ import annotations

from kicad_harness.agent import goal as goals
from kicad_harness.agent.audit import part_refs, render_inventory, summarise, AuditGap, AuditResult


def test_parity_flags_board_parts_missing_from_the_schematic() -> None:
    """The real pi-relay failure: 62 board parts, 24 in the schematic."""
    sch = ["C1", "C2", "F1", "J31", "R1", "#PWR001"]
    brd = ["C1", "C2", "F1", "J31", "R1", "K1", "K2", "U1", "J40"]
    out = part_refs(sch, brd)
    assert out["missing_from_schematic"] == ["J40", "K1", "K2", "U1"]
    assert out["missing_from_board"] == []
    # power flags are not parts
    assert out["schematic_count"] == 5


def test_parity_is_clean_when_they_match() -> None:
    out = part_refs(["R1", "K1"], ["K1", "R1"])
    assert out["missing_from_schematic"] == [] and out["missing_from_board"] == []


def test_parity_criterion_fails_and_says_what_is_missing() -> None:
    drc = {"schematic_missing": ["K1", "K2", "U1"], "board_missing": []}
    result = goals.evaluate_criterion(
        {"id": "parity", "type": "schematic_parity", "max_missing": 0}, {}, drc, []
    )
    assert result["passed"] is False
    assert "K1" in result["detail"] and "3 board parts" in result["detail"]


def test_parity_is_mandatory_even_if_the_agent_omits_it() -> None:
    """An agent-authored goal cannot dodge parity by leaving it out."""
    goal = {"criteria": [{"id": "drc", "type": "drc_errors", "max": 0}]}
    drc = {"counts": {"errors": 0}, "schematic_missing": ["K1"], "board_missing": []}
    evaluation = goals.evaluate(goal, {"board": {"edge_bbox": [0, 0, 10, 10]}, "footprints": []}, drc, [])
    ids = {c["id"]: c["passed"] for c in evaluation["criteria"]}
    assert ids.get("parity") is False
    assert evaluation["met"] is False, "parity is a floor, not an optional criterion"


def test_inventory_hands_the_reviewer_facts() -> None:
    text = render_inventory("16 relays on a 20 V rail", {"parts_on_board": ["K1", "K2"]})
    assert "16 relays" in text and "parts_on_board" in text


def test_audit_result_summary_names_gaps() -> None:
    audit = AuditResult(complete=False, gaps=[AuditGap(requirement="16 relays", problem="only 2 relays present")])
    assert "16 relays" in summarise(audit) and "only 2 relays" in summarise(audit)
