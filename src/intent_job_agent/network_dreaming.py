"""Durable, sanitized batch evidence and a host-independent dreaming outbox.

No model or business execution happens here. Only bounded structured observations
cross the host boundary. The online host interprets them outside SQLite transactions.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from . import watchdog
from .network_experience import POLICY, digest

CURRENT = ContextVar("network_execution", default=None)
KINDS = {"stage", "tool_use", "tool_result", "outcome", "missing"}
OBSERVATIONS = {
    "started",
    "returned",
    "error",
    "success",
    "failed",
    "waiting",
    "stopped",
    "interrupted",
    "missing",
    "captcha",
    "login",
    "account_locked",
    "unknown",
}


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fact", "hypothesis", "strategy", "unresolved"]
    sources: list[int] = Field(min_length=1, max_length=20)
    strategy: Literal[
        "inspect_prior_stage", "await_human", "use_existing_resume", "check_current_evidence", "unknown_cause"
    ]
    counterevidence: list[int] = Field(default_factory=list, max_length=20)
    replaces: list[str] = Field(default_factory=list, max_length=20)
    ttl_days: int = Field(default=7, ge=1, le=30)
    explanation: Literal["unknown", "observed_tool_error", "human_gate_unresolved", "prior_attempt_available"] = (
        "unknown"
    )
    condition: Literal["same_scope_and_operation", "human_resolution_observed"] = "same_scope_and_operation"


class Journal:
    def __init__(self, data_dir):
        self.policy = yaml.safe_load(POLICY.read_text())
        self.path = Path(data_dir) / self.policy["file"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY, task TEXT, batch TEXT, parent TEXT, continuation TEXT,
                    pid INTEGER, birth REAL, started REAL, ended REAL, status TEXT);
                CREATE TABLE IF NOT EXISTS trajectory (
                    execution TEXT, seq INTEGER, at REAL, body TEXT, PRIMARY KEY(execution,seq));
                CREATE TABLE IF NOT EXISTS dreams (
                    execution TEXT PRIMARY KEY, status TEXT, lease TEXT, until REAL,
                    attempts INTEGER DEFAULT 0, watermark INTEGER, input TEXT, output TEXT);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, execution TEXT, site TEXT, operation TEXT,
                    body TEXT, expires REAL, active INTEGER DEFAULT 1);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def start(self, task, *, batch=None, parent=None, continuation=None, execution=None):
        if task not in {"fetch", "discovery", "apply", "form_fill", "resume"}:
            raise ValueError("Unknown task")
        if execution is None:
            self.recover()
        execution = execution or uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO executions VALUES(?,?,?,?,?,?,?,?,NULL,?)",
                (
                    execution,
                    task,
                    digest(str(batch)) if batch else None,
                    parent,
                    continuation,
                    os.getpid(),
                    watchdog.me()[1],
                    time.time(),
                    "running",
                ),
            )
        return execution

    def event(
        self, execution, kind, *, site="", operation="", observation="unknown", call="", tool="", action="unknown"
    ):
        if kind not in KINDS or observation not in OBSERVATIONS:
            raise ValueError("Unstructured observation")
        if operation not in {"", "fetch", "apply", "form_fill", "discovery", "resume"}:
            raise ValueError("Unknown operation")
        # Site must come from scope(), never page content. Tool names are only categorized.
        body = dict(
            kind=kind,
            site=site,
            operation=operation,
            observation=observation,
            call=digest(call) if call else "",
            tool=tool if tool in {"browser", "gmail", "checkpoint", "other"} else "other",
            action=action
            if action in {"navigate", "snapshot", "click", "fill", "extract", "retry", "timeout", "unknown"}
            else "unknown",
            coverage="observable_metadata_only",
            account="unknown",
        )
        with self.db() as db:
            row = db.execute("SELECT status FROM executions WHERE id=?", (execution,)).fetchone()
            if not row or row["status"] != "running":
                raise ValueError("Execution is closed")
            seq = db.execute(
                "SELECT coalesce(max(seq),0)+1 FROM trajectory WHERE execution=?", (execution,)
            ).fetchone()[0]
            db.execute("INSERT INTO trajectory VALUES(?,?,?,?)", (execution, seq, time.time(), json.dumps(body)))

    def stream(self, execution, line, site, operation):
        try:
            value = json.loads(line)
        except ValueError:
            return
        if not isinstance(value, dict):
            return
        message = value.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            return
        for item in message["content"]:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind not in {"tool_use", "tool_result"}:
                continue
            name = str(item.get("name", "")).lower()
            tool = next((t for t in ("browser", "gmail", "checkpoint") if t in name), "other")
            self.event(
                execution,
                kind,
                site=site,
                operation=operation,
                observation="started" if kind == "tool_use" else ("error" if item.get("is_error") else "returned"),
                call=str(item.get("id") or item.get("tool_use_id") or ""),
                tool=tool,
                action=next((a for a in ("navigate", "snapshot", "click", "fill", "extract") if a in name), "unknown"),
            )

    def finish(self, execution, status, *, authoritative=False):
        if status not in OBSERVATIONS:
            status = "unknown"
        with self.db() as db:
            row = db.execute("SELECT * FROM executions WHERE id=?", (execution,)).fetchone()
            if not row:
                return
            if row["ended"] is not None:
                if not authoritative or row["status"] == status:
                    return
                db.execute("DELETE FROM dreams WHERE execution=?", (execution,))
                db.execute("UPDATE memories SET active=0 WHERE execution=?", (execution,))
            seq = db.execute(
                "SELECT coalesce(max(seq),0)+1 FROM trajectory WHERE execution=?", (execution,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO trajectory VALUES(?,?,?,?)",
                (
                    execution,
                    seq,
                    time.time(),
                    json.dumps(
                        dict(
                            kind="outcome",
                            observation=status,
                            site="",
                            operation=row["task"],
                            coverage="batch_status_not_website_success",
                        )
                    ),
                ),
            )
            db.execute("UPDATE executions SET ended=?,status=? WHERE id=?", (time.time(), status, execution))
            db.execute(
                "INSERT OR IGNORE INTO dreams(execution,status,watermark) VALUES(?,'pending',?)", (execution, seq)
            )

    def recover(self):
        with self.db() as db:
            rows = list(db.execute("SELECT * FROM executions WHERE status='running'"))
        for row in rows:
            alive = watchdog.alive(row["pid"], row["birth"])
            if not alive:
                self.finish(row["id"], "interrupted")
        with self.db() as db:
            db.execute(
                "UPDATE dreams SET status='error' WHERE attempts>=? AND status='leased' AND until<?",
                (self.policy["dream_max_attempts"], time.time()),
            )
            cutoff = time.time() - self.policy["retention_days"] * 86400
            db.execute(
                "DELETE FROM memories WHERE expires<? OR execution IN (SELECT id FROM executions WHERE ended<?)",
                (time.time(), cutoff),
            )
            db.execute("DELETE FROM trajectory WHERE execution IN (SELECT id FROM executions WHERE ended<?)", (cutoff,))
            db.execute("DELETE FROM dreams WHERE execution IN (SELECT id FROM executions WHERE ended<?)", (cutoff,))
            db.execute("DELETE FROM executions WHERE ended<?", (cutoff,))

    def prepare(self):
        self.recover()
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM dreams WHERE status!='complete' AND attempts<? AND (lease IS NULL OR until<?) "
                "ORDER BY rowid LIMIT 1",
                (self.policy["dream_max_attempts"], time.time()),
            ).fetchone()
            if not row:
                return None
            lease = uuid.uuid4().hex
            events = [
                dict(seq=r["seq"], at=r["at"], **json.loads(r["body"]))
                for r in db.execute("SELECT * FROM trajectory WHERE execution=? ORDER BY seq", (row["execution"],))
            ]
            limit = self.policy["dream_max_events"]
            payload = dict(
                execution_id=row["execution"],
                execution=dict(db.execute("SELECT * FROM executions WHERE id=?", (row["execution"],)).fetchone()),
                lease=lease,
                watermark=row["watermark"],
                events=events[-limit:],
                truncated=len(events) > limit,
                missing=[
                    "browser HTTP",
                    "private reasoning",
                    "tool arguments and bodies",
                    "unexposed vendor internals",
                ],
                schema=Memory.model_json_schema(),
                old_memories=[
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM memories WHERE active=1 AND expires>? AND site IN "
                        "(SELECT DISTINCT json_extract(body,'$.site') FROM trajectory WHERE execution=?)",
                        (time.time(), row["execution"]),
                    )
                ],
            )
            db.execute(
                "UPDATE dreams SET status='leased',lease=?,until=?,attempts=attempts+1,input=? WHERE execution=?",
                (lease, time.time() + self.policy["dream_lease_seconds"], json.dumps(payload), row["execution"]),
            )
            return payload

    def submit(self, lease, memories):
        if len(memories) > self.policy["dream_max_memories"]:
            raise ValueError("Memory budget exceeded")
        parsed = [Memory.model_validate(m) for m in memories]
        with self.db() as db:
            row = db.execute("SELECT * FROM dreams WHERE lease=? AND status='leased'", (lease,)).fetchone()
            if not row or row["until"] < time.time():
                raise ValueError("Stale lease")
            payload = json.loads(row["input"])
            events = {e["seq"]: e for e in payload["events"]}
            current = db.execute("SELECT max(seq) FROM trajectory WHERE execution=?", (row["execution"],)).fetchone()[0]
            if current != row["watermark"]:
                raise ValueError("Stale evidence")
            for m in parsed:
                if any(s not in events for s in m.sources + m.counterevidence):
                    raise ValueError("Unknown source")
                sources = [events[s] for s in m.sources + m.counterevidence]
                scopes = {(s["site"], s["operation"]) for s in sources}
                if len(scopes) != 1 or not sources[0]["site"]:
                    raise ValueError("Mixed or missing scope")
                site, operation = next(iter(scopes))
                if m.strategy == "await_human" and not any(
                    s["observation"] in {"captcha", "login", "account_locked", "waiting"} for s in sources
                ):
                    raise ValueError("No human gate evidence")
                if m.explanation == "observed_tool_error" and not any(s["observation"] == "error" for s in sources):
                    raise ValueError("Explanation has no error evidence")
                if m.explanation == "human_gate_unresolved" and m.strategy != "await_human":
                    raise ValueError("Gate explanation requires gate strategy")
                if m.counterevidence and not any(
                    events[s]["observation"] in {"success", "captcha", "login", "account_locked"}
                    for s in m.counterevidence
                ):
                    raise ValueError("Counterevidence has no verified result")
                for old in m.replaces:
                    prior = db.execute(
                        "SELECT * FROM memories WHERE id=? AND site=? AND operation=?", (old, site, operation)
                    ).fetchone()
                    if not prior or not m.counterevidence:
                        raise ValueError("Unsupported update")
                    db.execute("UPDATE memories SET active=0 WHERE id=?", (old,))
                db.execute(
                    "INSERT OR IGNORE INTO memories VALUES(?,?,?,?,?,?,1)",
                    (
                        digest(row["execution"] + str(row["watermark"]) + m.model_dump_json()),
                        row["execution"],
                        site,
                        operation,
                        m.model_dump_json(),
                        time.time() + m.ttl_days * 86400,
                    ),
                )
            db.execute("UPDATE dreams SET status='complete',output=? WHERE lease=?", (json.dumps(memories), lease))
        return {"status": "complete", "execution_id": row["execution"]}

    def context(self, site, operation):
        self.recover()
        with self.db() as db:
            memories = []
            for row in db.execute(
                "SELECT * FROM memories WHERE site=? AND operation=? AND active=1 AND expires>?",
                (site, operation, time.time()),
            ):
                memory = dict(row)
                refs = json.loads(row["body"])["sources"]
                memory["evidence"] = [
                    dict(seq=e["seq"], **json.loads(e["body"]))
                    for e in db.execute("SELECT * FROM trajectory WHERE execution=?", (row["execution"],))
                    if e["seq"] in refs
                ]
                memories.append(memory)
            resolutions = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='resolutions'").fetchone():
                resolutions = [
                    dict(r)
                    for r in db.execute(
                        "SELECT r.attempt,r.time,r.kind FROM resolutions r JOIN attempts a ON a.id=r.attempt "
                        "WHERE a.site=?",
                        (site,),
                    )
                ]
            return dict(
                site=site,
                operation=operation,
                account="unknown",
                memories=memories,
                human_resolutions=resolutions,
                instruction=(
                    "Apply later actual human resolution before older unresolved advice; "
                    "it does not prove website submission."
                ),
            )

    def sync_store(self, store):
        """Receive the business transaction's durable handoff idempotently."""
        for row in store.db.execute("SELECT * FROM network_lifecycle WHERE closed=1"):
            self.start(row["task"], execution=row["execution"], batch=row["batch"])
            self.finish(row["execution"], row["status"], authoritative=True)
            with store._tx():
                store.db.execute("DELETE FROM network_lifecycle WHERE execution=? AND closed=1", (row["execution"],))

    def status(self):
        self.recover()
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT execution,status,attempts,watermark FROM dreams")]


