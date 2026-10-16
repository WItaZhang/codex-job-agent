"""Form-fill inputs (specs/form-fill.md 2.1, 2.3, 2.4): the job list, answers.yaml, checklist.yaml and the field
paths of profile.json. All data here is SYNTHETIC: invented applicants, files written to tmp_path."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill.inputs import load_answers, load_checklist, load_list, profile_field_paths

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/form_fill"
EXAMPLES = ROOT / "docs/examples"

PROFILE_FIELDS = {"personal.full_name", "personal.email", "personal.phone", "personal.city"}
APPLICANT_FIELDS = {"relocation", "onsite_ok"}
NAMES = {"name.first": {"answer": "Sam", "kind": "text"}, "name.last": {"answer": "Synthetic", "kind": "text"}}


def write(path: Path, content) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else yaml.safe_dump(content, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def sample_entries() -> dict:
    entries = dict(NAMES)
    for name in ("samples_work_auth.yaml", "samples_profile.yaml"):
        data = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
        assert data["synthetic"] is True
        entries.update(data["answers"])
    return entries


def answers_file(tmp_path: Path, entries: dict, **extra) -> Path:
    return write(tmp_path / "answers.yaml", {"version": 1, "entries": entries, **extra})


def answers(tmp_path: Path, entries: dict, **extra):
    return load_answers(answers_file(tmp_path, entries, **extra), PROFILE_FIELDS, APPLICANT_FIELDS)


def answers_error(tmp_path: Path, entries: dict, **extra) -> str:
    with pytest.raises(InvariantError) as error:
        answers(tmp_path, entries, **extra)
    message = str(error.value)
    assert "answers.yaml" in message
    return message


# --- answers.yaml -----------------------------------------------------------------------------------------------


def test_sample_answers_load(tmp_path):
    loaded = answers(tmp_path, sample_entries())
    assert set(loaded) >= {"name.first", "work_auth.sponsorship_opt_named", "education.ms_esu", "eeo.race"}
    opt = loaded["work_auth.sponsorship_opt_named"]
    assert opt.answer == "Yes" and opt.kind == "yes_no" and opt.category == "work_auth"
    assert opt.overrides == ("work_auth.sponsorship_now", "work_auth.sponsorship_future")
    race = loaded["eeo.race"]
    assert "Prefer not to say" in race.accept and race.otherwise is None
    assert loaded["eeo.hispanic_latino"].otherwise == "No"
    record = loaded["education.ms_esu"]
    assert record.kind == "record" and record.answer is None
    assert record.record == {
        "school": "Example State University",
        "degree": "Master of Science",
        "major": "Statistics",
        "start": "2025-08",
        "end": "2027-05",
    }
    assert record.record_accept["degree"] == ("M.S.", "MS", "Master's Degree")
    assert record.aliases == ("ESU",)
    assert loaded["compensation.expected"].answer == "90000"
    assert loaded["availability.start_date"].answer == "2027-06-01"


def test_values_are_kept_as_written(tmp_path):
    path = write(
        tmp_path / "answers.yaml",
        """
version: 1
entries:
  name.first: {answer: Sam}
  name.last: {answer: Synthetic}
  education.gpa: {answer: 3.60, kind: text, accept: [3.6, 3.60/4.0]}
  compensation.expected: {answer: 95000, kind: number_min}
  availability.start_date: {answer: 2027-06-01, kind: date}
  availability.graduation: {answer: 2027-05, kind: month}
  work_auth.authorized_us: {answer: Yes, kind: yes_no}
  work_auth.sponsorship_now: {answer: No, kind: yes_no}
  experience.intern:
    kind: record
    company: Synthetic Labs
    title: Data Intern
    start: 2024-06
    end: 2024-09
