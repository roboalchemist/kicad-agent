"""Images for the agent and the UI (DESIGN.md, "render.py").

``render_2d`` crops a kicad-cli board-area SVG (see ``kicad_cli.export_svg``) by rewriting its
viewBox, draws highlights, labels and pins from the pick index, and rasterizes with cairosvg.
``render_3d_view`` frames ``kicad-cli pcb render`` on a point with the measured camera model.
"""

from __future__ import annotations

import io
import math
import os
import re
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image

from ..config import Config
from . import geometry, kicad_cli
from .client import EngineError

# cairocffi finds libcairo through ctypes, which reads this at lookup time (Homebrew's cairo).
os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

# kicad-cli pcb render (orthographic, KiCad 10.0.3): at zoom 1 the requested image height shows
# K * max(board_w, board_h) mm, and the image centre is board_centre + pivot.
CAMERA_K = 1.0355
# kicad-cli renders slightly smaller than requested (8 px raytracer blocks, up to ~32 px less);
# render_3d_view requests this much extra and crops the centre.
RENDER_PAD_PX = 64
MAX_PX = 6000

# KiCad Default (dark PCB theme) board background: rgb(0,16,35) in the built-in theme.
# Shared with the SVG drill-hole punch-through and the web viewer canvas (kicad_cli.SVG_BACKGROUND).
BACKGROUND = "#001023"
HIGHLIGHT = "#39ff14"  # not a KiCad default layer colour (B.Mask is cyan, F.Mask purple)
LABEL = "#ffffff"
LABEL_DIM = "#8a96a3"
PIN = "#ffb300"
FONT = "Helvetica, Arial, sans-serif"

_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)
_VIEWBOX = re.compile(r'viewBox\s*=\s*"([^"]+)"')


@dataclass(frozen=True)
class SvgFrame:
    """Maps board coordinates (mm) to the user units of a board-area SVG.

    The SVG origin is the top-left of ``edge_bbox``; a mirrored (back) SVG has
    ``x_svg = page_w - (x - ex)``.
    """

    ex: float
    ey: float
    page_w: float
    page_h: float
    mirror: bool

    def to_svg(self, x: float, y: float) -> tuple[float, float]:
        """Board (x, y) -> SVG user units."""
        sx = x - self.ex
        return (self.page_w - sx if self.mirror else sx, y - self.ey)


def svg_frame(board_svg: str, index: dict[str, Any], side: str = "front") -> SvgFrame:
    """The board->SVG mapping for an SVG from ``kicad_cli.export_svg``."""
    tag = _SVG_TAG.search(board_svg)
    box = _VIEWBOX.search(tag.group(0)) if tag else None
    if not box:
        raise EngineError("InvalidParams", "board_svg has no <svg viewBox=...>; pass kicad_cli.export_svg output")
    vals = [float(v) for v in box.group(1).replace(",", " ").split()]
    ex, ey, _, _ = index["board"]["edge_bbox"]
    return SvgFrame(ex, ey, vals[2], vals[3], _side(side) == "back")


def _side(side: str) -> str:
    s = {"top": "front", "bottom": "back"}.get(side, side)
    if s not in ("front", "back"):
        raise EngineError("InvalidParams", f"side must be 'front' or 'back', got {side!r}")
    return s


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _crop(index: dict[str, Any], center: Sequence[float] | None, view_mm: float | None,
          region: Sequence[float] | None = None) -> tuple[float, float, float, float]:
    ex, ey, ew, eh = index["board"]["edge_bbox"]
    if region is not None:
        x1, y1, x2, y2 = (float(v) for v in region)
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w <= 0 or h <= 0:
            raise EngineError("InvalidParams", f"region must be a non-empty box, got {list(region)}")
        return min(x1, x2), min(y1, y2), w, h
    if center is None and view_mm is None:
        margin = max(0.5, 0.03 * max(ew, eh))
        return ex - margin, ey - margin, ew + 2 * margin, eh + 2 * margin
    cx, cy = (float(center[0]), float(center[1])) if center is not None else (ex + ew / 2, ey + eh / 2)
    v = float(view_mm) if view_mm is not None else max(ew, eh) * 1.06
    if v <= 0:
        raise EngineError("InvalidParams", f"view_mm must be > 0, got {view_mm}")
    return cx - v / 2, cy - v / 2, v, v


