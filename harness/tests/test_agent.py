"""Agent tools, goal loop, approvals and the server, driven by a scripted model against real KiCad."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from kicad_harness.agent.agent import HarnessDeps, Services, build_agent
from kicad_harness.agent.state import DesignState
from kicad_harness.config import Config
from kicad_harness.store.workspace import Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "blinker.kicad_pcb"


class Script:
    """A FunctionModel that plays fixed steps. Each step is a list of (tool, args) calls or a final text."""

    def __init__(self, steps):
        self.steps = steps
        self.seen: list[list[ModelMessage]] = []

    def _step(self, messages: list[ModelMessage]):
        self.seen.append(list(messages))
        done = sum(isinstance(m, ModelResponse) for m in messages)
        return self.steps[min(done, len(self.steps) - 1)]

    def function(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = self._step(messages)
        if isinstance(step, str):
            return ModelResponse(parts=[TextPart(step)])
        return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=f"call-{len(self.seen)}-{i}")
                                    for i, (name, args) in enumerate(step)])

    async def stream(self, messages: list[ModelMessage], info: AgentInfo):
        step = self._step(messages)
        if isinstance(step, str):
            yield step
            return
        for i, (name, args) in enumerate(step):
            yield {i: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{len(self.seen)}-{i}")}

    def model(self) -> FunctionModel:
        return FunctionModel(self.function, stream_function=self.stream)


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    config = Config(home=tmp_path_factory.mktemp("home")).ensure_dirs()
    svc = Services.create(config)
    yield svc
    close = getattr(svc.engine, "close", None)
    if close:
        close()


@pytest.fixture
def design(services, request):
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)[:50]
    ws = Workspace.create(services.config, name, FIXTURE, None, services.engine)
    return name, ws


def deps_for(services, name, **state) -> HarnessDeps:
    return HarnessDeps(state=DesignState(design=name, **state), services=services, run_id="test-run")


def footprint(services, ws, ref):
    board_hash, _ = services.artifacts.materialize(ws.board_path.read_bytes())
    index = services.artifacts.index(board_hash)
    return next(f for f in index["footprints"] if f["ref"] == ref), index


async def test_edit_inspect_check_commits_versions(services, design):
    name, ws = design
    start = len(ws.log())
    script = Script([
        [("design_summary", {})],
        [("move_footprint", {"ref": "D1", "x": 20.0, "y": 15.0})],
        [("render_view", {"target": "D1", "highlight": ["D1"]})],
        [("render_view", {"mode": "3d", "rotate": [-45, 0, 45]})],
        [("check", {})],
        "done",
    ])
    agent = build_agent()
    deps = deps_for(services, name)
    with agent.override(model=script.model()):
        result = await agent.run("move D1", deps=deps)
    assert result.output == "done"

    log = ws.log()
    assert len(log) == start + 1
    head = log[0] if isinstance(log[0], dict) else log[0].__dict__
    assert head["subject"].startswith("Move D1 to (20, 15)")
    assert head["author"] == "agent"
    assert head["trailers"].get("Harness-Tool") == "move_footprint"
    fp, _ = footprint(services, ws, "D1")
    assert (round(fp["x"], 3), round(fp["y"], 3)) == (20.0, 15.0)

    assert deps.state.head == ws.head() and deps.state.board_hash
    assert deps.state.goal is not None
    assert deps.state.last_render and deps.state.last_render.url.startswith("/api/renders/")

    images = [
        item
        for m in result.all_messages() if isinstance(m, ModelRequest)
        for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "render_view" and isinstance(p.content, list)
        for item in p.content if isinstance(item, BinaryContent)
    ]
    assert len(images) == 2 and all(i.media_type == "image/png" and len(i.data) > 1000 for i in images)


async def test_engine_errors_come_back_as_data(services, design):
    name, ws = design
    script = Script([[("move_footprint", {"ref": "NOPE9", "x": 1, "y": 1})], "ok"])
    agent = build_agent()
    with agent.override(model=script.model()):
        result = await agent.run("move", deps=deps_for(services, name))
    returns = [p for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts
               if isinstance(p, ToolReturnPart) and p.tool_name == "move_footprint"]
    assert returns and returns[0].content["ok"] is False and "NOPE9" in returns[0].content["error"]
    assert len(ws.log()) == 1


async def test_goal_loop_keeps_the_agent_working(services, design):
    name, ws = design
    with ws.lock:
        ws.set_goal({"summary": "LED on the left", "criteria": [
            {"id": "led_left", "type": "within_region", "ref": "D1", "region": [0, 12, 8, 20]}]}, author="user")
    script = Script([
        "looks fine to me",  # tries to stop early -> sent back by the output validator
        [("move_footprint", {"ref": "D1", "x": 4.0, "y": 17.0})],
        "moved D1 to the left",
    ])
    agent = build_agent()
    deps = deps_for(services, name, loop=True)
    with agent.override(model=script.model()):
        result = await agent.run("put the LED on the left", deps=deps)
    assert result.output == "moved D1 to the left"
    assert deps.state.goal and deps.state.goal.met is True
    retry_prompts = [p for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts
                     if type(p).__name__ == "RetryPromptPart"]
    assert retry_prompts and "led_left" in str(retry_prompts[0].content)


async def test_supervised_tools_wait_for_approval(services, design):
    name, ws = design
    script = Script([[("delete_tracks", {"nets": ["OUTPUT"]})], "ripped up OUTPUT"])
    agent = build_agent()
    deps = deps_for(services, name, supervised=True)
    with agent.override(model=script.model()):
        first = await agent.run("rip up OUTPUT", deps=deps)
        assert isinstance(first.output, DeferredToolRequests)
        call = first.output.approvals[0]
        assert call.tool_name == "delete_tracks"
        assert len(ws.log()) == 1  # nothing happened yet
        second = await agent.run(
            message_history=first.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: True}),
            deps=deps,
        )
    assert second.output == "ripped up OUTPUT"
    _, index = footprint(services, ws, "U1")
    assert not [t for t in index["tracks"] if t["net"] == "OUTPUT"]
    head = ws.log()[0]
    head = head if isinstance(head, dict) else head.__dict__
    assert head["subject"] == "Rip up tracks on OUTPUT"


async def test_feedback_tools(services, design):
    name, ws = design
    fp, _ = footprint(services, ws, "D1")
    anchor = {"kind": "footprint", "uuid": fp["uuid"], "ref": "D1", "pad": None, "net": None, "layer": "F.Cu",
              "side": "front", "x": fp["x"], "y": fp["y"], "entity_x": fp["x"], "entity_y": fp["y"], "region": None}
    thread = services.store.review.create(name, ws.head(), anchor, "LED too close to the edge", tags=["placement"],
                                          severity="must_fix")
    script = Script([
        [("list_feedback", {})],
        [("mark_addressed", {"number": thread["number"], "note": "moved inward"})],
        "handled",
    ])
    agent = build_agent()
    with agent.override(model=script.model()):
        result = await agent.run("address feedback", deps=deps_for(services, name))
    listed = next(p for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts
                  if isinstance(p, ToolReturnPart) and p.tool_name == "list_feedback")
    assert listed.content["count"] == 1
    assert listed.content["threads"][0]["where"]["ref"] == "D1"
    assert listed.content["threads"][0]["where"]["anchor_state"] == "exact"
    updated = services.store.review.get(thread["id"])
    assert updated["status"] == "addressed" and updated["addressed_sha"] == ws.head()


async def test_steering_reaches_a_running_agent(services, design):
    name, ws = design
    services.store.steering.push(name, "Also look closely at D1 please")
    script = Script([
        [("design_summary", {})],
        [("query", {"kind": "footprint", "ref": "D1"})],
        "looked at D1",
    ])
    agent = build_agent()
    with agent.override(model=script.model()):
        result = await agent.run("look around", deps=deps_for(services, name))
    prompts = [str(p.content) for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts
               if type(p).__name__ == "UserPromptPart"]
    assert any("Also look closely at D1 please" in p for p in prompts), prompts
    assert services.store.steering.list(name)[0]["delivered_run"] == "test-run"


def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


async def test_server_agui_and_rest(services, design):
    from kicad_harness.server import create_app

    name, ws = design
    import_sha = ws.head()
    app = create_app()
    script = Script([
        [("move_footprint", {"ref": "D1", "x": 20.0, "y": 15.0})],
        [("render_view", {"target": "D1"})],
        "moved",
    ])
    agent = build_agent()
    app.state.services, app.state.metric_tasks = services, set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
        with agent.override(model=script.model()):
            # The server holds the agent in an AgentHost so its model can be swapped at runtime.
            app.state.agent_host.agent = agent
            run_input = {
                "threadId": "thread-1", "runId": "run-1", "state": {"design": name},
                "messages": [{"id": "m1", "role": "user", "content": "move D1"}],
                "tools": [], "context": [], "forwardedProps": {},
            }
            response = await client.post("/agui", json=run_input,
                                         headers={"Accept": "text/event-stream"})
        assert response.status_code == 200, response.text
        events = _sse_events(response.text)
        types = [e["type"] for e in events]
        assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
        assert types.count("TOOL_CALL_RESULT") == 2
        snapshots = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"]
        assert snapshots and snapshots[-1]["head"] == ws.head() and snapshots[-1]["last_render"]["url"]

        logged = (await client.get("/api/runs/run-1/events")).json()
        assert len(logged) == len(events)
        runs = (await client.get(f"/api/designs/{name}/runs")).json()
        assert runs[0]["run_id"] == "run-1" and runs[0]["status"] == "finished"

        designs = (await client.get("/api/designs")).json()
        assert name in [d["name"] for d in designs]
        versions = (await client.get(f"/api/designs/{name}/versions")).json()
        assert [v["subject"] for v in versions][:2] == ["Move D1 to (20, 15)", f"Import {FIXTURE.name}"]
        metrics = (await client.get(f"/api/designs/{name}/versions/{versions[0]['sha']}/metrics")).json()
        assert set(metrics["metrics"]) == {"errors", "warnings", "unconnected"}

        index = (await client.get(f"/api/designs/{name}/rev/working/index")).json()
        pad = next(p for p in index["pads"] if p["ref"] == "D1" and p["pad"] == "1")
        svg = await client.get(f"/api/designs/{name}/rev/{import_sha}/svg/front")
        assert svg.status_code == 200 and "<svg" in svg.text[:500]
        render = (await client.get(f"/api/renders/{Path(snapshots[-1]['last_render']['url']).name}"))
        assert render.status_code == 200 and render.content[:4] == b"\x89PNG"

        diff = (await client.get(f"/api/designs/{name}/diff", params={"a": import_sha, "b": "working"})).json()
        assert any(m["kind"] == "footprint" and m["ref"] == "D1" for m in diff["moved"])

        anchor = {"kind": "pad", "uuid": pad["uuid"], "ref": "D1", "pad": "1", "net": pad["net"],
                  "layer": "F.Cu", "side": "front", "x": pad["x"], "y": pad["y"], "entity_x": pad["x"],
                  "entity_y": pad["y"], "region": None}
        created = (await client.post(f"/api/designs/{name}/threads", json={
            "anchor": anchor, "body": "check this pad", "tags": ["DFM"], "severity": "warning"})).json()
        assert created["number"] == 1 and created["created_sha"] == ws.head()
        listed = (await client.get(f"/api/designs/{name}/threads", params={"rev": import_sha})).json()
        assert listed[0]["resolved_anchor"]["state"] == "moved"  # D1 sat elsewhere in the imported version
        status = (await client.post(f"/api/threads/{created['id']}/status", json={"status": "resolved"})).json()
        assert status["status"] == "resolved"

        goal_status = (await client.get(f"/api/designs/{name}/goal/status")).json()
        # "parity" is mandatory: the goal cannot be graded without it, even for an imported board.
        assert {c["id"] for c in goal_status["criteria"]} == {"drc", "routed", "review", "parity", "sync"}

        restored = (await client.post(f"/api/designs/{name}/versions/{import_sha}/restore")).json()
        assert restored["subject"].startswith("Restore")
        fp, _ = footprint(services, ws, "D1")
        assert (round(fp["x"], 3), round(fp["y"], 3)) == (19.0, 16.0)


async def test_models_api_lists_and_switches(services, monkeypatch):
    """The UI's model picker: list, persist a switch, refuse bad input, refuse mid-run."""
    import httpx

    from kicad_harness.agent import agent as agent_mod
    from kicad_harness.server import create_app

    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    previous = agent_mod.current_model()
    swapped: list[str] = []

    async def fake_swap(model: str) -> None:
        swapped.append(model)
        app.state.agent_host.model = model

    monkeypatch.setattr(app.state.agent_host, "swap", fake_swap)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = (await client.get("/api/models")).json()
        ids = [m["id"] for m in listed["models"]]
        assert listed["current"] == agent_mod.current_model()
        # The picker only offers models whose provider has credentials: no dead routes.
        assert listed["models"][0]["current"] is True
        assert listed["current"] in ids
        from kicad_harness.agent.models import _provider_configured

        assert all(_provider_configured(model_id) for model_id in ids), ids
        assert len(ids) == len(set(ids)), f"duplicate entries: {ids}"

        bad = await client.put("/api/models", json={"model": "nonsense"})
        assert bad.status_code == 400 and "provider:model-name" in bad.text

        chosen = "google:gemini-3.1-pro-preview"
        ok = await client.put("/api/models", json={"model": chosen})
        assert ok.status_code == 200 and ok.json()["current"] == chosen
        assert swapped == [chosen]
        assert services.store.get_setting("model") == chosen
        assert (await client.get("/api/models")).json()["current"] == chosen

        # A live run must not have its agent swapped underneath it.
        app.state.agent_host.active = 1
        busy = await client.put("/api/models", json={"model": "openai:anthropic/claude-sonnet-5"})
        assert busy.status_code == 409
        app.state.agent_host.active = 0

    # Leave the process as we found it: the model override and the stored setting are global.
    agent_mod.set_model(None)
    services.store.set_setting("model", "")
    assert agent_mod.current_model() == services.config.model or previous