""",
    )
    loaded = load_answers(path, PROFILE_FIELDS, APPLICANT_FIELDS)
    assert loaded["education.gpa"].answer == "3.60"
    assert loaded["education.gpa"].accept == ("3.6", "3.60/4.0")
    assert loaded["compensation.expected"].answer == "95000"
    assert loaded["availability.start_date"].answer == "2027-06-01"
    assert loaded["availability.graduation"].answer == "2027-05"
    assert loaded["work_auth.authorized_us"].answer == "Yes"
    assert loaded["work_auth.sponsorship_now"].answer == "No"
    assert loaded["experience.intern"].record["start"] == "2024-06"


def test_references_load(tmp_path):
    loaded = answers(
        tmp_path,
        {
            "name.legal": {"from_profile": "personal.full_name"},
            "name.first": {"answer": "Sam"},
            "name.last": {"from_profile": "personal.full_name"},
            "contact.email": {"from_profile": "personal.email", "asks": ["e-mail address"]},
            "location.relocation": {"from_applicant": "relocation"},
        },
    )
    assert loaded["contact.email"].from_profile == "personal.email"
    assert loaded["contact.email"].asks == ("e-mail address",)
    assert loaded["location.relocation"].from_applicant == "relocation"


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ({"hobby.chess": {"answer": "Yes"}}, "unknown category 'hobby'"),
        ({"work_auth": {"answer": "Yes"}}, "<category>.<name>"),
        ({"work_auth.a.b": {"answer": "Yes"}}, "<category>.<name>"),
        ({"contact.email": {"answer": "sam@example.test"}}, "contact.email: contact entries may only use from_profile"),
        ({"contact.phone": {"from_profile": "personal.phone", "kind": "text"}}, "contact.phone: contact entries"),
        ({"contact.phone": {"asks": ["phone"]}}, "contact.phone: needs from_profile"),
        ({"work_auth.x": {"kind": "yes_no"}}, "work_auth.x: needs exactly one of answer, from_profile"),
        (
            {"work_auth.x": {"answer": "Yes", "from_applicant": "relocation"}},
            "work_auth.x: needs exactly one of answer, from_profile",
        ),
        ({"work_auth.x": {"answer": "Yes", "colour": "red"}}, "work_auth.x: unknown field 'colour'"),
        ({"work_auth.x": {"answer": "Yes", "kind": "boolean"}}, "work_auth.x: unknown kind 'boolean'"),
        ({"work_auth.x": {"answer": "Maybe", "kind": "yes_no"}}, "work_auth.x: a yes_no answer must be Yes or No"),
        ({"compensation.x": {"answer": "lots", "kind": "number_min"}}, "compensation.x: a number_min answer"),
        ({"work_auth.x": {"answer": ["Yes"]}}, "work_auth.x: answer must be text"),
        ({"work_auth.x": {"answer": ""}}, "work_auth.x: answer is empty"),
        ({"work_auth.x": {"answer": "Yes", "accept": "Y"}}, "work_auth.x: accept must be a list"),
        ({"work_auth.x": {"answer": "Yes", "asks": "work"}}, "work_auth.x: asks must be a list"),
        ({"work_auth.x": "Yes"}, "work_auth.x: must be a mapping"),
        ({"name.nick": {"from_profile": "personal.nickname"}}, "name.nick: from_profile 'personal.nickname' is not"),
        ({"location.x": {"from_applicant": "salary"}}, "location.x: from_applicant 'salary' is not"),
        ({"work_auth.x": {"answer": "Yes", "overrides": ["work_auth.y"]}}, "work_auth.x: overrides unknown key"),
        ({"work_auth.x": {"answer": "Yes", "aliases": ["X"]}}, "work_auth.x: unknown field 'aliases'"),
    ],
)
def test_answer_errors(tmp_path, entries, expected):
    message = answers_error(tmp_path, {**NAMES, **entries})
    assert expected in message


def test_contact_value_is_not_echoed(tmp_path):
    message = answers_error(tmp_path, {**NAMES, "contact.email": {"answer": "sam.synthetic@example.test"}})
    assert "sam.synthetic@example.test" not in message


def test_name_first_and_last_required(tmp_path):
    message = answers_error(tmp_path, {"name.first": {"answer": "Sam"}})
    assert "name.last is required" in message
    message = answers_error(tmp_path, {"work_auth.x": {"answer": "Yes"}})
    assert "name.first is required" in message and "name.last is required" in message


@pytest.mark.parametrize(
    "entries",
    [
        {"work_auth.a": {"answer": "Yes", "overrides": ["work_auth.a"]}},
        {
            "work_auth.a": {"answer": "Yes", "overrides": ["work_auth.b"]},
            "work_auth.b": {"answer": "No", "overrides": ["work_auth.a"]},
        },
        {
            "work_auth.a": {"answer": "Yes", "overrides": ["work_auth.b"]},
            "work_auth.b": {"answer": "No", "overrides": ["work_auth.c"]},
            "work_auth.c": {"answer": "No", "overrides": ["work_auth.a"]},
        },
    ],
)
def test_override_cycles(tmp_path, entries):
    assert "overrides form a cycle" in answers_error(tmp_path, {**NAMES, **entries})


def test_duplicate_keys(tmp_path):
    path = write(
        tmp_path / "answers.yaml",
        """
version: 1
entries:
  name.first: {answer: Sam}
  name.last: {answer: Synthetic}
  work_auth.x: {answer: "Yes"}
  work_auth.x: {answer: "No"}
