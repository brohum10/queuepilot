"""Local SQLite inbox and append-only review audit; no evaluation-set updates."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from routing import QUEUES, plan, summarize


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, source TEXT NOT NULL, created TEXT NOT NULL, threshold REAL NOT NULL, budget INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(session TEXT NOT NULL, id TEXT NOT NULL, arrival INTEGER NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, review TEXT, PRIMARY KEY(session,id));
                CREATE TABLE IF NOT EXISTS reviews(event TEXT PRIMARY KEY, session TEXT NOT NULL, message TEXT NOT NULL, created TEXT NOT NULL, queue TEXT NOT NULL, note TEXT NOT NULL, prior_version INTEGER NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            # Upgrade existing local inboxes in place; never recreate their data.
            columns = {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}
            if "revision" not in columns:
                db.execute(
                    "ALTER TABLE sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, rows, source, threshold, budget):
        plan(rows, threshold, budget)  # Validate entire request before any write.
        session = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,source,created,threshold,budget) VALUES(?,?,?,?,?)",
                (
                    session,
                    source,
                    datetime.now(timezone.utc).isoformat(),
                    threshold,
                    budget,
                ),
            )
            for i, row in enumerate(rows):
                db.execute(
                    "INSERT INTO messages(session,id,arrival,payload) VALUES(?,?,?,?)",
                    (session, row["id"], i, json.dumps(row, allow_nan=False)),
                )
        return self.state(session)

    def state(self, session=None):
        with self.connect() as db:
            # Both SELECTs must observe the same database snapshot.
            db.execute("BEGIN")
            return self._state(db, session)

    def _state(self, db, session=None):
        record = db.execute(
            (
                "SELECT * FROM sessions WHERE id=?"
                if session
                else "SELECT * FROM sessions ORDER BY created DESC,id DESC LIMIT 1"
            ),
            (session,) if session else (),
        ).fetchone()
        if record is None:
            if session:
                raise ValueError("Unknown inbox session.")
            return {"session": None, "rows": [], "metrics": summarize([])}
        messages = db.execute(
            "SELECT * FROM messages WHERE session=? ORDER BY arrival",
            (record["id"],),
        ).fetchall()
        rows = [
            {
                **json.loads(r["payload"]),
                "version": r["version"],
                "review": json.loads(r["review"]) if r["review"] else None,
            }
            for r in messages
        ]
        planned = plan(rows, record["threshold"], record["budget"])
        return {
            **dict(record),
            "session": record["id"],
            "rows": planned,
            "metrics": summarize(planned),
        }

    def sessions(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Session limit must be 1–100.")
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT s.id,s.source,s.created,s.budget,s.revision,"
                    "count(m.id) AS total,sum(m.review IS NOT NULL) AS reviewed "
                    "FROM sessions s LEFT JOIN messages m ON m.session=s.id "
                    "GROUP BY s.id ORDER BY s.created DESC,s.id DESC LIMIT ?",
                    (limit,),
                )
            ]

    @staticmethod
    def check_revision(state, revision):
        if revision is not None:
            if type(revision) is not int or revision < 0:
                raise ValueError("Inbox revision must be a nonnegative integer.")
            if revision != state["revision"]:
                raise Conflict(
                    "Inbox changed in another tab. Reopen it before saving; no change was applied."
                )

    def configure(self, session, threshold, budget, revision=None):
        if not isinstance(session, str) or len(session) != 32:
            raise ValueError("A valid inbox session is required.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db, session)
            self.check_revision(state, revision)
            plan(state["rows"], threshold, budget)
            if budget < state["metrics"]["reviewed"]:
                raise Conflict(
                    "Cannot reduce capacity below reviews already completed."
                )
            db.execute(
                "UPDATE sessions SET threshold=?,budget=?,revision=revision+1 WHERE id=?",
                (threshold, budget, session),
            )
            return self._state(db, session)

    def review(self, session, ident, queue, note, version, revision=None):
        if not isinstance(session, str) or len(session) != 32:
            raise ValueError("A valid inbox session is required.")
        if (
            not isinstance(queue, str)
            or queue not in QUEUES
            or not isinstance(note, str)
            or not 1 <= len(note.strip()) <= 500
            or type(version) is not int
            or version < 0
        ):
            raise ValueError(
                "Choose a queue, add a 1–500 character note, and supply the current version."
            )
        review = {
            "queue": queue,
            "note": note.strip(),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db, session)
            self.check_revision(state, revision)
            row = next((r for r in state["rows"] if r["id"] == ident), None)
            if row is None:
                raise ValueError("Unknown message.")
            if row["status"] != "review":
                raise Conflict(
                    "This message is not in an available review slot. Reopen the inbox."
                )
            cursor = db.execute(
                "UPDATE messages SET review=?,version=version+1 WHERE session=? AND id=? AND version=? AND review IS NULL",
                (json.dumps(review), session, ident, version),
            )
            if cursor.rowcount != 1:
                raise Conflict("Message was already reviewed or changed; reload first.")
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    session,
                    ident,
                    review["created"],
                    queue,
                    note.strip(),
                    version,
                ),
            )
            db.execute("UPDATE sessions SET revision=revision+1 WHERE id=?", (session,))
            return self._state(db, session)

    def corrections(self, session):
        if not isinstance(session, str) or len(session) != 32:
            raise ValueError("A valid inbox session is required.")
        self.state(session)
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM reviews WHERE session=? ORDER BY created,event",
                    (session,),
                )
            ]
