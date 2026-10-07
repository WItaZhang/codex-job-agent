"""Run a frozen synthetic comparison with real Codex and LangMem calls.

Usage: python -m applypilot_agent.evaluation.profile_memory --config CONFIG.yaml
Only the experiment's ignored log directory is written; no job runtime is opened.
"""

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import random
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

import yaml

from .profile_memory_cases import Profile, canonical_profile, load_cases, score_turn
from .profile_memory_config import ExperimentConfig, load_config
from .profile_memory_report import markdown_report, summarize
from .profile_memory_strategies import update_profile


class _Tee:
    def __init__(self, stream, log, lock):
        self.stream, self.log, self.lock = stream, log, lock

    def write(self, value):
        with self.lock:
            self.log.write(value)
            return self.stream.write(value)

    def flush(self):
        with self.lock:
            self.log.flush()
            self.stream.flush()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _record(value) -> dict:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return dict(value)


def _chain(config: ExperimentConfig, run_dir: Path, arm: str, case, repetition: int) -> list[dict]:
    from .profile_memory_codex import CodexCallConfig, CodexChatModel, CodexCLI

    chain_dir = run_dir / "trials" / f"{case.scenario_id}-r{repetition}" / arm
    chain_dir.mkdir(parents=True, exist_ok=False)
    current = case.initial_profile.model_copy(deep=True)
    previous_expected = case.initial_profile
    write_json(chain_dir / "current.json", canonical_profile(current))
    rows = []
    failed = False
    for turn in case.turns[: config.turn_limit]:
        turn_dir = chain_dir / turn.turn_id
        turn_dir.mkdir()
        row = {
            "arm": arm,
            "case_id": case.scenario_id,
            "repetition": repetition,
            "turn_id": turn.turn_id,
            "status": "skipped" if failed else "pending",
            "calls": [],
            "score": None,
            "error": "Previous turn failed; no oracle state injected" if failed else None,
        }
        if failed:
            write_json(turn_dir / "result.json", row)
            rows.append(row)
            continue
        # These are the only task inputs accessible to the update protocol.
        write_json(
            turn_dir / "input.json",
            {"current_profile": canonical_profile(current), "message": turn.message.model_dump()},
        )
        bridge = CodexCLI(
            CodexCallConfig(
                executable=config.codex.executable,
                model=config.codex.model,
                reasoning_effort=config.codex.reasoning_effort,
                timeout_seconds=config.codex.timeout_seconds,
                max_calls=1 if arm == "codex_direct" else config.max_calls_per_turn,
                config_overrides=config.codex.config_overrides,
            ),
            turn_dir / "calls",
        )
        started = time.perf_counter()
        try:
            updated = update_profile(
                arm,
                CodexChatModel(bridge=bridge),
                current,
                turn.message.model_dump(),
                config.profile_instructions,
                config.langmem_max_steps,
            )
            # Persist and reload the current artifact; subsequent turns never use chat history.
            write_json(chain_dir / "current.json", canonical_profile(updated))
            current = Profile.model_validate_json((chain_dir / "current.json").read_text(encoding="utf-8"))
            row.update(
                status="ok",
                actual=canonical_profile(current),
                score=score_turn(current, turn.expected_profile, turn.probes, previous=previous_expected),
            )
            previous_expected = turn.expected_profile
        except Exception as exc:  # noqa: BLE001 -- record arbitrary provider/framework failures, never score as success
            failed = True
            row.update(status="error", error=f"{type(exc).__name__}: {exc}")
        row["elapsed_seconds"] = time.perf_counter() - started
        row["calls"] = [_record(call) for call in bridge.calls]
        write_json(turn_dir / "result.json", row)
        rows.append(row)
        print(json.dumps({key: row.get(key) for key in ("arm", "case_id", "turn_id", "status", "error")}), flush=True)
    return rows


