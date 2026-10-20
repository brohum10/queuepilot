import copy
import json
from pathlib import Path
import unittest

from diagnostics import reference_audit
from routing import plan, queue_for
from test_routing import row

ROOT = Path(__file__).resolve().parent


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT / "evidence/predictions.json").read_text())
        cls.report = json.loads((ROOT / "evidence/report.json").read_text())
        cls.threshold = cls.report["models"]["candidate"]["policy"]["threshold"]
        cls.audit = reference_audit(cls.rows, cls.threshold)

    def test_reference_denominators(self):
        self.assertEqual(self.audit["test_messages"], 3072)
        self.assertEqual(self.audit["automatic_suggestions"], 1684)
        self.assertEqual(self.audit["intent_disagreements"], 28)
        self.assertEqual(self.audit["queue_disagreements"], 7)
        self.assertEqual(self.audit["groups"]["intent_only"]["count"], 21)

    def test_every_group_membership(self):
        for name, group in self.audit["groups"].items():
            for case in group["examples"]:
                same_intent = case["candidate"]["label"] == case["label"]
                same_queue = case["suggested_queue"] == case["label_queue"]
                with self.subTest(group=name, case=case["id"]):
                    if name == "queue_disagreement":
                        self.assertTrue(case["suggestion_without_review"])
                        self.assertFalse(same_queue)
                    if name == "intent_only":
                        self.assertTrue(case["suggestion_without_review"])
                        self.assertTrue(same_queue)
                        self.assertFalse(same_intent)
                    if name == "flagged_match":
                        self.assertFalse(case["suggestion_without_review"])
                        self.assertTrue(same_intent)
                    if name == "new_mistake":
                        self.assertFalse(same_intent)
                        self.assertEqual(case["baseline"]["label"], case["label"])

    def test_example_order_and_cap(self):
        smaller = reference_audit(self.rows, self.threshold, limit=1)
        self.assertIn("First 1 examples", smaller["order"])
        self.assertTrue(
            all(len(g["examples"]) <= 1 for g in smaller["groups"].values())
        )
        for group in self.audit["groups"].values():
            cases = group["examples"]
            self.assertLessEqual(len(cases), 6)
            self.assertEqual(
                cases,
                sorted(cases, key=lambda r: (-r["candidate"]["confidence"], r["id"])),
            )

    def test_counts_are_full_groups_not_preview_counts(self):
        self.assertEqual(self.audit["groups"]["new_mistake"]["count"], 71)
        self.assertGreater(self.audit["groups"]["flagged_match"]["count"], 6)

    def test_analysis_does_not_mutate_predictions_or_plan(self):
        before = copy.deepcopy(self.rows)
        original = plan(self.rows, self.threshold, 30)
        reference_audit(self.rows, self.threshold)
        self.assertEqual(self.rows, before)
        self.assertEqual(plan(self.rows, self.threshold, 30), original)

    def test_truth_can_change_analysis_never_routing(self):
        changed = copy.deepcopy(self.rows[:5])
        before = plan(changed, self.threshold, 2)
        for case in changed:
            case["label"] = "a_different_label"
        after = plan(changed, self.threshold, 2)
        self.assertEqual(
            [(r["id"], r["status"], r["reasons"]) for r in before],
            [(r["id"], r["status"], r["reasons"]) for r in after],
        )

    def test_reject_missing_labels_or_bad_limit(self):
        with self.assertRaises(ValueError):
            reference_audit([row(truth=None)], 0.8)
        for limit in [0, 21, True, 1.5]:
            with self.assertRaises(ValueError):
                reference_audit(self.rows, self.threshold, limit)

    def test_full_queue_disagreement_count_recomputes(self):
        automatic = [
            r for r in plan(self.rows, self.threshold, 0) if r["status"] == "auto"
        ]
        self.assertEqual(
            sum(queue_for(r["label"]) != r["suggested_queue"] for r in automatic),
            self.audit["queue_disagreements"],
        )


if __name__ == "__main__":
    unittest.main()
