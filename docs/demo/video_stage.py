"""Compose the demo video's stage page from a storyboard and one recorded demo run.

The page is a fixed 1280x720 canvas. Every timed element carries absolute
``data-from``/``data-to`` seconds, and ``window.renderAt(t)`` shows the frame
for time ``t``: opacity and transforms follow from those bounds, and footage
<video> elements seek to the matching moment of the real recording. Rendering is
deterministic, so the same run and storyboard always give the same frames.

Layout: what the executor saw (real browser recording) on the left; on the
right, the three rules being checked and the mock employer's ledger, which only
the demo can see. A one-line strip shows the latest event the runtime persisted.
"""

import json
from html import escape
from pathlib import Path

FADE = 0.35

STATE_LABELS = {
    "review": ("待审核", "awaiting review"),
    "ready": ("可自动提交", "ready to submit"),
    "submitted": ("已提交", "submitted"),
    "unknown": ("结果未知 · unknown", "outcome unknown"),
}


def _short(value: str | None, n: int = 8) -> str:
    return (value or "")[:n]


class Run:
    """The values one demo run recorded, keyed for storyboard placeholders."""

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        self.events = json.loads((run_dir / "events.json").read_text(encoding="utf-8"))
        self.ledger = {
            entry["job_id"]: entry
            for entry in map(json.loads, (run_dir / "employer-ledger.jsonl").read_text(encoding="utf-8").splitlines())
        }
        self.footage = json.loads((run_dir / "footage" / "footage.json").read_text(encoding="utf-8"))
        config = (run_dir / "config.yaml").read_text(encoding="utf-8")
        timeout = next(
            (line.split(":", 1)[1].strip() for line in config.splitlines() if line.strip().startswith("timeout_ms:")),
            "",
        )
        self.timeout_s = f"{int(timeout) / 1000:g}" if timeout.isdigit() else "?"
        if not (self.summary.get("passed") and self.summary.get("synthetic")):
            raise SystemExit("Only a passing synthetic demo run can be rendered")

    def scenario(self, job: str) -> dict:
        return next(s for s in self.summary["scenarios"] if s["job_id"] == job)

    def event(self, job: str, kind: str) -> dict:
        return next(e for e in self.events if e["job_id"] == job and e["kind"] == kind)

    def clip(self, job: str, step: str) -> dict:
        return next(c for c in self.footage["clips"] if c["job_id"] == job and c["step"] == step)

    def values(self, job: str | None) -> dict:
        values = {"timeout_s": self.timeout_s, "run_id": self.summary["run_id"]}
        if job is None:
            return values
        refused = [c["refused"] for c in self.footage["calls"] if c["job_id"] == job and c.get("refused")]
        scenario = self.scenario(job)
        entry = self.ledger[job]
        rendered = self.event(job, "material_rendered")["payload"]["attachment"]["sha256"]
        values.update(
            packet_hash=_short(scenario["packet_hash"]),
            receipt_id=entry["receipt_id"],
            resume_sha=_short(rendered),
            employer_sha=_short(entry["files"]["resume"]["sha256"]),
            posts=str(scenario["employer_posts"]),
            refused=refused[0] if refused else "",
        )
        return values


def _fill(text: str, values: dict) -> str:
    return text.format(**values) if text else ""


def _timed(cls: str, start: float, end: float, inner: str, anim: str = "fade") -> str:
    return f'<div class="tm {cls}" data-from="{start:.3f}" data-to="{end:.3f}" data-anim="{anim}">{inner}</div>'


