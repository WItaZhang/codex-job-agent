"""SQLite persistence. Append-only history; `apply_decision` is the only way to change the intent."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .changes import apply_changes
from .config import Settings
from .domain import IntentModel, InvariantError, Job, Label, Vocabulary
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
CREATE TABLE IF NOT EXISTS dimension_requests (seq INTEGER PRIMARY KEY, text TEXT NOT NULL, label_id TEXT,
                                               created_at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: str | Path, settings: Settings):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        # Callers serialize access (the MCP server holds a lock); the SDK may call from worker threads.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

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

    def is_tagged(self, job_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tagged WHERE job_id = ?", (job_id,)).fetchone() is not None

    def set_job_tags(self, job_id: str, tags: list[str]) -> Job:
        """Initial tags of a job not yet shown; later changes go through the misread correction."""
        if job_id in self.shown_job_ids():
            raise InvariantError(f"Job {job_id} was already shown; fix its tags through a misread correction")
        job = self.job(job_id).model_copy(update={"tags": tags})
        with self.db:
            self.db.execute("UPDATE jobs SET data = ? WHERE id = ?", (job.model_dump_json(), job_id))
            self.db.execute("INSERT OR REPLACE INTO tagged (job_id, tagged_at) VALUES (?, ?)", (job_id, _now()))
        return job

    def untagged_jobs(self) -> list[Job]:
        """Open jobs still waiting for tags, most recently stored first."""
        return [job for job in reversed(self.jobs()) if not self.is_tagged(job.id) and self.is_open(job)]

    def record_board_snapshot(self, source: str, board: str, job_ids: list[str]) -> None:
        """The complete set of postings a successful fetch saw; only successful fetches are recorded."""
        with self.db:
            self.db.execute(
                "INSERT INTO board_snapshots (source, board, job_ids, fetched_at) VALUES (?, ?, ?, ?)",
                (source, board, json.dumps(sorted(job_ids)), _now()),
            )

    def is_open(self, job: Job) -> bool:
        """Manually imported jobs stay open; board jobs are open while their board's latest fetch lists them."""
        if job.source == "manual":
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

    def effective_tags(self, job_id: str) -> list[str]:
        """Job tags after any user-confirmed misread correction."""
        row = self.db.execute("SELECT tags FROM tag_overrides WHERE job_id = ?", (job_id,)).fetchone()
        return json.loads(row["tags"]) if row else list(self.job(job_id).tags)

    def set_effective_tags(self, job_id: str, tags: list[str], label_id: str | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tag_overrides (job_id, tags, decided_at) VALUES (?, ?, ?)",
                (job_id, json.dumps(tags), _now()),
            )
            self._decision(None, "tag_fix", None, {"job_id": job_id, "tags": tags, "label_id": label_id})

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
        return Vocabulary.model_validate_json(row["data"])

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
                proposal.analysis_id, "accept", proposal_id, {"inputs": inputs or {}, "approved_via": approved_via}
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
