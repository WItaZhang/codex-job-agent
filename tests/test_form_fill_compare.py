"""Form-fill fact check (specs/form-fill.md 2.3, 4.4, 5.1, 5.2; slice-1 plan section 5): each field read back against
its answer-sheet entry, by kind. The 29 confirmed samples, the fill errors the user marked (decisions 15 and 16), and
the comparison rules. Contact values are compared as the saved pages show them: masked. All data here is SYNTHETIC."""

from pathlib import Path

import pytest
import yaml

from intent_job_agent.form_fill.compare import check_page, compare_part, compare_value
from intent_job_agent.form_fill.inputs import load_answers
from intent_job_agent.form_fill.models import AnswerEntry, ReadField

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


def test_record_otherwise_may_name_several_fallbacks(tmp_path):
    entry = dict(ERRORS["answers"]["education.beng_nru"])
    entry["otherwise"] = {"major": ["Computer Science", "Computer and Information Science"]}
    record = load(tmp_path, {"education.beng_nru": entry})["education.beng_nru"]
    for value in ("Computer Science", "Computer and Information Science"):
        assert compare_part(record, "major", value).result == "correct"
    assert compare_part(record, "major", "Information").result == "not_reviewed"


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


# --- review round 1 (decision 19) -------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["No Response", "No answer provided"])
@pytest.mark.parametrize("answer", ["Yes", "No"])
def test_a_yes_no_left_on_a_placeholder_is_missing_never_no(tmp_path, value, answer):
    answers = load(tmp_path, {"work_auth.sponsorship_now": {"answer": answer, "kind": "yes_no"}})
    v = compare_value(answers["work_auth.sponsorship_now"], value, control="select")
    assert (v.result, v.severity) == ("wrong", "missing")


CONTACTS = {
    "contact.email": {"from_profile": "personal.email"},
    "contact.phone": {"from_profile": "personal.phone"},
    "contact.address": {"from_profile": "personal.address"},
    "contact.city": {"from_profile": "personal.city"},
    "contact.linkedin": {"from_profile": "personal.linkedin_url"},
}


@pytest.mark.parametrize(
    ("key", "value", "want"),
    [
        ("contact.linkedin", "[linkedin]", "correct"),
        ("contact.linkedin", "[github]", "wrong"),
        ("contact.linkedin", "[linkedin]/details/experience", "wrong"),
        ("contact.city", "[city]", "correct"),
        ("contact.city", "[city], California, United States", "correct"),
        ("contact.city", "[address]", "wrong"),
        ("contact.city", "[address], [city]", "wrong"),
        ("contact.address", "[address] [city], CA [postal_code] United States of America", "correct"),
        ("contact.address", "[city], TX", "wrong"),
        ("contact.phone", "[phone] (Mobile)", "correct"),
        ("contact.phone", "[phone] [phone]", "wrong"),
        ("contact.email", "[email]", "correct"),
    ],
)
def test_each_contact_field_wants_its_own_placeholder(tmp_path, key, value, want):
    fields = PROFILE_FIELDS | {"personal.linkedin_url"}
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": {**NAMES, **CONTACTS}}), "utf-8")
    answers = load_answers(path, fields, set())
    assert compare_value(answers[key], value).result == want


@pytest.mark.parametrize(
    ("answer", "value", "want"),
    [
        ("TX", "Texas", "correct"),
        ("Texas", "TX", "correct"),
        ("United States", "United States of America", "correct"),
        ("United States", "USA", "correct"),
        ("TX", "Oregon", "wrong"),
        ("TX", "Lone Star", "not_reviewed"),
        ("Ely", "Ely, Cambridgeshire", "correct"),
        ("TX", "", "wrong"),
    ],
)
def test_contact_values_never_masked_compare_as_places(answer, value, want):
    entry = AnswerEntry(key="contact.state", from_profile="personal.province_state", answer=answer, kind="text")
    assert compare_value(entry, value).result == want


def test_a_record_otherwise_is_wrong_when_the_page_offers_the_original(sheet):
    options = ("Computer Science", "Geographic Information System", "Geography")
    v = compare_part(sheet["education.beng_nru"], "major", "Computer Science", options=options)
    assert (v.result, v.severity) == ("wrong", "fact")
    v = compare_part(sheet["education.beng_nru"], "major", "Computer Science", options=("Computer Science", "Art"))
    assert v.result == "correct"


