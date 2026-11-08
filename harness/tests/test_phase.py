"""The schematic phase end to end: a prompt-created design, drawn, then handed to layout.

Driven through the real agent tools with a scripted model, against real KiCad, so this covers
the tools, the workspace phases and the schematic→board transition together.
"""

from __future__ import annotations

import re

import pytest

from kicad_harness.agent.agent import HarnessDeps, Services, build_agent
from kicad_harness.agent.state import DesignState
from kicad_harness.config import Config
from kicad_harness.store.workspace import Workspace
from tests.test_agent import Script

ARE_PACKAGE = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
R_PACKAGE = "Resistor_SMD:R_0805_2012Metric"
C_PACKAGE = "Capacitor_SMD:C_0805_2012Metric"


def _footprints_available(config: Config) -> bool:
    return any((dir / "Resistor_SMD.pretty").is_dir() for dir in (config.footprint_dirs or []))


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    config = Config(home=tmp_path_factory.mktemp("home")).ensure_dirs()
    svc = Services.create(config)
    yield svc
    close = getattr(svc.engine, "close", None)
    if close:
        close()


@pytest.fixture
def new_project(services, request):
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)[:50]
    ws = Workspace.create_schematic(services.config, name, "A 555 LED blinker run from 5 V.")
    return name, ws


def deps_for(services, name, **state) -> HarnessDeps:
    return HarnessDeps(state=DesignState(design=name, phase="schematic", **state), services=services, run_id="t")


def test_prompt_project_starts_in_the_schematic_phase(services, new_project):
    name, ws = new_project
    assert ws.phase() == "schematic"
    assert ws.brief() == "A 555 LED blinker run from 5 V."
    assert ws.schematic_path.is_file()
    assert not ws.board_path.exists()
    goal = ws.goal()
    assert {c["type"] for c in goal["criteria"]} == {
        "unconnected_pins", "erc_errors", "board_laid_out", "schematic_parity",
    }
    # The brief reaches the agent through the goal summary.
    assert "555" in goal["summary"]


async def test_agent_draws_and_then_hands_off_to_layout(services, new_project):
    if not _footprints_available(services.config):
        pytest.skip("no KiCad footprint libraries on this machine")
    name, ws = new_project
    part = {"library": "Device", "symbol": "R", "value": "10k", "footprint": R_PACKAGE}
    # A complete divider: every pin lands on a net, so ERC is clean and the handoff can happen.
    nets = [
        {"name": "VIN", "pins": ["R1.1", "C1.1"]},
        {"name": "VOUT", "pins": ["R1.2", "R2.1", "C2.1"]},
        {"name": "GND", "pins": ["R2.2", "C1.2", "C2.2"]},
    ]
    script = Script([
        [("get_design", {})],
        [
            (
                "generate_schematic",
                {
                    "parts": [
                        {"ref": "R1", **part},
                        {"ref": "R2", **part},
                        {"ref": "C1", "library": "Device", "symbol": "C", "value": "100n", "footprint": C_PACKAGE},
                        {"ref": "C2", "library": "Device", "symbol": "C", "value": "100n", "footprint": C_PACKAGE},
                    ],
                    "nets": nets,
                },
            )
        ],
        [("check", {})],
        [("start_board", {})],
        "done",
    ])
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("draw it", deps=deps)
    assert result.output == "done"

    # The schematic holds every part, and the board came from it.
    returns = [
        p
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if type(p).__name__ in ("ToolReturnPart", "RetryPromptPart")
    ]
    detail = [
        (getattr(p, "tool_name", ""), str(getattr(p, "content", ""))[:300]) for p in returns
    ]
    assert ws.phase() == "board", detail
    assert ws.board_path.is_file()
    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    assert {p.reference for p in doc.placements() if not p.reference.startswith("#")} == {
        "R1", "R2", "C1", "C2",
    }

    board_hash, _ = services.artifacts.materialize(ws.board_path.read_bytes())
    index = services.artifacts.index(board_hash)
    assert {"R1", "R2", "C1", "C2"} <= {f["ref"] for f in index["footprints"]}
    # Nets named in the schematic exist on the board.
    assert {"VIN", "VOUT", "GND"} <= set(n for n in index.get("nets", []) if n)
    assert deps.state.phase == "board"


