"""Fact check (specs/form-fill.md 2.3, 4.4, 5.1, 5.2; slice-1 plan section 1): each field read back against the
entry it was matched to, by kind. Results: correct, wrong, no_answer, ambiguous, not_reviewed; severities: fact,
guess, missing, format, none.

Contact values are compared as the saved pages show them, masked: each of the applicant's own values is its field's
placeholder ([email], [phone], [address], [city], [linkedin] ...), someone else's is [other email] or [other phone].
So a run can be checked again from its saved files without the values; the answer sheet is masked the same way
before a check (cli.mask_sheet). A field left empty, or on a placeholder option such as "No Response", is missing
when the entry has an answer. A value the code cannot read (a date it does not know) is not reviewed, never wrong.
"""

import datetime
import re
from typing import NamedTuple

from ..duplicates import COMPANY_WORDS
from .matching import is_empty, match_page, norm
from .models import AnswerEntry, CheckResult
from .redact import FIELD_PLACEHOLDER, PLACEHOLDER

OTHERS = (PLACEHOLDER["other_email"], PLACEHOLDER["other_phone"])
MARK = re.compile(r"\[[^\[\]]+\]")
ADDRESS_MARKS = {FIELD_PLACEHOLDER["address"], FIELD_PLACEHOLDER["city"], FIELD_PLACEHOLDER["postal_code"]}
HIDDEN = "[其他值，已隐去]"  # a contact field's value that is not the applicant's own and that masking missed
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
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m/%d/%y",
    "%m-%d-%y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)
ONGOING = frozenset({"present", "current", "now", "ongoing"})
NUMBER = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)(?![\d,]|\.\d)(?:\s*(k)(?=usd\b|[^a-z]|$))?", re.I)
RANGE_SEPARATOR = re.compile(r"\s+(?:-|–|—|to|until|through)\s+|\s*[–—]\s*", re.I)
NAV_NOTE = "页面上的步骤导航，不是填写项"
OLD_PAGE = "这一页是按旧的打码格式存的，分不出是不是你自己的联系方式；重新读这一页才能核对"
REQUIRED_EMPTY = "必填，空着，也没有标准答案"
APARTMENT = frozenset({"apt", "apartment", "suite", "ste", "unit", "floor", "fl", "room", "rm"})
EXTRA = "多出来的一段经历"
EMPTY_BLOCK = "空着的一段，对不上标准答案里的哪一段"
US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "dc": "district of columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky",
    "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota",
    "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania", "ri": "rhode island",
    "sc": "south carolina", "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}  # fmt: skip
UNITED_STATES = frozenset({"united states", "united states of america", "usa", "us", "u s", "u s a", "america"})
PLACE_PHRASE = re.compile(  # whole state names and US spellings, longest first, in normalized text
    r"\b(?:" + "|".join(sorted({*US_STATES.values(), *UNITED_STATES}, key=len, reverse=True)) + r")\b"
)


class Verdict(NamedTuple):
    result: str
    severity: str = "none"
    expected: str | None = None
    note: str = ""


def _same(value: str, candidates) -> bool:
    """Equal after normalizing; placeholders compare as themselves, so "[linkedin]" never equals "LinkedIn"."""
    v, marks = norm(value), MARK.findall(value or "")
    return any(v == norm(c) and marks == MARK.findall(c) for c in candidates if c is not None and norm(c))


TRAILING_BRACKET = re.compile(r"\s*\(([^()]*)\)\s*$")
SITE_WORDS = {"website": "site", "page": "site", "portal": "site"}
SITE_DROPPED = frozenset({"career", "careers", "job", "jobs"})


def _without_place(text: str) -> str:
    """The text without a trailing bracket that names a place ("No (United States of America)"); other brackets
    stay, since they tell options apart ("Master's Degree (in progress)")."""
    m = TRAILING_BRACKET.search(text)
    return text[: m.start()] if m and _place_key(m[1]) else text


