"""Evidence validation and isolated, deterministic application artifacts."""

import hashlib
from html import escape
from pathlib import Path

from .models import Attachment, Packet, Profile


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_evidence(profile: Profile, packet: Packet) -> None:
    facts = {
        fact.id: fact
        for fact in profile.facts
        if fact.confirmed and (not fact.scope_job_ids or packet.job_id in fact.scope_job_ids)
    }
    for item in [*packet.claims, *packet.answers.values()]:
        missing = set(item.fact_ids) - facts.keys()
        if missing:
            raise ValueError(f"Unsupported or unconfirmed fact IDs: {sorted(missing)}")
    for attachment in packet.attachments:
        if set(attachment.fact_ids) - facts.keys():
            raise ValueError("Attachment references missing, unconfirmed or out-of-scope facts")
        path = Path(attachment.path)
        if not path.is_file() or file_hash(path) != attachment.sha256:
            raise ValueError(f"Attachment missing or changed: {path}")


def render_resume(profile: Profile, fact_ids: list[str], output: Path, *, pdf: bool = True) -> dict:
    """Render selected confirmed facts verbatim; tailoring chooses evidence, never invents it."""
    facts = {fact.id: fact for fact in profile.facts if fact.confirmed}
    if not fact_ids or set(fact_ids) - facts.keys():
        raise ValueError("Select at least one confirmed fact; every ID must exist")
    output.mkdir(parents=True, exist_ok=True)
    selected = [facts[key] for key in dict.fromkeys(fact_ids)]
    markdown = f"# {profile.name}\n\n" + "\n\n".join(fact.text for fact in selected) + "\n"
    (output / "resume.md").write_text(markdown, encoding="utf-8")
    body = "".join(f"<p>{escape(fact.text).replace(chr(10), '<br>')}</p>" for fact in selected)
    document = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Resume</title>"
        "<style>@page{size:A4;margin:18mm}body{font:11pt Arial,sans-serif;color:#17202a}"
        "h1{font-size:22pt}p{line-height:1.4;break-inside:avoid;white-space:pre-wrap}</style>"
        f"</head><body><h1>{escape(profile.name)}</h1>{body}</body></html>"
    )
    html_path = output / "resume.html"
    html_path.write_text(document, encoding="utf-8")
    path = html_path
    if pdf:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(document, wait_until="load")
                path = output / "resume.pdf"
                page.pdf(path=str(path), prefer_css_page_size=True, print_background=True)
            finally:
                browser.close()
    return {
        "attachment": Attachment(path=str(path.resolve()), sha256=file_hash(path), fact_ids=fact_ids).model_dump(),
        "fact_ids": fact_ids,
        "markdown_path": str(output / "resume.md"),
        "html_path": str(html_path),
    }
