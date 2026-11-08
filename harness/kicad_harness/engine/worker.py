"""JSON-lines RPC worker around KiCad's SWIG ``pcbnew`` (see DESIGN.md, "Engine").

Runs under KiCad's bundled Python 3.9, so this file must stay 3.9-compatible: no ``X | Y``
unions and no ``match``. It never imports the rest of ``kicad_harness``.

Protocol: one JSON request per line on stdin, one JSON reply per line on stdout.
  request: {"id": 1, "method": "move_footprint", "params": {"board": "/abs/board.kicad_pcb", ...}}
  reply:   {"id": 1, "result": ...} or {"id": 1, "error": {"type": "NotFound", "message": "..."}}
Anything else (pcbnew's wx "traits" assert, wx debug logs, print()) goes to stderr: fd 1 is
pointed at stderr before pcbnew is imported, and replies go to a private duplicate of the
original stdout.

Every call names its board file. Reads cache the loaded BOARD on (path, inode, mtime_ns, size).
Edits load the board, edit it, refill existing zones when copper changed, save to a temporary
file next to the board and rename it into place, so a failed edit never writes the file.

pcbnew 10 gotchas handled here:
- ``BOARD.Remove(item)`` hands ownership to Python; when the wrapper is collected SWIG reports a
  "memory leak ... no destructor found" and later ``Tracks()`` calls stop returning proxies.
  Always delete with ``BOARD.Delete(item)``.
- ``PCB_VIA.GetWidth()`` without a layer asserts; pass the via's top layer.
- ``BOARD.GetLayerName()`` returns user names ("F.Silkscreen"); ``LSET.Name()`` gives the
  canonical names ("F.SilkS") the harness uses.
- ``pcbnew.SaveBoard(path, board)`` also writes .kicad_pro/.kicad_prl unless aSkipSettings=True.
"""

from __future__ import annotations

import difflib
import glob
import inspect
import io
import json
import math
import os
import re
import sys
import time
import traceback
import uuid as uuidlib
from collections import OrderedDict


def _claim_stdout():
    """Keep the real stdout for replies; route fd 1 (C++ printf, wx logs, print) to stderr."""
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return io.open(proto_fd, "w", encoding="utf-8", newline="\n")


_PROTO = _claim_stdout()

import pcbnew  # noqa: E402  (must be imported after the fd swap)

IU_PER_MM = float(pcbnew.pcbIUScale.IU_PER_MM)
CACHE_MAX = 4
_CACHE = OrderedDict()  # path -> ((ino, mtime_ns, size), BOARD)

_PAD_SHAPES = {
    pcbnew.PAD_SHAPE_CIRCLE: "circle",
    pcbnew.PAD_SHAPE_RECTANGLE: "rect",
    pcbnew.PAD_SHAPE_OVAL: "oval",
    pcbnew.PAD_SHAPE_ROUNDRECT: "roundrect",
    pcbnew.PAD_SHAPE_TRAPEZOID: "custom",
    pcbnew.PAD_SHAPE_CHAMFERED_RECT: "custom",
    pcbnew.PAD_SHAPE_CUSTOM: "custom",
}


class RpcError(Exception):
    """An error reported to the client as {"type": ..., "message": ...}."""

    def __init__(self, type_, message):
        Exception.__init__(self, message)
        self.type = type_
        self.message = message


# --------------------------------------------------------------------------- units and names


def _mm(iu):
    v = round(iu / IU_PER_MM, 6)
    return 0.0 if v == 0 else v


def _iu(mm):
    return int(round(float(mm) * IU_PER_MM))


def _vec(x, y):
    return pcbnew.VECTOR2I(_iu(x), _iu(y))


def _pt(v):
    return [_mm(v.x), _mm(v.y)]


def _bbox(box):
    return [_mm(box.GetX()), _mm(box.GetY()), _mm(box.GetWidth()), _mm(box.GetHeight())]


