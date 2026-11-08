/** Header: design picker, branch + head, run indicator with Stop, loop/supervised toggles. */

import { useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import { useEffect, useState } from "react";

import { AGENT_ID } from "./agent.ts";
import { initStore, selectDesign, setStore, useHarness } from "./store.ts";
import { api } from "./api.ts";
import { ModelPicker } from "./ModelPicker.tsx";
import { ContextMeter } from "./ContextMeter.tsx";

import { getTheme, toggleTheme } from "./theme.ts";

export type Page = "workspace" | "projects" | "settings";

export function Header({ page, onNavigate, onToggleLoop, onToggleSupervised }: {
  page: Page;
  onNavigate: (page: Page) => void;
  onToggleLoop: (v: boolean) => void;
  onToggleSupervised: (v: boolean) => void;
}) {
  const h = useHarness();
  const { agent } = useAgent({ agentId: AGENT_ID });
  const { copilotkit } = useCopilotKit();
  const [now, setNow] = useState(Date.now());
  const [theme, setTheme] = useState<"light" | "dark">(() => getTheme());

  useEffect(() => {
    if (!h.runActiveSince) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [h.runActiveSince]);

  const running = agent.isRunning;
  const elapsed = h.runActiveSince ? Math.max(0, Math.round((now - h.runActiveSince) / 1000)) : 0;

  return (
    <header className="harness-header">
      <div className="header-left">
        <span className="brand">kicad-harness</span>
        <select
          data-testid="design-picker"
          value={h.designName ?? ""}
          onChange={(e) => void selectDesign(e.target.value)}
        >
          {(h.designs ?? []).map((d) => (
            <option key={d.name} value={d.name}>
              {d.name}
            </option>
          ))}
        </select>
        <button
          className={`projects-btn ${page === "projects" ? "active" : ""}`}
          data-testid="projects-open"
          onClick={() => onNavigate("projects")}
          title="Every project on this harness"
        >
          Projects
        </button>
        <button
          className={`projects-btn ${page === "settings" ? "active" : ""}`}
          data-testid="settings-open"
          onClick={() => onNavigate("settings")}
          title="Global harness settings: tools, workflow and guidance"
        >
          Settings
        </button>
        <span className="muted" data-testid="branch-head">
          {h.design?.branch ?? "—"} @ {(h.design?.head ?? "").slice(0, 8) || "—"}
        </span>
        <ContextMeter design={h.designName} />
      </div>
      <div className="header-right">
        <ModelPicker />
        <button
          className="theme-toggle"
          data-testid="theme-toggle"
          data-theme={theme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() => setTheme(toggleTheme())}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <label className="toggle" title="Goal loop: the agent keeps working until every goal criterion passes. While on, any message you send also joins the loop.">
          <input
            type="checkbox"
            data-testid="toggle-loop"
            checked={h.loopEnabled}
            onChange={(e) => onToggleLoop(e.target.checked)}
          />
          Goal loop
        </label>
        <label className="toggle" title="Supervised: destructive tools need approval in the chat">
          <input
            type="checkbox"
            data-testid="toggle-supervised"
            checked={h.supervised}
            onChange={(e) => onToggleSupervised(e.target.checked)}
          />
          Supervised
        </label>
        <span className={`run-status ${running ? "running" : ""}`} data-testid="run-status" data-running={running}>
          {running ? `running ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}` : "idle"}
        </span>
        {running && (
          <button
            className="stop-btn"
            data-testid="stop-run"
            onClick={() => {
              // Ask the server too: a run started in another tab (or orphaned by a reload) has no
              // client-side agent to abort.
              if (h.designName) void api.stopRun(h.designName).catch(() => undefined);
              try {
                agent.abortRun();
              } catch {
                try {
                  void copilotkit.stopAgent({ agent });
                } catch {
                  // already stopped
                }
              }
            }}
          >
            Stop
          </button>
        )}
        {h.error && (
          <span className="error-text" title={h.error} onClick={() => setStore({ error: null })}>
            ⚠ {h.error.slice(0, 60)}
          </span>
        )}
      </div>
    </header>
  );
}

/** Re-init helper used after server restarts. */
export function reloadDesigns() {
  void initStore();
}
