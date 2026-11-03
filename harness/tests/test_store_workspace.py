"""Git-backed design workspaces (store/workspace.py), built from the blinker fixture without KiCad."""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from kicad_harness.config import Config
from kicad_harness.store.workspace import DEFAULT_GOAL, GITIGNORE, Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "blinker.kicad_pcb"


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path / "home")


@pytest.fixture
def board_src(tmp_path):
    src = tmp_path / "src" / "blinker.kicad_pcb"
    src.parent.mkdir()
    shutil.copy(FIXTURE, src)
    return src


@pytest.fixture
def ws(config, board_src):
    return Workspace.create(config, "blinker", board_src)


def set_thickness(ws, value):
    """Edit the working-copy board (no KiCad needed: git only sees bytes)."""
    text = ws.board_path.read_text()
    new, n = re.subn(r"\(thickness [0-9.]+\)", f"(thickness {value})", text, count=1)
    assert n == 1
    ws.board_path.write_text(new)


def git(ws, *args):
    return subprocess.run(["git", "--git-dir", str(ws.path / ".git"), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def tree(ws, sha):
    return git(ws, "rev-parse", f"{sha}^{{tree}}")


# -- create / open / list -----------------------------------------------------------------------


def test_create_imports_the_board(ws, config, board_src):
    assert ws.path == (config.workspaces / "blinker").resolve()
    assert sorted(p.name for p in ws.path.iterdir()) == [".git", ".gitignore", "board.kicad_pcb", "goal.json"]
    assert ws.board_path.read_bytes() == FIXTURE.read_bytes()
    assert (ws.path / ".gitignore").read_text() == GITIGNORE
    assert GITIGNORE.split() == [
        "*.kicad_prl", "fp-info-cache", "*-backups/", "~*.lck", ".history/", "skidl/", "*_skidl.erc",
    ]
    assert ws.goal() == DEFAULT_GOAL
    assert [c["id"] for c in ws.goal()["criteria"]] == ["drc", "routed", "review", "parity"]
    assert ws.branch() == "main" and not ws.dirty()
    [version] = ws.log()
    assert version["subject"] == version["message"] == "Import blinker.kicad_pcb"
    assert (version["author"], version["parents"], version["branch"], version["trailers"]) == ("user", [], "main", {})
    assert version["sha"] == ws.head() and version["sha"].startswith(version["short"])
    assert re.fullmatch(r"[0-9a-f]{40}", version["sha"])
    assert abs(version["time"] - time.time()) < 10
    assert git(ws, "log", "-1", "--format=%an <%ae>|%cn <%ce>") == \
        "harness-user <user@harness.local>|harness-user <user@harness.local>"
    assert git(ws, "ls-files").split() == [".gitignore", "board.kicad_pcb", "goal.json"]
    assert Workspace.list(config) == ["blinker"]
    assert Workspace.exists(config, "blinker") and not Workspace.exists(config, "other")
    assert Workspace.open(config, "blinker").head() == ws.head()


def test_create_with_goal_and_engine(config, board_src):
    goal = {"summary": "small", "criteria": [{"id": "size", "type": "board_size", "max_w": 40, "max_h": 60}]}
    seen = []

    class FakeEngine:
        def normalize(self, board_path):
            seen.append((Path(board_path).name, Path(board_path).read_bytes() == FIXTURE.read_bytes()))
            Path(board_path).write_text(Path(board_path).read_text().replace("(thickness 1.6)", "(thickness 1.2)", 1))
            return {"replaced": 3}

    ws = Workspace.create(config, "small", board_src, goal=goal, engine=FakeEngine())
    assert seen == [("board.kicad_pcb", True)]
    assert ws.goal() == goal
    assert b"(thickness 1.2)" in ws.file_at(ws.head())  # the normalized board is what gets committed
    assert not ws.dirty()


def test_create_errors_leave_nothing_behind(config, board_src, ws):
    with pytest.raises(FileExistsError):
        Workspace.create(config, "blinker", board_src)
    for bad in ("", "../escape", ".hidden", "a b", "a/b", "x" * 101):
        with pytest.raises(ValueError):
            Workspace.create(config, bad, board_src)
        assert not Workspace.exists(config, bad)
    with pytest.raises(FileNotFoundError):
        Workspace.create(config, "missing", board_src.parent / "nope.kicad_pcb")

    class BrokenEngine:
        def normalize(self, board_path):
            raise RuntimeError("worker died")

    with pytest.raises(RuntimeError):
        Workspace.create(config, "broken", board_src, engine=BrokenEngine())
    assert sorted(p.name for p in config.workspaces.iterdir()) == ["blinker"]
    with pytest.raises(FileNotFoundError):
        Workspace.open(config, "broken")
    assert Workspace.list(Config(home=config.home / "empty")) == []


def test_git_ignores_user_config_and_environment(config, board_src, tmp_path, monkeypatch):
    hostile = tmp_path / "hostile"
    (hostile / "git").mkdir(parents=True)
    (hostile / "git" / "ignore").write_text("*.json\n*.kicad_pcb\n")
    (hostile / "gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n[user]\n\tname = Someone Else\n\temail = someone@example.com\n"
        "[color]\n\tui = always\n[log]\n\tshowSignature = true\n[init]\n\tdefaultBranch = trunk\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile / "gitconfig"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Impostor")

    ws = Workspace.create(config, "isolated", board_src)
    assert git(ws, "ls-files").split() == [".gitignore", "board.kicad_pcb", "goal.json"]
    assert ws.branch() == "main"
    assert ws.log()[0]["author"] == "user"
    set_thickness(ws, 1.0)
    assert ws.commit("Edit", author="agent")["author"] == "agent"
    assert not (tmp_path / "elsewhere").exists()


# -- commit / log / file_at --------------------------------------------------------------------------


def test_commit_without_changes_returns_none(ws):
    assert ws.commit("Nothing", author="agent") is None
    assert len(ws.log()) == 1


def test_commit_fields_and_trailers(ws):
    base = ws.head()
    set_thickness(ws, 1.2)
    message = "Move C3 next to U1\n\n# 3 asked for this\n---\nkept verbatim"
    trailers = {"Harness-Run": "run-1", "Harness-Tool": "move_footprint", "Harness-Tool-Call": "call_7",
                "Addresses": ["#3", "#5"], "Harness-Skip": None}
    v = ws.commit(message, author="agent", trailers=trailers)
    assert v["sha"] == ws.head() and v["parents"] == [base]
    assert (v["subject"], v["author"], v["branch"]) == ("Move C3 next to U1", "agent", "main")
    assert v["trailers"] == {"Harness-Run": "run-1", "Harness-Tool": "move_footprint",
                             "Harness-Tool-Call": "call_7", "Addresses": "#3, #5"}
    assert v["message"] == message + "\n\nHarness-Run: run-1\nHarness-Tool: move_footprint\n" \
                                     "Harness-Tool-Call: call_7\nAddresses: #3\nAddresses: #5"
    assert git(ws, "log", "-1", "--format=%(trailers:key=Addresses,valueonly,separator=%x2C)") == "#3,#5"
    assert git(ws, "log", "-1", "--format=%an <%ae>|%cn <%ce>") == \
        "harness-agent <agent@harness.local>|harness-agent <agent@harness.local>"
    assert ws.log()[0] == v
    assert not ws.dirty()


def test_commit_validation(ws):
    set_thickness(ws, 1.2)
    with pytest.raises(ValueError):
        ws.commit("x", author="robot")
    with pytest.raises(ValueError):
        ws.commit("  \n ", author="user")
    with pytest.raises(ValueError):
        ws.commit("x", author="user", trailers={"Bad Key": "v"})
    assert ws.dirty() and len(ws.log()) == 1


def test_log_order_limit_and_file_at(ws):
    shas = [ws.head()]
    for value in (1.0, 1.2, 1.4):
        set_thickness(ws, value)
        shas.append(ws.commit(f"Thickness {value}", author="agent")["sha"])
    log = ws.log()
    assert [v["sha"] for v in log] == shas[::-1]
    assert [v["parents"] for v in log] == [[shas[2]], [shas[1]], [shas[0]], []]
    assert [v["sha"] for v in ws.log(limit=2)] == shas[:1:-1]
    assert b"(thickness 1.6)" in ws.file_at(shas[0])
    assert b"(thickness 1.2)" in ws.file_at(shas[2])
    assert ws.file_at(shas[2][:8]) == ws.file_at(shas[2])
    assert json.loads(ws.file_at(shas[0], "goal.json")) == DEFAULT_GOAL
    set_thickness(ws, 2.0)
    assert b"(thickness 2.0)" in ws.file_at("working")
    with pytest.raises(FileNotFoundError):
        ws.file_at(shas[0], "nope.txt")
    with pytest.raises(FileNotFoundError):
        ws.file_at("0" * 40)
    for bad in ("-p", "a b"):
        with pytest.raises(ValueError):
            ws.file_at(bad)
    with pytest.raises(ValueError):
        ws.file_at(shas[0], "../outside")
    with pytest.raises(ValueError):
        ws.log(branch="nope")


# -- restore -------------------------------------------------------------------------------------


def test_restore_makes_a_new_commit_with_the_old_tree(ws):
    v0 = ws.log()[0]
    set_thickness(ws, 1.2)
    v1 = ws.commit("Thinner", author="agent")
    v2 = ws.restore(v0["sha"])
    assert v2["sha"] == ws.head() and v2["sha"] not in (v0["sha"], v1["sha"])
    assert v2["parents"] == [v1["sha"]]
    assert tree(ws, v2["sha"]) == tree(ws, v0["sha"]) != tree(ws, v1["sha"])
    assert ws.board_path.read_bytes() == ws.file_at(v0["sha"])
    assert (v2["subject"], v2["author"]) == (f"Restore {v0['sha'][:7]}", "user")
    assert v2["trailers"] == {"Harness-Restore": v0["sha"]}
    assert [v["sha"] for v in ws.log()] == [v2["sha"], v1["sha"], v0["sha"]]
    assert not ws.dirty()


def test_restore_options_noop_and_errors(ws):
    v0 = ws.head()
    set_thickness(ws, 1.2)
    v1 = ws.commit("Thinner", author="agent")["sha"]
    v2 = ws.restore(v0, author="agent", message="Undo thinning", trailers={"Harness-Run": "r9"})
    assert (v2["subject"], v2["author"]) == ("Undo thinning", "agent")
    assert v2["trailers"] == {"Harness-Run": "r9", "Harness-Restore": v0}
    # Restoring a version whose tree is already current makes no commit.
    assert ws.restore(v0)["sha"] == v2["sha"]
    assert len(ws.log()) == 3
    with pytest.raises(ValueError):
        ws.restore("0" * 40)
    with pytest.raises(ValueError):
        ws.restore("--hard")
    assert ws.restore(v1[:10])["trailers"]["Harness-Restore"] == v1


def test_restore_saves_uncommitted_changes_first(ws):
    v0 = ws.head()
    set_thickness(ws, 1.2)
    ws.commit("Thinner", author="agent")
    set_thickness(ws, 0.8)  # not committed
    (ws.path / "notes.txt").write_text("scratch")
    restored = ws.restore(v0)
    saved = ws.log()[1]
    assert saved["subject"] == f"Save working changes before restoring {v0[:7]}"
    assert b"(thickness 0.8)" in ws.file_at(saved["sha"])
    assert ws.file_at(saved["sha"], "notes.txt") == b"scratch"
    assert restored["parents"] == [saved["sha"]]
    assert not (ws.path / "notes.txt").exists()
    assert not ws.dirty()


# -- branches ------------------------------------------------------------------------------------


def test_branches(ws):
    v0 = ws.head()
    set_thickness(ws, 1.2)
    v1 = ws.commit("Main edit", author="agent")["sha"]
    assert ws.create_branch("alt", v0) is None
    assert (ws.branch(), ws.head()) == ("alt", v0)
    assert ws.board_path.read_bytes() == ws.file_at(v0)
    set_thickness(ws, 2.0)
    v2 = ws.commit("Alt edit", author="user")
    assert (v2["branch"], v2["parents"]) == ("alt", [v0])
    assert [v["sha"] for v in ws.log()] == [v2["sha"], v0]
    assert all(v["branch"] == "alt" for v in ws.log())
    main_log = ws.log(branch="main")
    assert [v["sha"] for v in main_log] == [v1, v0] and main_log[0]["branch"] == "main"
    assert ws.branches() == [
        {"name": "alt", "head": v2["sha"], "current": True},
        {"name": "main", "head": v1, "current": False},
    ]
    ws.switch("main")
    assert (ws.branch(), ws.head()) == ("main", v1)
    assert b"(thickness 1.2)" in ws.board_path.read_bytes()
    ws.switch("main")  # already there: no-op
    ws.create_branch("feature/from-head")
    assert (ws.branch(), ws.head()) == ("feature/from-head", v1)


def test_branch_errors(ws):
    ws.create_branch("alt")
    for bad in ("alt", "main", "bad..name", "-x", "HEAD", "working", "trailing/", "with space", "x.lock"):
        with pytest.raises(ValueError):
            ws.create_branch(bad)
    with pytest.raises(ValueError):
        ws.create_branch("ok", "0" * 40)
    with pytest.raises(ValueError):
        ws.switch("nope")
    assert ws.branch() == "alt"
    assert [b["name"] for b in ws.branches()] == ["alt", "main"]


def test_switch_and_branch_commit_uncommitted_changes_first(ws):
    v0 = ws.head()
    ws.create_branch("alt")
    ws.switch("main")
    set_thickness(ws, 0.8)
    ws.switch("alt")
    main_head = ws.log(branch="main")[0]
    assert main_head["subject"] == "Save working changes before switching to alt"
    assert main_head["parents"] == [v0]
    assert ws.board_path.read_bytes() == ws.file_at(v0)  # the edit stayed on main
    set_thickness(ws, 0.6)
    ws.create_branch("alt2", v0)
    assert ws.log(branch="alt")[0]["subject"] == "Save working changes before branching to alt2"
    assert ws.board_path.read_bytes() == ws.file_at(v0)


# -- dirty / goal -------------------------------------------------------------------------------------


def test_dirty(ws):
    assert not ws.dirty()
    for ignored in ("board.kicad_prl", "fp-info-cache", "board-backups/board-2026.zip", "~board.kicad_pcb.lck",
                    ".history/objects/x"):
        path = ws.path / ignored
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert not ws.dirty()
    (ws.path / "extra.txt").write_text("x")
    assert ws.dirty()
    (ws.path / "extra.txt").unlink()
    assert not ws.dirty()
    set_thickness(ws, 1.2)
    assert ws.dirty()
    ws.commit("Edit", author="agent")
    assert not ws.dirty()
    assert "board.kicad_prl" not in git(ws, "ls-files")


def test_set_goal_commits_only_goal_json(ws):
    set_thickness(ws, 1.2)  # an uncommitted board edit must stay uncommitted
    goal = dict(DEFAULT_GOAL, summary="Make it smaller")
    v = ws.set_goal(goal, "user")
    assert (v["subject"], v["author"]) == ("Set goal", "user")
    assert ws.goal() == goal
    assert json.loads(ws.file_at(v["sha"], "goal.json")) == goal
    assert b"(thickness 1.6)" in ws.file_at(v["sha"])
    assert ws.dirty()
    assert ws.set_goal(goal) is None
    assert ws.set_goal(dict(goal, summary="agent tweak"), author="agent")["author"] == "agent"
    with pytest.raises(ValueError):
        ws.set_goal(["not", "a", "goal"])


# -- locking ------------------------------------------------------------------------------------------


def test_lock_is_shared_and_reentrant(ws, config):
    assert Workspace(config, "blinker").lock is ws.lock
    assert Workspace.open(config, "blinker").lock is ws.lock
    results = []

    def try_acquire(timeout):
        got = ws.lock.acquire(timeout=timeout)
        results.append(got)
        if got:
            ws.lock.release()

    with ws.lock:
        with ws.lock:  # re-entrant, and commit() takes it again inside
            set_thickness(ws, 1.2)
            assert ws.commit("Edit under lock", author="agent") is not None
        other = threading.Thread(target=try_acquire, args=(0.2,))
        other.start()
        other.join()
        assert results == [False]
        assert ws.lock.acquire(blocking=False)  # the owner can always re-enter
        ws.lock.release()
    other = threading.Thread(target=try_acquire, args=(2,))
    other.start()
    other.join()
    assert results == [False, True]
    with pytest.raises(RuntimeError):
        ws.lock.release()


def test_lock_excludes_other_processes(ws):
    probe = (
        "import fcntl, sys\n"
        "f = open(sys.argv[1], 'a+b')\n"
        "try:\n"
        "    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('acquired')\n"
        "except BlockingIOError:\n"
        "    print('blocked')\n"
    )
    lock_file = str(ws.path / ".git" / "harness.lock")

    def probe_lock():
        return subprocess.run([sys.executable, "-c", probe, lock_file], capture_output=True, text=True,
                              check=True).stdout.strip()

    with ws.lock:
        assert probe_lock() == "blocked"
        with ws.lock:
            pass
        assert probe_lock() == "blocked"  # an inner release keeps the file lock
    assert probe_lock() == "acquired"


def test_concurrent_commits_under_the_lock(ws, config):
    def worker(i):
        local = Workspace(config, "blinker")
        with local.lock:
            (local.path / f"note-{i}.txt").write_text(f"note {i}\n")
            assert local.commit(f"Note {i}", author="agent", trailers={"Harness-Tool-Call": str(i)}) is not None

    errors = []

    def wrapped(i):
        try:
            worker(i)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    log = ws.log()
    assert len(log) == 9
    for v in log[:8]:  # each commit holds exactly its own note
        i = v["trailers"]["Harness-Tool-Call"]
        changed = git(ws, "diff-tree", "--no-commit-id", "--name-only", "-r", v["sha"]).split()
        assert changed == [f"note-{i}.txt"]
    assert not ws.dirty()
