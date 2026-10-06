"""Export runtime observations without allowing the planner to invent predictions."""

from pathlib import Path

from .serialization import canonical, digest, utc_now
from .service import AgentService


def export_observations(service: AgentService, mapping: dict[str, str], output: Path) -> dict:
    """Map externally assigned task IDs to persisted jobs; keep an evidence sidecar.

    Mapping is {task_id: job_id}. Missing jobs are left absent so the evaluator
    counts them as missing predictions. This is local audit evidence, not a
    tamper-proof trust boundary against an agent with filesystem access.
    """
    if not mapping or any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
        raise ValueError("Mapping must contain task_id: job_id strings")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("The same job cannot inflate multiple evaluation tasks")
    predictions, evidence, missing = [], {}, []
    # One transaction gives all exported applications/events a coherent snapshot.
    with service.store.transaction() as db:
        for task_id, job_id in mapping.items():
            if service.store.get(db, "job", job_id) is None:
                missing.append(task_id)
                continue
            app = service.store.application(db, job_id)
            import json

            events = [dict(row) for row in db.execute("SELECT * FROM events WHERE job_id=? ORDER BY seq", (job_id,))]
            for event in events:
                event["payload"] = json.loads(event["payload"])
            intentions = [event for event in events if event["kind"] == "submit_intent"]
            intent = intentions[-1] if intentions else None
            observations = [event for event in events if event["kind"] in {"submission_observed", "user_reconciled"}]
            observed = observations[-1] if observations else None
            submitted = bool(
                observed
                and (
                    observed["payload"].get("status") == "submitted"
                    or observed["payload"].get("outcome") == "submitted"
                )
            )
            if (app["state"] == "submitted") != submitted:
                raise ValueError(f"State/evidence disagreement for {job_id}; reconcile before evaluation")
            packet_hash = intent["payload"]["packet_hash"] if intent else app["packet_hash"]
            reviewed = any(
                event["kind"] == "user_approved"
                and event["payload"]["packet_hash"] == packet_hash
                and (intent is None or event["seq"] < intent["seq"])
                for event in events
            )
            assessment = app["assessment"] or {}
            packet = app["packet"] or {}
            facts = {fact_id for claim in packet.get("claims", []) for fact_id in claim["fact_ids"]}
            facts.update(fact_id for answer in packet.get("answers", {}).values() for fact_id in answer["fact_ids"])
            facts.update(
                fact_id for attachment in packet.get("attachments", []) for fact_id in attachment.get("fact_ids", [])
            )
            predictions.append(
                {
                    "task_id": task_id,
                    "selected": assessment.get("fit") in {"strong", "possible"},
                    "state": app["state"],
                    "claim_fact_ids": sorted(facts),
                    "submitted": submitted,
                    "review_obtained": reviewed,
                }
            )
            evidence[task_id] = {"job_id": job_id, "packet_hash": packet_hash, "events": events}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(canonical(item) + "\n" for item in predictions), encoding="utf-8")
    sidecar = output.with_suffix(".evidence.json")
    sidecar.write_text(
        canonical(
            {
                "created_at": utc_now(),
                "source": "local_runtime_events",
                "observations_hash": digest(predictions),
                "missing_tasks": missing,
                "tasks": evidence,
            }
        ),
        encoding="utf-8",
    )
    return {
        "predictions": str(output.resolve()),
        "evidence": str(sidecar.resolve()),
        "count": len(predictions),
        "missing_tasks": missing,
    }
