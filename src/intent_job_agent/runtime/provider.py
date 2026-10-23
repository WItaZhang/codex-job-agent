"""Bounded S1 execution. A provider returns suggestions, never executes business tools.

Worker lifetime is accounted for until it actually returns; cancellation does not release a
slot for an uncooperative worker. Only fake is executable in S1, no SDK/HTTP/credential loader.
"""

import re
import time
from copy import deepcopy
from threading import Event as Signal
from threading import Lock, Thread
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from . import privacy
from .config import Model, RuntimeConfig, effective_runtime
from .contracts import Cancellation, ErrorCode, Event, RuntimeFault, Usage, validate_value, wire_schema
from .providers.fake import ScriptedProvider


class Provider(Protocol):
    def send(self, body: str, cancel: Signal) -> dict: ...


class Reply(Model):
    value: dict | None = None
    usage: Usage = Field(default_factory=Usage)
    error: ErrorCode | None = None
    transport: Literal["completed", "aborted", "unknown"] = "completed"
    remote: Literal["unknown", "cancel_acknowledged", "completed"] = "unknown"


class Tool(Model):
    name: Literal["inspect_page", "fill_field", "select_option"]
    parameters: type[BaseModel]


class Handle:
    def __init__(self, request_id, run_id, node, revision):
        self._lock = Lock()
        self._done = Signal()
        self.cancel_signal = Signal()
        self.drained = Signal()
        self._value = None
        self._event = Event(
            protocol_version=1, request_id=request_id, run_id=run_id, node=node, revision=revision, status="running"
        )
        self._usages: list[Usage] = []

    @property
    def value(self):
        with self._lock:
            return deepcopy(self._value)

    def snapshot(self) -> Event:
        with self._lock:
            return self._event.model_copy(deep=True)

    def wait(self, timeout=None) -> Event:
        if not self._done.wait(timeout):
            raise TimeoutError("Runtime result is not ready")
        return self.snapshot()

    def cancel(self):
        self.finish("stopped", stop=True)

    def finish(self, status, error=None, value=None, stop=False):
        with self._lock:
            if self._done.is_set():
                return
            cancel = self._event.cancel
            if stop:
                self.cancel_signal.set()
                cancel = cancel.model_copy(update={"local": "stopped"})
            self._event = self._event.model_copy(update={"status": status, "error": error, "cancel": cancel})
            self._value = value
            self._done.set()

    def sent(self):
        with self._lock:
            if self._done.is_set():
                return False
            self._usages.append(Usage())
            self._event = self._event.model_copy(
                update={
                    "attempts": len(self._usages),
                    "usage": Usage(),
                    "attempt_usage": list(self._usages),
                    "cancel": self._event.cancel.model_copy(update={"transport": "in_flight"}),
                }
            )
            return True

    def observed(self, reply: Reply):
        with self._lock:
            if not self._usages:
                return
            self._usages[-1] = reply.usage

            def total(field):
                values = [getattr(u, field) for u in self._usages]
                return sum(values) if all(v is not None for v in values) else None

            # No invented rates or actual account/model metadata in this simulation.
            usage = Usage(input_tokens=total("input_tokens"), output_tokens=total("output_tokens"))
            self._event = self._event.model_copy(
                update={
                    "usage": usage,
                    "attempt_usage": list(self._usages),
                    "cancel": Cancellation(
                        local=self._event.cancel.local, transport=reply.transport, remote=reply.remote
                    ),
                }
            )


