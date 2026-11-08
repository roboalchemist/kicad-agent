"""Engine worker + client against the real KiCad 10 pcbnew."""

from __future__ import annotations

import os
import re
import signal
import time
from pathlib import Path

import pytest

from _engine_support import FIXTURE, NETS, REFS, board, by_ref, engine, engine_config, fixture_index, pad  # noqa: F401
from kicad_harness.engine.client import Engine, EngineError

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def all_uuids(index: dict) -> list[str]:
    return [e["uuid"] for key in ("footprints", "pads", "tracks", "vias", "zones") for e in index[key]]


# --------------------------------------------------------------------------- protocol


def test_ping(engine):
    assert engine.ping()["pcbnew"].startswith("10.")


def test_unknown_method_and_bad_params(engine, board):
    with pytest.raises(EngineError) as err:
        engine.call("no_such_method")
    assert err.value.type == "UnknownMethod" and "move_footprint" in err.value.message
    with pytest.raises(EngineError) as err:
        engine.call("move_footprint", board, ref="U1")  # x, y missing
    assert err.value.type == "InvalidParams" and "'x'" in err.value.message
    with pytest.raises(EngineError) as err:
        engine.index(board.parent / "missing.kicad_pcb")
    assert err.value.type == "NotFound"


def test_worker_restarts_after_dying(engine_config):
    with Engine(engine_config) as eng:
        eng.ping()
        pid = eng.pid
        assert pid
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
        assert eng.ping()["pcbnew"].startswith("10.")
        assert eng.pid not in (None, pid)
        assert eng.restarts == 1


def test_stdout_stays_clean_under_noise(engine, board):
    # SaveBoard makes wx print debug lines; they must not corrupt the protocol.
    for _ in range(3):
        engine.move_footprint(board, "R1", 4.5, 3)
    assert engine.ping()["pcbnew"]


# --------------------------------------------------------------------------- index


def test_index_shape(fixture_index):
    idx = fixture_index
    assert set(idx) == {"board", "nets", "footprints", "pads", "tracks", "vias", "zones"}
    b = idx["board"]
    assert b["edge_bbox"] == [0.0, 0.0, 25.0, 20.0]  # outline geometry, no stroke
    assert b["bbox"][0] <= 0 and b["bbox"][2] >= 25  # covers all items (incl. the 0.05 stroke)
    assert b["layers"] == ["F.Cu", "B.Cu"]
    assert b["thickness"] == 1.6
    assert idx["nets"] == NETS

    assert [f["ref"] for f in idx["footprints"]] == REFS
    u1 = by_ref(idx, "U1")
    assert (u1["x"], u1["y"], u1["rotation"], u1["side"]) == (9.0, 10.0, 0.0, "front")
    assert u1["value"] == "NE555DR" and u1["footprint"] == "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL"
    for f in idx["footprints"]:
        assert set(f) >= {"uuid", "ref", "value", "footprint", "x", "y", "rotation", "side", "bbox", "courtyard",
                          "ref_text"}
        assert len(f["bbox"]) == 4 and f["bbox"][2] > 0
        assert set(f["ref_text"]) == {"x", "y", "bbox", "visible", "layer", "height"}
        assert f["ref_text"]["layer"] == "F.SilkS"
    assert by_ref(idx, "C3")["rotation"] == -90.0
    assert by_ref(idx, "U1")["ref_text"]["x"] == 14.0 and by_ref(idx, "U1")["ref_text"]["y"] == 8.0

    assert len(idx["pads"]) == 22
    p1 = pad(idx, "U1", "1")
    assert p1["net"] == "GND" and p1["shape"] == "oval" and p1["layers"] == ["F.Cu", "F.Mask", "F.Paste"]
    assert (p1["x"], p1["y"]) == pytest.approx((7.094996, 12.569977))
    assert (p1["w"], p1["h"]) == pytest.approx((0.588011, 2.045009))
    assert p1["through"] is False and p1["drill"] == 0.0
    xs = [p[0] for p in p1["polygon"]]
    ys = [p[1] for p in p1["polygon"]]
    assert len(p1["polygon"]) > 8  # rounded ends
    assert min(xs) == pytest.approx(p1["x"] - p1["w"] / 2, abs=0.01)
    assert max(ys) == pytest.approx(p1["y"] + p1["h"] / 2, abs=0.01)
    assert pad(idx, "C1", "1")["shape"] == "rect"
    for p in idx["pads"]:
        assert set(p) == {"uuid", "ref", "pad", "net", "x", "y", "w", "h", "rotation", "shape", "layers", "through",
                          "drill", "polygon"}

    assert len(idx["tracks"]) == 43 and len(idx["vias"]) == 2 and idx["zones"] == []
    t = idx["tracks"][0]
    assert set(t) == {"uuid", "net", "layer", "x1", "y1", "x2", "y2", "width"}
    assert {t["layer"] for t in idx["tracks"]} == {"F.Cu", "B.Cu"}
    via = next(v for v in idx["vias"] if v["x"] == 12.305)
    assert via == {"uuid": via["uuid"], "net": "VCC", "x": 12.305, "y": 12.57, "size": 0.6,
                   "drill": 0.3, "type": "through", "layers": ["F.Cu", "B.Cu"]}

    uuids = all_uuids(idx)
    assert len(uuids) == len(set(uuids)) and all(UUID.match(u) for u in uuids)


