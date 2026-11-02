"""Open-answer bank, slice 1 (specs/open-answers.md, open-answers.plan.md; samples O1-O11 of
specs/open-answers.samples.md): the bank file, its checks, and the section the filling agents get after the answer
sheet and the checklist, in /apply's auto-submitted jobs and in the form-fill module alike.
All data is SYNTHETIC; nothing here starts Chrome, Claude Code or a browser, and no real profile is read."""

import json

import pytest
import yaml

from intent_job_agent import applicant as applicant_files
from intent_job_agent import open_answers
from intent_job_agent.config import Settings
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import runner
from intent_job_agent.form_fill.redact import Redactor
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from .apply_fakes import FormFillFake
from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings
from .test_form_fill_runner import FakeBrowser, write_list
from .test_slice6 import DAY, FakeRunner, add_jobs, write_applicant, write_letters

SAMPLES = yaml.safe_load((ROOT / "tests/fixtures/open_answers/samples.yaml").read_text(encoding="utf-8"))
assert SAMPLES["synthetic"] is True
CASES = {case["id"]: case for case in SAMPLES["cases"]}
REDACTOR = Redactor.from_profile(SAMPLES["profile"])
BUDGET = settings().open_answers.max_chars
STOP_RULES = ("ARBITRATION", "SSN", "PAYMENT", "ALREADY APPLIED", "NO AI WRITING", "CAPTCHA")
HEADER = "== OPEN ANSWER EXAMPLES =="


def write_bank(path, bank) -> object:
    path.write_text("# SYNTHETIC test data\n" + yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), "utf-8")
    return path


def load_case(tmp_path, case_id, max_chars=BUDGET):
    return open_answers.load(write_bank(tmp_path / "open_answers.yaml", CASES[case_id]["bank"]), REDACTOR, max_chars)


# --- the bank file -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", [case["id"] for case in SAMPLES["cases"] if case["expect"]["problems"]])
def test_bad_banks_list_every_problem_together_and_never_echo_a_value(tmp_path, case_id):
    expect = CASES[case_id]["expect"]
    with pytest.raises(InvariantError) as error:
        load_case(tmp_path, case_id)
    message = str(error.value)
    assert "open_answers.yaml" in message
    assert f"{len(expect['problems'])} problem(s)" in message
    for problem in expect["problems"]:
        assert problem in message
    for value in expect.get("never_echoed", []):
        assert value not in message


@pytest.mark.parametrize("case_id", [case["id"] for case in SAMPLES["cases"] if not case["expect"]["problems"]])
def test_good_banks_inject_only_the_accepted_entries(tmp_path, case_id):
    expect = CASES[case_id]["expect"]
    bank = load_case(tmp_path, case_id)
    assert [entry.id for entry in bank.accepted] == expect["accepted"]
    text = open_answers.section(bank)
    if expect.get("unchanged"):
        assert text == ""
        return
    at = 0
    for part in expect["section_in_order"]:
        at = text.index(part, at)  # each part comes after the one before
    for part in expect.get("section_lacks", []):
        assert part not in text


def test_multi_line_answers_keep_their_lines(tmp_path):
    text = open_answers.section(load_case(tmp_path, "O1"))
    assert (
        "  Answer:\n"
        "    Ranking is where I learned that a model is only as good as the question it answers.\n"
        "    On my last team I chose which signals to trust before I tuned anything.\n"
    ) in text
    assert "[why-role-ranking] Question: Why do you want to work on search ranking at our company?" in text


def test_the_section_tells_the_agent_how_to_use_the_examples_and_never_overrides_a_stop(tmp_path):
    text = open_answers.section(load_case(tmp_path, "O1"))
    rules = text[: text.index("-- why_role:")]
    lowered = rules.lower()
    for phrase in (
        "which facts and motivations",
        "how they phrase",
        "this job and the exact question",
        "only when the question is the same",
        "never add a fact",
        "rejected wording",
        "never override",
        "applicant's own words",
    ):
        assert phrase in lowered, phrase
    for rule in STOP_RULES:
        assert rule in rules


def test_over_budget_is_a_problem_and_never_cut(tmp_path):  # O8
    full = open_answers.section(load_case(tmp_path, "O1"))
    with pytest.raises(InvariantError) as error:
        load_case(tmp_path, "O1", max_chars=200)
    assert f"section of {len(full)} characters, over open_answers.max_chars 200" in str(error.value)
    assert load_case(tmp_path, "O1", max_chars=len(full)).accepted  # exactly at the budget passes


def test_without_a_redactor_contact_details_are_not_checked_here(tmp_path):
    path = write_bank(tmp_path / "open_answers.yaml", CASES["O2"]["bank"])
    assert open_answers.load(path, None, BUDGET).accepted  # load_inputs reports the broken profile itself


