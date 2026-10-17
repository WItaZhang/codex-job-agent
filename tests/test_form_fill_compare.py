"""Form-fill fact check (specs/form-fill.md 2.3, 4.4, 5.1, 5.2; slice-1 plan section 5): each field read back against
its answer-sheet entry, by kind. The 29 confirmed samples, the fill errors the user marked (decisions 15 and 16), and
the comparison rules. Contact values are compared as the saved pages show them: masked. All data here is SYNTHETIC."""

from pathlib import Path

import pytest
import yaml

from intent_job_agent.form_fill.compare import check_page, compare_part, compare_value
from intent_job_agent.form_fill.inputs import load_answers
from intent_job_agent.form_fill.models import ReadField

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/form_fill"
PROFILE_FIELDS = {"personal.full_name", "personal.email", "personal.phone", "personal.city", "personal.address"}
NAMES = {"name.first": {"answer": "Sam", "kind": "text"}, "name.last": {"answer": "Synthetic", "kind": "text"}}


def fixture(name: str) -> dict:
    data = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    return data


WORK_AUTH = fixture("samples_work_auth.yaml")
PROFILE = fixture("samples_profile.yaml")
ERRORS = fixture("samples_fill_errors.yaml")


def load(tmp_path: Path, entries: dict) -> dict:
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": {**NAMES, **entries}}, allow_unicode=True), "utf-8")
    return load_answers(path, PROFILE_FIELDS, {"relocation"})


def field(question: str, value: str, control: str = "text", options=(), n: int = 1) -> ReadField:
    origin = "review_text" if control == "review" else "control"
    return ReadField(id=f"p1.{n}", question=question, value=value, control=control, origin=origin, options=options)


def page(rows) -> tuple[ReadField, ...]:
    return tuple(field(q, v, c, n=n) for n, (q, v, c) in enumerate(rows, start=1))


def by_id(results) -> dict:
    return {r.field_id: r for r in results if r.field_id}


# --- the 29 confirmed samples -----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", WORK_AUTH["cases"], ids=lambda c: c["question"][:50])
def test_work_auth_samples_are_right_with_their_answer_and_wrong_with_the_other(tmp_path, case):
    answers = load(tmp_path, WORK_AUTH["answers"])
    options = tuple(case["options"])
    right = by_id(check_page([field(case["question"], case["expect"], "radio", options)], answers))["p1.1"]
    assert (right.key, right.result, right.severity) == (case["entry"], "correct", "none")
    other = next(option for option in options if option != case["expect"])
    wrong = by_id(check_page([field(case["question"], other, "radio", options)], answers))["p1.1"]
    assert (wrong.result, wrong.severity) == ("wrong", "fact")


@pytest.mark.parametrize("case", [c for c in PROFILE["cases"] if "record" not in c], ids=lambda c: c["question"][:50])
def test_profile_samples_give_the_confirmed_result(tmp_path, case):
    answers = load(tmp_path, PROFILE["answers"])
    got = by_id(
        check_page([field(case["question"], case["filled"], "select", tuple(case.get("options", ())))], answers)
    )
    assert got["p1.1"].key == case["entry"]
    assert got["p1.1"].result == case["result"]
    if case["result"] == "wrong":
        assert got["p1.1"].severity == "fact"


RECORD_LABELS = {"school": "School", "degree": "Degree", "major": "Field of Study", "start": "From", "end": "To"}


@pytest.mark.parametrize("case", [c for c in PROFILE["cases"] if "record" in c], ids=lambda c: c["note"][:40])
def test_profile_record_samples_give_the_confirmed_result(tmp_path, case):
    answers = load(tmp_path, PROFILE["answers"])
    rows = [(RECORD_LABELS[name], value, "review") for name, value in case["record"].items()]
    results = [r.result for r in check_page(page(rows), answers)]
    if case["result"] == "correct":
        assert results == ["correct"] * 5
    else:
        assert results == ["correct", "correct", "correct", "correct", "wrong"], "only the end month differs"


# --- the fill errors of decisions 15 and 16 ---------------------------------------------------------------------


