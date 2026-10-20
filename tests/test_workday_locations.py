"""Workday locations: every place a Workday posting lists is stored, and pre-tagging reads the formats employers
use (spec section 10 items 18, 19 and 31). All data here is SYNTHETIC and offline; the Workday API is faked."""

import sqlite3

import pytest
import yaml

from intent_job_agent import applypilot_bridge as bridge
from intent_job_agent.domain import Job, Vocabulary
from intent_job_agent.pretag import pretag, read_locations
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox
from intent_job_agent.vendor.applypilot.discovery import workday as ap_workday

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings

VOCAB = Vocabulary.build(BASE_VOCAB)
SAMPLES = yaml.safe_load((ROOT / "tests/fixtures/workday_locations.yaml").read_text(encoding="utf-8"))
assert SAMPLES["synthetic"] is True
BOTH, HYBRID = {"location", "work_mode"}, "work_mode.hybrid"
LONG = "SYNTHETIC posting. " + "Build and ship machine learning systems with a small team. " * 8


def read(location: str) -> tuple[set[str], set[str]]:
    """The location and work_mode pre-tags of a location, and which of those two were read with confidence."""
    result = pretag(Job(id="j", title="Engineer", location=location, tags=[]), VOCAB)
    tags = {tag for tag in result.tags if tag.startswith(("location.", "work_mode."))}
    return tags, set(result.confident & {"location", "work_mode"})


# --- reading a location (item 31) ------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SAMPLES["samples"]))
def test_samples_a1_to_a10(name):
    sample = SAMPLES["samples"][name]
    assert read(sample["location"]) == (set(sample["tags"]), set(sample["confident"]))


@pytest.mark.parametrize(
    ("location", "tags", "confident"),
    [
        ("San Jose", set(), set()),  # a city alone
        ("US FL JAX 347", set(), set()),  # an address code
        ("2 Locations", set(), set()),
        ("US, CA, Santa Clara; US FL JAX 347", set(), set()),  # no partial reading
        ("US, CA, Remote; Canada, Remote", {"location.us", "location.canada", "work_mode.remote"}, BOTH),
        ("Seattle, WA (Hybrid) | Portland, OR", {"location.us.seattle", "location.us.portland"}, {"location"}),
        ("Seattle, WA (Hybrid) | Portland, OR (Hybrid)", {"location.us.seattle", "location.us.portland", HYBRID}, BOTH),
        ("Seattle, WA; Remote", {"location.us.seattle"}, {"location"}),  # only one place says Remote
        ("Austin, TX or Remote", {"location.us.austin"}, {"location"}),  # was read as remote before item 31
        ("Seattle, WA (Remote/Hybrid)", {"location.us.seattle", "work_mode.hybrid"}, {"location", "work_mode"}),
        ("Ontario - Toronto", {"location.canada.toronto"}, {"location"}),
        ("Canada, ON, Toronto", {"location.canada.toronto"}, {"location"}),
        ("Germany, Munich", {"location.germany.munich"}, {"location"}),
        ("Switzerland - Zurich", {"location.switzerland.zurich"}, {"location"}),
        ("Seoul, South Korea", {"location.south_korea.seoul"}, {"location"}),
        ("USA - California", {"location.us"}, {"location"}),
        ("Texas, United States", {"location.us"}, {"location"}),
        ("Germany, Bavaria, Munich", set(), set()),  # three parts, country first: only the US and Canada
        ("US, Canada", set(), set()),  # two countries in one place
        ("Massachusetts - Cambridge - Boston", set(), set()),
    ],
)  # fmt: skip
def test_more_formats(location, tags, confident):
    assert read(location) == (tags, confident)


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
    ],
)
def test_formats_read_before_stay_the_same(location, tags, confident):
    assert read(location) == (tags, confident)


def test_structured_work_mode_still_wins_over_the_text():
    job = Job(id="j", title="Engineer", location="US, CA, Santa Clara; US, CA, Remote", tags=[])
    job = job.model_copy(update={"attributes": {"workplace_type": "hybrid"}})
    result = pretag(job, VOCAB)
    assert "work_mode.hybrid" in result.tags and "work_mode" in result.confident


def test_read_locations_gives_canonical_tags_only_when_every_place_is_read():
    vocab = Vocabulary.build({**BASE_VOCAB, "location": {**BASE_VOCAB["location"], "us.bay_area": ["us.palo_alto"]}})
    assert read_locations("California - Palo Alto; US, WA, Seattle", vocab) == [
        "location.us.bay_area",
        "location.us.seattle",
    ]
    assert read_locations("California - Palo Alto; Drug Manufacturing", vocab) is None
    assert read_locations("Remote", vocab) is None
    assert read_locations("", vocab) is None