# --------------------------------------------------------------------------- edits


def test_move_footprint(engine, board):
    res = engine.move_footprint(board, "R3", 17, 13.5, rotation=90)
    assert res == {"ref": "R3", "before": {"x": 17.0, "y": 12.0, "rotation": 0.0, "side": "front"},
                   "after": {"x": 17.0, "y": 13.5, "rotation": 90.0, "side": "front"}}
    r3 = by_ref(engine.index(board), "R3")
    assert (r3["x"], r3["y"], r3["rotation"]) == (17.0, 13.5, 90.0)
    p = pad(engine.index(board), "R3", "1")  # pad 1 sits at local (-1, 0): rotated 90° CCW -> above
    assert (p["x"], p["y"]) == pytest.approx((17.0, 14.5))


def test_rotate_footprint_is_relative_and_normalized(engine, board):
    assert engine.rotate_footprint(board, "U1", 90)["after"]["rotation"] == 90.0
    assert engine.rotate_footprint(board, "U1", 135)["after"]["rotation"] == -135.0
    assert engine.rotate_footprint(board, "U1", -45)["after"]["rotation"] == 180.0
    u1 = by_ref(engine.index(board), "U1")
    assert (u1["x"], u1["y"], u1["rotation"]) == (9.0, 10.0, 180.0)


def test_flip_footprint(engine, board):
    res = engine.flip_footprint(board, "C3")
    assert res["before"]["side"] == "front" and res["after"]["side"] == "back"
    assert (res["after"]["x"], res["after"]["y"]) == (5.0, 10.0)
    idx = engine.index(board)
    assert by_ref(idx, "C3")["side"] == "back"
    assert all(p["layers"][0] == "B.Cu" for p in idx["pads"] if p["ref"] == "C3")
    assert by_ref(idx, "C3")["ref_text"]["layer"] == "B.SilkS"
    assert engine.flip_footprint(board, "C3")["after"]["side"] == "front"


def test_move_reference_text(engine, board):
    res = engine.move_reference_text(board, "U1", 9, 4.5)
    assert res == {"ref": "U1", "before": {"x": 14.0, "y": 8.0}, "after": {"x": 9.0, "y": 4.5}}
    rt = by_ref(engine.index(board), "U1")["ref_text"]
    assert (rt["x"], rt["y"]) == (9.0, 4.5)


