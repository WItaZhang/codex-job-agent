"""Read-only evidence projection of the current typed profile, never a second store."""

from pydantic import Field

from .contracts import Record
from .profile_models import Profile

RESUME_SECTIONS = {"education", "work_experience", "projects", "publications", "competitions", "skills"}


class Evidence(Record):
    id: str
    record_id: str
    section: str
    field: str | None = None
    text: str
    value: str | bool | None = None
    source: str
    confirmed: bool
    scope_job_ids: list[str] = Field(default_factory=list)


def display(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "; ".join(display(item) for item in value)
    return str(value)


def profile_evidence(profile: Profile) -> list[Evidence]:
    result = []
    for section, record in profile.records():
        fields = []
        for name, value in record.domain_values().items():
            # Foreign keys are navigation, not independent factual claims.
            if name in {"work_experience_id", "supporting_record_ids"} or value == []:
                continue
            evidence = record.field_evidence.get(name, record.evidence)
            fields.append(
                Evidence(
                    id=f"{record.id}.{name}",
                    record_id=record.id,
                    section=section,
                    field=name,
                    text=display(value),
                    value=value if isinstance(value, (str, bool)) else None,
                    **evidence.model_dump(),
                )
            )
        if fields:
            scopes = [set(item.scope_job_ids) for item in fields if item.scope_job_ids]
            common_scope = set.intersection(*scopes) if scopes else set()
            result.append(
                Evidence(
                    id=record.id,
                    record_id=record.id,
                    section=section,
                    text="\n".join(f"{item.field.replace('_', ' ').title()}: {item.text}" for item in fields),
                    source="; ".join(dict.fromkeys(item.source for item in fields)),
                    confirmed=all(item.confirmed for item in fields) and (not scopes or bool(common_scope)),
                    scope_job_ids=sorted(common_scope),
                )
            )
            result.extend(fields)
    return result


def confirmed_evidence(profile: Profile, job_id: str | None = None) -> dict[str, Evidence]:
    """Without a job context, only globally usable evidence is available."""
    return {
        item.id: item
        for item in profile_evidence(profile)
        if item.confirmed and (not item.scope_job_ids or job_id in item.scope_job_ids)
    }
