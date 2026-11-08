"""The harness agent: Pydantic AI tools over the KiCad engine, the design workspace and the review store.

See DESIGN.md, "Agent".
- Every editing tool commits one version.
- Inspection tools return images the model sees.
- When `state.loop` is on, the output validator keeps the agent working until the goal is met.
"""

from __future__ import annotations

import asyncio
import math
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
            "generate_schematic) and call start_board when ERC is clean; the board "
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
            "schematic_metrics": await asyncio.to_thread(_schematic_metrics_facts, ws),
            "manufacturing": None,
            "heights": None,
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
        "schematic_metrics": await asyncio.to_thread(_schematic_metrics_facts, ws),
        "manufacturing": await asyncio.to_thread(_manufacturing_facts, ws),
        "heights": await asyncio.to_thread(_height_facts, ws, index),
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

def _schematic_metrics_facts(ws) -> dict[str, Any] | None:
    from kicad_harness.engine import layout
    from kicad_harness.engine.schematic import Schematic

    if not ws.schematic_path.is_file():
        return None
    try:
        return layout.metrics(Schematic.load(ws.schematic_path))
    except Exception:
        return None


def _height_facts(ws, index: dict[str, Any]) -> dict[str, Any] | None:
    from kicad_harness.engine import heights

    try:
        return heights.report(index, heights.load(ws))
    except Exception:
        return None


def _manufacturing_facts(ws) -> dict[str, Any] | None:
    from kicad_harness.engine import manufacturing

    if not ws.board_path.is_file():
        return None
    try:
        return manufacturing.status(ws)
    except Exception:
        return None


def _parts_facts(ctx: RunContext[HarnessDeps]) -> dict[str, Any]:
    """Ground truth about which parts exist: schematic symbols and board footprints."""
    from kicad_harness.agent.audit import design_parts

    return design_parts(ctx.deps.services, _ws(ctx))

async def _adversarial_audit(ctx: RunContext[HarnessDeps], model: Any = None) -> tuple[bool, str]:
    """Have a reviewer check the artifacts against the original brief.

    Returns ``(complete, summary)``. The reviewer is a separate agent with adversarial
    instructions, so it is not grading the working agent's own summary.
    """
    from kicad_harness.agent import judge as judge_mod
    from kicad_harness.agent import models as model_catalog

    # A scripted/test model cannot review anything, and a real review would make network calls
    # inside tests; only real runs get judged.
    if type(model).__name__ in ("FunctionModel", "TestModel"):
        return True, ""

    svc = ctx.deps.services
    ws = _ws(ctx)
    brief = ws.brief() or (ws.goal() or {}).get("summary")
    if not brief:
        return True, ""  # nothing was asked for, so nothing to judge against

    try:
        facts = await _judge_facts(ctx)
    except Exception as exc:
        return False, f"the judge could not read the design: {_engine_error_text(exc)}"

    judge_model = model
    configured = getattr(svc.config, "judge_model", "")
    if configured:
        try:
            judge_model = model_catalog.resolve(configured)
        except Exception:
            judge_model = model
    verdict = await judge_mod.assess(facts, judge_model, reviewer=None)
    st = ctx.deps.state
    st.judge_complete = bool(verdict.complete)
    st.judge_findings = verdict.findings[:20]
    if verdict.complete:
        return True, ""
    return False, judge_mod.summarise(verdict)

async def _judge_facts(ctx: RunContext[HarnessDeps]) -> dict[str, Any]:
    """The evidence packet: measured facts only, never the worker's claims."""
    from kicad_harness.agent import audit
    from kicad_harness.engine import balance, design_rules, heights, manufacturing, qa, si

    svc = ctx.deps.services
    ws = _ws(ctx)
    evaluation = await _evaluate(ctx)
    parts = await asyncio.to_thread(audit.design_parts, svc, ws)
    facts: dict[str, Any] = {
        "brief": ws.brief() or (ws.goal() or {}).get("summary"),
        "phase": ws.phase(),
        "board": ws.board_path.is_file(),
        "evaluation": evaluation,
        "parts": parts,
        "rules": None,
        "manufacturing": None,
        "heights": None,
        "via_stubs": None,
        "silk": None,
        "courtyard": None,
        "test_coverage": None,
        "copper_balance": None,
        "waivers": _judge_waivers(ws),
    }
    if not ws.board_path.is_file():
        return facts
    board_hash, index = await _working(ctx)
    rules = await asyncio.to_thread(design_rules.load, ws)
    violations = await asyncio.to_thread(design_rules.violations, index, rules)
    facts.update({
        "rules": {
            "count": len(violations),
            "violations": violations[:10],
            "netclasses": rules.get("netclasses"),
            "stackup": rules.get("stackup"),
        },
        "manufacturing": await asyncio.to_thread(manufacturing.status, ws),
        "heights": await asyncio.to_thread(lambda: heights.report(index, heights.load(ws))),
        "via_stubs": await asyncio.to_thread(si.report, index, rules),
        "silk": await asyncio.to_thread(qa.silkscreen, index),
        "courtyard": await asyncio.to_thread(qa.courtyards, index),
        "test_coverage": balance.test_coverage(index),
        "copper_balance": balance.report(index),
        "drc_counts": await asyncio.to_thread(lambda: svc.artifacts.drc(board_hash).get("counts", {})),
        "waivers": _judge_waivers(ws),
    })
    return facts


def _judge_waivers(ws) -> dict[str, Any]:
    """Human-authored waivers only; the agent cannot sign away a rule."""
    import datetime

    path = Path(ws.path) / "judge_waivers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    today = datetime.date.today()
    for rule_id, entry in data.items():
        if not isinstance(entry, dict) or entry.get("author") == "agent":
            continue
        expires = entry.get("expires")
        if expires:
            try:
                if datetime.date.fromisoformat(str(expires)) < today:
                    continue
            except ValueError:
                continue
        out[str(rule_id)] = entry
    return out


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
                "the schematic tools (add_symbol, connect_pins, generate_schematic, ...) and call "
                "start_board when ERC is clean; then the board tools become available."
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
                        content[k] = "[older render omitted to save context; call render again to look]"
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

_JUDGE_READ_ONLY = {
    "check", "inspect", "render", "query", "measure", "measure_clearance", "probe",
    "get_design", "list_reviews", "list_versions", "diff_versions", "read_reference",
    "read_part_datasheet", "find_parts", "find_footprints", "silkscreen_check",
    "courtyard_check", "match_length", "export_manufacturing",
}


def _judge_hooks() -> Hooks:
    """Invalidate a judge verdict as soon as the design changes.

    A verdict is tied to the artifacts it reviewed. Without this, the UI (and a later stop
    decision) could show a green judge from before an edit. The hook is deliberately conservative:
    every tool that is not known read-only clears the last verdict.
    """
    hooks = Hooks()

    @hooks.on.after_tool_execute
    async def invalidate(ctx: RunContext[HarnessDeps], *, call, tool_def, args, result):
        if ctx.deps is not None and call.tool_name not in _JUDGE_READ_ONLY:
            ctx.deps.state.judge_complete = None
            ctx.deps.state.judge_findings = []
        return result

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
- When the user states a goal in words ("move the LED to the left edge", "keep it under 30 mm"), turn it into machine-checkable criteria yourself and save them with `set_goal`. Inspect the board first (get_design, query, measure) so references, regions and distances are real, then call `check`. Never ask the user to pick criterion types.
- `set_goal` takes a short `summary` (their intent, in their words where possible) and `criteria` from: drc_errors, drc_warnings, unconnected, board_size (max_w/max_h), edge_clearance (min_mm), max_distance/min_distance (a, b, max_mm/min_mm), within_region (ref, region [x1,y1,x2,y2]), open_threads (max, optional severities), schematic_legibility (max).

