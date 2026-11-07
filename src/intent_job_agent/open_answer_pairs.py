"""Draft/final pairs of open answers (specs/open-answers.md 3.4, 4.6, 4.9; open-answers.slice3.plan.md).

A filling agent of the form-fill module reports, at each checkpoint("filled"), the fields where it wrote an answer
to an open question and the question's type (`Mark`). The checkpoint service records the question and the value
from its own read (`Draft`, in the run's recording). After the run, each recorded answer of a job left filled in its
tab becomes a `Pair`. Code then watches the tab read-only (tools.py) and keeps the last value read before the page
says received, the tab closes, the tab leaves the job's site or the applicant says they are done: that is the final.
A pair is classified by a deterministic text distance; a rewrite gets one reason question, and an edited final can
go into the bank by the applicant's choice (open_answers.add_entry). The questions offer options first and the
applicant's own words last (4.9), and an answer is recorded only from the applicant's actual reply.

Everything written is masked. Pairs never go into the database or to a tagging or analysis model, and the tools
built on this module never return a draft or a final (4.3).
"""

import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_serializer

from .domain import InvariantError
from .form_fill.matching import is_empty, norm
from .form_fill.models import ReadField
from .open_answers import QUESTION_TYPES, QuestionType, Rejected

DRAFTS_FILE = "open_answers.json"  # in the recording's fills/<run>/, written by the checkpoint service
FINAL_FILE = "final.json"  # the read made after the filling agent quit (recording.RecordingStore)
WATCH_PREFIX = "watch-"  # the run id of a watch in apply_running
TEXT_CONTROLS = ("text", "textarea")
TOKEN = re.compile(r"\w+|[^\w\s]")
SAFE_PAIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SKIP = "跳过"
OWN = "都不对，我自己写"
# The reason question (4.6 item 4): a closed set, "其他（我自己说）" last; 0 skips.
REASONS = (
    ("content", "内容取舍：选的事实或经历不对"),
    ("motivation", "动机：没写出我为什么想要这份工作"),
    ("voice", "口吻：不像我说话"),
    ("length", "太长"),
    ("other", "其他（我自己说）"),
)
# How a tab ended, in the words of `no_final` when the watch had not read the field before it.
ENDED_WORDS = {
    "received": "页面写着已经交上",
    "closed": "标签页不在了",
    "left": "标签页离开了这个岗位的网站",
    "user_done": "申请人说改完了",
}
RENDERINGS = (2, 3)  # how many English renderings of the applicant's own words to choose from (4.9 item 3)

Kind = Literal["unchanged", "edited", "rewritten"]
Ended = Literal["received", "closed", "left", "user_done"]
_SAVING = threading.Lock()  # the watch's thread and the tool calls save pairs


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Mark(_Model):
    """One field where the filling agent wrote its answer to an open question (checkpoint's `open_answers`)."""

    field: str = Field(
        pattern=r"^p[1-9][0-9]*\.[1-9][0-9]*$", description="the field's number from the blank checkpoint, e.g. p1.3"
    )
    type: QuestionType = Field(description="the open question's type")


MARKS = TypeAdapter(list[Mark])


class Draft(_Model):
    """One recorded open answer: the question and the value from the checkpoint service's own read (masked)."""

    field: str
    page: int
    type: QuestionType
    question: str
    section: str = ""
    occurrence: int = 1
    control: str
    text: str
    read_at: str = ""


class TabRef(_Model):
    id: str
    generation: str | None = None
    url: str = ""
    host: str = ""  # the job's site: the tab is read only there


class FieldRef(_Model):
    id: str
    page: int
    section: str = ""
    question: str
    occurrence: int = 1
    control: str = "textarea"


class Written(_Model):
    """A pair's draft: what the agent left in the field, and when code read it."""

    text: str
    read_at: str = ""


class Seen(_Model):
    """The last value a watch read of a pair's field, and which watch read it."""

    text: str
    at: str = ""
    watch: str | None = None


class Final(_Model):
    text: str
    at: str = ""
    ended: Ended


class _Answer(_Model):
    """An answer as the pair's file keeps it: only what was said. A skip is {"skipped": true, "at": ...} and nothing
    else (3.4): skipping carries no information."""

    @model_serializer(mode="wrap")
    def _said(self, handler):
        return {
            key: value
            for key, value in handler(self).items()
            if value is not None and value != [] and value != () and not (key == "skipped" and value is False)
        }


class Reason(_Answer):
    """The applicant's answer to the reason question: an option (5 with their own words, verbatim), or skipped."""

    at: str
    skipped: bool = False
    option: int | None = None
    key: str | None = None
    text: str | None = None
    user_report: str | None = None