async def test_designs_list_and_create_from_upload_or_fixture(services, design):
    """The project selector needs identity + a thumbnail; the UI can create projects."""
    import httpx

    from kicad_harness.server import create_app

    name, ws = design
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = {d["name"]: d for d in (await client.get("/api/designs")).json()}
        me = listed[name]
        assert me["head"] == ws.head() and me["latest"]["sha"] == ws.head()
        assert me["thumbnail"] == f"/api/designs/{name}/rev/{ws.head()}/svg/front"
        assert isinstance(me["updated_at"], int) and me["latest"]["subject"]

        text = FIXTURE.read_text()
        created = await client.post("/api/designs", json={"name": f"{name}-upload", "board_text": text})
        assert created.status_code == 200, created.text
        up = {d["name"]: d for d in (await client.get("/api/designs")).json()}[f"{name}-upload"]
        assert up["thumbnail"] and up["latest"]["subject"].startswith("Import")
        thumb = await client.get(up["thumbnail"])
        assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/svg")

        fixture_made = await client.post("/api/designs", json={"name": f"{name}-fixture", "from_fixture": True,
                                                              "goal": {"summary": "test goal", "criteria": [{"id": "drc", "type": "drc_errors", "max": 99}]}})
        assert fixture_made.status_code == 200, fixture_made.text

        dup = await client.post("/api/designs", json={"name": f"{name}-upload", "board_text": text})
        assert dup.status_code == 409
        junk = await client.post("/api/designs", json={"name": f"{name}-junk", "board_text": "not a board"})
        assert junk.status_code == 400


