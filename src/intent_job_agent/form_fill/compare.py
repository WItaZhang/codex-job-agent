"""Fact check (specs/form-fill.md 2.3, 4.4, 5.1, 5.2; slice-1 plan section 1): each field read back against the
entry it was matched to, by kind. Results: correct, wrong, no_answer, ambiguous, not_reviewed; severities: fact,
guess, missing, format, none.

Contact values are compared as the saved pages show them, masked: the applicant's own value is a placeholder such
as [email], someone else's is [other email]. So a run can be checked again from its saved files without the values.
A field left empty, or on a placeholder option such as "No Response", is missing when the entry has an answer.
"""

import datetime
import re
from typing import NamedTuple

from .matching import match_page, norm
from .models import AnswerEntry, CheckResult
from .redact import FIELDS, PLACEHOLDER

# Normalized values that mean nothing was chosen.
EMPTY = frozenset(
    {
        "",
        "n a",
        "na",
        "none",
        "no response",
        "select",
        "select one",
        "select an option",
        "please select",
        "choose one",
        "choose an option",
        "no answer provided",
        "not provided",
    }
)
OTHERS = (PLACEHOLDER["other_email"], PLACEHOLDER["other_phone"])
MONTHS = {
    name: n
    for n, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y")
NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?(?![a-z])", re.I)
RANGE = re.compile(r"\s+[-–—]\s+|\s*[–—]\s*|\s+to\s+", re.I)
NUMBER_RANGE = re.compile(r"\d\s*k?\s*(?:[-–—]|to)\s*\$?\s*\d", re.I)  # "$80k-$100k", "80,000 to 100,000"
NAV_NOTE = "页面上的步骤导航，不是填写项"
EXTRA = "多出来的一段经历"


class Verdict(NamedTuple):
    result: str
    severity: str = "none"
    expected: str | None = None
    note: str = ""


def is_empty(value: str) -> bool:
    return norm(value) in EMPTY


def _same(value: str, candidates) -> bool:
    v = norm(value)
    return any(v == norm(c) for c in candidates if c is not None and norm(c))


# --- parsing ----------------------------------------------------------------------------------------------------


def parse_date(text: str) -> datetime.date | None:
    t = re.sub(r"(?<=[A-Za-z])\.", "", str(text or "")).replace(",", " ")
    t = " ".join(t.split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def parse_month(text: str) -> tuple[int, int | None] | None:
    """(year, month), or (year, None) for a year alone."""
    t = " ".join(str(text or "").replace(".", " ").split()).lower()
    if m := re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})", t):
        year, month = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(\d{1,2})\s*[-/]\s*(\d{4})", t):
        year, month = int(m[2]), int(m[1])
    elif (m := re.fullmatch(r"([a-z]+),?\s+(\d{4})", t)) and m[1] in MONTHS:
        year, month = int(m[2]), MONTHS[m[1]]
    elif m := re.fullmatch(r"(\d{4})", t):
        return int(m[1]), None
    elif day := parse_date(text):
        return day.year, day.month
    else:
        return None
    return (year, month) if 1 <= month <= 12 else None


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) * (1000 if k else 1) for n, k in NUMBER.findall(str(text or ""))]


def _url(text: str) -> str:
    t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(text or "").strip().lower())
    return re.sub(r"^www\.", "", t).rstrip("/")


# --- one value against one entry --------------------------------------------------------------------------------


def placeholder(from_profile: str | None) -> str | None:
    """The placeholder a profile field is masked with in saved files, or None when it is never masked."""
    if not from_profile or not from_profile.startswith("personal."):
        return None
    kind = FIELDS.get(from_profile.removeprefix("personal."))
    return PLACEHOLDER[kind] if kind else None


def _missing(expected: str | None, note: str = "空着") -> Verdict:
    return Verdict("wrong", "missing", expected, note)


def _contact(entry: AnswerEntry, value: str) -> Verdict:
    mark = placeholder(entry.from_profile)
    if mark is None:
        if entry.answer:
            return _text(entry, value, ())
        return Verdict("not_reviewed", note="这一栏的联系方式代码核对不了")
    if is_empty(value):
        return _missing(mark)
    if mark in value and not any(other in value for other in OTHERS):
        return Verdict("correct")
    return Verdict("wrong", "fact", mark, "不是你自己的" if any(o in value for o in OTHERS) else "")


