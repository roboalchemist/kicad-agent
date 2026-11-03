/** Shapes returned by the harness REST API (see DESIGN.md, "Server"). */

/** A design moves through phases: the schematic is drawn first, then the board. */
export type Phase = "schematic" | "board";

export interface DesignInfo {
  name: string;
  branch: string;
  head: string | null;
  dirty?: boolean;
  updated_at?: number | null;
  latest?: { sha: string; short: string; subject: string } | null;
  thumbnail?: string | null;
  goal_summary?: string | null;
  phase?: Phase;
  brief?: string | null;
}

/** One symbol on the schematic. */
export interface SchematicSymbol {
  reference: string;
  value: string;
  lib_id: string;
  x: number;
  y: number;
  rotation: number;
  footprint: string;
  pins: string[];
}

/** The schematic as the API describes it. */
export interface SchematicInfo {
  phase: Phase;
  brief: string | null;
  paper: string;
  sheet: [number, number];
  symbols: SchematicSymbol[];
  wires: number;
  labels: number;
  global_labels: number;
  nets: Record<string, { ref: string; pin: string; name: string }[]>;
  unconnected_pins: { ref: string; pin: string }[];
  erc: { counts: { errors?: number | null; warnings?: number | null }; violations?: unknown[]; error?: string };
}


/** One line of a project's bill of materials: a distinct part and every reference using it. */
export interface BomLine {
  refs: string[];
  qty: number;
  value: string;
  footprint: string;
  lcsc: string;
  url?: string;
  line_total?: number | null;
  description?: string;
  mpn?: string;
  tier?: "basic" | "extended" | null;
  stock?: number | null;
  unit_price?: number | null;
}

/** Basic-vs-extended coverage: extended parts carry a per-part JLCPCB reel fee. */
export interface BomTierSummary {
  parts: number;
  basic_parts: number;
  extended_parts: number;
  basic_units: number;
  units: number;
}

export interface BomResponse {
  phase: Phase;
  source: "schematic" | "board" | "none";
  lines: BomLine[];
  line_count: number;
  total_qty: number;
  total_price: number;
  unpriced_qty: number;
  tier_summary?: BomTierSummary;
}

/** One line of the basic-tier audit: is there a basic part for this value, and if not, why not. */
export interface BomBasicLine {
  value: string;
  footprint: string;
  refs: string[];
  qty: number;
  lcsc: string;
  mpn: string;
  package: string;
  category: string;
  tier?: "basic" | "extended" | null;
  basic: boolean;
  basic_alternative: BomLine | null;
  reason: string;
}

export interface BomBasicReport {
  lines: BomBasicLine[];
  line_count: number;
  basic_lines: number;
  extended_lines: number;
  basic_units: number;
  total_units: number;
  replaceable: BomBasicLine[];
  extended_parts: { value: string; lcsc: string; mpn: string; category: string; reason: string }[];
}

/** Token accounting for a project's current thread (see /api/designs/{d}/usage). */
export interface UsageInfo {
  model: string;
  context_window: number | null;
  max_output_tokens: number;
  context_estimate: number | null;
  percent: number | null;
  last_run: {
    run_id?: string | null;
    status?: string;
    input_tokens?: number | null;
    output_tokens?: number | null;
    total_tokens?: number | null;
    requests?: number | null;
  };
}

/** The project list returns a short goal summary string. */
export interface DesignListItem extends DesignInfo {
  goal_summary?: string | null;
}

export interface ModelInfo {
  id: string;
  label: string;
  provider: string;
  current: boolean;
}

export interface ModelsResponse {
  current: string;
  models: ModelInfo[];
  busy: boolean;
}

/** Diff of one version against its parent, plus the cached LLM note. */
export interface VersionSummary {
  sha: string;
  subject: string;
  parent: string | null;
  diff: DiffResult;
  counts: { added: number; removed: number; moved: number; net_changed: number; silkscreen: number; outline: number };
  summary: string | null;
  cached: boolean;
  error: string | null;
}

export interface DesignDetail extends DesignInfo {
  dirty: boolean;
  has_board?: boolean;
  has_schematic?: boolean;
  goal: Goal;
  board_hash: string | null;
  phase?: Phase;
  brief?: string | null;
  schematic_hash?: string | null;
  summary: {
    drc: { errors?: number; warnings?: number; unconnected?: number };
    size: [number, number];
    components: number;
  };
}

export interface BoardBox {
  bbox: [number, number, number, number];
  edge_bbox: [number, number, number, number];
  layers: string[];
  thickness: number;
}

export interface FootprintEntity {
  uuid: string;
  ref: string;
  value?: string;
  footprint?: string;
  x: number;
  y: number;
  rotation: number;
  side: "front" | "back";
  bbox: [number, number, number, number];
  courtyard: [number, number][] | null;
  ref_text: {
    x: number;
    y: number;
    bbox: [number, number, number, number];
    visible: boolean;
    layer: string;
    height: number;
  };
}

export interface PadEntity {
  uuid: string;
  ref: string;
  pad: string;
  net: string;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number;
  shape: string;
  layers: string[];
  through: boolean;
  drill: number;
  polygon: [number, number][];
}

export interface TrackEntity {
  uuid: string;
  net: string;
  layer: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  width: number;
}

export interface ViaEntity {
  uuid: string;
  net: string;
  x: number;
  y: number;
  size: number;
  drill: number;
}