# --- storing every Workday location (item 19) -------------------------------------------------------

EMPLOYER = {
    "name": "Example Corp",
    "tenant": "example",
    "site_id": "External",
    "base_url": "https://example.wd1.myworkdayjobs.com",
}
PATH = "/job/Santa-Clara/ML-Engineer_JR100001"
URL = f"{EMPLOYER['base_url']}/{EMPLOYER['site_id']}{PATH}"


class FakeWorkday:
    """Stands in for the Workday CXS API: one posting with the given search-page text and detail locations;
    `fail` makes the detail request fail as a 403 does."""

    def __init__(self, monkeypatch, listed="2 Locations"):
        self.listed, self.location, self.additional, self.fail = listed, None, [], False
        monkeypatch.setattr(ap_workday, "workday_search", self.search)
        monkeypatch.setattr(ap_workday, "workday_detail", self.detail)

    def search(self, employer, search_text, limit=20, offset=0):
        return {
            "total": 1,
            "jobPostings": [{"title": "ML Engineer", "locationsText": self.listed, "externalPath": PATH}],
        }

    def detail(self, employer, external_path):
        if self.fail:
            raise OSError("HTTP Error 403: Forbidden")
        info = {"jobDescription": f"<p>{LONG}</p>", "externalUrl": URL}
        if self.location is not None:
            info["location"] = self.location
        if self.additional:
            info["additionalLocations"] = self.additional
        return {"jobPostingInfo": info}


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL", "INTENT_AGENT_PROXY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": BASE_INTENT, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    (data / "employers.yaml").write_text(yaml.safe_dump({"employers": {"example": EMPLOYER}}), encoding="utf-8")
    (data / "searches.yaml").write_text(
        yaml.safe_dump({"queries": [{"query": "machine learning engineer", "tier": 1}]}), encoding="utf-8"
    )
    return box


def discover(box) -> dict:
    result = box.discover_jobs(sources=["workday"], enrich=False, wait=True)
    assert result["status"] == "done" and result["sources"]["workday"]["error"] is None, result
    return result["synced"]


def the_job(box) -> Job:
    (job,) = box.store.jobs()
    return job


def test_b1_a_new_posting_stores_every_place_primary_first(box, monkeypatch):
    fake = FakeWorkday(monkeypatch)
    fake.location, fake.additional = "California - Palo Alto", ["California - San Francisco"]
    discover(box)
    job = the_job(box)
    assert job.location == "California - Palo Alto; California - San Francisco"
    assert job.description.startswith("SYNTHETIC posting.")  # the rest of ApplyPilot's detail is kept


def test_b2_without_the_detail_the_search_page_text_is_stored(box, monkeypatch):
    fake = FakeWorkday(monkeypatch, listed="Seattle, WA")
    fake.location, fake.fail = "Washington - Seattle", True
    discover(box)
    assert the_job(box).location == "Seattle, WA"


def test_b3_places_are_stored_as_written_once_each(box, monkeypatch):
    fake = FakeWorkday(monkeypatch, listed="")
    fake.location = "Massachusetts - Cambridge"
    fake.additional = ["Massachusetts - Cambridge", "Drug Manufacturing", " "]
    discover(box)
    job = the_job(box)
    assert job.location == "Massachusetts - Cambridge; Drug Manufacturing"
    assert "location" not in pretag(job, VOCAB).confident


def test_the_detail_location_is_kept_beside_applypilots_row(box, monkeypatch):
    fake = FakeWorkday(monkeypatch)
    fake.location = "California - Palo Alto"
    discover(box)
    db = sqlite3.connect(bridge.staging_dir(box.data_dir, box.settings) / "applypilot.db")
    try:
        assert db.execute("SELECT location FROM jobs WHERE url = ?", (URL,)).fetchone() == ("2 Locations",)
    finally:
        db.close()


# --- refreshing stored jobs (item 18) ---------------------------------------------------------------


def stored_with_search_text(box, monkeypatch) -> tuple[FakeWorkday, Job]:
    """A posting imported before this fix: only the search page's "2 Locations"."""
    fake = FakeWorkday(monkeypatch)
    discover(box)
    job = the_job(box)
    assert job.location == "2 Locations"
    return fake, job


def tagged_and_listed(box, job: Job) -> Job:
    box.submit_job_tags(job.id, ["location.us.seattle", "role.ml_engineering", "work_mode.hybrid"])
    box.store.list_applications([job.id], "2026-10-19")
    return box.store.job(job.id)


def tagged_at(box, job: Job) -> str:
    return box.store.db.execute("SELECT tagged_at FROM tagged WHERE job_id = ?", (job.id,)).fetchone()[0]


