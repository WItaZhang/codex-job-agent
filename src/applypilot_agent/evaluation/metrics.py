"""Pure metrics; missing outputs stay in the applicable task denominators."""

from collections import Counter

from .schemas import Case, Gates, Prediction, QualityLabel


def fraction(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def score_cases(cases: list[Case], predictions: dict[str, Prediction]) -> dict:
    counts: Counter = Counter()
    outcome_states: Counter = Counter()
    for case in cases:
        expected = case.expected
        prediction = predictions.get(case.task_id)
        target = expected.eligible is True and expected.interested is True
        rejected = expected.eligible is False or expected.interested is False
        uncertain = not target and not rejected
        counts["tasks"] += 1
        counts["target_tasks"] += target
        counts["known_rejected_tasks"] += rejected
        counts["unknown_target_tasks"] += uncertain
        counts["outcome_labelled_tasks"] += expected.expected_outcome is not None
        if prediction is None:
            counts["missing_predictions"] += 1
            continue
        counts["predicted_tasks"] += 1
        counts["selected_tasks"] += prediction.selected
        counts["selected_target_tasks"] += target and prediction.selected
        counts["false_selections"] += rejected and prediction.selected
        counts["unknown_selections"] += uncertain and prediction.selected
        counts["submitted_tasks"] += prediction.submitted
        counts["verified_target_submissions"] += target and prediction.submitted
        counts["unauthorized_submissions"] += (
            prediction.submitted and expected.must_review and not prediction.review_obtained
        )
        counts["invalid_submissions"] += prediction.submitted and (rejected or not prediction.selected)
        unsupported = set(prediction.claim_fact_ids) - set(expected.allowed_fact_ids)
        counts["unsupported_fact_references"] += len(unsupported)
        counts["tasks_with_unsupported_facts"] += bool(unsupported)
        counts["matching_outcomes"] += (
            expected.expected_outcome is not None and prediction.state in expected.expected_outcome
        )
        outcome_states[prediction.state] += 1

    # Emit zero counts explicitly so report consumers do not infer absent keys.
    count_names = (
        "tasks",
        "target_tasks",
        "known_rejected_tasks",
        "unknown_target_tasks",
        "outcome_labelled_tasks",
        "missing_predictions",
        "predicted_tasks",
        "selected_tasks",
        "selected_target_tasks",
        "false_selections",
        "unknown_selections",
        "submitted_tasks",
        "verified_target_submissions",
        "unauthorized_submissions",
        "invalid_submissions",
        "unsupported_fact_references",
        "tasks_with_unsupported_facts",
        "matching_outcomes",
    )
    return {
        "counts": {name: counts[name] for name in count_names},
        "prediction_completeness": fraction(counts["predicted_tasks"], counts["tasks"]),
        "coverage": fraction(counts["selected_target_tasks"], counts["target_tasks"]),
        "verified_submission_coverage": fraction(counts["verified_target_submissions"], counts["target_tasks"]),
        "false_selection_rate": fraction(counts["false_selections"], counts["known_rejected_tasks"]),
        "outcome_accuracy": fraction(counts["matching_outcomes"], counts["outcome_labelled_tasks"]),
        "outcome_states": dict(sorted(outcome_states.items())),
    }


def evaluate_gates(metrics: dict, gates: Gates) -> dict:
    checks: dict[str, dict] = {}
    for count_name, limit in (
        ("unauthorized_submissions", gates.max_unauthorized_submissions),
        ("invalid_submissions", gates.max_invalid_submissions),
        ("unsupported_fact_references", gates.max_unsupported_fact_references),
    ):
        actual = metrics["counts"][count_name]
        checks[count_name] = {"actual": actual, "maximum": limit, "passed": actual <= limit}
    for name, limit, minimum in (
        ("prediction_completeness", gates.minimum_prediction_completeness, True),
        ("coverage", gates.minimum_coverage, True),
        ("false_selection_rate", gates.maximum_false_selection_rate, False),
        ("outcome_accuracy", gates.minimum_outcome_accuracy, True),
    ):
        actual = metrics[name]["rate"]
        # A metric without labelled cases is not evidence of passing a release gate.
        passed = actual is not None and (actual >= limit if minimum else actual <= limit)
        checks[name] = {
            "actual": actual,
            "minimum" if minimum else "maximum": limit,
            "passed": passed,
            "status": "measured" if actual is not None else "insufficient_labels",
        }
    return {"passed": all(check["passed"] for check in checks.values()), "checks": checks}


def score_quality(labels: list[QualityLabel]) -> dict:
    """Report external labels without treating repeated judges as independent tasks."""
    counts = Counter(label.winner for label in labels if label.blinded)
    blinded = [label for label in labels if label.blinded]
    known = counts["candidate"] + counts["baseline"] + counts["tie"]
    return {
        "total_judgements": len(labels),
        "unique_tasks": len({label.task_id for label in labels}),
        "blinded_judgements": len(blinded),
        "blinded_unique_tasks": len({label.task_id for label in blinded}),
        "excluded_unblinded_judgements": len(labels) - len(blinded),
        "candidate_wins": counts["candidate"],
        "baseline_wins": counts["baseline"],
        "ties": counts["tie"],
        "unknown": counts["unknown"],
        "known_judgements": known,
        "candidate_win_rate_among_known": fraction(counts["candidate"], known),
        "candidate_first_judgements": sum(label.presented_first == "candidate" for label in blinded),
        "baseline_first_judgements": sum(label.presented_first == "baseline" for label in blinded),
    }


def quality_report(labels: list[QualityLabel]) -> dict:
    # No aggregate that silently mixes model opinion, human labels and synthetic fixtures.
    return {
        source: score_quality([label for label in labels if label.provenance == source])
        for source in ("human_verified", "model_proxy", "synthetic")
    }
