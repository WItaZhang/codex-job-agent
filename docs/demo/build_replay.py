"""Build the zero-install demo replay from one local browser demo run.

Run the demo, then pass its log directory (task data, not configuration):

    uv run --locked python -m applypilot_agent.demo --config configs/demo.yaml
    uv run --locked python docs/demo/build_replay.py logs/<run_id>

Writes docs/demo/index.html as one self-contained page: screenshots are
embedded, absolute local paths are dropped and hashes are shortened.
``--fragment PATH`` also writes the same page without the document skeleton,
for hosts that supply their own. Standard library only.
"""

import argparse
import base64
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

OUT = Path(__file__).parent / "index.html"
REPO = "https://github.com/WItaZhang/codex-job-agent"

SCENARIO_NAMES = {
    "review-required": ("Review required", "需要审核"),
    "auto-authorized": ("Inside automatic scope", "在自动授权范围内"),
    "receipt-missing": ("Receipt never shown", "回执没有出现"),
}

NOTE_PREFIXES = {
    "Before approval: ": "批准前尝试提交，被拒绝：",
    "Automatic retry blocked: ": "自动重试被拒绝：",
}
NOTES_ZH = {
    "Dry run made zero POSTs. Synthetic approval was bound to the exact packet hash.": (
        "试填没有产生任何 POST；合成批准绑定到这份申请包的哈希。"
    ),
    "Verbatim confirmed facts and the configured scope authorized automatic execution.": (
        "材料只用原样的已确认事实，且岗位在配置的自动范围内，所以允许自动执行。"
    ),
    "Browser receipt, received attachment hash and persisted submission agree.": (
        "浏览器看到的回执、招聘方收到的附件哈希、数据库里的提交记录三者一致。"
    ),
    "Server accepted the POST, but the agent could not observe confirmation and preserved uncertainty.": (
        "服务器接受了 POST，但 Agent 没有观察到确认，于是保留了不确定状态。"
    ),
}


def t(en: str, zh: str) -> str:
    return f'<span class="en">{escape(en)}</span><span class="zh" lang="zh-CN">{escape(zh)}</span>'


def short(value: str | None) -> str:
    return (value or "")[:12]


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def image(path: str) -> str:
    data = Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def note(text: str) -> str:
    for prefix, zh in NOTE_PREFIXES.items():
        if text.startswith(prefix):
            rest = text[len(prefix) :]
            return t(text, zh + rest)
    return t(text, NOTES_ZH.get(text, text))


def describe(event: dict) -> tuple[str, str]:
    """Plain-language label and a mono detail line for one persisted event."""
    kind, p = event["kind"], event["payload"]
    if kind == "assessed":
        a = p["assessment"]
        return t("Fit assessed (fixture input)", "匹配评估（测试输入）"), (
            f"fit={a['fit']} · evidence={','.join(a['evidence_fact_ids'])}"
        )
    if kind == "form_inspected":
        detail = f"{len(p['fields'])} fields · {p['iframes']} iframes · {len(p['blocked_requests'])} blocked"
        return t("Form inspected (read-only)", "观察表单（只读）"), detail
    if kind == "material_rendered":
        return t("Resume rendered from confirmed facts", "用已确认事实生成简历"), (
            f"sha256 {short(p['attachment']['sha256'])}"
        )
    if kind == "packet_saved":
        auto = p["decision"]["action"] == "auto"
        label = (
            t("Packet saved; policy allows automatic submission", "申请包已保存；策略允许自动提交")
            if auto
            else t("Packet saved; policy requires review", "申请包已保存；策略要求审核")
        )
        return label, f"packet {short(p['packet_hash'])} · {p['decision']['reasons'][0]}"
    if kind == "dry_run_prepared":
        return t("Dry run filled the form, no submit", "试填表单，没有提交"), f"plan {short(p['plan_digest'])}"
    if kind == "user_approved":
        return t("Approval bound to this exact packet (synthetic)", "批准绑定到这份申请包（合成）"), (
            f"packet {short(p['packet_hash'])}"
        )
    if kind == "quality_snapshot_frozen":
        return t("Materials frozen for later audit", "冻结材料，供事后抽检"), f"manifest {short(p['manifest_hash'])}"
    if kind == "submit_intent":
        return t("Submit intent written before the click", "点击前先写入提交意图"), (
            f"attempt {short(p['attempt_id'])} · packet {short(p['packet_hash'])}"
        )
    if kind == "submission_observed":
        if p["status"] == "submitted":
            return t("Receipt observed on the page", "页面上观察到回执"), p["receipt"]["text"]
        return t("No confirmation observed; state set to unknown", "没有观察到确认；状态记为 unknown"), (
            f"reason={p.get('reason')}"
        )
    return escape(kind), ""


