"""Git-backed design workspaces. See DESIGN.md, "workspace.py".

Each design is its own git repository at ``config.workspaces/<name>``. Git runs as a subprocess
with the user's global and system config switched off and the repository named explicitly, so
personal settings (signing, hooks, global excludes, colour) can't change what gets committed,
and a damaged workspace can never fall through to an enclosing repository.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import weakref
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, Self

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from ..config import Config


def empty_schematic(paper: str = "A4") -> str:
    """A new, empty schematic file's text (imported lazily so workspace stays engine-free)."""
    from ..engine.schematic import empty_schematic as build

    return build(paper)


BOARD_FILE = "board.kicad_pcb"
SCHEMATIC_FILE = "schematic.kicad_sch"
GOAL_FILE = "goal.json"
PROJECT_FILE = "project.json"
GITIGNORE = "*.kicad_prl\nfp-info-cache\n*-backups/\n~*.lck\n.history/\nskidl/\n*_skidl.erc\n"

# A design moves through phases: the agent draws the schematic first, then lays out the board
# the schematic was netlisted into. See DESIGN.md, "Phases".
PHASE_SCHEMATIC = "schematic"
PHASE_BOARD = "board"
PHASES = (PHASE_SCHEMATIC, PHASE_BOARD)
DEFAULT_BRANCH = "main"
WORKING = "working"  # file_at() revision meaning the working copy
LOCK_FILE = "harness.lock"  # inside .git; flock'ed by the outermost holder of Workspace.lock

AUTHORS = {
    "agent": ("harness-agent", "agent@harness.local"),
    "user": ("harness-user", "user@harness.local"),
}

DEFAULT_GOAL: dict[str, Any] = {
    "summary": "Make the board DRC-clean and fully routed, and address every must-fix review thread.",
    "criteria": [
        {"id": "drc", "type": "drc_errors", "max": 0},
        {"id": "routed", "type": "unconnected", "max": 0},
        {"id": "review", "type": "open_threads", "severities": ["must_fix"], "max": 0},
        {"id": "parity", "type": "schematic_parity", "max_missing": 0},
    ],
    "budget": {"max_requests": 120, "max_minutes": 30},
}

# The schematic phase is judged before any board exists: the netlist has to be electrically
# sane, and a board must actually have been laid out from it.
DEFAULT_SCHEMATIC_GOAL: dict[str, Any] = {
    "summary": "Draw a complete schematic for the brief with no ERC errors, then lay out the board.",
    "criteria": [
        {"id": "wired", "type": "unconnected_pins", "max": 0},
        {"id": "erc", "type": "erc_errors", "max": 0},
        {"id": "board", "type": "board_laid_out", "min": 1},
        {"id": "parity", "type": "schematic_parity", "max_missing": 0},
    ],
    "budget": {"max_requests": 120, "max_minutes": 45},
}

DEFAULT_PROJECT: dict[str, Any] = {
    "phase": PHASE_BOARD,
    "brief": None,
    "schematic_engine": None,
}

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
_TRAILER_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Forced on every git call (through GIT_CONFIG_COUNT), on top of an empty global/system config.
# The excludes and attributes files have XDG defaults that apply even without any config file.
_GIT_CONFIG = {
    "core.excludesFile": os.devnull,
    "core.attributesFile": os.devnull,
    "core.hooksPath": os.devnull,
    "core.autocrlf": "false",
    "core.quotePath": "false",
    "commit.gpgSign": "false",
    "color.ui": "false",
    "log.showSignature": "false",
    "init.defaultBranch": DEFAULT_BRANCH,
}
# One record per commit (NUL-terminated with -z); the body comes last so it may contain anything.
_LOG_FORMAT = "%H%x1f%h%x1f%P%x1f%ae%x1f%at%x1f%(trailers:only,unfold)%x1f%B"


class Normalizer(Protocol):
    """What ``Workspace.create`` needs from the engine."""

    def normalize(self, board_path: Path) -> dict[str, Any]: ...


