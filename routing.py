"""Capacity-constrained routing. Ground-truth labels never influence decisions."""

from __future__ import annotations

import csv
import io
import math
import re

QUEUES = {
    "security": "Security & access",
    "cards": "Cards & delivery",
    "payments": "Card payments",
    "transfers": "Transfers & deposits",
    "cash": "Cash & ATMs",
    "topups": "Top-ups",
    "account": "Account & app",
}
SECURITY = {
    "card_payment_not_recognised",
    "cash_withdrawal_not_recognised",
    "direct_debit_payment_not_recognised",
    "compromised_card",
    "lost_or_stolen_card",
    "lost_or_stolen_phone",
    "passcode_forgotten",
    "pin_blocked",
    "unable_to_verify_identity",
    "verify_my_identity",
    "verify_source_of_funds",
    "why_verify_identity",
}
MAX_ROWS, MAX_TEXT = 1000, 2000
MAX_PLAN_ROWS = 5000  # Full offline benchmark; HTTP inbox imports stay capped at 1000.


def queue_for(label):
    if label in SECURITY:
        return "security"
    if any(
        x in label.lower()
        for x in ("cash_withdrawal", "atm_", "amount_of_cash", "card_swallowed")
    ):
        return "cash"
    if "top_up" in label or "topping_up" in label:
        return "topups"
    if any(
        x in label for x in ("transfer", "deposit", "receiving_money", "beneficiary")
    ):
        return "transfers"
    if any(
        x in label.lower()
        for x in (
            "payment",
            "refund",
            "transaction_charged",
            "extra_charge",
            "reverted_card",
        )
    ):
        return "payments"
    if any(x in label for x in ("card", "pin", "visa", "contactless")):
        return "cards"
    return "account"


def redact(text):
    """Basic local minimization, not a comprehensive PII detector."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    return re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[number]", text)


def clean_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("Message must contain 1–2000 characters.")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
        raise ValueError("Message contains control characters.")
    return redact(" ".join(text.split()))


def reasons_for(text, prediction, threshold):
    """Transparent conservative rules; no claim of a general OOD/multi-intent model."""
    confidence, margin = prediction["confidence"], prediction["margin"]
    if not all(
        type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1
        for v in (confidence, margin, threshold)
    ):
        raise ValueError("Confidence, margin and threshold must be finite in [0, 1].")
    reasons = []
    lower = text.lower()
    if prediction["label"] in SECURITY or re.search(
        r"\b(stolen|hacked|fraud|compromised|scam)\b", lower
    ):
        reasons.append("sensitive_request")
    money = bool(
        re.search(r"\b(charged|charge|refund|payment|transfer|deposit)\b", lower)
    )
    access = bool(
        re.search(r"\b(log ?in|login|password|passcode|locked out|sign ?in)\b", lower)
    )
    if money and access:
        reasons.append("multiple_issue_rule")
    if prediction.get("known_features", 1) == 0:
        reasons.append("no_known_features")
    if confidence < threshold:
        reasons.append("below_threshold")
    if margin < 0.12:
        reasons.append("close_alternatives")
    return reasons


def plan(rows, threshold, slots):
    if type(slots) is not int or not 0 <= slots <= MAX_PLAN_ROWS:
        raise ValueError("Review slots must be an integer from 0 to 5000.")
    if (
        type(threshold) not in (float, int)
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        raise ValueError("Threshold must be finite in [0, 1].")
    if not isinstance(rows, list) or len(rows) > MAX_PLAN_ROWS:
        raise ValueError("Too many messages.")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Message IDs must be unique.")
    output = []
    for index, row in enumerate(rows):
        item = {**row, "arrival": index}
        item["reasons"] = reasons_for(item["text"], item["candidate"], threshold)
        item["suggested_queue"] = queue_for(item["candidate"]["label"])
        item["status"] = (
            "reviewed"
            if item.get("review")
            else "waiting" if item["reasons"] else "auto"
        )
        output.append(item)
    # Sensitive / multi-issue requests first, then uncertainty, then stable arrival.
    flagged = sorted(
        (r for r in output if r["status"] == "waiting"),
        key=lambda r: (
            not any(
                x in r["reasons"] for x in ("sensitive_request", "multiple_issue_rule")
            ),
            r["candidate"]["confidence"],
            r["arrival"],
        ),
    )
    reviewed = sum(r["status"] == "reviewed" for r in output)
    available = max(0, slots - reviewed)
    for row in flagged[:available]:
        row["status"] = "review"
    return output


def summarize(rows):
    counts = {
        s: sum(r["status"] == s for r in rows)
        for s in ("auto", "review", "waiting", "reviewed")
    }
    labeled = [r for r in rows if r.get("label")]
    auto_labeled = [r for r in labeled if r["status"] == "auto"]
    wrong_auto = sum(r["candidate"]["label"] != r["label"] for r in auto_labeled)
    return {
        **counts,
        "total": len(rows),
        "flagged": counts["review"] + counts["waiting"],
        "labeled": len(labeled),
        "labeled_auto": len(auto_labeled),
        "wrong_auto": wrong_auto if auto_labeled else None,
        "auto_accuracy": 1 - wrong_auto / len(auto_labeled) if auto_labeled else None,
        "coverage": counts["auto"] / len(rows) if rows else 0,
    }


def parse_csv(content, labels):
    if not isinstance(content, str) or len(content.encode()) > 500_000:
        raise ValueError("CSV must be UTF-8 text under 500 KB.")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if (
        not reader.fieldnames
        or len(set(reader.fieldnames)) != len(reader.fieldnames)
        or not {"id", "text"} <= set(reader.fieldnames)
        or set(reader.fieldnames) - {"id", "text", "label"}
    ):
        raise ValueError("CSV headers must be id,text and optional label.")
    rows, seen = [], set()
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("CSV row does not match its header.")
        ident = row["id"].strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", ident) or ident in seen:
            raise ValueError(
                "IDs must be unique, 1–80 letters, numbers, dots, dashes or underscores."
            )
        label = row.get("label", "").strip()
        if label and label not in labels:
            raise ValueError("Unknown label: " + label)
        rows.append(
            {"id": ident, "text": clean_text(row["text"]), "label": label or None}
        )
        seen.add(ident)
        if len(rows) > MAX_ROWS:
            raise ValueError("CSV is limited to 1000 messages.")
    if not rows:
        raise ValueError("CSV has no messages.")
    return rows


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "text",
            "suggested_intent",
            "suggested_queue",
            "status",
            "reviewed_queue",
            "note",
        ]
    )
    for row in rows:
        review = row.get("review") or {}
        writer.writerow(
            [
                safe_cell(v)
                for v in [
                    row["id"],
                    row["text"],
                    row["candidate"]["label"],
                    row["suggested_queue"],
                    row["status"],
                    review.get("queue"),
                    review.get("note"),
                ]
            ]
        )
    return output.getvalue()
