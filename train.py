"""Reproduce the fixed BANKING77 experiment. Never fit on official test data."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import scipy
import sklearn
from sklearn.model_selection import train_test_split

import evaldelta
from routing import QUEUES, plan, queue_for, reasons_for, summarize
from text_model import TextModel

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(text):
    return " ".join(text.casefold().split())


def read_data(root=ROOT):
    protocol = json.loads((root / "protocol.json").read_text())
    for name, sha in protocol["source_sha256"].items():
        if digest(root / name) != sha:
            raise ValueError("Pinned source checksum mismatch: " + name)
    train, test, dropped, seen = [], [], [], {}
    for split, target in [("train", train), ("test", test)]:
        with (root / f"data/{split}.csv").open(newline="") as stream:
            for i, row in enumerate(csv.DictReader(stream)):
                key = canonical(row["text"])
                if key in seen:
                    dropped.append(
                        {
                            "id": f"{split}-{i}",
                            "duplicate_of": seen[key],
                            "same_label": row["category"] == seen[key]["label"],
                        }
                    )
                    continue
                item = {
                    "id": f"{split}-{i}",
                    "text": row["text"],
                    "label": row["category"],
                }
                seen[key] = {"id": item["id"], "label": item["label"]}
                target.append(item)
    return train, test, dropped


def split_training(rows, seed):
    fitting, remainder = train_test_split(
        rows, test_size=0.3, random_state=seed, stratify=[r["label"] for r in rows]
    )
    calibration, policy = train_test_split(
        remainder,
        test_size=0.5,
        random_state=seed,
        stratify=[r["label"] for r in remainder],
    )
    return fitting, calibration, policy


def choose_threshold(rows, target=0.98, minimum=40):
    eligible = sorted(
        (r for r in rows if not reasons_for(r["text"], r["candidate"], 0)),
        key=lambda r: -r["candidate"]["confidence"],
    )
    accepted, correct, choices = 0, 0, []
    for i, row in enumerate(eligible):
        accepted += 1
        correct += row["candidate"]["label"] == row["label"]
        confidence = row["candidate"]["confidence"]
        if (
            i + 1 < len(eligible)
            and eligible[i + 1]["candidate"]["confidence"] == confidence
        ):
            continue
        if accepted >= minimum and correct / accepted >= target:
            choices.append((accepted, confidence))
    threshold = max(choices)[1] if choices else 1.0
    stats = summarize(plan(rows, threshold, len(rows)))
    return {
        "threshold": threshold,
        "policy_counts": stats,
        "target_met_on_policy": bool(choices),
        "target": target,
        "minimum_accepted": minimum,
        "scope": "Empirical policy-split target, not a guarantee. Frozen before official test evaluation.",
    }


def evaluate_model(model, rows):
    return [
        {**r, model.kind: p}
        for r, p in zip(rows, model.predict([r["text"] for r in rows]), strict=True)
    ]


def benchmark(root=ROOT):
    root = Path(root)
    protocol = json.loads((root / "protocol.json").read_text())
    training, test, dropped = read_data(root)
    fitting, calibration, policy = split_training(training, protocol["seed"])
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)
    models_dir = root / "models"
    details, predictions = {}, {}
    for kind in ("baseline", "candidate"):
        started = time.perf_counter()
        model = TextModel(kind).fit(
            [r["text"] for r in fitting], [r["label"] for r in fitting]
        )
        cal = model.calibrate(
            [r["text"] for r in calibration], [r["label"] for r in calibration]
        )
        model.save(models_dir)
        loaded = TextModel.load(models_dir, kind)
        assert np.allclose(
            model.logits([r["text"] for r in calibration[:10]]),
            loaded.logits([r["text"] for r in calibration[:10]]),
            atol=1e-9,
        )
        policy_rows = [
            {**r, "candidate": p}
            for r, p in zip(
                policy, loaded.predict([r["text"] for r in policy]), strict=True
            )
        ]
        policy_result = choose_threshold(
            policy_rows,
            protocol["policy_accuracy_target"],
            protocol["minimum_policy_accepted"],
        )
        predictions[kind] = loaded.predict([r["text"] for r in test])
        details[kind] = {
            "name": (
                "Word TF-IDF + logistic regression"
                if kind == "baseline"
                else "Word + character TF-IDF + logistic regression"
            ),
            "calibration": cal,
            "policy": policy_result,
            "training_seconds": time.perf_counter() - started,
            "n_features": int(loaded.coef.shape[1]),
            "parameters": int(loaded.coef.size + loaded.intercept.size),
            "accuracy": sum(
                p["label"] == r["label"]
                for p, r in zip(predictions[kind], test, strict=True)
            )
            / len(test),
        }
        print(
            kind,
            "accuracy:",
            round(details[kind]["accuracy"], 4),
            "threshold:",
            round(policy_result["threshold"], 4),
            flush=True,
        )
    rows = [
        {**r, "baseline": a, "candidate": b}
        for r, a, b in zip(
            test, predictions["baseline"], predictions["candidate"], strict=True
        )
    ]
    paths = {}
    for kind in details:
        path = evidence / (kind + ".jsonl")
        with path.open("w") as f:
            for row in rows:
                item = {
                    "id": row["id"],
                    "case_hash": hashlib.sha256(
                        json.dumps(
                            {"text": row["text"], "label": row["label"]}, sort_keys=True
                        ).encode()
                    ).hexdigest(),
                    "suite": "banking77-deduplicated-v1",
                    "scorer": "exact-intent-v1",
                    "run": kind,
                    "tags": [queue_for(row["label"])],
                    "score": int(row[kind]["label"] == row["label"]),
                    "latency_ms": row[kind]["batch_amortized_ms"],
                }
                f.write(json.dumps(item, sort_keys=True) + "\n")
        paths[kind] = path
    kwargs = dict(
        margin=protocol["release_max_loss"],
        min_cases=protocol["release_min_cases"],
        samples=protocol["bootstrap_samples"],
        seed=protocol["seed"],
        max_p95_ms=protocol["latency_budget_batch_amortized_ms"],
    )
    gate = evaldelta.compare(paths["baseline"], paths["candidate"], **kwargs)
    control = evaldelta.compare(paths["baseline"], paths["baseline"], **kwargs)
    assert control["decision"] == "pass"
    frozen = {
        kind: summarize(
            plan(
                [{**r, "candidate": r[kind]} for r in rows],
                details[kind]["policy"]["threshold"],
                len(rows),
            )
        )
        for kind in details
    }
    changes = Counter(
        (
            "both_correct"
            if r["baseline"]["label"] == r["label"]
            and r["candidate"]["label"] == r["label"]
            else (
                "improved"
                if r["candidate"]["label"] == r["label"]
                else (
                    "new_mistake"
                    if r["baseline"]["label"] == r["label"]
                    else "both_wrong"
                )
            )
        )
        for r in rows
    )
    report = {
        "version": 2,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": digest(root / "protocol.json"),
        "source_sha256": {
            n: digest(root / n)
            for n in ("train.py", "text_model.py", "routing.py", "evaldelta.py")
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
        },
        "counts": {
            "raw_train": 10003,
            "raw_test": 3080,
            "fit": len(fitting),
            "calibration": len(calibration),
            "policy": len(policy),
            "test": len(test),
            "excluded_duplicates": len(dropped),
            "intents": len(details) and len(set(r["label"] for r in fitting)),
        },
        "models": details,
        "gate": gate,
        "identical_control": control,
        "changes": dict(changes),
        "frozen_policy_test": frozen,
        "queue_counts": dict(Counter(queue_for(r["label"]) for r in test)),
        "limitations": [
            "Crowdsourced English, single-intent BANKING77 benchmark; not real customer traffic.",
            "One predeclared seed and a previously public benchmark, not a blind generalization study.",
            "Confidence is not correctness. Sparse text features are not an LLM or semantic understanding.",
            "Approximate paired bootstrap intervals are unadjusted per slice; PASS is not production approval.",
            "Latency is batch-amortized inference on this machine, not request p95 or an SLA.",
            "Policy target is empirical. Live imports have no error rate unless labels are supplied.",
            "Capacity is a batch review-slot assumption, not measured human throughput or staffing savings.",
            "Sensitive/multiple-issue rules are conservative heuristics, not general safety or OOD detectors.",
        ],
    }
    (evidence / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    (evidence / "predictions.json").write_text(
        json.dumps(rows, separators=(",", ":"), allow_nan=False) + "\n"
    )
    splits = {
        name: [
            {
                "id": r["id"],
                "text_sha256": hashlib.sha256(
                    canonical(r["text"]).encode()
                ).hexdigest(),
            }
            for r in group
        ]
        for name, group in [
            ("fit", fitting),
            ("calibration", calibration),
            ("policy", policy),
            ("test", test),
        ]
    }
    (evidence / "splits.json").write_text(
        json.dumps({"splits": splits, "excluded": dropped}, indent=2) + "\n"
    )
    print("Gate:", gate["decision"], "changes:", dict(changes), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New empty directory; source/data are copied, original artifacts stay intact.",
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must not exist; choose a fresh directory.")
    import shutil

    args.output.mkdir(parents=True)
    for name in (
        "train.py",
        "text_model.py",
        "routing.py",
        "evaldelta.py",
        "protocol.json",
    ):
        shutil.copy2(ROOT / name, args.output / name)
    shutil.copytree(ROOT / "data", args.output / "data")
    benchmark(args.output)
