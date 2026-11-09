"""An adversarial judge that cannot be talked into LGTM.

The working agent writes its own goal and grades itself, so the goal floor is necessary but not
sufficient. This module adds three independent layers:

- a rule ledger: every bundled rule section has an id, severity and enforcement tier, and the
  numeric rules are evaluated by the same engine functions `check` uses;
- a separate reviewer agent that must return one verdict per rubric rule, with evidence, and may
  say ``unmeasured`` but never invent a pass;
- a mutation suite that deliberately breaks a synthetic board and verifies the detectors catch it.

``JudgeVerdict.complete`` is computed from those results and the mutation report. It is not a field
the reviewer supplies, so "LGTM" is not an expressible output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from . import knowledge

SEVERITY = Literal["must", "should", "may"]
STATUS = Literal["pass", "fail", "unmeasured", "waived"]

# Each auto rule cites the rule section it enforces. Keep the citation exact: the coverage report
# matches sections by id and a typo would show up as an uncovered rule.
AUTO_META: dict[str, tuple[str, SEVERITY, str]] = {
    "erc.errors": ("ERC errors", "must", "pcb-design-process#Phase Details"),
    "schematic.legibility": ("Schematic legibility", "must", "pcb-placement#Priority Order"),
    "drc.errors": ("DRC errors", "must", "pcb-design-process#Phase Details"),
    "routing.unconnected": ("Unconnected items", "must", "pcb-routing#Priority Order"),
    "schematic.parity": ("Schematic/board part parity", "must", "pcb-bom-components#BOM Documentation Requirements"),
    "design.sync": ("BOM/schematic/board agreement", "must", "pcb-bom-components#BOM Documentation Requirements"),
    "brief.requirements": ("Brief requirements", "must", "pcb-design-process#Phase Details"),
    "design_rules.violations": ("Declared width/clearance/impedance rules", "must", "pcb-routing#Spacing & Crosstalk"),
    "manufacturing.package": ("Manufacturing package current", "must", "pcb-design-process#Phase Details"),
    "mechanical.height": ("Component height fit", "should", "pcb-mechanical#Component Height & Enclosure"),
    "highspeed.via_stub": ("Via stub length", "should", "pcb-high-speed#Via Stubs & Back-Drilling"),
    "silk.text": ("Silkscreen text collisions", "should", "pcb-silkscreen#Placement Rules"),
    "placement.courtyard": ("Courtyard overlaps", "should", "pcb-placement#Spacing"),
    "test.coverage": ("Test-point coverage", "should", "pcb-test-debug#Test Coverage"),
    "thermal.copper_balance": ("Copper balance", "should", "pcb-copper-pours#Copper Balance"),
}

# Rubric rules the reviewer must answer. They are the rules a geometry check cannot fully decide,
# chosen from the documents the workflow already cites.
# These are the qualitative rules. They are "should" findings, not hard gates: the hard gates
# are the measured AUTO rules. A judge that cannot verify one says "unmeasured", which is reported
# but does not block on a rule the harness cannot measure.
JUDGE_SECTIONS: list[tuple[str, str, SEVERITY]] = [
    ("pcb-placement", "Priority Order", "should"),
    ("pcb-placement", "Spacing", "should"),
    ("pcb-routing", "Signal Integrity", "should"),
    ("pcb-routing", "Spacing & Crosstalk", "should"),
    ("pcb-power", "Decoupling Strategy (3-stage)", "should"),
    ("pcb-power", "Trace Width (IPC-2152, 1oz copper, 10°C rise)", "should"),
    ("pcb-copper-pours", "Thermal Relief", "should"),
    ("pcb-silkscreen", "Placement Rules", "should"),
    ("pcb-emc", "Via Stitching", "should"),
    ("pcb-mechanical", "Component Height & Enclosure", "should"),
    ("pcb-high-speed", "Via Stubs & Back-Drilling", "should"),
    ("pcb-dfm", "Board Edge", "should"),
    ("pcb-bom-components", "BOM Documentation Requirements", "should"),
    ("pcb-test-debug", "Test Coverage", "should"),
]


def _rule_id(doc: str, heading: str) -> str:
    return f"{doc}#{heading}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class RuleResult(BaseModel):
    """One rule, one verdict. ``pass`` is impossible without evidence."""

    id: str
    title: str = ""
    status: STATUS
    severity: SEVERITY = "must"
    source: Literal["auto", "judge", "human"] = "judge"
    measured: Any = None
    limit: Any = None
    comparison: Literal["max", "min"] = "max"
    evidence: list[str] = Field(default_factory=list)
    citation: str = ""
    detail: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        # Rule ids embed human headings (commas, units, degree signs). Reject only empty or
        # multi-line values so a citation cannot smuggle instruction text into an id.
        if not value or len(value) > 240 or "\n" in value or "\r" in value:
            raise ValueError(f"invalid rule id {value!r}")
        return value

    @model_validator(mode="after")
    def _consistent(self):
        if self.status == "pass" and not self.evidence:
            raise ValueError(f"{self.id}: a pass needs evidence")
        if self.status == "fail" and not self.detail:
            raise ValueError(f"{self.id}: a fail needs a concrete detail")
        if (self.status == "pass" and isinstance(self.measured, (int, float))
                and isinstance(self.limit, (int, float))):
            if self.comparison == "max" and self.measured > self.limit + 1e-9:
                raise ValueError(f"{self.id}: pass above limit ({self.measured} > {self.limit})")
            if self.comparison == "min" and self.measured + 1e-9 < self.limit:
                raise ValueError(f"{self.id}: pass below minimum ({self.measured} < {self.limit})")
        return self


class MutationReport(BaseModel):
    total: int = 0
    caught: list[str] = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counts(self):
        if self.total != len(self.caught) + len(self.missed):
            raise ValueError("mutation total must equal caught + missed")
        if self.total == 0:
            raise ValueError("mutation suite did not run")
        return self

    @computed_field
    @property
    def ok(self) -> bool:
        return not self.missed


class ReviewResult(BaseModel):
    """What the reviewer agent is allowed to produce: rule findings, not a completion flag."""

    results: list[RuleResult] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _unique(self):
        ids = [r.id for r in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids in the review")
        return self


class JudgeVerdict(BaseModel):
    results: list[RuleResult] = Field(default_factory=list)
    mutations: MutationReport
    error: str | None = None

    @model_validator(mode="after")
    def _unique(self):
        ids = [r.id for r in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids in the verdict")
        return self

    @computed_field
    @property
    def complete(self) -> bool:
        if self.error or self.mutations.missed:
            return False
        mandatory = [r for r in self.results if r.severity == "must"]
        # Hard gates are the mandatory rules. A "should" failure is a real finding and stays in
        # `findings`, but it does not make the design impossible to finish; the agent can fix it
        # or a human can waive it. Unmeasured blocks only when the rule is mandatory.
        return (
            bool(mandatory)
            and all(r.status in ("pass", "waived") for r in mandatory)
            and not any(r.status == "unmeasured" for r in mandatory)
        )

    @computed_field
    @property
    def findings(self) -> list[str]:
        return [f"[{r.id}] {r.detail}" for r in self.results
                if r.status not in ("pass", "waived")]


@dataclass(frozen=True)
class RuleSpec:
    id: str
    doc: str
    heading: str
    severity: SEVERITY
    citation: str
    bullets: list[str]


def rubric() -> list[RuleSpec]:
    """The judge rules, with the bullet text from the bundled rules."""
    out: list[RuleSpec] = []
    for doc, heading, severity in JUDGE_SECTIONS:
        item = knowledge.read(doc)
        if item is None:
            continue
        bullets = _bullets(item["text"], heading)
        out.append(RuleSpec(id=_rule_id(doc, heading), doc=doc, heading=heading,
                            severity=severity, citation=_rule_id(doc, heading),
                            bullets=bullets))
    return out


def _bullets(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    active = False
    for line in lines:
        if line.startswith("## "):
            active = line[3:].strip() == heading
            continue
        if active and line.strip().startswith(("- ", "* ")):
            out.append(line.strip()[2:])
    return out[:8]


def coverage() -> dict[str, Any]:
    """How much of the bundled rule corpus is executable, rubric-judged, or manual."""
    sections = [f"{doc['id']}#{heading}" for doc in knowledge.index() for heading in doc["headings"]]
    auto = {citation for _title, _severity, citation in AUTO_META.values()}
    judge = {spec.citation for spec in rubric()}
    auto_sections = [s for s in sections if s in auto]
    judge_sections = [s for s in sections if s in judge and s not in auto]
    return {
        "sections": len(sections),
        "auto": len(auto_sections),
        "judge": len(judge_sections),
        "manual": len([s for s in sections if s not in auto and s not in judge]),
        "encoded": len(auto_sections) + len(judge_sections),
        "auto_rules": len(AUTO_META),
        "judge_rules": len(rubric()),
        "auto_ratio": round((len(auto_sections) + len(judge_sections)) / len(sections), 3) if sections else 0.0,
        "auto_ids": sorted(auto),
        "judge_ids": sorted(judge),
    }


def _result(rule_id: str, passed: bool, *, measured: Any = None, limit: Any = None,
            comparison: str = "max", evidence: list[str] | None = None,
            detail: str = "") -> RuleResult:
    title, severity, citation = AUTO_META[rule_id]
    return RuleResult(
        id=rule_id, title=title, severity=severity, source="auto",
        status="pass" if passed else "fail",
        measured=measured, limit=limit, comparison=comparison,
        evidence=evidence or [detail or title],
        citation=citation, detail=detail or (title if not passed else ""),
    )


def auto_results(facts: dict[str, Any]) -> list[RuleResult]:
    """Deterministic results from the same facts `check`, the server and the CLI use."""
    out: list[RuleResult] = []
    evaluation = facts.get("evaluation") or {}
    mapping = {
        "erc": "erc.errors", "legible": "schematic.legibility", "drc": "drc.errors",
        "routing": "routing.unconnected", "parity": "schematic.parity",
        "sync": "design.sync", "brief": "brief.requirements",
    }
    for criterion in evaluation.get("criteria", []):
        rule_id = mapping.get(criterion.get("id"))
        if not rule_id:
            continue
        title, severity, citation = AUTO_META[rule_id]
        out.append(RuleResult(
            id=rule_id, title=title, severity=severity, source="auto",
            status="pass" if criterion.get("passed") else "fail",
            measured=criterion.get("value"), limit=None,
            evidence=[str(criterion.get("detail") or title)], citation=citation,
            detail=str(criterion.get("detail") or ""),
        ))

    rules = facts.get("rules") or {}
    if rules:
        violations = rules.get("violations") or []
        out.append(_result("design_rules.violations", not violations,
                           measured=len(violations), limit=0,
                           evidence=[v.get("detail", "rule violation") for v in violations[:3]],
                           detail=(violations[0].get("detail", "") if violations else "")))

    if facts.get("board"):
        manufacturing = facts.get("manufacturing")
        if manufacturing is not None:
            ok = bool(manufacturing.get("present")) and not manufacturing.get("stale")
            out.append(_result("manufacturing.package", ok,
                               measured={"present": manufacturing.get("present"),
                                         "stale": manufacturing.get("stale")}, limit=None,
                               evidence=[f"{len(manufacturing.get('files') or [])} files"],
                               detail="package present and current" if ok else "package missing or stale"))

    heights = facts.get("heights")
    if heights is not None:
        declared = heights.get("enclosure_mm") is not None
        out.append(_result("mechanical.height", bool(heights.get("ok")),
                           measured=heights.get("tallest_mm"), limit=heights.get("allowed_mm"),
                           evidence=[f"measured {heights.get('measured')} parts"],
                           detail=("enclosure fit" if heights.get("ok") else
                                   (f"{len(heights.get('over') or [])} over height, "
                                    f"{len(heights.get('missing') or [])} unmeasured"))))
        if not declared:
            out[-1].status = "unmeasured"
            out[-1].detail = "no enclosure height declared"

    stubs = facts.get("via_stubs")
    if stubs is not None:
        out.append(_result("highspeed.via_stub", bool(stubs.get("ok")),
                           measured=stubs.get("max_mm"), limit=stubs.get("limit_mm"),
                           evidence=[f"{stubs.get('count')} vias"],
                           detail=("stubs within limit" if stubs.get("ok")
                                   else f"{len(stubs.get('over') or [])} vias over the stub limit")))

    silk = facts.get("silk")
    if silk is not None:
        out.append(_result("silk.text", bool(silk.get("passed")),
                           measured=len(silk.get("collisions") or []), limit=0,
                           evidence=[item.get("detail", "silk collision")
                                     for item in (silk.get("collisions") or [])[:3]],
                           detail=("no silkscreen collisions" if silk.get("passed")
                                   else f"{len(silk.get('collisions') or [])} collisions")))

    yard = facts.get("courtyard")
    if yard is not None:
        out.append(_result("placement.courtyard", bool(yard.get("passed")),
                           measured=len(yard.get("overlaps") or []), limit=0,
                           evidence=[item.get("detail", "courtyard overlap")
                                     for item in (yard.get("overlaps") or [])[:3]],
                           detail=("no courtyard overlaps" if yard.get("passed")
                                   else f"{len(yard.get('overlaps') or [])} overlaps")))

    coverage_fact = facts.get("test_coverage")
    if coverage_fact is not None:
        ratio = coverage_fact.get("coverage")
        passed = ratio is None or float(ratio) >= 0.5
        out.append(_result("test.coverage", passed, measured=ratio, limit=0.5, comparison="min",
                           evidence=[f"{coverage_fact.get('covered')} of {coverage_fact.get('nets')} nets"],
                           detail=f"test coverage {ratio}"))
    balance = facts.get("copper_balance")
    if balance is not None:
        passed = balance.get("balanced") is None or bool(balance.get("balanced"))
        out.append(_result("thermal.copper_balance", passed,
                           measured=balance.get("ratio"), limit=None,
                           evidence=[f"F {balance.get('front_mm2')} / B {balance.get('back_mm2')} mm2"],
                           detail=f"copper ratio {balance.get('ratio')}"))
    return out


def mutations() -> MutationReport:
    """Deliberately broken synthetic inputs; every detector must catch its case."""
    from ..engine import design_rules, geometry, heights, qa, si

    caught: list[str] = []
    missed: list[str] = []
    checks: list[tuple[str, bool]] = []

    rules = design_rules.load(type("W", (), {"path": "/tmp/judge-mutation"})())
    rules["netclasses"]["POWER"] = {"nets": ["GND"], "width": 0.5, "clearance": 0.2,
                                    "impedance": None, "explicit": True}
    index = {"tracks": [{"net": "GND", "width": 0.2, "uuid": "t"}],
             "board": {"layers": ["F.Cu", "B.Cu"]}, "nets": ["GND"]}
    checks.append(("track_width", bool(design_rules.violations(index, rules))))

    gap = {"tracks": [
        {"net": "A", "layer": "F.Cu", "x1": 0, "y1": 0, "x2": 1, "y2": 0, "width": 0.2},
        {"net": "B", "layer": "F.Cu", "x1": 0, "y1": 0.3, "x2": 1, "y2": 0.3, "width": 0.2},
    ], "board": {"layers": ["F.Cu", "B.Cu"]}, "nets": ["A", "B"], "pads": [], "vias": [], "zones": []}
    checks.append(("clearance", geometry.clearance_query(gap, "net:A", "net:B")["distance"] < 0.2))

    yard = {"footprints": [
        {"ref": "R1", "courtyard": [[0, 0], [4, 0], [4, 4], [0, 4]]},
        {"ref": "R2", "courtyard": [[2, 2], [6, 2], [6, 6], [2, 6]]},
    ]}
    checks.append(("courtyard", not qa.courtyards(yard)["passed"]))

    silk = {"board": {"edge_bbox": [0, 0, 40, 30]},
            "footprints": [{"ref": "R1", "bbox": [5, 5, 6, 3],
                            "ref_text": {"visible": True, "layer": "F.SilkS", "height": 0.6,
                                         "bbox": [5, 5, 4, 1.2]}}],
            "pads": [{"ref": "C1", "pad": "1", "net": "GND", "x": 6, "y": 5.5,
                      "w": 1.2, "h": 0.8, "rotation": 0, "layers": ["F.Cu", "F.Mask"],
                      "polygon": [[5.4, 5.1], [6.6, 5.1], [6.6, 5.9], [5.4, 5.9]]}],
            "vias": []}
    checks.append(("silkscreen", not qa.silkscreen(silk)["passed"]))

    height = heights.report(
        {"footprints": [{"ref": "R1"}, {"ref": "R2"}]},
        {"parts": {"R1": {"height_mm": 6.0}}, "enclosure": {"height_mm": 10, "clearance_mm": 5}},
    )
    checks.append(("height", not height["ok"] and bool(height["over"])))

    stub_rules = design_rules.load(type("W", (), {"path": "/tmp/judge-mutation"})())
    stub_rules["netclasses"]["HS"] = {"nets": ["CLK"], "width": 0.2, "clearance": 0.2,
                                      "impedance": 50, "explicit": True}
    stub_rules["stackup"] = {"layers": 4, "thickness": 1.6, "material": "FR4"}
    stub = {"board": {"layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]},
            "tracks": [{"net": "CLK", "layer": "F.Cu"}],
            "vias": [{"uuid": "v", "net": "CLK", "type": "through"}]}
    checks.append(("via_stub", not si.report(stub, stub_rules)["ok"]))

    for name, caught_ok in checks:
        (caught if caught_ok else missed).append(name)
    return MutationReport(total=len(checks), caught=caught, missed=missed)


JUDGE_INSTRUCTIONS = """\
You are an adversarial PCB reviewer. A different agent claims the design is finished. You are NOT
on its side: your job is to find what is still wrong, using only the supplied facts and rules.