def test_record_accept_and_otherwise_count_for_dates_and_city(tmp_path):
    entry = {
        "kind": "record",
        "company": "Acme",
        "title": "Analyst",
        "start": "2024-06",
        "end": "2025-06",
        "city": "Riverton",
        "accept": {"end": ["Present"]},
        "otherwise": {"city": ["Greater Riverton Area"]},
    }
    record = load(tmp_path, {"experience.acme": entry})["experience.acme"]
    assert compare_part(record, "end", "Present").result == "correct"
    assert compare_part(record, "range", "Jun 2024 - Present").result == "correct"
    assert compare_part(record, "city", "Greater Riverton Area").result == "correct"


@pytest.mark.parametrize("value", ["12-14-2026", "12/14/26", "Dec. 14, 2026", "December 14th, 2026"])
def test_more_date_spellings(tmp_path, value):
    entry = load(tmp_path, {"availability.start": {"answer": "2026-12-14", "kind": "date"}})["availability.start"]
    assert compare_value(entry, value).result == "correct"


def test_sept_is_september(tmp_path):
    entry = load(tmp_path, {"availability.start": {"answer": "2026-09-14", "kind": "date"}})["availability.start"]
    assert compare_value(entry, "Sept 14, 2026").result == "correct"


def test_a_value_the_code_cannot_read_is_not_reviewed_never_wrong(sheet):
    assert compare_value(sheet["availability.start_date"], "the first Monday after graduation").result == "not_reviewed"
    assert compare_value(sheet["availability.grad_month"], "end of next year").result == "not_reviewed"
    assert compare_part(sheet["education.ms_esu"], "range", "since my second year").result == "not_reviewed"


@pytest.mark.parametrize("value", ["09/2025-12/2026", "Sep 2025-Dec 2026", "2025-09 - 2026-12"])
def test_ranges_without_spaces(sheet, value):
    assert compare_part(sheet["education.ms_esu"], "range", value).result == "correct"


def test_a_month_with_a_note_in_brackets(sheet):
    assert compare_value(sheet["availability.grad_month"], "Dec 2026 (expected)").result == "correct"


@pytest.mark.parametrize("value", ["95000USD", "95kUSD", "USD 95,000"])
def test_number_min_with_a_currency_stuck_on(sheet, value):
    assert compare_value(sheet["compensation.expected"], value).result == "correct"


def test_two_records_at_one_school_are_both_checked(tmp_path):
    ms = {k: v for k, v in ERRORS["answers"]["education.beng_nru"].items() if k not in ("accept", "otherwise")}
    ms.update({"degree": "Master of Science", "start": "2025-09", "end": "2027-05"})
    answers = load(tmp_path, {"education.ms_nru": ms, "education.beng_nru": ERRORS["answers"]["education.beng_nru"]})
    rows = [
        ("School", "Northern River University", "review"),
        ("Degree", "Master of Science", "review"),
        ("Dates attended", "9 / 2025 - 12 / 2030", "review"),
        ("School", "Northern River University", "review"),
        ("Degree", "Bachelor of Engineering", "review"),
        ("Dates attended", "9 / 2021 - 6 / 2025", "review"),
    ]
    results = check_page(page(rows), answers)
    got = {r.field_id: (r.key, r.result) for r in results}
    assert got["p1.3"] == ("education.ms_nru", "wrong") and got["p1.6"] == ("education.beng_nru", "correct")
    assert all(r.field_id for r in results), "no segment reported missing"


def test_an_empty_school_is_missing_inside_its_segment(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    rows = [("School or University", "No Response", "review"), ("Degree", "Master of Science", "review")]
    got = by_id(check_page(page(rows), answers))
    assert (got["p1.1"].key, got["p1.1"].result, got["p1.1"].severity) == ("education.ms_esu", "wrong", "missing")
    assert got["p1.2"].result == "correct"


def test_a_contact_fields_unmasked_value_is_hidden_in_results(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    rows = [("Mobile phone number", "5550109999", "text"), ("Email", "[other email]", "text")]
    got = by_id(check_page(page(rows), answers))
    assert got["p1.1"].value == "[其他值，已隐去]" and got["p1.1"].result == "wrong"
    assert got["p1.2"].value == "[other email]"


def test_a_missing_option_is_named_with_the_choices(sheet):
    v = compare_value(sheet["eeo.race"], "", options=("Select...", "Asian", "White"))
    assert v.result == "no_answer" and "Asian、White" in v.note


# --- review round 2 (decision 20) -------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["No Selection", "No answer", "Choose..."])
def test_a_yes_no_left_on_other_placeholders_is_missing(tmp_path, value):
    answers = load(tmp_path, {"work_auth.sponsorship_now": {"answer": "No", "kind": "yes_no"}})
    v = compare_value(answers["work_auth.sponsorship_now"], value, control="select")
    assert (v.result, v.severity) == ("wrong", "missing")


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("03/09/2025 - 07/12/2026", "wrong"),
        ("Mar 1, 2025 - Jul 15, 2026", "wrong"),
        ("03-2025 - 09-2026", "wrong"),
        ("03.2025 - 09.2026", "wrong"),
        ("09/01/2025 - 12/01/2026", "correct"),
        ("09-2025 - 12-2026", "correct"),
        ("Sep 1, 2025 to Dec 15, 2026", "correct"),
        ("2025-09-2026-12", "correct"),
    ],
)
def test_ranges_parse_each_half_as_a_whole(sheet, value, want):
    assert compare_part(sheet["education.ms_esu"], "range", value).result == want


