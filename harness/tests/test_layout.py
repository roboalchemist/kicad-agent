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


@pytest.fixture(scope="module")
def many_pin(libraries) -> str:
    name = next(iter(libraries))
    symbol = next(s for s in libraries[name].values() if len(s.pins) >= 3)
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


def _power_library() -> dict[str, dict[str, sch.LibrarySymbol]]:
    path = Path("/usr/share/kicad/symbols/power.kicad_sym")
    if not path.is_file():
        pytest.skip("no KiCad power library here")
    return {"power": sch.read_library(path)}


def test_findings_name_a_power_symbol_that_contradicts_its_net(tmp_path: Path, libraries, two_pin: str) -> None:
    """The placer must not decide between a symbol's value and its net: it reports the conflict."""
    extra = _power_library()
    merged = {**libraries, **extra}
    doc = sch.Schematic.create(tmp_path / "power.kicad_sch", libraries=merged)
    doc.add_symbol(two_pin, "R1", 100.0, 100.0)
    doc.add_symbol("power:GND", "#PWR01", 130.0, 100.0)
    doc.connect_pins(("#PWR01", "1"), ("R1", "1"), net="+5V")
    messages = layout.findings(doc)
    assert any("#PWR01" in message and "+5V" in message for message in messages), messages


def test_tidy_puts_a_passive_beside_the_part_it_serves(tmp_path: Path, libraries, two_pin, many_pin) -> None:
    """A pull-up or decoupling part belongs next to its owner, not in a column of its own."""
    doc = sch.Schematic.create(tmp_path / "owner.kicad_sch", libraries=libraries)
    doc.add_symbol(many_pin, "U1", 100.0, 100.0)        # many-pin anchor
    doc.add_symbol(two_pin, "R1", 240.0, 240.0)         # far away
    doc.connect_pins(("U1", "1"), ("R1", "1"), net="SIG")
    layout.tidy(doc)
    boxes = layout.symbol_boxes(doc)
    def centre(ref):
        box = boxes[ref]
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    import math
    assert math.dist(centre("U1"), centre("R1")) < 30, "the passive was not placed with its owner"


def test_router_wires_a_point_to_point_net_and_keeps_the_netlist(
    tmp_path: Path, libraries, two_pin: str
) -> None:
    doc = sch.Schematic.create(tmp_path / "route.kicad_sch", libraries=libraries)
    doc.add_symbol(two_pin, "R1", 60.0, 60.0)
    doc.add_symbol(two_pin, "R2", 90.0, 60.0)
    doc.connect_pins(("R1", "1"), ("R2", "1"), net="MID")
    layout.tidy(doc)
    before = layout._partitions(doc)
    report = layout.route(doc)
    assert report["partitions_ok"] is True
    assert layout._partitions(doc) == before
    assert "MID" in report["wired"], report


def test_tidy_aligns_the_pins_of_a_cross_stage_net(tmp_path: Path, libraries, many_pin: str) -> None:
    """Channel placement lines the two pins of a point-to-point net up, so a wire can be straight."""
    doc = sch.Schematic.create(tmp_path / "align.kicad_sch", libraries=libraries)
    doc.add_symbol(many_pin, "U1", 80.0, 80.0)
    doc.add_symbol(many_pin, "U2", 200.0, 140.0)
    doc.connect_pins(("U1", "1"), ("U2", "1"), net="SIG")
    layout.tidy(doc)
    a = doc.pin_position(doc._placement("U1"), "1")
    b = doc.pin_position(doc._placement("U2"), "1")
    assert a is not None and b is not None
    assert abs(a[1] - b[1]) < 0.2, "the two pins of SIG are not on one row"
    # and the router can now draw it without changing the netlist
    before = layout._partitions(doc)
    report = layout.route(doc)
    assert report["partitions_ok"] and "SIG" in report["wired"], report


def test_alignment_follows_a_chain_of_stages(tmp_path: Path, libraries, many_pin: str) -> None:
    """Aligning U2 to U1 moves U2; U3 must then be aligned to U2's *new* row (second pass).

    The three parts are put in three columns by hand so the test exercises the multi-pass
    target refresh itself, not the stage assignment (a hub U2 would put U1 and U3 in one column,
    where they cannot both sit on pins 6.35 mm apart).
    """
    doc = sch.Schematic.create(tmp_path / "chain.kicad_sch", libraries=libraries)
    doc.add_symbol(many_pin, "U1", 60.0, 60.0)
    doc.add_symbol(many_pin, "U2", 160.0, 120.0)
    doc.add_symbol(many_pin, "U3", 260.0, 180.0)
    doc.connect_pins(("U1", "1"), ("U2", "1"), net="A")
    doc.connect_pins(("U2", "2"), ("U3", "1"), net="B")
    layout._align_symbols(doc, layout._alias_nets(doc), passes=3)

    def pin_point(ref, number):
        return doc.pin_position(doc._placement(ref), number)

    a1, a2 = pin_point("U1", "1"), pin_point("U2", "1")
    b2, b3 = pin_point("U2", "2"), pin_point("U3", "1")
    assert abs(a1[1] - a2[1]) < 0.2, "stage 1 is not on one row"
    assert abs(b2[1] - b3[1]) < 0.2, "stage 2 was aligned to U2's old row"


def test_schematic_png_is_a_viewable_image(tmp_path: Path, libraries, two_pin: str) -> None:
    """The agent gets a plain picture of the sheet, not just numbers."""
    from kicad_harness.engine import render

    doc = sch.Schematic.create(tmp_path / "shot.kicad_sch", libraries=libraries)
    doc.add_symbol(two_pin, "R1", 60.0, 60.0)
    doc.add_symbol(two_pin, "R2", 100.0, 60.0)
    doc.connect_pins(("R1", "1"), ("R2", "1"), net="MID")
    doc.save()
    out = render.schematic_png(doc.path, tmp_path / "shot.png", width=900)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    with Image.open(out) as image:
        assert image.width == 900 and image.height > 100