""",
    )
    with pytest.raises(InvariantError, match="duplicate key 'work_auth.x'"):
        load_answers(path, PROFILE_FIELDS, APPLICANT_FIELDS)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"kind": "record", "school": "ESU", "degree": "MS", "start": "2025-08", "end": "2027-05"}, "needs major"),
        ({"kind": "record", "school": "ESU", "degree": "MS", "major": "Stats", "start": "2025-08"}, "needs end"),
        (
            {
                "kind": "record",
                "school": "ESU",
                "degree": "MS",
                "major": "Stats",
                "start": "a",
                "end": "b",
                "answer": "x",
            },
            "a record carries no answer",
        ),
        (
            {"kind": "record", "company": "Labs", "title": "Intern", "start": "2024-06", "end": "2024-09"},
            "an education record needs school, degree, major",
        ),
        (
            {
                "kind": "record",
                **{"school": "ESU", "degree": "MS", "major": "Stats", "start": "a", "end": "b"},
                "accept": ["MS"],
            },
            "accept of a record must map record fields to lists",
        ),
        (
            {
                "kind": "record",
                **{"school": "ESU", "degree": "MS", "major": "Stats", "start": "a", "end": "b"},
                "accept": {"gpa": ["3.6"]},
            },
            "accept names 'gpa'",
        ),
        ({"answer": "x", "school": "ESU"}, "unknown field 'school'"),
    ],
)
def test_record_errors(tmp_path, entry, expected):
    message = answers_error(tmp_path, {**NAMES, "education.ms": entry})
    assert "education.ms" in message and expected in message


def test_experience_record_needs_company_and_title(tmp_path):
    entry = {"kind": "record", "school": "ESU", "degree": "MS", "major": "Stats", "start": "a", "end": "b"}
    assert "an experience record needs company, title" in answers_error(tmp_path, {**NAMES, "experience.x": entry})
    entry = {"kind": "record", "answer": "x"}
    assert "a record belongs to education or experience" in answers_error(tmp_path, {**NAMES, "screening.x": entry})


def test_top_level_errors(tmp_path):
    path = write(tmp_path / "answers.yaml", {"version": 2, "entries": NAMES, "extra": 1})
    with pytest.raises(InvariantError) as error:
        load_answers(path, PROFILE_FIELDS, APPLICANT_FIELDS)
    assert "version must be 1" in str(error.value) and "unknown field 'extra'" in str(error.value)
    path = write(tmp_path / "bad.yaml", "version: 1\nentries: [\n")
    with pytest.raises(InvariantError, match="bad.yaml"):
        load_answers(path, PROFILE_FIELDS, APPLICANT_FIELDS)
    with pytest.raises(InvariantError, match="missing"):
        load_answers(tmp_path / "nope.yaml", PROFILE_FIELDS, APPLICANT_FIELDS)


def test_all_answer_errors_reported_together(tmp_path):
    message = answers_error(
        tmp_path,
        {
            "hobby.chess": {"answer": "Yes"},
            "contact.email": {"answer": "x"},
            "work_auth.a": {"answer": "Yes", "overrides": ["work_auth.a"]},
        },
    )
    for part in ("hobby", "contact.email", "overrides form a cycle", "name.first is required"):
        assert part in message


# --- job list ---------------------------------------------------------------------------------------------------


def job(**changes) -> dict:
    entry = {
        "id": "acme-ml-01",
        "url": "https://job-boards.greenhouse.io/acme/jobs/1",
        "company": "Acme Synthetic",
        "title": "ML Engineer",
        "resume": "main",
        "cover_letter": "letters/acme.txt",
    }
    entry.update(changes)
    return {key: value for key, value in entry.items() if value is not None}


def list_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "lists"
    write(folder / "letters/acme.txt", "Synthetic letter.")
    write(folder / "letters/acme.pdf", "%PDF synthetic")
    return folder


def test_list_loads(tmp_path):
    folder = list_dir(tmp_path)
    path = write(folder / "list.yaml", {"jobs": [job(), job(id="beta-02", cover_letter="none", revisit=True)]})
    jobs = load_list(path, {"main"}).jobs
    assert [entry.id for entry in jobs] == ["acme-ml-01", "beta-02"]
    assert Path(jobs[0].cover_letter) == (folder / "letters/acme.txt").resolve()
    assert jobs[0].revisit is False
    assert jobs[1].cover_letter == "none" and jobs[1].revisit is True


def test_list_absolute_letter(tmp_path):
    folder = list_dir(tmp_path)
    path = write(tmp_path / "elsewhere/list.yaml", {"jobs": [job(cover_letter=str(folder / "letters/acme.txt"))]})
    assert Path(load_list(path, {"main"}).jobs[0].cover_letter) == (folder / "letters/acme.txt").resolve()


@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        ([job(), job()], "duplicate id 'acme-ml-01'"),
        ([job(url="http://example.test/jobs/1")], "url must start with https://"),
        ([job(url="jobs.lever.co/x")], "url must start with https://"),
        ([job(resume="research")], "unknown resume 'research'"),
        ([job(cover_letter="letters/missing.txt")], "letters/missing.txt"),
        ([job(cover_letter="letters/acme.pdf")], "cover_letter must be 'none' or a .txt"),
        ([job(company=None)], "company"),
        ([job(colour="red")], "colour"),
        ([job(revisit="sometimes")], "revisit"),
        ([], "jobs"),
    ],
)
def test_list_errors(tmp_path, jobs, expected):
    folder = list_dir(tmp_path)
    path = write(folder / "list.yaml", {"jobs": jobs})
    with pytest.raises(InvariantError) as error:
        load_list(path, {"main"})
    assert "list.yaml" in str(error.value) and expected in str(error.value)


def test_list_letter_needs_pdf(tmp_path):
    folder = list_dir(tmp_path)
    (folder / "letters/acme.pdf").unlink()
    path = write(folder / "list.yaml", {"jobs": [job()]})
    with pytest.raises(InvariantError, match=r"acme\.pdf"):
        load_list(path, {"main"})


def test_list_errors_reported_together(tmp_path):
    folder = list_dir(tmp_path)
    path = write(folder / "list.yaml", {"jobs": [job(url="http://x"), job(id="b", resume="other")]})
    with pytest.raises(InvariantError) as error:
        load_list(path, {"main"})
    assert "https://" in str(error.value) and "unknown resume 'other'" in str(error.value)


# --- checklist --------------------------------------------------------------------------------------------------


def test_checklist_loads(tmp_path):
    path = write(
        tmp_path / "checklist.yaml",
        """
