"""Deterministic fixture/scorer checks; these are not model performance results."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from applypilot_agent.evaluation.profile_memory_cases import (
    Profile,
    canonical_profile,
    invariant_violations,
    load_cases,
    score_turn,
)

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "evals/synthetic/profile_memory_cases.jsonl"


def cases():
    return load_cases(CASES_PATH)


def test_synthetic_cases_have_complete_current_oracles_and_consistent_probes():
    values = cases()
    assert len(values) == 8
    assert sum(len(case.turns) for case in values) == 24
    assert all(case.synthetic for case in values)
    required_tags = {
        "reversal",
        "related_cleanup",
        "company_exception",
        "preservation",
        "fact_correction",
        "ambiguity",
        "prompt_injection",
        "multi_update",
    }
    assert required_tags <= {tag for case in values for tag in case.tags}
    for case in values:
        previous = case.initial_profile
        for turn in case.turns:
            expected = turn.expected_profile
            score = score_turn(expected, expected, turn.probes, previous)
            assert score["exact_match"]
            assert score["field_accuracy"] == score["probe_pass_rate"] == score["preservation_accuracy"] == 1
            assert not score["leaf_differences"]
            assert not score["invariant_violations"]
            previous = expected


def test_set_order_is_irrelevant_but_duplicate_or_additional_modes_are_not():
    turn = cases()[0].turns[0]
    value = canonical_profile(turn.expected_profile)
    value["allowed_modes"].reverse()
    assert score_turn(value, turn.expected_profile, turn.probes)["exact_match"]
    value["allowed_modes"].append("hybrid")
    assert not score_turn(value, turn.expected_profile, turn.probes)["exact_match"]
    with pytest.raises(ValueError, match="distinct"):
        Profile.model_validate(value)


def test_missing_is_not_null_and_bool_is_not_integer():
    turn = cases()[0].turns[1]
    value = canonical_profile(turn.expected_profile)
    del value["max_office_days"]
    score = score_turn(value, turn.expected_profile, turn.probes)
    assert not score["exact_match"]
    assert score["probe_pass_rate"] == 0.5
    difference = score["leaf_differences"][0]
    assert difference == {
        "path": ["max_office_days"],
        "expected_present": True,
        "expected": None,
        "actual_present": False,
        "actual": None,
    }
    value = canonical_profile(turn.expected_profile)
    value["auto_submit"] = 0
    assert not score_turn(value, turn.expected_profile, turn.probes)["exact_match"]


def test_extra_old_directives_and_empty_exceptions_are_exposed():
    turn = cases()[2].turns[2]
    value = canonical_profile(turn.expected_profile)
    value["company_exceptions"]["acme"] = {}
    value["old_directives"] = ["do not accept hybrid", "now accept hybrid"]
    score = score_turn(value, turn.expected_profile, turn.probes)
    assert not score["exact_match"]
    assert len(score["leaf_differences"]) == 2
    assert score["invariant_violations"] == ["company_exceptions.acme: empty exception should be removed"]


def test_preservation_measures_unchanged_fact_leaves_inside_a_changed_container():
    case = cases()[4]
    turn = case.turns[0]
    value = canonical_profile(turn.expected_profile)
    del value["confirmed_facts"]["location"]
    before = deepcopy(value)
    score = score_turn(value, turn.expected_profile, turn.probes, case.initial_profile)
    assert score["preservation_accuracy"] < 1
    assert score["preservation_denominator"] - score["preservation_numerator"] == 1
    assert score["leaf_differences"][0]["path"] == ["confirmed_facts", "location"]
    assert value == before


def test_missing_fact_container_reports_each_missing_leaf():
    turn = cases()[4].turns[0]
    value = canonical_profile(turn.expected_profile)
    expected_keys = set(value.pop("confirmed_facts"))
    score = score_turn(value, turn.expected_profile, turn.probes)
    assert {tuple(item["path"]) for item in score["leaf_differences"]} == {
        ("confirmed_facts", key) for key in expected_keys
    }
    assert all(item["actual_present"] is False for item in score["leaf_differences"])


def test_company_partial_override_keeps_absence_distinct_from_explicit_null():
    value = canonical_profile(cases()[2].turns[0].expected_profile)
    assert value["company_exceptions"] == {"acme": {"travel": "occasional"}}
    value["company_exceptions"]["acme"]["max_office_days"] = None
    parsed = Profile.model_validate(value)
    assert canonical_profile(parsed)["company_exceptions"]["acme"]["max_office_days"] is None
    assert invariant_violations(parsed) == ["company_exceptions.acme: remote-only must have max_office_days=0"]


def test_stale_office_limit_is_diagnosed_without_repairing_it():
    value = canonical_profile(cases()[0].turns[1].expected_profile)
    value["max_office_days"] = 2
    assert invariant_violations(value) == ["onsite conflicts with max_office_days<5"]
    assert canonical_profile(value)["max_office_days"] == 2
    value["allowed_modes"] = ["remote"]
    assert invariant_violations(value) == ["remote-only must have max_office_days=0"]


def test_prompt_injections_and_ambiguity_do_not_change_oracle_state():
    selected = [case for case in cases() if case.scenario_id in {"untrusted_job_content", "ambiguous_no_overreach"}]
    assert len(selected) == 2
    for case in selected:
        for turn in case.turns:
            assert canonical_profile(turn.expected_profile) == canonical_profile(case.initial_profile)
    assert sum(turn.message.role == "job_content" for case in selected for turn in case.turns) == 2


@pytest.mark.parametrize("mutation", ["duplicate_case", "duplicate_turn", "wrong_probe", "non_synthetic"])
def test_corrupt_evaluator_data_is_rejected(tmp_path, mutation):
    value = cases()[0].model_dump(exclude_unset=True)
    values = [value]
    if mutation == "duplicate_case":
        values.append(deepcopy(value))
    elif mutation == "duplicate_turn":
        value["turns"][1]["turn_id"] = value["turns"][0]["turn_id"]
    elif mutation == "wrong_probe":
        value["turns"][0]["probes"][0]["expected"] = ["onsite"]
    elif mutation == "non_synthetic":
        value["synthetic"] = False
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in values), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)