def chip(state: str) -> str:
    labels = {"submitted": ("submitted", "已提交"), "unknown": ("unknown", "不确定")}
    en, zh = labels.get(state, (state, state))
    return f'<span class="chip chip-{escape(state)}">{t(en, zh)}</span>'


def gate(authorization: str) -> str:
    if authorization == "review":
        return f'<span class="gate">{t("user review", "用户审核")}</span>'
    return f'<span class="gate">{t("automatic scope", "自动范围")}</span>'


def scenario_section(scenario: dict, events: list[dict], entry: dict) -> str:
    job = scenario["job_id"]
    obs = scenario["observation"]
    own = [e for e in events if e["job_id"] == job and e["kind"] not in {"job_observed"}]
    start = when(own[0]["created_at"])
    rows = []
    for e in own:
        label, detail = describe(e)
        offset = (when(e["created_at"]) - start).total_seconds()
        rows.append(
            f'<li class="event"><span class="seq">{e["seq"]:02d}</span>'
            f'<span class="dt">+{offset:.2f}s</span>'
            f'<span class="what"><span class="label">{label}</span>'
            f'<code class="detail">{escape(detail)}</code></span></li>'
        )
    rendered = next(e for e in own if e["kind"] == "material_rendered")["payload"]["attachment"]["sha256"]
    received = entry["files"]["resume"]["sha256"]
    receipt = obs.get("receipt")
    seen = escape(receipt["text"]) if receipt else t("nothing observed", "没有观察到")
    match = rendered == received
    name_en, name_zh = SCENARIO_NAMES.get(job, (scenario["title"], scenario["title"]))
    mismatch = " row-flag" if receipt is None else ""
    notes = "".join(f"<li>{note(n)}</li>" for n in scenario["notes"])
    return f"""
<section class="scenario" id="{escape(job)}">
  <header class="scenario-head">
    <h2>{t(name_en, name_zh)}</h2>
    <div class="tags">{gate(scenario["authorization"])}{chip(scenario["state"])}</div>
  </header>
  <div class="scenario-body">
    <div class="trace">
      <h3>{t("Event log", "事件日志")}</h3>
      <ol class="events">{"".join(rows)}</ol>
      <h3>{t("Checks this run asserted", "本次运行断言的检查")}</h3>
      <ul class="notes">{notes}</ul>
    </div>
    <div class="evidence">
      <div class="shots">
        <figure><img src="{image(obs["before_screenshot"])}" alt="Filled form before submit · 提交前已填好的表单">
          <figcaption>{t("Before submit", "提交前")}</figcaption></figure>
        <figure><img src="{image(obs["after_screenshot"])}" alt="Page after submit · 提交后的页面">
          <figcaption>{t("After submit", "提交后")}</figcaption></figure>
      </div>
      <div class="table-wrap">
        <table class="ledger">
          <thead><tr><th></th><th>{t("Executor saw on the page", "执行器在页面上看到")}</th>
            <th>{t("Mock employer recorded", "模拟招聘方记录")}</th></tr></thead>
          <tbody>
            <tr><th>{t("Submissions", "提交次数")}</th><td>1 {t("intent", "次意图")}</td>
              <td>{scenario["employer_posts"]} POST</td></tr>
            <tr class="{mismatch.strip()}"><th>{t("Receipt", "回执")}</th><td>{seen}</td>
              <td>{escape(entry["receipt_id"])}</td></tr>
            <tr><th>{t("Resume sha256", "简历 sha256")}</th><td><code>{short(rendered)}</code></td>
              <td><code>{short(received)}</code> {"✓" if match else "✗"}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</section>"""


