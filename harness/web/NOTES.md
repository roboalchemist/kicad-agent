# Harness web UI — build notes (2026-10-16)

Deliverable notes for the CopilotKit/AG-UI web UI in `harness/web/` (branch `harness-web`).
The contract is `harness/DESIGN.md` ("Web" + "UI specification"). Everything below was verified in
a real Playwright browser (chromium) against the live stack; screenshots in `e2e/screenshots/`.

## Running it

```bash
cd harness
harness/.venv/bin/kicad-harness serve          # Python FastAPI on :8100
npm --prefix web run runtime                   # Node CopilotRuntime on :8101 (proxies /agui + thread store)
npm --prefix web run dev                       # Vite on :5173 (proxies /api and /copilotkit -> :8101)
open http://127.0.0.1:5173                     # click blinker-demo if not selected
```

Real model (backend env, per repo AGENTS.md):

```bash
export OPENAI_API_KEY=sk-or-...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export HARNESS_MODEL=openai:anthropic/claude-sonnet-5
```

Walkthrough drivers (not playwright tests, just scripted browsers):

```bash
cd harness/web
node e2e/walk.mjs                        # board flows: basics review measure select revview diff back3d branch restore
node e2e/agent.mjs                       # agent flows: chat goal approve activity
node e2e/layers.mjs                      # layer panel: show/hide, per-layer opacity, persistence
```

`revview`, `diff` and `restore` need ≥2 versions, so run them after the agent has made edits.

## What works — verified

Board viewer (walk.mjs, screenshots 01–14):
- KiCad SVG front/back + pick-index overlay; hover tooltips (`pad · R3.2 · LED_ANODE · F.Cu`).
- Comment mode → entity pick → composer → thread created; pins render, click-pin opens the thread.
- Measure mode: two pads → `9.2751 mm` measure line + nearest points box (07-measure.png).
- Select mode → pick popover (`footprint · C2`).
- View old revision → read-only banner + "Back to latest" (09-rev-banner.png).
- Diff overlay from Versions "compare" (10-diff-overlay.png).
- Back tab: B.Cu tracks/vias only, correct hit-shape side filtering (11-back-side.png).
- Layer panel: one row per plotted layer with a visibility checkbox and an opacity slider
  (10-100%). A translucent layer keeps its colour and stays pickable while what is under it
  shows through; the setting persists across reloads (30-layer-opacity.png).
- 3D tab: kicad render via REST, preset buttons top/bottom/iso/iso-back (12-3d.png).
- Branch create ("branch…" modal) + switch via dropdown, verified through `/branches` (13-branch.png).
- Restore old version → new HEAD commit "Restore …", verified through `/versions` (14-restored.png).

Agent tab (agent.mjs, screenshots 20–26):
- Real chat over OpenRouter (`openai:anthropic/claude-sonnet-5`): streams text into CopilotChat.
- Frontend tools work end-to-end: `focus_view` (zooms), `highlight` (cyan outlines on board).
- Tool cards: `design_summary` (counts line), `check` (DRC counts + criteria table + goal badge),
  `render_view` (inline image with caption), `query` (args + result JSON), editing tools collapse to
  one line with a version link.
- Goal loop: header toggle + "▶ Run goal" → the agent works autonomously (rip-up, move, outline,
  re-route, re-check) and commits a version per edit; goal validator retries until criteria pass.
- Steering mid-run: steer input → REST `/steer` → `delivered_run` set while the run is live
  ("queued ✓" chip in UI); the message arrives via the backend steering hooks.
- Supervised mode: risky tool → AG-UI interrupt → approval card → Approve resumes the run
  (`copilotkit.runAgent({agent, resume})`).
- Activity tab: run list + event replay from `/runs/{id}/events`.
- SSE live updates: board/version/threads/run/steer events refresh the UI without reload.

## Bugs found & fixed (frontend, in this branch)

1. **`design_summary` card crashed the whole app.** `r.components` is an array of objects; React
   "Objects are not valid as a React child" unmounted the entire tree (no error boundary), so the
   chat appeared dead. Now renders `components.length`. Consider an error boundary around the chat.
2. **`highlight` tool crashed when the model omits `nets`.** The AG-UI tool-arg path does not apply
   zod `.default()`s; destructured params were `undefined` → `nets.map` threw. Fixed with default
   destructuring in the handler.
3. **Thread-pin click fell through to a Point pick.** Pins have no `data-hit`, so `onPointerUp`
   created a spurious point selection under the pin. Fixed: ignore pointerup on `.thread-pin`.