def _company_names(company: str) -> list[str]:
    """The job's company name without legal words (inc, llc ...), then ever shorter from the end: "Perplexity AI",
    "Perplexity"."""
    words = [w for w in norm(company).split() if w not in COMPANY_WORDS]
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def _site_form(text: str, company: str) -> str:
    """A "how did you hear" option in one spelling (decision 23): any trailing bracket left out; the job's company
    name, "corporate" and "company's" as "company"; careers and jobs left out; website, page and portal as "site"."""
    t = f" {norm(TRAILING_BRACKET.sub('', text or ''))} "
    name = next((n for n in _company_names(company) if f" {n} " in t), None)
    if name:
        t = t.replace(f" {name} ", " company ", 1)
    t = t.replace(" web site ", " site ").replace(" corporate ", " company ").replace(" company s ", " company ")
    return " ".join(SITE_WORDS.get(word, word) for word in t.split() if word not in SITE_DROPPED)


def _same_meaning(value: str, candidates, company: str = "") -> bool:
    """Equal as written; or without a trailing place in brackets ("I prefer not to disclose (United States of
    America)"); or, for a site, the same once the job's company name is read as "company" (decision 23)."""
    value = value or ""
    if _same(value, candidates):
        return True
    core = _without_place(value)
    if core != value and norm(core) and _same(core, candidates):
        return True
    form = _site_form(value, company)
    return "site" in form.split() and any(c and form == _site_form(c, company) for c in candidates)


def _missing(expected: str | None, note: str = "空着") -> Verdict:
    return Verdict("wrong", "missing", expected, note)


# --- parsing ----------------------------------------------------------------------------------------------------


def parse_date(text: str) -> datetime.date | None:
    t = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", str(text or ""), flags=re.I)
    t = re.sub(r"\bsept\b", "sep", t, flags=re.I)
    t = " ".join(re.sub(r"(?<=[A-Za-z])\.", "", t).replace(",", " ").split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def parse_month(text: str) -> tuple[int, int | None] | None:
    """(year, month), or (year, None) for a year alone. Words in brackets and "expected" are left out."""
    t = re.sub(r"\([^()]*\)|\bexpected\b|\banticipated\b", " ", str(text or ""), flags=re.I)
    t = " ".join(t.replace(".", " ").split()).lower()
    if m := re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})", t):
        year, month = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(\d{1,2})\s*[-/ ]\s*(\d{4})", t):  # 09/2025, 09-2025, 09.2025
        year, month = int(m[2]), int(m[1])
    elif (m := re.fullmatch(r"([a-z]+),?\s+(\d{4})", t)) and m[1] in MONTHS:
        year, month = int(m[2]), MONTHS[m[1]]
    elif m := re.fullmatch(r"(\d{4})", t):
        return int(m[1]), None
    elif day := parse_date(t):
        return day.year, day.month
    else:
        return None
    return (year, month) if 1 <= month <= 12 else None


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) * (1000 if k else 1) for n, k in NUMBER.findall(str(text or ""))]


def _is_range(text: str) -> bool:
    """Two numbers joined by a dash, "~", "to" or "and", with currency and per-year words around them allowed."""
    found = list(NUMBER.finditer(str(text or "")))
    if len(found) < 2:
        return False
    between = text[found[0].end() : found[1].start()]
    between = re.sub(r"(?i)\b(usd|dollars?|per (year|annum)|a year|annually)\b|/\s*(yr|year|y)\b|\$", " ", between)
    return bool(re.fullmatch(r"(?i)\s*k?\s*(?:[-–—~]|to|and)\s*", between))


def _url(text: str) -> str:
    t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(text or "").strip().lower())
    return re.sub(r"^www\.", "", t).rstrip("/")


# --- one value against one entry --------------------------------------------------------------------------------


