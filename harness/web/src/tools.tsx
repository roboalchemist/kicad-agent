/**
 * CopilotKit wiring: frontend tools that drive the viewer, per-tool chat renderers, and the
 * approval card for supervised interrupts. See DESIGN.md, "Agent tab".
 */

import type { Interrupt, Message, ResumeEntry, ToolCall } from "@ag-ui/client";
import {
  useAgent,
  useCopilotKit,
  useDefaultRenderTool,
  useFrontendTool,
  useInterrupt,
  useRenderTool,
} from "@copilotkit/react-core/v2";
import { useCallback, useState } from "react";
import { z } from "zod";

import { AGENT_ID } from "./agent.ts";
import { api } from "./api.ts";
import { focusOnBoard, getStore, setStore, setRev } from "./store.ts";
import type { DesignState } from "./types.ts";

type ToolStatus = "inProgress" | "executing" | "complete";

const EDITING_TOOLS = new Set([
  "move_footprint",
  "rotate_footprint",
  "flip_footprint",
  "add_track",
  "add_via",
  "autoroute",
  "set_board_outline",
  "add_copper_zone",
  "refill_zones",
  "add_footprint",
  "delete_board_items",
  "restore_version",
]);

/** Registers every tool renderer, frontend tool and approval UI the harness uses. */
export function useHarnessTools() {
  useFrontendTool(
    {
      name: "focus_view",
      description:
        "Frontend tool: pan and zoom the user's board viewer to a target (U1, U1.3, net:GND, a UUID) and optionally set the visible size in mm.",
      parameters: z.object({
        target: z.string().describe("Reference designator, pad (U1.3), net (net:GND) or UUID"),
        view_mm: z.number().optional().describe("Visible area height in mm"),
      }),
      handler: async ({ target, view_mm }) => {
        const index = getStore().index;
        const center = resolveTargetCenter(index, target);
        focusOnBoard(center, view_mm ?? null);
        if (!getStore().highlight && center) highlightRefs(index, [target]);
        return { ok: true, focused: target, center, view_mm: view_mm ?? null };
      },
      agentId: AGENT_ID,
    },
    [],
  );

  useFrontendTool(
    {
      name: "highlight",
      description:
        "Frontend tool: draw a cyan outline on the user's board viewer around refs (U1, U1.3), nets (net:GND) and/or UUIDs. Pass empty lists to clear.",
      parameters: z.object({
        refs: z.array(z.string()).default([]),
        nets: z.array(z.string()).default([]),
        uuids: z.array(z.string()).default([]),
      }),
      handler: async ({ refs = [], nets = [], uuids = [] }) => {
        const index = getStore().index;
        highlightRefs(index, refs.length || nets.length ? [...refs, ...nets.map((n) => `net:${n}`)] : []);
        setStore({ highlight: { uuids: uuids ?? [], refs: refs ?? [], nets: nets ?? [] } });
        return { ok: true, highlighted: (refs?.length ?? 0) + (nets?.length ?? 0) + (uuids?.length ?? 0) };
      },
      agentId: AGENT_ID,
    },
    [],
  );

  useFrontendTool(
    {
      name: "show_version",
      description: "Frontend tool: show the board at an older version (git sha) in the user's viewer.",
      parameters: z.object({ sha: z.string().describe("Git commit sha (full or short prefix)") }),
      handler: async ({ sha }) => {
        setStore({ showVersion: sha });
        await setRev(sha);
        return { ok: true, viewing: sha };
      },
      agentId: AGENT_ID,
    },
    [],
  );

  useFrontendTool(
    {
      name: "compare_versions",
      description: "Frontend tool: overlay a diff of two versions (a = old, b = new) on the user's board viewer.",
      parameters: z.object({ a: z.string(), b: z.string() }),
      handler: async ({ a, b }) => {
        setStore({ compare: { a, b } });
        const name = getStore().designName;
        if (name) {
          try {
            const diff = await api.diff(name, a, b);
            // setRev clears overlay state, so navigate first and set the diff after
            await setRev(b);
            setStore({ diff, diffVs: `${a.slice(0, 8)}→${b.slice(0, 8)}` });
          } catch {
            // leave the banner state; the viewer shows what it can
          }
        }
        return { ok: true, comparing: [a, b] };
      },
      agentId: AGENT_ID,
    },
    [],
  );

  useRenderTool(
    {
      name: "render",
      parameters: z.object({}).passthrough(),
      render: ({ parameters, status, result }) => <RenderViewCard status={status} result={result} params={parameters} />,
      agentId: AGENT_ID,
    },
    [],
  );

  useRenderTool(
    {
      name: "check",
      parameters: z.object({}).passthrough(),
      render: ({ status, result }) => <CheckCard status={status} result={result} />,
      agentId: AGENT_ID,
    },
    [],
  );

  useRenderTool(
    {
      name: "list_reviews",
      parameters: z.object({}).passthrough(),
      render: ({ status, result }) => <FeedbackCard status={status} result={result} />,
      agentId: AGENT_ID,
    },
    [],
  );

  useRenderTool(
    {
      name: "get_design",
      parameters: z.object({}).passthrough(),
      render: ({ status, result }) => <SummaryCard status={status} result={result} />,
      agentId: AGENT_ID,
    },
    [],
  );

  useRenderTool(
    {
      name: "measure",
      parameters: z.object({}).passthrough(),
      render: ({ status, result }) => <MeasureCard status={status} result={result} />,
      agentId: AGENT_ID,
    },
    [],
  );

  useDefaultRenderTool({
    render: ({ name, parameters, status, result }) => {
      // Editing tools collapse to one line with a version link.
      if (EDITING_TOOLS.has(name)) return <EditCard name={name} parameters={parameters} status={status} result={result} />;
      return <ToolCard name={name} parameters={parameters} status={status} result={result} />;
    },
  });

  useInterrupt({
    agentId: AGENT_ID,
    render: ({ interrupts }) => <ApprovalCard interrupts={interrupts} />,
  });
}

