"""The adversarial judge: derived verdicts, evidence requirements and mutation checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kicad_harness.agent import judge


def _pass(rule_id: str, severity: str = "must") -> judge.RuleResult:
    return judge.RuleResult(id=rule_id, status="pass", severity=severity,
                            evidence=[f"measured {rule_id}"], citation=rule_id)


def test_a_pass_needs_evidence_and_cannot_be_below_its_limit() -> None:
    with pytest.raises(ValidationError):
        judge.RuleResult(id="silk", status="pass", citation="c")
    with pytest.raises(ValidationError):
        judge.RuleResult(id="silk", status="pass", measured=0.08, limit=0.15,
                         comparison="min", evidence=["x"], citation="c")
    with pytest.raises(ValidationError):
        judge.RuleResult(id="silk", status="pass", measured=0.2, limit=0.15,
                         evidence=["x"], citation="c")
    assert judge.RuleResult(id="silk", status="pass", measured=0.1, limit=0.15,
                            evidence=["x"], citation="c").status == "pass"


def test_complete_is_derived_and_fails_closed() -> None:
    mutations = judge.mutations()
    assert mutations.ok and mutations.total == 6
    verdict = judge.JudgeVerdict(results=[_pass("a")], mutations=mutations)
    assert verdict.complete is True

    failed = judge.JudgeVerdict(
        results=[judge.RuleResult(id="a", status="fail", detail="too close", citation="c")],
        mutations=mutations)
    assert failed.complete is False and "too close" in failed.findings[0]

    unmeasured = judge.JudgeVerdict(
        results=[judge.RuleResult(id="a", status="unmeasured", detail="no data", citation="c")],
        mutations=mutations)
    assert unmeasured.complete is False

    broken = judge.JudgeVerdict(results=[_pass("a")],
                                mutations=judge.MutationReport(total=1, caught=[], missed=["x"]))
    assert broken.complete is False

    # A measured should-fix failure is still a known defect: do not call it complete.
    should_fail = judge.JudgeVerdict(
        results=[_pass("a"), judge.RuleResult(id="b", status="fail", severity="should",
                                              detail="silk", citation="c")],
        mutations=mutations)
    assert should_fail.complete is False


def test_auto_results_come_from_the_same_evaluation() -> None:
    facts = {
        "evaluation": {"criteria": [
            {"id": "erc", "passed": True, "value": 0, "detail": "0 ERC errors"},
            {"id": "drc", "passed": False, "value": 2, "detail": "2 DRC errors"},
        ]},
        "board": False,
    }
    results = {r.id: r for r in judge.auto_results(facts)}
    assert results["erc.errors"].status == "pass"
    assert results["drc.errors"].status == "fail"
    assert results["drc.errors"].detail == "2 DRC errors"


def test_coverage_ledger_reports_the_encoded_fraction() -> None:
    report = judge.coverage()
    assert report["sections"] > 400
    assert report["auto"] >= 10
    assert report["judge"] >= 5
    assert 0 < report["auto_ratio"] < 1
    assert "pcb-high-speed#Via Stubs & Back-Drilling" in report["auto_ids"]


@pytest.mark.asyncio
async def test_assess_requires_a_verdict_for_every_rule_and_fails_closed(monkeypatch) -> None:
    facts = {"brief": "a board", "board": False,
             "evaluation": {"criteria": [{"id": "erc", "passed": True, "value": 0,
                                          "detail": "0 errors"}]}}
    failing = {"brief": "a board", "board": False,
               "evaluation": {"criteria": [
                   {"id": "erc", "passed": True, "value": 0, "detail": "0 errors"},
                   {"id": "drc", "passed": False, "value": 1, "detail": "1 error"}]}}

    async def partial(facts, specs, auto, model):
        return judge.ReviewResult(results=[_pass(specs[0].id, specs[0].severity)])

    verdict = await judge.assess(failing, object(), reviewer=partial)
    assert verdict.complete is False
    assert any(r.status == "unmeasured" for r in verdict.results)
    assert any(r.id == "pcb-placement#Priority Order" for r in verdict.results)

    async def full(facts, specs, auto, model):
        return judge.ReviewResult(results=[_pass(spec.id, spec.severity) for spec in specs])

    complete = await judge.assess(facts, object(), reviewer=full)
    assert complete.complete is True, complete.findings

    async def broken(facts, specs, auto, model):
        raise TimeoutError("judge down")

    failed = await judge.assess(facts, object(), reviewer=broken)
    assert failed.complete is False and "judge failed" in (failed.error or "")


@pytest.mark.asyncio
async def test_a_scripted_model_is_not_judged() -> None:
    class FunctionModel:
        pass

    verdict = await judge.assess({"evaluation": {"criteria": []}}, FunctionModel())
    assert verdict.complete is False and verdict.error


@pytest.mark.asyncio
async def test_a_human_waiver_can_clear_a_finding_but_an_agent_waiver_cannot() -> None:
    facts = {"brief": "a board", "board": False,
             "evaluation": {"criteria": [{"id": "erc", "passed": True, "value": 0,
                                          "detail": "0 errors"}]},
             "waivers": {"pcb-placement#Priority Order": {"author": "human",
                                                          "reason": "mechanical constraint"}}}

    async def partial(facts, specs, auto, model):
        return judge.ReviewResult(results=[
            judge.RuleResult(id="pcb-placement#Priority Order", status="fail",
                             severity="should", detail="row order", citation="pcb-placement#Priority Order")])

    verdict = await judge.assess(facts, object(), reviewer=partial)
    waived = next(r for r in verdict.results if r.id == "pcb-placement#Priority Order")
    assert waived.status == "waived" and "mechanical constraint" in waived.detail
    assert verdict.complete is True

    facts["waivers"] = {"pcb-placement#Priority Order": {"author": "agent", "reason": "lgtm"}}
    blocked = await judge.assess(facts, object(), reviewer=partial)
    assert next(r for r in blocked.results if r.id == "pcb-placement#Priority Order").status == "fail"
    assert blocked.complete is False
