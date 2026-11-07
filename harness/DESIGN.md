# kicad-harness design

An agent edits a KiCad 10 board in a loop toward a goal while a user watches, chats, comments on specific entities, and rolls versions back. This document is the contract between the parts. The research behind the choices is in `../research/agent-harness-2026.md`.

## Layout

```
harness/
  pyproject.toml          # package kicad-harness (Python 3.12), installed editable into harness/.venv
  kicad_harness/
    config.py             # paths and env (see Config)
    engine/               # CAD engine: everything that touches KiCad
      worker.py           # runs under KiCad's Python 3.9; JSON-lines RPC around SWIG pcbnew
      client.py           # Engine: spawns and talks to worker.py
      kicad_cli.py        # kicad-cli wrappers: drc, export svg, render (3D), stats
      render.py           # agent/user images: 2D SVG crops with highlights/labels, 3D framed renders
      geometry.py         # shapely measurements over the pick index
      freerouting.py      # autoroute through DSN/SES with the vendored Java 25 + Freerouting jar
      artifacts.py        # per-board-content cache: index, svgs, drc, stats, renders
    store/
      db.py               # SQLite connection + schema (one file: $HARNESS_HOME/harness.db)
      workspace.py        # git-backed design workspace
      review.py           # threads, anchors, tags, statuses, carry-forward
      runs.py             # agent runs + AG-UI event log
    agent/
      state.py            # DesignState (AG-UI shared state)
      goal.py             # goal spec + evaluator
      agent.py            # Pydantic AI agent + tools
    server.py             # FastAPI: AG-UI endpoint, REST API, live SSE
    cli.py                # typer CLI: init, run (headless), versions, threads, serve
  tests/                  # pytest (real KiCad; no network unless marked)
  web/                    # Vite + React + CopilotKit, plus Node CopilotRuntime server
  scripts/fetch_vendor.sh # downloads Java 25 JRE + Freerouting jar into harness/.vendor/
```

Not committed: `harness/.venv/`, `harness/.vendor/`, `harness/var/` (workspaces, DB, caches), `web/node_modules/`, `web/dist/`.

## Config (`kicad_harness/config.py`)

Everything comes from one `Config` dataclass, with defaults from the environment:

| Field | Env | Default |
|---|---|---|
| `home` | `HARNESS_HOME` | `<repo>/harness/var` |
| `kicad_app` | `KICAD_APP` | `/Applications/KiCad/KiCad.app` |
| `kicad_python` | `KICAD_PYTHON` | `<kicad_app>/Contents/Frameworks/Python.framework/Versions/Current/bin/python3` |
| `kicad_cli` | `KICAD_CLI` | `<kicad_app>/Contents/MacOS/kicad-cli` |
| `footprint_dirs` | `HARNESS_FOOTPRINT_DIRS` (os.pathsep list) | `[<repo>/demo/libs/footprints, <kicad_app>/Contents/SharedSupport/footprints]` |
| `java` | `HARNESS_JAVA` | `harness/.vendor/jre/Contents/Home/bin/java` |
| `freerouting_jar` | `HARNESS_FREEROUTING_JAR` | `harness/.vendor/freerouting.jar` |
| `model` | `HARNESS_MODEL` | `anthropic:claude-sonnet-5-5` |

`config.get()` returns a process-wide instance; tests build their own with a tmp `home`.

Derived paths: `workspaces = home/workspaces`, `cache = home/cache`, `renders = home/renders`, `db_path = home/harness.db`.

## Units and conventions

- Lengths are mm and coordinates are KiCad board coordinates: x right, **y down**.
- Angles are degrees, using KiCad's orientation convention (positive = counter-clockwise on screen).
- Layers use KiCad canonical names: `F.Cu`, `B.Cu`, `F.SilkS`, `B.SilkS`, `Edge.Cuts`, etc.
- Sides are `"front"` / `"back"`.
- Entities are identified by KiCad UUID (`KIID` string). Footprints are also addressable by refdes (`U1`), pads as `REF.PAD` (`U1.3`), nets as `net:NAME`.
- The board file inside a workspace is always `board.kicad_pcb` in KiCad 10 format (written by pcbnew 10).

## Engine

### `worker.py` (runs under KiCad's Python 3.9: no `X | Y` unions, no `match`)

