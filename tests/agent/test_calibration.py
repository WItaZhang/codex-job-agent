"""Synthetic adversarial judges exercise the calibration protocol, not real quality."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
import yaml
from typer.testing import CliRunner

from applypilot_agent.evaluation import calibration
from applypilot_agent.evaluation.calibration import app, prepare, record, report
from applypilot_agent.lease import LeaseBusy

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def configuration(tmp_path, **updates):
    config = yaml.safe_load((ROOT / "configs/judge_calibration.yaml").read_text(encoding="utf-8"))
    config["cases_path"] = str(ROOT / "evals/synthetic/judge_calibration_cases.jsonl")
    config["log_dir"] = str(tmp_path / "logs")
    config["judge"] = {
        "judge_id": "unit-fixture-generator",
        "judge_version": "fixture-v1",
        "provenance": "synthetic_metric_fixture",
        "configuration": {"generator": "deterministic test function"},
        "metadata_complete": True,
    }
    config.update(updates)
    path = tmp_path / "calibration.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def make_result(run, entry, winner):
    packet = read(run / "reviewer" / f"{entry['trial_id']}.json")
    manifest = read(run / "manifest.json")["payload"]
    binding = packet["judge_binding"]
    return {
        "trial_id": entry["trial_id"],
        "packet_sha256": manifest["files"][f"reviewer/{entry['trial_id']}.json"],
        "judge_id": binding["judge_id"],
        "judge_version": binding["judge_version"],
        "judge_config_sha256": binding["judge_config_sha256"],
        "rubric_version": packet["rubric"]["version"],
        "rubric_sha256": packet["rubric_sha256"],
        "provenance": binding["provenance"],
        "reviewer_session_id": f"synthetic-fixture-context-{entry['trial_id']}",
        "winner": winner,
        "evidence": [
            {"candidate": label, "quote": text, "reason": "Synthetic unit-fixture contrast; no actual model judgement."}
            for label, text in packet["candidates"].items()
        ],
        "rationale": "Synthetic metric unit fixture, not a real independent judge result.",
    }


def expected_winner(entry):
    return next(
        (key for key, value in entry["presentation"].items() if value == entry["preferred"]), entry["preferred"]
    )


def record_judge(run, tmp_path, judge):
    for entry in read(run / "evaluator_mapping.json"):
        packet = read(run / "reviewer" / f"{entry['trial_id']}.json")
        result = make_result(run, entry, judge(entry, packet))
        record(run, write(tmp_path / "response.json", result))


def test_prepare_blinds_labels_swaps_order_freezes_inputs_and_repeats_seed(tmp_path):
    config = configuration(tmp_path)
    first, second = prepare(config), prepare(config)
    mapping = read(first / "evaluator_mapping.json")
    assert mapping == read(second / "evaluator_mapping.json")
    pairs = {}
    for entry in mapping:
        packet = read(first / "reviewer" / f"{entry['trial_id']}.json")
        assert not {"case_id", "kind", "preferred", "presentation", "control_explanation", "pair_id"} & packet.keys()
        assert entry["case_id"] not in json.dumps(packet)
        assert entry["control_explanation"] not in json.dumps(packet)
        assert set(packet["candidates"]) == {"A", "B"}
        assert "actual employer" in packet["perspective"]
        pairs.setdefault(entry["pair_id"], []).append((entry, packet))
    for pair in pairs.values():
        assert len(pair) == 2
        assert pair[0][1]["candidates"]["A"] == pair[1][1]["candidates"]["B"]
        assert pair[0][1]["candidates"]["B"] == pair[1][1]["candidates"]["A"]
    assert read(first / "manifest.json")["payload"]["run_id"] != read(second / "manifest.json")["payload"]["run_id"]
    # Later edits to the original config cannot alter a frozen run.
    config.write_text("invalid: true", encoding="utf-8")
    assert report(first)["expected_trials"] == 12


def test_perfect_synthetic_protocol_pass_cannot_release_a_judge(tmp_path):
    run = prepare(configuration(tmp_path))
    record_judge(run, tmp_path, lambda entry, packet: expected_winner(entry))
    metrics = report(run)
    assert metrics["status"] == "pass"
    assert metrics["release_eligible"] is False
    assert metrics["decision_provenance"] == "synthetic_metric_fixture"
    assert metrics["result_completeness"] == {"numerator": 12, "denominator": 12, "rate": 1.0}
    assert metrics["position_inconsistency"]["rate"] == 0.0
    assert len(metrics["result_hashes"]) == 12


def test_concision_preference_for_redundancy_is_accepted_without_rewarding_shortness_alone(tmp_path):
    run = prepare(configuration(tmp_path))

    def reference_when_redundant(entry, packet):
        if entry["kind"] == "redundant_padding":
            return next(label for label, value in entry["presentation"].items() if value == "reference")
        return expected_winner(entry)

    record_judge(run, tmp_path, reference_when_redundant)
    metrics = report(run)
    assert metrics["status"] == "pass"
    assert metrics["by_kind"]["redundant_padding"]["criterion_success_over_expected"]["rate"] == 1.0


@pytest.mark.parametrize("behavior", ["prefer_long", "prefer_short", "prefer_A"])
def test_adversarial_judges_fail_controlled_contrasts(tmp_path, behavior):
    run = prepare(configuration(tmp_path))

    def adversary(entry, packet):
        if behavior == "prefer_A":
            return "A"
        lengths = {label: len(text.split()) for label, text in packet["candidates"].items()}
        if lengths["A"] == lengths["B"]:
            return "tie"
        choose = max if behavior == "prefer_long" else min
        return choose(lengths, key=lengths.get)

    record_judge(run, tmp_path, adversary)
    metrics = report(run)
    assert metrics["status"] == "fail"
    assert metrics["release_eligible"] is False
    if behavior == "prefer_long":
        assert metrics["by_kind"]["redundant_padding"]["padding_preference_among_resolved"]["rate"] == 1.0
    elif behavior == "prefer_short":
        assert metrics["by_kind"]["useful_longer"]["criterion_failures_among_resolved"]["rate"] == 1.0
    else:
        assert metrics["position_inconsistency"]["rate"] == 1.0


def test_missing_and_unknown_results_do_not_improve_fixed_denominators(tmp_path):
    run = prepare(configuration(tmp_path))
    initial = report(run)
    assert initial["status"] == "insufficient_evidence"
    assert initial["missing_results"] == initial["expected_trials"] == 12
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "unknown")
    result["evidence"] = []
    record(run, write(tmp_path / "response.json", result))
    metrics = report(run)
    assert metrics["status"] == "insufficient_evidence"
    assert metrics["missing_results"] == 11
    assert metrics["unknown_results"] == 1
    assert metrics["result_completeness"]["denominator"] == metrics["unknown_over_expected"]["denominator"] == 12
    assert metrics["resolved_over_expected"]["numerator"] == 0
    assert metrics["position_inconsistency"]["rate"] is None


def test_repeated_trials_cannot_replace_distinct_cases(tmp_path):
    config = configuration(tmp_path)
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["repetitions"] = 2
    values["gates"]["minimum_distinct_cases_per_kind"] = 3
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    run = prepare(config)
    record_judge(run, tmp_path, lambda entry, packet: expected_winner(entry))
    metrics = report(run)
    assert metrics["expected_trials"] == 24
    assert metrics["status"] == "insufficient_evidence"
    assert all(stats["distinct_cases"] == 2 for stats in metrics["by_kind"].values())


def test_unreviewed_distinct_cases_do_not_satisfy_sample_gate(tmp_path):
    config = configuration(tmp_path, repetitions=2)
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["gates"]["minimum_completeness"] = 0.0
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    run = prepare(config)
    chosen = {}
    for entry in read(run / "evaluator_mapping.json"):
        chosen.setdefault(entry["kind"], entry["case_id"])
        if entry["case_id"] == chosen[entry["kind"]]:
            record(run, write(tmp_path / "response.json", make_result(run, entry, expected_winner(entry))))
    metrics = report(run)
    assert metrics["status"] == "insufficient_evidence"
    assert all(stats["distinct_cases"] == 2 for stats in metrics["by_kind"].values())
    assert all(stats["resolved_pairs"] == 2 for stats in metrics["by_kind"].values())
    assert all(stats["distinct_resolved_cases"] == 1 for stats in metrics["by_kind"].values())


def test_unknown_host_model_cannot_qualify_even_with_complete_results(tmp_path):
    config = configuration(tmp_path)
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["judge"]["judge_version"] = "unreported-host-model"
    values["judge"]["metadata_complete"] = False
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    run = prepare(config)
    record_judge(run, tmp_path, lambda entry, packet: expected_winner(entry))
    metrics = report(run)
    assert metrics["status"] == "insufficient_evidence"
    assert "actual judge model/version metadata is incomplete" in metrics["insufficient_evidence"]


def test_duplicate_foreign_and_reused_context_results_rejected(tmp_path):
    config = configuration(tmp_path)
    run, other_run = prepare(config), prepare(config)
    entries = read(run / "evaluator_mapping.json")
    result = make_result(run, entries[0], "tie")
    response = write(tmp_path / "response.json", result)
    record(run, response)
    with pytest.raises(ValueError, match="duplicate result"):
        record(run, response)
    with pytest.raises(ValueError, match="packet_sha256"):
        record(other_run, response)
    next_result = make_result(run, entries[1], "tie")
    next_result["reviewer_session_id"] = result["reviewer_session_id"]
    with pytest.raises(ValueError, match="session reused"):
        record(run, write(response, next_result))
    next_result["trial_id"] = "f" * 24
    with pytest.raises(ValueError, match="foreign trial"):
        record(run, write(response, next_result))


@pytest.mark.parametrize(
    "field", ["judge_id", "judge_version", "judge_config_sha256", "rubric_version", "rubric_sha256", "provenance"]
)
def test_mixed_judge_or_rubric_versions_rejected(tmp_path, field):
    run = prepare(configuration(tmp_path))
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "tie")
    result[field] = (
        "model_proxy" if field == "provenance" else "0" * 64 if field.endswith("sha256") else "another-version"
    )
    with pytest.raises(ValueError, match=f"binding mismatch: {field}"):
        record(run, write(tmp_path / "response.json", result))


@pytest.mark.parametrize("mutation", ["missing_citation", "foreign_quote", "blank_reason"])
def test_decisions_require_actual_citations_to_both_presented_candidates(tmp_path, mutation):
    run = prepare(configuration(tmp_path))
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "A")
    if mutation == "missing_citation":
        result["evidence"].pop()
    elif mutation == "foreign_quote":
        result["evidence"][0]["quote"] = "invented supporting statement"
    else:
        result["evidence"][0]["reason"] = " "
    with pytest.raises(ValueError):
        record(run, write(tmp_path / "response.json", result))


@pytest.mark.parametrize(
    "target", ["inputs/config.yaml", "inputs/cases.jsonl", "evaluator_mapping.json", "packet", "manifest"]
)
def test_tampered_frozen_inputs_fail_closed(tmp_path, target):
    run = prepare(configuration(tmp_path))
    if target == "manifest":
        path = run / "manifest.json"
        value = read(path)
        value["payload"]["expected_trials"] = 2
        write(path, value)
    else:
        path = next((run / "reviewer").iterdir()) if target == "packet" else run / target
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        report(run)


def test_tampered_recorded_result_and_unrecorded_files_fail_closed(tmp_path):
    run = prepare(configuration(tmp_path))
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "tie")
    record(run, write(tmp_path / "response.json", result))
    result["winner"] = "A"
    write(run / "results" / f"{entry['trial_id']}.json", result)
    with pytest.raises(ValueError, match="recorded result integrity"):
        report(run)
    write(run / "results/foreign.json", result)
    with pytest.raises(ValueError, match="foreign result receipt"):
        report(run)


def test_interrupted_receipt_recovers_identical_result_without_overwriting_verdict(tmp_path, monkeypatch):
    run = prepare(configuration(tmp_path))
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "tie")
    response = write(tmp_path / "response.json", result)
    original_write = calibration._write

    def interrupt_receipt(path, value, exclusive=False):
        if path.parent.name == "result_receipts":
            raise OSError("injected interruption before receipt creation")
        return original_write(path, value, exclusive)

    with monkeypatch.context() as patch:
        patch.setattr(calibration, "_write", interrupt_receipt)
        with pytest.raises(OSError, match="injected interruption"):
            record(run, response)
    stored = run / "results" / f"{entry['trial_id']}.json"
    original_bytes = stored.read_bytes()
    with pytest.raises(ValueError, match="missing or foreign result receipt"):
        report(run)
    with pytest.raises(ValueError, match="identical recorded result"):
        record(run, write(response, {**result, "winner": "A"}))
    assert stored.read_bytes() == original_bytes
    assert not list((run / "result_receipts").iterdir())
    recovered = record(run, write(response, result))
    assert recovered["recovered_receipt"] is True
    assert recovered["received_trials"] == 1
    assert stored.read_bytes() == original_bytes
    assert report(run)["result_completeness"]["numerator"] == 1
    with pytest.raises(ValueError, match="duplicate result"):
        record(run, response)


def test_orphan_recovery_does_not_ignore_foreign_receipts(tmp_path):
    run = prepare(configuration(tmp_path))
    entry = read(run / "evaluator_mapping.json")[0]
    result = make_result(run, entry, "tie")
    stored = write(run / "results" / f"{entry['trial_id']}.json", result)
    original_bytes = stored.read_bytes()
    write(run / "result_receipts/foreign.json", {"trial_id": "foreign", "sha256": "0" * 64})
    with pytest.raises(ValueError, match="missing or foreign result receipt"):
        record(run, write(tmp_path / "response.json", result))
    assert stored.read_bytes() == original_bytes
    assert not (run / "result_receipts" / stored.name).exists()


def test_run_lease_prevents_concurrent_record_and_partial_report(tmp_path, monkeypatch):
    run = prepare(configuration(tmp_path))
    entries = read(run / "evaluator_mapping.json")
    first = write(tmp_path / "first.json", make_result(run, entries[0], "tie"))
    second = write(tmp_path / "second.json", make_result(run, entries[1], "tie"))
    receipt_reached, release = Event(), Event()
    original_write = calibration._write

    def hold_receipt(path, value, exclusive=False):
        if path.parent.name == "result_receipts" and path.stem == entries[0]["trial_id"]:
            receipt_reached.set()
            if not release.wait(timeout=10):
                raise RuntimeError("test failed to release paused writer")
        return original_write(path, value, exclusive)

    monkeypatch.setattr(calibration, "_write", hold_receipt)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(record, run, first)
        try:
            assert receipt_reached.wait(timeout=10)
            with pytest.raises(LeaseBusy):
                record(run, second)
            with pytest.raises(LeaseBusy):
                report(run)
        finally:
            release.set()
        assert pending.result(timeout=10)["received_trials"] == 1
    assert record(run, second)["received_trials"] == 2
    assert report(run)["result_completeness"]["numerator"] == 2


def test_budget_enforced_before_creating_run_and_cli_exposes_missing_work(tmp_path):
    config = configuration(tmp_path, maximum_trials=2)
    with pytest.raises(ValueError, match="budget"):
        prepare(config)
    assert not (tmp_path / "logs").exists()
    config = configuration(tmp_path)
    runner = CliRunner()
    output = runner.invoke(app, ["prepare", "--config", str(config)])
    assert output.exit_code == 0, output.output
    run = json.loads(output.output)["run_dir"]
    output = runner.invoke(app, ["report", "--run", run])
    assert output.exit_code == 1
    assert json.loads(output.output)["status"] == "insufficient_evidence"
