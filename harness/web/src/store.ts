/**
 * Central app store: a vanilla observable shared by React components (via useSyncExternalStore)
 * and by CopilotKit tool handlers, which need the freshest state outside the render loop.
 */

import { useSyncExternalStore } from "react";

import { api } from "./api.ts";
import type {
  DesignDetail,
  DesignInfo,
  DiffResult,
  Goal,
  GoalEval,
  MeasureResult,
  PickIndex,
  RunInfo,
  Thread,
  VersionInfo,
} from "./types.ts";

export type Side = "front" | "back" | "3d";
export type ViewerMode = "select" | "comment" | "measure";

/** A plot layer of one board side: what the right-hand layer panel toggles. */
export interface BoardLayer {
  id: string;
  label: string;
  /** Colour kicad-cli plots it in (KiCad's default theme), shown as the panel's swatch. */
  color: string;
}

/** The layers of each side, bottom to top, in the order they are stacked in the viewer. */
export const BOARD_LAYERS: Record<"front" | "back", BoardLayer[]> = {
  front: [
    { id: "F.Cu", label: "F.Cu", color: "#C83434" },
    { id: "F.Mask", label: "F.Mask", color: "#D864FF" },
    { id: "F.SilkS", label: "F.Silkscreen", color: "#F2EDA1" },
    { id: "Edge.Cuts", label: "Edge.Cuts", color: "#D0D2CD" },
  ],
  back: [
    { id: "B.Cu", label: "B.Cu", color: "#4D7FC4" },
    { id: "B.Mask", label: "B.Mask", color: "#02FFEE" },
    { id: "B.SilkS", label: "B.Silkscreen", color: "#E8B2A7" },
    { id: "Edge.Cuts", label: "Edge.Cuts", color: "#D0D2CD" },
  ],
};

/** The layer panel's groups: every layer of *both* sides, with the shared board outline once.
 *
 * KiCad lists all layers in one panel and draws them all at once, so every row is meaningful
 * whatever way the board is flipped; the viewing side's group is marked as the near one.
 */
export const LAYER_PANEL_GROUPS: { title: string; side: "front" | "back" | null; layers: BoardLayer[] }[] = [
  { title: "Front", side: "front", layers: BOARD_LAYERS.front.filter((l) => l.id !== "Edge.Cuts") },
  { title: "Back", side: "back", layers: BOARD_LAYERS.back.filter((l) => l.id !== "Edge.Cuts") },
  { title: "Board", side: null, layers: BOARD_LAYERS.front.filter((l) => l.id === "Edge.Cuts") },
];

/** Every plotted layer, bottom to top, as KiCad stacks them in one composite view.
 *
 * Back layers are plotted unmirrored (at board coordinates), so the whole board lines up: the
 * front view sees through to the back through any gaps, and flipping mirrors the finished stack.
 */
export const BOARD_STACK: { id: string; side: "front" | "back" }[] = [
  { id: "B.Cu", side: "back" },
  { id: "B.Mask", side: "back" },
  { id: "B.SilkS", side: "back" },
  { id: "F.Cu", side: "front" },
  { id: "F.Mask", side: "front" },
  { id: "F.SilkS", side: "front" },
  { id: "Edge.Cuts", side: "front" },
];


/** The entity the user picked in the viewer (an anchor in the making). */
export interface Pick {
  kind: "footprint" | "pad" | "track" | "via" | "zone" | "point";
  uuid: string | null;
  ref: string | null;
  pad: string | null;
  net: string | null;
  layer: string | null;
  side: "front" | "back";
  x: number; // click point, board mm
  y: number;
  entity_x: number | null;
  entity_y: number | null;
}

export interface ViewState {
  /** Centre in current-side SVG mm, and the visible height in mm. */
  cx: number;
  cy: number;
  mm: number;
}

export interface FocusRequest {
  center: [number, number] | null; // board mm
  viewMM: number | null;
  nonce: number;
}

