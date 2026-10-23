"""R1 accidental-publication guard; static YAML only, not a shell or adversarial-code sandbox."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def validate_workflow(document):
    # Deliberately enumerate approved structure/commands; unknown steps fail closed.
    assert set(document) == {"name", "on", "permissions", "jobs"}
    assert document["on"] == ["push", "pull_request"]
    assert document["permissions"] == {"contents": "read"}
    assert set(document["jobs"]) == {"check"}
    job = document["jobs"]["check"]
    assert set(job) == {"runs-on", "steps"}  # no job-level permission escalation or reusable workflow
    assert job["runs-on"] == "ubuntu-latest"
    assert job["steps"] == [
        {"uses": "actions/checkout@v4"},
        {"uses": "astral-sh/setup-uv@v6", "with": {"python-version": "3.13"}},
        {"run": "uv sync --locked"},
        {"run": "uv run playwright install --with-deps chromium"},
        {"run": "uv run ruff check src tests"},
        {"run": "uv run ruff format --check src tests"},
        {"run": "uv run python -m pytest -q"},
        {"run": "uv run python scripts/check_package.py"},
        {"run": "uv run python scripts/check_editable_resources.py"},
    ]


def workflow():
    # BaseLoader preserves GitHub's YAML 'on' key, which YAML 1.1 otherwise interprets as True.
    return yaml.load((WORKFLOWS / "check.yml").read_text(), Loader=yaml.BaseLoader)


def test_only_approved_internal_workflow_exists():
    assert sorted(p.name for p in WORKFLOWS.iterdir() if p.suffix in (".yml", ".yaml")) == ["check.yml"]
    validate_workflow(workflow())


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "actions/upload-artifact@v4", "with": {"path": "dist/*.whl"}},
        {"uses": "softprops/action-gh-release@v2"},
        {"uses": "actions/deploy-pages@v4"},
        {"uses": "pypa/gh-action-pypi-publish@release/v1"},
        {"run": "uv publish"},
        {"run": "gh release create synthetic dist/*.whl"},
        {"run": "uv run python -m pytest -q && uv publish"},
    ],
)
def test_synthetic_publication_step_is_rejected(step):
    document = deepcopy(workflow())
    document["jobs"]["check"]["steps"].append(step)
    with pytest.raises(AssertionError):
        validate_workflow(document)


@pytest.mark.parametrize(
    "mutation", ["write", "job_permissions", "extra_job", "release_trigger", "missing_probe", "missing_browser"]
)
def test_synthetic_contract_drift_is_rejected(mutation):
    document = deepcopy(workflow())
    if mutation == "write":
        document["permissions"]["contents"] = "write"
    elif mutation == "job_permissions":
        document["jobs"]["check"]["permissions"] = {"id-token": "write"}
    elif mutation == "extra_job":
        document["jobs"]["publish"] = {"steps": [{"run": "uv publish"}]}
    elif mutation == "release_trigger":
        document["on"].append("release")
    elif mutation == "missing_browser":
        document["jobs"]["check"]["steps"].remove({"run": "uv run playwright install --with-deps chromium"})
    else:
        document["jobs"]["check"]["steps"].pop()
    with pytest.raises(AssertionError):
        validate_workflow(document)