def placeholder(from_profile: str | None) -> str | None:
    """The placeholder a profile field is masked with in saved files, or None when it is never masked."""
    if not from_profile or not from_profile.startswith("personal."):
        return None
    return FIELD_PLACEHOLDER.get(from_profile.removeprefix("personal."))


def _place_key(text: str) -> str | None:
    n = norm(text)
    if n in UNITED_STATES:
        return "united states"
    if n in US_STATES:
        return US_STATES[n]
    return n if n in US_STATES.values() else None


def _plain_contact(entry: AnswerEntry, value: str) -> Verdict:
    """A contact value saved pages never mask (state, country, a very short city): compared as text, with US state
    abbreviations and the names of the United States counted as the same; a spelling the code does not know is not
    reviewed rather than wrong."""
    if is_empty(value):
        return _missing(entry.answer)
    if _same(value, [entry.answer]) or _same(value.split(",", 1)[0], [entry.answer]):
        return Verdict("correct")
    want, got = _place_key(entry.answer or ""), _place_key(value)
    if want and got:
        return Verdict("correct") if want == got else Verdict("wrong", "fact", entry.answer)
    return Verdict("not_reviewed", note="写法和 profile.json 里的不一样，代码认不出是不是同一个")


def _contact(entry: AnswerEntry, value: str) -> Verdict:
    if entry.answer is not None:  # cli.plain_contacts gave it the profile value: never masked in saved files
        return _plain_contact(entry, value)
    mark = placeholder(entry.from_profile)
    if mark is None:
        return Verdict("not_reviewed", note="这一栏的联系方式代码核对不了")
    if is_empty(value):
        return _missing(mark)
    marks = MARK.findall(value)
    own = {mark, *entry.accept}  # accept: the marks of profile fields with the same value (cli.load_sheet)
    field = (entry.from_profile or "").removeprefix("personal.")
    if any(m in OTHERS for m in marks):
        return Verdict("wrong", "fact", mark, "不是你自己的")
    if field == "address":  # the street, with the applicant's own city, state, postal code and country around it
        if not (own & set(marks) and set(marks) <= own | ADDRESS_MARKS):
            return Verdict("wrong", "fact", mark)
        state, country = _own_places(entry)
        text = norm(MARK.sub(" ", value))
        for phrase in PLACE_PHRASE.findall(text):
            key = _place_key(phrase)
            if key == "united states" or state is None or key == state:
                text = text.replace(phrase, " ", 1)
        rest = [
            w
            for w in text.split()
            if not (_place_key(w) and (state is None or _place_key(w) in (state, "united states")))
        ]
        if not rest:
            return Verdict("correct")
        if all(w in APARTMENT or w.isdigit() and len(w) <= 4 for w in rest):
            return Verdict("not_reviewed", note="住址里多了门牌、楼层这类内容，代码判断不了")
        return Verdict("wrong", "fact", mark, "住址里有不是你自己的城市或邮编")
    if field == "city":  # the city first; then only the applicant's own state, country or postal code
        lead = MARK.match(value.strip())
        if not (lead and lead[0] in own and set(marks) <= own | {FIELD_PLACEHOLDER["postal_code"]}):
            return Verdict("wrong", "fact", mark)
        state, country = _own_places(entry)
        tail = MARK.sub(" ", value.strip()[lead.end() :])
        for piece in (p for p in re.split(r"[,()\-–·|/]", tail) if norm(p)):
            key = _place_key(piece)
            if key is None:
                return Verdict("not_reviewed", note=f"城市后面的「{piece.strip()}」代码认不出是哪里")
            if key == "united states":
                wrong = country is not None and country != "united states"
            else:
                wrong = (state is not None and key != state) or (country is not None and country != "united states")
            if wrong:
                return Verdict("wrong", "fact", mark, "城市名对，但州或国家不是你的")
        return Verdict("correct")
    else:  # the value itself, with at most a label in brackets: "[phone] (Mobile)"
        found = [m for m in marks if m in own]
        rest = re.sub(r"\([^()]*\)", " ", value.replace(found[0], " ", 1)) if found else value
        ok = len(marks) == 1 and bool(found) and not re.search(r"\w", rest)
    return Verdict("correct") if ok else Verdict("wrong", "fact", mark)


