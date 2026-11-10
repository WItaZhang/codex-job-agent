"""Original-tab continuation identity and one-time email-code validation.

No credential reads, browser starts, submission or automatic resend happens here.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from ..domain import InvariantError

CODE_ENV = "INTENT_FORM_FILL_EMAIL_CODE"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class Source(NamedTuple):
    run_id: str
    job_id: str
    url: str
    company: str
    title: str
    tab_url: str
    tab_id: str
    generation: str
    started: str
    gate: str


def load_source(data_dir, settings, run_id: str, job_id: str) -> Source:
    if not all(SAFE_ID.fullmatch(value) for value in (run_id, job_id)):
        raise InvariantError("Invalid form-fill run or job identifier")
    root = Path(data_dir).resolve() / settings.form_fill.dir / "runs"
    folder = root / run_id / job_id
    if not folder.resolve().is_relative_to(root.resolve()):
        raise InvariantError("Form-fill source is outside this data directory")
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        generation = meta.get("generation")
        if not generation:
            # Old runs saved generation in checkpoint.json, but omitted it from meta.
            config = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
            if (
                config["run_id"],
                config["job_id"],
                Path(config["root"]).resolve(),
                Path(config["db_path"]).resolve(),
            ) != (run_id, job_id, Path(data_dir).resolve(), Path(settings.storage.db_path).resolve()):
                raise ValueError("scope")
            generation = config["binding"]["generation"]
        required = [meta[key] for key in ("url", "company", "title", "tab", "tab_id", "started", "agent_result")]
        if meta["job_id"] != job_id or not generation or not all(isinstance(v, str) for v in required):
            raise ValueError("identity")
        if not meta["tab_id"] or not meta["tab"] or meta.get("checks", {}).get("not_submitted") is not True:
            raise ValueError("unsafe source")
        if meta["agent_result"] in ("dry_run", "already_applied", "applied"):
            raise ValueError("already complete")
        return Source(run_id, job_id, *required[:3], required[3], required[4], str(generation), *required[5:])
    except (OSError, KeyError, ValueError, TypeError):
        raise InvariantError(
            "Original form-fill identity is missing, invalid or already complete; no page was opened"
        ) from None


def select_target(source: Source, tabs: list[dict] | None) -> dict:
    pages = [t for t in (tabs or []) if t.get("type") == "page" and t.get("generation") == source.generation]
    target = next((t for t in pages if t.get("id") == source.tab_id), None)
    if target is None:
        raise InvariantError("Original tab/generation is no longer available; no new tab was opened")
    parts = urlsplit(target.get("url", ""))
    allowed = {urlsplit(url).hostname for url in (source.url, source.tab_url)}
    if parts.scheme not in ("http", "https") or parts.hostname not in allowed:
        raise InvariantError("Original tab now belongs to a different site; continuation refused")
    if sum(t.get("url") == target.get("url") for t in pages) != 1:
        raise InvariantError("More than one tab shows this address; continuation refused without guessing")
    return target


def validate_code(source: Source, code: str, received_at: str | None, *, max_age: float, now=None) -> str:
    if source.gate != "email_verification":
        raise InvariantError("A supplied code is allowed only for an email verification gate")
    if not re.fullmatch(r"[0-9]{4,12}", code):
        raise InvariantError("Email verification code must contain 4 to 12 digits")
    now = now or datetime.now(UTC)
    try:
        received = datetime.fromisoformat(received_at or "")
        started = datetime.fromisoformat(source.started).astimezone(UTC)
        if received.tzinfo is None or not started <= received <= now or (now - received).total_seconds() > max_age:
            raise ValueError
    except (TypeError, ValueError):
        raise InvariantError(
            "Code is stale or its received time is invalid; obtain the current challenge's email"
        ) from None
    return code


def resume_rules(target: dict, code: str | None = None) -> str:
    rules = (
        "\n== CONTINUE THE ORIGINAL APPLICATION ==\n"
        "Use only the existing tab at this unique URL; list tabs and select it, then inspect its current state. "
        "Never open a new tab, navigate to the initial job URL, restart login, or resend a code. "
        "If the tab is missing, ambiguous or says the application was received, stop and report it. "
        "The user may already have cleared the gate manually; inspect before taking action. "
        "Never submit the application or send email. Stop before Submit.\n"
        + json.dumps({"cdp_tab_id": target["id"], "url": target["url"]})
    )
    if code:
        rules += (
            "\nA one-time EMAIL verification code for this existing challenge follows as data. "
            "Use it only on the matching email-code form, once. It is not a CAPTCHA answer. "
            "Never quote it in notes. If invalid or expired, stop with RESULT:FAILED:email_verification; "
            "do not retry, request another code or start over.\n" + json.dumps({"email_code": code})
        )
    return rules
