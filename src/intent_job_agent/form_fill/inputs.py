"""Read and check the form-fill inputs (specs/form-fill.md 2.1, 2.3, 2.4): the job list, answers.yaml and
checklist.yaml. Every problem in a file is collected and reported together in one InvariantError.

profile.json is only walked for the paths of its fields (4.1); its values are never returned, logged or put in an
error message. Error messages about answers.yaml never echo values either (a contact value written by mistake must
not leak into a log).
"""

import datetime
import json
import re
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from ..domain import InvariantError
from .models import CATEGORIES, RECORD_FIELDS, AnswerEntry, ChecklistItem, JobList, Kind, ListEntry

KINDS = get_args(Kind)
VALUE_FIELDS = ("answer", "from_profile", "from_applicant")
ENTRY_FIELDS = (*VALUE_FIELDS, "kind", "accept", "otherwise", "asks", "overrides", "source")
CONTACT_FIELDS = ("from_profile", "asks", "source")
RECORD_ENTRY_FIELDS = ("kind", "accept", "otherwise", "aliases", "source", *RECORD_FIELDS)
EDUCATION_FIELDS = ("school", "degree", "major")
EXPERIENCE_FIELDS = ("company", "title")
KEY = re.compile(r"^([a-z_]+)\.([a-z0-9_]+)$")
NUMBER = re.compile(r"\d")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Scalars that YAML would turn into numbers, booleans or dates stay text as written ("3.60", "Yes", "2027-05").
_TEXT_TAGS = {"tag:yaml.org,2002:" + name for name in ("bool", "int", "float", "timestamp")}


class _Loader(yaml.SafeLoader):
    """Safe YAML that records duplicate mapping keys instead of keeping the last one."""

    def __init__(self, stream):
        super().__init__(stream)
        self.duplicates: list[str] = []

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = key_node.value if isinstance(key_node, yaml.ScalarNode) else None
            if key in seen:
                self.duplicates.append(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class _TextLoader(_Loader):
    pass


_TextLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag not in _TEXT_TAGS]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _fail(path: Path, problems: list[str]) -> None:
    if problems:
        lines = "\n".join(f"- {problem}" for problem in problems)
        raise InvariantError(f"{path}: {len(problems)} problem(s):\n{lines}")


def _read_yaml(path: Path, loader: type[_Loader]) -> tuple[Any, list[str]]:
    path = Path(path)
    if not path.is_file():
        raise InvariantError(f"{path} is missing")
    stream = loader(path.read_text(encoding="utf-8"))
    try:
        data = stream.get_single_data()
    except yaml.YAMLError as error:
        raise InvariantError(f"{path}: not valid YAML: {error}") from None
    finally:
        stream.dispose()
    return data, list(stream.duplicates)


def _text(value: Any) -> Any:
    """Scalars as text; anything else (lists, mappings, None) unchanged."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime.date | int | float):
        return value.isoformat() if isinstance(value, datetime.date) else str(value)
    return value


def _top(data: Any, fields: tuple[str, ...], problems: list[str]) -> dict:
    if not isinstance(data, dict):
        problems.append(f"the file must be a mapping with {', '.join(fields)}")
        return {}
    problems.extend(f"unknown field {key!r}" for key in data if key not in fields)
    return data


def _pydantic_problems(prefix: str, error: ValidationError) -> list[str]:
    return [f"{prefix}{'.'.join(str(p) for p in item['loc'])}: {item['msg']}" for item in error.errors()]


def _validate(model: type[BaseModel], raw: dict, prefix: str, problems: list[str]) -> BaseModel | None:
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        problems.extend(_pydantic_problems(prefix, error))
        return None


# --- job list (2.1) ---------------------------------------------------------------------------------------------


def load_list(path: Path, resume_ids: set[str]) -> JobList:
    """The job list. A cover letter path is resolved against the list's folder and returned absolute."""
    path = Path(path)
    data, problems = _read_yaml(path, _Loader)
    data = _top(data, ("jobs",), problems)
    raw_jobs = data.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        problems.append("jobs must be a non-empty list")
        raw_jobs = []
    jobs: list[ListEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_jobs):
        where = f"jobs[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        raw = {key: (_text(value) if key != "revisit" else value) for key, value in raw.items()}
        if isinstance(raw.get("id"), str):
            where = f"jobs[{index}] ({raw['id']})"
        entry = _validate(ListEntry, raw, f"{where}: ", problems)
        if entry is None:
            continue
        before = len(problems)
        if entry.id in seen:
            problems.append(f"{where}: duplicate id {entry.id!r}")
        seen.add(entry.id)
        problems.extend(
            f"{where}: {name} is empty" for name in ("company", "title", "resume") if not getattr(entry, name).strip()
        )
        if not entry.url.startswith("https://") or len(entry.url) <= len("https://"):
            problems.append(f"{where}: url must start with https://")
        if entry.resume not in resume_ids:
            problems.append(f"{where}: unknown resume {entry.resume!r}; choose from {sorted(resume_ids)}")
        letter = _letter(path, entry.cover_letter, where, problems)
        if len(problems) == before:
            jobs.append(entry.model_copy(update={"cover_letter": letter}))
    _fail(path, problems)
    return JobList(jobs=jobs)