async def test_board_tools_refuse_until_there_is_a_board(services, new_project):
    name, ws = new_project
    script = Script([[("move_footprint", {"ref": "R1", "x": 10.0, "y": 10.0})], "done"])
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("move it", deps=deps)
    # The model was told to stay in the schematic phase; nothing was committed.
    assert result.output == "done"
    assert ws.phase() == "schematic"
    assert not ws.board_path.exists()
    assert len(ws.log()) == 1  # only the creation commit


async def test_schematic_tools_edit_and_commit(services, new_project):
    name, ws = new_project
    script = Script(
        [
            [("add_symbol", {"symbol": "Device:R", "ref": "R1", "x": 100.0, "y": 100.0})],
            [("add_symbol", {"symbol": "Device:R", "ref": "R2", "x": 120.0, "y": 100.0})],
            [("connect_pins", {"a": "R1.1", "b": "R2.1", "net": "MID"})],
            [("label_net", {"target": "110.0,100.0", "net": "GND"})],
            "done",
        ]
    )
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        await agent.run("draw two resistors", deps=deps)

    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    assert {p.reference for p in doc.placements()} == {"R1", "R2"}
    assert "MID" in doc.nets()
    # Each edit is its own version.
    assert len(ws.log()) == 5
    assert deps.state.schematic_hash


async def test_schematic_edits_are_allowed_after_the_board_exists(services, new_project):
    """The schematic is a view of the design, so layout must not lock it."""
    name, ws = new_project
    ws.set_phase("board")
    script = Script([[("add_symbol", {"symbol": "Device:R", "ref": "R9", "x": 10.0, "y": 10.0})], "done"])
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        await agent.run("add one", deps=deps)

    from kicad_harness.engine.schematic import Schematic

    assert [p.reference for p in Schematic.load(ws.schematic_path).placements()] == ["R9"]


async def test_skidl_failure_returns_a_readable_error(services, new_project):
    """A bad symbol must come back as tool data with suggestions, not crash the run."""
    name, ws = new_project
    script = Script(
        [
            [
                (
                    "generate_schematic",
                    {
                        "parts": [{"ref": "U1", "library": "Timer", "symbol": "NE555NOTREAL", "value": "x"}],
                        "nets": [],
                    },
                )
            ],
            "done",
        ]
    )
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("try", deps=deps)
    assert result.output == "done"

    returns = [
        p for m in result.all_messages() for p in getattr(m, "parts", []) if type(p).__name__ == "ToolReturnPart"
    ]
    errors = [r for r in returns if isinstance(r.content, dict) and r.content.get("ok") is False]
    assert errors, returns
    message = errors[0].content["error"]
    assert "Unable to find part" in message
    assert "NE555P" in message  # the suggestion the model needs to fix it
    assert ws.phase() == "schematic"


async def test_unreadable_schematic_is_reported_not_raised(services, new_project):
    """A half-written schematic must come back as data, not exhaust the tool-retry budget."""
    name, ws = new_project
    ws.schematic_path.write_text("(kicad_sch (version 20250114)", encoding="utf-8")  # truncated
    script = Script([[("get_design", {})], "done"])
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("look", deps=deps)
    assert result.output == "done"

    returns = [
        p for m in result.all_messages() for p in getattr(m, "parts", []) if type(p).__name__ == "ToolReturnPart"
    ]
    assert returns, "the tool should have returned"
    payload = returns[0].content
    assert isinstance(payload, dict) and payload.get("ok") is False
    assert "could not be read" in payload["error"]
    assert ws.phase() == "schematic"


