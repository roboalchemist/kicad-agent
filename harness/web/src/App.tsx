/** App shell: CopilotKit provider, header, board viewer, right-side tab panel, live SSE. */

import { CopilotChat, CopilotKitProvider, useAgent, useCopilotKit, UseAgentUpdate } from "@copilotkit/react-core/v2";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AGENT_ID } from "./agent.ts";
import { api } from "./api.ts";
import { BoardViewer } from "./BoardViewer.tsx";
import { BomTable } from "./BomTable.tsx";
import { ProjectsPage } from "./ProjectsDialog.tsx";
import { SettingsPage } from "./SettingsDialog.tsx";
import { toolCallRenderers } from "./ToolCalls.tsx";
import { SchematicViewer } from "./SchematicViewer.tsx";
import { Header, type Page } from "./Header.tsx";
import { asDesignState, useHarnessTools } from "./tools.tsx";
import {
  focusOnBoard,
  getStore,
  initStore,
  refreshDesignData,
  refreshGoal,
  refreshRuns,
  refreshThreads,
  refreshVersions,
  setAgentStateSink,
  setSide,
  setStore,
  useHarness,
} from "./store.ts";
import { setAgentSender } from "./agentBus.ts";
import { ActivityTab } from "./tabs/ActivityTab.tsx";
import { AgentTab } from "./tabs/AgentTab.tsx";
import { GoalTab } from "./tabs/GoalTab.tsx";
import { ReviewTab } from "./tabs/ReviewTab.tsx";
import { VersionsTab } from "./tabs/VersionsTab.tsx";
import type { Thread } from "./types.ts";

/** Keeps the thread in `?thread=` so a reload reconnects to it through the runtime. */
function threadIdFromUrl(): string {
  const url = new URL(window.location.href);
  let threadId = url.searchParams.get("thread");
  if (!threadId) {
    threadId = crypto.randomUUID();
    url.searchParams.set("thread", threadId);
    window.history.replaceState(null, "", url);
  }
  return threadId;
}

export function App() {
  const [threadId] = useState(threadIdFromUrl);
  const [errors, setErrors] = useState<string[]>([]);

  return (
    <CopilotKitProvider
      runtimeUrl="/copilotkit"
      agentId={AGENT_ID}
      enableInspector={import.meta.env.VITE_COPILOTKIT_INSPECTOR === "1"}
      showIntelligenceIndicator={false}
      renderToolCalls={toolCallRenderers}
      onError={({ error, code }) => {
        console.error(`[copilotkit] ${code}`, error);
        setErrors((prev) => [...prev.slice(-4), `${code}: ${error.message}`]);
      }}
    >
      <Harness threadId={threadId} errors={errors} />
    </CopilotKitProvider>
  );
}

type TabId = "agent" | "review" | "versions" | "activity" | "goal";

/** The three sections of a project, switchable from the main panel's tab bar. */
type Section = "bom" | "schematic" | "board";

const SECTIONS: Section[] = ["bom", "schematic", "board"];

function isSection(value: string | null): value is Section {
  return value !== null && (SECTIONS as string[]).includes(value);
}

/** The section named by `?tab=` (the older `?section=` is still read) so links open a tab. */
function sectionFromUrl(): Section | null {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("tab") ?? params.get("section");
  return isSection(value) ? value : null;
}

/** Record the open section in the URL; back/forward then walk the tabs the user visited. */
function rememberSection(section: Section) {
  const url = new URL(window.location.href);
  if (url.searchParams.get("tab") === section) return;
  url.searchParams.set("tab", section);
  window.history.pushState(null, "", url);
}

function pageFromUrl(): Page {
  const value = new URL(window.location.href).searchParams.get("page");
  return value === "projects" || value === "settings" ? value : "workspace";
}

function sectionFromStorage(): Section | null {
  const value = localStorage.getItem("harness.section");
  return isSection(value) ? value : null;
}

