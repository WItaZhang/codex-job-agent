"""Form-fill redaction (specs/form-fill.md 5.6; slice-1 plan, check 3): contact details and the password are masked
at write time, and a scan of written files finds any that leaked. All values are SYNTHETIC and invented."""

import pytest

from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill.redact import KINDS, Redactor, total_hits

PROFILE = {  # ApplyPilot's profile format, synthetic values
    "personal": {
        "full_name": "Robin Q Synthetic",
        "preferred_name": "Robin",
        "email": "Robin.Synthetic@example.test",
        "password": "Synth-Pass-4417",
        "phone": "(555) 010-2468",
        "address": "742 Evergreen Street",
        "city": "Springfieldia",
        "province_state": "WA",
        "country": "United States",
        "postal_code": "98765",
        "linkedin_url": "https://www.linkedin.com/in/robin-synthetic/",
        "github_url": "github.com/robinsynth",
        "portfolio_url": "",
        "website_url": "http://robin.example.test",
    }
}


@pytest.fixture
def redactor():
    return Redactor.from_profile(PROFILE)


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Email: Robin.Synthetic@example.test.", "Email: [email]."),
        ("robin.synthetic@EXAMPLE.TEST", "[email]"),
        ("mailto:robin.synthetic%40example.test", "mailto:[email]"),
        ("call 5550102468", "call [phone]"),
        ("call 15550102468", "call [phone]"),
        ("call +1 555 010 2468", "call [phone]"),
        ("call +1-555-010-2468", "call [phone]"),
        ("call 555-010-2468", "call [phone]"),
        ("call 555.010.2468", "call [phone]"),
        ("call (555) 010-2468", "call [phone]"),
        ("call (555)010-2468!", "call [phone]!"),
        ("lives at 742 Evergreen Street", "lives at [address]"),
        ("lives at 742 evergreen st.", "lives at [address]"),
        ("lives at 742  Evergreen\nSt", "lives at [address]"),
        ("in SPRINGFIELDIA", "in [city]"),
        ("zip 98765", "zip [postal_code]"),
        ("zip 98765-4321", "zip [postal_code]"),
        ("https://www.linkedin.com/in/robin-synthetic/", "[linkedin]"),
        ("see linkedin.com/in/robin-synthetic here", "see [linkedin] here"),
        ("http://LinkedIn.com/in/robin-synthetic", "[linkedin]"),
        ("https://github.com/robinsynth/", "[github]"),
        ("www.github.com/robinsynth", "[github]"),
        ("robin.example.test/", "[website]"),
        ("password Synth-Pass-4417 typed", "password [已填] typed"),
    ],
)
def test_every_variant_is_masked(redactor, text, masked):
    assert redactor.mask(text) == masked


def test_other_emails_and_phones_are_masked_by_format(redactor):
    text = "Prefilled: other.person@mail.example, header acct@x.example; phone 206-555-0199 or +1 (425) 555-0123."
    out = redactor.mask(text)
    assert "@" not in out
    assert "555-0199" not in out and "555-0123" not in out
    assert out.count("[other email]") == 2 and out.count("[other phone]") == 2, "never the applicant's own placeholder"
    assert "[email]" not in out and "[phone]" not in out


def test_own_and_other_values_keep_apart(redactor):
    out = redactor.mask("robin.synthetic@example.test or other.person@mail.example")
    assert out == "[email] or [other email]"
    assert redactor.mask(out) == out


def test_masking_is_idempotent(redactor):
    text = (
        "Robin.Synthetic@example.test (555) 010-2468 742 Evergreen St Springfieldia 98765 "
        "https://www.linkedin.com/in/robin-synthetic/ Synth-Pass-4417 other@x.example 206-555-0199"
    )
    once = redactor.mask(text)
    assert redactor.mask(once) == once


def test_names_and_ordinary_text_stay(redactor):
    text = "Robin Q Synthetic (Robin) applied on 2026-10-15 for job 12345; state WA, United States."
    assert redactor.mask(text) == text


def test_short_and_empty_values_are_not_patterns():
    profile = {"personal": {"email": "", "phone": "12", "city": "Ab", "password": "xyz", "github_url": None}}
    r = Redactor.from_profile(profile)
    assert r.mask("Ab xyz 12 about") == "Ab xyz 12 about"