def _angle(deg):
    """Normalize an angle in degrees to (-180, 180]."""
    a = math.fmod(float(deg), 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    a = round(a, 6)
    return 0.0 if a == 0 else a


def _layer_name(layer_id):
    return str(pcbnew.LSET.Name(layer_id))


def _chain_points(chain):
    return [_pt(chain.CPoint(i)) for i in range(chain.PointCount())]


def _natural_key(text):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def _net_name(name):
    name = "" if name is None else str(name)
    return name[4:] if name.startswith("net:") else name


def _num(value, name, positive=False, nonneg=False):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise RpcError("InvalidParams", "%s must be a number, got %r" % (name, value))
    if not math.isfinite(v):
        raise RpcError("InvalidParams", "%s must be finite, got %r" % (name, value))
    if positive and v <= 0:
        raise RpcError("InvalidParams", "%s must be > 0, got %r" % (name, value))
    if nonneg and v < 0:
        raise RpcError("InvalidParams", "%s must be >= 0, got %r" % (name, value))
    return v


def _points(value, name, minimum):
    if not isinstance(value, (list, tuple)) or len(value) < minimum:
        raise RpcError("InvalidParams", "%s must be a list of at least %d [x, y] points" % (name, minimum))
    out = []
    for i, p in enumerate(value):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise RpcError("InvalidParams", "%s[%d] must be [x, y], got %r" % (name, i, p))
        out.append((_num(p[0], "%s[%d].x" % (name, i)), _num(p[1], "%s[%d].y" % (name, i))))
    return out


def _as_list(value, name):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise RpcError("InvalidParams", "%s must be a string or a list of strings, got %r" % (name, value))


# --------------------------------------------------------------------------- board I/O


def _abspath(path):
    if not path or not isinstance(path, str):
        raise RpcError("InvalidParams", "board must be a path to a .kicad_pcb file")
    return os.path.abspath(os.path.expanduser(path))


def _stat_key(path):
    st = os.stat(path)
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _load(path, for_edit=False):
    """Load a board, reusing the cached BOARD when the file is unchanged.

    For edits the cached board is removed from the cache, since the caller mutates it and
    discards it if the edit fails.
    """
    path = _abspath(path)
    if not os.path.isfile(path):
        raise RpcError("NotFound", "Board file not found: %s" % path)
    key = _stat_key(path)
    hit = _CACHE.get(path)
    if hit is not None and hit[0] == key:
        if for_edit:
            del _CACHE[path]
        else:
            _CACHE.move_to_end(path)
        return hit[1]
    try:
        board = pcbnew.LoadBoard(path)
    except Exception as exc:  # pcbnew raises IO_ERROR subclasses with the parser message
        raise RpcError("BoardLoadError", "pcbnew could not load %s: %s" % (path, exc))
    if board is None:
        raise RpcError("BoardLoadError", "pcbnew could not load %s" % path)
    if not for_edit:
        _CACHE[path] = (key, board)
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
    return board


def _tmp_name(path, tag):
    d, base = os.path.split(path)
    return os.path.join(d, ".%s.%s-%d-%s.kicad_pcb" % (base, tag, os.getpid(), uuidlib.uuid4().hex[:8]))


def _save(board, path):
    """Save in KiCad 10 format to a temp file beside ``path``, then rename it into place."""
    tmp = _tmp_name(path, "save")
    try:
        ok = pcbnew.SaveBoard(tmp, board, True)  # aSkipSettings: no .kicad_pro/.kicad_prl
        if not ok or not os.path.isfile(tmp):
            raise RpcError("SaveError", "pcbnew could not save %s" % path)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        _CACHE.pop(path, None)


def _fill_zones(board):
    zones = board.Zones()
    n = len(zones)
    if n:
        pcbnew.ZONE_FILLER(board).Fill(zones)
    return n


def _edit(path, fn, refill=True):
    """Run ``fn(board)`` on a fresh board and save it; nothing is written if ``fn`` raises."""
    path = _abspath(path)
    board = _load(path, for_edit=True)
    result = fn(board)
    if refill:
        _fill_zones(board)
    _save(board, path)
    return result


# --------------------------------------------------------------------------- lookups


def _footprint(board, ref):
    if not isinstance(ref, str) or not ref:
        raise RpcError("InvalidParams", "ref must be a reference designator such as 'U1'")
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        refs = sorted((f.GetReference() for f in board.GetFootprints()), key=_natural_key)
        raise RpcError("NotFound", "No footprint with ref %r. Valid refs: %s" % (ref, ", ".join(refs) or "(none)"))
    return fp


def _pads_by_number(fp, number):
    number = str(number)
    pads = [p for p in fp.Pads() if p.GetNumber() == number]
    if not pads:
        nums = sorted(set(p.GetNumber() for p in fp.Pads()), key=_natural_key)
        raise RpcError(
            "NotFound",
            "%s has no pad %r. Its pads: %s" % (fp.GetReference(), number, ", ".join(nums) or "(none)"),
        )
    return pads


def _net_names(board):
    return sorted((str(k) for k in board.GetNetsByName().keys()), key=_natural_key)


def _find_net(board, name, create=False):
    name = _net_name(name)
    net = board.FindNet(name)
    if net is None:
        if not create:
            names = [n for n in _net_names(board) if n]
            raise RpcError("NotFound", "No net %r. Nets: %s" % (name, ", ".join(names) or "(none)"))
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
    return net


def _copper_layer(board, name):
    copper = [_layer_name(lid) for lid in board.GetEnabledLayers().CuStack()]
    lid = board.GetLayerID(str(name)) if isinstance(name, str) else -1
    if lid < 0 or not pcbnew.IsCopperLayer(lid) or _layer_name(lid) not in copper:
        raise RpcError("InvalidParams", "Layer %r is not a copper layer of this board. Copper layers: %s"
                       % (name, ", ".join(copper)))
    return lid


def _items_by_uuid(board):
    """Map uuid -> (item, parent footprint or None) for every board item and footprint child."""
    items = {}
    for t in board.GetTracks():
        items[t.m_Uuid.AsString()] = (t, None)
    for d in board.GetDrawings():
        items[d.m_Uuid.AsString()] = (d, None)
    for z in board.Zones():
        items[z.m_Uuid.AsString()] = (z, None)
    for g in board.Groups():
        items[g.m_Uuid.AsString()] = (g, None)
    for fp in board.GetFootprints():
        items[fp.m_Uuid.AsString()] = (fp, None)
        for child in list(fp.Pads()) + list(fp.GraphicalItems()) + list(fp.Zones()) + list(fp.GetFields()):
            items.setdefault(child.m_Uuid.AsString(), (child, fp))
    return items


# --------------------------------------------------------------------------- index


def _edge_bbox(board):
    """Bounding box of the board outline geometry, without the Edge.Cuts stroke width.

    This is the box kicad-cli uses for board-area SVG pages (their origin is its top-left) and
    the board size kicad-cli render frames.
    """
    outline = pcbnew.SHAPE_POLY_SET()
    try:
        ok = board.GetBoardPolygonOutlines(outline, False)
    except Exception:
        ok = False
    if ok and outline.OutlineCount() > 0:
        return _bbox(outline.BBox())
    edges = board.GetBoardEdgesBoundingBox()
    if edges.GetWidth() > 0 and edges.GetHeight() > 0:
        return _bbox(edges)
    return _bbox(board.ComputeBoundingBox(False))


def _board_outline_poly(board):
    outline = pcbnew.SHAPE_POLY_SET()
    try:
        ok = board.GetBoardPolygonOutlines(outline, False)
    except Exception:
        ok = False
    return outline if ok and outline.OutlineCount() > 0 else None


def _fp_state(fp):
    p = fp.GetPosition()
    return {"x": _mm(p.x), "y": _mm(p.y), "rotation": _angle(fp.GetOrientationDegrees()),
            "side": "back" if fp.IsFlipped() else "front"}


def _courtyard(fp):
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    fp.BuildCourtyardCaches()
    poly = fp.GetCourtyard(layer)
    if poly.OutlineCount() == 0:
        return None
    best = max(range(poly.OutlineCount()), key=lambda i: abs(poly.Outline(i).Area()))
    return _chain_points(poly.Outline(best))


def _footprint_entry(fp):
    ref = fp.Reference()
    rpos = ref.GetPosition()
    entry = {
        "uuid": fp.m_Uuid.AsString(),
        "ref": fp.GetReference(),
        "value": fp.GetValue(),
        "footprint": str(fp.GetFPID().GetLibItemName()),
        "bbox": _bbox(fp.GetBoundingBox(False)),
        "courtyard": _courtyard(fp),
        "ref_text": {
            "x": _mm(rpos.x),
            "y": _mm(rpos.y),
            "bbox": _bbox(ref.GetBoundingBox()),
            "visible": bool(ref.IsVisible()),
            "layer": _layer_name(ref.GetLayer()),
            "height": _mm(ref.GetTextHeight()),
        },
    }
    entry.update(_fp_state(fp))
    return entry


def _pad_entry(fp, pad, enabled):
    layer_ids = [lid for lid in pad.GetLayerSet().Seq() if enabled.Contains(lid)]
    copper = [lid for lid in layer_ids if pcbnew.IsCopperLayer(lid)]
    first = copper[0] if copper else pad.GetPrincipalLayer()
    pos = pad.GetPosition()
    size = pad.GetSize(first)
    drill = pad.GetDrillSize()
    drill_mm = _mm(min(drill.x, drill.y)) if drill.x > 0 and drill.y > 0 else _mm(max(drill.x, drill.y))
    polygon = []
    try:
        poly = pad.GetEffectivePolygon(first, pcbnew.ERROR_INSIDE)
        if poly.OutlineCount() > 0:
            best = max(range(poly.OutlineCount()), key=lambda i: abs(poly.Outline(i).Area()))
            polygon = _chain_points(poly.Outline(best))
    except Exception:
        polygon = []
    if not polygon:  # e.g. an NPTH hole with no copper: fall back to the pad's rotated rectangle
        a = math.radians(pad.GetOrientationDegrees())
        hw, hh = size.x / 2.0, size.y / 2.0
        for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
            polygon.append([_mm(pos.x + dx * math.cos(a) + dy * math.sin(a)),
                            _mm(pos.y - dx * math.sin(a) + dy * math.cos(a))])
    return {
        "uuid": pad.m_Uuid.AsString(),
        "ref": fp.GetReference(),
        "pad": pad.GetNumber(),
        "net": pad.GetNetname(),
        "x": _mm(pos.x),
        "y": _mm(pos.y),
        "w": _mm(size.x),
        "h": _mm(size.y),
        "rotation": _angle(pad.GetOrientationDegrees()),
        "shape": _PAD_SHAPES.get(pad.GetShape(first), "custom"),
        "layers": [_layer_name(lid) for lid in layer_ids],
        "through": pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH),
        "drill": drill_mm,
        "polygon": polygon,
    }