def _text(entry: AnswerEntry, value: str, options) -> Verdict:
    """text and choice: the answer or an accepted spelling; `otherwise` only when the page lacks the answer."""
    answers = (entry.answer, *entry.accept)
    if _same(value, answers):
        return Verdict("correct")
    offered = any(_same(option, answers) for option in options) if options else None
    if entry.otherwise and _same(value, [entry.otherwise]):
        if offered:
            return Verdict("wrong", "fact", entry.answer, "页面上有标准答案的选项，不该选 otherwise")
        return Verdict("correct", note="看不到选项，按 otherwise 算对" if offered is None else "页面上没有答案的选项")
    expected = entry.otherwise if offered is False and entry.otherwise else entry.answer
    if offered is False and not entry.otherwise:
        if is_empty(value):
            return Verdict("no_answer", note="页面上没有标准答案的选项，也没写 otherwise")
        return Verdict("no_answer", "guess", None, "页面上没有标准答案的选项，填表 agent 却选了一项")
    if is_empty(value):
        return _missing(expected)
    return Verdict("wrong", "fact", expected)


def _yes_no(entry: AnswerEntry, value: str, control: str) -> Verdict:
    answer = norm(entry.answer)
    if _same(value, entry.accept):
        return Verdict("correct")
    if control == "checkbox" and is_empty(value):  # an unticked box says No
        return Verdict("correct") if answer == "no" else _missing(entry.answer, "没勾")
    first = norm(value).split(" ", 1)[0]
    if first in ("yes", "no"):
        return Verdict("correct") if first == answer else Verdict("wrong", "fact", entry.answer)
    if is_empty(value):
        return _missing(entry.answer)
    return Verdict("no_answer", note="选项不以 Yes 或 No 开头，也不在 accept 里")


def _month(expected: str, value: str) -> Verdict:
    want, got = parse_month(expected), parse_month(value)
    if is_empty(value):
        return _missing(expected)
    if want is None or got is None:
        return Verdict("wrong", "fact", expected, "读不出月份")
    if got[1] is None:
        if got[0] == want[0]:
            return Verdict("correct", note="页面上只有年份")
        return Verdict("wrong", "fact", expected)
    return Verdict("correct") if got == want else Verdict("wrong", "fact", expected)


def _number_min(entry: AnswerEntry, value: str) -> Verdict:
    floor = (_numbers(entry.answer or "") or [0])[0]
    if is_empty(value):
        return _missing(entry.answer)
    got = _numbers(value)
    if not got:
        return Verdict("not_reviewed", note="读不出数字")
    if len(got) >= 2 and NUMBER_RANGE.search(value):
        low, high = got[0], got[1]
        ok = low <= floor <= high or low >= floor
    else:
        ok = got[0] >= floor
    return Verdict("correct") if ok else Verdict("wrong", "fact", f"不低于 {entry.answer}")


def compare_value(entry: AnswerEntry, value: str, *, options=(), control: str = "text") -> Verdict:
    """One field outside a segment against its entry."""
    value = str(value or "")
    if entry.category == "contact":
        return _contact(entry, value)
    kind = entry.kind or "text"
    if kind == "yes_no":
        return _yes_no(entry, value, control)
    if kind == "number_min":
        return _number_min(entry, value)
    if kind == "month":
        return _month(entry.answer or "", value)
    if kind == "date":
        if _same(value, entry.accept):
            return Verdict("correct")
        if is_empty(value):
            return _missing(entry.answer)
        got, want = parse_date(value), parse_date(entry.answer or "")
        if got is None:
            return Verdict("wrong", "fact", entry.answer, "读不出日期")
        return Verdict("correct") if got == want else Verdict("wrong", "fact", entry.answer)
    if kind == "url":
        if is_empty(value):
            return _missing(entry.answer)
        ok = _url(value) in {_url(a) for a in (entry.answer, *entry.accept) if a}
        return Verdict("correct") if ok else Verdict("wrong", "fact", entry.answer)
    return _text(entry, value, tuple(options))


# --- record segments --------------------------------------------------------------------------------------------


