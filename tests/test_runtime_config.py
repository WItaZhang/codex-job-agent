"""Synthetic runtime defaults/overrides, never read user configuration."""

from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from intent_job_agent.config import Settings
from intent_job_agent.resources import default_config


def test_old_workspace_and_recursive_override():
    from intent_job_agent.runtime.config import effective_runtime

    raw = yaml.safe_load(default_config().read_text())
    raw.pop("runtime", None)
    settings = Settings.model_validate(raw)
    base = effective_runtime(settings.runtime)
    raw["runtime"] = {"budgets": {"max_retries": 0}, "provider": {"model": "synthetic"}}
    changed = effective_runtime(Settings.model_validate(raw).runtime)
    assert changed.budgets.max_retries == 0
    assert changed.budgets.max_requests == base.budgets.max_requests
    assert changed.provider.model == "synthetic"
    assert changed.provider.name == base.provider.name


@pytest.mark.parametrize(
    "bad",
    [
        {"budgets": {"typo": 1}},
        {"budgets": None},
        {"budgets": {"max_requests": 0}},
        {"budgets": {"max_retries": -1}},
        {"provider": {"api_key_env": "SECRET"}},
        {"budgets": {"max_requests": True}},
        {"protocol_version": 2},
    ],
)
def test_invalid_partial_config_rejected_at_settings_boundary(bad):
    raw = yaml.safe_load(default_config().read_text())
    raw["runtime"] = bad
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_recursive_merge_replaces_lists_and_scalars_without_mutating_defaults():
    from intent_job_agent.runtime.config import merge

    original = {"nested": {"a": 1, "b": 2}, "items": [1, 2]}
    before = deepcopy(original)
    assert merge(original, {"nested": {"a": 3}, "items": []}) == {"nested": {"a": 3, "b": 2}, "items": []}
    assert original == before