class _Overlay:
    """SVG overlay elements, written in SVG user units (mm) with pixel-sized strokes and text."""

    def __init__(self, frame: SvgFrame, px_mm: float) -> None:
        self.frame = frame
        self.px = 1.0 / px_mm  # one output pixel in mm
        self.parts: list[str] = []

    def pts(self, points: Iterable[Sequence[float]]) -> str:
        return " ".join(f"{_fmt(sx)},{_fmt(sy)}" for sx, sy in (self.frame.to_svg(p[0], p[1]) for p in points))

    def polygon(self, points: Sequence[Sequence[float]], *, stroke: str, fill: str = "none",
                fill_opacity: float = 0.0, width_px: float = 2.0, dash: bool = False) -> None:
        if len(points) < 2:
            return
        extra = f' stroke-dasharray="{_fmt(6 * self.px)},{_fmt(4 * self.px)}"' if dash else ""
        self.parts.append(
            f'<polygon points="{self.pts(points)}" fill="{fill}" fill-opacity="{fill_opacity}" stroke="{stroke}" '
            f'stroke-width="{_fmt(width_px * self.px)}" stroke-linejoin="round"{extra}/>')

    def circle(self, x: float, y: float, r: float, *, stroke: str, fill: str = "none", fill_opacity: float = 0.0,
               width_px: float = 2.0) -> None:
        sx, sy = self.frame.to_svg(x, y)
        self.parts.append(f'<circle cx="{_fmt(sx)}" cy="{_fmt(sy)}" r="{_fmt(r)}" fill="{fill}" '
                          f'fill-opacity="{fill_opacity}" stroke="{stroke}" stroke-width="{_fmt(width_px * self.px)}"/>')

    def text(self, x: float, y: float, label: str, *, size_px: float, color: str = LABEL, bold: bool = True,
             halo: bool = True, angle: float = 0.0) -> None:
        """Centred text at board (x, y); ``angle`` is an SVG rotation (degrees, clockwise)."""
        sx, sy = self.frame.to_svg(x, y)
        size = size_px * self.px
        weight = ' font-weight="bold"' if bold else ""
        turn = f' transform="rotate({_fmt(angle)} {_fmt(sx)} {_fmt(sy)})"' if angle else ""
        common = (f'x="{_fmt(sx)}" y="{_fmt(sy + 0.35 * size)}" font-family="{FONT}" font-size="{_fmt(size)}" '
                  f'text-anchor="middle"{weight}{turn}')
        body = escape(label)
        if halo:
            self.parts.append(f'<text {common} fill="none" stroke="#000000" stroke-opacity="0.85" '
                              f'stroke-width="{_fmt(3 * self.px)}" stroke-linejoin="round">{body}</text>')
        self.parts.append(f'<text {common} fill="{color}">{body}</text>')

    def svg(self) -> str:
        return '<g id="harness-overlay">' + "".join(self.parts) + "</g>"


def _pad_label(ov: _Overlay, pad: dict[str, Any], label_px: float) -> None:
    """Net name on a pad, along its long axis and shrunk to fit it (never below 7 px)."""
    w, h, text = pad["w"], pad["h"], pad["net"]
    long_axis = pad["rotation"] + (90.0 if h > w else 0.0)  # KiCad degrees, CCW on screen
    angle = long_axis if ov.frame.mirror else -long_axis  # SVG rotation, clockwise
    angle = (angle + 90.0) % 180.0 - 90.0  # keep text upright
    px_mm = 1.0 / ov.px
    size = min(label_px * 0.8, 0.8 * min(w, h) * px_mm, 0.9 * max(w, h) * px_mm / (0.62 * max(len(text), 1)))
    ov.text(pad["x"], pad["y"], text, size_px=max(size, 7.0), color=HIGHLIGHT, angle=angle)