class GitError(RuntimeError):
    """A git command failed."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(command)} failed ({returncode}): {stderr.strip()}")


# ---------------------------------------------------------------------------------------------
# Locking


class WorkspaceLock:
    """Re-entrant lock for one workspace, across threads and processes.

    Works like ``threading.RLock`` (``with ws.lock:``, ``acquire(blocking, timeout)``,
    ``release()``). A ``threading.RLock`` serializes threads of this process; the outermost
    holder also takes an ``flock`` on ``.git/harness.lock``, so a CLI run and the server
    serialize their edits too. Every ``Workspace`` on the same path shares one instance.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rlock = threading.RLock()
        self._depth = 0
        self._owner: int | None = None
        self._file: Any = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the lock; returns ``False`` if it wasn't acquired (non-blocking or timed out)."""
        if not blocking:
            timeout = 0
        deadline = None if timeout is None or timeout < 0 else time.monotonic() + timeout
        if not self._rlock.acquire(True, -1 if deadline is None else timeout):
            return False
        if self._depth == 0:
            try:
                if not self._lock_file(deadline):
                    self._rlock.release()
                    return False
            except BaseException:
                self._rlock.release()
                raise
            self._owner = threading.get_ident()
        self._depth += 1
        return True

    def release(self) -> None:
        """Release one level; the outermost release also drops the file lock."""
        if self._owner != threading.get_ident() or self._depth == 0:
            raise RuntimeError("cannot release un-acquired lock")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._unlock_file()
        self._rlock.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def _lock_file(self, deadline: float | None) -> bool:
        git_dir = self.path / ".git"
        if fcntl is None or not git_dir.is_dir():
            return True
        handle = open(git_dir / LOCK_FILE, "a+b")
        try:
            if deadline is None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            handle.close()
                            return False
                        time.sleep(0.01)
        except BaseException:
            handle.close()
            raise
        self._file = handle
        return True

    def _unlock_file(self) -> None:
        handle, self._file = self._file, None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


_locks: weakref.WeakValueDictionary[str, WorkspaceLock] = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def workspace_lock(path: Path | str) -> WorkspaceLock:
    """The process-wide lock for the workspace at ``path`` (one per real path)."""
    key = os.path.realpath(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = WorkspaceLock(Path(key))
        return lock


# ---------------------------------------------------------------------------------------------
# Git plumbing


def _git_env(path: Path | None, readonly: bool, extra: dict[str, str] | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k == "GIT_EXEC_PATH"}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT=str(len(_GIT_CONFIG)),
    )
    for i, (key, value) in enumerate(_GIT_CONFIG.items()):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    if path is not None:
        env["GIT_DIR"] = str(path / ".git")
        env["GIT_WORK_TREE"] = str(path)
    if readonly:
        # Don't refresh the index as a side effect (git status), so pollers never hold index.lock.
        env["GIT_OPTIONAL_LOCKS"] = "0"
    if extra:
        env.update(extra)
    return env


def _git(
    path: Path,
    *args: str,
    input: str | None = None,
    binary: bool = False,
    check: bool = True,
    readonly: bool = False,
    env: dict[str, str] | None = None,
    repo: bool = True,
) -> subprocess.CompletedProcess[Any]:
    command = ["git", *args]
    full_env = _git_env(path if repo else None, readonly, env)
    if binary:
        proc = subprocess.run(command, cwd=path, env=full_env, capture_output=True, timeout=120, check=False)
    else:
        proc = subprocess.run(
            command, cwd=path, env=full_env, input=input, capture_output=True, timeout=120, check=False,
            text=True, encoding="utf-8", errors="replace",
        )
    if check and proc.returncode != 0:
        stderr = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise GitError(list(args), proc.returncode, stderr)
    return proc


