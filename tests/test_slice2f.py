"""Slice 2f: the daily fetch funnel (pre-tags, pre-filter, rough rank, tagging budget) and the
/fetch-jobs and /today orchestration files. All data is SYNTHETIC."""

import json
import re
from datetime import date

import pytest
import yaml

from intent_job_agent.domain import Job, Vocabulary
from intent_job_agent.pretag import pretag
from intent_job_agent.store import Store
from intent_job_agent.tools import COMMIT_TOOLS, TOOL_TIERS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings

TODAY = date.today().isoformat()
VOCAB = Vocabulary.build(BASE_VOCAB)


def job(title="Engineer", location="", **kw) -> Job:
    return Job(id=kw.pop("id", "j"), title=title, location=location, tags=[], **kw)


# --- pre-tags: only what code can read with confidence ------------------------------------------


@pytest.mark.parametrize(
    ("location", "tags", "confident"),
    [
        ("San Francisco, CA", {"location.us.san_francisco"}, {"location"}),
        ("New York, NY (Hybrid)", {"location.us.new_york", "work_mode.hybrid"}, {"location", "work_mode"}),
        ("Seattle, WA, United States", {"location.us.seattle"}, {"location"}),
        ("Remote - US", {"location.us", "work_mode.remote"}, {"location", "work_mode"}),
        ("London, UK", {"location.uk.london"}, {"location"}),
        ("Bangalore, India", {"location.india.bangalore"}, {"location"}),
        ("Remote", {"work_mode.remote"}, {"work_mode"}),
        ("Somewhere Nice", set(), set()),
        ("", set(), set()),
    ],
)
def test_locations_are_read_only_when_unambiguous(location, tags, confident):
    result = pretag(job(location=location), VOCAB)
    assert set(result.tags) == tags
    assert result.confident == confident


def test_structured_fields_and_title_words():
    result = pretag(
        job(
            "Senior Machine Learning Engineer",
            "Austin, TX",
            attributes={"employment_type": "Full-time", "workplace_type": "onSite"},
        ),
        VOCAB,
    )
    assert {"seniority.senior", "employment_type.full_time", "work_mode.onsite", "location.us.austin"} <= set(
        result.tags
    )
    assert {"seniority", "employment_type", "work_mode", "location"} <= result.confident
    intern = pretag(job("Machine Learning Intern"), VOCAB)
    assert "employment_type.internship" in intern.tags


def test_title_aliases_suggest_keys_without_confidence():
    vocab = Vocabulary.build({**BASE_VOCAB, "role": {"ml_engineering": ["machine learning engineer"]}})
    result = pretag(job("Machine Learning Engineer, Ads"), vocab)
    assert "role.ml_engineering" in result.tags
    assert "role" not in result.confident  # suggestions for ranking, never for exclusion


# --- toolbox: funnel, budget, rules and status ------------------------------------------------------


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    spec = {**BASE_INTENT, "location": {"us": {"level": "require"}, "china.shanghai": {"level": "prefer"}}}
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": spec, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    return box


def add_jobs(box, jobs: list[dict]) -> list[str]:
    path = box.data_dir / "jobs.json"
    path.write_text(json.dumps([{"description": "SYNTHETIC", **j} for j in jobs]), encoding="utf-8")
    return box.import_jobs(str(path))["job_ids"]


def test_jobs_violating_confident_hard_constraints_are_held_back(box):
    add_jobs(
        box,
        [
            {"id": "london", "title": "ML Engineer", "location": "London, UK"},
            {"id": "senior", "title": "Senior ML Engineer", "location": "Austin, TX"},
            {"id": "unknown", "title": "ML Engineer", "location": "Remote"},
            {"id": "ok", "title": "ML Engineer", "location": "Austin, TX"},
        ],
    )
    listed = box.list_untagged_jobs(limit=10)
    assert {j["job_id"] for j in listed["jobs"]} == {"unknown", "ok"}
    assert listed["prefiltered"] == 2
    ok = next(j for j in listed["jobs"] if j["job_id"] == "ok")
    assert "location.us.austin" in ok["suggested_tags"] and "location" in ok["confident_dimensions"]


def test_held_back_jobs_return_when_the_intent_changes(box):
    add_jobs(box, [{"id": "london", "title": "ML Engineer", "location": "London, UK"}])
    assert box.list_untagged_jobs()["jobs"] == []
    analysis = box.propose_intent_edit("UK is fine too", [{"op": "add", "ref": "location.uk", "level": "require"}])
    option = next(o for o in analysis["options"] if "proposal_id" in o and "必须" in o["summary"])
    box.decide(analysis["id"], option["n"], summary=option["summary"])
    assert [j["job_id"] for j in box.list_untagged_jobs()["jobs"]] == ["london"]