async def test_version_rows_have_thumbnails_and_summaries(services, design, monkeypatch):
    """Versions: real thumbnail URL, diff vs the parent commit, and a cached LLM note."""
    import httpx

    from kicad_harness.agent import summarize as summarizer
    from kicad_harness.server import create_app

    name, ws = design
    services.engine.move_footprint(ws.board_path, "D1", 20.0, 15.0)
    assert ws.commit("Move D1 right", author="agent", trailers={"Harness-Tool": "move_footprint"})

    calls: list[tuple[str, str]] = []

    async def fake_summary(model: str, subject: str, diff: dict) -> str:
        calls.append((model, subject))
        return "moved d1 right"

    monkeypatch.setattr(summarizer, "summarize_diff", fake_summary)

    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rows = (await client.get(f"/api/designs/{name}/versions")).json()
        head = rows[0]
        assert head["subject"] == "Move D1 right"
        assert head["thumbnail"] == f"/api/designs/{name}/rev/{head['sha']}/svg/front"
        assert head["parent"] == rows[1]["sha"]
        assert (await client.get(head["thumbnail"])).status_code == 200

        first = (await client.get(f"/api/designs/{name}/versions/{head['sha']}/summary")).json()
        assert first["summary"] == "moved d1 right" and first["cached"] is False
        assert first["counts"]["moved"] >= 1
        assert any(e.get("ref") == "D1" for e in first["diff"]["moved"])
        assert calls and calls[0][1] == "Move D1 right"

        again = (await client.get(f"/api/designs/{name}/versions/{head['sha']}/summary")).json()
        assert again["summary"] == "moved d1 right" and again["cached"] is True
        assert len(calls) == 1  # cached per version, not regenerated

        # The list picks the cached note up for the UI.
        rows = (await client.get(f"/api/designs/{name}/versions")).json()
        assert rows[0]["summary"] == "moved d1 right"