def _choices_note(options) -> str:
    return f"（可选的有：{_choices(options)}）" if _choices(options) else ""


def _own_places(entry: AnswerEntry) -> tuple[str | None, str | None]:
    """The applicant's own state and country, as cli.plain_contacts put them on a city or address entry
    (aliases = (state, country), "" when profile.json leaves one out); None when unknown or unrecognised."""
    state, country = (*entry.aliases, "", "")[:2]
    return _place_key(state) if state else None, _place_key(country) if country else None


def _choices(options) -> str:
    return "、".join(o for o in options[:10] if not is_empty(o))


def _text(entry: AnswerEntry, value: str, options, company: str = "") -> Verdict:
    """text and choice: the answer or an accepted spelling (or one meaning the same, see _same_meaning); `otherwise`
    only when the page lacks the answer."""
    answers = (entry.answer, *entry.accept)
    if _same_meaning(value, answers, company):
        return Verdict("correct")
    offered = any(_same_meaning(option, answers, company) for option in options) if options else None
    if entry.otherwise and _same_meaning(value, [entry.otherwise], company):
        if offered:
            return Verdict("wrong", "fact", entry.answer, "页面上有标准答案的选项，不该选 otherwise")
        return Verdict("correct", note="看不到选项，按 otherwise 算对" if offered is None else "页面上没有答案的选项")
    expected = entry.otherwise if offered is False and entry.otherwise else entry.answer
    if offered is False and not entry.otherwise:
        shown = "、".join(o for o in options[:10] if not is_empty(o))
        if is_empty(value):
            return Verdict("no_answer", note=f"页面上没有标准答案的选项（可选的有：{shown}），也没写 otherwise")
        return Verdict(
            "no_answer", "guess", None, f"页面上没有标准答案的选项（可选的有：{shown}），填表 agent 却选了一项"
        )
    if is_empty(value):
        return _missing(expected)
    return Verdict("wrong", "fact", expected)


def _yes_or_no(text: str) -> str | None:
    first = norm(text).split(" ", 1)[0]
    return first if first in ("yes", "no") else None


def _yes_no(entry: AnswerEntry, value: str, control: str, options=()) -> Verdict:
    """Yes/No questions. A lone box (no options, or one such as "I agree") says No unticked and Yes ticked; a group of
    boxes or radios is read by the chosen option's text. `otherwise` counts only when the page lacks the answer."""
    answer = norm(entry.answer)
    shown = [o for o in options if not is_empty(o)]
    if _same(value, entry.accept):
        return Verdict("correct")
    if control == "checkbox" and len(shown) <= 1:  # a lone box: unticked says No, ticked says Yes, whatever its label
        said = "yes" if not is_empty(value) else "no"
        if said == answer:
            return Verdict("correct")
        return _missing(entry.answer, "没勾") if said == "no" else Verdict("wrong", "fact", entry.answer, "勾上了")
    offered = [o for o in shown if _yes_or_no(o) == answer or _same(o, entry.accept)]
    if entry.otherwise and _same(value, [entry.otherwise]):
        if offered:
            return Verdict("wrong", "fact", entry.answer, "页面上有标准答案的选项，不该选 otherwise")
        return Verdict("correct", note="按 otherwise")
    readable = [o for o in shown if _yes_or_no(o) or _same(o, entry.accept)]
    if shown and not readable:  # the page offers no Yes/No and no accepted spelling
        if is_empty(value):
            return Verdict(
                "no_answer", note=f"页面上没有标准答案的选项（可选的有：{_choices(shown)}），也没写 otherwise"
            )
        note = f"页面上没有标准答案的选项（可选的有：{_choices(shown)}），填表 agent 却选了一项"
        return Verdict("no_answer", "guess", None, note)
    if is_empty(value):  # before the first word: "No Response" is not a No
        return _missing(entry.answer)
    picks = [pick for pick in value.split("; ") if not is_empty(pick)]
    if len(picks) > 1:  # a group of boxes with more than one ticked
        return Verdict("wrong", "fact", entry.answer, "勾了不止一项")
    said = _yes_or_no(value)
    if said:
        return Verdict("correct") if said == answer else Verdict("wrong", "fact", entry.answer)
    if offered:  # the page offers the answer, and something else was chosen
        return Verdict("wrong", "fact", entry.answer)
    return Verdict("no_answer", note="选项不以 Yes 或 No 开头，也不在 accept 里")


