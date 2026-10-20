"""The recording folder (specs/form-fill.md 4.1, 4.3, 5.6, 6.2, 6.5; slice-2a plan section 1).

    recordings/<recording id>/
      meta.json                 address, company, title, platform, pages, what it can retest, the last conclusion
      form/                     the blank form: structure (no values), simplified HTML, screenshot, MHTML, network
      fills/<run>/              one fill: state.json, checkpoints.json, requests.json, p<n>.prefilled.json,
                                p<n>.values.json, p<n>.auto.json, p<n>.filled.html/.png, final.json/.html/.png,
                                network.partial.jsonl while the run goes on

Everything is masked before it is written: by value (redact.Redactor) and by column (columns.mask_snapshot).
The checkpoint service writes while the filling agent runs; the runner adds the final read, closes the network
record and reads every page back with read_run.
"""

import base64
import hashlib
import json
import quopri
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from .columns import mask_snapshot  # noqa: F401 - the checkpoint service masks each read with it
from .matching import norm
from .models import ReadField
from .readback import simplify_html
from .recorder import Observation, Recorder, prefilled_sources
from .redact import Redactor

STATE_FILE, META_FILE = "state.json", "meta.json"
PARTIAL, NETWORK = "network.partial.jsonl", "network.json"
NETWORK_MAX_BYTES = 30 * 2**20
OWN_KINDS = ("email", "phone", "address", "postal_code", "url", "password")
TRACKING = re.compile(
    r"^(utm_.*|gh_src|gh_jid_src|lever-source|lever-origin|source|src|ref|refid|trackingid|trk.*)$", re.I
)
# Conclusions after which a job is not visited again unless the list says revisit: true (spec 4.1). A form that
# could not be filled because of a gate, a login, a missing answer, a broken page, a stuck agent or a lost tab is.
DONE = ("ok", "check_then_submit", "do_not_submit", "maybe_submitted", "not_eligible")
DONE_REASONS = ("expired", "wrong_job", "email_only", "already_applied")
DROPPED_HEADERS = re.compile(r"cookie|authorization|token|auth|session|csrf|xsrf", re.I)
# Documents (a resume preview after an upload) are personal and their text may be compressed past any scan.
DOCUMENT_TYPES = re.compile(r"application/(pdf|msword|vnd\.openxmlformats|vnd\.oasis|rtf)|text/rtf", re.I)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
TEXT_TYPES = re.compile(
    r"^(text/|application/(json|javascript|x-javascript|ecmascript|xml|xhtml|ld\+json))|\+json|\+xml|svg", re.I
)


# --- recording ids (spec 6.2) -----------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")


def _clean_query(query: str) -> str:
    return urlencode(sorted((k, v) for k, v in parse_qsl(query) if not TRACKING.match(k)))


