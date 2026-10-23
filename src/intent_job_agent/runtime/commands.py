"""Closed v1 command consumer bound by trusted code, not wire-supplied authority.

References resolve only in an injected registry. No dynamic imports, eval, permission writes,
synthetic submission controllers, or business commands. Python runtime calls remain internal.
"""

from threading import Lock
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from .config import Model
from .contracts import Event, RuntimeFault
from .privacy import bounded_json
from .provider import Runtime, Tool

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class Envelope(Model):
    protocol_version: Literal[1]
    workspace: Identifier
    run_id: Identifier
    revision: int = Field(ge=0)
    request_id: Identifier


class Structured(Envelope):
    command: Literal["structured"]
    node: str
    schema_ref: Identifier
    data: dict
    categories: list[str]
    history: list[dict] = Field(default_factory=list)


class ToolTurn(Envelope):
    command: Literal["tool_turn"]
    node: Literal["form_fill"]
    tool_refs: list[Identifier]
    data: dict
    categories: list[str]
    history: list[dict] = Field(default_factory=list)


class Control(Envelope):
    command: Literal["cancel", "status"]


COMMAND = TypeAdapter(Annotated[Structured | ToolTurn | Control, Field(discriminator="command")])


def consume_command(raw):
    if type(raw) is not dict:
        raise RuntimeFault("schema_violation")
    if type(raw.get("protocol_version")) is not int or raw.get("protocol_version") != 1:
        raise RuntimeFault("unsupported_protocol")
    try:
        return COMMAND.validate_python(raw)
    except (ValidationError, ValueError, TypeError):
        raise RuntimeFault("schema_violation") from None


class Result(Model):
    protocol_version: Literal[1]
    workspace: str
    event: Event
    value: dict | None = None


class Consumer:
    def __init__(
        self,
        runtime: Runtime,
        *,
        workspace: str,
        run_id: str,
        revision: int,
        schemas: dict[str, type[BaseModel]],
        tools: dict[str, Tool],
    ):
        # Validate the trusted binding without accepting a wire-selected scope.
        Envelope(protocol_version=1, workspace=workspace, run_id=run_id, revision=revision, request_id="binding")
        if any(not isinstance(model, type) or not issubclass(model, BaseModel) for model in schemas.values()):
            raise RuntimeFault("schema_unsupported")
        if any(not isinstance(tool, Tool) for tool in tools.values()):
            raise RuntimeFault("permission_denied")
        self.runtime = runtime
        self._workspace = workspace
        self._run_id = run_id
        self._revision = revision
        self._schemas = MappingProxyType(dict(schemas))
        self._tools = MappingProxyType(dict(tools))
        self._handles = {}
        self._lock = Lock()

    def _handle(self, request_id):
        handle = self._handles.get(request_id)
        if handle is None or handle.snapshot().revision != self._revision:
            raise RuntimeFault("stale")
        return handle

    def _result(self, handle):
        # Done precedes immutable value publication inside the handle lock; value may only
        # transition once. Snapshot first, then expose a value solely for completed events.
        event = handle.snapshot()
        return Result(
            protocol_version=1,
            workspace=self._workspace,
            event=event,
            value=handle.value if event.status == "completed" else None,
        )

    def dispatch(self, raw) -> Result:
        if type(raw) is not dict:
            raise RuntimeFault("schema_violation")
        b = self.runtime.config.budgets
        # Control commands remain usable after execution budgets are exhausted.
        command = consume_command(raw)
        with self._lock:
            if (command.workspace, command.run_id, command.revision) != (self._workspace, self._run_id, self._revision):
                raise RuntimeFault("stale")
            if isinstance(command, Control):
                handle = self._handle(command.request_id)
                if command.command == "cancel":
                    handle.cancel()
                return self._result(handle)
            bounded_json(raw, b.max_input_bytes, b.max_depth)
            if command.request_id in self._handles:
                raise RuntimeFault("stale")
            arguments = dict(
                request_id=command.request_id,
                workspace=self._workspace,
                run_id=self._run_id,
                revision=self._revision,
                node=command.node,
                data=command.data,
                categories=command.categories,
                history=command.history,
            )
            if isinstance(command, Structured):
                schema = self._schemas.get(command.schema_ref)
                if schema is None:
                    raise RuntimeFault("permission_denied")
                handle = self.runtime.structured(schema=schema, **arguments)
            else:
                if (
                    not command.tool_refs
                    or len(set(command.tool_refs)) != len(command.tool_refs)
                    or any(ref not in self._tools for ref in command.tool_refs)
                ):
                    raise RuntimeFault("permission_denied")
                handle = self.runtime.tool_turn(tools=tuple(self._tools[ref] for ref in command.tool_refs), **arguments)
            self._handles[command.request_id] = handle
            return self._result(handle)

    def wait(self, request_id: str, timeout=None) -> Result:
        with self._lock:
            handle = self._handle(request_id)
        handle.wait(timeout)
        with self._lock:
            return self._result(self._handle(request_id))

    def advance_revision(self, revision: int):
        with self._lock:
            if type(revision) is not int or revision <= self._revision:
                raise RuntimeFault("stale")
            self.runtime.set_revision(self._workspace, self._run_id, revision)
            self._revision = revision

    def close(self):
        self.runtime.invalidate(self._workspace, self._run_id)