ADDRESS = {**CONTACTS, "contact.github": {"from_profile": "personal.github_url"}}


def address_sheet(tmp_path):
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": {**NAMES, **ADDRESS}}), "utf-8")
    return load_answers(path, PROFILE_FIELDS | {"personal.linkedin_url", "personal.github_url"}, set())


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("[address], [city], TX [postal_code], USA", "correct"),
        ("[address], Springfield, IL 62701", "wrong"),
        ("[address] 99999", "wrong"),
        ("[address], Apt 5", "not_reviewed"),
    ],
)
def test_an_address_may_only_hold_the_applicants_own_parts(tmp_path, value, want):
    assert compare_value(address_sheet(tmp_path)["contact.address"], value).result == want


def test_a_profile_field_sharing_its_value_accepts_the_other_placeholder(tmp_path):
    entry = address_sheet(tmp_path)["contact.github"].model_copy(update={"accept": ("[linkedin]",)})
    assert compare_value(entry, "[linkedin]").result == "correct"
    assert compare_value(entry, "[github]").result == "correct"
    assert compare_value(address_sheet(tmp_path)["contact.github"], "[linkedin]").result == "wrong"


def test_a_city_named_like_its_state_may_repeat(tmp_path):
    assert compare_value(address_sheet(tmp_path)["contact.city"], "[city], [city], United States").result == "correct"


@pytest.mark.parametrize(("answer", "want"), [("Yes", ("correct", "none")), ("No", ("wrong", "fact"))])
def test_a_ticked_box_says_yes_whatever_its_label(tmp_path, answer, want):
    entry = load(tmp_path, {"screening.consent": {"answer": answer, "kind": "yes_no"}})["screening.consent"]
    v = compare_value(entry, "I agree to be contacted", control="checkbox")
    assert (v.result, v.severity) == want


def test_a_yes_no_whose_page_offers_neither_yes_nor_no(tmp_path):
    entry = load(tmp_path, {"work_auth.ok": {"answer": "Yes", "kind": "yes_no"}})["work_auth.ok"]
    options = ("I am authorized to work in the US", "I am not authorized to work in the US")
    v = compare_value(entry, "", options=options, control="radio")
    assert v.result == "no_answer" and "I am authorized to work in the US" in v.note
    declined = load(tmp_path, {"eeo.x": {"answer": "No", "kind": "yes_no", "otherwise": "I don't wish to answer"}})
    assert compare_value(declined["eeo.x"], "I don't wish to answer").result == "correct"


def test_a_placeholder_never_equals_a_plain_word(tmp_path):
    entry = load(tmp_path, {"screening.heard": {"answer": "LinkedIn", "kind": "text"}})["screening.heard"]
    assert compare_value(entry, "[linkedin]").result == "wrong"
    assert compare_value(entry, "LinkedIn").result == "correct"


@pytest.mark.parametrize("value", ["Riverton NY", "Riverton (NY)", "Riverton · United States"])
def test_a_city_with_the_state_but_no_comma_is_format(sheet, value):
    v = compare_part(sheet["education.beng_nru"], "city", value)
    assert (v.result, v.severity) == ("wrong", "format")


@pytest.mark.parametrize(
    ("value", "want"), [("90000.", "correct"), ("80k~100k", "correct"), ("between 80k and 100k", "correct")]
)
def test_number_min_more_spellings(sheet, value, want):
    assert compare_value(sheet["compensation.expected"], value).result == want


