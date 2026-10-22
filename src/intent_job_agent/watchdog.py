"""Keeping an apply step from hanging (spec 4.10 item 11, section 10 items 107-114).

- The process table (`ps`), an agent's process tree and how to end all of it: its process group, and every
  descendant seen while it ran, told apart by pid and start time, even after it left the group or lost its parent.
- `LIVE`: the agent run in progress in this process (one applying Chrome, so one at a time), so apply_status can say
  when it last wrote anything and stop_applying can stop it.
- `follow`: feed an agent its instructions and read its output line by line until it ends, with a limit on every wait.
- `kill_new_drivers`: the Playwright drivers a read started, ended when the read runs out of time.

Nothing here clicks, types or navigates.
"""

import os
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    started: str  # ps lstart: with the pid, it tells a process apart from a later one given the same pid
    command: str


def processes() -> dict[int, Proc]:
    """Every process: pid, parent, process group, start time and command line."""
    out = subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,lstart=,command="],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    ).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) < 4 or not all(part.isdigit() for part in parts[:3]):
            continue
        rest = parts[3]  # lstart is always 24 characters ("Tue Oct 21 18:37:30 2026"), then the command
        pid, ppid, pgid = (int(part) for part in parts[:3])
        table[pid] = Proc(pid, ppid, pgid, rest[:24], rest[24:].strip())
    return table


def descendants(root: int, table: dict[int, Proc]) -> set[int]:
    children: dict[int, list[int]] = {}
    for proc in table.values():
        children.setdefault(proc.ppid, []).append(proc.pid)
    found, todo = set(), [root]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found and child != root:
                found.add(child)
                todo.append(child)
    return found


def _kill(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def children() -> set[tuple[int, str]]:
    """This process's child processes now, as (pid, start time)."""
    me = os.getpid()
    try:
        return {(p.pid, p.started) for p in processes().values() if p.ppid == me}
    except (OSError, subprocess.SubprocessError):
        return set()


def kill_new_drivers(before: set[tuple[int, str]]) -> list[int]:
    """End the Playwright drivers this process started since `before` (`children()` then), with their descendants: a
    read stuck on a page that never answers fails once its driver is gone (spec section 10 item 108)."""
    try:
        table = processes()
    except (OSError, subprocess.SubprocessError):
        return []
    me = os.getpid()
    drivers = {
        p.pid for p in table.values() if p.ppid == me and (p.pid, p.started) not in before and "run-driver" in p.command
    }
    for pid in list(drivers):
        drivers |= descendants(pid, table)
    return sorted(pid for pid in drivers if _kill(pid))


class Tree:
    """An agent and the processes it started. `scan` remembers every descendant (pid and start time) while the agent
    runs, so one that moved to its own session, or whose parent already quit, is still found when the run ends."""

    def __init__(self, pid: int):
        self.pid = pid
        self.started: str | None = None  # the agent's own start time, once seen
        self.seen: dict[int, str] = {}

    def scan(self) -> None:
        try:
            table = processes()
        except (OSError, subprocess.SubprocessError):
            return
        agent = table.get(self.pid)
        if agent is None or (self.started is not None and agent.started != self.started):
            return  # the agent is gone: its pid now belongs to someone else, or to no one
        self.started = agent.started
        for pid in descendants(self.pid, table):
            self.seen[pid] = table[pid].started

    def kill(self, alive: bool) -> list[int]:
        """SIGKILL the agent's process group (only while it is the agent's: `alive`, or a member seen before), then
        every descendant seen that is still the same process, and their descendants now. Returns the pids signalled."""
        try:
            table = processes()
        except (OSError, subprocess.SubprocessError):
            table = {}
        same = {pid for pid, started in self.seen.items() if pid in table and table[pid].started == started}
        if alive or any(table[pid].pgid == self.pid for pid in same):
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        agent = table.get(self.pid)
        if agent is not None and agent.started == self.started:
            same |= descendants(self.pid, table)
        for pid in list(same):
            same |= descendants(pid, table)
        return sorted(pid for pid in same if _kill(pid))


class Live:
    """The agent run in progress in this process: which job, its mode, when it started and last wrote anything,
    whether it still runs, and a stop signal. The last run's job and end time stay until the next one begins."""

    def __init__(self):
        self._lock = threading.Lock()
        self._run: dict | None = None

    def begin(self, job_id: str, mode: str) -> threading.Event:
        stop = threading.Event()
        now = time.time()
        with self._lock:
            self._run = {"job_id": job_id, "mode": mode, "started": now, "last": now, "alive": True, "stop": stop}
        return stop

    def touch(self) -> None:
        with self._lock:
            if self._run is not None and self._run["alive"]:
                self._run["last"] = time.time()

    def end(self, stop: threading.Event) -> None:
        with self._lock:
            if self._run is not None and self._run["stop"] is stop:
                self._run.update(alive=False, last=time.time())

    def view(self) -> dict | None:
        with self._lock:
            return {k: v for k, v in self._run.items() if k != "stop"} if self._run else None

    def stop(self, modes) -> str | None:
        """Ask the running agent to stop when its mode is one of `modes`; returns its job id, or None."""
        with self._lock:
            if self._run is None or not self._run["alive"] or self._run["mode"] not in modes:
                return None
            self._run["stop"].set()
            return self._run["job_id"]


LIVE = Live()


def _feed(pipe, data: bytes) -> None:
    """The agent's instructions, written from their own thread: an agent that never reads them cannot hold the run."""
    try:
        pipe.write(data)
        pipe.close()
    except (OSError, ValueError):  # the agent is gone or was ended; its run ends on its own terms
        pass


def follow(
    proc: subprocess.Popen,
    text: str,
    on_line,
    *,
    timeout: float,
    stall: float,
    drain: float,
    interval: float,
    stop: threading.Event | None = None,
    ticks=(),
) -> str | None:
    """Feed `text` to the agent and hand each line of its output to `on_line` until it ends. Returns why the run was
    cut short: "timeout" (`timeout` seconds in all), "stalled" (no output for `stall` seconds), "stopped" (`stop` was
    set), or None when it ended on its own: its output closed, or it quit and `drain` seconds passed (whatever still
    holds the output open is not the agent). `ticks` run every `interval` seconds, the first before any wait."""
    threading.Thread(target=_feed, args=(proc.stdin, text.encode("utf-8")), name="agent-input", daemon=True).start()
    fd = proc.stdout.fileno()
    start = last = time.monotonic()
    exited, next_tick, buffer, why = None, start, b"", None
    while True:
        now = time.monotonic()
        if now >= next_tick:
            for tick in ticks:
                try:
                    tick()
                except Exception:  # noqa: BLE001 - a look at the processes or the browser never stops the run
                    pass
            next_tick = time.monotonic() + interval
        if exited is None and proc.poll() is not None:
            exited = now
        if exited is not None:
            if now - exited >= drain:
                break
        elif stop is not None and stop.is_set():
            why = "stopped"
            break
        elif now - start >= timeout:
            why = "timeout"
            break
        elif now - last >= stall:
            why = "stalled"
            break
        ready, _, _ = select.select([fd], [], [], max(0.0, min(interval, next_tick - time.monotonic())))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        last = time.monotonic()
        *lines, buffer = (buffer + chunk).split(b"\n")
        for line in lines:
            on_line(line.decode("utf-8", errors="replace") + "\n")
    if buffer:
        on_line(buffer.decode("utf-8", errors="replace"))
    return why
