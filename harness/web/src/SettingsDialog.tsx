/**
 * Global harness settings: the reference sheet for every project.
 *
 * This lives outside a design on purpose. It answers three questions that are the same for every
 * board: which tools exist (and at what stage), how the BOM / schematic / board workflow runs, and
 * what the general guidance is for each phase. Nothing here is project state.
 */

import { useEffect, useMemo, useState } from "react";

import { api } from "./api.ts";
import type { SettingsResponse, SettingsTool } from "./types.ts";

type Tab = "tools" | "workflow" | "guidance";

const WORKFLOW_TITLES: Record<string, string> = {
  bom: "BOM / sourcing",
  schematic: "Schematic",
  board: "Board layout",
};

export function SettingsPage({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("tools");
  const [level, setLevel] = useState<string>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    api
      .settings()
      .then((value) => live && setData(value))
      .catch((e) => live && setError(String(e instanceof Error ? e.message : e)));
    return () => {
      live = false;
    };
  }, []);

  const tools = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    return data.tools.filter(
      (tool) =>
        (level === "all" || tool.level === level) &&
        (!needle ||
          tool.name.toLowerCase().includes(needle) ||
          tool.summary.toLowerCase().includes(needle) ||
          tool.guidance.toLowerCase().includes(needle)),
    );
  }, [data, level, query]);

  const levelLabel = (id: string) => data?.levels.find((item) => item.id === id)?.label ?? id;

  return (
    <div className="page settings-page" data-testid="settings-page">
        <div className="page-head">
          <div>
            <b>Harness settings</b>{" "}
            <span className="muted" data-testid="settings-scope">
              global · every project
            </span>
          </div>
          <button className="link" onClick={onClose} data-testid="settings-back">
            ← back to workspace
          </button>
        </div>

        <div className="settings-tabs" data-testid="settings-tabs">
          {(
            [
              ["tools", "Tools"],
              ["workflow", "Workflow"],
              ["guidance", "Guidance"],
            ] as [Tab, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              className={`tab ${tab === id ? "active" : ""}`}
              data-testid={`settings-tab-${id}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
          {data?.model && (
            <span className="muted settings-model" title="Model new runs use">
              model {data.model}
            </span>
          )}
        </div>

        {error && <div className="error-text">{error}</div>}

        {tab === "tools" && (
          <div className="settings-tools" data-testid="settings-tools">
            <div className="settings-toolbar">
              <input
                type="search"
                placeholder="Filter tools…"
                value={query}
                data-testid="settings-tool-search"
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="settings-chips">
                <button
                  className={`chip ${level === "all" ? "active" : ""}`}
                  data-testid="settings-level-all"
                  onClick={() => setLevel("all")}
                >
                  All {data ? `(${data.tools.length})` : ""}
                </button>
                {(data?.levels ?? []).map((item) => {
                  const count = data ? data.tools.filter((t) => t.level === item.id).length : 0;
                  return (
                    <button
                      key={item.id}
                      className={`chip ${level === item.id ? "active" : ""}`}
                      data-testid={`settings-level-${item.id}`}
                      onClick={() => setLevel(item.id)}
                    >
                      {item.label} ({count})
                    </button>
                  );
                })}
              </div>
            </div>

            {!data && !error && <div className="muted">loading…</div>}
            <div className="settings-tool-list">
              {tools.map((tool) => (
                <ToolRow key={tool.name} tool={tool} levelLabel={levelLabel(tool.level)} />
              ))}
              {data && tools.length === 0 && <div className="muted">No tool matches that filter.</div>}
            </div>
          </div>
        )}

        {tab === "workflow" && data && (
          <div className="settings-workflow" data-testid="settings-workflow">
            {(["bom", "schematic", "board"] as const).map((phase) => (
              <section key={phase} className="settings-phase" data-testid={`settings-workflow-${phase}`}>
                <h4>{WORKFLOW_TITLES[phase]}</h4>
                <ol>
                  {(data.workflows[phase] ?? []).map((item) => (
                    <li key={item.step}>
                      <b>{item.step}</b>
                      <div className="muted">{item.detail}</div>
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </div>
        )}

        {tab === "guidance" && data && (
          <div className="settings-guidance" data-testid="settings-guidance">
            {(["bom", "schematic", "board"] as const).map((phase) => (
              <section key={phase} className="settings-phase" data-testid={`settings-guidance-${phase}`}>
                <h4>{WORKFLOW_TITLES[phase]}</h4>
                <p>{data.guidance[phase]}</p>
              </section>
            ))}
          </div>
        )}
    </div>
  );
}

function ToolRow({ tool, levelLabel }: { tool: SettingsTool; levelLabel: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`settings-tool ${open ? "open" : ""}`} data-testid={`settings-tool-${tool.name}`}>
      <button className="settings-tool-head" onClick={() => setOpen((value) => !value)}>
        <code>{tool.name}</code>
        <span className={`badge level-${tool.level}`}>{levelLabel}</span>
        <span className="settings-tool-summary">{tool.summary || "—"}</span>
      </button>
      {(open || tool.guidance) && (
        <div className="settings-tool-body">
          {tool.guidance && <p className="settings-tool-guidance">{tool.guidance}</p>}
          {open && tool.description && <p className="muted settings-tool-full">{tool.description}</p>}
        </div>
      )}
    </div>
  );
}

