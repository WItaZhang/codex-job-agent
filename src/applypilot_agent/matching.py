"""Interpretable baseline. Missing information stays unknown, never zero-as-error."""

import re

from .config import MatchingSettings
from .models import Assessment, Job, Profile
from .profile_evidence import RESUME_SECTIONS, profile_evidence
from .serialization import digest, job_digest


def check_constraints(profile: Profile, job: Job) -> tuple[str, list[str]]:
    fields = {**job.attributes, "title": job.title, "company": job.company, "location": job.location}
    failed, unknown = [], []
    for rule in profile.preferences.constraints:
        actual = fields.get(rule.field, "").casefold().strip()
        expected = rule.value if isinstance(rule.value, list) else [rule.value]
        values = [value.casefold().strip() for value in expected]
        if not actual:
            unknown.append(f"Missing job field: {rule.field}")
            continue
        checks = {
            "equals": actual == values[0],
            "contains": all(value in actual for value in values),
            "excludes": all(value not in actual for value in values),
            "one_of": actual in values,
        }
        if not checks[rule.operator]:
            failed.append(f"Constraint failed: {rule.field} {rule.operator} {rule.value}")
    return ("fail", failed + unknown) if failed else (("unknown", unknown) if unknown else ("pass", []))


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w+#.]+", text.casefold()))


def baseline(profile: Profile, job: Job, settings: MatchingSettings | None = None) -> Assessment:
    settings = settings or MatchingSettings()
    eligibility, issues = check_constraints(profile, job)
    text = f"{job.title} {job.description}".casefold()
    preferred = profile.preferences.preferred_terms or profile.preferences.directions
    hits = [term for term in preferred if term.casefold() in text]
    avoids = [term for term in profile.preferences.avoided_terms if term.casefold() in text]
    job_tokens = tokens(text)
    evidence = [
        fact.id
        for fact in profile_evidence(profile)
        if fact.confirmed
        and fact.section in RESUME_SECTIONS
        and (not fact.scope_job_ids or job.id in fact.scope_job_ids)
        and len(tokens(fact.text) & job_tokens) >= settings.minimum_token_overlap
    ]
    priority = max(
        0.0,
        min(
            1.0,
            len(hits) / max(1, len(preferred)) - settings.avoided_term_penalty * len(avoids) / max(1, len(preferred)),
        ),
    )
    fit = "no" if eligibility == "fail" else "possible" if hits else "unknown"
    if priority >= settings.strong_threshold and evidence and eligibility == "pass":
        fit = "strong"
    return Assessment(
        job_id=job.id,
        job_hash=job_digest(job),
        profile_hash=digest(profile),
        fit=fit,
        eligibility=eligibility,
        priority=priority,
        evidence_fact_ids=evidence,
        reasons=issues + [f"Baseline term matches: {hits}; avoided terms: {avoids}"],
        unknowns=issues if eligibility == "unknown" else [],
        source="baseline",
    )
