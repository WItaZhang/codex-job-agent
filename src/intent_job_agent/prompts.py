"""Prompts and structured-output schemas for the model's three jobs in slice 1.

Prompts are built only from tags, labels, the user's own words and the intent model.
Job titles and descriptions are untrusted and never included.
"""

from pydantic import BaseModel, Field

from .domain import IntentModel


class ScopeOption(BaseModel):
    label: str
    keys: list[str]


class ReasonMapping(BaseModel):
    """Map a free-text reason onto dimension.key references, or ask about its scope."""

    keys: list[str] = Field(default_factory=list)
    scope_options: list[ScopeOption] = Field(default_factory=list)
    mentions_salary: bool = False
    unexpressible: str | None = None


class CauseRanking(BaseModel):
    order: list[str] = Field(default_factory=list)


class FeedbackChanges(BaseModel):
    changes: list[dict] = Field(default_factory=list)


def _intent_lines(intent: IntentModel) -> str:
    lines = [f"- {ref}: {entry.level}" for ref, entry in intent.iter_entries()]
    for ref, entry in intent.iter_entries():
        lines.extend(f"  - {ref} when {rule.when}: {rule.level}" for rule in entry.exceptions)
    comp = intent.compensation
    lines.append(f"- salary floor: {comp.floor} {comp.currency}/{comp.period}" if comp else "- salary: not considered")
    return "\n".join(lines)


def reason_prompt(intent: IntentModel, value: str, tags: list[str], text: str, dimensions: tuple[str, ...]) -> str:
    return f"""The user labelled a job `{value}` and explained: "{text}"

Job tags: {", ".join(tags)}
Dimensions (closed set): {", ".join(dimensions)}
Current intent:
{_intent_lines(intent)}

Map the explanation to `dimension.key` references. If it could mean different scopes, give
scope_options instead. Set mentions_salary if it is about pay. If no dimension can express it,
describe the missing dimension in `unexpressible` instead of forcing it into another one."""


def ranking_prompt(intent: IntentModel, value: str, tag_sets: list[list[str]], causes: list[tuple[str, str]]) -> str:
    jobs = "\n".join(f"- {', '.join(tags)}" for tags in tag_sets)
    options = "\n".join(f"- {cause_id}: {description}" for cause_id, description in causes)
    return f"""The user labelled these jobs `{value}`, which the current intent did not predict.
Job tags:
{jobs}
Current intent:
{_intent_lines(intent)}

Candidate causes:
{options}

Return the candidate ids ordered from most to least likely."""


def feedback_prompt(intent: IntentModel, value: str, tag_sets: list[list[str]], text: str) -> str:
    jobs = "\n".join(f"- {', '.join(tags)}" for tags in tag_sets)
    return f"""While reviewing why the user labelled these jobs `{value}`, the user said: "{text}"
Job tags:
{jobs}
Current intent:
{_intent_lines(intent)}

Propose edits as a list of changes. Allowed ops: set (ref, to_level), add (ref, level), delete (ref),
add_exception (ref, when, level), remove_exception (ref, when). Levels: exclude, strong_avoid, avoid,
prefer, strong_prefer, require. Edit existing entries instead of adding parallel ones."""