def _letter(list_path: Path, value: str, where: str, problems: list[str]) -> str:
    if value == "none":
        return value
    if not value.endswith(".txt"):
        problems.append(f"{where}: cover_letter must be 'none' or a .txt file (its .pdf sits next to it)")
        return value
    letter = Path(value).expanduser()
    letter = (letter if letter.is_absolute() else list_path.parent / letter).resolve()
    for needed in (letter, letter.with_suffix(".pdf")):
        if not needed.is_file():
            problems.append(f"{where}: cover letter file {needed} is missing (from {value})")
    return str(letter)


# --- answers.yaml (2.3) -----------------------------------------------------------------------------------------


def load_answers(path: Path, profile_fields: set[str], applicant_fields: set[str]) -> dict[str, AnswerEntry]:
    """answers.yaml keyed by entry key. `profile_fields` are profile.json paths (profile_field_paths), and
    `applicant_fields` the applicant.yaml fields a from_applicant reference may name."""
    path = Path(path)
    data, problems = _read_yaml(path, _TextLoader)
    data = _top(data, ("version", "entries"), problems)
    if _text(data.get("version")) != "1":
        problems.append("version must be 1")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, dict):
        problems.append("entries must be a mapping of <category>.<name> to entries")
        raw_entries = {}
    entries: dict[str, AnswerEntry] = {}
    for key, raw in raw_entries.items():
        entry = _answer_entry(str(key), raw, profile_fields, applicant_fields, problems)
        if entry is not None:
            entries[entry.key] = entry
    problems.extend(f"{key} is required" for key in ("name.first", "name.last") if key not in raw_entries)
    _check_overrides(raw_entries, problems)
    _fail(path, problems)
    return entries


def _answer_entry(
    key: str, raw: Any, profile_fields: set[str], applicant_fields: set[str], problems: list[str]
) -> AnswerEntry | None:
    before = len(problems)
    match = KEY.match(key)
    if not match:
        problems.append(f"{key}: keys are <category>.<name> (lower case, one dot)")
    elif match.group(1) not in CATEGORIES:
        problems.append(f"{key}: unknown category {match.group(1)!r}; choose from {', '.join(CATEGORIES)}")
    if not isinstance(raw, dict):
        problems.append(f"{key}: must be a mapping of fields")
        return None
    raw = {str(name): _text(value) for name, value in raw.items()}
    category = key.split(".", 1)[0]
    if category == "contact":
        fields = _contact(key, raw, problems)
    elif raw.get("kind") == "record":
        fields = _record(key, category, raw, problems)
    else:
        fields = _plain(key, raw, problems)
    if isinstance(raw.get("from_profile"), str) and raw["from_profile"] not in profile_fields:
        problems.append(f"{key}: from_profile {raw['from_profile']!r} is not a field of profile.json")
    if isinstance(raw.get("from_applicant"), str) and raw["from_applicant"] not in applicant_fields:
        problems.append(f"{key}: from_applicant {raw['from_applicant']!r} is not a field of applicant.yaml")
    if len(problems) > before:
        return None
    return AnswerEntry(key=key, **fields)


def _unknown(key: str, raw: dict, allowed: tuple[str, ...], problems: list[str], hint: str = "") -> None:
    problems.extend(f"{key}: unknown field {name!r}{hint}" for name in raw if name not in allowed)