Phases:
- A design starts in the SCHEMATIC phase: there is no board yet, only `schematic.kicad_sch`. Draw the circuit, keep ERC clean (erc_errors/erc_warnings criteria), then call `start_board`. That creates the board, places the footprints from KiCad's own netlist and assigns their nets, so layout starts electrically correct. After it, you are in the BOARD phase and move parts and route.
- Prefer `generate_schematic` to draw a whole design from a parts and nets list; use the individual symbol and wiring tools to fix or extend it. Every part needs a real KiCad footprint (`Library:Footprint`) or it cannot be placed on the board.
- A schematic is not finished when it is merely ERC-clean: it must be readable. Call `check` after drawing or editing; while it reports a score above 0, call `tidy_schematic` and check again. The [legible] goal criterion is measured from the drawing itself and is mandatory, so the loop will not end until it passes.
- Arrange by circuit, not by reference: a part belongs beside the part it serves. Series/pull-up resistors sit by the input they protect, decoupling capacitors by the IC they decouple, and each power symbol at the pin it names. `tidy_schematic` puts 2-pin passives beside their owner; `check` lists the remaining offenders under `findings` (a passive far from its owner, a point-to-point net left as two distant labels, a power symbol whose value contradicts its net). Act on those findings rather than accepting them.
- `findings` also surfaces broken power symbols (e.g. a GND symbol wired to +5V). Fix the netlist or the symbol first: no placement can put a ground next to a part while the symbol names a different net.
- Once the parts are close, call `route_schematic` to draw direct wires for point-to-point nets instead of leaving two labels. It keeps the net names and rolls back any wire that would change connectivity; if it skips a net, the two parts are too far apart or a wire would cross a pin — move them closer and try again.
- Look at the sheet as an image too: `check` returns the rendered schematic with its score, and `check` returns the rendered board with its criteria. A sheet can score 0 and still look wrong at a glance — a part marooned at the edge, channel rows that do not line up, a wall of duplicate labels. Use `render` / `render` for a closer look, and fix what you see before calling the work finished.

Part sourcing (do this before choosing parts):
- Boards are assembled by JLCPCB, so a part is only real if JLCPCB stocks it. Use `find_parts` to search their catalogue: pass `query` for a part number (e.g. "NE555" finds NE555DR) or `category` to browse by type (e.g. "LED", "Relays"). It reports stock, unit price, and whether a part is `basic` (no assembly fee) or `extended` (a per-part reel fee). Prefer basic parts with large stock unless the user asks otherwise. The catalogue's description text is empty, so searching for words like "red LED" will not work — browse the category instead.
- Very few parts are basic: if `find_parts` returns an empty list, read its `empty_reason` (it says which filter removed everything) and retry with that filter relaxed rather than repeating the same query.
- Use `find_parts` on each part you intend to use: it returns the JLCPCB matches plus datasheet availability. If `usable_for_jlc` is false, say so and choose a stocked alternative rather than designing a board that cannot be made.
- Use `read_part_datasheet` to confirm a pinout or rating when you are not certain; it returns datasheet text. If i2cdb has no record, a supplier datasheet URL is included — do not invent pin numbers you have not verified.
- Mention the LCSC codes and the basic/extended split to the user when you finish the schematic.

Conventions:
- Coordinates are millimetres in KiCad board space: x to the right, y DOWN. Angles are degrees, counter-clockwise.
- Refer to parts by reference designator (U1), pads as REF.PAD (U1.3), nets as net:NAME.

How to work:
1. Orient: call get_design, then render to look at the board. Use query and measure for exact numbers; \
never guess coordinates from an image alone.
2. Edit with the editing tools. Every successful edit is committed as a new version the user can see and roll back, \
so prefer a few deliberate edits over many tiny ones.
3. Verify after edits: call check (DRC plus the goal criteria) and, for the schematic, check. Look at the result visually with render, zoomed \
on whatever you changed (pass target, view_mm and highlight). Tracks under solder mask are easier to judge in 2D than 3D.
4. Routing: after moving parts, rip up the affected tracks (delete_tracks) and call autoroute rather than drawing \
long routes by hand; hand-route only short or critical connections.
5. Review feedback: list_reviews shows the user's comments, anchored to specific entities. Address must_fix and \
warning items first. After handling one, call mark_review_addressed with a short note (only the user can resolve it); reply \
with reply_review if you disagree or need clarification.
6. Never claim the goal is met unless check reports every criterion passing. If you are stuck, say exactly what \
blocks you.
Keep messages to the user short: what you changed and why.
"""

def _envelope(payload: Any, *, ok: bool = True) -> dict[str, Any]:
    """Every tool result shares one shape: ``{ok, data|error, version}``.

    Legacy flattened keys are kept alongside ``data`` so existing prompts/tests keep working;
    image results are a list whose first item is this envelope and whose remaining items are
    attachments (``BinaryContent``).
    """
    if not isinstance(payload, dict):
        return {"ok": ok, "data": payload, "version": None}
    if ok and "ok" not in payload:
        payload = {**payload, "ok": True}
    if payload.get("ok") is False:
        return {"ok": False, "error": payload.get("error") or "tool failed",
                "data": None, "version": payload.get("version"), **payload}
    core = {key: value for key, value in payload.items()
            if key not in ("ok", "data", "error", "version")}
    return {**payload, "ok": True, "data": payload.get("data", core),
            "version": payload.get("version")}


def _envelope_result(value: Any) -> Any:
    """Wrap a tool's return value (ToolReturn, list with attachments, dict, or plain data)."""
    from pydantic_ai import ToolReturn

    if isinstance(value, ToolReturn):
        rv = value.return_value
        if isinstance(rv, list) and rv:
            head = rv[0]
            if isinstance(head, dict):
                head = _envelope(head)
            elif isinstance(head, str):
                try:
                    parsed = json.loads(head)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    head = json.dumps(_envelope(parsed))
            return ToolReturn(return_value=[head, *rv[1:]], metadata=value.metadata)
        return ToolReturn(return_value=_envelope(rv), metadata=value.metadata)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [_envelope(value[0]), *value[1:]]
    return _envelope(value)


_tool_description_cache: dict[str, str] | None = None

