/**
 * The board viewer: KiCad SVG background + pick-index overlay, zoom/pan, hover, select/comment/
 * measure modes, thread pins, agent highlight, diff overlay, and the 3D tab.
 * See DESIGN.md, "Board viewer".
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api.ts";
import {
  BOARD_LAYERS,
  boardToSvg,
  fitView,
  getStore,
  handlePick,
  setMode,
  setRev,
  setBoardSize,
  setSide as setBoardSide,
  showAllLayers,
  toggleGrid,
  toggleLayer,
  togglePins,
  setView,
  svgToBoard,
  useHarness,
  type Pick,
  type Side,
} from "./store.ts";
import { buildHitShapes, entityInfo, type HitShape } from "./pick.ts";
import { ErrorBoundary } from "./ErrorBoundary.tsx";
import { Viewer3D } from "./Viewer3D.tsx";
import type { DiffEntity, Thread } from "./types.ts";

const SEVERITY_COLOR: Record<string, string> = {
  info: "#8a8f98",
  suggestion: "#2f6feb",
  warning: "#d99a17",
  must_fix: "#d43a3a",
};

interface SvgGeom {
  w: number;
  h: number;
  /** inner content for React: parsed svg with fixed px size */
  bg: React.ReactNode;
}

function parseSvg(svgText: string, w: number, h: number, key: string): SvgGeom | null {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const el = doc.querySelector("svg");
  if (!el) return null;
  el.setAttribute("width", String(w));
  el.setAttribute("height", String(h));
  // Edge.Cuts sits exactly on the viewBox edge; let its stroke paint outside the viewport.
  el.setAttribute("overflow", "visible");

  el.removeAttribute("style");
  return {
    w,
    h,
    bg: (
      <div
        key={key}
        className="board-bg"
        dangerouslySetInnerHTML={{ __html: el.outerHTML }}
      />
    ),
  };
}

/** CopilotKit-relevant highlight: agent-picked entities get cyan outlines. */
function highlightedUuids(index: NonNullable<ReturnType<typeof getStore>["index"]>, highlight: { uuids: string[]; refs: string[]; nets: string[] } | null): Set<string> {
  const out = new Set<string>(highlight?.uuids ?? []);
  if (!highlight) return out;
  const refs = new Set(highlight.refs.map((r) => r.toUpperCase()));
  const nets = new Set(highlight.nets.map((n) => n.replace(/^net:/i, "").toUpperCase()));
  for (const p of index.pads) {
    if (refs.has(p.ref.toUpperCase()) || (p.ref && p.pad && refs.has(`${p.ref}.${p.pad}`.toUpperCase())) || (p.net && nets.has(p.net.toUpperCase()))) out.add(p.uuid);
  }
  for (const f of index.footprints) if (refs.has(f.ref.toUpperCase())) out.add(f.uuid);
  for (const t of index.tracks) if (t.net && nets.has(t.net.toUpperCase())) out.add(t.uuid);
  for (const v of index.vias) if (v.net && nets.has(v.net.toUpperCase())) out.add(v.uuid);
  for (const z of index.zones) if (z.net && nets.has(z.net.toUpperCase())) out.add(z.uuid);
  return out;
}