def _contact(key: str, raw: dict, problems: list[str]) -> dict:
    extra = [name for name in raw if name not in CONTACT_FIELDS]
    if extra:
        problems.append(
            f"{key}: contact entries may only use from_profile (with optional asks and source); "
            f"not {', '.join(extra)}. Contact details live only in profile.json"
        )
    if raw.get("from_profile") is None:
        problems.append(f"{key}: needs from_profile")
    return {
        "from_profile": _string(key, "from_profile", raw.get("from_profile"), problems),
        "asks": _strings(key, "asks", raw.get("asks"), problems),
        "source": _string(key, "source", raw.get("source"), problems),
    }


def _plain(key: str, raw: dict, problems: list[str]) -> dict:
    _unknown(key, raw, ENTRY_FIELDS, problems, " (a record entry needs kind: record)")
    given = [name for name in VALUE_FIELDS if raw.get(name) is not None]
    if len(given) != 1:
        problems.append(f"{key}: needs exactly one of answer, from_profile, from_applicant")
    kind = raw.get("kind")
    if kind is not None and kind not in KINDS:
        problems.append(f"{key}: unknown kind {kind!r}; choose from {', '.join(KINDS)}")
    answer = _string(key, "answer", raw.get("answer"), problems)
    if answer is not None and kind == "yes_no" and answer.lower() not in ("yes", "no"):
        problems.append(f"{key}: a yes_no answer must be Yes or No")
    if answer is not None and kind == "number_min" and not NUMBER.search(answer):
        problems.append(f"{key}: a number_min answer must contain a number")
    return {
        "answer": answer,
        "from_profile": _string(key, "from_profile", raw.get("from_profile"), problems),
        "from_applicant": _string(key, "from_applicant", raw.get("from_applicant"), problems),
        "kind": kind if kind in KINDS else None,
        "accept": _strings(key, "accept", raw.get("accept"), problems),
        "otherwise": _string(key, "otherwise", raw.get("otherwise"), problems),
        "asks": _strings(key, "asks", raw.get("asks"), problems),
        "overrides": _strings(key, "overrides", raw.get("overrides"), problems),
        "source": _string(key, "source", raw.get("source"), problems),
    }


def _record(key: str, category: str, raw: dict, problems: list[str]) -> dict:
    if any(raw.get(name) is not None for name in VALUE_FIELDS):
        problems.append(f"{key}: a record carries no answer, from_profile or from_applicant; it has record fields")
    _unknown(key, {k: v for k, v in raw.items() if k not in VALUE_FIELDS}, RECORD_ENTRY_FIELDS, problems)
    if category == "education":
        own, other, label = EDUCATION_FIELDS, EXPERIENCE_FIELDS, "an education record needs school, degree, major"
    elif category == "experience":
        own, other, label = EXPERIENCE_FIELDS, EDUCATION_FIELDS, "an experience record needs company, title"
    else:
        problems.append(f"{key}: a record belongs to education or experience")
        return {}
    if any(name in raw for name in other):
        problems.append(f"{key}: {label} (not {', '.join(other)})")
    record = {}
    for name in (*own, "start", "end"):
        if raw.get(name) is None:
            problems.append(f"{key}: record needs {name}")
        else:
            record[name] = _string(key, name, raw[name], problems)
    for name in ("start", "end"):
        if isinstance(record.get(name), str) and not MONTH.match(record[name]):
            problems.append(f"{key}: {name} must be a month like 2025-09 (other spellings go in accept)")
    if raw.get("city") is not None:
        record["city"] = _string(key, "city", raw["city"], problems)
    fields = (*own, "start", "end", "city")
    accept = raw.get("accept")
    record_accept: dict[str, tuple[str, ...]] = {}
    if accept is not None and not isinstance(accept, dict):
        problems.append(f"{key}: accept of a record must map record fields to lists, e.g. degree: [M.S., MS]")
    elif accept:
        for name, values in accept.items():
            if name not in fields:
                problems.append(f"{key}: accept names {name!r}, which is not a field of this record")
            record_accept[str(name)] = _strings(key, f"accept.{name}", values, problems)
    otherwise = raw.get("otherwise")
    record_otherwise: dict[str, tuple[str, ...]] = {}
    if otherwise is not None and not isinstance(otherwise, dict):
        problems.append(f"{key}: otherwise of a record must map record fields to text, e.g. major: Computer Science")
    elif otherwise:
        for name, value in otherwise.items():
            if name not in fields:
                problems.append(f"{key}: otherwise names {name!r}, which is not a field of this record")
            value = _text(value)
            if isinstance(value, list):
                picks = _strings(key, f"otherwise.{name}", value, problems)
            elif isinstance(value, str):
                picks = tuple(text for text in [_string(key, f"otherwise.{name}", value, problems)] if text)
            else:
                problems.append(f"{key}: otherwise.{name} must be text or a list of text")
                picks = ()
            if picks:
                record_otherwise[str(name)] = picks
    return {
        "kind": "record",
        "record": record,
        "record_accept": record_accept,
        "record_otherwise": record_otherwise,
        "aliases": _strings(key, "aliases", raw.get("aliases"), problems),
        "source": _string(key, "source", raw.get("source"), problems),
    }


