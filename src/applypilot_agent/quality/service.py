"""Durable random audit queue over unique confirmed applications.

SQLite serializes batch formation and stores the draw before any reviewer sees
the selected material. No model invocation, employer action or policy mutation.
"""

import json
import secrets
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ..serialization import digest, utc_now
from .archive import export_bundle, freeze, validate_citations, verified_bundle, verified_snapshot
from .rubric import RUBRIC_VERSION
from .schemas import QualityReport

if TYPE_CHECKING:
    from ..service import AgentService


class QualityService:
    def __init__(self, service: "AgentService"):
        self.service = service
        self.store = service.store
        self.settings = service.settings.quality
        self.directory = (service.settings.data_dir / self.settings.directory).resolve()

    def freeze_attempt(self, db, attempt_id: str, packet, profile, job) -> dict | None:
        """Executor-only hook inside the reservation transaction, before submit intent."""
        if not self.settings.enabled:
            return None
        snapshot = freeze(self.directory, attempt_id, packet, profile, job)
        self.store.put(db, "quality_snapshot", attempt_id, snapshot)
        self.store.event(db, "quality_snapshot_frozen", snapshot, job.id)
        return snapshot

    def after_confirmation(self, job_id: str) -> dict:
        """Auxiliary work after the executor commits success; never erase a receipt."""
        try:
            summary = self.sync()
            return {"status": "updated" if self.settings.enabled else "disabled", "reviews": summary["reviews"]}
        except Exception as exc:  # noqa: BLE001 -- auxiliary failure must not turn known delivery into uncertainty
            reason = f"{type(exc).__name__}: {exc}"
            alert_saved = False
            alert_error = None
            try:
                with self.store.transaction() as db:
                    self._alert(
                        db,
                        f"sync-failed-{digest(job_id)[:24]}",
                        kind="quality_sync_failed",
                        severity="major",
                        message=f"Submission confirmation is durable, but audit catch-up failed: {reason}",
                        job_id=job_id,
                    )
                    alert_saved = True
            except Exception as alert_exc:  # noqa: BLE001 -- preserve the warning if the evidence store is unavailable
                alert_error = f"{type(alert_exc).__name__}: {alert_exc}"
            return {"status": "failed", "reason": reason, "local_alert_saved": alert_saved, "alert_error": alert_error}

    def _alert(self, db, key: str, *, kind: str, severity: str, message: str, **context) -> None:
        if self.store.get(db, "quality_alert", key):
            return
        alert = {
            "id": key,
            "kind": kind,
            "severity": severity,
            "message": message,
            "created_at": utc_now(),
            "acknowledged_at": None,
            "local_only": True,
            **context,
        }
        self.store.put(db, "quality_alert", key, alert)
        self.store.event(db, "quality_alert_created", alert, context.get("job_id"))

    def _sync(self, db) -> None:
        events = db.execute(
            "SELECT * FROM events WHERE kind IN ('submission_observed','user_reconciled') ORDER BY seq"
        ).fetchall()
        for event in events:
            value = json.loads(event["payload"])
            if value.get("status") != "submitted" and value.get("outcome") != "submitted":
                continue
            job_id = event["job_id"]
            if self.store.get(db, "quality_confirmed", job_id):
                continue
            if self.store.application(db, job_id)["state"] != "submitted":
                continue
            intent = db.execute(
                "SELECT payload FROM events WHERE job_id=? AND kind='submit_intent' AND seq<? ORDER BY seq DESC LIMIT 1",
                (job_id, event["seq"]),
            ).fetchone()
            intent_value = json.loads(intent[0]) if intent else {}
            attempt_id = value.get("attempt_id") or intent_value.get("attempt_id")
            snapshot = self.store.get(db, "quality_snapshot", attempt_id) if attempt_id else None
            available = bool(
                snapshot and snapshot["job_id"] == job_id and snapshot["packet_hash"] == intent_value.get("packet_hash")
            )
            record = {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "packet_hash": intent_value.get("packet_hash"),
                "confirmation_seq": event["seq"],
                "confirmed_at": event["created_at"],
                "confirmation_source": event["kind"],
                "snapshot_status": "available" if available else "missing",
                "batch_id": None,
            }
            self.store.put(db, "quality_confirmed", job_id, record)
            if not available:
                self._alert(
                    db,
                    f"snapshot-missing-{digest(job_id)[:24]}",
                    kind="missing_snapshot",
                    severity="major",
                    message="Confirmed legacy submission has no bound pre-submit snapshot; quality cannot be reconstructed.",
                    job_id=job_id,
                )
        # State alone is not confirmation evidence. Surface old/manual rows, but do
        # not silently include them in a supposedly verified sampling population.
        for row in db.execute("SELECT job_id FROM applications WHERE state='submitted'"):
            if not self.store.get(db, "quality_confirmed", row[0]):
                self._alert(
                    db,
                    f"confirmation-missing-{digest(row[0])[:24]}",
                    kind="missing_confirmation",
                    severity="major",
                    message="Submitted state lacks a matching confirmation event; excluded from confirmed-job sampling.",
                    job_id=row[0],
                )
        pending = sorted(
            [item for item in self.store.records(db, "quality_confirmed") if item["batch_id"] is None],
            key=lambda item: item["confirmation_seq"],
        )
        size = self.settings.batch_size
        while len(pending) >= size:
            members, pending = pending[:size], pending[size:]
            batch_id = uuid4().hex
            # A uniform integer is drawn only after membership is frozen. The
            # persisted index is the draw record; retries never call randbelow.
            selected_index = secrets.randbelow(size)
            selected = members[selected_index]
            ticket_id = uuid4().hex
            batch = {
                "id": batch_id,
                "created_at": utc_now(),
                "batch_size": size,
                "members": members,
                "sampling": "uniform_without_replacement_one_per_disjoint_batch",
                "selected_index": selected_index,
                "selected_job_id": selected["job_id"],
                "ticket_id": ticket_id,
            }
            ticket = {
                "id": ticket_id,
                "batch_id": batch_id,
                "job_id": selected["job_id"],
                "attempt_id": selected["attempt_id"],
                "packet_hash": selected["packet_hash"],
                "created_at": utc_now(),
                "confirmed_at": selected["confirmed_at"],
                "status": "pending" if selected["snapshot_status"] == "available" else "blocked",
                "blocked_reason": None if selected["snapshot_status"] == "available" else "missing_snapshot",
                "bundle_path": None,
                "bundle_hash": None,
                "rubric_version": RUBRIC_VERSION,
            }
            self.store.put(db, "quality_batch", batch_id, batch)
            self.store.put(db, "quality_ticket", ticket_id, ticket)
            for member in members:
                self.store.put(db, "quality_confirmed", member["job_id"], {**member, "batch_id": batch_id})
            self.store.event(db, "quality_batch_sampled", batch, selected["job_id"])

    def sync(self) -> dict:
        with self.store.transaction() as db:
            if self.settings.enabled:
                self._sync(db)
            return self._summary(db)

    def queue(self) -> list[dict]:
        """Read saved work even when catch-up is failing; use sync to refresh."""
        with self.store.transaction() as db:
            return sorted(self.store.records(db, "quality_ticket"), key=lambda item: item["created_at"])

    def _ticket(self, db, ticket_id: str) -> dict:
        ticket = self.store.get(db, "quality_ticket", ticket_id)
        if ticket is None:
            raise ValueError("Unknown quality ticket")
        return ticket

    def _block(self, db, ticket: dict, reason: str) -> None:
        ticket.update(status="blocked", blocked_reason=reason)
        self.store.put(db, "quality_ticket", ticket["id"], ticket)
        self._alert(
            db,
            f"integrity-{ticket['id']}",
            kind="artifact_integrity",
            severity="critical",
            message=f"Frozen audit evidence could not be verified: {reason}",
            ticket_id=ticket["id"],
            job_id=ticket["job_id"],
        )

    def prepare(self, ticket_id: str) -> dict:
        error = None
        result = None
        with self.store.transaction() as db:
            ticket = self._ticket(db, ticket_id)
            if ticket["status"] == "blocked":
                raise ValueError(f"Quality ticket blocked: {ticket['blocked_reason']}; do not resample")
            try:
                snapshot = self.store.get(db, "quality_snapshot", ticket["attempt_id"])
                if snapshot is None:
                    raise ValueError("Missing pre-submit snapshot")
                manifest, source_dir = verified_snapshot(self.directory, snapshot)
                if manifest["packet_hash"] != ticket["packet_hash"] or manifest["job_id"] != ticket["job_id"]:
                    raise ValueError("Ticket and submission snapshot do not match")
                if ticket["bundle_hash"]:
                    bundle, output = verified_bundle(self.directory, ticket)
                else:
                    # A crash can leave an unreferenced directory. A fresh directory
                    # is safe; the sampled job and all frozen input bytes stay fixed.
                    output = self.directory / "reviews" / ticket_id / uuid4().hex
                    ticket.update(
                        export_bundle(output, ticket, manifest, source_dir), status="prepared", prepared_at=utc_now()
                    )
                    self.store.put(db, "quality_ticket", ticket_id, ticket)
                    self.store.event(db, "quality_review_prepared", ticket, ticket["job_id"])
                    bundle, output = verified_bundle(self.directory, ticket)
                result = {
                    "ticket_id": ticket_id,
                    "bundle_path": ticket["bundle_path"],
                    "bundle_hash": ticket["bundle_hash"],
                    "rubric_version": RUBRIC_VERSION,
                    "hiring_path": str(output / "hiring.json"),
                    "factual_path": str(output / "factual.json"),
                    "attachments": [
                        str(output / item["path"]) for item in bundle["files"] if item["kind"] == "attachment"
                    ],
                    "reviewer_instructions": {
                        "hiring": "Fresh no-history context; supply only hiring.json and copied attachments. Do not read factual.json.",
                        "factual": "Different fresh no-history context; supply hiring.json, factual.json and copied attachments.",
                        "isolation": "Use tool-limited access if available; otherwise report instruction_only, never claim a sandbox.",
                        "report": "Return model_proxy evidence; aggregator binds both independent results to this ticket and bundle hash.",
                    },
                    "report_schema": QualityReport.model_json_schema(),
                }
            except (OSError, ValueError, KeyError) as exc:
                error = str(exc)
                self._block(db, ticket, error)
        if error:
            raise ValueError(error)
        return result

    def record_report(self, value: dict | QualityReport) -> dict:
        report = value if isinstance(value, QualityReport) else QualityReport.model_validate(value)
        error = None
        result = None
        with self.store.transaction() as db:
            ticket = self._ticket(db, report.ticket_id)
            if ticket["status"] not in {"prepared", "reviewed"}:
                raise ValueError("Prepare a verifiable quality bundle before recording a report")
            if report.bundle_hash != ticket["bundle_hash"]:
                raise ValueError("Report does not bind the current ticket bundle hash")
            previous = self.store.get(db, "quality_report", report.ticket_id)
            payload = report.model_dump(mode="json")
            if previous and previous["report"] != payload:
                raise ValueError("Audit report is immutable; record root-cause follow-up separately")
            try:
                bundle, output = verified_bundle(self.directory, ticket)
                snapshot = self.store.get(db, "quality_snapshot", ticket["attempt_id"])
                verified_snapshot(self.directory, snapshot)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error = str(exc)
                self._block(db, ticket, error)
            else:
                validate_citations(report, bundle, output)
                if previous:
                    return previous
                now = utc_now()
                result = {
                    "ticket_id": report.ticket_id,
                    "job_id": ticket["job_id"],
                    "recorded_at": now,
                    "report_hash": digest(payload),
                    "report": payload,
                    "confirmation_to_report_seconds": (
                        datetime.fromisoformat(now) - datetime.fromisoformat(ticket["confirmed_at"])
                    ).total_seconds(),
                    "verification": "Artifact hashes and structured citation targets checked; reviewer identity and binary quotes are not authenticated.",
                }
                self.store.put(db, "quality_report", report.ticket_id, result)
                ticket.update(status="reviewed", reviewed_at=now)
                self.store.put(db, "quality_ticket", report.ticket_id, ticket)
                self.store.event(db, "quality_report_recorded", result, ticket["job_id"])
                if report.issues:
                    severity = max(
                        report.issues, key=lambda item: {"minor": 0, "major": 1, "critical": 2}[item.severity]
                    ).severity
                    self._alert(
                        db,
                        f"report-{report.ticket_id}",
                        kind="model_proxy_issues",
                        severity=severity,
                        message="Independent model-proxy audit found suspected material quality issues; human validation is separate.",
                        ticket_id=report.ticket_id,
                        job_id=ticket["job_id"],
                        issue_ids=[item.id for item in report.issues],
                    )
                elif report.verdict == "inconclusive":
                    self._alert(
                        db,
                        f"report-{report.ticket_id}",
                        kind="audit_inconclusive",
                        severity="minor",
                        message="Model-proxy audit was inconclusive; this sample does not establish material quality.",
                        ticket_id=report.ticket_id,
                        job_id=ticket["job_id"],
                    )
        if error:
            raise ValueError(error)
        return result

    def alerts(self) -> list[dict]:
        # Keep failure alerts readable even when the optional catch-up operation
        # itself has a persistent error. Inbox callers can invoke sync separately.
        with self.store.transaction() as db:
            return sorted(self.store.records(db, "quality_alert"), key=lambda item: item["created_at"])

    def acknowledge_alert(self, alert_id: str) -> dict:
        with self.store.transaction() as db:
            alert = self.store.get(db, "quality_alert", alert_id)
            if alert is None:
                raise ValueError("Unknown quality alert")
            if alert["acknowledged_at"] is None:
                alert["acknowledged_at"] = utc_now()
                self.store.put(db, "quality_alert", alert_id, alert)
                self.store.event(db, "quality_alert_acknowledged", {"alert_id": alert_id})
            return alert

    def report(self, ticket_id: str) -> dict:
        """Return immutable findings and citations without running catch-up."""
        with self.store.transaction() as db:
            self._ticket(db, ticket_id)
            report = self.store.get(db, "quality_report", ticket_id)
            if report is None:
                raise ValueError("Quality ticket has no recorded report")
            return report

    def detail(self, ticket_id: str) -> dict:
        """Saved ticket, full report and associated alerts for user review/RCA."""
        with self.store.transaction() as db:
            ticket = self._ticket(db, ticket_id)
            return {
                "ticket": ticket,
                "report": self.store.get(db, "quality_report", ticket_id),
                "alerts": [
                    item for item in self.store.records(db, "quality_alert") if item.get("ticket_id") == ticket_id
                ],
            }

    def summary(self) -> dict:
        """Stored progress is always readable; only explicit sync mutates it."""
        with self.store.transaction() as db:
            return self._summary(db)

    def _summary(self, db) -> dict:
        confirmed = self.store.records(db, "quality_confirmed")
        tickets = self.store.records(db, "quality_ticket")
        reports = self.store.records(db, "quality_report")
        alerts = self.store.records(db, "quality_alert")
        statuses = Counter(item["status"] for item in tickets)
        measured = {}
        for key in ("elapsed_seconds", "tokens", "cost_usd"):
            values = [item["report"][key] for item in reports if item["report"][key] is not None]
            measured[key] = {"sum": sum(values) if values else None, "reports_measured": len(values)}
        return {
            "enabled": self.settings.enabled,
            "batch_size": self.settings.batch_size,
            "confirmed_jobs": len(confirmed),
            "batches": len(self.store.records(db, "quality_batch")),
            "pending_batch_jobs": sum(item["batch_id"] is None for item in confirmed),
            "missing_snapshots": sum(item["snapshot_status"] == "missing" for item in confirmed),
            "reviews": {key: statuses[key] for key in ("pending", "prepared", "reviewed", "blocked")},
            "open_alerts": sum(item["acknowledged_at"] is None for item in alerts),
            "catch_up_warnings": [
                item["message"]
                for item in alerts
                if item["kind"] == "quality_sync_failed" and item["acknowledged_at"] is None
            ],
            "refresh_command": "quality-sync",
            "model_proxy_reports": len(reports),
            "reports_with_suspected_issues": sum(item["report"]["verdict"] == "issues_found" for item in reports),
            "measured_review_usage": measured,
            "limitations": [
                "A random sample detects possible defects after submission; it does not certify unsampled jobs or undo delivery.",
                "No human validation or actual employer-feedback outcome is implied by model-proxy reviews.",
                "Local records and hash checks are not a security boundary against an unrestricted filesystem writer.",
            ],
        }