def batch_work(data_dir, task, work, *, batch=None, continuation=None, run=None, activity=None):
    """Persist before thread launch; a dead owner becomes interrupted on next access."""
    journal = Journal(data_dir)
    execution = journal.start(
        task, batch=batch, continuation=continuation, execution=(activity or {}).get("execution_id")
    )

    def wrapped():
        token = CURRENT.set((journal, execution))
        status = "success"
        handler = TraceHandler(journal, execution, threading.get_ident(), task)
        logging.getLogger().addHandler(handler)
        try:
            return work()
        except BaseException:
            status = "failed"
            raise
        finally:
            if run is not None:
                status = batch_status(run)
            logging.getLogger().removeHandler(handler)
            journal.finish(execution, status)
            CURRENT.reset(token)

    wrapped.execution_id = execution
    wrapped.journal = journal
    return wrapped


def observe(kind, **kwargs):
    active = CURRENT.get()
    if active:
        active[0].event(active[1], kind, **kwargs)


class TraceHandler(logging.Handler):
    """Only same-thread, explicitly correlated vendor diagnostics; never stores log text."""

    def __init__(self, journal, execution, thread, operation):
        super().__init__()
        self.journal, self.execution, self.thread, self.operation = journal, execution, thread, operation

    def emit(self, record):
        if record.thread != self.thread or not record.name.startswith("intent_job_agent.vendor."):
            return
        text = record.getMessage().lower()
        action = "retry" if "retry" in text else "timeout" if "timeout" in text else "unknown"
        if action != "unknown":
            self.journal.event(
                self.execution,
                "stage",
                site="adapter:unattributed",
                operation=self.operation,
                observation="error",
                action=action,
            )


def batch_status(run):
    status = {"done": "success", "failed": "failed", "stopped": "stopped"}.get(run.get("status"), "unknown")
    if status == "success" and any(
        getattr(item.get("outcome"), "status", None) in {"held", "waiting"} for item in run.get("jobs", [])
    ):
        return "waiting"
    return status