def test_a_required_field_left_empty_without_an_answer_is_missing(tmp_path):
    answers = load(tmp_path, {})
    f = ReadField(
        id="p1.1",
        question="Have you worked for Acme?",
        value="",
        control="radio",
        origin="control",
        options=("Yes", "No"),
        required=True,
    )
    (r,) = check_page([f], answers)
    assert (r.result, r.severity) == ("no_answer", "missing")


def test_contact_fields_on_an_old_page_are_not_reviewed(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    rows = [("Email", "[email]", "text"), ("First name", "Sam", "text")]
    got = by_id(check_page(page(rows), answers, contacts=False))
    assert got["p1.1"].result == "not_reviewed" and "旧的打码格式" in got["p1.1"].note
    assert got["p1.2"].result == "correct"
    (empty,) = check_page(page([("Location", "", "combobox")]), answers, contacts=False)
    assert (empty.key, empty.result, empty.severity) == ("contact.city", "wrong", "missing")


# --- review round 3 (decision 21) -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "value", "want"),
    [
        ("Yes", "No", ("wrong", "fact")),
        ("No", "No", ("correct", "none")),
        ("No", "", ("wrong", "missing")),
        ("Yes", "Yes", ("correct", "none")),
    ],
)
def test_a_yes_no_checkbox_group_is_read_by_the_ticked_label(tmp_path, answer, value, want):
    entry = load(tmp_path, {"work_auth.x": {"answer": answer, "kind": "yes_no"}})["work_auth.x"]
    v = compare_value(entry, value, options=("Yes", "No"), control="checkbox")
    assert (v.result, v.severity) == want


def test_a_yes_no_otherwise_is_wrong_when_the_page_offers_the_answer(tmp_path):
    entry = load(tmp_path, {"eeo.x": {"answer": "No", "kind": "yes_no", "otherwise": "I don't wish to answer"}})[
        "eeo.x"
    ]
    options = ("Yes, I have a disability", "No, I do not have a disability", "I don't wish to answer")
    v = compare_value(entry, "I don't wish to answer", options=options, control="radio")
    assert (v.result, v.severity) == ("wrong", "fact")


def test_a_yes_no_filled_when_no_option_is_readable_is_a_guess(tmp_path):
    entry = load(tmp_path, {"work_auth.x": {"answer": "No", "kind": "yes_no"}})["work_auth.x"]
    options = ("I will require sponsorship now or in the future", "I will not require sponsorship")
    v = compare_value(entry, options[0], options=options, control="select")
    assert (v.result, v.severity) == ("no_answer", "guess")


def test_a_third_option_on_a_yes_no_page_is_wrong(tmp_path):
    entry = load(tmp_path, {"screening.us_person": {"answer": "No", "kind": "yes_no"}})["screening.us_person"]
    v = compare_value(entry, "Prefer not to answer", options=("Yes", "No", "Prefer not to answer"), control="radio")
    assert (v.result, v.severity) == ("wrong", "fact")


def test_a_record_part_whose_value_the_page_does_not_offer(sheet):
    options = ("Bachelor's Degree", "Master's Degree", "Doctor of Philosophy (Ph.D.)")
    v = compare_part(sheet["education.ms_esu"], "degree", "Master's Degree", options=options)
    assert (v.result, v.severity) == ("no_answer", "guess") and "Master's Degree" in v.note
    assert compare_part(sheet["education.ms_esu"], "degree", "", options=options).result == "no_answer"


def test_the_city_must_be_in_the_applicants_own_state(tmp_path):
    entry = address_sheet(tmp_path)["contact.city"].model_copy(update={"aliases": ("ME", "United States")})
    assert compare_value(entry, "[city], Maine, United States").result == "correct"
    assert compare_value(entry, "[city], Oregon, United States").result == "wrong"
    assert compare_value(entry, "[city], OR").result == "wrong"


@pytest.mark.parametrize(
    "value",
    [
        "[address], [city], New York [postal_code]",
        "[address], [city], North Carolina [postal_code], United States",
        "[address], [city], TX [postal_code], U.S.",
    ],
)
def test_multi_word_states_and_us_spellings_in_an_address(tmp_path, value):
    assert compare_value(address_sheet(tmp_path)["contact.address"], value).result == "correct"


def test_a_contact_fields_unmasked_rest_is_hidden(tmp_path):
    answers = load(tmp_path, {**ERRORS["answers"], "contact.address": {"from_profile": "personal.address"}})
    (r,) = check_page(page([("Address", "742 Evergreen Terrace, Unit 3, [city], CA [postal_code]", "text")]), answers)
    assert "Evergreen" not in r.value and "[city]" in r.value and "[其他值，已隐去]" in r.value


