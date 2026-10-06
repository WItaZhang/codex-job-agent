"""Typed browser instructions contain data only; authorization lives in the caller."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def url_origin(url: str) -> str:
    """Validate an HTTP URL and return its normalized origin."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not supported")
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    suffix = f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
    return f"{parsed.scheme}://{host}{suffix}"


class BrowserField(BaseModel):
    """A unique CSS selector and an explicitly supplied value."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    selector: str = Field(min_length=1)
    kind: Literal["text", "select", "checkbox", "radio", "file"]
    value: str | bool

    @model_validator(mode="after")
    def validate_value(self) -> BrowserField:
        if self.kind in {"checkbox", "radio"} and not isinstance(self.value, bool):
            raise ValueError("Checkbox and radio values must be booleans")
        if self.kind == "radio" and self.value is not True:
            raise ValueError("Select a radio option with true; do not attempt to uncheck a radio")
        if self.kind not in {"checkbox", "radio"} and not isinstance(self.value, str):
            raise ValueError("Text, select and file values must be strings")
        if self.kind == "file" and not self.value:
            raise ValueError("File path must not be empty")
        return self


class BrowserPlan(BaseModel):
    """Plan for one page; selectors are CSS, never executable JavaScript."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    fields: tuple[BrowserField, ...] = ()
    submit_selector: str | None = None
    confirmation_selector: str | None = None
    confirmation_text: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url_origin(value)
        return value

    @model_validator(mode="after")
    def unique_selectors(self) -> BrowserPlan:
        selectors = [field.selector for field in self.fields]
        if len(set(selectors)) != len(selectors):
            raise ValueError("Field selectors must be unique")
        for value in (self.submit_selector, self.confirmation_selector, self.confirmation_text):
            if value is not None and not value.strip():
                raise ValueError("Optional selectors and confirmation text cannot be blank")
        return self


class BrowserError(RuntimeError):
    """Recoverable browser boundary failure suitable for a review inbox."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}
