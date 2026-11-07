"""Schematic legibility: measuring an unreadable sheet, tidying it, and the goal gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_harness.agent import goal
from kicad_harness.engine import layout
from kicad_harness.engine import schematic as sch

REPO = Path(__file__).resolve().parent.parent.parent
JLC_LIB = REPO / "demo" / "libs" / "symbols" / "jlcpcb.kicad_sym"
SYSTEM_LIB = Path("/usr/share/kicad/symbols/Device.kicad_sym")


def _library() -> tuple[Path, str]:
    if JLC_LIB.is_file():
        return JLC_LIB, "jlcpcb"
    if SYSTEM_LIB.is_file():
        return SYSTEM_LIB, "Device"
    pytest.skip("no symbol library available")


@pytest.fixture(scope="module")
def libraries() -> dict[str, dict[str, sch.LibrarySymbol]]:
    path, name = _library()
    return {name: sch.read_library(path)}


@pytest.fixture(scope="module")
def two_pin(libraries) -> str:
    name = next(iter(libraries))
    symbol = next(s for s in libraries[name].values() if len(s.pins) == 2)
    return symbol.lib_id


def _messy(tmp_path: Path, libraries, two_pin: str) -> sch.Schematic:
    """Two symbols on top of each other, a crossed pair of wires, a dangling end, a label pile."""
    doc = sch.Schematic.create(tmp_path / "messy.kicad_sch", libraries=libraries)
    doc.add_symbol(two_pin, "R1", 60.0, 60.0)
    doc.add_symbol(two_pin, "R2", 61.0, 60.0)  # overlaps R1
    doc.add_wire([(120.0, 120.0), (130.0, 120.0)])
    doc.add_wire([(125.0, 115.0), (125.0, 125.0)])  # crosses the wire above
    for i in range(4):
        doc.add_label("VCC", 200.0, 200.0)  # four labels in the same spot
    return doc


def test_analysis_finds_the_unreadable_parts(tmp_path: Path, libraries, two_pin: str) -> None:
    report = layout.analyze(_messy(tmp_path, libraries, two_pin))
    assert report["ok"] is False
    counts = report["counts"]
    assert counts["symbol_overlaps"] >= 1
    assert counts["label_overlaps"] >= 1
    assert counts["wire_crossings"] >= 1
    assert counts["dangling_wires"] >= 4  # both ends of both wires
    assert report["score"] == (
        counts["symbol_overlaps"] + counts["label_overlaps"] + counts["wires_through_symbols"]
        + counts["dangling_wires"] + counts["off_sheet"]
    )


def test_tidy_makes_the_mess_readable_and_keeps_the_netlist(
    tmp_path: Path, libraries, two_pin: str
) -> None:
    doc = _messy(tmp_path, libraries, two_pin)
    doc.connect_pins(("R1", "1"), ("R2", "1"), net="MID")
    before_nets = {frozenset((m["ref"], m["pin"]) for m in members)
                   for members in doc.nets().values()}
    before = layout.analyze(doc)
    assert before["score"] > 0

    report = layout.tidy(doc)
    doc.save()

    reloaded = sch.Schematic.load(tmp_path / "messy.kicad_sch", libraries=libraries)
    after = layout.analyze(reloaded)
    assert report["after"]["score"] == 0
    assert after["score"] == 0, after["counts"]
    # Labels carry the connectivity: the same pins still belong to the same nets.
    after_nets = {frozenset((m["ref"], m["pin"]) for m in members)
                  for members in reloaded.nets().values()}
    assert after_nets == before_nets
    assert "MID" in reloaded.nets()


def test_legibility_is_a_mandatory_goal_criterion() -> None:
    criteria = [{"id": "drc", "type": "drc_errors", "max": 0}]

    # 1. Not measured: the criterion must fail rather than look like a pass.
    # A schematic that could not be read reports ``None``: measured-and-unreadable, not absent.
    unmeasured = goal.evaluate({"criteria": criteria}, {},
                               {"erc_counts": {"errors": 0, "warnings": 0},
                                "schematic_layout": None}, [])
    legible = next(c for c in unmeasured["criteria"] if c["id"] == "legible")
    assert legible["passed"] is False and "not measured" in legible["detail"]
    assert unmeasured["met"] is False

    # 2. Measured with violations: still fails, and the detail names them.
    bad = goal.evaluate({"criteria": criteria}, {},
                        {"erc_counts": {"errors": 0, "warnings": 0},
                         "schematic_layout": {"score": 3, "counts": {"symbol_overlaps": 1,
                                                                     "label_overlaps": 2}}}, [])
    legible = next(c for c in bad["criteria"] if c["id"] == "legible")
    assert legible["passed"] is False and "3 layout violation" in legible["detail"]

    # 3. Score 0: the baseline is satisfied and the goal can be met.
    good = goal.evaluate({"criteria": criteria}, {},
                         {"erc_counts": {"errors": 0, "warnings": 0},
                          "schematic_layout": {"score": 0, "counts": {}}}, [])
    assert all(c["passed"] for c in good["criteria"]), good
    assert good["met"] is True


def test_an_agent_cannot_relax_the_legibility_floor() -> None:
    """A goal that says ``max: 99`` still gets the harness's max: 0."""
    authored = {"criteria": [{"id": "legible", "type": "schematic_legibility", "max": 99}]}
    floor = goal.mandatory_criteria(authored, {"schematic_layout": {"score": 0}})
    legible = next(c for c in floor if c["id"] == "legible")
    assert legible["max"] == 0
