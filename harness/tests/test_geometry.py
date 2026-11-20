"""geometry.resolve / measure / clearance over real pick indexes."""

from __future__ import annotations

import math

import pytest

from _engine_support import board, by_ref, engine, engine_config, fixture_index, pad  # noqa: F401
from kicad_harness.engine import geometry
from kicad_harness.engine.client import EngineError


def test_resolve_specs(fixture_index):
    idx = fixture_index
    (u1,) = geometry.resolve(idx, "U1")
    assert u1["kind"] == "footprint" and u1["ref"] == "U1"
    (p3,) = geometry.resolve(idx, "U1.3")
    assert p3["kind"] == "pad" and p3["net"] == "OUTPUT"
    gnd = geometry.resolve(idx, "net:GND")
    assert {e["kind"] for e in gnd} == {"pad", "track"}
    assert all(e["net"] == "GND" for e in gnd)
    assert len([e for e in gnd if e["kind"] == "pad"]) == len([p for p in idx["pads"] if p["net"] == "GND"])
    via = idx["vias"][0]
    assert geometry.resolve(idx, via["uuid"])[0] == dict(via, kind="via")
    assert geometry.resolve(idx, "10, 12.5") == [{"kind": "point", "x": 10.0, "y": 12.5}]
    assert geometry.resolve(idx, [3, 4]) == [{"kind": "point", "x": 3.0, "y": 4.0}]
    assert geometry.resolve(idx, "u1")[0]["ref"] == "U1"  # case-insensitive fallback
    assert geometry.resolve(idx, dict(via))[0]["kind"] == "via"


@pytest.mark.parametrize("spec,needle", [
    ("U9", "C1, C2, C3, D1, R1, R2, R3, U1"),
    ("U1.9", "1, 2, 3, 4, 5, 6, 7, 8"),
    ("net:NOPE", "GND"),
    ("00000000-0000-0000-0000-000000000000", "uuid"),
])
def test_resolve_errors(fixture_index, spec, needle):
    with pytest.raises(EngineError) as err:
        geometry.resolve(fixture_index, spec)
    assert err.value.type == "NotFound" and needle in err.value.message


def test_measure_known_distances(fixture_index):
    idx = fixture_index
    # Adjacent SOIC pads: 1.27 mm pitch, 0.588 mm wide -> 0.682 mm gap.
    m = geometry.measure(idx, "U1.1", "U1.2")
    assert m["distance"] == pytest.approx(1.27 - 0.588011, abs=0.012)
    assert m["a"]["ref"] == "U1" and m["a"]["pad"] == "1" and m["b"]["pad"] == "2"
    assert m["b_point"][0] - m["a_point"][0] == pytest.approx(m["distance"], abs=1e-3)

    # A point beside pad 3's straight flank: 10 - (9.635 + 0.294).
    m = geometry.measure(idx, "10,12.5", "U1.3")
    assert m["distance"] == pytest.approx(10 - 9.635001 - 0.588011 / 2, abs=0.006)

    # Footprints are the union of their pads: C3 to U1 is a pad corner to an oval pad's end cap.
    m = geometry.measure(idx, "C3", "U1")
    r = 0.588011 / 2
    cap_y = 12.569977 - 2.045009 / 2 + r  # U1.1's upper cap centre; C3.2's corner is (5.675, 11.705)
    expect = math.hypot(7.094996 - 5.675, cap_y - 11.705) - r
    assert m["distance"] == pytest.approx(expect, abs=0.012)
    assert m["a"]["ref"] == "C3" and m["b"]["ref"] == "U1" and m["a"]["kind"] == "pad"
    body = geometry.measure(idx, "C3", "U1", body=True)  # bounding boxes (no courtyards here)
    assert body["distance"] < m["distance"] and body["a"]["kind"] == "footprint"


def test_measure_touching_is_zero(fixture_index):
    idx = fixture_index
    p1 = pad(idx, "U1", "1")
    track = next(t for t in idx["tracks"]
                 if any(math.isclose(x, p1["x"], abs_tol=0.01) and math.isclose(y, p1["y"], abs_tol=0.01)
                        for x, y in ((t["x1"], t["y1"]), (t["x2"], t["y2"]))))
    assert geometry.measure(idx, "U1.1", track["uuid"])["distance"] == 0.0
    a = {"kind": "pad", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "x": 0.5, "y": 0.5}
    b = {"kind": "pad", "polygon": [[1, 0], [2, 0], [2, 1], [1, 1]], "x": 1.5, "y": 0.5}
    assert geometry.measure(idx, a, b)["distance"] == 0.0
    v = idx["vias"][0]
    assert geometry.measure(idx, v["uuid"], f"{v['x'] + 1},{v['y']}")["distance"] == pytest.approx(1 - v["size"] / 2,
                                                                                                    abs=1e-3)


def test_clearance(engine, board, fixture_index):
    res = geometry.clearance(fixture_index, "GND", "VCC")
    assert res["layer"] == "F.Cu" and 0.2 <= res["distance"] < 2
    assert res["a"]["net"] == "GND" and res["b"]["net"] == "VCC"
    with pytest.raises(EngineError):  # GND has no copper on B.Cu yet
        geometry.clearance(fixture_index, "GND", "VCC", layer="B.Cu")

    engine.add_zone(board, "GND", "B.Cu", clearance=0.3)
    idx = engine.index(board)
    res = geometry.clearance(idx, "net:GND", "net:VCC", layer="B.Cu")
    assert res["a"]["kind"] == "zone"
    assert 0.299 <= res["distance"] <= 0.36  # the pour keeps its 0.3 mm clearance

    engine.move_footprint(board, "R3", 9, 10)  # onto U1: shorts
    assert geometry.clearance(engine.index(board), "OUTPUT", "THRESHOLD")["distance"] == 0.0


def test_clearance_query_by_region_and_layer(fixture_index) -> None:
    """Regression: a layer-scoped region query raised KeyError('kind') on bare index records."""
    from kicad_harness.engine import geometry

    tracks = fixture_index.get("tracks") or []
    track = next(t for t in tracks if t.get("net"))
    mid = ((track["x1"] + track["x2"]) / 2, (track["y1"] + track["y2"]) / 2)
    region = [mid[0] - 1, mid[1] - 1, mid[0] + 1, mid[1] + 1]
    found = geometry.copper_in_region(fixture_index, region, track["layer"])
    assert found, "the region contains the track"
    assert all(item["kind"] for item in found)
    assert all(track["layer"] in geometry._layers_of(item, fixture_index) for item in found)