class Runtime:
    def __init__(self, config: RuntimeConfig, provider: Provider | None = None):
        if not isinstance(config, RuntimeConfig):
            raise TypeError("Runtime requires validated configuration")
        self.config = config
        self.provider = provider if provider is not None else ScriptedProvider()
        self._lock = Lock()
        self._started: float | None = None
        self._scope: tuple[str, str] | None = None
        self._closed = False
        self._requests = 0
        self._active = 0
        self._ids: set[str] = set()
        self._invalid: set[tuple[str, str]] = set()
        self._revisions: dict[tuple[str, str], int] = {}
        self._handles: list[tuple[str, str, Handle]] = []

    @classmethod
    def load(cls, overrides: dict | None, provider: Provider | None = None):
        return cls(effective_runtime(overrides), provider)

    def configuration_status(self):
        provider = self.config.provider
        error = (
            "not_configured"
            if provider.name == "unconfigured"
            else "missing_credential"
            if provider.name == "external" and not provider.credential_ref
            else "unsupported_model"
            if provider.name == "external"
            else None
        )
        return {
            "protocol_version": 1,
            "provider": provider.name,
            "model": provider.model,
            "simulation": provider.name == "fake",
            "credential_state": (
                "not_used"
                if provider.name == "fake"
                else "configured_unverified"
                if provider.credential_ref
                else "missing"
            ),
            "error": error,
            "cost": "simulated" if not error else "unknown",
            "destination": "local_only",
            "categories": deepcopy(privacy.POLICIES),
            "remote_cancel_supported": False,
        }

    def set_revision(self, workspace: str, run_id: str, revision: int):
        if type(revision) is not int or revision < 0:
            raise RuntimeFault("schema_violation")
        with self._lock:
            key = (workspace, run_id)
            if self._scope is not None and self._scope != key:
                raise RuntimeFault("stale")
            previous = self._revisions.get(key)
            if previous is not None and revision <= previous:
                raise RuntimeFault("stale")
            self._revisions[key] = revision
            for w, run, handle in self._handles:
                if (w, run) == key:
                    handle.finish("failed", "stale", stop=True)

    def close(self):
        """Stop locally; return without waiting for uncooperative transport threads."""
        with self._lock:
            self._closed = True
            for w, run, handle in self._handles:
                self._invalid.add((w, run))
                handle.cancel()

    def structured(self, **kwargs):
        if kwargs.get("tools"):
            raise RuntimeFault("permission_denied")
        return self.start(**kwargs)

    def tool_turn(self, **kwargs):
        if not kwargs.get("tools") or kwargs.get("node") != "form_fill":
            raise RuntimeFault("permission_denied")
        return self.start(schema=None, **kwargs)

    def invalidate(self, workspace: str, run_id: str):
        with self._lock:
            self._invalid.add((workspace, run_id))
            for w, run, handle in self._handles:
                if (w, run) == (workspace, run_id):
                    handle.finish("failed", "stale", stop=True)

    def start(
        self,
        *,
        request_id: str,
        workspace: str,
        run_id: str,
        revision: int,
        node: str,
        data: dict,
        categories: list[str],
        schema: type[BaseModel] | None,
        tools: tuple[Tool, ...] = (),
        history: tuple[dict, ...] = (),
        stopped: bool = False,
    ) -> Handle:
        handle = Handle(request_id, run_id, node, revision)
        reserved = False
        try:
            if stopped:
                handle.cancel()
                handle.drained.set()
                return handle
            if self.config.provider.name != "fake":
                raise RuntimeFault(self.configuration_status()["error"])
            if any(
                not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", x)
                for x in (workspace, run_id, request_id)
            ):
                raise RuntimeFault("schema_violation")
            b = self.config.budgets
            if any(
                getattr(b, key) == 0
                for key in (
                    "max_requests",
                    "max_input_bytes",
                    "max_output_bytes",
                    "max_depth",
                    "max_output_tokens",
                    "concurrency",
                )
            ):
                raise RuntimeFault("budget_exhausted")
            if tools and b.max_tool_calls == 0:
                raise RuntimeFault("budget_exhausted")
            if type(history) not in (tuple, list):
                raise RuntimeFault("schema_violation")
            privacy.bounded_json(
                {"data": data, "categories": categories, "history": list(history)}, b.max_input_bytes, b.max_depth
            )
            prepared = privacy.assemble(node, data, categories, b)
            # History is data under the same policy, never raw role/system messages.
            prepared["history"] = [
                privacy.assemble(node, item["data"], item["categories"], b)
                for item in history
                if self._history_valid(item)
            ]
            if tools and (node != "form_fill" or len({t.name for t in tools}) != len(tools)):
                raise RuntimeFault("permission_denied")
            for tool in tools:
                if not isinstance(tool, Tool):
                    raise RuntimeFault("permission_denied")
            prepared["kind"] = "tool_turn" if tools else "structured"
            prepared["schema"] = None if tools else wire_schema(schema)
            prepared["tools"] = [
                {"name": t.name, "parameters": wire_schema(t.parameters), "strict": True} for t in tools
            ]
            prepared["max_output_tokens"] = b.max_output_tokens
            body = privacy.bounded_json(prepared, b.max_input_bytes, b.max_depth)
            with self._lock:
                key = (workspace, run_id)
                if self._closed or (self._scope is not None and self._scope != key):
                    raise RuntimeFault("stale")
                if request_id in self._ids or (workspace, run_id) in self._invalid:
                    raise RuntimeFault("stale")
                if self._active >= b.concurrency or self._requests >= b.max_requests or self._remaining() <= 0:
                    raise RuntimeFault("budget_exhausted")
                key = (workspace, run_id)
                if key in self._revisions and self._revisions[key] != revision:
                    raise RuntimeFault("stale")
                self._scope = key
                self._revisions[key] = revision
                self._ids.add(request_id)
                self._active += 1
                self._handles.append((workspace, run_id, handle))
                reserved = True
            Thread(target=self._run, args=(handle, body, schema, tools), daemon=True).start()
        except RuntimeFault as exc:
            handle.finish("failed", "internal" if reserved else exc.code)
            if reserved:
                with self._lock:
                    self._active -= 1
                    self._rollback_unsent(handle)
            handle.drained.set()
        except Exception:
            handle.finish("failed", "internal" if reserved else "schema_violation")
            if reserved:
                with self._lock:
                    self._active -= 1
                    self._rollback_unsent(handle)
            handle.drained.set()
        return handle

    @staticmethod
    def _history_valid(item):
        if type(item) is not dict or set(item) != {"data", "categories"}:
            raise RuntimeFault("permission_denied")
        return True

    def _remaining(self):
        if self._started is None:
            return self.config.budgets.total_timeout_seconds
        return self.config.budgets.total_timeout_seconds - (time.monotonic() - self._started)

    def _rollback_unsent(self, handle):
        """Called under runtime lock after releasing a failed unsent reservation."""
        if handle.snapshot().attempts == 0:
            self._ids.discard(handle.snapshot().request_id)
            self._handles = [(w, run, h) for w, run, h in self._handles if h is not handle]
            if not self._handles and self._started is None:
                self._scope = None

    def _run(self, handle, body, schema, tools):
        try:
            b = self.config.budgets
            for attempt in range(b.max_retries + 1):
                done, ready = Signal(), Signal()
                outcome, timing = [], {}

                def call(outcome=outcome, done=done, ready=ready, timing=timing):
                    try:
                        with self._lock:
                            if handle.cancel_signal.is_set():
                                return
                            if self._requests >= b.max_requests or self._remaining() <= 0:
                                raise RuntimeFault("budget_exhausted")
                            if not handle.sent():
                                return
                            # This point is reached only by a successfully started transport worker.
                            now = time.monotonic()
                            if self._started is None:
                                self._started = now
                            self._requests += 1
                            timing["deadline"] = now + min(b.request_timeout_seconds, max(0, self._remaining()))
                        ready.set()
                        raw = self.provider.send(body, handle.cancel_signal)
                        privacy.bounded_json(raw, b.max_output_bytes, b.max_depth)
                        reply = Reply.model_validate(raw)
                        handle.observed(reply)  # late usage allowed, late value forbidden
                        outcome.append((reply, time.monotonic()))
                    except RuntimeFault as exc:
                        outcome.append((Reply(error=exc.code), time.monotonic()))
                    except ValidationError:
                        outcome.append((Reply(error="schema_violation"), time.monotonic()))
                    except Exception:
                        outcome.append((Reply(error="internal"), time.monotonic()))
                    finally:
                        ready.set()
                        done.set()

                Thread(target=call, daemon=True).start()
                if not ready.wait(b.request_timeout_seconds):
                    handle.finish("failed", "timeout", stop=True)
                    done.wait()  # no new slot until this worker really ends
                    return
                deadline = timing.get("deadline")
                if deadline is not None and not done.wait(max(0, deadline - time.monotonic())):
                    handle.finish("failed", "budget_exhausted" if self._remaining() <= 0 else "timeout", stop=True)
                    done.wait()
                    return
                if handle.cancel_signal.is_set():
                    # If ready was set before send, retain the slot for any outstanding work.
                    done.wait()
                    return
                reply, completed_at = outcome[0]
                if deadline is not None and completed_at > deadline:
                    raise RuntimeFault("budget_exhausted" if self._remaining() <= 0 else "timeout")
                try:
                    if reply.usage.output_tokens is not None and reply.usage.output_tokens > b.max_output_tokens:
                        raise RuntimeFault("budget_exhausted")
                    if reply.error:
                        raise RuntimeFault(reply.error)
                    if reply.value is None:
                        raise RuntimeFault("schema_violation")
                    value = (
                        self._tools(reply.value, tools) if tools else validate_value(schema, reply.value).model_dump()
                    )
                    handle.finish("completed", value=value)
                    return
                except RuntimeFault as exc:
                    if attempt == b.max_retries or exc.code not in ("rate_limited", "network", "schema_violation"):
                        raise
                    if handle.cancel_signal.wait(min(b.backoff_seconds, max(0, self._remaining()))):
                        return
        except RuntimeFault as exc:
            handle.finish("failed", exc.code)
        except Exception:
            handle.finish("failed", "internal")
        finally:
            with self._lock:
                self._active -= 1
                self._rollback_unsent(handle)
            handle.drained.set()

    def _tools(self, value, tools):
        if type(value) is not dict or set(value) != {"tool_calls", "final"}:
            raise RuntimeFault("schema_violation")
        calls, final = value["tool_calls"], value["final"]
        if type(calls) is not list or len(calls) > self.config.budgets.max_tool_calls:
            raise RuntimeFault("budget_exhausted")
        if (calls and final is not None) or (not calls and type(final) is not str):
            raise RuntimeFault("schema_violation")
        allowed = {t.name: t for t in tools}
        for call in calls:
            if type(call) is not dict or set(call) != {"name", "arguments"} or call["name"] not in allowed:
                raise RuntimeFault("permission_denied")
            validate_value(allowed[call["name"]].parameters, call["arguments"])
        return deepcopy(value)  # validated suggestion only; no dispatch in S1
