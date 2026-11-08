"""The harness agent: Pydantic AI tools over the KiCad engine, the design workspace and the review store.

See DESIGN.md, "Agent".
- Every editing tool commits one version.
- Inspection tools return images the model sees.
- When `state.loop` is on, the output validator keeps the agent working until the goal is met.
"""

from __future__ import annotations

import asyncio
import os
import dataclasses
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import (
    Agent,
    ApprovalRequired,
    BinaryContent,
    CustomEvent,
    DeferredToolRequests,
    ModelRetry,
    RunContext,
    ToolReturn,
)
from pydantic_ai.capabilities import Caching, ProcessHistory, Thinking
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.settings import ModelSettings

from kicad_harness.agent import goal as goals
from kicad_harness.agent.state import DesignState, GoalStatus, RenderInfo
from kicad_harness.config import Config

KEEP_RECENT_IMAGES = 4
MAX_LIST = 150


@dataclass
class Services:
    """Long-lived handles shared by every run (one per process)."""

    config: Config
    engine: Any  # kicad_harness.engine.client.Engine
    artifacts: Any  # kicad_harness.engine.artifacts.Artifacts
    store: Any  # kicad_harness.store.db.Store

    @classmethod
    def create(cls, config: Config | None = None) -> "Services":
        from kicad_harness import config as config_mod
        from kicad_harness.engine.artifacts import Artifacts
        from kicad_harness.engine.client import Engine
        from kicad_harness.store.db import Store

        config = config or config_mod.get()
        engine = Engine(config)
        return cls(config=config, engine=engine, artifacts=Artifacts(config, engine), store=Store(config))

    def workspace(self, name: str):
        from kicad_harness.store.workspace import Workspace

        return Workspace.open(self.config, name)


@dataclass
class HarnessDeps:
    """Run dependencies. Implements Pydantic AI's StateHandler protocol through `state`."""

    state: DesignState
    services: Services
    run_id: str | None = None
    started_at: float = field(default_factory=time.time)
    max_minutes: float | None = None
    on_commit: Callable[[dict], None] | None = None


# --------------------------------------------------------------------------- helpers


def _ws(ctx: RunContext[HarnessDeps]):
    design = ctx.deps.state.design
    if not design:
        raise RuntimeError("No design selected: the run's state must include `design`.")
    return ctx.deps.services.workspace(design)


def _snapshot(state: DesignState) -> StateSnapshotEvent:
    return StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state.model_dump(mode="json"))


async def _working(ctx: RunContext[HarnessDeps]) -> tuple[str, dict]:
    """Board hash and pick index of the working copy; raises when there is no board."""
    svc = ctx.deps.services
    ws = _ws(ctx)
    if not ws.board_path.is_file():
        _require_board(ctx, "reading the board")
    data = ws.board_path.read_bytes()
    board_hash, _ = await asyncio.to_thread(svc.artifacts.materialize, data)
    index = await asyncio.to_thread(svc.artifacts.index, board_hash)
    return board_hash, index


class ToolRefusal(Exception):
    """A tool can't act in the current state; the model gets data back, not a retry.

    Raising ``ModelRetry`` for a state problem (no board yet, wrong phase) counts against
    pydantic-ai's 5-tool retry budget, so a model that keeps trying dies with "exceeded max
    retries count of 5". A refusal returns a normal tool result instead.
    """


def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a ``ToolRefusal`` raised inside a tool into an ``ok: false`` tool result."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ToolRefusal as exc:
            return ToolReturn(return_value={"ok": False, "error": str(exc)})

    return wrapper


def _require_board(ctx: RunContext[HarnessDeps], tool: str = "this tool"):
    """Refuse a board-only operation while the design is still a schematic.

    Raises ``ToolRefusal`` so the model gets a clear instruction as ordinary tool data.
    """
    ws = _ws(ctx)
    if ws.phase() == "schematic" or not ws.board_path.is_file():
        raise ToolRefusal(
            f"{tool} needs a board, but this design is in the schematic phase and has none yet. "
            "Draw the schematic with the schematic tools (add_symbol, connect_pins, "
            "generate_schematic_skidl) and call start_board_layout when ERC is clean; the board "
            "tools work after that."
        )


def _empty_index() -> dict:
    """A pick index with nothing on it, for the schematic phase."""
    return {"board": {"edge_bbox": [0, 0, 0, 0], "bbox": [0, 0, 0, 0], "layers": ["F.Cu", "B.Cu"]},
            "footprints": [], "pads": [], "tracks": [], "vias": [], "zones": [], "nets": []}


async def _sync_state(ctx: RunContext[HarnessDeps], *, bump: bool = False) -> None:
    ws = _ws(ctx)
    st = ctx.deps.state
    st.branch = ws.branch()
    st.head = ws.head()
    st.phase = ws.phase()
    st.brief = ws.brief()
    if ws.phase() == "schematic" and not ws.board_path.is_file():
        # Nothing to hash yet; the schematic's own hash tracks progress in this phase.
        await _sync_schematic_state(ctx, bump=bump)
        return
    board_hash, _ = await asyncio.to_thread(ctx.deps.services.artifacts.materialize, ws.board_path.read_bytes())
    if bump or board_hash != st.board_hash:
        st.revision += 1
    st.board_hash = board_hash


async def _evaluate(ctx: RunContext[HarnessDeps]) -> dict:
    """Evaluate the stored goal on the working copy; updates state.goal.

    In the schematic phase the criteria are judged against ERC and whether a board exists yet;
    in the board phase against DRC and the pick index.
    """
    svc = ctx.deps.services
    ws = _ws(ctx)
    threads = svc.store.review.list(ctx.deps.state.design)
    if ws.phase() == "schematic":
        from kicad_harness.engine import kicad_cli

        try:
            report = await asyncio.to_thread(kicad_cli.erc, ws.schematic_path, config=svc.config)
            erc_counts = report["counts"]
        except Exception:  # an unreadable schematic fails the ERC criteria, not the run
            erc_counts = {"errors": 1, "warnings": 0}
        try:
            from kicad_harness.engine.schematic import Schematic

            floating = await asyncio.to_thread(lambda: len(Schematic.load(ws.schematic_path).unconnected_pins()))
        except Exception:
            floating = 0
        facts = await asyncio.to_thread(_parts_facts, ctx)
        fake = {
            "schematic_missing": facts["missing_from_schematic"],
            "board_missing": facts["missing_from_board"],
            "sync_gaps": facts["sync_gaps"],
            "requirements": facts["requirements"],
            "requirement_gaps": facts["requirement_gaps"],
            "erc_counts": erc_counts,
            "board_laid_out": ws.board_path.is_file(),
            "unconnected_pins": floating,
            "schematic_layout": await asyncio.to_thread(_schematic_layout_facts, ws),
        }
        evaluation = goals.evaluate(ws.goal(), {}, fake, threads)
        ctx.deps.state.goal = GoalStatus.model_validate(evaluation)
        return evaluation
    board_hash, index = await _working(ctx)
    drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
    drc = goals.with_board_state(drc, ws.board_path.is_file())
    # ERC is a property of the schematic in either phase; the board criteria do not measure it.
    from kicad_harness.engine import kicad_cli as _kc

    if ws.schematic_path.is_file():
        try:
            erc_report = await asyncio.to_thread(_kc.erc, ws.schematic_path, config=svc.config)
            drc = {**drc, "erc_counts": erc_report["counts"]}
        except Exception:
            drc = {**drc, "erc_counts": None}
    facts = await asyncio.to_thread(_parts_facts, ctx)
    drc = {
        **drc,
        "schematic_missing": facts["missing_from_schematic"],
        "board_missing": facts["missing_from_board"],
        "sync_gaps": facts["sync_gaps"],
        "requirements": facts["requirements"],
        "requirement_gaps": facts["requirement_gaps"],
        "schematic_layout": await asyncio.to_thread(_schematic_layout_facts, ws),
    }
    evaluation = goals.evaluate(ws.goal(), index, drc, threads)
    ctx.deps.state.goal = GoalStatus.model_validate(evaluation)
    return evaluation


def _schematic_layout_facts(ws) -> dict[str, Any] | None:
    """Legibility measurement of the schematic, or None when it cannot be read.

    None (rather than a zero score) makes the [legible] criterion fail: an unmeasured sheet must
    never look like a passing one.
    """
    from kicad_harness.engine import layout
    from kicad_harness.engine.schematic import Schematic

    if not ws.schematic_path.is_file():
        return None
    try:
        return layout.analyze(Schematic.load(ws.schematic_path))
    except Exception:
        return None


def _schematic_image(svc, ws) -> tuple[bytes, str]:
    """PNG bytes and URL of the current schematic sheet (cached by content hash)."""
    import hashlib

    from kicad_harness.engine import render as render_mod

    key = hashlib.sha256(ws.schematic_path.read_bytes()).hexdigest()[:16]
    out = Path(svc.config.renders) / f"schematic-{key}.png"
    if not out.is_file():
        render_mod.schematic_png(ws.schematic_path, out)
    return out.read_bytes(), f"/api/renders/{out.name}"


def _parts_facts(ctx: RunContext[HarnessDeps]) -> dict[str, Any]:
    """Ground truth about which parts exist: schematic symbols and board footprints."""
    from kicad_harness.agent.audit import design_parts

    return design_parts(ctx.deps.services, _ws(ctx))