export function BoardViewer({ onOpenThread }: { onOpenThread: (n: number) => void }) {
  const h = useHarness();
  const { index, svg, svgSide, side, rev, design, threads, diff, mode, selection, measure, measureA, measureB, highlight, view } = h;
  const sideLayers = BOARD_LAYERS[side === "back" ? "back" : "front"];
  // Layers the user has hidden in the right-hand panel; hidden layers are not painted and their
  // entities are not clickable, so the viewer stays consistent with what it shows.
  const visibleLayerSet = useMemo(
    () => new Set(sideLayers.filter((layer) => !h.hiddenLayers.includes(layer.id)).map((layer) => layer.id)),
    [sideLayers, h.hiddenLayers],
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ shape: HitShape; label: string; mx: number; my: number } | null>(null);
  const [render3d, setRender3d] = useState<{ url: string; loading: boolean; params: string }>({ url: "", loading: false, params: "" });
  const dragRef = useRef<{ sx: number; sy: number; cx: number; cy: number } | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;

  // Resize observer for the container.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // Keep the store's notion of the viewport in sync so no-arg fitView() calls (design switches,
  // SSE board updates) fit the real pane instead of a window-size guess.
  useEffect(() => {
    if (size.w > 0 && size.h > 0) setBoardSize(size.w, size.h);
  }, [size.w, size.h]);

  // Initial fit whenever the index first appears or rev/side changes.
  const fitKey = `${rev}-${side}-${index?.board_hash ?? ""}`;
  const lastFit = useRef("");
  useEffect(() => {
    if (index && size.w > 0 && lastFit.current !== fitKey) {
      lastFit.current = fitKey;
      fitView(size.w, size.h);
    }
  }, [index, size.w, size.h, fitKey]);

  // focus_view tool requests.
  useEffect(() => {
    if (h.focus && viewRef.current) {
      // focus carries board coords; store.focusOnBoard already set view when index exists.
      // For robustness re-apply here.
      if (h.focus.center && index) {
        const p = boardToSvg(h.focus.center[0], h.focus.center[1]);
        setView({ cx: p[0], cy: p[1], ...(h.focus.viewMM ? { mm: h.focus.viewMM } : {}) });
      }
    }
  }, [h.focus?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // One parsed SVG per layer, bottom to top; the panel hides a layer by dropping it from here.
  const layerGeoms = useMemo(() => {
    const key = `${rev}-${svgSide}-${h.svgKey}-${design?.board_hash ?? ""}`;
    const layers = sideLayers.flatMap((layer) => {
      const text = h.layerSvgs[layer.id];
      const dims = svgDimsOf(text ?? null);
      if (!text || !dims) return [];
      const geom = parseSvg(text, dims.w, dims.h, `${key}-${layer.id}`);
      return geom ? [{ id: layer.id, geom }] : [];
    });
    if (layers.length || !svg) return layers;
    // Before the per-layer stack for this side arrives (or if a layer fails to plot), fall back
    // to the single combined SVG the store last loaded, so the board never blinks away.
    const dims = svgDimsOf(svg);
    const geom = dims ? parseSvg(svg, dims.w, dims.h, `${key}-all`) : null;
    return geom ? [{ id: "all", geom }] : [];
  }, [sideLayers, h.layerSvgs, svg, rev, svgSide, h.svgKey, design?.board_hash]);

  const toSvg = useCallback(
    (x: number, y: number): [number, number] => {
      const [ex, ey, ew] = index ? index.board.edge_bbox : [0, 0, 0];
      if (side === "back") return [ex + ew - x, y - ey];
      return [x - ex, y - ey];
    },
    [index, side],
  );

  const hitShapes = useMemo(() => {
    if (!index) return [];
    const current = side === "back" ? "back" : "front";
    return buildHitShapes(index, toSvg, visibleLayerSet).filter((s) => {
      const sSide = sideEntitySide(index, s.uuid);
      return sSide === "both" || sSide === current;
    });
  }, [index, toSvg, side, visibleLayerSet]);

  // ------- 3D render loading
  const load3d = useCallback(
    async (params: string) => {
      if (!design) return;
      setRender3d((r) => ({ ...r, loading: true }));
      try {
        const url = await api.render3dUrl(design.name, rev, params);
        // fetch to force download before display (kicad render can take seconds)
        const res = await fetch(url);
        if (!res.ok) throw new Error(`render3d: ${res.status}`);
        const blob = await res.blob();
        setRender3d({ url: URL.createObjectURL(blob), loading: false, params });
      } catch {
        setRender3d((r) => ({ ...r, loading: false }));
      }
    },
    [design, rev],
  );
  useEffect(() => {
    if (side === "3d" && design) {
      const params = new URLSearchParams({ side: "top", quality: "basic" });
      void load3d(params.toString());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [side, design?.name, rev]);

  // ------- pointer handling
  const svgPoint = useCallback(
    (ev: { clientX: number; clientY: number }): [number, number] | null => {
      const rect = containerRef.current?.getBoundingClientRect();
      const v = viewRef.current;
      if (!rect || !v) return null;
      const scale = rect.height / v.mm;
      return [(ev.clientX - rect.left - rect.width / 2) / scale + v.cx, (ev.clientY - rect.top - rect.height / 2) / scale + v.cy];
    },
    [],
  );

  // Wheel zoom needs a non-passive listener: React attaches wheel handlers passively, so
  // preventDefault() there logs "Unable to preventDefault inside passive event listener" and the
  // page scrolls while zooming.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const rect = el.getBoundingClientRect();
      const v = viewRef.current;
      if (!rect || !v) return;
      const factor = Math.exp(ev.deltaY * 0.0012);
      const mm = Math.min(4000, Math.max(0.8, v.mm * factor));
      const scale = rect.height / v.mm;
      const newScale = rect.height / mm;
      const sx = (ev.clientX - rect.left - rect.width / 2) / scale + v.cx;
      const sy = (ev.clientY - rect.top - rect.height / 2) / scale + v.cy;
      const cx = sx - (ev.clientX - rect.left - rect.width / 2) / newScale;
      const cy = sy - (ev.clientY - rect.top - rect.height / 2) / newScale;
      setView({ cx, cy, mm });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [side]);

  const onPointerDown = useCallback((ev: React.PointerEvent) => {
    const v = viewRef.current;
    if (!v) return;
    dragRef.current = { sx: ev.clientX, sy: ev.clientY, cx: v.cx, cy: v.cy };
    (ev.target as Element).setPointerCapture?.(ev.pointerId);
  }, []);

  const onPointerMove = useCallback(
    (ev: React.PointerEvent) => {
      const drag = dragRef.current;
      const rect = containerRef.current?.getBoundingClientRect();
      if (drag && rect) {
        const v = viewRef.current!;
        const scale = rect.height / v.mm;
        setView({ cx: drag.cx - (ev.clientX - drag.sx) / scale, cy: drag.cy - (ev.clientY - drag.sy) / scale });
        return;
      }
      // hover: find the topmost hit shape under the cursor
      const el = document.elementFromPoint(ev.clientX, ev.clientY) as SVGElement | null;
      const hit = el?.closest("[data-hit]") as SVGElement | null;
      if (hit && index) {
        const uuid = hit.getAttribute("data-hit")!;
        const info = entityInfo(index, uuid);
        if (info) {
          const label = [
            info.kind,
            info.ref ? `${info.ref}${info.pad ? `.${info.pad}` : ""}` : info.net ? `net:${info.net}` : "",
            info.net && info.ref ? info.net : "",
            info.layer ?? "",
          ]
            .filter(Boolean)
            .join(" · ");
          setHover({ shape: { uuid, kind: info.kind as HitShape["kind"], d: "", center: [0, 0] }, label, mx: ev.clientX - (rect?.left ?? 0), my: ev.clientY - (rect?.top ?? 0) });
          return;
        }
      }
      setHover(null);
    },
    [index],
  );

  const onPointerUp = useCallback(
    (ev: React.PointerEvent) => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag && Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy) > 4) return; // was a pan
      const el = document.elementFromPoint(ev.clientX, ev.clientY) as SVGElement | null;
      if (el?.closest(".thread-pin")) return; // pin clicks are handled by the pin itself
      const hit = el?.closest("[data-hit]") as SVGElement | null;
      const pt = svgPoint(ev);
      if (!pt) return;
      const [bx, by] = svgToBoard(pt[0], pt[1]);
      if (hit && index) {
        const info = entityInfo(index, hit.getAttribute("data-hit")!);
        if (info) {
          void handlePick({
            kind: info.kind as Pick["kind"],
            uuid: info.uuid,
            ref: info.ref,
            pad: info.pad,
            net: info.net,
            layer: info.layer,
            side: side === "back" ? "back" : "front",
            x: bx,
            y: by,
            entity_x: info.x,
            entity_y: info.y,
          });
          return;
        }
      }
      // empty board -> point anchor
      void handlePick({
        kind: "point",
        uuid: null,
        ref: null,
        pad: null,
        net: null,
        layer: null,
        side: side === "back" ? "back" : "front",
        x: bx,
        y: by,
        entity_x: null,
        entity_y: null,
      });
    },
    [index, side, svgPoint],
  );

  const cursor = mode === "select" ? "default" : mode === "comment" ? "crosshair" : "crosshair";

  // The overlay in svg-mm space.
  const dims = layerGeoms.length ? { w: layerGeoms[0].geom.w, h: layerGeoms[0].geom.h } : svgDimsOf(svg);
  const highlightSet = useMemo(() => (index && highlight ? highlightedUuids(index, highlight) : new Set<string>()), [index, highlight]);
  const shownThreads = useMemo(() => (threads ?? []).filter((t) => t.resolved_anchor && t.resolved_anchor.state !== "detached"), [threads]);

  const v = view;
  const scale = size.h > 0 && v ? size.h / v.mm : 1;
  // KiCad-style dot grid: pick a step that keeps the dots 8-40 px apart on screen.
  const gridStep = [0.5, 1, 2, 5, 10].find((st) => st * scale >= 18) ?? 10;
  const gridPx = gridStep * scale;
  const tx = size.w / 2 - (v?.cx ?? 0) * scale;
  const showGrid = h.showGrid;
  // A round millimetre length for the scale bar, aiming at ~90 px.
  // Largest round length that still fits the legend box (~120 px).
  const barMM = [1, 2, 5, 10, 20, 50, 100].filter((mm) => mm * scale <= 120).pop() ?? 1;
  const ty = size.h / 2 - (v?.cy ?? 0) * scale;

  return (
    <div className="viewer-root">
      <ViewerToolbar side={side} setSide={setBoardSide} mode={mode} render3dLoading={render3d.loading} />
      {rev !== "working" && (
        <div className="rev-banner" data-testid="rev-banner">
          Viewing <b>{rev.slice(0, 8)}</b> · read-only ·{" "}
          <button className="link" onClick={() => void setRev("working")}>
            Back to latest
          </button>
        </div>
      )}
      <div className="board-area" data-testid="board-area">
        {side === "3d" ? (
          <div className="board-3d" data-testid="board-3d">
            <ErrorBoundary label="3D viewer">
            <Viewer3D
              design={design?.name ?? null}
              rev={rev}
              boardHash={design?.board_hash ?? null}
              fallbackUrl={!render3d.loading ? render3d.url : null}
            />
            </ErrorBoundary>
          </div>
        ) : (
          <div
            ref={containerRef}
            className="board-viewport"
            data-testid="board-viewport"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={() => {
              dragRef.current = null;
              setHover(null);
            }}
            style={{ cursor }}
          >
            {showGrid && (
              <div
                className="board-grid"
                data-testid="board-grid"
                style={{ backgroundSize: `${gridPx}px ${gridPx}px`, backgroundPosition: `${tx % gridPx}px ${ty % gridPx}px` }}
              />
            )}
            {layerGeoms.length > 0 && v && dims ? (
              <div className="board-stage" style={{ transform: `translate(${tx}px, ${ty}px) scale(${scale})`, width: dims.w, height: dims.h }}>
                <div className="board-bg-wrap" style={{ ["--edge-stroke" as string]: `${1.25 / Math.max(scale, 0.001)}` }}>
                  {layerGeoms
                    .filter((layer) => layer.id === "all" || !h.hiddenLayers.includes(layer.id))
                    .map((layer) => (
                      <div
                        key={layer.id}
                        className="board-bg-layer"
                        data-testid={`board-layer-${layer.id}`}
                        data-layer={layer.id}
                      >
                        {layer.geom.bg}
                      </div>
                    ))}
                </div>
                <OverlaySvg
                  dims={dims}
                  pxPerMM={scale}
                  hitShapes={hitShapes}
                  index={index}
                  threads={h.showPins ? shownThreads : []}
                  selection={selection}
                  highlightSet={highlightSet}
                  diff={diff}
                  measure={measure}
                  measureA={measureA}
                  measureB={measureB}
                  onOpenThread={onOpenThread}
                />
              </div>
            ) : (
              <div className="board-empty">{h.loading ? "Loading board…" : "No board loaded"}</div>
            )}
            {hover && (
              <div className="hover-tip" style={{ left: hover.mx + 14, top: hover.my + 14 }} data-testid="hover-tip">
                {hover.label}
              </div>
            )}
          </div>
        )}
        <PickPopover />
        <MeasureBox />
        {side !== "3d" && <ScaleLegend scale={scale} gridStep={gridStep} barMM={barMM} />}
        {side !== "3d" && (
          <LayerPanel
            layers={sideLayers}
            hidden={h.hiddenLayers}
            onToggle={toggleLayer}
            onShowAll={showAllLayers}
          />
        )}
        <FitButton onFit={() => fitView(size.w, size.h)} />
      </div>
    </div>
  );
}