def test_diff_covers_outline_and_silkscreen():
    """Outline resizes and reference-text moves must appear in a version diff (not 'no change')."""
    from kicad_harness.server import _diff

    a = {
        "board": {"edge_bbox": [0.0, 0.0, 20.0, 18.0]},
        "footprints": [{"uuid": "f1", "ref": "D1", "ref_text": {"x": 2.0, "y": 2.0}}],
    }
    b = {
        "board": {"edge_bbox": [0.0, 0.0, 25.0, 18.0]},
        "footprints": [{"uuid": "f1", "ref": "D1", "ref_text": {"x": 4.0, "y": 2.0}}],
    }
    d = _diff(a, b)
    assert d["outline"] == {"from": [20.0, 18.0], "to": [25.0, 18.0]}
    assert d["silkscreen"] == [{"kind": "footprint", "uuid": "f1", "ref": "D1", "from": [2.0, 2.0],
                                "to": [4.0, 2.0]}]
    assert _diff(a, a)["outline"] is None and _diff(a, a)["silkscreen"] == []


async def test_delete_thread_endpoint(services, design):
    """The UI (and the e2e walkthroughs' cleanup) can delete a comment thread."""
    import httpx

    from kicad_harness.server import create_app

    name, _ws = design
    anchor = {"kind": "footprint", "uuid": "fp-u1", "ref": "U1", "layer": "F.Cu", "side": "front",
              "x": 9.1, "y": 12.4, "entity_x": 9.0, "entity_y": 12.57}
    thread = services.store.review.create(name, "s1", anchor, "delete me")
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        gone = await client.delete(f"/api/designs/{name}/threads/{thread['number']}")
        assert gone.status_code == 204
        listed = (await client.get(f"/api/designs/{name}/threads?rev=working")).json()
        assert all(t["number"] != thread["number"] for t in listed)
        missing = await client.delete(f"/api/designs/{name}/threads/{thread['number']}")
        assert missing.status_code == 404