/** Extract refs like U1, U1.3, net:GND from a target string and set the store highlight. */
function highlightRefs(index: ReturnType<typeof getStore>["index"], targets: string[]) {
  const refs: string[] = [];
  const nets: string[] = [];
  const uuids: string[] = [];
  for (const t of targets) {
    if (/^net:/i.test(t)) nets.push(t.replace(/^net:/i, ""));
    else if (index) {
      const u = resolveTargetUuids(index, t);
      if (u.length) uuids.push(...u);
      else refs.push(t);
    } else refs.push(t);
  }
  setStore({ highlight: { uuids, refs, nets } });
}

/** Resolve a target (ref, ref.pad, net, uuid) to uuids via the pick index. */
function resolveTargetUuids(index: NonNullable<ReturnType<typeof getStore>["index"]>, target: string): string[] {
  const t = target.trim().replace(/^net:/i, "");
  const out = new Set<string>();
  const upper = t.toUpperCase();
  for (const f of index.footprints) if (f.ref.toUpperCase() === upper) out.add(f.uuid);
  if (/^[^\s.]+\.\d+$/.test(t)) {
    const [ref, pad] = [t.slice(0, t.indexOf(".")), t.slice(t.indexOf(".") + 1)];
    for (const p of index.pads) if (p.ref.toUpperCase() === ref.toUpperCase() && p.pad === pad) out.add(p.uuid);
  }
  for (const p of index.pads) if (p.net && p.net.toUpperCase() === upper) out.add(p.uuid);
  for (const tr of index.tracks) if (tr.net && tr.net.toUpperCase() === upper) out.add(tr.uuid);
  for (const v of index.vias) if (v.net && v.net.toUpperCase() === upper) out.add(v.uuid);
  for (const z of index.zones) if (z.net && z.net.toUpperCase() === upper) out.add(z.uuid);
  return [...out];
}