@pytest.mark.parametrize("sample", ERRORS["pages"], ids=lambda p: p["name"])
def test_fill_error_pages_give_the_expected_results(tmp_path, sample):
    answers = load(tmp_path, ERRORS["answers"])
    results = check_page(page(sample["fields"]), answers)
    got = {r.field_id: (r.key, r.result, r.severity) for r in results if r.field_id}
    want = {fid: (e["key"], e["result"], e.get("severity", "none")) for fid, e in sample["expect"].items()}
    assert got == want
    missing = [(r.key, r.result, r.severity) for r in results if not r.field_id]
    assert missing == [(key, "wrong", "missing") for key in sample["missing"]]


def test_wrong_facts_name_the_expected_value(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    results = by_id(check_page(page(ERRORS["pages"][0]["fields"]), answers))
    assert results["p1.17"].expected == "Lumen Search (China) Co., Ltd."
    assert results["p1.19"].expected == "Hangzhou"
    assert results["p1.29"].expected == "2025-09 – 2026-12"
    assert "标准答案里没有" in results["p1.11"].note
    assert results["p1.35"].expected == "Yes"


def test_contact_fields_expect_the_placeholder_never_a_value(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    results = by_id(check_page(page(ERRORS["pages"][3]["fields"]), answers))
    assert results["p1.1"].expected == "[email]" and results["p1.4"].expected == "[phone]"


# --- comparison rules -------------------------------------------------------------------------------------------


@pytest.fixture
def sheet(tmp_path):
    return load(
        tmp_path,
        {
            **ERRORS["answers"],
            "eeo.hispanic_latino": {
                "answer": "Decline to self-identify",
                "kind": "choice",
                "accept": ["Prefer not to say", "I don't wish to answer"],
                "otherwise": "No",
                "asks": ["hispanic or latino"],
            },
            "eeo.race": {"answer": "Decline to self-identify", "kind": "choice", "accept": ["Prefer not to say"]},
            "work_auth.authorized_us": {"answer": "Yes", "kind": "yes_no", "accept": ["I am authorized to work"]},
            "screening.newsletter": {"answer": "No", "kind": "yes_no"},
            "compensation.expected": {"answer": "90000", "kind": "number_min"},
            "availability.start_date": {"answer": "2027-06-01", "kind": "date"},
            "availability.grad_month": {"answer": "2026-12", "kind": "month"},
            "screening.portfolio": {"answer": "https://www.example.test/u/sam", "kind": "url"},
            "education.gpa": {"answer": "3.6", "kind": "text", "accept": ["3.6/4.0"]},
        },
    )


def verdict(entry, value, **kw):
    v = compare_value(entry, value, **kw)
    return (v.result, v.severity)


@pytest.mark.parametrize(
    ("value", "options", "want"),
    [
        ("Prefer not to say", ("Yes", "No", "Prefer not to say"), ("correct", "none")),
        ("No", ("Yes", "No"), ("correct", "none")),
        ("No", ("Yes", "No", "I don't wish to answer"), ("wrong", "fact")),
        ("No", (), ("correct", "none")),
        ("", ("Yes", "No", "Prefer not to say"), ("wrong", "missing")),
    ],
    ids=["decline", "otherwise-without-decline", "otherwise-with-decline", "review-text-otherwise", "empty"],
)
def test_choice_with_otherwise(sheet, value, options, want):
    assert verdict(sheet["eeo.hispanic_latino"], value, options=options) == want


def test_a_choice_without_its_option_and_without_otherwise_has_no_answer(sheet):
    options = ("Asian", "White", "Two or more races")
    assert verdict(sheet["eeo.race"], "", options=options) == ("no_answer", "none")
    assert verdict(sheet["eeo.race"], "Asian", options=options) == ("no_answer", "guess"), "the agent guessed"


@pytest.mark.parametrize(
    ("value", "control", "key", "want"),
    [
        ("Yes", "radio", "work_auth.authorized_us", ("correct", "none")),
        ("Yes, I am", "radio", "work_auth.authorized_us", ("correct", "none")),
        ("No", "radio", "work_auth.authorized_us", ("wrong", "fact")),
        ("I am authorized to work", "radio", "work_auth.authorized_us", ("correct", "none")),
        ("I hold a valid permit", "radio", "work_auth.authorized_us", ("no_answer", "none")),
        ("", "radio", "work_auth.authorized_us", ("wrong", "missing")),
        ("", "checkbox", "screening.newsletter", ("correct", "none")),
        ("Yes", "checkbox", "screening.newsletter", ("wrong", "fact")),
        ("", "checkbox", "screening.contact_consent", ("wrong", "missing")),
    ],
)
def test_yes_no(sheet, value, control, key, want):
    assert verdict(sheet[key], value, control=control) == want


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("95k", "correct"),
        ("$90,000", "correct"),
        ("$95,000 - $110,000", "correct"),
        ("$80k–$100k", "correct"),
        ("$100k+", "correct"),
        ("$70k", "wrong"),
        ("$60k-$80k", "wrong"),
    ],
)
def test_number_min(sheet, value, want):
    assert compare_value(sheet["compensation.expected"], value).result == want


