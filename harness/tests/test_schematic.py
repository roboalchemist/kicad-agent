"""Schematic documents: S-expression round-trip, symbol placement, and real-KiCad validation.

The last test is the important one: it writes a schematic, then asks ``kicad-cli`` to render it
and to run ERC. Anything the writer emits that KiCad cannot parse fails there. See WORKLOG.md
for the format rules these tests protect.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kicad_harness.config import Config
from kicad_harness.engine import schematic as sch

REPO = Path(__file__).resolve().parent.parent.parent
JLC_LIB = REPO / "demo" / "libs" / "symbols" / "jlcpcb.kicad_sym"
SYSTEM_LIB = Path("/usr/share/kicad/symbols/Device.kicad_sym")


def _library_file() -> tuple[Path, str]:
    """The symbol library to test against, and the library name symbols are addressed by."""
    if JLC_LIB.is_file():
        return JLC_LIB, "jlcpcb"
    if SYSTEM_LIB.is_file():
        return SYSTEM_LIB, "Device"
    pytest.skip("no symbol library available")


@pytest.fixture(scope="module")
def jlc() -> dict[str, sch.LibrarySymbol]:
    path, _ = _library_file()
    return sch.read_library(path)


@pytest.fixture(scope="module")
def libraries(jlc: dict[str, sch.LibrarySymbol]) -> dict[str, dict[str, sch.LibrarySymbol]]:
    _, name = _library_file()
    return {name: jlc}


def _two_pin(lib: dict[str, sch.LibrarySymbol]) -> sch.LibrarySymbol:
    return next(s for s in lib.values() if len(s.pins) == 2)


def _many_pin(lib: dict[str, sch.LibrarySymbol]) -> sch.LibrarySymbol:
    return next(s for s in lib.values() if len(s.pins) >= 3)


@pytest.fixture
def ids(jlc: dict[str, sch.LibrarySymbol]) -> tuple[str, str]:
    """(two-pin lib_id, many-pin lib_id)."""
    return _two_pin(jlc).lib_id, _many_pin(jlc).lib_id


def _kicad_cli(tmp_path: Path) -> str | None:
    return shutil.which(str(Config(home=tmp_path / "home").kicad_cli or ""))


# ---------------------------------------------------------------------------------------------
# Parsing and writing


def test_empty_schematic_round_trips() -> None:
    text = sch.empty_schematic("A3")
    tree = sch.parse(text)
    assert tree[0] == "kicad_sch"
    paper = sch.find_one(tree, "paper")
    assert paper is not None and paper[1] == "A3"
    assert sch.write_top(sch.parse(text)) == text.rstrip("\n")


def test_parse_keeps_nesting_and_strings() -> None:
    tree = sch.parse('(root (a "x y") (b (c 1)))')
    assert tree[0] == "root"
    assert tree[1] == ["a", "x y"]
    assert sch.find_one(tree, "b") == ["b", ["c", "1"]]


def test_parse_rejects_unbalanced() -> None:
    with pytest.raises(ValueError):
        sch.parse("(root (a 1)")
    with pytest.raises(ValueError):
        sch.parse('(root "unterminated)')


def test_writer_drops_negative_zero() -> None:
    assert sch.quote("-0.0") == "0"
    assert sch.quote(-0.0) == "0"


# ---------------------------------------------------------------------------------------------
# Libraries and placement


def test_library_reads_pins(jlc: dict[str, sch.LibrarySymbol], ids) -> None:
    assert jlc
    two_pin = jlc[ids[0].split(":", 1)[1]]
    assert len(two_pin.pins) == 2
    many = jlc[ids[1].split(":", 1)[1]]
    assert len(many.pins) >= 3


def test_place_symbol_and_read_it_back(tmp_path: Path, libraries, ids) -> None:
    path = tmp_path / "s.kicad_sch"
    doc = sch.Schematic.create(path, libraries=libraries)
    doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    doc.save()

    again = sch.Schematic.load(path, libraries=libraries)
    (placed,) = again.placements()
    assert placed.reference == "R1"
    assert placed.lib_id == ids[0]
    # Coordinates snap to KiCad's 1.27 mm grid, so the offset from it is tiny.
    offset = placed.x / sch.GRID_MM
    assert abs(offset - round(offset)) < 1e-6


def test_duplicate_reference_is_rejected(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    with pytest.raises(ValueError, match="already placed"):
        doc.add_symbol(ids[1], "R1", 120.0, 100.0)


def test_unknown_symbol_lists_alternatives(tmp_path: Path, libraries) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    with pytest.raises(ValueError, match="available:"):
        doc.add_symbol("nope:Missing", "X1", 10.0, 10.0)


def test_pin_position_respects_rotation(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    flat = doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    turned = doc.add_symbol(ids[0], "R2", 100.0, 100.0, rotation=90.0)
    assert doc.pin_position(flat, "1") != doc.pin_position(turned, "1")


def test_remove_symbol_drops_its_wires(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    doc.add_symbol(ids[0], "R2", 120.0, 100.0)
    doc.connect_pins(("R1", "1"), ("R2", "1"), net="N")
    before = len(doc.wires)
    assert before
    assert doc.remove_symbol("R2")
    assert len(doc.wires) < before
    assert [p.reference for p in doc.placements()] == ["R1"]


# ---------------------------------------------------------------------------------------------
# Connectivity


def test_connect_pins_names_a_net(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    many, two = ids[1], ids[0]
    doc.add_symbol(many, "U1", 100.0, 100.0)
    doc.add_symbol(two, "R1", 140.0, 100.0)
    first_pin = next(iter(doc.lookup(many).pins))
    second_pin = next(iter(doc.lookup(two).pins))
    assert doc.connect_pins(("U1", first_pin), ("R1", second_pin), net="OUT") == "OUT"
    doc.save()

    again = sch.Schematic.load(doc.path, libraries=libraries)
    members = again.nets()["OUT"]
    assert {(m["ref"], m["pin"]) for m in members} == {("U1", first_pin), ("R1", second_pin)}


def test_wire_splits_into_segments(tmp_path: Path, libraries) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    nodes = doc.add_wire([(0.0, 0.0), (12.7, 0.0), (12.7, 12.7)])
    assert len(nodes) == 2
    for node in nodes:
        pts = sch.find_one(node, "pts")
        assert pts is not None and len(sch.find_all(pts, "xy")) == 2


def test_unconnected_pins_reports_floating(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    placed = doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    first = next(iter(doc.lookup(ids[0]).pins))
    assert {f["pin"] for f in doc.unconnected_pins()} == set(doc.lookup(ids[0]).pins)
    doc.add_label("A", *doc.pin_position(placed, first))
    assert first not in {f["pin"] for f in doc.unconnected_pins()}


def test_board_netlist_exposes_components(tmp_path: Path, libraries, ids) -> None:
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    many, two = ids[1], ids[0]
    doc.add_symbol(many, "U1", 100.0, 100.0)
    doc.add_symbol(two, "R1", 140.0, 100.0)
    first_pin = next(iter(doc.lookup(many).pins))
    second_pin = next(iter(doc.lookup(two).pins))
    doc.connect_pins(("U1", first_pin), ("R1", second_pin), net="OUT")

    netlist = sch.board_netlist(doc)
    assert {c["ref"] for c in netlist["components"]} == {"U1", "R1"}
    assert {m["ref"] for m in netlist["nets"]["OUT"]} == {"U1", "R1"}


# ---------------------------------------------------------------------------------------------
# The check that matters: real KiCad


def test_kicad_renders_and_erc_passes(tmp_path: Path, libraries, ids) -> None:
    """The writer must produce a file real KiCad loads, plots, and finds electrically clean."""
    cli = _kicad_cli(tmp_path)
    if not cli:
        pytest.skip("kicad-cli is not available")

    path = tmp_path / "board.kicad_sch"
    doc = sch.Schematic.create(path, libraries=libraries)
    many, two = ids[1], ids[0]
    doc.add_symbol(many, "U1", 100.0, 100.0)
    doc.add_symbol(two, "R1", 145.0, 100.0)
    first_pin = next(iter(doc.lookup(many).pins))
    second_pin = next(iter(doc.lookup(two).pins))
    doc.connect_pins(("U1", first_pin), ("R1", second_pin), net="OUT")
    doc.save()

    svg = subprocess.run(
        [cli, "sch", "export", "svg", str(path), "-o", str(tmp_path / "svg")],
        capture_output=True,
        text=True,
    )
    assert svg.returncode == 0, svg.stdout + svg.stderr
    assert list((tmp_path / "svg").glob("*.svg")), "kicad-cli produced no SVG"

    erc_path = tmp_path / "erc.json"
    erc = subprocess.run(
        [cli, "sch", "erc", str(path), "--format", "json", "-o", str(erc_path)],
        capture_output=True,
        text=True,
    )
    assert erc.returncode == 0, erc.stdout + erc.stderr
    report = json.loads(erc_path.read_text(encoding="utf-8"))
    errors = [v for v in report.get("violations", []) if v.get("severity") == "error"]
    assert not errors, errors


# ---------------------------------------------------------------------------------------------
# skidl generator


def test_skidl_spec_validation() -> None:
    from kicad_harness.engine import skidl_gen

    parts, nets = skidl_gen.validate(
        {
            "parts": [
                {"ref": "R1", "library": "Device", "symbol": "R", "value": "10k"},
                {"ref": "C1", "library": "Device", "symbol": "C"},
            ],
            "nets": [{"name": "MID", "pins": ["R1.2", "C1.1"]}],
        }
    )
    assert [p.ref for p in parts] == ["R1", "C1"]
    assert nets[0].pins == [("R1", "2"), ("C1", "1")]

    with pytest.raises(ValueError, match="unknown part"):
        skidl_gen.validate(
            {"parts": [{"ref": "R1", "library": "Device", "symbol": "R"}], "nets": [{"name": "X", "pins": ["R9.1"]}]}
        )
    with pytest.raises(ValueError, match="REF.PIN"):
        skidl_gen.validate(
            {"parts": [{"ref": "R1", "library": "Device", "symbol": "R"}], "nets": [{"name": "X", "pins": ["R1"]}]}
        )
    with pytest.raises(ValueError, match="duplicate reference"):
        skidl_gen.validate(
            {
                "parts": [
                    {"ref": "R1", "library": "Device", "symbol": "R"},
                    {"ref": "R1", "library": "Device", "symbol": "R"},
                ]
            }
        )


def test_skidl_generates_a_loadable_schematic(tmp_path: Path) -> None:
    """skidl output must land as schematic.kicad_sch and be loadable by KiCad."""
    from kicad_harness.engine import skidl_gen

    if not skidl_gen.available():
        pytest.skip("skidl is not installed")
    if not skidl_gen._default_symbol_dirs()[0].is_dir() and not (
        Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols").is_dir()
    ):
        pytest.skip("no KiCad symbol library on this machine")

    parts = [
        skidl_gen.PartSpec("R1", "Device", "R", value="10k", footprint="Resistor_SMD:R_0805_2012Metric"),
        skidl_gen.PartSpec("C1", "Device", "C", value="100n", footprint="Capacitor_SMD:C_0805_2012Metric"),
    ]
    nets = [
        skidl_gen.NetSpec("VCC", [("R1", "1")]),
        skidl_gen.NetSpec("MID", [("R1", "2"), ("C1", "1")]),
        skidl_gen.NetSpec("GND", [("C1", "2")]),
    ]
    out = tmp_path / "schematic.kicad_sch"
    result = skidl_gen.generate(parts, nets, out)
    assert result.path.is_file() and result.path.stat().st_size > 0

    loaded = sch.Schematic.load(out)
    refs = {p.reference for p in loaded.placements() if not p.reference.startswith("#")}
    assert refs == {"R1", "C1"}

    cli = _kicad_cli(tmp_path)
    if not cli:
        return
    svg = subprocess.run(
        [cli, "sch", "export", "svg", str(out), "-o", str(tmp_path / "svg")],
        capture_output=True,
        text=True,
    )
    assert svg.returncode == 0, svg.stdout + svg.stderr


def test_skidl_failure_names_the_problem(tmp_path: Path) -> None:
    """A wrong symbol must come back as a readable reason, not a traceback."""
    from kicad_harness.engine import skidl_gen

    if not skidl_gen.available():
        pytest.skip("skidl is not installed")
    parts = [skidl_gen.PartSpec("U1", "Timer", "NE555NOTREAL", value="x")]
    with pytest.raises(Exception) as info:
        skidl_gen.generate(parts, [], tmp_path / "schematic.kicad_sch")
    assert "Unable to find part" in str(info.value)

    # ...and the near-miss suggestions point at a real symbol.
    matches = skidl_gen.suggest_symbols("Timer", "NE555")
    assert "NE555P" in matches


def test_pin_net_follows_a_chain_of_wires(tmp_path: Path, libraries, ids) -> None:
    """Regression: a pin joined by several segments read as unconnected.

    Generators draw one segment per leg with the label elsewhere on the chain; looking only at
    wires touching the pin made every chained connection look floating.
    """
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    a = doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    pin = next(iter(doc.lookup(ids[0]).pins))
    x, y = doc.pin_position(a, pin)
    # Two segments, then the label at the far end of the second.
    elbow = (x + 5.08, y)
    far = (x + 5.08, y + 5.08)
    doc.add_wire([(x, y), elbow])
    doc.add_wire([elbow, far])
    doc.add_label("CHAINED", *far)

    assert doc.pin_net(a, pin) == "CHAINED"
    assert {"CHAINED"} <= set(doc.nets())
    assert {"ref": "R1", "pin": pin} not in doc.unconnected_pins()


def test_draw_pin_stubs_wires_labels_and_keeps_the_netlist(tmp_path: Path, libraries, ids) -> None:
    """Labels sitting on pin anchors get a visible stub without changing connectivity."""
    doc = sch.Schematic.create(tmp_path / "s.kicad_sch", libraries=libraries)
    a = doc.add_symbol(ids[0], "R1", 100.0, 100.0)
    pin = next(iter(doc.lookup(ids[0]).pins))
    doc.add_label("STUBBED", *doc.pin_position(a, pin))
    assert not doc.wires

    result = doc.draw_pin_stubs()
    assert result["stubbed"] == 1
    assert len(doc.wires) == 1
    doc.save()

    again = sch.Schematic.load(doc.path, libraries=libraries)
    assert again.pin_net(again.placements()[0], pin) == "STUBBED"
    assert again.unconnected_pins() == [{"ref": "R1", "pin": p} for p in again.lookup(ids[0]).pins if p != pin]


def test_derived_symbols_inherit_pins_from_their_base() -> None:
    """KiCad's `extends` symbols (AMS1117-3.3 etc.) were being loaded with zero pins."""
    from kicad_harness.engine.schematic import read_library

    library = read_library("/usr/share/kicad/symbols/Regulator_Linear.kicad_sym")
    ams = library["AMS1117-3.3"]
    assert len(ams.pins) == 3
    assert {(pin.number, pin.name) for pin in ams.pins.values()} == {
        ("1", "GND"), ("2", "VO"), ("3", "VI")}
    assert ams.pins["2"].electrical != ""