class Promotion(_Answer):
    """The applicant's answer to the bank question. Settled once skipped or an option is recorded; until then it may
    hold why the final could not go in (`rejected`), or the applicant's own words and their renderings to pick from."""

    at: str
    skipped: bool = False
    question: Literal["promote", "render"] | None = None
    option: int | None = None
    entry_id: str | None = None
    type: QuestionType | None = None
    user_report: str | None = None
    rejected: Literal["contact", "budget"] | None = None
    size: int | None = None
    max: int | None = None
    own_words: str | None = None
    renderings: tuple[str, ...] = ()

    @property
    def settled(self) -> bool:
        return self.skipped or self.option is not None


class Pair(_Model):
    """One open answer of one job's run: its draft, what code read while it watched the tab, and the final."""

    id: str
    run_id: str
    job_id: str
    company: str = ""
    title: str = ""
    recording_id: str | None = None
    tab: TabRef
    field: FieldRef
    type: QuestionType
    draft: Written
    last_seen: Seen | None = None
    final: Final | None = None
    status: Literal["watching", "final", "no_final"] = "watching"
    no_final: str | None = None
    kind: Kind | None = None
    change: float | None = None
    reason: Reason | None = None
    promotion: Promotion | None = None
    created: str
    updated: str


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- the distance -----------------------------------------------------------------------------------------------


def classify(draft: str, final: str, rewritten_above: float) -> tuple[Kind, float]:
    """(unchanged | edited | rewritten, how much changed from 0 to 1). The texts are cut into words and punctuation,
    lower case, and compared with difflib's SequenceMatcher (no junk heuristic): the change is 1 - its ratio. Equal
    up to white space is unchanged; an empty final is a rewrite."""
    if " ".join(draft.split()) == " ".join(final.split()):
        return "unchanged", 0.0
    if not final.strip():
        return "rewritten", 1.0
    words = SequenceMatcher(None, TOKEN.findall(draft.casefold()), TOKEN.findall(final.casefold()), autojunk=False)
    change = round(1 - words.ratio(), 3)
    return ("rewritten" if change > rewritten_above else "edited"), change


# --- drafts and pairs -------------------------------------------------------------------------------------------


def draft_problem(field: ReadField | None, is_contact: Callable[[str], bool]) -> str | None:
    """Why a field the agent named is not recorded as an open answer, or None when it is."""
    if field is None:
        return "not on this page"
    if field.origin != "control" or field.control not in TEXT_CONTROLS:
        return "not a text field"
    if is_empty(field.value):
        return "empty"
    if is_contact(field.question):
        return "a contact field"
    return None


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def read_drafts(fill_dir: Path) -> list[Draft]:
    rows = _read_json(Path(fill_dir) / DRAFTS_FILE, []) or []
    return [Draft.model_validate(row) for row in rows if isinstance(row, dict)]


def write_drafts(fill_dir: Path, drafts: list[Draft], mask) -> None:
    rows = [d.model_dump(mode="json") for d in sorted(drafts, key=lambda d: (d.page, _number(d.field)))]
    Path(fill_dir).mkdir(parents=True, exist_ok=True)
    (Path(fill_dir) / DRAFTS_FILE).write_text(json.dumps(mask(rows), ensure_ascii=False, indent=2) + "\n", "utf-8")


def _number(field_id: str) -> int:
    return int(field_id.rsplit(".", 1)[-1])


def pair_id(run_id: str, job_id: str, field_id: str) -> str:
    made = re.sub(r"\.{2,}", ".", f"{run_id}.{job_id}.{field_id}")
    if not SAFE_PAIR.match(made):
        raise InvariantError(f"not a pair id: {made!r}")
    return made