4. `thread.resolved_anchor` is a **slim dict** (state/x/y/uuid/layer/detail), not a full anchor —
   the identity label must come from `thread.anchor`; side must be inferred from the resolved
   layer ("B.*" → back). Types + ReviewTab/App fixed accordingly.
5. `api.send()` had swapped method/path args (caught before it bit at runtime).

## Bugs found in the backend (NOT fixed here — report)

- **B1 (blocker for every UI run): `RUN_ERROR: Cannot set a custom run output_type when the agent
  has output validators`.** pydantic-ai's AG-UI adapter composes a run-level `output_type` whenever
  frontend tools are present (`[agent.output_type, DeferredToolRequests]`,
  `pydantic_ai/ui/_adapter.py` in `run_stream_native`), but `kicad_harness/agent/agent.py` registers
  the goal-loop `@agent.output_validator`, and pydantic-ai rejects any custom run `output_type` on
  an agent with output validators. Every run from the web UI dies in ~2 ms before reaching the
  model. The agent's own `output_type=[str, DeferredToolRequests]` already covers deferred tools, so
  the adapter's composition is redundant here — fix in `kicad_harness/agent/agent.py` (drop
  `DeferredToolRequests` from the agent output_type? no — the validator handles both; likely the
  right fix is a pydantic-ai upgrade/patch or making the adapter skip composition when the agent has
  validators). (Fixed after the merge — see "Post-merge fixes" below; the temporary
  `serve_patched.py` shim has been deleted.)
  composed output type; all agent-flow verification in this branch ran under it.
- Versions metrics are computed lazily via background tasks (first `/versions` poll may show
  `metrics: null`); harmless but worth documenting.
- `POST /branches` **creates and switches** to the new branch (UI's create-flow then reports it as
  current even before the user switches).
- `resolved_anchor` slim shape (above) is surprising given DESIGN.md's anchor table.

## CopilotKit ↔ AG-UI notes

- CopilotKit v2 (`@copilotkit/react-core/v2`) ↔ Node CopilotRuntime ↔ Python `AGUIAdapter` works
  with `mode: "sse"`. Frontend tools declared via `useFrontendTool` arrive as AG-UI `tools` in
  `RunAgentInput`; the adapter wraps them in an ExternalToolset (which is what triggers B1).
- Zod defaults on frontend tool params are NOT applied by the adapter path — always
  default-destructure in handlers.
- Tool results arrive as JSON **strings**; `JSON.parse` (with try/catch) before rendering, and never
  render raw parsed values as React children.
- Mid-run user chat messages cannot be appended to a streaming run — they go through
  `POST /api/designs/{d}/steer` (`agentBus.ts` holds the registered sender).
- Approval resume works via `copilotkit.runAgent({ agent, resume })` with
  `interrupts.map(i => ({ interruptId: i.id, status: "resolved", payload }))`.
- `agent.state` mirrors design/selection/loop/supervised/board_hash; the UI watches `board_hash` to
  reload the board after agent edits (SSE `board` events do the same for external changes).

## Known gaps / deferred

- No global React error boundary around the chat (one bad renderer unmounts the app — see bug 1).
- `walk.mjs` creates a throwaway review thread + a restore commit on `blinker-demo` each run.
- The viewer renders KiCad's built-in *plot* palette (identical to the dark default theme) on a dark canvas; the
  mask tint makes pads read pink (F.Mask #D864FF at 0.4 over copper) — that is KiCad's own layering, not a bug.
- The version diff covers footprints/pads/tracks/vias/zones only — reference-text (silkscreen label)
  moves produce an empty diff, because the pick index has no text entities. walk.mjs therefore picks
  the first version pair with a non-empty diff for its diff phase.

## Post-merge fixes (on harness-v1, 2026-10-16)

- **B1 fixed for real**: `kicad_harness/agui_compat.py` makes composed AG-UI run output_types fall
  back to the agent's own schema for validator-agents (version-guarded monkeypatch, regression tests
  in `tests/test_agui_compat.py`). The temporary `e2e/serve_patched.py` shim was deleted; all agent-flow
  verification after the merge ran against plain `kicad-harness serve`.
- **Mode-switch race fixed** (`BoardViewer.tsx`): the mode buttons did
  `import("./store.ts").then(st => st.setMode(m))` — an async dynamic import, so clicking Comment and
  then immediately clicking a pad could pick before `setMode` landed, silently dropping the
  comment anchor. Now a static import. (This was the "composer-entity never appears" flake.)
