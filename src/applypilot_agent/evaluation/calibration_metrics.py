"""Paired contrast metrics with explicit fixed denominators and sample gates."""

from .calibration_models import KINDS, Config, Result


def _rate(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def summarize(config: Config, mapping: list[dict], results: dict[str, Result]) -> dict:
    """Missing/unknown decisions never count as ties or successful controls."""
    scored = []
    for entry in mapping:
        result = results.get(entry["trial_id"])
        winner = None
        if result:
            winner = entry["presentation"].get(result.winner, result.winner)
        scored.append({**entry, "winner": winner})
    total = len(scored)
    missing = sum(item["winner"] is None for item in scored)
    unknown = sum(item["winner"] == "unknown" for item in scored)
    by_kind = {}
    all_pairs = []
    for kind in KINDS:
        trials = [item for item in scored if item["kind"] == kind]
        resolved = [item for item in trials if item["winner"] not in (None, "unknown")]
        pairs = {}
        for item in trials:
            pairs.setdefault(item["pair_id"], []).append(item)
        resolved_pairs = [
            pair for pair in pairs.values() if all(item["winner"] not in (None, "unknown") for item in pair)
        ]
        inconsistent = sum(pair[0]["winner"] != pair[1]["winner"] for pair in resolved_pairs)
        all_pairs.extend(resolved_pairs)
        # Concision may improve readability even when factual utility is equal.
        # The padding probe detects rewarding redundancy; it does not force ties.
        failures = sum(
            item["winner"] == "alternative" if kind == "redundant_padding" else item["winner"] != item["preferred"]
            for item in resolved
        )
        padded = sum(item["winner"] == "alternative" for item in resolved)
        by_kind[kind] = {
            "distinct_cases": len({item["case_id"] for item in trials}),
            "distinct_resolved_cases": len({pair[0]["case_id"] for pair in resolved_pairs}),
            "expected_trials": len(trials),
            "received_trials": sum(item["winner"] is not None for item in trials),
            "resolved_trials": len(resolved),
            "expected_pairs": len(pairs),
            "resolved_pairs": len(resolved_pairs),
            "position_inconsistency": _rate(inconsistent, len(resolved_pairs)),
            # Rates among resolved trials and fixed expected denominators are both explicit.
            "criterion_failures_among_resolved": _rate(failures, len(resolved)),
            "criterion_success_over_expected": _rate(len(resolved) - failures, len(trials)),
            "padding_preference_among_resolved": _rate(padded, len(resolved)) if kind == "redundant_padding" else None,
        }
    completeness = _rate(total - missing, total)
    unknown_rate = _rate(unknown, total)
    position = _rate(sum(pair[0]["winner"] != pair[1]["winner"] for pair in all_pairs), len(all_pairs))
    failures = []
    insufficient = []
    gate = config.gates
    if not config.judge.metadata_complete or config.judge.judge_version.strip().lower() in {
        "unknown",
        "unreported-host-model",
        "unreported",
        "unspecified",
    }:
        insufficient.append("actual judge model/version metadata is incomplete")
    for kind, stats in by_kind.items():
        if stats["distinct_resolved_cases"] < gate.minimum_distinct_cases_per_kind:
            insufficient.append(f"{kind}: too few distinct resolved cases")
        if stats["resolved_pairs"] < gate.minimum_resolved_pairs_per_kind:
            insufficient.append(f"{kind}: too few resolved independent trial pairs")
        if kind != "redundant_padding":
            rate = stats["criterion_failures_among_resolved"]["rate"]
            if rate is not None and rate > gate.maximum_positive_control_failure:
                failures.append(f"{kind}: positive-control failure")
    padding = by_kind["redundant_padding"]["padding_preference_among_resolved"]
    if completeness["rate"] is None or completeness["rate"] < gate.minimum_completeness:
        failures.append("missing results exceed completeness threshold")
    if unknown_rate["rate"] is not None and unknown_rate["rate"] > gate.maximum_unknown_rate:
        failures.append("unknown decisions exceed threshold")
    if padding["rate"] is not None and padding["rate"] > gate.maximum_padding_preference:
        failures.append("redundant padding is preferred too often")
    if position["rate"] is not None and position["rate"] > gate.maximum_position_inconsistency:
        failures.append("order-swapped decisions are inconsistent")
    status = "insufficient_evidence" if insufficient else "fail" if failures else "pass"
    return {
        "status": status,
        "gate_failures": failures,
        "insufficient_evidence": insufficient,
        "judge": config.judge.model_dump(),
        "rubric_version": config.rubric.version,
        "case_provenance": "synthetic",
        "decision_provenance": config.judge.provenance,
        "release_eligible": status == "pass" and config.judge.provenance == "model_proxy",
        "expected_trials": total,
        "missing_results": missing,
        "unknown_results": unknown,
        "result_completeness": completeness,
        "unknown_over_expected": unknown_rate,
        "resolved_over_expected": _rate(total - missing - unknown, total),
        "position_inconsistency": position,
        "by_kind": by_kind,
        "limitations": [
            "Synthetic controlled contrasts are diagnostic; passing does not eliminate bias or measure employer outcomes.",
            "Model-proxy decisions and synthetic metric fixtures are separate provenance; fixture passes cannot release a judge.",
            "Order inconsistency includes judge stochasticity and is not a causal estimate of position bias.",
            "Fresh reviewer sessions are declared and checked for reuse; packet separation is not a context sandbox.",
            "Repeats increase trial count, not distinct-case coverage. Rates are descriptive, not confidence guarantees.",
            "Unknown host model metadata permits a protocol demonstration only; the operator must record host-reported versions.",
        ],
    }
