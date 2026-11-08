/** Review tab: Fictiv-style critique composer + thread list (DESIGN.md, "Review tab"). */

import { useMemo, useState } from "react";

import { api } from "../api.ts";
import { sendAgentMessage } from "../agentBus.ts";
import { pickedRef, refreshThreads, setStore, useHarness } from "../store.ts";
import type { Thread, ThreadSeverity } from "../types.ts";

const PRESET_TAGS = ["placement", "routing", "clearance", "silkscreen", "DFM", "mechanical", "thermal", "cost", "question"];
const SEVERITIES: { id: ThreadSeverity; label: string }[] = [
  { id: "info", label: "Info" },
  { id: "suggestion", label: "Suggestion" },
  { id: "warning", label: "Warning" },
  { id: "must_fix", label: "Must fix" },
];

export function ReviewTab({ onShowOnBoard }: { onShowOnBoard: (t: Thread) => void }) {
  const h = useHarness();
  const [body, setBody] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [freeTag, setFreeTag] = useState("");
  const [severity, setSeverity] = useState<ThreadSeverity>("suggestion");
  const [saving, setSaving] = useState(false);
  const [filterStatus, setFilterStatus] = useState<"open" | "addressed" | "resolved" | "all">("open");
  const [filterSeverity, setFilterSeverity] = useState<string>("all");
  const [filterTag, setFilterTag] = useState<string>("all");
  const [replyDraft, setReplyDraft] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);

  const pick = h.composerPick;
  const rev = h.rev === "working" ? (h.design?.head ?? "working") : h.rev;

  const filtered = useMemo(() => {
    return (h.threads ?? [])
      .filter((t) => {
        if (t.resolved_anchor?.state === "detached") return false; // listed separately below
        if (filterStatus === "open" && !["open", "reopened"].includes(t.status)) return false;
        if (filterStatus === "addressed" && t.status !== "addressed") return false;
        if (filterStatus === "resolved" && !["resolved", "wontfix"].includes(t.status)) return false;
        if (filterSeverity !== "all" && t.severity !== filterSeverity) return false;
        if (filterTag !== "all" && !t.tags.includes(filterTag)) return false;
        return true;
      })
      .sort((a, b) => a.number - b.number);
  }, [h.threads, filterStatus, filterSeverity, filterTag]);

  const allTags = useMemo(() => {
    const s = new Set<string>(PRESET_TAGS);
    for (const t of h.threads ?? []) for (const tag of t.tags) s.add(tag);
    return [...s];
  }, [h.threads]);

  const submit = async () => {
    if (!pick || !body.trim() || !h.designName) return;
    setSaving(true);
    setError(null);
    try {
      await api.createThread(h.designName, {
        sha: rev === "working" ? undefined : rev,
        anchor: {
          kind: pick.kind,
          uuid: pick.uuid,
          ref: pick.ref,
          pad: pick.pad,
          net: pick.net,
          layer: pick.layer,
          side: pick.side,
          x: pick.x,
          y: pick.y,
          entity_x: pick.entity_x,
          entity_y: pick.entity_y,
        },
        body: body.trim(),
        tags: [...tags, freeTag.trim()].filter(Boolean),
        severity,
        view: h.view ? { side: h.side === "back" ? "back" : "front", center: null, view_mm: h.view.mm } : undefined,
      });
      setBody("");
      setTags([]);
      setFreeTag("");
      setStore({ composerPick: null });
      await refreshThreads();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const askAgent = async () => {
    const open = (h.threads ?? []).filter((t) => ["open", "reopened"].includes(t.status));
    if (!open.length) return;
    const msg = `Please address the open review comments: ${open.map((t) => `#${t.number}`).join(", ")}. Use list_reviews to see them, fix the board, then mark each addressed.`;
    await sendAgentMessage(msg);
  };

  return (
    <div className="review-tab">
      {/* composer */}
      <div className="composer" data-testid="composer">
        <div className="composer-head">New comment</div>
        {pick ? (
          <div className="composer-entity" data-testid="composer-entity">
            <b>{pickedRef(pick) ?? "Point"}</b> · {pick.kind} · {pick.layer ?? "any layer"} · ({pick.x.toFixed(2)}, {pick.y.toFixed(2)})
            <button className="link" onClick={() => setStore({ composerPick: null })}>
              clear
            </button>
          </div>
        ) : (
          <div className="composer-entity muted">Click an entity on the board (Comment mode), or a pin.</div>
        )}
        <textarea
          data-testid="comment-body"
          placeholder="What should change here?"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={3}
        />
        <div className="tag-row">
          {PRESET_TAGS.map((tag) => (
            <button
              key={tag}
              className={`chip ${tags.includes(tag) ? "chip-on" : ""}`}
              data-testid={`tag-${tag}`}
              onClick={() => setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))}
            >
              {tag}
            </button>
          ))}
          <input
            className="free-tag"
            placeholder="tag…"
            value={freeTag}
            onChange={(e) => setFreeTag(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && freeTag.trim()) {
                e.preventDefault();
                setTags((prev) => [...new Set([...prev, freeTag.trim()])]);
                setFreeTag("");
              }
            }}
          />
        </div>
        <div className="sev-row">
          {SEVERITIES.map((s) => (
            <label key={s.id} className={`sev-opt sev-${s.id}`}>
              <input type="radio" name="severity" checked={severity === s.id} onChange={() => setSeverity(s.id)} />
              {s.label}
            </label>
          ))}
        </div>
        {error && <div className="error-text">{error}</div>}
        <div className="composer-actions">
          <button data-testid="comment-submit" disabled={!pick || !body.trim() || saving} onClick={() => void submit()}>
            Comment
          </button>
          <button className="secondary" data-testid="ask-agent" onClick={() => void askAgent()} title="Send a chat message asking the agent to address open comments">
            Ask the agent to address open comments
          </button>
        </div>
      </div>

      {/* filters */}
      <div className="thread-filters" data-testid="thread-filters">
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value as typeof filterStatus)} data-testid="filter-status">
          <option value="open">Open + reopened</option>
          <option value="addressed">Addressed</option>
          <option value="resolved">Resolved + won't fix</option>
          <option value="all">All</option>
        </select>
        <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)} data-testid="filter-severity">
          <option value="all">Any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <select value={filterTag} onChange={(e) => setFilterTag(e.target.value)} data-testid="filter-tag">
          <option value="all">Any tag</option>
          {allTags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {/* thread list */}
      <div className="thread-list" data-testid="thread-list">
        {filtered.length === 0 && <div className="muted">No threads match.</div>}
        {filtered.map((t) => (
          <ThreadCard
            key={t.id}
            thread={t}
            onShowOnBoard={onShowOnBoard}
            onReply={async (text) => {
              await api.reply(t.id, text);
              await refreshThreads();
            }}
            onStatus={async (status, note) => {
              await api.setStatus(t.id, status, note);
              await refreshThreads();
            }}
            replyDraft={replyDraft[t.id] ?? ""}
            setReplyDraft={(v) => setReplyDraft((prev) => ({ ...prev, [t.id]: v }))}
          />
        ))}
      </div>

      {/* detached threads (entity removed): listed separately per DESIGN.md */}
      {detachedCount(h.threads) > 0 && (
        <div className="detached-group" data-testid="detached-threads">
          <div className="muted detached-note">
            Detached from the board (entity removed) — {detachedCount(h.threads)}
          </div>
          {(h.threads ?? [])
            .filter((t) => !t.resolved_anchor || t.resolved_anchor.state === "detached")
            .map((t) => (
              <ThreadCard
                key={t.id}
                thread={t}
                onShowOnBoard={onShowOnBoard}
                onReply={async (text) => {
                  await api.reply(t.id, text);
                  await refreshThreads();
                }}
                onStatus={async (status, note) => {
                  await api.setStatus(t.id, status, note);
                  await refreshThreads();
                }}
                replyDraft={replyDraft[t.id] ?? ""}
                setReplyDraft={(v) => setReplyDraft((prev) => ({ ...prev, [t.id]: v }))}
              />
            ))}
        </div>
      )}
    </div>
  );
}