- e2e scripts: agent.mjs waits for the chat run to finish before Run-goal (the button is disabled
  while a run is active); walk.mjs waits for hit paths with real area (zero-area track lines are
  "invisible" to Playwright) and picks a version pair with a non-empty diff.
- Model note: the OpenRouter keys available during development stopped working (limits), so the
  post-merge browser e2e ran on `google:gemini-3.1-pro-preview` — needs `pydantic-ai-slim[google]`,
  now a declared extra.


## Colour scheme — KiCad Default (dark)

The board canvas is KiCad's default dark theme, and every plot/composite uses the same background so
the viewer, the agent's renders and the SVG all agree.

**Source of truth.** KiCad 10 mirrors its built-in theme to disk:
`~/Library/Preferences/kicad/10.0/colors/user.json` has `meta.name = "KiCad Default"`, `meta.version = 5`.
Values we use (rgb → hex):

| Key | rgb | hex | Used for |
|---|---|---|---|
| `board.background` | rgb(0, 16, 35) | `#001023` | viewer canvas, drill-hole punch-through, 2D render composite |
| `board.copper.f` | rgb(200, 52, 52) | `#C83434` | F.Cu |
| `board.copper.b` | rgb(77, 127, 196) | `#4D7FC4` | B.Cu |
| `board.f_silks` | rgb(242, 237, 161) | `#F2EDA1` | F.Silkscreen (refs, value, text) |
| `board.b_silks` | rgb(232, 178, 167) | `#E8B2A7` | B.Silkscreen |
| `board.edge_cuts` | rgb(208, 210, 205) | `#D0D2CD` | Edge.Cuts |
| `board.f_mask` | rgba(216, 100, 255, 0.40) | `#D864FF` | F.Mask (pads read pink over copper) |
| `board.b_mask` | rgba(2, 255, 238, 0.40) | `#02FFEE` | B.Mask |
| `board.plated_hole` | rgb(26, 196, 210) | `#1AC4D2` | plated holes |
| `board.via_hole` | rgb(227, 183, 46) | `#E3B72E` | via holes |

**Where it is applied**