@pytest.mark.parametrize("value", ["USD 75,000 - USD 100,000", "75,000 USD - 100,000 USD", "$75,000/yr - $100,000/yr"])
def test_salary_ranges_with_currency_words(sheet, value):
    entry = sheet["compensation.expected"].model_copy(update={"answer": "80000"})
    assert compare_value(entry, value).result == "correct"


def test_a_required_empty_field_lists_its_choices(tmp_path):
    f = ReadField(
        id="p1.1",
        question="Have you previously been employed by Acme Corporation or any subsidiary?",
        value="",
        control="radio",
        origin="control",
        options=("Yes", "No", "Unsure"),
        required=True,
    )
    (r,) = check_page([f], load(tmp_path, {}))
    assert "Yes、No、Unsure" in r.note


# --- review round 4 (decision 22) -------------------------------------------------------------------------------


def test_date_and_city_drop_downs_in_a_segment_are_compared_by_their_own_rule(sheet):
    years = tuple(str(y) for y in range(2018, 2031))
    assert compare_part(sheet["education.ms_esu"], "start", "2025", options=years).result == "correct"
    assert compare_part(sheet["education.ms_esu"], "end", "2027", options=years).result == "wrong"
    months = ("Sep 2025", "Dec 2026", "May 2027")
    assert compare_part(sheet["education.ms_esu"], "end", "Dec 2026", options=months).result == "correct"


@pytest.mark.parametrize(
    ("places", "value", "want"),
    [
        (("", "United States"), "[city], CA, United States", "correct"),
        (("CA", ""), "[city], California, USA", "correct"),
        (("CA", "United States"), "[city], England, United Kingdom", "not_reviewed"),
        (("CA", "United States"), "[city], Ontario", "not_reviewed"),
        (("CA", "United States"), "[city] CA", "correct"),
        (("CA", "United States"), "[city], CA [postal_code]", "correct"),
        (("CA", "United States"), "[city] - Texas", "wrong"),
    ],
)
def test_the_city_rule_with_partial_or_foreign_places(tmp_path, places, value, want):
    entry = address_sheet(tmp_path)["contact.city"].model_copy(update={"aliases": places})
    assert compare_value(entry, value).result == want


def test_a_third_option_while_the_page_offers_the_original_major(tmp_path):
    entry = {**ERRORS["answers"]["education.beng_nru"]}
    record = load(tmp_path, {"education.beng_nru": entry})["education.beng_nru"]
    options = ("Biology", "Geographic Information System", "Statistics", "Computer Science")
    v = compare_part(record, "major", "Statistics", options=options)
    assert (v.result, v.severity) == ("wrong", "fact")


def test_an_empty_part_whose_original_the_page_lacks_expects_the_otherwise(sheet):
    v = compare_part(sheet["education.beng_nru"], "major", "", options=("Biology", "Computer Science"))
    assert (v.result, v.severity, v.expected) == ("wrong", "missing", "Computer Science")


def test_a_yes_no_group_with_both_boxes_ticked_is_wrong(tmp_path):
    entry = load(tmp_path, {"work_auth.x": {"answer": "Yes", "kind": "yes_no"}})["work_auth.x"]
    v = compare_value(entry, "Yes; No", options=("Yes", "No"), control="checkbox")
    assert (v.result, v.severity) == ("wrong", "fact")


def test_an_address_naming_another_state_is_wrong(tmp_path):
    entry = address_sheet(tmp_path)["contact.address"].model_copy(update={"aliases": ("CA", "United States")})
    assert compare_value(entry, "[address], [city], CA [postal_code]").result == "correct"
    assert compare_value(entry, "[address], [city], NV [postal_code]").result == "wrong"
    assert compare_value(entry, "[address], New York, NY").result == "wrong"