def test_delete_tracks_by_net_uuid_and_all(engine, board, fixture_index):
    gnd = [t for t in fixture_index["tracks"] if t["net"] == "GND"]
    assert engine.delete_tracks(board, nets=["net:GND"])["deleted"] == len(gnd)
    idx = engine.index(board)
    assert not [t for t in idx["tracks"] if t["net"] == "GND"]
    assert len(idx["tracks"]) == 43 - len(gnd) and len(idx["vias"]) == 2

    victims = [idx["tracks"][0]["uuid"], idx["vias"][0]["uuid"]]
    assert engine.delete_tracks(board, uuids=victims)["deleted"] == 2
    idx = engine.index(board)
    assert not set(victims) & set(all_uuids(idx))

    assert engine.delete_tracks(board)["deleted"] == len(idx["tracks"]) + len(idx["vias"])
    idx = engine.index(board)
    assert idx["tracks"] == [] and idx["vias"] == []
    assert len(idx["pads"]) == 22


def test_delete_items(engine, board, fixture_index):
    d1 = by_ref(fixture_index, "D1")["uuid"]
    via = fixture_index["vias"][0]["uuid"]
    d1_pad = pad(fixture_index, "D1", "1")["uuid"]  # child of a footprint deleted in the same call
    r1_pad = pad(fixture_index, "R1", "2")["uuid"]
    assert engine.delete_items(board, [d1, via, d1_pad, r1_pad]) == {"deleted": 4}
    idx = engine.index(board)
    assert "D1" not in [f["ref"] for f in idx["footprints"]]
    assert via not in all_uuids(idx) and r1_pad not in all_uuids(idx)
    assert [p["pad"] for p in idx["pads"] if p["ref"] == "R1"] == ["1"]


def test_add_track_and_via(engine, board):
    res = engine.add_track(board, "GND", [[1, 1], [2, 1], [2, 2.5]], width=0.3, layer="B.Cu")
    assert len(res["uuids"]) == 2
    idx = engine.index(board)
    new = [t for t in idx["tracks"] if t["uuid"] in res["uuids"]]
    assert sorted((t["x1"], t["y1"], t["x2"], t["y2"]) for t in new) == [(1.0, 1.0, 2.0, 1.0), (2.0, 1.0, 2.0, 2.5)]
    assert all(t["net"] == "GND" and t["layer"] == "B.Cu" and t["width"] == 0.3 for t in new)

    via = engine.add_via(board, 3, 3.5, "VCC", size=0.8, drill=0.4)["uuid"]
    v = next(v for v in engine.index(board)["vias"] if v["uuid"] == via)
    assert v == {"uuid": via, "net": "VCC", "x": 3.0, "y": 3.5, "size": 0.8, "drill": 0.4,
                 "type": "through", "layers": ["F.Cu", "B.Cu"]}


def test_set_outline(engine, board):
    assert engine.set_outline(board, 30, 24, x=1, y=2, corner_radius=2) == {"bbox": [1.0, 2.0, 30.0, 24.0]}
    idx = engine.index(board)
    assert idx["board"]["edge_bbox"] == [1.0, 2.0, 30.0, 24.0]
    text = board.read_text()
    assert text.count('(layer "Edge.Cuts")') == 1 and "(radius 2)" in text


def test_add_zone_and_refill(engine, board):
    zid = engine.add_zone(board, "GND", "B.Cu")["uuid"]
    idx = engine.index(board)
    (zone,) = idx["zones"]
    assert zone["uuid"] == zid and zone["net"] == "GND" and zone["layer"] == "B.Cu"
    xs = [p[0] for p in zone["outline"]]
    ys = [p[1] for p in zone["outline"]]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((0.5, 0.5, 24.5, 19.5), abs=0.01)
    assert zone["filled"] and all(len(poly) >= 3 for poly in zone["filled"])
    assert engine.refill_zones(board) == {"zones": 1}

    custom = engine.add_zone(board, "VCC", "F.Cu", polygon=[[1, 1], [4, 1], [4, 4], [1, 4]])["uuid"]
    z = next(z for z in engine.index(board)["zones"] if z["uuid"] == custom)
    assert sorted(map(tuple, z["outline"])) == [(1.0, 1.0), (1.0, 4.0), (4.0, 1.0), (4.0, 4.0)]


