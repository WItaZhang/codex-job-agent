"""One tailoring run on disk (specs/resume-tailor.md sections 3-6). Layout under <data>/<resume_tailor.dir>/:

    probes.json                         isolation probe per evaluator CLI version
    <job id or slug>/jd.md, selection.md, requirements.json, user_feedback.md
    <job id or slug>/<run>/meta.json, costs.jsonl, rounds.jsonl
    <job id or slug>/<run>/vNN/resume.tex, resume.pdf, page1.png, render.json, evaluation.json, eval_usage.json,
                               verdict.json, response.md, changes.diff

A version whose evaluation is valid is never rewritten; v00 is the base resume as it is."""

import difflib
import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import applicant as applicant_files
from ..applicant import TAILORED_FILE
from ..domain import InvariantError
from ..form_fill.redact import FIELD_PLACEHOLDER, PLACEHOLDER, Redactor
from . import checks
from .config import TailorFile
from .evaluator import RUBRIC, ClaudeHost, ProbeUnusable, fresh_folder, message, parse_object
from .render import Programs

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION = re.compile(r"^v(\d{2,})$")
TEXT_SUFFIXES = {".md", ".txt", ".tex", ".yaml", ".yml", ".json", ".csv", ".rst"}
FORBIDDEN_NAMES = {"profile.json", ".env"}
MAX_EVIDENCE_FILES = 500
RUN_FILES = {"jd": "jd.md", "selection": "selection.md", "requirements": "requirements.json",
             "user_feedback": "user_feedback.md"}  # fmt: skip
VERSION_FILES = {"resume.tex", "response.md", "evaluation.json", "changes.diff"}
OWN_KINDS = ("email", "phone", "address", "postal_code", "url")  # the applicant's own contact details
TEX_SAFE = {v: v.replace("_", " ") for v in (*PLACEHOLDER.values(), *FIELD_PLACEHOLDER.values()) if "_" in v}


# LaTeX writes an underscore as \_ and often joins phone parts with ~ or \, : the plain-text patterns of the shared
# redactor miss those, so the applicant's own values get LaTeX-aware patterns first. Anything still left is caught
# by the check on the rendered PDF text and stops the evaluation.
TEX_SEP = r"(?:[\s().~-]|\\[, ]|\\-)*"


def _tex_patterns(profile: dict) -> list[tuple[re.Pattern[str], str]]:
    personal = profile.get("personal") or {}
    out = []
    for field in ("email", "linkedin_url", "github_url", "portfolio_url", "website_url"):
        value = personal.get(field)
        if isinstance(value, str) and "_" in value.strip():
            out.append((re.compile(re.escape(value.strip().replace("_", "\\_")), re.I), FIELD_PLACEHOLDER[field]))
    phone = personal.get("phone")
    digits = re.sub(r"\D", "", phone) if isinstance(phone, str) else ""
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 7:
        prefix = r"(?:\+?\s*1" + TEX_SEP + r")?" if len(digits) == 10 else r"\+?\s*"
        out.append((re.compile(rf"(?<![\w+]){prefix}\(?{TEX_SEP.join(digits)}(?!\d)"), FIELD_PLACEHOLDER["phone"]))
    return out


def mask_tex(tex: str, profile: dict) -> tuple[str, Redactor]:
    """The evaluator's copy: comments dropped, the applicant's contact details replaced by placeholders."""
    redactor = Redactor.from_profile(profile)
    masked = checks.strip_comments(tex)
    for pattern, mark in _tex_patterns(profile):
        masked = pattern.sub(mark, masked)
    masked = redactor.mask(masked)
    for mark, safe in TEX_SAFE.items():
        masked = masked.replace(mark, safe)
    return masked, redactor


def tailored_id(job_id: str) -> str:
    """The adopted resume's id: readable, unique per job, and valid as a resume id."""
    slug = re.sub(r"[^a-z0-9]+", "-", job_id.lower()).strip("-")[:40] or "job"
    return f"tailored-{slug}-{hashlib.sha256(job_id.encode()).hexdigest()[:6]}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Model):
    file: str = Field(min_length=1)  # an id from get_tailor_setup / get_tailor_file
    quote: str = Field(min_length=1)


