"""Run real browser/application mechanics against a synthetic local employer.

No model or personal account is required. The synthetic assessments exercise
Codex's input contract; this demonstration does not claim to evaluate a model.
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from .browser import BrowserField, BrowserPlan
from .config import Settings
from .demo_support import DemoConfig, DemoScenario, LocalATS, load_demo_config
from .execution import Executor
from .models import Answer, Assessment, Attachment, Claim, Job, Packet, Policy
from .profile_evidence import profile_evidence
from .serialization import utc_now
from .service import AgentService


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _prepare_packet(service: AgentService, executor: Executor, config: DemoConfig, job_id: str) -> dict:
    context = service.context(job_id)
    service.assess(
        job_id,
        Assessment(
            job_id=job_id,
            job_hash=context["job_hash"],
            profile_hash=context["profile_hash"],
            fit="strong",
            eligibility="pass",
            source="codex",
            reasons=["Synthetic contract input: Python evidence supports the fixture's data-engineering requirement."],
            evidence_fact_ids=config.resume_fact_ids,
            priority=1.0,
        ),
    )
    inspection = executor.inspect(job_id)
    _require(len(inspection["fields"]) >= 3, "The real browser did not observe the expected local form")
    rendered = service.render(job_id, config.resume_fact_ids, pdf=config.render_pdf)
    facts = {fact.id: fact for fact in profile_evidence(config.profile)}
    facts_by_key = {
        "name": facts[f"{config.profile.personal.id}.full_name"],
        "email": facts[f"{config.profile.personal.id}.email"],
    }
    answers = {
        "#name": Answer(value=facts_by_key["name"].text, fact_ids=[facts_by_key["name"].id]),
        "#email": Answer(value=facts_by_key["email"].text, fact_ids=[facts_by_key["email"].id]),
    }
    plan = BrowserPlan(
        url=context["job"]["apply_url"],
        fields=[
            *[BrowserField(selector=selector, kind="text", value=answer.value) for selector, answer in answers.items()],
            BrowserField(selector="#resume", kind="file", value=rendered["attachment"]["path"]),
        ],
        submit_selector="#submit",
        confirmation_selector="#receipt",
        confirmation_text="Application received:",
    )
    packet = Packet(
        job_id=job_id,
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        answers=answers,
        claims=[Claim(text=facts[fact_id].text, fact_ids=[fact_id]) for fact_id in config.resume_fact_ids],
        attachments=[Attachment.model_validate(rendered["attachment"])],
        browser_plan=plan.model_dump(mode="json"),
    )
    return {"saved": service.save_packet(packet), "packet": packet, "inspection": inspection}


def _run_scenario(
    service: AgentService,
    executor: Executor,
    ats: LocalATS,
    config: DemoConfig,
    scenario: DemoScenario,
    logger: logging.Logger,
) -> dict:
    service.settings.policy = Policy(
        auto_fit=["strong"] if scenario.authorization == "auto" else [],
        allowed_domains=[config.bind_host],
        daily_submission_limit=config.daily_submission_limit,
        require_review_for_rewrites=True,
    )
    prepared = _prepare_packet(service, executor, config, scenario.id)
    packet, saved = prepared["packet"], prepared["saved"]
    notes = []
    logger.info("%s packet=%s initial_state=%s", scenario.id, saved["packet_hash"], saved["state"])
    if scenario.authorization == "review":
        _require(saved["state"] == "review", "Default scope must request user review")
        dry_run = executor.execute(scenario.id, dry_run=True)
        _require(dry_run["submitted"] is False and not ats.entries(scenario.id), "Dry run performed an external write")
        _require(service.context(scenario.id)["application"]["state"] == "review", "Dry run marked completion")
        try:
            executor.execute(scenario.id, dry_run=False)
        except ValueError as exc:
            notes.append(f"Before approval: {exc}")
        else:
            raise AssertionError("Review-required packet submitted without approval")
        _require(not ats.entries(scenario.id), "Blocked submission reached the server")
        approval = service.approve(
            scenario.id,
            saved["packet_hash"],
            "Synthetic test authorization of this exact packet; no real user involved.",
        )
        _require(approval["packet_hash"] == saved["packet_hash"], "Approval hash mismatch")
        notes.append("Dry run made zero POSTs. Synthetic approval was bound to the exact packet hash.")
    else:
        _require(saved["state"] == "ready", "Authorized synthetic match did not route automatically")
        _require(service.context(scenario.id)["application"]["approval"] is None, "Automatic case gained an approval")
        notes.append("Verbatim confirmed facts and the configured scope authorized automatic execution.")

    result = executor.execute(scenario.id, dry_run=False)
    app = service.context(scenario.id)["application"]
    entries = ats.entries(scenario.id)
    _require(len(entries) == 1, "Each scenario must reach the synthetic employer exactly once")
    entry = entries[0]
    _require(entry["fields"]["full_name"] == packet.answers["#name"].value, "Server received a different name")
    _require(entry["fields"]["email"] == packet.answers["#email"].value, "Server received a different email")
    _require(entry["files"]["resume"]["sha256"] == packet.attachments[0].sha256, "Uploaded bytes differ from approval")
    if scenario.receipt:
        _require(result["state"] == app["state"] == "submitted", "Observed receipt did not become submitted")
        _require(entry["receipt_id"] in result["receipt"]["text"], "Browser receipt differs from server ledger")
        notes.append("Browser receipt, received attachment hash and persisted submission agree.")
    else:
        _require(result["state"] == app["state"] == "unknown", "Missing receipt was incorrectly called success")
        _require(result.get("receipt") is None, "Missing-receipt fixture unexpectedly produced a receipt")
        try:
            executor.execute(scenario.id, dry_run=False)
        except ValueError as exc:
            notes.append(f"Automatic retry blocked: {exc}")
        else:
            raise AssertionError("Unknown outcome was retried")
        _require(len(ats.entries(scenario.id)) == 1, "Retry created a duplicate application")
        notes.append(
            "Server accepted the POST, but the agent could not observe confirmation and preserved uncertainty."
        )
    events = service.store.events(scenario.id)
    intents = [event for event in events if event["kind"] == "submit_intent"]
    _require(len(intents) == 1, "Each scenario must have exactly one durable submission intent")
    logger.info("%s final_state=%s employer_posts=%s", scenario.id, app["state"], len(entries))
    return {
        "job_id": scenario.id,
        "title": scenario.title,
        "authorization": scenario.authorization,
        "packet_hash": saved["packet_hash"],
        "state": app["state"],
        "employer_posts": len(entries),
        "employer_receipt_id": entry["receipt_id"],
        "attachment_hash_verified": True,
        "notes": notes,
        "observation": result,
    }


def _write_overview(path: Path, summary: dict) -> None:
    cards = []
    for scenario in summary["scenarios"]:
        bullets = "".join(f"<li>{escape(note)}</li>" for note in scenario["notes"])
        image = scenario["observation"].get("after_screenshot")
        screenshot = (
            f'<img alt="Observed synthetic ATS after submission" src="{Path(image).as_uri()}">' if image else ""
        )
        cards.append(
            f"<article><h2>{escape(scenario['title'])}</h2><p><b>{escape(scenario['state'])}</b> · "
            f"{scenario['employer_posts']} employer POST · attachment bytes verified</p><ul>{bullets}</ul>{screenshot}</article>"
        )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>ApplyPilot agent — executable demo</title>"
        "<style>body{max-width:1050px;margin:40px auto;padding:0 20px;background:#f4f7fa;color:#183047;"
        "font:16px/1.6 system-ui}article{background:white;border:1px solid #d8e2ec;border-radius:12px;"
        "padding:24px;margin:22px 0}img{max-width:100%;max-height:600px;border:1px solid #e1e7ee}"
        "h1{font-size:30px}h2{font-size:21px}code{overflow-wrap:anywhere}</style></head><body>"
        "<p>EXECUTABLE LOCAL DEMO · SYNTHETIC DATA</p><h1>Three application boundaries, observed in Chromium</h1>"
        "<p>Real browser, HTTP submissions and SQLite state. The employer, identity, assessments and approval "
        "are fixtures; this does not establish real recruiting quality or general ATS compatibility.</p>"
        + "".join(cards)
        + f"<p>Independent employer ledger: <code>{escape(summary['ledger_path'])}</code></p></body></html>",
        encoding="utf-8",
    )


def run_demo(config_path: str | Path) -> dict:
    config_path = Path(config_path).resolve()
    config = load_demo_config(config_path)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f") + "_" + config.experiment_name
    logs, state = config.logs_dir / run_id, config.state_dir / run_id
    logs.mkdir(parents=True, exist_ok=False)
    state.mkdir(parents=True, exist_ok=False)
    (logs / "config.yaml").write_bytes(config_path.read_bytes())
    logger = logging.getLogger(f"applypilot.demo.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = [logging.FileHandler(logs / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)]
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    try:
        service = AgentService(Settings(data_dir=state, logs_dir=logs, browser=config.browser))
        service.save_profile(config.profile)
        executor = Executor(service)
        ledger_path = logs / "employer-ledger.jsonl"
        with LocalATS(config, ledger_path) as ats:
            service.import_jobs(
                [
                    Job(
                        id=scenario.id,
                        source="synthetic-demo",
                        source_id=scenario.id,
                        url=f"{ats.origin}/apply/{scenario.id}",
                        apply_url=f"{ats.origin}/apply/{scenario.id}",
                        title=scenario.title,
                        company=scenario.company,
                        description=scenario.description,
                        fetched_at=utc_now(),
                    )
                    for scenario in config.scenarios
                ]
            )
            logger.info("Local ATS %s; isolated state %s", ats.origin, state)
            scenarios = [
                _run_scenario(service, executor, ats, config, scenario, logger) for scenario in config.scenarios
            ]
            _require(len(ats.entries()) == len(config.scenarios), "Unexpected total employer write count")
        summary = {
            "synthetic": True,
            "live_model_evaluated": False,
            "passed": True,
            "run_id": run_id,
            "logs_dir": str(logs),
            "state_dir": str(state),
            "database_path": str(service.store.path),
            "ledger_path": str(ledger_path),
            "overview_path": str(logs / "overview.html"),
            "scenarios": scenarios,
        }
        (logs / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (logs / "events.json").write_text(json.dumps(service.store.events(), indent=2) + "\n", encoding="utf-8")
        _write_overview(logs / "overview.html", summary)
        logger.info("All three scenarios passed. Overview: %s", logs / "overview.html")
        return summary
    except Exception:
        logger.exception("Executable demo failed")
        raise
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Demonstrate guarded applications against a synthetic loopback ATS")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run_demo(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