function detachedCount(threads: Thread[] | null): number {
  return (threads ?? []).filter((t) => !t.resolved_anchor || t.resolved_anchor.state === "detached").length;
}

function ThreadCard({
  thread,
  onShowOnBoard,
  onReply,
  onStatus,
  replyDraft,
  setReplyDraft,
}: {
  thread: Thread;
  onShowOnBoard: (t: Thread) => void;
  onReply: (body: string) => Promise<void>;
  onStatus: (status: string, note?: string) => Promise<void>;
  replyDraft: string;
  setReplyDraft: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // Identity label comes from the thread's own anchor: resolved_anchor is a slim
  // dict (state/x/y/uuid/layer) with identity fields always null.
  const a = thread.anchor;
  const where = [a.ref ? a.ref + (a.pad ? `.${a.pad}` : "") : a.net ? `net:${a.net}` : a.kind, a.layer ?? ""].filter(Boolean).join(" · ");
  const anchorState = thread.resolved_anchor?.state;
  return (
    <div className={`thread-card sev-border-${thread.severity} ${thread.status}`} data-testid={`thread-${thread.number}`} data-status={thread.status}>
      <div className="thread-head" onClick={() => setOpen((o) => !o)}>
        <span className={`pin-dot pin-${thread.severity} ${["resolved", "wontfix"].includes(thread.status) ? "hollow" : ""}`}>{thread.number}</span>
        <span className="thread-where">{where}</span>
        <span className={`status-chip status-${thread.status}`}>{thread.status}</span>
        <span className={`sev-chip sev-${thread.severity}`}>{thread.severity.replace("_", " ")}</span>
      </div>
      <div className="thread-body">{thread.body}</div>
      <div className="thread-meta">
        {thread.tags.map((tag) => (
          <span key={tag} className="chip chip-sm">
            {tag}
          </span>
        ))}
        <span className="muted">
          on {thread.created_sha.slice(0, 8)}
          {anchorState && anchorState !== "exact" ? ` · anchor ${anchorState}` : ""}
        </span>
      </div>
      <div className="thread-actions">
        <button className="link" onClick={() => onShowOnBoard(thread)}>
          show on board
        </button>
        <button className="link" onClick={() => void onStatus("resolved")}>
          resolve
        </button>
        <button className="link" onClick={() => void onStatus("reopened")}>
          reopen
        </button>
        <button className="link" onClick={() => void onStatus("wontfix")}>
          won't fix
        </button>
      </div>
      {open && (
        <div className="thread-detail">
          {(thread.replies ?? []).map((r) => (
            <div key={r.id} className="reply">
              <b>{r.author}</b> {r.body}
            </div>
          ))}
          <div className="reply-row">
            <input
              placeholder="Reply…"
              value={replyDraft}
              onChange={(e) => setReplyDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && replyDraft.trim()) {
                  void onReply(replyDraft.trim()).then(() => setReplyDraft(""));
                }
              }}
            />
            <button
              className="secondary"
              disabled={!replyDraft.trim()}
              onClick={() => {
                void onReply(replyDraft.trim()).then(() => setReplyDraft(""));
              }}
            >
              Reply
            </button>
          </div>
          <div className="muted status-log">
            {(thread.status_log ?? []).map((l, i) => (
              <div key={i}>
                {l.status} by {l.by} {l.note ? `— ${l.note}` : ""}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