def _month(expected: str, value: str, accept=(), otherwise=()) -> Verdict:
    if _same(value, accept):
        return Verdict("correct")
    if _same(value, otherwise):
        return Verdict("correct", note="按 otherwise")
    if is_empty(value):
        return _missing(expected)
    if norm(value) in ONGOING:
        return Verdict("wrong", "fact", expected, "写成了至今")
    want, got = parse_month(expected), parse_month(value)
    if want is None or got is None:
        return Verdict("not_reviewed", note="读不出月份，代码判断不了")
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
    if len(got) >= 2 and _is_range(value):
        low, high = got[0], got[1]
        ok = low <= floor <= high or low >= floor
    else:
        ok = got[0] >= floor
    return Verdict("correct") if ok else Verdict("wrong", "fact", f"不低于 {entry.answer}")


def compare_value(entry: AnswerEntry, value: str, *, options=(), control: str = "text", company: str = "") -> Verdict:
    """One field outside a segment against its entry. `company` is the job's company, for "<company> Careers
    Website" (decision 23)."""
    value = str(value or "")
    if entry.category == "contact":
        return _contact(entry, value)
    kind = entry.kind or "text"
    if kind == "yes_no":
        return _yes_no(entry, value, control, tuple(options))
    if kind == "number_min":
        return _number_min(entry, value)
    if kind == "month":
        return _month(entry.answer or "", value, entry.accept)
    if kind == "date":
        if _same(value, entry.accept):
            return Verdict("correct")
        if is_empty(value):
            return _missing(entry.answer)
        got, want = parse_date(value), parse_date(entry.answer or "")
        if got is None:
            return Verdict("not_reviewed", note="读不出日期，代码判断不了")
        return Verdict("correct") if got == want else Verdict("wrong", "fact", entry.answer)
    if kind == "url":
        if is_empty(value):
            return _missing(entry.answer)
        ok = _url(value) in {_url(a) for a in (entry.answer, *entry.accept) if a}
        return Verdict("correct") if ok else Verdict("wrong", "fact", entry.answer)
    return _text(entry, value, tuple(options), company)


# --- record segments --------------------------------------------------------------------------------------------


def split_range(value: str) -> tuple[str, str] | None:
    """A start and an end in one field, split on " - ", "–", " to " ..., or on a bare hyphen between two dates."""
    pieces = RANGE_SEPARATOR.split(value.strip(), maxsplit=1)
    if len(pieces) == 2:
        return pieces[0], pieces[1]
    for at in [i for i, c in enumerate(value) if c == "-"]:
        left, right = value[:at], value[at + 1 :]
        if parse_month(left) and (parse_month(right) or norm(right) in ONGOING):
            return left, right
    return None


def _range(entry: AnswerEntry, value: str) -> Verdict:
    want = f"{entry.record['start']} – {entry.record['end']}"
    if is_empty(value):
        return _missing(want)
    tokens = split_range(value)
    if tokens is None:
        return Verdict("not_reviewed", note="读不出起止，代码判断不了")
    halves = [
        _month(entry.record[part], token, entry.record_accept.get(part, ()), entry.record_otherwise.get(part, ()))
        for part, token in zip(("start", "end"), tokens, strict=True)
    ]
    if all(half.result == "correct" for half in halves):
        return Verdict("correct")
    if any(half.result == "wrong" for half in halves):
        return Verdict("wrong", "fact", want)
    return Verdict("not_reviewed", note="读不出起止，代码判断不了")