def _track_entry(t):
    s, e = t.GetStart(), t.GetEnd()
    entry = {
        "uuid": t.m_Uuid.AsString(),
        "net": t.GetNetname(),
        "layer": _layer_name(t.GetLayer()),
        "x1": _mm(s.x), "y1": _mm(s.y), "x2": _mm(e.x), "y2": _mm(e.y),
        "width": _mm(t.GetWidth()),
    }
    if t.GetClass() == "PCB_ARC":
        entry["mid"] = _pt(t.GetMid())
    return entry


def _via_entry(v):
    p = v.GetPosition()
    kind = {
        getattr(pcbnew, "VIATYPE_THROUGH", -1): "through",
        getattr(pcbnew, "VIATYPE_BLIND", -2): "blind",
        getattr(pcbnew, "VIATYPE_BURIED", -3): "buried",
        getattr(pcbnew, "VIATYPE_BLIND_BURIED", -4): "blind",
        getattr(pcbnew, "VIATYPE_MICROVIA", -5): "micro",
    }.get(v.GetViaType(), "through")
    return {
        "uuid": v.m_Uuid.AsString(),
        "net": v.GetNetname(),
        "x": _mm(p.x),
        "y": _mm(p.y),
        "size": _mm(v.GetWidth(v.TopLayer())),
        "drill": _mm(v.GetDrillValue()),
        "type": kind,
        "layers": [_layer_name(v.TopLayer()), _layer_name(v.BottomLayer())],
    }