def platform(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for name, marks in (
        ("greenhouse", ("greenhouse.io",)),
        ("lever", ("lever.co",)),
        ("ashby", ("ashbyhq.com",)),
        ("workday", ("myworkdayjobs.com", "myworkday.com", "myworkdaysite.com")),
        ("linkedin", ("linkedin.com",)),
    ):
        if any(host == m or host.endswith("." + m) for m in marks):
            return name
    return "other"


def recording_id(url: str) -> str:
    """From the address: platform-company-job for the known platforms, other-<host>-<8 hex> for the rest. The host
    is lower-cased and tracking parameters do not count."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = [p for p in parts.path.split("/") if p]
    query = dict(parse_qsl(parts.query))
    kind = platform(url)
    found = _known(kind, host, path, query)
    if found and SAFE_ID.match(found) and ".." not in found:
        return found
    canonical = f"{host}{parts.path.rstrip('/')}?{_clean_query(parts.query)}"
    host_part = re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.") or "site"
    return f"other-{host_part}-{hashlib.sha256(canonical.encode()).hexdigest()[:8]}"


def _known(kind: str, host: str, path: list[str], query: dict) -> str | None:
    """platform-company-job for a known platform's address, None when the address does not show both."""
    if kind == "greenhouse":
        if "jobs" in path and path.index("jobs") >= 1 and len(path) > path.index("jobs") + 1:
            i = path.index("jobs")
            return f"greenhouse-{_slug(path[i - 1])}-{_slug(path[i + 1])}"
        job = query.get("token") or query.get("gh_jid") or ""
        if query.get("for") and job.isdigit():  # the embedded application form
            return f"greenhouse-{_slug(query['for'])}-{job}"
    if kind in ("lever", "ashby") and len(path) >= 2:
        return f"{kind}-{_slug(path[0])}-{_slug(path[1])}"
    if kind == "workday":
        job = next((m.group(1) for p in path if (m := re.search(r"_((?:[A-Za-z]+-?)?\d[\w-]*)$", p))), None)
        job = job or next((m.group(1) for p in path if (m := re.search(r"\$(\d+)\.htmld$", p))), None)
        if host.endswith("myworkdayjobs.com"):
            tenant = host.split(".")[0]
        elif path[:1] == ["recruiting"] and len(path) > 1:  # myworkdaysite.com/recruiting/<tenant>/...
            tenant = path[1]
        else:
            tenant = path[0] if path else ""
        if job and tenant:
            return f"workday-{_slug(tenant)}-{_slug(job)}"
    if kind == "linkedin":
        job = query.get("currentJobId") or next((p for p in path if p.isdigit()), None)
        if job and job.isdigit():
            return f"linkedin-{job}"
    return None


# --- another real visit? (spec 4.1, 6.5) ------------------------------------------------------------------------


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def note_conclusion(rec_dir: Path, run_id: str, conclusion: str, reason: str | None) -> None:
    """The last run's conclusion in the recording's meta.json (spec 4.1). The report of an older run of the same
    recording, made again later, does not change it."""
    meta = _read_json(Path(rec_dir) / META_FILE, {}) or {}
    runs = meta.get("runs") or []
    if run_id in runs and runs[-1] != run_id:
        return
    meta["last"] = {"run": run_id, "conclusion": conclusion, "reason": reason}
    _write_json(Path(rec_dir) / META_FILE, meta)


def needs_visit(rec_dir: Path, revisit: bool) -> tuple[bool, dict | None]:
    """(whether the job needs a real visit, the recording's last run). A job is not visited again when its last
    conclusion says the form was filled or will never be (spec 4.1), unless the list says revisit: true."""
    meta = _read_json(Path(rec_dir) / META_FILE)
    last = meta.get("last") if isinstance(meta, dict) else None
    if not isinstance(last, dict):
        return True, None
    done = last.get("conclusion") in DONE or (
        last.get("conclusion") == "not_filled" and last.get("reason") in DONE_REASONS
    )
    return revisit or not done, last


# --- network records (spec 6.2) ---------------------------------------------------------------------------------


def request_entry(method: str, url: str) -> str:
    parts = urlsplit(url)
    return f"{method.upper()} {parts.scheme}://{parts.netloc}{parts.path}"


def network_entry(event: dict, redactor: Redactor) -> tuple[dict | None, bool]:
    """A GET response as network.json keeps it, or None, and whether it held the applicant's own contact details (a
    signed-in page). Cookies and auth-like headers are dropped. The address, every header kept and the body (read
    as text whatever its type says) are checked: any contact detail drops the response whole. Documents (PDF, Word)
    are never kept. Other methods keep nothing here (see request_entry)."""
    if str(event.get("method", "")).upper() != "GET":
        return None, False
    url, body = str(event.get("url", "")), event.get("body") or b""
    headers = {
        str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if not DROPPED_HEADERS.search(str(k))
    }
    kind = headers.get("content-type", "")
    as_text = body.decode("utf-8", errors="replace")
    seen = "\n".join([url, *(f"{k}: {v}" for k, v in headers.items()), as_text])
    hits = redactor.hits(seen)
    if sum(hits.values()):
        return None, any(hits[k] for k in OWN_KINDS)
    if DOCUMENT_TYPES.search(kind):
        return None, False
    entry = {"url": url, "status": int(event.get("status") or 0), "headers": headers}
    if TEXT_TYPES.search(kind):
        entry["body_text"] = as_text
    else:
        entry["body_base64"] = base64.b64encode(body).decode("ascii")
    return entry, False


# --- MHTML (spec 5.6) -------------------------------------------------------------------------------------------

_BOUNDARY = re.compile(r'boundary="?([^";\r\n]+)"?', re.I)


def _split_headers(chunk: str) -> tuple[str, str, str]:
    """(headers, the blank line, body) of one MIME part."""
    match = re.search(r"\r?\n\r?\n", chunk)
    if not match:
        return chunk, "", ""
    return chunk[: match.start()], match.group(), chunk[match.end() :]


def _header(headers: str, name: str) -> str:
    match = re.search(rf"^{name}:\s*(.+)$", headers, re.I | re.M)
    return match.group(1).strip() if match else ""


def _parts(text: str):
    head, gap, body = _split_headers(text)
    match = _BOUNDARY.search(head)
    if not match or "multipart/" not in head.lower():
        return None
    pieces = body.split("--" + match.group(1))
    if len(pieces) < 3:
        return None
    return head, gap, match.group(1), pieces


def _decode(headers: str, body: str) -> tuple[str | None, str]:
    """A text part's decoded text and its transfer encoding; None for anything else."""
    kind, encoding = _header(headers, "Content-Type"), _header(headers, "Content-Transfer-Encoding").lower()
    if not TEXT_TYPES.search(kind):
        return None, encoding
    charset = (re.search(r'charset="?([\w-]+)', kind, re.I) or [None, "utf-8"])[1]
    raw = body.strip("\r\n").encode("latin-1", errors="replace") if encoding not in ("8bit", "binary") else None
    if encoding == "quoted-printable":
        data = quopri.decodestring(raw)
    elif encoding == "base64":
        data = base64.b64decode(re.sub(rb"\s+", b"", raw))
    else:
        data = body.encode("utf-8", errors="replace") if raw is None else raw
    try:
        return data.decode(charset, errors="replace"), encoding
    except LookupError:
        return data.decode("utf-8", errors="replace"), encoding


def _encode(text: str, encoding: str) -> str:
    data = text.encode("utf-8")
    if encoding == "quoted-printable":
        return quopri.encodestring(data).decode("ascii").replace("\r\n", "\n").replace("\n", "\r\n")
    if encoding == "base64":
        flat = base64.b64encode(data).decode("ascii")
        return "\r\n".join(flat[i : i + 76] for i in range(0, len(flat), 76))
    return text


def mask_mhtml(text: str, redactor: Redactor) -> str | None:
    """The MHTML with each text part decoded, masked and encoded again, and every header masked; None when it
    cannot be parsed or a scan of the result still finds a contact detail (then it is not kept)."""
    split = _parts(text)
    if split is None:
        return None
    head, gap, boundary, pieces = split
    out = [redactor.mask(head) + gap + pieces[0]]
    for chunk in pieces[1:]:
        if chunk.startswith("--"):  # the closing delimiter and what follows it
            out.append(chunk)
            continue
        headers, blank, body = _split_headers(chunk)
        decoded, encoding = _decode(headers, body)
        if decoded is not None:
            lead = "\r\n" if body.endswith("\r\n") else "\n" if body.endswith("\n") else ""
            body = _encode(redactor.mask(decoded), encoding) + lead
        out.append(redactor.mask(headers) + blank + body)
    masked = ("--" + boundary).join(out)
    return masked if mhtml_hits(masked, redactor) == 0 else None


def mhtml_hits(text: str, redactor: Redactor) -> int:
    """Contact details left in an MHTML file, its text parts decoded (1 when it cannot be parsed)."""
    split = _parts(text)
    if split is None:
        return 1
    head, _, _, pieces = split
    total = sum(redactor.hits(head).values())
    for chunk in pieces[1:]:
        if chunk.startswith("--"):
            continue
        headers, _, body = _split_headers(chunk)
        decoded, _ = _decode(headers, body)
        total += sum(redactor.hits(headers + "\n" + (decoded or "")).values())
    return total


# --- the store --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capture:
    """What a checkpoint saves besides the values: the page's HTML (simplified and masked on write), a screenshot
    with contact values blacked out (None and why when that could not be done), and the MHTML for a blank page
    (None and why when it was not taken)."""

    html: str | None = None
    screenshot: bytes | None = None
    why: str = ""
    mhtml: str | None = None
    mhtml_why: str = ""


def _structure_key(fields) -> list:
    return [
        [f["origin"], norm(f["section"]), norm(f["question"]), f["control"], f["required"], f["options"]]
        for f in fields
    ]


class RecordingStore:
    """One run's view of one recording: writes form/ (or form-v2/ when the blank form changed since an earlier
    visit) and fills/<run>/. The checkpoint service and the runner are two processes: what they share (the page
    numbers, which form folder this run uses, the network flags) is in state.json, read again before it is used.
    The first page's network record grows in fills/<run>/ and only moves to the form folder when the run ends."""

    def __init__(self, root: Path, rec_id: str, run_id: str, redactor: Redactor, network_max_bytes=NETWORK_MAX_BYTES):
        if not SAFE_ID.match(rec_id) or ".." in rec_id or not SAFE_ID.match(run_id.lower()):
            raise ValueError(f"not a recording or run id: {rec_id!r}, {run_id!r}")
        self.rec_id, self.run_id, self.redactor = rec_id, run_id, redactor
        self.rec_dir = Path(root) / rec_id
        self.fill_dir = self.rec_dir / "fills" / run_id
        self.network_max_bytes = network_max_bytes
        self._state: dict = {}
        self._refresh()

    def _refresh(self) -> None:
        self._state = _read_json(self.fill_dir / STATE_FILE, {}) or {}
        self.form_dir = self.rec_dir / self._state.get("form_dir", "form")
        partial = self.fill_dir / PARTIAL
        self._net_bytes = partial.stat().st_size if partial.is_file() else 0

    # meta.json

    def write_meta(self, url: str, company: str, title: str) -> None:
        meta = _read_json(self.rec_dir / META_FILE, {}) or {}
        runs = [r for r in meta.get("runs", []) if r != self.run_id] + [self.run_id]
        meta.update(
            recording_id=self.rec_id,
            url=self.redactor.mask(url),
            company=company,
            title=title,
            platform=platform(url),
            recorded=meta.get("recorded") or date.today().isoformat(),
            runs=runs,
        )
        meta.setdefault("pages", 0)
        meta.setdefault("replay_capability", "check_only")  # slice 2b's self-check decides "fill"
        meta.setdefault("self_check", None)
        _write_json(self.rec_dir / META_FILE, self.redactor.mask_json(meta))

    def begin_run(self) -> None:
        """Before the filling agent starts: this run's state.json, empty."""
        (self.fill_dir / PARTIAL).unlink(missing_ok=True)
        self._net_bytes = 0
        self._write_state()

    # state.json

    def save_state(self, recorder: Recorder) -> None:
        """The recorder's pages and numbers. Its texts are already masked (columns.mask_snapshot); the dump is
        masked again so nothing in state.json escapes the redactor."""
        self._state["recorder"] = self.redactor.mask_json(recorder.to_json())
        self._write_state()

    def load_recorder(self, stuck_after: int = 3) -> Recorder:
        self._refresh()
        return Recorder.from_json(self._state.get("recorder") or {}, stuck_after)

    def _write_state(self) -> None:
        self._state["form_dir"] = self.form_dir.name
        _write_json(self.fill_dir / STATE_FILE, self._state)

    def _flag(self, name: str) -> None:
        network = self._state.setdefault("network", {})
        if not network.get(name):
            network[name] = True
            self._write_state()

    def note_password(self) -> None:
        self._flag("password")

    def note_signed_in(self) -> None:
        self._flag("signed_in")

    def note_received(self, text: str, url: str) -> None:
        """A read showed "application received" (spec 5.6): kept for the report's first line."""
        if not self._state.get("received"):
            self._state["received"] = {"text": self.redactor.mask(text)[:200], "url": self.redactor.mask(url)}
            self._write_state()

    def received(self) -> dict | None:
        self._refresh()
        found = self._state.get("received")
        return found if isinstance(found, dict) else None

    # one read

    def _dump(self, obs: Observation, extra: dict | None = None) -> dict:
        data = {
            "page": obs.page,
            "title": obs.title,
            "read_at": obs.read_at,
            "url": obs.url,
            "tab": obs.tab,
            "fields": [f.model_dump(mode="json") for f in obs.fields],
            **(extra or {}),
        }
        return self.redactor.mask_json(data)

    def save(self, obs: Observation | None, stage: str, capture: Capture | None) -> None:
        if obs is None:
            return
        n = obs.page
        if stage == "blank":
            self._save_blank(obs, capture)
        elif stage == "filled":
            _write_json(self.fill_dir / f"p{n}.values.json", self._dump(obs))
            self._save_capture(capture, self.fill_dir, f"p{n}.filled")
        elif stage == "auto":
            if obs.keep:
                _write_json(self.fill_dir / f"p{n}.auto.json", self._dump(obs))
        elif stage == "final":
            _write_json(self.fill_dir / "final.json", self._dump(obs))
            self._save_capture(capture, self.fill_dir, "final")
        else:
            raise ValueError(f"unknown stage {stage!r}")

    def _save_blank(self, obs: Observation, capture: Capture | None) -> None:
        """The page's values at blank (this run's prefill), and its blank form. A blank form an earlier visit saved
        with the same structure stands: a later visit only adds its own fills/ (spec 6.2)."""
        n = obs.page
        prefilled = self.fill_dir / f"p{n}.prefilled.json"
        if prefilled.is_file():  # the agent came back to a page it has seen: its first blank read stands
            return
        _write_json(prefilled, self._dump(obs))
        fields = [
            {
                "id": f.id,
                "origin": f.origin,
                "section": f.section,
                "occurrence": f.occurrence,
                "question": f.question,
                "control": f.control,
                "required": f.required,
                "options": list(f.options),
            }
            for f in obs.fields
        ]
        structure = self.redactor.mask_json({"page": n, "title": obs.title, "url": obs.url, "fields": fields})
        self._refresh_form_dir()
        self._pick_form_dir(n, structure["fields"])
        if (self.form_dir / f"p{n}.structure.json").is_file():
            return
        _write_json(self.form_dir / f"p{n}.structure.json", structure)
        self._save_capture(capture, self.form_dir, f"p{n}")
        if capture is None:
            return
        masked = mask_mhtml(capture.mhtml, self.redactor) if capture.mhtml is not None else None
        if masked is not None:
            (self.form_dir / f"p{n}.mhtml").write_text(masked, encoding="utf-8", newline="")
        else:
            why = capture.mhtml_why or ("打码做不到" if capture.mhtml is not None else "没有截到")
            (self.form_dir / f"p{n}.mhtml.not-saved.txt").write_text(f"MHTML 未存：{why}\n", encoding="utf-8")

    def _refresh_form_dir(self) -> None:
        state = _read_json(self.fill_dir / STATE_FILE, {}) or {}
        if state.get("form_dir"):
            self.form_dir = self.rec_dir / state["form_dir"]

    def _pick_form_dir(self, n: int, fields: list) -> None:
        """form/ unless an earlier visit saved another structure for this page there: then the first form-v<k>/
        that is free for it or holds the same structure (spec 6.2)."""
        if self.form_dir.name != "form":
            return
        k, folder = 1, self.rec_dir / "form"
        while True:
            saved = _read_json(folder / f"p{n}.structure.json")
            if saved is None or _structure_key(saved.get("fields", [])) == _structure_key(fields):
                break
            k += 1
            folder = self.rec_dir / f"form-v{k}"
        if folder != self.form_dir:
            self.form_dir = folder
            self._write_state()

    def _save_capture(self, capture: Capture | None, folder: Path, stem: str) -> None:
        if capture is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        if capture.html is not None:
            (folder / f"{stem}.html").write_text(self.redactor.mask(simplify_html(capture.html)), encoding="utf-8")
        if capture.screenshot is not None:
            (folder / f"{stem}.png").write_bytes(capture.screenshot)
        else:
            why = capture.why or "没有截图"
            (folder / f"{stem}.png.not-saved.txt").write_text(self.redactor.mask(f"截图未存：{why}\n"), "utf-8")

    # logs

    def log_checkpoint(self, entry: dict) -> None:
        path = self.fill_dir / "checkpoints.json"
        logged = _read_json(path, []) or []
        logged.append(self.redactor.mask_json(entry))
        _write_json(path, logged)

    def add_request(self, method: str, url: str) -> None:
        if method.upper() == "GET":
            return
        path = self.fill_dir / "requests.json"
        seen = _read_json(path, []) or []
        line = self.redactor.mask(request_entry(method, url))
        if line not in seen:
            _write_json(path, [*seen, line])

    def add_network(self, entry: dict) -> bool:
        """Append one GET response to the first page's record; False once the size limit is reached."""
        if self._state.get("network", {}).get("full"):
            return False
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        size = len(line.encode("utf-8"))
        if self._net_bytes + size > self.network_max_bytes:
            self._flag("full")
            return False
        self.fill_dir.mkdir(parents=True, exist_ok=True)
        with (self.fill_dir / PARTIAL).open("a", encoding="utf-8") as out:
            out.write(line)
        self._net_bytes += size
        return True


def finish_network(store: RecordingStore) -> bool:
    """Keep the first page's network record as network.json in this run's form folder only for a signed-out
    single-page form: one page, no password field, no response with the applicant's contact details (spec 6.2). A
    network.json an earlier visit left there stands. Returns whether the form folder now has one from such a run."""
    recorder = store.load_recorder()
    network = store._state.get("network", {})
    partial = store.fill_dir / PARTIAL
    target = store.form_dir / NETWORK
    keep = (
        partial.is_file()
        and partial.stat().st_size > 0
        and recorder.pages == 1
        and not network.get("password")
        and not network.get("signed_in")
    )
    if keep and not target.is_file():
        lines = partial.read_text(encoding="utf-8").splitlines()
        _write_json(target, [json.loads(line) for line in lines if line.strip()])
    partial.unlink(missing_ok=True)
    return keep and target.is_file()


def finish_meta(store: RecordingStore) -> None:
    """The recording's page count after a run."""
    meta = _read_json(store.rec_dir / META_FILE, {}) or {}
    meta["pages"] = max(int(meta.get("pages") or 0), store.load_recorder().pages)
    _write_json(store.rec_dir / META_FILE, meta)


# --- reading a run back (spec 4.3, 5.3) -------------------------------------------------------------------------


@dataclass(frozen=True)
class PageRead:
    """One page of a run as the report reads it: where its values come from, when they were read, the fields
    (blank structure only when unrecorded) and each non-empty field's source."""

    page: int
    title: str
    read_from: str
    read_at: str
    fields: tuple[ReadField, ...]
    sources: dict[str, str]


def _fields(data) -> tuple[ReadField, ...] | None:
    if not isinstance(data, dict):
        return None
    return tuple(ReadField.model_validate(row) for row in data.get("fields", []))


def read_run(rec_dir: Path, run_id: str) -> list[PageRead]:
    """Every page of a run: its last checkpoint("filled") read, else its background read, else nothing (the blank
    structure, all values empty); the page the agent ended on takes the read made after it quit."""
    rec_dir = Path(rec_dir)
    fill = rec_dir / "fills" / run_id
    state = _read_json(fill / STATE_FILE)
    if not isinstance(state, dict):
        return []
    recorder = Recorder.from_json(state.get("recorder") or {})
    form = rec_dir / state.get("form_dir", "form")
    final = _read_json(fill / "final.json")
    pages = []
    for n in range(1, recorder.pages + 1):
        blank = _fields(_read_json(fill / f"p{n}.prefilled.json"))
        if isinstance(final, dict) and final.get("page") == n:
            data, read_from = final, "final"
        elif (data := _read_json(fill / f"p{n}.values.json")) is not None:
            read_from = "filled"
        elif (data := _read_json(fill / f"p{n}.auto.json")) is not None:
            read_from = "auto"
        else:
            data = _read_json(form / f"p{n}.structure.json") or _read_json(rec_dir / "form" / f"p{n}.structure.json")
            rows = (data or {}).get("fields", [])
            fields = tuple(ReadField.model_validate({**row, "value": ""}) for row in rows)
            pages.append(PageRead(n, recorder.title(n), "unrecorded", "", fields, {}))
            continue
        fields = _fields(data) or ()
        pages.append(
            PageRead(n, recorder.title(n), read_from, data.get("read_at", ""), fields, prefilled_sources(blank, fields))
        )
    return pages
