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
    assert next(c for c in stale["criteria"] if c["id"] == "mfg")["passed"] is False
    assert next(c for c in stale["criteria"] if c["id"] == "align")["passed"] is False


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
    assert layout.analyze(doc)["score"] == 0
    assert kicad_cli.erc(live / "schematic.kicad_sch")["counts"]["errors"] == 0


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