def _city(entry: AnswerEntry, value: str) -> Verdict:
    city = entry.record["city"]
    names = (city, *entry.record_accept.get("city", ()))
    if _same(value, names):
        return Verdict("correct")
    if is_empty(value):
        return _missing(city)
    if _same(value.split(",", 1)[0], names):
        return Verdict("wrong", "format", city, "只写城市名，不写州和国家")
    return Verdict("wrong", "fact", city)


def compare_part(entry: AnswerEntry, part: str, value: str) -> Verdict:
    """One field of a matched segment against its record entry: `part` is a record field, "range" (start and end
    in one field) or "other" (a description and the like, which code cannot judge)."""
    value = str(value or "")
    if part == "other":
        return Verdict("no_answer") if is_empty(value) else Verdict("not_reviewed", note="描述类的栏，代码判断不了")
    if part == "range":
        want = f"{entry.record['start']} – {entry.record['end']}"
        if is_empty(value):
            return _missing(want)
        pieces = RANGE.split(value.strip(), maxsplit=1)
        if len(pieces) != 2:
            return Verdict("wrong", "fact", want, "读不出起止")
        start, end = _month(entry.record["start"], pieces[0]), _month(entry.record["end"], pieces[1])
        ok = start.result == end.result == "correct"
        return Verdict("correct") if ok else Verdict("wrong", "fact", want)
    if part not in entry.record:
        return Verdict("no_answer") if is_empty(value) else Verdict("not_reviewed", note="标准答案里没有这一栏")
    if part in ("start", "end"):
        return _month(entry.record[part], value)
    if part == "city":
        return _city(entry, value)
    expected = entry.record[part]
    names = (expected, *entry.record_accept.get(part, ()))
    if part in ("school", "company"):
        names = (*names, *entry.aliases)
    if _same(value, names):
        return Verdict("correct")
    otherwise = entry.record_otherwise.get(part, ())
    if _same(value, otherwise):
        return Verdict("correct", note="按 otherwise")
    if is_empty(value):
        return _missing(expected)
    if otherwise:
        picks = " 或 ".join(otherwise)
        return Verdict("not_reviewed", note=f"可能是最相近的选项；原值 {expected}，找不到时选 {picks}")
    return Verdict("wrong", "fact", expected)


# --- a page -----------------------------------------------------------------------------------------------------


def check_page(fields, answers: dict[str, AnswerEntry]) -> tuple[CheckResult, ...]:
    """Every field of the page checked, in page order, then one result per record segment the page lacks."""
    fields = list(fields)
    found = match_page(fields, answers)
    labels = {segment.index: segment.label for segment in found.segments}
    results = []
    for f in fields:
        m = found.fields[f.id]
        base = {"field_id": f.id, "question": f.question, "value": f.value, "key": m.key}
        if m.navigation:
            verdict = Verdict("no_answer", note=NAV_NOTE)
        elif m.ambiguous:
            verdict = Verdict("ambiguous", note="对到了 " + "、".join(m.ambiguous))
        elif m.extra:
            label = labels.get(m.segment) or "（没有公司或学校名）"
            verdict = (
                Verdict("no_answer", note=f"{EXTRA}（{label}）里空着的栏")
                if is_empty(f.value)
                else Verdict("wrong", "fact", None, f"{EXTRA}（{label}），标准答案里没有这一段")
            )
        elif m.key and m.segment is not None:
            verdict = compare_part(answers[m.key], m.part or "other", f.value)
        elif m.key:
            verdict = compare_value(answers[m.key], f.value, options=f.options, control=f.control)
        elif is_empty(f.value):
            verdict = Verdict("no_answer")
        elif f.control == "file":
            verdict = Verdict("not_reviewed", note="上传的文件，切片 2 再按运行记录核对")
        else:
            verdict = Verdict("not_reviewed", note="没有对上标准答案，代码判断不了")
        results.append(CheckResult(**base, **verdict._asdict()))
    for key in found.missing:
        entry = answers[key]
        label = " · ".join(v for v in (entry.record.get(p) for p in ("school", "company", "degree", "title")) if v)
        results.append(
            CheckResult(
                field_id="",
                question=key,
                key=key,
                result="wrong",
                severity="missing",
                expected=label,
                note="页面上少了这一段",
            )
        )
    return tuple(results)
