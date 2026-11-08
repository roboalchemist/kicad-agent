"""Shapely measurements over the pick index (DESIGN.md, "geometry.py").

Entities are index entries with an added ``kind``: ``footprint``, ``pad``, ``track``, ``via``,
``zone`` or ``point``. Copper geometry: pads are their polygons, tracks are segments buffered
by half their width (round ends), vias are circles, zones are their filled polygons (their
outline when unfilled), and footprints are their pads (their courtyard, else bbox, with
``body=True``).
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from .client import EngineError

Entity = dict[str, Any]

_POINT_RE = re.compile(r"^\s*\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)?\s*$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_KINDS = (("footprints", "footprint"), ("pads", "pad"), ("tracks", "track"), ("vias", "via"), ("zones", "zone"))


def _natural_key(text: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def _with_kind(entry: dict[str, Any], kind: str) -> Entity:
    out = dict(entry)
    out["kind"] = kind
    return out


def _infer_kind(entry: dict[str, Any]) -> str:
    if "kind" in entry:
        return str(entry["kind"])
    if "pad" in entry and "polygon" in entry:
        return "pad"
    if "x1" in entry:
        return "track"
    if "drill" in entry and "size" in entry:
        return "via"
    if "outline" in entry:
        return "zone"
    if "ref" in entry and "footprint" in entry:
        return "footprint"
    if "x" in entry and "y" in entry:
        return "point"
    raise EngineError("InvalidParams", f"Can't tell what entity {entry!r} is")


def net_members(index: dict[str, Any], net: str) -> list[Entity]:
    """All copper on ``net``: pads, tracks, vias and zones."""
    out: list[Entity] = []
    for key, kind in _KINDS[1:]:
        out += [_with_kind(e, kind) for e in index.get(key, []) if e.get("net") == net]
    return out


def resolve(index: dict[str, Any], ref: Any) -> list[Entity]:
    """Resolve ``U1``, ``U1.3``, ``net:GND``, a UUID, ``"x,y"`` (or ``[x, y]``, or an entity dict)
    to entities. Raises ``EngineError("NotFound")`` with the valid choices when nothing matches."""
    if isinstance(ref, dict):
        return [_with_kind(ref, _infer_kind(ref))]
    if isinstance(ref, (list, tuple)) and len(ref) == 2 and all(isinstance(v, (int, float)) for v in ref):
        return [{"kind": "point", "x": float(ref[0]), "y": float(ref[1])}]
    if not isinstance(ref, str) or not ref.strip():
        raise EngineError("InvalidParams", f"Can't resolve {ref!r}: use U1, U1.3, net:GND, a UUID or x,y")
    spec = ref.strip()
    footprints = index.get("footprints", [])

    if spec.startswith("net:"):
        name = spec[4:]
        if name not in index.get("nets", []):
            nets = ", ".join(n for n in index.get("nets", []) if n)
            raise EngineError("NotFound", f"No net {name!r}. Nets: {nets}")
        return net_members(index, name)

    m = _POINT_RE.match(spec)
    if m:
        return [{"kind": "point", "x": float(m.group(1)), "y": float(m.group(2))}]

    if _UUID_RE.match(spec):
        for key, kind in _KINDS:
            for e in index.get(key, []):
                if e.get("uuid") == spec:
                    return [_with_kind(e, kind)]
        raise EngineError("NotFound", f"No entity with uuid {spec}")

    by_ref = {f["ref"]: f for f in footprints}
    if spec in by_ref:
        return [_with_kind(by_ref[spec], "footprint")]
    head, dot, pad = spec.rpartition(".")
    if dot and head:
        fp = by_ref.get(head) or next((f for r, f in by_ref.items() if r.lower() == head.lower()), None)
        if fp is not None:
            pads = [p for p in index.get("pads", []) if p["ref"] == fp["ref"] and p["pad"] == pad]
            if pads:
                return [_with_kind(p, "pad") for p in pads]
            nums = sorted({p["pad"] for p in index.get("pads", []) if p["ref"] == fp["ref"]}, key=_natural_key)
            raise EngineError("NotFound", f"{fp['ref']} has no pad {pad!r}. Its pads: {', '.join(nums)}")
    for r, f in by_ref.items():
        if r.lower() == spec.lower():
            return [_with_kind(f, "footprint")]
    refs = ", ".join(sorted(by_ref, key=_natural_key))
    raise EngineError("NotFound", f"Nothing matches {spec!r}. Use a ref ({refs}), REF.PAD, net:NAME, a UUID or x,y")


# --------------------------------------------------------------------------- shapes


def _polygon(points: Sequence[Sequence[float]]) -> BaseGeometry:
    if len(points) < 3:
        return LineString(points) if len(points) == 2 else Point(points[0]) if points else Polygon()
    poly = Polygon(points)
    return poly if poly.is_valid else shapely.make_valid(poly)


def _arc_points(p1: Sequence[float], mid: Sequence[float], p2: Sequence[float], n: int = 24) -> list[tuple[float, float]]:
    (x1, y1), (xm, ym), (x2, y2) = p1, mid, p2
    d = 2 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-12:
        return [tuple(p1), tuple(p2)]  # type: ignore[list-item]
    ux = ((x1**2 + y1**2) * (ym - y2) + (xm**2 + ym**2) * (y2 - y1) + (x2**2 + y2**2) * (y1 - ym)) / d
    uy = ((x1**2 + y1**2) * (x2 - xm) + (xm**2 + ym**2) * (x1 - x2) + (x2**2 + y2**2) * (xm - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, am, a2 = (math.atan2(y - uy, x - ux) for x, y in (p1, mid, p2))

    def ccw(a: float, b: float) -> float:
        return (b - a) % (2 * math.pi)

    sweep = ccw(a1, a2) if ccw(a1, am) <= ccw(a1, a2) else -ccw(a2, a1)
    return [(ux + r * math.cos(a1 + sweep * i / n), uy + r * math.sin(a1 + sweep * i / n)) for i in range(n + 1)]


def entity_shape(ent: Entity, body: bool = False, index: dict[str, Any] | None = None) -> BaseGeometry:
    """The copper (or body) geometry of one entity, in board mm."""
    kind = ent["kind"]
    if kind == "pad":
        return _polygon(ent["polygon"])
    if kind == "track":
        if "mid" in ent:
            line = LineString(_arc_points((ent["x1"], ent["y1"]), ent["mid"], (ent["x2"], ent["y2"])))
        else:
            line = LineString([(ent["x1"], ent["y1"]), (ent["x2"], ent["y2"])])
        return line.buffer(ent["width"] / 2.0, quad_segs=16)
    if kind == "via":
        return Point(ent["x"], ent["y"]).buffer(ent["size"] / 2.0, quad_segs=16)
    if kind == "zone":
        polys = [_polygon(p) for p in ent.get("filled") or [] if len(p) >= 3]
        return shapely.union_all(polys) if polys else _polygon(ent.get("outline") or [])
    if kind == "point":
        return Point(ent["x"], ent["y"])
    if kind == "footprint":
        if body or index is None:
            return footprint_body(ent)
        pads = [_polygon(p["polygon"]) for p in index.get("pads", []) if p["ref"] == ent["ref"]]
        return shapely.union_all(pads) if pads else footprint_body(ent)
    raise EngineError("InvalidParams", f"Unknown entity kind {kind!r}")


def footprint_body(fp: Entity) -> BaseGeometry:
    """A footprint's courtyard polygon, or its bounding box when it has no courtyard."""
    if fp.get("courtyard"):
        return _polygon(fp["courtyard"])
    x, y, w, h = fp["bbox"]
    return box(x, y, x + w, y + h)


