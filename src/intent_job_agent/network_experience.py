"""Local, deterministic operation admission. Never stores page text or personal facts."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from .domain import InvariantError

POLICY = Path(__file__).resolve().parents[2] / "configs/network-experience.yaml"
PENDING = ContextVar("network_pending", default=None)
ACTIVE = ContextVar("network_guard", default=None)
RESULTS = {"success", "transient", "rate_limit", "captcha", "login", "account_locked", "unknown", "interrupted"}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def scope(url):
    """Fixed ATS route mappings; arbitrary paths cannot create new risk scopes."""
    u = urlsplit(url)
    host = (u.hostname or "").lower().rstrip(".")
    if u.scheme not in {"http", "https"} or not host:
        raise ValueError("A valid HTTP site is required")
    parts = [p for p in u.path.split("/") if p]
    routes = {
        "boards-api.greenhouse.io": ("greenhouse", 2),
        "boards.greenhouse.io": ("greenhouse", 0),
        "job-boards.greenhouse.io": ("greenhouse", 0),
        "api.lever.co": ("lever", 2),
        "jobs.lever.co": ("lever", 0),
        "api.ashbyhq.com": ("ashby", 2),
        "jobs.ashbyhq.com": ("ashby", 0),
    }
    if host in routes:
        provider, index = routes[host]
        if len(parts) <= index:
            raise ValueError("Unknown ATS tenant; no network attempt admitted")
        site = provider + ":" + digest(parts[index].casefold())[:24]
    else:
        site = host
    return site, host, digest(host + u.path)


class Paused(InvariantError):
    pass


class Guard:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.policy = yaml.safe_load(POLICY.read_text())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sites (
                    site TEXT PRIMARY KEY, host TEXT, failures INTEGER DEFAULT 0,
                    reason TEXT, until REAL DEFAULT 0, manual INTEGER DEFAULT 0,
                    active TEXT, latest TEXT, updated REAL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, site TEXT, resource TEXT, operation TEXT,
                    started REAL, finished REAL, result TEXT, evidence TEXT);
                CREATE TABLE IF NOT EXISTS denials (site TEXT, time REAL, reason TEXT);
                CREATE TABLE IF NOT EXISTS resolutions (
                    attempt TEXT PRIMARY KEY, time REAL, kind TEXT, evidence_hash TEXT);
            """)
        self.path.chmod(0o600)
        with self.db() as db:
            db.execute("UPDATE sites SET manual=0,until=0 WHERE reason IN ('unknown','transient','interrupted')")
        self.prune()

    @classmethod
    def local(cls, data_dir):
        policy = yaml.safe_load(POLICY.read_text())
        return cls(Path(data_dir) / policy["file"])

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

    def begin(self, url, operation):
        if operation not in {"fetch", "apply", "form_fill"}:
            raise ValueError("Unknown operation")
        site, host, resource = scope(url)
        now = self.clock()
        blocked = None
        attempt = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO sites(site,host,updated) VALUES(?,?,?)", (site, host, now))
            row = dict(db.execute("SELECT * FROM sites WHERE site=?", (site,)).fetchone())
            if row["active"]:
                old = db.execute("SELECT * FROM attempts WHERE id=?", (row["active"],)).fetchone()
                if now - old["started"] >= self.policy["lease_seconds"]:
                    db.execute(
                        "UPDATE attempts SET finished=?,result='interrupted',evidence='lease_expired' WHERE id=?",
                        (now, old["id"]),
                    )
                    db.execute(
                        "UPDATE sites SET active=NULL,manual=0,reason='interrupted',updated=? WHERE site=?", (now, site)
                    )
                    row.update(active=None, manual=0, reason="interrupted", updated=now)
            if row["active"] or row["manual"] or row["until"] > now:
                blocked = self.summary(row)
                db.execute("INSERT INTO denials VALUES(?,?,?)", (site, now, row["reason"] or "in_flight"))
            else:
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?,?,?,NULL,NULL,NULL)", (attempt, site, resource, operation, now)
                )
                db.execute("UPDATE sites SET active=?,latest=?,updated=? WHERE site=?", (attempt, attempt, now, site))
        if blocked:
            raise Paused(json.dumps(blocked, ensure_ascii=False))
        return attempt

    def finish(self, attempt, result, *, retry_after=None):
        if result not in RESULTS:
            raise ValueError("Only structured outcome codes are accepted")
        now = self.clock()
        with self.db() as db:
            a = db.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
            if a is None:
                raise ValueError("Unknown attempt")
            if a["result"]:
                if a["result"] != result:
                    raise ValueError("Conflicting result or expired lease")
                return
            s = db.execute("SELECT * FROM sites WHERE site=?", (a["site"],)).fetchone()
            failures = s["failures"] + (result != "success")
            manual = result in {"captcha", "login", "account_locked"}
            delay = self.policy["backoff_seconds"] if result == "rate_limit" else 0
            if result == "rate_limit" and retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        dt = parsedate_to_datetime(retry_after)
                        delay = max(delay, dt.replace(tzinfo=dt.tzinfo or UTC).timestamp() - now)
                    except (ValueError, TypeError, OverflowError):
                        pass
            db.execute(
                "UPDATE attempts SET finished=?,result=?,evidence=? WHERE id=?",
                (now, result, "structured_adapter_result", attempt),
            )
            db.execute(
                "UPDATE sites SET failures=?,reason=?,until=?,manual=?,active=NULL,updated=? WHERE site=?",
                (failures, result, now + delay, int(manual), now, a["site"]),
            )

    @staticmethod
    def summary(row):
        return {
            **dict(row),
            "account": "unknown",
            "needs_you": bool(row["manual"]),
            "next": "本人核实并登记当前暂停的处理证据"
            if row["manual"]
            else ("等待当前操作结束" if row["active"] else f"冷却至 Unix {row['until']} 后可尝试"),
            "confidence": "仅受控入口观察；不是封号诊断",
        }

    def resolve(self, attempt, kind, evidence):
        if kind not in {"user_resolved", "user_checked_no_execution"} or not evidence.strip():
            raise ValueError("Explicit human resolution evidence required")
        with self.db() as db:
            s = db.execute("SELECT * FROM sites WHERE latest=?", (attempt,)).fetchone()
            if not s or not s["manual"] or s["active"]:
                raise ValueError("Not the current manual pause")
            if s["reason"] in {"captcha", "login", "account_locked"} and kind != "user_resolved":
                raise ValueError("The actual human gate must be resolved")
            if s["reason"] == "rate_limit" and s["until"] > self.clock():
                raise ValueError("Server cooldown has not elapsed")
            db.execute("INSERT INTO resolutions VALUES(?,?,?,?)", (attempt, self.clock(), kind, digest(evidence)))
            db.execute("UPDATE sites SET manual=0,failures=0,until=0 WHERE site=?", (s["site"],))

    def report(self):
        with self.db() as db:
            sites = [self.summary(r) for r in db.execute("SELECT * FROM sites ORDER BY site")]
            counts = [
                dict(r) for r in db.execute("SELECT site,result,count(*) AS count FROM attempts GROUP BY site,result")
            ]
            recovered = db.execute(
                "SELECT count(DISTINCT r.attempt) FROM resolutions r JOIN attempts old ON old.id=r.attempt "
                "JOIN attempts a ON a.site=old.site AND a.started>=r.time AND a.result='success'"
            ).fetchone()[0]
            return {
                "sites": sites,
                "outcomes": counts,
                "denials": [
                    dict(r)
                    for r in db.execute(
                        "SELECT site,reason,count(*) AS count,max(time) AS last FROM denials GROUP BY site,reason"
                    )
                ],
                "recoveries": recovered,
                "source": str(self.path),
                "coverage": "受控逻辑尝试；不含任意浏览器动作",
                "model": "none",
            }

    def prune(self):
        with self.db() as db:
            cutoff = self.clock() - self.policy["retention_days"] * 86400
            db.execute("DELETE FROM denials WHERE time<?", (cutoff,))
            db.execute("DELETE FROM resolutions WHERE time<? AND attempt NOT IN (SELECT latest FROM sites)", (cutoff,))
            db.execute(
                "DELETE FROM attempts WHERE finished<? AND id NOT IN (SELECT latest FROM sites) "
                "AND id NOT IN (SELECT attempt FROM resolutions)",
                (cutoff,),
            )