def _highlight(ov: _Overlay, index: dict[str, Any], entities: list[dict[str, Any]], label_px: float) -> None:
    labelled: set[str] = set()
    for ent in entities:
        kind = ent["kind"]
        if kind == "footprint":
            body = geometry.footprint_body(ent)
            ov.polygon(list(body.exterior.coords), stroke=HIGHLIGHT, width_px=2, dash=True)
            pads = [dict(p, kind="pad") for p in index.get("pads", []) if p["ref"] == ent["ref"]]
        else:
            pads = [ent] if kind == "pad" else []
        for pad in pads:
            ov.polygon(pad["polygon"], stroke=HIGHLIGHT, fill=HIGHLIGHT, fill_opacity=0.25, width_px=2)
        if kind in ("track", "zone"):
            shape = geometry.entity_shape(ent)
            for g in getattr(shape, "geoms", [shape]):
                if hasattr(g, "exterior"):
                    ov.polygon(list(g.exterior.coords), stroke=HIGHLIGHT, fill=HIGHLIGHT,
                               fill_opacity=0.25 if kind == "track" else 0.12, width_px=2)
        elif kind == "via":
            ov.circle(ent["x"], ent["y"], ent["size"] / 2, stroke=HIGHLIGHT, fill=HIGHLIGHT, fill_opacity=0.3)
        elif kind == "point":
            ov.circle(ent["x"], ent["y"], 6 * ov.px, stroke=HIGHLIGHT, width_px=2.5)
        for pad in pads:
            if pad.get("net") and pad["uuid"] not in labelled:
                labelled.add(pad["uuid"])
                _pad_label(ov, pad, label_px)


def render_2d(board_svg: str, index: dict[str, Any], *, center: Sequence[float] | None = None,
              view_mm: float | None = None, region: Sequence[float] | None = None,
              px_per_mm: float | None = None, width_px: int = 1200,
              highlight: Iterable[Any] = (), labels: bool = True, pad_labels: bool = False,
              pins: Iterable[dict[str, Any]] = (), scale_bar: bool = False,
              side: str = "front") -> bytes:
    """Rasterize a crop of a board-area SVG to PNG bytes.

    - Crop: ``region`` [x1,y1,x2,y2] wins; with neither ``center`` nor ``view_mm``, the whole
      board plus a small margin; else a square ``view_mm`` wide around ``center``.
    - Size: ``px_per_mm`` if given, else ``width_px`` across (capped at 6000 px a side).
    - ``highlight``: specs for ``geometry.resolve`` (``U1``, ``U1.3``, ``net:GND``, UUIDs) or
      entity dicts; outlined in cyan, with net names on highlighted pads.
    - ``labels``: refdes at each footprint (dimmed for parts on the other side).
    - ``pad_labels``: pad numbers at their centres (for close-up inspection).
    - ``scale_bar``: a labelled mm bar in the lower-left corner.
    - ``pins``: ``{x, y, label}`` markers, e.g. review-thread numbers.
    - ``side``: ``front``, or ``back`` for the mirrored back SVG.
    """
    side = _side(side)
    frame = svg_frame(board_svg, index, side)
    x0, y0, w, h = _crop(index, center, view_mm, region)
    if px_per_mm is not None:
        if px_per_mm <= 0:
            raise EngineError("InvalidParams", f"px_per_mm must be > 0, got {px_per_mm}")
        out_w, out_h = round(w * px_per_mm), round(h * px_per_mm)
    else:
        out_w = int(width_px)
        out_h = round(out_w * h / w)
    if out_w < 1 or out_h < 1 or max(out_w, out_h) > MAX_PX:
        raise EngineError("InvalidParams", f"output size {out_w}x{out_h}px is out of range (max {MAX_PX})")
    px_mm = out_w / w

    # viewBox in SVG units; mirroring flips which board edge is on the left.
    sx_a, sy0 = frame.to_svg(x0, y0)
    sx_b, _ = frame.to_svg(x0 + w, y0)
    vb = (min(sx_a, sx_b), sy0, w, h)

    ov = _Overlay(frame, px_mm)
    label_px = max(11.0, min(20.0, out_w / 55.0))
    entities = geometry.entities_in(index, highlight)
    _highlight(ov, index, entities, label_px)
    if pad_labels:
        for pad in index.get("pads", []):
            if x0 <= float(pad["x"]) <= x0 + w and y0 <= float(pad["y"]) <= y0 + h:
                # Offset to the pad's upper-right corner so it does not sit under the net name
                # that `_highlight` draws along the pad's long axis.
                ov.text(float(pad["x"]) + 0.32 * float(pad.get("w") or 0),
                        float(pad["y"]) - 0.32 * float(pad.get("h") or 0),
                        str(pad["pad"]), size_px=label_px * 0.7, color=PIN)
    if scale_bar:
        _scale_bar(ov, x0, y0, w, h, px_mm)
    if labels:
        for fp in index.get("footprints", []):
            bx, by, bw, bh = fp["bbox"]
            near = fp.get("side", "front") == side
            ov.text(bx + bw / 2, by + bh / 2, fp["ref"], size_px=label_px, color=LABEL if near else LABEL_DIM)
    for pin in pins:
        r = 11 * ov.px
        ov.circle(float(pin["x"]), float(pin["y"]), r, stroke="#000000", fill=PIN, fill_opacity=0.95, width_px=1.5)
        ov.text(float(pin["x"]), float(pin["y"]), str(pin.get("label", "")), size_px=13, color="#000000", halo=False)

    tag = _SVG_TAG.search(board_svg)
    assert tag is not None  # svg_frame checked it
    head = tag.group(0)
    head = re.sub(r'\s(width|height|viewBox|preserveAspectRatio)\s*=\s*"[^"]*"', "", head)
    head = head[:-1].rstrip("/").rstrip() + (
        f' width="{out_w}" height="{out_h}" viewBox="{" ".join(_fmt(v) for v in vb)}"'
        f' preserveAspectRatio="none">')
    background = (f'<rect x="{_fmt(vb[0])}" y="{_fmt(vb[1])}" width="{_fmt(vb[2])}" height="{_fmt(vb[3])}" '
                  f'fill="{BACKGROUND}"/>')
    body = board_svg[tag.end():]
    end = body.rfind("</svg>")
    if end < 0:
        raise EngineError("InvalidParams", "board_svg is truncated (no </svg>)")
    svg = board_svg[:tag.start()] + head + background + body[:end] + ov.svg() + body[end:]

    import cairosvg  # imported late: needs DYLD_FALLBACK_LIBRARY_PATH (set above)

    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=out_w, output_height=out_h)
    assert isinstance(png, bytes)
    return png