async def _adversarial_audit(ctx: RunContext[HarnessDeps], model: Any = None) -> tuple[bool, str]:
    """Have a reviewer check the artifacts against the original brief.

    Returns ``(complete, summary)``. The reviewer is a separate agent with adversarial
    instructions, so it is not grading the working agent's own summary.
    """
    from pydantic_ai import Agent as _Agent

    from kicad_harness.agent import models as model_catalog
    from kicad_harness.agent.audit import AUDIT_INSTRUCTIONS, AuditResult, render_inventory, summarise

    # A scripted/test model cannot review anything, and a real review would make network calls
    # inside tests; only real runs get audited.
    if type(model).__name__ in ("FunctionModel", "TestModel"):
        return True, ""

    svc = ctx.deps.services
    ws = _ws(ctx)
    brief = ws.brief() or (ws.goal() or {}).get("summary")
    if not brief:
        return True, ""  # nothing was asked for, so nothing to audit against

    facts = await asyncio.to_thread(_parts_facts, ctx)
    evaluation = await _evaluate(ctx)
    inventory = {
        "brief": brief,
        "parts_on_board": facts["board_refs"],
        "parts_in_schematic": facts["schematic_refs"],
        "missing_from_schematic": facts["missing_from_schematic"],
        "missing_from_board": facts["missing_from_board"],
        "goal_criteria": [
            {"id": c["id"], "passed": c["passed"], "detail": c["detail"]}
            for c in evaluation.get("criteria", [])
        ],
    }
    reviewer = _Agent(
        model_catalog.resolve(current_model()),
        output_type=AuditResult,
        instructions=AUDIT_INSTRUCTIONS,
    )
    try:
        result = await reviewer.run(render_inventory(brief, inventory))
    except Exception:
        return True, ""  # a broken reviewer must not block the loop
    verdict: AuditResult = result.output
    return verdict.complete, summarise(verdict)


def _trailers(ctx: RunContext[HarnessDeps], tool: str) -> dict[str, str]:
    trailers = {"Harness-Tool": tool}
    if ctx.deps.run_id:
        trailers["Harness-Run"] = ctx.deps.run_id
    if ctx.tool_call_id:
        trailers["Harness-Tool-Call"] = ctx.tool_call_id
    return trailers


def _version_brief(version: Any) -> dict | None:
    if not version:
        return None
    v = version if isinstance(version, dict) else dataclasses.asdict(version)
    return {"sha": v.get("sha"), "short": v.get("short"), "subject": v.get("subject") or v.get("message")}


def _engine_error_text(exc: Exception) -> str:
    kind = getattr(exc, "type", type(exc).__name__)
    return f"{kind}: {exc}"


def _split_pin(text: str) -> tuple[str, str]:
    """Split ``"U1.3"`` into ``("U1", "3")``; raises ``ValueError`` with a usable message."""
    if not isinstance(text, str) or "." not in text:
        raise ValueError(f"pins are written 'REF.PIN' (e.g. 'U1.3'); got {text!r}")
    ref, number = text.rsplit(".", 1)
    if not ref or not number:
        raise ValueError(f"pins are written 'REF.PIN' (e.g. 'U1.3'); got {text!r}")
    return ref, number


def _schematic(ctx: RunContext[HarnessDeps], want: str | None = None):
    """The working-copy schematic, with the libraries it actually uses resolved.

    The file's own ``lib_symbols`` block comes first (that is what a saved schematic carries);
    any library it references but does not embed is parsed on demand. Nothing is preloaded —
    parsing all ~200 installed libraries takes over two minutes on a cold start.
    """
    from kicad_harness.engine.schematic import Schematic

    doc = Schematic.load(_ws(ctx).schematic_path)
    needed = {lib for lib in (p.lib_id.split(":", 1)[0] for p in doc.placements()) if lib}
    if want:
        needed.add(want)
    for name in needed:
        if doc.libraries.get(name):
            continue
        table = _library(ctx, name)
        if table:
            doc.libraries[name] = table
    return doc


_KI_LIBRARIES: dict[str, dict] = {}
_KI_LIBRARY_DIRS: list[Path] | None = None


def _kiCad_libraries(ctx: RunContext[HarnessDeps]) -> dict[str, dict]:
    """``{library: {symbol: LibrarySymbol}}`` for KiCad's installed symbol libraries.

    Parsing every installed library takes over two minutes on a cold start, so nothing is
    preloaded: ``_library`` resolves one library on demand and caches it. Callers that need a
    specific library ask for it by name.
    """
    global _KI_LIBRARY_DIRS
    if _KI_LIBRARY_DIRS is None:
        _KI_LIBRARY_DIRS = _symbol_libraries(ctx)
    return _KI_LIBRARIES


def _library(ctx: RunContext[HarnessDeps], name: str) -> dict:
    """One symbol library by name (``Device``), parsed once and cached across runs."""
    from kicad_harness.engine.schematic import read_library

    _kiCad_libraries(ctx)
    if name in _KI_LIBRARIES:
        return _KI_LIBRARIES[name]
    for path in _KI_LIBRARY_DIRS or []:
        if path.stem == name:
            try:
                _KI_LIBRARIES[name] = read_library(path)
            except Exception:  # a broken library must not block editing the schematic
                _KI_LIBRARIES[name] = {}
            return _KI_LIBRARIES[name]
    _KI_LIBRARIES[name] = {}
    return _KI_LIBRARIES[name]


def _symbol_libraries(ctx: RunContext[HarnessDeps]) -> list[Path]:
    """KiCad's own ``.kicad_sym`` files, plus any in the harness's configured directories."""
    import os

    dirs: list[Path] = []
    env = os.environ.get("HARNESS_SYMBOL_DIRS")
    if env:
        dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    else:
        config = ctx.deps.services.config
        app = Path(config.kicad_app)
        dirs += [
            Path("/usr/share/kicad/symbols"),
            app / "Contents" / "SharedSupport" / "symbols",
            app / "symbols",
        ]
    files: list[Path] = []
    for directory in dirs:
        if directory.is_dir():
            files += sorted(p for p in directory.glob("*.kicad_sym") if p.is_file())
    return files


async def _schematic_commit(
    ctx: RunContext[HarnessDeps], tool: str, subject: str, payload: dict
) -> ToolReturn:
    """Commit the schematic working copy as a version and push the new state to the UI."""
    ws = _ws(ctx)
    version = await asyncio.to_thread(
        lambda: ws.commit(subject, author="agent", trailers=_trailers(ctx, tool))
    )
    await _sync_schematic_state(ctx, bump=True)
    if version and ctx.deps.on_commit:
        ctx.deps.on_commit({"tool": tool, **_version_brief(version)})
    return ToolReturn(
        return_value={"ok": True, **payload, "version": _version_brief(version)},
        metadata=[_snapshot(ctx.deps.state)],
    )


async def _schematic_edit(
    ctx: RunContext[HarnessDeps],
    tool: str,
    subject: str,
    work: Callable[[Any], Any],
    want: str | None = None,
) -> ToolReturn:
    """Apply ``work(doc)`` to the schematic, save it, and commit a version.

    ``want`` names a symbol library the edit needs (e.g. "Device"); it is resolved and attached
    to the document before ``work`` runs.
    """
    # The schematic is not frozen by layout: it is one view of the same design, so it can be
    # edited at any time. The board is reconciled afterwards (see sync_board_to_schematic).
    ws = _ws(ctx)
    try:
        doc = await asyncio.to_thread(_schematic, ctx, want)
        result = await asyncio.to_thread(work, doc)
        await asyncio.to_thread(doc.save)
    except Exception as exc:  # engine/lib errors go back to the model as data, not retries
        return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
    payload = result if isinstance(result, dict) else {"result": result}
    return await _schematic_commit(ctx, tool, subject, payload)


async def _sync_schematic_state(ctx: RunContext[HarnessDeps], *, bump: bool = False) -> None:
    """Refresh phase, brief, head and the schematic's content hash in the shared state."""
    import hashlib

    ws = _ws(ctx)
    st = ctx.deps.state
    st.branch = ws.branch()
    st.head = ws.head()
    st.phase = ws.phase()
    st.brief = ws.brief()
    try:
        data = ws.schematic_path.read_bytes()
    except FileNotFoundError:
        st.schematic_hash = None
    else:
        digest = hashlib.sha256(data).hexdigest()[:16]
        if bump or digest != st.schematic_hash:
            st.revision += 1
        st.schematic_hash = digest


async def _edit(
    ctx: RunContext[HarnessDeps],
    tool: str,
    subject: str,
    work: Callable[[str], Any],
    *,
    risky: bool = False,
) -> ToolReturn:
    """Run a board edit under the workspace lock, commit a version, push state to the UI."""
    st = ctx.deps.state
    if risky and st.supervised and not ctx.tool_call_approved:
        raise ApprovalRequired()
    ws = _ws(ctx)
    if ws.phase() == "schematic":
        return ToolReturn(return_value={
            "ok": False,
            "error": (
                "This design has no board yet: it is in the schematic phase. Draw the schematic with "
                "the schematic tools (add_symbol, connect_pins, generate_schematic_skidl, ...) and call "
                "start_board_layout when ERC is clean; then the board tools become available."
            ),
        })

    def run() -> tuple[Any, Any]:
        with ws.lock:
            result = work(str(ws.board_path))
            version = ws.commit(subject, author="agent", trailers=_trailers(ctx, tool))
            return result, version

    try:
        result, version = await asyncio.to_thread(run)
    except Exception as exc:  # engine errors go back to the model as data, not retries
        return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
    await _sync_state(ctx, bump=True)
    brief = _version_brief(version)
    if brief and ctx.deps.on_commit:
        ctx.deps.on_commit({"tool": tool, **brief})
    return ToolReturn(
        return_value={"ok": True, "result": result, "version": brief or "no change"},
        metadata=[_snapshot(st)],
    )