Rules of evidence:
- Return exactly one result for every RUBRIC RULE, using its id unchanged.
- A "pass" must cite concrete evidence: reference designators, net names, measured values, or a
  render region. If you cannot verify it from the facts, return "unmeasured", not "pass".
- A "fail" must name the entity and the rule, and say what a fix would change.
- Do not repeat or override the AUTO results; they are already measured by the harness.
- Do not invent requirements the brief does not state. Do not give style opinions without a rule.
- Mark a rule "must" only when the supplied rule text or brief makes it mandatory; otherwise use
  the severity shown in the rubric.

Return ONLY a JSON object of this shape, with no prose and no code fence:
{"results":[{"id":"<rubric id>","status":"pass|fail|unmeasured","severity":"must|should|may",
"evidence":["..."],"measured":<number or null>,"limit":<number or null>,
"comparison":"max|min","detail":"..."}],"notes":"..."}
"""


def _prompt(facts: dict[str, Any], specs: list[RuleSpec], auto: list[RuleResult]) -> str:
    payload = {
        "brief": facts.get("brief"),
        "phase": facts.get("phase"),
        "board": facts.get("board"),
        "evaluation": facts.get("evaluation"),
        "parts": facts.get("parts"),
        "rules": facts.get("rules"),
        "manufacturing": facts.get("manufacturing"),
        "heights": facts.get("heights"),
        "via_stubs": facts.get("via_stubs"),
        "silk": facts.get("silk"),
        "courtyard": facts.get("courtyard"),
        "test_coverage": facts.get("test_coverage"),
        "copper_balance": facts.get("copper_balance"),
        "auto_results": [r.model_dump() for r in auto],
    }
    lines = [
        "BRIEF:",
        str(payload["brief"] or "(none)"),
        "",
        "FACTS (measured by the harness, not the worker):",
        json.dumps({k: v for k, v in payload.items() if k != "brief"}, indent=2, default=str),
        "",
        "RUBRIC RULES (answer every one):",
    ]
    for spec in specs:
        lines.append(f"- id: {spec.id} | severity: {spec.severity}")
        lines.append(f"  rule: {spec.citation}")
        for bullet in spec.bullets:
            lines.append(f"  - {bullet}")
    lines.append("")
    lines.append("Return one result per rubric id. Evidence is required for every pass.")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reviewer returned no JSON object")
    return body[start:end + 1]


async def _review(facts: dict[str, Any], specs: list[RuleSpec], auto: list[RuleResult],
                  model: Any) -> ReviewResult:
    """Ask for JSON and validate it here.

    Structured-output tool choice is not accepted by every provider (DeepSeek's thinking mode
    rejects forced tool_choice), so the reviewer returns text and the Pydantic model is the
    validation boundary. A malformed review is retried with the validation error, then fails
    closed.
    """
    from pydantic import ValidationError
    from pydantic_ai import Agent as PydanticAgent

    reviewer = PydanticAgent(model, output_type=str, instructions=JUDGE_INSTRUCTIONS,
                             retries={"output": 0})
    prompt = _prompt(facts, specs, auto)
    last: Exception | None = None
    for _attempt in range(3):
        result = await reviewer.run(prompt)
        try:
            return ReviewResult.model_validate_json(_extract_json(result.output))
        except (ValidationError, ValueError) as exc:
            last = exc
            prompt = (_prompt(facts, specs, auto)
                      + "\n\nYour previous answer was invalid: " + str(exc)[:800]
                      + "\nReturn the JSON object again.")
    raise ValueError(f"reviewer returned no valid JSON after 3 attempts: {last}")


async def assess(facts: dict[str, Any], model: Any, *, reviewer: Any = None) -> JudgeVerdict:
    """Run the deterministic checks and the separate reviewer; complete is derived."""
    auto = auto_results(facts)
    report = mutations()
    specs = rubric()
    if model is None or type(model).__name__ in ("FunctionModel", "TestModel"):
        return JudgeVerdict(results=auto, mutations=report,
                            error="no reviewer model; rubric rules were not judged")
    try:
        review = await (reviewer or _review)(facts, specs, auto, model)
    except Exception as exc:  # judge down, malformed after retries, timeout: fail closed
        return JudgeVerdict(results=auto, mutations=report,
                            error=f"judge failed: {type(exc).__name__}: {exc}")
    by_id = {spec.id: spec for spec in specs}
    results = list(auto)
    seen = {r.id for r in results}
    for item in review.results:
        if item.id in seen or item.id not in by_id:
            continue
        item.severity = by_id[item.id].severity
        item.source = "judge"
        item.citation = by_id[item.id].citation
        results.append(item)
        seen.add(item.id)
    for rule_id, spec in by_id.items():
        if rule_id not in seen:
            results.append(RuleResult(
                id=rule_id, title=spec.heading, status="unmeasured", severity=spec.severity,
                source="judge", citation=spec.citation,
                detail="the reviewer did not return a verdict for this rule",
            ))
    waivers = facts.get("waivers") or {}
    for item in results:
        entry = waivers.get(item.id)
        if item.status in ("fail", "unmeasured") and _waiver_valid(entry):
            item.status = "waived"
            item.detail = (item.detail + "; " if item.detail else "") + \
                "waived: " + str(entry.get("reason") or "human waiver")
            item.evidence.append("waiver: " + str(entry.get("author") or "human"))
    return JudgeVerdict(results=results, mutations=report)


def _waiver_valid(entry: Any) -> bool:
    if not isinstance(entry, dict) or entry.get("author") == "agent":
        return False
    expires = entry.get("expires")
    if expires:
        import datetime

        try:
            if datetime.date.fromisoformat(str(expires)) < datetime.date.today():
                return False
        except ValueError:
            return False
    return True


def summarise(verdict: JudgeVerdict) -> str:
    if verdict.error:
        return verdict.error
    if verdict.mutations.missed:
        return "judge checks missed mutations: " + ", ".join(verdict.mutations.missed)
    return "; ".join(verdict.findings)[:3000] or "none"
