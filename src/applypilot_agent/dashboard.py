"""A local review summary; approval remains an explicit user action in Codex."""

from html import escape
from pathlib import Path

from .service import AgentService

LABELS = {
    "discovered": "待判断",
    "assessed": "待准备",
    "needs_info": "需要补充信息",
    "review": "等待审核",
    "ready": "准备就绪",
    "submitting": "提交中，等待核验",
    "unknown": "提交结果未知",
    "submitted": "已核验投递",
    "skipped": "已跳过",
    "retryable": "可重新准备",
}

HINTS = {
    "submitted": "申请已记录。后续招聘回复可以在 Codex 对话中补充。",
    "skipped": "已按求职要求跳过。方向变化时可以重新评估。",
    "needs_info": "在 Codex 对话中补充缺失信息后继续。",
    "unknown": "先核对投递结果，避免重复提交。",
    "submitting": "正在等待这次提交的实际结果。",
    "review": "在 Codex 对话中告诉我需要修改的地方，或确认这份材料。",
    "ready": "材料已准备好，助手会按已保存的授权继续。",
}


def write_dashboard(service: AgentService, path: Path) -> Path:
    from .quality import QualityService

    quality = QualityService(service)
    summary = quality.summary()
    quality_cards = []
    for alert in quality.alerts():
        if alert["acknowledged_at"] is not None:
            continue
        job = next((item["job"] for item in service.list_jobs() if item["job"]["id"] == alert.get("job_id")), None)
        title = f"{job['company']} · {job['title']}" if job else "申请材料复核"
        message = {
            "model_proxy_issues": "独立模型复核发现疑似材料问题，请在 Codex 中查看具体证据。",
            "audit_inconclusive": "本次复核证据不足，需要进一步检查。",
        }.get(alert["kind"], "材料快照或复核流程需要检查，请在 Codex 中查看待办。")
        quality_cards.append(f"<li><strong>{escape(title)}</strong>：{escape(message)}</li>")
    audit_count = sum(summary["reviews"][key] for key in ("pending", "prepared", "blocked"))
    quality_section = (
        f"<section class='quality'><h2>材料质量复核</h2><p>待处理抽检 {audit_count} 份 · "
        f"待查看提醒 {summary['open_alerts']} 条</p><ul>{''.join(quality_cards)}</ul>"
        "<p class='hint'>模型复核是辅助质量信号；抽样通过不代表整批材料都已验证。"
        "有疑问时在 Codex 中查看冻结材料、引用证据和修正建议。</p></section>"
    )
    cards = []
    for item in service.list_jobs():
        job, application = item["job"], item["application"]
        assessment = application["assessment"] or {}
        packet = application["packet"] or {}
        state = application["state"]
        inspections = [
            event["payload"] for event in service.store.events(job["id"]) if event["kind"] == "form_inspected"
        ]
        labels = {
            field["selector"]: field.get("label") or field.get("name") or "申请回答"
            for inspection in inspections
            for field in inspection.get("fields", [])
        }
        reasons = "".join(f"<li>{escape(reason)}</li>" for reason in assessment.get("reasons", []))
        claims = "".join(f"<li>{escape(claim['text'])}</li>" for claim in packet.get("claims", []))
        answers = "".join(
            f"<tr><td>{escape(labels.get(key, '申请回答'))}</td><td>{escape(str(answer['value']))}</td></tr>"
            for key, answer in packet.get("answers", {}).items()
        )
        attachments = "".join(
            f"<a class='attachment' href='{escape(Path(value['path']).resolve().as_uri(), quote=True)}'>"
            f"{escape(value['label'])}</a>"
            for value in packet.get("attachments", [])
        )
        error = f"<p class='issue'>{escape(application['last_error'])}</p>" if application["last_error"] else ""
        version = (application["packet_hash"] or "")[:12]
        cards.append(
            f"<article data-state='{escape(state)}'><div class='top'><span class='state'>{LABELS[state]}</span>"
            f"<span>{escape(assessment.get('fit', ''))}</span></div>"
            f"<h2>{escape(job['title'])}</h2><p>{escape(job['company'])} · {escape(job['location'])}</p>"
            f"<a href='{escape(job['url'], quote=True)}' target='_blank' rel='noreferrer'>查看岗位原文 ↗</a>"
            f"<ul>{reasons}</ul>{error}<details><summary>查看材料与答案 · 版本 {version or '待准备'}</summary>"
            f"<ul>{claims}</ul><table>{answers}</table>{attachments}</details>"
            f"<p class='hint'>{HINTS.get(state, '助手会根据当前资料继续准备。')}</p></article>"
        )
    document = (
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ApplyPilot · 求职工作台</title>
<style>body{margin:0;background:#f4f5f0;color:#20332c;font:16px/1.6 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 24px}h1{font-size:36px;margin-bottom:8px}
.lead,.hint{color:#66736c}.hint{font-size:13px}nav{display:flex;gap:10px;margin:24px 0;flex-wrap:wrap}
button{border:1px solid #ccd5ce;border-radius:20px;background:white;padding:8px 18px;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}
article{padding:24px;background:white;border:1px solid #dce2d9;border-radius:16px;min-width:0;overflow-wrap:anywhere}
.top{display:flex;justify-content:space-between}.state{background:#e3efe6;padding:2px 10px;border-radius:8px}
h2{font-size:22px;line-height:1.35}a{color:#17644f}details{border-top:1px solid #eee;padding-top:12px}
table{width:100%;border-collapse:collapse}td{padding:8px;border-bottom:1px solid #eee;word-break:break-word}
.issue{background:#fff0d6;padding:12px;border-radius:8px}.attachment{display:inline-block;margin:12px 8px 0 0}
[hidden]{display:none}</style></head><body><main><p>APPLYPILOT / LOCAL</p><h1>求职工作台</h1>
<p class="lead">把注意力留给值得争取的岗位。材料、审核与待解决的问题都在这里。</p>
"""
        + quality_section
        + """<nav><button data-filter="all">全部</button><button data-filter="review">等待审核</button>
<button data-filter="needs_info">需要信息</button><button data-filter="unknown">结果未知</button>
<button data-filter="submitted">已投递</button></nav><section class="grid">"""
        + "".join(cards)
        + """</section>
<p class="hint">这是生成时的本地快照；继续工作后可在 Codex 中刷新。没有真实回执的申请会保留为未知。</p>
</main><script>document.querySelectorAll('button[data-filter]').forEach(button=>button.onclick=()=>{
document.querySelectorAll('article').forEach(card=>card.hidden=button.dataset.filter!=='all'&&
card.dataset.state!==button.dataset.filter);});</script></body></html>"""
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path.resolve()
