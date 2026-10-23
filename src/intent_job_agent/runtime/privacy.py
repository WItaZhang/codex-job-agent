"""Non-optional node projection/redaction, also used for fake requests.

This is not an arbitrary-Python sandbox or a detector for every natural-language secret.
No file-reading functions exist here; structured sources must be supplied by trusted services.
"""

import json
import re

from .config import Budgets
from .contracts import RuntimeFault

# Closed fields and types: callers cannot invent a new category to smuggle a file.
FIELDS = {
    "job": {"title": str, "company": str, "description": str, "location": str},
    "job_tags": {"tags": list, "title": str},
    "vocabulary": {"entries": list, "rules": list},
    "search": {"terms": list, "locations": list},
    "intent": {"entries": list},
    "feedback": {"text": str, "labels": list},
    "candidates": {"summaries": list},
    "resume_summary": {"experience": list, "skills": list},
    "letter_material": {"experience": list, "skills": list, "rules": list, "title": str},
    "letter_header": {"name": str, "email": str, "phone": str},
    "form_fields": {"name": str, "email": str, "phone": str, "answers": list},
    "attachment_refs": {"handles": list},
    "tool_result": {"text": str},
}
POLICIES = {
    "discovery": ("job", "search"),
    "tagging": ("job", "vocabulary"),
    "analysis": ("intent", "feedback", "job_tags", "vocabulary", "candidates", "resume_summary"),
    "intent_edit": ("intent", "feedback", "vocabulary", "candidates"),
    "letter": ("job", "letter_material", "letter_header"),
    "form_fill": ("job", "form_fields", "attachment_refs", "tool_result"),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{6,}\d(?!\w)")
_SECRET = re.compile(r"(?i)\b(password|api[_ -]?key|ssn|token)\s*[:=]\s*\S+")
_HANDLE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def redact(text: str, contacts: bool = False) -> str:
    text = _SECRET.sub("[redacted]", text)
    if not contacts:
        text = _EMAIL.sub("[redacted]", text)
        text = _PHONE.sub("[redacted]", text)
    return text


def bounded_json(value, max_bytes: int, max_depth: int) -> str:
    def check(item, depth):
        if depth > max_depth:
            raise RuntimeFault("budget_exhausted")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise RuntimeFault("schema_violation")
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise RuntimeFault("schema_violation")

    try:
        check(value, 0)
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except RuntimeFault:
        raise
    except (ValueError, TypeError, RecursionError):
        raise RuntimeFault("schema_violation") from None
    if len(text.encode()) > max_bytes:
        raise RuntimeFault("budget_exhausted")
    return text


def assemble(node: str, data: dict, categories: list[str], budgets: Budgets) -> dict:
    bounded_json(data, budgets.max_input_bytes, budgets.max_depth)
    if (
        node not in POLICIES
        or type(data) is not dict
        or type(categories) is not list
        or any(type(c) is not str for c in categories)
        or len(set(categories)) != len(categories)
        or set(data) != set(categories)
        or not set(categories) <= set(POLICIES[node])
    ):
        raise RuntimeFault("permission_denied")
    clean = {}
    for category, values in data.items():
        if type(values) is not dict or not set(values) <= set(FIELDS[category]):
            raise RuntimeFault("permission_denied")
        clean[category] = {}
        for field, value in values.items():
            expected = FIELDS[category][field]
            if type(value) is not expected or (expected is list and any(type(x) is not str for x in value)):
                raise RuntimeFault("schema_violation")
            if category == "attachment_refs" and any(not _HANDLE.fullmatch(x) for x in value):
                raise RuntimeFault("permission_denied")
            contacts = category in ("letter_header", "form_fields") and field in ("name", "email", "phone")
            clean[category][field] = (
                [redact(x) for x in value] if expected is list else redact(value, contacts=contacts)
            )
    body = {"policy_version": 1, "node": node, "categories": list(categories), "data": clean}
    bounded_json(body, budgets.max_input_bytes, budgets.max_depth)
    return body
