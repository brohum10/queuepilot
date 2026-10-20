import concurrent.futures
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest

from store import Store, Conflict
from test_routing import row


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.db"
        self.store = Store(self.path)
        self.state = self.store.create([row("a"), row("b"), row("c")], "test", 0.8, 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_retains_inbox(self):
        self.assertEqual(Store(self.path).state()["session"], self.state["session"])

    def test_restart_retains_review(self):
        self.store.review(self.state["session"], "a", "cards", "card delivery", 0)
        fresh = Store(self.path).state()
        self.assertEqual(fresh["metrics"]["reviewed"], 1)
        self.assertEqual(fresh["rows"][0]["review"]["queue"], "cards")

    def test_review_never_changes_original_label_or_prediction(self):
        after = self.store.review(
            self.state["session"], "a", "payments", "different route", 0
        )
        self.assertEqual(after["rows"][0]["label"], "card_arrival")
        self.assertEqual(after["rows"][0]["candidate"]["label"], "card_arrival")

    def test_review_is_append_only(self):
        session = self.state["session"]
        self.store.review(session, "a", "cards", "checked", 0)
        with self.assertRaises(Conflict):
            self.store.review(session, "a", "account", "again", 1)
        self.assertEqual(len(self.store.corrections(session)), 1)

    def test_no_review_outside_capacity(self):
        with self.assertRaises(Conflict):
            self.store.review(self.state["session"], "c", "cards", "checked", 0)

    def test_stale_version_conflicts(self):
        with self.assertRaises(Conflict):
            self.store.review(self.state["session"], "a", "cards", "checked", 5)

    def test_completed_reviews_consume_capacity(self):
        session = self.state["session"]
        self.store.review(session, "a", "cards", "checked", 0)
        after = self.store.review(session, "b", "cards", "checked", 0)
        self.assertEqual(after["metrics"]["review"], 0)
        self.assertEqual(after["metrics"]["waiting"], 1)

    def test_cannot_shrink_below_completed(self):
        session = self.state["session"]
        self.store.review(session, "a", "cards", "checked", 0)
        with self.assertRaises(Conflict):
            self.store.configure(session, 0.8, 0)

    def test_invalid_create_writes_nothing(self):
        before = self.store.state()["session"]
        with self.assertRaises(ValueError):
            self.store.create([row(), row()], "bad", 0.8, 1)
        self.assertEqual(before, self.store.state()["session"])

    def test_old_sessions_preserved(self):
        old = self.state["session"]
        new = self.store.create([row("other")], "second", 0.8, 1)
        self.assertNotEqual(old, new["session"])
        self.assertEqual(len(self.store.state(old)["rows"]), 3)

    def test_invalid_reviews(self):
        for queue, note, version in [
            ("fake", "note", 0),
            ("cards", "", 0),
            ("cards", "a" * 501, 0),
            ("cards", "note", True),
        ]:
            with self.subTest(queue=queue), self.assertRaises(ValueError):
                self.store.review(self.state["session"], "a", queue, note, version)

    def test_unknown_or_missing_session(self):
        for session in [None, "", "x" * 32]:
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.store.configure(session, 0.8, 1)

    def test_concurrent_duplicate_review_one_success(self):
        def attempt(_):
            try:
                self.store.review(self.state["session"], "a", "cards", "checked", 0)
                return "saved"
            except Conflict:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result), ["conflict", "saved"])
        self.assertEqual(len(self.store.corrections(self.state["session"])), 1)

    def test_session_listing_has_counts_not_message_text(self):
        result = self.store.sessions()
        self.assertEqual(result[0]["total"], 3)
        self.assertEqual(result[0]["reviewed"], 0)
        self.assertNotIn("rows", result[0])
        self.assertNotIn("payload", result[0])
        for limit in [0, 101, True]:
            with self.assertRaises(ValueError):
                self.store.sessions(limit)

    def test_revision_increments_for_review_and_plan(self):
        session = self.state["session"]
        self.assertEqual(self.state["revision"], 0)
        after = self.store.configure(session, 0.8, 2, revision=0)
        self.assertEqual(after["revision"], 1)
        reviewed = self.store.review(session, "a", "cards", "checked", 0, revision=1)
        self.assertEqual(reviewed["revision"], 2)

    def test_stale_plan_cannot_overwrite_newer_budget(self):
        session = self.state["session"]
        self.store.configure(session, 0.9, 1, revision=0)
        with self.assertRaises(Conflict):
            self.store.configure(session, 0.8, 0, revision=0)
        self.assertEqual(self.store.state(session)["budget"], 1)

    def test_stale_review_rejected_after_plan_changed(self):
        session = self.state["session"]
        self.store.configure(session, 0.8, 0, revision=0)
        with self.assertRaises(Conflict):
            self.store.review(session, "a", "cards", "stale tab", 0, revision=0)
        self.assertEqual(self.store.corrections(session), [])

    def test_review_rechecks_current_admission_without_revision(self):
        session = self.state["session"]
        self.store.configure(session, 0.8, 0)
        with self.assertRaises(Conflict):
            self.store.review(session, "a", "cards", "not admitted", 0)

    def test_concurrent_review_and_shrink_exactly_one_wins(self):
        for _ in range(10):
            state = self.store.create([row("a")], "race", 0.8, 1)
            barrier = threading.Barrier(2)

            def attempt(action):
                barrier.wait(timeout=5)
                try:
                    if action == "review":
                        self.store.review(
                            state["session"], "a", "cards", "checked", 0, revision=0
                        )
                    else:
                        self.store.configure(state["session"], 0.8, 0, revision=0)
                    return "saved"
                except Conflict:
                    return "conflict"

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(
                    sorted(pool.map(attempt, ["review", "shrink"])),
                    ["conflict", "saved"],
                )
            final = self.store.state(state["session"])
            self.assertLessEqual(final["metrics"]["reviewed"], final["budget"])
            self.assertEqual(
                len(self.store.corrections(state["session"])),
                final["metrics"]["reviewed"],
            )

    def test_legacy_database_is_migrated_without_losing_inbox(self):
        legacy = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy) as db:
            db.executescript("""
                CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT NOT NULL, created TEXT NOT NULL, threshold REAL NOT NULL, budget INTEGER NOT NULL);
                CREATE TABLE messages(session TEXT NOT NULL,id TEXT NOT NULL,arrival INTEGER NOT NULL,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,review TEXT,PRIMARY KEY(session,id));
            """)
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                ("a" * 32, "legacy", "2026-01-01", 0.8, 1),
            )
            db.execute(
                "INSERT INTO messages(session,id,arrival,payload) VALUES(?,?,?,?)",
                ("a" * 32, "kept", 0, json.dumps(row("kept"))),
            )
        migrated = Store(legacy)
        self.assertEqual(migrated.state()["rows"][0]["id"], "kept")
        self.assertEqual(migrated.state()["revision"], 0)
        self.assertEqual(Store(legacy).state()["source"], "legacy")

    def test_invalid_revision_never_writes(self):
        for revision in [True, -1, "0", 0.5]:
            with self.assertRaises(ValueError):
                self.store.configure(self.state["session"], 0.8, 1, revision)
        self.assertEqual(self.store.state()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