async def test_goal_tool_derives_criteria_and_stop_endpoint(services, design, monkeypatch):
    """The agent (not the user) writes the criteria, and a run can be stopped from any client."""
    import httpx

    from kicad_harness.agent.agent import build_agent, set_model
    from kicad_harness.server import create_app

    name, ws = design
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # The goal tool lands the spec the agent derived.
        script = Script([
            [("set_goal", {"summary": "LED on the left edge",
                           "criteria": [{"id": "drc", "type": "drc_errors", "max": 0},
                                        {"id": "led", "type": "within_region", "ref": "D1", "region": [0, 0, 6, 25]}]})],
            "goal set",
        ])
        agent = build_agent()
        with agent.override(model=script.model()):
            app.state.agent_host.agent = agent
            deps = deps_for(services, name)
            result = await agent.run("set up the goal", deps=deps)
        saved = ws.goal()
        assert saved["summary"] == "LED on the left edge"
        assert [c["type"] for c in saved["criteria"]] == ["drc_errors", "within_region"]
        assert "goal set" in str(result.output)

        # A run can be stopped through the REST side channel.
        stop = await client.post(f"/api/designs/{name}/stop")
        assert stop.status_code == 200 and stop.json()["stopping"] is True
        assert app.state.stop_flags[name].is_set()


