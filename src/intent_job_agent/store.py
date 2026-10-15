"""SQLite persistence. Append-only history; `apply_decision` is the only way to change the intent."""

import json
import sqlite3
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from .changes import apply_changes
from .config import Settings
from .domain import BOARD_SOURCES, IntentModel, InvariantError, Job, Label, Vocabulary
from .proposals import Analysis, Proposal
from .replay import evaluate, replay

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tag_overrides (job_id TEXT PRIMARY KEY, tags TEXT NOT NULL, decided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS labels (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
                                   superseded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS support (label_id TEXT NOT NULL, ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vocabulary (seq INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intent_versions (version INTEGER PRIMARY KEY, data TEXT NOT NULL,
                                            decision_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, base_version INTEGER NOT NULL,
                                      data TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, analysis_id TEXT,
                                      kind TEXT NOT NULL, proposal_id TEXT, payload TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tagged (job_id TEXT PRIMARY KEY, tagged_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS selections (day TEXT NOT NULL, job_id TEXT NOT NULL, slot TEXT NOT NULL,
                                       position INTEGER NOT NULL, PRIMARY KEY (day, job_id));
CREATE TABLE IF NOT EXISTS board_snapshots (seq INTEGER PRIMARY KEY, source TEXT NOT NULL, board TEXT NOT NULL,
                                            job_ids TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS external_rows (url TEXT PRIMARY KEY, job_id TEXT, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backfills (job_id TEXT NOT NULL, dimension TEXT NOT NULL, tags TEXT NOT NULL,
                                      labelled INTEGER NOT NULL, created_at TEXT NOT NULL,
                                      PRIMARY KEY (job_id, dimension));
CREATE TABLE IF NOT EXISTS fetch_log (seq INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, first_listed TEXT NOT NULL,
                                         updated_at TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS cover_letters (job_id TEXT PRIMARY KEY, resume_id TEXT NOT NULL, txt_path TEXT NOT NULL,
                                          pdf_path TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS apply_batches (id TEXT PRIMARY KEY, day TEXT NOT NULL, data TEXT NOT NULL,
                                          created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dimension_requests (seq INTEGER PRIMARY KEY, text TEXT NOT NULL, label_id TEXT,
                                               created_at TEXT NOT NULL);
"""


APPLICATION_STATUSES = ("pending", "applied", "skipped")  # what the user sets
# What an application run reports: waiting = stopped before submit; pending = never reached the site.
RUN_STATUSES = ("applied", "waiting", "failed", "pending")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: str | Path, settings: Settings):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path).resolve()
        self.settings = settings
        # Callers serialize access (the MCP server holds a lock); the SDK may call from worker threads.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(applications)")}
        if "note" not in columns:  # databases created before slice 6
            with self.db:
                self.db.execute("ALTER TABLE applications ADD COLUMN note TEXT")

    # --- jobs and labels (never touch the intent) ---------------------------------

    def add_job(self, job: Job) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs (id, data) VALUES (?, ?)", (job.id, job.model_dump_json()))

    def job(self, job_id: str) -> Job:
        row = self.db.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise InvariantError(f"Unknown job {job_id}")
        return Job.model_validate_json(row["data"])

    def jobs(self) -> list[Job]:
        return [Job.model_validate_json(row["data"]) for row in self.db.execute("SELECT data FROM jobs ORDER BY rowid")]

    def has_job(self, job_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is not None

    def job_ids_by_url(self) -> dict[str, str]:
        """Stored jobs by their posting URL (the first stored job wins), for deduplicating fetched postings."""
        urls: dict[str, str] = {}
        for job in self.jobs():
            if job.url:
                urls.setdefault(job.url, job.id)
        return urls

    def is_tagged(self, job_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tagged WHERE job_id = ?", (job_id,)).fetchone() is not None

    def tagged_ids(self) -> set[str]:
        return {row["job_id"] for row in self.db.execute("SELECT job_id FROM tagged")}

    def set_job_tags(self, job_id: str, tags: list[str]) -> Job:
        """Initial tags of a job not yet shown; later changes go through the misread correction."""
        if job_id in self.shown_job_ids():
            raise InvariantError(f"Job {job_id} was already shown; fix its tags through a misread correction")
        if self.db.execute("SELECT 1 FROM tag_overrides WHERE job_id = ?", (job_id,)).fetchone():
            raise InvariantError(
                f"Job {job_id} has tags a misread correction set (spec section 10 item 83); fix them the same way"
            )
        job = self.job(job_id).model_copy(update={"tags": tags})
        with self.db:
            self.db.execute("UPDATE jobs SET data = ? WHERE id = ?", (job.model_dump_json(), job_id))
            self.db.execute("INSERT OR REPLACE INTO tagged (job_id, tagged_at) VALUES (?, ?)", (job_id, _now()))
            # Tagged now, so the dimensions that need backfill for older jobs are already covered.
            for dimension in self.settings.tagging.backfill_dimensions:
                own = [tag for tag in tags if tag.startswith(dimension + ".")]
                self.db.execute(
                    "INSERT OR IGNORE INTO backfills (job_id, dimension, tags, labelled, created_at) "
                    "VALUES (?, ?, ?, 0, ?)",
                    (job_id, dimension, json.dumps(own), _now()),
                )
        return job

    def backfilled(self, job_id: str, dimension: str) -> bool:
        row = self.db.execute("SELECT 1 FROM backfills WHERE job_id = ? AND dimension = ?", (job_id, dimension))
        return row.fetchone() is not None

    def backfill(self, job_id: str, dimension: str, tags: list[str]) -> list[str]:
        """Add tags of a newly added dimension to a tagged job (shown or not). Other tags never change."""
        if not self.is_tagged(job_id):
            raise InvariantError(f"Job {job_id} is not tagged yet; tag it with submit_job_tags")
        if self.backfilled(job_id, dimension):
            raise InvariantError(f"Job {job_id} was already backfilled for {dimension}; fix tags through misread")
        current = self.effective_tags(job_id)
        added = [tag for tag in dict.fromkeys(tags) if tag not in current]
        labelled = self.db.execute(
            "SELECT 1 FROM labels WHERE json_extract(data, '$.job_id') = ?", (job_id,)
        ).fetchone()
        with self.db:
            if added:
                row = self.db.execute("SELECT 1 FROM tag_overrides WHERE job_id = ?", (job_id,)).fetchone()
                if row:
                    self.db.execute(
                        "UPDATE tag_overrides SET tags = ? WHERE job_id = ?", (json.dumps(current + added), job_id)
                    )
                else:
                    job = self.job(job_id).model_copy(update={"tags": current + added})
                    self.db.execute("UPDATE jobs SET data = ? WHERE id = ?", (job.model_dump_json(), job_id))
            self.db.execute(
                "INSERT INTO backfills (job_id, dimension, tags, labelled, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, dimension, json.dumps(added), int(bool(labelled and added)), _now()),
            )
        return current + added

    def tag_revision(self) -> int:
        """Changes analyses depend on besides the intent version: backfilled tags of labelled jobs, and misread
        fixes that reached a labelled job from another listing of its posting (spec section 10 item 83)."""
        backfilled = self.db.execute("SELECT COUNT(*) FROM backfills WHERE labelled = 1").fetchone()[0]
        rippled = self.db.execute(
            "SELECT COUNT(*) FROM decisions WHERE kind = 'tag_fix' AND json_extract(payload, '$.labelled') = 1"
        ).fetchone()[0]
        return backfilled + rippled

    def log_fetch(self, kind: str) -> None:
        """Jobs were fetched or imported today (the user's local date)."""
        with self.db:
            self.db.execute(
                "INSERT INTO fetch_log (day, kind, at) VALUES (?, ?, ?)", (date.today().isoformat(), kind, _now())
            )

    def fetched_on(self, day: str) -> bool:
        return self.db.execute("SELECT 1 FROM fetch_log WHERE day = ?", (day,)).fetchone() is not None

    def tagged_on(self, day: str) -> int:
        """Jobs fully tagged on `day` (local date), for the daily tagging budget."""
        rows = self.db.execute("SELECT tagged_at FROM tagged").fetchall()
        return sum(1 for row in rows if datetime.fromisoformat(row["tagged_at"]).astimezone().date().isoformat() == day)

    def untagged_jobs(self) -> list[Job]:
        """Open jobs still waiting for tags: those with full text first, then most recently stored first."""
        waiting = [job for job in reversed(self.jobs()) if not self.is_tagged(job.id) and self.is_open(job)]
        full = self.settings.discovery.full_text_chars
        return sorted(waiting, key=lambda job: len(job.description) < full)

    def external_row(self, url: str) -> dict | None:
        """Whether a posting from ApplyPilot's working DB was already imported or excluded."""
        row = self.db.execute("SELECT job_id, status FROM external_rows WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def mark_external(self, url: str, job_id: str | None, status: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO external_rows (url, job_id, status) VALUES (?, ?, ?)", (url, job_id, status)
            )

    def record_board_snapshot(self, source: str, board: str, job_ids: list[str]) -> None:
        """The complete set of postings a successful fetch saw; only successful fetches are recorded."""
        with self.db:
            self.db.execute(
                "INSERT INTO board_snapshots (source, board, job_ids, fetched_at) VALUES (?, ?, ?, ?)",
                (source, board, json.dumps(sorted(job_ids)), _now()),
            )

    def is_open(self, job: Job) -> bool:
        """Board jobs are open while their board's latest fetch lists them; other sources stay open
        (search results are not complete listings, so absence says nothing)."""
        if job.source not in BOARD_SOURCES:
            return True
        row = self.db.execute(
            "SELECT job_ids FROM board_snapshots WHERE source = ? AND board = ? ORDER BY seq DESC LIMIT 1",
            (job.source, job.board),
        ).fetchone()
        return row is not None and job.id in json.loads(row["job_ids"])

    def save_selection(self, day: str, items: list[tuple[str, str]]) -> None:
        with self.db:
            if self.db.execute("SELECT 1 FROM selections WHERE day = ?", (day,)).fetchone():
                raise InvariantError(f"Jobs for {day} were already selected")
            self.db.executemany(
                "INSERT INTO selections (day, job_id, slot, position) VALUES (?, ?, ?, ?)",
                [(day, job_id, slot, n) for n, (job_id, slot) in enumerate(items, start=1)],
            )

    def selection(self, day: str) -> list[tuple[str, str]]:
        rows = self.db.execute("SELECT job_id, slot FROM selections WHERE day = ? ORDER BY position", (day,))
        return [(row["job_id"], row["slot"]) for row in rows]

    def shown_job_ids(self) -> set[str]:
        return {row["job_id"] for row in self.db.execute("SELECT job_id FROM selections")}

    def shown_in_order(self) -> list[str]:
        """Jobs shown on any day, by day and then by their place that day."""
        rows = self.db.execute("SELECT job_id FROM selections ORDER BY day, position")
        return list(dict.fromkeys(row["job_id"] for row in rows))

    def effective_tags(self, job_id: str) -> list[str]:
        """Job tags after any user-confirmed misread correction."""
        row = self.db.execute("SELECT tags FROM tag_overrides WHERE job_id = ?", (job_id,)).fetchone()
        return json.loads(row["tags"]) if row else list(self.job(job_id).tags)

    def tag_overrides(self) -> dict[str, list[str]]:
        """Every misread correction at once: job id -> its corrected tags."""
        return {
            row["job_id"]: json.loads(row["tags"]) for row in self.db.execute("SELECT job_id, tags FROM tag_overrides")
        }

    def set_effective_tags(
        self, job_id: str, tags: list[str], label_id: str | None, from_job: str | None = None, labelled: bool = False
    ) -> None:
        """A misread correction, on the misread job or (`from_job`) reaching another listing of its posting;
        `labelled` when that listing has a want/reject label, which makes open analyses stale."""
        payload = {"job_id": job_id, "tags": tags, "label_id": label_id}
        if from_job:
            payload |= {"from_job": from_job, "labelled": int(labelled)}
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tag_overrides (job_id, tags, decided_at) VALUES (?, ?, ?)",
                (job_id, json.dumps(tags), _now()),
            )
            self._decision(None, "tag_fix", None, payload)

    def add_label(self, label: Label) -> None:
        self.job(label.job_id)
        with self.db:
            self.db.execute("INSERT INTO labels (id, data) VALUES (?, ?)", (label.id, label.model_dump_json()))

    def _label(self, row) -> Label:
        return Label.model_validate_json(row["data"]).model_copy(update={"superseded": bool(row["superseded"])})

    def label(self, label_id: str) -> Label:
        row = self.db.execute("SELECT data, superseded FROM labels WHERE id = ?", (label_id,)).fetchone()
        if row is None:
            raise InvariantError(f"Unknown label {label_id}")
        return self._label(row)

    def labels(self) -> list[Label]:
        return [self._label(row) for row in self.db.execute("SELECT data, superseded FROM labels ORDER BY seq")]

    def add_support(self, label_id: str, refs: list[str]) -> None:
        """A consistent label recorded as support for entries; not an intent change."""
        with self.db:
            self.db.executemany("INSERT INTO support (label_id, ref) VALUES (?, ?)", [(label_id, r) for r in refs])

    def add_dimension_request(self, text: str, label_id: str | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO dimension_requests (text, label_id, created_at) VALUES (?, ?, ?)", (text, label_id, _now())
            )

    def dimension_requests(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT text, label_id FROM dimension_requests ORDER BY seq")]

    # --- the apply list: what the user did with listed jobs (never touches the intent or labels) ---

    def applications(self) -> dict[str, dict]:
        """Listed jobs by id: status (pending / applied / skipped, or waiting / failed after a run), the day they
        were first listed and the last run's note."""
        rows = self.db.execute("SELECT job_id, status, first_listed, note FROM applications")
        return {
            row["job_id"]: {"status": row["status"], "first_listed": row["first_listed"], "note": row["note"]}
            for row in rows
        }

    def list_applications(self, job_ids: list[str], day: str) -> None:
        """Jobs entering the list for the first time start as pending; jobs listed before keep their status."""
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO applications (job_id, status, first_listed, updated_at) "
                "VALUES (?, 'pending', ?, ?)",
                [(job_id, day, _now()) for job_id in job_ids],
            )

    def set_application_status(self, job_ids: list[str], status: str) -> None:
        if status not in APPLICATION_STATUSES:
            raise InvariantError(f"Status must be one of {list(APPLICATION_STATUSES)}, not {status!r}")
        listed = self.applications()
        unknown = [job_id for job_id in job_ids if job_id not in listed]
        if unknown:
            raise InvariantError(f"Jobs {unknown} were never on the apply list")
        with self.db:
            self.db.executemany(
                "UPDATE applications SET status = ?, updated_at = ?, note = NULL WHERE job_id = ?",
                [(status, _now(), job_id) for job_id in job_ids],
            )

    def record_application_result(self, job_id: str, status: str, note: str | None) -> None:
        if status not in RUN_STATUSES:
            raise InvariantError(f"Run status must be one of {list(RUN_STATUSES)}, not {status!r}")
        with self.db:
            self.db.execute(
                "UPDATE applications SET status = ?, note = ?, updated_at = ? WHERE job_id = ?",
                (status, note, _now(), job_id),
            )

    def save_cover_letter(self, job_id: str, resume_id: str) -> None:
        """Which resume a saved letter was written from. The files' location is derived from the job id, so no
        path is stored (the project can move; older rows' path columns are ignored)."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO cover_letters (job_id, resume_id, txt_path, pdf_path, created_at) "
                "VALUES (?, ?, '', '', ?)",
                (job_id, resume_id, _now()),
            )

    def cover_letter(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT resume_id FROM cover_letters WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def save_apply_batch(self, batch_id: str, day: str, data: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO apply_batches (id, day, data, created_at) VALUES (?, ?, ?, ?)",
                (batch_id, day, json.dumps(data, ensure_ascii=False), _now()),
            )

    def apply_batch(self, batch_id: str) -> dict:
        row = self.db.execute("SELECT day, data FROM apply_batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise InvariantError(f"Unknown apply batch {batch_id}")
        return {"day": row["day"], **json.loads(row["data"])}

    # --- intent: initialize once, then change only through accepted proposals ---------

    def initialize(self, intent: IntentModel, vocab: Vocabulary) -> IntentModel:
        with self.db:
            if self.db.execute("SELECT 1 FROM intent_versions").fetchone():
                raise InvariantError("The intent is already initialized; later changes need a user decision")
            intent = intent.model_copy(update={"version": 1})
            self.db.execute(
                "INSERT INTO intent_versions (version, data, decision_id, created_at) VALUES (1, ?, NULL, ?)",
                (intent.model_dump_json(), _now()),
            )
            self.db.execute("INSERT INTO vocabulary (data) VALUES (?)", (vocab.model_dump_json(),))
        return intent

    def current_version(self) -> int:
        row = self.db.execute("SELECT MAX(version) AS v FROM intent_versions").fetchone()
        if row["v"] is None:
            raise InvariantError("The intent has not been initialized")
        return row["v"]

    def current_intent(self) -> IntentModel:
        row = self.db.execute("SELECT data FROM intent_versions ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            raise InvariantError("The intent has not been initialized")
        return IntentModel.model_validate_json(row["data"])

    def versions(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM intent_versions ORDER BY version")]

    def vocabulary(self) -> Vocabulary:
        row = self.db.execute("SELECT data FROM vocabulary ORDER BY seq DESC LIMIT 1").fetchone()
        return Vocabulary.model_validate_json(row["data"]).with_builtin()

    # --- analyses, proposals and decisions --------------------------------------------

    def save_analysis(self, analysis: Analysis) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO analyses (id, data) VALUES (?, ?)", (analysis.id, analysis.model_dump_json())
            )
            for proposal in analysis.proposals:
                self.db.execute(
                    "INSERT OR IGNORE INTO proposals (id, analysis_id, base_version, data, status) "
                    "VALUES (?, ?, ?, ?, 'pending')",
                    (proposal.id, analysis.id, proposal.base_version, proposal.model_dump_json()),
                )

    def analyses(self) -> list[Analysis]:
        return [Analysis.model_validate_json(row["data"]) for row in self.db.execute("SELECT data FROM analyses")]

    def analysis(self, analysis_id: str) -> Analysis:
        row = self.db.execute("SELECT data FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if row is None:
            raise InvariantError(f"Unknown analysis {analysis_id}")
        return Analysis.model_validate_json(row["data"])

    def _decision(self, analysis_id, kind, proposal_id, payload) -> str:
        decision_id = new_id("decision")
        self.db.execute(
            "INSERT INTO decisions (id, analysis_id, kind, proposal_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (decision_id, analysis_id, kind, proposal_id, json.dumps(payload, ensure_ascii=False), _now()),
        )
        return decision_id

    def record_decision(self, analysis_id: str, kind: str, payload: dict | None = None) -> str:
        """Record a choice that does not change the intent ("no_change", "feedback")."""
        if kind == "accept":
            raise InvariantError("Accepting a proposal goes through apply_decision")
        with self.db:
            if kind in ("no_change", "feedback"):  # options offered so far are no longer pending
                self.db.execute(
                    "UPDATE proposals SET status = 'closed' WHERE analysis_id = ? AND status = 'pending'",
                    (analysis_id,),
                )
            return self._decision(analysis_id, kind, None, payload)

    def close_pending(self, analysis_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE proposals SET status = 'closed' WHERE analysis_id = ? AND status = 'pending'", (analysis_id,)
            )

    def decisions(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM decisions ORDER BY seq")]

    def apply_decision(self, proposal_id: str, inputs: dict | None = None, approved_via: str = "direct") -> IntentModel:
        """The single write path: apply a pending proposal the user chose, on the version it was built for."""
        row = self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise InvariantError(f"Unknown proposal {proposal_id}")
        if row["status"] != "pending":
            raise InvariantError(f"Proposal {proposal_id} is {row['status']}")
        proposal = Proposal.model_validate_json(row["data"])
        current = self.current_version()
        if proposal.base_version != current:
            raise InvariantError(f"Proposal {proposal_id} was built for version {proposal.base_version}, now {current}")

        vocab = self.vocabulary()
        for ref in proposal.new_keys:
            vocab = vocab.with_key(ref)
        before = self.current_intent()
        after = apply_changes(before, proposal.resolved_changes(inputs), vocab, tuple(proposal.evidence))
        after = after.model_copy(update={"version": current + 1})
        result, _ = replay(evaluate(before, self, self.settings), after, self, self.settings)

        with self.db:
            decision_id = self._decision(
                proposal.analysis_id,
                "accept",
                proposal_id,
                {"inputs": inputs or {}, "approved_via": approved_via, "origin": proposal.origin},
            )
            self.db.execute(
                "INSERT INTO intent_versions (version, data, decision_id, created_at) VALUES (?, ?, ?, ?)",
                (current + 1, after.model_dump_json(), decision_id, _now()),
            )
            if proposal.new_keys:
                self.db.execute("INSERT INTO vocabulary (data) VALUES (?)", (vocab.model_dump_json(),))
            # Labels this user-confirmed change contradicts are kept, but no longer count.
            self.db.executemany("UPDATE labels SET superseded = 1 WHERE id = ?", [(i,) for i in result.broken])
            self.db.execute("UPDATE proposals SET status = 'accepted' WHERE id = ?", (proposal_id,))
            self.db.execute(
                "UPDATE proposals SET status = 'closed' WHERE analysis_id = ? AND status = 'pending'",
                (proposal.analysis_id,),
            )
        return after
