"""FastAPI service: the AG-UI endpoint for the agent, the REST API for the UI, and live design events.

See DESIGN.md, "Server".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings
from pydantic_ai.ui.ag_ui import AGUIAdapter

from kicad_harness.agent import models as model_catalog

import kicad_harness.agui_compat

log = logging.getLogger(__name__)

kicad_harness.agui_compat.apply_pydantic_ai_agui_patch()

from kicad_harness.agent import goal as goals
from kicad_harness.agent.agent import (
    HarnessDeps,
    Services,
    _snapshot,
    current_model,
    get_agent,
    set_model as set_agent_model,
    usage_limits_for,
)
from kicad_harness.agent.state import DesignState

SEVERITIES = ("info", "suggestion", "warning", "must_fix")


def _svc(request: Request) -> Services:
    return request.app.state.services


def _workspace(svc: Services, name: str):
    from kicad_harness.store.workspace import Workspace

    if not Workspace.exists(svc.config, name):
        raise HTTPException(404, f"no design {name!r}")
    return Workspace.open(svc.config, name)


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    import dataclasses

    return dataclasses.asdict(obj)


_REF_RE = re.compile(r"^(\D*?)(\d+)(.*)$")


def estimate_tokens(payload: Any) -> int | None:
    """Rough token count for a JSON-serialisable payload (~4 characters per token)."""
    try:
        if isinstance(payload, list):
            payload = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in payload]
        return max(1, len(json.dumps(payload, default=str)) // 4)
    except Exception:
        return None


def ref_sort_key(ref: str) -> tuple:
    """Sort ``R10`` after ``R9`` (plain string order puts it before)."""
    match = _REF_RE.match(ref.strip())
    if not match:
        return (ref, 0, "")
    prefix, number, suffix = match.groups()
    return (prefix, int(number), suffix)


def group_bom(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Group identical parts into BOM lines, as a real BOM does.

    A line is an *orderable part*, not a schematic value. Two symbols carrying the same LCSC
    code are the same reel and collapse into one line whatever their values say; without a code,
    connectors group by footprint because a connector's value is the net it carries (nineteen
    terminal blocks wired as OUT1…OUT16 are one line of nineteen). Any other unsourced part
    groups by value *and* footprint, so 10k and 47k resistors stay separate.

    Returns ``(lines, total_qty)`` with lines ordered by reference; each line also lists the
    distinct ``values`` it merged, so a schematic inconsistency is still visible.
    """
    from kicad_harness.engine.bom_source import part_key

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        value = str(row.get("value") or "").strip()
        footprint = str(row.get("footprint") or "").strip()
        lcsc = str(row.get("lcsc") or "").strip().upper()
        key = part_key(value, footprint, lcsc)
        line = groups.get(key)
        if line is None:
            line = groups[key] = {
                "value": value,
                "footprint": footprint,
                "lcsc": lcsc,
                "description": row.get("description") or "",
                "mpn": row.get("mpn") or "",
                "url": row.get("url") or "",
                "tier": row.get("tier"),
                "stock": row.get("stock"),
                "unit_price": row.get("unit_price"),
                "refs": [],
                "values": [],
                "_values_by_ref": {},
            }
        ref = str(row.get("ref") or "")
        line["refs"].append(ref)
        line["_values_by_ref"][ref] = value
        # Keep supplier facts if a later row for the same part carried them.
        for field in ("mpn", "url", "tier", "stock", "unit_price"):
            if line.get(field) in (None, "") and row.get(field) not in (None, ""):
                line[field] = row[field]

    lines = list(groups.values())
    for line in lines:
        line["refs"] = sorted((r for r in line["refs"] if r), key=ref_sort_key)
        line["qty"] = len(line["refs"])
        by_ref = line.pop("_values_by_ref")
        line["values"] = [by_ref[r] for r in line["refs"] if by_ref.get(r)]
        # De-duplicate while preserving reference order, so the value shown is the one on the
        # first reference of the line.
        seen: list[str] = []
        for value in line["values"]:
            if value not in seen:
                seen.append(value)
        line["values"] = seen
        line["value"] = seen[0] if seen else line["value"]
    lines.sort(key=lambda ln: ref_sort_key(ln["refs"][0]) if ln["refs"] else ("", 0, ""))
    return lines, sum(line["qty"] for line in lines)


def summarise_tiers(lines: list[dict[str, Any]], total_qty: int) -> dict[str, Any]:
    """Basic-vs-extended coverage, counted per *part number*.

    One terminal block used nineteen times is one extended part to order and one reel fee, so
    counting lines would overstate the problem by an order of magnitude.
    """
    tiers: dict[str, str] = {}
    for line in lines:
        code = str(line.get("lcsc") or "").upper()
        if code:
            tiers.setdefault(code, line.get("tier") or "extended")
    units = {"basic": 0, "extended": 0}
    for line in lines:
        code = str(line.get("lcsc") or "").upper()
        if code in tiers:
            units[tiers[code]] += line["qty"]
    basic = sum(1 for tier in tiers.values() if tier == "basic")
    return {
        "parts": len(tiers),
        "basic_parts": basic,
        "extended_parts": len(tiers) - basic,
        "basic_units": units["basic"],
        "units": total_qty,
    }


