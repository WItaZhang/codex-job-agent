"""Reports (specs/form-fill.md 3.2, 5.2; slice-1 plan section 1): each job's conclusion, the numbered list of what the
user has to do, the per-job JSON and the run's summary.md.

Conclusions, first match wins: maybe_submitted (check 4 failed, or the last page says the application was received);
not_eligible (the page says applicants like this one are rejected, and the filling agent stopped: decision 23);
not_filled (a gate, or the filling agent stopped before the final page: needs_answer, stuck, page_error ...);
unreadable (the tab was lost, the read failed, or nothing was read); do_not_submit (a wrong fact or an unfounded
guess); check_then_submit (everything else: slice 1 has no reviewer, so code never says a form is fine as it is).
"""

import json
from collections import defaultdict
from pathlib import Path

from .compare import EMPTY_BLOCK, EXTRA, NAV_NOTE, OLD_PAGE, REQUIRED_EMPTY, check_page, is_empty
from .models import CheckResult, JobReport, PageInfo
from .runner import GATE_REASONS

CONCLUSIONS = {
    "maybe_submitted": "可能已提交",
    "not_eligible": "不符合申请资格",
    "do_not_submit": "不能提交",
    "check_then_submit": "检查后可提交",
    "not_filled": "没填完",
    "unreadable": "读不出",
}
REASONS = {
    "gate": "门槛",
    "needs_answer": "缺标准答案",
    "stuck": "卡住",
    "stopped": "你停下了",  # /apply's stop (spec section 10 item 109)
    "system_error": "程序没跑起来",
    "page_error": "页面出错",
    "expired": "已下架",
    "already_applied": "已经申请过",
    "login_failed": "登录或注册失败",
    "wrong_job": "跳到了别的岗位",
    "email_only": "只收邮件",
    "screening_requires_candidate_written_answers": "筛选题要本人作答",  # /apply's shared rule (spec 5.5)
}
AGENT_REASONS = {
    "timeout": "stuck",
    "stalled": "stuck",  # no output for too long: ended by code (spec section 10 item 107)
    "no_result_line": "stuck",
    "system_error": "system_error",
    "login_issue": "login_failed",  # could not sign in nor register: not a gate (spec 5.5)
}
ADVICE = {  # next action or real capability blocker; no extra user authorization gate (decision 25)
    "gate": "请你自己在标签页里过了这一关（不填 SSN、付款，不接受仲裁），实际门槛解决后由协调者安排重跑 {job}。",
    "needs_answer": "依据权威资料补齐标准答案；缺事实则报告，资料齐备后安排重跑 {job}。",
    "stuck": "协调者检查卡住原因并安排重跑 {job}；不得编造答案。",
    "stopped": (  # /apply's stop (spec section 10 item 109): the job is back where it was
        "你在这一步运行时停下了它：这个岗位没有做完、也没有提交，标签页里可能有填了一半的表。它已回到原来的状态。"
    ),
    "system_error": (
        "填表 agent 没起来，网站没有打开过。请检查投递用的 Chrome 和 Claude Code，"
        "实际门槛解决后由协调者安排重跑 {job}。"
    ),
    "page_error": "请在标签页里看看页面怎么了，页面恢复正常后由协调者安排重跑 {job}。",
    "login_failed": "登录和注册都没成功：请你自己在标签页里登录，实际门槛解决后由协调者安排重跑 {job}。",
    "expired": "岗位已经下架，不用再填。",
    "already_applied": "你已经申请过这个岗位，不用再填。",
    "wrong_job": "页面跳到了别的岗位，请确认清单里的地址。",
    "email_only": "这个岗位只收邮件投递。备注里的邮箱地址已经打码，请到岗位页面上看。",
    "screening_requires_candidate_written_answers": (  # a rerun would stop on the same page (decision 33)
        "这一页写明筛选题要你本人作答、不许用 AI 写，原文见备注。写不写由你决定：要投就在标签页里自己写完再提交。"
    ),
}
UNREADABLE = ("tab_lost", "readback_failed", "system_error")
# RUN_RULES' result, and the one ApplyPilot's own prompt offers for a work-authorization bar (decision 23)
NOT_ELIGIBLE = ("not_eligible", "not_eligible_work_auth")
SEVERITIES = {"fact": "错的事实", "guess": "无依据的猜测", "missing": "缺漏", "format": "格式与写法"}
UNRECORDED = '未录到：这一页既没有 checkpoint("filled") 时的读回，也没有补录'
READ_FROM = {
    "filled": 'checkpoint("filled") 时',
    "auto": "补录",
    "final": "填表 agent 退出后重读",
    "unrecorded": "未录到",
}


