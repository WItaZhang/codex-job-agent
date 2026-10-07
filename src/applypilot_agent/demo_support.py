"""Synthetic demo configuration and a loopback-only ATS with an independent ledger."""

import hashlib
import json
import threading
from email import policy
from email.parser import BytesParser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field, model_validator

from .config import BrowserSettings
from .models import Profile, Record
from .profile_evidence import confirmed_evidence, profile_evidence
from .serialization import utc_now


class DemoScenario(Record):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    company: str
    description: str
    authorization: Literal["review", "auto"]
    receipt: bool


class DemoConfig(Record):
    experiment_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    logs_dir: Path
    state_dir: Path
    bind_host: Literal["127.0.0.1"]
    bind_port: int = Field(ge=0, le=65535)
    maximum_request_bytes: int = Field(gt=0)
    browser: BrowserSettings
    render_pdf: bool
    daily_submission_limit: int = Field(ge=3)
    profile: Profile
    resume_fact_ids: list[str] = Field(min_length=1)
    scenarios: list[DemoScenario] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_scenarios(self):
        if len({scenario.id for scenario in self.scenarios}) != len(self.scenarios):
            raise ValueError("Demo scenario IDs must be unique")
        modes = {(scenario.authorization, scenario.receipt) for scenario in self.scenarios}
        if modes != {("review", True), ("auto", True), ("auto", False)}:
            raise ValueError("Demo requires review+receipt, auto+receipt and auto+missing-receipt scenarios")
        facts = {fact.id: fact for fact in profile_evidence(self.profile) if fact.confirmed}
        if set(self.resume_fact_ids) - facts.keys():
            raise ValueError("Demo resume facts must all be confirmed")
        keys = set(confirmed_evidence(self.profile))
        if not {f"{self.profile.personal.id}.full_name", f"{self.profile.personal.id}.email"}.issubset(keys):
            raise ValueError("Synthetic profile requires confirmed name and email facts")
        return self


def load_demo_config(path: Path) -> DemoConfig:
    config = DemoConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
    config.logs_dir = (path.parent / config.logs_dir).resolve()
    config.state_dir = (path.parent / config.state_dir).resolve()
    return config


def _multipart_fields(content_type: str, body: bytes) -> tuple[dict, dict]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart():
        raise ValueError("Multipart form required")
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if not name or payload is None:
            raise ValueError("Malformed form part")
        filename = part.get_filename()
        if filename is None:
            fields[name] = payload.decode("utf-8")
        else:
            files[name] = {"filename": filename, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    return fields, files


class LocalATS:
    """A test employer writes a ledger independently of browser/runtime state."""

    def __init__(self, config: DemoConfig, ledger_path: Path):
        self.config = config
        self.ledger_path = ledger_path
        self.scenarios = {scenario.id: scenario for scenario in config.scenarios}
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _respond(self, status: int, body: bytes, content_type: str):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if not path.startswith("/apply/") or path.removeprefix("/apply/") not in owner.scenarios:
                    self._respond(404, b"Unknown synthetic job", "text/plain")
                    return
                scenario = owner.scenarios[path.removeprefix("/apply/")]
                self._respond(200, owner._form(scenario).encode(), "text/html; charset=utf-8")

            def do_POST(self):
                path = urlsplit(self.path).path
                job_id = path.removeprefix("/applications/")
                if not path.startswith("/applications/") or job_id not in owner.scenarios:
                    self._respond(404, b"Unknown synthetic job", "text/plain")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= owner.config.maximum_request_bytes:
                        raise ValueError("Invalid request size")
                    fields, files = _multipart_fields(self.headers.get("Content-Type", ""), self.rfile.read(length))
                    if (
                        not fields.get("full_name")
                        or not fields.get("email")
                        or not files.get("resume", {}).get("bytes")
                    ):
                        raise ValueError("Required application fields are missing")
                    entry = owner._record(job_id, fields, files)
                except (ValueError, UnicodeError) as exc:
                    self._respond(422, str(exc).encode(), "text/plain")
                    return
                self._respond(200, json.dumps({"receipt_id": entry["receipt_id"]}).encode(), "application/json")

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text("", encoding="utf-8")
        self._server = ThreadingHTTPServer((self.config.bind_host, self.config.bind_port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def origin(self) -> str:
        if self._server is None:
            raise RuntimeError("Local ATS is not running")
        return f"http://{self.config.bind_host}:{self._server.server_port}"

    def entries(self, job_id: str | None = None) -> list[dict]:
        with self._lock:
            return [entry.copy() for entry in self._entries if job_id is None or entry["job_id"] == job_id]

    def _record(self, job_id: str, fields: dict, files: dict) -> dict:
        with self._lock:
            entry = {
                "job_id": job_id,
                "receipt_id": f"DEMO-{job_id}-{len(self._entries) + 1}",
                "received_at": utc_now(),
                "fields": fields,
                "files": files,
            }
            with self.ledger_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry) + "\n")
            self._entries.append(entry)
            return entry.copy()

    @staticmethod
    def _form(scenario: DemoScenario) -> str:
        show_receipt = "true" if scenario.receipt else "false"
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
        <title>Synthetic ATS — {escape(scenario.title)}</title>
        <style>body{{font:16px system-ui;max-width:680px;margin:48px auto;color:#17304a}}
        label{{display:block;margin:18px 0}}input{{display:block;padding:8px;width:90%}}
        button{{padding:12px;background:#1758a8;color:white;border:0;border-radius:6px}}</style></head>
        <body><p>LOCAL SYNTHETIC DEMO • No real employer</p><h1>{escape(scenario.title)}</h1>
        <p>{escape(scenario.company)}</p><form id="application">
        <label>Full name<input id="name" name="full_name" required></label>
        <label>Email<input id="email" name="email" type="email" required></label>
        <label>Resume<input id="resume" name="resume" type="file" required></label>
        <button id="submit" type="submit">Submit synthetic application</button></form>
        <p id="receipt" hidden></p><p id="error" hidden></p>
        <script>document.querySelector('#application').addEventListener('submit', async event => {{
          event.preventDefault();
          const response = await fetch('/applications/{scenario.id}', {{method:'POST',body:new FormData(event.target)}});
          if (!response.ok) {{const error=document.querySelector('#error');error.hidden=false;error.textContent='Rejected';return;}}
          const result = await response.json();
          if ({show_receipt}) {{const receipt=document.querySelector('#receipt');receipt.hidden=false;
            receipt.textContent='Application received: '+result.receipt_id;}}
        }});</script></body></html>"""
