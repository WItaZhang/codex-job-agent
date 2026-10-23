"""Independent types; defaults come only from the installed package resource."""

from copy import deepcopy
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..resources import default_config


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProviderConfig(Model):
    name: Literal["fake", "unconfigured"]
    model: str | None
    credential_ref: str | None  # identifier only; S1 never resolves credentials


class Budgets(Model):
    max_requests: int = Field(gt=0)
    max_retries: int = Field(ge=0)  # extra attempts, not the first request
    backoff_seconds: float = Field(ge=0)
    request_timeout_seconds: float = Field(gt=0)
    total_timeout_seconds: float = Field(gt=0)
    max_input_bytes: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_depth: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    concurrency: int = Field(gt=0)


class RuntimeConfig(Model):
    protocol_version: Literal[1]
    provider: ProviderConfig
    budgets: Budgets


def merge(defaults: dict, overrides: dict) -> dict:
    """Mappings recurse; other values replace. Validation follows the complete merge."""
    result = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def effective_runtime(overrides: dict | None) -> RuntimeConfig:
    raw = yaml.safe_load(default_config().read_text(encoding="utf-8"))["runtime"]
    return RuntimeConfig.model_validate(merge(raw, overrides) if overrides is not None else raw)