def conclude(meta: dict, results) -> tuple[str, str | None]:
    """(conclusion, why not filled) from the job's meta.json and its check results."""
    checks, page = meta.get("checks") or {}, meta.get("page") or {}
    status, agent = meta.get("status") or "filled", meta.get("agent_result")
    received = page.get("received") or meta.get("received_seen")  # the final page, or any page recorded (5.6)
    if received or (checks.get("not_submitted") is False and agent != "already_applied"):
        return "maybe_submitted", None
    if agent in NOT_ELIGIBLE:
        return "not_eligible", None
    if agent in AGENT_REASONS or agent in REASONS:
        return "not_filled", AGENT_REASONS.get(agent, agent)
    if status == "gate" or agent in GATE_REASONS:
        return "not_filled", "gate"
    if status in UNREADABLE:
        return "unreadable", None
    if agent not in (None, "dry_run"):
        return "not_filled", agent
    if not any(r.field_id and r.result != "unreadable" for r in results):
        return "unreadable", None
    if any(r.severity in ("fact", "guess") for r in results):
        return "do_not_submit", None
    return "check_then_submit", None


def _count(results, severity: str) -> int:
    return sum(1 for r in results if r.severity == severity)


def _headline(meta: dict, conclusion: str, reason: str | None, results) -> str:
    job = meta.get("job_id", "")
    if conclusion == "maybe_submitted":
        said = (meta.get("page") or {}).get("received") or meta.get("received_seen")
        why = f"最后一页写着「{said}」" if said else "第 4 项（没有提交）没通过"
        return f"可能已提交：{why}。请先打开标签页确认，再决定怎么办。"
    if conclusion == "not_eligible":
        return (
            "不符合申请资格：页面写明不收这类申请人，如实作答会被系统自动拒（填表 agent 的结果：not_eligible），"
            "原文见下面的备注。这个岗位不用再填，投不投由你决定。"
        )
    if conclusion == "not_filled":
        agent = meta.get("agent_result") or meta.get("status")
        advice = ADVICE.get(reason or "", "协调者核对标签页状态，再决定接着填还是重跑 {job}。").format(job=job)
        tail = "下面是已经填了的部分的核对结果。" if any(r.field_id for r in results) else ""
        return f"没填完：{REASONS.get(reason or '', reason)}（填表 agent 的结果：{agent}）。{advice}{tail}"
    if conclusion == "unreadable":
        status = meta.get("status")
        why = status if status in UNREADABLE else "一栏都没读到"
        return f"读不出：{why}。标签页可能还在，请你自己打开看。"
    if conclusion == "do_not_submit":
        facts = sum(1 for r in results if r.severity == "fact" and not _per_segment(r))
        facts += len({r.segment for r in results if r.severity == "fact" and _per_segment(r)})
        return f"不能提交：{facts} 个错的事实、{_count(results, 'guess')} 个无依据的猜测。下面这些改好之前不要提交。"
    if not any(r.field_id and r.result == "correct" for r in results):
        return "检查后可提交：没有查到错的事实，但没有一栏由代码核对为对，请逐栏看一遍标签页再提交。"
    return "检查后可提交：没有查到错的事实。请按下面几条看一遍标签页再提交。"


def _span(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1]}"


def _per_segment(r) -> bool:
    """Results reported once per segment rather than per field: extra, empty and tied segments."""
    return r.segment is not None and (
        r.note.startswith(EXTRA) or r.note == EMPTY_BLOCK or (r.result == "ambiguous" and bool(r.candidates))
    )