def test_b4_an_untagged_job_takes_the_new_location(box, monkeypatch):
    fake, job = stored_with_search_text(box, monkeypatch)
    fake.location, fake.additional = "US, CA, Santa Clara", ["US, WA, Seattle"]
    synced = discover(box)
    assert (synced["relocated"], synced["retagged"]) == (1, 0)
    after = box.store.job(job.id)
    assert after.location == "US, CA, Santa Clara; US, WA, Seattle"
    assert after.tags == [] and not box.store.is_tagged(job.id)


def test_b5_a_tagged_job_swaps_only_its_location_tags(box, monkeypatch):
    fake, job = stored_with_search_text(box, monkeypatch)
    job = tagged_and_listed(box, job)
    before = tagged_at(box, job)
    fake.location, fake.additional = "US, CA, Santa Clara", ["US, WA, Seattle"]
    synced = discover(box)
    assert (synced["relocated"], synced["retagged"]) == (1, 0)
    after = box.store.job(job.id)
    assert after.location == "US, CA, Santa Clara; US, WA, Seattle"
    assert set(after.tags) == {
        "role.ml_engineering",
        "work_mode.hybrid",
        "location.us.santa_clara",
        "location.us.seattle",
    }
    assert box.store.is_tagged(job.id) and tagged_at(box, job) == before  # no tagging budget used
    assert box.store.applications()[job.id]["status"] == "pending"


def test_b6_a_tagged_job_whose_new_location_is_not_fully_read_goes_back_to_tagging(box, monkeypatch):
    fake, job = stored_with_search_text(box, monkeypatch)
    job = tagged_and_listed(box, job)
    fake.location, fake.additional = "US, CA, Santa Clara", ["US FL JAX 347"]
    synced = discover(box)
    assert (synced["relocated"], synced["retagged"]) == (1, 1)
    after = box.store.job(job.id)
    assert after.location == "US, CA, Santa Clara; US FL JAX 347"
    assert after.tags == [] and not box.store.is_tagged(job.id)
    assert job.id in [waiting.id for waiting in box.store.untagged_jobs()]
    assert box.store.applications()[job.id]["status"] == "pending"


def shown(box, job):
    box.store.save_selection("2026-10-19", [(job.id, "recommended")])


def corrected(box, job):
    box.store.set_effective_tags(job.id, ["location.us.seattle", "role.ml_engineering"], None)


def corrected_from_another_listing(box, job):
    box.store.set_effective_tags(job.id, ["location.us.seattle", "role.ml_engineering"], None, from_job="other")


def waiting(box, job):
    box.store.record_application_result(job.id, "waiting", "stopped before submit")


def failed(box, job):
    box.store.record_application_result(job.id, "failed", "synthetic failure")


def skipped(box, job):
    box.store.set_application_status([job.id], "skipped")


def applied(box, job):
    box.store.set_application_status([job.id], "applied")


@pytest.mark.parametrize(
    "freeze", [shown, corrected, corrected_from_another_listing, waiting, failed, skipped, applied]
)
def test_b7_shown_corrected_and_acted_on_jobs_keep_their_location_and_tags(box, monkeypatch, freeze):
    fake, job = stored_with_search_text(box, monkeypatch)
    job = tagged_and_listed(box, job)
    freeze(box, job)
    tags = box.store.effective_tags(job.id)
    fake.location, fake.additional = "US, CA, Santa Clara", ["US FL JAX 347"]
    assert discover(box)["relocated"] == 0
    after = box.store.job(job.id)
    assert after.location == "2 Locations" and after.tags == job.tags
    assert box.store.effective_tags(job.id) == tags and box.store.is_tagged(job.id)


def test_b8_a_failed_detail_keeps_the_location_read_before(box, monkeypatch):
    fake = FakeWorkday(monkeypatch)
    fake.location, fake.additional = "California - Palo Alto", ["California - San Francisco"]
    discover(box)
    fake.fail = True
    assert discover(box)["relocated"] == 0
    assert the_job(box).location == "California - Palo Alto; California - San Francisco"


def test_b9_an_unchanged_location_changes_nothing(box, monkeypatch):
    fake = FakeWorkday(monkeypatch)
    fake.location = "California - Palo Alto"
    discover(box)
    job = tagged_and_listed(box, the_job(box))
    synced = discover(box)
    assert (synced["relocated"], synced["retagged"], synced["updated"]) == (0, 0, 0)
    assert box.store.job(job.id) == job


def test_vendored_workday_module_is_unchanged_except_imports():
    source = (ROOT / "src/intent_job_agent/vendor/applypilot/discovery/workday.py").read_text(encoding="utf-8")
    assert "intent_agent" not in source
    assert 'info.get("location"' not in source  # the fix lives in the bridge, not in ApplyPilot's code