STYLE = """
/* Layout: an audit record. One reading column for the argument, then a two-pane row per scenario:
   the event log on the left, the evidence (screenshots and the employer's ledger) on the right. */
:root {
  --bg: #f3f5f8; --surface: #ffffff; --ink: #18202e; --muted: #5a6476; --rule: #d9dee6;
  --accent: #2f45c4; --ok: #1d7a4b; --ok-bg: #e3f3ea; --warn: #9a5410; --warn-bg: #fbeedd;
  --code-bg: #eef1f5;
  --display: "Archivo", "Noto Sans SC", system-ui, sans-serif;
  --body: "Source Sans 3", "Noto Sans SC", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #11141a; --surface: #191e27; --ink: #e5e8ee; --muted: #9aa3b3; --rule: #2b3240;
  --accent: #9aa8ff; --ok: #6fd09c; --ok-bg: #173527; --warn: #f0b16b; --warn-bg: #3a2814;
  --code-bg: #222833; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #11141a; --surface: #191e27; --ink: #e5e8ee; --muted: #9aa3b3; --rule: #2b3240;
  --accent: #9aa8ff; --ok: #6fd09c; --ok-bg: #173527; --warn: #f0b16b; --warn-bg: #3a2814;
  --code-bg: #222833; color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font: 1rem/1.6 var(--body); margin: 0; }
.zh { display: none; }
:root[data-lang="zh"] .zh { display: inline; }
:root[data-lang="zh"] .en { display: none; }
.page { max-width: 72rem; margin: 0 auto; padding-inline: 1rem; padding-block: 2.5rem 4rem;
  display: grid; gap: 3rem; }
.top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.eyebrow { font: 500 0.78rem/1.4 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); }
.lang { display: inline-flex; border: 1px solid var(--rule); border-radius: 999px; overflow: hidden; }
.lang button { font: 500 0.85rem var(--body); color: var(--muted); background: transparent; border: 0;
  padding: 0.3rem 0.85rem; cursor: pointer; }
.lang button[aria-pressed="true"] { background: var(--ink); color: var(--bg); }
.lang button:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; margin: 0; }
h1 { font-size: clamp(1.9rem, 4.4vw, 3rem); line-height: 1.12; font-weight: 700; font-stretch: 112%;
  max-width: 22ch; }
.intro { display: grid; gap: 1.25rem; }
.lede { font-size: 1.15rem; color: var(--muted); max-width: 62ch; margin: 0; }
.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1px;
  background: var(--rule); border: 1px solid var(--rule); border-radius: 10px; overflow: hidden; }
.split > div { background: var(--surface); padding: 1rem 1.25rem; min-width: 0; }
.split h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.split p { margin: 0.35rem 0 0; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; }
.card { display: grid; gap: 0.5rem; align-content: start; padding: 1rem 1.15rem; border: 1px solid var(--rule);
  border-radius: 10px; background: var(--surface); color: inherit; text-decoration: none; }
.card:hover { border-color: var(--accent); }
.card .name { font: 600 1.05rem/1.3 var(--display); }
.card .facts { font: 0.85rem/1.5 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
.card.flag { border-color: var(--warn); }
.callout { border-left: 3px solid var(--warn); padding: 0.25rem 0 0.25rem 1rem; max-width: 62ch; margin: 0; }
.tags { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.chip, .gate { font: 500 0.78rem/1 var(--mono); padding: 0.32rem 0.55rem; border-radius: 4px; white-space: nowrap; }
.chip-submitted { color: var(--ok); background: var(--ok-bg); }
.chip-unknown { color: var(--warn); background: var(--warn-bg); }
.gate { color: var(--accent); border: 1px solid currentColor; }
.scenario { display: grid; gap: 1rem; border-top: 1px solid var(--rule); padding-top: 1.75rem; }
.scenario-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
.scenario-head h2 { font-size: 1.5rem; font-weight: 650; }
.scenario-body { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 2rem; }
@media (max-width: 860px) { .scenario-body { grid-template-columns: minmax(0, 1fr); } }
.trace, .evidence { display: grid; gap: 0.85rem; align-content: start; min-width: 0; }
.trace h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.events { list-style: none; margin: 0; padding: 0; display: grid; }
.event { display: grid; grid-template-columns: 2rem 4.2rem minmax(0, 1fr); gap: 0.5rem;
  padding: 0.5rem 0; border-bottom: 1px dashed var(--rule); }
.seq, .dt { font: 0.8rem/1.6 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
.what { display: grid; min-width: 0; }
.label { font-weight: 600; }
.detail { font: 0.78rem/1.5 var(--mono); color: var(--muted); overflow-wrap: anywhere; }
.notes { margin: 0; padding-left: 1.1rem; display: grid; gap: 0.35rem; color: var(--muted); font-size: 0.95rem; }
.shots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem; }
figure { margin: 0; display: grid; gap: 0.35rem; }
figure img { width: 100%; max-width: 100%; aspect-ratio: 4 / 3; object-fit: cover; object-position: 48% 0;
  border: 1px solid var(--rule); border-radius: 6px; background: #fff; }
figcaption { font: 0.78rem var(--mono); color: var(--muted); }
.table-wrap { overflow-x: auto; }
.ledger { width: 100%; border-collapse: collapse; font-size: 0.92rem; font-variant-numeric: tabular-nums; }
.ledger th, .ledger td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--rule); vertical-align: top; }
.ledger thead th { font: 500 0.75rem var(--mono); text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.ledger tbody th { font-weight: 600; white-space: nowrap; }
.ledger .row-flag td:first-of-type { color: var(--warn); font-weight: 600; }
.ledger .row-flag { background: var(--warn-bg); }
code { font-family: var(--mono); font-size: 0.85em; }
.rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr)); gap: 1rem 2rem;
  margin: 0; padding: 0; list-style: none; }
.rules li { display: grid; gap: 0.2rem; padding-top: 0.75rem; border-top: 1px solid var(--rule); }
.rules .where { font: 0.78rem var(--mono); color: var(--muted); }
.block { display: grid; gap: 1rem; }
.block > h2 { font-size: 1.5rem; font-weight: 650; }
.prose { max-width: 66ch; display: grid; gap: 0.75rem; }
.prose p { margin: 0; }
pre { margin: 0; background: var(--code-bg); border-radius: 8px; padding: 1rem; overflow-x: auto;
  font: 0.85rem/1.6 var(--mono); }
a { color: var(--accent); }
footer { font-size: 0.9rem; color: var(--muted); border-top: 1px solid var(--rule); padding-top: 1.25rem; }
@media (prefers-reduced-motion: no-preference) { .card { transition: border-color 0.15s; } }
"""

