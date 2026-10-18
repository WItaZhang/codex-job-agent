"""Form-fill runner, slice 1 (specs/form-fill.plan.md sections 1-3). It never submits.

For each job on the list: the existing filling agent (apply_bridge, ApplyPilot's dry run) fills the form up to the
final submit button with the answer sheet and the checklist added to its instructions, without Gmail and with more
tools refused. Then code finds the job's tab, reads the last page back over CDP, and writes meta.json and the job's
section of the run's feasibility.md: one row per field read back, for the user to mark against the tab, and the
automatic checks of plan section 2 (items 1, 3, 4 and 5).

Everything written under the run folder is redacted; the agent's own log is masked line by line as it is written,
and the screenshot has the page's contact values painted black (or is not saved).
Contact entries of answers.yaml never reach the instructions: the prompt already carries the profile's contact
details, and only code reads profile.json.
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import get_args
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from .. import apply_bridge
from ..applicant import Applicant
from ..config import Settings
from ..domain import InvariantError
from . import readback
from .inputs import load_answers, load_checklist, load_list, profile_field_paths
from .models import RECORD_FIELDS, AnswerEntry, ChecklistItem, JobList, JobReport, ListEntry, PageState, ReadField
from .redact import VERSION as REDACTION_VERSION
from .redact import Redactor, total_hits

_Status = JobReport.model_fields["status"].annotation
STATUSES = get_args(_Status)
# apply_bridge reasons for an agent that stopped at a wall it may not pass (specs/form-fill.md 5.5).
GATE_REASONS = (
    "captcha",
    "login_issue",
    "sso_required",
    "email_verification",
    "arbitration",
    "ssn_required",
    "payment_required",
    "unsafe_permissions",
    "unsafe_verification",
)
APPLICANT_ANSWERS = ("relocation", "onsite_ok")  # applicant.yaml fields a from_applicant entry may name
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")  # a job id names a folder
CODE_TOOLS = ("browser_evaluate", "browser_run_code", "browser_run_code_unsafe")
PAGE_URL = re.compile(r"Page URL: (\S+)")
LOG_FILE = "log.jsonl"
META_FILE = "meta.json"
REPORT_FILE = "feasibility.md"
CELL_CHARS = 300
NOTE_CHARS = 2000  # long enough for the agent's reason, such as the eligibility statement it quotes (decision 23)

SHEET_RULES = """Rules for the sheet (where they differ from the APPLICANT PROFILE, SCREENING, SALARY or any other
answering rule above, these win):
- Answer every question the sheet covers by the sheet: choose the option or write the text that matches its answer.
- If a question states its own rule (for example "answer Yes if you are on F-1/OPT"), follow the question's rule.
- Never guess a fact that is not on the sheet (dates, numbers, work authorization, demographics, degrees,
  certificates). A skills or experience question may be answered from a sentence in the RESUME TEXT, or by a
  checklist rule below. Otherwise leave an optional question empty. If a required question blocks the next step,
  stop on that page, write a "NOTE:" line quoting the question and its options, and output RESULT:FAILED:needs_answer
- Contact details come only from the APPLICANT PROFILE above."""

RUN_RULES = """

== THIS RUN (overrides anything above) ==
- Fill the form up to the final submit button and stop there. Never click Submit, Submit application, Send or
  Finish, and never use submit: true or press Enter in a field where that could send the form.
- Do not judge location eligibility: skip the LOCATION CHECK above and fill the form whatever the location.
- This run has no email tools. If the site wants a code or a link from email, stop on that page, write a "NOTE:"
  line naming the site, and output RESULT:FAILED:email_verification