def _event_label(run: Run, job: str, kind: str, lang: str) -> str:
    """Plain-language label for one persisted event, from the run's own payload."""
    p = run.event(job, kind)["payload"]
    zh = lang == "zh"
    if kind == "packet_saved":
        auto = p["decision"]["action"] == "auto"
        if zh:
            return "策略：允许自动提交" if auto else "策略：需要审核"
        return "Policy: automatic submission" if auto else "Policy: review required"
    if kind == "submission_observed":
        if p["status"] == "submitted":
            return "页面出现回执 → 已提交" if zh else "Receipt on the page → submitted"
        return "页面没有确认 → 结果未知" if zh else "No confirmation → outcome unknown"
    labels = {
        "assessed": ("匹配评估（测试输入，未运行模型）", "Fit assessment (fixture input, no model)"),
        "form_inspected": ("读取表单字段（只读）", "Form fields read (read-only)"),
        "material_rendered": ("用已确认事实生成简历", "Resume built from confirmed facts"),
        "dry_run_prepared": ("试填完成，未点提交", "Dry run done, submit not clicked"),
        "user_approved": ("合成批准：绑定这一版申请", "Synthetic approval bound to this version"),
        "quality_snapshot_frozen": ("冻结要发送的材料", "Materials to send frozen"),
        "submit_intent": ("点击前记下提交意图", "Submit intent saved before the click"),
    }
    return labels.get(kind, (kind, kind))[0 if zh else 1]


def _footage(run: Run, job: str, shots: list[dict], videos: list[dict]) -> str:
    clips = []
    for shot in shots:
        clip = run.clip(job, shot["step"])
        vid = f"v{len(videos)}"
        videos.append(
            {
                "id": vid,
                "src": str((run.dir / "footage" / clip["file"]).resolve()),
                "from": shot["from"],
                "to": shot["to"],
                "offset": shot.get("offset", 0.0),
                "rate": shot.get("rate", 1.0),
                "duration": clip["duration"],
            }
        )
        clips.append(
            _timed("clip", shot["from"], shot["to"], f'<video id="{vid}" muted preload="auto"></video>', "cut")
        )
    return "".join(clips)


def _captions(scene: dict, values: dict, lang: str) -> str:
    return "".join(
        _timed("caption", cap["from"], cap["to"], escape(_fill(cap[lang], values))) for cap in scene.get("captions", [])
    )


def _stamps(scene: dict, values: dict, lang: str) -> str:
    return "".join(
        _timed(f"stamp tone-{s.get('tone', 'accent')}", s["from"], s["to"], escape(_fill(s[lang], values)), "stamp")
        for s in scene.get("stamps", [])
    )


def _card_scene(scene: dict, values: dict, lang: str) -> str:
    start, end = scene["start"], scene["end"]
    blocks = []
    for item in scene["items"]:
        tone = f" tone-{item['tone']}" if item.get("tone") else ""
        inner = escape(_fill(item.get(lang, ""), values))
        if item["style"] == "rule":
            inner = f'<span class="rule-n">{escape(item["n"])}</span><span>{inner}</span>'
        blocks.append(
            _timed(
                f"{item['style']}{tone}", item.get("from", start), item.get("to", end), inner, item.get("anim", "fade")
            )
        )
    layout = scene.get("layout", "title")
    return _timed(f"scene card layout-{layout}", start, end, "".join(blocks), "cut")


def _coldopen_scene(scene: dict, run: Run, lang: str, videos: list[dict]) -> str:
    start, end, job = scene["start"], scene["end"], scene["job"]
    values = run.values(job)
    label = "执行器看到的页面 · 真实浏览器录像" if lang == "zh" else "What the executor sees · real browser recording"
    footage = (
        f'<section class="footage wide"><div class="frame-label"><span>{escape(label)}</span></div>'
        f'<div class="viewport">{_footage(run, job, scene["footage"], videos)}</div></section>'
    )
    body = footage + _stamps(scene, values, lang) + _captions(scene, values, lang)
    return _timed("scene coldopen", start, end, body, "cut")


