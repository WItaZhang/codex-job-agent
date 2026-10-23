"""Versioned closed contracts. Errors never expose provider or applicant text."""

from copy import deepcopy
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import Model

ErrorCode = Literal[
    "missing_credential",
    "invalid_credential",
    "unsupported_model",
    "schema_unsupported",
    "schema_violation",
    "refused",
    "incomplete",
    "timeout",
    "rate_limited",
    "network",
    "permission_denied",
    "stale",
    "budget_exhausted",
    "not_configured",
    "internal",
    "unsupported_protocol",
]
MESSAGES = {
    "not_configured": "尚未选择模型服务，请先配置；模拟模式需明确选择。",
    "missing_credential": "尚未配置服务凭据，请查看模型服务设置。",
    "invalid_credential": "服务拒绝了凭据，请检查配置。",
    "unsupported_model": "当前运行时不支持此模型，请选择支持的服务。",
    "schema_unsupported": "当前服务不支持此数据格式，请检查兼容配置。",
    "schema_violation": "返回内容不符合要求，本次未采用。",
    "refused": "服务拒绝处理本次内容，请检查输入。",
    "incomplete": "服务未返回完整结果，本次未采用。",
    "timeout": "本地等待超时；服务端结果及费用可能尚不确定。",
    "rate_limited": "服务请求受限，请稍后再试。",
    "network": "连接失败；服务端结果尚不能确认。",
    "permission_denied": "此步骤不允许这些数据或动作，请检查输入。",
    "stale": "此请求已失效，请使用当前状态重新操作。",
    "budget_exhausted": "本次处理预算已用尽，未启动后续动作。",
    "internal": "本次处理未完成，请重试或查看固定错误编号。",
    "unsupported_protocol": "不支持的协议版本，请更新兼容组件。",
}


class RuntimeFault(Exception):
    def __init__(self, code: ErrorCode):
        self.code = code if code in MESSAGES else "internal"
        super().__init__(MESSAGES[self.code])


class Usage(Model):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class Cancellation(Model):
    local: Literal["running", "stopped"] = "running"
    transport: Literal["not_sent", "in_flight", "completed", "aborted", "unknown"] = "not_sent"
    remote: Literal["unknown", "cancel_acknowledged", "completed"] = "unknown"


class Event(Model):
    protocol_version: Literal[1]
    request_id: str
    run_id: str
    node: str
    revision: int = Field(ge=0)
    status: Literal["running", "completed", "failed", "stopped"]
    error: ErrorCode | None = None
    usage: Usage = Field(default_factory=Usage)
    cancel: Cancellation = Field(default_factory=Cancellation)
    attempts: int = Field(default=0, ge=0)
    attempt_usage: list[Usage] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "failed" and self.error is None:
            raise ValueError("failed requires error")
        if self.status in ("running", "completed", "stopped") and self.error is not None:
            raise ValueError("unexpected error")
        if self.status == "stopped" and self.cancel.local != "stopped":
            raise ValueError("inconsistent local stop")
        if len(self.attempt_usage) != self.attempts:
            raise ValueError("inconsistent attempt count")
        return self


def consume(raw: dict) -> Event:
    if type(raw.get("protocol_version")) is not int or raw.get("protocol_version") != 1:
        raise RuntimeFault("unsupported_protocol")
    try:
        return Event.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        raise RuntimeFault("schema_violation") from None


def wire_schema(model: type[BaseModel]) -> dict:
    """Explicit strict object schema. Preserve types/default semantics, never add null."""
    try:
        schema = deepcopy(model.model_json_schema())

        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    if value.get("additionalProperties") not in (None, False):
                        raise RuntimeFault("schema_unsupported")
                    value["additionalProperties"] = False
                    value["required"] = list(value.get("properties", {}))
                value.pop("default", None)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(schema)
        Draft202012Validator.check_schema(schema)
        return schema
    except RuntimeFault:
        raise
    except Exception:
        raise RuntimeFault("schema_unsupported") from None


def validate_value(model: type[BaseModel], value: dict) -> BaseModel:
    try:
        Draft202012Validator(wire_schema(model)).validate(value)
        return model.model_validate(value, strict=True)
    except RuntimeFault:
        raise
    except Exception:
        raise RuntimeFault("schema_violation") from None