def test_refill_without_zones_leaves_file_alone(engine, board):
    before = board.read_bytes()
    assert engine.refill_zones(board) == {"zones": 0}
    assert board.read_bytes() == before


def test_edits_refill_existing_zones(engine, board):
    engine.add_zone(board, "GND", "B.Cu")
    before = engine.index(board)["zones"][0]["filled"]
    engine.add_via(board, 20, 4, "VCC")  # a foreign via must get a clearance hole in the GND pour
    after = engine.index(board)["zones"][0]["filled"]
    assert after != before


def test_add_and_remove_footprint(engine, board):
    res = engine.add_footprint(board, "Resistor_SMD:R_0805_2012Metric", "R9", "1k", 21, 5)
    assert res["ref"] == "R9" and [p["pad"] for p in res["pads"]] == ["1", "2"]
    idx = engine.index(board)
    r9 = by_ref(idx, "R9")
    assert (r9["x"], r9["y"], r9["side"], r9["value"], r9["footprint"]) == (21.0, 5.0, "front", "1k", "R_0805_2012Metric")
    assert r9["courtyard"] and len(r9["courtyard"]) >= 4
    assert len(set(all_uuids(idx))) == len(all_uuids(idx))

    res = engine.add_footprint(board, "R0805", "R10", "2k", 21, 9, rotation=90, side="back")  # bare demo name
    r10 = by_ref(engine.index(board), "R10")
    assert (r10["side"], r10["rotation"]) == ("back", 90.0)
    assert all(p["layers"][0] == "B.Cu" for p in res["pads"])

    assert engine.remove_footprint(board, "R9") == {"ref": "R9", "uuid": r9["uuid"]}
    assert "R9" not in [f["ref"] for f in engine.index(board)["footprints"]]


def test_add_footprint_errors(engine, board):
    with pytest.raises(EngineError) as err:
        engine.add_footprint(board, "Resistor_SMD:R_0805_2012Metrik", "R9", "1k", 21, 5)
    assert err.value.type == "NotFound" and "Resistor_SMD:R_0805_2012Metric" in err.value.message
    with pytest.raises(EngineError) as err:
        engine.add_footprint(board, "R0805", "R1", "1k", 21, 5)
    assert err.value.type == "InvalidParams" and "R1" in err.value.message


def test_set_pad_net_creates_nets(engine, board):
    assert engine.set_pad_net(board, "R1", 1, "NEWNET") == {"ref": "R1", "pad": "1", "net": "NEWNET"}
    idx = engine.index(board)
    assert pad(idx, "R1", "1")["net"] == "NEWNET" and "NEWNET" in idx["nets"]
    engine.set_pad_net(board, "R1", "2", "GND")
    assert pad(engine.index(board), "R1", "2")["net"] == "GND"


FAILING_EDITS = [
    ("move_footprint", {"ref": "U9", "x": 1, "y": 1}, "NotFound", "Valid refs: C1, C2, C3, D1, R1, R2, R3, U1"),
    ("rotate_footprint", {"ref": "U1", "angle": "abc"}, "InvalidParams", "angle"),
    ("flip_footprint", {"ref": "Q1"}, "NotFound", "Valid refs"),
    ("move_reference_text", {"ref": "X", "x": 1, "y": 1}, "NotFound", "Valid refs"),
    ("delete_tracks", {"nets": ["NOPE"]}, "NotFound", "GND"),
    ("delete_tracks", {"uuids": ["00000000-0000-0000-0000-000000000000"]}, "NotFound", "track"),
    ("delete_items", {"uuids": ["00000000-0000-0000-0000-000000000000"]}, "NotFound", "uuid"),
    ("add_track", {"net": "NOPE", "points": [[1, 1], [2, 2]]}, "NotFound", "Nets:"),
    ("add_track", {"net": "GND", "points": [[1, 1], [2, 2]], "layer": "F.SilkS"}, "InvalidParams", "F.Cu, B.Cu"),
    ("add_track", {"net": "GND", "points": [[1, 1]]}, "InvalidParams", "points"),
    ("add_via", {"x": 1, "y": 1, "net": "GND", "size": 0.3, "drill": 0.4}, "InvalidParams", "drill"),
    ("set_outline", {"width": 10, "height": 10, "corner_radius": 6}, "InvalidParams", "corner_radius"),
    ("add_zone", {"net": "GND", "layer": "Edge.Cuts"}, "InvalidParams", "copper"),
    ("add_zone", {"net": "GND", "layer": "B.Cu", "margin": 50}, "InvalidParams", "margin"),
    ("remove_footprint", {"ref": "U7"}, "NotFound", "Valid refs"),
    ("set_pad_net", {"ref": "U1", "pad": "9", "net": "GND"}, "NotFound", "1, 2, 3, 4, 5, 6, 7, 8"),
    ("import_ses", {"path": "/nonexistent/x.ses"}, "NotFound", "Session"),
]