def test_a_missing_bank_file_is_a_problem(tmp_path):
    with pytest.raises(InvariantError, match="missing"):
        open_answers.load(tmp_path / "open_answers.yaml", REDACTOR, BUDGET)


def test_duplicate_yaml_keys_are_a_problem(tmp_path):
    path = tmp_path / "open_answers.yaml"
    path.write_text("version: 1\nversion: 1\nentries: []\n", encoding="utf-8")
    with pytest.raises(InvariantError, match="duplicate key 'version'"):
        open_answers.load(path, REDACTOR, BUDGET)


def test_the_question_types_are_a_closed_set_with_other_last():
    assert list(open_answers.QUESTION_TYPES) == [
        "why_role",
        "origin",
        "team_fit",
        "about_me",
        "challenge",
        "disagreement",
        "proudest_work",
        "anything_else",
        "other",
    ]


def test_the_budget_is_in_the_config():
    raw = yaml.safe_load((ROOT / "configs/default.yaml").read_text(encoding="utf-8"))
    assert raw["open_answers"]["max_chars"] == BUDGET == 8000
    old = {key: value for key, value in raw.items() if key != "open_answers"}  # an older workspace's settings
    assert Settings.model_validate(old).open_answers.max_chars == 8000


def test_the_example_bank_loads():
    examples = ROOT / "docs/examples/applicant"
    profile = json.loads((examples / "profile.example.json").read_text(encoding="utf-8"))
    bank = open_answers.load(examples / "open_answers.example.yaml", Redactor.from_profile(profile), BUDGET)
    assert bank.accepted and any(entry.status != "accepted" for entry in bank.entries)
    assert "open_answers:" in (examples / "applicant.example.yaml").read_text(encoding="utf-8")


# --- the instructions ------------------------------------------------------------------------------------------


def test_the_section_follows_the_checklist_and_without_a_bank_nothing_changes(tmp_path):
    before = runner.instructions({}, [])
    assert runner.instructions({}, [], None) == before
    for case_id in ("O4", "O5"):
        assert runner.instructions({}, [], load_case(tmp_path, case_id)) == before
    text = runner.instructions({}, [], load_case(tmp_path, "O1"))
    assert text.startswith(before) and text.index("== CHECKLIST ==") < text.index(HEADER)
    assert text == before + "\n\n" + open_answers.section(load_case(tmp_path, "O1"))


# --- the applicant field ---------------------------------------------------------------------------------------


def test_the_applicant_field_is_optional_and_resolved_in_the_applicant_folder(tmp_path):
    write_applicant(tmp_path / "plain")
    assert applicant_files.load(tmp_path / "plain", "applicant").open_answers is None
    data = tmp_path / "data"
    write_applicant(data, open_answers="open_answers.yaml")
    loaded = applicant_files.load(data, "applicant")
    assert loaded.open_answers == data / "applicant" / "open_answers.yaml"
    assert str(loaded.open_answers) in loaded.missing_files()  # configured but not there: a missing file
    write_bank(loaded.open_answers, CASES["O1"]["bank"])
    assert applicant_files.load(data, "applicant").missing_files() == []


# --- /apply ----------------------------------------------------------------------------------------------------


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": {**BASE_INTENT, "location": {"us": {"level": "require"}}}, "vocabulary": BASE_VOCAB}),
        encoding="utf-8",
    )
    box.initialize_intent(str(data / "intent.yaml"))
    box.apply_runner = FakeRunner()
    box.form_fill_runner = FormFillFake(box)
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.apply_in_background = False
    return box


def configure(box, case_id=None):
    """The applicant's files, with open_answers.yaml written from a sample (None: no bank configured)."""
    if case_id is None:
        write_applicant(box.data_dir)
        return
    write_applicant(box.data_dir, open_answers="open_answers.yaml")
    write_bank(box.data_dir / "applicant" / "open_answers.yaml", CASES[case_id]["bank"])


def both_steps(box, companies=("Co0", "Co1", "Co2", "Co3", "Co4", "Acme")):
    """Run the review step (form-fill module) and the rest (Acme auto-submitted); return each prompt addition."""
    jobs = add_jobs(box, list(companies))
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"])
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    through_form_fill = {id(spec) for spec, _ in box.form_fill_runner.fills}
    (submitted,) = [spec for spec in box.apply_runner.calls if id(spec) not in through_form_fill]
    return submitted.sheet, [options["extra_prompt"] for _, options in box.form_fill_runner.fills]


def test_both_ways_of_filling_get_the_same_section(box):  # O11
    configure(box, "O1")
    sheet, prompts = both_steps(box)
    section = open_answers.section(open_answers.load(box.data_dir / "applicant/open_answers.yaml", None, BUDGET))
    assert sheet.endswith("\n\n" + section)
    for prompt in prompts:
        assert "\n\n" + section + runner.RUN_RULES in prompt
        assert prompt.index("== CHECKLIST ==") < prompt.index(HEADER)


