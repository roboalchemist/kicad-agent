"""Headless entry point: run the agent against a design's goal without any UI. See DESIGN.md, "CLI"."""

from __future__ import annotations

import os

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import typer
from pydantic_ai.settings import ModelSettings

app = typer.Typer(help="Agent harness for KiCad boards: headless runs, versions, review threads, and the API server.")

DIM, BOLD, CYAN, GREEN, YELLOW, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def _color(enabled: bool):
    def paint(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if enabled else text

    return paint


def _services():
    from kicad_harness.agent.agent import Services

    return Services.create()


def _workspace(svc, name: str):
    from kicad_harness.store.workspace import Workspace

    if not Workspace.exists(svc.config, name):
        typer.echo(f"No design {name!r}. Create one with: kicad-harness init {name} --from BOARD", err=True)
        raise typer.Exit(2)
    return Workspace.open(svc.config, name)


@app.command()
def init(name: str, from_board: Path = typer.Option(..., "--from", exists=True, dir_okay=False),
         goal: Path | None = typer.Option(None, "--goal", exists=True, dir_okay=False)):
    """Create a design workspace (a git repo) from an existing .kicad_pcb."""
    from kicad_harness.agent import goal as goals
    from kicad_harness.store.workspace import Workspace

    svc = _services()
    if Workspace.exists(svc.config, name):
        typer.echo(f"Design {name!r} already exists.", err=True)
        raise typer.Exit(1)
    spec = goals.validate_goal(json.loads(goal.read_text())) if goal else None
    ws = Workspace.create(svc.config, name, from_board, spec, svc.engine)
    typer.echo(f"Created {name} at {ws.board_path.parent} (head {ws.head()[:8]})")


@app.command("goal")
def goal_cmd(name: str, set_file: Path | None = typer.Option(None, "--set", exists=True, dir_okay=False)):
    """Show the design's goal, or replace it from a JSON file."""
    from kicad_harness.agent import goal as goals

    svc = _services()
    ws = _workspace(svc, name)
    if set_file:
        spec = goals.validate_goal(json.loads(set_file.read_text()))
        with ws.lock:
            ws.set_goal(spec, author="user")
    typer.echo(goals.describe(ws.goal()))


@app.command()
def versions(name: str, limit: int = 20):
    """List versions on the current branch, newest first."""
    svc = _services()
    ws = _workspace(svc, name)
    for v in ws.log(limit=limit):
        v = v if isinstance(v, dict) else v.__dict__
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v["time"]))
        typer.echo(f"{v['short']}  {when}  {v['author']:<5}  {v['subject']}")


@app.command()
def threads(name: str, status: str | None = None):
    """List review threads."""
    svc = _services()
    _workspace(svc, name)
    for t in svc.store.review.list(name, status=status):
        a = t["anchor"]
        where = ".".join(x for x in (a.get("ref"), a.get("pad")) if x) or a.get("net") or f"({a.get('x')}, {a.get('y')})"
        tags = ",".join(t.get("tags", []))
        typer.echo(f"#{t['number']:<3} {t['status']:<9} {t['severity']:<10} {where:<10} [{tags}] {t['body']}")


@app.command()
def comment(name: str, body: str = typer.Option(...), ref: str | None = None, pad: str | None = None,
            net: str | None = None, x: float | None = None, y: float | None = None,
            severity: str = "suggestion", tag: list[str] = typer.Option([], "--tag")):
    """Leave a review comment anchored to a footprint, pad, net or point (like clicking it in the UI)."""
    svc = _services()
    ws = _workspace(svc, name)
    board_hash, _ = svc.artifacts.materialize(ws.board_path.read_bytes())
    index = svc.artifacts.index(board_hash)
    anchor: dict[str, Any] = {"kind": "point", "uuid": None, "ref": None, "pad": None, "net": net, "layer": None,
                              "side": "front", "x": x, "y": y, "entity_x": x, "entity_y": y, "region": None}
    if ref and pad:
        hit = next((p for p in index["pads"] if p["ref"] == ref and p["pad"] == pad), None)
        if not hit:
            raise typer.BadParameter(f"no pad {ref}.{pad}")
        anchor.update(kind="pad", uuid=hit["uuid"], ref=ref, pad=pad, net=hit.get("net"), layer=hit["layers"][0],
                      x=hit["x"], y=hit["y"], entity_x=hit["x"], entity_y=hit["y"])
    elif ref:
        hit = next((f for f in index["footprints"] if f["ref"] == ref), None)
        if not hit:
            raise typer.BadParameter(f"no footprint {ref}")
        anchor.update(kind="footprint", uuid=hit["uuid"], ref=ref, x=hit["x"], y=hit["y"],
                      entity_x=hit["x"], entity_y=hit["y"])
    elif x is None or y is None:
        raise typer.BadParameter("give --ref (and optionally --pad), or --x and --y")
    thread = svc.store.review.create(name, ws.head(), anchor, body, tags=tag, severity=severity)
    typer.echo(f"Created thread #{thread['number']}")