def _keep_recent_images(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Replace all but the newest few tool-returned images with a placeholder to bound context growth."""
    seen = 0
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        msg = out[i]
        if not isinstance(msg, ModelRequest):
            continue
        parts = list(msg.parts)
        changed = False
        for j in range(len(parts) - 1, -1, -1):
            part = parts[j]
            if not (isinstance(part, ToolReturnPart) and isinstance(part.content, list)):
                continue
            content = list(part.content)
            for k in range(len(content) - 1, -1, -1):
                item = content[k]
                if isinstance(item, BinaryContent) and item.media_type.startswith("image/"):
                    seen += 1
                    if seen > KEEP_RECENT_IMAGES:
                        content[k] = "[older render omitted to save context; call render_view again to look]"
                        changed = True
            if changed:
                parts[j] = dataclasses.replace(part, content=content)
        if changed:
            out[i] = dataclasses.replace(msg, parts=parts)
    return out


@dataclass(kw_only=True)
class SteeringDelivered(CustomEvent):
    """AG-UI CUSTOM event: user messages sent mid-run have reached the agent."""

    messages: list[str]


def _steering_hooks() -> Hooks:
    """Before each model request, hand the agent any messages the user sent while it was working."""
    hooks = Hooks()

    @hooks.on.before_model_request
    async def deliver_steering(ctx: RunContext[HarnessDeps], request_context):
        deps = ctx.deps
        if deps is None or not deps.state.design or not deps.run_id:
            return request_context
        pending = await asyncio.to_thread(deps.services.store.steering.take, deps.state.design, deps.run_id)
        if pending:
            lines = "\n".join(f"- {p['message']}" for p in pending)
            ctx.enqueue(f"Message from the user while you were working (take it into account now):\n{lines}")
            try:
                await ctx.emit(SteeringDelivered(messages=[p["message"] for p in pending]))
            except Exception:  # an event sink is optional (plain agent.run has none)
                pass
        return request_context

    return hooks


def _center_of(entities: list[dict]) -> tuple[float, float, float]:
    """Centre and a sensible view size (mm) for a set of resolved entities."""
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if "bbox" in e and e["bbox"]:
            x, y, w, h = e["bbox"]
            xs += [x, x + w]
            ys += [y, y + h]
        elif "x1" in e:
            xs += [e["x1"], e["x2"]]
            ys += [e["y1"], e["y2"]]
        elif "x" in e:
            half = max(e.get("w", 0), e.get("h", 0), e.get("size", 0)) / 2
            xs += [e["x"] - half, e["x"] + half]
            ys += [e["y"] - half, e["y"] + half]
    if not xs:
        raise ValueError("target has no geometry")
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    return cx, cy, max(6.0, span * 2.2 + 3.0)


def _resolve(index: dict, ref: str) -> list[dict]:
    from kicad_harness.engine.geometry import resolve

    return resolve(index, ref)


def _uuids_for(index: dict, refs: list[str]) -> list[str]:
    uuids: list[str] = []
    for ref in refs:
        for entity in _resolve(index, ref):
            if entity.get("uuid"):
                uuids.append(entity["uuid"])
    return uuids


def _compact_component(fp: dict) -> dict:
    return {
        "ref": fp["ref"],
        "value": fp.get("value"),
        "footprint": fp.get("footprint"),
        "x": round(fp["x"], 3),
        "y": round(fp["y"], 3),
        "rotation": round(fp.get("rotation", 0), 1),
        "side": fp.get("side"),
    }


def _thread_brief(thread: dict) -> dict:
    anchor = thread.get("anchor") or {}
    resolved = thread.get("resolved_anchor") or {}
    where = {k: anchor.get(k) for k in ("kind", "ref", "pad", "net", "layer") if anchor.get(k)}
    if resolved:
        where["at"] = [round(resolved.get("x", 0), 3), round(resolved.get("y", 0), 3)]
        where["anchor_state"] = resolved.get("state")
    return {
        "number": thread["number"],
        "status": thread["status"],
        "severity": thread["severity"],
        "tags": thread.get("tags", []),
        "title": thread.get("title"),
        "body": thread.get("body"),
        "where": where,
        "on_version": (thread.get("created_sha") or "")[:8],
        "replies": [{"author": r["author"], "body": r["body"]} for r in thread.get("replies", [])[-3:]],
    }


INSTRUCTIONS = """\
You are a PCB layout engineer editing a KiCad 10 board through tools. A human watches every step live in a web UI \
(board view, your reasoning and tool calls, version history), can comment on specific parts of the board, and can roll \
back your work.

Goal handling:
- When the user states a goal in words ("move the LED to the left edge", "keep it under 30 mm"), turn it into machine-checkable criteria yourself and save them with `set_goal`. Inspect the board first (design_summary, query, measure) so references, regions and distances are real, then call `check`. Never ask the user to pick criterion types.
- `set_goal` takes a short `summary` (their intent, in their words where possible) and `criteria` from: drc_errors, drc_warnings, unconnected, board_size (max_w/max_h), edge_clearance (min_mm), max_distance/min_distance (a, b, max_mm/min_mm), within_region (ref, region [x1,y1,x2,y2]), open_threads (max, optional severities), schematic_legibility (max).

Phases:
- A design starts in the SCHEMATIC phase: there is no board yet, only `schematic.kicad_sch`. Draw the circuit, keep ERC clean (erc_errors/erc_warnings criteria), then call `start_board_layout`. That creates the board, places the footprints from KiCad's own netlist and assigns their nets, so layout starts electrically correct. After it, you are in the BOARD phase and move parts and route.
- Prefer `generate_schematic_skidl` to draw a whole design from a parts and nets list; use the individual symbol and wiring tools to fix or extend it. Every part needs a real KiCad footprint (`Library:Footprint`) or it cannot be placed on the board.
- A schematic is not finished when it is merely ERC-clean: it must be readable. Call `check_schematic_layout` after drawing or editing; while it reports a score above 0, call `tidy_schematic` and check again. The [legible] goal criterion is measured from the drawing itself and is mandatory, so the loop will not end until it passes.
- Arrange by circuit, not by reference: a part belongs beside the part it serves. Series/pull-up resistors sit by the input they protect, decoupling capacitors by the IC they decouple, and each power symbol at the pin it names. `tidy_schematic` puts 2-pin passives beside their owner; `check_schematic_layout` lists the remaining offenders under `findings` (a passive far from its owner, a point-to-point net left as two distant labels, a power symbol whose value contradicts its net). Act on those findings rather than accepting them.
- `findings` also surfaces broken power symbols (e.g. a GND symbol wired to +5V). Fix the netlist or the symbol first: no placement can put a ground next to a part while the symbol names a different net.
- Once the parts are close, call `route_schematic` to draw direct wires for point-to-point nets instead of leaving two labels. It keeps the net names and rolls back any wire that would change connectivity; if it skips a net, the two parts are too far apart or a wire would cross a pin — move them closer and try again.
- Look at the sheet as an image too: `check_schematic_layout` returns the rendered schematic with its score, and `check` returns the rendered board with its criteria. A sheet can score 0 and still look wrong at a glance — a part marooned at the edge, channel rows that do not line up, a wall of duplicate labels. Use `render_schematic` / `render_view` for a closer look, and fix what you see before calling the work finished.

Part sourcing (do this before choosing parts):
- Boards are assembled by JLCPCB, so a part is only real if JLCPCB stocks it. Use `find_parts` to search their catalogue: pass `query` for a part number (e.g. "NE555" finds NE555DR) or `category` to browse by type (e.g. "LED", "Relays"). It reports stock, unit price, and whether a part is `basic` (no assembly fee) or `extended` (a per-part reel fee). Prefer basic parts with large stock unless the user asks otherwise. The catalogue's description text is empty, so searching for words like "red LED" will not work — browse the category instead.
- Very few parts are basic: if `find_parts` returns an empty list, read its `empty_reason` (it says which filter removed everything) and retry with that filter relaxed rather than repeating the same query.
- Use `check_part` on each part you intend to use: it returns the JLCPCB matches plus datasheet availability. If `usable_for_jlc` is false, say so and choose a stocked alternative rather than designing a board that cannot be made.
- Use `part_datasheet` to confirm a pinout or rating when you are not certain; it returns datasheet text. If i2cdb has no record, a supplier datasheet URL is included — do not invent pin numbers you have not verified.
- Mention the LCSC codes and the basic/extended split to the user when you finish the schematic.

Conventions:
- Coordinates are millimetres in KiCad board space: x to the right, y DOWN. Angles are degrees, counter-clockwise.
- Refer to parts by reference designator (U1), pads as REF.PAD (U1.3), nets as net:NAME.

How to work:
1. Orient: call design_summary, then render_view to look at the board. Use query and measure for exact numbers; \
never guess coordinates from an image alone.
2. Edit with the editing tools. Every successful edit is committed as a new version the user can see and roll back, \
so prefer a few deliberate edits over many tiny ones.
3. Verify after edits: call check (DRC plus the goal criteria) and, for the schematic, check_schematic_layout. Look at the result visually with render_view, zoomed \
on whatever you changed (pass target, view_mm and highlight). Tracks under solder mask are easier to judge in 2D than 3D.
4. Routing: after moving parts, rip up the affected tracks (delete_tracks) and call autoroute rather than drawing \
long routes by hand; hand-route only short or critical connections.
5. Review feedback: list_feedback shows the user's comments, anchored to specific entities. Address must_fix and \
warning items first. After handling one, call mark_addressed with a short note (only the user can resolve it); reply \
with reply_to_thread if you disagree or need clarification.
6. Never claim the goal is met unless check reports every criterion passing. If you are stuck, say exactly what \
blocks you.
Keep messages to the user short: what you changed and why.
"""


_model_override: str | None = None


def current_model() -> str:
    """The model new runs use: the UI's selection, else the configured HARNESS_MODEL."""
    from kicad_harness import config as config_mod

    return _model_override or config_mod.get().model


def set_model(model: str | None) -> None:
    """Select the model for subsequent runs. Drops the cached agent so it is rebuilt lazily."""
    global _model_override, _agent
    _model_override = model or None
    _agent = None


def build_agent(model: str | None = None) -> Agent[HarnessDeps, str | DeferredToolRequests]:
    """Create the harness agent. `model` defaults to `current_model()`.

    ``max_tokens`` and thinking effort are configurable because a reasoning model can spend the
    whole completion budget on thinking, returning ``finish_reason='length'`` with no actionable
    output — which pydantic-ai raises as a fatal ``UnexpectedModelBehavior`` ("Model token limit
    ... exceeded before any response was generated").
    """
    from kicad_harness.agent import models as model_catalog

    # Follow the model's own output limit rather than a fixed number; the server and CLI
    # additionally adapt it up or down at runtime if a provider disagrees.
    chosen = model or current_model()
    max_tokens = model_catalog.budget_for(chosen)
    thinking = os.environ.get("HARNESS_THINKING", "low")

    agent: Agent[HarnessDeps, str | DeferredToolRequests] = Agent(
        model_catalog.resolve(chosen),
        name="kicad_harness",
        deps_type=HarnessDeps,
        output_type=[str, DeferredToolRequests],
        instructions=INSTRUCTIONS,
        capabilities=[
            Caching(),
            Thinking(effort=thinking),
            ProcessHistory(_keep_recent_images),
            _steering_hooks(),
        ],
        retries={"tools": 5, "output": 60},
        model_settings=ModelSettings(max_tokens=max_tokens),
        defer_model_check=True,
    )

    @agent.instructions
    def session_context(ctx: RunContext[HarnessDeps]) -> str:
        st = ctx.deps.state
        if not st.design:
            return "No design is selected."
        ws = _ws(ctx)
        phase = ws.phase()
        lines = [
            f"Design: {st.design} (branch {ws.branch()}, head {ws.head()[:8]}, phase: {phase}).",
            goals.describe(ws.goal()),
        ]
        brief = ws.brief()
        if brief:
            lines.append(f"The user asked for: {brief}")
        if phase == "schematic":
            lines.append(
                "SCHEMATIC PHASE: there is no board yet. Draw the circuit — either generate_schematic_skidl "
                "for a whole design at once, or add_symbol/connect_pins/add_schematic_label by hand. Check with "
                "check_schematic (ERC), then call start_board_layout to create the netlisted board. Board tools "
                "(move_footprint, autoroute, drc, ...) will refuse until then."
            )
        if st.loop:
            lines.append(
                "Goal loop is ON: keep inspecting, editing and checking until every criterion passes. "
                "You will be sent back to work if you stop early."
            )
        if st.supervised:
            lines.append("Supervised mode is ON: destructive tools pause for the user's approval.")
        if st.selection:
            lines.append(f"The user currently has this selected in the viewer: {json.dumps(st.selection)}")
        if st.viewing and st.viewing not in ("working", st.head):
            lines.append(f"The user is looking at older version {st.viewing[:8]}, not the working copy.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ inspection

    @agent.tool
    @guarded
    async def design_summary(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Board outline, components (ref, value, footprint, position, side), nets, DRC counts, goal status and open review threads."""
        svc = ctx.deps.services
        ws = _ws(ctx)
        await _sync_state(ctx)
        if ws.phase() == "schematic" and not ws.board_path.is_file():
            # A model's first move is often design_summary; answer it for the schematic rather
            # than failing, so the run starts with the right picture.
            return await schematic_summary(ctx)
        board_hash, index = await _working(ctx)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        evaluation = await _evaluate(ctx)
        threads = svc.store.review.list(ctx.deps.state.design)
        ex, ey, ew, eh = index["board"]["edge_bbox"]
        summary = {
            "design": ctx.deps.state.design,
            "head": (ctx.deps.state.head or "")[:8],
            "board_outline": {"x": round(ex, 3), "y": round(ey, 3), "width": round(ew, 3), "height": round(eh, 3)},
            "components": [_compact_component(fp) for fp in index["footprints"]],
            "nets": [n for n in index.get("nets", []) if n],
            "track_segments": len(index.get("tracks", [])),
            "vias": len(index.get("vias", [])),
            "zones": len(index.get("zones", [])),
            "drc": drc.get("counts", {}),
            "goal": evaluation,
            "open_threads": [
                _thread_brief(t) for t in threads if t["status"] in goals.OPEN_STATUSES
            ],
        }
        return ToolReturn(return_value=summary, metadata=[_snapshot(ctx.deps.state)])

    @agent.tool
    @guarded
    async def query(
        ctx: RunContext[HarnessDeps],
        kind: str,
        ref: str | None = None,
        net: str | None = None,
        region: list[float] | None = None,
    ) -> dict:
        """List entities of one kind from the current board.

        Args:
            kind: footprint, pad, track, via or zone.
            ref: Only items of this footprint (U1) or pad (U1.3).
            net: Only items on this net (GND).
            region: Only items whose position lies in [x1, y1, x2, y2] (mm).
        """
        plural = {"footprint": "footprints", "pad": "pads", "track": "tracks", "via": "vias", "zone": "zones"}
        if kind not in plural:
            return ToolReturn(return_value={
                "ok": False, "error": f"kind must be one of {sorted(plural)}",
            })
        _, index = await _working(ctx)
        items = index.get(plural[kind], [])
        if ref:
            r, _, p = ref.partition(".")
            items = [i for i in items if i.get("ref") == r and (not p or i.get("pad") == p)]
        if net:
            items = [i for i in items if i.get("net") == net]
        if region:
            x1, y1, x2, y2 = region
            x1, x2, y1, y2 = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)

            def inside(i: dict) -> bool:
                if "x" in i:
                    return x1 <= i["x"] <= x2 and y1 <= i["y"] <= y2
                if "x1" in i:
                    return any(x1 <= x <= x2 and y1 <= y <= y2 for x, y in ((i["x1"], i["y1"]), (i["x2"], i["y2"])))
                return True

            items = [i for i in items if inside(i)]
        trimmed = [{k: v for k, v in i.items() if k not in ("polygon", "outline", "filled", "courtyard")} for i in items]
        return {"count": len(trimmed), "items": trimmed[:MAX_LIST], "truncated": len(trimmed) > MAX_LIST}

    @agent.tool
    @guarded
    async def measure(ctx: RunContext[HarnessDeps], a: str, b: str) -> Any:
        """Edge-to-edge copper distance between two things (0 = touching).

        Args:
            a: U1, U1.3, net:GND, a UUID, or "x,y".
            b: Same forms as `a`.
        """
        from kicad_harness.engine.geometry import measure as measure_fn

        _, index = await _working(ctx)
        try:
            return measure_fn(index, a, b)
        except Exception as exc:
            raise ModelRetry(f"Could not measure {a!r} to {b!r}: {exc}") from exc

    @agent.tool
    @guarded
    async def check(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Run KiCad DRC on the current board and evaluate every goal criterion."""
        svc = ctx.deps.services
        await _sync_state(ctx)
        board_hash, _ = await _working(ctx)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        evaluation = await _evaluate(ctx)

        def brief(v: dict) -> dict:
            return {
                "type": v.get("type"),
                "severity": v.get("severity"),
                "description": v.get("description"),
                "items": [
                    {"description": it.get("description"), "x": it.get("x"), "y": it.get("y"), "uuid": it.get("uuid")}
                    for it in v.get("items", [])[:3]
                ],
            }

        result = {
            "drc_counts": drc.get("counts", {}),
            "violations": [brief(v) for v in drc.get("violations", [])[:30]],
            "unconnected": [brief(v) for v in drc.get("unconnected", [])[:20]],
            "goal_met": evaluation["met"],
            "criteria": evaluation["criteria"],
        }
        # A plain image of the layout, so the model can catch what DRC cannot: a part stranded
        # at the edge, a wall of labels, a channel that reads backwards.
        ws = _ws(ctx)
        if ws.board_path.is_file():
            try:
                spec = {"mode": "2d", "side": "front"}
                png_path, url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
                ctx.deps.state.last_render = RenderInfo(
                    url=url, caption="Board layout, front (fitted)", view=spec)
                png = Path(png_path).read_bytes()
                return ToolReturn(
                    return_value=[result, BinaryContent(data=png, media_type="image/png")],
                    metadata=[_snapshot(ctx.deps.state)],
                )
            except Exception:
                pass
        return ToolReturn(return_value=result, metadata=[_snapshot(ctx.deps.state)])

    @agent.tool
    @guarded
    async def render_view(
        ctx: RunContext[HarnessDeps],
        target: str | None = None,
        x: float | None = None,
        y: float | None = None,
        view_mm: float | None = None,
        mode: str = "2d",
        side: str | None = None,
        rotate: list[float] | None = None,
        highlight: list[str] | None = None,
        labels: bool = True,
    ) -> ToolReturn:
        """Render the board as an image you can see.

        Args:
            target: Centre the view on this (U1, U1.3, net:GND, a UUID). Overrides x/y.
            x: View centre x in mm (with y). Omit target and x/y for the whole board.
            y: View centre y in mm.
            view_mm: Size of the visible area in mm (zoom). Defaults to fit the target or the board.
            mode: "2d" (KiCad layer plot: crisp copper and silkscreen) or "3d" (raytraced, shows parts and orientation).
            side: 2d: "front" (default) or "back". 3d: "top" (default), "bottom", or an edge view
                ("left", "right", "front", "back").
            rotate: 3d only: [x, y, z] degrees, e.g. [-45, 0, 45] for an isometric view (frames the whole board).
            highlight: 2d only: things to outline in cyan (refs, REF.PAD, net:NAME, UUIDs).
            labels: 2d only: draw reference designators (and net names on highlighted pads).
        """
        svc = ctx.deps.services
        await _sync_state(ctx)
        board_hash, index = await _working(ctx)
        center: list[float] | None = None
        if target:
            try:
                cx, cy, auto_view = _center_of(_resolve(index, target))
            except Exception as exc:
                raise ModelRetry(f"Unknown render target {target!r}: {exc}") from exc
            center = [cx, cy]
            view_mm = view_mm or auto_view
        elif x is not None and y is not None:
            center = [x, y]
        if mode == "2d":
            side_2d = {"top": "front", "bottom": "back"}.get(side or "front", side or "front")
            if side_2d not in ("front", "back"):
                raise ModelRetry('In 2d, side must be "front" or "back"')
            try:
                highlight_uuids = _uuids_for(index, highlight or [])
            except Exception as exc:
                raise ModelRetry(f"Unknown highlight item: {exc}") from exc
            spec: dict[str, Any] = {"mode": "2d", "side": side_2d, "center": center, "view_mm": view_mm,
                                    "highlight": highlight_uuids, "labels": labels}
        elif mode == "3d":
            spec = {"mode": "3d", "side": side or "top", "center": center, "view_mm": view_mm,
                    "rotate": rotate, "quality": "basic"}
        else:
            raise ModelRetry('mode must be "2d" or "3d"')
        try:
            png_path, url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        what = target or (f"({x}, {y})" if center else "whole board")
        caption = f"{mode.upper()} {spec['side']} view of {what}" + (f", {view_mm:.1f} mm tall" if view_mm else "")
        ctx.deps.state.last_render = RenderInfo(url=url, caption=caption, view=spec)
        png = Path(png_path).read_bytes()
        meta = json.dumps({"url": url, "caption": caption, "view": spec})
        return ToolReturn(
            return_value=[meta, BinaryContent(data=png, media_type="image/png")],
            metadata=[_snapshot(ctx.deps.state)],
        )

    # ------------------------------------------------------------------ review

    @agent.tool
    @guarded
    async def list_feedback(ctx: RunContext[HarnessDeps], status: str = "open") -> Any:
        """The user's review threads, anchored to entities on the board and located on the current version.

        Args:
            status: open (open + reopened), addressed, resolved, wontfix, or all.
        """
        svc = ctx.deps.services
        _, index = await _working(ctx)
        threads = svc.store.review.list(ctx.deps.state.design)
        if status == "open":
            threads = [t for t in threads if t["status"] in goals.OPEN_STATUSES]
        elif status != "all":
            threads = [t for t in threads if t["status"] == status]
        for t in threads:
            t["resolved_anchor"] = svc.store.review.resolve_anchor(t, index)
        order = {"must_fix": 0, "warning": 1, "suggestion": 2, "info": 3}
        threads.sort(key=lambda t: (order.get(t["severity"], 9), t["number"]))
        return {"count": len(threads), "threads": [_thread_brief(t) for t in threads]}

    @agent.tool
    async def reply_to_thread(ctx: RunContext[HarnessDeps], number: int, message: str) -> dict:
        """Reply to review thread #number (to explain a decision or ask the user a question)."""
        svc = ctx.deps.services
        thread = svc.store.review.by_number(ctx.deps.state.design, number)
        if not thread:
            raise ModelRetry(f"No thread #{number}")
        svc.store.review.reply(thread["id"], message, author="agent", sha=_ws(ctx).head())
        return {"ok": True, "thread": number}

    @agent.tool
    async def mark_addressed(ctx: RunContext[HarnessDeps], number: int, note: str) -> dict:
        """Mark review thread #number as addressed by the current version, with a short note on what changed."""
        svc = ctx.deps.services
        thread = svc.store.review.by_number(ctx.deps.state.design, number)
        if not thread:
            raise ModelRetry(f"No thread #{number}")
        head = _ws(ctx).head()
        svc.store.review.set_status(thread["id"], "addressed", by="agent", sha=head, note=note)
        return {"ok": True, "thread": number, "addressed_in": head[:8]}

    # ------------------------------------------------------------------ editing

    engine = lambda ctx: ctx.deps.services.engine  # noqa: E731

    @agent.tool(sequential=True)
    async def move_footprint(
        ctx: RunContext[HarnessDeps], ref: str, x: float, y: float, rotation: float | None = None
    ) -> ToolReturn:
        """Move a footprint to an absolute position (its anchor point), optionally setting its rotation (degrees)."""
        subject = f"Move {ref} to ({x:g}, {y:g})" + (f" at {rotation:g} deg" if rotation is not None else "")
        return await _edit(
            ctx, "move_footprint", subject,
            lambda b: engine(ctx).call("move_footprint", board=b, ref=ref, x=x, y=y, rotation=rotation),
        )

    @agent.tool(sequential=True)
    async def rotate_footprint(ctx: RunContext[HarnessDeps], ref: str, angle: float) -> ToolReturn:
        """Rotate a footprint by `angle` degrees (relative, counter-clockwise) about its anchor."""
        return await _edit(
            ctx, "rotate_footprint", f"Rotate {ref} by {angle:g} deg",
            lambda b: engine(ctx).call("rotate_footprint", board=b, ref=ref, angle=angle),
        )

    @agent.tool(sequential=True)
    async def flip_footprint(ctx: RunContext[HarnessDeps], ref: str) -> ToolReturn:
        """Move a footprint to the other side of the board (front <-> back), in place."""
        return await _edit(
            ctx, "flip_footprint", f"Flip {ref} to the other side",
            lambda b: engine(ctx).call("flip_footprint", board=b, ref=ref),
        )

    @agent.tool(sequential=True)
    async def move_reference_text(ctx: RunContext[HarnessDeps], ref: str, x: float, y: float) -> ToolReturn:
        """Move a footprint's silkscreen reference designator to an absolute position (mm)."""
        return await _edit(
            ctx, "move_reference_text", f"Move {ref} silkscreen label to ({x:g}, {y:g})",
            lambda b: engine(ctx).call("move_reference_text", board=b, ref=ref, x=x, y=y),
        )

    @agent.tool(sequential=True)
    async def delete_tracks(ctx: RunContext[HarnessDeps], nets: list[str] | None = None) -> ToolReturn:
        """Rip up tracks and vias: only on `nets` if given, otherwise ALL routing on the board."""
        what = ", ".join(nets) if nets else "all nets"
        return await _edit(
            ctx, "delete_tracks", f"Rip up tracks on {what}",
            lambda b: engine(ctx).call("delete_tracks", board=b, nets=nets),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def delete_items(ctx: RunContext[HarnessDeps], uuids: list[str]) -> ToolReturn:
        """Delete specific board items (tracks, vias, zones, shapes) by UUID."""
        return await _edit(
            ctx, "delete_items", f"Delete {len(uuids)} item(s)",
            lambda b: engine(ctx).call("delete_items", board=b, uuids=uuids),
        )

    @agent.tool(sequential=True)
    async def add_track(
        ctx: RunContext[HarnessDeps], net: str, points: list[list[float]], width: float = 0.25, layer: str = "F.Cu"
    ) -> ToolReturn:
        """Draw a track through `points` [[x, y], ...] on `layer` (F.Cu or B.Cu). Prefer 45-degree bends."""
        return await _edit(
            ctx, "add_track", f"Route {net} on {layer} ({len(points) - 1} segment(s))",
            lambda b: engine(ctx).call("add_track", board=b, net=net, points=points, width=width, layer=layer),
        )

    @agent.tool(sequential=True)
    async def add_via(
        ctx: RunContext[HarnessDeps], x: float, y: float, net: str, size: float = 0.6, drill: float = 0.3
    ) -> ToolReturn:
        """Add a through via at (x, y) on `net`."""
        return await _edit(
            ctx, "add_via", f"Add {net} via at ({x:g}, {y:g})",
            lambda b: engine(ctx).call("add_via", board=b, x=x, y=y, net=net, size=size, drill=drill),
        )

    @agent.tool(sequential=True)
    async def autoroute(ctx: RunContext[HarnessDeps], timeout_s: int = 120) -> ToolReturn:
        """Route every unrouted connection with Freerouting. Existing tracks are kept; rip up first to re-route."""
        from kicad_harness.engine.freerouting import autoroute as run_freerouting

        svc = ctx.deps.services
        return await _edit(
            ctx, "autoroute", "Autoroute with Freerouting",
            lambda b: run_freerouting(svc.engine, b, svc.config, timeout_s=timeout_s),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def set_board_outline(
        ctx: RunContext[HarnessDeps], width: float, height: float, x: float = 0.0, y: float = 0.0,
        corner_radius: float = 0.0,
    ) -> ToolReturn:
        """Replace the board outline with a width x height rectangle at (x, y), optionally with rounded corners."""
        return await _edit(
            ctx, "set_board_outline", f"Set board outline to {width:g} x {height:g} mm",
            lambda b: engine(ctx).call(
                "set_outline", board=b, width=width, height=height, x=x, y=y, corner_radius=corner_radius
            ),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def add_copper_zone(
        ctx: RunContext[HarnessDeps], net: str, layer: str, margin: float = 0.5,
        polygon: list[list[float]] | None = None,
    ) -> ToolReturn:
        """Add a filled copper pour on `net`/`layer`, covering the board outline inset by `margin` unless `polygon` is given."""
        return await _edit(
            ctx, "add_copper_zone", f"Add {net} copper zone on {layer}",
            lambda b: engine(ctx).call("add_zone", board=b, net=net, layer=layer, margin=margin, polygon=polygon),
        )

    @agent.tool(sequential=True)
    async def refill_zones(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Refill every copper zone (do this after moving parts or tracks under a pour)."""
        return await _edit(
            ctx, "refill_zones", "Refill copper zones",
            lambda b: engine(ctx).call("refill_zones", board=b),
        )

    @agent.tool
    async def find_footprints(ctx: RunContext[HarnessDeps], query: str, limit: int = 30) -> dict:
        """Search the footprint libraries by name, e.g. "TestPoint_Pad_1.0", "MountingHole_3.2mm", "PinHeader_1x02"."""
        words = [w.lower() for w in query.split()]
        found: list[str] = []
        for root in ctx.deps.services.config.footprint_dirs:
            root = Path(root)
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.kicad_mod")):
                lib = path.parent.name.removesuffix(".pretty") if path.parent.suffix == ".pretty" else ""
                name = f"{lib}:{path.stem}" if lib else path.stem
                if all(w in name.lower() for w in words):
                    found.append(name)
                    if len(found) >= limit:
                        return {"matches": found, "truncated": True}
        return {"matches": found, "truncated": False}

    @agent.tool(sequential=True)
    async def add_footprint(
        ctx: RunContext[HarnessDeps], footprint: str, ref: str, value: str, x: float, y: float,
        rotation: float = 0.0, side: str = "front", pad_nets: dict[str, str] | None = None,
    ) -> ToolReturn:
        """Place a library footprint (from find_footprints) as a new part and optionally connect its pads to nets."""
        svc = ctx.deps.services

        def work(board: str) -> dict:
            placed = svc.engine.call(
                "add_footprint", board=board, footprint=footprint, ref=ref, value=value, x=x, y=y,
                rotation=rotation, side=side, footprint_dirs=[str(p) for p in svc.config.footprint_dirs],
            )
            for pad, net in (pad_nets or {}).items():
                svc.engine.call("set_pad_net", board=board, ref=ref, pad=str(pad), net=net)
            return placed

        return await _edit(ctx, "add_footprint", f"Add {ref} ({footprint}) at ({x:g}, {y:g})", work)

    @agent.tool(sequential=True)
    async def remove_footprint(ctx: RunContext[HarnessDeps], ref: str) -> ToolReturn:
        """Delete a footprint from the board."""
        return await _edit(
            ctx, "remove_footprint", f"Remove {ref}",
            lambda b: engine(ctx).call("remove_footprint", board=b, ref=ref),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def set_pad_net(ctx: RunContext[HarnessDeps], ref: str, pad: str, net: str) -> ToolReturn:
        """Connect a pad to a net (the net is created if new). Changes connectivity, so route afterwards."""
        return await _edit(
            ctx, "set_pad_net", f"Connect {ref}.{pad} to {net}",
            lambda b: engine(ctx).call("set_pad_net", board=b, ref=ref, pad=str(pad), net=net),
        )

    # ------------------------------------------------------------------ history

    @agent.tool
    async def list_versions(ctx: RunContext[HarnessDeps], limit: int = 20) -> dict:
        """Recent versions on the current branch, newest first."""
        ws = _ws(ctx)
        versions = ws.log(limit=limit)
        return {
            "branch": ws.branch(),
            "versions": [
                {"sha": v["sha"][:8] if isinstance(v, dict) else v.sha[:8],
                 "subject": (v["subject"] if isinstance(v, dict) else v.subject),
                 "author": (v["author"] if isinstance(v, dict) else v.author)}
                for v in versions
            ],
        }

    @agent.tool(sequential=True)
    async def restore_version(ctx: RunContext[HarnessDeps], sha: str) -> ToolReturn:
        """Restore the board to an earlier version, as a new version on the current branch."""
        st = ctx.deps.state
        if st.supervised and not ctx.tool_call_approved:
            raise ApprovalRequired()
        ws = _ws(ctx)
        try:
            version = await asyncio.to_thread(
                lambda: ws.restore(sha, author="agent", trailers=_trailers(ctx, "restore_version"))
            )
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        await _sync_state(ctx, bump=True)
        brief = _version_brief(version)
        if brief and ctx.deps.on_commit:
            ctx.deps.on_commit({"tool": "restore_version", **brief})
        return ToolReturn(return_value={"ok": True, "version": brief}, metadata=[_snapshot(st)])

    # ------------------------------------------------------------------ schematic phase

    @agent.tool
    async def schematic_summary(
        ctx: RunContext[HarnessDeps], **_: Any
    ) -> ToolReturn:
        """The schematic: paper size, every symbol (ref, value, footprint, position), nets, floating pins and ERC counts.

        Takes no arguments; any extra arguments are ignored.
        """
        svc = ctx.deps.services
        ws = _ws(ctx)
        from kicad_harness.engine import kicad_cli

        try:
            doc = await asyncio.to_thread(_schematic, ctx)
            index = doc.index()
        except Exception as exc:
            # A half-written or unparseable schematic is a state to report, not a crash: raising
            # here would burn the tool-retry budget and end the whole run.
            return ToolReturn(
                return_value={
                    "ok": False,
                    "error": f"the schematic could not be read: {exc}",
                    "hint": "It may be mid-write. Call schematic_summary again, or regenerate the "
                    "schematic with generate_schematic_skidl.",
                    "phase": ws.phase(),
                    "brief": ws.brief(),
                },
                metadata=[_snapshot(ctx.deps.state)],
            )

        try:
            report = await asyncio.to_thread(kicad_cli.erc, ws.schematic_path, config=svc.config)
            counts = report["counts"]
            violations = report["violations"][:15]
        except Exception as exc:  # ERC failing must not hide the schematic itself
            counts = {"errors": None, "warnings": None}
            violations = [{"error": _engine_error_text(exc)}]
        from kicad_harness.engine import layout as layout_mod

        try:
            layout_report = layout_mod.analyze(doc)
            layout_report["findings"] = layout_mod.findings(doc)
        except Exception as exc:  # measurement must not hide the schematic itself
            layout_report = {"ok": False, "score": None, "error": _engine_error_text(exc)}
        summary = {
            "design": ctx.deps.state.design,
            "phase": ws.phase(),
            "brief": ws.brief(),
            "paper": index["paper"],
            "layout": layout_report,
            "symbols": index["symbols"],
            "nets": index["nets"],
            "wires": index["wires"],
            "labels": index["labels"],
            "unconnected_pins": index["unconnected_pins"],
            "erc": counts,
            "erc_violations": violations,
            "head": (ctx.deps.state.head or "")[:8],
        }
        await _sync_schematic_state(ctx)
        return ToolReturn(return_value=summary, metadata=[_snapshot(ctx.deps.state)])

    @agent.tool(sequential=True)
    async def add_symbol(
        ctx: RunContext[HarnessDeps],
        symbol: str,
        ref: str,
        x: float,
        y: float,
        rotation: float = 0,
        value: str | None = None,
        footprint: str | None = None,
    ) -> ToolReturn:
        """Place a library symbol on the schematic (library:Name, e.g. "Device:R"), at (x, y) in mm with an optional rotation."""
        # Resolve the requested library up front; it may not be one the file already uses.
        lib_name = symbol.split(":", 1)[0] if ":" in symbol else ""
        if lib_name and not _library(ctx, lib_name):
            known = sorted(p.stem for p in (_KI_LIBRARY_DIRS or []))[:20]
            return ToolReturn(return_value={
                "ok": False,
                "error": f"no symbol library named {lib_name!r}",
                "available_libraries": known,
            })
        return await _schematic_edit(
            ctx, "add_symbol", f"Place {ref} ({symbol})",
            lambda d: d.add_symbol(symbol, ref, x, y, rotation=rotation, value=value, footprint=footprint),
            want=lib_name or None,
        )

    @agent.tool(sequential=True)
    async def remove_symbol(ctx: RunContext[HarnessDeps], ref: str) -> ToolReturn:
        """Delete a symbol from the schematic, along with wires that end on its pins."""
        doc = _schematic(ctx)
        if doc.remove_symbol(ref):
            return await _schematic_commit(ctx, "remove_symbol", f"Remove {ref}", {"removed": ref})
        return ToolReturn(return_value={"ok": False, "error": f"no symbol {ref!r} on the schematic"})

    @agent.tool(sequential=True)
    async def connect_pins(
        ctx: RunContext[HarnessDeps],
        a: str,
        b: str,
        net: str | None = None,
    ) -> ToolReturn:
        """Wire one pin to another and name the connection: pins as "REF.PIN" (e.g. "U1.3", "R1.1")."""
        try:
            ref_a, pin_a = _split_pin(a)
            ref_b, pin_b = _split_pin(b)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return await _schematic_edit(
            ctx, "connect_pins", f"Connect {a} to {b}",
            lambda d: {"net": d.connect_pins((ref_a, pin_a), (ref_b, pin_b), net=net)},
        )

    @agent.tool(sequential=True)
    async def move_schematic_symbol(
        ctx: RunContext[HarnessDeps], ref: str, x: float, y: float, rotation: float | None = None
    ) -> ToolReturn:
        """Move a schematic symbol to (x, y) mm, optionally rotating it. The reference/value text moves with it."""
        return await _schematic_edit(
            ctx, "move_schematic_symbol", f"Move {ref} to ({x:g}, {y:g})",
            lambda d: {"moved": bool(d.move_symbol(ref, x, y, rotation))},
        )

    @agent.tool(sequential=True)
    async def set_symbol_value(ctx: RunContext[HarnessDeps], ref: str, value: str) -> ToolReturn:
        """Change a symbol's Value. For a power symbol this changes the net it names — use it to
        fix a symbol whose value contradicts the net it is wired to, e.g. a GND symbol on +5V."""
        return await _schematic_edit(
            ctx, "set_symbol_value", f"Set {ref} value to {value}",
            lambda d: {"changed": bool(d.set_symbol_value(ref, value))},
        )

    @agent.tool(sequential=True)
    async def disconnect_schematic(ctx: RunContext[HarnessDeps], net: str | None = None,
                                   ref: str | None = None) -> ToolReturn:
        """Remove wires (and, for a net, its labels) so a wrong connection can be redone:
        pass net="NAME" to clear that net, or ref="R1" to clear every wire touching R1's pins."""
        if not net and not ref:
            return ToolReturn(return_value={"ok": False, "error": "give a net or a ref"})
        return await _schematic_edit(
            ctx, "disconnect_schematic", f"Disconnect {net or ref}",
            lambda d: {"removed": d.remove_wires(net=net, reference=ref)},
        )

    @agent.tool(sequential=True)
    async def label_pin(ctx: RunContext[HarnessDeps], pin: str, net: str) -> ToolReturn:
        """Attach a net to one pin (e.g. "U1.3") with a short stub and a label, without needing a partner pin."""
        try:
            ref, number = _split_pin(pin)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

        def work(doc) -> dict:
            placement = doc._placement(ref)
            if placement is None:
                return {"ok": False, "error": f"no symbol {ref!r}"}
            point = doc.pin_position(placement, number)
            if point is None:
                return {"ok": False, "error": f"{ref} has no pin {number}"}
            end = (point[0] + 3.81, point[1])
            doc.add_wire([point, end])
            doc.add_label(net, *end, anchor="left")
            return {"ok": True, "pin": pin, "net": net}

        return await _schematic_edit(ctx, "label_pin", f"Label {pin} as {net}", work)

    @agent.tool(sequential=True)
    async def add_schematic_label(
        ctx: RunContext[HarnessDeps], name: str, x: float, y: float, global_: bool = False
    ) -> ToolReturn:
        """Put a net label on the schematic at (x, y); `global_` makes it a global (sheet-wide power-style) label."""
        return await _schematic_edit(
            ctx, "add_schematic_label", f"Label {name} at ({x:g}, {y:g})",
            lambda d: {"label": str(d.add_label(name, x, y, global_=global_)[1])},
        )

    @agent.tool(sequential=True)
    async def add_schematic_text(ctx: RunContext[HarnessDeps], text: str, x: float, y: float) -> ToolReturn:
        """Add a note to the schematic at (x, y). Notes don't connect anything."""
        return await _schematic_edit(
            ctx, "add_schematic_text", f"Note at ({x:g}, {y:g})",
            lambda d: {"text": text[:40]},
        )

    @agent.tool(sequential=True)
    async def generate_schematic_skidl(
        ctx: RunContext[HarnessDeps],
        parts: list[dict],
        nets: list[dict],
    ) -> ToolReturn:
        """Draw the whole schematic at once with skidl, from parts and nets.

        Use this for a new board instead of placing dozens of symbols by hand. Each part is
        {"ref": "R1", "library": "Device", "symbol": "R", "value": "10k", "footprint": "Resistor_SMD:R_0805_2012Metric"}.
        Each net is {"name": "VCC", "pins": ["R1.1", "U1.8"]}. Footprints must be KiCad library
        names (Library:Footprint) because the board phase places them.
        """
        from kicad_harness.engine import skidl_gen

        ws = _ws(ctx)
        try:
            validated_parts, validated_nets = skidl_gen.validate({"parts": parts, "nets": nets})
        except ValueError as exc:
            return ToolReturn(return_value={"ok": False, "error": f"the netlist is invalid: {exc}"})
        target = ws.schematic_path
        # Keep the current file so a refused generation can put it back untouched.
        previous = target.read_bytes() if target.is_file() else None
        try:
            result = await asyncio.to_thread(
                skidl_gen.generate, validated_parts, validated_nets, target,
            )
        except Exception as exc:
            message = _engine_error_text(exc)
            # "Unable to find part NE555 in library Timer." is the usual mistake; name the
            # alternatives so the model can fix it instead of guessing again.
            missing = re.search(r"Unable to find part (\S+) in library (\S+)", message)
            if missing:
                name, library = missing.group(1).rstrip("."), missing.group(2).rstrip(".")
                try:
                    options = skidl_gen.suggest_symbols(library, name)
                except Exception:
                    options = []
                if options:
                    message += f" (symbols in {library} that match: {', '.join(options)})"
            return ToolReturn(return_value={"ok": False, "error": message})
        # A netless run produces a sheet of unconnected symbols and discards existing wiring
        # (this is how pi-relay lost 20 hand-drawn wires). Restore the previous file and say so.
        if not validated_nets:
            if previous is not None:
                await asyncio.to_thread(target.write_bytes, previous)
            return ToolReturn(return_value={
                "ok": False,
                "error": "no nets were given, so nothing would be connected — the schematic was left unchanged",
                "hint": (
                    "Pass a 'nets' list wiring the pins (e.g. {\"name\": \"GND\", \"pins\": "
                    "[\"U1.1\", \"C1.2\"]}), or use connect_pins to wire specific pins."
                ),
            })
        # Give labelled pins visible wires: skidl places labels on pin anchors, and a sheet with
        # labels but no wires reads as unfinished even though the netlist is right.
        try:
            from kicad_harness.engine.schematic import Schematic

            doc = await asyncio.to_thread(Schematic.load, target)
            stubs = await asyncio.to_thread(doc.draw_pin_stubs)

            # Record supplier parts on the symbols so the project's BOM carries them.
            recorded = 0
            for part in validated_parts:
                if part.lcsc and doc.set_symbol_property(part.ref, "LCSC", part.lcsc):
                    recorded += 1
                if part.description and doc.set_symbol_property(part.ref, "Description", part.description):
                    pass

            if stubs.get("stubbed") or recorded:
                await asyncio.to_thread(doc.save)
        except Exception:
            stubs = {}
        payload = {"ok": True, "path": target.name, "parts": len(validated_parts),
                   "nets": len(validated_nets), "stubs": stubs}
        if result.warnings:
            payload["warnings"] = result.warnings[:10]
        # Report which parts JLCPCB actually stocks, so an unmakeable board is visible immediately.
        try:
            from kicad_harness.engine import parts as parts_mod

            sourcing = []
            for p in validated_parts:
                if not p.value:
                    continue
                try:
                    hit = parts_mod.search_jlc(p.value, limit=1)
                except Exception:
                    continue
                match = (hit.get("parts") or [None])[0]
                sourcing.append(
                    {
                        "ref": p.ref,
                        "value": p.value,
                        "jlc_stocked": bool(match),
                        "lcsc": match["lcsc"] if match else None,
                        "tier": match["tier"] if match else None,
                    }
                )
            if sourcing:
                payload["jlc_sourcing"] = sourcing
                payload["unstocked"] = [s["value"] for s in sourcing if not s["jlc_stocked"]]
        except Exception:
            pass  # sourcing is advice; it must never block drawing the schematic
        return await _schematic_commit(ctx, "generate_schematic_skidl", f"Draw the schematic with skidl ({len(validated_parts)} parts)", payload)

    @agent.tool
    async def check_schematic_layout(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Measure whether the schematic is legible: overlapping symbols or net labels, wires
        running through parts, dangling wire ends, anything off the sheet. Returns a score (0 is
        legible), per-fault counts and the worst violations. The goal's mandatory [legible]
        criterion uses the same measurement, so the run cannot finish while it is above 0."""
        from kicad_harness.engine import layout as layout_mod

        try:
            doc = await asyncio.to_thread(_schematic, ctx)
            report = await asyncio.to_thread(layout_mod.analyze, doc)
            report["findings"] = await asyncio.to_thread(layout_mod.findings, doc)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        await _sync_schematic_state(ctx)
        ws = _ws(ctx)
        try:
            png, url = await asyncio.to_thread(_schematic_image, ctx.deps.services, ws)
        except Exception:
            return ToolReturn(return_value=report, metadata=[_snapshot(ctx.deps.state)])
        ctx.deps.state.last_render = RenderInfo(url=url, caption="Schematic sheet", view={})
        return ToolReturn(
            return_value=[report, BinaryContent(data=png, media_type="image/png")],
            metadata=[_snapshot(ctx.deps.state)],
        )

    @agent.tool
    async def render_schematic(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Render the whole schematic sheet as an image you can look at.

        Same picture the UI shows. Use it after tidy_schematic: the legibility score catches
        overlaps and dangling wires, but only the image shows a sheet that is technically clean
        and still reads badly (a channel that runs backwards, a part marooned at the edge, a wall
        of duplicate labels)."""
        ws = _ws(ctx)
        try:
            png, url = await asyncio.to_thread(_schematic_image, ctx.deps.services, ws)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        caption = "Schematic sheet"
        ctx.deps.state.last_render = RenderInfo(url=url, caption=caption, view={})
        return ToolReturn(
            return_value=[json.dumps({"url": url, "caption": caption}),
                          BinaryContent(data=png, media_type="image/png")],
            metadata=[_snapshot(ctx.deps.state)],
        )

    @agent.tool(sequential=True)
    async def tidy_schematic(ctx: RunContext[HarnessDeps], paper: str | None = None) -> ToolReturn:
        """Relayout the schematic: symbols in circuit-flow order, each 2-pin passive beside the
        part it serves (pull-ups by their input, decoupling caps by their IC), one short stub and
        net label per pin. Connectivity is preserved (labels with the same net name merge), so the
        netlist does not change. Call check_schematic_layout afterwards, then route_schematic."""
        from kicad_harness.engine import layout as layout_mod

        return await _schematic_edit(
            ctx, "tidy_schematic", "Tidy the schematic layout",
            lambda doc: layout_mod.tidy(doc, paper=paper),
        )

    @agent.tool(sequential=True)
    async def route_schematic(ctx: RunContext[HarnessDeps], max_mm: float = 300) -> ToolReturn:
        """Draw direct wires between the two pins of point-to-point nets (<= max_mm apart).

        Labels stay in place, so net names are unchanged; the wire only makes the connection
        visible. Each wire is checked against the pin partition and the whole edit is rejected
        (and rolled back) if ERC errors increase. Returns which nets were wired and why the
        others were skipped — move those parts closer with add_symbol/move or leave the label."""
        from kicad_harness.engine import kicad_cli
        from kicad_harness.engine import layout as layout_mod

        ws = _ws(ctx)
        svc = ctx.deps.services
        original = ws.schematic_path.read_text(encoding="utf-8")
        try:
            erc_before = kicad_cli.erc(ws.schematic_path, config=svc.config)["counts"].get("errors", 0)
        except Exception:
            erc_before = None
        try:
            doc = await asyncio.to_thread(_schematic, ctx)
            report = await asyncio.to_thread(lambda: layout_mod.route(doc, max_mm=max_mm))
            await asyncio.to_thread(doc.save)
        except Exception as exc:
            ws.schematic_path.write_text(original, encoding="utf-8")
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        try:
            erc_after = kicad_cli.erc(ws.schematic_path, config=svc.config)["counts"].get("errors", 0)
        except Exception:
            erc_after = None
        if erc_before is not None and erc_after is not None and erc_after > erc_before:
            ws.schematic_path.write_text(original, encoding="utf-8")
            return ToolReturn(return_value={
                "ok": False,
                "error": "the new wires raised ERC errors, so the edit was rolled back",
                "erc_before": erc_before, "erc_after": erc_after, **report,
            })
        return await _schematic_commit(
            ctx, "route_schematic",
            f"Wire {len(report['wired'])} point-to-point nets", report,
        )

    @agent.tool
    async def check_schematic(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Run ERC and the legibility check on the working schematic, and evaluate the goal."""
        return ToolReturn(return_value=await _evaluate(ctx), metadata=[_snapshot(ctx.deps.state)])

    @agent.tool(sequential=True)
    async def start_board_layout(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Finish the schematic and lay out its board: creates the board, places every footprint and
        assigns the nets KiCad's netlist gives, then switches the design to the board phase.
        Call this once the schematic is complete and ERC is clean."""
        ws = _ws(ctx)
        from kicad_harness.engine import kicad_cli, seed

        svc = ctx.deps.services
        try:
            report = await asyncio.to_thread(kicad_cli.erc, ws.schematic_path, config=svc.config)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        if report["counts"].get("errors"):
            return ToolReturn(return_value={
                "ok": False,
                "error": (
                    f"ERC still reports {report['counts']['errors']} errors; fix them before laying out."
                ),
                "erc_errors": [
                    v["description"] or v["type"]
                    for v in report["violations"]
                    if v["severity"] == "error"
                ][:20],
                "hint": "Use check_schematic for the full list, fix the wiring, then call this again.",
            })

        def run() -> dict:
            netlist = kicad_cli.parse_netlist(
                kicad_cli.export_netlist(ws.schematic_path, ws.path / ".netlist.net", config=svc.config).read_text()
            )
            try:
                doc = _schematic(ctx)
                if ws.board_path.is_file():
                    # Already laid out: bring the board in line with the edited schematic instead
                    # of throwing the layout away.
                    return seed.reconcile_board(doc, ws.board_path, svc.engine, netlist=netlist).to_dict()
                result = seed.seed_board(doc, ws.board_path, svc.engine, netlist=netlist)
            finally:
                (ws.path / ".netlist.net").unlink(missing_ok=True)
            ws.set_phase("board")
            return result.to_dict()

        try:
            payload = await asyncio.to_thread(run)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        version = await asyncio.to_thread(
            lambda: ws.commit("Lay out the board from the schematic", author="agent",
                              trailers=_trailers(ctx, "start_board_layout"))
        )
        await _sync_state(ctx, bump=True)
        await _sync_schematic_state(ctx)
        payload.update(ok=True, phase="board", version=_version_brief(version))
        if ctx.deps.on_commit and version:
            ctx.deps.on_commit({"tool": "start_board_layout", **_version_brief(version)})
        return ToolReturn(return_value=payload, metadata=[_snapshot(ctx.deps.state)])

    # ------------------------------------------------------------------ parts sourcing

    @agent.tool
    async def find_parts(
        ctx: RunContext[HarnessDeps],
        query: str | None = None,
        category: str | None = None,
        package: str | None = None,
        basic_only: bool = False,
        limit: int = 15,
    ) -> ToolReturn:
        """Search the JLCPCB catalogue for parts that can actually be assembled.

        `query` matches the manufacturer part number (e.g. "NE555" finds NE555DR). `category`
        browses by type (e.g. "LED", "Resistors", "Relays"), which is how to find a part when you
        know what it does but not its number. Results carry stock, unit price and whether the
        part is basic (no assembly fee) or extended.
        """
        from kicad_harness.engine import parts

        try:
            result = await asyncio.to_thread(
                parts.search_jlc, query, category=category, package=package,
                basic_only=basic_only, limit=limit,
            )
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        result["ok"] = True
        return ToolReturn(return_value=result)

    @agent.tool(sequential=True)
    async def draw_schematic_wires(ctx: RunContext[HarnessDeps]) -> ToolReturn:
        """Draw visible wire stubs from every labelled pin out to its net label.

        Generators place net labels exactly on pin anchors: correct electrically, but nothing is
        drawn, so the sheet looks unwired. This wires pin -> label for every such pin and leaves
        already-wired pins alone. Safe to call repeatedly.
        """
        return await _schematic_edit(
            ctx, "draw_schematic_wires", "Draw wire stubs to net labels",
            lambda d: {"stubs": d.draw_pin_stubs()},
        )

    @agent.tool
    async def part_categories(ctx: RunContext[HarnessDeps], term: str | None = None) -> ToolReturn:
        """List JLCPCB part categories (optionally filtered, e.g. "relay") with how many are in stock."""
        from kicad_harness.engine import parts

        try:
            items = await asyncio.to_thread(parts.categories, term)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        return ToolReturn(return_value={"ok": True, "categories": items})

    @agent.tool
    async def check_part(ctx: RunContext[HarnessDeps], mpn: str, lcsc: str | None = None) -> ToolReturn:
        """Check one part before using it: JLCPCB stock/price/tier, plus datasheet availability from i2cdb."""
        from kicad_harness.engine import parts

        try:
            result = await asyncio.to_thread(parts.check_part, mpn, lcsc)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        result["ok"] = True
        return ToolReturn(return_value=result)

    @agent.tool
    async def part_datasheet(
        ctx: RunContext[HarnessDeps], mpn: str, manufacturer: str | None = None, pages: str | None = "1-6"
    ) -> ToolReturn:
        """Read a part's datasheet (markdown) to confirm pinout and ratings. Needs the manufacturer slug or MPN."""
        from kicad_harness.engine import parts

        try:
            if not manufacturer:
                hits = await asyncio.to_thread(parts.search_i2cdb, mpn, 10)
                exact = next(
                    (h for h in hits["parts"] if (h.get("mpn") or "").upper() == mpn.upper()), None
                )
                if not exact:
                    return ToolReturn(return_value={
                        "ok": False,
                        "error": f"i2cdb has no record for {mpn!r}; near matches: "
                                 f"{[h.get('mpn') for h in hits['parts'][:5]]}",
                    })
                manufacturer = exact["manufacturer"]
            result = await asyncio.to_thread(parts.datasheet, manufacturer, mpn, pages)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        result["ok"] = bool(result.get("has_pdf"))
        return ToolReturn(return_value=result)

    # ------------------------------------------------------------------ goal

    @agent.tool(sequential=True)
    async def set_goal(ctx: RunContext[HarnessDeps], summary: str, criteria: list[dict]) -> ToolReturn:
        """Set this design's goal: a short summary of what the user wants, plus the machine-checkable
        criteria that capture it (drc_errors, drc_warnings, unconnected, board_size, edge_clearance,
        max_distance, min_distance, within_region, open_threads).  Derive the criteria from the
        user's stated goal by inspecting the board, then call `check` to evaluate them."""
        from kicad_harness.agent import goal as goals

        st = ctx.deps.state
        ws = _ws(ctx)
        spec = {"summary": summary.strip(), "criteria": criteria}
        budget = ws.goal().get("budget")
        if budget:
            spec["budget"] = budget
        try:
            goals.validate_goal(spec)
        except ValueError as exc:
            return ToolReturn(return_value={
                "ok": False,
                "error": f"the goal is invalid: {exc}",
                "criteria_types": sorted(goals.CRITERIA),
            })
        with ws.lock:
            version = ws.set_goal(spec, author="agent")
        await _sync_state(ctx, bump=False)
        if version is not None and ctx.deps.on_commit:
            ctx.deps.on_commit({"tool": "set_goal", **_version_brief(version)})
        evaluation = None
        try:
            svc = ctx.deps.services
            board_hash, _ = svc.artifacts.materialize(ws.board_path.read_bytes())
            index = svc.artifacts.index(board_hash)
            drc = goals.with_board_state(svc.artifacts.drc(board_hash), ws.board_path.is_file())
            evaluation = goals.evaluate(spec, index, drc, svc.store.review.list(st.design))
            st.goal_met = bool(evaluation.get("met"))
            st.goal = evaluation
        except Exception:
            pass
        return ToolReturn(
            return_value={"ok": True, "goal": spec, "evaluation": evaluation,
                          "version": _version_brief(version) if version else None},
            metadata=[_snapshot(st)],
        )

    # ------------------------------------------------------------------ goal loop

    @agent.output_validator
    async def enforce_goal(
        ctx: RunContext[HarnessDeps], output: str | DeferredToolRequests
    ) -> str | DeferredToolRequests:
        if isinstance(output, DeferredToolRequests) or not ctx.deps.state.loop or not ctx.deps.state.design:
            return output
        evaluation = await _evaluate(ctx)
        overdue = bool(
            ctx.deps.max_minutes and time.time() - ctx.deps.started_at > ctx.deps.max_minutes * 60
        )
        if evaluation["met"]:
            # The goal is the agent's own yardstick, so it is not trusted on its own: a separate
            # reviewer checks the artifacts against the original brief before this can finish.
            if overdue:
                return output
            complete, gaps = await _adversarial_audit(ctx, agent.model)
            if complete:
                return output
            raise ModelRetry(
                "The goal criteria pass, but a reviewer checked the design against the original "
                f"brief and found it incomplete. Fix these before finishing: {gaps}"
            )
        if overdue:
            return f"{output}\n\n(Stopped: time budget reached with the goal unmet: {goals.failing_summary(evaluation)})"
        raise ModelRetry(
            "The goal is not met yet: "
            + goals.failing_summary(evaluation)
            + ". Keep working (inspect, edit, check) and only finish when check reports every criterion passing."
        )

    return agent


_agent: Agent[HarnessDeps, str | DeferredToolRequests] | None = None


def get_agent() -> Agent[HarnessDeps, str | DeferredToolRequests]:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


def usage_limits_for(goal: dict):
    """UsageLimits from a goal's budget."""
    from pydantic_ai.usage import UsageLimits

    budget = goal.get("budget") or {}
    return UsageLimits(request_limit=int(budget.get("max_requests", 120)))
