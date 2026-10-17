"""Form-fill module: core types (specs/form-fill.md; slice-1 plan, section 4)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = (
    "name",
    "contact",
    "work_auth",
    "education",
    "experience",
    "availability",
    "compensation",
    "location",
    "eeo",
    "screening",
)
Kind = Literal["text", "choice", "yes_no", "date", "month", "number_min", "url", "record"]
RECORD_FIELDS = ("school", "degree", "major", "company", "title", "start", "end", "city")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListEntry(_Model):
    """One job to fill. `cover_letter` is a path to the .txt (its .pdf sits next to it), or "none"."""

    id: str = Field(min_length=1)
    url: str
    company: str
    title: str
    resume: str
    cover_letter: str
    revisit: bool = False


class JobList(_Model):
    jobs: list[ListEntry] = Field(min_length=1)


class AnswerEntry(_Model):
    """One fact from answers.yaml. Exactly one of `answer`, `from_profile`, `from_applicant` is set, except for
    records, which carry the record fields instead. Values are kept as text (YAML numbers and dates included)."""

    key: str
    answer: str | None = None
    from_profile: str | None = None
    from_applicant: str | None = None
    kind: Kind | None = None
    accept: tuple[str, ...] = ()
    otherwise: str | None = None
    asks: tuple[str, ...] = ()
    overrides: tuple[str, ...] = ()
    source: str | None = None
    record: dict[str, str] = Field(default_factory=dict)  # school/degree/major or company/title, start/end, city
    record_accept: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    record_otherwise: dict[str, str] = Field(default_factory=dict)  # what to pick when the page lacks the value
    aliases: tuple[str, ...] = ()

    @property
    def category(self) -> str:
        return self.key.split(".", 1)[0]


class ChecklistItem(_Model):
    id: str
    rule: str
    level: Literal["guess", "missing", "format"]
    added: str | None = None
    from_: str | None = Field(default=None, alias="from")


class ReadField(_Model):
    """One value read back from the final page. `origin` is a form control or a question/value pair shown as text
    (a review page). `id` is p<page>.<n> in page order; slice 1 reads one page, so it is always p1."""

    id: str
    question: str
    origin: Literal["control", "review_text"]
    control: str  # text, textarea, select, checkbox, radio, combobox, file, review
    value: str
    options: tuple[str, ...] = ()
    required: bool = False


class PageState(_Model):
    """Where the form on the final page stands (slice-1 check 4): the visible buttons that would still send it or move
    it on (as shown), and the text saying the application was received, if any."""

    actions: tuple[str, ...] = ()
    received: str = ""

    @property
    def before_submit(self) -> bool:
        return bool(self.actions) and not self.received


Result = Literal["correct", "wrong", "no_answer", "ambiguous", "not_reviewed", "unreadable"]
Severity = Literal["fact", "guess", "missing", "format", "none"]


class CheckResult(_Model):
    """One field checked against the answer sheet. A record segment the page lacks has an empty `field_id`. `value`
    and `expected` are as saved: contact values are placeholders, never the values themselves."""

    field_id: str
    question: str = ""
    value: str = ""
    key: str | None
    result: Result
    expected: str | None = None
    severity: Severity = "none"
    note: str = ""


Conclusion = Literal["maybe_submitted", "do_not_submit", "check_then_submit", "not_filled", "unreadable"]


class JobReport(_Model):
    """One job's report (specs/form-fill.md 3.2; slice-1 plan section 1). `not_filled_reason` says why a form was
    not filled up to the final submit button (gate, needs_answer, stuck, page_error ...)."""

    job_id: str
    company: str = ""
    title: str = ""
    status: Literal["filled", "gate", "tab_lost", "readback_failed", "system_error"]
    agent_result: str | None = None
    conclusion: Conclusion
    not_filled_reason: str | None = None
    fields: tuple[CheckResult, ...] = ()
    todo: tuple[str, ...] = ()
    page_dir: str | None = None
    notes: tuple[str, ...] = ()