def _segment_items(results) -> list[str]:
    """One item per extra or tied segment, in page order. An extra segment with nothing filled in is not a wrong
    fact: it is an empty block the user may delete or fill."""
    by_segment: dict[int, list] = defaultdict(list)
    for r in results:
        if _per_segment(r):
            by_segment[r.segment].append(r)
    items = []
    for rows in by_segment.values():
        span = _span([r.field_id for r in rows])
        if rows[0].result == "ambiguous":
            keys = "、".join(rows[0].candidates)
            items.append(f"{span} 这一段对到了 {keys}，代码分不出是哪一条，所以没有核对。请你在标签页里看一眼。")
        elif any(r.result == "wrong" for r in rows):
            label = rows[0].note.split("（", 1)[1].rsplit("）", 1)[0]
            items.append(f"{span} {EXTRA}：{label}，标准答案里没有这一段。请在标签页里删掉这一段。")
        else:
            items.append(f"{span} 是空着的一段，对不上标准答案里的哪一段。可以删掉；如果是给少了的那段留的，请填上。")
    return items


def todo_items(meta: dict, conclusion: str, reason: str | None, results) -> tuple[str, ...]:
    """The headline, then one item per problem, worst first; fields nobody checked are listed together. A job the
    applicant is not eligible for gets the headline alone: there is nothing to fill in (decision 23)."""
    items = [_headline(meta, conclusion, reason, results)]
    if conclusion == "not_eligible":
        return tuple(items)
    groups: dict[str, list[str]] = defaultdict(list)
    for r in results:
        where = f"{r.field_id} {r.question}" if r.field_id else r.question
        if _per_segment(r):
            continue  # one item per segment, below
        if r.result == "wrong" and r.severity == "fact":
            why = f"，{r.note}" if r.note else ""
            groups["fact"].append(
                f"{where}：现在是「{r.value}」，应为「{r.expected}」（{r.key}）{why}。请在标签页里改。"
            )
        elif r.severity == "guess":
            groups["guess"].append(f"{where}：{r.note}「{r.value}」。请确认，或补标准答案（{r.key}）。")
        elif r.note.startswith(REQUIRED_EMPTY):
            groups["required"].append(f"{r.field_id} {r.question}{r.note.removeprefix(REQUIRED_EMPTY)}")
        elif r.severity == "missing" and r.field_id and r.expected:
            groups["missing"].append(f"{where}：{r.note or '空着'}，应为「{r.expected}」（{r.key}）。")
        elif r.severity == "missing" and r.field_id:
            groups["missing"].append(f"{where}：{r.note or '空着'}。")
        elif r.severity == "missing":
            groups["missing"].append(f"页面上少了一段：{r.expected}（{r.key}）。")
        elif r.severity == "format":
            groups["format"].append(f"{where}：「{r.value}」应写成「{r.expected}」，{r.note}。")
        elif r.result == "ambiguous":
            groups["ambiguous"].append(f"{where}：{r.note}，代码没判。请在其中一条的 asks 里补能区分的短语。")
        elif r.result == "no_answer" and r.key and r.note.startswith("页面上没有标准答案的选项"):
            state = "，空着" if is_empty(r.value) else f"，现在是「{r.value}」"
            groups["no_option"].append(
                f"{where}：{r.note}{state}。请补这一条（{r.key}）的 otherwise，或在标签页里自己选。"
            )
        elif r.result == "no_answer" and r.key and not is_empty(r.value):
            groups["spelling"].append(f"{where}：「{r.value}」对不上 {r.key} 的写法。{r.note}，请补 accept。")
        elif r.result == "not_reviewed" and r.note == OLD_PAGE:
            groups["old_page"].append(f"{r.field_id} {r.question[:60]}「{r.value[:40]}」")
        elif r.result == "not_reviewed":
            groups["not_reviewed"].append(f"{r.field_id} {r.question[:60]}「{r.value[:40]}」")
        elif r.result == "no_answer" and r.note != NAV_NOTE and r.field_id and is_empty(r.value):
            groups["empty"].append(f"{r.field_id} {r.question[:60]}")
    items += groups["fact"] + _segment_items(results)
    items += groups["guess"] + groups["missing"]
    if groups["required"]:
        items.append(
            f"必填、空着、也没有标准答案的 {len(groups['required'])} 栏（不填交不了，请补标准答案或在标签页里自己填）："
            + "；".join(groups["required"])
        )
    items += groups["format"] + groups["ambiguous"]
    items += groups["no_option"] + groups["spelling"]
    if groups["old_page"]:
        items.append(
            f"联系方式 {len(groups['old_page'])} 栏没有核对：{OLD_PAGE}。请在标签页里看一眼："
            + "；".join(groups["old_page"])
        )
    if groups["not_reviewed"]:
        items.append(
            f"未审查 {len(groups['not_reviewed'])} 栏（代码判断不了，请在标签页里看一眼）："
            + "；".join(groups["not_reviewed"])
        )
    if groups["empty"]:
        items.append(
            f"空着、也没有标准答案的 {len(groups['empty'])} 栏（要填的话，请补标准答案或要求清单）："
            + "；".join(groups["empty"])
        )
    return tuple(items)


