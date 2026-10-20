import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from routing import plan, reasons_for, summarize
from text_model import TextModel, softmax
from train import ROOT, read_data, split_training, choose_threshold


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / "evidence/report.json").read_text())
        cls.rows = json.loads((ROOT / "evidence/predictions.json").read_text())
        cls.models = {
            kind: TextModel.load(ROOT / "models", kind)
            for kind in ("baseline", "candidate")
        }

    def test_all_77_intents(self):
        self.assertEqual(len(self.models["candidate"].labels), 77)

    def test_softmax_stable(self):
        self.assertTrue(np.allclose(softmax([[10000, 10000]]), [[0.5, 0.5]]))

    def test_all_exported_predictions_recompute(self):
        for kind, model in self.models.items():
            predicted = model.predict([r["text"] for r in self.rows])
            self.assertEqual(
                [p["label"] for p in predicted], [r[kind]["label"] for r in self.rows]
            )
            self.assertTrue(
                np.allclose(
                    [p["confidence"] for p in predicted],
                    [r[kind]["confidence"] for r in self.rows],
                    atol=1e-12,
                )
            )

    def test_all_reference_accuracy_recomputes(self):
        for kind in self.models:
            accuracy = sum(r[kind]["label"] == r["label"] for r in self.rows) / len(
                self.rows
            )
            self.assertEqual(accuracy, self.report["models"][kind]["accuracy"])

    def test_frozen_policy_counts_recompute(self):
        for kind in self.models:
            rows = [{**r, "candidate": r[kind]} for r in self.rows]
            self.assertEqual(
                summarize(
                    plan(
                        rows,
                        self.report["models"][kind]["policy"]["threshold"],
                        len(rows),
                    )
                ),
                self.report["frozen_policy_test"][kind],
            )

    def test_no_exact_text_leakage(self):
        data = json.loads((ROOT / "evidence/splits.json").read_text())["splits"]
        sets = {k: {r["text_sha256"] for r in v} for k, v in data.items()}
        for a in sets:
            for b in sets:
                if a != b:
                    self.assertFalse(sets[a] & sets[b])

    def test_deduplicated_counts_and_splits(self):
        fitting, test, excluded = read_data()
        parts = split_training(fitting, 17)
        self.assertEqual(
            [len(p) for p in parts],
            [self.report["counts"][x] for x in ("fit", "calibration", "policy")],
        )
        self.assertEqual(len(test), len(self.rows))
        self.assertEqual(len(excluded), self.report["counts"]["excluded_duplicates"])

    def test_pinned_training_sources(self):
        for name, sha in self.report["source_sha256"].items():
            self.assertEqual(
                hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), sha, name
            )

    def test_roundtrip_without_pickle(self):
        with tempfile.TemporaryDirectory() as directory:
            self.models["candidate"].save(directory)
            loaded = TextModel.load(directory, "candidate")
            self.assertEqual(
                loaded.predict(["Where is my card?"])[0]["label"],
                self.models["candidate"].predict(["Where is my card?"])[0]["label"],
            )

    def test_invalid_model_weights_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.models["baseline"].save(directory)
            np.savez(
                Path(directory) / "baseline.npz",
                coef=np.array([[float("nan")]]),
                intercept=np.zeros(77),
            )
            with self.assertRaises(ValueError):
                TextModel.load(directory, "baseline")

    def test_challenge_rules_on_real_model_predictions(self):
        fixtures = json.loads((ROOT / "data/challenge.json").read_text())["cases"]
        for case in fixtures:
            prediction = self.models["candidate"].predict([case["text"]])[0]
            with self.subTest(case=case["id"]):
                self.assertIn(
                    case["required_reason"], reasons_for(case["text"], prediction, 0.8)
                )

    def test_identical_control_passes(self):
        self.assertEqual(self.report["identical_control"]["decision"], "pass")

    def test_policy_threshold_fallback_not_fake_target(self):
        from test_routing import row

        result = choose_threshold([row(truth="wrong")], target=0.98, minimum=40)
        self.assertEqual(result["threshold"], 1)
        self.assertFalse(result["target_met_on_policy"])

    def test_policy_confidence_ties_grouped(self):
        from test_routing import row

        rows = [row("a", 0.9), row("b", 0.9, truth="wrong")]
        self.assertFalse(
            choose_threshold(rows, target=1, minimum=1)["target_met_on_policy"]
        )


if __name__ == "__main__":
    unittest.main()
