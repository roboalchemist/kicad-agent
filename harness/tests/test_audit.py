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


def test_three_way_sync_flags_any_divergence() -> None:
    """BOM, schematic and board must agree on refs, values and footprints."""
    from kicad_harness.agent.audit import compare_sources

    agree = {"bom": {"R1": ("10k", "R0805")},
             "schematic": {"R1": ("10k", "R0805")},
             "board": {"R1": ("10k", "R0805")}}
    assert compare_sources(agree) == []

    # the real pi-relay shape: a part on the board that neither the BOM nor schematic lists
    missing = {"bom": {"R1": ("10k", "R0805")},
               "schematic": {"R1": ("10k", "R0805")},
               "board": {"R1": ("10k", "R0805"), "K1": ("relay", "Relay")}}
    assert any("K1" in g and "missing" in g for g in compare_sources(missing))

    drifted = {"bom": {"R1": ("10k", "R0805")},
               "schematic": {"R1": ("10k", "R0805")},
               "board": {"R1": ("12k", "R0805")}}
    assert any("R1" in g and "value differs" in g for g in compare_sources(drifted))

    rehoused = {"bom": {"R1": ("10k", "R0805")},
                "schematic": {"R1": ("10k", "R0805")},
                "board": {"R1": ("10k", "R0603")}}
    assert any("R1" in g and "footprint differs" in g for g in compare_sources(rehoused))


def test_design_sync_is_a_mandatory_loop_criterion() -> None:
    """An agent-authored goal cannot omit the three-way check."""
    goal = {"criteria": [{"id": "drc", "type": "drc_errors", "max": 0}]}
    drc = {"counts": {"errors": 0}, "sync_gaps": ["K1: missing from schematic"]}
    evaluation = goals.evaluate(goal, {"board": {"edge_bbox": [0, 0, 10, 10]}, "footprints": []}, drc, [])
    by_id = {c["id"]: c for c in evaluation["criteria"]}
    assert by_id["sync"]["passed"] is False
    assert "K1" in by_id["sync"]["detail"]
    assert evaluation["met"] is False


def test_schematic_is_never_frozen() -> None:
    """The schematic is a view of the design, not a phase artifact: edits must not be refused."""
    import inspect

    from kicad_harness.agent import agent as agent_mod

    for fn in ("_schematic_edit", "generate_schematic_skidl", "start_board_layout"):
        source = inspect.getsource(getattr(agent_mod, fn, None) or agent_mod.build_agent)
        if fn != "build_agent":
            assert "schematic is final" not in source, f"{fn} still freezes the schematic"


def test_reconcile_adds_missing_parts_without_rebuilding() -> None:
    """Since the schematic can change after layout, the board is reconciled, not recreated."""
    import inspect

    from kicad_harness.engine import seed

    src = inspect.getsource(seed.reconcile_board)
    assert "add_footprint" in src, "missing parts must be placed"
    assert "only_on_board" in src, "board-only parts must be reported, not silently dropped"


def test_erc_criterion_fails_when_not_measured() -> None:
    """An unmeasured ERC check must fail, not silently read 0."""
    from kicad_harness.agent import goal as g

    unmeasured = g.evaluate_criterion({"id": "erc", "type": "erc_errors", "max": 0}, {}, {}, [])
    assert unmeasured["passed"] is False
    assert "not measured" in unmeasured["detail"]

    measured = g.evaluate_criterion(
        {"id": "erc", "type": "erc_errors", "max": 0}, {}, {"erc_counts": {"errors": 235}}, []
    )
    assert measured["passed"] is False and measured["value"] == 235


# ---------------------------------------------------------------------------------------------
# The harness floor: hard checks the agent cannot remove or relax


def _board_payload(**extra):
    return {"counts": {"errors": 0, "warnings": 0, "unconnected": 0}, "erc_counts": {"errors": 0},
            "schematic_missing": [], "board_missing": [], "sync_gaps": [], **extra}


def _index():
    return {"board": {"edge_bbox": [0, 0, 10, 10]}, "footprints": []}


def test_floor_applies_even_when_the_goal_omits_it() -> None:
    """An agent-authored goal with no erc/drc criteria still gets them, at the strict values."""
    goal = {"criteria": [{"id": "size", "type": "board_size", "max_w": 40, "max_h": 40}]}
    drc = _board_payload(erc_counts={"errors": 235})
    evaluation = goals.evaluate(goal, _index(), drc, [])
    by_type = {c["type"]: c for c in evaluation["criteria"]}
    assert by_type["erc_errors"]["passed"] is False
    assert by_type["erc_errors"]["value"] == 235
    assert by_type["drc_errors"]["passed"] is True
    assert evaluation["met"] is False


def test_floor_cannot_be_relaxed_by_the_agents_goal() -> None:
    """Setting drc max: 99 in goal.json must not weaken the harness check."""
    goal = {"criteria": [{"id": "drc", "type": "drc_errors", "max": 99}]}
    drc = _board_payload(counts={"errors": 5, "warnings": 0, "unconnected": 0})
    evaluation = goals.evaluate(goal, _index(), drc, [])
    drc_results = [c for c in evaluation["criteria"] if c["type"] == "drc_errors"]
    assert len(drc_results) == 1, "the agent's relaxed copy must be replaced, not appended"
    assert drc_results[0]["passed"] is False and drc_results[0]["value"] == 5


def test_floor_is_measured_where_it_is_measurable() -> None:
    """Schematic-phase runs still get ERC; board checks wait until there is a board."""
    goal = {"criteria": []}
    schematic_only = {"erc_counts": {"errors": 1}, "schematic_missing": [], "board_missing": [],
                      "sync_gaps": [], "requirements": []}
    types = {c["type"] for c in goals.evaluate(goal, {}, schematic_only, [])["criteria"]}
    assert "erc_errors" in types
    assert "unconnected" not in types, "no board yet, so routing is not measurable"
