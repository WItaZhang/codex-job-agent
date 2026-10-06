"""Prepare anonymous packets, record independent reviews, and gate a frozen judge.

Run ``python -m applypilot_agent.evaluation.calibration --help``. Workflow:
``prepare --config <yaml>`` creates a fresh run; provide each packet separately
to a fresh reviewer context, then ``record --run <directory> --result <json>``.
``report --run <directory>`` validates frozen inputs and writes ``metrics.json``.
The reviewer receives only one ``reviewer/*.json`` plus its SHA256 from the
manifest. Keep ``evaluator_mapping.json`` and input snapshots evaluator-only.
The tool does not invoke a model, enforce context isolation, or modify skills.
"""

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
import yaml

from ..lease import LeaseBusy, application_lease
from .calibration_metrics import summarize
from .calibration_models import Case, Config, Result

app = typer.Typer(help=__doc__, no_args_is_help=True)


def digest(value: object) -> str:
    """Canonical JSON hash for judge/rubric configuration binding."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object, exclusive: bool = False) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cases(contents: str) -> list[Case]:
    cases = [Case.model_validate(json.loads(line)) for line in contents.splitlines() if line.strip()]
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("cases must be nonempty with unique IDs")
    return cases


def _event(run_dir: Path, event: str) -> None:
    with (run_dir / "run.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(UTC).isoformat()} {event}\n")


def prepare(config_path: str | Path) -> Path:
    """Freeze config/cases and create randomized paired order-swapped packets."""
    config_path = Path(config_path).resolve()
    config_bytes = config_path.read_bytes()
    config = Config.model_validate(yaml.safe_load(config_bytes))
    cases_path = (config_path.parent / config.cases_path).resolve()
    cases_bytes = cases_path.read_bytes()
    cases = _cases(cases_bytes.decode("utf-8"))
    trial_count = len(cases) * config.repetitions * 2
    if trial_count > config.maximum_trials:
        raise ValueError("trial count exceeds configured budget")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = (config_path.parent / config.log_dir).resolve() / f"{timestamp}_{config.experiment_name}"
    run_dir.mkdir(parents=True, exist_ok=False)
    for child in ("inputs", "reviewer", "results", "result_receipts"):
        (run_dir / child).mkdir()
    (run_dir / "inputs/config.yaml").write_bytes(config_bytes)
    (run_dir / "inputs/cases.jsonl").write_bytes(cases_bytes)
    generator = random.Random(config.seed)
    generator.shuffle(cases)
    run_id = uuid4().hex
    mapping = []
    rubric = config.rubric.model_dump()
    judge = config.judge.model_dump()
    files = {}
    for case in cases:
        for repetition in range(config.repetitions):
            pair_id = f"{case.case_id}:{repetition}"
            first = generator.choice(("reference", "alternative"))
            for order in (first, "alternative" if first == "reference" else "reference"):
                trial_id = f"{generator.getrandbits(96):024x}"
                presentation = {"A": order, "B": "alternative" if order == "reference" else "reference"}
                packet = {
                    "schema_version": "judge-calibration-packet-v1",
                    "run_id": run_id,
                    "trial_id": trial_id,
                    "case_provenance": "synthetic",
                    "perspective": "Simulated hiring review; not the actual employer or a hiring prediction.",
                    "rubric": rubric,
                    "rubric_sha256": digest(rubric),
                    "judge_binding": {**judge, "judge_config_sha256": digest(judge)},
                    "job_context": case.job_context,
                    "confirmed_facts": case.confirmed_facts,
                    "candidates": {label: getattr(case, name) for label, name in presentation.items()},
                    "response_contract": {
                        "winner": "A, B, tie, or unknown",
                        "evidence": "For resolved decisions cite exact quote(s) from BOTH candidates with a reason.",
                        "bindings": "Copy trial_id, packet_sha256 (provided separately), judge_id, judge_version, "
                        "judge_config_sha256, rubric_version, rubric_sha256, and provenance.",
                        "reviewer_session_id": "Identify the fresh independent review context used for this trial.",
                        "rationale": "Explain the job-relevant difference or uncertainty without assuming extra facts.",
                    },
                    "result_schema": Result.model_json_schema(),
                }
                relative = f"reviewer/{trial_id}.json"
                _write(run_dir / relative, packet, exclusive=True)
                files[relative] = file_hash(run_dir / relative)
                mapping.append(
                    {
                        "trial_id": trial_id,
                        "case_id": case.case_id,
                        "kind": case.kind,
                        "pair_id": pair_id,
                        "presentation": presentation,
                        "preferred": case.preferred,
                        "control_explanation": case.control_explanation,
                    }
                )
    _write(run_dir / "evaluator_mapping.json", mapping, exclusive=True)
    for relative in ("inputs/config.yaml", "inputs/cases.jsonl", "evaluator_mapping.json"):
        files[relative] = file_hash(run_dir / relative)
    payload = {
        "schema_version": "judge-calibration-run-v1",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_config": str(config_path),
        "source_cases": str(cases_path),
        "expected_trials": trial_count,
        "files": files,
        "judge_config_sha256": digest(judge),
        "rubric_sha256": digest(rubric),
    }
    _write(run_dir / "manifest.json", {"payload": payload, "sha256": digest(payload)}, exclusive=True)
    _event(run_dir, f"prepared {trial_count} trials; reviewer packets exclude evaluator labels")
    return run_dir


def _load(run_dir: Path) -> tuple[Config, dict, list[dict], dict]:
    envelope = _read(run_dir / "manifest.json")
    manifest = envelope["payload"]
    if digest(manifest) != envelope["sha256"]:
        raise ValueError("run metadata integrity check failed")
    for name, expected in manifest["files"].items():
        path = (run_dir / name).resolve()
        if not path.is_relative_to(run_dir.resolve()) or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"frozen input integrity check failed: {name}")
    config = Config.model_validate(yaml.safe_load((run_dir / "inputs/config.yaml").read_text(encoding="utf-8")))
    _cases((run_dir / "inputs/cases.jsonl").read_text(encoding="utf-8"))
    mapping = _read(run_dir / "evaluator_mapping.json")
    if len(mapping) != manifest["expected_trials"] or len({item["trial_id"] for item in mapping}) != len(mapping):
        raise ValueError("trial manifest is inconsistent")
    packets = {item["trial_id"]: _read(run_dir / "reviewer" / f"{item['trial_id']}.json") for item in mapping}
    actual_packets = {path.name for path in (run_dir / "reviewer").iterdir()}
    if actual_packets != {f"{trial_id}.json" for trial_id in packets}:
        raise ValueError("foreign or missing reviewer packet")
    return config, manifest, mapping, packets


def _validate_result(result: Result, config: Config, manifest: dict, packets: dict) -> None:
    if result.trial_id not in packets:
        raise ValueError("foreign trial ID")
    bindings = {
        "packet_sha256": manifest["files"][f"reviewer/{result.trial_id}.json"],
        "judge_id": config.judge.judge_id,
        "judge_version": config.judge.judge_version,
        "judge_config_sha256": manifest["judge_config_sha256"],
        "rubric_version": config.rubric.version,
        "rubric_sha256": manifest["rubric_sha256"],
        "provenance": config.judge.provenance,
    }
    for key, expected in bindings.items():
        if getattr(result, key) != expected:
            raise ValueError(f"result binding mismatch: {key}")
    candidates = packets[result.trial_id]["candidates"]
    for item in result.evidence:
        if not item.quote.strip() or item.quote not in candidates[item.candidate] or not item.reason.strip():
            raise ValueError("evidence quote must occur in the cited candidate and have a reason")


def _results(
    run_dir: Path, config: Config, manifest: dict, packets: dict, *, recoverable_orphan: str | None = None
) -> dict[str, Result]:
    results = {}
    sessions = set()
    result_names = {path.name for path in (run_dir / "results").iterdir()}
    receipt_names = {path.name for path in (run_dir / "result_receipts").iterdir()}
    expected_missing = {f"{recoverable_orphan}.json"} if recoverable_orphan else set()
    if result_names - receipt_names != expected_missing or receipt_names - result_names:
        raise ValueError("missing or foreign result receipt; result recording may have been interrupted")
    for path in (run_dir / "results").iterdir():
        if path.suffix != ".json" or not path.is_file():
            raise ValueError("foreign result file")
        if path.stem != recoverable_orphan:
            receipt = _read(run_dir / "result_receipts" / path.name)
            if receipt != {"trial_id": path.stem, "sha256": file_hash(path)}:
                raise ValueError("recorded result integrity check failed")
        result = Result.model_validate(_read(path))
        _validate_result(result, config, manifest, packets)
        if path.stem != result.trial_id or result.trial_id in results:
            raise ValueError("duplicate or misnamed trial result")
        if result.reviewer_session_id in sessions:
            raise ValueError("reviewer session reused across independent trials")
        sessions.add(result.reviewer_session_id)
        results[result.trial_id] = result
    return results


def record(run_dir: str | Path, result_path: str | Path) -> dict:
    """Serialize writes and recover only an identical, valid result missing its receipt.

    An interruption before receipt creation can be resumed with the same response. The entire
    structured response must equal the orphan; the stored verdict is never
    replaced. Other receipt inconsistencies and completed duplicates fail closed.
    """
    run_dir = Path(run_dir).resolve()
    with application_lease(run_dir / "calibration.lock"):
        return _record(run_dir, Path(result_path))


def _record(run_dir: Path, result_path: Path) -> dict:
    config, manifest, _, packets = _load(run_dir)
    result = Result.model_validate(_read(result_path))
    _validate_result(result, config, manifest, packets)
    destination = run_dir / "results" / f"{result.trial_id}.json"
    receipt = run_dir / "result_receipts" / destination.name
    recovering = destination.exists() and not receipt.exists()
    if recovering:
        orphan = Result.model_validate(_read(destination))
        if orphan != result:
            raise ValueError("orphan recovery requires the identical recorded result; the verdict cannot be replaced")
        # This validates the orphan's bindings, citations and session uniqueness
        # as well as every completed result, allowing only this absent receipt.
        results = _results(run_dir, config, manifest, packets, recoverable_orphan=result.trial_id)
    else:
        results = _results(run_dir, config, manifest, packets)
        if result.trial_id in results:
            raise ValueError("duplicate result; a recorded decision is immutable")
        if result.reviewer_session_id in {item.reviewer_session_id for item in results.values()}:
            raise ValueError("reviewer session reused across independent trials")
        _write(destination, result.model_dump(), exclusive=True)
    _write(
        receipt,
        {"trial_id": result.trial_id, "sha256": file_hash(destination)},
        exclusive=True,
    )
    action = "recovered" if recovering else "recorded"
    _event(run_dir, f"{action} trial {result.trial_id} provenance={result.provenance}")
    return {
        "recorded": result.trial_id,
        "recovered_receipt": recovering,
        "received_trials": len(results) + (0 if recovering else 1),
        "expected_trials": manifest["expected_trials"],
    }


def report(run_dir: str | Path) -> dict:
    """Fail closed on integrity errors; report missing work and sample insufficiency."""
    run_dir = Path(run_dir).resolve()
    with application_lease(run_dir / "calibration.lock"):
        return _report(run_dir)


def _report(run_dir: Path) -> dict:
    config, manifest, mapping, packets = _load(run_dir)
    results = _results(run_dir, config, manifest, packets)
    metrics = summarize(config, mapping, results)
    metrics.update(
        {
            "run_id": manifest["run_id"],
            "run_metadata_sha256": digest(manifest),
            "judge_config_sha256": manifest["judge_config_sha256"],
            "rubric_sha256": manifest["rubric_sha256"],
            "result_hashes": {trial: file_hash(run_dir / "results" / f"{trial}.json") for trial in results},
        }
    )
    _write(run_dir / "metrics.json", metrics)
    _event(run_dir, f"reported status={metrics['status']}; release_eligible={metrics['release_eligible']}")
    return metrics


@app.command("prepare")
def prepare_command(config: Annotated[Path, typer.Option(exists=True, dir_okay=False)]):
    """Create a run from YAML; config owns paths, budget, rubric and judge identity."""
    try:
        typer.echo(json.dumps({"run_dir": str(prepare(config))}))
    except (ValueError, OSError, KeyError, LeaseBusy) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("record")
def record_command(
    run: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    result: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
):
    """Record one independent judge response; paths identify task data, not overrides."""
    try:
        typer.echo(json.dumps(record(run, result)))
    except (ValueError, OSError, KeyError, LeaseBusy) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("report")
def report_command(run: Annotated[Path, typer.Option(exists=True, file_okay=False)]):
    """Write descriptive metrics; non-passing or fixture-only reports exit nonzero."""
    try:
        metrics = report(run)
        typer.echo(json.dumps(metrics, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, LeaseBusy) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not metrics["release_eligible"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
