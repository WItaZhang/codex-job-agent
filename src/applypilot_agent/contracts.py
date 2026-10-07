"""Shared strict shape for domain contracts."""

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