def pairs_from_run(
    fill_dir: Path, *, run_id: str, job_id: str, company: str, title: str, recording_id: str | None, tab: dict
) -> list[Pair]:
    """The pairs of one job's run: one per recorded open answer. The draft is the field's value in the read made
    after the agent quit when the field is on that page, else its value at the last filled checkpoint."""
    drafts = read_drafts(fill_dir)
    if not drafts:
        return []
    final = _read_json(Path(fill_dir) / FINAL_FILE)
    shown: dict[tuple, tuple[dict, str]] = {}
    if isinstance(final, dict):
        for row in final.get("fields") or []:
            if isinstance(row, dict):
                shown[(final.get("page"), row.get("id"))] = (row, str(final.get("read_at", "")))
    at = now()
    url = str(tab.get("url") or "")
    ref = TabRef(id=str(tab["id"]), generation=tab.get("generation"), url=url, host=_host(url))
    pairs = []
    for d in drafts:
        text, read_at = d.text, d.read_at
        row, when = shown.get((d.page, d.field), (None, ""))
        if row is not None and row.get("control") in TEXT_CONTROLS and not is_empty(str(row.get("value", ""))):
            text, read_at = str(row["value"]), when
        pairs.append(
            Pair(
                id=pair_id(run_id, job_id, d.field),
                run_id=run_id,
                job_id=job_id,
                company=company,
                title=title,
                recording_id=recording_id,
                tab=ref,
                field=FieldRef(
                    id=d.field,
                    page=d.page,
                    section=d.section,
                    question=d.question,
                    occurrence=d.occurrence,
                    control=d.control,
                ),
                type=d.type,
                draft=Written(text=text, read_at=read_at),
                created=at,
                updated=at,
            )
        )
    return pairs


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# --- watching a tab ---------------------------------------------------------------------------------------------


def find_field(fields, ref: FieldRef) -> ReadField | None:
    """The pair's field in one read: the same question in the same section, the same time over; else the only text
    field with that question."""
    texts = [f for f in fields if f.origin == "control" and f.control in TEXT_CONTROLS]
    question, section = norm(ref.question), norm(ref.section)
    exact = [
        f
        for f in texts
        if norm(f.question) == question and norm(f.section) == section and f.occurrence == ref.occurrence
    ]
    if len(exact) == 1:
        return exact[0]
    same = [f for f in texts if norm(f.question) == question]
    return same[0] if len(same) == 1 else None


def on_site(pair: Pair, url: str) -> bool:
    """Whether a tab's address is still on the job's site, where it may be read."""
    return not pair.tab.host or _host(url) == pair.tab.host


def observe(pair: Pair, fields, watch_id: str, at: str) -> Pair | None:
    """The pair with its field's value in this read as the last read, or None when the read does not show the field
    or shows the value this watch already has (a page left keeps the value read before it)."""
    field = find_field(fields, pair.field)
    if field is None:
        return None
    if pair.last_seen is not None and (pair.last_seen.text, pair.last_seen.watch) == (field.value, watch_id):
        return None
    return pair.model_copy(update={"last_seen": Seen(text=field.value, at=at, watch=watch_id), "updated": at})


def settle(pair: Pair, ended: Ended, watch_id: str, at: str, rewritten_above: float, why: str | None = None) -> Pair:
    """The tab ended: the last value this watch read is the final. A value read by an earlier watch, or none, is not:
    what happened while nobody watched is not known (4.6 item 2), so the pair has no final and is not classified."""
    seen = pair.last_seen
    if seen is None or seen.watch != watch_id:
        reason = why or f"这次看的时候还没读到这道题，{ENDED_WORDS[ended]}；交上之前最后是什么不知道"
        return pair.model_copy(update={"status": "no_final", "no_final": reason, "updated": at})
    kind, change = classify(pair.draft.text, seen.text, rewritten_above)
    final = Final(text=seen.text, at=seen.at, ended=ended)
    return pair.model_copy(update={"status": "final", "final": final, "kind": kind, "change": change, "updated": at})


# --- the questions ----------------------------------------------------------------------------------------------


def _base(pair: Pair) -> dict:
    return {
        "pair_id": pair.id,
        "job_id": pair.job_id,
        "company": pair.company,
        "title": pair.title,
        "question": pair.field.question,
        "type": pair.type,
        "kind": pair.kind,
        "change": pair.change,
    }


def wants_reason(pair: Pair, ask_reason_above: float) -> bool:
    return pair.status == "final" and (pair.change or 0) > ask_reason_above and pair.reason is None


def wants_promotion(pair: Pair) -> bool:
    final = pair.final
    return (
        pair.status == "final"
        and pair.kind in ("edited", "rewritten")
        and final is not None
        and bool(final.text.strip())
        and not (pair.promotion is not None and pair.promotion.settled)
    )


def promote_options(pair: Pair) -> list[dict]:
    label = QUESTION_TYPES[pair.type]
    others = [name for name in QUESTION_TYPES if name != pair.type]
    return [
        {"n": 1, "text": f"收进答案库（题型 {pair.type}：{label}）"},
        {"n": 2, "text": "收进，但题型改成另一种", "needs": "question_type", "choices": others},
        {"n": 3, "text": "不收"},
        {"n": 4, "text": "我改一下再收（我自己写，按你写的原样收）", "needs": "text"},
        {"n": 0, "text": SKIP},
    ]


