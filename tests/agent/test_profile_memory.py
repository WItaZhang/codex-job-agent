"""Experiment bookkeeping and predicted-state isolation, without model calls."""

import json
from pathlib import Path

import pytest

from applypilot_agent.evaluation.profile_memory_cases import load_cases
from applypilot_agent.evaluation.profile_memory_config import load_config
from applypilot_agent.evaluation.profile_memory_report import summarize

ROOT = Path(__file__).resolve().parents[2]


def test_config_paths_resolve_from_yaml_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(ROOT / "configs/profile_memory.yaml")
    assert config.cases_path == ROOT / "evals/synthetic/profile_memory_cases.jsonl"
    assert config.logs_dir == ROOT / "logs"


def test_missing_predictions_cannot_produce_complete_comparison():
    summary = summarize([], expected_by_arm=24)
    assert not summary["comparison_complete"]
    for arm in summary["arms"].values():
        assert arm["exact_rate_completed"] is None
        assert arm["correct_state_delivery_rate"] == 0
        assert arm["metrics_on_completed_turns"]["field"]["rate"] is None


def test_chain_reuses_predictions_never_reference_state(tmp_path, monkeypatch):
    pytest.importorskip("langchain_core")
    from applypilot_agent.evaluation import profile_memory as runner
    from applypilot_agent.evaluation import profile_memory_codex as adapter

    class Bridge:
        def __init__(self, *args, **kwargs):
            self.calls = []

    monkeypatch.setattr(adapter, "CodexCLI", Bridge)
    monkeypatch.setattr(adapter, "CodexChatModel", lambda **kwargs: object())
    seen = []

    def predict(arm, model, current, message, instructions, max_steps):
        seen.append(current.minimum_salary)
        return current.model_copy(update={"minimum_salary": 12345})

    monkeypatch.setattr(runner, "update_profile", predict)
    config = load_config(ROOT / "configs/profile_memory.yaml")
    case = load_cases(config.cases_path)[0]
    rows = runner._chain(config, tmp_path, "codex_direct", case, 0)
    assert seen[1:] == [12345] * (len(seen) - 1)
    assert not all(row["score"]["exact_match"] for row in rows)
    for path in tmp_path.rglob("input.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"current_profile", "message"}
        assert "expected_profile" not in data and "probes" not in data


def test_chain_error_stops_without_oracle_recovery(tmp_path, monkeypatch):
    pytest.importorskip("langchain_core")
    from applypilot_agent.evaluation import profile_memory as runner
    from applypilot_agent.evaluation import profile_memory_codex as adapter

    class Bridge:
        def __init__(self, *args, **kwargs):
            self.calls = []

    monkeypatch.setattr(adapter, "CodexCLI", Bridge)
    monkeypatch.setattr(adapter, "CodexChatModel", lambda **kwargs: object())

    def fail(*args, **kwargs):
        raise TimeoutError("synthetic infrastructure failure")

    monkeypatch.setattr(runner, "update_profile", fail)
    config = load_config(ROOT / "configs/profile_memory.yaml")
    rows = runner._chain(config, tmp_path, "codex_direct", load_cases(config.cases_path)[0], 0)
    assert rows[0]["status"] == "error"
    assert all(row["status"] == "skipped" for row in rows[1:])
    assert all(row["score"] is None for row in rows)