def check_pages(pages, answers, contacts: bool = True, company: str = "") -> tuple[CheckResult, ...]:
    """Every page of a recorded run checked on its own (segments are matched page by page), each result carrying
    its page, where the values come from, when they were read and the field's source. A page never read back has
    every field unreadable."""
    results: list[CheckResult] = []
    offset = 0  # segments are numbered per page; numbered on across pages they stay apart in the report
    for page in pages:
        where = {"page": page.page, "read_from": page.read_from, "read_at": page.read_at}
        if page.read_from == "unrecorded":
            results += [
                CheckResult(field_id=f.id, question=f.question, key=None, result="unreadable", note=UNRECORDED, **where)
                for f in page.fields
            ]
            continue
        checked = check_page(page.fields, answers, contacts=contacts, company=company) if page.fields else ()
        for r in checked:
            segment = None if r.segment is None else r.segment + offset
            results.append(r.model_copy(update={**where, "source": page.sources.get(r.field_id), "segment": segment}))
        offset += 1 + max((r.segment for r in checked if r.segment is not None), default=-1)
    return tuple(results)


def _page_items(pages, results=()) -> list[str]:
    items = []
    for page in pages:
        if page.read_from == "unrecorded" and page.fields:
            title = f"（{page.title}）" if page.title else ""
            items.append(
                f"第 {page.page} 页{title}没录到读回值：这一页的 {len(page.fields)} 栏读不出（{UNRECORDED}），"
                "请你在标签页里看这一页。"
            )
    unrecorded = [r for r in results if r.source == "unrecorded" and r.field_id]
    if unrecorded:  # spec 3.2, 5.3: no blank read, so code cannot tell what the site filled in
        listed = "；".join(f"{r.field_id} {r.question}「{r.value}」" for r in unrecorded)
        items.append(f"这几栏的页没有填之前的录制，分不出是不是网站预填的（来源未录到），请在标签页里看一眼：{listed}")
    return items


def job_report(meta: dict, fields, answers, contacts: bool = True, pages=None) -> JobReport:
    """One job's report from its meta.json and the fields read back from its last page, or, for a run recorded page
    by page (slice 2a), from every page of its recording (`pages`, recording.read_run). `contacts=False` for a page
    saved before redact.VERSION (see compare.check_page)."""
    company = meta.get("company", "")
    if pages is None:
        results = check_page(fields, answers, contacts=contacts, company=company) if fields else ()
    else:
        results = check_pages(pages, answers, contacts=contacts, company=company)
    conclusion, reason = conclude(meta, results)
    infos = tuple(PageInfo(page=p.page, title=p.title, read_from=p.read_from, read_at=p.read_at) for p in pages or ())
    return JobReport(
        job_id=meta.get("job_id", ""),
        company=meta.get("company", ""),
        title=meta.get("title", ""),
        status=meta.get("status") or "filled",
        agent_result=meta.get("agent_result"),
        conclusion=conclusion,
        not_filled_reason=reason,
        fields=results,
        todo=todo_items(meta, conclusion, reason, results) + tuple(_page_items(pages or (), results)),
        page_dir=meta.get("page_dir"),
        notes=tuple(meta.get("notes") or ()),
        recording_id=meta.get("recording"),
        pages=infos,
    )


def _tally(report: JobReport) -> str:
    rows = [r for r in report.fields if r.field_id]
    parts = [f"读回 {len(rows)} 栏：对 {sum(r.result == 'correct' for r in rows)}"]
    parts += [f"{label} {_count(report.fields, s)}" for s, label in SEVERITIES.items() if _count(report.fields, s)]
    for result, label in (("ambiguous", "有歧义"), ("not_reviewed", "未审查")):
        n = sum(r.result == result for r in rows)
        if n:
            parts.append(f"{label} {n}")
    return "、".join(parts)