def frame_3d(index: dict[str, Any], *, center: Sequence[float] | None = None, view_mm: float | None = None,
             height: int = 900, requested_height: int | None = None) -> dict[str, Any]:
    """Camera parameters that put ``center`` in the middle and ``view_mm`` across ``height`` px.

    Returns ``{"zoom", "pivot", "px_per_mm", "view_mm", "center"}``. ``pivot`` is
    ``((x - cx)/10, (cy - y)/10, 0)`` cm from the outline centre (+Y up). The zoom is scaled for
    ``requested_height`` (the taller image actually requested from kicad-cli) so that the crop
    of ``height`` px shows ``view_mm``: px/mm = zoom * requested_height / (K * max(w, h)).
    """
    ex, ey, ew, eh = index["board"]["edge_bbox"]
    maxdim = max(ew, eh)
    if maxdim <= 0:
        raise EngineError("InvalidParams", "board has no outline to frame")
    cx, cy = ex + ew / 2, ey + eh / 2
    x, y = (float(center[0]), float(center[1])) if center is not None else (cx, cy)
    view = float(view_mm) if view_mm is not None else CAMERA_K * maxdim
    if view <= 0:
        raise EngineError("InvalidParams", f"view_mm must be > 0, got {view_mm}")
    req_h = requested_height or height
    px_per_mm = height / view
    zoom = px_per_mm * CAMERA_K * maxdim / req_h
    return {"zoom": zoom, "pivot": ((x - cx) / 10.0, (cy - y) / 10.0, 0.0), "px_per_mm": px_per_mm,
            "view_mm": view, "center": [x, y]}