/** Centre of a target for focus_view: footprints/pads/vias at their position, tracks at midpoint. */
function resolveTargetCenter(index: ReturnType<typeof getStore>["index"], target: string): [number, number] | null {
  if (!index) return null;
  const t = target.trim();
  let xs: number[] = [];
  let ys: number[] = [];
  const add = (x: number, y: number, half = 0) => {
    xs.push(x - half, x + half);
    ys.push(y - half, y + half);
  };
  if (/^net:/i.test(t)) {
    const net = t.replace(/^net:/i, "").toUpperCase();
    for (const p of index.pads) if (p.net && p.net.toUpperCase() === net) add(p.x, p.y, Math.max(p.w, p.h) / 2);
    for (const tr of index.tracks)
      if (tr.net && tr.net.toUpperCase() === net) add((tr.x1 + tr.x2) / 2, (tr.y1 + tr.y2) / 2);
    for (const v of index.vias) if (v.net && v.net.toUpperCase() === net) add(v.x, v.y, v.size / 2);
    for (const z of index.zones)
      if (z.net && z.net.toUpperCase() === net) {
        for (const [x, y] of z.outline) add(x, y);
      }
  } else if (/^[^\s.]+\.\d+$/.test(t)) {
    const dot = t.indexOf(".");
    const ref = t.slice(0, dot).toUpperCase();
    const pad = t.slice(dot + 1);
    for (const p of index.pads) if (p.ref.toUpperCase() === ref && p.pad === pad) add(p.x, p.y, Math.max(p.w, p.h) / 2);
  } else {
    const upper = t.toUpperCase();
    const fp = index.footprints.find((f) => f.ref.toUpperCase() === upper);
    if (fp) {
      const [x, y, w, h] = fp.bbox;
      add(x, y);
      add(x + w, y + h);
    } else {
      // maybe a uuid
      const pad = index.pads.find((p) => p.uuid === t);
      if (pad) add(pad.x, pad.y, Math.max(pad.w, pad.h) / 2);
      else {
        const via = index.vias.find((v) => v.uuid === t);
        if (via) add(via.x, via.y, via.size / 2);
      }
    }
  }
  if (!xs.length) return null;
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

// --------------------------------------------------------------------------- renderers

function statusLabel(status: ToolStatus): string {
  return { inProgress: "running", executing: "running", complete: "done" }[status];
}

/** render returns `["<meta json>", {data, media_type, kind:"binary"}]` as a JSON string.
 * Pydantic serializes `data` as URL-safe base64, which a `data:` URL rejects, so convert it. */
function parseRenderResult(result: string | undefined): { meta?: { url: string; caption: string }; image?: string } {
  if (!result) return {};
  try {
    const parsed: unknown = JSON.parse(result);
    const parts = Array.isArray(parsed) ? parsed : [parsed];
    const metaJson = parts.find((p): p is string => typeof p === "string");
    const meta = metaJson ? (JSON.parse(metaJson) as { url: string; caption: string }) : undefined;
    const binary = parts.find(
      (p): p is { data: string; media_type: string } =>
        typeof p === "object" && p !== null && typeof p.data === "string" && String(p.media_type).startsWith("image/"),
    );
    if (binary) {
      const base64 = binary.data.replace(/-/g, "+").replace(/_/g, "/");
      return { meta, image: `data:${binary.media_type};base64,${base64}` };
    }
    return { meta };
  } catch {
    return {};
  }
}

/** One-line summary + collapsible detail, collapsed by default.
 *
 * A goal loop makes hundreds of tool calls; rendering every argument and result expanded
 * buries the conversation. The header always shows what ran and whether it finished, and the
 * detail is one click away.
 */
function Card({
  name,
  status,
  children,
  testId,
  toolName,
  extra,
  className = "",
}: {
  name: string;
  status: ToolStatus;
  children?: React.ReactNode;
  testId?: string;
  toolName?: string;
  extra?: React.ReactNode;
  className?: string;
}) {
  return (
    <details className={`tool-card ${className}`} data-testid={testId} data-tool={toolName ?? name} data-status={status}>
      <summary className="tool-card-header">
        <span className="tool-name">{name}</span>
        <span className={`tool-status tool-status-${status}`}>{statusLabel(status)}</span>
        {extra}
      </summary>
      {children ? <div className="tool-card-body">{children}</div> : null}
    </details>
  );
}

function RenderViewCard({ status, result, params }: { status: ToolStatus; result?: string; params?: unknown }) {
  const { meta, image } = parseRenderResult(result);
  const target = (params as { target?: string } | undefined)?.target;
  return (
    <div className="tool-card render-card" data-testid="render-view-card" data-status={status}>
      <div className="tool-card-header">
        <span className="tool-name">render</span>
        {target && <span className="tool-args">{target}</span>}
        <span className={`tool-status tool-status-${status}`}>{statusLabel(status)}</span>
      </div>
      {image ? (
        <img className="render-image" src={image} alt="Agent render" data-testid="render-view-image" />
      ) : status === "complete" ? (
        <div className="render-placeholder">No image in the result</div>
      ) : (
        <div className="render-placeholder">Rendering…</div>
      )}
      {meta?.caption && <div className="tool-caption">{meta.caption}</div>}
    </div>
  );
}

function parseResult<T>(result: string | undefined): T | null {
  if (!result) return null;
  try {
    return JSON.parse(result) as T;
  } catch {
    return null;
  }
}

interface CheckResult {
  drc_counts?: { errors?: number; warnings?: number; unconnected?: number };
  violations?: { severity?: string; description?: string }[];
  unconnected?: { description?: string }[];
  goal_met?: boolean;
  criteria?: { id: string; type: string; passed: boolean | null; value?: unknown; detail?: string }[];
}

function CheckCard({ status, result }: { status: ToolStatus; result?: string }) {
  const r = parseResult<CheckResult>(result);
  return (
    <Card
      name="check"
      status={status}
      testId="check-card"
      className="check-card"
      extra={
        r?.goal_met !== undefined ? (
          <span className={`badge ${r.goal_met ? "badge-ok" : "badge-warn"}`}>{r.goal_met ? "goal met" : "goal not met"}</span>
        ) : undefined
      }
    >
      {r && (
        <>
          <div className="drc-counts">
            {(["errors", "warnings", "unconnected"] as const).map((k) => (
              <span key={k} className={`drc-count drc-${k}`}>
                {k}: <b>{r.drc_counts?.[k] ?? 0}</b>
              </span>
            ))}
          </div>
          {r.criteria && (
            <table className="criteria-table" data-testid="criteria-table">
              <tbody>
                {r.criteria.map((c) => (
                  <tr key={c.id} className={c.passed === false ? "crit-fail" : "crit-pass"}>
                    <td>{c.passed === false ? "✗" : c.passed ? "✓" : "–"}</td>
                    <td>{c.id}</td>
                    <td>{c.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(r.violations?.length ?? 0) > 0 && (
            <ul className="violations">
              {r.violations!.slice(0, 6).map((v, i) => (
                <li key={i}>
                  {v.severity === "error" ? "⛔" : "⚠"} {v.description}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

interface FeedbackResult {
  count?: number;
  threads?: { number: number; severity: string; status: string; tags: string[]; body: string; where?: { ref?: string; pad?: string } }[];
}

function FeedbackCard({ status, result }: { status: ToolStatus; result?: string }) {
  const r = parseResult<FeedbackResult>(result);
  return (
    <div className="tool-card feedback-card" data-status={status}>
      <div className="tool-card-header">
        <span className="tool-name">list_reviews</span>
        <span className={`tool-status tool-status-${status}`}>{statusLabel(status)}</span>
        {r?.count !== undefined && <span className="badge">{r.count} threads</span>}
      </div>
      <div className="thread-chips">
        {(r?.threads ?? []).map((t) => (
          <span key={t.number} className={`chip sev-${t.severity}`} data-testid="feedback-chip" title={t.body}>
            #{t.number} {t.where?.ref ?? ""}
            {t.where?.pad ? `.${t.where.pad}` : ""} · {t.severity}
          </span>
        ))}
      </div>
    </div>
  );
}

function SummaryCard({ status, result }: { status: ToolStatus; result?: string }) {
  const r = parseResult<Record<string, unknown>>(result);
  if (!r) return <ToolCard name="get_design" parameters={{}} status={status} result={result} />;
  const drc = r.drc_counts as Record<string, number> | undefined;
  return (
    <div className="tool-card" data-status={status}>
      <div className="tool-card-header">
        <span className="tool-name">get_design</span>
        <span className={`tool-status tool-status-${status}`}>{statusLabel(status)}</span>
      </div>
      <div className="summary-line">
        {typeof r.size === "object" && Array.isArray(r.size) ? `${(r.size as number[]).map((v) => Math.round(v)).join(" × ")} mm · ` : ""}
        {Array.isArray(r.components) ? r.components.length : String(r.components ?? "?")} components · DRC {drc?.errors ?? "?"}e/{drc?.warnings ?? "?"}w/{drc?.unconnected ?? "?"}u · head {String(r.head ?? "").slice(0, 8) || "?"}
      </div>
    </div>
  );
}

function MeasureCard({ status, result }: { status: ToolStatus; result?: string }) {
  const r = parseResult<{ distance?: number; a?: unknown; b?: unknown }>(result);
  return (
    <div className="tool-card" data-status={status}>
      <div className="tool-card-header">
        <span className="tool-name">measure</span>
        <span className={`tool-status tool-status-${status}`}>{statusLabel(status)}</span>
      </div>
      {r?.distance !== undefined && <div className="summary-line">{r.distance.toFixed(3)} mm</div>}
    </div>
  );
}

function EditCard({ name, parameters, status, result }: { name: string; parameters: unknown; status: ToolStatus; result?: string }) {
  const r = parseResult<{ ok?: boolean; version?: { sha?: string; short?: string; subject?: string } | string; error?: string }>(result);
  const version = typeof r?.version === "object" ? r.version : undefined;
  const short = version?.short ?? (typeof r?.version === "string" ? r.version : undefined);
  const subject = version?.subject ?? summarizeArgs(parameters);
  return (
    <Card
      name={name}
      status={status}
      testId="edit-card"
      toolName={name}
      className={`edit-card ${r?.ok === false ? "edit-error" : ""}`}
      extra={
        <>
          <span className="tool-args">{subject}</span>
          {short && (
            <a
              className="version-link"
              href="#"
              data-testid="edit-version-link"
              data-sha={version?.sha}
              title="View this version"
              onClick={(e) => {
                e.preventDefault();
                if (version?.sha) void setRev(version.sha);
              }}
            >
              {short}
            </a>
          )}
        </>
      }
    >
      {r?.error && <div className="tool-caption error-text">{r.error}</div>}
    </Card>
  );
}

function summarizeArgs(params: unknown): string {
  if (!params || typeof params !== "object") return "";
  const entries = Object.entries(params as Record<string, unknown>).slice(0, 3);
  return entries.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" ");
}

function ToolCard({ name, parameters, status, result }: { name: string; parameters: unknown; status: ToolStatus; result?: string }) {
  const args = JSON.stringify(parameters ?? {}, null, 2);
  return (
    <Card name={name} status={status} testId="tool-card" toolName={name} extra={<ArgsPeek value={parameters} />}>
      {args !== "{}" && <pre className="tool-json">{args}</pre>}
      {result !== undefined && (
        <pre className="tool-json tool-result" data-testid="tool-card-result">
          {result.length > 600 ? `${result.slice(0, 600)}…` : result}
        </pre>
      )}
    </Card>
  );
}

/** A short argument summary shown in the collapsed header. */
function ArgsPeek({ value, limit = 60 }: { value: unknown; limit?: number }) {
  let text = "";
  try {
    text = typeof value === "string" ? value : JSON.stringify(value ?? {});
  } catch {
    return null;
  }
  if (!text || text === "{}" || text === "null") return null;
  return <span className="tool-args-preview">{text.length > limit ? `${text.slice(0, limit)}…` : text}</span>;
}

// --------------------------------------------------------------------------- approvals

function findToolCall(messages: Message[], toolCallId: string | undefined): ToolCall | undefined {
  if (!toolCallId) return undefined;
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    const call = message.toolCalls?.find((c) => c.id === toolCallId);
    if (call) return call;
  }
  return undefined;
}

function ApprovalCard({
  interrupts,
}: {
  interrupts: Interrupt[];
}) {
  const { copilotkit } = useCopilotKit();
  const { agent } = useAgent({ agentId: AGENT_ID });
  const [submitting, setSubmitting] = useState(false);

  const decide = useCallback(
    async (approved: boolean) => {
      setSubmitting(true);
      const payload = approved ? { approved: true } : { approved: false, reason: "Rejected in the UI." };
      try {
        const resume: ResumeEntry[] = interrupts.map((i) => ({ interruptId: i.id, status: "resolved", payload }));
        await copilotkit.runAgent({ agent, resume });
      } finally {
        setSubmitting(false);
      }
    },
    [agent, copilotkit, interrupts],
  );

  return (
    <div className="approval-card" data-testid="approval-card">
      <div className="approval-title">Approval needed</div>
      <ul>
        {interrupts.map((interrupt) => {
          const call = findToolCall(agent.messages, interrupt.toolCallId);
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(call?.function.arguments || "{}");
          } catch {
            // show the call without arguments rather than failing the card
          }
          const desc = call ? `${call.function.name}(${summarizeArgs(args)})` : interrupt.message ?? "Approve?";
          return (
            <li key={interrupt.id} data-tool={call?.function.name}>
              {desc}
            </li>
          );
        })}
      </ul>
      <div className="approval-actions">
        <button data-testid="approve-button" disabled={submitting} onClick={() => void decide(true)}>
          Approve
        </button>
        <button data-testid="reject-button" disabled={submitting} onClick={() => void decide(false)}>
          Reject
        </button>
      </div>
    </div>
  );
}

/** Type guard used by App.tsx to read the agent state. */
export function asDesignState(state: unknown): DesignState {
  return (state ?? {}) as DesignState;
}