def test_state_abbreviations_stay_visible_in_contact_fields(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    (r,) = check_page(page([("City", "[city], TX, USA", "text")]), answers)
    assert r.value == "[city], TX, USA"


def test_a_required_empty_field_inside_a_segment_is_missing(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    rows = [
        ReadField(id="p1.1", question="Company", value="Vista Video Inc.", control="text", origin="control"),
        ReadField(
            id="p1.2", question="Role Description", value="", control="textarea", origin="control", required=True
        ),
    ]
    got = by_id(check_page(rows, answers))
    assert (got["p1.2"].result, got["p1.2"].severity) == ("no_answer", "missing")


# --- decision 23: spellings that mean the same ------------------------------------------------------------------


def test_a_trailing_bracket_is_left_out_when_the_whole_option_does_not_match(tmp_path):
    answers = load(
        tmp_path,
        {
            "eeo.race": {
                "answer": "Decline to self-identify",
                "kind": "choice",
                "accept": ["I prefer not to disclose"],
                "asks": ["ethnicity"],
            }
        },
    )
    picked = "I prefer not to disclose (United States of America)"
    options = ("Asian (United States of America)", picked)
    row = by_id(check_page([field("Ethnicity", picked, "select", options)], answers))["p1.1"]
    assert (row.key, row.result, row.severity) == ("eeo.race", "correct", "none")
    other = by_id(check_page([field("Ethnicity", options[0], "select", options)], answers))["p1.1"]
    assert (other.result, other.severity) == ("wrong", "fact")


HOW_HEARD = {
    "screening.how_heard": {
        "answer": "Company website",
        "kind": "choice",
        "accept": ["Company career site", "Careers page"],
        "asks": ["how did you hear"],
    }
}


def test_the_company_careers_site_counts_as_the_company_website(tmp_path):
    answers = load(tmp_path, HOW_HEARD)
    rows = [field("How Did You Hear About Us?", "Acme Synthetic Careers Website", "select")]
    row = by_id(check_page(rows, answers, company="Acme Synthetic"))["p1.1"]
    assert (row.key, row.result) == ("screening.how_heard", "correct")


@pytest.mark.parametrize(
    ("company", "value"),
    [
        ("Acme Synthetic, Inc.", "Acme Synthetic Careers Website"),  # legal words left out of the name
        ("Acme Synthetic AI", "Acme Synthetic Careers Page"),  # a shorter name from the end
        ("Acme Synthetic", "Acme Synthetic Careers Website (Workday)"),  # a bracket after the site
    ],
)
def test_company_name_variants_still_count_as_the_company_website(tmp_path, company, value):
    answers = load(tmp_path, {"screening.how_heard": {"answer": "Company website", "kind": "choice", "asks": ["how"]}})
    row = by_id(check_page([field("How did you hear about this job?", value, "select")], answers, company=company))
    assert row["p1.1"].result == "correct"


def test_a_bracket_that_tells_options_apart_is_kept(tmp_path):
    """Review round (decision 23): only a trailing place in brackets is left out, so "(in progress)" and
    "(completed)" stay different, and picking `otherwise` is still right when the plain answer is not offered."""
    answers = load(
        tmp_path,
        {"education.level": {"answer": "Master's Degree", "kind": "choice", "otherwise": "Other", "asks": ["level"]}},
    )
    options = ("Master's Degree (in progress)", "Master's Degree (completed)", "Other")
    picked = by_id(check_page([field("Education level", options[0], "select", options)], answers))["p1.1"]
    assert picked.result != "correct"
    other = by_id(check_page([field("Education level", "Other", "select", options)], answers))["p1.1"]
    assert other.result == "correct"


def test_otherwise_with_a_trailing_place_counts(tmp_path):
    answers = load(
        tmp_path,
        {
            "eeo.hispanic_latino": {
                "answer": "Decline to self-identify",
                "kind": "choice",
                "otherwise": "No",
                "asks": ["hispanic"],
            }
        },
    )
    options = ("Yes (United States of America)", "No (United States of America)")
    row = by_id(check_page([field("Are you Hispanic or Latino?", options[1], "select", options)], answers))["p1.1"]
    assert row.result == "correct"


@pytest.mark.parametrize("value", ["LinkedIn Careers Page", "Beta Health Careers Website"])
def test_another_sites_careers_page_is_not_the_company_website(tmp_path, value):
    answers = load(tmp_path, HOW_HEARD)
    row = by_id(check_page([field("How Did You Hear About Us?", value, "select")], answers, company="Acme Synthetic"))
    assert (row["p1.1"].result, row["p1.1"].severity) == ("wrong", "fact")


def test_a_date_with_an_empty_part_is_a_gap_not_a_match():
    """Review #14: "?/2024" (the month empty) is not the year alone."""
    from intent_job_agent.form_fill.models import ReadField

    entry = AnswerEntry(key="availability.start", answer="2026-12-14", kind="date", asks=("start date",))
    fields = (ReadField(id="p1.1", question="Start date", origin="control", control="date", value="?/2026"),)
    (result,) = check_page(fields, {"availability.start": entry})
    assert (result.result, result.severity) == ("wrong", "missing")
