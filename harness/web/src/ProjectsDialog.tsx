/** Project selector: every design with a thumbnail, plus creation from an upload or the demo fixture. */

import { useEffect, useRef, useState } from "react";

import { api } from "./api.ts";
import { refreshDesigns, selectDesign, useHarness } from "./store.ts";
import type { DesignInfo, Goal } from "./types.ts";

function ago(seconds?: number | null): string {
  if (!seconds) return "";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 90) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  return `${Math.round(delta / 86400)} d ago`;
}

export function ProjectsPage({ onClose }: { onClose: () => void }) {
  const h = useHarness();
  const [items, setItems] = useState<DesignInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [text, setText] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [useFixture, setUseFixture] = useState(true);
  const [creating, setCreating] = useState(false);
  /** "prompt" describes a new board; "board" starts from a .kicad_pcb or the demo board. */
  const [mode, setMode] = useState<"prompt" | "board">("prompt");
  const [prompt, setPrompt] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = () => api.designs().then(setItems).catch((e) => setError(String(e)));
  useEffect(() => {
    void load();
    void refreshDesigns();
  }, []);

  const rename = async (current: string) => {
    const next = window.prompt("New project name", current)?.trim();
    if (!next || next === current) return;
    try {
      await api.renameDesign(current, next);
      if (h.designName === current) await selectDesign(next);
      await load();
      await refreshDesigns();
    } catch (e) {
      setError(String(e));
    }
  };

  const duplicate = async (current: string) => {
    const next = window.prompt("Name for the copy", `${current}-copy`)?.trim();
    if (!next) return;
    try {
      await api.duplicateDesign(current, next);
      await load();
      await refreshDesigns();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (current: string) => {
    if (!window.confirm(`Delete project "${current}" and its history?`)) return;
    try {
      await api.deleteDesign(current);
      await load();
      await refreshDesigns();
    } catch (e) {
      setError(String(e));
    }
  };

  const pickFile = async (file: File | null) => {
    if (!file) {
      setText(null);
      setFileName(null);
      return;
    }
    setText(await file.text());
    setFileName(file.name);
    setUseFixture(false);
    if (!name) setName(file.name.replace(/\.kicad_pcb$/i, "").replace(/[^A-Za-z0-9_.-]+/g, "-").slice(0, 40));
  };

  const create = async () => {
    if (!name.trim()) return;
    if (mode === "prompt" && !prompt.trim()) return;
    setCreating(true);
    setError(null);
    try {
      if (mode === "prompt") {
        // A described board starts in the schematic phase; the agent draws it from the text.
        await api.createDesign({ name: name.trim(), prompt: prompt.trim(), autostart: true });
        await refreshDesigns();
        await selectDesign(name.trim());
        onClose();
        return;
      }
      const goal: Goal | undefined = summary.trim()
        ? {
            summary: summary.trim(),
            criteria: [
              { id: "drc", type: "drc_errors", max: 0 },
              { id: "routed", type: "unconnected", max: 0 },
            ],
          }
        : undefined;
      await api.createDesign({
        name: name.trim(),
        board_text: text ?? undefined,
        from_fixture: text ? undefined : useFixture,
        goal,
      });
      await refreshDesigns();
      await selectDesign(name.trim());
      onClose();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setCreating(false);
    }
  };

  /** Turn a pasted idea into a usable project name (a few meaningful words). */
  const nameFromPrompt = () => {
    if (name.trim() || !prompt.trim()) return;
    const words = prompt
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 2 && !["the", "and", "with", "for", "that", "from", "board", "circuit"].includes(w))
      .slice(0, 4);
    if (words.length) setName(words.join("-").slice(0, 40));
  };

  return (
    <div className="page projects-page" data-testid="projects-page">
      <div className="page-head">
        <div>
          <b>Projects</b> <span className="muted">every design on this harness</span>
        </div>
        <button className="link" onClick={onClose} data-testid="projects-back">
          ← back to workspace
        </button>
      </div>
      <div className="page-body projects-page-body">

        <div className="projects-list" data-testid="projects-list">
          {(items.length ? items : h.designs ?? []).map((d) => (
            <div
              key={d.name}
              className={`project-card ${d.name === h.designName ? "current" : ""}`}
              data-testid={`project-${d.name}`}
            >
              <button
                className="project-open"
                onClick={() => {
                  void selectDesign(d.name);
                  onClose();
                }}
              >
                {d.thumbnail ? (
                  <img className="project-thumb" src={d.thumbnail} alt={`${d.name} board`} loading="lazy" />
                ) : (
                  <span className="project-thumb empty" />
                )}
                <span className="project-meta">
                  <b>
                    {d.name}
                    {d.phase === "schematic" && <span className="badge badge-phase">schematic</span>}
                  </b>
                  <span className="muted">
                    {d.branch} @ {(d.head ?? "").slice(0, 8) || "—"} · {ago(d.updated_at)}
                  </span>
                  <span className="project-subject">{d.brief ?? d.latest?.subject ?? "no versions yet"}</span>
                  {d.goal_summary && <span className="project-goal">{d.goal_summary}</span>}
                </span>
                {d.name === h.designName && <span className="badge badge-ok">current</span>}
              </button>
              <div className="project-actions">
                <button className="link" data-testid={`project-rename-${d.name}`} onClick={() => void rename(d.name)}>
                  rename
                </button>
                <button className="link" data-testid={`project-duplicate-${d.name}`} onClick={() => void duplicate(d.name)}>
                  duplicate
                </button>
                <button className="link danger" data-testid={`project-delete-${d.name}`} onClick={() => void remove(d.name)}>
                  delete
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="project-new">
          <div className="section-title">New project</div>
          <div className="project-form">
            <div className="new-project-modes" data-testid="new-project-modes">
              <button
                className={`mode-pill ${mode === "prompt" ? "active" : ""}`}
                data-testid="new-project-mode-prompt"
                onClick={() => setMode("prompt")}
              >
                describe a board
              </button>
              <button
                className={`mode-pill ${mode === "board" ? "active" : ""}`}
                data-testid="new-project-mode-board"
                onClick={() => setMode("board")}
              >
                import a board
              </button>
            </div>

            <input
              data-testid="new-project-name"
              placeholder="project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />

            {mode === "prompt" ? (
              <>
                <textarea
                  className="new-project-prompt"
                  data-testid="new-project-prompt"
                  data-1p-ignore
                  rows={7}
                  placeholder={
                    "What are you building?\n\n" +
                    "e.g. A 555 astable LED blinker on a 5 V supply: NE555, R1 10k, R2 47k, C1 10 µF, " +
                    "C2 10 nF, one red 0805 LED with a 470 Ω series resistor. Single-sided if possible, 25 × 20 mm."
                  }
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onBlur={nameFromPrompt}
                />
                <div className="muted new-project-hint">
                  The agent draws the schematic from your description, checks it with ERC, then lays out the
                  board from the finished netlist.
                </div>
              </>
            ) : (
              <>
                <input
                  data-testid="new-project-goal"
                  placeholder="goal summary (optional)"
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                />
                <label className="toggle">
                  <input type="checkbox" checked={useFixture} onChange={(e) => setUseFixture(e.target.checked)} />
                  start from the demo board
                </label>
                <button className="secondary" onClick={() => fileRef.current?.click()} data-testid="new-project-file">
                  {fileName ? `board: ${fileName}` : "upload .kicad_pcb…"}
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".kicad_pcb"
                  style={{ display: "none" }}
                  onChange={(e) => void pickFile(e.target.files?.[0] ?? null)}
                />
              </>
            )}

            <button
              className="run-goal-btn"
              data-testid="new-project-create"
              disabled={!name.trim() || creating || (mode === "prompt" && !prompt.trim())}
              onClick={() => void create()}
            >
              {creating ? "creating…" : mode === "prompt" ? "Create and start drawing" : "Create project"}
            </button>
          </div>
          {error && (
            <div className="error-text" data-testid="new-project-error">
              {error.slice(0, 200)}
            </div>
          )}
      </div>
      </div>
    </div>
  );
}