def compare_part(entry: AnswerEntry, part: str, value: str, *, options=()) -> Verdict:
    """One field of a matched segment against its record entry: `part` is a record field, "range" (start and end
    in one field) or "other" (a description and the like, which code cannot judge). `options` are the field's
    choices when the page shows them: then an otherwise pick is right only if the original value is not offered."""
    value = str(value or "")
    if part == "other":
        return Verdict("no_answer") if is_empty(value) else Verdict("not_reviewed", note="描述类的栏，代码判断不了")
    if part == "range":
        return _range(entry, value)
    if part not in entry.record:
        return Verdict("no_answer") if is_empty(value) else Verdict("not_reviewed", note="标准答案里没有这一栏")
    expected = entry.record[part]
    names = (expected, *entry.record_accept.get(part, ()))
    if part in ("school", "company"):
        names = (*names, *entry.aliases)
    if _same(value, names):
        return Verdict("correct")
    otherwise = entry.record_otherwise.get(part, ())
    if otherwise and _same(value, otherwise):
        if options and any(_same(option, names) for option in options):
            return Verdict("wrong", "fact", expected, "页面上有原值的选项，不该选 otherwise")
        return Verdict("correct", note="按 otherwise")
    text_part = part in ("school", "company", "degree", "major", "title")
    lacks = text_part and bool(options) and not any(_same(option, names) for option in options)
    if lacks and not otherwise:
        if is_empty(value):
            return Verdict(
                "no_answer", note=f"页面上没有标准答案的选项（可选的有：{_choices(options)}），也没写 otherwise"
            )
        note = f"页面上没有标准答案的选项（可选的有：{_choices(options)}），填表 agent 却选了一项"
        return Verdict("no_answer", "guess", None, note)
    if is_empty(value):
        return _missing(" 或 ".join(otherwise) if lacks else expected)
    if part in ("start", "end"):
        return _month(expected, value)
    if part == "city":
        head = re.split(r"\s*[,(·|]\s*", value.strip(), maxsplit=1)[0]
        tail = next((norm(value)[len(norm(n)) :].strip() for n in names if norm(value).startswith(norm(n) + " ")), "")
        if _same(head, names) or (tail and (_place_key(tail) or norm(tail) in UNITED_STATES)):
            return Verdict("wrong", "format", expected, "只写城市名，不写州和国家")
        return Verdict("wrong", "fact", expected)
    if otherwise and text_part and options and not lacks:  # the page offers the original and something else was chosen
        return Verdict("wrong", "fact", expected, "页面上有原值的选项")
    if otherwise:
        picks = " 或 ".join(otherwise)
        return Verdict("not_reviewed", note=f"可能是最相近的选项；原值 {expected}，找不到时选 {picks}")
    return Verdict("wrong", "fact", expected)


def segment_score(entry: AnswerEntry, values: dict[str, str]) -> int:
    """How many of a segment's filled fields (record field -> value) agree with a record entry."""
    return sum(
        1
        for part, value in values.items()
        if part != "other" and not is_empty(value) and compare_part(entry, part, value).result == "correct"
    )


# --- a page -----------------------------------------------------------------------------------------------------


def _shown(value: str, entry: AnswerEntry | None, ambiguous) -> str:
    """The value as the report shows it. A field matched to a masked contact entry shows only placeholders: what
    masking missed there is someone else's contact value (spec 5.6)."""
    contact = (entry is not None and entry.category == "contact" and entry.answer is None) or any(
        key.startswith("contact.") for key in ambiguous
    )
    return contact_shown(value) if contact else value


