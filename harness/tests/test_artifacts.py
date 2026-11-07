"""Artifact cache keyed by board hash: each artifact is built once per hash."""

from __future__ import annotations

import hashlib
import threading
from collections import Counter

import pytest

from _engine_support import FIXTURE, board, engine, engine_config, save_output  # noqa: F401
from kicad_harness.config import Config
from kicad_harness.engine import artifacts as artifacts_mod
from kicad_harness.engine import kicad_cli, render
from kicad_harness.engine.artifacts import Artifacts
from kicad_harness.engine.client import EngineError


class CountingEngine:
    """Wraps an Engine and counts calls per method."""

    def __init__(self, engine):
        self._engine = engine
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name):
        attr = getattr(self._engine, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return wrapper


@pytest.fixture
def counted(monkeypatch):
    """Count calls of the kicad-cli and render functions the cache builds with."""
    calls: Counter[str] = Counter()
    for mod, name in ((kicad_cli, "export_svg"), (kicad_cli, "drc"), (kicad_cli, "stats"),
                      (render, "render_2d"), (render, "render_3d_view")):
        original = getattr(mod, name)

        def wrapper(*args, _original=original, _name=name, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(mod, name, wrapper)
    return calls


@pytest.fixture
def arts(engine, tmp_path):
    cfg = Config(home=tmp_path / "home").ensure_dirs()
    return Artifacts(cfg, CountingEngine(engine))


def test_materialize_is_content_addressed(arts):
    data = FIXTURE.read_bytes()
    h, path = arts.materialize(data)
    assert h == hashlib.sha256(data).hexdigest()[:16] == artifacts_mod.board_hash(data)
    # The cache path carries artifacts.CACHE_FORMAT so a plot/theme change can invalidate it.
    assert path == arts.dir(h) / "board.kicad_pcb" and path.read_bytes() == data
    assert arts.dir(h).parent.name == artifacts_mod.CACHE_FORMAT
    mtime = path.stat().st_mtime_ns
    assert arts.materialize(data) == (h, path)
    assert path.stat().st_mtime_ns == mtime  # not rewritten
    h2, _ = arts.materialize(data + b"\n")
    assert h2 != h


def test_index_svg_drc_stats_build_once(arts, counted):
    h, _ = arts.materialize(FIXTURE.read_bytes())
    first = arts.index(h)
    assert arts.index(h) == first and len(first["footprints"]) == 8
    assert arts.engine.calls["index"] == 1

    front, back = arts.svg(h, "front"), arts.svg(h, "back")
    assert arts.svg(h, "front") == front and arts.svg(h, "back") == back
    assert counted["export_svg"] == 2
    assert 'viewBox="0.0000 0.0000 24.9936 19.9898"' in front and front != back

    report = arts.drc(h)
    assert arts.drc(h) == report and report["counts"]["errors"] == 0
    stats = arts.stats(h)
    assert arts.stats(h) == stats and stats["board"]["width"] == 25.0
    assert counted["drc"] == 1 and counted["stats"] == 1
    assert sorted(p.name for p in arts.dir(h).iterdir()) == [
        "back.svg", "board.kicad_pcb", "drc.json", "front.svg", "index.json", "stats.json"]


def test_svg_can_be_plotted_one_layer_at_a_time(arts, counted):
    """The layer panel asks for one layer at a time; each subset is built once and cached."""
    h, _ = arts.materialize(FIXTURE.read_bytes())
    copper = arts.svg(h, "front", ["F.Cu"])
    assert "#C83434" in copper, "F.Cu is plotted in the copper colour"
    silk = arts.svg(h, "front", ["F.SilkS"])
    assert silk != copper
    assert arts.svg(h, "front", ["F.Cu"]) == copper, "the per-layer SVG is cached by hash and subset"
    # Request order does not create a second cache entry: the plot order is canonical.
    assert arts.svg(h, "front", ["Edge.Cuts", "F.Cu"]) == arts.svg(h, "front", ["F.Cu", "Edge.Cuts"])
    assert counted["export_svg"] == 3
    with pytest.raises(EngineError):
        arts.svg(h, "front", ["B.Cu"])  # a back layer is not a front-side layer
    with pytest.raises(EngineError):
        arts.svg(h, "front", [])


def test_concurrent_builds_happen_once(arts):
    h, _ = arts.materialize(FIXTURE.read_bytes())
    barrier = threading.Barrier(4)
    results = []

    def work():
        barrier.wait()
        results.append(arts.index(h))

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4 and all(r == results[0] for r in results)
    assert arts.engine.calls["index"] == 1


def test_render_once_per_spec(arts, counted):
    h, _ = arts.materialize(FIXTURE.read_bytes())
    spec = {"mode": "2d", "side": "front", "center": [9, 10], "view_mm": 8, "width_px": 400, "highlight": ["U1"]}
    path, url = arts.render(h, spec)
    assert url == f"/api/renders/{path.name}" and path.parent == arts.config.renders
    assert path.name.startswith(f"{artifacts_mod.CACHE_FORMAT}-{h}-") and path.read_bytes()[:4] == b"\x89PNG"
    assert render.png_size(path.read_bytes()) == (400, 400)
    again = arts.render(h, dict(reversed(list(spec.items()))))  # key order doesn't matter
    assert again == (path, url) and counted["render_2d"] == 1

    p3, _ = arts.render(h, {"mode": "3d", "side": "top", "center": [9, 10], "view_mm": 10, "width": 400,
                            "height": 300})
    assert render.png_size(p3.read_bytes()) == (400, 300)
    save_output("artifacts_render_3d.png", p3.read_bytes())
    back, _ = arts.render(h, {"side": "back", "width_px": 300})
    assert render.png_size(back.read_bytes())[0] == 300
    assert counted["render_3d_view"] == 1 and counted["render_2d"] == 2


def test_errors(arts):
    with pytest.raises(EngineError) as err:
        arts.index("0123456789abcdef")
    assert err.value.type == "NotFound"
    with pytest.raises(EngineError):
        arts.index("../etc")
    h, _ = arts.materialize(FIXTURE.read_bytes())
    with pytest.raises(EngineError) as err:
        arts.render(h, {"mode": "2d", "zoom": 3})
    assert err.value.type == "InvalidParams"
    with pytest.raises(EngineError):
        arts.svg(h, "left")
