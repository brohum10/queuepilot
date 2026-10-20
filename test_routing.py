import copy
import csv
import io
import math
import unittest

from routing import (
    clean_text,
    export_csv,
    parse_csv,
    plan,
    reasons_for,
    summarize,
    queue_for,
)


def row(
    ident="a",
    confidence=0.5,
    label="card_arrival",
    text="Where is my card?",
    truth="card_arrival",
):
    prediction = {
        "label": label,
        "confidence": confidence,
        "margin": 0.3,
        "known_features": 3,
        "alternatives": [
            {"label": label, "confidence": confidence},
            {"label": "card_delivery_estimate", "confidence": 0.1},
        ],
    }
    return {
        "id": ident,
        "text": text,
        "label": truth,
        "candidate": prediction,
        "baseline": copy.deepcopy(prediction),
    }


class RoutingTests(unittest.TestCase):
    def test_zero_slots_waits_not_auto(self):
        result = plan([row()], 0.8, 0)
        self.assertEqual(result[0]["status"], "waiting")

    def test_budget_never_forces_uncertain_automation(self):
        results = plan([row(str(i)) for i in range(5)], 0.8, 2)
        self.assertEqual(
            [r["status"] for r in results],
            ["review", "review", "waiting", "waiting", "waiting"],
        )

    def test_confident_row_is_auto(self):
        self.assertEqual(plan([row(confidence=0.9)], 0.8, 0)[0]["status"], "auto")

    def test_exact_threshold_is_accepted(self):
        self.assertEqual(plan([row(confidence=0.8)], 0.8, 0)[0]["status"], "auto")

    def test_small_margin_requires_review(self):
        item = row(confidence=0.9)
        item["candidate"]["margin"] = 0.119
        self.assertIn("close_alternatives", plan([item], 0.8, 1)[0]["reasons"])

    def test_sensitive_label_never_auto(self):
        self.assertEqual(
            plan([row(confidence=0.999, label="compromised_card")], 0, 0)[0]["status"],
            "waiting",
        )

    def test_sensitive_keyword_requires_review(self):
        self.assertIn(
            "sensitive_request",
            plan([row(confidence=0.99, text="I was hacked")], 0, 1)[0]["reasons"],
        )

    def test_multiple_issue_rule_is_explicit(self):
        item = row(confidence=0.99, text="I was charged twice and can't log in")
        self.assertIn("multiple_issue_rule", plan([item], 0, 1)[0]["reasons"])

    def test_unknown_features_hold(self):
        item = row(confidence=0.99)
        item["candidate"]["known_features"] = 0
        self.assertIn("no_known_features", plan([item], 0, 0)[0]["reasons"])

    def test_sensitive_priority_does_not_need_truth(self):
        items = [row("uncertain", 0.1), row("sensitive", 0.9, text="stolen card")]
        result = plan(items, 0.95, 1)
        self.assertEqual(result[1]["status"], "review")

    def test_changing_truth_cannot_change_routing(self):
        items = [row(str(i), i / 10) for i in range(10)]
        changed = copy.deepcopy(items)
        for item in changed:
            item["label"] = "wrong_label"
        self.assertEqual(
            [(r["id"], r["status"], r["reasons"]) for r in plan(items, 0.8, 3)],
            [(r["id"], r["status"], r["reasons"]) for r in plan(changed, 0.8, 3)],
        )

    def test_ties_preserve_arrival(self):
        self.assertEqual(
            [
                r["id"]
                for r in plan([row("b"), row("a")], 0.8, 1)
                if r["status"] == "review"
            ],
            ["b"],
        )

    def test_reviewed_rows_consume_slots(self):
        item = row()
        item["review"] = {"queue": "cards", "note": "checked"}
        results = plan([item, row("b")], 0.8, 1)
        self.assertEqual([r["status"] for r in results], ["reviewed", "waiting"])

    def test_invalid_thresholds(self):
        for value in [True, -0.1, 1.1, float("nan"), float("inf"), None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan([], value, 1)

    def test_invalid_capacities(self):
        for value in [True, -1, 5001, 1.5, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan([], 0, value)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            plan([row(), row()], 0.8, 1)

    def test_metrics_do_not_invent_unlabeled_accuracy(self):
        item = row(confidence=0.9, truth=None)
        stats = summarize(plan([item], 0.8, 0))
        self.assertIsNone(stats["wrong_auto"])
        self.assertIsNone(stats["auto_accuracy"])

    def test_wrong_auto_denominator(self):
        items = [
            row("a", 0.9),
            row("b", 0.9, truth="wrong"),
            row("c", 0.9, truth=None),
            row("d", 0.2),
        ]
        metrics = summarize(plan(items, 0.8, 1))
        self.assertEqual(
            (
                metrics["auto"],
                metrics["labeled_auto"],
                metrics["wrong_auto"],
                metrics["auto_accuracy"],
            ),
            (3, 2, 1, 0.5),
        )

    def test_empty_metrics(self):
        self.assertEqual(summarize([])["coverage"], 0)

    def test_queue_mapping_examples(self):
        for label, expected in [
            ("compromised_card", "security"),
            ("Refund_not_showing_up", "payments"),
            ("cash_withdrawal_charge", "cash"),
            ("card_arrival", "cards"),
            ("transfer_timing", "transfers"),
            ("pending_top_up", "topups"),
            ("edit_personal_details", "account"),
        ]:
            self.assertEqual(queue_for(label), expected)

    def test_masks_email_and_long_numbers(self):
        self.assertEqual(
            clean_text("help a@example.com with 123456789012"),
            "help [email] with [number]",
        )

    def test_rejects_empty_oversized_and_control_text(self):
        for text in ["", None, "   ", "x" * 2001, "a\x00b"]:
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                clean_text(text)

    def test_csv_accepts_bom_commas_and_optional_label(self):
        result = parse_csv(
            '\ufeffid,text,label\r\na,"My card, please",card_arrival\r\n',
            {"card_arrival"},
        )
        self.assertEqual(result[0]["text"], "My card, please")

    def test_csv_unlabeled(self):
        self.assertIsNone(parse_csv("id,text\na,hello", set())[0]["label"])

    def test_csv_invalid_inputs_are_atomic(self):
        for content in [
            "id,text\na,hello\na,bye",
            "text\nhello",
            "id,text,text\na,b,c",
            "id,text,label\na,x,bad",
            "id,text\na,x,y",
            "id,text\na",
            "id,text\n",
            "id,text\n../a,x",
        ]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_csv(content, {"good"})

    def test_csv_limits(self):
        for content in [
            "id,text\n" + "\n".join(f"{i},hi" for i in range(1001)),
            "x" * 500001,
        ]:
            with self.assertRaises(ValueError):
                parse_csv(content, set())

    def test_export_neutralizes_spreadsheet_formulas(self):
        item = row(text="=SUM(A1:A2)")
        item["suggested_queue"] = "cards"
        item["status"] = "auto"
        values = list(csv.DictReader(io.StringIO(export_csv([item]))))
        self.assertTrue(values[0]["text"].startswith("'="))

    def test_plain_route_rule_does_not_claim_semantic_detection(self):
        self.assertNotIn(
            "multiple_issue_rule",
            reasons_for(
                "two difficult questions", row(confidence=0.99)["candidate"], 0.8
            ),
        )


if __name__ == "__main__":
    unittest.main()
