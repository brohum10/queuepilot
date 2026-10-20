"""Local SQLite inbox and append-only review audit; no evaluation-set updates."""

from __future__ import annotations

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

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create(self, rows, source, threshold, budget):
        plan(rows, threshold, budget)  # Validate entire request before any write.
        session = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
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
            record = db.execute(
                (
                    "SELECT * FROM sessions WHERE id=?"
                    if session
                    else "SELECT * FROM sessions ORDER BY created DESC LIMIT 1"
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

    def configure(self, session, threshold, budget):
        if not isinstance(session, str) or len(session) != 32:
            raise ValueError("A valid inbox session is required.")
        state = self.state(session)
        plan(state["rows"], threshold, budget)
        if budget < state["metrics"]["reviewed"]:
            raise Conflict("Cannot reduce capacity below reviews already completed.")
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET threshold=?,budget=? WHERE id=?",
                (threshold, budget, session),
            )
        return self.state(session)

    def review(self, session, ident, queue, note, version):
        if not isinstance(session, str) or len(session) != 32:
            raise ValueError("A valid inbox session is required.")
        if (
            queue not in QUEUES
            or not isinstance(note, str)
            or not 1 <= len(note.strip()) <= 500
            or type(version) is not int
            or version < 0
        ):
            raise ValueError(
                "Choose a queue, add a 1–500 character note, and supply the current version."
            )
        state = self.state(session)
        row = next((r for r in state["rows"] if r["id"] == ident), None)
        if row is None:
            raise ValueError("Unknown message.")
        if row["status"] != "review":
            raise Conflict(
                "This message is not in an available review slot. Reopen the inbox."
            )
        review = {
            "queue": queue,
            "note": note.strip(),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE messages SET review=?,version=version+1 WHERE session=? AND id=? AND version=? AND review IS NULL",
                (json.dumps(review), session, ident, version),
            )
            if cursor.rowcount != 1:
                raise Conflict("Message was already reviewed or changed; reload first.")
            # Admission is rechecked while holding the writer lock to avoid exceeding capacity.
            config = db.execute(
                "SELECT budget FROM sessions WHERE id=?", (session,)
            ).fetchone()
            completed = db.execute(
                "SELECT count(*) FROM messages WHERE session=? AND review IS NOT NULL",
                (session,),
            ).fetchone()[0]
            if completed > config["budget"]:
                raise Conflict("The review budget changed. No correction was saved.")
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
        return self.state(session)

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
