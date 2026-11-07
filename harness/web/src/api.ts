/** Thin fetch wrappers over the harness REST API. Everything is relative so Vite proxies /api. */

import type {
  BomBasicReport,
  BomResponse,
  BranchInfo,
  DesignDetail,
  DesignInfo,
  DiffResult,
  Goal,
  GoalEval,
  MeasureResult,
  ModelsResponse,
  Phase,
  PickIndex,
  RunEvent,
  RunInfo,
  SchematicInfo,
  SteerMessage,
  Thread,
  UsageInfo,
  VersionInfo,
  VersionSummary,
} from "./types.ts";

/** A transient failure (backend restarting, proxied socket dropped) is worth retrying. */
async function fetchWithRetry(path: string, init?: RequestInit, attempts = 3): Promise<Response> {
  let lastError: unknown = null;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const res = await fetch(path, init);
      // 502/503/504 are what a restarting or briefly unresponsive backend produces.
      if (res.ok || (res.status !== 502 && res.status !== 503 && res.status !== 504)) return res;
      lastError = new Error(`${res.status} ${res.statusText}`);
    } catch (err) {
      lastError = err;
    }
    if (attempt < attempts - 1) await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithRetry(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  return res.json() as Promise<T>;
}

async function send<T>(path: string, method: string, body: unknown): Promise<T> {
  const res = await fetchWithRetry(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${await res.text().catch(() => "")}`.slice(0, 300));
  return res.json() as Promise<T>;
}

export const api = {
  designs: () => get<DesignInfo[]>("/api/designs"),
  createDesign: (body: {
    name: string;
    board_text?: string;
    from_fixture?: boolean;
    goal?: Goal;
    prompt?: string;
    autostart?: boolean;
  }) =>
    send<{ name: string; branch: string; head: string; phase?: Phase; brief?: string | null; autostart?: boolean }>(
      "/api/designs",
      "POST",
      body,
    ),
  schematic: (d: string) => get<SchematicInfo>(`/api/designs/${d}/schematic`),
  bom: (d: string) => get<BomResponse>(`/api/designs/${d}/bom`),
  bomBasic: (d: string) => get<BomBasicReport>(`/api/designs/${d}/bom/basic`),
  usage: (d: string) => get<UsageInfo>(`/api/designs/${d}/usage`),
  schematicSvgUrl: (d: string, rev = "working", sheet = 0, version?: string | null) =>
    `/api/designs/${d}/schematic.svg?rev=${encodeURIComponent(rev)}&sheet=${sheet}` +
    (version ? `&v=${encodeURIComponent(version)}` : ""),
  models: () => get<ModelsResponse>("/api/models"),
  stopRun: (d: string) => send<{ stopping: boolean }>(`/api/designs/${d}/stop`, "POST", {}),
  setModel: (model: string) => send<{ current: string }>("/api/models", "PUT", { model }),
  versionSummary: (d: string, sha: string) => get<VersionSummary>(`/api/designs/${d}/versions/${sha}/summary`),
  design: (d: string) => get<DesignDetail>(`/api/designs/${d}`),
  index: (d: string, rev: string) => get<PickIndex>(`/api/designs/${d}/rev/${rev}/index`),
  svg: async (
    d: string,
    rev: string,
    side: "front" | "back",
    cacheBust?: string | null,
    layers?: string[] | null,
    mirror?: boolean | null,
  ): Promise<string> => {
    const params = new URLSearchParams();
    if (cacheBust) params.set("v", cacheBust);
    if (layers && layers.length) params.set("layers", layers.join(","));
    if (mirror !== undefined && mirror !== null) params.set("mirror", mirror ? "1" : "0");
    const q = params.size ? `?${params.toString()}` : "";
    const res = await fetch(`/api/designs/${d}/rev/${rev}/svg/${side}${q}`);
    if (!res.ok) throw new Error(`svg: ${res.status}`);
    return res.text();
  },
  drc: (d: string, rev: string) => get(`/api/designs/${d}/rev/${rev}/drc`),
  render3dUrl: (d: string, rev: string, params: string) => `/api/designs/${d}/rev/${rev}/render3d?${params}`,
  measure: (d: string, rev: string, a: string, b: string) =>
    get<MeasureResult>(`/api/designs/${d}/rev/${rev}/measure?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  diff: (d: string, a: string, b: string) =>
    get<DiffResult>(`/api/designs/${d}/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),

  versions: (d: string, branch?: string, limit = 200) =>
    get<VersionInfo[]>(`/api/designs/${d}/versions?limit=${limit}${branch ? `&branch=${branch}` : ""}`),
  restore: (d: string, sha: string) => send(`/api/designs/${d}/versions/${sha}/restore`, "POST", undefined),
  branches: (d: string) => get<BranchInfo[]>(`/api/designs/${d}/branches`),
  createBranch: (d: string, name: string, from_sha?: string) =>
    send(`/api/designs/${d}/branches`, "POST", { name, from_sha }),
  switchBranch: (d: string, name: string) => send(`/api/designs/${d}/branches/${name}/switch`, "POST", undefined),

  threads: (d: string, rev = "working") => get<Thread[]>(`/api/designs/${d}/threads?rev=${rev}`),
  createThread: (d: string, body: unknown) => send<Thread>(`/api/designs/${d}/threads`, "POST", body),
  reply: (threadId: number, body: string) => send<Thread>(`/api/threads/${threadId}/replies`, "POST", { body }),
  setStatus: (threadId: number, status: string, note?: string) =>
    send<Thread>(`/api/threads/${threadId}/status`, "POST", { status, note }),

  goal: (d: string) => get<Goal>(`/api/designs/${d}/goal`),
  putGoal: (d: string, goal: Goal) => send<Goal>(`/api/designs/${d}/goal`, "PUT", goal),
  goalStatus: (d: string, rev = "working") => get<GoalEval>(`/api/designs/${d}/goal/status?rev=${rev}`),

  steer: (d: string, message: string, runId?: string | null) =>
    send<SteerMessage>(`/api/designs/${d}/steer`, "POST", { message, run_id: runId }),
  steerList: (d: string, limit = 50) => get<SteerMessage[]>(`/api/designs/${d}/steer?limit=${limit}`),

  runs: (d: string) => get<RunInfo[]>(`/api/designs/${d}/runs`),
  runEvents: (runId: string, after = 0) => get<RunEvent[]>(`/api/runs/${runId}/events?after=${after}`),
};
