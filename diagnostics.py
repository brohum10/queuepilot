"""Retrospective reference analysis, never a source of routing decisions.

An intent-label mismatch is not necessarily a team-queue mismatch. The queue
mapping is an explicit project convention, not validated operational ground truth.
"""

from routing import plan, queue_for


def reference_audit(rows, threshold, limit=6):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Example limit must be 1–20.")
    if not rows or any(not r.get("label") for r in rows):
        raise ValueError("Reference analysis requires supplied labels for every row.")
    planned = plan(rows, threshold, len(rows))
    groups = {
        name: []
        for name in (
            "queue_disagreement",
            "intent_only",
            "flagged_match",
            "new_mistake",
        )
    }
    auto = []
    for row in planned:
        matches = row["candidate"]["label"] == row["label"]
        same_queue = row["suggested_queue"] == queue_for(row["label"])
        if row["status"] == "auto":
            auto.append(row)
            if not same_queue:
                groups["queue_disagreement"].append(row)
            elif not matches:
                groups["intent_only"].append(row)
        elif matches:
            groups["flagged_match"].append(row)
        if not matches and row["baseline"]["label"] == row["label"]:
            groups["new_mistake"].append(row)

    def example(row):
        return {
            key: row[key]
            for key in (
                "id",
                "text",
                "label",
                "baseline",
                "candidate",
                "reasons",
                "suggested_queue",
            )
        } | {
            "label_queue": queue_for(row["label"]),
            "suggestion_without_review": row["status"] == "auto",
        }

    return {
        "scope": "Retrospective public official-test analysis; never used to fit or change the frozen policy. Supplied labels may be ambiguous. Team mappings are project conventions, not real support outcomes.",
        "test_messages": len(planned),
        "automatic_suggestions": len(auto),
        "intent_disagreements": len(groups["queue_disagreement"])
        + len(groups["intent_only"]),
        "queue_disagreements": len(groups["queue_disagreement"]),
        "threshold": threshold,
        "order": "Highest candidate confidence first; ties by case ID. First six examples per group, not representative samples.",
        "groups": {
            name: {
                "count": len(items),
                "examples": [
                    example(row)
                    for row in sorted(
                        items, key=lambda r: (-r["candidate"]["confidence"], r["id"])
                    )[:limit]
                ],
            }
            for name, items in groups.items()
        },
    }