function Harness({ threadId, errors }: { threadId: string; errors: string[] }) {
  const h = useHarness();
  const { agent, isReady } = useAgent({
    agentId: AGENT_ID,
    updates: [UseAgentUpdate.OnStateChanged, UseAgentUpdate.OnRunStatusChanged],
  });
  const { copilotkit } = useCopilotKit();
  const [tab, setTab] = useState<TabId>("agent");
  const [page, setPage] = useState<Page>(() => pageFromUrl());

  const navigate = useCallback((next: Page) => {
    const url = new URL(window.location.href);
    if (next === "workspace") url.searchParams.delete("page");
    else url.searchParams.set("page", next);
    window.history.pushState(null, "", url);
    setPage(next);
  }, []);

  useEffect(() => {
    const onPop = () => setPage(pageFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  // Default follows the project's phase until the user picks a section explicitly; a `?tab=`
  // link wins over the remembered choice so a shared URL opens the tab it names.
  const [section, setSection] = useState<Section | null>(() => sectionFromUrl() ?? sectionFromStorage());
  useHarnessTools();

  // Back/forward between tabs: the URL is the source of truth once a section is in it.
  useEffect(() => {
    const onPop = () => setSection(sectionFromUrl() ?? sectionFromStorage());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The agent owns the phase; the viewer follows it (schematic sheet until a board exists).
  const phase = ((agent.state as { phase?: string } | undefined)?.phase ?? h.design?.phase ?? "board") as
    | "schematic"
    | "board";

  // An explicit choice wins; otherwise show the section the project is actually in.
  const active: Section = section ?? (phase === "schematic" ? "schematic" : "board");

  // ---- AG-UI state: register sinks so store actions write design/selection/viewing/loop/supervised
  // into the agent state that accompanies the next run.
  useEffect(() => {
    if (!isReady) return;
    setAgentStateSink((patch) => {
      try {
        agent.setState({ ...(agent.state as Record<string, unknown>), ...patch });
      } catch {
        // agent swapped underneath us; the next sync retries
      }
    });
    setAgentSender(async (message: string) => {
      if (agent.isRunning) {
        // Mid-run: CopilotKit can't append to a streaming run — steer through the REST side channel.
        const name = getStore().designName;
        if (name) await api.steer(name, message);
        return;
      }
      agent.addMessage({ id: crypto.randomUUID(), role: "user", content: message });
      // Don't await the whole run: the composer must stay usable (and steerable) while it streams.
      // Failures surface through CopilotKit's onError handler above.
      void copilotkit.runAgent({ agent }).catch(() => undefined);
    });
    // Kick the initial design into the agent state once connected.
    const { designName, design } = getStore();
    if (designName) {
      try {
        agent.setState({
          ...(agent.state as Record<string, unknown>),
          design: designName,
          branch: design?.branch,
          head: design?.head,
          loop: getStore().loopEnabled,
          supervised: getStore().supervised,
        });
      } catch {
        // ignore
      }
    }
  }, [agent, isReady, copilotkit]);

  // ---- Mirror interesting AG-UI state into the store (highlight, goal, toggles).
  const state = asDesignState(agent.state);
  useEffect(() => {
    setStore({
      agentGoal: state.goal ?? null,
      highlight: state.highlight ?? null,
      loopEnabled: state.loop ?? getStore().loopEnabled,
      supervised: state.supervised ?? getStore().supervised,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.goal, state.highlight, state.loop, state.supervised]);

  // The agent's board_hash changes when an editing tool commits → reload index + SVG.
  const agentHash = state.board_hash;
  const lastHash = useRef<string | null>(null);
  useEffect(() => {
    if (agentHash && agentHash !== lastHash.current) {
      const first = lastHash.current === null;
      lastHash.current = agentHash;
      if (!first) void refreshDesignData(false);
    }
  }, [agentHash]);

  // ---- Run status: header indicator + activity refresh.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (agent.isRunning && !wasRunning.current) setStore({ runActiveSince: Date.now() });
    if (!agent.isRunning && wasRunning.current) {
      setStore({ runActiveSince: null });
      void refreshRuns();
      void refreshThreads();
      void refreshVersions();
      void refreshGoal();
    }
    wasRunning.current = agent.isRunning;
  }, [agent.isRunning]);

  // ---- Live SSE: board/version/threads/run/steer.
  useEffect(() => {
    if (!h.designName) return;
    const es = new EventSource(`/api/designs/${h.designName}/events`);
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { type: string; head?: string; branch?: string };
        switch (data.type) {
          case "hello":
            setStore({ sse: "connected" });
            break;
          case "board":
            void refreshDesignData(false);
            break;
          case "version":
            void refreshVersions();
            void refreshDesignData(false);
            break;
          case "threads":
            void refreshThreads();
            break;
          case "run":
            void refreshRuns();
            break;
          case "steer":
            setStore({ steerDeliveredAt: Date.now() });
            break;
        }
      } catch {
        // ignore malformed lines
      }
    };
    es.addEventListener("message", onMsg);
    es.onerror = () => setStore({ sse: "error" });
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [h.designName]);

  // ---- Initial load.
  useEffect(() => {
    void initStore();
  }, []);

  const showThreadOnBoard = useCallback(async (t: Thread) => {
    setStore({ selectedThread: t.number });
    const view = t.view;
    const ax = t.resolved_anchor?.x ?? t.anchor.x;
    const ay = t.resolved_anchor?.y ?? t.anchor.y;
    focusOnBoard([ax, ay], view?.view_mm ?? null);
    // resolved_anchor has no side field; infer from its layer ("B.*" = back), else the anchor's side.
    const raLayer = t.resolved_anchor?.layer ?? "";
    const side = raLayer.startsWith("B.") ? "back" : raLayer.startsWith("F.") ? "front" : t.anchor.side;
    await setSide(side);
  }, []);

  const toggleLoop = useCallback(
    (v: boolean) => {
      setStore({ loopEnabled: v });
      try {
        agent.setState({ ...(agent.state as Record<string, unknown>), loop: v });
      } catch {
        // ignore
      }
    },
    [agent],
  );
  const toggleSupervised = useCallback(
    (v: boolean) => {
      setStore({ supervised: v });
      try {
        agent.setState({ ...(agent.state as Record<string, unknown>), supervised: v });
      } catch {
        // ignore
      }
    },
    [agent],
  );

  const chat = useMemo(
    () => (
      <CopilotChat
        agentId={AGENT_ID}
        threadId={threadId}
        labels={{ chatInputPlaceholder: "Ask the KiCad agent…" }}
        // Open at the newest message: a long thread is thousands of lines tall.
        autoScroll="pin-to-bottom"
      />
    ),
    [threadId],
  );

  return (
    <div className="harness">
      <Header
        page={page}
        onNavigate={navigate}
        onToggleLoop={toggleLoop}
        onToggleSupervised={toggleSupervised}
      />
      {page === "projects" ? (
        <ProjectsPage onClose={() => navigate("workspace")} />
      ) : page === "settings" ? (
        <SettingsPage onClose={() => navigate("workspace")} />
      ) : (
      <div className="harness-body">
        <section className="board-panel">
          <div className="section-tabs" data-testid="section-tabs">
            {(
              [
                ["bom", "BOM"],
                ["schematic", "Schematic"],
                ["board", "Board layout"],
              ] as [Section, string][]
            ).map(([id, label]) => (
              <button
                key={id}
                className={`section-tab ${active === id ? "active" : ""}`}
                data-testid={`section-tab-${id}`}
                onClick={() => {
                  setSection(id);
                  localStorage.setItem("harness.section", id);
                  rememberSection(id);
                }}
              >
                {label}
              </button>
            ))}
            <span className="section-hint muted">{h.designName ?? ""}</span>
          </div>
          <div className="section-body">
            {!h.designName ? (
              <div className="section-empty muted">Pick a project to see its BOM, schematic and board.</div>
            ) : active === "bom" ? (
              <BomTable design={h.designName} />
            ) : active === "schematic" ? (
              h.design?.has_schematic === false ? (
                <div className="section-empty muted" data-testid="no-schematic">
                  This project was imported from a board file, so it has no schematic.
                </div>
              ) : (
                <SchematicViewer
                  design={h.designName}
                  rev={h.rev}
                  version={h.design?.schematic_hash ?? null}
                />
              )
            ) : h.design?.has_board === false ? (
              <div className="section-empty muted" data-testid="no-board">
                No board yet — the schematic phase creates it.{" "}
                {phase === "schematic" ? "Use the Schematic tab, then lay out the board." : ""}
              </div>
            ) : (
              <BoardViewer onOpenThread={() => setTab("review")} />
            )}
          </div>
        </section>
        <section className="side-panel">
          <div className="side-tabs">
            {(
              [
                ["agent", "Agent"],
                ["review", "Review"],
                ["versions", "Versions"],
                ["activity", "Activity"],
                ["goal", "Goal"],
              ] as [TabId, string][]
            ).map(([id, label]) => (
              <button key={id} className={`side-tab ${tab === id ? "active" : ""}`} data-testid={`side-tab-${id}`} onClick={() => setTab(id)}>
                {label}
                {id === "review" && (h.threads ?? []).some((t) => ["open", "reopened"].includes(t.status)) ? " ●" : ""}
              </button>
            ))}
          </div>
          <div className="side-content">
            {tab === "agent" && <AgentTab chat={chat} onEditGoal={() => setTab("goal")} />}
            {tab === "review" && <ReviewTab onShowOnBoard={(t) => void showThreadOnBoard(t)} />}
            {tab === "versions" && <VersionsTab />}
            {tab === "activity" && <ActivityTab activeRunId={activeRunIdFrom(h.runs)} />}
            {tab === "goal" && <GoalTab />}
          </div>
        </section>
      </div>
      )}
      {errors.length > 0 && (
        <div className="harness-errors" data-testid="harness-errors">
          {errors.map((error, i) => (
            <div key={i}>{error}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function activeRunIdFrom(runs: { run_id: string; status: string }[] | null): string | null {
  const running = (runs ?? []).find((r) => r.status === "running");
  return running?.run_id ?? null;
}