def _string(key: str, name: str, value: Any, problems: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        problems.append(f"{key}: {name} must be text")
        return None
    if not value.strip():
        problems.append(f"{key}: {name} is empty")
        return None
    return value


def _strings(key: str, name: str, value: Any, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.append(f"{key}: {name} must be a list")
        return ()
    items = [_text(item) for item in value]
    if not all(isinstance(item, str) and item.strip() for item in items):
        problems.append(f"{key}: every item of {name} must be non-empty text")
        return ()
    return tuple(items)


def _check_overrides(raw_entries: dict, problems: list[str]) -> None:
    graph: dict[str, list[str]] = {}
    for key, raw in raw_entries.items():
        targets = raw.get("overrides") if isinstance(raw, dict) else None
        if not isinstance(targets, list):
            continue
        graph[key] = [str(target) for target in targets]
        problems.extend(
            f"{key}: overrides unknown key {target!r}" for target in graph[key] if target not in raw_entries
        )
    reported: set[frozenset[str]] = set()
    for start in graph:
        _find_cycle(start, [start], graph, reported, problems)


def _find_cycle(node: str, trail: list[str], graph: dict, reported: set, problems: list[str]) -> None:
    for target in graph.get(node, []):
        if target == trail[0]:
            cycle = frozenset(trail)
            if cycle not in reported:
                reported.add(cycle)
                problems.append(f"overrides form a cycle: {' -> '.join([*trail, target])}")
        elif target not in trail:
            _find_cycle(target, [*trail, target], graph, reported, problems)


# --- checklist.yaml (2.4) ---------------------------------------------------------------------------------------


def load_checklist(path: Path) -> tuple[ChecklistItem, ...]:
    path = Path(path)
    data, problems = _read_yaml(path, _TextLoader)
    data = _top(data, ("version", "items"), problems)
    if _text(data.get("version")) != "1":
        problems.append("version must be 1")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        problems.append("items must be a list")
        raw_items = []
    items: list[ChecklistItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        where = f"items[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        item = _validate(ChecklistItem, {key: _text(value) for key, value in raw.items()}, f"{where}: ", problems)
        if item is None:
            continue
        where = f"{where} ({item.id})"
        if not item.id.strip() or not item.rule.strip():
            problems.append(f"{where}: id and rule must not be empty")
        if item.id in seen:
            problems.append(f"{where}: duplicate id {item.id!r}")
        seen.add(item.id)
        items.append(item)
    _fail(path, problems)
    return tuple(items)


# --- profile.json (4.1) -----------------------------------------------------------------------------------------


def profile_field_paths(profile_path: Path) -> set[str]:
    """Dotted paths of the filled leaves of profile.json (personal.email, ...). Empty values ("" or null) do not
    count, so a reference to them fails. Only the paths leave this function, never a value."""
    path = Path(profile_path)
    if not path.is_file():
        raise InvariantError(f"{path} is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:  # the message holds a position only, never the document
        raise InvariantError(f"{path}: not valid JSON (line {error.lineno}, column {error.colno})") from None
    if not isinstance(data, dict):
        raise InvariantError(f"{path}: must be a JSON object")
    paths: set[str] = set()
    _walk(data, "", paths)
    return paths


def _walk(node: dict, prefix: str, paths: set[str]) -> None:
    for name, value in node.items():
        dotted = f"{prefix}{name}"
        if isinstance(value, dict):
            _walk(value, f"{dotted}.", paths)
        elif value not in (None, "", []):
            paths.add(dotted)