def _problems(report: JobReport) -> set[str]:
    """Answer-sheet keys with a problem: a severity, a tie, or a no-answer that asks the user to amend the entry."""
    keys = {r.key for r in report.fields if r.key and r.severity != "none"}
    keys |= {r.key for r in report.fields if r.key and r.result == "no_answer" and r.note and r.note != NAV_NOTE}
    return keys | {key for r in report.fields if r.result == "ambiguous" for key in r.candidates}


def summary_md(run_id: str, reports, skipped=()) -> str:
    """The run's summary. `skipped`: jobs not visited because they were recorded before (spec 4.1)."""
    reports = list(reports)
    counts = {c: sum(r.conclusion == c for r in reports) for c in CONCLUSIONS}
    lines = [
        f"# 填表报告 {run_id}",
        "",
        f"- 岗位 {len(reports)} 个：" + "、".join(f"{CONCLUSIONS[c]} {n}" for c, n in counts.items() if n),
        "- 核对在存下的页上做，不访问网站。每个岗位的逐栏结果在同一目录的 <岗位>.json 里。",
        "- 本模块从不提交；还没有独立评审（切片 2c），所以结论最好也只到“检查后可提交”。",
        "",
    ]
    for report in reports:
        reason = (
            f"：{REASONS.get(report.not_filled_reason, report.not_filled_reason)}" if report.not_filled_reason else ""
        )
        lines += [
            f"## {report.job_id} · {report.company} · {report.title}",
            "",
            f"**结论：{CONCLUSIONS[report.conclusion]}{reason}**",
            "",
            report.todo[0] if report.todo else "",
            "",
            f"- {_tally(report)}",
            *(
                [
                    "- 读回："
                    + "；".join(
                        f"第 {p.page} 页{f' {p.title}' if p.title else ''}：{READ_FROM[p.read_from]}"
                        for p in report.pages
                    )
                ]
                if report.pages
                else []
            ),
            *([f"- 录制：{report.recording_id}"] if report.recording_id else []),
            *([f"- 页面目录：{report.page_dir}"] if report.page_dir else []),
            *(f"- 填表 agent 的备注：{note}" for note in report.notes),
            "",
        ]
        if len(report.todo) > 1:
            lines += ["需要你做的事：", ""]
            lines += [f"{n}) {item}" for n, item in enumerate(report.todo[1:], start=1)]
            lines.append("")
    seen: dict[str, list[str]] = defaultdict(list)
    for report in (r for r in reports if r.conclusion != "not_eligible"):  # nothing was filled there on purpose
        for key in sorted(_problems(report)):
            seen[key].append(report.job_id)
    if skipped:
        lines += ["## 这次没有访问的岗位", ""]
        for item in skipped:
            last = CONCLUSIONS.get(item.get("last_conclusion") or "", item.get("last_conclusion") or "没有结论")
            lines.append(
                f"- {item['job_id']}：已录制，请用重放重测（录制 {item['recording_id']}，"
                f"上次运行 {item.get('last_run')} 的结论：{last}）。要再真实访问，在清单里写 revisit: true。"
            )
        lines.append("")
    repeated = [f"- {key}：{'、'.join(jobs)}" for key, jobs in seen.items() if len(jobs) >= 2]
    lines += ["## 在两个以上岗位出现的问题", "", *(repeated or ["（没有）"]), ""]
    return "\n".join(lines)


def write_reports(run_dir: Path, reports, redactor, skipped=()) -> Path:
    """<job>.json for each report and summary.md, all masked, in the run folder. Returns summary.md's path."""
    run_dir = Path(run_dir)
    reports = list(reports)
    for report in reports:
        data = redactor.mask_json(report.model_dump(mode="json"))
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (run_dir / f"{report.job_id}.json").write_text(text, encoding="utf-8")
    summary = run_dir / "summary.md"
    summary.write_text(redactor.mask(summary_md(run_dir.name, reports, skipped)), encoding="utf-8")
    return summary