def _scenario_scene(scene: dict, rules: list[dict], run: Run, lang: str, videos: list[dict]) -> str:
    start, end, job = scene["start"], scene["end"], scene["job"]
    values = run.values(job)
    zh = lang == "zh"
    header = (
        f'<header class="bar"><span class="step-no">{escape(scene["number"])}</span>'
        f'<span class="scenario-name">{escape(scene["name_" + lang])}</span>'
        f'<span class="route">{escape(scene["route_" + lang])}</span></header>'
    )
    parts = [header]

    states = []
    for i, chip in enumerate(scene["states"]):
        until = scene["states"][i + 1]["from"] if i + 1 < len(scene["states"]) else end
        label = STATE_LABELS[chip["state"]][0 if zh else 1]
        states.append(_timed(f"chip state-{chip['state']}", chip["from"], until, escape(label), "cut"))
    label = "执行器看到的页面 · 真实浏览器录像" if zh else "What the executor sees · real browser recording"
    parts.append(
        f'<section class="footage"><div class="frame-label"><span>{escape(label)}</span>'
        f'<span class="chips">{"".join(states)}</span></div>'
        f'<div class="viewport">{_footage(run, job, scene["footage"], videos)}</div></section>'
    )

    # Right column: the three rules, then the mock employer's ledger.
    rows = []
    ticks = {r["n"]: r for r in scene.get("rules", [])}
    for n, rule in enumerate(rules, start=1):
        text = escape(rule[lang])
        slot = f'<div class="rule-row"><span class="rule-n">{n}</span><span>{text}</span></div>'
        if n in ticks:
            slot += _timed(
                f"rule-row ticked tone-{ticks[n].get('tone', 'ok')}",
                ticks[n]["tick"],
                end,
                f'<span class="rule-n">✓</span><span>{text}</span>',
                "stamp",
            )
        rows.append(f'<div class="rule-slot">{slot}</div>')
    rules_title = "三条规则" if zh else "Three rules"
    parts.append(f'<section class="rules"><h3>{rules_title}</h3><div class="rule-list">{"".join(rows)}</div></section>')

    ledger = scene["ledger"]
    counts = []
    for i, step in enumerate(ledger["counts"]):
        until = ledger["counts"][i + 1]["from"] if i + 1 < len(ledger["counts"]) else end
        unit = f"收到 {step['count']} 份" if zh else f"received {step['count']}"
        counts.append(_timed("count", step["from"], until, escape(unit), "stamp" if i else "cut"))
    detail = []
    if "receipt_at" in ledger:
        key = "编号" if zh else "record"
        detail.append(
            _timed(
                "lrow",
                ledger["receipt_at"],
                end,
                f'<span class="lkey">{key}</span><span class="lval">{escape(values["receipt_id"])}</span>',
            )
        )
    if "sha_at" in ledger:
        key = "简历" if zh else "resume"
        same = "= 批准的那份 ✓" if zh else "= the approved file ✓"
        detail.append(
            _timed(
                "lrow",
                ledger["sha_at"],
                end,
                f'<span class="lkey">{key}</span><span class="lval">sha {escape(values["employer_sha"])} {same}</span>',
            )
        )
    title = "模拟招聘方台账 · 仅演示可见" if zh else "Mock employer ledger · demo-only view"
    flag = " flag" if ledger.get("flag") else ""
    parts.append(
        f'<section class="ledger{flag}"><h3>{escape(title)}</h3><div class="counts">{"".join(counts)}</div>'
        f"{''.join(detail)}</section>"
    )

    # Strip: the latest persisted event, replaced as new ones arrive.
    events = scene.get("events", [])
    strip = []
    for i, row in enumerate(events):
        until = events[i + 1]["at"] if i + 1 < len(events) else end
        if "kind" in row:
            seq = f"#{run.event(job, row['kind'])['seq']}"
            text, raw = _event_label(run, job, row["kind"], lang), row["kind"]
        else:
            seq, text, raw = "—", _fill(row[lang], values), _fill(row.get("raw", ""), values)
        tone = f" tone-{row['tone']}" if row.get("tone") else ""
        strip.append(
            _timed(
                f"event{tone}",
                row["at"],
                until,
                f'<span class="seq">{escape(seq)}</span><span class="elabel">{escape(text)}</span>'
                f'<span class="raw">{escape(raw)}</span>',
                "slide",
            )
        )
    strip_title = "运行时写入 SQLite 的最新事件" if zh else "Latest event the runtime wrote to SQLite"
    parts.append(f'<section class="strip"><h3>{escape(strip_title)}</h3>{"".join(strip)}</section>')
    parts.append(_stamps(scene, values, lang))
    parts.append(_captions(scene, values, lang))
    return _timed("scene scenario", start, end, "".join(parts), "cut")


