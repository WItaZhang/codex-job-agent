"""/apply and the form-fill module (spec section 10 items 86-96), the pure part: a batch job as a form-fill list
entry, a form-fill report as an apply-list status, profile.json against answers.yaml on the facts both hold, and a
week of attempts against the done standard (spec section 9). Deterministic; no files, no database, no model."""

import datetime
import re
from dataclasses import dataclass

from .apply_bridge import GATES
from .config import ApplyDone
from .domain import InvariantError
from .form_fill.matching import norm
from .form_fill.models import AnswerEntry, JobReport

FORM_FILL_MODES = ("review", "careful")  # the modes the form-fill module fills (spec section 10 item 86)
WORKED = ("waiting", "applied")  # what counts as "something in this step worked" (spec section 10 item 68)
UNKNOWN_COMPANY = "Unknown company (see the posting)"  # the form-fill list needs a name; the posting has none

# The done standard's failure categories (spec section 9) and the result codes that fall into each.
CATEGORIES = {
    "login": ("login_issue", "login_failed", "sso_required", "email_verification"),  # e-mail: decision 22
    "captcha": ("captcha",),
    "arbitration": ("arbitration",),
    "ssn": ("ssn_required",),
    "payment": ("payment_required",),
    "no_ai": ("screening_requires_candidate_written_answers",),
    "expired": ("expired",),
    "timeout": ("timeout",),
}
_CATEGORY_OF = {code: name for name, codes in CATEGORIES.items() for code in codes}
# A held job's gate as apply_status names it (spec section 10 item 97).
GATE_TEXT = {
    "login_issue": "登录和注册都不成",
    "sso_required": "单点登录",
    "captcha": "验证码",
    "email_verification": "邮箱验证码",
    "interrupted": "工具服务中途停了，可能已经交过",  # not a gate: held for the user to look (item 113)
}
STEP_NAMES = {"review": "第一步", "rest": "第二步", "resume": "接着做"}  # in the notification (item 99)
LABELS = {
    "login": "登录",
    "captcha": "验证码",
    "arbitration": "仲裁协议",
    "ssn": "SSN",
    "payment": "支付",
    "no_ai": "筛选题不用 AI 写",
    "expired": "岗位已下架",
    "timeout": "网络超时",
}

# profile.json against answers.yaml: the facts both hold one to one (form-fill spec 2.3). Sponsorship is left out:
# profile.json has one item for now and the future, answers.yaml tells them apart.
PROFILE_PAIRS = (
    ("work_authorization.legally_authorized_to_work", "work_auth.authorized_us", "text"),
    ("compensation.salary_expectation", "compensation.expected", "number"),
    ("eeo_voluntary.gender", "eeo.gender", "text"),
    ("eeo_voluntary.race_ethnicity", "eeo.race", "text"),
    ("eeo_voluntary.veteran_status", "eeo.veteran", "text"),
    ("eeo_voluntary.disability_status", "eeo.disability", "text"),
)
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.I)


@dataclass(frozen=True)
class Result:
    status: str  # pending | waiting | held | applied | failed (the apply list's run statuses)
    reason: str


def worked(status: str) -> bool:
    return status in WORKED


SHORT_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def short_code(reason: str | None) -> str | None:
    """A result code as the attempt log keeps it: a short code as written, any other text "other" (review #5: an
    agent may write free text, even an e-mail address, after RESULT:FAILED:; that text goes masked into the note)."""
    if not reason:
        return None
    return reason if SHORT_CODE.fullmatch(reason) else "other"


def category(reason: str | None) -> str | None:
    """The done standard's category of a result code, or None for a failure that has to be fixed."""
    return _CATEGORY_OF.get(reason or "")


# --- a batch job as a list entry, a report as a status ------------------------------------------------------------


def list_entry(job_id: str, url: str, company: str, title: str, resume: str, letter_txt) -> dict:
    """One job of an apply step as an entry of the form-fill list (spec section 10 item 87): the job id as its id,
    the letter's .txt as an absolute path, and revisit: true, since a job in a batch is always visited."""
    return {
        "id": job_id,
        "url": url,
        "company": company.strip() or UNKNOWN_COMPANY,
        "title": title,
        "resume": resume,
        "cover_letter": str(letter_txt),
        "revisit": True,
    }