@app.command()
def steer(name: str, message: str):
    """Send a message to the design's running agent (it sees it at its next step), e.g. from another terminal."""
    svc = _services()
    _workspace(svc, name)
    row = svc.store.steering.push(name, message, author="user")
    typer.echo(f"Queued message {row['id']} for {name}'s agent.")


@app.command()
def restore(name: str, sha: str):
    """Restore an earlier version as a new commit on the current branch."""
    svc = _services()
    ws = _workspace(svc, name)
    with ws.lock:
        v = ws.restore(sha, author="user")
    v = v if isinstance(v, dict) else v.__dict__
    typer.echo(f"Restored {sha[:8]} as {v['short']}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8100, reload: bool = False):
    """Run the API server (AG-UI endpoint + REST + live events) for the web UI."""
    import uvicorn

    uvicorn.run("kicad_harness.server:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def run(
    name: str,
    prompt: str = typer.Option(
        "Work toward the design goal: orient yourself, address open review feedback, fix what the checks report, "
        "and stop when every criterion passes.",
        help="What to ask the agent.",
    ),
    loop: bool = typer.Option(True, help="Keep the agent working until the goal is met (within the budget)."),
    model: str | None = typer.Option(None, help="Model, e.g. anthropic:claude-sonnet-5-5 (default: HARNESS_MODEL)."),
    supervised: bool = typer.Option(False, help="Ask before destructive tools (rip-up, autoroute, outline, removals)."),
    thread: str | None = typer.Option(None, help="Continue a previous CLI conversation thread id."),
    color: bool = typer.Option(sys.stdout.isatty(), help="Colored output."),
):
    """Run the agent headless against the design's goal, printing its reasoning, tool calls and versions."""
    raise typer.Exit(asyncio.run(_run(name, prompt, loop, model, supervised, thread, color)))


async def _run(name, prompt, loop, model, supervised, thread_id, color) -> int:
    from pydantic_ai import DeferredToolRequests, DeferredToolResults
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    from pydantic_ai.run import AgentRunResultEvent
    from pydantic_ai.ui.ag_ui import AGUIEventStream

    from kicad_harness.agent import goal as goals
    from kicad_harness.agent.agent import HarnessDeps, build_agent, usage_limits_for
    from kicad_harness.agent.state import DesignState

    paint = _color(color)
    svc = _services()
    ws = _workspace(svc, name)
    goal = ws.goal()
    agent = build_agent(model)
    thread_id = thread_id or f"cli-{uuid.uuid4().hex[:12]}"
    history_bytes = svc.store.history.load(thread_id)
    history = ModelMessagesTypeAdapter.validate_json(history_bytes) if history_bytes else None

    def on_commit(info: dict) -> None:
        print(paint(GREEN, f"  ● version {info.get('short')}: {info.get('subject')}"), flush=True)

    deps = HarnessDeps(
        state=DesignState(design=name, loop=loop, supervised=supervised, branch=ws.branch(), head=ws.head()),
        services=svc,
        max_minutes=(goal.get("budget") or {}).get("max_minutes"),
        on_commit=on_commit,
    )
    print(paint(BOLD, f"Design {name} on {ws.branch()} @ {ws.head()[:8]} — model {model or svc.config.model}"))
    print(paint(DIM, goals.describe(goal)))

    deferred: DeferredToolResults | None = None
    user_prompt: str | None = prompt
    status = 1
    async with agent:
        while True:
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            deps.run_id = run_id
            svc.store.runs.start(name, thread_id=thread_id, run_id=run_id, model=model or svc.config.model,
                                 prompt=user_prompt or "(resume after approval)", source="cli")
            stream = AGUIEventStream(thread_id=thread_id, run_id=run_id)
            captured: dict[str, Any] = {}
            started: dict[str, bool] = {}

            async def attempt(budget):
                # Yield events for one run at the given completion budget. Any event at all means
                # the attempt produced output, so a later failure must not be retried blindly.
                async with agent.run_stream_events(
                    user_prompt, deps=deps, message_history=history, deferred_tool_results=deferred,
                    usage_limits=usage_limits_for(goal),
                    model_settings=ModelSettings(max_tokens=budget),
                ) as events:
                    async for event in events:
                        if isinstance(event, AgentRunResultEvent):
                            captured["result"] = event.result
                        started["ok"] = True
                        yield event

            async def native():
                from kicad_harness.server import clamp_budget, next_budget

                from kicad_harness.agent import models as model_catalog
                from kicad_harness.agent.agent import current_model

                budget = clamp_budget(model_catalog.budget_for(model or current_model()))
                for _ in range(6):
                    started["ok"] = False
                    try:
                        async for event in attempt(budget):
                            yield event
                        return
                    except Exception as exc:
                        retry = next_budget(budget, exc)
                        if retry is None or started["ok"]:
                            raise
                        print(paint(YELLOW, f"\n(adapting max_tokens {budget} -> {retry})"))
                        budget = retry

            printer = _Printer(paint)
            run_status = "error"
            try:
                async for event in stream.transform_stream(native()):
                    svc.store.runs.event(run_id, event.model_dump(mode="json", by_alias=True, exclude_none=True))
                    printer.handle(event)
                run_status = "finished"
            except Exception as exc:
                print(paint(RED, f"\nRun failed: {type(exc).__name__}: {exc}"))
                svc.store.runs.finish(run_id, "error", error=str(exc))
                return 1
            finally:
                printer.flush()
            svc.store.runs.finish(run_id, run_status)
            result = captured.get("result")
            if result is None:
                return 1
            history = result.all_messages()
            svc.store.history.save(thread_id, name, ModelMessagesTypeAdapter.dump_json(history))
            output = result.output
            if isinstance(output, DeferredToolRequests):
                approvals: dict[str, bool] = {}
                for call in output.approvals:
                    args = call.args if isinstance(call.args, str) else json.dumps(call.args)
                    approvals[call.tool_call_id] = typer.confirm(f"Approve {call.tool_name}({args})?", default=False)
                deferred = DeferredToolResults(approvals=approvals)
                user_prompt = None
                continue
            break

    evaluation = goals.evaluate(goal, *_eval_inputs(svc, ws, name))
    mark = paint(GREEN, "MET") if evaluation["met"] else paint(YELLOW, "NOT MET")
    print(f"\nGoal {mark}")
    for c in evaluation["criteria"]:
        sym = paint(GREEN, "✓") if c["passed"] else paint(RED, "✗")
        print(f"  {sym} [{c['id']}] {c['detail']}")
    print(paint(DIM, f"thread {thread_id} · head {ws.head()[:8]} · continue with --thread {thread_id}"))
    status = 0 if evaluation["met"] else 3
    return status


def _eval_inputs(svc, ws, name):
    """The ``(index, drc, threads)`` triple the goal evaluator needs, for either phase."""
    from kicad_harness.agent import goal as goals

    threads = svc.store.review.list(name)
    if ws.phase() == "schematic" or not ws.board_path.is_file():
        # No board yet: judge ERC and the wiring, exactly as the agent does in this phase.
        from kicad_harness.engine import kicad_cli
        from kicad_harness.engine.schematic import Schematic

        try:
            counts = kicad_cli.erc(ws.schematic_path).get("counts", {})
        except Exception:
            counts = {"errors": 1, "warnings": 0}
        try:
            doc = Schematic.load(ws.schematic_path)
            floating = len(doc.unconnected_pins())
        except Exception:
            doc = None
            floating = 0
        from kicad_harness.engine import layout as layout_mod

        try:
            layout_report = layout_mod.analyze(doc) if doc is not None else None
        except Exception:
            layout_report = None
        return {}, {
            "erc_counts": counts,
            "unconnected_pins": floating,
            "board_laid_out": False,
            "schematic_layout": layout_report,
        }, threads

    board_hash, _ = svc.artifacts.materialize(ws.board_path.read_bytes())
    drc = goals.with_board_state(svc.artifacts.drc(board_hash), True)
    from kicad_harness.agent.audit import design_parts
    from kicad_harness.engine import kicad_cli as _kc

    if ws.schematic_path.is_file():
        try:
            drc = {**drc, "erc_counts": _kc.erc(ws.schematic_path, config=svc.config)["counts"]}
        except Exception:
            drc = {**drc, "erc_counts": None}
    parts = design_parts(svc, ws)
    layout_report = None
    if ws.schematic_path.is_file():
        from kicad_harness.engine import layout as layout_mod
        from kicad_harness.engine.schematic import Schematic

        try:
            layout_report = layout_mod.analyze(Schematic.load(ws.schematic_path))
        except Exception:
            layout_report = None
    drc = {**drc, "schematic_missing": parts["missing_from_schematic"],
           "board_missing": parts["missing_from_board"], "sync_gaps": parts["sync_gaps"],
           "requirements": parts["requirements"], "requirement_gaps": parts["requirement_gaps"],
           "schematic_layout": layout_report}
    return svc.artifacts.index(board_hash), drc, threads


class _Printer:
    """Turns AG-UI events into a readable terminal transcript."""

    def __init__(self, paint):
        self.paint = paint
        self.mode: str | None = None
        self.args: dict[str, list[str]] = {}
        self.names: dict[str, str] = {}

    def _switch(self, mode: str | None):
        if self.mode != mode and self.mode is not None:
            sys.stdout.write("\n")
        self.mode = mode

    def flush(self):
        self._switch(None)
        sys.stdout.flush()

    def handle(self, event) -> None:
        t = event.type.value if hasattr(event.type, "value") else str(event.type)
        if t in ("REASONING_MESSAGE_CONTENT", "THINKING_TEXT_MESSAGE_CONTENT"):
            if self.mode != "think":
                self._switch("think")
                sys.stdout.write(self.paint(DIM, "  ~ "))
            sys.stdout.write(self.paint(DIM, event.delta.replace("\n", "\n    ")))
        elif t == "TEXT_MESSAGE_CONTENT":
            if self.mode != "text":
                self._switch("text")
                sys.stdout.write(self.paint(BOLD, "agent: "))
            sys.stdout.write(event.delta)
        elif t == "TOOL_CALL_START":
            self._switch(None)
            self.names[event.tool_call_id] = event.tool_call_name
            self.args[event.tool_call_id] = []
        elif t == "TOOL_CALL_ARGS":
            self.args.setdefault(event.tool_call_id, []).append(event.delta)
        elif t == "TOOL_CALL_END":
            args = "".join(self.args.get(event.tool_call_id, []))
            print(self.paint(CYAN, f"  → {self.names.get(event.tool_call_id, '?')}({_short(args, 200)})"))
        elif t == "TOOL_CALL_RESULT":
            print(self.paint(DIM, f"    ← {_short(_result_text(event.content), 240)}"))
        elif t == "RUN_ERROR":
            self._switch(None)
            print(self.paint(RED, f"  ! {event.message}"))
        sys.stdout.flush()


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _result_text(content: Any) -> str:
    """Tool results are JSON; drop inline image data so the transcript stays readable."""
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return str(content)

    def strip(obj):
        if isinstance(obj, dict):
            if "data" in obj and "media_type" in obj:
                return f"<{obj['media_type']}>"
            return {k: strip(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    return json.dumps(strip(data))


if __name__ == "__main__":
    app()
