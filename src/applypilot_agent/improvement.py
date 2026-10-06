"""Evidence-bound RCA ledger for development. Recording never deploys a change."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from .improvement_evidence import verify_comparison
from .improvement_models import ImprovementProposal, ImprovementValidation
from .serialization import digest, utc_now
from .service import AgentService


def _regression_result(contents: bytes) -> dict:
    if b"<!DOCTYPE" in contents.upper() or b"<!ENTITY" in contents.upper():
        raise ValueError("Regression evidence must be plain JUnit XML")
    root = ElementTree.fromstring(contents)
    cases = list(root.iter("testcase"))
    failures = sum(bool(list(case.iter("failure"))) or bool(list(case.iter("error"))) for case in cases)
    skipped = sum(bool(list(case.iter("skipped"))) for case in cases)
    suite_errors = sum(int(suite.get("errors", "0")) for suite in root.iter("testsuite"))
    return {
        "tests": len(cases),
        "passed_case_names": [
            case.get("name", "")
            for case in cases
            if not any(list(case.iter(tag)) for tag in ("failure", "error", "skipped"))
        ],
        "failures": failures,
        "skipped": skipped,
        "passed": bool(cases) and len(cases) > skipped and failures == 0 and suite_errors == 0,
    }


class ImprovementService:
    def __init__(self, service: AgentService):
        self.service = service
        self.store = service.store

    def propose(self, value: dict, alert: dict) -> dict:
        proposal = ImprovementProposal.model_validate(value)
        if proposal.source_alert_id != alert.get("id", alert.get("alert_id")):
            raise ValueError("Root-cause proposal must reference the actual quality alert")
        if any(not item.strip() for item in proposal.supporting_evidence + proposal.regression_cases):
            raise ValueError("Evidence and regression cases must be nonempty")
        identity = digest({"proposal": proposal, "source_alert": alert})
        with self.store.transaction() as db:
            existing = self.store.get(db, "improvement_proposal", identity)
            if existing:
                return existing
            record = {
                "id": identity,
                "proposal": proposal.model_dump(),
                "source_alert": alert,
                "source_alert_hash": digest(alert),
                "created_at": utc_now(),
                "state": "proposed",
                "automatic_deployment": False,
            }
            self.store.put(db, "improvement_proposal", identity, record)
            self.store.event(db, "improvement_proposed", {"proposal_id": identity})
        return record

    def validate(self, value: dict) -> dict:
        validation = ImprovementValidation.model_validate(value)
        with self.store.transaction() as db:
            proposal = self.store.get(db, "improvement_proposal", validation.proposal_id)
        if proposal is None:
            raise ValueError("Unknown root-cause proposal")
        if validation.reviewer.casefold() == proposal["proposal"]["author"].casefold():
            raise ValueError("Validation needs a separately identified reviewer")
        if validation.baseline_revision == validation.candidate_revision:
            raise ValueError("Record distinct baseline and candidate revisions")
        regression_bytes = Path(validation.regression_report).read_bytes()
        evaluation_bytes = Path(validation.evaluation_report).read_bytes()
        try:
            regression = _regression_result(regression_bytes)
            evaluation = json.loads(evaluation_bytes)
        except (ElementTree.ParseError, json.JSONDecodeError) as exc:
            raise ValueError("Unreadable regression/evaluation evidence") from exc
        versions = evaluation.get("versions", {})
        comparison_present = "baseline" in versions and "candidate" in versions
        candidate_passed = versions.get("candidate", {}).get("gates", {}).get("passed") is True
        regression_cases_passed = set(proposal["proposal"]["regression_cases"]).issubset(
            regression["passed_case_names"]
        )
        snapshots = evaluation.get("inputs", {})
        if not snapshots or any(not item.get("sha256") for item in snapshots.values()):
            raise ValueError("Evaluation evidence must identify the frozen inputs")
        verified_inputs = verify_comparison(evaluation, Path(validation.evaluation_report))
        if validation.decision == "accept" and (
            not regression["passed"]
            or not regression_cases_passed
            or not candidate_passed
            or not comparison_present
            or proposal["proposal"]["root_cause"] == "unknown"
        ):
            raise ValueError(
                "Acceptance requires a diagnosed cause, passing named regression cases and paired candidate gates"
            )
        # Keep exact evidence bytes, so a later rewrite of a report does not change this review.
        identifier = uuid4().hex
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        directory = self.service.settings.logs_dir / f"{stamp}_improvement_validation"
        directory.mkdir(parents=True, exist_ok=False)
        paths = {"regression": directory / "regression.xml", "evaluation": directory / "evaluation.json"}
        for name, contents in (("regression", regression_bytes), ("evaluation", evaluation_bytes)):
            paths[name].write_bytes(contents)
        (directory / "inputs").mkdir()
        for name, contents in verified_inputs.items():
            paths[name] = directory / "inputs" / f"{name}.jsonl"
            paths[name].write_bytes(contents)
        record = {
            "id": identifier,
            "validation": validation.model_dump(),
            "created_at": utc_now(),
            "regression": regression,
            "candidate_gates_passed": candidate_passed,
            "evaluation_scope": evaluation.get("evaluation_scope", {}),
            "evidence": {
                name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for name, path in paths.items()
            },
            "automatic_deployment": False,
            "limitations": [
                "Reviewer identities and code revisions are declared development metadata, not authentication.",
                "Synthetic evaluation and passing unit tests do not establish real recruiting quality.",
                "This record never edits or deploys code, skills, policy, evaluation labels or applications.",
            ],
        }
        with self.store.transaction() as db:
            self.store.put(db, "improvement_validation", identifier, record)
            self.store.event(db, "improvement_validated", record)
        return record

    def records(self) -> dict:
        with self.store.transaction() as db:
            return {
                "proposals": self.store.records(db, "improvement_proposal"),
                "validations": self.store.records(db, "improvement_validation"),
            }