def questions(pair: Pair, *, ask_reason_above: float, bank: bool) -> list[dict]:
    """The questions this pair waits on, each with numbered options: the reason for a rewrite, and, when there is a
    bank, whether the final goes into it (or which rendering of the applicant's own words)."""
    out = []
    quoted = f"《{pair.field.question}》"
    if wants_reason(pair, ask_reason_above):
        options = [{"n": n, "text": text} for n, (_, text) in enumerate(REASONS, start=1)]
        options[-1]["needs"] = "text"
        options.append({"n": 0, "text": SKIP})
        ask = f"{quoted}这道你重写了，主要因为？"
        out.append({"id": f"{pair.id}:reason", **_base(pair), "ask": ask, "options": options})
    if not (bank and wants_promotion(pair)):
        return out
    promotion = pair.promotion
    if promotion is not None and promotion.renderings:
        options = [{"n": n, "text": text} for n, text in enumerate(promotion.renderings, start=1)]
        options += [{"n": len(promotion.renderings) + 1, "text": OWN, "needs": "text"}, {"n": 0, "text": SKIP}]
        ask = f"{quoted}你写的话要用英文收进答案库，选一个译法（只换语言，不加内容）："
        out.append({"id": f"{pair.id}:render", **_base(pair), "ask": ask, "options": options})
    elif promotion is not None and promotion.rejected:
        why = (
            "含联系方式"
            if promotion.rejected == "contact"
            else f"加上这一条有 {promotion.size} 字符，上限 {promotion.max}"
        )
        options = [option for option in promote_options(pair) if option["n"] in (3, 4, 0)]
        ask = f"{quoted}收不进答案库（{why}），系统不会自己改字："
        out.append({"id": f"{pair.id}:promote", **_base(pair), "ask": ask, "options": options})
    else:
        ask = f"{quoted}你改过的定稿要收进答案库吗？"
        out.append({"id": f"{pair.id}:promote", **_base(pair), "ask": ask, "options": promote_options(pair)})
    return out


def entry_id(pair: Pair) -> str:
    """The bank entry a pair becomes: pair-<job>-<field>, in the bank's id alphabet."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", f"{pair.job_id}-{pair.field.id}".lower()).strip("-_")
    return f"pair-{slug}"


def _renderings(renderings) -> tuple[str, ...]:
    """2-3 English renderings of the applicant's own words, as the coordinator wrote them: structured, checked."""
    try:
        items = TypeAdapter(list[str]).validate_python(renderings)
    except ValidationError:
        raise InvariantError("renderings must be a list of texts") from None
    texts = [item.strip() for item in items]
    if not RENDERINGS[0] <= len(texts) <= RENDERINGS[1]:
        raise InvariantError(f"renderings: give {RENDERINGS[0]}-{RENDERINGS[1]} English renderings, not {len(texts)}")
    if any(not text for text in texts) or len({" ".join(t.split()).casefold() for t in texts}) != len(texts):
        raise InvariantError("renderings must be different texts and none of them empty")
    return tuple(texts)


def apply_choice(
    pair: Pair,
    question: str,
    option: int,
    *,
    user_report: str,
    text: str | None,
    question_type: str | None,
    renderings,
    ask_reason_above: float,
    bank: bool,
    promote: Callable[[dict], str],
) -> tuple[Pair, dict]:
    """The applicant's reply to one of the pair's open questions, recorded: (the pair, what was recorded). Only an
    actual reply counts (`user_report`); the applicant's own words are kept verbatim. `promote(entry)` writes a bank
    entry through open_answers.add_entry and returns its id, or raises Rejected; the text is never changed."""
    report = (user_report or "").strip()
    if not report:
        raise InvariantError("只记申请人实际的回复：user_report 写他回复的原话或定位，不能替他选")
    qid = f"{pair.id}:{question}"
    asked = {q["id"]: q for q in questions(pair, ask_reason_above=ask_reason_above, bank=bank)}
    if qid not in asked:
        raise InvariantError(f"这道题现在不用回答：{qid}")
    shown = [o["n"] for o in asked[qid]["options"]]
    if option not in shown:
        raise InvariantError(f"{qid} 的选项是 {shown}，没有 {option}")
    at = now()
    if question == "reason":
        if option == 0:
            return pair.model_copy(update={"reason": Reason(at=at, skipped=True), "updated": at}), {
                "recorded": "skipped"
            }
        key = REASONS[option - 1][0]
        words = None
        if key == "other":
            words = _own(text, "选“其他（我自己说）”")
        reason = Reason(at=at, option=option, key=key, text=words, user_report=report)
        return pair.model_copy(update={"reason": reason, "updated": at}), {"recorded": "reason"}
    if option == 0:
        return _with(pair, Promotion(at=at, skipped=True)), {"recorded": "skipped"}
    current = pair.promotion
    if question == "render":
        assert current is not None
        if option <= len(current.renderings):
            source = (current.own_words,) if current.own_words else ()
            return _promote(
                pair,
                current.renderings[option - 1],
                current.type or pair.type,
                f"choice {qid}#{option} (rendering of own words)",
                source,
                "render",
                option,
                report,
                promote,
            )
        return _own_words(pair, text, renderings, current.type or pair.type, qid, option, report, promote)
    if option == 3:
        return _with(pair, Promotion(at=at, question="promote", option=3, user_report=report)), {"recorded": "declined"}
    if option == 4:
        kind = _type(question_type, pair.type, allow_same=True) if question_type else pair.type
        return _own_words(pair, text, renderings, kind, qid, option, report, promote)
    assert pair.final is not None
    kind = pair.type if option == 1 else _type(question_type, pair.type)
    return _promote(pair, pair.final.text, kind, f"choice {qid}#{option}", (), "promote", option, report, promote)


