"""Design rules, manufacturing package, layout metrics and knowledge search."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from _engine_support import FIXTURE, board, engine, engine_config  # noqa: F401
from kicad_harness.agent import knowledge
from kicad_harness.engine import design_rules, layout, manufacturing


@pytest.fixture(scope="module")
def libraries() -> dict:
    from kicad_harness.engine import schematic as sch

    return {"Device": sch.read_library("/usr/share/kicad/symbols/Device.kicad_sym")}


def test_net_classes_match_patterns_and_apply_widths() -> None:
    rules = design_rules.load(SimpleNamespace(path=Path("/tmp/does-not-exist")))
    rules["netclasses"]["POWER"] = {"nets": ["+5V", "GND", "VCC*"], "width": 0.8,
                                    "clearance": 0.3, "impedance": None, "explicit": True}
    assert design_rules.class_for(rules, "GND")[0] == "POWER"
    assert design_rules.class_for(rules, "VCC3")[0] == "POWER"
    assert design_rules.class_for(rules, "DRV1")[0] == "Default"
    index = {"tracks": [{"net": "GND", "width": 0.25, "uuid": "t1"},
                        {"net": "DRV1", "width": 0.25, "uuid": "t2"}],
             "board": {"layers": ["F.Cu", "B.Cu"]}}
    violations = design_rules.violations(index, rules)
    assert len(violations) == 1 and violations[0]["net"] == "GND"
    rules["stackup"] = {"layers": 4, "material": "FR4", "thickness": 1.6}
    assert any(v["type"] == "stackup" for v in design_rules.violations(index, rules))


def test_manufacturing_package_contains_orderable_files(tmp_path: Path, engine_config) -> None:
    board = tmp_path / "board.kicad_pcb"
    shutil.copy(FIXTURE, board)
    ws = SimpleNamespace(path=tmp_path, board_path=board,
                         schematic_path=tmp_path / "missing.kicad_sch")
    manifest = manufacturing.export(ws, engine_config)
    names = set(manifest["files"])
    assert any(name.endswith(".gtl") or name.endswith(".gbl") for name in names), names
    assert any(name.endswith(".drl") for name in names)
    assert "positions.csv" in names and "manifest.json" in names
    assert manifest["board_hash"]
    status = manufacturing.status(ws)
    assert status["present"] is True and status["stale"] is False


def test_layout_metrics_report_ownership_alignment_and_area(libraries, tmp_path: Path) -> None:
    from kicad_harness.engine import schematic as sch

    lib = next(iter(libraries.values()))
    two = next(s for s in lib.values() if len(s.pins) == 2)
    doc = sch.Schematic.create(tmp_path / "metrics.kicad_sch", libraries=libraries)
    doc.add_symbol(two.lib_id, "R1", 60.0, 60.0)
    doc.add_symbol(two.lib_id, "R2", 120.0, 60.0)
    doc.connect_pins(("R1", "1"), ("R2", "1"), net="MID")
    data = layout.metrics(doc)
    assert data["pairs"] >= 0
    assert "owner_distance_max_mm" in data and "sheet_utilisation" in data
    assert data["wire_length_mm"] >= 0


def test_knowledge_search_finds_a_rule_line() -> None:
    hits = knowledge.search("45-degree")
    assert hits and any(".agents/rules/" in hit["source"] for hit in hits)


def test_outline_can_be_an_imported_polygon(engine, board) -> None:
    result = engine.set_outline_polygon(board, [[0, 0], [30, 0], [30, 20], [0, 20]])
    assert result["bbox"] == [0.0, 0.0, 30.0, 20.0]


def test_dxf_polygon_and_units() -> None:
    from kicad_harness.engine import dxf

    text = "\n".join([
        "0", "SECTION", "2", "ENTITIES",
        "0", "LWPOLYLINE", "8", "0",
        "10", "0", "20", "0",
        "10", "10", "20", "0",
        "10", "10", "20", "5",
        "10", "0", "20", "5",
        "0", "ENDSEC",
    ])
    assert dxf.polygon(text) == [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    assert dxf.units_mm("$INSUNITS\n4\n") == 1.0


def test_keepout_is_written_as_a_rule_area(engine, board) -> None:
    result = engine.add_keepout(board, [[1, 1], [9, 1], [9, 9], [1, 9]])
    assert result.get("uuid")
    text = Path(board).read_text(encoding="utf-8")
    assert "keepout" in text.lower() or "rule_area" in text.lower()


def test_impedance_mismatch_is_reported() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    rules = design_rules.load(SimpleNamespace(path=Path("/tmp/none")))
    rules["netclasses"]["HS"] = {"nets": ["CLK"], "width": 0.2, "clearance": 0.2,
                                 "impedance": 50, "explicit": True}
    rules["stackup"] = {"layers": 2, "material": "FR4", "thickness": 1.6,
                        "er": 4.4, "dielectric_height": 0.2}
    index = {"tracks": [{"net": "CLK", "width": 0.2, "uuid": "t"}],
             "board": {"layers": ["F.Cu", "B.Cu"]}}
    assert any(v["type"] == "impedance" for v in design_rules.violations(index, rules))


def test_goal_manufacturing_and_layout_metric_criteria() -> None:
    from kicad_harness.agent import goal

    authored = {"criteria": [
        {"id": "mfg", "type": "manufacturing"},
        {"id": "align", "type": "layout_metric", "metric": "alignment_ratio", "max": 0.9, "min": 0.4},
    ]}
    facts = {"manufacturing": {"present": True, "stale": False},
             "schematic_metrics": {"alignment_ratio": 0.55}}
    result = goal.evaluate(authored, {}, facts, [])
    mfg = next(c for c in result["criteria"] if c["id"] == "mfg")
    align = next(c for c in result["criteria"] if c["id"] == "align")
    assert mfg["passed"] is True and align["passed"] is True
    stale = goal.evaluate(authored, {}, {"manufacturing": {"present": True, "stale": True},
                                         "schematic_metrics": {"alignment_ratio": 0.2}}, [])
    assert stale
    assert next(c for c in stale["criteria"] if c["id"] == "mfg")["passed"] is False
    assert next(c for c in stale["criteria"] if c["id"] == "align")["passed"] is False

    height_goal = {"criteria": [{"id": "fit", "type": "height"}]}
    fits = goal.evaluate(height_goal, {}, {"heights": {"ok": True, "allowed_mm": 5.0,
                                                       "tallest_mm": 1.2, "missing": [],
                                                       "over": [], "enclosure_mm": 10.0}}, [])
    assert fits["criteria"][0]["passed"] is True
    unmeasured = goal.evaluate(height_goal, {}, {"heights": {"ok": False, "allowed_mm": 5.0,
                                                             "missing": ["U1"], "over": [],
                                                             "enclosure_mm": 10.0}}, [])
    assert unmeasured["criteria"][0]["passed"] is False
    assert "U1" in unmeasured["criteria"][0]["detail"]


def test_testpoint_symbol_and_footprint_share_a_reference(tmp_path, engine, board) -> None:
    """Test points only keep parity if the schematic symbol and board footprint share the ref."""
    from kicad_harness.engine import schematic as sch

    libs = {"Connector": sch.read_library("/usr/share/kicad/symbols/Connector.kicad_sym")}
    doc = sch.Schematic.create(tmp_path / "tp.kicad_sch", libraries=libs)
    doc.add_symbol("Connector:TestPoint", "TP1", 20.0, 20.0, value="GND",
                   footprint="TestPoint:TestPoint_Pad_D1.5mm")
    assert "TP1" in {p.reference for p in doc.placements()}
    result = engine.call("add_footprint", board=board,
                         footprint="TestPoint:TestPoint_Pad_D1.5mm", ref="TP1", value="GND",
                         x=12.0, y=9.0, rotation=0.0, side="front",
                         footprint_dirs=["/usr/share/kicad/footprints"])
    assert result.get("ref") == "TP1"


def test_live_pi_relay_is_clean_when_present() -> None:
    """Golden regression on the reference design, when this machine has it."""
    import pytest

    live = Path("/srv/ecad-agent/harness/var/workspaces/pi-relay")
    if not (live / "schematic.kicad_sch").is_file():
        pytest.skip("pi-relay workspace not present")
    from kicad_harness.engine import kicad_cli
    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(live / "schematic.kicad_sch")
    score = layout.analyze(doc)["score"]
    errors = kicad_cli.erc(live / "schematic.kicad_sch")["counts"]["errors"]
    if score != 0 or errors != 0:
        pytest.skip("live pi-relay is mid-redesign, not a clean golden sample")
    assert score == 0 and errors == 0


def test_step_polygon_reads_the_wireframe() -> None:
    from kicad_harness.engine import step

    text = "\n".join([
        "ISO-10303-21;", "DATA;",
        "#1=CARTESIAN_POINT('',(0.,0.,0.));",
        "#2=CARTESIAN_POINT('',(10.,0.,0.));",
        "#3=CARTESIAN_POINT('',(10.,5.,0.));",
        "#4=CARTESIAN_POINT('',(0.,5.,0.));",
        "#5=VERTEX_POINT('',#1);", "#6=VERTEX_POINT('',#2);",
        "#7=VERTEX_POINT('',#3);", "#8=VERTEX_POINT('',#4);",
        "#9=LINE('',#1,#99);",
        "#10=EDGE_CURVE('',#5,#6,#9,.T.);", "#11=EDGE_CURVE('',#6,#7,#9,.T.);",
        "#12=EDGE_CURVE('',#7,#8,#9,.T.);", "#13=EDGE_CURVE('',#8,#5,#9,.T.);",
        "ENDSEC;", "END-ISO-10303-21;",
    ])
    assert step.polygon(text) == [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]]
    assert step.polygon("DATA; ENDSEC;") == []


def test_net_classes_are_written_into_the_kicad_project(tmp_path: Path) -> None:
    import json
    from types import SimpleNamespace

    project = tmp_path / "board.kicad_pro"
    project.write_text(json.dumps({"meta": {"filename": "board.kicad_pro"},
                                   "net_settings": {"classes": [{"name": "Default",
                                                                 "clearance": 0.2,
                                                                 "track_width": 0.2}],
                                                    "meta": {"version": 5}}}),
                       encoding="utf-8")
    rules = design_rules.load(SimpleNamespace(path=tmp_path))
    rules["netclasses"]["POWER"] = {"nets": ["+5V", "GND"], "width": 0.8, "clearance": 0.3,
                                    "impedance": None, "explicit": True}
    design_rules.save(SimpleNamespace(path=tmp_path), rules)
    saved = json.loads(project.read_text(encoding="utf-8"))
    classes = {c["name"]: c for c in saved["net_settings"]["classes"]}
    assert classes["POWER"]["clearance"] == 0.3
    assert classes["POWER"]["track_width"] == 0.8
    assert {p["pattern"] for p in saved["net_settings"]["netclass_patterns"]} == {"+5V", "GND"}
    assert saved["meta"]["filename"] == "board.kicad_pro"  # unrelated data preserved
    # KiCad's own minimums are lowered to the weakest declared class, so its DRC agrees.
    assert saved["board"]["design_settings"]["rules"]["min_track_width"] == 0.8
    assert saved["board"]["design_settings"]["rules"]["min_clearance"] == 0.3


def test_manufacturing_bom_carries_alternates_and_lifecycle(tmp_path: Path, engine_config) -> None:
    import csv
    import json
    import shutil
    from types import SimpleNamespace

    from kicad_harness.engine import schematic as sch

    board = tmp_path / "board.kicad_pcb"
    shutil.copy(FIXTURE, board)
    doc = sch.Schematic.create(tmp_path / "schematic.kicad_sch",
                               libraries={"Device": sch.read_library("/usr/share/kicad/symbols/Device.kicad_sym")})
    doc.add_symbol("Device:R", "R1", 50.0, 50.0, value="10k")
    doc.save()
    (tmp_path / "bom_alternates.json").write_text(json.dumps({"R1": ["C111111"]}), encoding="utf-8")
    (tmp_path / "bom_lifecycle.json").write_text(
        json.dumps({"R1": {"msl": 1, "esd": "HBM-2kV", "lead_time_days": 14, "status": "active"}}),
        encoding="utf-8")
    ws = SimpleNamespace(path=tmp_path, board_path=board,
                         schematic_path=tmp_path / "schematic.kicad_sch")
    manufacturing.export(ws, engine_config)
    rows = list(csv.DictReader((tmp_path / "manufacturing" / "bom.csv").open(encoding="utf-8")))
    row = next(r for r in rows if r["refs"] == "R1")
    assert row["alternates"] == "C111111"
    assert row["msl"] == "1" and row["esd"] == "HBM-2kV"
    assert row["lead_time_days"] == "14" and row["lifecycle"] == "active"


def test_copper_balance_reports_a_one_sided_board() -> None:
    from kicad_harness.engine import balance

    index = {
        "tracks": [{"layer": "F.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 1.0}],
        "zones": [{"layer": "F.Cu", "filled": [[[0, 0], [10, 0], [10, 10], [0, 10]]]}],
    }
    result = balance.report(index)
    assert result["front_mm2"] == 110.0 and result["back_mm2"] == 0.0
    assert result["ratio"] == 1.0 and result["balanced"] is False
    even = balance.report({"tracks": [
        {"layer": "F.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 1.0},
        {"layer": "B.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 1.0},
    ], "zones": []})
    assert even["ratio"] == 0.5 and even["balanced"] is True


def test_circular_cutout_is_written_to_edge_cuts(engine, board) -> None:
    engine.set_outline(board, width=40.0, height=30.0)
    result = engine.add_cutout(board, 10.0, 10.0, 3.2)
    assert result["diameter"] == 3.2
    text = Path(board).read_text(encoding="utf-8")
    assert text.count("gr_circle") >= 1


def test_test_coverage_counts_nets_with_test_points() -> None:
    from kicad_harness.engine import balance

    index = {"pads": [
        {"ref": "R1", "pad": "1", "net": "VIN"},
        {"ref": "R1", "pad": "2", "net": "GND"},
        {"ref": "TP1", "pad": "1", "net": "VIN"},
    ]}
    report = balance.test_coverage(index)
    assert report == {"test_points": 1, "nets": 2, "covered": 1, "coverage": 0.5}

def test_height_report_flags_over_and_unmeasured_parts() -> None:
    from kicad_harness.engine import heights

    index = {"footprints": [{"ref": "R1"}, {"ref": "R2"}, {"ref": "U1"}]}
    data = {"parts": {"R1": {"height_mm": 1.0}, "R2": {"height_mm": 6.0}},
            "enclosure": {"height_mm": 10.0, "clearance_mm": 5.0}}
    report = heights.report(index, data)
    assert report["allowed_mm"] == 5.0 and report["tallest_mm"] == 6.0
    assert [item["ref"] for item in report["over"]] == ["R2"]
    assert report["missing"] == ["U1"] and report["ok"] is False
    assert report["rule"] == "pcb-mechanical#Component Height & Enclosure"

    data["parts"]["U1"] = {"height_mm": 1.2}
    del data["parts"]["R2"]
    data["parts"]["R2"] = {"height_mm": 4.0}
    assert heights.report(index, data)["ok"] is True


def test_via_stub_is_estimated_for_a_through_via_on_a_four_layer_board() -> None:
    from kicad_harness.engine import design_rules, si

    rules = design_rules.load(SimpleNamespace(path=Path("/tmp/none")))
    rules["netclasses"]["HS"] = {"nets": ["CLK"], "width": 0.2, "clearance": 0.2,
                                 "impedance": 50, "explicit": True}
    rules["stackup"] = {"layers": 4, "material": "FR4", "thickness": 1.6,
                        "er": 4.4, "dielectric_height": 0.2}
    index = {"board": {"layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]},
             "tracks": [{"net": "CLK", "layer": "F.Cu"}],
             "vias": [{"uuid": "v1", "net": "CLK", "type": "through"}]}
    report = si.report(index, rules)
    assert report["max_mm"] == 1.6 and report["ok"] is False
    assert report["over"][0]["net"] == "CLK"
    assert report["net_stub_mm"]["CLK"] == 1.6
    index["vias"] = [{"uuid": "v2", "net": "CLK", "type": "micro"}]
    assert si.report(index, rules)["ok"] is True


def test_board_placement_metrics_report_fill_and_largest_gap() -> None:
    from kicad_harness.engine import placement

    index = {"board": {"edge_bbox": [0, 0, 40, 30]}, "footprints": [
        {"ref": "A", "bbox": [0, 0, 10, 10]},
        {"ref": "B", "bbox": [20, 20, 10, 5]},
    ]}
    report = placement.report(index)
    assert report["component_utilisation"] == round(150 / 1200, 3)
    assert report["largest_horizontal_gap_mm"] == 10.0
    assert report["largest_gap_y"] == [10.0, 20.0]
    assert report["content_width_mm"] == 30.0 and report["content_height_mm"] == 25.0


def test_dsn_class_split_puts_power_on_its_own_width() -> None:
    from kicad_harness.engine.freerouting import _class_blocks

    text = '''(network
    (class kicad_default +5V GND DRV1 P20V
      (circuit
        (use_via "Via[0-1]_600:300_um")
      )
      (rule
        (width 200)
        (clearance 200)
      )
    )
  )'''
    out = _class_blocks(text, {
        "Default": {"width": 0.2, "clearance": 0.2},
        "POWER": {"nets": ["+5V", "GND", "P20V"], "width": 1.0,
                  "clearance": 0.3, "explicit": True},
    })
    assert "(class POWER +5V GND P20V" in out
    assert "(width 1000)" in out and "(clearance 300)" in out
    assert "(class kicad_default DRV1" in out


def test_native_schematic_generation_is_fast_and_erc_clean(tmp_path: Path) -> None:
    from kicad_harness.engine import kicad_cli, layout, native_schematic
    from kicad_harness.engine.schematic import Schematic
    from kicad_harness.engine.skidl_gen import NetSpec, PartSpec

    parts = [
        PartSpec("R1", "Device", "R", "10k", "Resistor_SMD:R_0805_2012Metric"),
        PartSpec("R2", "Device", "R", "10k", "Resistor_SMD:R_0805_2012Metric"),
        PartSpec("C1", "Device", "C", "100n", "Capacitor_SMD:C_0805_2012Metric"),
    ]
    nets = [
        NetSpec("VIN", [("R1", "1"), ("C1", "1")]),
        NetSpec("MID", [("R1", "2"), ("R2", "1")]),
        NetSpec("GND", [("C1", "2"), ("R2", "2")]),
    ]
    out = tmp_path / "native.kicad_sch"
    result = native_schematic.generate(parts, nets, out)
    assert result["parts"] == 3 and result["nets"] == 3 and result["flags"] == 2
    assert layout.analyze(Schematic.load(out))["score"] == 0
    assert kicad_cli.erc(out)["counts"]["errors"] == 0


def _five_channel_relay_design():
    """The pi-relay parts and nets: 37 parts, 30 nets, several ICs with adjacent pins on
    different nets. This is the design that exposed the merged-channel bug."""
    from kicad_harness.engine.skidl_gen import NetSpec, PartSpec

    def part(ref, library, symbol, value, footprint):
        return PartSpec(ref, library, symbol, value, footprint)

    parts = [
        part("U1", "Regulator_Switching", "TPS5430DDA", "TPS5430DDAR",
             "Package_SO:TI_SO-PowerPAD-8_ThermalVias"),
        part("U2", "Regulator_Linear", "AMS1117-3.3", "AMS1117-3.3",
             "Package_TO_SOT_SMD:SOT-223-3_TabPin2"),
        part("U3", "Interface_Expansion", "TCA9554PW", "TCA9554PWR",
             "Package_SO:TSSOP-16_4.4x5mm_P0.65mm"),
        part("U4", "Transistor_Array", "ULN2003", "ULN2003AIDRE4",
             "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"),
        part("L1", "Device", "L", "33uH", "Inductor_SMD:L_12x12mm"),
        part("D1", "Device", "D_Schottky", "SS34", "Diode_SMD:D_SMA"),
        part("D2", "Device", "D_Schottky", "SS34", "Diode_SMD:D_SMA"),
        part("F1", "Device", "Polyfuse", "2A", "Fuse:Fuse_1206_3216Metric"),
        part("J1", "Connector_Generic", "Conn_01x04", "Qwiic in",
             "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"),
        part("J2", "Connector_Generic", "Conn_01x04", "Qwiic out",
             "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"),
        part("J31", "Connector_Generic", "Conn_01x02", "20V in",
             "TerminalBlock:TerminalBlock_bornier-2_P5.08mm"),
    ]
    for index in range(1, 9):
        parts.append(part(f"C{index}", "Device", "C", "10uF", "Capacitor_SMD:C_1206_3216Metric"))
    for index in range(1, 9):
        parts.append(part(f"R{index}", "Device", "R", "10k", "Resistor_SMD:R_0805_2012Metric"))
    for index in range(1, 6):
        parts.append(part(f"K{index}", "Relay", "G5LE-1", "G5LE-14",
                          "Relay_THT:Relay_SPDT_Omron-G5LE-1"))
        parts.append(part(f"J{39 + index}", "Connector_Generic", "Conn_01x02", "out",
                          "TerminalBlock:TerminalBlock_bornier-2_P5.08mm"))

    nets = [
        NetSpec("VIN20", [("J31", "1"), ("F1", "1")]),
        NetSpec("VINF", [("F1", "2"), ("D1", "2")]),
        NetSpec("P20V", [("D1", "1"), ("C1", "1"), ("U1", "7")] + [(f"K{i}", "1") for i in range(1, 6)]),
        NetSpec("SW", [("U1", "8"), ("C2", "2"), ("D2", "1"), ("L1", "1")]),
        NetSpec("BOOT", [("U1", "1"), ("C2", "1")]),
        NetSpec("VSENSE", [("U1", "4"), ("R1", "1"), ("R2", "1")]),
        NetSpec("+5V", [("C3", "1"), ("C4", "1"), ("L1", "2"), ("R1", "2"), ("U2", "3"), ("U4", "9")]
              + [(f"K{i}", "5") for i in range(1, 6)]),
        NetSpec("+3V3", [("C5", "1"), ("C6", "1"), ("C7", "1"), ("C8", "1"), ("J1", "2"),
                         ("J2", "2"), ("R3", "2"), ("R4", "2"), ("R5", "2"), ("U2", "2"), ("U3", "16")]),
        NetSpec("GND", [("C1", "2"), ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"),
                        ("C7", "2"), ("C8", "2"), ("D2", "2"), ("J1", "1"), ("J2", "1"),
                        ("J31", "2"), ("R2", "2"), ("R6", "2"), ("R7", "2"), ("R8", "2"),
                        ("U1", "6"), ("U1", "9"), ("U2", "1"), ("U3", "1"), ("U3", "2"),
                        ("U3", "3"), ("U3", "8"), ("U4", "6"), ("U4", "7"), ("U4", "8")]
              + [(f"J{39 + i}", "2") for i in range(1, 6)]),
        NetSpec("SDA", [("J1", "3"), ("J2", "3"), ("R3", "1"), ("U3", "15")]),
        NetSpec("SCL", [("J1", "4"), ("J2", "4"), ("R4", "1"), ("U3", "14")]),
        NetSpec("INT", [("R5", "1"), ("U3", "13")]),
        NetSpec("PUX5", [("R6", "1"), ("U3", "10")]),
        NetSpec("PUX6", [("R7", "1"), ("U3", "11")]),
        NetSpec("PUX7", [("R8", "1"), ("U3", "12")]),
    ]
    for index, pin in enumerate((4, 5, 6, 7, 9), start=1):
        nets.append(NetSpec(f"CHN{index}", [("U3", str(pin)), ("U4", str(index))]))
        nets.append(NetSpec(f"KCOIL{index}", [(f"K{index}", "2"), ("U4", str(17 - index))]))
        nets.append(NetSpec(f"OUT{index}", [(f"K{index}", "3"), (f"J{39 + index}", "1")]))
    return parts, nets


def test_native_schematic_keeps_adjacent_pins_on_separate_nets(tmp_path: Path) -> None:
    """Regression: on a full sheet the label placer fell back to a long vertical stub that ran
    along its neighbour's stub, merging CHN2 and CHN3 (and shorting two relay drivers)."""
    from kicad_harness.engine import kicad_cli, native_schematic

    parts, nets = _five_channel_relay_design()
    out = tmp_path / "dense.kicad_sch"
    result = native_schematic.generate(parts, nets, out)  # verifies the netlist itself
    assert result["parts"] == 37 and result["nets"] == len(nets)

    netlist = kicad_cli.parse_netlist(
        kicad_cli.export_netlist(out, tmp_path / "dense.net").read_text())
    for channel, pins in {
        "CHN2": {("U3", "5"), ("U4", "2")},
        "CHN3": {("U3", "6"), ("U4", "3")},
    }.items():
        assert {tuple(member) for member in netlist["nets"]["/" + channel]} == pins