def _parts(index: dict[str, Any], ent: Entity, body: bool) -> list[tuple[Entity, BaseGeometry]]:
    if ent["kind"] == "footprint" and not body:
        pads = [_with_kind(p, "pad") for p in index.get("pads", []) if p["ref"] == ent["ref"]]
        if pads:
            return [(p, entity_shape(p)) for p in pads]
    return [(ent, entity_shape(ent, body=body, index=index))]


def describe(ent: Entity) -> dict[str, Any]:
    """A compact identity for an entity (kind, uuid, ref, pad, net, layer, x, y)."""
    out: dict[str, Any] = {"kind": ent["kind"]}
    for key in ("uuid", "ref", "pad", "net", "layer"):
        if ent.get(key) not in (None, ""):
            out[key] = ent[key]
    if ent["kind"] in ("footprint", "pad", "via", "point"):
        out["x"], out["y"] = ent["x"], ent["y"]
    return out


def _closest(parts_a: list[tuple[Entity, BaseGeometry]], parts_b: list[tuple[Entity, BaseGeometry]]) -> dict[str, Any]:
    ga = np.array([g for _, g in parts_a], dtype=object)
    gb = np.array([g for _, g in parts_b], dtype=object)
    dist = shapely.distance(ga[:, None], gb[None, :])
    i, j = np.unravel_index(int(np.argmin(dist)), dist.shape)
    pa, pb = nearest_points(ga[i], gb[j])
    return {
        "distance": round(float(dist[i, j]), 4),
        "a_point": [round(pa.x, 4), round(pa.y, 4)],
        "b_point": [round(pb.x, 4), round(pb.y, 4)],
        "a": describe(parts_a[i][0]),
        "b": describe(parts_b[j][0]),
    }


