"""Application operations; domain validation and state changes share transactions."""

from pathlib import Path

from .config import Settings
from .matching import baseline, check_constraints
from .materials import render_resume, validate_evidence
from .models import Assessment, Job, Packet, Profile
from .policy import route
from .serialization import destination_key, digest, job_digest, utc_now
from .store import Store

LOCKED_STATES = {"submitting", "unknown", "submitted"}


class AgentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir)

    def profile(self, db=None) -> Profile:
        if db is None:
            with self.store.transaction() as connection:
                return self.profile(connection)
        data = self.store.get(db, "profile", "active")
        if data is None:
            raise ValueError("No profile. Complete onboarding before matching or applying.")
        return Profile.model_validate(data)

    def save_profile(self, profile: Profile) -> dict:
        with self.store.transaction() as db:
            self.store.put(db, "profile", "active", profile)
            self.store.put(db, "profile_version", digest(profile), profile)
            self.store.event(db, "profile_updated", {"profile_hash": digest(profile)})
            # Preserve packets for review; their version check will prevent reuse.
            db.execute("UPDATE applications SET approval=NULL WHERE state NOT IN ('submitted','submitting','unknown')")
        return {"profile_hash": digest(profile), "confirmed_facts": sum(f.confirmed for f in profile.facts)}

    def import_jobs(self, jobs: list[Job]) -> dict:
        aliases, observed = {}, set()
        with self.store.transaction() as db:
            destinations = {destination_key(item["apply_url"]): item["id"] for item in self.store.records(db, "job")}
            for job in jobs:
                target = destination_key(job.apply_url)
                alias = self.store.get(db, "job_alias", job.id)
                existing = alias or destinations.get(target)
                if alias and destinations.get(target) not in (None, alias):
                    raise ValueError("Changed application destination conflicts with another known job")
                if existing and existing != job.id:
                    aliases[job.id] = existing
                    self.store.put(db, "job_alias", job.id, existing)
                    self.store.put(db, "job_version", job_digest(job), job)
                    self.store.event(
                        db, "duplicate_destination_observed", {"alias": job.id, "source": job.source}, existing
                    )
                    canonical_job = self.store.get(db, "job", existing)
                    identity = {
                        "id": existing,
                        "source": canonical_job["source"],
                        "source_id": canonical_job["source_id"],
                    }
                    if destination_key(canonical_job["apply_url"]) == target:
                        identity.update(url=canonical_job["url"], apply_url=canonical_job["apply_url"])
                    job = job.model_copy(update=identity)
                destinations[target] = job.id
                observed.add(job.id)
                previous = self.store.get(db, "job", job.id)
                self.store.put(db, "job", job.id, job)
                self.store.put(db, "job_version", job_digest(job), job)
                db.execute(
                    "INSERT OR IGNORE INTO applications(job_id,state,updated_at) VALUES(?,?,?)",
                    (job.id, "discovered", utc_now()),
                )
                if previous and job_digest(Job.model_validate(previous)) != job_digest(job):
                    app = self.store.application(db, job.id)
                    if app["state"] not in LOCKED_STATES:
                        self.store.update_application(db, job.id, state="discovered", assessment=None, approval=None)
                self.store.event(db, "job_observed", {"job_hash": job_digest(job)}, job.id)
        return {"imported": len(observed), "aliases": aliases}

    def job(self, db, job_id: str) -> Job:
        value = self.store.get(db, "job", job_id)
        if value is None:
            raise ValueError(f"Unknown job: {job_id}")
        return Job.model_validate(value)

    def context(self, job_id: str) -> dict:
        with self.store.transaction() as db:
            profile, job = self.profile(db), self.job(db, job_id)
            return {
                "profile": profile.model_dump(),
                "profile_hash": digest(profile),
                "job": job.model_dump(),
                "job_hash": job_digest(job),
                "application": self.store.application(db, job_id),
                "feedback": self.store.get(db, "feedback", job_id),
                "policy": self.settings.policy.model_dump(),
            }

    def assess(self, job_id: str, assessment: Assessment | None = None) -> dict:
        with self.store.transaction() as db:
            profile, job = self.profile(db), self.job(db, job_id)
            app = self.store.application(db, job_id)
            if app["state"] in LOCKED_STATES:
                raise ValueError(f"Cannot reassess application in state {app['state']}")
            assessment = assessment or baseline(profile, job, self.settings.matching)
            if (
                assessment.job_id != job_id
                or assessment.job_hash != job_digest(job)
                or assessment.profile_hash != digest(profile)
            ):
                raise ValueError("Assessment refers to stale or different inputs")
            known = {
                fact.id
                for fact in profile.facts
                if fact.confirmed and (not fact.scope_job_ids or job.id in fact.scope_job_ids)
            }
            if set(assessment.evidence_fact_ids) - known:
                raise ValueError("Assessment references unconfirmed or missing facts")
            eligibility, issues = check_constraints(profile, job)
            if eligibility != "pass" and assessment.eligibility != eligibility:
                raise ValueError(f"Assessment contradicts explicit constraints: {issues}")
            feedback = self.store.get(db, "feedback", job_id)
            if feedback and feedback["interested"] == "no":
                assessment.fit = "no"
                assessment.reasons.append(f"User declined: {feedback['reason']}")
            decision = route(profile, job, assessment, self.settings.policy)
            state = {"skip": "skipped", "clarify": "needs_info"}.get(decision.action, "assessed")
            self.store.update_application(db, job_id, state=state, assessment=assessment, approval=None)
            self.store.event(db, "assessed", {"assessment": assessment, "decision": decision.model_dump()}, job_id)
            return {"assessment": assessment.model_dump(), "decision": decision.model_dump(), "state": state}

    def save_packet(self, packet: Packet) -> dict:
        with self.store.transaction() as db:
            profile, job = self.profile(db), self.job(db, packet.job_id)
            app = self.store.application(db, packet.job_id)
            if app["state"] in LOCKED_STATES:
                raise ValueError(f"Cannot replace packet in state {app['state']}")
            self.validate_packet(db, profile, job, packet, app)
            decision = route(profile, job, Assessment.model_validate(app["assessment"]), self.settings.policy, packet)
            state = {"skip": "skipped", "clarify": "needs_info", "review": "review", "auto": "ready"}[decision.action]
            self.store.update_application(
                db, job.id, state=state, packet=packet, packet_hash=digest(packet), approval=None
            )
            self.store.event(
                db, "packet_saved", {"packet_hash": digest(packet), "decision": decision.model_dump()}, job.id
            )
            return {"packet_hash": digest(packet), "state": state, "decision": decision.model_dump()}

    def validate_packet(self, db, profile: Profile, job: Job, packet: Packet, app: dict) -> None:
        if packet.job_id != job.id or packet.profile_hash != digest(profile) or packet.job_hash != job_digest(job):
            raise ValueError("Packet refers to stale profile or job; rebuild and review")
        if not app["assessment"]:
            raise ValueError("Assess the job before preparing a packet")
        assessment = Assessment.model_validate(app["assessment"])
        if assessment.profile_hash != digest(profile) or assessment.job_hash != job_digest(job):
            raise ValueError("Assessment is stale; reassess before preparing")
        validate_evidence(profile, packet)
        if packet.browser_plan:
            from .browser import BrowserPlan

            plan = BrowserPlan.model_validate(packet.browser_plan)
            if plan.url != job.apply_url:
                raise ValueError("Browser destination must equal the observed application URL")
            attachments = {str(Path(item.path).resolve()) for item in packet.attachments}
            for field in plan.fields:
                if field.kind == "file":
                    if str(Path(str(field.value)).resolve()) not in attachments:
                        raise ValueError("Browser upload is not included in the versioned packet")
                elif field.selector not in packet.answers or packet.answers[field.selector].value != field.value:
                    raise ValueError(f"Browser field lacks a matching evidenced answer: {field.selector}")

    def approve(self, job_id: str, packet_hash: str, note: str) -> dict:
        if not note.strip():
            raise ValueError("Record the user's approval instruction")
        with self.store.transaction() as db:
            profile, job = self.profile(db), self.job(db, job_id)
            app = self.store.application(db, job_id)
            if app["state"] in LOCKED_STATES or not app["packet"]:
                raise ValueError("Application cannot be approved in its current state")
            packet = Packet.model_validate(app["packet"])
            self.validate_packet(db, profile, job, packet, app)
            if digest(packet) != packet_hash:
                raise ValueError("Approval must reference the exact current packet hash")
            decision = route(profile, job, Assessment.model_validate(app["assessment"]), self.settings.policy, packet)
            if decision.action in ("skip", "clarify"):
                raise ValueError("Resolve eligibility and missing information before approval")
            approval = {
                "packet_hash": packet_hash,
                "policy_hash": digest(self.settings.policy),
                "note": note,
                "created_at": utc_now(),
            }
            self.store.update_application(db, job_id, state="ready", approval=approval)
            self.store.event(db, "user_approved", approval, job_id)
            return approval

    def render(self, job_id: str, fact_ids: list[str], *, pdf: bool = True) -> dict:
        context = self.context(job_id)
        profile = Profile.model_validate(context["profile"])
        scoped_facts = {
            fact.id
            for fact in profile.facts
            if fact.confirmed and (not fact.scope_job_ids or job_id in fact.scope_job_ids)
        }
        if set(fact_ids) - scoped_facts:
            raise ValueError("Selected facts are unconfirmed or do not apply to this job")
        version = digest({"profile": context["profile_hash"], "facts": fact_ids, "pdf": pdf})
        output = self.settings.data_dir / "artifacts" / digest(job_id)[:16] / version[:16]
        output.mkdir(parents=True, exist_ok=True)
        manifest = output / "identity.json"
        identity = {"job_id": job_id, "version": version}
        if manifest.exists():
            import json

            if json.loads(manifest.read_text(encoding="utf-8")) != identity:
                raise ValueError("Artifact directory identity collision")
        else:
            from .serialization import canonical

            manifest.write_text(canonical(identity), encoding="utf-8")
        result = render_resume(profile, fact_ids, output, pdf=pdf)
        with self.store.transaction() as db:
            self.store.put(
                db,
                "rendered_artifact",
                result["attachment"]["sha256"],
                {"profile_hash": digest(profile), "fact_ids": fact_ids, "job_id": job_id},
            )
            self.store.event(db, "material_rendered", result, job_id)
        return result

    def list_jobs(self) -> list[dict]:
        with self.store.transaction() as db:
            return [
                {
                    "job": job,
                    "application": self.store.application(db, job["id"]),
                    "feedback": self.store.get(db, "feedback", job["id"]),
                }
                for job in self.store.records(db, "job")
            ]

    def record_feedback(self, job_id: str, interested: str, reason: str, high_priority: bool = False) -> dict:
        if interested not in {"yes", "no", "unknown"} or not reason.strip():
            raise ValueError("Feedback needs yes/no/unknown and the user's reason")
        with self.store.transaction() as db:
            profile, job = self.profile(db), self.job(db, job_id)
            record = {
                "job_id": job_id,
                "job_hash": job_digest(job),
                "profile_hash": digest(profile),
                "interested": interested,
                "high_priority": high_priority,
                "reason": reason,
                "provenance": "user_reported",
                "created_at": utc_now(),
            }
            self.store.put(db, "feedback", job_id, record)
            app = self.store.application(db, job_id)
            if app["state"] not in LOCKED_STATES:
                self.store.update_application(
                    db, job_id, state="skipped" if interested == "no" else "discovered", assessment=None, approval=None
                )
            self.store.event(db, "user_feedback", record, job_id)
        return record

    def inbox(self) -> list[dict]:
        return [
            item
            for item in self.list_jobs()
            if item["application"]["state"] in {"review", "needs_info", "unknown", "submitting"}
        ]

    def plan(self, limit: int | None = None) -> dict:
        limit = min(limit or self.settings.daily_job_limit, self.settings.daily_job_limit)
        pending = [
            item
            for item in self.list_jobs()
            if item["application"]["state"] in {"discovered", "assessed", "retryable", "ready"}
        ]

        def priority(item):
            assessment = item["application"]["assessment"] or {}
            feedback = item.get("feedback") or {}
            return (
                feedback.get("high_priority", False) or assessment.get("fit") == "strong",
                assessment.get("priority", 0),
            )

        strong = sorted([item for item in pending if priority(item)[0]], key=priority, reverse=True)
        reserved = strong[: min(limit, self.settings.high_fit_reserved)]
        selected_ids = {item["job"]["id"] for item in reserved}
        remaining = sorted(
            [item for item in pending if item["job"]["id"] not in selected_ids],
            key=lambda item: item["application"]["updated_at"],
        )
        ranked = reserved + remaining
        return {
            "jobs": ranked[:limit],
            "inbox_count": len(self.inbox()),
            "budget": limit,
            "remaining": max(0, len(ranked) - limit),
        }