export interface HarnessData {
  designs: DesignInfo[];
  designName: string | null;
  design: DesignDetail | null;
  rev: string; // "working" or a sha
  side: Side;
  index: PickIndex | null;
  svg: string | null; // SVG text for (rev, side); the Edge.Cuts layer when per-layer stacks load
  /** One SVG per plot layer of the current side, so the layer panel can toggle without a re-render. */
  layerSvgs: Record<string, string>;
  /** Layers the user has hidden in the right-hand layer panel (persisted). */
  hiddenLayers: string[];
  svgSide: "front" | "back" | null;
  svgKey: number; // bump to force reload (cache-bust)
  threads: Thread[];
  versions: VersionInfo[];
  branches: { name: string; head: string | null; current: boolean }[];
  goal: Goal | null;
  goalStatus: GoalEval | null;
  /** Latest goal evaluation the agent pushed into AG-UI state (state.goal). */
  agentGoal: GoalEval | null;
  runs: RunInfo[];
  diff: DiffResult | null;
  diffVs: string | null;
  showGrid: boolean;
  /** Show review-comment pins on the board. */
  showPins: boolean;
  /** Measured viewer viewport in CSS px, so fitView works before/after layout changes. */
  boardPx: { w: number; h: number };
  mode: ViewerMode;
  selection: Pick | null;
  composerPick: Pick | null;
  measureA: Pick | null;
  measureB: Pick | null;
  measure: MeasureResult | null;
  highlight: { uuids: string[]; refs: string[]; nets: string[] } | null;
  focus: FocusRequest | null;
  compare: { a: string; b: string } | null; // requested by the compare_versions tool
  showVersion: string | null; // requested by the show_version tool
  view: ViewState | null;
  selectedThread: number | null;
  viewMMRequest: number | null;
  loopEnabled: boolean;
  supervised: boolean;
  runActiveSince: number | null;
  sse: "connected" | "error" | null;
  steerDeliveredAt: number | null;
  loading: boolean;
  error: string | null;
}

const initial: HarnessData = {
  designs: [],
  designName: null,
  design: null,
  rev: "working",
  side: "front",
  index: null,
  svg: null,
  layerSvgs: {},
  hiddenLayers: hiddenLayersFromStorage(),
  svgSide: null,
  svgKey: 0,
  threads: [],
  versions: [],
  branches: [],
  goal: null,
  goalStatus: null,
  agentGoal: null,
  runs: [],
  diff: null,
  diffVs: null,
  showGrid: gridFromStorage(),
  showPins: pinsFromStorage(),
  boardPx: { w: 0, h: 0 },
  mode: "select",
  selection: null,
  composerPick: null,
  measureA: null,
  measureB: null,
  measure: null,
  highlight: null,
  focus: null,
  compare: null,
  showVersion: null,
  view: null,
  selectedThread: null,
  viewMMRequest: null,
  loopEnabled: false,
  supervised: false,
  runActiveSince: null,
  sse: null,
  steerDeliveredAt: null,
  loading: false,
  error: null,
};

type Listener = () => void;
let data: HarnessData = initial;
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l();
}

export function getStore(): HarnessData {
  return data;
}

export function setStore(patch: Partial<HarnessData>) {
  data = { ...data, ...patch };
  emit();
}

export function useHarness(): HarnessData {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => data,
  );
}

// --------------------------------------------------------------------------- actions

function catchErr(fn: () => Promise<void>): Promise<void> {
  return fn().catch((e) => {
    setStore({ error: String(e instanceof Error ? e.message : e) });
  });
}

export async function initStore(designName?: string | null) {
  await catchErr(async () => {
    const designs = await api.designs();
    const name = designName ?? new URLSearchParams(window.location.search).get("design") ?? designs[0]?.name ?? null;
    setStore({ designs });
    if (name) await selectDesign(name);
    else setStore({ loading: false });
  });
}