def _scale_bar(ov: _Overlay, x0: float, y0: float, w: float, h: float, px_mm: float) -> None:
    """A round-number mm bar in the lower-left of the crop, with its label."""
    target = w / 6.0
    exponent = math.floor(math.log10(target)) if target > 0 else 0
    length = min((1, 2, 5, 10), key=lambda m: abs(m * 10 ** exponent - target)) * 10 ** exponent
    if length <= 0:
        return
    x, y = x0 + 0.04 * w, y0 + h - 0.06 * h
    pad = 5 * ov.px
    ov.polygon([[x - pad, y - 8 * ov.px], [x + length + pad, y - 8 * ov.px],
                [x + length + pad, y + 3 * ov.px], [x - pad, y + 3 * ov.px]],
               stroke="none", fill=BACKGROUND, fill_opacity=0.72)
    ov.polygon([[x, y], [x + length, y]], stroke=LABEL, width_px=4)
    ov.polygon([[x, y - 1.2 / px_mm], [x, y + 1.2 / px_mm]], stroke=LABEL, width_px=3)
    ov.polygon([[x + length, y - 1.2 / px_mm], [x + length, y + 1.2 / px_mm]], stroke=LABEL, width_px=3)
    ov.text(x + length / 2, y - 2.4 / px_mm, f"{length:g} mm", size_px=13, color=LABEL)


def schematic_view(schematic: str | os.PathLike[str], *, region: Sequence[float] | None = None,
                   center: Sequence[float] | None = None, view_mm: float | None = None,
                   width: int = 1800, highlight: Iterable[Sequence[float]] = (),
                   scale_bar: bool = True) -> tuple[bytes, dict[str, Any]]:
    """Crop and annotate a schematic sheet; returns ``(png_bytes, view)``.

    ``region`` is ``[x1, y1, x2, y2]`` in sheet millimetres. ``highlight`` boxes are drawn in the
    same coordinates, so the caller can outline a symbol or a net without reading pixels.
    """
    import cairosvg

    with tempfile.TemporaryDirectory(prefix="harness-sch-view-") as tmp:
        svg = kicad_cli.export_sch_svg(schematic, tmp)[0]
        text = svg.read_text(encoding="utf-8")
    tag = _SVG_TAG.search(text)
    vb = _VIEWBOX.search(tag.group(0)) if tag else None
    if not vb:
        raise EngineError("RenderFailed", "the schematic SVG has no viewBox")
    px, py, pw, ph = (float(v) for v in vb.group(1).replace(",", " ").split())
    if region is not None:
        x1, y1, x2, y2 = (float(v) for v in region)
        cx0, cy0, cw, ch = min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
    elif center is not None or view_mm is not None:
        cw = ch = float(view_mm or min(pw, ph))
        cx, cy = (float(center[0]), float(center[1])) if center else (px + pw / 2, py + ph / 2)
        cx0, cy0 = cx - cw / 2, cy - ch / 2
    else:
        cx0, cy0, cw, ch = px, py, pw, ph
    if cw <= 0 or ch <= 0:
        raise EngineError("InvalidParams", "schematic view region is empty")
    out_w = max(64, min(int(width), MAX_PX))
    out_h = max(1, round(out_w * ch / cw))
    stroke = 2.5 * cw / out_w
    font = max(10.0, out_w / 120.0) * cw / out_w
    parts: list[str] = []
    for raw in highlight:
        if len(raw) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in raw[:4])
        parts.append(f'<rect x="{_fmt(x1)}" y="{_fmt(y1)}" width="{_fmt(abs(x2 - x1))}" '
                     f'height="{_fmt(abs(y2 - y1))}" fill="none" stroke="{HIGHLIGHT}" '
                     f'stroke-width="{_fmt(stroke)}" stroke-dasharray="{_fmt(6 * stroke)},{_fmt(4 * stroke)}"/>')
    if scale_bar:
        target = cw / 6.0
        exponent = math.floor(math.log10(target)) if target > 0 else 0
        length = min((1, 2, 5, 10), key=lambda m: abs(m * 10 ** exponent - target)) * 10 ** exponent
        bx, by = cx0 + 0.04 * cw, cy0 + ch - 0.06 * ch
        parts.append(f'<line x1="{_fmt(bx)}" y1="{_fmt(by)}" x2="{_fmt(bx + length)}" y2="{_fmt(by)}" '
                     f'stroke="#000000" stroke-width="{_fmt(4 * cw / out_w)}"/>')
        parts.append(f'<text x="{_fmt(bx + length / 2)}" y="{_fmt(by - 2.4 * cw / out_w)}" '
                     f'font-family="{FONT}" font-size="{_fmt(font)}" fill="#000000" '
                     f'text-anchor="middle">{length:g} mm</text>')
    opening = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(cw)}mm" height="{_fmt(ch)}mm" '
               f'viewBox="{_fmt(cx0)} {_fmt(cy0)} {_fmt(cw)} {_fmt(ch)}">')
    text = _SVG_TAG.sub(opening, text, count=1)
    text = text.replace("</svg>", '<g id="harness-overlay">' + "".join(parts) + "</g></svg>")
    png = cairosvg.svg2png(bytestring=text.encode("utf-8"), output_width=out_w,
                           output_height=out_h, background_color="#FFFFFF")
    return png, {"x0": round(cx0, 3), "y0": round(cy0, 3), "w": round(cw, 3),
                 "h": round(ch, 3), "px_per_mm": round(out_w / cw, 4)}