def test_bifrost_catalog_is_cached_and_filters_non_chat(monkeypatch):
    """The gateway call must happen once per TTL (it used to block every /api/models request)."""
    import json
    from types import SimpleNamespace

    from kicad_harness.agent import models

    calls: list[str] = []

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        return _Response(
            {"data": [{"id": "private/qwen3.8-27b"}, {"id": "bge-m3-embed"}, {"id": "zai/glm-5.3"}]}
        )

    monkeypatch.setattr(models, "_IDS_CACHE", None)
    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)
    cfg = SimpleNamespace(
        bifrost_base_url="https://gateway.test/v1", bifrost_api_key="k", bifrost_models=[]
    )

    first = models.bifrost_ids(cfg)
    second = models.bifrost_ids(cfg)

    assert calls == ["https://gateway.test/v1/models"]
    assert "private/qwen3.8-27b" in first and "zai/glm-5.3" in first
    assert not any("embed" in model_id for model_id in first)
    assert first == second == list(models.KNOWN_GOOD_ALIASES) + ["private/qwen3.8-27b", "zai/glm-5.3"]

    # A gateway outage must not hide the curated aliases, and must not raise.
    monkeypatch.setattr(models, "_IDS_CACHE", None)

    def down(request, timeout=0):
        raise OSError("gateway is down")

    monkeypatch.setattr(models.urllib.request, "urlopen", down)
    assert models.bifrost_ids(cfg) == list(models.KNOWN_GOOD_ALIASES)


def test_bifrost_catalog_serves_stale_while_refreshing(monkeypatch):
    """An expired cache must answer instantly and refresh in the background, not stall the picker."""
    import threading
    import time
    from types import SimpleNamespace

    from kicad_harness.agent import models

    cfg = SimpleNamespace(bifrost_base_url="https://g/v1", bifrost_api_key="k", bifrost_models=[])
    monkeypatch.setattr(models, "_IDS_CACHE", (time.monotonic() - 9999, "https://g/v1", ["stale/model"]))
    monkeypatch.setattr(models, "_REFRESHING", False)
    started, finished = threading.Event(), threading.Event()

    def fake_fetch(base, key, timeout):
        started.set()
        finished.wait(2)
        return ["fresh/model"]

    monkeypatch.setattr(models, "_fetch_ids", fake_fetch)
    began = time.monotonic()
    assert models.bifrost_ids(cfg) == ["stale/model"]
    assert time.monotonic() - began < 0.5
    assert started.wait(2)
    finished.set()
    for _ in range(200):  # the worker thread finishes shortly after
        if models.bifrost_ids(cfg) == ["fresh/model"]:
            break
        time.sleep(0.01)
    assert models.bifrost_ids(cfg) == ["fresh/model"]


async def test_version_summary_resolves_the_selected_model(services, design, monkeypatch):
    """A bifrost:<id> selection must be resolved before it reaches pydantic-ai."""
    import httpx

    from kicad_harness.agent import models as model_catalog
    from kicad_harness.agent import summarize as summarizer
    from kicad_harness.server import create_app

    name, ws = design
    app = create_app()
    app.state.services, app.state.metric_tasks = services, set()
    head = ws.log(limit=1)[0]["sha"]
    seen: dict[str, object] = {}
    sentinel = object()

    def fake_resolve(model: str):
        seen["requested"] = model
        return sentinel

    async def fake_summary(model, subject, diff):
        seen["model"] = model
        return "moved a part"

    monkeypatch.setattr(model_catalog, "resolve", fake_resolve)
    monkeypatch.setattr(summarizer, "summarize_diff", fake_summary)
    app.state.agent_host.model = "bifrost:private/qwen3.8-27b"
    services.store.versions.put(name, head, summary=None, diff=None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/api/designs/{name}/versions/{head}/summary")).json()
    assert seen["requested"] == "bifrost:private/qwen3.8-27b"
    assert seen["model"] is sentinel
    assert body["summary"] == "moved a part" and body["error"] is None
