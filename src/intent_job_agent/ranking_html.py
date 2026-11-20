"""The local, regenerable score ranking for pending postings."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from .apply_list import Posting
from .store import Store


def _local_day(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().date().isoformat()


def fetch_days(store: Store, stage: Path) -> dict[str, str]:
    days = {}
    for row in store.db.execute("SELECT job_ids, fetched_at FROM board_snapshots ORDER BY fetched_at"):
        for job_id in json.loads(row["job_ids"]):
            days.setdefault(job_id, _local_day(row["fetched_at"]))
    if stage.is_file():
        imported = {row["url"]: row["job_id"] for row in store.external_rows() if row["job_id"]}
        with closing(sqlite3.connect(stage.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if {"url", "discovered_at"} <= columns:
                for url, stamp in db.execute("SELECT url, discovered_at FROM jobs WHERE discovered_at IS NOT NULL"):
                    try:
                        if job_id := imported.get(url):
                            day = _local_day(stamp)
                            days[job_id] = min(day, days.get(job_id, day))
                    except (ValueError, TypeError):
                        continue
    return days


def write(path: Path, day: str, pending: list[Posting], days: dict[str, str]) -> None:
    def score(posting):
        return max(candidate.result.score for candidate in posting.candidates)

    rows = []
    for rank, posting in enumerate(sorted(pending, key=score, reverse=True), start=1):
        job = posting.use.job
        ids = [j.id for j in posting.listings]
        stamp = min((days[i] for i in ids if i in days), default="未知")
        title = escape(job.title)
        try:
            link = urlsplit(job.url)
            if link.scheme in {"http", "https"} and link.hostname:
                title = f'<a href="{escape(job.url, quote=True)}" target="_blank" rel="noreferrer">{title}</a>'
        except ValueError:
            pass
        location = ", ".join(dict.fromkeys(j.location for j in posting.offered if j.location))
        rows.append(
            f'<tr data-ids="{escape(json.dumps(ids), quote=True)}"><td>{rank}</td><td>{score(posting)}</td>'
            f"<td>{title}</td><td>{escape(job.company)}</td><td>{escape(location)}</td><td><time>{stamp}</time></td>"
            f'<td><input type="checkbox" aria-label="已申请 {escape(job.title, quote=True)}"></td></tr>'
        )
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        out.write(PAGE.replace("{{day}}", escape(day)).replace("{{rows}}", "\n".join(rows)))
    Path(out.name).replace(path)


PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>岗位总榜</title>
<style>
*{box-sizing:border-box}body{font:16px/1.55 Inter,system-ui,sans-serif;color:#202f32;background:#f7f8f5;
margin:clamp(18px,4vw,64px)}h1{font-size:2.3rem;margin:0}p{color:#526361}button{font:inherit;
background:white;color:#164c48;border:1px solid #8ca6a0;border-radius:6px;padding:5px 12px;cursor:pointer}
section{overflow:auto;margin-top:16px}table{border-collapse:collapse;background:white;width:100%}
td,th{text-align:left;padding:10px 14px;border-bottom:1px solid #d9e2dd}th{white-space:nowrap;
background:#e8efea}td:nth-child(2),time{white-space:nowrap;font-variant-numeric:tabular-nums}
a{color:#155a56}input{width:20px;height:20px;accent-color:#176562}
</style><h1>岗位总榜</h1><p>更新于 {{day}} · 按分数降序 · 抓取日取最早记录</p>
<p>勾选后从榜单移除。勾选记录保存在当前浏览器，可在“已申请”中撤销。</p>
<button id="switch" type="button"></button> <span id="count" aria-live="polite"></span>
<p id="error" role="alert" hidden>浏览器无法保存勾选，请在允许本地存储的浏览器中打开此文件。</p>
<section><table><thead><tr><th>排名</th><th>分数</th><th>岗位</th><th>公司</th><th>地点</th>
<th>首次抓取</th><th>已申请</th></tr></thead><tbody>{{rows}}</tbody></table></section>
<script>
const rows = Array.from(document.querySelectorAll('tbody tr'), row => ({
  row, ids: JSON.parse(row.dataset.ids), box: row.querySelector('input')
}));
const key = 'job-ranking-applied-v1:' + location.pathname;
const toggle = document.getElementById('switch');
let archive = false;
function read() {return new Set(JSON.parse(localStorage.getItem(key) || '[]'));}
function render() {
  let saved;
  try {saved = read();} catch {document.getElementById('error').hidden = false;return;}
  let pending = 0, applied = 0;
  for (const {row, ids, box} of rows) {
    const done = ids.some(id => saved.has(id));
    box.checked = done;
    if (done) applied++; else pending++;
    row.hidden = done !== archive;
    row.cells[0].textContent = done ? '—' : pending;
  }
  toggle.textContent = archive ? '返回待申请' : '已申请（' + applied + '）';
  document.getElementById('count').textContent = archive ? '已申请 ' + applied + ' 个' : '待申请 ' + pending + ' 个';
}
for (const {ids,box} of rows) box.onchange = () => {
  try {
    const saved = read();
    for (const id of ids) {if (box.checked) saved.add(id); else saved.delete(id);}
    localStorage.setItem(key, JSON.stringify([...saved]));
  } catch {box.checked = !box.checked;document.getElementById('error').hidden = false;}
  render();
};
toggle.onclick = () => {archive = !archive;render();};
window.addEventListener('storage', render);
render();
</script></html>"""