def _zone_entry(z):
    layer_ids = list(z.GetLayerSet().Seq())
    first = layer_ids[0] if layer_ids else z.GetLayer()
    outline = z.Outline()
    filled = []
    if z.IsFilled() and z.HasFilledPolysForLayer(first):
        polys = z.GetFilledPolysList(first)
        filled = [_chain_points(polys.Outline(i)) for i in range(polys.OutlineCount())]
    entry = {
        "uuid": z.m_Uuid.AsString(),
        "net": z.GetNetname(),
        "layer": _layer_name(first),
        "outline": _chain_points(outline.Outline(0)) if outline.OutlineCount() else [],
        "filled": filled,
    }
    if len(layer_ids) > 1:
        entry["layers"] = [_layer_name(lid) for lid in layer_ids]
    if z.GetIsRuleArea():
        entry["keepout"] = True
    return entry


def _build_index(board):
    enabled = board.GetEnabledLayers()
    footprints, pads = [], []
    for fp in board.GetFootprints():
        footprints.append(_footprint_entry(fp))
        for pad in fp.Pads():
            pads.append(_pad_entry(fp, pad, enabled))
    tracks, vias = [], []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            vias.append(_via_entry(t))
        else:
            tracks.append(_track_entry(t))
    footprints.sort(key=lambda f: _natural_key(f["ref"]))
    return {
        "board": {
            "bbox": _bbox(board.ComputeBoundingBox(False)),
            "edge_bbox": _edge_bbox(board),
            "layers": [_layer_name(lid) for lid in enabled.CuStack()],
            "thickness": _mm(board.GetDesignSettings().GetBoardThickness()),
        },
        "nets": _net_names(board),
        "footprints": footprints,
        "pads": pads,
        "tracks": tracks,
        "vias": vias,
        "zones": [_zone_entry(z) for z in board.Zones()],
    }


# --------------------------------------------------------------------------- footprint libraries


def _find_footprint_file(spec, dirs):
    """Resolve ``Lib:Name`` or a bare ``Name`` to (library dir, nickname, name)."""
    if not isinstance(spec, str) or not spec.strip():
        raise RpcError("InvalidParams", "footprint must be 'Lib:Name' or a bare footprint name")
    lib, _, name = spec.strip().rpartition(":")
    dirs = [os.path.expanduser(str(d)) for d in (dirs or [])]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        if lib:
            for libdir in (os.path.join(d, lib + ".pretty"), os.path.join(d, lib)):
                if os.path.isfile(os.path.join(libdir, name + ".kicad_mod")):
                    return libdir, lib, name
            if os.path.basename(d.rstrip("/")) in (lib, lib + ".pretty") and \
                    os.path.isfile(os.path.join(d, name + ".kicad_mod")):
                return d, lib, name
        else:
            if os.path.isfile(os.path.join(d, name + ".kicad_mod")):
                nick = os.path.basename(d.rstrip("/"))
                return d, (nick[:-7] if nick.endswith(".pretty") else ""), name
            for libdir in sorted(glob.glob(os.path.join(d, "*.pretty"))):
                if os.path.isfile(os.path.join(libdir, name + ".kicad_mod")):
                    return libdir, os.path.basename(libdir)[:-7], name
    hints = _suggest_footprints(name, dirs)
    msg = "Footprint %r not found in %s." % (spec, ", ".join(dirs) or "(no footprint_dirs)")
    if hints:
        msg += " Similar: " + ", ".join(hints)
    raise RpcError("NotFound", msg)


def _suggest_footprints(name, dirs, limit=8):
    """Library ids whose names contain ``name`` or resemble it (for NotFound messages)."""
    names = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.kicad_mod")) + glob.glob(os.path.join(d, "*.pretty", "*.kicad_mod")):
            stem = os.path.basename(path)[:-10]
            parent = os.path.basename(os.path.dirname(path))
            names.setdefault(stem, "%s:%s" % (parent[:-7], stem) if parent.endswith(".pretty") else stem)
    needle = name.lower()
    hits = [names[n] for n in sorted(names) if needle in n.lower()][:limit]
    if len(hits) < limit:
        for n in difflib.get_close_matches(name, list(names), n=limit, cutoff=0.6):
            if names[n] not in hits:
                hits.append(names[n])
    return hits[:limit]


# --------------------------------------------------------------------------- methods

METHODS = {}


def rpc(fn):
    METHODS[fn.__name__] = fn
    return fn


@rpc
def ping(board=None):
    """Liveness check: returns the pcbnew version."""
    return {"pcbnew": str(pcbnew.Version())}


@rpc
def index(board):
    """The pick index (DESIGN.md, "Pick index")."""
    return _build_index(_load(board))


UUID_RE = re.compile(r'\(uuid "([0-9A-Za-z-]{8,64})"\)')