@pytest.mark.parametrize("method,params,etype,needle", FAILING_EDITS, ids=[f"{m}-{t}" for m, _, t, _ in FAILING_EDITS])
def test_failed_edit_leaves_file_untouched(engine, board, method, params, etype, needle):
    before = board.read_bytes()
    st = board.stat()
    with pytest.raises(EngineError) as err:
        engine.call(method, board, **params)
    assert err.value.type == etype
    assert needle in err.value.message
    assert board.read_bytes() == before
    assert board.stat().st_mtime_ns == st.st_mtime_ns
    assert not [p for p in board.parent.iterdir() if p.name != board.name]  # no temp or side files


# --------------------------------------------------------------------------- normalize


def test_normalize_dedupes_uuids(engine, tmp_path, fixture_index):
    text = FIXTURE.read_text()
    c1 = [pad(fixture_index, "C1", n)["uuid"] for n in ("1", "2")]
    c2 = [pad(fixture_index, "C2", n)["uuid"] for n in ("1", "2")]
    for old, new in zip(c2, c1):  # C2's pads now carry C1's pad UUIDs
        assert text.count(old) == 1
        text = text.replace(old, new)
    dup = tmp_path / "dup.kicad_pcb"
    dup.write_text(text)
    assert len(all_uuids(engine.index(dup))) - len(set(all_uuids(engine.index(dup)))) == 2

    assert engine.normalize(dup)["replaced"] == 2
    idx = engine.index(dup)
    uuids = all_uuids(idx)
    assert len(uuids) == len(set(uuids))
    kept = {pad(idx, "C1", n)["uuid"] for n in ("1", "2")} | {pad(idx, "C2", n)["uuid"] for n in ("1", "2")}
    assert set(c1) <= kept  # one copy of each keeps its UUID
    assert re.search(r"\(version 2026\d+\)", dup.read_text())
    assert engine.normalize(dup) == {"replaced": 0}


def test_normalize_upgrades_format(engine, board):
    text = board.read_text()
    assert engine.normalize(board) == {"replaced": 0}
    assert re.search(r"\(version 2026\d+\)", board.read_text())
    assert len(engine.index(board)["tracks"]) == text.count("\t(segment")


def test_export_dsn(engine, board, tmp_path):
    out = tmp_path / "x.dsn"
    assert engine.export_dsn(board, out) == {"path": str(out)}
    dsn = out.read_text()
    assert dsn.startswith("(pcb") and "(network" in dsn and "U1" in dsn


def test_moves_several_reference_texts_in_one_save(engine, board):
    result = engine.move_reference_texts(board, [
        {"ref": "U1", "x": 4.0, "y": 5.0},
        {"ref": "C1", "x": 6.0, "y": 7.0},
    ])
    assert result["moved"] == 2
    from kicad_harness.engine.geometry import resolve
    index = engine.index(board)
    u1 = next(f for f in index["footprints"] if f["ref"] == "U1")
    assert (u1["ref_text"]["x"], u1["ref_text"]["y"]) == (4.0, 5.0)