export async function selectDesign(name: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("design", name);
  window.history.replaceState(null, "", url);
  setStore({ designName: name, rev: "working", loading: true, diff: null, diffVs: null, selection: null, measure: null, measureA: null, measureB: null, composerPick: null, selectedThread: null });
  await refreshDesignData(true);
}

/** Loads everything that depends on the design + current rev/side. */
export async function refreshDesignData(full = false) {
  const { designName: name, rev, side } = getStore();
  if (!name) return;
  await catchErr(async () => {
    const design = await api.design(name);
    setStore({ design, goal: design.goal });
    const [index, threads, versions, branches, runs] = await Promise.all([
      api.index(name, rev),
      api.threads(name, rev),
      api.versions(name),
      api.branches(name),
      api.runs(name),
    ]);
    let goalStatus = getStore().goalStatus;
    try {
      goalStatus = await api.goalStatus(name, rev);
    } catch {
      // keep old
    }
    setStore({ index, threads, versions, branches, runs, goalStatus, loading: false });
    if (side !== "3d") await loadSvg();
    if (full) fitView();
    // Keep the AG-UI agent state (head/branch/revision) in sync for the next run.
    syncAgentDesign();
  });
}

export async function loadSvg() {
  const { designName: name, rev, side, svgKey, design } = getStore();
  if (!name || (side !== "front" && side !== "back")) return;
  // A schematic-phase design has no board to plot; the schematic viewer loads its own sheet.
  if (design?.phase === "schematic") return;
  await catchErr(async () => {
    const bust = svgKey ? `${svgKey}-${design?.board_hash ?? ""}` : design?.board_hash;
    // Both sides are fetched every time and stacked in the viewer, so switching the viewed side
    // is a pure flip and never a reload of different artwork.
    const results = await Promise.allSettled(
      BOARD_STACK.map((layer) => api.svg(name, rev, layer.side, bust, [layer.id], false)),
    );
    const layerSvgs: Record<string, string> = {};
    results.forEach((result, i) => {
      if (result.status === "fulfilled") layerSvgs[BOARD_STACK[i].id] = result.value;
    });
    if (!Object.keys(layerSvgs).length) throw new Error("no board layer could be plotted");
    setStore({
      // Edge.Cuts carries the page/viewBox every layer shares, so it is the dimension reference.
      svg: layerSvgs["Edge.Cuts"] ?? Object.values(layerSvgs)[0],
      layerSvgs,
      svgSide: side,
    });
  });
}

export async function setRev(rev: string) {
  setStore({ rev, diff: null, diffVs: null, selection: null, measure: null, measureA: null, measureB: null });
  await refreshDesignData(true);
  syncAgentViewing(rev);
}

export async function setSide(side: Side) {
  setStore({ side });
  if (side === "front" || side === "back") await loadSvg();
}

/** Reload the project list (after creating a project, or from the selector). */
export async function refreshDesigns() {
  await catchErr(async () => {
    setStore({ designs: await api.designs() });
  });
}

export async function refreshThreads() {
  const { designName: name, rev } = getStore();
  if (!name) return;
  await catchErr(async () => setStore({ threads: await api.threads(name, rev) }));
}

export async function refreshVersions() {
  const { designName: name } = getStore();
  if (!name) return;
  await catchErr(async () => setStore({ versions: await api.versions(name) }));
}

export async function refreshGoal() {
  const { designName: name, rev } = getStore();
  if (!name) return;
  await catchErr(async () => {
    const [goal, goalStatus] = await Promise.all([api.goal(name), api.goalStatus(name, rev)]);
    setStore({ goal, goalStatus });
  });
}

export async function refreshRuns() {
  const { designName: name } = getStore();
  if (!name) return;
  await catchErr(async () => setStore({ runs: await api.runs(name) }));
}

export async function saveGoal(goal: Goal) {
  const { designName: name } = getStore();
  if (!name) throw new Error("no design");
  const saved = await api.putGoal(name, goal);
  setStore({ goal: saved });
  await refreshGoal();
  return saved;
}