def contact_shown(value: str) -> str:
    """A contact field's value with only its placeholders and place names (states, countries) kept: any other text is
    a contact value masking missed, shown as HIDDEN (spec 5.6, masking by column)."""
    if is_empty(value):
        return value
    pieces = re.split(r"(\[[^\[\]]+\])", value)
    kept = [
        piece
        if MARK.fullmatch(piece)
        or not re.search(r"\w", piece)
        or all(_place_key(w) for w in PLACE_PHRASE.sub(" ", norm(piece)).split())
        else HIDDEN
        for piece in pieces
    ]
    return "".join(kept)


def check_page(
    fields, answers: dict[str, AnswerEntry], contacts: bool = True, company: str = ""
) -> tuple[CheckResult, ...]:
    """Every field of the page checked, in page order, then one result per record segment the page lacks.
    `contacts=False` for a page saved before redact.VERSION: its contact fields are not reviewed (decision 20).
    `company` is the job's company (decision 23)."""
    fields = list(fields)
    found = match_page(fields, answers, score=segment_score)
    labels = {segment.index: segment.label for segment in found.segments}
    values = {f.id: f.value for f in fields}
    blank = {  # extra or unanchored segments with nothing filled in: an empty block, not a wrong fact
        segment.index
        for segment in found.segments
        if (segment.extra or segment.unanchored) and all(is_empty(values[fid]) for fid in segment.field_ids)
    }
    results = []
    for f in fields:
        m = found.fields[f.id]
        entry = answers.get(m.key) if m.key else None
        base = {
            "field_id": f.id,
            "question": f.question,
            "value": _shown(f.value, entry, m.ambiguous),
            "key": m.key,
            "segment": m.segment,
            "candidates": m.ambiguous,
        }
        if m.navigation:
            verdict = Verdict("no_answer", note=NAV_NOTE)
        elif m.ambiguous and m.segment is not None:
            verdict = Verdict("ambiguous", note="这一段对到了 " + "、".join(m.ambiguous) + "，代码分不出是哪一条")
        elif m.ambiguous:
            verdict = Verdict("ambiguous", note="对到了 " + "、".join(m.ambiguous))
        elif m.segment in blank:
            verdict = Verdict("no_answer", note=EMPTY_BLOCK)
        elif m.extra:
            label = labels.get(m.segment) or "（没有公司或学校名）"
            verdict = (
                Verdict("no_answer", note=f"{EXTRA}（{label}）里空着的栏")
                if is_empty(f.value)
                else Verdict("wrong", "fact", None, f"{EXTRA}（{label}），标准答案里没有这一段")
            )
        elif m.unanchored:
            if m.part in ("school", "company"):
                verdict = _missing(None, "这一段的学校或公司没填，对不上标准答案里的哪一段")
            elif is_empty(f.value):
                verdict = Verdict("no_answer")
            else:
                verdict = Verdict("not_reviewed", note="这一段没填学校或公司，代码对不上是哪一段")
        elif entry is not None and entry.category == "contact" and entry.answer is None and not contacts:
            # an empty field is empty in any masking format; a filled one cannot be told the applicant's own
            verdict = (
                _missing(placeholder(entry.from_profile))
                if is_empty(f.value)
                else Verdict("not_reviewed", note=OLD_PAGE)
            )
        elif entry is not None and m.segment is not None:
            verdict = compare_part(entry, m.part or "other", f.value, options=f.options)
            if verdict == Verdict("no_answer") and f.required:
                verdict = Verdict("no_answer", "missing", None, REQUIRED_EMPTY + _choices_note(f.options))
        elif entry is not None:
            verdict = compare_value(entry, f.value, options=f.options, control=f.control, company=company)
        elif is_empty(f.value) and f.required:
            verdict = Verdict("no_answer", "missing", None, REQUIRED_EMPTY + _choices_note(f.options))
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