export interface ZoneEntity {
  uuid: string;
  net: string;
  layer: string;
  outline: [number, number][];
  filled: [number, number][][];
}

export interface PickIndex {
  board: BoardBox;
  nets: string[];
  footprints: FootprintEntity[];
  pads: PadEntity[];
  tracks: TrackEntity[];
  vias: ViaEntity[];
  zones: ZoneEntity[];
  board_hash: string;
}

export type ThreadSeverity = "info" | "suggestion" | "warning" | "must_fix";
export type ThreadStatus = "open" | "addressed" | "resolved" | "wontfix" | "reopened";

export interface Anchor {
  kind: "footprint" | "pad" | "track" | "via" | "zone" | "point" | "region";
  uuid: string | null;
  ref: string | null;
  pad: string | null;
  net: string | null;
  layer: string | null;
  side: "front" | "back";
  x: number;
  y: number;
  entity_x: number | null;
  entity_y: number | null;
  region: [number, number, number, number] | null;
}

/** resolve_anchor result: a slim dict, not a full anchor (see store/review.py). */
export interface ResolvedAnchor {
  state: "exact" | "moved" | "remapped" | "detached";
  x: number;
  y: number;
  uuid: string | null;
  layer: string | null;
  detail?: string;
}

export interface ThreadReply {
  id: number;
  author: string;
  body: string;
  created_at: number;
  sha: string | null;
}

export interface Thread {
  id: number;
  design: string;
  number: number;
  kind: string;
  source: string;
  severity: ThreadSeverity;
  tags: string[];
  status: ThreadStatus;
  title: string | null;
  body: string;
  author: string;
  created_at: number;
  created_sha: string;
  anchor: Anchor;
  view: { side: string; center: [number, number]; view_mm: number } | null;
  addressed_sha: string | null;
  resolved_sha: string | null;
  replies: ThreadReply[];
  status_log: { status: ThreadStatus; by: string; at: number; sha: string | null; note?: string }[];
  resolved_anchor: ResolvedAnchor | null;
  updated_at: number;
}

export interface VersionInfo {
  phase?: Phase;
  sha: string;
  short: string;
  message: string;
  subject: string;
  author: "agent" | "user";
  time: number;
  parents: string[];
  branch: string;
  trailers: Record<string, string>;
  board_hash?: string | null;
  metrics?: { errors: number; warnings: number; unconnected: number } | null;
  thumbnail?: string;
  parent?: string | null;
  summary?: string | null;
  diff?: DiffResult | null;
}

export interface BranchInfo {
  name: string;
  head: string | null;
  current: boolean;
}

export type CriterionType =
  | "drc_errors"
  | "drc_warnings"
  | "unconnected"
  | "board_size"
  | "edge_clearance"
  | "max_distance"
  | "min_distance"
  | "within_region"
  | "open_threads";

export interface GoalCriterion {
  id: string;
  type: CriterionType;
  max?: number;
  max_w?: number;
  max_h?: number;
  min_mm?: number;
  max_mm?: number;
  a?: string;
  b?: string;
  ref?: string;
  region?: [number, number, number, number];
  severities?: string[];
}

export interface Goal {
  summary: string;
  criteria: GoalCriterion[];
  budget?: { max_requests?: number; max_minutes?: number };
}

export interface CriterionStatus {
  id: string;
  type: string;
  passed: boolean | null;
  value: unknown;
  detail: string;
}

export interface GoalEval {
  met: boolean;
  criteria: CriterionStatus[];
}

export interface DiffEntity {
  kind: string;
  uuid: string;
  ref?: string;
  pad?: string;
  net?: string;
  layer?: string;
  x?: number;
  y?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  width?: number;
  size?: number;
  bbox?: [number, number, number, number];
}

export interface DiffResult {
  added: DiffEntity[];
  removed: DiffEntity[];
  moved: { kind: string; uuid: string; ref?: string; pad?: string; net?: string; from: number[]; to: number[] }[];
  net_changed: { kind: string; uuid: string; ref?: string; pad?: string; from: string | null; to: string | null }[];
  silkscreen?: { kind: string; uuid: string; ref?: string; from: number[]; to: number[] }[];
  outline?: { from: [number, number]; to: [number, number] } | null;
}

export interface MeasureResult {
  distance: number;
  a_point: [number, number];
  b_point: [number, number];
  a: unknown;
  b: unknown;
}

export interface RunInfo {
  run_id: string;
  design: string;
  thread_id: string;
  model: string;
  prompt: string;
  source: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  updated_at: number;
  usage: unknown;
  error: string | null;
  event_count: number;
}

/** One row of GET /api/runs/{id}/events: `event` is the logged AG-UI event (camelCase). */
export interface RunEvent {
  seq: number;
  at: number;
  type: string;
  event: Record<string, unknown>;
}

export interface SteerMessage {
  id: number;
  design: string;
  message: string;
  author: string;
  run_id: string | null;
  delivered_run: string | null;
  created_at: number;
}

/** AG-UI shared state published by the agent (harness/kicad_harness/agent/state.py). */
export interface DesignState {
  design?: string | null;
  branch?: string | null;
  head?: string | null;
  revision?: number;
  board_hash?: string | null;
  goal?: GoalEval | null;
  last_render?: { url: string; caption: string; view: Record<string, unknown> } | null;
  highlight?: { uuids: string[]; refs: string[]; nets: string[] } | null;
  selection?: Record<string, unknown> | null;
  viewing?: string | null;
  loop?: boolean;
  supervised?: boolean;
}
