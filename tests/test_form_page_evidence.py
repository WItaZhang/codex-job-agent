"""Synthetic page structures from anonymous bug reports; no real website or applicant data."""

from dataclasses import replace

from intent_job_agent.form_fill import columns, readback
from intent_job_agent.form_fill.recorder import Recorder
from intent_job_agent.form_fill.redact import Redactor

from . import test_form_fill_readback as browser_cases
from .test_form_fill_recorder import f, snap

chromium = browser_cases.chromium


def test_native_radios_inside_aria_groups_are_read_once(chromium):
    _, page, _ = chromium
    groups = []
    for n in range(7):
        options = "".join(
            f'<input type="radio" id="g{n}-{i}" name="g{n}" {"checked" if i == 1 else ""}>'
            f'<label for="g{n}-{i}">Option {i}</label>'
            for i in range(3)
        )
        groups.append(
            f'<h2 id="q{n}">Synthetic question {n}</h2>'
            f'<div role="radiogroup" aria-labelledby="q{n}" aria-required="true">{options}</div>'
        )
    page.set_content("<main>" + "".join(groups) + "</main>")
    fields = readback.normalize(readback._extract(page))
    assert len(fields) == 7
    assert all(v.control == "radio" and v.required and v.value == "Option 1" for v in fields)
    assert all(v.options == ("Option 0", "Option 1", "Option 2") for v in fields)
    assert [v.question for v in fields] == [f"Synthetic question {n}" for n in range(7)]


def test_hidden_contact_metadata_does_not_black_out_the_visible_form(chromium, tmp_path):
    browser, page, _ = chromium
    page.set_content("""<style>.hidden {display:none}</style><main>
      <script type="application/json">{"email":"sam.synthetic@example.test"}</script>
      <div class="hidden">sam.synthetic@example.test</div>
      <div style="visibility:hidden">sam.synthetic@example.test</div>
      <div style="visibility:hidden">hidden text
        <span id="visible-child" style="visibility:visible">sam.synthetic@example.test</span>
      </div>
      <h1>Visible application</h1><p id="question">Synthetic eligibility question</p>
      <input id="visible-email" value="sam.synthetic@example.test">
      <p id="public">Public explanatory text</p>
      <div id="split">sam.synthetic<span>@example.test</span></div>
      <button>Submit application</button></main>""")
    redactor = Redactor.from_profile({"personal": {"email": "sam.synthetic@example.test"}})
    paths = readback.contact_paths(page.evaluate(readback.CONTACT_JS), redactor)
    covered = page.evaluate(
        """paths => Object.fromEntries(
      ['visible-email','split','question','public','visible-child'].map(id => [id, paths.some(p =>
        document.querySelector(p).contains(document.getElementById(id)))]))""",
        paths,
    )
    assert covered == {"visible-email": True, "split": True, "question": False, "public": False, "visible-child": True}
    assert "body" not in paths
    image, reason = readback._masked_screenshot(page, redactor, render_seconds=2)
    assert image is not None and not reason
    for selector, black in (("#visible-email", True), ("#split", True), ("#question", False), ("#public", False)):
        box = page.locator(selector).bounding_box()
        color = browser_cases.pixel(browser, image, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        assert (color[:3] == [0, 0, 0]) == black
    (tmp_path / "synthetic-masked.png").write_bytes(image)
    assert page.locator("#visible-email").input_value() == "sam.synthetic@example.test"


def test_shared_search_does_not_merge_different_steps_and_trigger_false_stuck():
    rec = Recorder()
    pages = []
    for question in ("Degree requirement", "Experience requirement", "Voluntary disclosure", "Consent choice"):
        state = snap(f("Search"), f(question), marker=("Application",))
        pages.append(rec.observe(state, "filled"))
    assert [p.page for p in pages] == [1, 2, 3, 4]
    assert not any(p.stop for p in pages)
    assert rec.observe(snap(f("Search"), f("Degree requirement"), marker=("Application",)), "filled").page == 1


def test_explicit_step_changes_even_when_sparse_fields_are_identical():
    rec = Recorder()
    first = replace(snap(f("Search"), marker=("First step",)), step="First step")
    second = replace(snap(f("Search"), marker=("Second step",)), step="Second step")
    assert rec.observe(first, "filled").page == 1
    assert rec.observe(second, "filled").page == 2
    restored = Recorder.from_json(rec.to_json())
    assert restored.observe(first, "filled").page == 1
    assert not restored.observe(first, "filled").stop
    assert restored.observe(first, "filled").stop


def test_step_survives_browser_readback_masking_and_old_state(chromium):
    _, page, _ = chromium
    rec = Recorder()
    redactor = Redactor.from_profile({"personal": {"email": "sam.synthetic@example.test"}})
    for n in range(4):
        page.set_content(
            f'<h1>Application</h1><p aria-current="step">Step {n} sam.synthetic@example.test</p>'
            '<label for="search">Search</label><input id="search">'
        )
        state = readback.snapshot(readback._extract(page), "T1", "synthetic")
        masked, _, _ = columns.mask_snapshot(state, redactor, {})
        assert masked.step and "sam.synthetic@example.test" not in masked.step
        result = rec.observe(masked, "filled")
        assert result.page == n + 1 and not result.stop
        rec = Recorder.from_json(rec.to_json())
    assert "sam.synthetic@example.test" not in repr(rec.to_json())
    old = rec.to_json()
    for record in old["pages"]:
        del record["step"]
    restored = Recorder.from_json(old)
    assert restored.observe(masked, "filled").page == 4


def test_password_placeholder_is_protected_without_reading_password(chromium):
    _, page, _ = chromium
    page.set_content(
        '<input id="pw" type="password" placeholder="sam.synthetic@example.test">'
        '<input id="filled" type="password" value="private-password">'
    )
    # A password value accessor is forbidden even when collecting ancestor text.
    page.evaluate("""() => {
      for (const el of document.querySelectorAll('input')) {
        Object.defineProperty(el, 'value', {get() { throw Error('must not read password'); }});
      }
    }""")
    redactor = Redactor.from_profile({"personal": {"email": "sam.synthetic@example.test"}})
    nodes = page.evaluate(readback.CONTACT_JS)
    paths = readback.contact_paths(nodes, redactor)
    assert "private-password" not in repr(nodes)
    assert page.evaluate("paths => paths.some(p => document.querySelector(p).id === 'pw')", paths)