@rpc
def normalize(board):
    """Give every duplicated UUID a fresh one (first occurrence keeps its value); save as KiCad 10."""
    path = _abspath(board)
    first = _load(path, for_edit=True)
    tmp1, tmp2 = _tmp_name(path, "norm1"), _tmp_name(path, "norm2")
    try:
        if not pcbnew.SaveBoard(tmp1, first, True):
            raise RpcError("SaveError", "pcbnew could not save %s" % tmp1)
        with io.open(tmp1, "r", encoding="utf-8") as fh:
            text = fh.read()
        seen = set()
        replaced = [0]

        def repl(m):
            u = m.group(1)
            if u in seen:
                replaced[0] += 1
                return '(uuid "%s")' % uuidlib.uuid4()
            seen.add(u)
            return m.group(0)

        text = UUID_RE.sub(repl, text)
        if replaced[0]:
            with io.open(tmp1, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            fixed = pcbnew.LoadBoard(tmp1)
            if fixed is None or not pcbnew.SaveBoard(tmp2, fixed, True):
                raise RpcError("SaveError", "pcbnew could not re-save the deduplicated board")
            os.replace(tmp2, path)
        else:
            os.replace(tmp1, path)
    finally:
        for tmp in (tmp1, tmp2):
            if os.path.exists(tmp):
                os.remove(tmp)
        _CACHE.pop(path, None)
    return {"replaced": replaced[0]}


@rpc
def move_footprint(board, ref, x, y, rotation=None):
    """Move a footprint's anchor to (x, y); optionally set its absolute rotation."""
    x, y = _num(x, "x"), _num(y, "y")
    rot = None if rotation is None else _num(rotation, "rotation")

    def edit(b):
        fp = _footprint(b, ref)
        before = _fp_state(fp)
        fp.SetPosition(_vec(x, y))
        if rot is not None:
            fp.SetOrientationDegrees(_angle(rot))
        return {"ref": ref, "before": before, "after": _fp_state(fp)}

    return _edit(board, edit)


@rpc
def rotate_footprint(board, ref, angle):
    """Rotate a footprint about its anchor by ``angle`` degrees (relative, CCW positive)."""
    angle = _num(angle, "angle")

    def edit(b):
        fp = _footprint(b, ref)
        before = _fp_state(fp)
        fp.SetOrientationDegrees(_angle(fp.GetOrientationDegrees() + angle))
        return {"ref": ref, "before": before, "after": _fp_state(fp)}

    return _edit(board, edit)


@rpc
def flip_footprint(board, ref):
    """Move a footprint to the other side in place (KiCad's left/right flip about its anchor)."""

    def edit(b):
        fp = _footprint(b, ref)
        before = _fp_state(fp)
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        return {"ref": ref, "before": before, "after": _fp_state(fp)}

    return _edit(board, edit)


@rpc
def move_reference_text(board, ref, x, y):
    """Move a footprint's reference designator text to absolute board coordinates."""
    x, y = _num(x, "x"), _num(y, "y")

    def edit(b):
        text = _footprint(b, ref).Reference()
        before = _pt(text.GetPosition())
        text.SetPosition(_vec(x, y))
        after = _pt(text.GetPosition())
        return {"ref": ref, "before": {"x": before[0], "y": before[1]}, "after": {"x": after[0], "y": after[1]}}

    return _edit(board, edit, refill=False)


@rpc
def move_reference_texts(board, moves):
    """Move several reference designators in one load/save (silkscreen placement)."""
    if not isinstance(moves, list):
        raise RpcError("InvalidParams", "moves must be a list of {ref, x, y}")

    def edit(b):
        out = []
        for item in moves:
            ref = item.get("ref")
            text = _footprint(b, ref).Reference()
            text.SetPosition(_vec(_num(item.get("x"), "x"), _num(item.get("y"), "y")))
            out.append({"ref": ref, "x": _mm(text.GetPosition().x), "y": _mm(text.GetPosition().y)})
        return {"moved": len(out), "items": out}

    return _edit(board, edit, refill=False)


@rpc
def delete_tracks(board, nets=None, uuids=None):
    """Delete tracks and vias on ``nets`` and/or with ``uuids``; with neither, delete all of them."""
    net_list = None if nets is None else [_net_name(n) for n in _as_list(nets, "nets")]
    uuid_list = None if uuids is None else _as_list(uuids, "uuids")

    def edit(b):
        tracks = list(b.GetTracks())
        if net_list is None and uuid_list is None:
            victims = tracks
        else:
            victims, chosen = [], set()
            if net_list is not None:
                valid = set(_net_names(b))
                unknown = [n for n in net_list if n not in valid]
                if unknown:
                    raise RpcError("NotFound", "No net %s. Nets: %s"
                                   % (", ".join(repr(n) for n in unknown), ", ".join(n for n in sorted(valid) if n)))
                wanted = set(net_list)
                for t in tracks:
                    if t.GetNetname() in wanted:
                        chosen.add(t.m_Uuid.AsString())
                        victims.append(t)
            if uuid_list is not None:
                by_uuid = dict((t.m_Uuid.AsString(), t) for t in tracks)
                missing = [u for u in uuid_list if u not in by_uuid]
                if missing:
                    raise RpcError("NotFound", "Not a track or via on this board: %s" % ", ".join(missing))
                for u in uuid_list:
                    if u not in chosen:
                        chosen.add(u)
                        victims.append(by_uuid[u])
        for t in victims:
            b.Delete(t)
        return {"deleted": len(victims)}

    return _edit(board, edit)


@rpc
def delete_items(board, uuids):
    """Delete any board items (tracks, vias, footprints, zones, drawings, pads...) by UUID."""
    uuid_list = list(OrderedDict.fromkeys(_as_list(uuids, "uuids")))

    def edit(b):
        items = _items_by_uuid(b)
        missing = [u for u in uuid_list if u not in items]
        if missing:
            raise RpcError("NotFound", "No board item with uuid %s" % ", ".join(missing))
        top, children = [], []
        for u in uuid_list:
            item, parent = items[u]
            if parent is None:
                top.append(item)
            else:
                if item.GetClass() == "PCB_FIELD" and item.IsMandatory():
                    raise RpcError("InvalidParams", "%s is a mandatory field of %s and cannot be deleted"
                                   % (u, parent.GetReference()))
                children.append((item, parent))
        doomed = set(it.m_Uuid.AsString() for it in top)
        for item, parent in children:
            if parent.m_Uuid.AsString() not in doomed:
                parent.Delete(item)
        for item in top:
            b.Delete(item)
        return {"deleted": len(uuid_list)}

    return _edit(board, edit)


@rpc
def add_track(board, net, points, width=0.25, layer="F.Cu"):
    """Add a track on ``layer``: one segment per consecutive pair of ``points``."""
    pts = _points(points, "points", 2)
    width = _num(width, "width", positive=True)
    for i in range(len(pts) - 1):
        if _iu(pts[i][0]) == _iu(pts[i + 1][0]) and _iu(pts[i][1]) == _iu(pts[i + 1][1]):
            raise RpcError("InvalidParams", "points[%d] and points[%d] are the same point" % (i, i + 1))

    def edit(b):
        netinfo = _find_net(b, net)
        lid = _copper_layer(b, layer)
        uuids = []
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(_vec(x1, y1))
            t.SetEnd(_vec(x2, y2))
            t.SetWidth(_iu(width))
            t.SetLayer(lid)
            t.SetNet(netinfo)
            b.Add(t)
            uuids.append(t.m_Uuid.AsString())
        return {"uuids": uuids}

    return _edit(board, edit)


@rpc
def add_via(board, x, y, net, size=0.6, drill=0.3, via_type="through", layers=None):
    """Add a via at (x, y): ``through`` (outer layers), ``blind``/``buried`` or ``micro``.

    Blind and buried vias need ``layers`` naming the two copper layers they join.
    """
    x, y = _num(x, "x"), _num(y, "y")
    size = _num(size, "size", positive=True)
    drill = _num(drill, "drill", positive=True)
    if drill >= size:
        raise RpcError("InvalidParams", "drill (%g) must be smaller than size (%g)" % (drill, size))
    kind = str(via_type or "through").lower()
    if kind not in ("through", "blind", "buried", "micro"):
        raise RpcError("InvalidParams", "via_type must be through, blind, buried or micro")

    def edit(b):
        netinfo = _find_net(b, net)
        copper = list(b.GetEnabledLayers().CuStack())
        v = pcbnew.PCB_VIA(b)
        if kind == "through":
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(copper[0], copper[-1])
        elif kind == "micro":
            v.SetViaType(pcbnew.VIATYPE_MICROVIA)
            if layers and len(layers) == 2:
                v.SetLayerPair(_copper_layer(b, layers[0]), _copper_layer(b, layers[1]))
            else:
                v.SetLayerPair(copper[0], copper[1])
        else:
            blind = getattr(pcbnew, "VIATYPE_BLIND", None)
            buried = getattr(pcbnew, "VIATYPE_BURIED", None)
            if blind is not None and buried is not None:
                v.SetViaType(blind if kind == "blind" else buried)
            else:  # older KiCad had one combined type
                v.SetViaType(getattr(pcbnew, "VIATYPE_BLIND_BURIED"))
            if not layers or len(layers) != 2:
                raise RpcError("InvalidParams", "%s via needs layers=[from, to]" % kind)
            v.SetLayerPair(_copper_layer(b, layers[0]), _copper_layer(b, layers[1]))
        v.SetPosition(_vec(x, y))
        v.SetWidth(_iu(size))
        v.SetDrill(_iu(drill))
        v.SetNet(netinfo)
        b.Add(v)
        return {"uuid": v.m_Uuid.AsString()}

    return _edit(board, edit)


@rpc
def set_outline(board, width, height, x=0, y=0, corner_radius=0):
    """Replace all board-level Edge.Cuts shapes with one (optionally rounded) rectangle."""
    width = _num(width, "width", positive=True)
    height = _num(height, "height", positive=True)
    x, y = _num(x, "x"), _num(y, "y")
    radius = _num(corner_radius, "corner_radius", nonneg=True)
    if radius * 2 > min(width, height):
        raise RpcError("InvalidParams", "corner_radius %g is more than half the smaller side" % radius)

    def edit(b):
        for d in list(b.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts and d.GetClass() == "PCB_SHAPE":
                b.Delete(d)
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_RECTANGLE)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetStart(_vec(x, y))
        s.SetEnd(_vec(x + width, y + height))
        s.SetWidth(b.GetDesignSettings().GetLineThickness(pcbnew.Edge_Cuts))
        if radius > 0:
            s.SetCornerRadius(_iu(radius))
        b.Add(s)
        return {"bbox": [round(x, 6), round(y, 6), round(width, 6), round(height, 6)]}

    return _edit(board, edit)


@rpc
def set_copper_layers(board, layers):
    """Set the board's copper layer count (2, 4, 6, ...)."""
    count = int(_num(layers, "layers", positive=True))
    if count < 2 or count % 2:
        raise RpcError("InvalidParams", "layers must be an even number >= 2")

    def edit(b):
        b.SetCopperLayerCount(count)
        return {"layers": count}

    return _edit(board, edit)


@rpc
def set_outline_polygon(board, points):
    """Replace Edge.Cuts with a closed polygon through ``points`` (mechanical outline import)."""
    pts = _points(points, "points", 3)
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]

    def edit(b):
        for d in list(b.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts and d.GetClass() == "PCB_SHAPE":
                b.Delete(d)
        width = b.GetDesignSettings().GetLineThickness(pcbnew.Edge_Cuts)
        for start, end in zip(pts, pts[1:]):
            segment = pcbnew.PCB_SHAPE(b)
            segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
            segment.SetLayer(pcbnew.Edge_Cuts)
            segment.SetStart(_vec(*start))
            segment.SetEnd(_vec(*end))
            segment.SetWidth(width)
            b.Add(segment)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return {"bbox": [round(min(xs), 6), round(min(ys), 6),
                         round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6)]}

    return _edit(board, edit)


