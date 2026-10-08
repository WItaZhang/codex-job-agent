"""Model access through one interface: `structured(prompt, schema) -> validated object`.

Slice 1 ships only the fake client; Anthropic and OpenAI-compatible adapters come with slice 3.
"""

from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: type[T]) -> T: ...


class FakeClient:
    """Scripted responses keyed by schema name; unscripted schemas get their defaults. Records prompts."""

    def __init__(self, handlers: dict[str, Callable[[str], dict | BaseModel]] | None = None):
        self.handlers = handlers or {}
        self.calls: list[tuple[str, str]] = []

    def structured(self, prompt: str, schema: type[T]) -> T:
        self.calls.append((schema.__name__, prompt))
        handler = self.handlers.get(schema.__name__)
        raw = handler(prompt) if handler else {}
        return schema.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)


class NoModelClient:
    """Tool mode: the host agent passes understanding as tool arguments, so nothing is asked here."""

    def structured(self, prompt: str, schema: type[T]) -> T:
        return schema()


def make_client(settings: Settings) -> LLMClient:
    if settings.llm.provider == "fake":
        return FakeClient()
    if settings.llm.provider == "host":
        return NoModelClient()
    raise NotImplementedError(f"Provider {settings.llm.provider!r} arrives in a later slice")
