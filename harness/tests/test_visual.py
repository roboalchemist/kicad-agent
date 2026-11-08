"""Close-up rendering, clearance selectors, probe and the geometry-only QA checks."""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image
from shapely.geometry import Polygon

from _engine_support import (  # noqa: F401
    FIXTURE, board, engine, engine_config, fixture_index, pad, save_output,
)
from kicad_harness.engine import kicad_cli, qa, render


@pytest.fixture(scope="module")
def svgs(tmp_path_factory, engine_config):
    tmp = tmp_path_factory.mktemp("visual-svg")
    return kicad_cli.export_svg(FIXTURE, ["F.Cu", "F.Mask", "F.SilkS", "Edge.Cuts"],
                                tmp / "front.svg", config=engine_config).read_text()


def test_clearance_query_supports_nets_entities_and_regions(fixture_index):
    from kicad_harness.engine import geometry

    both = geometry.clearance_query(fixture_index, "net:GND", "net:VCC")
    assert both["method"] == "net-net" and both["layer"] == "F.Cu"
    assert both["net_a"] == "GND" and both["net_b"] == "VCC"

    nearest = geometry.clearance_query(fixture_index, "net:OUTPUT")
    assert nearest["method"] == "net-other" and nearest["distance"] >= 0

    region = geometry.clearance_query(fixture_index, region=[0, 0, 30, 30])
    assert region["method"] == "region" and region["net_a"] != region["net_b"]
    assert region["distance"] <= both["distance"] + 5

    entity = geometry.clearance_query(fixture_index, "U1.1", "U1.2")
    assert entity["method"] == "entity"
    assert entity["distance"] == pytest.approx(0.682, abs=0.02)


def test_probe_returns_the_pad_under_a_point(fixture_index):
    from kicad_harness.engine import geometry

    target = pad(fixture_index, "U1", "1")
    result = geometry.probe(fixture_index, target["x"], target["y"])
    assert result["within_board"] is True
    assert any(hit["kind"] == "pad" and hit["ref"] == "U1" and hit["pad"] == "1"
               for hit in result["hits"])


def test_qa_silkscreen_flags_text_over_pad_and_small_text():
    index = {
        "board": {"edge_bbox": [0, 0, 40, 30]},
        "footprints": [{
            "ref": "R1", "bbox": [5, 5, 6, 3],
            "ref_text": {"visible": True, "layer": "F.SilkS", "height": 0.6,
                         "bbox": [5, 5, 4, 1.2]},
        }, {
            "ref": "R2", "bbox": [0.2, 10, 4, 2],
            "ref_text": {"visible": True, "layer": "F.SilkS", "height": 1.2,
                         "bbox": [0.2, 10, 3, 1.2]},
        }],
        "pads": [{
            "ref": "C1", "pad": "1", "net": "GND", "x": 6, "y": 5.5, "w": 1.2, "h": 0.8,
            "rotation": 0, "layers": ["F.Cu", "F.Mask"],
            "polygon": [[5.4, 5.1], [6.6, 5.1], [6.6, 5.9], [5.4, 5.9]],
        }],
        "vias": [],
    }
    result = qa.silkscreen(index)
    assert result["passed"] is False
    assert any(item["type"] == "text_over_pad" and item["ref"] == "R1"
               for item in result["collisions"])
    assert result["undersized"] and result["undersized"][0]["ref"] == "R1"
    assert result["edge"] and result["edge"][0]["ref"] == "R2"
    assert result["rule"].startswith("pcb-silkscreen#")


def test_qa_courtyard_overlap_and_missing():
    index = {"footprints": [
        {"ref": "R1", "courtyard": [[0, 0], [4, 0], [4, 4], [0, 4]]},
        {"ref": "R2", "courtyard": [[2, 2], [6, 2], [6, 6], [2, 6]]},
        {"ref": "R3", "courtyard": None},
    ]}
    result = qa.courtyards(index)
    assert result["passed"] is False
    assert result["overlaps"][0]["a"] == "R1" and result["overlaps"][0]["b"] == "R2"
    assert result["overlaps"][0]["area_mm2"] == 4.0
    assert result["missing"] == ["R3"]


def test_qa_diff_reports_added_removed_and_changed():
    old = {"footprints": [{"uuid": "a", "ref": "R1", "x": 1, "y": 1, "rotation": 0, "side": "front",
                           "bbox": [0, 0, 2, 2], "courtyard": None}],
           "tracks": [], "pads": [], "vias": [], "zones": []}
    new = {"footprints": [{"uuid": "a", "ref": "R1", "x": 5, "y": 1, "rotation": 0, "side": "front",
                           "bbox": [4, 0, 2, 2], "courtyard": None},
                          {"uuid": "b", "ref": "R2", "x": 9, "y": 1, "rotation": 0, "side": "front",
                           "bbox": [8, 0, 2, 2], "courtyard": None}],
           "tracks": [], "pads": [], "vias": [], "zones": []}
    result = qa.diff_indexes(old, new)
    assert result["summary"] == {"added": 1, "removed": 0, "changed": 1}
    assert result["added"][0]["ref"] == "R2" and result["changed"][0]["ref"] == "R1"


def test_render_region_has_exact_aspect_and_scale_bar(svgs, fixture_index):
    png = render.render_2d(svgs, fixture_index, region=[5, 5, 15, 11], width_px=500,
                           pad_labels=True, scale_bar=True)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.size == (500, 300)


def test_schematic_view_crops_and_rasterizes(tmp_path, engine_config):
    from kicad_harness.engine import schematic as sch

    libs = {"Device": sch.read_library("/usr/share/kicad/symbols/Device.kicad_sym")}
    doc = sch.Schematic.create(tmp_path / "view.kicad_sch", libraries=libs)
    doc.add_symbol("Device:R", "R1", 60.0, 60.0)
    doc.save()
    png, view = render.schematic_view(tmp_path / "view.kicad_sch", region=[40, 40, 80, 60],
                                     width=600, highlight=[[55, 55, 65, 65]])
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.size == (600, 300)
    assert view["px_per_mm"] == 15.0 and view["w"] == 40 and view["h"] == 20
