"""Pure application routing. The service rechecks this at the action boundary."""

from urllib.parse import urlsplit

from .matching import check_constraints
from .models import Assessment, Decision, Job, Packet, Policy, Profile


def route(profile: Profile, job: Job, assessment: Assessment, policy: Policy, packet: Packet | None = None) -> Decision:
    eligibility, issues = check_constraints(profile, job)
    if eligibility == "fail" or assessment.eligibility == "fail" or assessment.fit == "no":
        return Decision(action="skip", reasons=issues or assessment.reasons)
    if eligibility == "unknown" or assessment.eligibility == "unknown" or assessment.unknowns:
        return Decision(action="clarify", reasons=issues + assessment.unknowns or ["Eligibility unresolved"])
    if not packet:
        return Decision(action="review", reasons=["Prepare application packet before authorization"])
    if assessment.source == "baseline":
        return Decision(
            action="review", reasons=["Lexical baseline requires a substantive assessment before auto-submit"]
        )
    if not assessment.evidence_fact_ids:
        return Decision(action="review", reasons=["Automatic submission requires confirmed matching evidence"])
    visible_evidence = {fact_id for claim in packet.claims for fact_id in claim.fact_ids}
    visible_evidence.update(fact_id for attachment in packet.attachments for fact_id in attachment.fact_ids)
    if assessment.fit == "strong" and not visible_evidence.intersection(assessment.evidence_fact_ids):
        return Decision(action="review", reasons=["High-fit packet does not present the matching experience evidence"])
    host = (urlsplit(job.apply_url).hostname or "").casefold()
    if host not in {domain.casefold() for domain in policy.allowed_domains}:
        return Decision(action="review", reasons=[f"Destination not authorized for automatic submission: {host}"])
    if job.company.casefold() in {name.casefold() for name in policy.review_companies}:
        return Decision(action="review", reasons=["User requires review for this company"])
    if assessment.fit not in policy.auto_fit:
        return Decision(action="review", reasons=["Match category requires user review"])
    facts = {fact.id: fact.text for fact in profile.facts if fact.confirmed}
    answer_values = {
        fact.id: fact.value if fact.value is not None else fact.text for fact in profile.facts if fact.confirmed
    }
    rewritten = any(claim.text not in [facts.get(key) for key in claim.fact_ids] for claim in packet.claims) or any(
        answer.value not in [answer_values.get(key) for key in answer.fact_ids] for answer in packet.answers.values()
    )
    if rewritten and policy.require_review_for_rewrites:
        return Decision(action="review", reasons=["Rewritten factual claims require semantic review"])
    return Decision(action="auto", reasons=["Within the user's configured automatic submission scope"])