def render_3d_view(board: str | os.PathLike[str], index: dict[str, Any], *, center: Sequence[float] | None = None,
                   view_mm: float | None = None, side: str = "top", rotate: Sequence[float] | None = None,
                   quality: str = "basic", width: int = 1200, height: int = 900,
                   config: Config | None = None) -> bytes:
    """Render a framed 3D view (PNG bytes, exactly ``width`` x ``height``).

    Without ``rotate``: an orthographic top/bottom view centred on ``center`` (default: board
    centre) showing ``view_mm`` vertically (default: the whole board); the bottom view mirrors X.
    With ``rotate`` (X,Y,Z degrees) the whole board is framed and ``center``/``view_mm`` are
    ignored. ``side`` accepts top/bottom or front/back.
    """
    side3d = {"front": "top", "back": "bottom"}.get(side, side)
    if side3d not in ("top", "bottom"):
        raise EngineError("InvalidParams", f"side must be top/bottom (or front/back), got {side!r}")
    width, height = int(width), int(height)
    if not (16 <= width <= MAX_PX and 16 <= height <= MAX_PX):
        raise EngineError("InvalidParams", f"size {width}x{height} out of range")
    req_w, req_h = width + RENDER_PAD_PX, height + RENDER_PAD_PX
    if rotate is not None:
        cam = frame_3d(index, height=height, requested_height=req_h)
        pivot = None
    else:
        cam = frame_3d(index, center=center, view_mm=view_mm, height=height, requested_height=req_h)
        pivot = cam["pivot"]
    with tempfile.TemporaryDirectory(prefix="harness-render-") as tmp:
        out = Path(tmp) / "render.png"
        kicad_cli.render_3d(board, out, side=side3d, rotate=rotate, zoom=cam["zoom"], pivot=pivot,
                            width=req_w, height=req_h, quality=quality, background="opaque", config=config)
        with Image.open(out) as im:
            im = im.convert("RGBA")
            ow, oh = im.size
            left, top = (ow - width) / 2.0, (oh - height) / 2.0
            if left >= 0 and top >= 0:
                img = im.crop((round(left), round(top), round(left) + width, round(top) + height))
            else:  # unexpectedly small output: centre it on a canvas
                img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
                img.paste(im, (round(-left), round(-top)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_size(png: bytes) -> tuple[int, int]:
    """(width, height) of PNG bytes."""
    with Image.open(io.BytesIO(png)) as im:
        return im.size


__all__ = ["render_2d", "render_3d_view", "schematic_png", "frame_3d", "svg_frame", "SvgFrame", "png_size", "CAMERA_K"]

def schematic_png(schematic: str | os.PathLike[str], out: str | os.PathLike[str], *,
                  width: int = 1800) -> Path:
    """Rasterise a whole ``.kicad_sch`` sheet, so the agent can simply look at it.

    The numeric legibility check cannot see taste: a sheet can score 0 and still look wrong at a
    glance (a part marooned at the edge, a wall of labels, a channel that reads backwards). This is
    the plain "get the image" companion to ``analyze``.
    """
    import cairosvg

    source = Path(schematic)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        svg = kicad_cli.export_sch_svg(source, tmp)[0]
        cairosvg.svg2png(url=str(svg), write_to=str(target),
                         output_width=min(int(width), MAX_PX), background_color="#FFFFFF")
    if not target.is_file():
        raise EngineError("RenderFailed", f"no PNG written to {target}")
    return target