def request(client, url, **kwargs):
    guard = ACTIVE.get()
    if guard is None:
        return client.get(url, **kwargs)
    if PENDING.get():
        guard.finish(PENDING.get(), "success")
        PENDING.set(None)
    a = guard.begin(url, "fetch")
    try:
        response = client.get(url, **kwargs)
    except httpx.TransportError as error:
        from .network_dreaming import observe

        observe(
            "stage",
            site=scope(url)[0],
            operation="fetch",
            observation="error",
            action="timeout" if isinstance(error, httpx.TimeoutException) else "unknown",
        )
        guard.finish(a, "transient")
        raise
    except BaseException:
        guard.finish(a, "unknown")
        raise
    from .network_dreaming import observe

    observe(
        "stage",
        site=scope(url)[0],
        operation="fetch",
        observation="returned" if response.status_code < 400 else "error",
        http_status=response.status_code,
    )
    code = response.status_code
    result = (
        "success"
        if 200 <= code < 300
        else ("rate_limit" if code == 429 else ("transient" if code >= 500 else "unknown"))
    )
    if result == "success":
        PENDING.set(a)
    else:
        guard.finish(a, result, retry_after=response.headers.get("Retry-After"))
    return response


def guarded_fetch(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        guard = Guard.local(self.data_dir)
        token = ACTIVE.set(guard)
        pending = PENDING.set(None)
        try:
            return fn(self, *args, **kwargs)
        finally:
            PENDING.reset(pending)
            ACTIVE.reset(token)

    return wrapped


def guarded_application(fn):
    @wraps(fn)
    def wrapped(spec, data_dir, settings, *args, **kwargs):
        from .apply_bridge import ApplyOutcome

        settings.require_external("network admission", data_dir)
        guard = Guard.local(data_dir)
        operation = "form_fill" if kwargs.get("extra_servers") else "apply"
        try:
            a = guard.begin(spec.url, operation)
        except Paused as exc:
            return ApplyOutcome("pending", "network_paused", [str(exc)])
        try:
            outcome = fn(spec, data_dir, settings, *args, **kwargs)
        except BaseException:
            guard.finish(a, "unknown")
            raise
        reason = {
            "captcha": "captcha",
            "login_issue": "login",
            "sso_required": "login",
            "email_verification": "login",
            "account_locked": "account_locked",
        }.get(outcome.reason)
        result = reason or ("success" if outcome.status in {"applied", "waiting"} else "unknown")
        guard.finish(a, result)
        from .network_dreaming import observe

        observe(
            "stage",
            site=scope(spec.url)[0],
            operation=operation,
            observation=reason or "returned",
            job_id=getattr(spec, "job_id", None),
        )
        if result != "success":
            outcome.notes.append(json.dumps(guard.report(), ensure_ascii=False))
        return outcome

    return wrapped


def parsed_response(fn):
    """Finish the last HTTP attempt only after its containing parser succeeds."""

    @wraps(fn)
    def wrapped(*args, **kwargs):
        guard = ACTIVE.get()
        if guard is None:
            return fn(*args, **kwargs)
        try:
            value = fn(*args, **kwargs)
        except BaseException:
            if PENDING.get():
                guard.finish(PENDING.get(), "unknown")
                PENDING.set(None)
                raise
            raise
        if PENDING.get():
            guard.finish(PENDING.get(), "success")
            PENDING.set(None)
        return value

    return wrapped


def demo():
    """Synthetic evidence generation only; model work stays pending without an online host."""
    import tempfile

    from .network_dreaming import Journal

    with tempfile.TemporaryDirectory(prefix="synthetic-network-") as folder:
        journal = Journal(folder)
        execution = journal.start("apply")
        journal.event(execution, "stage", site="synthetic.example", operation="apply", observation="captcha")
        journal.finish(execution, "waiting")
        print(
            json.dumps(
                {"synthetic": True, "model": "pending_online_host", "input": journal.prepare()},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Offline synthetic network guard demo")
    parser.add_argument("--demo", action="store_true", required=True)
    parser.parse_args()
    demo()