// --------------------------------------------------------------------------- viewer actions

export function setView(view: Partial<ViewState>) {
  const v = getStore().view ?? { cx: 0, cy: 0, mm: 30 };
  setStore({ view: { ...v, ...view } });
}

/** Fit the board edge outline into the viewport (the viewer supplies container size). */
/** Remember the viewer's measured size; {@link fitView} uses it when called without args. */
export function setBoardSize(w: number, h: number) {
  const cur = getStore().boardPx;
  if (cur.w !== w || cur.h !== h) setStore({ boardPx: { w, h } });
}

export function fitView(containerW = 0, containerH = 0) {
  const idx = getStore().index;
  if (!idx || !idx.board) return;
  const [, ex, ey, ew, eh] = [0, idx.board.edge_bbox[0], idx.board.edge_bbox[1], idx.board.edge_bbox[2], idx.board.edge_bbox[3]];
  const dims = svgDims();
  const w = dims?.w ?? ew;
  const h = dims?.h ?? eh;
  // Prefer the real measured viewport: the old window fractions fitted a slightly-too-large
  // board, which pushed the left/right Edge.Cuts out of the pane at fit zoom.
  const measured = getStore().boardPx;
  const cw = containerW || measured.w || window.innerWidth * 0.6;
  const chh = containerH || measured.h || window.innerHeight * 0.8;
  const mm = Math.max(h, (w * chh) / cw) * 1.04;
  setStore({ view: { cx: w / 2, cy: h / 2, mm } });
  void ey; // svg space already includes the edge offset
  void ex;
}

export function focusOnBoard(center: [number, number] | null, viewMM: number | null) {
  // Convert board mm to the current side's svg mm.
  const idx = getStore().index;
  if (!idx || !center) {
    setStore({ focus: { center, viewMM, nonce: Date.now() } });
    return;
  }
  const p = boardToSvg(center[0], center[1]);
  setStore({ focus: { center, viewMM, nonce: Date.now() }, view: { cx: p[0], cy: p[1], mm: viewMM ?? getStore().view?.mm ?? 30 } });
}

/** board mm -> svg mm for the current side (SVG origin = top-left of edge_bbox; back mirrors x). */
export function boardToSvg(x: number, y: number): [number, number] {
  const idx = getStore().index;
  const [ex, ey, ew] = idx ? idx.board.edge_bbox : [0, 0, 0];
  if (getStore().side === "back") return [ex + ew - x, y - ey];
  return [x - ex, y - ey];
}

export function svgToBoard(sx: number, sy: number): [number, number] {
  const idx = getStore().index;
  const [ex, ey, ew] = idx ? idx.board.edge_bbox : [0, 0, 0];
  if (getStore().side === "back") return [ex + ew - sx, sy + ey];
  return [sx + ex, sy + ey];
}

/** Intrinsic dimensions of the loaded SVG (from its viewBox), in mm. */
export function svgDims(): { w: number; h: number } | null {
  const { svg } = getStore();
  if (!svg) return null;
  const m = /viewBox="([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"/.exec(svg);
  if (!m) return null;
  return { w: parseFloat(m[3]), h: parseFloat(m[4]) };
}

// --------------------------------------------------------------------------- picking

export function setSelection(pick: Pick | null) {
  setStore({ selection: pick });
  syncAgentSelection(pick);
}

function gridFromStorage(): boolean {
  try {
    return localStorage.getItem("kicad-harness-grid") !== "0";
  } catch {
    return true;
  }
}

function pinsFromStorage(): boolean {
  try {
    return localStorage.getItem("kicad-harness-pins") !== "0";
  } catch {
    return true;
  }
}

function hiddenLayersFromStorage(): string[] {
  try {
    const raw = localStorage.getItem("kicad-harness-hidden-layers");
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((l): l is string => typeof l === "string") : [];
  } catch {
    return [];
  }
}