SCRIPT = """
(function () {
  var root = document.documentElement;
  function stored() { try { return localStorage.getItem("replay-lang"); } catch (e) { return null; } }
  function pick() {
    var h = location.hash.replace("#", "");
    if (h === "zh" || h === "en") return h;
    var s = stored();
    if (s === "zh" || s === "en") return s;
    return (navigator.language || "").toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
  }
  function buttons() {
    var lang = root.getAttribute("data-lang");
    document.querySelectorAll(".lang button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
    });
  }
  function apply(lang) {
    root.setAttribute("data-lang", lang);
    root.setAttribute("lang", lang === "zh" ? "zh-CN" : "en");
    buttons();
  }
  apply(pick());
  document.addEventListener("DOMContentLoaded", buttons);
  document.addEventListener("click", function (event) {
    var b = event.target.closest(".lang button");
    if (!b) return;
    apply(b.dataset.lang);
    try { localStorage.setItem("replay-lang", b.dataset.lang); } catch (e) {}
  });
})();
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,500..800'
    "&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;600&family=Source+Sans+3:wght@400;600"
    '&display=swap">'
)

RULES = [
    (
        ("Approval binds the exact packet", "批准绑定整份申请包"),
        (
            (
                "Answers, attachment hashes, job and profile versions and the browser plan form one hash, "
                "and the approval is also tied to the current policy. Change any of them and it no longer applies."
            ),
            "答案、附件哈希、岗位和档案版本、浏览器计划合成一个哈希，批准还绑定当前策略；任何一项变化都会失效。",
        ),
        "policy.py · execution.py",
    ),
    (
        ("Dry runs cannot write", "试填不能写入"),
        (
            "Until the submission step the browser aborts non-GET requests to the form's origin and any request to another origin.",
            "到提交那一步之前，浏览器中止发往表单来源的非 GET 请求和所有跨域请求。",
        ),
        "browser.py",
    ),
    (
        ("Intent is recorded before the click", "点击前先记录提交意图"),
        (
            "A per-job file lock stops two operations on the same application from running at once.",
            "每个岗位一把文件锁，同一份申请不能同时运行两个操作。",
        ),
        "execution.py · lease.py",
    ),
    (
        ("Success needs an observed receipt", "成功必须有观察到的回执"),
        (
            "A model saying it worked does not count. The runtime looks for the confirmation on the page.",
            "模型说成功不算数，运行时要在页面上看到确认。",
        ),
        "browser.py · execution.py",
    ),
    (
        ("Unknown is a locked state", "不确定是锁定状态"),
        (
            (
                "The runtime never retries on its own and refuses another submit. Only a reconciliation with "
                "user-checked evidence can move it to submitted or retryable."
            ),
            "运行时从不自行重试，再次提交也会被拒绝；只有带用户核实证据的对账，才能改成已提交或可重试。",
        ),
        "execution.py",
    ),
    (
        ("Sent materials are frozen for audit", "已发送材料冻结待审"),
        (
            (
                "A sampled share goes to two fresh model contexts, one for hiring relevance and one for factual "
                "support. Their findings are proxy judgements, not human review."
            ),
            "按比例抽样，交给两个全新的模型上下文：一个看招聘相关性，一个核对事实依据；这是代理判断，不是人工评审。",
        ),
        "quality/",
    ),
]


def build(run_dir: Path) -> tuple[str, str]:
    """Return the page head (title, fonts, style, script) and its body content."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    events = json.loads((run_dir / "events.json").read_text(encoding="utf-8"))
    ledger = [
        json.loads(line)
        for line in (run_dir / "employer-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not summary.get("passed") or not summary.get("synthetic"):
        raise SystemExit("Only a passing synthetic demo run can be published as a replay")
    by_job = {entry["job_id"]: entry for entry in ledger}
    scenarios = summary["scenarios"]
    run_id = summary["run_id"]
    # Demo run directories are named in UTC.
    started = datetime.strptime(run_id[:15], "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    stamp = started.strftime("%Y-%m-%d %H:%M UTC")

    cards = []
    for s in scenarios:
        name_en, name_zh = SCENARIO_NAMES.get(s["job_id"], (s["title"], s["title"]))
        seen = s["observation"].get("receipt") is not None
        facts_en = f"{s['employer_posts']} POST received · receipt {'seen' if seen else 'not seen'}"
        facts_zh = f"招聘方收到 {s['employer_posts']} 次 POST · {'看到' if seen else '没看到'}回执"
        flag = "" if seen else " flag"
        cards.append(
            f'<a class="card{flag}" href="#{escape(s["job_id"])}"><span class="name">{t(name_en, name_zh)}</span>'
            f'<span class="tags">{gate(s["authorization"])}{chip(s["state"])}</span>'
            f'<span class="facts">{t(facts_en, facts_zh)}</span></a>'
        )
    sections = "".join(scenario_section(s, events, by_job[s["job_id"]]) for s in scenarios)
    rules = "".join(
        f'<li><strong>{t(*title)}</strong><span>{t(*body)}</span><span class="where">{escape(where)}</span></li>'
        for title, body, where in RULES
    )

    head = f"<title>Codex Job Agent Replay</title>\n{FONTS}\n<style>{STYLE}</style>\n<script>{SCRIPT}</script>\n"
    return (
        head,
        f"""<main class="page">
  <div class="top">
    <span class="eyebrow">{
            t(f"Replay · recorded run {stamp} · synthetic data", f"回放 · 运行于 {stamp} · 合成数据")
        }</span>
    <div class="lang" role="group" aria-label="Language">
      <button type="button" data-lang="en" aria-pressed="true">English</button>
      <button type="button" data-lang="zh" aria-pressed="false">中文</button>
    </div>
  </div>

  <section class="intro">
    <h1>{
            t(
                "An agent that submits at most once, only with permission, and says when it isn't sure",
                "只在获准时提交、最多提交一次、不确定就如实记录的 Agent",
            )
        }</h1>
    <p class="lede">{
            t(
                "Codex decides what to write. A deterministic runtime decides whether it may be sent, sends it at most "
                "once, and records only what it actually observed. This page replays one real run: real Chromium, real "
                "HTTP and SQLite, against a local mock employer with synthetic people and jobs.",
                "Codex 负责判断和写作；确定性的运行时决定能不能发、保证最多发一次，并且只记录实际观察到的结果。"
                "这一页回放的是一次真实运行：真实的 Chromium、HTTP 和 SQLite，对象是本地模拟的招聘网站，"
                "人和岗位都是合成数据。",
            )
        }</p>
    <div class="split">
      <div><h3>{t("The model decides", "模型判断")}</h3>
        <p>{
            t(
                "Whether a job fits, which confirmed facts to use, how to answer application questions.",
                "岗位是否合适、用哪些已确认的事实、申请问题怎么回答。",
            )
        }</p></div>
      <div><h3>{t("The runtime enforces", "运行时保证")}</h3>
        <p>{
            t(
                "Authorization, packet versions, one submission per approval, receipts, retry rules.",
                "授权、申请包版本、一次批准只提交一次、回执、重试规则。",
            )
        }</p></div>
    </div>
  </section>

  <section class="block">
    <h2>{t("Three runs through the same executor", "同一个执行器的三次运行")}</h2>
    <div class="summary">{"".join(cards)}</div>
    <p class="callout">{
            t(
                "In the third run the mock employer got the application, but the page never confirmed it. The "
                "executor recorded unknown and refused a second submit, so the application was not sent twice.",
                "第三次运行中，模拟招聘方其实收到了申请，但页面没有给出确认。执行器把状态记为 unknown，"
                "并拒绝了再次提交，所以申请没有被重复发送。",
            )
        }</p>
  </section>

  {sections}

  <section class="block">
    <h2>{t("What the runtime enforced", "运行时保证的规则")}</h2>
    <ul class="rules">{rules}</ul>
  </section>

  <section class="block">
    <h2>{t("What this replay does not show", "这次回放不能说明的事")}</h2>
    <div class="prose">
      <p>{
            t(
                "No language model ran here. The fit assessments and the approval in the first run are fixtures, so "
                "this tests the runtime: the part that has to be right every time. Model quality is evaluated "
                "separately with independent reviewers and bias checks.",
                "这次回放没有运行语言模型。匹配评估和第一个场景里的批准都是测试输入，所以它检验的是运行时，"
                "也就是每次都必须正确的那一部分。模型输出的质量另外用独立评审和偏差检验来评估。",
            )
        }</p>
      <p>{
            t(
                "The employer is a loopback server written for the test. Passing here does not mean every real "
                "applicant tracking system is supported, and it says nothing about interview or offer rates.",
                "招聘网站是为测试写的本地服务器。这里通过不代表所有真实招聘系统都支持，也不能说明面试率或录用率。",
            )
        }</p>
    </div>
  </section>

  <section class="block">
    <h2>{t("Run it yourself", "自己运行")}</h2>
    <pre><code>git clone {REPO}.git
cd codex-job-agent
uv sync --locked --extra dev
uv run playwright install chromium
uv run python -m applypilot_agent.demo --config configs/demo.yaml
uv run python docs/demo/build_replay.py logs/&lt;run_id&gt;</code></pre>
  </section>

  <footer>{t("Generated from run", "生成自运行记录")} <code>{escape(run_id)}</code> ·
    <a href="{REPO}">{t("Source on GitHub", "GitHub 源码")}</a></footer>
</main>
""",
    )


def document(head: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{head}</head>\n<body>\n{body}</body>\n</html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path, help="logs/<run_id> directory written by the browser demo")
    parser.add_argument("--fragment", type=Path, help="also write the page without a document skeleton")
    args = parser.parse_args()
    head, body = build(args.run_dir)
    OUT.write_text(document(head, body), encoding="utf-8")
    print(OUT)
    if args.fragment:
        args.fragment.write_text(head + body, encoding="utf-8")
        print(args.fragment)


if __name__ == "__main__":
    main()
