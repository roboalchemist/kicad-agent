"""Golden end-to-end flow on a bundled four-part design.

Draw the schematic, make it legible, route it, hand it to layout, route the board and write the
manufacturing package. Nothing here reads the live pi-relay workspace, so it runs anywhere the
KiCad libraries exist and fails loudly if any step regresses.
"""

from __future__ import annotations

import pytest

from kicad_harness.agent.agent import build_agent
from tests.test_agent import Script
from tests.test_phase import (  # noqa: F401  (pytest fixtures)
    C_PACKAGE,
    R_PACKAGE,
    _footprints_available,
    deps_for,
    new_project,
    services,
)


async def test_draw_tidy_route_board_and_manufacture(services, new_project) -> None:
    if not _footprints_available(services.config):
        pytest.skip("no KiCad footprint libraries on this machine")
    name, ws = new_project
    resistor = {"library": "Device", "symbol": "R", "value": "10k", "footprint": R_PACKAGE}
    capacitor = {"library": "Device", "symbol": "C", "value": "100n", "footprint": C_PACKAGE}
    parts = [
        {"ref": "R1", **resistor},
        {"ref": "R2", **resistor},
        {"ref": "C1", **capacitor},
        {"ref": "C2", **capacitor},
    ]
    nets = [
        {"name": "VIN", "pins": ["R1.1", "C1.1"]},
        {"name": "VOUT", "pins": ["R1.2", "R2.1", "C2.1"]},
        {"name": "GND", "pins": ["R2.2", "C1.2", "C2.2"]},
    ]
    script = Script([
        [("generate_schematic", {"parts": parts, "nets": nets})],
        [("check", {})],
        [("tidy_schematic", {})],
        [("route_schematic", {})],
        [("check", {})],
        [("start_board", {})],
        [("set_board_outline", {"width": 200.0, "height": 150.0, "x": 0.0, "y": 0.0})],
        "done",
    ])
    agent = build_agent()
    with agent.override(model=script.model()):
        result = await agent.run("build the divider end to end", deps=deps_for(services, name))
    assert result.output == "done"

    from kicad_harness.engine import manufacturing
    from kicad_harness.engine.schematic import Schematic

    # Schematic: every part and net made it, and tidying did not break connectivity.
    from pydantic_ai.messages import ToolReturnPart

    checks = []
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "check":
                payload = part.content[0] if isinstance(part.content, list) else part.content
                if isinstance(payload, dict):
                    checks.append(payload)
    assert checks, "check never ran"
    assert checks[-1]["erc"]["errors"] == 0, checks[-1]["erc"]
    assert checks[-1]["layout"]["score"] == 0, checks[-1]["layout"]["counts"]

    doc = Schematic.load(ws.schematic_path)
    assert {p.reference for p in doc.placements() if not p.reference.startswith("#")} == {
        "R1", "R2", "C1", "C2",
    }
    assert {"VIN", "VOUT", "GND"} <= set(doc.nets())

    # Board: created from the schematic.
    assert ws.phase() == "board" and ws.board_path.is_file()
    board_hash, _ = services.artifacts.materialize(ws.board_path.read_bytes())
    index = services.artifacts.index(board_hash)
    assert {"R1", "R2", "C1", "C2"} <= {f["ref"] for f in index["footprints"]}
    # Parity: the same pins share the same nets on board and schematic.
    board_nets: dict[str, set[tuple[str, str]]] = {}
    for pad in index["pads"]:
        net = (pad.get("net") or "").lstrip("/")
        if net and not str(pad.get("ref", "")).startswith("#"):
            board_nets.setdefault(net, set()).add((pad["ref"], str(pad["pad"])))
    schematic_nets = {name: {(m["ref"], str(m["pin"])) for m in members}
                      for name, members in doc.nets().items()}
    for net in ("VIN", "VOUT", "GND"):
        assert board_nets.get(net) == schematic_nets.get(net), (net, board_nets.get(net),
                                                                schematic_nets.get(net))

    # Route one board connection with the track tool (Freerouting is an optional vendor extra,
    # so the golden flow uses the deterministic router every deployment has).
    pads = {(p["ref"], str(p["pad"])): p for p in index["pads"]}
    first, second = pads[("C1", "1")], pads[("R1", "1")]
    net = first["net"]
    route = Script([
        [("add_track", {"net": net,
                        "points": [[first["x"], first["y"]], [second["x"], second["y"]]],
                        "layer": "F.Cu"})],
        [("check", {})],
        [("export_manufacturing", {})],
        "done",
    ])
    with agent.override(model=route.model()):
        await agent.run("route and export", deps=deps_for(services, name))
    board_hash, _ = services.artifacts.materialize(ws.board_path.read_bytes())
    index = services.artifacts.index(board_hash)
    assert index.get("tracks"), "add_track wrote no track"

    # Manufacturing: an orderable package with copper, drill and a BOM.
    files = set(manufacturing.status(ws)["files"])
    assert any(name.endswith((".gtl", ".gbl")) for name in files), files
    assert any(name.endswith(".drl") for name in files), files
    assert "bom.csv" in files and "positions.csv" in files

    # Every result the model saw was one envelope: {ok, data|error, version}.
    from pydantic_ai.messages import ToolReturnPart

    returns = [p for m in result.all_messages() for p in getattr(m, "parts", [])
               if isinstance(p, ToolReturnPart)]
    payloads = [r.content[0] if isinstance(r.content, list) else r.content for r in returns]
    assert payloads and all(isinstance(p, dict) and "ok" in p and "data" in p and "version" in p
                            for p in payloads), payloads[:2]


async def test_place_test_points_keeps_parity(services, new_project) -> None:
    """The test-point tool must add the symbol and the footprint in one edit."""
    if not _footprints_available(services.config):
        pytest.skip("no KiCad footprint libraries on this machine")
    name, ws = new_project
    resistor = {"library": "Device", "symbol": "R", "value": "10k", "footprint": R_PACKAGE}
    capacitor = {"library": "Device", "symbol": "C", "value": "100n", "footprint": C_PACKAGE}
    script = Script([
        [("generate_schematic", {
            "parts": [{"ref": "R1", **resistor}, {"ref": "R2", **resistor},
                      {"ref": "C1", **capacitor}, {"ref": "C2", **capacitor}],
            "nets": [{"name": "VIN", "pins": ["R1.1", "C1.1"]},
                     {"name": "VOUT", "pins": ["R1.2", "R2.1", "C2.1"]},
                     {"name": "GND", "pins": ["R2.2", "C1.2", "C2.2"]}],
        })],
        [("start_board", {})],
        [("place_test_points", {"nets": ["GND"]})],
        "done",
    ])
    agent = build_agent()
    with agent.override(model=script.model()):
        result = await agent.run("add a ground test point", deps=deps_for(services, name))
    from pydantic_ai.messages import ToolReturnPart

    assert result.output == "done"

    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    refs = {p.reference for p in doc.placements() if p.reference.startswith("TP")}
    assert refs, "no test point symbol was added"
    board_hash, _ = services.artifacts.materialize(ws.board_path.read_bytes())
    index = services.artifacts.index(board_hash)
    assert refs <= {f["ref"] for f in index["footprints"]}, index["footprints"]
    pads = [p for p in index["pads"] if p["ref"] in refs]
    assert pads and any((p.get("net") or "").lstrip("/") == "GND" for p in pads), pads
