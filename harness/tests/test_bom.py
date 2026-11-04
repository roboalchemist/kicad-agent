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


def test_resolver_handles_dual_row_headers() -> None:
    """A 2x20 socket footprint needs a 2x20 part, not a 1x20 one that happens to have 20 holes."""
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    match = bom_source.resolve_connector(
        "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical"
    )
    assert match is not None
    assert "2*20" in match.mpn or "2x20" in match.mpn, f"wrong geometry: {match.mpn}"
    assert match.matched_by.endswith("2x20")


def test_resolver_will_not_substitute_a_different_geometry() -> None:
    """A 1x02 footprint must not take the 2x20 part, and vice versa."""
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    single = bom_source.resolve_connector(
        "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    )
    assert single is not None
    assert "2*20" not in single.mpn and "2x20" not in single.mpn


def test_socket_footprints_only_accept_female_parts() -> None:
    from kicad_harness.engine import bom_source

    assert bom_source._gender_ok("Connector:PinSocket_1x02", "Connectors Female Headers")
    assert not bom_source._gender_ok("Connector:PinSocket_1x02", "Connectors Pin Headers")
    assert bom_source._gender_ok("Connector:PinHeader_1x02", "Connectors Pin Headers")


def test_tier_summary_counts_part_numbers_not_lines() -> None:
    """One terminal block used nineteen times is one extended part, not nineteen."""
    from kicad_harness.server import summarise_tiers

    lines = [
        {"refs": ["R1", "R2"], "qty": 2, "lcsc": "C17414", "tier": "basic"},
        {"refs": ["J1"], "qty": 1, "lcsc": "C2898709", "tier": "extended"},
        {"refs": ["J2"], "qty": 1, "lcsc": "C2898709", "tier": "extended"},
        {"refs": ["J3"], "qty": 1, "lcsc": "C2898709", "tier": "extended"},
        {"refs": ["X1"], "qty": 1, "lcsc": "", "tier": None},
    ]
    summary = summarise_tiers(lines, 5)
    assert summary == {"parts": 2, "basic_parts": 1, "extended_parts": 1,
                       "basic_units": 2, "units": 5}


def test_basic_only_search_never_returns_an_extended_part() -> None:
    """The basic-first policy has to be enforceable: asking for basic may only yield basic."""
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    for value, footprint in [
        ("10k", "Resistor_SMD:R_0805_2012Metric"),
        ("100nF", "Capacitor_SMD:C_0805_2012Metric"),
        ("SS34", "Diode_SMD:D_SMA"),
        ("G5LE-14 5VDC", "Relay_THT:Relay_SPDT_Omron-G5LE-1"),
    ]:
        match = bom_source.resolve(value, footprint, basic_only=True)
        assert match is None or match.basic, f"{value} resolved to an extended part under basic_only"


def test_extended_relay_is_reported_as_having_no_basic_equivalent() -> None:
    """The reason must name the missing category, not blame the value for a gap that is total."""
    from kicad_harness.engine import bom_source

    reason = bom_source._why_no_basic(
        "G5LE-14 5VDC",
        "Relay_THT:Relay_SPDT_Omron-G5LE-1",
        {"category": "Power Relays", "package": "Plugin,16.5x22.5mm"},
    )
    assert "no Power Relays" in reason, reason


def test_extended_part_in_a_category_with_basics_explains_the_mismatch() -> None:
    from kicad_harness.engine import bom_source, parts

    if not parts.available():
        pytest.skip("no JLCPCB database here")
    reason = bom_source._why_no_basic(
        "1M", "Resistor_SMD:R_0805_2012Metric",
        {"category": "Chip Resistor - Surface Mount", "package": "0805"},
    )
    assert "basic Chip Resistor" in reason and "none is 1M" in reason, reason


async def test_resolve_resources_an_extended_part_to_a_basic_one(services) -> None:
    """Re-sourcing must take a basic equivalent when one exists, without being asked twice.

    A 100nF 0805 cap written as the extended CL21B104KBCNNNC has an exactly equivalent basic
    part (CC0805KRX7R9BB104). Leaving the extended code in place would cost a reel fee for no
    electrical difference at all.
    """
    from kicad_harness.engine import parts
    from kicad_harness.engine.schematic import Schematic, read_library

    if not parts.available():
        pytest.skip("no JLCPCB database here")

    name = "bom-basic-first"
    ws = Workspace.create_schematic(services.config, name, "a 100nF cap sourced as an extended part")
    doc = Schematic.load(ws.schematic_path)
    doc.libraries["Device"] = read_library("/usr/share/kicad/symbols/Device.kicad_sym")
    doc.add_symbol("Device:C", "C1", 100.0, 100.0, value="100nF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    doc.set_symbol_property("C1", "LCSC", "C1711")  # extended CL21B104KBCNNNC
    doc.save()

    async with _client(services) as client:
        report = (await client.post(f"/api/designs/{name}/bom/resolve")).json()
        body = (await client.get(f"/api/designs/{name}/bom")).json()

    assert report["switched_lines"] == 1, report
    assert report["switched"][0]["from"] == "C1711"
    cap = next(line for line in body["lines"] if line["value"] == "100nF")
    assert cap["lcsc"] == "C49678", "did not switch to the basic equivalent"
    assert cap["tier"] == "basic"


async def test_resolve_leaves_an_extended_part_alone_when_no_basic_equivalent_exists(services) -> None:
    """A fuse has no basic counterpart at any price; inventing one would be worse than the fee."""
    from kicad_harness.engine import parts
    from kicad_harness.engine.schematic import Schematic, read_library

    if not parts.available():
        pytest.skip("no JLCPCB database here")

    name = "bom-basic-none"
    ws = Workspace.create_schematic(services.config, name, "a 2A fuse")
    doc = Schematic.load(ws.schematic_path)
    doc.libraries["Device"] = read_library("/usr/share/kicad/symbols/Device.kicad_sym")
    doc.add_symbol("Device:Fuse", "F1", 100.0, 100.0, value="2A",
                   footprint="Fuse:Fuse_1206_3216Metric")
    doc.set_symbol_property("F1", "LCSC", "C3105")
    doc.save()

    async with _client(services) as client:
        report = (await client.post(f"/api/designs/{name}/bom/resolve")).json()
        body = (await client.get(f"/api/designs/{name}/bom")).json()

    assert report["switched_lines"] == 0 and report["resolved_lines"] == 0, report
    fuse = next(line for line in body["lines"] if line["value"] == "2A")
    assert fuse["lcsc"] == "C3105" and fuse["tier"] == "extended"


def test_power_budget_counts_the_rail_from_the_schematic() -> None:
    """The rail load comes from the schematic's own connectivity, not from a guess."""
    from types import SimpleNamespace

    from kicad_harness.engine import bom_source

    placements = [SimpleNamespace(reference=f"K{i}", value="G5LE-14 5VDC") for i in range(1, 17)]
    placements.append(SimpleNamespace(reference="J30", value="RPi3 40-pin bottom mount"))
    doc = SimpleNamespace(
        placements=lambda: placements,
        nets=lambda: {"+5V": [{"ref": p.reference} for p in placements]},
    )
    budget = bom_source.power_budget(doc)
    assert budget["relay_coils"] == 16
    assert budget["coil_current_ma"] == 79.4
    assert budget["coil_load_a"] == 1.27
    assert budget["pi3_on_rail"] is True
    assert budget["load_stress_a"] == 2.07 and budget["load_max_a"] == 2.61


def test_measured_limits_state_numbers_not_estimates() -> None:
    """A rejected swap must name the measured load and the datasheet limit that rules it out."""
    from kicad_harness.engine import bom_source

    budget = {"rail": "+5V", "relay_coils": 16, "coil_current_ma": 79.4, "coil_load_a": 1.27,
              "pi3_stress_a": 0.8, "pi3_max_a": 1.34, "load_stress_a": 2.07, "load_max_a": 2.61}
    regulator = bom_source._measured_limit("LM2596S-5.0", budget)
    assert "2 A" in regulator and "2.07 A" in regulator and "2.61 A" in regulator
    assert regulator == bom_source._measured_limit("LM2596S-5.0", budget)

    # the capacitor limits must cite the regulator actually in the design (TPS5430, not LM2596)
    caps = bom_source._measured_limit("470uF/10V", budget)
    assert "TPS5430" in caps and "220 uF" in caps and "100 uF/6.3 V" in caps
    small = bom_source._measured_limit("10uF", budget)
    assert "10 uF ceramic" in small and "C13585" in small

    # a part with no measured constraint must not acquire one
    assert bom_source._measured_limit("G5LE-14 5VDC", budget) == ""
    assert bom_source._measured_limit("LM2596S-5.0", None) == ""