def run(config_path: Path) -> Path:
    config_path = config_path.resolve()
    config = load_config(config_path)
    cases = load_cases(config.cases_path)[: config.case_limit]
    expected = sum(min(len(case.turns), config.turn_limit) for case in cases) * config.repetitions
    worst_case_calls = expected * (1 + config.max_calls_per_turn)
    if worst_case_calls > config.maximum_calls:
        raise ValueError(f"Worst-case model calls {worst_case_calls} exceed configured budget {config.maximum_calls}")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_dir = config.logs_dir / f"{stamp}_{config.experiment_name}_{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    lock = Lock()
    with (
        (run_dir / "run.log").open("w", encoding="utf-8") as log,
        redirect_stdout(_Tee(sys.stdout, log, lock)),
        redirect_stderr(_Tee(sys.stderr, log, lock)),
    ):
        try:
            return _execute(config_path, config, run_dir, cases, expected, worst_case_calls)
        except Exception:  # Preserve fatal framework traces before propagating.
            traceback.print_exc()
            raise


def _execute(config_path, config, run_dir, cases, expected, worst_case_calls) -> Path:
    inputs = run_dir / "inputs"
    inputs.mkdir()
    (inputs / "config.yaml").write_bytes(config_path.read_bytes())
    (inputs / "cases.jsonl").write_bytes(config.cases_path.read_bytes())
    (inputs / "resolved_config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    write_json(inputs / "profile_schema.json", Profile.model_json_schema())
    code_dir = inputs / "code"
    code_dir.mkdir()
    for path in Path(__file__).parent.glob("profile_memory*.py"):
        (code_dir / path.name).write_bytes(path.read_bytes())
    repo = Path(__file__).resolve().parents[3]
    (inputs / "uv.lock").write_bytes((repo / "uv.lock").read_bytes())
    packages = {
        name: importlib.metadata.version(name)
        for name in ("codex-job-agent", "langmem", "trustcall", "langchain-core", "pydantic")
    }
    version = subprocess.run([config.codex.executable, "--version"], capture_output=True, text=True, check=True)
    jobs = [
        (arm, case, repetition)
        for repetition in range(config.repetitions)
        for case in cases
        for arm in ("codex_direct", "langmem_profile")
    ]
    random.Random(config.seed).shuffle(jobs)
    manifest = {
        "provenance": "synthetic_development_pilot",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_config": str(config_path),
        "requested_model": config.codex.model,
        "requested_reasoning_effort": config.codex.reasoning_effort,
        "codex_version": version.stdout.strip(),
        "package_versions": packages,
        "expected_turns_per_arm": expected,
        "maximum_model_calls": worst_case_calls,
        "case_ids": [case.scenario_id for case in cases],
        "execution_order": [
            {"arm": arm, "case_id": case.scenario_id, "repetition": repetition} for arm, case, repetition in jobs
        ],
        "frozen_files": {
            str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(inputs.rglob("*"))
            if path.is_file()
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    print(
        json.dumps({"run_dir": str(run_dir), "expected_turns_per_arm": expected, "call_budget": worst_case_calls}),
        flush=True,
    )
    rows = []
    with (
        (run_dir / "run.jsonl").open("w", encoding="utf-8") as log,
        ThreadPoolExecutor(max_workers=config.concurrency) as pool,
    ):
        futures = [pool.submit(_chain, config, run_dir, arm, case, repetition) for arm, case, repetition in jobs]
        for future in as_completed(futures):
            for row in future.result():
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            log.flush()
    rows.sort(key=lambda row: (row["case_id"], row["repetition"], row["arm"], row["turn_id"]))
    write_json(run_dir / "results.json", rows)
    summary = summarize(rows, expected)
    write_json(run_dir / "metrics.json", summary)
    (run_dir / "report.md").write_text(markdown_report(summary, rows, config.model_dump(mode="json")), encoding="utf-8")
    print(
        json.dumps({"report": str(run_dir / "report.md"), "comparison_complete": summary["comparison_complete"]}),
        flush=True,
    )
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Experiment YAML; no implicit parameter overrides")
    run(parser.parse_args().config)


if __name__ == "__main__":
    main()
