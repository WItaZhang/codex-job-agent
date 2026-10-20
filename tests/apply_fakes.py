"""Fakes for /apply steps whose jobs go through the form-fill module (spec section 10 items 86-96).
All data is SYNTHETIC; nothing here starts Chrome, Claude Code or a browser."""

import json
from pathlib import Path

from intent_job_agent import apply_bridge
from intent_job_agent.form_fill import readback
from intent_job_agent.form_fill.models import PageState, ReadField

ANSWERS = """# SYNTHETIC test data
version: 1
entries:
  name.legal:
    from_profile: personal.full_name
  name.first:
    answer: Sam
    kind: text
  name.last:
    answer: Example
    kind: text
  contact.email:
    from_profile: personal.email
  work_auth.authorized_us:
    answer: "Yes"
    kind: yes_no
    asks: ["legally authorized to work"]
  work_auth.sponsorship_future:
    answer: "Yes"
    kind: yes_no
    asks: ["now or in the future"]
"""
CHECKLIST = """# SYNTHETIC test data
version: 1
items:
  - id: websites_linkedin_github_only
    rule: The Websites field holds only the LinkedIn and GitHub profile links.
    level: format
"""
FIELDS = (ReadField(id="p1.1", question="First Name", origin="control", control="text", value="Sam", required=True),)
FINAL = PageState(actions=("Submit application",))  # the last page still shows its submit button


def write_sheet(data_dir: Path, answers: str = ANSWERS, checklist: str = CHECKLIST) -> None:
    folder = Path(data_dir) / "applicant"
    (folder / "answers.yaml").write_text(answers, encoding="utf-8")
    (folder / "checklist.yaml").write_text(checklist, encoding="utf-8")


def _line(*blocks, kind="assistant") -> str:
    if kind == "assistant":
        return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})
    return json.dumps({"type": "user", "message": {"role": "user", "content": list(blocks)}})


class FormFillFake:
    """The applying Chrome and the filling agent of the form-fill jobs of an apply step. The agent's outcome comes
    from `box.apply_runner`, so a FakeRunner's canned outcomes and its `calls` cover both kinds of job; the job's tab
    opens on its own site (unless the job is in `no_tab`), and its last page reads back `fields`. `state` is the last
    page's state, `states` the state of particular jobs (`received` makes a page look submitted)."""

    running = True  # whether the applying Chrome already runs when a step starts

    def __init__(self, box, fields=FIELDS, state=FINAL, states=None, no_tab=(), personal=()):
        self.box, self.fields, self.state = box, tuple(fields), state
        self.states, self.no_tab = states or {}, set(no_tab)
        self.tabs = [{"id": f"P{n}", "type": "page", "url": url} for n, url in enumerate(personal)]
        self.tabs.append({"id": "T1", "type": "page", "url": "https://jobs.example.test/checking"})
        self.fills, self.reads = [], []

    def ensure_chrome(self) -> int:
        return 9222

    def targets(self) -> list[dict]:
        return [dict(tab) for tab in self.tabs]

    def open_tabs(self) -> list[dict] | None:
        return self.targets() if self.running else None

    def fill(self, spec, **options):
        self.fills.append((spec, options))
        outcome = self.box.apply_runner(spec)  # may raise, as a broken runner does
        url = f"{spec.url}/apply"
        navigate = {"type": "tool_use", "id": "t", "name": "mcp__playwright__browser_navigate", "input": {"url": url}}
        answer = {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [{"type": "text", "text": f"- Page URL: {url}"}],
        }
        lines = [_line(navigate), _line(answer, kind="user")]
        log = options["log_path"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("".join(options["mask"](line + "\n") for line in lines), encoding="utf-8")
        if outcome.status != "pending" and spec.job_id not in self.no_tab:  # a run that never started opened no tab
            self.tabs.append({"id": f"N-{spec.job_id}", "type": "page", "url": url})
        notes, sites, emails = list(outcome.notes), list(outcome.other_sites), list(outcome.emails)
        return apply_bridge.ApplyOutcome(outcome.status, outcome.reason, notes, sites, emails, str(log))

    def read(self, target, out_dir, redactor, sheet=None):
        self.reads.append(target)
        out_dir.mkdir(parents=True, exist_ok=True)
        dumped = [field.model_dump(mode="json") for field in self.fields]
        (out_dir / "readback.json").write_text(json.dumps(redactor.mask_json(dumped)), encoding="utf-8")
        return readback.FinalPage(self.fields, self.states.get(target["id"].removeprefix("N-"), self.state))