def application_result(report: JobReport) -> Result:
    """A form-fill report as the apply-list status and the reason recorded first in its note (spec section 10
    item 89). The reason is the filling agent's own result code, written as ApplyPilot writes it; code's own
    findings use the form-fill module's names. A gate holds the job for the user (item 97), even when its tab was
    lost: the user can still pass it and the job is resumed from its address."""
    agent, conclusion = report.agent_result, report.conclusion
    if conclusion == "maybe_submitted":  # before anything else: a page said "received", so never pending again
        return Result("applied", "maybe_submitted")
    if agent == "stopped":  # the user stopped the step (spec section 10 item 109): the job goes back where it was
        return Result("pending", "stopped")
    if report.status == "system_error" or agent == "system_error":  # nothing reached the site (spec item 67)
        return Result("pending", "system_error")
    if conclusion in ("check_then_submit", "do_not_submit"):
        return Result("waiting", conclusion)
    if conclusion == "unreadable":
        if agent not in (None, "dry_run"):  # the agent itself said it failed: never waiting
            return Result("failed", agent)
        if report.status == "tab_lost":
            return Result("failed", "tab_lost")
        return Result("waiting", "unreadable")  # the agent said it filled the form; the tab is there to check
    if conclusion == "not_filled" and agent == "already_applied":
        return Result("applied", "already_applied")
    if agent in GATES:
        return Result("held", agent)
    if agent and agent != "dry_run":
        return Result("failed", agent)
    return Result("failed", report.not_filled_reason or conclusion)


def close_step(data: dict, phase: str, *, broke: bool, worked: bool, held: list[str], maybe: str | None) -> dict:
    """A batch's step that has ended - done, stopped, broken or cut short by a tool server that stopped (spec section
    10 items 68, 90, 101, 109, 112) - written into the batch's data. `broke`: the run itself failed; `worked`: a job
    of the step was filled or submitted; `held`: its jobs held at a gate; `maybe`: a job that may have been submitted.
    A first step with nothing that worked waits for its held jobs, or else voids the batch; a possible submission
    voids it either way."""
    data["phases"][phase] = "done"
    if phase == "review" and not broke and not (maybe or worked) and held:
        data["review_holds"] = held  # not void yet: decided once the held jobs are dealt with (item 101)
    elif maybe or (phase == "review" and (broke or not worked)):
        if data["phases"]["rest"] == "proposed":
            data["phases"]["rest"] = "void"
        data["void"] = True
        if maybe:
            data["void_reason"] = {"why": "maybe_submitted", "job_id": maybe}
    return data


def note(reason: str, conclusion: str, run_id: str, notes) -> str:
    """The apply list's note for a form-fill job: the result code first, so the week's numbers can sort it."""
    return "；".join([reason, conclusion, f"填表运行 {run_id}", *(n for n in notes if n)])


# --- profile.json against answers.yaml ----------------------------------------------------------------------------