@rpc
def add_zone(board, net, layer, polygon=None, clearance=0.3, min_width=0.25, margin=0.5):
    """Add a copper zone (default: the board outline inset by ``margin``) and fill it."""
    pts = None if polygon is None else _points(polygon, "polygon", 3)
    clearance = _num(clearance, "clearance", nonneg=True)
    min_width = _num(min_width, "min_width", positive=True)
    margin = _num(margin, "margin", nonneg=True)

    def edit(b):
        netinfo = _find_net(b, net)
        lid = _copper_layer(b, layer)
        corners = pts
        if corners is None:
            outline = _board_outline_poly(b)
            if outline is None:
                raise RpcError("InvalidParams", "The board has no closed Edge.Cuts outline; pass polygon explicitly")
            if margin > 0:
                outline.Deflate(_iu(margin), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS,
                                b.GetDesignSettings().m_MaxError)
            if outline.OutlineCount() == 0:
                raise RpcError("InvalidParams", "margin %g leaves no room inside the board outline" % margin)
            corners = [(p[0], p[1]) for p in _chain_points(outline.Outline(0))]
        z = pcbnew.ZONE(b)
        z.SetLayer(lid)
        z.SetNet(netinfo)
        shape = z.Outline()
        shape.NewOutline()
        for cx, cy in corners:
            shape.Append(_iu(cx), _iu(cy))
        z.SetLocalClearance(_iu(clearance))
        z.SetMinThickness(_iu(min_width))
        b.Add(z)
        return {"uuid": z.m_Uuid.AsString()}

    return _edit(board, edit)