class Requirement(_Model):
    text: str = Field(min_length=1)
    kind: Literal["hard", "plus"]
    evidence: list[Evidence] = []  # empty: the applicant's material has nothing for it; it is not written


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _squash(text: str) -> str:
    return " ".join(text.split())


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append(path: Path, data) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def _lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def chunks(text: str, size: int) -> list[str]:
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > size:
            parts.append(current)
            current = ""
        current += line
    return [*parts, current] if current or not parts else parts


class Tailor:
    def __init__(self, box):
        self.box = box
        self.settings = box.settings.resume_tailor
        self.root = box.data_dir / self.settings.dir
        self.programs = box.tailor_programs or Programs(self.settings)
        self.runner = box.tailor_runner

    # ---- configuration ---------------------------------------------------------------------------------------

    def load(self):
        applicant = self.box._applicant()
        if not applicant.resume_tailor:
            raise InvariantError("Resume tailoring is not configured: applicant.yaml has no resume_tailor")
        applicant = self.box._ready_applicant()
        cfg = TailorFile.load(applicant.resume_tailor)
        problems = self._folder_problems(cfg, applicant)
        if problems:
            raise InvariantError(f"evidence_read folders that would expose private files: {problems}")
        return applicant, cfg

    def _folder_problems(self, cfg: TailorFile, applicant) -> list[str]:
        """Like apply_agent_read, except that a folder inside the applicant folder is fine: files are only read
        through get_tailor_file, which lists text files and never profile.json or .env."""
        data = self.box.data_dir.resolve()
        profile = applicant.profile.resolve()
        own = applicant.folder.resolve()
        out = []
        for folder in cfg.evidence_read:
            real = folder.resolve()
            if (real in (Path("/"), Path.home().resolve()) or data.is_relative_to(real) or profile.is_relative_to(real)
                    or (real.is_relative_to(data) and not real.is_relative_to(own))):  # fmt: skip
                out.append(str(folder))
        return out

    def setup(self) -> dict:
        result = {"configured": False, "problems": [], "resumes": [], "directions": [], "evidence": [],
                  "programs": {}, "evaluator": {"host": None}}  # fmt: skip
        try:
            applicant = self.box._applicant()
        except InvariantError as error:
            result["problems"].append(str(error))
            return result
        result["resumes"] = [{"id": r.id, "use_for": r.use_for, "tailorable": r.tex is not None}
                             for r in applicant.resumes]  # fmt: skip
        if not applicant.resume_tailor:
            result["problems"].append("applicant.yaml has no resume_tailor (see docs/examples/applicant)")
            return result
        try:
            _, cfg = self.load()
        except InvariantError as error:
            result["problems"].append(str(error))
            return result
        result["configured"] = True
        result["directions"] = sorted(cfg.roles)
        result["evidence"] = self._evidence_files(cfg)
        available = getattr(self.programs, "available", None)
        result["programs"] = available() if available else {}
        result["evaluator"] = self._evaluator_status()
        return result

    def _evaluator_status(self) -> dict:
        if self.box.settings.resume_tailor.evaluator.host == "codex":
            return {"host": None, "reason": "The Codex evaluator is not available yet (spec 5.3, slice 3)"}
        host = self._claude()
        if not host.binary():
            return {"host": None, "reason": "The Claude CLI is not installed"}
        try:
            version = host.version()
        except InvariantError as error:
            return {"host": None, "reason": str(error)}
        probe = (_read_json(self.root / "probes.json") or {}).get(f"claude {version}")
        if probe and not probe["passed"]:
            return {"host": None, "reason": "isolation probe failed", "problems": probe["problems"]}
        return {"host": "claude", "cli_version": version, "probe": "passed" if probe else "pending"}

    def _claude(self) -> ClaudeHost:
        return ClaudeHost(self.box.settings.resume_tailor.evaluator, self.runner)

    def _evidence_files(self, cfg: TailorFile) -> list[dict]:
        out = []
        for n, folder in enumerate(cfg.evidence_read, 1):
            for path in sorted(folder.rglob("*")):
                rel = path.relative_to(folder)
                if (not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES
                        or path.name.lower() in FORBIDDEN_NAMES
                        or any(part.startswith(".") for part in rel.parts)):  # fmt: skip
                    continue
                out.append({"id": f"evidence/{n}/{rel.as_posix()}", "path": str(path)})
                if len(out) >= MAX_EVIDENCE_FILES:
                    return out
        return out

    # ---- files ------------------------------------------------------------------------------------------------

    def resolve(self, file_id: str, applicant, cfg: TailorFile, run_id: str | None = None) -> Path:
        kind, _, rest = file_id.partition("/")
        if kind == "resume":
            resume = next((r for r in applicant.resumes if r.id == rest), None)
            if resume is None or resume.tex is None:
                raise InvariantError(f"{file_id!r}: no such resume with LaTeX source")
            return resume.tex
        if kind == "role" and rest in cfg.roles:
            return cfg.roles[rest]
        if file_id in ("style", "approved_numbers") and getattr(cfg, file_id):
            return getattr(cfg, file_id)
        if file_id == "rubric":
            return cfg.rubric or RUBRIC
        if kind == "evidence":
            index, _, rel = rest.partition("/")
            if not index.isdigit() or not 1 <= int(index) <= len(cfg.evidence_read) or not rel:
                raise InvariantError(f"{file_id!r} is not an evidence file")
            listed = {e["id"]: Path(e["path"]) for e in self._evidence_files(cfg)}  # only what setup lists
            folder = cfg.evidence_read[int(index) - 1].resolve()
            path = listed[file_id].resolve() if file_id in listed else None
            if path is None or not path.is_relative_to(folder) or path.name.lower() in FORBIDDEN_NAMES:
                raise InvariantError(f"{file_id!r} is not an evidence file")
            return path
        if run_id is not None:
            run = self.run_dir(run_id)
            if file_id == "jd" and (run / "jd.md").is_file():
                return run / "jd.md"
            if file_id in RUN_FILES and (run.parent / RUN_FILES[file_id]).is_file():
                return run.parent / RUN_FILES[file_id]
            if kind == "version":
                version, _, name = rest.partition("/")
                path = run / version / name
                if VERSION.match(version) and name in VERSION_FILES and path.is_file():
                    return path
        raise InvariantError(f"{file_id!r} is not a tailoring file (see get_tailor_setup)")

    def file(self, file_id: str, part: int, run_id: str | None) -> dict:
        applicant, cfg = self.load()
        parts = chunks(self.resolve(file_id, applicant, cfg, run_id).read_text(encoding="utf-8"),
                       self.settings.read_chars)  # fmt: skip
        if not 1 <= part <= len(parts):
            raise InvariantError(f"{file_id} has parts 1 to {len(parts)}, not {part}")
        return {"id": file_id, "part": part, "parts": len(parts), "text": parts[part - 1]}

    # ---- runs -------------------------------------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        key, _, stamp = run_id.partition("/")
        if (
            not key
            or key in (".", "..")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", key)
            or not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", stamp)
        ):
            raise InvariantError(f"{run_id!r} is not a tailoring run id")
        path = self.root / key / stamp
        if not (path / "meta.json").is_file():
            raise InvariantError(f"No tailoring run {run_id!r}")
        return path

    def start(self, resume_id, direction, reason, job_id, jd_text, slug, second_choice, mode="full") -> dict:
        if mode not in ("base", "skills", "full"):
            raise InvariantError("Unknown tailoring mode")
        applicant, cfg = self.load()
        if (job_id is None) == (jd_text is None):
            raise InvariantError("Give exactly one of job_id or jd_text")
        resume = applicant.resume(resume_id)
        if resume.tex is None:
            raise InvariantError(f"Resume {resume_id!r} has no LaTeX source (tex in applicant.yaml); it cannot be "
                                 "tailored")  # fmt: skip
        if direction not in cfg.roles:
            raise InvariantError(f"Unknown direction {direction!r}; choose from {sorted(cfg.roles)}")
        if not reason or not reason.strip():
            raise InvariantError("Say why this resume is the base (reason)")
        if job_id is not None:
            jd = self.box.store.job(job_id).description
            key = re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
        else:
            if not slug or not SLUG.match(slug):
                raise InvariantError("A pasted JD needs a slug: lowercase letters, digits and hyphens")
            jd, key = jd_text, slug
        folder = self.root / key
        old = folder / "jd.md"
        if old.is_file() and old.read_text(encoding="utf-8") != jd:
            if job_id is None:
                raise InvariantError(f"{key} already has a different JD from an earlier run; use another slug")
            (folder / "requirements.json").unlink(missing_ok=True)  # they were drawn from the old text
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run, n = folder / stamp, 2
        while run.exists():
            run, n = folder / f"{stamp}-{n}", n + 1
        (run / "v00").mkdir(parents=True)
        (folder / "jd.md").write_text(jd, encoding="utf-8")
        (run / "jd.md").write_text(jd, encoding="utf-8")  # what this run is judged against
        lines = [f"# Base resume for {job_id or slug}", "", f"- resume: {resume_id}", f"- direction: {direction}",
                 f"- reason: {reason.strip()}"]  # fmt: skip
        if second_choice:
            lines.append(f"- second choice: {second_choice.strip()}")
        (folder / "selection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        base = resume.tex.read_text(encoding="utf-8")
        (run / "v00" / "resume.tex").write_text(base, encoding="utf-8")
        run_id = f"{key}/{run.name}"
        _write_json(run / "meta.json", {"run_id": run_id, "job_id": job_id, "slug": slug, "resume_id": resume_id,
                                        "direction": direction, "mode": mode, "base_sha256": _sha(resume.tex),
                                        "created": _now()})  # fmt: skip
        return {"run_id": run_id, **self._render(run / "v00", cfg, None)}

    def _render(self, vdir: Path, cfg: TailorFile, base: str | None) -> dict:
        for name in ("resume.pdf", "page1.png", "render.json"):
            (vdir / name).unlink(missing_ok=True)
        tex = (vdir / "resume.tex").read_text(encoding="utf-8")
        try:
            self.programs.compile(tex, vdir / "resume.pdf")
            pages = self.programs.pages(vdir / "resume.pdf")
            self.programs.first_page_png(vdir / "resume.pdf", vdir / "page1.png")
            precheck = checks.pages_problem(pages)
        except InvariantError as error:
            pages, precheck = None, [str(error)]
        precheck += checks.check_tex(tex, base, cfg.approved(), cfg.checks, cfg.template)
        _write_json(vdir / "render.json", {"pages": pages, "precheck": precheck})
        return {"version": vdir.name, "pages": pages, "precheck": precheck}

    def _versions(self, run: Path) -> list[Path]:
        found = [p for p in run.iterdir() if p.is_dir() and VERSION.match(p.name)]
        return sorted(found, key=lambda p: int(VERSION.match(p.name).group(1)))

    def _valid_evaluation(self, vdir: Path) -> dict | None:
        ev = _read_json(vdir / "evaluation.json")
        return ev if ev is not None and not checks.evaluation_problems(ev) else None

    def save_version(self, run_id: str, tex: str, user_feedback: str | None) -> dict:
        _, cfg = self.load()
        run = self.run_dir(run_id)
        last = self._versions(run)[-1]
        mode = _read_json(run / "meta.json").get("mode", "full")
        if mode == "base":
            raise InvariantError("Base mode keeps v00 unchanged")
        if mode == "skills":
            base = (run / "v00" / "resume.tex").read_text(encoding="utf-8")
            if not checks.skills_only_edit(base, tex):
                raise InvariantError("Change only the existing Skills section; use full mode for other templates")
            if last.name == "v01" and (_read_json(last / "render.json") or {}).get("precheck") == []:
                raise InvariantError("This skills edit is final; start a new run to revise it")
            target = run / "v01"
        elif last.name == "v00":
            if not self._valid_evaluation(last):
                raise InvariantError("First evaluate v00: the base resume is the baseline")
            target = run / "v01"
        elif not self._valid_evaluation(last):
            target = last  # not evaluated yet: replaced
        else:
            status = (_read_json(last / "verdict.json") or {}).get("status")
            if status == "stop":
                raise InvariantError(f"{last.name} already reached stop; nothing more to tailor in this run")
            if status not in ("continue", "ask_user") or not (last / "response.md").is_file():
                raise InvariantError(f"First save the response to {last.name}'s evaluation")
            target = run / f"v{int(last.name[1:]) + 1:02d}"
        target.mkdir(exist_ok=True)
        for name in ("evaluation.json", "eval_usage.json", "verdict.json", "response.md", "changes.diff"):
            (target / name).unlink(missing_ok=True)
        (target / "resume.tex").write_text(tex, encoding="utf-8")
        if user_feedback and user_feedback.strip():
            with (run.parent / "user_feedback.md").open("a", encoding="utf-8") as f:
                f.write(f"- {_now()} ({target.name}): {user_feedback.strip()}\n")
        base = (run / "v00" / "resume.tex").read_text(encoding="utf-8")
        return self._render(target, cfg, base)

    def _version_dir(self, run: Path, version: str) -> Path:
        vdir = run / version
        if not VERSION.match(version) or not (vdir / "resume.tex").is_file():
            raise InvariantError(f"No version {version!r} in this run")
        return vdir

    def evaluate(self, run_id: str, version: str) -> dict:
        applicant, cfg = self.load()
        run = self.run_dir(run_id)
        vdir = self._version_dir(run, version)
        if self._valid_evaluation(vdir):
            raise InvariantError(f"{version} was already evaluated; a version is evaluated once")
        rendered = _read_json(vdir / "render.json") or {}
        if rendered.get("pages") is None:
            raise InvariantError(f"{version} does not compile; fix it with save_tailor_version first")
        if version != "v00" and rendered.get("precheck"):
            raise InvariantError(f"{version} has precheck problems: {rendered['precheck']}")
        costs = run / "costs.jsonl"
        if len(_lines(costs)) >= self.settings.max_evaluations_per_run:
            raise InvariantError(f"Reached the limit of {self.settings.max_evaluations_per_run} evaluations for this "
                                 "run; ask the applicant")  # fmt: skip
        host = self._host()
        cli_version = host.version()
        self._probe(host, cli_version)
        meta = _read_json(run / "meta.json")
        work = fresh_folder("resume-tailor-eval-")
        try:
            prompt = self._prepare(work, vdir, run, meta, applicant, cfg)
            text, usage = host.call(work, prompt)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        tex_sha = _sha(vdir / "resume.tex")
        retry = any(x.get("version") == version and x.get("tex_sha256") == tex_sha for x in _lines(costs))
        usage = {"version": version, "tex_sha256": tex_sha, "cli_version": cli_version, "retry": retry, **usage}
        _append(costs, usage)
        _write_json(vdir / "eval_usage.json", usage)
        if text is None:
            raise InvariantError(f"The evaluator call failed: {usage.get('error', '')}")
        ev = parse_object(text)
        if ev is None:
            raise InvariantError("Evaluator output is not JSON with an object at the top; evaluation.json not "
                                 f"written. Start of output: {text[:300]}")  # fmt: skip
        _write_json(vdir / "evaluation.json", ev)
        problems = checks.evaluation_problems(ev)
        if problems:
            result = {"status": "fail", "reasons": problems, "total": None}
        else:
            result = self._verdict(run, vdir, ev, None)
        _write_json(vdir / "verdict.json", result)
        if result["status"] == "stop":
            self._round(run, vdir, result)
        return {"version": version, "host": host.name, "scores": ev.get("scores"),
                "total": result["total"], "issues": ev.get("issues"), "verdict": result,
                "usage": usage}  # fmt: skip

    def _host(self) -> ClaudeHost:
        if self.box.settings.resume_tailor.evaluator.host == "codex":
            raise InvariantError("The Codex evaluator is not available yet: Codex cannot be isolated so far "
                                 "(specs/resume-tailor.md 5.3, slice 3); use host auto or claude")  # fmt: skip
        host = self._claude()
        if not host.binary():
            raise InvariantError("The Claude CLI is not installed; the evaluator needs it")
        return host

    def _probe(self, host: ClaudeHost, cli_version: str) -> None:
        path = self.root / "probes.json"
        probes = _read_json(path) or {}
        key = f"{host.name} {cli_version}"
        if key not in probes:
            self.root.mkdir(parents=True, exist_ok=True)
            attempts = self.root / "probe_attempts.jsonl"
            try:
                probes[key] = host.probe()
            except ProbeUnusable as error:  # nothing cached, but the money spent is recorded
                _append(attempts, {"key": key, "error": str(error), "usage": error.usage, "at": _now()})
                raise
            _append(attempts, {"key": key, "passed": probes[key]["passed"], "usage": probes[key]["usage"],
                               "at": _now()})  # fmt: skip
            _write_json(path, probes)
        if not probes[key]["passed"]:
            raise InvariantError(f"The evaluator isolation probe failed for {key}: {probes[key]['problems']}")

    def _prepare(self, work: Path, vdir: Path, run: Path, meta: dict, applicant, cfg: TailorFile) -> str:
        """The evaluator's folder: the redacted round, the rubric, the role files and the JD. Nothing else."""
        profile = json.loads(applicant.profile.read_text(encoding="utf-8"))
        masked, redactor = mask_tex((vdir / "resume.tex").read_text("utf-8"), profile)
        (work / "resume.tex").write_text(masked, encoding="utf-8")
        try:
            self.programs.compile(masked, work / "resume.pdf")
            pages = self.programs.pages(work / "resume.pdf")
            self.programs.first_page_png(work / "resume.pdf", work / "page1.png")
            rendered = self.programs.text(work / "resume.pdf")
        except InvariantError as error:
            raise InvariantError(f"The redacted copy for the evaluator does not render: {error}") from error
        if pages != 1:
            raise InvariantError(f"The redacted copy for the evaluator is {pages} pages, not one")
        hits = redactor.hits(masked) + redactor.hits(rendered)
        left = sorted(kind for kind in OWN_KINDS if hits[kind])
        if left:  # an escaped underscore or a ~ inside a value: the tex pattern misses it, the PDF shows it
            raise InvariantError(
                f"Contact details survive redaction in the evaluator copy ({', '.join(left)}); write them "
                "plainly in the tex (no escaped underscore or ~ inside them)"
            )
        shutil.copyfile(cfg.rubric or RUBRIC, work / "rubric.md")
        direction = meta["direction"]
        sections = cfg.role_sections.get(direction, {})
        (work / "roles").mkdir()
        for role in {direction, *sections.values()}:
            shutil.copyfile(cfg.roles[role], work / "roles" / f"{role}.md")
        jd = run / "jd.md" if (run / "jd.md").is_file() else run.parent / "jd.md"
        if jd.is_file():
            shutil.copyfile(jd, work / "jd.md")
        return message(direction, direction, sections, jd.is_file())

    def _history(self, run: Path, k: int) -> list[int | None]:
        out = []
        for j in range(k):
            ev = self._valid_evaluation(run / f"v{j:02d}")
            out.append(checks.total(ev) if ev else None)
        return out

    def _verdict(self, run: Path, vdir: Path, ev: dict, response: str | None) -> dict:
        k = int(vdir.name[1:])
        result = checks.verdict(
            k, ev, response, self._history(run, k), len(_lines(run / "costs.jsonl")),
            self.settings.max_evaluations_per_run,
        )  # fmt: skip
        precheck = (_read_json(vdir / "render.json") or {}).get("precheck") or []
        if result["status"] == "stop" and precheck:  # only v00 is evaluated with precheck problems
            return {**result, "status": "fail", "reasons": ["The base breaks hard checks: " + "; ".join(precheck)]}
        return result

    def _round(self, run: Path, vdir: Path, result: dict) -> None:
        if any(r["version"] == vdir.name for r in _lines(run / "rounds.jsonl")):
            return
        files = ("resume.tex", "resume.pdf", "evaluation.json")
        _append(run / "rounds.jsonl", {"version": vdir.name, "status": result["status"], "total": result["total"],
                                       "sha256": {n: _sha(vdir / n) for n in files}, "at": _now()})  # fmt: skip

    def respond(self, run_id: str, version: str, text: str) -> dict:
        run = self.run_dir(run_id)
        vdir = run / version
        ev = self._valid_evaluation(vdir) if VERSION.match(version) and vdir.is_dir() else None
        if ev is None:
            raise InvariantError(f"{version} has no valid evaluation to respond to")
        if self._versions(run)[-1] != vdir:
            raise InvariantError(f"{version} is not the latest version")
        earlier = (_read_json(vdir / "verdict.json") or {}).get("status")
        if (vdir / "response.md").is_file() and earlier != "fail":
            raise InvariantError(f"{version}'s response is already recorded ({earlier}); it stays as it is")
        (vdir / "response.md").write_text(text, encoding="utf-8")
        k = int(version[1:])
        prev = run / f"v{k - 1:02d}" / "resume.tex"
        if k and prev.is_file():
            diff = difflib.unified_diff(prev.read_text("utf-8").splitlines(keepends=True),
                                        (vdir / "resume.tex").read_text("utf-8").splitlines(keepends=True),
                                        f"v{k - 1:02d}/resume.tex", f"{version}/resume.tex")  # fmt: skip
            (vdir / "changes.diff").write_text("".join(diff), encoding="utf-8")
        else:
            (vdir / "changes.diff").write_text("# base version, nothing before it\n", encoding="utf-8")
        result = self._verdict(run, vdir, ev, text)
        _write_json(vdir / "verdict.json", result)
        if result["status"] != "fail":
            self._round(run, vdir, result)
        return result

    # ---- adoption (section 3, items 3-4) ----------------------------------------------------------------------

    def adopt(self, job_id: str, run_id: str | None, version: str | None, reason: str | None, actor: str) -> dict:
        if actor not in ("user", "delegated_coordinator"):
            raise InvariantError("actor must be user or delegated_coordinator (an audit statement, not a login)")
        self.box.store.job(job_id)
        base = applicant_files.folder(self.box.data_dir, self.box.settings.apply.applicant_dir)
        path = base / TAILORED_FILE
        key = re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
        record = {"job_id": job_id, "actor": actor, "reason": (reason or "").strip() or None, "at": _now()}
        if version is None:  # withdrawing needs neither the tailoring settings nor the run folder
            if run_id is not None:
                raise InvariantError("Give a version to adopt it, or only the job id to withdraw the adoption")
            with self.box.store.exclusive():  # read and rewrite as one step against another session
                entries = applicant_files.read_tailored(base)
                if not any(e.get("job_id") == job_id for e in entries):
                    raise InvariantError(f"No tailored resume is adopted for {job_id}")
                self._write_tailored(path, [e for e in entries if e.get("job_id") != job_id])
            self._void_letter(job_id)
            (self.root / key).mkdir(parents=True, exist_ok=True)
            _append(self.root / key / "adoptions.jsonl", {**record, "withdrawn": True})
            return {"job_id": job_id, "withdrawn": True}
        self.load()
        entries = applicant_files.read_tailored(base)
        if run_id is None:
            raise InvariantError("Give the run_id of the version to adopt")
        run = self.run_dir(run_id)
        meta = _read_json(run / "meta.json")
        if meta.get("job_id") is None:
            raise InvariantError(f"Run {run_id} tailors a pasted JD; only a run started from a stored job can be used")
        if meta["job_id"] != job_id:
            raise InvariantError(f"Run {run_id} is for job {meta['job_id']}, not {job_id}")
        vdir = self._version_dir(run, version)
        mode = meta.get("mode", "full")
        quick = (mode, version) in {("base", "v00"), ("skills", "v01")}
        if not quick and not self._valid_evaluation(vdir):
            raise InvariantError(f"{version} has no valid evaluation; only an evaluated version can be adopted")
        rendered = _read_json(vdir / "render.json") or {}
        if rendered.get("pages") != 1 or rendered.get("precheck"):
            raise InvariantError(f"{version} has precheck problems: {rendered.get('precheck')}")
        status = (_read_json(vdir / "verdict.json") or {}).get("status")
        if quick:
            status = mode
        if status in (None, "fail"):
            raise InvariantError(f"{version}'s verdict is {status or 'missing'}; fix it before adopting")
        if not quick and status != "stop" and not record["reason"]:
            raise InvariantError(f"{version} did not reach stop ({status}): give the reason it is adopted anyway")
        pdf, txt = vdir / "resume.pdf", vdir / "resume.txt"
        txt.write_text(self.programs.text(pdf), encoding="utf-8")
        entry = {"id": tailored_id(job_id), "job_id": job_id, "pdf": str(pdf), "text": str(txt), "sha256": _sha(pdf),
                 "use_for": f"Tailored for job {job_id} ({run_id} {version}): use it for this job"}  # fmt: skip
        with self.box.store.exclusive():  # read again inside the lock: another session may have just written
            entries = applicant_files.read_tailored(base)
            self._write_tailored(path, [*(e for e in entries if e.get("job_id") != job_id), entry])
        self._void_letter(job_id)
        _append(run.parent / "adoptions.jsonl", {**record, "run_id": run_id, "version": version, "status": status,
                                                 "sha256": entry["sha256"]})  # fmt: skip
        return {"job_id": job_id, "resume_id": entry["id"], "run_id": run_id, "version": version, "pdf": str(pdf)}

    @staticmethod
    def _write_tailored(path: Path, entries: list[dict]) -> None:
        """Written whole to a temporary file, then renamed: an interrupted write never leaves a broken file."""
        head = "# Written by adopt_tailored_resume (specs/resume-tailor.md). Do not edit; withdraw or adopt instead.\n"
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(head + yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)

    def _void_letter(self, job_id: str) -> None:
        """A letter was written from the old resume: forget it, so it is written again from the new one."""
        self.box.store.delete_cover_letter(job_id)
        for path in self.box._letter_files(job_id):
            path.unlink(missing_ok=True)

    # ---- requirements and status ------------------------------------------------------------------------------

    def requirements(self, run_id: str, items: list) -> dict:
        applicant, cfg = self.load()
        run = self.run_dir(run_id)
        try:
            reqs = [Requirement.model_validate(item) for item in items]
        except (ValidationError, TypeError) as error:
            raise InvariantError(f"Invalid requirements: {error}") from error
        problems = []
        for n, req in enumerate(reqs, 1):
            for ev in req.evidence:
                try:
                    source = self.resolve(ev.file, applicant, cfg)
                except InvariantError as error:
                    problems.append(f"requirement {n}: {error}")
                    continue
                if ev.file.startswith(("role/", "rubric", "style", "approved_numbers")):
                    problems.append(f"requirement {n}: {ev.file} is not the applicant's material")
                elif _squash(ev.quote) not in _squash(source.read_text(encoding="utf-8")):
                    problems.append(f"requirement {n}: the quote is not in {ev.file}")
        if problems:
            raise InvariantError("Requirements not saved: " + "; ".join(problems))
        _write_json(run.parent / "requirements.json", [r.model_dump() for r in reqs])
        return {"ok": True, "uncovered": [r.text for r in reqs if not r.evidence]}

    def info(self, run_id: str) -> dict:
        run = self.run_dir(run_id)
        meta = _read_json(run / "meta.json")
        reqs = _read_json(run.parent / "requirements.json") or []
        versions, latest = [], None
        for vdir in self._versions(run):
            rendered = _read_json(vdir / "render.json") or {}
            ev = self._valid_evaluation(vdir)
            verdict = _read_json(vdir / "verdict.json")
            versions.append({"version": vdir.name, "pages": rendered.get("pages"),
                             "precheck": rendered.get("precheck", []), "evaluated": ev is not None,
                             "total": checks.total(ev) if ev else None, "verdict": verdict,
                             "responded": (vdir / "response.md").is_file()})  # fmt: skip
            if ev is not None:
                latest = {"version": vdir.name, **ev}
        lines = _lines(run / "costs.jsonl")
        tokens: dict[str, int] = {}
        for line in lines:
            for name, value in (line.get("tokens") or {}).items():
                tokens[name] = tokens.get(name, 0) + (value or 0)
        uncovered = [r["text"] for r in reqs if not r["evidence"]]
        return {**meta, "requirements": {"count": len(reqs), "uncovered": uncovered},
                "versions": versions, "latest_evaluation": latest,
                "costs": {"evaluations": len(lines), "cost_usd": sum(x.get("cost_usd") or 0 for x in lines),
                          "tokens": tokens}}  # fmt: skip