@pytest.mark.parametrize(
    "value", ["2027-06-01", "06/01/2027", "6/1/2027", "June 1, 2027", "Jun 1, 2027", "1 June 2027"]
)
def test_date_formats(sheet, value):
    assert compare_value(sheet["availability.start_date"], value).result == "correct"


@pytest.mark.parametrize("value", ["2026-12", "12/2026", "12 / 2026", "Dec 2026", "Dec. 2026", "December 2026"])
def test_month_formats(sheet, value):
    assert compare_value(sheet["availability.grad_month"], value).result == "correct"


def test_month_against_a_year_only_value_compares_the_year(sheet):
    assert compare_value(sheet["availability.grad_month"], "2026").result == "correct"
    assert compare_value(sheet["availability.grad_month"], "2027").result == "wrong"


def test_url_and_text(sheet):
    assert compare_value(sheet["screening.portfolio"], "example.test/u/sam/").result == "correct"
    assert compare_value(sheet["screening.portfolio"], "https://example.test/u/other").result == "wrong"
    assert compare_value(sheet["education.gpa"], "3.6 / 4.0").result == "correct"
    assert compare_value(sheet["name.first"], " sam ").result == "correct"


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("[email]", ("correct", "none")),
        ("[other email]", ("wrong", "fact")),
        ("[email], [other email]", ("wrong", "fact")),
        ("", ("wrong", "missing")),
        ("Select One", ("wrong", "missing")),
    ],
)
def test_contact_email_by_placeholder(sheet, value, want):
    assert verdict(sheet["contact.email"], value) == want


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("Riverton", ("correct", "none")),
        ("riverton", ("correct", "none")),
        ("Riverton, China", ("wrong", "format")),
        ("Riverton, NY, United States", ("wrong", "format")),
        ("河城", ("wrong", "fact")),
        ("Springfield", ("wrong", "fact")),
        ("N/A", ("wrong", "missing")),
        ("", ("wrong", "missing")),
    ],
)
def test_record_city(sheet, value, want):
    v = compare_part(sheet["education.beng_nru"], "city", value)
    assert (v.result, v.severity) == want


@pytest.mark.parametrize(
    ("key", "value", "want"),
    [
        ("education.beng_nru", "Geographic Information System", ("correct", "none")),
        ("education.beng_nru", "Remote Sensing Science and Technology", ("correct", "none")),
        ("education.beng_nru", "Computer Science", ("correct", "none")),
        ("education.beng_nru", "Geography", ("not_reviewed", "none")),
        ("education.beng_nru", "No Response", ("wrong", "missing")),
        ("education.beng_nru", "Select One", ("wrong", "missing")),
        ("education.beng_nru", "", ("wrong", "missing")),
        ("education.ms_esu", "Computer Science", ("wrong", "fact")),
    ],
    ids=["answer", "accept", "otherwise", "closest-or-not", "no-response", "select-one", "empty", "no-otherwise"],
)
def test_record_major_with_and_without_otherwise(sheet, key, value, want):
    v = compare_part(sheet[key], "major", value)
    assert (v.result, v.severity) == want


@pytest.mark.parametrize(
    ("part", "value", "want"),
    [
        ("school", "ESU", "correct"),
        ("school", "example state university", "correct"),
        ("degree", "M.S.", "correct"),
        ("degree", "Bachelor of Science", "wrong"),
        ("start", "09/2025", "correct"),
        ("start", "2025", "correct"),
        ("end", "Present", "wrong"),
        ("range", "9 / 2025 - 12 / 2026", "correct"),
        ("range", "Sep 2025 – Dec 2026", "correct"),
        ("range", "9 / 2025 - 3 / 2027", "wrong"),
        ("other", "Teaching assistant for Stats 101", "not_reviewed"),
        ("other", "", "no_answer"),
    ],
)
def test_record_parts(sheet, part, value, want):
    assert compare_part(sheet["education.ms_esu"], part, value).result == want