def _profile_value(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _number(text: str) -> float | None:
    match = _NUMBER.search(str(text))
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * 1000 if match.group(2) else value


def _agree(kind: str, profile_value: str, entry: AnswerEntry) -> bool:
    if kind == "number":
        mine, theirs = _number(profile_value), _number(entry.answer or "")
        return mine is None or theirs is None or mine == theirs
    return norm(profile_value) in {norm(text) for text in (entry.answer or "", *entry.accept)}


def profile_conflicts(profile: dict, answers: dict[str, AnswerEntry]) -> list[str]:
    """The one-to-one facts on which profile.json and answers.yaml disagree, as "<profile field> / <answers key>"
    (spec section 10 item 92). Never a value: the caller shows these to the user and the model. A fact written on
    one side only is not compared."""
    conflicts = []
    for field, key, kind in PROFILE_PAIRS:
        value, entry = _profile_value(profile, field), answers.get(key)
        if entry is None or entry.answer is None or value is None or not str(value).strip():
            continue
        if not _agree(kind, str(value), entry):
            conflicts.append(f"{field} / {key}")
    return conflicts


# --- a week of attempts -------------------------------------------------------------------------------------------


def week_start(text: str) -> datetime.date:
    """A week is named by its Monday (YYYY-MM-DD)."""
    try:
        day = datetime.date.fromisoformat(str(text))
    except ValueError:
        raise InvariantError(f"week must be a Monday as YYYY-MM-DD, not {text!r}") from None
    if day.weekday() != 0:
        raise InvariantError(f"week must be a Monday; {text} is a {day.strftime('%A')}")
    return day


def _count(counts: dict, key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def week_stats(attempts, week: datetime.date, tz: datetime.tzinfo | None, done: ApplyDone) -> dict:
    """The week's numbers against the done standard (spec section 9, section 10 item 96). An attempt belongs to the
    week its time falls in, in `tz` (None: local time, with the rules of that attempt's own day). Submitted:
    ApplyPilot's submissions (not already_applied), the user's own after checking a form or passing a gate, and a
    form the user submitted in a held job's tab (submitted_by_user). Not submitted: the failed attempts, allowed or
    to fix, including a held job the user gave up (the user's own failed attempt, decision 22). Held jobs and
    everything else are listed apart."""
    end = week + datetime.timedelta(days=6)
    submitted, allowed, to_fix = 0, {}, {}
    apart = dict.fromkeys(
        ("waiting", "held", "maybe_submitted", "already_applied", "pending", "user_skipped", "user_pending"), 0
    )
    for attempt in attempts:
        day = datetime.datetime.fromisoformat(attempt["at"]).astimezone(tz).date()
        if not week <= day <= end:
            continue
        status, reason, via = attempt["status"], attempt.get("reason"), attempt["via"]
        if via == "user" and status != "failed":
            if status == "applied":
                submitted += 1
            elif status in ("skipped", "pending"):
                apart[f"user_{status}"] += 1
        elif status == "applied" and reason in ("already_applied", "maybe_submitted"):
            apart[reason] += 1
        elif status == "applied":
            submitted += 1
        elif status == "failed":
            name = category(reason)
            if name in done.allowed:
                _count(allowed, name)
            else:
                _count(to_fix, reason or "unknown")
        elif status in ("waiting", "held", "pending"):
            apart[status] += 1
    failed = sum(allowed.values()) + sum(to_fix.values())
    rate = submitted / (submitted + failed) if submitted + failed else None
    met = submitted >= done.weekly_target and rate is not None and rate >= done.success_rate
    numbers = {
        "week": week.isoformat(),
        "end": end.isoformat(),
        "submitted": submitted,
        "failed": failed,
        "success_rate": rate,
        "allowed": allowed,
        "to_fix": to_fix,
        "not_counted": apart,
        "met": met,
        "target": done.weekly_target,
        "rate_target": done.success_rate,
    }
    numbers["line"] = _line(numbers)
    return numbers


APART = {  # what is not counted, as the line names it
    "waiting": ("停在提交前", " 次"),
    "held": ("等你处理", " 次"),
    "maybe_submitted": ("可能已提交", ""),
    "already_applied": ("已申请过", ""),
    "pending": ("退回待投", ""),
    "user_skipped": ("你跳过", ""),
    "user_pending": ("你改回待投", ""),
}


def _line(n: dict) -> str:
    span = f"{n['week']} – {n['end'][5:]}"
    goal = f"要 {n['target']} 份、{round(n['rate_target'] * 100)}%"
    if n["success_rate"] is None:
        head = f"{span}：还没有提交或失败的尝试。没达标：{goal}。"
    else:
        rate = f"{round(n['success_rate'] * 100)}%（{n['submitted']}/{n['submitted'] + n['failed']}）"
        verdict = "达标" if n["met"] else f"没达标：{goal}"
        head = f"{span}：提交 {n['submitted']} 份，失败 {n['failed']} 份，成功率 {rate}。{verdict}。"
    failures = []
    if n["allowed"]:
        listed = "、".join(f"{LABELS[k]} {v}" for k, v in n["allowed"].items())
        failures.append(f"失败里允许的 {sum(n['allowed'].values())} 份：{listed}")
    if n["to_fix"]:
        listed = "、".join(f"{k} {v}" for k, v in n["to_fix"].items())
        failures.append(f"要修的 {sum(n['to_fix'].values())} 份：{listed}")
    apart = [f"{APART[k][0]} {v}{APART[k][1]}" for k, v in n["not_counted"].items() if v]
    return (
        head + ("；".join(failures) + "。" if failures else "") + (f"不算进去：{'、'.join(apart)}。" if apart else "")
    )