def tool_descriptions() -> dict[str, str]:
    """``{tool name: live docstring}`` for the Settings page, built once per process.

    Building the agent registers the tools without calling a model (``defer_model_check``), so
    this is cheap and always reflects the code, not a hand-maintained list.
    """
    global _tool_description_cache
    if _tool_description_cache is None:
        try:
            built = build_agent()
            _tool_description_cache = {
                name: (tool.description or "")
                for name, tool in built._function_toolset.tools.items()
            }
        except Exception:
            _tool_description_cache = {}
    return _tool_description_cache

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
            _judge_hooks(),
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
                "SCHEMATIC PHASE: there is no board yet. Draw the circuit — either generate_schematic "
                "for a whole design at once, or add_symbol/connect_pins/label_net by hand. Check with "
                "check (ERC), then call start_board to create the netlisted board. Board tools "
                "(move_footprint, autoroute, drc, ...) will refuse until then."
            )
        if st.loop:
            lines.append(
                "Goal loop is ON: keep inspecting, editing and checking until every criterion passes. "
                "You will be sent back to work if you stop early."
            )
        from . import tool_catalog

        # Workflow + rules digest is large (~1-2k tokens). Send it once per phase per run; the
        # model keeps the conversation, and re-sending it every step only burns context.
        if st.playbook_phase != phase:
            lines.append("")
            lines.append(tool_catalog.phase_instructions(phase))
            st.playbook_phase = phase
        if st.supervised:
            lines.append("Supervised mode is ON: destructive tools pause for the user's approval.")
        if st.selection:
            lines.append(f"The user currently has this selected in the viewer: {json.dumps(st.selection)}")
        if st.viewing and st.viewing not in ("working", st.head):
            lines.append(f"The user is looking at older version {st.viewing[:8]}, not the working copy.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ inspection

    @agent.tool
    async def read_reference(
        ctx: RunContext[HarnessDeps], topic: str | None = None, section: str | None = None,
        query: str | None = None,
    ) -> ToolReturn:
        """Read the harness's PCB rule/reference for a topic (e.g. "pcb-routing", "dfm"), or
        search all 53 documents with `query`. Pass `section` to get only one heading."""
        from kicad_harness.agent import knowledge

        if query and not topic:
            return ToolReturn(return_value={"ok": True, "query": query,
                                            "matches": knowledge.search(query)})
        if not topic:
            return ToolReturn(return_value={"ok": False, "error": "give a topic or a query"})
        doc = knowledge.read(topic)
        if doc is None:
            return ToolReturn(return_value={
                "ok": False,
                "error": f"unknown topic {topic!r}",
                "topics": knowledge.topic_ids(),
            })
        text = doc["text"]
        if section:
            wanted = section.strip().lower()
            chunks = text.split("\n## ")
            keep = [chunk for chunk in chunks if chunk.split("\n", 1)[0].strip().lower().startswith(wanted)]
            if keep:
                text = "## " + keep[0]
        return ToolReturn(return_value={
            "ok": True,
            "topic": doc["id"],
            "title": doc["title"],
            "source": doc["source"],
            "headings": doc["headings"],
            "text": text[:20000],
        })

    @agent.tool
    @guarded
    async def get_design(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
        """Board outline, components (ref, value, footprint, position, side), nets, DRC counts, goal status and open review threads."""
        svc = ctx.deps.services
        ws = _ws(ctx)
        await _sync_state(ctx)
        if ws.phase() == "schematic" and not ws.board_path.is_file():
            # A model's first move is often get_design; answer it for the schematic rather
            # than failing, so the run starts with the right picture.
            return await schematic_summary(ctx)
        board_hash, index = await _working(ctx)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        evaluation = await _evaluate(ctx)
        threads = svc.store.review.list(ctx.deps.state.design)
        ex, ey, ew, eh = index["board"]["edge_bbox"]
        summary = {
            "ok": True,
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
    async def measure_clearance(
        ctx: RunContext[HarnessDeps], a: str | None = None, b: str | None = None,
        region: list[float] | None = None, layer: str | None = None,
        rule_mm: float | None = None,
    ) -> ToolReturn:
        """Nearest copper clearance, rule-aware.

        - two nets (`net:GND`, `net:+5V`, or bare names): layer-by-layer net-to-net clearance;
        - two entities: edge-to-edge distance (same as `measure`);
        - one net: its nearest copper on any other net;
        - `region` only: the closest pair of different nets inside [x1,y1,x2,y2].

        `rule_mm` turns the result into a pass/fail against a declared limit. Prefer this over
        several one-off distances when checking spacing."""
        from kicad_harness.engine import geometry

        _, index = await _working(ctx)
        try:
            result = await asyncio.to_thread(geometry.clearance_query, index, a, b, region, layer)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        passed = None
        if rule_mm is not None:
            passed = float(result["distance"]) + 1e-9 >= float(rule_mm)
        return ToolReturn(return_value={
            "ok": True, **result, "rule_mm": rule_mm, "pass": passed,
        })

    @agent.tool
    async def probe(ctx: RunContext[HarnessDeps], x: float, y: float) -> ToolReturn:
        """What is at a board point (mm): copper hits (pad/track/via/zone), the footprint whose
        bbox contains it, and its visible reference text. Use it to turn a spot in an image into
        an entity you can query or edit."""
        from kicad_harness.engine import geometry

        _, index = await _working(ctx)
        return ToolReturn(return_value={"ok": True, **geometry.probe(index, x, y)})

    @agent.tool
    async def check(ctx: RunContext[HarnessDeps], detail: bool = False) -> ToolReturn:
        """Check the current phase and return an image of it.

        Schematic phase: ERC counts, the legibility score/counts/findings and the goal.
        Board phase: DRC counts, unconnected items and the goal. `detail=True` adds the first
        violations; the rendered artifact always comes with the result so visual faults show up.
        """
        svc = ctx.deps.services
        ws = _ws(ctx)
        await _sync_state(ctx)

        if ws.phase() == "schematic" or not ws.board_path.is_file():
            from kicad_harness.engine import kicad_cli
            from kicad_harness.engine import layout as layout_mod

            try:
                erc = await asyncio.to_thread(kicad_cli.erc, ws.schematic_path, config=svc.config)
                erc_counts, erc_violations = erc["counts"], erc["violations"]
            except Exception as exc:
                erc_counts = {"errors": None, "warnings": None}
                erc_violations = [{"error": _engine_error_text(exc)}]
            try:
                doc = await asyncio.to_thread(_schematic, ctx)
                layout_report = await asyncio.to_thread(layout_mod.analyze, doc)
                layout_report["findings"] = await asyncio.to_thread(layout_mod.findings, doc)
                layout_report["metrics"] = await asyncio.to_thread(layout_mod.metrics, doc)
            except Exception as exc:
                layout_report = {"ok": False, "score": None, "error": _engine_error_text(exc)}
            evaluation = await _evaluate(ctx)
            result: dict = {
                "ok": True,
                "phase": "schematic",
                "erc": erc_counts,
                "layout": layout_report,
                "goal_met": evaluation["met"],
                "criteria": evaluation["criteria"],
            }
            if detail:
                result["erc_violations"] = erc_violations[:15]
            try:
                png, url = await asyncio.to_thread(_schematic_image, svc, ws)
                ctx.deps.state.last_render = RenderInfo(url=url, caption="Schematic sheet", view={})
                return ToolReturn(
                    return_value=[result, BinaryContent(data=png, media_type="image/png")],
                    metadata=[_snapshot(ctx.deps.state)],
                )
            except Exception:
                return ToolReturn(return_value=result, metadata=[_snapshot(ctx.deps.state)])

        board_hash, _ = await _working(ctx)
        drc = await asyncio.to_thread(svc.artifacts.drc, board_hash)
        evaluation = await _evaluate(ctx)
        from kicad_harness.engine import balance, design_rules, heights, manufacturing, si

        index = await asyncio.to_thread(svc.artifacts.index, board_hash)
        rules = await asyncio.to_thread(design_rules.load, ws)
        rule_violations = await asyncio.to_thread(design_rules.violations, index, rules)
        mfg = await asyncio.to_thread(manufacturing.status, ws)
        height_report = await asyncio.to_thread(
            lambda: heights.report(index, heights.load(ws)))
        stub_report = await asyncio.to_thread(si.report, index, rules)

        def brief(v: dict) -> dict:
            return {
                "type": v.get("type"),
                "severity": v.get("severity"),
                "description": v.get("description"),
                "items": [
                    {"description": it.get("description"), "x": it.get("x"), "y": it.get("y"),
                     "uuid": it.get("uuid")}
                    for it in v.get("items", [])[:3]
                ],
            }

        schematic_report = None
        if ws.schematic_path.is_file():
            from kicad_harness.engine import layout as layout_mod
            try:
                sdoc = await asyncio.to_thread(_schematic, ctx)
                schematic_report = await asyncio.to_thread(layout_mod.analyze, sdoc)
                schematic_report["findings"] = await asyncio.to_thread(layout_mod.findings, sdoc)
                schematic_report["metrics"] = await asyncio.to_thread(layout_mod.metrics, sdoc)
            except Exception:
                schematic_report = None
        result = {
            "ok": True,
            "phase": "board",
            "drc_counts": drc.get("counts", {}),
            "violations": [brief(v) for v in drc.get("violations", [])[:30]],
            "unconnected": [brief(v) for v in drc.get("unconnected", [])[:20]],
            "goal_met": evaluation["met"],
            "criteria": evaluation["criteria"],
            "rules": {
                "netclasses": rules.get("netclasses"),
                "stackup": rules.get("stackup"),
                "count": len(rule_violations),
                "violations": rule_violations[:20],
            },
            "manufacturing": mfg,
            "copper_balance": balance.report(index),
            "test_coverage": balance.test_coverage(index),
            "heights": height_report,
            "via_stubs": stub_report,
            "schematic": schematic_report,
        }
        try:
            spec = {"mode": "2d", "side": "front"}
            png_path, url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
            ctx.deps.state.last_render = RenderInfo(
                url=url, caption="Board layout, front (fitted)", view=spec)
            return ToolReturn(
                return_value=[result, BinaryContent(data=Path(png_path).read_bytes(),
                                                   media_type="image/png")],
                metadata=[_snapshot(ctx.deps.state)],
            )
        except Exception:
            return ToolReturn(return_value=result, metadata=[_snapshot(ctx.deps.state)])

    @agent.tool
    async def silkscreen_check(ctx: RunContext[HarnessDeps],
                               region: list[float] | None = None) -> ToolReturn:
        """Check reference text against pads, vias, other text, the minimum character height and
        the board-edge band, citing `pcb-silkscreen#Placement Rules`."""
        from kicad_harness.engine import qa

        _, index = await _working(ctx)
        return ToolReturn(return_value={"ok": True,
                                        **await asyncio.to_thread(qa.silkscreen, index, region)})

    @agent.tool
    async def courtyard_check(ctx: RunContext[HarnessDeps],
                              region: list[float] | None = None) -> ToolReturn:
        """Report overlapping footprint courtyards (assembly/rework clearance) and footprints with
        no courtyard, citing `pcb-placement#Spacing`."""
        from kicad_harness.engine import qa

        _, index = await _working(ctx)
        return ToolReturn(return_value={"ok": True,
                                        **await asyncio.to_thread(qa.courtyards, index, region)})

    @agent.tool
    @guarded
    async def render(
        ctx: RunContext[HarnessDeps],
        kind: str = "board",
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
        """Render the board (`kind="board"`) or the whole schematic sheet (`kind="schematic"`)
        as an image you can see.

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
        if kind == "schematic":
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

    @agent.tool
    @guarded
    async def inspect(
        ctx: RunContext[HarnessDeps], target: str | None = None,
        region: list[float] | None = None, side: str = "front",
        annotate: list[str] | None = None, width_px: int = 1400,
    ) -> ToolReturn:
        """A close-up, annotated, calibrated view of the board or schematic.

        Args:
            target: Zoom to an entity: U1, U1.3, net:GND, a UUID, or "x,y".
            region: Explicit [x1, y1, x2, y2] crop in mm (wins over target).
            side: front or back for a board view.
            annotate: any of "refs" (reference designators), "pads" (pad numbers), "scale"
                (mm scale bar). Default: all three on the board, refs on the schematic.
            width_px: Image width (capped at 6000).

        Returns the image plus the view box and mm-per-pixel, so image coordinates can be
        converted back to mm and fed to `probe` or `measure_clearance`."""
        from kicad_harness.engine import geometry, layout as layout_mod, render as render_mod

        ws = _ws(ctx)
        annotate = [item.lower() for item in (annotate or [])] or ["refs", "pads", "scale"]
        if ws.phase() == "schematic" or not ws.board_path.is_file():
            doc = await asyncio.to_thread(_schematic, ctx)
            boxes = await asyncio.to_thread(layout_mod.symbol_boxes, doc)
            highlight: list[list[float]] = []
            if target:
                if target.startswith("net:"):
                    name = target[4:]
                    refs = {m["ref"] for m in doc.nets().get(name, [])
                            if not m["ref"].startswith("#")}
                    chosen = [boxes[ref] for ref in refs if ref in boxes]
                elif target in boxes:
                    chosen = [boxes[target]]
                else:
                    raise ModelRetry(f"Unknown schematic target {target!r}: use a ref, net:NAME or x,y")
                if not chosen:
                    raise ModelRetry(f"Nothing to inspect for {target!r}")
                highlight = [list(box) for box in chosen]
                view_region = [min(box[0] for box in chosen) - 4,
                               min(box[1] for box in chosen) - 4,
                               max(box[2] for box in chosen) + 4,
                               max(box[3] for box in chosen) + 4]
            else:
                view_region = region
            png, view = await asyncio.to_thread(
                render_mod.schematic_view, ws.schematic_path, region=view_region,
                width=width_px, highlight=highlight, scale_bar="scale" in annotate)
            meta = {"ok": True, "kind": "schematic", "target": target, "view": view,
                    "mm_per_px": round(1.0 / view["px_per_mm"], 4) if view.get("px_per_mm") else None,
                    "highlight": highlight, "annotations": annotate}
            return ToolReturn(return_value=[json.dumps(meta),
                                            BinaryContent(data=png, media_type="image/png")],
                              metadata=[_snapshot(ctx.deps.state)])

        svc = ctx.deps.services
        board_hash, index = await _working(ctx)
        highlight_specs = [target] if target else []
        entities: list[dict] = []
        if target:
            try:
                entities = await asyncio.to_thread(geometry.entities_in, index, [target])
            except Exception as exc:
                raise ModelRetry(f"Unknown inspect target {target!r}: {exc}") from exc
        if region is not None:
            x1, y1, x2, y2 = (float(v) for v in region)
            view_region = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        elif entities:
            bounds = [geometry.entity_shape(ent).bounds for ent in entities]
            x1 = min(b[0] for b in bounds)
            y1 = min(b[1] for b in bounds)
            x2 = max(b[2] for b in bounds)
            y2 = max(b[3] for b in bounds)
            margin = max(1.5, 0.18 * max(x2 - x1, y2 - y1))
            view_region = [x1 - margin, y1 - margin, x2 + margin, y2 + margin]
        else:
            ex, ey, ew, eh = index["board"]["edge_bbox"]
            view_region = [ex, ey, ex + ew, ey + eh]
        minimal = 3.0
        if view_region[2] - view_region[0] < minimal or view_region[3] - view_region[1] < minimal:
            cx, cy = ((view_region[0] + view_region[2]) / 2, (view_region[1] + view_region[3]) / 2)
            view_region = [cx - minimal / 2, cy - minimal / 2, cx + minimal / 2, cy + minimal / 2]
        width_px = max(200, min(6000, int(width_px)))
        spec = {"mode": "2d", "side": side, "region": view_region, "width_px": width_px,
                "highlight": highlight_specs, "labels": "refs" in annotate,
                "pad_labels": "pads" in annotate, "scale_bar": "scale" in annotate}
        try:
            png_path, url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        w = view_region[2] - view_region[0]
        h = view_region[3] - view_region[1]

        def inside(x: float, y: float) -> bool:
            return view_region[0] <= x <= view_region[2] and view_region[1] <= y <= view_region[3]

        counts = {
            "pads": sum(1 for item in index.get("pads", []) if inside(float(item["x"]), float(item["y"]))),
            "vias": sum(1 for item in index.get("vias", []) if inside(float(item["x"]), float(item["y"]))),
            "tracks": sum(1 for item in index.get("tracks", [])
                          if inside((float(item["x1"]) + float(item["x2"])) / 2,
                                    (float(item["y1"]) + float(item["y2"])) / 2)),
        }
        meta = {"ok": True, "kind": "board", "target": target, "side": side,
                "view": {"x0": round(view_region[0], 3), "y0": round(view_region[1], 3),
                         "w": round(w, 3), "h": round(h, 3)},
                "mm_per_px": round(w / width_px, 4), "counts": counts,
                "entities": [geometry.describe(ent) for ent in entities[:50]],
                "annotations": annotate, "url": url}
        ctx.deps.state.last_render = RenderInfo(
            url=url, caption=f"inspect {target or view_region}", view=spec)
        return ToolReturn(return_value=[json.dumps(meta),
                                        BinaryContent(data=Path(png_path).read_bytes(),
                                                      media_type="image/png")],
                          metadata=[_snapshot(ctx.deps.state)])

    # ------------------------------------------------------------------ review

    @agent.tool
    @guarded
    async def list_reviews(ctx: RunContext[HarnessDeps], status: str = "open") -> Any:
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
    async def reply_review(ctx: RunContext[HarnessDeps], number: int, message: str) -> dict:
        """Reply to review thread #number (to explain a decision or ask the user a question)."""
        svc = ctx.deps.services
        thread = svc.store.review.by_number(ctx.deps.state.design, number)
        if not thread:
            raise ModelRetry(f"No thread #{number}")
        svc.store.review.reply(thread["id"], message, author="agent", sha=_ws(ctx).head())
        return {"ok": True, "thread": number}

    @agent.tool
    async def mark_review_addressed(ctx: RunContext[HarnessDeps], number: int, note: str) -> dict:
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
    async def delete_board_items(
        ctx: RunContext[HarnessDeps],
        nets: list[str] | None = None,
        refs: list[str] | None = None,
        uuids: list[str] | None = None,
    ) -> ToolReturn:
        """Delete board items by selector: `nets` rip up their tracks and vias, `refs` remove
        footprints (and the copper on them), `uuids` delete any item returned by query."""
        if not (nets or refs or uuids):
            return ToolReturn(return_value={"ok": False, "error": "give nets, refs or uuids"})

        def work(board) -> dict:
            eng = engine(ctx)
            out: dict = {"deleted": 0}
            if nets:
                result = eng.call("delete_tracks", board=board, nets=nets)
                out["tracks"] = result
                out["deleted"] += int(result.get("deleted", 0))
            for ref in refs or []:
                out.setdefault("footprints", []).append(
                    eng.call("remove_footprint", board=board, ref=ref))
                out["deleted"] += 1
            if uuids:
                result = eng.call("delete_items", board=board, uuids=uuids)
                out["items"] = result
                out["deleted"] += int(result.get("deleted", 0))
            return out

        if nets and not refs and not uuids:
            subject = f"Rip up tracks on {', '.join(nets)}"
        else:
            subject = "Delete board items"
        return await _edit(ctx, "delete_board_items", subject, work, risky=True)

    @agent.tool(sequential=True)
    async def add_track(
        ctx: RunContext[HarnessDeps], net: str, points: list[list[float]],
        width: float | None = None, layer: str = "F.Cu"
    ) -> ToolReturn:
        """Draw a track through `points` [[x, y], ...] on `layer` (F.Cu or B.Cu). Prefer
        45-degree bends. Omit `width` to use the net's declared class width."""
        if width is None:
            from kicad_harness.engine import design_rules
            rules = design_rules.load(_ws(ctx))
            _name, spec = design_rules.class_for(rules, net)
            width = float(spec.get("width") or 0.25)
        return await _edit(
            ctx, "add_track", f"Route {net} on {layer} ({len(points) - 1} segment(s))",
            lambda b: engine(ctx).call("add_track", board=b, net=net, points=points, width=width, layer=layer),
        )

    @agent.tool(sequential=True)
    async def add_via(
        ctx: RunContext[HarnessDeps], x: float, y: float, net: str, size: float = 0.6,
        drill: float = 0.3, via_type: str = "through", layers: list[str] | None = None,
    ) -> ToolReturn:
        """Add a via at (x, y) on `net`: through (default), micro, blind or buried.

        Blind/buried vias need `layers` naming the two copper layers they join."""
        return await _edit(
            ctx, "add_via", f"Add {net} {via_type} via at ({x:g}, {y:g})",
            lambda b: engine(ctx).call("add_via", board=b, x=x, y=y, net=net, size=size,
                                       drill=drill, via_type=via_type, layers=layers),
        )

    @agent.tool(sequential=True)
    async def add_via_stitching(
        ctx: RunContext[HarnessDeps], net: str = "GND", spacing: float = 5.0,
        inset: float = 1.0, size: float = 0.6, drill: float = 0.3,
    ) -> ToolReturn:
        """Ring stitching vias around the board edge on `net`, every `spacing` mm, `inset` from
        the edge. Use to tie the ground planes together (pcb-emc#Via Stitching)."""
        board_hash, index = await _working(ctx)
        ex, ey, ew, eh = index["board"]["edge_bbox"]
        step = max(1.0, float(spacing))
        inset = max(0.2, float(inset))
        points: list[tuple[float, float]] = []
        x, y = ex + inset, ey + inset
        while x <= ex + ew - inset + 1e-6:
            points.append((x, ey + inset))
            points.append((x, ey + eh - inset))
            x += step
        y = ey + inset + step
        while y <= ey + eh - inset - 1e-6:
            points.append((ex + inset, y))
            points.append((ex + ew - inset, y))
            y += step
        # Corners are covered by the first pass; avoid duplicate coordinates.
        points = list(dict.fromkeys((round(px, 3), round(py, 3)) for px, py in points))

        def work(board) -> dict:
            eng = engine(ctx)
            for px, py in points:
                eng.call("add_via", board=board, x=px, y=py, net=net, size=size, drill=drill)
            return {"vias": len(points), "net": net, "spacing": step, "inset": inset}

        return await _edit(ctx, "add_via_stitching", f"Stitch {net} with {len(points)} vias", work)

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
    async def place_silkscreen(ctx: RunContext[HarnessDeps]) -> ToolReturn:
        """Move every reference designator to a free spot beside its footprint, avoiding parts and
        other text. Use after placement/routing; DRC does not check text collisions."""
        board_hash, index = await _working(ctx)
        footprints = sorted(index.get("footprints", []), key=lambda fp: fp.get("ref", ""))

        def work(board) -> dict:
            eng = engine(ctx)
            occupied = [tuple(fp["bbox"]) for fp in footprints]
            placed = 0
            for fp in footprints:
                x, y, w, h = fp["bbox"]
                text = str(fp.get("ref") or "")
                width = max(2.0, 1.15 * len(text))
                candidates = [
                    (x + w / 2 - width / 2, y - 1.4),        # above
                    (x + w / 2 - width / 2, y + h + 1.4),    # below
                    (x + w + 1.2, y + h / 2),                # right
                    (x - width - 1.2, y + h / 2),            # left
                ]
                for cx, cy in candidates:
                    box = (cx, cy - 1.0, cx + width, cy + 1.0)
                    if any(_boxes_overlap(box, other) for other in occupied):
                        continue
                    eng.call("move_reference_text", board=board, ref=fp["ref"], x=cx, y=cy)
                    occupied.append(box)
                    placed += 1
                    break
            return {"placed": placed, "footprints": len(footprints)}

        return await _edit(ctx, "place_silkscreen", "Place silkscreen references", work)

    @agent.tool(sequential=True)
    async def add_thermal_vias(
        ctx: RunContext[HarnessDeps], ref: str, pad: str | None = None,
        count: int = 4, radius: float = 1.5,
    ) -> ToolReturn:
        """Ring `count` vias around a power-pad (default: the largest pad of `ref`) to spread heat
        into the plane. The vias take the pad's net."""
        board_hash, index = await _working(ctx)
        pads = [p for p in index.get("pads", [])
                if p.get("ref") == ref and (pad is None or str(p.get("pad")) == str(pad))]
        if not pads:
            return ToolReturn(return_value={"ok": False, "error": f"no matching pad on {ref}"})
        target = max(pads, key=lambda p: float(p.get("w", 0)) * float(p.get("h", 0)))
        net = target.get("net") or ""
        if not net:
            return ToolReturn(return_value={"ok": False, "error": "the pad has no net"})

        def work(board) -> dict:
            eng = engine(ctx)
            for i in range(max(1, count)):
                angle = 2 * math.pi * i / max(1, count)
                eng.call("add_via", board=board,
                         x=float(target["x"]) + radius * math.cos(angle),
                         y=float(target["y"]) + radius * math.sin(angle), net=net)
            return {"vias": max(1, count), "pad": target.get("pad"), "net": net}

        return await _edit(ctx, "add_thermal_vias", f"Thermal vias under {ref}", work)

    @agent.tool(sequential=True)
    async def route_diff_pair(
        ctx: RunContext[HarnessDeps], net_p: str, net_n: str, points: list[list[float]],
        spacing: float = 0.2, width: float | None = None, layer: str = "F.Cu",
    ) -> ToolReturn:
        """Route a differential pair through `points`: two parallel tracks offset by `spacing`."""
        if len(points) < 2:
            return ToolReturn(return_value={"ok": False, "error": "a pair needs two points"})
        if width is None:
            from kicad_harness.engine import design_rules
            rules = design_rules.load(_ws(ctx))
            _name, spec = design_rules.class_for(rules, net_p)
            width = float(spec.get("width") or 0.2)
        (x0, y0), (x1, y1) = points[0], points[1]
        length = math.dist((x0, y0), (x1, y1)) or 1.0
        nx, ny = -(y1 - y0) / length, (x1 - x0) / length
        offset = spacing / 2
        positive = [[x + nx * offset, y + ny * offset] for x, y in points]
        negative = [[x - nx * offset, y - ny * offset] for x, y in points]

        def work(board) -> dict:
            eng = engine(ctx)
            first = eng.call("add_track", board=board, net=net_p, points=positive,
                             width=width, layer=layer)
            second = eng.call("add_track", board=board, net=net_n, points=negative,
                              width=width, layer=layer)
            return {"net_p": net_p, "net_n": net_n, "width": width, "spacing": spacing,
                    "segments": len(first.get("uuids", [])) + len(second.get("uuids", []))}

        return await _edit(ctx, "route_diff_pair", f"Route pair {net_p}/{net_n}", work)

    @agent.tool
    async def match_length(ctx: RunContext[HarnessDeps], nets: list[str],
                           tolerance_mm: float = 1.0) -> ToolReturn:
        """Report each net's routed length, whether the group matches within `tolerance_mm`, and
        the worst via stub per net (a length match is meaningless if one net has a long stub)."""
        from kicad_harness.engine import design_rules, si

        board_hash, index = await _working(ctx)
        lengths: dict[str, float] = {}
        for net in nets:
            total = 0.0
            for track in index.get("tracks", []):
                if track.get("net") != net:
                    continue
                total += math.dist((track["x1"], track["y1"]), (track["x2"], track["y2"]))
            lengths[net] = round(total, 2)
        stub_report = si.report(index, design_rules.load(_ws(ctx)))
        by_net = stub_report.get("net_stub_mm") or {}
        stubs = {net: float(by_net.get(net, 0.0)) for net in nets}
        values = list(lengths.values())
        delta = round(max(values) - min(values), 2) if values else 0.0
        return ToolReturn(return_value={
            "ok": True,
            "lengths_mm": lengths,
            "delta_mm": delta,
            "tolerance_mm": tolerance_mm,
            "matched": bool(values) and delta <= tolerance_mm,
            "via_stub_mm": {net: round(value, 3) for net, value in stubs.items()},
            "stub_limit_mm": stub_report.get("limit_mm"),
        })

    def _boxes_overlap(a, b) -> bool:
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    @agent.tool(sequential=True)
    async def set_board_outline(
        ctx: RunContext[HarnessDeps], width: float | None = None, height: float | None = None,
        x: float = 0.0, y: float = 0.0, corner_radius: float = 0.0,
        points: list[list[float]] | None = None,
    ) -> ToolReturn:
        """Set the board outline: a `width` x `height` rectangle at (x, y), optionally rounded,
        or an imported polygon through `points` [[x,y], ...] from the mechanical drawing."""
        if points:
            if len(points) < 3:
                return ToolReturn(return_value={"ok": False, "error": "a polygon needs 3+ points"})
            return await _edit(
                ctx, "set_board_outline", f"Set board outline to a {len(points)}-point polygon",
                lambda b: engine(ctx).call("set_outline_polygon", board=b, points=points),
                risky=True,
            )
        if width is None or height is None:
            return ToolReturn(return_value={"ok": False, "error": "give width and height, or points"})
        return await _edit(
            ctx, "set_board_outline", f"Set board outline to {width:g} x {height:g} mm",
            lambda b: engine(ctx).call(
                "set_outline", board=b, width=width, height=height, x=x, y=y, corner_radius=corner_radius
            ),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def add_cutout(ctx: RunContext[HarnessDeps], x: float, y: float,
                         diameter: float) -> ToolReturn:
        """Cut a circular hole in the board (mounting hole, strain relief) at (x, y) mm."""
        return await _edit(
            ctx, "add_cutout", f"Cut a {diameter:g} mm hole at ({x:g}, {y:g})",
            lambda b: engine(ctx).call("add_cutout", board=b, x=x, y=y, diameter=diameter),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def add_mounting_holes(
        ctx: RunContext[HarnessDeps], drill: float = 3.2, inset: float = 5.0,
        pad: float = 6.0,
    ) -> ToolReturn:
        """Add four corner mounting holes (`drill` mm) `inset` mm from the board edge, with a
        `pad`-diameter keepout ring so copper stays away from the screw head."""
        board_hash, index = await _working(ctx)
        ex, ey, ew, eh = index["board"]["edge_bbox"]
        holes = [(ex + inset, ey + inset), (ex + ew - inset, ey + inset),
                 (ex + inset, ey + eh - inset), (ex + ew - inset, ey + eh - inset)]

        def work(board) -> dict:
            eng = engine(ctx)
            for hx, hy in holes:
                eng.call("add_cutout", board=board, x=hx, y=hy, diameter=drill)
                radius = max(drill, pad) / 2 + 0.5
                ring = [[hx + radius * math.cos(2 * math.pi * i / 24),
                         hy + radius * math.sin(2 * math.pi * i / 24)] for i in range(24)]
                eng.call("add_keepout", board=board, polygon=ring, no_pads=True,
                         no_tracks=True, no_vias=True, no_pours=True)
            return {"holes": len(holes), "drill": drill, "inset": inset}

        return await _edit(ctx, "add_mounting_holes", "Add four mounting holes", work,
                           risky=True)

    @agent.tool(sequential=True)
    async def add_keepout(
        ctx: RunContext[HarnessDeps], points: list[list[float]],
        layers: list[str] | None = None, no_tracks: bool = True, no_vias: bool = True,
        no_pours: bool = True, no_pads: bool = False,
    ) -> ToolReturn:
        """Add a rule-area keepout through `points` [[x,y],...]: no tracks/vias/pours (optionally
        pads) inside it. Use for antennas, mounting hardware and high-voltage gaps."""
        if len(points) < 3:
            return ToolReturn(return_value={"ok": False, "error": "a keepout needs 3+ points"})
        return await _edit(
            ctx, "add_keepout", f"Add keepout ({len(points)} points)",
            lambda b: engine(ctx).call(
                "add_keepout", board=b, polygon=points, layers=layers,
                no_tracks=no_tracks, no_vias=no_vias, no_pours=no_pours, no_pads=no_pads,
            ),
        )

    @agent.tool(sequential=True)
    async def import_outline(ctx: RunContext[HarnessDeps], path: str) -> ToolReturn:
        """Set the board outline from an ASCII DXF (LWPOLYLINE or LINE loop) or a STEP file
        (EDGE_CURVE wireframe, arcs sampled). Relative paths resolve inside the design workspace."""
        from kicad_harness.engine import dxf, step

        ws = _ws(ctx)
        source = Path(path) if Path(path).is_absolute() else ws.path / path
        if not source.is_file():
            return ToolReturn(return_value={"ok": False, "error": f"no mechanical file at {source}"})
        suffix = source.suffix.lower()
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
            if suffix in (".step", ".stp"):
                points = step.polygon(text)  # STEP is always millimetres
            elif suffix in (".dxf",):
                points = dxf.polygon(text)
                factor = dxf.units_mm(text)
                points = [[x * factor, y * factor] for x, y in points]
            else:
                return ToolReturn(return_value={"ok": False,
                                                "error": f"unsupported outline format {suffix!r}: use .dxf, .step or .stp"})
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        if len(points) < 3:
            return ToolReturn(return_value={"ok": False,
                                            "error": "no closed outline with 3+ points found in the file"})
        return await _edit(
            ctx, "import_outline", f"Import outline from {source.name}",
            lambda b: engine(ctx).call("set_outline_polygon", board=b, points=points),
            risky=True,
        )

    @agent.tool(sequential=True)
    async def place_test_points(
        ctx: RunContext[HarnessDeps], nets: list[str],
        footprint: str = "TestPoint:TestPoint_Pad_D1.5mm",
        symbol: str = "Connector:TestPoint",
    ) -> ToolReturn:
        """Place one test point per net, as a schematic symbol *and* a board footprint with the
        same reference, so schematic/board parity and the BOM stay valid. Points sit along the
        bottom board edge and take the net's class."""
        ws = _ws(ctx)
        if not ws.board_path.is_file():
            return ToolReturn(return_value={"ok": False, "error": "this design has no board yet"})
        if not ws.schematic_path.is_file():
            return ToolReturn(return_value={"ok": False, "error": "this design has no schematic"})

        board_hash, index = await _working(ctx)
        ex, ey, ew, eh = index["board"]["edge_bbox"]

        def work(board) -> dict:
            doc = _schematic(ctx, "Connector")
            existing = {p.reference for p in doc.placements() if p.reference.startswith("TP")}
            engine_obj = engine(ctx)
            added = []
            for i, net in enumerate(nets):
                ref = f"TP{1 + i}"
                while ref in existing:
                    i += 1
                    ref = f"TP{1 + i}"
                x = ex + 10 + i * 8
                y = ey + eh - 8
                doc.add_symbol(symbol, ref, x, y, value=net, footprint=footprint)
                engine_obj.call("add_footprint", board=board, footprint=footprint, ref=ref,
                                value=net, x=x, y=y, rotation=0, side="front",
                                footprint_dirs=[str(p) for p in ctx.deps.services.config.footprint_dirs])
                engine_obj.call("set_pad_net", board=board, ref=ref, pad="1", net=net)
                added.append({"ref": ref, "net": net, "x": round(x, 2), "y": round(y, 2)})
            doc.save()
            return {"added": added}

        return await _edit(ctx, "place_test_points", f"Place {len(nets)} test point(s)", work)

    @agent.tool
    async def set_net_class(
        ctx: RunContext[HarnessDeps],
        name: str,
        nets: list[str],
        width: float = 0.25,
        clearance: float = 0.2,
        impedance: float | None = None,
    ) -> ToolReturn:
        """Declare a net class (e.g. name="POWER", nets=["+5V","GND"], width=0.8).

        New tracks on those nets default to this width, `check` reports any narrower track, and
        the impedance target is recorded for high-speed nets. Net patterns may use `*`."""
        from kicad_harness.engine import design_rules

        ws = _ws(ctx)
        rules = await asyncio.to_thread(design_rules.load, ws)
        rules["netclasses"][name] = {"nets": nets, "width": width, "clearance": clearance,
                                     "impedance": impedance, "explicit": True}
        await asyncio.to_thread(design_rules.save, ws, rules)
        version = await asyncio.to_thread(
            lambda: ws.commit(f"Declare net class {name}", author="agent",
                              trailers=_trailers(ctx, "set_net_class")))
        return ToolReturn(return_value={"ok": True, "class": name, "nets": nets, "width": width,
                                        "clearance": clearance, "impedance": impedance,
                                        "version": version})

    @agent.tool
    async def set_stackup(
        ctx: RunContext[HarnessDeps], layers: int, material: str = "FR4",
        thickness: float = 1.6, impedance: dict | None = None,
        er: float = 4.4, dielectric_height: float = 0.2,
    ) -> ToolReturn:
        """Declare the stackup (copper layers, material, thickness, er, dielectric height,
        optional impedance map). `check` compares the declared copper count with the board and
        flags traces whose width misses the class impedance by >15%."""
        from kicad_harness.engine import design_rules

        ws = _ws(ctx)
        rules = await asyncio.to_thread(design_rules.load, ws)
        rules["stackup"] = {"layers": layers, "material": material, "thickness": thickness,
                            "impedance": impedance or {}, "er": er,
                            "dielectric_height": dielectric_height}
        await asyncio.to_thread(design_rules.save, ws, rules)
        applied = False
        if ws.board_path.is_file():
            try:
                ctx.deps.services.engine.call("set_copper_layers", board=str(ws.board_path),
                                              layers=layers)
                with ws.lock:
                    ws.commit(f"Set {layers}-layer stackup", author="agent",
                              trailers=_trailers(ctx, "set_stackup"))
                applied = True
            except Exception:
                applied = False
        return ToolReturn(return_value={"ok": True, **rules["stackup"], "board_layers_set": applied})

    @agent.tool(sequential=True)
    async def export_manufacturing(ctx: RunContext[HarnessDeps]) -> ToolReturn:
        """Write the orderable package: Gerbers for the production layers, Excellon drill,
        pick-and-place CSV, BOM CSV and a manifest with the board hash. Files go to
        `<design>/manufacturing`; `check` reports whether the package is present and current."""
        from kicad_harness.engine import manufacturing

        ws = _ws(ctx)
        if not ws.board_path.is_file():
            return ToolReturn(return_value={"ok": False, "error": "this design has no board yet"})
        try:
            manifest = await asyncio.to_thread(manufacturing.export, ws, ctx.deps.services.config)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        return ToolReturn(return_value={"ok": True, **manifest})

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

    # ------------------------------------------------------------------ history

    @agent.tool
    async def diff_versions(ctx: RunContext[HarnessDeps], a: str, b: str = "working",
                            scope: str = "nets", region: list[float] | None = None) -> ToolReturn:
        """Compare two revisions (a sha prefix or "working"): netlist (`scope="nets"`), BOM
        (`scope="bom"`) or the board geometry (`scope="board"`).

        Net/BOM diffs list added/removed/changed items; the board diff also returns two aligned
        images — additions/changes highlighted on `b`, removals/changes on `a` — so a silent
        layout drift is visible without reading pixels."""
        import os
        import tempfile

        from kicad_harness.engine import qa

        ws = _ws(ctx)
        svc = ctx.deps.services
        if scope == "board":
            def board_bytes(rev: str) -> bytes:
                if rev in ("working", "WORKING"):
                    return ws.board_path.read_bytes()
                return ws.file_at(rev, "board.kicad_pcb")

            try:
                old_hash, _ = await asyncio.to_thread(
                    lambda: svc.artifacts.materialize(board_bytes(a)))
                new_hash, _ = await asyncio.to_thread(
                    lambda: svc.artifacts.materialize(board_bytes(b)))
                old_index = await asyncio.to_thread(svc.artifacts.index, old_hash)
                new_index = await asyncio.to_thread(svc.artifacts.index, new_hash)
            except Exception as exc:
                return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
            diff = await asyncio.to_thread(qa.diff_indexes, old_index, new_index, region)
            if diff["summary"] == {"added": 0, "removed": 0, "changed": 0}:
                return ToolReturn(return_value={"ok": True, "scope": "board", **diff})
            boxes = [item["bbox"] for name in ("added", "removed", "changed")
                     for item in diff[name] if item.get("bbox")]
            if region is None and boxes:
                x1 = min(b[0] for b in boxes)
                y1 = min(b[1] for b in boxes)
                x2 = max(b[2] for b in boxes)
                y2 = max(b[3] for b in boxes)
                margin = max(2.0, 0.15 * max(x2 - x1, y2 - y1))
                view = [x1 - margin, y1 - margin, x2 + margin, y2 + margin]
            elif region is not None:
                view = list(region)
            else:
                ex, ey, ew, eh = new_index["board"]["edge_bbox"]
                view = [ex, ey, ex + ew, ey + eh]
            changed = [item["uuid"] for item in diff["changed"]]
            new_marks = [item["uuid"] for item in diff["added"]] + changed
            old_marks = [item["uuid"] for item in diff["removed"]] + changed
            spec_common = {"mode": "2d", "side": "front", "region": view, "width_px": 1400,
                           "labels": True, "pad_labels": False, "scale_bar": True}
            images = []
            for board_hash, marks, caption in (
                (new_hash, new_marks, f"added/changed on {b}"),
                (old_hash, old_marks, f"removed/changed on {a}"),
            ):
                spec = {**spec_common, "highlight": marks}
                try:
                    path, url = await asyncio.to_thread(svc.artifacts.render, board_hash, spec)
                except Exception as exc:
                    return ToolReturn(return_value={"ok": False, "scope": "board", **diff,
                                                    "error": _engine_error_text(exc)})
                images.append((caption, url, Path(path).read_bytes()))
            meta = {"ok": True, "scope": "board", "a": a, "b": b, "view": view,
                    "images": [{"caption": caption, "url": url} for caption, url, _ in images],
                    **diff}
            payload: list = [json.dumps(meta)]
            payload += [BinaryContent(data=png, media_type="image/png") for _, _, png in images]
            return ToolReturn(return_value=payload)

        def load(rev: str):
            if rev in ("working", "WORKING"):
                return Schematic.load(ws.schematic_path)
            data = ws.file_at(rev, "schematic.kicad_sch")
            with tempfile.NamedTemporaryFile(suffix=".kicad_sch", delete=False) as handle:
                handle.write(data)
                name = handle.name
            try:
                return Schematic.load(name)
            finally:
                os.unlink(name)

        try:
            left, right = await asyncio.to_thread(load, a), await asyncio.to_thread(load, b)
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        if scope == "bom":
            def bom(doc):
                return {p.reference: [p.value, p.footprint, (p.properties or {}).get("LCSC", "")]
                        for p in doc.placements() if not p.reference.startswith("#")}
            old, new = bom(left), bom(right)
            changed = {ref: {"before": old[ref], "after": new[ref]}
                       for ref in old.keys() & new.keys() if old[ref] != new[ref]}
            return ToolReturn(return_value={
                "ok": True, "scope": "bom",
                "added": sorted(new.keys() - old.keys()),
                "removed": sorted(old.keys() - new.keys()),
                "changed": changed,
            })
        def nets(doc):
            return {name: sorted((m["ref"], m["pin"]) for m in members)
                    for name, members in doc.nets().items()}
        old, new = nets(left), nets(right)
        changed = {name: {"before": old[name], "after": new[name]}
                   for name in old.keys() & new.keys() if old[name] != new[name]}
        return ToolReturn(return_value={
            "ok": True, "scope": "nets",
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": changed,
        })

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
                    "schematic with generate_schematic.",
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
            "ok": True,
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
    async def delete_schematic_items(ctx: RunContext[HarnessDeps],
                                     nets: list[str] | None = None,
                                     refs: list[str] | None = None) -> ToolReturn:
        """Delete schematic content: each `net` loses its wires and labels; each `ref` loses its
        symbol and the wires ending on its pins."""
        if not (nets or refs):
            return ToolReturn(return_value={"ok": False, "error": "give at least one net or ref"})

        def work(doc) -> dict:
            wires = sum(doc.remove_wires(net=net) for net in (nets or []))
            symbols = sum(1 for ref in (refs or []) if doc.remove_symbol(ref))
            return {"wires": wires, "symbols": symbols}

        return await _schematic_edit(
            ctx, "delete_schematic_items", "Delete schematic items", work,
        )

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
    async def move_symbol(
        ctx: RunContext[HarnessDeps], ref: str, x: float, y: float, rotation: float | None = None
    ) -> ToolReturn:
        """Move a schematic symbol to (x, y) mm, optionally rotating it. The reference/value text moves with it."""
        return await _schematic_edit(
            ctx, "move_symbol", f"Move {ref} to ({x:g}, {y:g})",
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
    async def label_net(ctx: RunContext[HarnessDeps], target: str, net: str,
                        global_: bool = False) -> ToolReturn:
        """Attach a net to one pin ("U1.3") or to a point ("120.5,80"); `global_` makes a
        global (sheet-wide power-style) label. For pin-to-pin connections use connect_pins."""
        text = target.strip()
        if "," in text:
            try:
                x_text, y_text = text.split(",", 1)
                x, y = float(x_text), float(y_text)
            except ValueError as exc:
                raise ModelRetry("target must be a pin (U1.3) or 'x,y' in mm") from exc
            return await _schematic_edit(
                ctx, "label_net", f"Label {net} at ({x:g}, {y:g})",
                lambda d: {"label": str(d.add_label(net, x, y, global_=global_)[1])},
            )
        try:
            ref, number = _split_pin(text)
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
            return {"ok": True, "pin": text, "net": net}

        return await _schematic_edit(ctx, "label_net", f"Label {text} as {net}", work)

    @agent.tool(sequential=True)
    async def generate_schematic(
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
        # Keep the current file so a failed generation can put it back untouched.
        previous = target.read_bytes() if target.is_file() else None
        try:
            result = await asyncio.to_thread(
                skidl_gen.generate, validated_parts, validated_nets, target,
                config=ctx.deps.services.config,
            )
        except Exception as exc:
            if previous is not None:
                await asyncio.to_thread(target.write_bytes, previous)
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
        return await _schematic_commit(ctx, "generate_schematic", f"Draw the schematic with skidl ({len(validated_parts)} parts)", payload)

    @agent.tool(sequential=True)
    async def tidy_schematic(ctx: RunContext[HarnessDeps], paper: str | None = None) -> ToolReturn:
        """Relayout the schematic: symbols in circuit-flow order, each 2-pin passive beside the
        part it serves (pull-ups by their input, decoupling caps by their IC), one short stub and
        net label per pin. Connectivity is preserved (labels with the same net name merge), so the
        netlist does not change. Call check afterwards, then route_schematic."""
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

    @agent.tool(sequential=True)
    async def start_board(ctx: RunContext[HarnessDeps], **_: Any) -> ToolReturn:
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
                "hint": "Use check for the full list, fix the wiring, then call this again.",
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
                              trailers=_trailers(ctx, "start_board"))
        )
        await _sync_state(ctx, bump=True)
        await _sync_schematic_state(ctx)
        payload.update(ok=True, phase="board", version=_version_brief(version))
        if ctx.deps.on_commit and version:
            ctx.deps.on_commit({"tool": "start_board", **_version_brief(version)})
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
        categories: bool = False,
        mpn: str | None = None,
        lcsc: str | None = None,
    ) -> ToolReturn:
        """Search the JLCPCB catalogue, browse its categories, or verify one part.

        - `query`/`category`/`package`: search parts (`query` matches the manufacturer MPN;
          `category` browses, e.g. "Relays").
        - `categories=True`: list categories with stock counts (`query` filters the list).
        - `mpn`/`lcsc`: verify one part - stock, price, tier and datasheet availability.
        Results carry stock, unit price and whether the part is basic or extended.
        """
        from kicad_harness.engine import parts

        try:
            if categories:
                items = await asyncio.to_thread(parts.categories, query)
                return ToolReturn(return_value={"ok": True, "categories": items})
            if mpn or lcsc:
                result = await asyncio.to_thread(parts.find_parts, mpn or "", lcsc)
                result["ok"] = True
                return ToolReturn(return_value=result)
            result = await asyncio.to_thread(
                parts.search_jlc, query, category=category, package=package,
                basic_only=basic_only, limit=limit,
            )
        except Exception as exc:
            return ToolReturn(return_value={"ok": False, "error": _engine_error_text(exc)})
        result["ok"] = True
        return ToolReturn(return_value=result)

    @agent.tool
    async def set_bom_alternates(ctx: RunContext[HarnessDeps], ref: str,
                                 lcscs: list[str]) -> ToolReturn:
        """Record second-source LCSC codes for a part; they are written into the manufacturing BOM."""
        import json

        ws = _ws(ctx)
        path = ws.path / "bom_alternates.json"
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
        data[ref] = sorted({code.strip().upper() for code in lcscs if code.strip()})
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return ToolReturn(return_value={"ok": True, "ref": ref, "alternates": data[ref]})

    @agent.tool
    async def set_component_height(ctx: RunContext[HarnessDeps], ref: str, height_mm: float,
                                   source: str | None = None) -> ToolReturn:
        """Record a part's height from its datasheet (mm above the board). `check` compares it
        with the enclosure and flags parts whose height was never measured."""
        from kicad_harness.engine import heights

        ws = _ws(ctx)
        if height_mm <= 0:
            return ToolReturn(return_value={"ok": False, "error": "height_mm must be positive"})
        entry = await asyncio.to_thread(heights.set_part, ws, ref, height_mm, source)
        return ToolReturn(return_value={"ok": True, **entry})

    @agent.tool
    async def set_enclosure(ctx: RunContext[HarnessDeps], height_mm: float,
                            clearance_mm: float = 5.0) -> ToolReturn:
        """Declare the internal enclosure (lid) height and the PCB-to-lid clearance.
        pcb-mechanical#Component Height & Enclosure calls for 5-10 mm; `check` then reports
        over-height and unmeasured parts."""
        from kicad_harness.engine import heights

        ws = _ws(ctx)
        if height_mm <= 0:
            return ToolReturn(return_value={"ok": False, "error": "height_mm must be positive"})
        entry = await asyncio.to_thread(heights.set_enclosure, ws, height_mm, clearance_mm)
        return ToolReturn(return_value={"ok": True, **entry})

    @agent.tool
    async def set_part_lifecycle(ctx: RunContext[HarnessDeps], ref: str, msl: int | None = None,
                                 esd: str | None = None, lead_time_days: int | None = None,
                                 status: str = "active") -> ToolReturn:
        """Record manufacturing lifecycle data for a part (moisture level, ESD class, lead time,
        active/EOL); the manufacturing BOM carries the columns."""
        import json

        ws = _ws(ctx)
        path = ws.path / "bom_lifecycle.json"
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
        entry = {"status": status}
        if msl is not None:
            entry["msl"] = msl
        if esd:
            entry["esd"] = esd
        if lead_time_days is not None:
            entry["lead_time_days"] = lead_time_days
        data[ref] = entry
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return ToolReturn(return_value={"ok": True, "ref": ref, **entry})

    @agent.tool
    async def read_part_datasheet(
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

    # One envelope for every tool result: {ok, data|error, version}; images stay attachments.
    import functools as _functools
    import inspect as _inspect

    for _tool in agent._function_toolset.tools.values():
        _schema = _tool.function_schema
        if _schema is None or getattr(_schema, "_enveloped", False):
            continue
        _original = _schema.function
        if _inspect.iscoroutinefunction(_original):
            @_functools.wraps(_original)
            async def _wrapped(*args, __original=_original, **kwargs):
                return _envelope_result(await __original(*args, **kwargs))
        else:
            @_functools.wraps(_original)
            def _wrapped(*args, __original=_original, **kwargs):
                return _envelope_result(__original(*args, **kwargs))
        try:
            _schema.function = _wrapped
            _schema._enveloped = True
        except Exception:
            pass

    # The Settings catalogue's one-line "when to use it" is part of the tool contract, not just
    # documentation: append it to the description the model receives. Must run after all tools
    # are registered (the decorators below the Agent(...) constructor add them).
    from . import tool_catalog as _catalog

    for name, tool in agent._function_toolset.tools.items():
        guidance = _catalog.tool_guidance(name)
        if guidance and guidance not in (tool.description or ""):
            merged = f"{tool.description or ''}\n\nWhen to use: {guidance}".strip()
            try:
                tool.description = merged
                if tool.function_schema is not None:
                    tool.function_schema.description = merged
            except Exception:
                pass

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