function svgDimsOf(svg: string | null): { w: number; h: number } | null {
  if (!svg) return null;
  const m = /viewBox="([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"/.exec(svg);
  return m ? { w: parseFloat(m[3]), h: parseFloat(m[4]) } : null;
}

/** Which side an index entity shows on (for filtering hit shapes). */
function sideEntitySide(index: NonNullable<ReturnType<typeof getStore>["index"]>, uuid: string): "front" | "back" | "both" {
  const pad = index.pads.find((p) => p.uuid === uuid);
  if (pad) {
    const f = pad.layers.includes("F.Cu");
    const b = pad.layers.includes("B.Cu");
    return f && b ? "both" : f ? "front" : "back";
  }
  const fp = index.footprints.find((f) => f.uuid === uuid);
  if (fp) return fp.side === "front" ? "front" : "back";
  const t = index.tracks.find((t) => t.uuid === uuid);
  if (t) return t.layer.startsWith("F.") ? "front" : "back";
  const z = index.zones.find((z) => z.uuid === uuid);
  if (z) return z.layer.startsWith("F.") ? "front" : "back";
  return "both"; // vias are through
}

function ViewerToolbar({ side, setSide, mode, render3dLoading }: { side: Side; setSide: (s: Side) => void; mode: string; render3dLoading: boolean }) {
  const h = useHarness();
  return (
    <div className="viewer-toolbar">
      <div className="tab-strip">
        {(["front", "back", "3d"] as Side[]).map((s) => (
          <button key={s} className={`tab ${side === s ? "active" : ""}`} data-testid={`tab-${s}`} onClick={() => setSide(s)}>
            {s === "3d" ? "3D" : s === "front" ? "Front" : "Back"}
          </button>
        ))}
      </div>
      <div className="mode-strip">
        {(
          [
            ["select", "Select"],
            ["comment", "Comment"],
            ["measure", "Measure"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            className={`tab small ${h.mode === m ? "active" : ""}`}
            data-testid={`mode-${m}`}
            onClick={() => setMode(m)}
          >
            {label}
          </button>
        ))}
      </div>
      <button
        className={`tab small ${h.showPins ? "active" : ""}`}
        data-testid="toggle-pins"
        title={h.showPins ? "Hide review comments" : "Show review comments"}
        onClick={() => togglePins()}
      >
        Comments
      </button>
      <button
        className={`tab small ${h.showGrid ? "active" : ""}`}
        data-testid="toggle-grid"
        title={h.showGrid ? "Hide the dot grid" : "Show the KiCad-style dot grid"}
        onClick={() => toggleGrid()}
      >
        Grid
      </button>
      {render3dLoading && <span className="muted">rendering…</span>}
      <span className="muted rev-label">{mode}</span>
    </div>
  );
}

function FitButton({ onFit }: { onFit: () => void }) {
  return (
    <button className="fit-btn" onClick={onFit} title="Fit board">
      Fit
    </button>
  );
}

function OverlaySvg({
  dims,
  pxPerMM,
  hitShapes,
  index,
  threads,
  selection,
  highlightSet,
  diff,
  measure,
  measureA,
  measureB,
  onOpenThread,
}: {
  dims: { w: number; h: number };
  pxPerMM: number;
  hitShapes: HitShape[];
  index: ReturnType<typeof getStore>["index"];
  threads: Thread[];
  selection: Pick | null;
  highlightSet: Set<string>;
  diff: ReturnType<typeof getStore>["diff"];
  measure: ReturnType<typeof getStore>["measure"];
  measureA: Pick | null;
  measureB: Pick | null;
  onOpenThread: (n: number) => void;
}) {
  if (!index) return null;
  const toSvg = (x: number, y: number): [number, number] => boardToSvg(x, y);
  return (
    <svg className="board-overlay" width={dims.w} height={dims.h} viewBox={`0 0 ${dims.w} ${dims.h}`} data-testid="board-overlay">
      {/* hit layer */}
      <g className="hit-layer">
        {hitShapes.map((s) => (
          <path
            key={s.uuid}
            data-hit={s.uuid}
            d={s.d}
            className={`hit hit-${s.kind}`}
            style={{ strokeWidth: s.kind === "track" ? trackWidth(index, s.uuid) ?? 0.3 : undefined }}
          />
        ))}
      </g>
      {/* visual layer */}
      <g className="visual-layer">
        {/* diff overlay */}
        {diff && <DiffOverlay diff={diff} toSvg={toSvg} />}
        {/* agent highlight: cyan outline */}
        {[...highlightSet].map((uuid) => (
          <OutlineRect key={`hl-${uuid}`} index={index} uuid={uuid} toSvg={toSvg} className="hl-outline" />
        ))}
        {/* selection outline */}
        {selection?.uuid && <OutlineRect index={index} uuid={selection.uuid} toSvg={toSvg} className="sel-outline" />}
        {/* measure line */}
        {measure && <MeasureLine measure={measure} toSvg={toSvg} />}
        {measureA && !measure && <Marker point={toSvg(measureA.x, measureA.y)} color="#7a4fd0" />}
        {measureB && !measure && <Marker point={toSvg(measureB.x, measureB.y)} color="#7a4fd0" />}
        {/* thread pins */}
        {threads.map((t) => (
          <ThreadPin key={`pin-${t.id}`} thread={t} pxPerMM={pxPerMM} toSvg={toSvg} selected={false} onClick={() => onOpenThread(t.number)} />
        ))}
      </g>
    </svg>
  );
}

function trackWidth(index: NonNullable<ReturnType<typeof getStore>["index"]>, uuid: string): number | null {
  const t = index.tracks.find((t) => t.uuid === uuid);
  return t ? t.width : null;
}

function OutlineRect({
  index,
  uuid,
  toSvg,
  className,
}: {
  index: NonNullable<ReturnType<typeof getStore>["index"]>;
  uuid: string;
  toSvg: (x: number, y: number) => [number, number];
  className: string;
}) {
  const pad = index.pads.find((p) => p.uuid === uuid);
  let d: string | null = null;
  if (pad?.polygon) d = pad.polygon.map((p, i) => `${i === 0 ? "M" : "L"}${toSvg(p[0], p[1]).map(round).join(" ")}`).join(" ") + " Z";
  const fp = index.footprints.find((f) => f.uuid === uuid);
  if (!d && fp) {
    const [x, y, w, h] = fp.bbox;
    const a = toSvg(x, y);
    const b = toSvg(x + w, y + h);
    d = `M${round(Math.min(a[0], b[0]))} ${round(Math.min(a[1], b[1]))} L${round(Math.max(a[0], b[0]))} ${round(Math.min(a[1], b[1]))} L${round(Math.max(a[0], b[0]))} ${round(Math.max(a[1], b[1]))} L${round(Math.min(a[0], b[0]))} ${round(Math.max(a[1], b[1]))} Z`;
  }
  const t = index.tracks.find((t) => t.uuid === uuid);
  if (!d && t) {
    const a = toSvg(t.x1, t.y1);
    const b = toSvg(t.x2, t.y2);
    d = `M${round(a[0])} ${round(a[1])} L${round(b[0])} ${round(b[1])}`;
  }
  const v = index.vias.find((v) => v.uuid === uuid);
  if (!d && v) {
    const c = toSvg(v.x, v.y);
    d = `M${round(c[0] - v.size / 2 - 0.2)} ${round(c[1])} A${round(v.size / 2 + 0.2)} ${round(v.size / 2 + 0.2)} 0 1 0 ${round(c[0] + v.size / 2 + 0.2)} ${round(c[1])} A${round(v.size / 2 + 0.2)} ${round(v.size / 2 + 0.2)} 0 1 0 ${round(c[0] - v.size / 2 - 0.2)} ${round(c[1])} Z`;
  }
  const z = index.zones.find((z) => z.uuid === uuid);
  if (!d && z) d = z.outline.map((p, i) => `${i === 0 ? "M" : "L"}${toSvg(p[0], p[1]).map(round).join(" ")}`).join(" ") + " Z";
  if (!d) return null;
  return <path d={d} className={className} style={{ strokeWidth: t ? t.width + 0.15 : undefined }} pointerEvents="none" />;
}

function round(v: number): number {
  return Math.round(v * 1000) / 1000;
}

function DiffOverlay({ diff, toSvg }: { diff: NonNullable<ReturnType<typeof getStore>["diff"]>; toSvg: (x: number, y: number) => [number, number] }) {
  const outline = (e: DiffEntity, color: string, key: string) => {
    if (e.bbox) {
      const [x, y, w, h] = e.bbox;
      const a = toSvg(x, y);
      const b = toSvg(x + w, y + h);
      return <rect key={key} x={Math.min(a[0], b[0])} y={Math.min(a[1], b[1])} width={Math.abs(b[0] - a[0])} height={Math.abs(b[1] - a[1])} className={`diff-${color}`} pointerEvents="none" />;
    }
    if (e.x !== undefined && e.y !== undefined) {
      const [cx, cy] = toSvg(e.x, e.y);
      return <circle key={key} cx={cx} cy={cy} r={0.6} className={`diff-${color}`} pointerEvents="none" />;
    }
    if (e.x1 !== undefined) {
      const a = toSvg(e.x1, e.y1!);
      const b = toSvg(e.x2!, e.y2!);
      return <line key={key} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={`diff-${color}`} pointerEvents="none" />;
    }
    return null;
  };
  return (
    <g data-testid="diff-overlay">
      {diff.added.map((e, i) => outline(e, "added", `add-${i}`))}
      {diff.removed.map((e, i) => outline(e, "removed", `rem-${i}`))}
      {diff.moved.map((e, i) => {
        const a = toSvg(e.from[0], e.from[1]);
        const b = toSvg(e.to[0], e.to[1]);
        return (
          <g key={`mv-${i}`}>
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="diff-moved" pointerEvents="none" />
            <circle cx={b[0]} cy={b[1]} r={0.45} className="diff-moved" pointerEvents="none" />
          </g>
        );
      })}
    </g>
  );
}

function MeasureLine({ measure, toSvg }: { measure: NonNullable<ReturnType<typeof getStore>["measure"]>; toSvg: (x: number, y: number) => [number, number] }) {
  const a = toSvg(measure.a_point[0], measure.a_point[1]);
  const b = toSvg(measure.b_point[0], measure.b_point[1]);
  return (
    <g data-testid="measure-line">
      <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="measure-line" pointerEvents="none" />
      <circle cx={a[0]} cy={a[1]} r={0.3} className="measure-dot" pointerEvents="none" />
      <circle cx={b[0]} cy={b[1]} r={0.3} className="measure-dot" pointerEvents="none" />
      <text x={(a[0] + b[0]) / 2} y={(a[1] + b[1]) / 2 - 0.5} className="measure-text" pointerEvents="none">
        {measure.distance.toFixed(3)} mm
      </text>
    </g>
  );
}

function Marker({ point, color }: { point: [number, number]; color: string }) {
  return <circle cx={point[0]} cy={point[1]} r={0.5} fill="none" stroke={color} strokeWidth={0.25} pointerEvents="none" />;
}

function ThreadPin({ thread, pxPerMM, toSvg, selected, onClick }: {
  thread: Thread;
  pxPerMM: number;
  toSvg: (x: number, y: number) => [number, number];
  selected: boolean;
  onClick: () => void;
}) {
  const ra = thread.resolved_anchor;
  if (!ra) return null;
  const [cx, cy] = toSvg(ra.x, ra.y);
  const done = ["resolved", "wontfix", "addressed"].includes(thread.status);
  const color = SEVERITY_COLOR[thread.severity] ?? "#8a8f98";
  // Screen-constant size: the stage is scaled by pxPerMM, so divide the pixel sizes by it.
  const k = Math.max(pxPerMM, 0.001);
  const r = 10.5 / k;
  const stem = 9 / k;
  const stroke = 1.6 / k;
  const font = 11 / k;
  const label = `Comment #${thread.number} · ${thread.severity.replace("_", " ")} · ${thread.status}`;
  return (
    <g
      className="thread-pin"
      data-testid={`pin-${thread.number}`}
      data-severity={thread.severity}
      data-status={thread.status}
      transform={`translate(${cx}, ${cy})`}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <title>{label}</title>
      {/* leader from the bubble down to the exact anchor point */}
      <line x1={0} y1={-stem} x2={0} y2={0} stroke={color} strokeWidth={stroke * 1.4} opacity={0.9} />
      <circle cx={0} cy={0} r={stroke * 1.6} fill={color} stroke="#ffffff" strokeWidth={stroke * 0.7} />
      {/* speech bubble */}
      <g className="pin-bubble" transform={`translate(0, ${-stem - r})`}>
        <circle r={r} fill={color} stroke="#ffffff" strokeWidth={stroke} opacity={done ? 0.55 : 0.97} />
        <text
          y={font * 0.36}
          textAnchor="middle"
          fontSize={font}
          fill="#ffffff"
          fontWeight={700}
          pointerEvents="none"
          opacity={done ? 0.85 : 1}
        >
          {thread.number}
        </text>
        {selected && <circle r={r * 1.35} fill="none" stroke="#ffffff" strokeWidth={stroke * 1.5} opacity={0.9} />}
      </g>
      {/* generous hit target, centred on the bubble */}
      <circle cy={-stem - r} r={r * 1.5} fill="transparent" />
    </g>
  );
}

/** Identity popover for the current selection. */
function PickPopover() {
  const h = useHarness();
  const sel = h.selection;
  if (!sel) return null;
  const label =
    sel.kind === "point"
      ? `Point (${sel.x.toFixed(2)}, ${sel.y.toFixed(2)})`
      : [sel.kind, sel.ref ? sel.ref + (sel.pad ? `.${sel.pad}` : "") : sel.net ? `net:${sel.net}` : "", sel.net && sel.ref ? sel.net : "", sel.layer ?? ""].filter(Boolean).join(" · ");
  return (
    <div className="pick-popover" data-testid="pick-popover">
      <div className="pick-title">{label}</div>
      <div className="pick-sub">
        at ({sel.x.toFixed(2)}, {sel.y.toFixed(2)}) · {sel.side}
      </div>
      {h.mode === "comment" && <div className="pick-hint">Open the Review tab to write the comment.</div>}
    </div>
  );
}

/**
 * KiCad-style layer visibility panel: one row per plot layer of the current side, with its
 * plotted colour, so layers can be shown together or hidden one at a time instead of only
 * choosing a whole side.
 */
function LayerPanel({
  layers,
  hidden,
  onToggle,
  onShowAll,
}: {
  layers: typeof BOARD_LAYERS.front;
  hidden: string[];
  onToggle: (layer: string) => void;
  onShowAll: () => void;
}) {
  const hiddenCount = hidden.length;
  return (
    <div className="layer-panel" data-testid="layer-panel">
      <div className="layer-panel-head">
        <span>Layers</span>
        <button className="link" onClick={onShowAll} disabled={hiddenCount === 0} data-testid="layers-show-all">
          show all
        </button>
      </div>
      {layers.map((layer) => {
        const on = !hidden.includes(layer.id);
        return (
          <label
            key={layer.id}
            className={`layer-row ${on ? "" : "off"}`}
            data-testid={`layer-row-${layer.id}`}
            title={`${on ? "Hide" : "Show"} ${layer.label}`}
          >
            <input
              type="checkbox"
              checked={on}
              onChange={() => onToggle(layer.id)}
              data-testid={`layer-toggle-${layer.id}`}
            />
            <span className="layer-swatch" style={{ background: layer.color }} aria-hidden="true" />
            <span className="layer-name">{layer.label}</span>
          </label>
        );
      })}
    </div>
  );
}

function ScaleLegend({ scale, gridStep, barMM }: { scale: number; gridStep: number; barMM: number }) {
  return (
    <div className="scale-legend" data-testid="scale-legend">
      <div className="legend-bar" style={{ width: Math.round(barMM * scale) }} />
      <div className="legend-row">
        <b data-testid="legend-scale">{barMM} mm</b>
        <span className="muted" data-testid="legend-grid">grid {gridStep} mm</span>
      </div>
    </div>
  );
}

function MeasureBox() {
  const h = useHarness();
  if (h.mode !== "measure") return null;
  return (
    <div className="measure-box" data-testid="measure-box">
      {h.measure ? (
        <>
          <b>{h.measure.distance.toFixed(4)} mm</b> · nearest points ({h.measure.a_point.map((v) => v.toFixed(2)).join(", ")}) → ({h.measure.b_point.map((v) => v.toFixed(2)).join(", ")})
        </>
      ) : h.measureA ? (
        "Now click the second item to measure."
      ) : (
        "Click two items (pads, tracks, vias) to measure the copper distance."
      )}
    </div>
  );
}

/** 3D preset buttons: top / bottom / iso (rotate -45,0,45) / iso-back. */
