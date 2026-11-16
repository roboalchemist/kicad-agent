"""Agent runs and their AG-UI event logs. See DESIGN.md, "runs.py"."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from ._common import from_json, jsonable, next_updated_at, to_json

if TYPE_CHECKING:
    from .db import Store


class Runs:
    """Agent runs (UI or CLI) and every AG-UI event they streamed, so any run can be replayed.

    A run is ``running`` from :meth:`start` until :meth:`finish` sets its final status. Event
    ``seq`` numbers start at 1 and increase by one per run, across processes.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def start(
        self,
        design: str,
        *,
        thread_id: str | None = None,
        run_id: str,
        model: str | None = None,
        prompt: str | None = None,
        source: str = "ui",
    ) -> dict[str, Any]:
        """Record a run as ``running`` and return it. ``source`` is ``"ui"`` or ``"cli"``.

        Starting a ``run_id`` that already exists for the same design marks it running again
        and keeps its events (their ``seq`` continues); another design's run_id is a ``ValueError``.
        """
        if not isinstance(design, str) or not design:
            raise ValueError("design must be a non-empty string")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be a non-empty string")
        with self._store.tx() as conn:
            now = next_updated_at(conn, "runs", design)
            row = conn.execute("SELECT design FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO runs (run_id, design, thread_id, model, prompt, source, status, started_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (run_id, design, thread_id, model, prompt, source, now, now),
                )
            elif row["design"] != design:
                raise ValueError(f"run {run_id} belongs to design {row['design']!r}, not {design!r}")
            else:
                conn.execute(
                    """
                    UPDATE runs SET status = 'running', finished_at = NULL, error = NULL, updated_at = ?,
                        thread_id = COALESCE(?, thread_id), model = COALESCE(?, model),
                        prompt = COALESCE(?, prompt), source = ?
                    WHERE run_id = ?
                    """,
                    (now, thread_id, model, prompt, source, run_id),
                )
            return self._get(conn, run_id)

    def event(self, run_id: str, event: dict[str, Any]) -> int:
        """Append one event to a run's log and return its ``seq``.

        ``event`` is an AG-UI event as a dict (a pydantic event is dumped with its camelCase
        aliases). Raises ``KeyError`` if the run doesn't exist.
        """
        data = jsonable(event)
        if not isinstance(data, dict):
            raise TypeError(f"event must be a dict, not {type(event).__name__}")
        kind = data.get("type")
        payload = json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":"))
        # The events table is the authority on the next seq: a cached ``runs.last_seq`` that
        # drifted (a reopened run, a migration, two writers racing on the same run) produced
        # "UNIQUE constraint failed: run_events.run_id, run_events.seq" mid-run and killed it.
        # MAX(seq)+1 inside BEGIN IMMEDIATE is consistent, and the retry covers a writer that
        # slipped in between the read and the insert.
        for attempt in range(4):
            try:
                with self._store.tx() as conn:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(seq), 0) AS last FROM run_events WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                        raise KeyError(f"no run {run_id}")
                    seq = int(row["last"]) + 1
                    conn.execute(
                        "INSERT INTO run_events (run_id, seq, type, at, data) VALUES (?, ?, ?, ?, ?)",
                        (run_id, seq, None if kind is None else str(kind), time.time(), payload),
                    )
                    conn.execute("UPDATE runs SET last_seq = ? WHERE run_id = ?", (seq, run_id))
                return seq
            except sqlite3.IntegrityError:
                if attempt == 3:
                    raise
                time.sleep(0.01 * (attempt + 1))
        raise AssertionError("unreachable")

    def interrupt_stale(self) -> int:
        """Mark runs still ``running`` as ``interrupted`` (called at startup after a crash/restart)."""
        import time as _time

        with self._store.tx() as conn:
            rows = conn.execute("SELECT run_id, design FROM runs WHERE status = 'running'").fetchall()
            now = _time.time()
            for row in rows:
                conn.execute(
                    "UPDATE runs SET status = 'interrupted', finished_at = ?, updated_at = ? WHERE run_id = ?",
                    (now, now, row["run_id"]),
                )
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?", (row["run_id"],)
                ).fetchone()[0]
                payload = '{"type": "RUN_ERROR", "message": "interrupted by a server restart"}'
                conn.execute(
                    "INSERT INTO run_events (run_id, seq, type, at, data) VALUES (?, ?, ?, ?, ?)",
                    (row["run_id"], seq, "RUN_ERROR", now, payload),
                )
            return len(rows)

    def finish(
        self, run_id: str, status: str, usage: Any = None, error: str | BaseException | None = None
    ) -> dict[str, Any]:
        """Set a run's final status (for example ``finished``, ``error``, ``interrupted`` or
        ``cancelled``), with optional usage and error, and return it. Raises ``KeyError`` if the
        run doesn't exist."""
        if not isinstance(status, str) or not status:
            raise ValueError("status must be a non-empty string")
        if isinstance(error, BaseException):
            error = f"{type(error).__name__}: {error}"
        with self._store.tx() as conn:
            row = conn.execute("SELECT design FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"no run {run_id}")
            now = next_updated_at(conn, "runs", row["design"])
            conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?, updated_at = ?, usage = ?, error = ? WHERE run_id = ?",
                (status, now, now, to_json(usage), error, run_id),
            )
            return self._get(conn, run_id)

    def event_bytes(self, run_id: str) -> int:
        """Characters of message content a run produced (text, reasoning, tool args, results).

        Only the content fields count — event metadata would inflate the estimate. Used as a
        proxy for how much the conversation grew during the run.
        """
        row = self._store.query_one(
            """
            SELECT COALESCE(SUM(LENGTH(COALESCE(
                json_extract(data, '$.delta'),
                json_extract(data, '$.content'),
                ''
            ))), 0) AS n
            FROM run_events WHERE run_id = ?
            """,
            (run_id,),
        )
        return int(row["n"]) if row else 0

    def list(self, design: str) -> list[dict[str, Any]]:
        """Runs of ``design``, newest first."""
        rows = self._store.query(
            "SELECT * FROM runs WHERE design = ? ORDER BY started_at DESC, rowid DESC", (design,)
        )
        return [self._run(row) for row in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return run ``run_id``, or ``None``."""
        row = self._store.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return None if row is None else self._run(row)

    def events(self, run_id: str, after_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        """Events of a run with ``seq > after_seq``, in order, as ``{"seq", "at", "type", "event"}``."""
        sql = "SELECT seq, type, at, data FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq"
        params: tuple[Any, ...] = (run_id, int(after_seq))
        if limit is not None:
            sql += " LIMIT ?"
            params += (int(limit),)
        return [
            {"seq": row["seq"], "at": row["at"], "type": row["type"], "event": json.loads(row["data"])}
            for row in self._store.query(sql, params)
        ]

    def last_updated(self, design: str) -> float:
        """The newest ``updated_at`` among the runs of ``design`` (0.0 if none). It moves when a
        run starts or finishes, not on every event."""
        row = self._store.query_one("SELECT MAX(updated_at) FROM runs WHERE design = ?", (design,))
        return float(row[0]) if row and row[0] is not None else 0.0

    # -- helpers -------------------------------------------------------------------------------

    def _get(self, conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
        return self._run(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "design": row["design"],
            "thread_id": row["thread_id"],
            "model": row["model"],
            "prompt": row["prompt"],
            "source": row["source"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
            "usage": from_json(row["usage"]),
            "error": row["error"],
            "event_count": row["last_seq"],
        }