async def test_netless_regenerate_does_not_destroy_existing_wiring(services, new_project):
    """A skidl run with no nets must leave hand-drawn wires alone (pi-relay lost 20 this way)."""
    name, ws = new_project
    from kicad_harness.engine.schematic import Schematic

    script = Script(
        [
            [("add_symbol", {"symbol": "Device:R", "ref": "R1", "x": 100.0, "y": 100.0})],
            [("add_symbol", {"symbol": "Device:R", "ref": "R2", "x": 130.0, "y": 100.0})],
            [("connect_pins", {"a": "R1.2", "b": "R2.1", "net": "MID"})],
            [
                (
                    "generate_schematic",
                    {"parts": [{"ref": "R9", "library": "Device", "symbol": "R", "value": "1k"}], "nets": []},
                )
            ],
            "done",
        ]
    )
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        await agent.run("wire then regenerate", deps=deps)

    doc = Schematic.load(ws.schematic_path)
    assert len(doc.wires) == 3, "the existing wires must survive a refused regeneration"
    assert {"R1", "R2"} == {p.reference for p in doc.placements()}
    assert "MID" in doc.nets()


async def test_connect_pins_joins_the_pins_it_names(services, new_project):
    """Regression: pin geometry had the Y axis inverted, so wires landed on the opposite pin."""
    name, ws = new_project
    script = Script(
        [
            [("add_symbol", {"symbol": "Device:R", "ref": "R1", "x": 100.0, "y": 100.0})],
            [("add_symbol", {"symbol": "Device:R", "ref": "R2", "x": 130.0, "y": 100.0})],
            [("connect_pins", {"a": "R1.2", "b": "R2.1", "net": "MID"})],
            "done",
        ]
    )
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        await agent.run("wire", deps=deps)

    from kicad_harness.engine import kicad_cli

    netlist = kicad_cli.parse_netlist(
        kicad_cli.export_netlist(ws.schematic_path, ws.path / "n.net").read_text()
    )
    # KiCad prefixes local nets with the sheet path ("/MID").
    mid = next((nodes for name, nodes in netlist["nets"].items() if name.lstrip("/") == "MID"), [])
    members = {(ref, pin) for ref, pin in mid}
    assert members == {("R1", "2"), ("R2", "1")}, netlist["nets"]


async def test_no_argument_tools_tolerate_stray_arguments(services, new_project):
    """Regression: models send placeholders like {"_": null} to no-arg tools.

    Rejecting them was a validation error, so the model retried the same call until the tool
    retry cap blew and the whole run died with "exceeded max retries count of 5".
    """
    name, ws = new_project
    script = Script(
        [
            [("get_design", {"_": None})],
            [("check", {})],
            [("get_design", {"junk": 1})],
            "done",
        ]
    )
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("look at it", deps=deps)
    assert result.output == "done"

    returns = [
        p for m in result.all_messages() for p in getattr(m, "parts", []) if type(p).__name__ == "ToolReturnPart"
    ]
    assert len(returns) == 3, [getattr(r, "tool_name", "") for r in returns]
    # get_design answers in the schematic phase instead of failing on the missing board.
    assert all(isinstance(r.content, (dict, list)) for r in returns), returns


async def test_sse_probes_are_shared_between_subscribers():
    """Many open tabs must not multiply live-state probes (they starved the tool thread pool)."""
    import asyncio
    import time

    from kicad_harness.server import SseFanout

    calls = {"n": 0}

    def probe() -> dict:
        calls["n"] += 1
        return {"n": calls["n"]}

    fan = SseFanout(ttl=0.4)

    async def client() -> None:
        for _ in range(5):
            await fan.get("design", probe)
            await asyncio.sleep(0.4)

    await asyncio.gather(*[client() for _ in range(6)])
    # 6 subscribers x 5 polls = 30 requests; sharing keeps it near one per ttl window.
    assert calls["n"] < 12, f"{calls['n']} probes executed for 30 subscriber polls"