@rpc
def add_cutout(board, x, y, diameter):
    """Add a circular cutout (mounting hole, slot end) to Edge.Cuts at (x, y)."""
    x, y = _num(x, "x"), _num(y, "y")
    diameter = _num(diameter, "diameter", positive=True)

    def edit(b):
        width = b.GetDesignSettings().GetLineThickness(pcbnew.Edge_Cuts)
        circle = pcbnew.PCB_SHAPE(b)
        circle.SetShape(pcbnew.SHAPE_T_CIRCLE)
        circle.SetLayer(pcbnew.Edge_Cuts)
        circle.SetCenter(_vec(x, y))
        circle.SetEnd(_vec(x + diameter / 2, y))
        circle.SetWidth(width)
        b.Add(circle)
        return {"uuid": circle.m_Uuid.AsString(), "diameter": diameter}

    return _edit(board, edit)


@rpc
def add_keepout(board, polygon, layers=None, no_tracks=True, no_vias=True, no_pours=True,
                no_pads=False):
    """Add a rule-area keepout: no tracks/vias/pours (optionally no pads) inside the polygon."""
    pts = _points(polygon, "polygon", 3)

    def edit(b):
        zone = pcbnew.ZONE(b)
        zone.SetIsRuleArea(True)
        zone.SetDoNotAllowTracks(bool(no_tracks))
        zone.SetDoNotAllowVias(bool(no_vias))
        zone.SetDoNotAllowZoneFills(bool(no_pours))
        zone.SetDoNotAllowPads(bool(no_pads))
        layer_set = pcbnew.LSET()
        for name in (layers or ["F.Cu", "B.Cu"]):
            layer_set.AddLayer(_copper_layer(b, name))
        zone.SetLayerSet(layer_set)
        shape = zone.Outline()
        shape.NewOutline()
        for x, y in pts:
            shape.Append(_iu(x), _iu(y))
        b.Add(zone)
        return {"uuid": zone.m_Uuid.AsString()}

    return _edit(board, edit)


@rpc
def refill_zones(board):
    """Refill every zone on the board (the file is left alone when there are none)."""
    path = _abspath(board)
    b = _load(path, for_edit=True)
    n = _fill_zones(b)
    if n:
        _save(b, path)
    return {"zones": n}


