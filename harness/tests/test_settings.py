"""Global settings payload: the tool catalogue, the phase workflows and the guidance."""

from __future__ import annotations

import httpx

from kicad_harness.server import create_app
from tests.test_phase import services  # noqa: F401  (pytest fixture)


def _client(services) -> httpx.AsyncClient:
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    app.state.stop_flags = {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_settings_lists_every_tool_with_a_level_and_guidance(services) -> None:
    async with _client(services) as client:
        response = await client.get("/api/settings")
    assert response.status_code == 200, response.text
    body = response.json()
    levels = {item["id"] for item in body["levels"]}
    assert {"global", "bom", "schematic", "board"} <= levels
    tools = body["tools"]
    assert len(tools) >= 30, f"only {len(tools)} tools listed"
    for tool in tools:
        assert tool["name"]
        assert tool["level"] in levels
        assert tool["summary"], tool["name"]
        assert tool["guidance"], tool["name"]


async def test_settings_has_the_three_phase_workflows_and_guidance(services) -> None:
    async with _client(services) as client:
        body = (await client.get("/api/settings")).json()
    for phase in ("bom", "schematic", "board"):
        assert body["workflows"][phase], phase
        assert body["workflows"][phase][0]["step"]
        assert body["workflows"][phase][0]["detail"]
        assert len(body["guidance"][phase]) > 100, phase


def test_catalogue_does_not_drop_an_unknown_tool() -> None:
    from kicad_harness.agent import tool_catalog

    listed = tool_catalog.describe_tools({"brand_new_tool": "Does a new thing."})
    names = {tool["name"] for tool in listed}
    assert "brand_new_tool" in names
    assert "route_schematic" in names  # a known tool, one of the 49


def test_phase_instructions_carry_the_workflow_and_standard() -> None:
    from kicad_harness.agent import tool_catalog

    schematic = tool_catalog.phase_instructions("schematic")
    assert "Sourcing workflow" in schematic
    assert "Schematic workflow" in schematic
    assert "Arrange by circuit" in schematic
    assert "gate: ERC errors 0" in schematic
    assert "pcb-placement#Grouping" in schematic
    assert tool_catalog.GUIDANCE["schematic"] in schematic

    board = tool_catalog.phase_instructions("board")
    assert "Board workflow" in board
    assert "Outline, holes & keepouts" in board
    assert "gate: DRC 0" in board
    assert "pcb-mechanical#Board Outline" in board
    assert tool_catalog.GUIDANCE["board"] in board
    assert "Schematic workflow" not in board


def test_tool_descriptions_inherit_the_catalogue_guidance() -> None:
    """The model sees the "when to use" line, not only the Settings page."""
    from kicad_harness.agent.agent import build_agent

    agent = build_agent()
    tool = agent._function_toolset.tools["route_schematic"]
    assert "When to use:" in (tool.description or "")
    assert "ERC-checked" in (tool.function_schema.description or "")


def test_knowledge_digest_is_sourced_and_cited() -> None:
    from kicad_harness.agent import knowledge

    assert len(knowledge.index()) >= 40
    board = knowledge.digest("board")
    assert ".agents/rules/" in board, "the playbook must cite the source documents"
    assert "45-degree" in board or "decoupling" in board
    doc = knowledge.read("pcb-routing")
    assert doc and doc["source"].endswith("pcb-routing.md")
    assert knowledge.read("routing"), "reference names resolve too"
    assert knowledge.read("no-such-topic") is None


async def test_settings_include_playbooks_and_the_reference_library(services) -> None:
    async with _client(services) as client:
        body = (await client.get("/api/settings")).json()
    assert len(body["references"]) >= 40
    for phase in ("bom", "schematic", "board"):
        assert len(body["playbooks"][phase]) > 500, phase
        assert ".agents/" in body["playbooks"][phase], phase
    assert "read_reference" in {tool["name"] for tool in body["tools"]}
    assert body["rules"]["sections"] > 400 and body["rules"]["auto"] > 0


def test_bundled_knowledge_matches_the_repo_rules() -> None:
    """Drift guard: the deployed knowledge must be the repo's .agents/rules, byte for byte."""
    from pathlib import Path

    from kicad_harness.agent import knowledge

    repo_rules = Path(__file__).resolve().parents[2] / ".agents" / "rules"
    if not repo_rules.is_dir():
        import pytest
        pytest.skip("repo rules not present in this deployment")
    for source in repo_rules.glob("*.md"):
        bundled = knowledge.RULES / source.name
        assert bundled.is_file(), source.name
        assert bundled.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), source.name


def test_every_workflow_step_cites_a_real_rule_section() -> None:
    from kicad_harness.agent import tool_catalog

    assert tool_catalog.workflow_issues() == []
    for phase, steps in tool_catalog.WORKFLOWS.items():
        assert len(steps) >= 6, phase
        for step in steps:
            assert step["tools"], step["step"]
            assert step["gate"], step["step"]
            assert "#" in step["ref"], step["step"]


def test_tool_surface_uses_one_verb_object_name_per_action() -> None:
    """The naming convention: one `<verb>_<object>` name, no phase-prefixed variants."""
    import re

    from kicad_harness.agent import tool_catalog
    from kicad_harness.agent.agent import build_agent

    names = set(build_agent()._function_toolset.tools)
    assert names == set(tool_catalog.TOOLS), (
        sorted(names - set(tool_catalog.TOOLS)), sorted(set(tool_catalog.TOOLS) - names))
    verbs = {
        "add", "autoroute", "check", "connect", "delete", "find", "flip", "generate", "get",
        "label", "list", "mark", "measure", "move", "query", "read", "refill", "render",
        "reply", "restore", "rotate", "route", "set", "start", "tidy", "export", "diff",
        "place", "match", "import", "inspect", "probe", "silkscreen", "courtyard",
    }
    for name in names:
        assert re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", name), name
        assert name.split("_")[0] in verbs, f"{name}: verb outside the convention"


def test_phase_playbook_cites_the_rules() -> None:
    from kicad_harness.agent import knowledge

    assert len(knowledge.index()) >= 53
    assert ".agents/rules/" in knowledge.digest("board")
