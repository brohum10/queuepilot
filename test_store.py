import concurrent.futures
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