version: 1
items:
  - {id: no_hard_breaks, rule: No line breaks inside sentences., level: format}
  - {id: open_answers, rule: Specific open answers., level: guess, added: 2026-10-15, from: run-1 suggestion 2}
""",
    )
    items = load_checklist(path)
    assert [item.id for item in items] == ["no_hard_breaks", "open_answers"]
    assert items[1].added == "2026-10-15" and items[1].from_ == "run-1 suggestion 2"


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([{"id": "a", "rule": "r", "level": "format"}, {"id": "a", "rule": "r", "level": "format"}], "duplicate id"),
        ([{"id": "a", "rule": "r", "level": "fact"}], "level"),
        ([{"id": "a", "level": "format"}], "rule"),
        ([{"id": "a", "rule": "r", "level": "format", "colour": "red"}], "colour"),
    ],
)
def test_checklist_errors(tmp_path, items, expected):
    path = write(tmp_path / "checklist.yaml", {"version": 1, "items": items})
    with pytest.raises(InvariantError) as error:
        load_checklist(path)
    assert "checklist.yaml" in str(error.value) and expected in str(error.value)


# --- profile.json -----------------------------------------------------------------------------------------------


def test_profile_field_paths_never_return_values(tmp_path):
    profile = {
        "personal": {"email": "sam.synthetic@example.test", "phone": "555-0100", "portfolio_url": ""},
        "skills": {"languages": ["Python"]},
        "note": None,
    }
    path = write(tmp_path / "profile.json", json.dumps(profile))
    paths = profile_field_paths(path)
    assert paths == {"personal.email", "personal.phone", "skills.languages"}
    assert not any("example.test" in item or "555" in item for item in paths)


def test_profile_field_paths_errors(tmp_path):
    with pytest.raises(InvariantError, match="missing"):
        profile_field_paths(tmp_path / "profile.json")
    path = write(tmp_path / "profile.json", '{"personal": {"email": "sam.secret@example.test",}')
    with pytest.raises(InvariantError) as error:
        profile_field_paths(path)
    assert "profile.json" in str(error.value) and "sam.secret" not in str(error.value)


# --- project examples -------------------------------------------------------------------------------------------


def test_examples_load(tmp_path):
    profile_fields = profile_field_paths(EXAMPLES / "applicant/profile.example.json")
    loaded = load_answers(EXAMPLES / "applicant/answers.example.yaml", profile_fields, APPLICANT_FIELDS)
    assert {"name.first", "name.last", "contact.email"} <= set(loaded)
    assert load_checklist(EXAMPLES / "applicant/checklist.example.yaml")

    path = tmp_path / "form-fill-list.yaml"
    shutil.copy(EXAMPLES / "form-fill-list.example.yaml", path)
    for item in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]:
        if item["cover_letter"] != "none":
            letter = tmp_path / item["cover_letter"]
            write(letter, "Synthetic letter.")
            write(letter.with_suffix(".pdf"), "%PDF synthetic")
    assert len(load_list(path, {"main", "research"}).jobs) >= 2