def measure(index: dict[str, Any], a: Any, b: Any, body: bool = False) -> dict[str, Any]:
    """Edge-to-edge distance in mm between two entities (0 if they touch or overlap).

    ``a``/``b`` are anything ``resolve`` accepts. ``a``/``b`` in the result describe the closest
    parts (e.g. which pad of a footprint); ``a_point``/``b_point`` are where the gap is measured.
    """
    parts_a = [part for e in resolve(index, a) for part in _parts(index, e, body)]
    parts_b = [part for e in resolve(index, b) for part in _parts(index, e, body)]
    if not parts_a or not parts_b:
        empty = a if not parts_a else b
        raise EngineError("NotFound", f"{empty!r} has no copper to measure")
    return _closest(parts_a, parts_b)


def _on_layer(ent: Entity, layer: str) -> bool:
    kind = ent["kind"]
    if kind == "pad":
        return layer in ent.get("layers", [])
    if kind == "via":
        return True  # through vias span every copper layer
    if kind == "zone":
        return layer in (ent.get("layers") or [ent.get("layer")])
    return ent.get("layer") == layer


def clearance(index: dict[str, Any], net_a: str, net_b: str, layer: str | None = None) -> dict[str, Any]:
    """Minimum copper-to-copper distance between two nets, compared layer by layer.

    Returns the ``measure`` shape plus ``layer``. Raises ``NotFound`` if the nets never share a
    copper layer.
    """
    names = [n[4:] if n.startswith("net:") else n for n in (net_a, net_b)]
    for n in names:
        if n not in index.get("nets", []):
            raise EngineError("NotFound", f"No net {n!r}. Nets: {', '.join(x for x in index.get('nets', []) if x)}")
    members = [net_members(index, n) for n in names]
    layers = [layer] if layer else list(index.get("board", {}).get("layers", []))
    best: dict[str, Any] | None = None
    for lyr in layers:
        pa = [(e, entity_shape(e)) for e in members[0] if _on_layer(e, lyr)]
        pb = [(e, entity_shape(e)) for e in members[1] if _on_layer(e, lyr)]
        if not pa or not pb:
            continue
        res = _closest(pa, pb)
        res["layer"] = lyr
        if best is None or res["distance"] < best["distance"]:
            best = res
    if best is None:
        where = f"on {layer}" if layer else "on any copper layer"
        raise EngineError("NotFound", f"Nets {names[0]!r} and {names[1]!r} don't both have copper {where}")
    return best


def _copper_layers(index: dict[str, Any]) -> list[str]:
    return [str(layer) for layer in (index.get("board", {}).get("layers") or [])
            if str(layer).endswith(".Cu")]


def _intersects(ent: Entity, region: tuple[float, float, float, float]) -> bool:
    try:
        return entity_shape(ent).intersects(Polygon([(region[0], region[1]), (region[2], region[1]),
                                                     (region[2], region[3]), (region[0], region[3])]))
    except Exception:
        return False


def copper_in_region(index: dict[str, Any], region: Sequence[float],
                     layer: str | None = None) -> list[Entity]:
    """Copper entities that intersect ``[x1, y1, x2, y2]``, optionally on one layer."""
    x1, y1, x2, y2 = (float(v) for v in region)
    box_region = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    out: list[Entity] = []
    for key, kind in _KINDS[1:]:
        for ent in index.get(key, []):
            if not ent.get("net"):
                continue
            if layer and not _on_layer(ent, layer):
                continue
            tagged = _with_kind(ent, kind)
            if _intersects(tagged, box_region):
                out.append(tagged)
    return out


def _pair_distance(a: Entity, b: Entity) -> dict[str, Any] | None:
    try:
        sa, sb = entity_shape(a), entity_shape(b)
    except Exception:
        return None
    distance = float(sa.distance(sb))
    pa, pb = nearest_points(sa, sb)
    return {
        "distance": round(distance, 4),
        "a_point": [round(pa.x, 4), round(pa.y, 4)],
        "b_point": [round(pb.x, 4), round(pb.y, 4)],
        "a": describe(a),
        "b": describe(b),
    }


