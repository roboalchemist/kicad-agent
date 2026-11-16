"""The SQLite store: schema, version cache, chat history, review threads and runs."""

import dataclasses
import threading

import pytest

from kicad_harness.config import Config
from kicad_harness.store import Store
from kicad_harness.store.db import SCHEMA_VERSION

ANCHOR = {"kind": "footprint", "uuid": "fp-u1", "ref": "U1", "layer": "F.Cu", "side": "front",
          "x": 9.1, "y": 12.4, "entity_x": 9.0, "entity_y": 12.57}
THREAD_KEYS = {
    "id", "design", "number", "kind", "source", "severity", "tags", "status", "title", "body", "author",
    "created_at", "created_sha", "anchor", "view", "addressed_sha", "resolved_sha", "replies", "status_log",
    "resolved_anchor", "updated_at",
}


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path / "home")


@pytest.fixture
def store(config):
    with Store(config) as s:
        yield s


def run_threads(target, n):
    errors = []

    def wrapped(i):
        try:
            target(i)
        except BaseException as exc:  # surface worker failures in the test
            errors.append(exc)

    workers = [threading.Thread(target=wrapped, args=(i,)) for i in range(n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert errors == []


# -- Store ----------------------------------------------------------------------------------------


def test_store_uses_wal_and_schema_is_idempotent(config):
    with Store(config) as first:
        assert first.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert first.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        first.review.create("d", "s1", ANCHOR, "kept")
    with Store(config) as second:
        assert [t["body"] for t in second.review.list("d")] == ["kept"]
        assert second.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_tx_rolls_back_on_error_and_nests(store):
    with pytest.raises(RuntimeError):
        with store.tx() as conn:
            conn.execute("INSERT INTO agent_history VALUES ('t', 'd', x'00', 0)")
            raise RuntimeError("boom")
    assert store.history.load("t") is None
    with store.tx():
        with store.tx() as conn:
            conn.execute("INSERT INTO agent_history VALUES ('t', 'd', x'01', 0)")
    assert store.history.load("t") == b"\x01"
    with pytest.raises(RuntimeError, match="write inside a read"):
        with store.tx(write=False):
            with store.tx():
                pass


def test_two_connections_see_each_others_writes(config):
    with Store(config) as a, Store(config) as b:
        a.review.create("d", None, ANCHOR, "from a")
        b.review.create("d", None, ANCHOR, "from b")
        assert [t["number"] for t in a.review.list("d")] == [1, 2]
        assert a.review.last_updated("d") == b.review.last_updated("d")


# -- VersionMeta and History ------------------------------------------------------------------------


def test_thread_delete_removes_replies_and_status_log(store):
    thread = store.review.create("d", "s1", ANCHOR, "remove me", tags=["placement"], severity="warning")
    store.review.reply(thread["id"], "a reply", author="user")
    store.review.set_status(thread["id"], "addressed", by="user")
    assert store.review.by_number("d", thread["number"])["replies"]

    assert store.review.delete("d", thread["number"]) is True
    assert store.review.by_number("d", thread["number"]) is None
    assert store.review.list("d") == []
    rows = store.conn.execute("SELECT COUNT(*) FROM thread_replies").fetchone()[0]
    logs = store.conn.execute("SELECT COUNT(*) FROM thread_status_log").fetchone()[0]
    assert rows == 0 and logs == 0
    assert store.review.delete("d", 999) is False


def test_version_meta_cache(store):
    assert store.versions.get("d", "abc") is None
    store.versions.put("d", "abc", "hash1")
    meta = store.versions.get("d", "abc")
    assert meta["board_hash"] == "hash1" and meta["metrics"] is None
    assert meta["summary"] is None and meta["diff"] is None

    store.versions.put("d", "abc", metrics={"errors": 2, "warnings": 0, "unconnected": 1})
    meta = store.versions.get("d", "abc")
    assert meta["board_hash"] == "hash1"
    assert meta["metrics"] == {"errors": 2, "warnings": 0, "unconnected": 1}

    # Diff + LLM summary are cached alongside, and later metric writes keep them.
    diff = {"added": [], "removed": [], "moved": [{"kind": "footprint", "ref": "D1"}], "net_changed": []}
    store.versions.put("d", "abc", diff=diff, summary="moved d1 right")
    store.versions.put("d", "abc", metrics={"errors": 0, "warnings": 0, "unconnected": 0})
    meta = store.versions.get("d", "abc")
    assert meta["diff"] == diff and meta["summary"] == "moved d1 right" and meta["summary_at"]
    assert meta["metrics"]["errors"] == 0
    assert store.versions.get("other", "abc") is None


def test_history_roundtrip(store):
    assert store.history.load("thread-1") is None
    blob = bytes(range(256))
    store.history.save("thread-1", "blinker", blob)
    assert store.history.load("thread-1") == blob
    store.history.save("thread-1", "blinker", b"[]")
    assert store.history.load("thread-1") == b"[]"
    store.history.save("thread-2", "blinker", '[{"kind":"request"}]')
    assert store.history.load("thread-2") == b'[{"kind":"request"}]'
    with pytest.raises(TypeError):
        store.history.save("thread-3", "blinker", [1, 2])


# -- Review ---------------------------------------------------------------------------------------


def test_create_returns_full_thread(store):
    view = {"side": "front", "center": [9.0, 12.5], "view_mm": 10}
    t = store.review.create("blinker", "abc123", ANCHOR, "C3 is too far from U1", title="Decoupling",
                            tags=["placement", "placement", " thermal ", ""], severity="must_fix", view=view)
    assert set(t) == THREAD_KEYS
    assert (t["number"], t["kind"], t["source"], t["author"], t["status"]) == (1, "comment", "user", "user", "open")
    assert (t["title"], t["severity"], t["created_sha"], t["view"]) == ("Decoupling", "must_fix", "abc123", view)
    assert t["tags"] == ["placement", "thermal"]
    assert t["anchor"]["uuid"] == "fp-u1" and t["anchor"]["region"] is None
    assert t["replies"] == [] and t["resolved_anchor"] is None
    assert t["status_log"] == [{"status": "open", "by": "user", "at": t["created_at"], "sha": "abc123", "note": None}]
    assert t["updated_at"] == t["created_at"]
    assert store.review.get(t["id"]) == t


def test_numbering_is_per_design(store):
    a1 = store.review.create("a", "s", ANCHOR, "one")
    a2 = store.review.create("a", "s", ANCHOR, "two")
    b1 = store.review.create("b", "s", ANCHOR, "three")
    assert (a1["number"], a2["number"], b1["number"]) == (1, 2, 1)
    assert store.review.by_number("a", 2)["id"] == a2["id"]
    assert store.review.by_number("b", 1)["body"] == "three"
    assert store.review.by_number("b", 2) is None
    assert store.review.get(9999) is None


def test_numbering_is_atomic_across_connections(config):
    with Store(config) as a, Store(config) as b:
        def worker(i):
            for _ in range(10):
                (a, b)[i % 2].review.create("d", None, ANCHOR, f"worker {i}")

        run_threads(worker, 4)
        assert sorted(t["number"] for t in a.review.list("d")) == list(range(1, 41))


def test_status_transitions_are_logged(store):
    t = store.review.create("d", "s0", ANCHOR, "body")
    t = store.review.set_status(t["id"], "addressed", by="agent", sha="s1", note="moved C3 closer")
    assert (t["status"], t["addressed_sha"], t["resolved_sha"]) == ("addressed", "s1", None)
    t = store.review.set_status(t["id"], "resolved", by="user", sha="s2")
    assert (t["status"], t["addressed_sha"], t["resolved_sha"]) == ("resolved", "s1", "s2")
    t = store.review.set_status(t["id"], "reopened", by="user", sha="s3", note="still too far")
    assert (t["status"], t["addressed_sha"], t["resolved_sha"]) == ("reopened", None, None)
    t = store.review.set_status(t["id"], "wontfix", by="user")
    assert t["status"] == "wontfix"
    log = [(e["status"], e["by"], e["sha"], e["note"]) for e in t["status_log"]]
    assert log == [
        ("open", "user", "s0", None),
        ("addressed", "agent", "s1", "moved C3 closer"),
        ("resolved", "user", "s2", None),
        ("reopened", "user", "s3", "still too far"),
        ("wontfix", "user", None, None),
    ]
    times = [e["at"] for e in t["status_log"]]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert t["updated_at"] == times[-1]


def test_replies(store):
    t = store.review.create("d", "s0", ANCHOR, "body")
    store.review.reply(t["id"], "Moved it.", author="agent", sha="s1")
    t = store.review.reply(t["id"], "Thanks", author="user")
    replies = [(r["author"], r["body"], r["sha"]) for r in t["replies"]]
    assert replies == [("agent", "Moved it.", "s1"), ("user", "Thanks", None)]
    assert t["replies"][0]["id"] < t["replies"][1]["id"]
    assert t["updated_at"] == t["replies"][-1]["created_at"]


def test_list_filters(store):
    first = store.review.create("d", "aaaa1111", ANCHOR, "first")
    second = store.review.create("d", "bbbb2222", ANCHOR, "second")
    third = store.review.create("d", "bbbb2222", ANCHOR, "third")
    store.review.create("other", "aaaa1111", ANCHOR, "elsewhere")
    store.review.set_status(first["id"], "addressed", by="agent", sha="cccc3333")
    store.review.set_status(second["id"], "resolved", by="user", sha="cccc3333")
    store.review.set_status(second["id"], "reopened", by="user")
    store.review.reply(third["id"], "hm", author="user", sha="dddd4444")

    assert [t["body"] for t in store.review.list("d")] == ["first", "second", "third"]
    assert [t["body"] for t in store.review.list("d", status="open")] == ["second", "third"]
    assert [t["body"] for t in store.review.list("d", status="reopened")] == ["second"]
    assert [t["body"] for t in store.review.list("d", status=["addressed", "reopened"])] == ["first", "second"]
    assert [t["body"] for t in store.review.list("d", sha="bbbb2222")] == ["second", "third"]
    assert [t["body"] for t in store.review.list("d", sha="cccc3333")] == ["first", "second"]
    assert [t["body"] for t in store.review.list("d", sha="dddd4444ffffffffffffffffffffffffffffffff")] == ["third"]
    assert [t["body"] for t in store.review.list("d", sha="aaaa")] == ["first"]
    with pytest.raises(ValueError):
        store.review.list("d", status="closed")


def test_validation(store):
    with pytest.raises(ValueError):
        store.review.create("d", None, ANCHOR, "x", severity="critical")
    with pytest.raises(ValueError):
        store.review.create("d", None, ANCHOR, "x", kind="issue")
    with pytest.raises(ValueError):
        store.review.create("d", None, ANCHOR, "x", source="human")
    with pytest.raises(ValueError):
        store.review.create("d", None, {"kind": "net", "net": "GND"}, "x")
    with pytest.raises(ValueError):
        store.review.create("", None, ANCHOR, "x")
    t = store.review.create("d", None, ANCHOR, "x")
    with pytest.raises(ValueError):
        store.review.set_status(t["id"], "closed", by="user")
    with pytest.raises(KeyError):
        store.review.set_status(9999, "resolved", by="user")
    with pytest.raises(KeyError):
        store.review.reply(9999, "x", author="user")
    assert len(store.review.get(t["id"])["status_log"]) == 1


def test_last_updated_moves_on_every_change(store):
    assert store.review.last_updated("d") == 0.0
    t = store.review.create("d", None, ANCHOR, "x")
    stamps = [store.review.last_updated("d")]
    store.review.reply(t["id"], "r", author="user")
    stamps.append(store.review.last_updated("d"))
    store.review.set_status(t["id"], "resolved", by="user")
    stamps.append(store.review.last_updated("d"))
    assert stamps[0] > 0 and stamps == sorted(stamps) and len(set(stamps)) == 3
    assert store.review.last_updated("other") == 0.0


# -- Runs ------------------------------------------------------------------------------------------


def test_run_lifecycle(store):
    run = store.runs.start("d", thread_id="th-1", run_id="run-1", model="anthropic:claude-sonnet-5-5",
                           prompt="Route it", source="cli")
    assert {k: run[k] for k in ("run_id", "design", "thread_id", "model", "prompt", "source", "status",
                                "finished_at", "usage", "error", "event_count")} == {
        "run_id": "run-1", "design": "d", "thread_id": "th-1", "model": "anthropic:claude-sonnet-5-5",
        "prompt": "Route it", "source": "cli", "status": "running", "finished_at": None, "usage": None,
        "error": None, "event_count": 0}
    events = [{"type": "RUN_STARTED", "threadId": "th-1", "runId": "run-1"},
              {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
              {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hi"},
              {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
              {"type": "RUN_FINISHED", "threadId": "th-1", "runId": "run-1"}]
    assert [store.runs.event("run-1", e) for e in events] == [1, 2, 3, 4, 5]
    logged = store.runs.events("run-1")
    assert [e["event"] for e in logged] == events
    assert [e["type"] for e in logged] == [e["type"] for e in events]
    assert [e["at"] for e in logged] == sorted(e["at"] for e in logged)
    assert [e["seq"] for e in store.runs.events("run-1", after_seq=3)] == [4, 5]
    assert [e["seq"] for e in store.runs.events("run-1", after_seq=1, limit=2)] == [2, 3]
    assert store.runs.events("run-1", after_seq=5) == []

    done = store.runs.finish("run-1", "finished", usage={"requests": 3, "input_tokens": 1200})
    assert (done["status"], done["event_count"]) == ("finished", 5)
    assert done["usage"] == {"requests": 3, "input_tokens": 1200}
    assert done["finished_at"] >= done["started_at"]
    assert store.runs.get("run-1") == done
    assert store.runs.get("nope") is None


def test_runs_are_listed_newest_first_and_seq_is_per_run(store):
    store.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")
    store.runs.start("d", run_id="r2", thread_id="t", model="m", prompt="p")
    store.runs.start("other", run_id="r3", thread_id="t", model="m", prompt="p")
    assert store.runs.event("r1", {"type": "A"}) == 1
    assert store.runs.event("r2", {"type": "A"}) == 1
    assert store.runs.event("r1", {"type": "B"}) == 2
    assert [r["run_id"] for r in store.runs.list("d")] == ["r2", "r1"]
    assert [r["run_id"] for r in store.runs.list("other")] == ["r3"]


def test_run_errors_and_restart(store):
    with pytest.raises(KeyError):
        store.runs.event("missing", {"type": "A"})
    with pytest.raises(KeyError):
        store.runs.finish("missing", "error")
    store.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")
    store.runs.event("r1", {"type": "A"})
    failed = store.runs.finish("r1", "error", error=ValueError("boom"))
    assert failed["error"] == "ValueError: boom"
    again = store.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")
    assert (again["status"], again["error"], again["finished_at"]) == ("running", None, None)
    assert store.runs.event("r1", {"type": "B"}) == 2
    with pytest.raises(ValueError):
        store.runs.start("other", run_id="r1", thread_id="t", model="m", prompt="p")
    with pytest.raises(TypeError):
        store.runs.event("r1", ["not", "a", "dict"])


def test_event_accepts_pydantic_like_events_and_dataclass_usage(store):
    class FakeEvent:
        def model_dump(self, *, mode, by_alias, exclude_none):
            assert (mode, by_alias, exclude_none) == ("json", True, True)
            return {"type": "STATE_SNAPSHOT", "snapshot": {"design": "d"}}

    @dataclasses.dataclass
    class Usage:
        requests: int = 2
        output_tokens: int = 50

    store.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")
    store.runs.event("r1", FakeEvent())
    assert store.runs.events("r1")[0]["event"] == {"type": "STATE_SNAPSHOT", "snapshot": {"design": "d"}}
    assert store.runs.finish("r1", "finished", usage=Usage())["usage"] == {"requests": 2, "output_tokens": 50}


def test_event_seq_is_atomic_across_connections(config):
    with Store(config) as a, Store(config) as b:
        a.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")

        def worker(i):
            for n in range(50):
                (a, b)[i % 2].runs.event("r1", {"type": "X", "worker": i, "n": n})

        run_threads(worker, 4)
        events = a.runs.events("r1")
        assert [e["seq"] for e in events] == list(range(1, 201))
        for i in range(4):  # each writer's events keep their order
            assert [e["event"]["n"] for e in events if e["event"]["worker"] == i] == list(range(50))
        assert a.runs.get("r1")["event_count"] == 200


def test_runs_last_updated_moves_on_start_and_finish_only(store):
    assert store.runs.last_updated("d") == 0.0
    store.runs.start("d", run_id="r1", thread_id="t", model="m", prompt="p")
    started = store.runs.last_updated("d")
    store.runs.event("r1", {"type": "A"})
    assert store.runs.last_updated("d") == started
    store.runs.finish("r1", "finished")
    assert store.runs.last_updated("d") > started


def test_run_events_survive_a_drifted_last_seq_and_concurrent_writers(store) -> None:
    """Regression: a stale seq counter aborted a live run with a UNIQUE constraint failure."""
    import threading

    store.runs.start("pi-relay", run_id="run-drift", model="m", prompt="p")
    assert store.runs.event("run-drift", {"type": "RUN_STARTED"}) == 1
    # A cached counter that fell behind the events must not matter.
    with store.tx() as conn:
        conn.execute("UPDATE runs SET last_seq = 0 WHERE run_id = 'run-drift'")
    assert store.runs.event("run-drift", {"type": "TEXT_MESSAGE_CONTENT"}) == 2

    seen: list[int] = []
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for _ in range(10):
                seen.append(store.runs.event("run-drift", {"type": "TEXT_MESSAGE_CONTENT"}))
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert sorted(seen) == list(range(3, 43))