STYLE = """
/* Layout: a 1280x720 recording desk. Left: what the executor sees (real browser footage).
   Right: the three rules and the mock employer's ledger. One caption line at the bottom. */
:root {
  --bg: #f3f5f8; --surface: #ffffff; --ink: #18202e; --muted: #5a6476; --rule: #d9dee6;
  --accent: #2f45c4; --accent-bg: #e7eafb; --ok: #1d7a4b; --ok-bg: #e3f3ea; --warn: #9a5410; --warn-bg: #fbeedd;
  --display: "Archivo", "Noto Sans SC", "WenQuanYi Zen Hei", sans-serif;
  --body: "Source Sans 3", "Noto Sans SC", "WenQuanYi Zen Hei", sans-serif;
  --mono: "IBM Plex Mono", "Noto Sans SC", "WenQuanYi Zen Hei", monospace;
  color-scheme: light;
}
* { box-sizing: border-box; }
html, body { margin: 0; width: 1280px; height: 720px; overflow: hidden; background: var(--bg); color: var(--ink);
  font-family: var(--body); -webkit-font-smoothing: antialiased; }
h3 { margin: 0; font: 500 15px/1.2 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
.tm { visibility: hidden; }
.scene { position: absolute; inset: 0; }
.badge { position: absolute; right: 40px; top: 12px; z-index: 5; font: 500 15px/1 var(--mono); color: var(--muted);
  background: var(--surface); border: 1px solid var(--rule); border-radius: 999px; padding: 7px 14px; }

.card { display: flex; flex-direction: column; justify-content: center; gap: 20px; padding: 40px 96px 0; }
.eyebrow { font: 500 22px/1.3 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.headline { font: 700 56px/1.16 var(--display); font-stretch: 108%; letter-spacing: -0.01em; max-width: 1090px; text-wrap: balance; }
.sub { font: 400 30px/1.4 var(--body); color: var(--muted); max-width: 1060px; }
.body { font: 600 38px/1.35 var(--body); max-width: 1090px; text-wrap: balance; }
.small { font: 400 24px/1.45 var(--body); color: var(--muted); max-width: 1090px; }
.footer { font: 400 20px/1.4 var(--mono); color: var(--muted); }
.url { font: 600 42px/1.2 var(--mono); color: var(--accent); }
.rule { display: flex; gap: 18px; align-items: baseline; font: 600 36px/1.3 var(--body); }
.rule .rule-n { font: 600 30px var(--mono); color: var(--accent); min-width: 36px; }
.tone-warn.body, .tone-warn.rule { color: var(--warn); }

.footage { position: absolute; left: 40px; top: 100px; width: 720px; height: 470px; background: var(--surface);
  border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; }
.footage.wide { left: 190px; width: 900px; height: 470px; top: 70px; }
.frame-label { height: 40px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px;
  font: 500 15px var(--mono); color: var(--muted); border-bottom: 1px solid var(--rule); background: var(--bg); }
.viewport { position: relative; height: 430px; overflow: hidden; background: #fff; }
.clip { position: absolute; inset: 0; }
/* Recording is 1280x720; show its form region (x 280-1038, y 42-494) at 0.95 scale. */
.clip video { position: absolute; width: 1216px; height: 684px; left: -266px; top: -40px; }
.wide .clip video { left: -176px; }
.chips { position: relative; height: 30px; min-width: 200px; }
.chip { position: absolute; right: 0; top: 0; font: 600 18px/1 var(--mono); padding: 6px 12px; border-radius: 6px; white-space: nowrap; }
.state-review, .state-ready { color: var(--accent); background: var(--accent-bg); }
.state-submitted { color: var(--ok); background: var(--ok-bg); }
.state-unknown { color: var(--warn); background: var(--warn-bg); }

/* The badge owns the top 44px; the scenario header sits below it. */
.bar { position: absolute; left: 40px; right: 40px; top: 46px; height: 44px; display: flex; align-items: center; gap: 16px; }
.step-no { font: 500 20px var(--mono); color: var(--muted); }
.scenario-name { font: 700 32px/1 var(--display); white-space: nowrap; }
.route { font: 500 18px var(--mono); color: var(--accent); border: 1.5px solid currentColor; border-radius: 6px; padding: 6px 10px; white-space: nowrap; }

.rules { position: absolute; left: 790px; right: 40px; top: 100px; display: flex; flex-direction: column; gap: 10px; }
.rule-list { display: grid; gap: 6px; }
.rule-list > * { grid-column: 1; }
.rule-row { display: grid; grid-template-columns: 30px 1fr; gap: 8px; align-items: baseline; padding: 7px 12px;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; font: 600 20px/1.3 var(--body); color: var(--muted); }
.rule-row .rule-n { font: 600 18px var(--mono); }
.rule-row.ticked { color: var(--ok); border-color: var(--ok); background: var(--ok-bg); }
.rule-row.ticked.tone-warn { color: var(--warn); border-color: var(--warn); background: var(--warn-bg); }
/* A ticked row is drawn over its unticked twin. */
.rule-slot { position: relative; }
.rule-slot .tm { position: absolute; inset: 0; }

.ledger { position: absolute; left: 790px; right: 40px; top: 300px; height: 178px; padding: 14px 16px;
  border: 2px dashed var(--muted); border-radius: 12px; background: var(--surface); display: flex; flex-direction: column; gap: 8px; }
.ledger.flag { border-color: var(--warn); }
.counts { position: relative; height: 54px; }
.count { position: absolute; left: 0; top: 0; font: 700 44px/1.2 var(--display); transform-origin: left center; }
.ledger.flag .count { color: var(--warn); }
.lrow { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font: 500 17px/1.35 var(--mono); }
.lkey { color: var(--muted); }

.strip { position: absolute; left: 790px; right: 40px; top: 490px; height: 76px; }
.strip h3 { margin-bottom: 6px; font-size: 13px; }
.event { position: absolute; left: 0; right: 0; top: 22px; display: grid; grid-template-columns: auto 1fr; column-gap: 10px;
  padding: 4px 0 0 10px; border-left: 3px solid var(--accent); }
.event.tone-ok { border-left-color: var(--ok); } .event.tone-warn { border-left-color: var(--warn); }
.event .seq { font: 500 15px/1.5 var(--mono); color: var(--muted); grid-row: span 2; }
.event .elabel { font: 600 19px/1.25 var(--body); }
.event .raw { font: 400 13px/1.4 var(--mono); color: var(--muted); }

.stamp { position: absolute; left: 400px; top: 270px; transform-origin: center; translate: -50% 0; z-index: 3;
  font: 700 34px/1.2 var(--display); padding: 14px 22px; border-radius: 10px; border: 3px solid currentColor;
  background: var(--surface); white-space: nowrap; box-shadow: 0 14px 32px rgba(24, 32, 46, 0.16); }
.coldopen .stamp { left: 640px; top: 290px; }
.stamp.tone-warn { color: var(--warn); } .stamp.tone-ok { color: var(--ok); } .stamp.tone-accent { color: var(--accent); }
.caption { position: absolute; left: 40px; right: 40px; bottom: 30px; min-height: 104px; display: flex; align-items: center;
  padding: 12px 26px; background: var(--ink); color: var(--bg); border-radius: 12px; font: 600 34px/1.3 var(--body);
  text-wrap: balance; z-index: 4; }
:root[lang="en"] .caption { font-size: 31px; }
"""

