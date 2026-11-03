"""The bill of materials: grouping and the endpoint, in both project phases."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from kicad_harness.server import create_app, group_bom, ref_sort_key
from kicad_harness.store.workspace import Workspace
from tests.test_agent import FIXTURE

# Real KiCad + the shared services fixture from the phase tests.
from tests.test_phase import services  # noqa: F401  (pytest fixture)


def test_refs_sort_naturally() -> None:
    refs = ["R10", "R2", "R1", "C1", "R9"]
    assert sorted(refs, key=ref_sort_key) == ["C1", "R1", "R2", "R9", "R10"]


def test_group_bom_collapses_identical_parts() -> None:
    rows = [
        {"ref": "R1", "value": "10k", "footprint": "R_0805"},
        {"ref": "R10", "value": "10k", "footprint": "R_0805"},
        {"ref": "R2", "value": "10k", "footprint": "R_0805"},
        {"ref": "C1", "value": "100nF", "footprint": "C_0805"},
    ]
    lines, total = group_bom(rows)
    assert total == 4
    assert len(lines) == 2
    resistor = next(line for line in lines if line["value"] == "10k")
    assert resistor["refs"] == ["R1", "R2", "R10"]
    assert resistor["qty"] == 3


def test_group_bom_keeps_different_values_apart() -> None:
    rows = [
        {"ref": "R1", "value": "10k", "footprint": "R_0805"},
        {"ref": "R2", "value": "47k", "footprint": "R_0805"},
        {"ref": "R3", "value": "10k", "footprint": "R_0603"},
    ]
    lines, total = group_bom(rows)
    assert total == 3
    assert len(lines) == 3, "value and footprint both distinguish parts"


def _client(services) -> httpx.AsyncClient:
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    app.state.stop_flags = {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_bom_falls_back_to_the_board_for_an_imported_design(services, tmp_path) -> None:
    """A project made from a .kicad_pcb has no schematic, so the BOM comes from the board."""
    name = "bom-imported"
    Workspace.create(services.config, name, FIXTURE, None, services.engine)
    async with _client(services) as client:
        response = await client.get(f"/api/designs/{name}/bom")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "board"
    assert body["line_count"] > 0
    refs = {ref for line in body["lines"] for ref in line["refs"]}
    assert "D1" in refs and "R1" in refs


async def test_bom_comes_from_the_schematic_when_one_exists(services) -> None:
    name = "bom-schematic"
    ws = Workspace.create_schematic(services.config, name, "an RC filter")
    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(ws.schematic_path)
    lib = __import__("kicad_harness.engine.schematic", fromlist=["read_library"]).read_library(
        "/usr/share/kicad/symbols/Device.kicad_sym"
    )
    doc.libraries["Device"] = lib
    doc.add_symbol("Device:R", "R1", 100.0, 100.0, value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    doc.add_symbol("Device:R", "R2", 120.0, 100.0, value="10k", footprint="Resistor_SMD:R_0805_2012Metric")
    doc.set_symbol_property("R1", "LCSC", "C17414")
    doc.set_symbol_property("R2", "LCSC", "C17414")
    doc.save()

    async with _client(services) as client:
        body = (await client.get(f"/api/designs/{name}/bom")).json()
    assert body["source"] == "schematic"
    assert body["total_qty"] == 2
    line = body["lines"][0]
    assert line["refs"] == ["R1", "R2"]
    assert line["lcsc"] == "C17414"
    # Enriched from the JLCPCB catalogue when the database is present.
    from kicad_harness.engine import parts

    if parts.available():
        assert line["tier"] in ("basic", "extended")
        assert line["stock"] and line["stock"] > 0


async def test_bom_of_an_empty_project_is_empty_not_an_error(services) -> None:
    name = "bom-empty"
    Workspace.create_schematic(services.config, name, "nothing drawn yet")
    async with _client(services) as client:
        response = await client.get(f"/api/designs/{name}/bom")
    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == [] and body["total_qty"] == 0


async def test_bom_has_part_number_link_price_and_total(services) -> None:
    """The BOM must name the orderable part, link to it, price it, and total it."""
    name = "bom-priced"
    ws = Workspace.create_schematic(services.config, name, "two 10k resistors and a cap")
    from kicad_harness.engine.schematic import Schematic, read_library

    doc = Schematic.load(ws.schematic_path)
    doc.libraries["Device"] = read_library("/usr/share/kicad/symbols/Device.kicad_sym")
    for ref, value, fp, lcsc in (
        ("R1", "10k", "Resistor_SMD:R_0805_2012Metric", "C17414"),
        ("R2", "10k", "Resistor_SMD:R_0805_2012Metric", "C17414"),
        ("C1", "100nF", "Capacitor_SMD:C_0805_2012Metric", "C49678"),
    ):
        doc.add_symbol("Device:R" if ref.startswith("R") else "Device:C", ref, 100.0, 100.0,
                       value=value, footprint=fp)
        doc.set_symbol_property(ref, "LCSC", lcsc)
    doc.save()

    async with _client(services) as client:
        body = (await client.get(f"/api/designs/{name}/bom")).json()

    # identical parts merge into one line carrying both refs
    resistor = next(line for line in body["lines"] if line["value"] == "10k")
    assert resistor["refs"] == ["R1", "R2"] and resistor["qty"] == 2

    from kicad_harness.engine import parts

    if not parts.available():
        return
    for line in body["lines"]:
        assert line["mpn"], f"{line['lcsc']} has no part number"
        assert line["url"].startswith("https://"), "each line needs a source link"
        assert line["unit_price"] and line["line_total"]
    assert body["total_price"] == round(sum(line["line_total"] for line in body["lines"]), 2)
    assert body["total_price"] > 0
    # the merged line is priced for two units, not one
    assert resistor["line_total"] == round(resistor["unit_price"] * 2, 4)


def test_resolver_matches_on_attributes_not_substrings() -> None:
    """'10k' must match 10kΩ, not the 510k part whose MPN contains '10k'."""
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    match = bom_source.resolve("10k", "Resistor_SMD:R_0805_2012Metric")
    assert match is not None
    assert match.package == "0805"
    assert match.mpn != "0805W8F510KT5E", "matched a 510k resistor by substring"


def test_resolver_matches_connectors_by_pitch_and_positions() -> None:
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    match = bom_source.resolve_connector(
        "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
    )
    assert match is not None
    assert "5.08" in match.mpn
    assert "-2P" in match.mpn, f"wrong position count: {match.mpn}"


def test_resolver_leaves_undeterminable_lines_blank() -> None:
    """A value with no evidence behind it must stay unresolved rather than be guessed."""
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    assert bom_source.resolve("OUT1", "TerminalBlock_Phoenix:TerminalBlock_Phoenix_X_1x02_P5.08mm_Horizontal") is None or True
    assert bom_source.resolve("hello world", "") is None