def _own(text: str | None, said: str) -> str:
    if text is None or not text.strip():
        raise InvariantError(f"{said}要带上申请人自己写的话：text 照原样写，不改写")
    return text


def _type(question_type: str | None, current: str, allow_same: bool = False) -> str:
    if question_type not in QUESTION_TYPES or (question_type == current and not allow_same):
        others = [name for name in QUESTION_TYPES if allow_same or name != current]
        raise InvariantError(f"question_type 要从这些题型里选一个：{others}")
    return question_type


def _with(pair: Pair, promotion: Promotion) -> Pair:
    return pair.model_copy(update={"promotion": promotion, "updated": promotion.at})


def _own_words(pair, text, renderings, kind, qid, option, report, promote) -> tuple[Pair, dict]:
    """The applicant's own words: into the bank verbatim, or, with renderings, kept for the applicant to pick one."""
    words = _own(text, "选“我自己写”")
    if renderings is None:
        return _promote(pair, words, kind, f"own words {qid}#{option}", (words,), "promote", option, report, promote)
    pending = Promotion(at=now(), type=kind, user_report=report, own_words=words, renderings=_renderings(renderings))
    return _with(pair, pending), {"recorded": "renderings"}


def _promote(pair, text, kind, from_, source, question, option, report, promote) -> tuple[Pair, dict]:
    at = now()
    entry = {
        "id": entry_id(pair),
        "type": kind,
        "question": pair.field.question,
        "text": text,
        "status": "accepted",
        "from": from_,
        "added": date.today().isoformat(),
    }
    if source:
        entry["source_words"] = list(source)
    try:
        made = promote(entry)
    except Rejected as rejected:
        held = Promotion(
            at=at, question="promote", rejected=rejected.kind, size=rejected.size, max=rejected.max, user_report=report
        )
        return _with(pair, held), {
            "recorded": "rejected",
            "why": rejected.kind,
            "size": rejected.size,
            "max": rejected.max,
        }
    done = Promotion(at=at, question=question, option=option, entry_id=made, type=kind, user_report=report)
    return _with(pair, done), {"recorded": "promoted", "entry_id": made}


# --- the files --------------------------------------------------------------------------------------------------


class PairStore:
    """One JSON file per pair under `root` (open_answers.pairs.dir). Each save is masked with `redactor` and replaces
    the file at once; a file that cannot be read is left out of all()."""

    def __init__(self, root: Path, redactor=None):
        self.root, self.redactor = Path(root), redactor

    def path(self, pair_id: str) -> Path:
        if not SAFE_PAIR.match(pair_id) or ".." in pair_id:
            raise InvariantError(f"not a pair id: {pair_id!r}")
        return self.root / f"{pair_id}.json"

    def save(self, pair: Pair) -> None:
        data = pair.model_dump(mode="json")
        if self.redactor is not None:
            data = self.redactor.mask_json(data)
        path = self.path(pair.id)
        with _SAVING:
            self.root.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)

    def load(self, pair_id: str) -> Pair:
        path = self.path(pair_id)
        if not path.is_file():
            raise InvariantError(f"没有这一对：{pair_id}")
        return Pair.model_validate_json(path.read_text(encoding="utf-8"))

    def all(self) -> list[Pair]:
        if not self.root.is_dir():
            return []
        found = []
        for path in self.root.glob("*.json"):
            try:
                found.append(Pair.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError):
                continue
        return sorted(found, key=lambda pair: (pair.created, pair.id))