def test_untagged_jobs_are_ordered_by_rough_score(box):
    vocab_jobs = [
        {"id": "plain", "title": "Data Analyst", "location": "Austin, TX"},
        {"id": "big", "title": "ML Engineer", "location": "Austin, TX (On-site)"},
        {"id": "remote", "title": "ML Engineer", "location": "Remote - US"},
    ]
    add_jobs(box, vocab_jobs)
    order = [j["job_id"] for j in box.list_untagged_jobs()["jobs"]]
    # onsite is avoided in BASE_INTENT, so the on-site job ranks below the others
    assert order.index("big") > order.index("remote")


def test_daily_tagging_budget(box):
    add_jobs(box, [{"id": f"j{n}", "title": "ML Engineer", "location": "Austin, TX"} for n in range(60)])
    budget = box.settings.tagging.daily_budget
    listed = box.list_untagged_jobs(limit=1000, day=TODAY)
    assert len(listed["jobs"]) == budget and listed["budget"]["remaining"] == budget
    for item in listed["jobs"][:5]:
        box.submit_job_tags(item["job_id"], ["role.ml_engineering"])
    after = box.list_untagged_jobs(limit=1000, day=TODAY)
    assert after["budget"]["tagged_today"] == 5 and len(after["jobs"]) == budget - 5
    assert len(box.list_untagged_jobs(limit=1000, day="2099-01-01")["jobs"]) == budget  # another day


def test_show_job_offers_pre_tags_for_untagged_jobs(box):
    add_jobs(box, [{"id": "a", "title": "ML Engineer", "location": "Austin, TX"}])
    shown = box.show_job("a")
    assert "location.us.austin" in shown["suggested_tags"]
    box.submit_job_tags("a", ["role.ml_engineering", "location.us.austin"])
    assert "suggested_tags" not in box.show_job("a")


def test_tagging_rules_cover_every_dimension(box):
    rules = box.get_tagging_rules()
    assert set(rules["dimensions"]) == set(rules["per_dimension"])
    assert "untrusted" in rules["general"].lower() or "instructions" in rules["general"].lower()
    assert rules["known_keys"]["work_mode"] == ["hybrid", "onsite", "remote"]


def test_fetch_status_reports_the_day(box):
    status = box.get_fetch_status(TODAY)
    assert status["fetched"] is False and status["selected"] is False
    add_jobs(box, [{"id": "a", "title": "ML Engineer", "location": "Austin, TX"}])
    box.submit_job_tags("a", ["role.ml_engineering", "location.us.austin"])
    status = box.get_fetch_status(TODAY)
    assert status["fetched"] is True  # importing counts as fetching for the day
    assert status["tagged_today"] == 1 and status["tagged_open"] == 1
    box.select_today(TODAY)
    assert box.get_fetch_status(TODAY)["selected"] is True


def test_new_read_tools_are_registered():
    assert TOOL_TIERS["get_tagging_rules"] == TOOL_TIERS["get_fetch_status"] == "read"


# --- orchestration files ----------------------------------------------------------------------------


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} needs YAML frontmatter"
    return yaml.safe_load(match.group(1)), text


def test_tagger_subagent_is_cheap_and_cannot_commit():
    meta, _ = frontmatter(ROOT / ".claude/agents/job-tagger.md")
    assert meta["name"] == "job-tagger" and meta["model"] == "haiku"
    tools = {t.strip() for t in str(meta["tools"]).split(",")}
    assert tools == {
        "mcp__intent-agent__get_tagging_rules",
        "mcp__intent-agent__show_job",
        "mcp__intent-agent__submit_job_tags",
    }
    assert not {f"mcp__intent-agent__{name}" for name in COMMIT_TOOLS} & tools


SKILL_TOOLS = {
    "fetch-jobs": {
        "get_fetch_status",
        "get_discovery_setup",
        "discover_jobs",
        "discovery_status",
        "list_untagged_jobs",
    },
    "today": {
        "get_fetch_status",
        "select_today",
        "record_labels",
        "choose_cause",
        "decide",
        "decline",
        "correct_tags",
    },
}


@pytest.mark.parametrize("skill", sorted(SKILL_TOOLS))
def test_skills_only_reference_existing_tools(skill):
    meta, text = frontmatter(ROOT / ".claude/skills" / skill / "SKILL.md")
    assert meta["name"] == skill and meta["description"]
    called = set(re.findall(r"`([a-z_]+)\(", text))  # tool calls are written as `name(...)`
    assert called <= set(TOOL_TIERS), called - set(TOOL_TIERS)
    assert SKILL_TOOLS[skill] <= called
    # A skill must never pre-approve a commit tool: those always go through the host's permission prompt.
    allowed = str(meta.get("allowed-tools", ""))
    assert not [name for name in COMMIT_TOOLS if name in allowed]


def test_fetch_jobs_hands_out_ids_without_job_text(box):
    add_jobs(box, [{"id": "a", "title": "ML Engineer", "location": "Austin, TX"}])
    brief = box.list_untagged_jobs(day=TODAY, brief=True)
    assert brief["jobs"] == [{"job_id": "a", "title": "ML Engineer", "company": ""}]
    assert "rules" not in brief and brief["budget"]["remaining"] == box.settings.tagging.daily_budget
