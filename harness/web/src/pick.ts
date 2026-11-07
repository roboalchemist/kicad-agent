/** Pick shapes and hit-testing built from the pick index (DESIGN.md, "Pick index"). */

import type { PickIndex } from "./types.ts";

export interface HitShape {
  uuid: string;
  kind: "pad" | "footprint" | "track" | "via" | "zone";
  /** SVG path data, in svg-mm space (caller converts from board mm). */
  d: string;
  /** For pads/vias we keep a point + radius for tooltips; tracks have two ends. */
  center: [number, number];
}

const r2 = (v: number) => Math.round(v * 1000) / 1000;

function polygonD(points: [number, number][]): string {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${r2(p[0])} ${r2(p[1])}`).join(" ") + " Z";
}

function rectD(bbox: [number, number, number, number]): string {
  const [x, y, w, h] = bbox;
  return `M${r2(x)} ${r2(y)} L${r2(x + w)} ${r2(y)} L${r2(x + w)} ${r2(y + h)} L${r2(x)} ${r2(y + h)} Z`;
}

/** True if the entity belongs on the given side's overlay. Front-side entities hide on the back view. */
export function onSide(e: { layers?: string[]; side?: string; layer?: string }, side: "front" | "back"): boolean {
  if (e.side) return e.side === side;
  const layer = e.layer;
  if (layer) {
    if (layer === "F.Cu" || layer.startsWith("F.")) return side === "front";
    if (layer === "B.Cu" || layer.startsWith("B.")) return side === "back";
    return true; // through / multi-layer
  }
  if (e.layers) {
    const cu = e.layers.find((l) => l.endsWith(".Cu"));
    if (cu) return cu.startsWith("F.") ? side === "front" : side === "back";
    return true;
  }
  return true;
}

/**
 * Build hit shapes in svg-mm coordinates.
 * `toSvg` maps board mm -> svg mm (front: x-ex; back: mirrored).
 */
export function buildHitShapes(index: PickIndex, toSvg: (x: number, y: number) => [number, number], visible?: Set<string>): HitShape[] {
  const shapes: HitShape[] = [];
  const shown = (layer?: string | null) => !visible || !layer || layerShown(layer, visible);
  // DOM order = pick priority (topmost last): zones < tracks < vias < footprints < pads.
  for (const z of index.zones) {
    if (!shown(z.layer)) continue;
    const pts = z.outline.map((p) => toSvg(p[0], p[1]));
    if (pts.length > 2) {
      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      shapes.push({ uuid: z.uuid, kind: "zone", d: polygonD(pts), center: [cx, cy] });
    }
  }
  for (const t of index.tracks) {
    if (!shown(t.layer)) continue;
    const [x1, y1] = toSvg(t.x1, t.y1);
    const [x2, y2] = toSvg(t.x2, t.y2);
    shapes.push({ uuid: t.uuid, kind: "track", d: `M${r2(x1)} ${r2(y1)} L${r2(x2)} ${r2(y2)}`, center: toSvg((t.x1 + t.x2) / 2, (t.y1 + t.y2) / 2) });
  }
  for (const v of index.vias) {
    if (visible && !visible.has("F.Cu") && !visible.has("B.Cu")) continue;
    const [cx, cy] = toSvg(v.x, v.y);
    const r = v.size / 2;
    shapes.push({ uuid: v.uuid, kind: "via", d: `M${r2(cx - r)} ${r2(cy)} A${r2(r)} ${r2(r)} 0 1 0 ${r2(cx + r)} ${r2(cy)} A${r2(r)} ${r2(r)} 0 1 0 ${r2(cx - r)} ${r2(cy)} Z`, center: [cx, cy] });
  }
  for (const fp of index.footprints) {
    // A footprint's picture is its silkscreen; with the silk hidden there is nothing to click.
    if (visible && !visible.has(fp.side === "back" ? "B.SilkS" : "F.SilkS")) continue;
    const [x, y, w, h] = fp.bbox;
    const [x1, y1] = toSvg(x, y);
    const [x2, y2] = toSvg(x + w, y + h);
    shapes.push({ uuid: fp.uuid, kind: "footprint", d: rectD([Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1)]), center: toSvg(fp.x, fp.y) });
  }
  for (const pad of index.pads) {
    if (visible && !pad.layers.some((layer) => layerShown(layer, visible))) continue;
    const pts = pad.polygon?.map((p) => toSvg(p[0], p[1]));
    if (pts && pts.length) shapes.push({ uuid: pad.uuid, kind: "pad", d: polygonD(pts), center: toSvg(pad.x, pad.y) });
  }
  return shapes;
}

/** Whether a plotted layer is shown, expanding KiCad's wildcard pad layers (``*.Cu``, ``*.Mask``). */
function layerShown(layer: string, visible: Set<string>): boolean {
  if (visible.has(layer)) return true;
  if (layer === "*.Cu") return visible.has("F.Cu") || visible.has("B.Cu");
  if (layer === "*.Mask") return visible.has("F.Mask") || visible.has("B.Mask");
  if (layer === "*.Paste") return visible.has("F.Paste") || visible.has("B.Paste");
  return false;
}

export interface EntityAt {
  kind: HitShape["kind"];
  uuid: string;
}

/** Index lookups by uuid for tooltips/identity. */
export function entityInfo(index: PickIndex, uuid: string):
  | { kind: string; ref: string | null; pad: string | null; net: string | null; layer: string | null; x: number; y: number; uuid: string }
  | null {
  const pad = index.pads.find((p) => p.uuid === uuid);
  if (pad) return { kind: "pad", ref: pad.ref, pad: pad.pad, net: pad.net, layer: pad.layers.find((l) => l.endsWith(".Cu")) ?? null, x: pad.x, y: pad.y, uuid };
  const fp = index.footprints.find((f) => f.uuid === uuid);
  if (fp) return { kind: "footprint", ref: fp.ref, pad: null, net: null, layer: null, x: fp.x, y: fp.y, uuid };
  const t = index.tracks.find((t) => t.uuid === uuid);
  if (t) return { kind: "track", ref: null, pad: null, net: t.net, layer: t.layer, x: (t.x1 + t.x2) / 2, y: (t.y1 + t.y2) / 2, uuid };
  const v = index.vias.find((v) => v.uuid === uuid);
  if (v) return { kind: "via", ref: null, pad: null, net: v.net, layer: "F.Cu&B.Cu", x: v.x, y: v.y, uuid };
  const z = index.zones.find((z) => z.uuid === uuid);
  if (z) return { kind: "zone", ref: null, pad: null, net: z.net, layer: z.layer, x: 0, y: 0, uuid };
  return null;
}
