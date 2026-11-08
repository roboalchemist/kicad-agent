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