const knownLayers = new Set(Object.values(BOARD_LAYERS).flat().map((layer) => layer.id));

/** Show or hide one plot layer (KiCad-style layer visibility). Persisted across reloads. */
export function toggleLayer(layer: string): void {
  if (!knownLayers.has(layer)) return;
  const hidden = getStore().hiddenLayers;
  const next = hidden.includes(layer) ? hidden.filter((l) => l !== layer) : [...hidden, layer];
  setStore({ hiddenLayers: next });
  try {
    localStorage.setItem("kicad-harness-hidden-layers", JSON.stringify(next));
  } catch {
    // storage disabled
  }
}

/** Turn every layer of the current side (and the other one) back on. */
export function showAllLayers(): void {
  setStore({ hiddenLayers: [] });
  try {
    localStorage.removeItem("kicad-harness-hidden-layers");
  } catch {
    // storage disabled
  }
}

/** Show/hide review-comment pins (persisted). */
export function togglePins(): void {
  const next = !getStore().showPins;
  setStore({ showPins: next });
  try {
    localStorage.setItem("kicad-harness-pins", next ? "1" : "0");
  } catch {
    // storage disabled
  }
}

/** Show/hide the KiCad-style dot grid (persisted). */
export function toggleGrid(): void {
  const next = !getStore().showGrid;
  setStore({ showGrid: next });
  try {
    localStorage.setItem("kicad-harness-grid", next ? "1" : "0");
  } catch {
    // storage disabled
  }
}

export function setMode(mode: ViewerMode) {
  setStore({ mode, measureB: null, measure: null, ...(mode === "measure" ? { measureA: null } : {}) });
}

export function pickedRef(p: Pick | null): string | null {
  if (!p) return null;
  if (p.pad && p.ref) return `${p.ref}.${p.pad}`;
  if (p.ref) return p.ref;
  if (p.net) return `net:${p.net}`;
  if (p.uuid) return p.uuid;
  return null;
}

export async function handlePick(pick: Pick | null) {
  const { mode } = getStore();
  setSelection(pick);
  if (mode === "comment" && pick) {
    setStore({ composerPick: pick });
    return;
  }
  if (mode === "measure" && pick) {
    const { measureA, designName: name, rev } = getStore();
    if (!measureA) {
      setStore({ measureA: pick, measure: null, measureB: null });
    } else {
      const a = pickedRef(measureA);
      const b = pickedRef(pick);
      setStore({ measureB: pick });
      if (a && b && name) {
        try {
          const measure = await api.measure(name, rev, a, b);
          setStore({ measure });
        } catch (e) {
          setStore({ error: String(e) });
        }
      }
    }
  }
}

// --------------------------------------------------------------------------- AG-UI state sync

type AgentStatePatch = Record<string, unknown>;
let agentStateSink: ((patch: AgentStatePatch) => void) | null = null;

/** App.tsx registers this so the store can push design/selection into the AG-UI agent state. */
export function setAgentStateSink(fn: (patch: AgentStatePatch) => void) {
  agentStateSink = fn;
  syncAgentDesign();
  syncAgentSelection(getStore().selection);
}

function agentPatch(patch: AgentStatePatch) {
  try {
    agentStateSink?.(patch);
  } catch {
    // agent not ready yet; the next sync will catch up
  }
}

export function syncAgentDesign() {
  const { designName, design } = getStore();
  if (!designName) return;
  agentPatch({ design: designName, branch: design?.branch, head: design?.head });
}

export function syncAgentViewing(rev: string) {
  agentPatch({ viewing: rev === "working" ? null : rev });
}

export function syncAgentSelection(pick: Pick | null) {
  agentPatch({
    selection: pick
      ? {
          kind: pick.kind,
          uuid: pick.uuid,
          ref: pick.ref,
          pad: pick.pad,
          net: pick.net,
          layer: pick.layer,
          side: pick.side,
          x: round3(pick.x),
          y: round3(pick.y),
        }
      : null,
  });
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}
