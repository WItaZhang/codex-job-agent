"""Guarded external actions. Intent is durable before any submit click."""

import json
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from .lease import LeaseBusy, application_lease
from .models import Assessment, Packet
from .policy import route
from .quality import QualityService
from .serialization import canonical, digest, utc_now
from .service import LOCKED_STATES, AgentService


class Executor:
    def __init__(self, service: AgentService, session_factory=None):
        self.service = service
        self.store = service.store
        if session_factory is None:
            from .browser import BrowserSession

            session_factory = BrowserSession
        self.session_factory = session_factory
        self._owns_lease = ContextVar(f"executor_lease_{id(self)}", default=False)

    def authorize(
        self, job_id: str, *, reserve: bool = False, expected_packet_hash: str | None = None
    ) -> tuple[Packet, str | None]:
        if reserve and not self._owns_lease.get():
            raise ValueError("Submission reservation is internal to a leased execute operation")
        with self.store.transaction() as db:
            app = self.store.application(db, job_id)
            profile, job = self.service.profile(db), self.service.job(db, job_id)
            if not app["packet"] or app["state"] in LOCKED_STATES:
                raise ValueError("No executable packet or application requires reconciliation")
            packet = Packet.model_validate(app["packet"])
            if expected_packet_hash and digest(packet) != expected_packet_hash:
                raise ValueError("Packet changed during browser preparation; prepare the new version")
            self.service.validate_packet(db, profile, job, packet, app)
            if not packet.browser_plan:
                raise ValueError("Inspect the form and attach a browser plan first")
            assessment = Assessment.model_validate(app["assessment"])
            decision = route(profile, job, assessment, self.service.settings.policy, packet)
            approval = app["approval"]
            approved = (
                approval
                and approval["packet_hash"] == digest(packet)
                and approval["policy_hash"] == digest(self.service.settings.policy)
            )
            if decision.action in {"skip", "clarify"}:
                raise ValueError(f"Application blocked: {decision.reasons}")
            if not approved and decision.action != "auto":
                raise ValueError("The current packet needs user approval")
            if not approved:
                for attachment in packet.attachments:
                    record = self.store.get(db, "rendered_artifact", attachment.sha256)
                    if not record or record["profile_hash"] != packet.profile_hash:
                        raise ValueError("External attachment needs user review before automatic submission")
                    if record["fact_ids"] != attachment.fact_ids:
                        raise ValueError(
                            "Attachment fact IDs differ from rendered evidence; rebuild the packet "
                            "from the current render output"
                        )
            day = utc_now()[:10]
            count = db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='submit_intent' AND substr(created_at,1,10)=?", (day,)
            ).fetchone()[0]
            if count >= self.service.settings.policy.daily_submission_limit:
                raise ValueError("Daily submission attempt budget exhausted (UTC day)")
            if not reserve:
                return packet, None
            attempt = uuid4().hex
            QualityService(self.service).freeze_attempt(db, attempt, packet, profile, job)
            self.store.put(db, "attempt", job_id, {"id": attempt, "packet_hash": digest(packet)})
            self.store.update_application(db, job_id, state="submitting", last_error=None)
            self.store.event(db, "submit_intent", {"attempt_id": attempt, "packet_hash": digest(packet)}, job_id)
            return packet, attempt

    def _session(self, evidence_dir: Path):
        settings = self.service.settings.browser
        return self.session_factory(
            headless=settings.headless,
            timeout_ms=settings.timeout_ms,
            evidence_dir=evidence_dir,
            allowed_origins=settings.allowed_origins or None,
            storage_state=settings.storage_state,
            executable_path=settings.executable_path,
        )

    def inspect(self, job_id: str) -> dict:
        context = self.service.context(job_id)
        output = self.service.settings.data_dir / "observations" / digest(job_id)[:16] / uuid4().hex[:16]
        output.mkdir(parents=True, exist_ok=True)
        try:
            with self._session(output) as browser:
                observed = browser.inspect(context["job"]["apply_url"])
        except RuntimeError as exc:
            self._blocked(job_id, str(exc))
            raise
        with self.store.transaction() as db:
            self.store.event(db, "form_inspected", observed, job_id)
        return observed

    def execute(self, job_id: str, *, dry_run: bool = True) -> dict:
        with application_lease(self._lease_path(job_id)):
            token = self._owns_lease.set(True)
            try:
                return self._execute(job_id, dry_run=dry_run)
            finally:
                self._owns_lease.reset(token)

    def _lease_path(self, job_id: str) -> Path:
        return self.service.settings.data_dir / "leases" / f"{digest(job_id)}.lock"

    def _execute(self, job_id: str, *, dry_run: bool = True) -> dict:
        from .browser import BrowserPlan

        if dry_run:
            context = self.service.context(job_id)
            if not context["application"]["packet"]:
                raise ValueError("Prepare an application packet first")
            packet = Packet.model_validate(context["application"]["packet"])
            with self.store.transaction() as db:
                self.service.validate_packet(
                    db,
                    self.service.profile(db),
                    self.service.job(db, job_id),
                    packet,
                    self.store.application(db, job_id),
                )
        else:
            packet, _ = self.authorize(job_id)
        plan = BrowserPlan.model_validate(packet.browser_plan)
        output = self.service.settings.data_dir / "observations" / digest(job_id)[:16] / uuid4().hex[:16]
        output.mkdir(parents=True, exist_ok=True)
        with self._session(output) as browser:
            try:
                prepared = browser.prepare(plan)
            except RuntimeError as exc:
                self._blocked(job_id, str(exc))
                raise
            if dry_run:
                with self.store.transaction() as db:
                    self.store.event(db, "dry_run_prepared", prepared, job_id)
                return {"status": "prepared", "submitted": False, "observation": prepared}
            # Preparation can take minutes. Recheck all versions and budget immediately
            # before reservation; concurrent attempts serialize here.
            _, attempt = self.authorize(job_id, reserve=True, expected_packet_hash=digest(packet))
            try:
                result = browser.submit(plan)
            except Exception as exc:  # noqa: BLE001 -- any post-intent failure has an uncertain external outcome
                result = {"status": "unknown", "reason": f"{type(exc).__name__}: {exc}"}
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            evidence_path = output / "result.json"
            evidence_path.write_text(canonical(result), encoding="utf-8")
            return self._finish(job_id, attempt, {**result, "evidence_path": str(evidence_path)})

    def _finish(self, job_id: str, attempt: str, result: dict) -> dict:
        # Only the browser adapter supplies this result. There is no public mark-success command.
        result = dict(result)
        if "attempt_id" in result:
            result["browser_attempt_id"] = result.pop("attempt_id")
        state = "submitted" if result.get("status") == "submitted" else "unknown"
        with self.store.transaction() as db:
            record = self.store.get(db, "attempt", job_id)
            if not record or record["id"] != attempt:
                raise ValueError("Submission attempt ownership changed")
            self.store.update_application(
                db, job_id, state=state, last_error=result.get("reason") if state == "unknown" else None
            )
            self.store.event(db, "submission_observed", {"attempt_id": attempt, **result}, job_id)
        if state == "submitted":
            result["quality"] = QualityService(self.service).after_confirmation(job_id)
        return {"state": state, "attempt_id": attempt, **result}

    def _blocked(self, job_id: str, reason: str):
        with self.store.transaction() as db:
            app = self.store.application(db, job_id)
            if app["state"] not in LOCKED_STATES:
                self.store.update_application(db, job_id, state="needs_info", last_error=reason)
            self.store.event(db, "browser_blocked", {"reason": reason}, job_id)

    def recover(self) -> dict:
        """An interrupted intent is uncertain, never automatically safe to retry."""
        with self.store.transaction() as db:
            rows = db.execute("SELECT job_id FROM applications WHERE state='submitting'").fetchall()
        recovered, active = [], []
        for row in rows:
            try:
                with application_lease(self._lease_path(row[0])), self.store.transaction() as db:
                    if self.store.application(db, row[0])["state"] != "submitting":
                        continue
                    self.store.update_application(
                        db, row[0], state="unknown", last_error="Interrupted submission intent"
                    )
                    self.store.event(db, "recovery_requires_reconciliation", {}, row[0])
                    recovered.append(row[0])
            except LeaseBusy:
                active.append(row[0])
        return {"unknown": recovered, "active": active}

    def reconcile(self, job_id: str, evidence_path: Path, outcome: str, user_note: str) -> dict:
        """Record user-verified external evidence. Never infer absence from a timeout."""
        with application_lease(self._lease_path(job_id)):
            return self._reconcile(job_id, evidence_path, outcome, user_note)

    def _reconcile(self, job_id: str, evidence_path: Path, outcome: str, user_note: str) -> dict:
        if outcome not in {"submitted", "not_submitted"} or not user_note.strip():
            raise ValueError("Reconciliation requires an outcome and the user's verification instruction")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
        if not isinstance(evidence, dict) or not evidence.get("source") or not evidence.get("observation"):
            raise ValueError("Evidence must contain source and observation")
        with self.store.transaction() as db:
            app = self.store.application(db, job_id)
            if app["state"] != "unknown":
                raise ValueError("Only unknown outcomes may be reconciled")
            state = "submitted" if outcome == "submitted" else "retryable"
            self.store.update_application(db, job_id, state=state, approval=None, last_error=None)
            self.store.event(
                db, "user_reconciled", {"outcome": outcome, "evidence": evidence, "user_note": user_note}, job_id
            )
        result = {"state": state, "verification": "user_attested"}
        if state == "submitted":
            result["quality"] = QualityService(self.service).after_confirmation(job_id)
        return result