def test_international_phone_variants():
    r = Redactor.from_profile({"personal": {"phone": "+44 20 7946 0958"}})
    for text in ("+44 20 7946 0958", "442079460958", "+44-20-7946-0958"):
        assert r.mask(text) == "[phone]"


def test_postal_code_with_letters():
    r = Redactor.from_profile({"personal": {"postal_code": "K1A 0B9"}})
    assert r.mask("code k1a0b9 / K1A 0B9 / K1A-0B9") == "code [postal_code] / [postal_code] / [postal_code]"


def test_mask_json_recurses(redactor):
    obj = {
        "fields": [{"question": "Email", "value": "robin.synthetic@example.test", "options": ["555-010-2468", 3]}],
        "ok": True,
        "n": None,
        "Robin.Synthetic@example.test": "key",
    }
    out = redactor.mask_json(obj)
    assert out == {
        "fields": [{"question": "Email", "value": "[email]", "options": ["[phone]", 3]}],
        "ok": True,
        "n": None,
        "[email]": "key",
    }


def test_bad_profile_names_only_the_kind():
    with pytest.raises(InvariantError) as err:
        Redactor.from_profile({"personal": {"phone": 5550102468, "password": ["Synth-Pass-4417"]}})
    assert "5550102468" not in str(err.value) and "Synth-Pass" not in str(err.value)
    with pytest.raises(InvariantError):
        Redactor.from_profile({"no_personal": {}})


def test_scan_finds_leaks_and_reports_zero_after_masking(redactor, tmp_path):
    leaked = tmp_path / "page.html"
    leaked.write_text(
        "<input value='ROBIN.SYNTHETIC@example.test'><p>+1 555.010.2468</p><p>742 Evergreen St</p>"
        "<a href='https://linkedin.com/in/robin-synthetic'>me</a> 98765 Synth-Pass-4417 "
        "x@y.example 206-555-0199",
        encoding="utf-8",
    )
    clean = tmp_path / "meta.json"
    clean.write_text('{"title": "Engineer"}', encoding="utf-8")
    report = redactor.scan([leaked, clean])
    hits = report[str(leaked)]
    assert set(hits) == set(KINDS)
    assert hits["email"] == 1 and hits["phone"] == 1 and hits["address"] == 1
    assert hits["url"] == 1 and hits["postal_code"] == 1 and hits["password"] == 1
    assert hits["other_email"] == 1 and hits["other_phone"] == 1
    assert total_hits(report) == 8
    assert sum(report[str(clean)].values()) == 0
    assert "Robin" not in repr(report) and "Synth" not in repr(report)

    leaked.write_text(redactor.mask(leaked.read_text(encoding="utf-8")), encoding="utf-8")
    report = redactor.scan([leaked, clean])
    assert total_hits(report) == 0


def test_scan_skips_binary_files_and_walks_directories(redactor, tmp_path):
    run = tmp_path / "run"
    (run / "job1").mkdir(parents=True)
    png = run / "job1" / "page.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nrobin.synthetic@example.test")
    blob = run / "job1" / "data.bin"
    blob.write_bytes(b"\x00\x01robin.synthetic@example.test")
    log = run / "job1" / "log.txt"
    log.write_bytes(b"bad \xff byte then 5550102468")
    report = redactor.scan([run])
    assert set(report["_skipped"]) == {str(png), str(blob)}
    assert report[str(log)]["phone"] == 1
    assert total_hits(report) == 1


def test_an_address_with_a_unit_is_masked_line_by_line():
    """Forms split the address over Line 1 and Line 2; each line is masked on its own (decision 22)."""
    r = Redactor.from_profile({"personal": {"address": "12 Elm Street Apt 4B", "city": "Springfieldia"}})
    assert r.mask("12 Elm Street") == "[address]" and r.mask("Apt 4B") == "[address]"
    assert r.mask("12 Elm Street Apt 4B, Springfieldia") == "[address], [city]"
    assert r.mask("Elm Street") == "Elm Street", "a part of the street alone stays"
