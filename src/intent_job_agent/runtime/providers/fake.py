"""Scripted provider; no defaults, network, credentials, or tool execution."""

from collections import deque
from threading import Lock

from ..contracts import RuntimeFault


class ScriptedProvider:
    def __init__(self, scripts=()):
        self._scripts = deque(scripts)
        self._lock = Lock()
        self.bodies: list[str] = []

    def send(self, body: str, cancel):
        with self._lock:
            self.bodies.append(body)
            if not self._scripts:
                raise RuntimeFault("schema_violation")
            script = self._scripts.popleft()
        return script(body, cancel) if callable(script) else script