def test_without_a_bank_the_prompts_are_unchanged(box, tmp_path):  # O10
    configure(box)
    sheet, prompts = both_steps(box)
    assert HEADER not in sheet and all(HEADER not in prompt for prompt in prompts)
    assert sheet.endswith(
        "- websites_linkedin_github_only: The Websites field holds only the LinkedIn and GitHub profile links."
    )
    assert "open_answers" not in box.get_apply_setup()


def test_setup_counts_the_accepted_entries_and_never_returns_their_text(box):  # O11
    configure(box, "O1")
    setup = box.get_apply_setup()
    assert setup["open_answers"] == {"file": str(box.data_dir / "applicant/open_answers.yaml"), "accepted": 4}
    assert setup["answer_sheet_problems"] == []
    assert "Ranking is where I learned" not in json.dumps(setup)


def test_an_invalid_bank_stops_the_step_and_is_shown_in_setup(box):  # O11 with O3 (duplicate ids)
    configure(box, "O3")
    setup = box.get_apply_setup()
    assert setup["ready"] is False
    assert any("duplicate id 'why-role'" in problem for problem in setup["answer_sheet_problems"])
    jobs = add_jobs(box, ["Co0", "Co1"])
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    with pytest.raises(InvariantError, match="duplicate id 'why-role'"):
        box.start_applying(batch["batch_id"], batch["summary"])
    assert box.apply_runner.calls == [] and box.form_fill_runner.fills == []
    assert box.store.applications()["j00"]["status"] == "pending"


def test_contact_details_in_the_bank_stop_the_step_without_echoing_them(box):
    configure(box)
    (box.data_dir / "applicant" / "applicant.yaml").write_text(
        (box.data_dir / "applicant" / "applicant.yaml").read_text(encoding="utf-8")
        + "open_answers: open_answers.yaml\n",
        encoding="utf-8",
    )
    bank = {
        "version": 1,
        "entries": [
            {
                "id": "about",
                "type": "about_me",
                "question": "Tell us about yourself.",
                "text": "Write to sam.synthetic@example.test or call 555-0100.",  # the synthetic profile's own
                "status": "accepted",
                "from": "SYNTHETIC",
            }
        ],
    }
    write_bank(box.data_dir / "applicant" / "open_answers.yaml", bank)
    setup = box.get_apply_setup()
    (problem,) = setup["answer_sheet_problems"]
    assert "entries[0] (about).text: contains contact details (email, phone)" in problem
    assert "sam.synthetic" not in problem and "555-0100" not in problem
    jobs = add_jobs(box, ["Co0"])
    write_letters(box, jobs)
    batch = box.prepare_apply_batch(DAY)
    with pytest.raises(InvariantError) as error:
        box.start_applying(batch["batch_id"], batch["summary"])
    assert "contains contact details" in str(error.value) and "sam.synthetic" not in str(error.value)
    assert box.apply_runner.calls == []


def test_a_configured_bank_that_is_missing_stops_applying(box):  # O11
    configure(box)
    path = box.data_dir / "applicant" / "applicant.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "open_answers: open_answers.yaml\n", encoding="utf-8")
    setup = box.get_apply_setup()
    assert setup["ready"] is False and str(box.data_dir / "applicant/open_answers.yaml") in setup["missing"]


def test_the_bank_never_reaches_the_tagging_rules(box):
    configure(box, "O1")
    assert "Ranking is where I learned" not in json.dumps(box.get_tagging_rules(), ensure_ascii=False)


# --- the form-fill module on its own ---------------------------------------------------------------------------


def form_fill_list(box) -> str:
    lists = box.data_dir / "form-fill" / "lists"
    return write_list(
        lists,
        [
            {
                "id": "acme-ml-01",
                "url": "https://jobs.example.test/acme-ml-01",
                "company": "Acme Robotics",
                "title": "ML Engineer",
                "resume": "ml",
                "cover_letter": "none",
            }
        ],
    )


def test_start_form_fill_gives_the_filling_agent_the_section(box):
    configure(box, "O1")
    box.form_fill_runner = FakeBrowser()
    box.form_fill_in_background = False
    box.start_form_fill(form_fill_list(box))
    ((_, options),) = box.form_fill_runner.calls
    assert HEADER in options["extra_prompt"] and "Ranking is where I learned" in options["extra_prompt"]


def test_start_form_fill_refuses_an_invalid_bank(box):
    configure(box, "O6")
    box.form_fill_runner = FakeBrowser()
    box.form_fill_in_background = False
    with pytest.raises(InvariantError, match="The form-fill run was not started") as error:
        box.start_form_fill(form_fill_list(box))
    assert "version must be 1" in str(error.value)
    assert box.form_fill_runner.calls == []