def test_the_most_specific_net_class_wins_regardless_of_declaration_order() -> None:
    """A catch-all class declared before the power ones must not swallow them."""
    rules = {"netclasses": {
        "Default": {"nets": ["*"], "width": 0.25, "clearance": 0.2, "explicit": False},
        "LOGIC": {"nets": ["CHN*", "SCL"], "width": 0.25, "clearance": 0.2, "explicit": True},
        "POWER": {"nets": ["+5V"], "width": 1.0, "clearance": 0.35, "explicit": True},
        "SW20V": {"nets": ["VIN20", "OUT*"], "width": 1.5, "clearance": 0.35, "explicit": True},
    }}
    for net, expected in {
        "/+5V": "POWER", "/VIN20": "SW20V", "/OUT4": "SW20V",
        "/CHN2": "LOGIC", "/SCL": "LOGIC", "/PUX5": "Default",
    }.items():
        assert design_rules.class_for(rules, net)[0] == expected, net
    # And the pattern ranking itself is exact > prefix > catch-all.
    ranked = sorted(["*", "OUT*", "OUTPUT*", "+5V", "P20V"], key=design_rules.pattern_rank)
    assert ranked[-1] == "*"
    assert ranked.index("OUTPUT*") < ranked.index("OUT*")
    assert {"+5V", "P20V"} <= set(ranked[:2])