SCRIPT = """
const PLAN = JSON.parse(document.getElementById('plan').textContent);
const FADE = PLAN.fade;
const timed = Array.from(document.querySelectorAll('.tm')).map(el => ({
  el, a: +el.dataset.from, b: +el.dataset.to, anim: el.dataset.anim }));
const videos = PLAN.videos.map(v => Object.assign(v, { el: document.getElementById(v.id) }));
window.ready = Promise.all([document.fonts.ready].concat(videos.map(v => new Promise((resolve, reject) => {
  v.el.addEventListener('loadeddata', resolve, { once: true });
  v.el.addEventListener('error', () => reject(new Error('video failed: ' + v.src)), { once: true });
  v.el.src = 'file://' + v.src;
}))));
function clamp(x) { return Math.max(0, Math.min(1, x)); }
window.renderAt = async (t) => {
  const sig = [];
  for (const item of timed) {
    let o = 0, shift = 0, scale = 1;
    if (t >= item.a && t < item.b) {
      if (item.anim === 'cut') o = 1;
      else {
        const inP = clamp((t - item.a) / FADE), outP = clamp((item.b - t) / (FADE * 0.7));
        o = Math.min(inP, outP);
        const ease = 1 - Math.pow(1 - inP, 3);
        if (item.anim === 'slide') shift = (1 - ease) * 12;
        if (item.anim === 'stamp') scale = 1.18 - 0.18 * ease;
      }
    }
    item.el.style.visibility = o > 0 ? 'visible' : 'hidden';
    item.el.style.opacity = o.toFixed(3);
    if (item.anim === 'slide') item.el.style.transform = `translateY(${shift.toFixed(2)}px)`;
    if (item.anim === 'stamp') item.el.style.transform = `scale(${scale.toFixed(3)})`;
    sig.push(o.toFixed(2), shift.toFixed(1), scale.toFixed(2));
  }
  const seeks = [];
  for (const v of videos) {
    if (t < v.from || t >= v.to) continue;
    const target = Math.min(v.duration - 0.04, Math.max(0, v.offset + (t - v.from) * v.rate));
    const frame = Math.round(target * 25) / 25;
    sig.push(v.id + '@' + frame.toFixed(2));
    if (Math.abs(v.el.currentTime - frame) > 1e-3) {
      seeks.push(new Promise(r => { v.el.addEventListener('seeked', r, { once: true }); v.el.currentTime = frame; }));
    }
  }
  await Promise.all(seeks);
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return sig.join('|');
};
"""

FONTS = (
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,500..800'
    "&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400;500;600;700"
    '&family=Source+Sans+3:wght@400;600;700&display=swap">'
)


def stage_html(storyboard: dict, run: Run, lang: str) -> tuple[str, float]:
    """Return the stage page for one language and the video duration in seconds."""
    videos: list[dict] = []
    scenes = []
    rules = storyboard["rules"]
    for scene in storyboard["scenes"]:
        if scene["kind"] == "scenario":
            scenes.append(_scenario_scene(scene, rules, run, lang, videos))
        elif scene["kind"] == "coldopen":
            scenes.append(_coldopen_scene(scene, run, lang, videos))
        else:
            scenes.append(_card_scene(scene, run.values(scene.get("job")), lang))
    duration = max(scene["end"] for scene in storyboard["scenes"])
    badge = storyboard["badge"]
    scenes.append(_timed("badge", badge.get("from", 0), duration, escape(badge[lang]), "cut"))
    plan = json.dumps({"fade": FADE, "videos": videos})
    html = (
        f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">{FONTS}'
        f"<style>{STYLE}</style></head><body>{''.join(scenes)}"
        f'<script id="plan" type="application/json">{plan}</script><script>{SCRIPT}</script></body></html>'
    )
    return html, duration