class SseFanout:
    """Share live-state probes between the SSE streams of one design.

    Every open browser tab keeps an EventSource that polls twice a second. Running that probe
    per subscriber put several polls per second into the *default* asyncio thread pool, which is
    also where tool execution runs (``asyncio.to_thread(engine.call, ...)``) — so a busy UI
    starved the agent and tools stalled for minutes. This caches one result per design per
    ``ttl`` and runs it on a dedicated executor, so polls can never occupy the pool tools need.
    """

    def __init__(self, ttl: float = 0.5, workers: int = 2) -> None:
        self.ttl = ttl
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sse-probe")
        self._entries: dict[str, tuple[float, dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, probe: Callable[[], dict], _svc: Any = None) -> dict:
        """The latest probe result for ``key``, running ``probe`` at most once per ``ttl``."""
        cached = self._entries.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl:
            return cached[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another subscriber may have refreshed while we waited for the lock.
            cached = self._entries.get(key)
            if cached and time.monotonic() - cached[0] < self.ttl:
                return cached[1]
            loop = asyncio.get_running_loop()
            value = await loop.run_in_executor(self._executor, probe)
            self._entries[key] = (time.monotonic(), value)
            return value


def clamp_budget(budget: int, min_budget: int = 4096, max_budget: int = 131072) -> int:
    return max(min_budget, min(max_budget, int(budget)))


# Reasons a provider gives when the *requested* budget is more than it will accept.
_BUDGET_TOO_LARGE = (
    "can only afford",
    "fewer max_tokens",
    "max_tokens is too large",
    "maximum context length",
    "reduce the length",
)


def next_budget(budget: int, exc: BaseException, min_budget: int = 4096, max_budget: int = 131072) -> int | None:
    """The budget to retry with after ``exc``, or ``None`` if this error is not about the budget.

    Two opposite failures need opposite responses: the model spending its whole budget on
    thinking needs a *larger* one, while a provider credit/context limit needs a *smaller* one.
    """
    message = str(exc).lower()
    if "token limit" in message and "exceeded before any response" in message and budget < max_budget:
        return min(max_budget, budget * 2)
    if any(marker in message for marker in _BUDGET_TOO_LARGE) and budget > min_budget:
        return max(min_budget, budget // 2)
    return None


async def stream_with_token_retry(
    make_stream: Callable[[int], Any],
    initial_budget: int | None = None,
    max_budget: int = 131072,
    min_budget: int = 4096,
    max_attempts: int = 6,
) -> Any:
    """Yield events from ``make_stream(budget)``, adapting the completion budget on failure.

    Two opposite failures, both fatal if unhandled:

    - **Too small.** A reasoning model spends the whole budget thinking and returns
      ``finish_reason='length'`` with no output; pydantic-ai raises ``UnexpectedModelBehavior``.
      Retry with a larger budget.
    - **Too large.** A provider with an account credit limit rejects the request outright
      ("requested up to 32000 tokens, but can only afford 25411"). Retry with a smaller one.

    Nothing actionable was streamed in either case, so retrying is safe.
    """
    if initial_budget is None:
        from kicad_harness.agent import agent as agent_mod
        from kicad_harness.agent import models as model_catalog

        initial_budget = model_catalog.budget_for(agent_mod.current_model())
    budget = clamp_budget(initial_budget, min_budget, max_budget)
    attempts = 0
    while True:
        attempts += 1
        try:
            async for event in make_stream(budget):
                yield event
            return
        except Exception as exc:  # noqa: BLE001 - provider errors arrive in many types
            retry = next_budget(budget, exc, min_budget, max_budget) if attempts < max_attempts else None
            if retry is None:
                raise
            log.warning("retrying with max_tokens=%d after: %s", retry, str(exc)[:120])
            budget = retry


async def _rev_hash(svc: Services, ws, rev: str) -> str:
    """Board hash of a revision: a sha (any unique prefix git accepts) or 'working'."""
    if rev in ("working", "WORKING"):
        data = ws.board_path.read_bytes()
    else:
        try:
            data = ws.file_at(rev)
        except Exception as exc:
            raise HTTPException(404, f"unknown revision {rev!r}: {exc}") from exc
    board_hash, _ = await asyncio.to_thread(svc.artifacts.materialize, data)
    return board_hash


def _last_user_text(run_input: Any) -> str:
    for message in reversed(run_input.messages or []):
        if getattr(message, "role", None) == "user":
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content[:2000]
            texts = [getattr(part, "text", "") for part in content or [] if getattr(part, "type", "") == "text"]
            return " ".join(texts)[:2000]
    return ""


def _event_dict(event: Any) -> dict:
    return event.model_dump(mode="json", by_alias=True, exclude_none=True)


def _diff(index_a: dict, index_b: dict) -> dict:
    """Entity-level diff by UUID between two pick indexes (a = old, b = new)."""
    result: dict[str, list] = {"added": [], "removed": [], "moved": [], "net_changed": [],
                               "silkscreen": [], "outline": None}
    edge_a = (index_a.get("board") or {}).get("edge_bbox")
    edge_b = (index_b.get("board") or {}).get("edge_bbox")
    if edge_a and edge_b:
        size_a, size_b = [round(edge_a[2], 3), round(edge_a[3], 3)], [round(edge_b[2], 3), round(edge_b[3], 3)]
        if size_a != size_b:
            result["outline"] = {"from": size_a, "to": size_b}
    for kind, plural in (("footprint", "footprints"), ("pad", "pads"), ("track", "tracks"), ("via", "vias"),
                         ("zone", "zones")):
        old = {e["uuid"]: e for e in index_a.get(plural, [])}
        new = {e["uuid"]: e for e in index_b.get(plural, [])}
        for uid in new.keys() - old.keys():
            result["added"].append({"kind": kind, **_slim(new[uid])})
        for uid in old.keys() - new.keys():
            result["removed"].append({"kind": kind, **_slim(old[uid])})
        for uid in old.keys() & new.keys():
            a, b = old[uid], new[uid]
            pos_a, pos_b = _pos(a), _pos(b)
            if pos_a != pos_b or round(a.get("rotation", 0), 3) != round(b.get("rotation", 0), 3):
                result["moved"].append({"kind": kind, "uuid": uid, "ref": b.get("ref"), "pad": b.get("pad"),
                                        "net": b.get("net"), "from": pos_a, "to": pos_b})
            if kind == "footprint":
                rt_a, rt_b = a.get("ref_text") or {}, b.get("ref_text") or {}
                pos_a = [round(rt_a.get("x", 0), 3), round(rt_a.get("y", 0), 3)]
                pos_b = [round(rt_b.get("x", 0), 3), round(rt_b.get("y", 0), 3)]
                if rt_a and rt_b and pos_a != pos_b:
                    result["silkscreen"].append({"kind": "footprint", "uuid": uid, "ref": b.get("ref"),
                                                 "from": pos_a, "to": pos_b})
            if kind != "footprint" and a.get("net") != b.get("net"):
                result["net_changed"].append({"kind": kind, "uuid": uid, "ref": b.get("ref"), "pad": b.get("pad"),
                                              "from": a.get("net"), "to": b.get("net")})
    return result


def _pos(e: dict) -> list[float]:
    if "x1" in e:
        return [round(e["x1"], 4), round(e["y1"], 4), round(e["x2"], 4), round(e["y2"], 4)]
    if "x" in e:
        return [round(e["x"], 4), round(e["y"], 4)]
    return []


def _slim(e: dict) -> dict:
    return {k: v for k, v in e.items() if k not in ("polygon", "outline", "filled", "courtyard")}


def _parent_sha(ws, sha: str) -> str | None:
    """First parent of ``sha`` (git order), or None for the import commit."""
    for commit in ws.log(limit=1000):
        if commit["sha"] == sha:
            parents = commit.get("parents") or []
            return parents[0] if parents else None
    return None


def _version_diff(svc: Services, ws, sha: str) -> tuple[str | None, dict]:
    """(parent, entity diff of parent → sha). Thumbnails/diffs come from real revisions."""
    parent = _parent_sha(ws, sha)
    new_hash, _ = svc.artifacts.materialize(ws.file_at(sha))
    new_index = svc.artifacts.index(new_hash)
    if parent:
        old_hash, _ = svc.artifacts.materialize(ws.file_at(parent))
        old_index = svc.artifacts.index(old_hash)
    else:
        old_index = {}
    return parent, _diff(old_index, new_index)


async def _compute_metrics(svc: Services, design: str, ws, sha: str) -> dict:
    data = await asyncio.to_thread(ws.file_at, sha)
    board_hash, _ = await asyncio.to_thread(svc.artifacts.materialize, data)
    drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
    counts = drc.get("counts", {})
    metrics = {"errors": counts.get("errors", 0), "warnings": counts.get("warnings", 0),
               "unconnected": counts.get("unconnected", 0)}
    svc.store.versions.put(design, sha, board_hash, metrics)
    return {"board_hash": board_hash, "metrics": metrics}


class AgentHost:
    """The running agent plus a safe way to swap its model at runtime.

    The agent is entered once (its toolset context lives there). Changing models rebuilds and
    re-enters it; that is refused while a run is streaming so a live run cannot lose its agent.
    """

    def __init__(self) -> None:
        self.agent = None
        self.model: str = current_model()
        self.active = 0

    async def start(self) -> None:
        self.agent = get_agent()
        self.model = current_model()
        await self.agent.__aenter__()

    async def swap(self, model: str) -> None:
        old_agent = self.agent
        set_agent_model(model)
        new_agent = get_agent()
        await new_agent.__aenter__()
        self.agent, self.model = new_agent, model
        if old_agent is not None:
            await old_agent.__aexit__(None, None, None)

    async def close(self) -> None:
        if self.agent is not None:
            await self.agent.__aexit__(None, None, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    svc = Services.create()
    app.state.services = svc
    # One shared probe cache so open tabs can't starve the thread pool tools run on.
    app.state.sse_fanout = SseFanout()
    # A model chosen in the UI survives restarts.
    stored_model = svc.store.get_setting("model")
    if stored_model:
        set_agent_model(stored_model)
    host = app.state.agent_host
    app.state.metric_tasks = set()
    app.state.stop_flags = {}
    stale = svc.store.runs.interrupt_stale()
    # Warm the Bifrost catalog in the background: the gateway's /v1/models takes seconds.
    asyncio.create_task(asyncio.to_thread(model_catalog.bifrost_ids, svc.config))
    # Also learn each model's advertised output limit, so max_tokens follows the model.
    asyncio.create_task(asyncio.to_thread(model_catalog.warm_model_limits, svc.config))
    # Stop replaying token-limit errors from threads that have since moved on.
    try:
        from kicad_harness.store.threads import prune_reasoning, prune_stale_token_errors

        async def _housekeeping() -> None:
            await asyncio.to_thread(prune_reasoning, svc.config.copilot_db)
            await asyncio.to_thread(
                prune_stale_token_errors,
                svc.config.copilot_db,
                model_catalog.budget_for(request.app.state.agent_host.model, svc.config),
            )

        asyncio.create_task(_housekeeping())
    except Exception:  # housekeeping must never block startup
        pass
    if stale:
        import logging

        logging.getLogger("kicad_harness").warning("marked %d stale run(s) interrupted", stale)
    await host.start()
    try:
        yield
    finally:
        await host.close()
        close = getattr(svc.engine, "close", None)
        if close:
            close()


def create_app() -> FastAPI:
    app = FastAPI(title="kicad-harness", lifespan=lifespan)
    # Created here (not only in lifespan) so routes have a host even when lifespan is skipped
    # (tests mount the app without it and may inject their own scripted agent).
    app.state.agent_host = AgentHost()
    # Per-design stop flags; also created here so routes work without lifespan (tests).
    app.state.stop_flags = {}

    # ------------------------------------------------------------------ agent (AG-UI)

    @app.post("/agui")
    async def agui(request: Request):
        svc = _svc(request)
        host = request.app.state.agent_host
        agent = host.agent
        adapter = await AGUIAdapter.from_request(request, agent=agent)
        run_input = adapter.run_input
        design = (run_input.state or {}).get("design") if isinstance(run_input.state, dict) else None
        goal: dict = goals.default_goal()
        if design:
            from kicad_harness.store.workspace import Workspace

            if Workspace.exists(svc.config, design):
                goal = Workspace.open(svc.config, design).goal()
        budget = goal.get("budget") or {}
        deps = HarnessDeps(state=DesignState(), services=svc, run_id=run_input.run_id,
                           max_minutes=budget.get("max_minutes"))
        run_id = run_input.run_id
        if design:
            svc.store.runs.start(design, thread_id=run_input.thread_id, run_id=run_id, model=host.model,
                                 prompt=_last_user_text(run_input), source="ui")

        # Token accounting for the context counter: the prompt size we were sent (the client
        # replays the thread), plus the real usage the model reported when the run ends.
        tokens: dict[str, Any] = {}
        tokens["context_estimate"] = estimate_tokens(getattr(run_input, "messages", None) or [])

        async def final_state(_result):
            try:
                usage = getattr(_result, "usage", None)
                if usage is not None:
                    tokens["usage"] = {
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                        "total_tokens": getattr(usage, "total_tokens", None),
                        "requests": getattr(usage, "requests", None),
                    }
            except Exception:
                pass
            yield _snapshot(deps.state)

        def make_stream(budget: int):
            return adapter.run_stream(
                deps=deps,
                usage_limits=usage_limits_for(goal),
                on_complete=final_state,
                model_settings=ModelSettings(max_tokens=budget),
            )

        async def event_stream():
            from kicad_harness.agent import models as model_catalog

            async for event in stream_with_token_retry(
                make_stream, model_catalog.budget_for(host.model)
            ):
                # Reasoning arrives as one event per token delta — tens of thousands per run —
                # and the web runtime stores every event it receives so it can replay a thread.
                # That made reopening a long thread a multi-megabyte replay of "thoughts".
                # The reasoning is not needed to reconstruct the conversation, so it is not
                # forwarded at all.
                if str(getattr(event, "type", "")).startswith("REASONING"):
                    continue
                yield event

        host.active += 1
        flags = request.app.state.stop_flags
        if design:
            flags[design] = asyncio.Event()
        stop = flags.get(design) if design else None

        async def logged():
            status = "error"
            try:
                async for event in event_stream():
                    if stop is not None and stop.is_set():
                        status = "cancelled"
                        break
                    if design:
                        svc.store.runs.event(run_id, _event_dict(event))
                    yield event
                else:
                    status = "finished"
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            finally:
                host.active = max(0, host.active - 1)
                if design:
                    svc.store.runs.finish(run_id, status, usage=tokens or None)

        return adapter.streaming_response(logged())

    # ------------------------------------------------------------------ models

    @app.get("/api/models")
    async def list_models(request: Request):
        """Models the UI may pick, plus the one new runs use."""
        host = request.app.state.agent_host
        current = host.model
        cfg = request.app.state.services.config
        # The Bifrost catalog does a network call; keep it off the event loop.
        entries = await asyncio.to_thread(model_catalog.catalog, cfg)
        if not any(e["id"] == current for e in entries):  # a model that is no longer listed
            entries.insert(0, model_catalog._entry(current))
        for entry in entries:
            entry["current"] = entry["id"] == current
        return {"current": current, "models": entries, "busy": host.active > 0}

    @app.put("/api/models")
    async def select_model(request: Request, body: dict = Body(...)):
        """Switch the model for subsequent runs. Refused while a run is streaming."""
        host = request.app.state.agent_host
        svc = _svc(request)
        model = (body.get("model") or "").strip()
        if not model or ":" not in model:
            raise HTTPException(400, "model must look like 'provider:model-name'")
        if host.active > 0:
            raise HTTPException(409, "a run is active; stop it before switching models")
        if model == host.model:
            return {"current": host.model}
        try:
            await host.swap(model)
        except Exception as exc:  # unknown provider, missing key, bad model id
            # Keep the previous agent usable and report a readable error.
            raise HTTPException(400, f"cannot use model {model!r}: {exc}") from exc
        svc.store.set_setting("model", model)
        return {"current": host.model}

    # ------------------------------------------------------------------ designs

    @app.get("/api/designs")
    async def list_designs(request: Request):
        """Every design with enough identity for the project selector (and a thumbnail)."""
        from kicad_harness.store.workspace import Workspace

        svc = _svc(request)
        out = []
        for name in Workspace.list(svc.config):
            ws = Workspace.open(svc.config, name)
            head = ws.head()
            log = ws.log(limit=1)
            latest = log[0] if log else None
            item = {
                "name": name,
                "branch": ws.branch(),
                "head": head,
                "dirty": ws.dirty(),
                "phase": ws.phase(),
                "brief": ws.brief(),
                "updated_at": (latest or {}).get("time"),
                "latest": {
                    "sha": latest["sha"],
                    "short": latest["short"],
                    "subject": latest["subject"],
                } if latest else None,
                "thumbnail": f"/api/designs/{name}/rev/{head}/svg/front" if head else None,
            }
            try:
                item["goal_summary"] = (ws.goal() or {}).get("summary")
            except Exception:  # a malformed goal.json must not hide the project
                item["goal_summary"] = None
            out.append(item)
        out.sort(key=lambda d: d.get("updated_at") or 0, reverse=True)
        return out

    @app.post("/api/designs")
    async def create_design(request: Request, body: dict = Body(...)):
        from kicad_harness.store.workspace import Workspace

        svc = _svc(request)
        name = body.get("name")
        if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise HTTPException(400, "name must be letters, digits, _ . -")
        if Workspace.exists(svc.config, name):
            raise HTTPException(409, f"design {name!r} exists")
        goal = body.get("goal")
        if goal:
            try:
                goals.validate_goal(goal)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        # A prompt starts a schematic-first project: nothing is drawn yet, and the agent's
        # first run draws the circuit from the description.
        prompt = body.get("prompt") or body.get("brief")
        if prompt is not None:
            if not isinstance(prompt, str) or not prompt.strip():
                raise HTTPException(400, "prompt must describe the board")
            try:
                ws = await asyncio.to_thread(Workspace.create_schematic, svc.config, name, prompt, goal)
            except FileExistsError as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"name": name, "branch": ws.branch(), "head": ws.head(), "phase": ws.phase(),
                    "brief": ws.brief(), "autostart": bool(body.get("autostart", True))}

        # Three ways in: a browser upload (board_text), the bundled demo fixture, or a path.
        src: Path | None = None
        cleanup: Path | None = None
        board_text = body.get("board_text")
        if board_text is not None:
            if not isinstance(board_text, str) or "kicad_pcb" not in board_text[:2000]:
                raise HTTPException(400, "board_text does not look like a .kicad_pcb file")
            tmp_dir = Path(svc.config.home) / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            src = tmp_dir / f"upload-{name}-{uuid.uuid4().hex[:8]}.kicad_pcb"
            src.write_text(board_text, encoding="utf-8")
            cleanup = src
        elif body.get("from_fixture"):
            fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "blinker.kicad_pcb"
            if not fixture.exists():
                raise HTTPException(400, "the bundled demo fixture is not available")
            src = fixture
        else:
            candidate = body.get("from_board")
            if not candidate or not Path(candidate).exists():
                raise HTTPException(400, f"from_board not found: {candidate!r}")
            src = Path(candidate)

        try:
            ws = await asyncio.to_thread(Workspace.create, svc.config, name, src, goal, svc.engine)
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
        return {"name": name, "branch": ws.branch(), "head": ws.head()}

    @app.get("/api/designs/{design}")
    async def get_design(request: Request, design: str):
        svc = _svc(request)
        ws = _workspace(svc, design)
        if ws.phase() == "schematic":
            # No board yet: the viewer shows the schematic instead of a board summary.
            from kicad_harness.engine.schematic import Schematic

            doc = await asyncio.to_thread(Schematic.load, ws.schematic_path)
            return {
                "name": design, "branch": ws.branch(), "head": ws.head(), "dirty": ws.dirty(),
                "goal": ws.goal(), "phase": "schematic", "brief": ws.brief(),
                "board_hash": None,
                "has_board": ws.board_path.is_file(),
                "has_schematic": ws.schematic_path.is_file(),
                "schematic_hash": await asyncio.to_thread(
                    lambda: __import__("hashlib").sha256(ws.schematic_path.read_bytes()).hexdigest()[:16]
                ),
                "summary": {"drc": {}, "size": [0.0, 0.0],
                            "components": len([s for s in doc.index()["symbols"]])},
            }
        board_hash = await _rev_hash(svc, ws, "working")
        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        _, _, w, h = index["board"]["edge_bbox"]
        return {
            "name": design, "branch": ws.branch(), "head": ws.head(), "dirty": ws.dirty(), "goal": ws.goal(),
            "phase": ws.phase(), "brief": ws.brief(),
            "has_board": ws.board_path.is_file(),
            "has_schematic": ws.schematic_path.is_file(),
            "schematic_hash": await asyncio.to_thread(
                lambda: __import__("hashlib").sha256(ws.schematic_path.read_bytes()).hexdigest()[:16]
            ) if ws.schematic_path.is_file() else None,
            "board_hash": board_hash,
            "summary": {"drc": drc.get("counts", {}), "size": [round(w, 3), round(h, 3)],
                        "components": len(index.get("footprints", []))},
        }

    # ------------------------------------------------------------------ schematic (phase)

    @app.get("/api/designs/{design}/schematic")
    async def schematic_index(request: Request, design: str):
        """Symbols, nets, floating pins and ERC for the working-copy schematic."""
        from kicad_harness.engine import kicad_cli
        from kicad_harness.engine.schematic import Schematic

        svc = _svc(request)
        ws = _workspace(svc, design)
        path = ws.schematic_path
        if not path.is_file():
            raise HTTPException(404, "this design has no schematic")
        doc = await asyncio.to_thread(Schematic.load, path)
        try:
            erc = await asyncio.to_thread(kicad_cli.erc, path, config=svc.config)
        except Exception as exc:  # ERC is advisory here; the viewer still renders
            erc = {"counts": {}, "violations": [], "error": str(exc)}
        return {"phase": ws.phase(), "brief": ws.brief(), **doc.index(), "erc": erc}

    @app.get("/api/designs/{design}/schematic.svg")
    async def schematic_svg(request: Request, design: str, rev: str = "working", sheet: int = 0):
        """The schematic plotted to SVG (per sheet), cached under its content hash."""
        from kicad_harness.engine import kicad_cli
        from kicad_harness.engine.artifacts import board_hash as content_hash

        svc = _svc(request)
        ws = _workspace(svc, design)
        try:
            data = await asyncio.to_thread(ws.file_at, rev, "schematic.kicad_sch")
        except FileNotFoundError as exc:
            raise HTTPException(404, "no schematic at that revision") from exc
        digest = content_hash(data)
        out_dir = Path(svc.config.cache) / "schematic" / digest
        plots = sorted(out_dir.glob("*.svg")) if out_dir.is_dir() else []

        def plot() -> list[Path]:
            src = out_dir.parent / f"{digest}.kicad_sch"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(data)
            try:
                return kicad_cli.export_sch_svg(src, out_dir, config=svc.config)
            finally:
                src.unlink(missing_ok=True)

        if not plots:
            try:
                plots = await asyncio.to_thread(plot)
            except Exception as exc:
                raise HTTPException(500, f"could not plot the schematic: {exc}") from exc
        if not plots:
            raise HTTPException(404, "the schematic plotted to nothing")
        chosen = min(max(sheet, 0), len(plots) - 1)
        return Response(content=plots[chosen].read_bytes(), media_type="image/svg+xml",
                        headers={"Cache-Control": "max-age=3600"})

    # ------------------------------------------------------------------ run control

    @app.post("/api/designs/{design}/goal/run")
    async def run_goal(request: Request, design: str, body: dict = Body(default={})):
        """Start (or resume) the goal loop for this design without the browser.

        Runs the same headless loop the CLI uses, as a detached process: that keeps a long run
        out of the API's event loop and writes to the same run/event store, so the UI follows it
        like any other run. Refuses while a run for the design is already active.
        """
        import subprocess
        import sys as _sys
        from pathlib import Path as _Path

        svc = _svc(request)
        ws = _workspace(svc, design)
        running = [r for r in svc.store.runs.list(design) if r.get("status") == "running"]
        if running:
            raise HTTPException(409, f"a run is already active for {design!r}: {running[0]['run_id']}")

        prompt = body.get("prompt") or body.get("nudge")
        thread = body.get("thread")
        # The CLI entry point lives beside the interpreter in the harness venv.
        args = [str(_Path(_sys.executable).parent / "kicad-harness")]
        args += ["run", design, "--loop"]
        if prompt:
            args += ["--prompt", str(prompt)]
        if thread:
            args += ["--thread", str(thread)]
        log_dir = _Path(svc.config.home) / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{design}-{int(time.time())}.log"
        try:
            log = open(log_path, "ab")
            proc = subprocess.Popen(args, cwd=str(_Path(__file__).resolve().parent.parent),
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception as exc:
            raise HTTPException(500, f"could not start the goal loop: {exc}") from exc
        return {"started": True, "design": design, "pid": proc.pid, "thread": thread,
                "prompt": prompt, "log": str(log_path)}

    @app.get("/api/designs/{design}/goal/runs")
    async def goal_runs(request: Request, design: str):
        """Runs started through the API (their pid is in the log line above them)."""
        svc = _svc(request)
        _workspace(svc, design)
        return [r for r in svc.store.runs.list(design) if r.get("source") == "cli"][:10]

    # ------------------------------------------------------------------ context usage

    @app.get("/api/designs/{design}/usage")
    async def usage(request: Request, design: str):
        """How much of the model's context the current thread is using.

        ``context_estimate`` is the prompt size the last run was sent (the client replays the
        thread); ``last_run`` carries the real token usage the model reported when it finished.
        """
        from kicad_harness.agent import models as model_catalog

        svc = _svc(request)
        _workspace(svc, design)
        model = request.app.state.agent_host.model
        runs = svc.store.runs.list(design)
        last = runs[0] if runs else {}
        stored = last.get("usage") or {}
        window = model_catalog.context_window(model, svc.config)
        limit = model_catalog.budget_for(model, svc.config)
        growth = svc.store.runs.event_bytes(last.get("run_id")) if last.get("run_id") else 0
        used = stored.get("context_estimate")
        grown = int((growth or 0) / 4)
        used = (used + grown) if used else (grown or None)
        return {
            "model": model,
            "context_window": window,
            "max_output_tokens": limit,
            "context_estimate": used,
            "percent": round(100 * used / window, 1) if used and window else None,
            "last_run": {
                "run_id": last.get("run_id"),
                "status": last.get("status"),
                **(stored.get("usage") or {}),
            },
        }

    # ------------------------------------------------------------------ bill of materials

    @app.get("/api/designs/{design}/bom")
    async def bom(request: Request, design: str, enrich: bool = True):
        """The project's bill of materials.

        Taken from the schematic when there is one (that is where refs, values and footprints
        are authored, so it works before layout exists), otherwise from the board's footprints —
        projects imported from a ``.kicad_pcb`` have no schematic.

        ``enrich`` adds JLCPCB stock/price for lines that carry an LCSC part number.
        """
        from kicad_harness.engine import parts as parts_mod
        from kicad_harness.engine.schematic import Schematic

        svc = _svc(request)
        ws = _workspace(svc, design)
        rows: list[dict[str, Any]] = []
        source = "none"

        if ws.schematic_path.is_file():
            try:
                doc = await asyncio.to_thread(Schematic.load, ws.schematic_path)
            except Exception as exc:  # a half-written sheet should not 500 the BOM
                raise HTTPException(422, f"the schematic could not be read: {exc}") from exc
            for placement in doc.placements():
                if placement.reference.startswith("#"):  # power flags are not parts
                    continue
                props = placement.properties or {}
                rows.append(
                    {
                        "ref": placement.reference,
                        "value": placement.value,
                        "footprint": placement.footprint,
                        "lcsc": props.get("LCSC") or "",
                        "description": props.get("Description") or "",
                    }
                )
            if rows:
                source = "schematic"

        if not rows and ws.board_path.is_file():
            board_hash = await _rev_hash(svc, ws, "working")
            index = await asyncio.to_thread(svc.artifacts.index, board_hash)
            for fp in index.get("footprints", []):
                rows.append(
                    {
                        "ref": fp.get("ref", ""),
                        "value": fp.get("value", ""),
                        "footprint": fp.get("footprint", ""),
                        "lcsc": "",
                        "description": "",
                    }
                )
            if rows:
                source = "board"

        if enrich:
            for row in rows:
                lcsc = row.get("lcsc")
                if not lcsc:
                    continue
                try:
                    hit = await asyncio.to_thread(parts_mod.get_jlc, lcsc)
                except Exception:  # supplier data is a bonus, never a reason to fail
                    hit = None
                if hit:
                    row.update(
                        mpn=hit["mpn"],
                        tier=hit["tier"],
                        stock=hit["stock"],
                        unit_price=hit["unit_price"],
                        url=hit.get("url") or f"https://www.lcsc.com/product-detail/{lcsc}",
                    )

        lines, total_qty = group_bom(rows)
        # Cost per line and for the board. Parts without a known unit price are counted in
        # "unpriced" so a total is never silently wrong.
        total_price = 0.0
        unpriced = 0
        for line in lines:
            unit = line.get("unit_price")
            if unit is None:
                line["line_total"] = None
                unpriced += line["qty"]
            else:
                line["line_total"] = round(float(unit) * line["qty"], 4)
                total_price += line["line_total"]
        # Basic-vs-extended coverage. JLCPCB charges a reel fee for every extended part, so this
        # is the number that decides whether a board is cheap to assemble.
        tier_summary = summarise_tiers(lines, total_qty)
        return {
            "phase": ws.phase(),
            "source": source,
            "lines": lines,
            "line_count": len(lines),
            "total_qty": total_qty,
            "total_price": round(total_price, 2),
            "unpriced_qty": unpriced,
            "tier_summary": tier_summary,
        }

    @app.get("/api/designs/{design}/bom/basic")
    async def bom_basic(request: Request, design: str):
        """How much of the BOM is basic-tier, and why the rest is not.

        "Use as many basic parts as possible" is only checkable per part: each extended line is
        reported with the basic alternative the catalogue offers, or the reason none exists
        (JLCPCB has no basic relay/connector/electrolytic at any price, for instance).
        """
        from kicad_harness.engine import bom_source

        svc = _svc(request)
        ws = _workspace(svc, design)
        if not ws.schematic_path.is_file():
            raise HTTPException(400, "this design has no schematic to source parts for")
        try:
            return await asyncio.to_thread(bom_source.basic_report, ws)
        except Exception as exc:
            raise HTTPException(500, f"could not read the BOM: {exc}") from exc

    @app.post("/api/designs/{design}/bom/resolve")
    async def resolve_bom(request: Request, design: str):
        """Fill in supplier part numbers for BOM lines that have none.

        Matching is evidence-based (catalogue attributes, part-number prefix, connector pitch and
        position count). Lines it cannot determine stay blank and are returned as unresolved.
        """
        from kicad_harness.engine import bom_source

        svc = _svc(request)
        ws = _workspace(svc, design)
        if not ws.schematic_path.is_file():
            raise HTTPException(400, "this design has no schematic to source parts for")
        try:
            report = await asyncio.to_thread(bom_source.resolve_workspace, ws)
        except Exception as exc:
            raise HTTPException(500, f"could not resolve parts: {exc}") from exc
        if report["resolved_lines"]:
            await asyncio.to_thread(
                lambda: ws.commit(f"Source {report['resolved_lines']} BOM line(s) from JLCPCB",
                                  author="agent")
            )
        return report

    # ------------------------------------------------------------------ versions

    @app.get("/api/designs/{design}/versions")
    async def versions(request: Request, design: str, branch: str | None = None, limit: int = 200):
        svc = _svc(request)
        ws = _workspace(svc, design)
        out = []
        phase = ws.phase()
        for v in ws.log(branch=branch, limit=limit):
            v = _as_dict(v)
            meta = svc.store.versions.get(design, v["sha"])
            v["thumbnail"] = (
                f"/api/designs/{design}/schematic.svg?rev={v['sha']}"
                if phase == "schematic"
                else f"/api/designs/{design}/rev/{v['sha']}/svg/front"
            )
            v["phase"] = phase
            parents = v.get("parents") or []
            v["parent"] = parents[0] if parents else None
            if phase == "schematic":
                # No board to measure or diff in this phase: the schematic plot is the preview.
                v.update(board_hash=None, metrics=None)
                out.append(v)
                continue
            if meta:
                v.update(board_hash=meta.get("board_hash"), metrics=meta.get("metrics"),
                         summary=meta.get("summary"), diff=meta.get("diff"))
            else:
                v.update(board_hash=None, metrics=None)
                key = (design, v["sha"])
                tasks = request.app.state.metric_tasks
                if key not in {t.get_name() for t in tasks} and len(tasks) < 4:
                    task = asyncio.create_task(_compute_metrics(svc, design, ws, v["sha"]), name=str(key))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            out.append(v)
        return out

    @app.get("/api/designs/{design}/versions/{sha}/summary")
    async def version_summary(request: Request, design: str, sha: str):
        """Diff against the parent plus a cached few-word LLM note about it."""
        from kicad_harness.agent import summarize as summarizer

        svc = _svc(request)
        ws = _workspace(svc, design)
        meta = svc.store.versions.get(design, sha) or {}
        subject = next((c["subject"] for c in ws.log(limit=1000) if c["sha"] == sha), sha[:8])
        diff = meta.get("diff")
        parent = _parent_sha(ws, sha)
        if diff is None:
            parent, diff = await asyncio.to_thread(_version_diff, svc, ws, sha)
            svc.store.versions.put(design, sha, diff=diff)
        cached_summary = meta.get("summary")
        summary = cached_summary
        error = None
        if not cached_summary:
            host = request.app.state.agent_host
            try:
                # bifrost:<id> needs the same resolution the agent uses; pydantic-ai only knows
                # provider:model-name strings.
                text = await summarizer.summarize_diff(model_catalog.resolve(host.model), subject, diff)
            except Exception as exc:  # no key / bad model / provider error
                text, error = None, f"{type(exc).__name__}: {exc}"[:300]
            if text:
                svc.store.versions.put(design, sha, summary=text)
                summary = text
        counts = {k: len(diff.get(k) or []) for k in ("added", "removed", "moved", "net_changed", "silkscreen")}
        counts["outline"] = 1 if diff.get("outline") else 0
        return {"sha": sha, "subject": subject, "parent": parent, "diff": diff, "counts": counts,
                "summary": summary, "cached": bool(cached_summary), "error": error}

    @app.get("/api/designs/{design}/versions/{sha}/metrics")
    async def version_metrics(request: Request, design: str, sha: str):
        svc = _svc(request)
        ws = _workspace(svc, design)
        meta = svc.store.versions.get(design, sha)
        return meta or await _compute_metrics(svc, design, ws, sha)

    @app.post("/api/designs/{design}/versions/{sha}/restore")
    async def restore(request: Request, design: str, sha: str):
        svc = _svc(request)
        ws = _workspace(svc, design)

        def run():
            with ws.lock:
                return ws.restore(sha, author="user")

        try:
            return _as_dict(await asyncio.to_thread(run))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/designs/{design}/branches")
    async def branches(request: Request, design: str):
        return _workspace(_svc(request), design).branches()

    @app.post("/api/designs/{design}/branches")
    async def create_branch(request: Request, design: str, body: dict = Body(...)):
        ws = _workspace(_svc(request), design)
        name = body.get("name")
        if not name or not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
            raise HTTPException(400, "invalid branch name")
        try:
            with ws.lock:
                ws.create_branch(name, body.get("from_sha") or ws.head())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"branch": ws.branch(), "head": ws.head()}

    @app.post("/api/designs/{design}/branches/{name}/switch")
    async def switch_branch(request: Request, design: str, name: str):
        ws = _workspace(_svc(request), design)
        try:
            with ws.lock:
                ws.switch(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"branch": ws.branch(), "head": ws.head()}

    # ------------------------------------------------------------------ revisions

    @app.get("/api/designs/{design}/rev/{rev}/index")
    async def rev_index(request: Request, design: str, rev: str):
        svc = _svc(request)
        if _workspace(svc, design).phase() == "schematic":
            # No board to index yet; the viewer uses the schematic endpoints instead.
            return {"board_hash": None, "phase": "schematic", "board": None, "footprints": [],
                    "pads": [], "tracks": [], "vias": [], "zones": [], "nets": []}
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        return {**index, "board_hash": board_hash}

    @app.get("/api/designs/{design}/rev/{rev}/svg/{side}")
    async def rev_svg(request: Request, design: str, rev: str, side: str, layers: str | None = None):
        """One side of the board as SVG, optionally plotting only ``layers`` (comma-separated).

        The viewer asks for each layer on its own so its layer panel can show and hide them
        without another render; without ``layers`` the side's full stack is returned.
        """
        if side not in ("front", "back"):
            raise HTTPException(400, "side must be front or back")
        from kicad_harness.engine.artifacts import LAYERS

        chosen: list[str] | None = None
        if layers is not None:
            chosen = [layer.strip() for layer in layers.split(",") if layer.strip()]
            unknown = [layer for layer in chosen if layer not in LAYERS[side]]
            if unknown or not chosen:
                raise HTTPException(
                    400,
                    f"layers must be a non-empty subset of {LAYERS[side]}, "
                    f"unknown: {', '.join(unknown) or '(none given)'}",
                )
        svc = _svc(request)
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        try:
            svg = await asyncio.to_thread(svc.artifacts.svg, board_hash, side, chosen)
        except Exception as exc:  # invalid layer names surface as a 400, not a 500
            from kicad_harness.engine.client import EngineError

            if isinstance(exc, EngineError) and exc.type == "InvalidParams":
                raise HTTPException(400, str(exc)) from exc
            raise
        text = Path(svg).read_text() if isinstance(svg, Path) else svg
        return Response(text, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=31536000"}
                        if rev not in ("working", "WORKING") else {})

    @app.get("/api/designs/{design}/rev/{rev}/model.glb")
    async def rev_model(request: Request, design: str, rev: str):
        """The interactive 3D model (binary glTF) for the viewer tab."""
        from kicad_harness.engine.kicad_cli import GLB_MEDIA_TYPE

        svc = _svc(request)
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        path = await asyncio.to_thread(svc.artifacts.glb_path, board_hash)
        headers = {"Cache-Control": "public, max-age=31536000"} if rev not in ("working", "WORKING") else {}
        return FileResponse(path, media_type=GLB_MEDIA_TYPE, headers=headers)

    @app.get("/api/designs/{design}/rev/{rev}/drc")
    async def rev_drc(request: Request, design: str, rev: str):
        svc = _svc(request)
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        return await asyncio.to_thread(svc.artifacts.drc, board_hash)

    @app.get("/api/designs/{design}/rev/{rev}/render3d")
    async def rev_render3d(request: Request, design: str, rev: str, side: str = "top", rotate: str | None = None,
                           x: float | None = None, y: float | None = None, view_mm: float | None = None,
                           quality: str = "basic"):
        svc = _svc(request)
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        if quality not in ("basic", "high"):
            raise HTTPException(400, "quality must be basic or high")
        try:
            rotation = [float(v) for v in rotate.split(",")] if rotate else None
        except ValueError as exc:
            raise HTTPException(400, "rotate must be 'x,y,z' degrees") from exc
        spec = {"mode": "3d", "side": side, "quality": quality, "rotate": rotation,
                "center": [x, y] if x is not None and y is not None else None, "view_mm": view_mm}
        png_path, _url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
        return FileResponse(png_path, media_type="image/png")

    @app.get("/api/designs/{design}/rev/{rev}/measure")
    async def rev_measure(request: Request, design: str, rev: str, a: str, b: str):
        from kicad_harness.engine.geometry import measure

        svc = _svc(request)
        board_hash = await _rev_hash(svc, _workspace(svc, design), rev)
        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        try:
            return measure(index, a, b)
        except Exception as exc:
            raise HTTPException(400, f"cannot measure {a!r} to {b!r}: {exc}") from exc

    @app.get("/api/designs/{design}/diff")
    async def diff(request: Request, design: str, a: str, b: str = "working"):
        svc = _svc(request)
        ws = _workspace(svc, design)
        ha, hb = await _rev_hash(svc, ws, a), await _rev_hash(svc, ws, b)
        ia = await asyncio.to_thread(svc.artifacts.index, ha)
        ib = await asyncio.to_thread(svc.artifacts.index, hb)
        return _diff(ia, ib)

    # ------------------------------------------------------------------ review

    @app.delete("/api/designs/{design}/threads/{number}")
    async def delete_thread(request: Request, design: str, number: int):
        """Delete a comment thread (used by the UI and by the e2e walkthroughs' cleanup)."""
        svc = _svc(request)
        _workspace(svc, design)
        if not svc.store.review.delete(design, number):
            raise HTTPException(404, f"no thread #{number} on {design!r}")
        return Response(status_code=204)

    @app.get("/api/designs/{design}/threads")
    async def threads(request: Request, design: str, status: str | None = None, rev: str = "working"):
        svc = _svc(request)
        ws = _workspace(svc, design)
        out = svc.store.review.list(design, status=status)
        if ws.phase() == "schematic":
            return out
        board_hash = await _rev_hash(svc, ws, rev)
        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        for t in out:
            t["resolved_anchor"] = svc.store.review.resolve_anchor(t, index)
        return out

    @app.post("/api/designs/{design}/threads")
    async def create_thread(request: Request, design: str, body: dict = Body(...)):
        svc = _svc(request)
        ws = _workspace(svc, design)
        if not body.get("body", "").strip():
            raise HTTPException(400, "body is required")
        if not isinstance(body.get("anchor"), dict):
            raise HTTPException(400, "anchor is required")
        severity = body.get("severity", "suggestion")
        if severity not in SEVERITIES:
            raise HTTPException(400, f"severity must be one of {SEVERITIES}")
        sha = body.get("sha") or ws.head()
        if sha in ("working", "WORKING"):
            sha = ws.head()
        try:
            return svc.store.review.create(
                design, sha, body["anchor"], body["body"].strip(), title=body.get("title"),
                tags=[t for t in body.get("tags", []) if t], severity=severity, kind="comment", source="user",
                author="user", view=body.get("view"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/threads/{thread_id}/replies")
    async def reply(request: Request, thread_id: int, body: dict = Body(...)):
        svc = _svc(request)
        if not body.get("body", "").strip():
            raise HTTPException(400, "body is required")
        thread = svc.store.review.get(thread_id)
        if not thread:
            raise HTTPException(404, "no such thread")
        sha = None
        try:
            sha = _workspace(svc, thread["design"]).head()
        except HTTPException:
            pass
        svc.store.review.reply(thread_id, body["body"].strip(), author="user", sha=sha)
        return svc.store.review.get(thread_id)

    @app.post("/api/threads/{thread_id}/status")
    async def set_status(request: Request, thread_id: int, body: dict = Body(...)):
        svc = _svc(request)
        thread = svc.store.review.get(thread_id)
        if not thread:
            raise HTTPException(404, "no such thread")
        try:
            sha = _workspace(svc, thread["design"]).head()
            svc.store.review.set_status(thread_id, body.get("status"), by="user", sha=sha, note=body.get("note"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return svc.store.review.get(thread_id)

    # ------------------------------------------------------------------ goal

    @app.get("/api/designs/{design}/goal")
    async def get_goal(request: Request, design: str):
        return _workspace(_svc(request), design).goal()

    @app.put("/api/designs/{design}/goal")
    async def put_goal(request: Request, design: str, body: dict = Body(...)):
        ws = _workspace(_svc(request), design)
        try:
            goals.validate_goal(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with ws.lock:
            ws.set_goal(body, author="user")
        return ws.goal()

    @app.get("/api/designs/{design}/goal/status")
    async def goal_status(request: Request, design: str, rev: str = "working"):
        from kicad_harness.engine import kicad_cli

        svc = _svc(request)
        ws = _workspace(svc, design)
        if ws.phase() == "schematic":
            # Judge the schematic: ERC counts plus whether a board exists yet.
            try:
                report = await asyncio.to_thread(kicad_cli.erc, ws.schematic_path, config=svc.config)
                erc_counts = report["counts"]
            except Exception:
                erc_counts = {"errors": 1, "warnings": 0}
            from kicad_harness.agent.audit import design_parts

            parts = await asyncio.to_thread(design_parts, svc, ws)
            return goals.evaluate(
                ws.goal(), {}, {"erc_counts": erc_counts,
                                "board_laid_out": ws.board_path.is_file(),
                                "schematic_missing": parts["missing_from_schematic"],
                                "board_missing": parts["missing_from_board"]},
                svc.store.review.list(design),
            )
        board_hash = await _rev_hash(svc, ws, rev)
        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        drc = goals.with_board_state(drc, ws.board_path.is_file())
        from kicad_harness.agent.audit import design_parts
        from kicad_harness.engine import kicad_cli as _kc

        if ws.schematic_path.is_file():
            try:
                erc_report = await asyncio.to_thread(_kc.erc, ws.schematic_path, config=svc.config)
                drc = {**drc, "erc_counts": erc_report["counts"]}
            except Exception:
                drc = {**drc, "erc_counts": None}
        parts = await asyncio.to_thread(design_parts, svc, ws)
        drc = {**drc, "schematic_missing": parts["missing_from_schematic"],
               "board_missing": parts["missing_from_board"], "sync_gaps": parts["sync_gaps"],
               "requirements": parts["requirements"], "requirement_gaps": parts["requirement_gaps"]}
        return goals.evaluate(ws.goal(), index, drc, svc.store.review.list(design))

    # ------------------------------------------------------------------ steering (messages mid-run)

    @app.post("/api/designs/{design}/steer")
    async def steer(request: Request, design: str, body: dict = Body(...)):
        svc = _svc(request)
        _workspace(svc, design)
        try:
            return svc.store.steering.push(design, body.get("message", ""), author="user", run_id=body.get("run_id"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/designs/{design}/steer")
    async def steer_list(request: Request, design: str, limit: int = 50):
        return _svc(request).store.steering.list(design, limit=limit)

    # ------------------------------------------------------------------ runs and renders

    @app.get("/api/designs/{design}/runs")
    async def runs(request: Request, design: str):
        return _svc(request).store.runs.list(design)

    @app.post("/api/designs/{design}/stop")
    async def stop_run(request: Request, design: str):
        """Ask the streaming run for this design to stop (works even from a fresh browser tab)."""
        _workspace(_svc(request), design)
        flags = request.app.state.stop_flags
        event = flags.setdefault(design, asyncio.Event())
        event.set()
        return {"stopping": True, "design": design}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str, after: int = 0):
        return _svc(request).store.runs.events(run_id, after_seq=after)

    @app.get("/api/renders/{name}")
    async def render_file(request: Request, name: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", name):
            raise HTTPException(400, "bad name")
        path = _svc(request).config.renders / name
        if not path.exists():
            raise HTTPException(404, "no such render")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000"})

    # ------------------------------------------------------------------ live events

    @app.get("/api/designs/{design}/events")
    async def design_events(request: Request, design: str):
        svc = _svc(request)
        ws = _workspace(svc, design)

        def probe() -> dict:
            # Before a board exists, watch the schematic instead so the UI still refreshes.
            watched = ws.board_path if ws.phase() == "board" else ws.schematic_path
            try:
                st = watched.stat()
                board = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                board = None
            return {
                "board": board,
                "version": (ws.head(), ws.branch()),
                "threads": svc.store.review.last_updated(design),
                "runs": svc.store.runs.last_updated(design),
                "steer": svc.store.steering.last_updated(design),
            }

        async def stream():
            # Polls are shared per design (see _sse_fanout) so N open tabs cost one probe, not N,
            # and they never queue behind tool calls in the default thread pool.
            last: dict | None = None
            last_beat = time.monotonic()
            while not await request.is_disconnected():
                try:
                    now = await request.app.state.sse_fanout.get(design, probe, svc)
                except Exception:  # workspace mid-write; try again next tick
                    now = last
                if now is not None and last is not None:
                    if now["version"] != last["version"]:
                        yield f"data: {json.dumps({'type': 'version', 'head': now['version'][0], 'branch': now['version'][1]})}\n\n"
                    if now["board"] != last["board"]:
                        mtime = now["board"][0] if now["board"] else None
                        yield f"data: {json.dumps({'type': 'board', 'mtime': mtime})}\n\n"
                    if now["threads"] != last["threads"]:
                        yield f"data: {json.dumps({'type': 'threads'})}\n\n"
                    if now["runs"] != last["runs"]:
                        yield f"data: {json.dumps({'type': 'run'})}\n\n"
                    if now["steer"] != last["steer"]:
                        yield f"data: {json.dumps({'type': 'steer'})}\n\n"
                elif now is not None:
                    yield f"data: {json.dumps({'type': 'hello', 'head': now['version'][0], 'branch': now['version'][1]})}\n\n"
                last = now if now is not None else last
                if time.monotonic() - last_beat > 15:
                    yield ": keep-alive\n\n"
                    last_beat = time.monotonic()
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