- `kicad_harness/engine/kicad_cli.py`: `SVG_THEME = "_builtin_default"` (pinned so a user's editor
  theme can't change plot colours) and `SVG_BACKGROUND = "#001023"`; `export_svg` rewrites kicad-cli's
  opaque white drill-hole group (`<g style="fill:#FFFFFF…">`) to the background so holes read as
  drilled through the canvas.
- `kicad_harness/engine/render.py`: `BACKGROUND = "#001023"` — the 2D crop/zoom composites the model sees.
- `web/src/app.css`: `.board-area` background `#001023`; interaction colours are tuned for it — hover
  `rgba(255,255,255,.18)` + white stroke, selection `#7FD1FF`, agent highlight `#00E5FF`, measure
  `#FFD166` with `#FFE9A0` text on a `#001023` halo, diff `#4ADE80` / `#FF6B6B` / `#FFC857`, and the
  board overlays (hover tip, pick popover, measure box, Fit) use the dark surface `#0B1B2B`.

**Changing a colour** means changing the KiCad value, not inventing one: read it out of `user.json`
(or the built-in theme's `board.*` entries), then update `SVG_BACKGROUND`/`BACKGROUND` and the CSS table
above, and bump `artifacts.CACHE_FORMAT` so cached SVGs and renders regenerate.

Note: the board overlays are dark, but the surrounding app chrome (toolbar, side panel) stays light by
design — only the CAD canvas follows the KiCad theme.

## Post-merge fixes (2)

- **Back tab rendered the front SVG.** `ViewerToolbar` set `store.side` directly instead of calling the
  store's `setSide()`, so `loadSvg()` never ran: the image stayed on F.Cu while hit shapes/coordinates
  flipped to the back. `BoardViewer` now passes the store action (`setSide as setBoardSide`), and the
  Back tab shows B.Cu (`#4D7FC4`) — verified by screenshot on the demo board, whose back is nearly empty.


## One composer, and theming

### Single composer (Agent tab)

CopilotKit still renders its own input overlay; we hide it (`app.css` hides
`[data-testid="copilot-input-overlay"]` inside `.chat-holder`) and `AgentTab` owns the only input:

| Element | `data-testid` | Behaviour |
|---|---|---|
| input | `composer-input` | Enter sends. Idle placeholder "Ask the KiCad agent…", running placeholder "Steer the running agent…" |
| button | `composer-send` | Label "Send" when idle, "Steer" while a run is active |
| status | `composer-status` | `sending…` then `queued ✓` for a steer |
| mode chip | `composer-mode` | Shown while running: "run active · typing steers it" |

`agentBus.sendAgentMessage` routes by run state: idle → `agent.addMessage` + `copilotkit.runAgent`,
running → `POST /api/designs/<d>/steer` (CopilotKit cannot append to a streaming run).

**The sender must not await the run**: `copilotkit.runAgent` resolves only when the run finishes, so
awaiting it left the composer disabled ("sending…") for the whole run and made steering impossible.
`App.tsx` now fires it (`void … .catch()`) and errors surface through CopilotKit's `onError` banner.
Walkthrough scripts use `composer-input` / `composer-send`; `agent.mjs`'s `sendChat` presses Enter.

### Dark mode

`src/theme.ts` owns the choice: `data-theme="light|dark"` **and** a `.dark` class on `<html>`
(CopilotKit's stylesheet keys its own dark palette off a `.dark` ancestor), `color-scheme` for native
controls, persisted in `localStorage["kicad-harness-theme"]`, defaulting to the system preference.
`main.tsx` applies it before the first render so there is no light flash; the header's
`[data-testid=theme-toggle]` flips it.

All chrome colours come from CSS custom properties in `app.css`: `:root` holds the light palette and
`[data-theme="dark"]` overrides the same tokens (`--bg`, `--surface`, `--surface-2/3`, `--border*`,
`--text`, `--muted`, `--accent*`, `--ok*`, `--warn*`, `--danger*`, `--chip-bg`, `--tab-bg`,
`--overlay*`, `--backdrop`). Rules read the tokens, so adding a theme means overriding the token set.
The KiCad board canvas (`#001023`) is intentionally identical in both themes — it follows the KiCad
theme, not the app theme.


## Interactive 3D viewer, projects, models, version summaries

### 3D viewer (`src/Viewer3D.tsx`)

- The 3D tab renders the real board model, not a picture. `kicad-cli pcb export glb`
  (`kicad_cli.export_glb`, flags `--include-tracks --include-pads --include-zones
  --include-silkscreen --include-soldermask --cut-vias-in-body`) is cached per board hash as
  `<CACHE_FORMAT>/<hash>/board.glb` and served at `GET /api/designs/{d}/rev/{rev}/model.glb`
  (`model/gltf-binary`).
- three.js + GLTFLoader + OrbitControls: drag orbits, right-drag pans, wheel zooms, presets
  `iso/top/bottom/front/back` (`[data-testid=viewer3d-*]`). Loading and error states are shown;
  the static `pcb render` PNG stays as the fallback image inside the error box.
- **Units:** KiCad's GLB is in metres (glTF convention). The viewer therefore never assumes a unit:
  it centres the model and scales its largest dimension to 100 scene units inside a wrapper group
  (the offset must live on the child so the group's scale moves it too), then fits the camera to the
  bounding sphere (`radius / sin(fov/2) * 1.25`). Top/bottom presets use up = ∓Z, otherwise the
  view comes out mirrored.

### Vias

- The pick index reports every via's real geometry from pcbnew: `x, y` (`GetPosition`, the hole
  centre), `size` (`GetWidth`, outer diameter) and `drill` (`GetDrillValue`). Covered by
  `test_via_geometry_is_exact_and_reaches_the_model`, which adds a 0.8/0.4 mm via and asserts an
  exact round-trip.
- The GLB includes via copper (`--include-tracks`) and cuts the hole in the board body
  (`--cut-vias-in-body`); `--fill-all-vias` is deliberately absent so the hole stays open. The same
  test asserts those flags.

### Model picker (`src/ModelPicker.tsx`, `GET|PUT /api/models`)

- Models come from the configured `HARNESS_MODEL`, `HARNESS_MODELS` (comma list) and the verified
  defaults (`openai:anthropic/claude-sonnet-5`, `google:gemini-3.1-pro-preview`).
- `PUT /api/models` rebuilds and re-enters the agent (`AgentHost.swap`) without a server restart,
  persists the choice in the `settings` table (schema v3) and refuses with 409 while a run is
  streaming. Build/key errors come back as a readable 400 shown next to the picker.
- Each run stores the model that produced it (the runs table's `model` column now uses the live
  selection), so history stays truthful after switching.

### Projects (`src/ProjectsDialog.tsx`)

- `GET /api/designs` now returns `updated_at`, `latest {sha, short, subject}`, `thumbnail`
  (`.../rev/<head>/svg/front`) and `goal_summary`, sorted newest first.
- The header's **Projects** dialog lists every design with its thumbnail, branch/head, last change
  and goal; clicking one switches project (design, threads, versions, goal and agent state reload).
- **New project** accepts an uploaded `.kicad_pcb` (`board_text`), the bundled demo fixture
  (`from_fixture`) or a server path (`from_board`), plus an optional goal summary; the API validates
  the goal and writes an import commit.

### Versions (`src/tabs/VersionsTab.tsx`, `GET /api/designs/{d}/versions[/{sha}/summary]`)

- Every row shows a thumbnail of that revision (`.../rev/<sha>/svg/front`, real revision content, not
  the working copy), the commit subject/metrics and, once expanded, an inline change view.
- `GET /versions/{sha}/summary` diffs the revision against its first parent and returns
  `{diff, counts, summary, cached}`. Counts cover added/removed/moved entities, net changes,
  **outline** resizes and **silkscreen** label moves (reference-text positions from the index).
- The few-word note is generated by the selected model (`kicad_harness/agent/summarize.py`, at most
  six words) from the real diff and cached in `version_meta.summary` — so it is stable and costs one
  call per revision. Provider errors are returned in `error` instead of failing the endpoint.
- `show diff on board` reuses the entity diff overlay for the same parent.

### Grid + scale legend (`BoardViewer.tsx`)

- KiCad-style dot grid over the board area with an adaptive step (0.5/1/2/5/10 mm sized to stay
  18-40 px apart at the current zoom), toggled by the toolbar **Grid** button and persisted in
  `localStorage["kicad-harness-grid"]`.
- Bottom-left legend shows a scale bar with a round millimetre length (largest that fits ~120 px)
  and the current grid step (`[data-testid=scale-legend]`); both are hidden on the 3D tab.


### Board outline (Edge.Cuts) disappearing

Three separate causes, all fixed:

1. **Clipped by the SVG viewport.** kicad-cli plots Edge.Cuts exactly on the viewBox boundary
   (`d="M 0,0 H W V H Z"` with a 29.9974 x 19.9898 viewBox), so the stroke's outer half was clipped
   and the right/bottom edges vanished. The injected `<svg>` now gets `overflow="visible"`.
2. **Fit used a window-size guess.** `refreshDesignData(full)` calls `fitView()` without arguments,
   which fell back to `innerWidth * 0.6` / `innerHeight * 0.8`; on a 1500x950 window that fitted the
   board ~6 px wider than the 980 px pane, pushing both vertical edges outside the clipped viewport.
   `store.boardPx` now records the measured viewport (set from `BoardViewer`'s ResizeObserver) and
   `fitView()` prefers it, so a fitted board is fully inside with a margin.
3. **Sub-pixel stroke.** KiCad's 0.05 mm outline is ~0.25 px when fitted. `BoardViewer` passes
   `--edge-stroke` (1.25 px expressed in board millimetres for the current zoom) and CSS applies it to
   the Edge.Cuts elements. Note `vector-effect: non-scaling-stroke` does **not** work here — neither
   from CSS nor as an inline attribute — because the SVG sits under a CSS `transform: scale()` on the
   stage; that made the outline paint at 1.2 *user units* (~40 px) instead of 1.2 px.

`e2e/walk.mjs` now asserts the outline's screen rect is inside the pane and that it has a stroke.


### Review-comment pins

The big numbered circles were review threads, not board features — and they were rendered in board
millimetres, so they grew with zoom (a 0.85 mm radius ballooned at 2x).

- Pins are now screen-constant: `ThreadPin` divides the pixel sizes by the current px-per-mm, so the
  bubble stays ~21 px at any zoom.
- Each pin is a speech bubble above the anchor: severity-coloured circle with a white ring and drop
  shadow, the thread number, a short leader down to the exact anchored point (with a dot there), and
  a `<title>` reading "Comment #N · severity · status". Addressed/resolved comments render at reduced
  opacity so finished notes recede; hovering or clicking opens the thread in the Review tab.
- A toolbar **Comments** toggle (`[data-testid=toggle-pins]`) hides all pins; the choice persists in
  `localStorage["kicad-harness-pins"]`.
- `DELETE /api/designs/{design}/threads/{number}` (204/404) deletes a thread with its replies and
  status log (`Review.delete`). `e2e/walk.mjs` now deletes the throwaway thread it creates, so the
  demo board no longer accumulates test comments; the two leftovers from earlier runs were removed.


### Bifrost models (and why the list is short)

The model picker lists everything the configured routes can actually run:

- `openai:` /`google:` entries (the routes the harness documents), plus
- **Bifrost** entries as `bifrost:<id>`, resolved to an `OpenAIChatModel` pointed at
  `config.bifrost_base_url` with `config.bifrost_api_key`.

Config: `BIFROST_BASE_URL` (no default; set it to your gateway's OpenAI-compatible base URL),
`BIFROST_API_KEY`, and `BIFROST_MODELS` (comma list) to override the catalog. Without a base URL no
Bifrost entries are offered.
Without an override the catalog is the gateway's `/v1/models` chat entries (OCR/ASR/embeddings/rerankers
are filtered out) plus a few known aliases such as `deepseek-flash-latest`. `kicad_harness/agent/models.py`
owns this; `resolve()` builds the provider for `bifrost:` ids.

A gateway only accepts the model ids it is configured for; ids it rejects are not advertised. The
catalog is whatever your gateway lists, so the exact names depend on your setup.

### Goals are written by the agent

Hand-writing criteria defeated the point, so:

- The agent has a `set_goal(summary, criteria)` tool. Its instructions tell it to turn a natural-language
  goal into criteria itself (inspecting refs/regions/sizes first), save them, then `check`.
- The Goal tab's primary flow is one textarea plus **Let the agent define the criteria**, which sends that
  request. The saved criteria appear as chips (read-only) and are editable via *edit criteria manually*
  or the *Advanced: goal JSON* section; the live evaluation table stays below.
- The Agent tab shows the current goal in a chip (`agent-goal-chip`) that opens the Goal tab.
- Saving the goal commits `goal.json`, so the existing SSE `version` event refreshes the UI.

### Stopping runs that outlive their tab

A streaming run kept going after the browser disconnected, and a fresh tab could not stop it (the Stop
button only aborts the client's agent object). Added `POST /api/designs/{design}/stop`, which sets a
per-design stop flag that the stream loop checks; the UI's Stop now calls it as well as `agent.abortRun()`.
At startup `runs.interrupt_stale()` marks leftover `running` rows `interrupted` (with a RUN_ERROR event)
so a crash cannot leave the picker 409-blocked for ever.

### Why clicking 3D looked like a crash

Two separate faults, found by driving the real app in chromium:

1. **The app never finished booting.** `/api/models` called the Bifrost gateway synchronously
   (`urllib`) inside an async endpoint. The gateway's `/v1/models` takes ~5 s here, so the call
   blocked the server's event loop and `/api/designs` queued behind it (measured 5–10 s per models
   request). `initStore()` awaits that request, so the UI sat uninitialised — header `— @ —`, no
   board, and the 3D tab stuck on `loading` because `Viewer3D` has no design to load. Two catalog
   calls (StrictMode mounts the picker twice) doubled the stall.
   Fix: `models.bifrost_ids()` caches ids for 5 minutes, `/api/models` runs `catalog()` in
   `asyncio.to_thread`, and the server warms the cache at startup. Warm, `/api/models` is ~1 ms and
   `/api/designs` is unaffected.

2. **A genuine unmount.** `new THREE.WebGLRenderer()` throws when a WebGL context cannot be created
   (GPU blocklist, exhausted contexts, remote sessions). Thrown from a `useEffect`, React unmounts
   the entire tree — clicking 3D blanked the page. Hardened:
   - renderer creation in `try/catch` → the viewer's error state with the static render fallback;
   - the GLTF `onLoad` body in `try/catch`, plus a non-finite-scale guard;
   - `webglcontextlost` / `webglcontextrestored` handlers: no mid-frame throw, a readable message, resume;
   - `renderer.forceContextLoss()` on unmount and texture disposal, so repeated switching stays under
     Chrome's context limit;
   - an `ErrorBoundary` around `Viewer3D` and another around `<App/>`, so no renderer failure can
     produce a blank page.

Verified in chromium: 3D opens `ready` and orbiting changes pixels; six Front↔3D round trips leave
exactly one canvas and one viewer; switching projects while on the 3D tab works; with
`getContext("webgl")` forced to `null` the header survives and the viewer shows "3D model
unavailable… WebGL is not available in this browser" with no page errors. Screenshots
`e2e/screenshots/33-3d-orbit.png` and `34-3d-no-webgl.png`. `tests/test_agent.py` now also pins the
catalog cache and its gateway-outage fallback (184 tests).