- **Protocol:** one JSON object per line on stdin, one reply per line on stdout. Everything else (pcbnew's wx "traits" assert, logging) goes to stderr.
  - Request: `{"id": 1, "method": "move_footprint", "params": {"board": "/abs/board.kicad_pcb", ...}}`
  - Reply: `{"id": 1, "result": ...}` or `{"id": 1, "error": {"type": "ValueError", "message": "..."}}`
- **Statelessness:** every call names the board file. Read calls load the board, cached on `(path, mtime_ns, size)`. Edit calls load it, edit, `pcbnew.SaveBoard`, and return. A failed edit must not write the file.

| Method | Params (besides `board`) | Result |
|---|---|---|
| `ping` | – | `{"pcbnew": version}` |
| `index` | – | pick index (below) |
| `normalize` | – | Gives every duplicated UUID a fresh one (keeping the first), saves in KiCad 10 format. → `{"replaced": n}` |
| `move_footprint` | `ref, x, y, rotation=None` | `{"ref", "before": {x,y,rotation,side}, "after": {...}}` |
| `rotate_footprint` | `ref, angle` (relative) | same |
| `flip_footprint` | `ref` (front↔back, in place) | same |
| `move_reference_text` | `ref, x, y` (absolute, board coords) | `{"ref", "before": {x,y}, "after": {x,y}}` |
| `delete_tracks` | `nets=None, uuids=None` (both None = all tracks and vias) | `{"deleted": n}` |
| `delete_items` | `uuids` | `{"deleted": n}` (any board item) |
| `add_track` | `net, points [[x,y],...], width=0.25, layer="F.Cu"` | `{"uuids": [...]}` (one segment per consecutive pair) |
| `add_via` | `x, y, net, size=0.6, drill=0.3` | `{"uuid"}` |
| `set_outline` | `width, height, x=0, y=0, corner_radius=0` | Replaces all Edge.Cuts shapes with a rectangle (rounded if r > 0). → `{"bbox": [x,y,w,h]}` |
| `add_zone` | `net, layer, polygon=None, clearance=0.3, min_width=0.25, margin=0.5` | Copper zone; default polygon = board outline inset by `margin`. Filled immediately. → `{"uuid"}` |
| `refill_zones` | – | `{"zones": n}` |
| `add_footprint` | `footprint` (`"Lib:Name"` or bare `Name`, searched in `footprint_dirs`), `ref, value, x, y, rotation=0, side="front", footprint_dirs` | `{"uuid", "ref", "pads": [...]}` |
| `remove_footprint` | `ref` | `{"ref", "uuid"}` |
| `set_pad_net` | `ref, pad, net` (creates the net if new) | `{"ref", "pad", "net"}` |
| `export_dsn` | `path` | `{"path"}` (`pcbnew.ExportSpecctraDSN`) |
| `import_ses` | `path` | `{"tracks": n, "vias": n}` (`pcbnew.ImportSpecctraSES`, then save) |

### Pick index (`index` result; served to the UI as JSON)

```json
{
  "board": {"bbox": [x, y, w, h], "edge_bbox": [x, y, w, h], "layers": ["F.Cu", "B.Cu"], "thickness": 1.6},
  "nets": ["", "GND", "VCC"],
  "footprints": [{"uuid", "ref", "value", "footprint": "R0805", "x", "y", "rotation", "side": "front",
                  "bbox": [x, y, w, h], "courtyard": [[x, y], ...] | null,
                  "ref_text": {"x", "y", "bbox": [x, y, w, h], "visible": true, "layer": "F.SilkS", "height": 1.0}}],
  "pads": [{"uuid", "ref", "pad": "1", "net", "x", "y", "w", "h", "rotation", "shape": "rect|oval|circle|roundrect|custom",
            "layers": ["F.Cu"], "through": false, "drill": 0.0, "polygon": [[x, y], ...]}],
  "tracks": [{"uuid", "net", "layer", "x1", "y1", "x2", "y2", "width"}],
  "vias": [{"uuid", "net", "x", "y", "size", "drill"}],
  "zones": [{"uuid", "net", "layer", "outline": [[x, y], ...], "filled": [[[x, y], ...], ...]}]
}
```

`board.bbox` covers all items; `edge_bbox` is the Edge.Cuts outline. `pads[].polygon` is the pad's copper outline on its first copper layer (from `GetEffectivePolygon`), so measurements and picking are exact.

### `client.py`

`Engine(config)` keeps one worker alive, restarts it if it dies, and serializes calls with a lock. `engine.call(method, board=path, **params)` returns the result or raises `EngineError(type, message)`. There are thin typed wrappers: `engine.index(path)`, `engine.move_footprint(path, ref, x, y, rotation=None)`, and so on.

### `kicad_cli.py`

- `drc(board) -> dict` runs `kicad-cli pcb drc --format json --severity-all --units mm` into a temp file and normalizes it to:
  ```json
  {"counts": {"errors": n, "warnings": n, "unconnected": n},
   "violations": [{"type", "severity", "description", "items": [{"uuid", "description", "x", "y"}]}],
   "unconnected": [{"type", "severity", "description", "items": [...]}]}
  ```
- `export_svg(board, layers, out, mirror=False)` uses board-area pages (`--page-size-mode 2 --exclude-drawing-sheet --mode-single`). Units are mm, with the SVG origin at the top-left of `edge_bbox`. With `mirror`, x' = page_w − x.
- `render_3d(board, out, side="top", rotate=None, zoom=1, pivot=None, width=1600, height=900, quality="basic")`.
- `stats(board) -> dict` comes from `pcb export stats --format json`.

### `render.py` (images for the agent and the UI)

```
render_2d(board_svg: str, index: dict, *, center=None, view_mm=None, px_per_mm=None, width_px=1200,
          highlight=(), labels=True, pins=(), side="front") -> bytes  # PNG
```
- Crops by rewriting the SVG viewBox. Defaults: the whole board, at `width_px` across.
- Draws highlight outlines from index geometry, refdes labels at footprint positions (and net names on highlighted pads), and numbered pins (`{x, y, label}`).
- Rasterizes with cairosvg. Before importing it, set `os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")`.

```
render_3d_view(board, index, *, center=None, view_mm=None, side="top", rotate=None, quality="basic",
               width=1200, height=900) -> bytes
```
- Frames `kicad-cli pcb render` on a point and view height with the measured camera model:
  - `zoom = 1.0355 * max(board_w, board_h) / view_mm`;
  - `pivot = ((x - cx)/10, (cy - y)/10, 0)` in cm from the edge-bbox centre, +Y up;
  - the bottom view mirrors X; never use `--pan`.
- With `rotate`, it frames the whole board (no pivot).

### `geometry.py`

- `resolve(index, ref) -> list[entity]` understands `U1`, `U1.3`, `net:GND`, a UUID, or `x,y`.
- `measure(index, a, b) -> {"distance", "a_point", "b_point", "a", "b"}` gives the edge-to-edge copper distance (0 if touching): pads are polygons, tracks are buffered segments, vias are circles, and footprints are the union of their pads (or the courtyard if `body=True`).
- `clearance(index, net_a, net_b, layer=None) -> same`, the minimum over all copper of two nets.

### `freerouting.py`

`autoroute(engine, board, config, timeout_s=120, passes=None) -> {"routed": bool, "unrouted": n, "tracks": n, "vias": n, "seconds"}`:
- Exports the DSN, runs `java -jar freerouting.jar -de in.dsn -do out.ses -mp <passes> --gui.enabled=false`, then imports the SES.
- Existing tracks are kept, so the caller rips up first if wanted.
- It raises `EngineError("FreeroutingUnavailable")` when Java or the jar is missing.

### `artifacts.py`

`Artifacts(config, engine)`. Every artifact is keyed by `sha256(board bytes)[:16]` (the "board hash"), so a commit and an identical working copy share a cache. Each build happens once per hash, under a lock.
- `materialize(board_bytes) -> (hash, path)` writes `cache/<hash>/board.kicad_pcb`.
- `index(hash)`, `svg(hash, side)` (front: F.Cu, F.SilkS, F.Mask, Edge.Cuts; back: B.Cu, B.SilkS, B.Mask, Edge.Cuts, mirrored), `drc(hash)`, `stats(hash)`.
- `render(hash, spec) -> (png_path, url)` writes `renders/<hash>-<spec_hash>.png`, served at `/api/renders/<file>`.

## Store

### `db.py`

One SQLite file (WAL mode, `check_same_thread=False`, a lock around writes, `busy_timeout` 5 s, writes in `BEGIN IMMEDIATE`). The schema is created idempotently. `Store(config)` exposes:
- `review` and `runs` (below);
- `versions`, the version metadata cache: `get(design, sha) -> {"board_hash", "metrics"} | None` and `put(design, sha, board_hash=None, metrics=None)`, where a `None` field keeps the cached value;
- `history`, the agent's serialized message history per AG-UI thread, as opaque bytes: `save(thread_id, design, data: bytes)` and `load(thread_id) -> bytes | None`.

### `workspace.py` (git CLI via subprocess)

`Workspace(config, name)` lives at `workspaces/<name>/`, a git repo whose default branch is `main`. Names are 1–100 letters, digits, `.`, `_` or `-`, starting with a letter or digit; anything else raises `ValueError`. Tracked: `board.kicad_pcb`, `goal.json`, `.gitignore` (`*.kicad_prl`, `fp-info-cache`, `*-backups/`, `~*.lck`, `.history/`). Git runs with the user's global and system config switched off, so personal settings (signing, hooks, global excludes) can't leak in.

- `Workspace.create(config, name, board_src: Path, goal: dict | None = None, engine=None) -> Workspace`:
  - copies the board, runs `engine.normalize` (skipped when `engine` is `None`), writes `goal.json` (`DEFAULT_GOAL` in `store.workspace`, below), and commits `"Import <src name>"` as author user;
  - builds in a hidden staging directory and renames it into place, so a design appears complete or not at all;
  - a taken name raises `FileExistsError`.
- `Workspace.open(config, name)` raises `FileNotFoundError` if the design is missing; `Workspace.exists(config, name) -> bool`; `Workspace.list(config) -> [names]`.
- `board_path`, `goal() -> dict`, `set_goal(goal, author="user") -> Version | None` (commits `goal.json` alone, leaving other changes uncommitted).
- `commit(message, *, author: "agent"|"user", trailers: dict[str,str] = {}) -> Version | None`:
  - stages everything and commits only if something changed;
  - authors are `harness-agent <agent@harness.local>` and `harness-user <user@harness.local>`;
  - trailers become `Harness-Run`, `Harness-Tool`, `Harness-Tool-Call`, `Addresses: #3`, etc., appended to the message as a git trailer block. A list value gives one line per item.
- `head() -> sha` (`None` before the first commit), `branch() -> str`, `branches() -> [{name, head, current}]`.
- `log(branch=None, limit=200) -> [Version]`, newest first. An unknown branch raises `ValueError`.
  - `Version = {sha, short, message, subject, author: "agent"|"user", time (unix), parents: [sha], branch, trailers: {}}`.
  - `message` is the whole message, trailers included. `author` is `agent` only for the agent's email. Repeated trailer keys are joined with `, `.
- `file_at(sha, path="board.kicad_pcb") -> bytes`. `sha` is any commit-ish, or `"working"` for the working copy. A missing file raises `FileNotFoundError`.
- `restore(sha, *, author="user", message=None, trailers=None) -> Version` restores the tree of `sha` as a new commit on the current branch, so history is never rewritten.
  - The commit carries `Harness-Restore: <full sha>` plus any `trailers`; the default message is `Restore <short>`.
  - If the tree already matches, no commit is made and the head Version is returned.
- `create_branch(name, from_sha=None) -> None` creates and switches (`from_sha` defaults to head); `switch(name)`. Bad, taken or unknown names raise `ValueError`.
- `restore`, `create_branch` and `switch` first commit any uncommitted changes on the current branch (author user, `"Save working changes before …"`), so nothing is lost.
- `dirty() -> bool`. Ignored files don't count.
- `lock` is held by every mutation (engine edit + commit, restore, switch). It is re-entrant and used like a `threading.RLock`, and every `Workspace` on the same path shares it. The outermost holder also `flock`s `.git/harness.lock`, so a CLI run and the server serialize too. Reads don't take it.

### `review.py`

**Thread:**
```json
{"id": 12, "design": "blinker", "number": 3, "kind": "comment|drc|agent", "source": "user|agent|checker",
 "severity": "info|suggestion|warning|must_fix", "tags": ["placement"], "status": "open|addressed|resolved|wontfix|reopened",
 "title": "short", "body": "text", "author": "user", "created_at": 1792190000.0, "created_sha": "abc123...",
 "anchor": Anchor, "view": {"side": "front", "center": [x, y], "view_mm": 10} | null,
 "addressed_sha": null, "resolved_sha": null,
 "replies": [{"id", "author", "body", "created_at", "sha"}],
 "status_log": [{"status", "by", "at", "sha", "note"}],
 "resolved_anchor": {"state": "exact|moved|remapped|detached", "x", "y", "uuid", "layer", "detail"} | null,
 "updated_at": 1792190000.0}
```

**Anchor:**
```json
{"kind": "footprint|pad|track|via|zone|point|region", "uuid": "..." | null, "ref": "U1" | null, "pad": "3" | null,
 "net": "GND" | null, "layer": "F.Cu" | null, "side": "front",
 "x": 9.1, "y": 12.4,
 "entity_x": 9.0, "entity_y": 12.57,
 "region": [x1, y1, x2, y2] | null}
```
`x, y` is the click point; `entity_x/entity_y` is the entity's centre at creation: the index `x, y` for footprints, pads and vias, the midpoint for tracks, and the centre of the outline's bounding box for zones. `create` stores every key of this shape; for entity anchors a missing click point defaults to the entity centre and vice versa, and a region's click point defaults to its centre.

**API (`Review(store)`):**
- `create(design, sha, anchor, body, *, title=None, tags=(), severity="suggestion", kind="comment", source="user", author="user", view=None) -> Thread`.
  - `number` is per design, starting at 1.
  - The thread starts `open`, which is also the first `status_log` entry.
  - An unknown severity, kind, source or anchor kind raises `ValueError`, as does an anchor with nothing to identify it.
- `get(id)` and `by_number(design, number)` return a Thread or `None`.
- `list(design, status=None, sha=None) -> [Thread]`, oldest first.
  - `status` is one status or a list; `"open"` also matches `"reopened"`.
  - `sha` keeps threads linked to that version: created, replied to, or changed status there.
  - Stored threads come back with `resolved_anchor: null`.
- `reply(id, body, *, author, sha=None) -> Thread`.
- `set_status(id, status, *, by, sha=None, note=None) -> Thread` records the change in the log. `addressed` sets `addressed_sha`, `resolved` sets `resolved_sha`, and `open`/`reopened` clear both. An unknown id raises `KeyError`.
- `last_updated(design) -> float` is the newest `updated_at`. Every change to a thread moves it forward, strictly, so the live stream never misses one.
- `resolve_anchor(thread, index) -> resolved_anchor` (also a module function; it takes a Thread or a bare Anchor) carries anchors forward:
  1. Find the UUID in the index. Same entity position (±0.001) → `exact`; otherwise `moved`, with the pin at new centre + (click − entity centre). An anchor without a UUID is identified by its `ref` (and `pad`), so for it a match in step 2 is also `exact` or `moved`.
  2. Otherwise, footprints by `ref` and pads by `ref`+`pad` → `remapped`, keeping the click offset.
  3. Otherwise, tracks and vias by the nearest item on the same net (and layer) within 2 mm of the click → `remapped`, pinned to the nearest point of the track centreline or to the via centre.
  4. `point` and `region` are always `exact`.
  5. Anything else → `detached`, keeping the original x, y.

### `runs.py`

- `start(design, *, thread_id, run_id, model, prompt, source="ui") -> run` (`source` is `"ui"` or `"cli"`) marks the run `running`.
- `event(run_id, event_dict) -> seq` assigns seq 1, 2, … per run (atomic across processes). It also takes a pydantic AG-UI event, dumped with its camelCase aliases.
- `finish(run_id, status, usage=None, error=None) -> run` sets the final status, for example `finished`, `error`, `interrupted` or `cancelled`.
- `list(design)` returns runs newest first; `get(run_id)` returns a run or `None`.
- `events(run_id, after_seq=0, limit=None) -> [{"seq", "at", "type", "event"}]`, where `event` is the logged dict.
- `last_updated(design) -> float` moves when a run starts or finishes, not on every event.
- A run is `{run_id, design, thread_id, model, prompt, source, status, started_at, finished_at, updated_at, usage, error, event_count}`.
- Every AG-UI event the server or CLI streams is logged, so the UI's run viewer can replay any run.

### `steering.py` (messages to a running agent)

CopilotKit can't add a message to an AG-UI run that's already streaming: its next message waits until the run ends. So "chat with the agent while it works" goes through a side channel instead:
1. The UI (`POST /api/designs/{d}/steer {message}`) or the CLI (`kicad-harness steer NAME "..."`) stores the message as pending.
2. The agent's `before_model_request` hook claims every pending message of its design (`steering.take(design, run_id)`), passes them to `RunContext.enqueue`, and emits a `SteeringDelivered` CUSTOM event.
3. The model sees the message at its next step. If the agent was about to finish, Pydantic AI redirects it to answer instead.

Messages with no `run_id` go to whichever run asks first, which is the next run if none is active. API: `push(design, message, author="user", run_id=None)`, `take(design, run_id)`, `list(design)`, `last_updated(design)`.

## Goal (`agent/goal.py`)

`goal.json` in the workspace:
```json
{"summary": "free text intent shown to the agent",
 "criteria": [
   {"id": "drc", "type": "drc_errors", "max": 0},
   {"id": "routed", "type": "unconnected", "max": 0},
   {"id": "size", "type": "board_size", "max_w": 40, "max_h": 60},
   {"id": "edge", "type": "edge_clearance", "min_mm": 1.0},
   {"id": "near", "type": "max_distance", "a": "C3", "b": "U1.8", "max_mm": 3},
   {"id": "apart", "type": "min_distance", "a": "U1", "b": "D1", "min_mm": 5},
   {"id": "inside", "type": "within_region", "ref": "D1", "region": [x1, y1, x2, y2]},
   {"id": "review", "type": "open_threads", "severities": ["must_fix"], "max": 0},
   {"id": "warnings", "type": "drc_warnings", "max": 5}
 ],
 "budget": {"max_requests": 120, "max_minutes": 30}}
```
The default goal is `drc` + `routed` + `review`.

`evaluate(goal, index, drc, threads) -> {"met": bool, "criteria": [{"id", "type", "passed", "value", "detail"}]}`.

## Agent (`agent/agent.py`)

- **Construction:** `build_agent(model=None) -> Agent[Deps, str | DeferredToolRequests]`. `Deps = StateDeps[DesignState]`, plus server-side handles (`HarnessContext`: config, engine, artifacts, store, run id), passed through `deps` or a contextvar.
- **Shared state** (`agent/state.py`, sent as AG-UI state):
```json
{"design": "blinker", "branch": "main", "head": "sha", "revision": 7, "board_hash": "ab12...",
 "goal": {"met": false, "criteria": [...]} | null, "last_render": {"url", "caption", "view"} | null,
 "highlight": {"uuids": [], "refs": [], "nets": []} | null, "selection": Anchor | null,
 "loop": false, "supervised": false}
```
  - The frontend sets `design`, `selection`, `loop` and `supervised`.
  - Tools update the rest and attach `StateSnapshotEvent`s to their `ToolReturn`, so the UI's board view reloads when `board_hash` changes.
- **Auto-commit:** every editing tool, on success, commits one version (author agent, trailers: run, tool, tool call) and bumps `revision`/`head`/`board_hash`.
- **Supervised mode:** when `supervised` is true, `delete_tracks`, `autoroute`, `set_board_outline`, `remove_footprint` and `restore_version` raise `ApprovalRequired` unless `ctx.tool_call_approved`.

| Tool | Purpose / returns |
|---|---|
| `design_summary()` | Board size, components (ref, value, x, y, rotation, side), nets, DRC counts, goal status, open threads, head |
| `query(kind, ref=None, net=None, region=None)` | Compact entity list from the index |
| `measure(a, b)` | geometry.measure |
| `check()` | DRC (normalized, top 30 items) + goal evaluation. Updates `state.goal` |
| `render_view(target=None, x=None, y=None, view_mm=None, mode="2d", side="front", rotate=None, highlight=None, labels=True)` | **Image to the model** (`BinaryContent`) + caption. Sets `state.last_render` |
| `list_feedback(status="open")` | Threads with anchors resolved to the current version (number, severity, tags, entity, location, body, replies) |
| `reply_to_thread(number, message)`, `mark_addressed(number, note)` | Review actions as the agent. `mark_addressed` records the current head |
| `move_footprint(ref, x, y, rotation=None)`, `rotate_footprint(ref, angle)`, `flip_footprint(ref)` | Placement edits |
| `move_reference_text(ref, x, y)` | Silkscreen |
| `delete_tracks(nets=None)`, `delete_items(uuids)` | Rip-up |
| `add_track(net, points, width=0.25, layer="F.Cu")`, `add_via(x, y, net)` | Manual routing |
| `autoroute(timeout_s=120)` | Freerouting |
| `set_board_outline(width, height, corner_radius=0)` | Outline |
| `add_copper_zone(net, layer, margin=0.5)`, `refill_zones()` | Pours |
| `add_footprint(footprint, ref, value, x, y, rotation=0, side="front", pad_nets={})`, `remove_footprint(ref)`, `set_pad_net(ref, pad, net)`, `find_footprints(query)` | Parts |
| `list_versions(limit=20)`, `restore_version(sha)` | History |

- **Frontend tools**, defined in the browser and offered to the model through AG-UI:
  - `focus_view(target, view_mm=None)`
  - `highlight(refs=[], nets=[], uuids=[])`
  - `show_version(sha)`
  - `compare_versions(a, b)`
- **Goal loop:** when `state.loop` is true (or the CLI runs with `--loop`), an `output_validator` re-evaluates the goal before the agent may finish. If it isn't met, it raises `ModelRetry("Goal not met: <failing criteria>. Keep working.")`. Budgets come from `goal.budget` as `UsageLimits`. `output_retries` is large (50), so retries are bounded by the budget, not the retry count.
- **History:** the server keeps each thread's message history (SQLite, keyed by AG-UI `threadId`) and passes it to the run, rather than trusting history replayed by the client.

## Server (`server.py`, FastAPI on 127.0.0.1:8100)

`POST /agui` is the AG-UI endpoint (CopilotRuntime → here). The design comes from `RunAgentInput.state.design`. Every event is logged to `runs`.

REST, under `/api`:

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/designs` | – | `[{name, branch, head}]` |
| POST | `/api/designs` | `{name, from_board, goal?}` | design |
| GET | `/api/designs/{d}` | – | `{name, branch, head, dirty, goal, board_hash, summary: {drc counts, size, components}}` |
| GET | `/api/designs/{d}/versions` | `branch?, limit?` | `[Version + {board_hash, metrics: {errors, warnings, unconnected}}]` (metrics computed lazily + cached) |
| POST | `/api/designs/{d}/versions/{sha}/restore` | – | Version |
| GET/POST | `/api/designs/{d}/branches` | POST `{name, from_sha}` | branches / created |
| POST | `/api/designs/{d}/branches/{name}/switch` | – | `{branch, head}` |
| GET | `/api/designs/{d}/rev/{rev}/index` | rev = sha \| `working` | pick index (+ `board_hash`) |
| GET | `/api/designs/{d}/rev/{rev}/svg/{side}` | front \| back | `image/svg+xml` |
| GET | `/api/designs/{d}/rev/{rev}/drc` | – | normalized DRC |
| GET | `/api/designs/{d}/rev/{rev}/render3d` | `side, rotate, x, y, view_mm, quality` | PNG |
| GET | `/api/designs/{d}/rev/{rev}/measure` | `a, b` (U1, U1.3, net:GND, uuid, "x,y") | `{distance, a_point, b_point, a, b}` |
| GET | `/api/designs/{d}/diff` | `a, b` | `{added: [entity], removed: [entity], moved: [{uuid, kind, ref, from, to}], net_changed: [...]}` (by UUID over the indexes) |
| GET | `/api/designs/{d}/threads` | `status?, rev?` | threads (with `resolved_anchor` for `rev`, default `working`) |
| POST | `/api/designs/{d}/threads` | `{sha?, anchor, body, title?, tags, severity, view?}` | thread |
| POST | `/api/threads/{id}/replies` | `{body}` | thread |
| POST | `/api/threads/{id}/status` | `{status, note?}` | thread |
| GET/PUT | `/api/designs/{d}/goal` | PUT body = goal | goal (PUT commits it) |
| GET | `/api/designs/{d}/goal/status` | `rev?` | evaluation |
| POST | `/api/designs/{d}/steer` | `{message, run_id?}` | queued steering message |
| GET | `/api/designs/{d}/steer` | `limit?` | recent steering messages (with `delivered_run`) |
| GET | `/api/designs/{d}/runs` | – | runs |
| GET | `/api/runs/{id}/events` | `after?` | events |
| GET | `/api/renders/{file}` | – | PNG |
| GET | `/api/designs/{d}/events` | – | **SSE** live stream (below) |

The live stream polls the workspace every 0.5 s: HEAD sha, branch, the board file's (mtime, size), and the max `updated_at` of threads and runs. It emits:
- `{"type": "board", "board_hash", "revision"}`
- `{"type": "version", "head", "branch"}`
- `{"type": "threads"}`
- `{"type": "run", "run_id", "status"}`
- `{"type": "steer"}`

The UI therefore updates live even while a headless CLI run edits the design from another process.

## CLI (`cli.py`, entry point `kicad-harness`)

- `init NAME --from BOARD [--goal goal.json]`
- `run NAME [--prompt TEXT] [--loop/--no-loop] [--model M] [--supervised]` runs the agent headless and prints a readable event stream: reasoning, tool calls with args, results, commits and goal status. Exits 0 when the goal is met.
- `goal NAME [--set FILE]`, `versions NAME`, `threads NAME`, `comment NAME --ref U1 [--pad 3] --body ... --severity must_fix --tag placement`, `restore NAME SHA`.
- `serve [--port 8100]`.

## Web (`web/`)

- **Topology:** Vite (5173) → Node server (8101: CopilotRuntime at `/copilotkit` with one `HttpAgent` named `kicad` → `http://127.0.0.1:8100/agui`; `/api` proxied to 8100).
- **Layout:** the board viewer is the main area. The right side has tabs for Agent (chat), Review (threads), Versions and Goal.
- **Viewer:**
  - The KiCad SVG for the selected revision (front/back), with an overlay built from the pick index: invisible hit shapes with `pointer-events: fill` (tracks use `stroke`).
  - Zoom and pan; hover highlight; select mode and comment mode.
  - Pins for threads (numbered, coloured by severity, hollow when resolved).
  - Agent highlights from `state.highlight`, and a diff overlay against another version (added green, removed red, moved amber).
  - A 3D tab that shows `render3d` images with orientation controls.
- **Comment flow (Fictiv-style):**
  1. Click an entity; it highlights and a popover shows its identity (ref, pad, net, layer).
  2. Enter the body, pick tags (placement, routing, clearance, silkscreen, DFM, mechanical, thermal, cost, question, or free text) and a severity (info, suggestion, warning, must fix).
  3. Save. The thread is stored against the version being viewed.
  4. Threads list with filters; clicking one focuses its anchor.
  5. Status chips: open, addressed, resolved, won't fix, reopened. The user resolves or reopens; the agent marks threads addressed.
  6. "Ask agent to address open comments" sends a chat message.
- **Versions:** a timeline for the current branch (author icon, subject, time, DRC metrics, linked thread numbers) with view, compare-with-current, restore, branch-from-here and a branch switcher. Viewing an old version shows its board and the pins resolved against it.
- **Agent tab:** chat with reasoning, tool-call cards (render images inline; edits show before/after; checks show pass/fail), approvals for supervised tools, and loop/supervised toggles. Frontend tools drive the viewer. `useAgentContext` shares the current selection and viewed version.

### UI specification (what "done" looks like)

**Header:**
- Design picker (`GET /api/designs`), branch name and head short SHA.
- A run indicator: idle, or running with elapsed time, plus a Stop button (`copilotkit.stopAgent` / `agent.abortRun`).
- Toggles: **Goal loop** (`state.loop`) and **Supervised** (`state.supervised`).

**Board viewer (main area)**, with tabs **Front**, **Back** and **3D**.
- **2D view:**
  - Shows `GET .../rev/{rev}/svg/{side}` as a background (an inline SVG or `<img>` sized in mm).
  - Every layer of both sides is drawn at once in one composite, as KiCad does: each layer is
    plotted separately (`/svg/{side}?layers=F.Cu`, cached per board hash and subset) and stacked
    B.Cu→B.Mask→B.SilkS→F.Cu→F.Mask→F.SilkS→Edge.Cuts. Back layers are requested with
    `mirror=false`, so they stay at board coordinates and register with the front layers; the
    back side therefore reads through the board, mirrored, and the two sides line up.
  - A KiCad-style panel on the right lists both sides' layers and the shared Edge.Cuts, and
    toggles each one (persisted); hidden layers are not painted and their entities are not
    pickable. The Front/Back buttons flip the finished stack as a whole: flipping mirrors every
    layer together and reverses the paint order, so the side facing the viewer is on top.
    `?tab=bom|schematic|board` in the page URL selects the main section.
  - Draws an overlay `<svg>` in the same mm coordinate system, built from `GET .../rev/{rev}/index`:
    - footprints: bbox rects, visually invisible;
    - pads: their polygons;
    - tracks: lines with `stroke-width` = track width;
    - vias: circles;
    - zones: their outline.
  - Hit-testing uses `pointer-events: fill` (`stroke` for tracks). The back side mirrors x (`x' = page_w − x`, page = `edge_bbox` plus the 0.025 mm outline stroke).
  - Wheel zoom around the cursor, drag to pan, and a Fit button. Hovering an item outlines it, with a tooltip of kind, ref.pad, net and layer.
- **Modes:**
  - **Select:** click picks the top item (pad over footprint over track over zone). The selection becomes `useAgentContext` and `state.selection`.
  - **Comment:** click picks, then opens the composer.
  - **Measure:** click two items; `GET .../rev/{rev}/measure?a=&b=` shows the distance and nearest points.
  - Clicking empty board makes a `point` anchor.
- **Pins:**
  - Every thread gets a numbered pin at `resolved_anchor.x/y` for the revision being viewed.
  - Colour by severity: info grey, suggestion blue, warning amber, must fix red. Resolved and won't-fix pins are hollow.
  - Detached threads are listed separately.
  - Clicking a pin opens its thread.
- **Agent highlight:** `state.highlight` and the `highlight` frontend tool draw a cyan outline. `focus_view` zooms to its target.
- **Live updates:** subscribe to `GET /api/designs/{d}/events` (EventSource). On `board`/`version`, refetch the working index and SVGs (cache-bust with `board_hash`), versions and goal status. On `threads`, refetch threads; on `run`, refresh the activity list.
- **Old versions:** a banner reads "Viewing <short> · read-only · Back to latest"; comments made there are stored against that SHA.
- **Diff toggle:** compare the viewed revision with another (default: its parent) using `GET .../diff`. Added items are outlined green, removed ones red (drawn from the other index), and moved ones amber with an arrow from the old to the new position.
- **3D tab:** `GET .../rev/{rev}/render3d` with presets (top, bottom, iso = rotate −45,0,45, iso-back) and a zoom slider. Click → focus uses `x/y/view_mm`.

**Review tab (Fictiv-style critique):**
- **Composer:** the picked entity's identity (kind, ref.pad, net, layer, x/y), body text, tag chips (presets + free text), and a severity selector (info / suggestion / warning / must fix). Comment → `POST .../threads` with `sha` = the viewed revision and `view` = the current zoom/centre.
- **Thread list:** filters by status (open+reopened / addressed / resolved+won't fix / all), severity and tag. Cards show the pin number, severity, tags, status chip, entity label, version, body, replies, a reply box, actions (resolve, reopen, won't fix) and "show on board".
- **"Ask the agent to address open comments":** sends a chat message, or a steering message if a run is active.

**Versions tab:**
- Shows `GET .../versions` (metrics badges appear as they're computed) and the branch switcher.
- Per version: view, compare with the current version, restore, and branch from here (asks for a name).
- Restoring or switching refreshes everything.

**Agent tab (CopilotKit):**
- **Chat panel:** CopilotChat or headless hooks, showing reasoning.
- **Tool cards:**
  - `render_view`: the image from `result[0].url` plus its caption.
  - `check`: a criteria pass/fail table and DRC counts.
  - Editing tools: one line with the new version short SHA, which links to that version.
  - `list_feedback`: thread chips.
  - Everything else: a default card (name, args, result).
- **Approvals:** an approval card (Approve / Reject) when supervised tools pause.
- **"Run goal" button:** sends "Work toward the goal; stop when every criterion passes." with loop on.
- **Steering:** while a run is active, the input posts to `POST /api/designs/{d}/steer` and shows "queued" until the delivery event or SSE `steer` confirms it.
- **Frontend tools:** `focus_view(target, view_mm?)`, `highlight(refs?, nets?, uuids?)`, `show_version(sha)`, `compare_versions(a, b)`.

**Activity tab** (`GET .../runs`, `GET /api/runs/{id}/events?after=`):
- Replays any run, UI or CLI, as a transcript: reasoning, text, tool calls (args and results, with render images), approvals, steering deliveries, errors.
- Tails the active run live.
- This is how a user inspects the agent after the fact, and it keeps working when long runs strain the chat panel.

**Goal tab:**
- The goal summary and live criteria (from `state.goal` or `GET .../goal/status`).
- A JSON editor that saves with `PUT .../goal` and shows validation errors.
- A "Run goal loop" button.

## Ports and processes

| Process | Port | Start |
|---|---|---|
| FastAPI | 8100 | `harness/.venv/bin/kicad-harness serve` |
| Node runtime | 8101 | `npm --prefix harness/web run server` |
| Vite | 5173 | `npm --prefix harness/web run dev` |
