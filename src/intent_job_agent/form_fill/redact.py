"""Redaction at write time and the leak scan of written files (specs/form-fill.md 5.6; slice-1 plan, check 3).

Two ways, both applied: the applicant's own contact values from profile.json (with their normalized variants), and
anything shaped like an e-mail address or a phone number. Names are not contact details (spec 2.3) and stay.
Raw values are never printed, logged or put in error messages; errors name only the field or kind.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from intent_job_agent.domain import InvariantError

KINDS = ("email", "phone", "address", "postal_code", "url", "password", "other_email", "other_phone")
PLACEHOLDER = {
    "email": "[email]",
    "phone": "[phone]",
    "address": "[address]",
    "postal_code": "[postal_code]",
    "url": "[url]",
    "password": "[已填]",
    "other_email": "[email]",
    "other_phone": "[phone]",
}
FIELDS = {  # personal.<field> in ApplyPilot's profile format -> kind
    "email": "email",
    "phone": "phone",
    "address": "address",
    "city": "address",
    "postal_code": "postal_code",
    "linkedin_url": "url",
    "github_url": "url",
    "portfolio_url": "url",
    "website_url": "url",
    "password": "password",
}
MIN_LEN = 4
MIN_PHONE_DIGITS = 7
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2"}
)
_STREET_WORDS = (
    ("street", "st"),
    ("avenue", "ave", "av"),
    ("road", "rd"),
    ("boulevard", "blvd"),
    ("drive", "dr"),
    ("lane", "ln"),
    ("court", "ct"),
    ("place", "pl"),
    ("terrace", "ter"),
    ("highway", "hwy"),
    ("parkway", "pkwy"),
    ("apartment", "apt"),
    ("suite", "ste"),
    ("north", "n"),
    ("south", "s"),
    ("east", "e"),
    ("west", "w"),
)
_SEP = r"[\s().-]*"
_OTHER_EMAIL = re.compile(r"(?<![\w.+%-])[\w.+-]+(?:@|%40)[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?![\w-])", re.I)
_US_PHONE = r"(?:\+?1[\s.-]*)?(?:\(\d{3}\)\s*|\d{3}[\s.-])\d{3}[\s.-]\d{4}"
_INTL_PHONE = r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}"
_OTHER_PHONE = re.compile(rf"(?<![\w+])(?:{_US_PHONE}|{_INTL_PHONE})(?!\d)")


def _email(value: str) -> str:
    local, _, domain = value.strip().partition("@")
    if not domain:
        return re.escape(value.strip())
    return rf"(?<![\w.+%-]){re.escape(local)}(?:@|%40){re.escape(domain)}(?![\w-]|\.\w)"


def _phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    prefix = r"(?:\+?\s*1[\s.-]*)?" if len(digits) == 10 else r"\+?\s*"
    return rf"(?<![\w+]){prefix}\(?{_SEP.join(digits)}(?!\d)"


def _words(value: str) -> str | None:
    tokens = value.replace(",", " ").split()
    if not tokens:
        return None
    parts = []
    for token in tokens:
        bare = token.rstrip(".").lower()
        forms = next((group for group in _STREET_WORDS if bare in group), None)
        parts.append(rf"(?:{'|'.join(forms)})\.?" if forms else re.escape(token))
    return r"(?<!\w)" + r"[\s,]+".join(parts) + r"(?!\w)"


def _postal(value: str) -> str | None:
    chars = re.sub(r"[^0-9a-z]", "", value.lower())
    if len(chars) < MIN_LEN:
        return None
    tail = r"(?:-\d{4})?" if chars.isdigit() and len(chars) == 5 else ""
    return r"(?<!\w)" + r"[\s-]?".join(chars) + tail + r"(?!\w)"


def _url(value: str) -> str | None:
    core = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value.strip(), flags=re.I)
    core = re.sub(r"^www\.", "", core, flags=re.I).rstrip("/")
    if len(core) < MIN_LEN:
        return None
    return rf"(?<![\w.@/-])(?:[a-z][a-z0-9+.-]*://)?(?:www\.)?{re.escape(core)}/?(?![\w-])"


_BUILD: dict[str, Callable[[str], str | None]] = {
    "email": _email,
    "phone": _phone,
    "address": _words,
    "postal_code": _postal,
    "url": _url,
    "password": lambda value: re.escape(value),
}


class Redactor:
    """Masks the applicant's contact values and password, plus any e-mail- or phone-shaped text."""

    def __init__(self, patterns: list[tuple[str, re.Pattern[str]]]):
        self._patterns = patterns

    def __repr__(self) -> str:
        return f"Redactor({len(self._patterns)} patterns)"

    @classmethod
    def from_profile(cls, profile: dict) -> "Redactor":
        personal = profile.get("personal") if isinstance(profile, dict) else None
        if not isinstance(personal, dict):
            raise InvariantError("profile: missing the personal section")
        found: list[tuple[int, str, str]] = []
        for field, kind in FIELDS.items():
            value = personal.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise InvariantError(f"profile: personal.{field} must be text ({kind})")
            value = value.strip()
            if len(value) < MIN_LEN:
                continue
            pattern = _BUILD[kind](value)
            if pattern:
                found.append((len(value), kind, pattern))
        found.sort(key=lambda item: -item[0])  # longer values first, so a city inside an address goes with it
        return cls([(kind, re.compile(pattern, re.I)) for _, kind, pattern in found])

    def _apply(self, text: str) -> tuple[str, Counter]:
        hits: Counter = Counter()
        for kind, pattern in self._patterns:
            text, n = pattern.subn(PLACEHOLDER[kind], text)
            hits[kind] += n
        text, n = _OTHER_EMAIL.subn(PLACEHOLDER["other_email"], text)
        hits["other_email"] += n

        def phone(match: re.Match[str]) -> str:
            if not 10 <= len(re.sub(r"\D", "", match.group())) <= 15:
                return match.group()
            hits["other_phone"] += 1
            return PLACEHOLDER["other_phone"]

        return _OTHER_PHONE.sub(phone, text), hits

    def mask(self, text: str) -> str:
        return self._apply(text)[0]

    def mask_json(self, obj):
        """Masks every string in a JSON-like value, dict keys included; other values are kept."""
        if isinstance(obj, str):
            return self.mask(obj)
        if isinstance(obj, dict):
            return {self.mask_json(key): self.mask_json(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.mask_json(item) for item in obj]
        return obj

    def scan(self, paths: Iterable[str | Path]) -> dict[str, dict[str, int]]:
        """Hit counts per file and kind (never the matched text). Directories are walked; binary files are listed
        under "_skipped" with 0."""
        report: dict[str, dict[str, int]] = {"_skipped": {}}
        for path in _files(paths):
            data = path.read_bytes()
            if path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in data[:8192]:
                report["_skipped"][str(path)] = 0
                continue
            hits = self._apply(data.decode("utf-8", errors="replace"))[1]
            report[str(path)] = {kind: hits[kind] for kind in KINDS}
        return report


def total_hits(report: dict[str, dict[str, int]]) -> int:
    """All hits in a scan report; skipped files do not count."""
    return sum(sum(hits.values()) for name, hits in report.items() if name != "_skipped")


def _files(paths: Iterable[str | Path]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*") if p.is_file()))
        elif path.is_file():
            out.append(path)
    return out