@rpc
def create_board(board, width=50, height=50):
    """Start a new, empty two-layer board with a rectangular outline.

    Used when a schematic becomes a laid-out board: the caller then places the schematic's
    footprints and assigns their pads. Refuses to overwrite an existing board.
    """
    width = _num(width, "width", positive=True)
    height = _num(height, "height", positive=True)
    path = _abspath(board)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        raise RpcError("InvalidParams", "board already exists: %s" % path)
    tmp = path + ".new"
    fresh = pcbnew.NewBoard(tmp)
    try:
        s = pcbnew.PCB_SHAPE(fresh)
        s.SetShape(pcbnew.SHAPE_T_RECTANGLE)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetStart(_vec(0, 0))
        s.SetEnd(_vec(width, height))
        s.SetWidth(fresh.GetDesignSettings().GetLineThickness(pcbnew.Edge_Cuts))
        fresh.Add(s)
        _save(fresh, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return {"bbox": [0, 0, round(width, 6), round(height, 6)]}


@rpc
def add_footprint(board, footprint, ref, value, x, y, rotation=0, side="front", footprint_dirs=None):
    """Place a library footprint (``Lib:Name`` or bare ``Name``, searched in ``footprint_dirs``)."""
    x, y, rotation = _num(x, "x"), _num(y, "y"), _num(rotation, "rotation")
    if side not in ("front", "back"):
        raise RpcError("InvalidParams", "side must be 'front' or 'back', got %r" % (side,))
    if not isinstance(ref, str) or not ref:
        raise RpcError("InvalidParams", "ref must be a non-empty reference designator")
    libdir, nick, name = _find_footprint_file(footprint, footprint_dirs)

    def edit(b):
        if b.FindFootprintByReference(ref) is not None:
            raise RpcError("InvalidParams", "Ref %r is already used on this board" % ref)
        fp = pcbnew.FootprintLoad(libdir, name)
        if fp is None:
            raise RpcError("NotFound", "pcbnew could not load footprint %s from %s" % (name, libdir))
        existing = set(_items_by_uuid(b))
        if fp.m_Uuid.AsString() in existing or any(p.m_Uuid.AsString() in existing for p in fp.Pads()):
            fp = pcbnew.Cast_to_FOOTPRINT(fp.Duplicate(False))  # fresh UUIDs for it and its children
        fp.SetFPID(pcbnew.LIB_ID(nick, name))
        fp.SetReference(ref)
        fp.SetValue(str(value))
        b.Add(fp)
        fp.SetPosition(_vec(x, y))
        if side == "back":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(_angle(rotation))
        enabled = b.GetEnabledLayers()
        return {"uuid": fp.m_Uuid.AsString(), "ref": ref,
                "pads": [_pad_entry(fp, p, enabled) for p in fp.Pads()]}

    return _edit(board, edit)


@rpc
def remove_footprint(board, ref):
    """Delete a footprint by reference designator."""

    def edit(b):
        fp = _footprint(b, ref)
        uid = fp.m_Uuid.AsString()
        b.Delete(fp)
        return {"ref": ref, "uuid": uid}

    return _edit(board, edit)


@rpc
def set_pad_net(board, ref, pad, net):
    """Assign ``net`` to every pad of ``ref`` numbered ``pad``; unknown nets are created."""

    def edit(b):
        fp = _footprint(b, ref)
        pads = _pads_by_number(fp, pad)
        netinfo = _find_net(b, net, create=True)
        for p in pads:
            p.SetNet(netinfo)
        return {"ref": ref, "pad": str(pad), "net": netinfo.GetNetname()}

    return _edit(board, edit)


@rpc
def export_dsn(board, path):
    """Export a Specctra DSN for Freerouting."""
    out = os.path.abspath(os.path.expanduser(str(path)))
    b = _load(board)
    if not pcbnew.ExportSpecctraDSN(b, out) or not os.path.isfile(out):
        raise RpcError("ExportError", "pcbnew could not export a DSN to %s" % out)
    return {"path": out}


@rpc
def import_ses(board, path):
    """Import a Specctra session (replaces the board's tracks and vias with the session's)."""
    ses = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(ses):
        raise RpcError("NotFound", "Session file not found: %s" % ses)

    def edit(b):
        if not pcbnew.ImportSpecctraSES(b, ses):
            raise RpcError("ImportError", "pcbnew could not import %s" % ses)
        tracks = list(b.GetTracks())
        vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
        return {"tracks": len(tracks) - vias, "vias": vias}

    return _edit(board, edit)


# --------------------------------------------------------------------------- dispatch


def _handle(line):
    try:
        req = json.loads(line)
    except ValueError as exc:
        return {"id": None, "error": {"type": "ProtocolError", "message": "Bad JSON: %s" % exc}}
    if not isinstance(req, dict):
        return {"id": None, "error": {"type": "ProtocolError", "message": "Request must be a JSON object"}}
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    fn = METHODS.get(method) if isinstance(method, str) else None
    if fn is None:
        return {"id": rid, "error": {"type": "UnknownMethod",
                                     "message": "Unknown method %r. Methods: %s" % (method, ", ".join(sorted(METHODS)))}}
    if not isinstance(params, dict):
        return {"id": rid, "error": {"type": "InvalidParams", "message": "params must be a JSON object"}}
    sig = inspect.signature(fn)
    try:
        sig.bind(**params)
    except TypeError as exc:
        return {"id": rid, "error": {"type": "InvalidParams",
                                     "message": "%s%s: %s" % (method, sig, exc)}}
    t0 = time.time()
    try:
        result = fn(**params)
        reply = {"id": rid, "result": result}
    except RpcError as exc:
        reply = {"id": rid, "error": {"type": exc.type, "message": exc.message}}
    except Exception as exc:  # unexpected: report the class name, log the traceback
        traceback.print_exc(file=sys.stderr)
        reply = {"id": rid, "error": {"type": type(exc).__name__, "message": "%s: %s" % (method, exc)}}
    sys.stderr.write("worker: %s %.3fs\n" % (method, time.time() - t0))
    return reply


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        reply = _handle(line)
        try:
            text = json.dumps(reply, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            text = json.dumps({"id": reply.get("id"),
                               "error": {"type": "ProtocolError", "message": "Unserializable result: %s" % exc}})
        _PROTO.write(text + "\n")
        _PROTO.flush()


if __name__ == "__main__":
    main()