- Do not try to solve a CAPTCHA: stop on that page, write a "NOTE:" line, and output RESULT:CAPTCHA
- Single sign-on (Google, Microsoft, Apple, Okta and the like): stop and output RESULT:FAILED:sso_required
- If the page says applicants like this one are not accepted or are rejected automatically (for example "not open
  to current students") and the true answer would be rejected: stop on that page, write a "NOTE:" line quoting the
  statement, and output RESULT:FAILED:not_eligible. Never give an untrue answer to get past it.
- Never close a tab, and never use the tabs that were open before you started."""

NO_LETTER = """
- COVER LETTER: there is none for this job. Leave an optional cover-letter field empty. If one is required, stop on
  that page, write a "NOTE:" line naming the field, and output RESULT:FAILED:needs_answer"""


class JobOutcome(BaseModel):
    """One job's result. `status` is a JobReport status; `agent_result` is the filling agent's own result
    ("dry_run" when it stopped before submit as told); `checks` are the automatic feasibility checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    status: _Status
    page_dir: str | None = None
    fields: int = 0
    notes: tuple[str, ...] = ()
    agent_result: str | None = None
    checks: dict[str, bool | int] = {}


@dataclass(frozen=True)
class Inputs:
    jobs: JobList
    answers: dict[str, AnswerEntry]  # references resolved, contact entries kept as references
    checklist: tuple[ChecklistItem, ...]
    redactor: Redactor


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_dir: Path
    settings: Settings
    applicant: Applicant
    answers: dict[str, AnswerEntry]
    checklist: tuple[ChecklistItem, ...]
    redactor: Redactor
    runner: "FormFillRunner"  # or a stand-in with the same four methods


# --- inputs -----------------------------------------------------------------------------------------------------


def applicant_values(applicant: Applicant) -> dict[str, str]:
    """The applicant.yaml answers a from_applicant entry may name, when they are set."""
    values = {"relocation": applicant.relocation or "", "onsite_ok": "; ".join(applicant.onsite_ok)}
    return {name: value for name, value in values.items() if name in APPLICANT_ANSWERS and value}


def _profile_value(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool):
        return "Yes" if node else "No"
    if isinstance(node, int | float):
        return str(node)
    if isinstance(node, list) and all(isinstance(item, str) for item in node):
        return "; ".join(node)
    return node if isinstance(node, str) else None


def resolve(answers: dict[str, AnswerEntry], profile: dict, values: dict[str, str]) -> dict[str, AnswerEntry]:
    """Non-contact references turned into answers: from_profile from profile.json, from_applicant from
    applicant.yaml. Contact entries stay references. Errors name the entry and the reference, never a value."""
    out, problems = {}, []
    for key, entry in answers.items():
        if entry.category == "contact" or not (entry.from_profile or entry.from_applicant):
            out[key] = entry
            continue
        if entry.from_profile:
            ref, value = f"from_profile {entry.from_profile}", _profile_value(profile, entry.from_profile)
        else:
            ref, value = f"from_applicant {entry.from_applicant}", values.get(entry.from_applicant or "")
        if not value or not value.strip():
            problems.append(f"{key}: {ref} has no value")
            continue
        out[key] = entry.model_copy(update={"answer": value.strip(), "from_profile": None, "from_applicant": None})
    if problems:
        raise InvariantError("answers.yaml references without a value:\n" + "\n".join(f"- {p}" for p in problems))
    return out


def load_inputs(list_path: str | Path, applicant: Applicant, settings: Settings) -> Inputs:
    """Every input checked before anything starts (specs/form-fill.md 2, 4.1). One error lists the problems of
    every file; contact values are never echoed."""
    problems: list[str] = []
    jobs = answers = checklist = redactor = None
    try:
        jobs = load_list(Path(list_path).expanduser(), {resume.id for resume in applicant.resumes})
    except InvariantError as error:
        problems.append(str(error))
    if jobs is not None:
        unsafe = [entry.id for entry in jobs.jobs if not SAFE_ID.match(entry.id)]
        if unsafe:
            problems.append(
                f"{list_path}: job ids name folders, so they must start with a letter or digit and hold only "
                f"letters, digits, '.', '_' and '-': {unsafe}"
            )
    try:
        checklist = load_checklist(applicant.folder / settings.form_fill.checklist_file)
    except InvariantError as error:
        problems.append(str(error))
    try:
        fields = profile_field_paths(applicant.profile)  # also checks that it is a JSON object
        profile = json.loads(applicant.profile.read_text(encoding="utf-8"))
        redactor = Redactor.from_profile(profile)
    except InvariantError as error:
        problems.append(str(error) if str(applicant.profile) in str(error) else f"{applicant.profile}: {error}")
    else:
        values = applicant_values(applicant)
        try:
            answers = resolve(
                load_answers(applicant.folder / settings.form_fill.answers_file, fields, set(values)), profile, values
            )
        except InvariantError as error:
            problems.append(str(error))
    if problems:
        message = "The form-fill run was not started:\n" + "\n".join(problems)
        raise InvariantError(redactor.mask(message) if redactor else message)  # an error may quote a contact value
    return Inputs(jobs, answers, checklist, redactor)


# --- instructions -----------------------------------------------------------------------------------------------


def _quoted(items) -> str:
    return "; ".join(f'"{item}"' for item in items)


def _sheet_line(key: str, entry: AnswerEntry) -> str:
    head = f"- {key}: " + " / ".join(f'"{phrase}"' for phrase in entry.asks) if entry.asks else f"- {key}"
    if entry.kind == "record":
        parts = []
        for n, name in enumerate(field for field in RECORD_FIELDS if field in entry.record):
            also = (*(entry.aliases if n == 0 else ()), *entry.record_accept.get(name, ()))
            part = f"{name} {entry.record[name]}" + (f" (also: {', '.join(also)})" if also else "")
            if name in entry.record_otherwise:
                picks = " or ".join(f'"{pick}"' for pick in entry.record_otherwise[name])
                part += f" (if the page has no such option: {picks})"
            parts.append(part)
        return f"{head} (one {entry.category} entry) -> " + "; ".join(parts)
    extras = []
    if entry.kind == "number_min":
        extras.append("the minimum: never give less, and a range must not start below it")
    if entry.accept:
        extras.append(f"also correct: {_quoted(entry.accept)}")
    if entry.otherwise:
        extras.append(f'if the page has no such option: "{entry.otherwise}"')
    return f"{head} -> {entry.answer}" + (f" ({'; '.join(extras)})" if extras else "")


def instructions(answers: dict[str, AnswerEntry], checklist: tuple[ChecklistItem, ...] | list) -> str:
    """The answer sheet and the checklist, appended to the filling prompt. Contact entries are left out (the prompt
    already carries the profile's contact details). from_profile / from_applicant must be resolved first."""
    unresolved = [
        key
        for key, entry in answers.items()
        if entry.category != "contact" and (entry.from_profile or entry.from_applicant)
    ]
    if unresolved:
        raise InvariantError(f"Resolve these references before writing the instructions: {', '.join(unresolved)}")
    lines = [
        "",
        "",
        "== ANSWER SHEET ==",
        "Each line: the entry, the question phrases it answers, and its one correct answer.",
        *(_sheet_line(key, entry) for key, entry in answers.items() if entry.category != "contact"),
        "",
        SHEET_RULES,
        "",
        "== CHECKLIST ==",
        "Follow each rule when you answer screening questions, open questions and optional fields:",
        *([f"- {item.id}: {item.rule}" for item in checklist] or ["- (none)"]),
    ]
    return "\n".join(lines)


def run_rules(entry: ListEntry) -> str:
    return RUN_RULES + (NO_LETTER if entry.cover_letter == "none" else "")


# --- reading a run ----------------------------------------------------------------------------------------------


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def read_log(path: str | Path) -> tuple[list[dict], list[str]]:
    """Tool calls ({name, input}) and the page addresses seen, in order, from a run's stream-json log (already
    masked): addresses the agent navigated to and the "Page URL" lines of the browser's answers."""
    path = Path(path)
    if not path.is_file():
        return [], []
    events, urls = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        body = message.get("message") if isinstance(message, dict) else None
        content = body.get("content") if isinstance(body, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                data = block.get("input") if isinstance(block.get("input"), dict) else {}
                name = str(block.get("name", ""))
                events.append({"name": name, "input": data})
                if data.get("url") and name.endswith(("browser_navigate", "browser_tabs")):
                    urls.append(str(data["url"]))
            elif block.get("type") == "tool_result":
                urls.extend(PAGE_URL.findall(_result_text(block.get("content"))))
    return events, urls


def submit_signs(events: list[dict]) -> list[str]:
    """Anything in the agent's tool calls that may have sent the form (plan section 2, item 4). On their own they are
    warnings: Enter in a search box and the agent's own description of a button match too."""
    signs = []
    for event in events:
        tool, data = event["name"].rsplit("__", 1)[-1], event["input"]
        if data.get("submit") is True:
            signs.append(f"{tool} 带 submit: true（{data.get('element', '')}）")
        if tool == "browser_click" and readback.SUBMIT_WORDS.search(str(data.get("element", ""))):
            signs.append(f"browser_click 点过「{data.get('element')}」")
        if tool in CODE_TOOLS and "submit" in json.dumps(data).lower():
            signs.append(f"{tool} 的代码里有 submit")
    return signs


def _pages(targets: list[dict]) -> list[dict]:
    return [
        t for t in targets if t.get("type") == "page" and not str(t.get("url", "")).startswith(readback.IGNORED_SCHEMES)
    ]


def personal_tabs(targets: list[dict], blocked_origins) -> list[str]:
    """Origins of the open tabs that hold the user's own accounts: mail, banks, payments, password managers
    (apply.blocked_origins) and school systems (.edu). Only the origin, never the path (decision 23)."""
    blocked = {origin.rstrip("/").lower() for origin in blocked_origins}
    found: list[str] = []
    for tab in _pages(targets):
        parts = urlsplit(str(tab.get("url", "")))
        host = (parts.hostname or "").lower()
        origin = f"{parts.scheme}://{host}"
        if host and (origin in blocked or host.endswith(".edu")) and origin not in found:
            found.append(origin)
    return found


def other_tabs(before: list[dict], after: list[dict]) -> list[str]:
    """Addresses of the tabs open before the run that are gone or now show another address (plan item 5)."""
    now = {t.get("id"): t.get("url") for t in after}
    return [str(t.get("url", "")) for t in _pages(before) if now.get(t.get("id")) != t.get("url")]


def agent_result(outcome: apply_bridge.ApplyOutcome) -> str:
    if outcome.status == "waiting":
        return "dry_run"
    return outcome.reason or outcome.status


# --- one job ----------------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:300]


def job_spec(entry: ListEntry, applicant: Applicant, job_dir: Path) -> apply_bridge.ApplySpec:
    """The list entry as a stop-before-submit application. Without a cover letter the letter path points at a file
    that is never written, so the prompt says there is none."""
    resume = applicant.resume(entry.resume)
    letter = job_dir / "no-cover-letter.txt" if entry.cover_letter == "none" else Path(entry.cover_letter)
    return apply_bridge.ApplySpec(
        job_id=entry.id,
        mode="stop",
        url=entry.url,
        title=entry.title,
        company=entry.company,
        resume_pdf=resume.pdf,
        resume_text=resume.text,
        letter_txt=letter,
        letter_pdf=letter.with_suffix(".pdf"),
        onsite_ok=tuple(applicant.onsite_ok),
        relocation=applicant.relocation,
    )


@dataclass
class _Seen:
    """What one job's checks were computed from, for its section of feasibility.md."""

    fields: tuple[ReadField, ...] = ()
    target: dict | None = None
    last_url: str | None = None
    signs: tuple[str, ...] = ()
    state: PageState | None = None  # where the form on the final page stands, when it was read
    moved: tuple[str, ...] = ()
    tabs_before: int = 0
    scan: dict | None = None
    filled: bool = False  # the agent ran


def run_job(entry: ListEntry, ctx: RunContext) -> JobOutcome:
    """Fill one job up to the final submit button (never submits), find its tab, read the last page back, and
    write meta.json and the job's section of feasibility.md. A failure is this job's own; the run goes on."""
    job_dir = ctx.run_dir / entry.id
    job_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    notes: list[str] = []
    checks: dict[str, bool | int] = {}
    seen = _Seen()
    status, page_dir, agent = "system_error", None, None
    try:
        ctx.runner.ensure_chrome()
        before = ctx.runner.targets()
        outcome = ctx.runner.fill(
            job_spec(entry, ctx.applicant, job_dir),
            log_path=job_dir / LOG_FILE,
            mask=ctx.redactor.mask,
            extra_prompt=instructions(ctx.answers, ctx.checklist) + run_rules(entry),
            gmail=False,
            extra_disallowed=tuple(ctx.settings.form_fill.disallowed_tools),
        )
    except Exception as error:  # noqa: BLE001 - nothing reached the site, or the run broke; the next job goes on
        notes.append(_error(error))
    else:
        agent = ctx.redactor.mask(agent_result(outcome))
        notes.extend(outcome.notes)
        if outcome.status != "pending":  # pending: the agent never started (apply_bridge.outcome_status)
            seen.filled, seen.tabs_before = True, len(_pages(before))
            status, page_dir = _after_fill(entry, ctx, job_dir, before, outcome, seen, notes, checks)
    seen.scan = ctx.redactor.scan([job_dir])
    checks["redaction_hits"] = total_hits(seen.scan)
    result = JobOutcome(
        job_id=entry.id,
        status=status,
        page_dir=page_dir,
        fields=len(seen.fields),
        notes=tuple(ctx.redactor.mask(note)[:NOTE_CHARS] for note in notes),
        agent_result=agent,
        checks=checks,
    )
    meta = {
        "job_id": entry.id,
        "company": entry.company,
        "title": entry.title,
        "url": entry.url,
        "status": result.status,
        "agent_result": result.agent_result,
        "fields": result.fields,
        "tab": seen.target.get("url") if seen.target else None,
        "checks": result.checks,
        "page": seen.state.model_dump(mode="json") if seen.state else None,
        "submit_warnings": list(seen.signs),
        "redaction": REDACTION_VERSION,  # how this job's saved files were masked (redact.VERSION)
        "notes": list(result.notes),
        "started": started,
        "finished": _now(),
    }
    (job_dir / META_FILE).write_text(_dump(ctx.redactor.mask_json(meta)), encoding="utf-8")
    with (ctx.run_dir / REPORT_FILE).open("a", encoding="utf-8") as report:
        report.write(ctx.redactor.mask(_section(entry, result, seen, ctx.redactor)))
    return result


def _after_fill(entry, ctx, job_dir, before, outcome, seen, notes, checks) -> tuple[str, str | None]:
    events, urls = read_log(outcome.log or job_dir / LOG_FILE)
    seen.last_url = urls[-1] if urls else None
    gate = outcome.reason in GATE_REASONS
    if outcome.status == "failed" and not gate:
        notes.append(f"the filling agent stopped early ({outcome.reason}); the form may be incomplete")
    if outcome.other_sites:
        notes.append("other sites opened: " + ", ".join(outcome.other_sites))
    try:
        after = ctx.runner.targets()
    except Exception as error:  # noqa: BLE001 - the browser went away: the tab is lost
        after = None
        notes.append(f"the browser's tabs could not be listed after the run: {_error(error)}")
    seen.target = readback.find_job_tab(before, after, seen.last_url) if after is not None else None
    checks["tab_found"] = seen.target is not None
    status, page_dir = "tab_lost", None
    if seen.target is None:
        notes.append("the job's tab was not found")
    else:
        try:
            final = ctx.runner.read(seen.target, job_dir, ctx.redactor)
            seen.fields, seen.state = tuple(final.fields), final.state
            status, page_dir = "filled", str(job_dir)
            left_out = job_dir / readback.SCREENSHOT_NOTE
            if left_out.is_file():
                notes.append(left_out.read_text(encoding="utf-8").strip())
        except Exception as error:  # noqa: BLE001 - the page could not be read; the tab stays as it is
            status = "readback_failed"
            notes.append(f"read back failed: {_error(error)}")
    seen.signs = tuple(submit_signs(events))
    checks["not_submitted"] = not_submitted(outcome, seen.state, seen.signs)
    seen.moved = tuple(other_tabs(before, after)) if after is not None else ("（标签页列不出来）",)
    checks["other_tabs_unchanged"] = not seen.moved
    return ("gate" if gate else status), page_dir


def not_submitted(outcome, state: PageState | None, signs) -> bool:
    """Check 4 (slice-1 plan, section 2). The agent did not report a submission or send mail, and the final page has
    no "application received" text. A page still showing its submit or next button is before submit, whatever the
    agent's tool calls say; without that evidence, any submit-like tool call fails the check."""
    if outcome.status == "applied" or outcome.emails or (state is not None and state.received):
        return False
    return (state is not None and state.before_submit) or not signs


# --- feasibility.md ---------------------------------------------------------------------------------------------


def _cell(text: str, redactor: Redactor, full: str = "readback.json") -> str:
    """One table cell; a long text is cut, and `full` names the file holding all of it."""
    text = re.sub(r"\s+", " ", redactor.mask(str(text))).strip()
    if len(text) > CELL_CHARS:
        text = text[:CELL_CHARS] + f"…（全文见 {full}）"
    return text.replace("\\", "\\\\").replace("|", "\\|") or "（空）"


def _agent_text(result: str | None) -> str:
    return "dry run（停在提交前）" if result == "dry_run" else (result or "没有运行")


def _passed(ok: bool) -> str:
    return "通过" if ok else "不通过（请你检查）"


def _section(entry: ListEntry, result: JobOutcome, seen: _Seen, redactor: Redactor) -> str:
    fields = seen.fields
    review = sum(1 for field in fields if field.origin == "review_text")
    lines = [
        f"## {entry.id} · {entry.company} · {entry.title}",
        "",
        f"- 状态：{result.status}",
        f"- 填表 agent 的结果：{_agent_text(result.agent_result)}",
        f"- 页面目录：{result.page_dir or '无'}",
        f"- 读回：{len(fields)} 栏（控件 {len(fields) - review}，确认页文字 {review}）",
        *(["- 备注："] + [f"  - {_cell(note, redactor, META_FILE)}" for note in result.notes] if result.notes else []),
        "",
        "| 编号 | 题目 | 读回值 | 来源 | 一致 / 不一致 / 漏 |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {f.id} | {_cell(f.question, redactor)} | {_cell(f.value, redactor)} | "
            f"{'确认页文字' if f.origin == 'review_text' else f'控件（{f.control}）'} |  |"
            for f in fields
        ),
        *([] if fields else ["| （没有读回） |  |  |  |  |"]),
        "",
        "漏掉的栏（标签页上有、表里没有的）：",
        "",
        "自动检查（第 2 项读回覆盖由你在上表判断）：",
        "",
        "| 项 | 结果 | 说明 |",
        "| --- | --- | --- |",
        *_check_rows(result, seen, redactor),
        "",
        "",
    ]
    return "\n".join(lines)


def _check_rows(result: JobOutcome, seen: _Seen, redactor: Redactor) -> list[str]:
    checks = result.checks
    hits = checks.get("redaction_hits", 0)
    scan = f"本岗位目录 {hits} 次命中" + (f"：{_hit_files(seen.scan)}" if hits else "（截图不在扫描范围内）")
    if not seen.filled:
        not_run = "没有检查：填表 agent 没有起来或运行出错（见备注）"
        return [
            f"| 1 找得到标签页 | {not_run} |  |",
            f"| 3 打码 | {_passed(hits == 0)} | {scan} |",
            f"| 4 没有提交 | {not_run} |  |",
            f"| 5 别的标签页没动 | {not_run} |  |",
        ]
    tab = seen.target.get("url", "") if seen.target else f"没有找到；agent 最后的地址：{seen.last_url or '无'}"
    submitted = _submit_text(result, seen)
    moved = (
        f"原有 {seen.tabs_before} 个标签页的地址和个数都没变"
        if checks["other_tabs_unchanged"]
        else "变了的原有标签页：" + "、".join(seen.moved)
    )
    return [
        f"| 1 找得到标签页 | {_passed(checks['tab_found'])} | {_cell(tab, redactor, META_FILE)} |",
        f"| 3 打码 | {_passed(hits == 0)} | {scan} |",
        f"| 4 没有提交 | {_passed(checks['not_submitted'])} | {_cell(submitted, redactor, META_FILE)} |",
        f"| 5 别的标签页没动 | {_passed(checks['other_tabs_unchanged'])} | {_cell(moved, redactor, META_FILE)} |",
    ]


def _submit_text(result: JobOutcome, seen: _Seen) -> str:
    """The explanation in check 4's row: what the agent said, what the final page shows, and the tool calls that
    looked like a submit (warnings while the page is still before submit; all of them are in meta.json)."""
    state, signs = seen.state, list(seen.signs)
    parts = [f"agent 结果是 {_agent_text(result.agent_result)}"]
    if state is None:
        parts.append("最后一页没有读到")
    elif state.received:
        parts.append(f"最后一页写着「{state.received}」")
    elif state.before_submit:
        parts.append("最后一页还停在提交前（看得到" + "、".join(f"「{a}」" for a in state.actions) + "）")
    else:
        parts.append("最后一页看不到提交或下一步按钮")
    if not signs:
        parts.append("审计里没有 submit: true，也没有点过提交类按钮")
    elif result.checks.get("not_submitted"):
        parts.append(f"警告（最后一页还在提交前，不算提交）{len(signs)} 条，例如 {signs[0]}，全文见 {META_FILE}")
    else:
        parts.extend(signs)
    return "；".join(parts)


def _hit_files(scan: dict | None, root: Path | None = None) -> str:
    """Files with hits and how many (never what matched)."""
    found = []
    for name, kinds in (scan or {}).items():
        count = sum(kinds.values())
        if name != "_skipped" and count:
            found.append(f"{Path(name).relative_to(root) if root else Path(name).name} {count}")
    return "、".join(found)


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


PERSONAL_TABS_WARNING = (
    "投递用的 Chrome 里开着这些网站的标签页：{tabs}。填表 agent 和你共用这个 Chrome，它被要求不碰原来的标签页，"
    "但这拦不住它读。下次开始前请先关掉；这次照常运行。"
)


def begin_feasibility(ctx: RunContext, list_path: str | Path, jobs: JobList, personal=()) -> Path:
    """Create the run folder and the head of feasibility.md. `personal`: the origins of personal_tabs at the start."""
    ctx.run_dir.mkdir(parents=True, exist_ok=False)
    path = ctx.run_dir / REPORT_FILE
    head = [
        f"# 可行性验证 {ctx.run_id}",
        "",
        f"- 清单：{Path(list_path).expanduser().resolve()}",
        f"- 开始：{_now()}",
        f"- 岗位：{len(jobs.jobs)} 个：{'、'.join(entry.id for entry in jobs.jobs)}",
        *([f"- {PERSONAL_TABS_WARNING.format(tabs='、'.join(personal))}"] if personal else []),
        "",
        "每个岗位一节。逐栏表的最后一列请你对照标签页填：一致 / 不一致 / 漏。标签页上有、表里没有的栏，写在表下面的"
        "“漏掉的栏”里。自动检查是计划 B 第 2 节的第 1、3、4、5 项；第 2 项由你判断。本模块从不点提交。",
        "",
        "",
    ]
    path.write_text(ctx.redactor.mask("\n".join(head)), encoding="utf-8")
    return path


def finish_feasibility(ctx: RunContext) -> None:
    """The whole-run line: the redaction scan of every file written under the run folder (plan item 3)."""
    scan = ctx.redactor.scan([ctx.run_dir])
    hits = total_hits(scan)
    files = _hit_files(scan, ctx.run_dir)
    lines = [
        "## 整次运行",
        "",
        f"- 结束：{_now()}",
        f"- 打码扫描（本次运行目录下的全部文本文件）：{_passed(hits == 0)}，{hits} 次命中"
        + (f"：{files}" if files else ""),
        f"- 没有扫描的文件（截图等。截图里页面文字中的联系方式已涂黑，图片里的字涂不到，请你看一眼）："
        f"{len(scan['_skipped'])} 个",
        "",
    ]
    with (ctx.run_dir / REPORT_FILE).open("a", encoding="utf-8") as report:
        report.write(ctx.redactor.mask("\n".join(lines)))


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# --- the real browser side --------------------------------------------------------------------------------------


class FormFillRunner:
    """The applying Chrome (shared with /apply), one Claude Code run per job, and the CDP read of the job's tab.
    Tests pass a stand-in with the same four methods instead."""

    def __init__(self, data_dir: Path, settings: Settings, profile: Path, read_dirs=()):
        self.data_dir, self.settings, self.profile = Path(data_dir).resolve(), settings, Path(profile).resolve()
        self.read_dirs = tuple(read_dirs)
        self.port: int | None = None

    def ensure_chrome(self) -> int:
        """Reuse the applying Chrome if it runs (its tabs may hold forms the user is checking); start it if not."""
        apply_bridge.configure(self.data_dir, self.settings, self.profile)
        self.port = apply_bridge.ensure_chrome(self.settings)
        return self.port

    def targets(self) -> list[dict]:
        return readback.list_targets(self._port())

    def fill(self, spec: apply_bridge.ApplySpec, **options) -> apply_bridge.ApplyOutcome:
        return apply_bridge.run_application(
            spec, self.data_dir, self.settings, self.profile, self._port(), self.read_dirs, **options
        )

    def read(self, target: dict, out_dir: Path, redactor: Redactor) -> readback.FinalPage:
        return readback.read_final_page(self._port(), target, out_dir, redactor)

    def _port(self) -> int:
        if self.port is None:
            raise InvariantError("The applying Chrome is not started; call ensure_chrome first")
        return self.port