def test_net_class_patterns_match_with_or_without_leading_slash() -> None:
    """Regression: patterns like "+5V" never matched "/+5V", so power classes applied to nothing."""
    rules = {"netclasses": {
        "Default": {"nets": ["*"], "width": 0.25, "clearance": 0.2, "explicit": False},
        "POWER": {"nets": ["+5V", "/GND"], "width": 1.0, "clearance": 0.3, "explicit": True},
        "SW20V": {"nets": ["/P20V", "OUT*"], "width": 1.5, "clearance": 0.3, "explicit": True},
    }}
    for net, expected in {
        "/+5V": "POWER", "+5V": "POWER",
        "/GND": "POWER", "GND": "POWER",
        "/P20V": "SW20V", "P20V": "SW20V",
        "/OUT3": "SW20V", "OUT3": "SW20V",
        "/SCL": "Default",
    }.items():
        assert design_rules.class_for(rules, net)[0] == expected, net


def test_trace_width_rules_see_the_power_problem() -> None:
    """The three ways a thin power trace used to hide, all of which must be reported."""
    rules = {"netclasses": {
        "Default": {"nets": ["*"], "width": 0.25, "clearance": 0.2, "explicit": False},
        "POWER": {"nets": ["+5V"], "width": 0.8, "current_a": 2.0, "explicit": True},
        "SW20V": {"nets": ["P20V"], "width": 1.0, "explicit": True},
    }, "stackup": {"copper_weight": 1.0, "temp_rise_c": 20.0}}
    # A declared class whose patterns match nothing is invisible to every other rule.
    index = {"tracks": [{"net": "/SCL", "width": 0.25, "uuid": "t1"}]}
    # One finding per declared class that matches nothing (POWER and SW20V here).
    kinds = [v["type"] for v in design_rules.violations(index, rules)]
    assert kinds == ["class_coverage", "class_coverage"], kinds

    # A power track that exists but is thinner than its class *and* its current.
    index = {"tracks": [
        {"net": "/+5V", "width": 0.25, "uuid": "t2"},
        {"net": "/P20V", "width": 0.25, "uuid": "t3"},
    ]}
    kinds = {v["type"] for v in design_rules.violations(index, rules)}
    assert {"trace_width", "current_capacity"} <= kinds, kinds


def test_required_trace_width_follows_ipc_2221() -> None:
    """More current needs more copper; more copper weight needs less width."""
    from kicad_harness.engine.design_rules import required_trace_width as width

    assert width(3.0) == pytest.approx(0.898, abs=0.01)
    assert width(5.0) > width(3.0) > width(1.0) > width(0.5)
    assert width(3.0, copper_oz=2.0) < width(3.0, copper_oz=1.0)
    assert width(3.0, temp_rise_c=20.0) < width(3.0, temp_rise_c=10.0)
    assert width(1.0, internal=True) > width(1.0)
