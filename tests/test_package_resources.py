"""Synthetic installed-resource checks; artifact-level tests run separately."""

from pathlib import Path


def test_default_resources_have_complete_single_source_content():
    from intent_job_agent.resources import default_config, letter_skill

    root = Path(__file__).resolve().parents[1]
    assert default_config().read_bytes() == (root / "configs/default.yaml").read_bytes()
    source = root / ".claude/skills/cover-letter"
    actual = letter_skill()
    for item in source.rglob("*.md"):
        assert (actual / item.relative_to(source)).read_bytes() == item.read_bytes()
    assert (actual / "references/structure.md").is_file()