def _compose_message(message: str, trailers: dict[str, Any] | None) -> str:
    """The commit message with a trailer paragraph appended (``Key: value`` per line).

    A list value becomes one trailer line per item; ``None`` and empty values are skipped.
    """
    if not isinstance(message, str):
        raise ValueError("commit message must be a string")
    text = _CONTROL_RE.sub("", message.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
    if not text.strip():
        raise ValueError("commit message is empty")
    block = []
    for key, value in (trailers or {}).items():
        if not isinstance(key, str) or not _TRAILER_KEY_RE.fullmatch(key):
            raise ValueError(f"bad trailer key {key!r}")
        for item in value if isinstance(value, (list, tuple)) else [value]:
            line = "" if item is None else " ".join(str(item).split())
            if line:
                block.append(f"{key}: {line}")
    if block:
        text += "\n\n" + "\n".join(block)
    return text + "\n"


def _commit(
    path: Path, message: str, author: str, trailers: dict[str, Any] | None = None, paths: list[str] | None = None
) -> str | None:
    """Stage everything (or just ``paths``) and commit if anything changed. Returns the new SHA."""
    if author not in AUTHORS:
        raise ValueError(f"author must be one of {', '.join(AUTHORS)}; got {author!r}")
    text = _compose_message(message, trailers)
    pathspec = ["--", *paths] if paths else ["--", "."]
    _git(path, "add", "-A", *pathspec)
    staged = _git(path, "diff", "--cached", "--quiet", *pathspec, check=False)
    if staged.returncode == 0:
        return None
    if staged.returncode != 1:
        raise GitError(["diff", "--cached", "--quiet"], staged.returncode, staged.stderr)
    name, email = AUTHORS[author]
    stamp = f"@{int(time.time())} +0000"
    only = ["--", *paths] if paths else []
    _git(
        path, "-c", f"user.name={name}", "-c", f"user.email={email}",
        "commit", "-q", "--no-verify", "--cleanup=whitespace", "-F", "-", *only,
        input=text, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _parse_trailers(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        out[key] = f"{out[key]}, {value}" if key in out else value
    return out


def _parse_version(record: str, branch: str) -> dict[str, Any]:
    sha, short, parents, email, at, trailers, body = record.split("\x1f", 6)
    message = body.strip("\n")
    first_paragraph = message.split("\n\n", 1)[0]
    return {
        "sha": sha,
        "short": short,
        "message": message,
        "subject": " ".join(line.strip() for line in first_paragraph.splitlines()).strip(),
        "author": "agent" if email.strip().lower() == AUTHORS["agent"][1] else "user",
        "time": int(at),
        "parents": parents.split(),
        "branch": branch,
        "trailers": _parse_trailers(trailers),
    }


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"bad design name {name!r}: use 1-100 letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return name


def _check_rev(rev: str) -> str:
    if not isinstance(rev, str) or not rev or rev.startswith("-") or any(c.isspace() or c == "\x00" for c in rev):
        raise ValueError(f"bad revision {rev!r}")
    return rev


def _check_rel_path(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"bad workspace path {path!r}")
    return pure.as_posix()


def _dump_goal(goal: dict[str, Any]) -> str:
    return json.dumps(goal, indent=2, ensure_ascii=False) + "\n"


def _check_phase(phase: str) -> str:
    if phase not in PHASES:
        raise ValueError(f"bad phase {phase!r}: use one of {', '.join(PHASES)}")
    return phase


# ---------------------------------------------------------------------------------------------
# Workspace


class Workspace:
    """A design's git working copy at ``config.workspaces/<name>``; the default branch is ``main``.

    Tracked: ``board.kicad_pcb``, ``goal.json`` and ``.gitignore``. Every mutation holds
    ``lock`` (re-entrant, so callers can hold it around an engine edit plus ``commit``). Reads
    don't take it, so a poller never waits behind a long edit. Versions are dicts:
    ``{sha, short, message, subject, author: "agent"|"user", time, parents, branch, trailers}``.
    """

    def __init__(self, config: Config, name: str) -> None:
        self.config = config
        self.name = _check_name(name)
        self.path = Path(os.path.realpath(Path(config.workspaces) / name))
        self.lock = workspace_lock(self.path)

    def __repr__(self) -> str:
        return f"Workspace({self.name!r}, path={str(self.path)!r})"

    # -- lifecycle -------------------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        config: Config,
        name: str,
        board_src: Path | str,
        goal: dict[str, Any] | None = None,
        engine: Normalizer | None = None,
    ) -> Workspace:
        """Create workspace ``name`` from a board file and return it.

        Copies the board to ``board.kicad_pcb``, runs ``engine.normalize`` on it (skipped when
        ``engine`` is ``None``), writes ``goal.json`` (``DEFAULT_GOAL`` if ``goal`` is ``None``)
        and ``.gitignore``, and commits ``"Import <src name>"`` as the user. The workspace is
        built in a hidden temporary directory and renamed into place, so it appears complete
        or not at all. Raises ``FileExistsError`` if the name is taken, ``FileNotFoundError``
        if ``board_src`` is missing, and ``ValueError`` for a bad name or goal.
        """
        _check_name(name)
        board_src = Path(board_src)
        if not board_src.is_file():
            raise FileNotFoundError(f"no board at {board_src}")
        if goal is not None and not isinstance(goal, dict):
            raise ValueError("goal must be a JSON object")
        root = Path(config.workspaces)
        root.mkdir(parents=True, exist_ok=True)
        final = root / name
        if final.exists():
            raise FileExistsError(f"design {name!r} already exists")
        staging = root / f".creating-{name}-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        try:
            _git(staging, "init", "-q", "-b", DEFAULT_BRANCH, "--template=", str(staging), repo=False)
            (staging / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
            shutil.copyfile(board_src, staging / BOARD_FILE)
            if engine is not None:
                engine.normalize(staging / BOARD_FILE)
            (staging / GOAL_FILE).write_text(_dump_goal(DEFAULT_GOAL if goal is None else goal), encoding="utf-8")
            _commit(staging, f"Import {board_src.name}", "user")
            try:
                os.rename(staging, final)
            except OSError as exc:
                if final.exists():
                    raise FileExistsError(f"design {name!r} already exists") from exc
                raise
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return cls(config, name)

    @classmethod
    def create_schematic(
        cls,
        config: Config,
        name: str,
        brief: str,
        goal: dict[str, Any] | None = None,
    ) -> Workspace:
        """Create workspace ``name`` for a brand-new board described by ``brief``.

        Nothing is drawn yet: the workspace starts in the schematic phase with an empty
        ``schematic.kicad_sch``, a ``project.json`` carrying the brief, and the schematic-phase
        goal, so the agent's first run draws the circuit from the description. The board phase
        begins when ``begin_board_phase`` creates a board from the finished schematic.

        Raises ``FileExistsError`` if the name is taken and ``ValueError`` for a bad name,
        brief or goal.
        """
        _check_name(name)
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError("a new project needs a prompt describing the board")
        if goal is not None and not isinstance(goal, dict):
            raise ValueError("goal must be a JSON object")
        root = Path(config.workspaces)
        root.mkdir(parents=True, exist_ok=True)
        final = root / name
        if final.exists():
            raise FileExistsError(f"design {name!r} already exists")
        staging = root / f".creating-{name}-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        try:
            _git(staging, "init", "-q", "-b", DEFAULT_BRANCH, "--template=", str(staging), repo=False)
            (staging / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
            (staging / SCHEMATIC_FILE).write_text(empty_schematic(), encoding="utf-8")
            project = dict(DEFAULT_PROJECT, phase=PHASE_SCHEMATIC, brief=brief.strip())
            (staging / PROJECT_FILE).write_text(_dump_goal(project), encoding="utf-8")
            goal_obj = DEFAULT_SCHEMATIC_GOAL if goal is None else dict(goal)
            # The brief is the intent; keep it in the goal so the agent always sees it.
            if goal is None:
                goal_obj = copy.deepcopy(DEFAULT_SCHEMATIC_GOAL)
                goal_obj["summary"] = f'{brief.strip()} — {DEFAULT_SCHEMATIC_GOAL["summary"]}'
            else:
                goal_obj.setdefault("summary", brief.strip())
            (staging / GOAL_FILE).write_text(_dump_goal(goal_obj), encoding="utf-8")
            _commit(staging, f"Start {name} from a prompt", "user")
            try:
                os.rename(staging, final)
            except OSError as exc:
                if final.exists():
                    raise FileExistsError(f"design {name!r} already exists") from exc
                raise
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return cls(config, name)

    @classmethod
    def open(cls, config: Config, name: str) -> Workspace:
        """Return the existing workspace ``name``; raises ``FileNotFoundError`` if there is none."""
        if not cls.exists(config, name):
            raise FileNotFoundError(f"no design named {name!r}")
        return cls(config, name)

    @classmethod
    def exists(cls, config: Config, name: str) -> bool:
        """Whether workspace ``name`` exists (``False`` for invalid names)."""
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            return False
        return (Path(config.workspaces) / name / ".git").is_dir()

    @classmethod
    def list(cls, config: Config) -> list[str]:
        """Names of all workspaces, sorted."""
        root = Path(config.workspaces)
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if _NAME_RE.fullmatch(p.name) and (p / ".git").is_dir())

    # -- files -------------------------------------------------------------------------------------

    @property
    def board_path(self) -> Path:
        """Absolute path of the working-copy board file."""
        return self.path / BOARD_FILE

    @property
    def schematic_path(self) -> Path:
        """Absolute path of the working-copy schematic file."""
        return self.path / SCHEMATIC_FILE

    def project(self) -> dict[str, Any]:
        """The working-copy ``project.json`` (``DEFAULT_PROJECT`` if the file is missing)."""
        path = self.path / PROJECT_FILE
        if not path.is_file():
            return copy.deepcopy(DEFAULT_PROJECT)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return copy.deepcopy(DEFAULT_PROJECT)
        return data if isinstance(data, dict) else copy.deepcopy(DEFAULT_PROJECT)

    def phase(self) -> str:
        """``"schematic"`` or ``"board"``.

        A workspace created before phases existed has a board and no ``project.json``, so the
        presence of the board file decides: no board means the schematic phase.
        """
        phase = self.project().get("phase")
        if phase in PHASES:
            return str(phase)
        return PHASE_BOARD if self.board_path.is_file() else PHASE_SCHEMATIC

    def set_phase(self, phase: str, author: str = "agent") -> None:
        """Record the design's phase in ``project.json`` (committed with the next change)."""
        _check_phase(phase)
        project = self.project()
        project["phase"] = phase
        with self.lock:
            (self.path / PROJECT_FILE).write_text(_dump_goal(project), encoding="utf-8")

    def brief(self) -> str | None:
        """The prompt the design was created from, if any."""
        brief = self.project().get("brief")
        return brief if isinstance(brief, str) and brief.strip() else None

    def goal(self) -> dict[str, Any]:
        """The working-copy ``goal.json`` (``DEFAULT_GOAL`` if the file is missing)."""
        path = self.path / GOAL_FILE
        if not path.is_file():
            return copy.deepcopy(DEFAULT_GOAL)
        return json.loads(path.read_text(encoding="utf-8"))

    def set_goal(self, goal: dict[str, Any], author: str = "user") -> dict[str, Any] | None:
        """Write ``goal.json`` and commit it alone (``"Set goal"``); ``None`` if it didn't change.

        Other uncommitted changes stay uncommitted.
        """
        if not isinstance(goal, dict):
            raise ValueError("goal must be a JSON object")
        with self.lock:
            (self.path / GOAL_FILE).write_text(_dump_goal(goal), encoding="utf-8")
            return self._commit_locked("Set goal", author, None, paths=[GOAL_FILE])

    def file_at(self, sha: str, path: str = BOARD_FILE) -> bytes:
        """Contents of ``path`` at version ``sha`` (any commit-ish), or in the working copy when
        ``sha`` is ``"working"``. Raises ``FileNotFoundError`` if it isn't there."""
        rel = _check_rel_path(path)
        if sha is None or sha == WORKING:
            target = self.path / rel
            if not target.is_file():
                raise FileNotFoundError(f"{rel} is not in the working copy of {self.name}")
            return target.read_bytes()
        _check_rev(sha)
        proc = self._git("cat-file", "blob", f"{sha}:{rel}", binary=True, check=False, readonly=True)
        if proc.returncode != 0:
            raise FileNotFoundError(f"{rel} is not in version {sha} of {self.name}")
        return proc.stdout

    # -- history -----------------------------------------------------------------------------------

    def commit(
        self, message: str, *, author: str, trailers: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Stage everything and commit it; returns the new Version, or ``None`` if nothing changed.

        ``author`` is ``"agent"`` or ``"user"``. ``trailers`` (``{"Harness-Run": id,
        "Addresses": "#3"}``) are appended to the message as git trailers.
        """
        with self.lock:
            return self._commit_locked(message, author, trailers)

    def head(self) -> str | None:
        """SHA of the current commit (``None`` before the first commit)."""
        proc = self._git("rev-parse", "--verify", "-q", "HEAD", check=False, readonly=True)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def branch(self) -> str:
        """Name of the current branch (``"HEAD"`` if detached)."""
        proc = self._git("symbolic-ref", "--short", "-q", "HEAD", check=False, readonly=True)
        return proc.stdout.strip() if proc.returncode == 0 else "HEAD"

    def branches(self) -> list[dict[str, Any]]:
        """All branches as ``[{name, head, current}]``, sorted by name."""
        out = self._git(
            "for-each-ref", "--format=%(refname:short)%00%(objectname)%00%(HEAD)", "refs/heads/", readonly=True
        ).stdout
        branches = []
        for line in out.splitlines():
            name, head, mark = line.split("\x00")
            branches.append({"name": name, "head": head, "current": mark.strip() == "*"})
        return branches

    def log(self, branch: str | None = None, limit: int | None = 200) -> list[dict[str, Any]]:
        """Versions of ``branch`` (default: the current one), newest first. Raises ``ValueError``
        for an unknown branch."""
        if branch is None:
            branch, rev = self.branch(), "HEAD"
        else:
            if not self._branch_exists(branch):
                raise ValueError(f"no branch named {branch!r}")
            rev = f"refs/heads/{branch}"
        if self._rev_parse(rev) is None:
            return []
        args = ["log", "-z", f"--format={_LOG_FORMAT}"]
        if limit is not None:
            args.append(f"--max-count={int(limit)}")
        out = self._git(*args, rev, "--", readonly=True).stdout
        return [_parse_version(record, branch) for record in out.split("\x00") if record]

    def restore(
        self,
        sha: str,
        *,
        author: str = "user",
        message: str | None = None,
        trailers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make the tree of version ``sha`` the current state, as a new commit on this branch.

        History is never rewritten: the new commit's parent is the current head, and it carries
        a ``Harness-Restore: <sha>`` trailer plus any ``trailers``. Uncommitted changes are
        first committed (as the user) so nothing is lost. If the tree already matches, no commit
        is made and the head Version is returned. Raises ``ValueError`` for an unknown version.
        """
        with self.lock:
            target = self._resolve_commit(sha)
            self._snapshot_if_dirty(f"Save working changes before restoring {target[:7]}")
            head = self.head()
            if head is not None and self._rev_parse(f"{target}^{{tree}}") == self._rev_parse(f"{head}^{{tree}}"):
                return self._version("HEAD")
            self._git("read-tree", "-u", "--reset", target)
            version = self._commit_locked(
                message or f"Restore {target[:7]}", author, {**(trailers or {}), "Harness-Restore": target}
            )
            return version if version is not None else self._version("HEAD")

    def create_branch(self, name: str, from_sha: str | None = None) -> None:
        """Create branch ``name`` at ``from_sha`` (default: the current head) and switch to it.

        Uncommitted changes are first committed on the current branch. Raises ``ValueError`` for
        a bad or existing name or an unknown version.
        """
        self._check_branch_name(name)
        with self.lock:
            if self._branch_exists(name):
                raise ValueError(f"branch {name!r} already exists")
            start = self._resolve_commit(from_sha) if from_sha is not None else None
            self._snapshot_if_dirty(f"Save working changes before branching to {name}")
            if start is None:
                start = self._resolve_commit("HEAD")
            self._git("switch", "-q", "-c", name, start)

    def switch(self, name: str) -> None:
        """Switch to branch ``name``, first committing any uncommitted changes on the current
        branch. Raises ``ValueError`` for an unknown branch."""
        with self.lock:
            if not self._branch_exists(name):
                raise ValueError(f"no branch named {name!r}")
            if name == self.branch():
                return
            self._snapshot_if_dirty(f"Save working changes before switching to {name}")
            self._git("switch", "-q", "--no-guess", name)

    def dirty(self) -> bool:
        """Whether the working copy differs from the head commit (ignored files don't count)."""
        out = self._git("status", "--porcelain", "--untracked-files=all", readonly=True).stdout
        return bool(out.strip())

    # -- helpers -----------------------------------------------------------------------------------

    def _git(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return _git(self.path, *args, **kwargs)

    def _commit_locked(
        self, message: str, author: str, trailers: dict[str, Any] | None, paths: list[str] | None = None
    ) -> dict[str, Any] | None:
        with self.lock:
            sha = _commit(self.path, message, author, trailers, paths)
            return None if sha is None else self._version(sha)

    def _snapshot_if_dirty(self, message: str) -> None:
        if self.dirty():
            self._commit_locked(message, "user", None)

    def _version(self, rev: str) -> dict[str, Any]:
        out = self._git("log", "-1", "-z", f"--format={_LOG_FORMAT}", rev, "--", readonly=True).stdout
        return _parse_version(out.split("\x00")[0], self.branch())

    def _rev_parse(self, rev: str) -> str | None:
        proc = self._git("rev-parse", "--verify", "-q", "--end-of-options", rev, check=False, readonly=True)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def _resolve_commit(self, rev: str) -> str:
        _check_rev(rev)
        sha = self._rev_parse(f"{rev}^{{commit}}")
        if sha is None:
            raise ValueError(f"unknown version {rev!r} in {self.name}")
        return sha

    def _branch_exists(self, name: str) -> bool:
        if not isinstance(name, str) or not _BRANCH_RE.fullmatch(name):
            return False
        return self._git("show-ref", "--verify", "-q", f"refs/heads/{name}", check=False, readonly=True).returncode == 0

    def _check_branch_name(self, name: str) -> None:
        ok = isinstance(name, str) and bool(_BRANCH_RE.fullmatch(name)) and name not in ("HEAD", WORKING)
        if ok:
            ok = self._git("check-ref-format", "--branch", name, check=False, readonly=True).returncode == 0
        if not ok:
            raise ValueError(f"bad branch name {name!r}")