def clearance_query(index: dict[str, Any], a: Any = None, b: Any = None,
                    region: Sequence[float] | None = None,
                    layer: str | None = None) -> dict[str, Any]:
    """Nearest copper clearance for one of four selector shapes.

    - ``a`` and ``b`` are nets: layer-by-layer net-to-net clearance.
    - ``a`` and ``b`` are entities: edge-to-edge distance (``measure``).
    - ``a`` is a net and ``b`` is omitted: nearest copper on any other net.
    - ``region`` only: nearest pair of different nets inside the region.

    The result always carries ``distance``, the two entities and the layer. Raising is left to
    the caller so the tool can turn a miss into data.
    """
    nets = [str(n) for n in index.get("nets", []) if n]
    names = {n[4:] if str(n).startswith("net:") else str(n) for n in nets}

    def is_net(value: Any) -> bool:
        return isinstance(value, str) and (value[4:] if value.startswith("net:") else value) in names

    if a and b and is_net(a) and is_net(b):
        result = clearance(index, a, b, layer)
        return {**result, "method": "net-net", "net_a": a[4:] if a.startswith("net:") else a,
                "net_b": b[4:] if b.startswith("net:") else b}

    if a and b:
        result = measure(index, a, b)
        return {**result, "method": "entity", "layer": layer}

    if a and is_net(a):
        name = a[4:] if a.startswith("net:") else a
        best: dict[str, Any] | None = None
        for other in sorted(names - {name}):
            try:
                candidate = clearance(index, name, other, layer)
            except EngineError:
                continue
            if best is None or candidate["distance"] < best["distance"]:
                best = candidate
                best["net_a"], best["net_b"] = name, other
        if best is None:
            raise EngineError("NotFound", f"net {name!r} has no copper near another net")
        return {**best, "method": "net-other"}

    if not region:
        raise EngineError("InvalidParams", "give two selectors or a region [x1,y1,x2,y2]")

    entities = copper_in_region(index, region, layer)
    if len(entities) > 600:
        raise EngineError("InvalidParams",
                          f"region contains {len(entities)} copper items; narrow it for a pairwise check")
    best = None
    for i, left in enumerate(entities):
        for right in entities[i + 1:]:
            if left.get("net") == right.get("net"):
                continue
            if layer is None and not (set(_layers_of(left, index)) & set(_layers_of(right, index))):
                continue
            candidate = _pair_distance(left, right)
            if candidate is None:
                continue
            if best is None or candidate["distance"] < best["distance"]:
                shared = set(_layers_of(left, index)) & set(_layers_of(right, index))
                candidate["layer"] = layer or (sorted(shared)[0] if shared else None)
                candidate["net_a"], candidate["net_b"] = left.get("net"), right.get("net")
                best = candidate
    if best is None:
        raise EngineError("NotFound", "no two different nets share copper in that region")
    return {**best, "method": "region"}


def _layers_of(ent: Entity, index: dict[str, Any]) -> list[str]:
    kind = ent["kind"]
    if kind == "pad":
        return [layer for layer in ent.get("layers", []) if layer.endswith(".Cu")]
    if kind == "via":
        return _copper_layers(index)
    if kind == "zone":
        return [layer for layer in (ent.get("layers") or [ent.get("layer")]) if layer and layer.endswith(".Cu")]
    return [ent["layer"]] if ent.get("layer") else []


def probe(index: dict[str, Any], x: float, y: float) -> dict[str, Any]:
    """What is at a board point: copper hits first, then the footprint and its reference text."""
    point = Point(float(x), float(y))
    hits: list[dict[str, Any]] = []
    for key, kind in _KINDS[1:]:
        for ent in index.get(key, []):
            try:
                tagged = _with_kind(ent, kind)
                if entity_shape(tagged).covers(point):
                    hits.append(describe(tagged))
            except Exception:
                continue
    for fp in index.get("footprints", []):
        bx, by, bw, bh = fp.get("bbox") or (0, 0, 0, 0)
        if bx <= point.x <= bx + bw and by <= point.y <= by + bh:
            hits.append({"kind": "footprint", "ref": fp.get("ref"), "uuid": fp.get("uuid")})
            text = fp.get("ref_text") or {}
            tx, ty, tw, th = text.get("bbox") or (0, 0, 0, 0)
            if text.get("visible") and tx <= point.x <= tx + tw and ty <= point.y <= ty + th:
                hits.append({"kind": "silk_text", "ref": fp.get("ref"), "layer": text.get("layer")})
    edge = index.get("board", {}).get("edge_bbox") or (0, 0, 0, 0)
    within = bool(edge[0] <= point.x <= edge[0] + edge[2] and edge[1] <= point.y <= edge[1] + edge[3])
    return {"point": [round(float(x), 4), round(float(y), 4)], "within_board": within,
            "hits": hits}


def entities_in(index: dict[str, Any], specs: Iterable[Any]) -> list[Entity]:
    """Resolve several specs and concatenate the entities (used for highlights)."""
    out: list[Entity] = []
    for spec in specs:
        out += resolve(index, spec)
    return out
