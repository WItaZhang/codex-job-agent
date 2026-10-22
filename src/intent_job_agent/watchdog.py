"""Keeping an apply step from hanging (spec 4.10 item 11, section 10 items 107-114).

- The process table (`ps`), an agent's process tree and how to end all of it: its process group, and every
  descendant seen while it ran, told apart by pid and start time, even after it left the group or lost its parent.
- `LIVE`: the agent run in progress in this process (one applying Chrome, so one at a time), so apply_status can say
  when it last wrote anything and stop_applying can stop it.
- `follow`: feed an agent its instructions and read its output line by line until it ends, with a limit on every wait.
- `kill_new_drivers`: the Playwright drivers a read started, ended when the read runs out of time.
- `me`, `alive`, `kill_orphans`: which tool server runs a step, whether it still does, and the agents one that
  stopped left behind.
- `ChromeWatch`: the applying Chrome's tabs while an agent works (CDP Target.getTargets, which knows which tab opened
  which), and what changed that the agent did not do: someone may be using that Chrome (spec section 10 item 114).

Nothing here clicks, types or navigates: the Chrome is only listed.
"""

import base64
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    started: str  # ps lstart: with the pid, it tells a process apart from a later one given the same pid
    command: str


def processes() -> dict[int, Proc]:
    """Every process: pid, parent, process group, start time and command line. The start time is written the same
    way whatever the caller's locale and time zone, so two tool servers read the same process the same way. Raises
    SubprocessError when ps fails or its list leaves out this very process: such a list says nothing (review #1)."""
    result = subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,lstart=,command="],
        capture_output=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    )
    if result.returncode != 0:
        raise subprocess.SubprocessError(f"ps exited with {result.returncode}")
    table = {}
    for line in result.stdout.decode("utf-8", errors="replace").split("\n"):
        parts = line.split(maxsplit=3)
        if len(parts) < 4 or not all(part.isdigit() for part in parts[:3]):
            continue
        rest = parts[3]  # lstart is always 24 characters ("Tue Oct 21 18:37:30 2026"), then the command
        pid, ppid, pgid = (int(part) for part in parts[:3])
        table[pid] = Proc(pid, ppid, pgid, rest[:24], rest[24:].strip())
    if os.getpid() not in table:
        raise subprocess.SubprocessError("ps did not list this process")
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


def me() -> tuple[int, str]:
    """This process: its pid and start time (a tool server, as the apply_running table names it). Raises
    SubprocessError when ps cannot tell."""
    pid = os.getpid()
    return pid, processes()[pid].started


def alive(pid: int, started: str) -> bool:
    """Whether the process with this pid and start time still runs. Only a process list that could be read, or then
    the system itself, can say it is gone; when ps fails, or the list leaves the pid out while the system still has
    it, the process counts as alive (review #1)."""
    try:
        proc = processes().get(int(pid))
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: never treat a live tool server as gone
    if proc is not None:
        return proc.started == started  # the same pid with another start time is another process
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: it exists, under another user
        return True
    return True


def kill_orphans(marks) -> list[int]:
    """End the agents a stopped tool server left behind, with everything they started (spec section 10 item 112):
    processes whose parent is now the system (1) and whose command line holds one of `marks` (this data folder's
    MCP configs). Another live tool server's agents have that server as their parent."""
    try:
        table = processes()
    except (OSError, subprocess.SubprocessError):
        return []
    left = {p.pid for p in table.values() if p.ppid == 1 and any(mark in p.command for mark in marks)}
    found = set(left)
    for pid in left:
        found |= descendants(pid, table)
        try:
            os.killpg(pid, signal.SIGKILL)  # an agent leads its own process group (start_new_session)
        except (ProcessLookupError, PermissionError):
            pass
    return sorted(pid for pid in found if _kill(pid))


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
    whether it still runs, and a stop signal. The last run's job and end time stay until the next one begins. A stop
    asked for a job whose agent has not started yet is kept, so the agent ends as soon as it starts."""

    def __init__(self):
        self._lock = threading.Lock()
        self._run: dict | None = None
        self._armed: tuple[str, tuple] | None = None  # (job id, modes) of a stop asked before its agent started

    def begin(self, job_id: str, mode: str) -> threading.Event:
        stop = threading.Event()
        now = time.time()
        with self._lock:
            if self._armed is not None and self._armed[0] == job_id:
                if mode in self._armed[1]:
                    stop.set()
                self._armed = None
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

    def stop(self, modes, job_id: str | None = None) -> str | None:
        """Ask the running agent (of `job_id`, when given) to stop when its mode is one of `modes`; returns its job id.
        When no agent of `job_id` runs yet, the stop waits for it (`begin`) and None is returned."""
        with self._lock:
            run = self._run
            if run is not None and run["alive"] and job_id in (None, run["job_id"]):
                if run["mode"] not in modes:
                    return None
                run["stop"].set()
                return run["job_id"]
            if job_id is not None:
                self._armed = (job_id, tuple(modes))
            return None

    def disarm(self) -> None:
        """Forget a stop still waiting for its agent (the step it was asked in has ended)."""
        with self._lock:
            self._armed = None


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


# --- the applying Chrome while an agent works (spec section 10 item 114) -------------------------------------------

CHROME_USE = "可能有人在用投递用的 Chrome"
IGNORED = ("chrome-extension://", "devtools://")  # not tabs a person uses (as readback.IGNORED_SCHEMES)
PAGE_URL = re.compile(r"Page URL: (\S+)")
MAX_MESSAGE = 32 * 2**20


class ChromeDown(OSError):
    """The applying Chrome does not answer on its debugging port."""


def _clock(at: float) -> str:
    return datetime.fromtimestamp(at).strftime("%H:%M:%S")


def _send(sock: socket.socket, payload: bytes) -> None:
    """One masked text frame (a client must mask, RFC 6455 5.3)."""
    mask, size = os.urandom(4), len(payload)
    if size < 126:
        length = bytes([0x80 | size])
    elif size < 2**16:
        length = bytes([0x80 | 126]) + struct.pack(">H", size)
    else:
        length = bytes([0x80 | 127]) + struct.pack(">Q", size)
    sock.sendall(bytes([0x81]) + length + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _receive(reader) -> bytes:
    """One whole message: its frames joined (text, then continuations); pings and pongs skipped."""
    parts, total = [], 0
    while True:
        head = reader.read(2)
        if len(head) < 2:
            raise ConnectionError("the websocket closed")
        fin, opcode, size = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x7F
        if size == 126:
            size = struct.unpack(">H", reader.read(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", reader.read(8))[0]
        mask = reader.read(4) if head[1] & 0x80 else b""
        total += size
        if total > MAX_MESSAGE:
            raise ConnectionError("the websocket message is too large")
        payload = reader.read(size)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise ConnectionError("the websocket closed")
        if opcode in (0x9, 0xA):
            continue
        parts.append(payload)
        if fin:
            return b"".join(parts)


def cdp_targets(port: int, timeout: float = 3.0) -> list[dict]:
    """The applying Chrome's targets with their openers: CDP Target.getTargets over the browser's websocket (/json/list
    has no openerId). A minimal RFC 6455 client - one request, its answer - that never attaches to a page. Raises
    ChromeDown when nothing answers on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=timeout) as resp:
            path = urlsplit(json.load(resp)["webSocketDebuggerUrl"]).path
    except OSError as error:
        raise ChromeDown(str(error)) from None
    with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as sock:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{int(port)}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        reader = sock.makefile("rb")
        if b" 101 " not in reader.readline():
            raise ConnectionError("CDP refused the websocket")
        while reader.readline() not in (b"\r\n", b""):
            pass
        _send(sock, json.dumps({"id": 1, "method": "Target.getTargets"}).encode())
        while True:
            message = json.loads(_receive(reader))
            if message.get("id") == 1:
                infos = (message.get("result") or {}).get("targetInfos") or []
                return [
                    {
                        "id": i.get("targetId"),
                        "type": i.get("type"),
                        "url": i.get("url", ""),
                        "openerId": i.get("openerId"),
                    }
                    for i in infos
                ]


def _host(url: str) -> str:
    parts = urlsplit(str(url))
    return (parts.hostname or "").lower() if parts.scheme in ("http", "https") else ""


def _same(a: str, b: str) -> bool:
    return a.split("#", 1)[0].rstrip("/") == b.split("#", 1)[0].rstrip("/")


def _tab_action(event: dict) -> str | None:
    """What an agent tool call did to the tabs: "new", "close" or nothing."""
    tool, data = str(event.get("name", "")).rsplit("__", 1)[-1], event.get("input") or {}
    if tool == "browser_tabs":
        return data.get("action") if data.get("action") in ("new", "close") else None
    return {"browser_tab_new": "new", "browser_tab_close": "close", "browser_close": "close"}.get(tool)


class ChromeWatch:
    """The applying Chrome's tabs while one agent works, listed every few seconds (`check`), never touched: the tabs
    open before it started, every tab seen since with the tab that opened it and the addresses it showed, and when
    Chrome stopped answering. `report` tells what changed that the agent's own tool calls do not explain. `own`: tabs
    open before the run that the agent works in (a resumed job's held tab, spec section 10 item 100)."""

    def __init__(self, port: int, blocked_origins, targets=cdp_targets, clock=time.time, own=()):
        self.port, self._targets, self._clock = port, targets, clock
        self.own = {tab_id for tab_id in own if tab_id}
        self.blocked = {(urlsplit(o).hostname or "").lower().removeprefix("www.") for o in blocked_origins} - {""}
        self.before: dict[str, str] | None = None  # the tabs open before the agent started, and their addresses
        self.tabs: dict[str, dict] = {}  # every tab seen: its opener and the addresses it showed
        self.present: set[str] = set()  # the tabs at the last check
        self.down: float | None = None  # when Chrome stopped answering, after it had answered
        self.up: float | None = None  # when it answered again

    def check(self) -> None:
        now = self._clock()
        try:
            listed = self._targets(self.port)
        except ChromeDown:
            if self.before is not None and self.down is None:
                self.down = now
            return
        except Exception:  # noqa: BLE001 - a list that could not be read says nothing either way
            return
        pages = [t for t in listed if t.get("type") == "page" and not str(t.get("url", "")).startswith(IGNORED)]
        if self.down is not None and self.up is None:
            self.up = now
        if self.before is None:
            self.before = {t["id"]: str(t.get("url", "")) for t in pages}
        for t in pages:
            entry = self.tabs.setdefault(t["id"], {"opener": t.get("openerId"), "urls": []})
            if str(t.get("url", "")) not in entry["urls"]:
                entry["urls"].append(str(t.get("url", "")))
        self.present = {t["id"] for t in pages}

    def _personal(self, host: str) -> bool:
        """A site the agent cannot open (apply.blocked_origins and their subdomains) or a school system."""
        return any(host == site or host.endswith("." + site) for site in self.blocked) or "edu" in host.split(".")

    def report(self, events: list[dict], pages=()) -> list[str]:
        """What changed in the applying Chrome that the agent's own tool calls (`events`; `pages`: the Page URLs its
        browser answered with) do not explain, one short line each, naming only sites (spec section 10 item 114):
        Chrome stopped answering; more new tabs without an opener than the agent opened; more of them closed than the
        agent closed; tabs open before the run closed or moved; a tab of the run on a site the agent cannot open."""
        if self.before is None:
            return []
        if self.down is not None:  # a Chrome that went away has other tabs when it is back: nothing else to compare
            line = f"投递用的 Chrome 从 {_clock(self.down)} 起连不上了（被关掉、崩溃，或调试端口没有响应）"
            return [line + (f"，{_clock(self.up)} 又连上了" if self.up is not None else "")]
        lines = []
        actions = [_tab_action(event) for event in events]
        visited = {_host((e.get("input") or {}).get("url", "")) for e in events} | {_host(url) for url in pages}
        new = {tab_id: entry for tab_id, entry in self.tabs.items() if tab_id not in self.before}
        # Opened without an opener and used: a blank tab nobody used (Playwright may open one) says nothing.
        bare = {
            tab_id: entry
            for tab_id, entry in new.items()
            if not entry["opener"] and any(url not in ("", "about:blank") for url in entry["urls"])
        }
        extra = len(bare) - actions.count("new")
        if extra > 0:
            hosts = sorted({h for e in bare.values() for h in map(_host, e["urls"]) if h and h not in visited})
            lines.append(f"运行中多了 {extra} 个不是 agent 开的新标签页（{'、'.join(hosts) or '新标签页'}）")
        gone = len([tab_id for tab_id in bare if tab_id not in self.present]) - actions.count("close")
        if gone > 0:
            lines.append(f"这次运行开的标签页有 {gone} 个被关掉了，不是 agent 关的")
        old = {tab_id: url for tab_id, url in self.before.items() if tab_id not in self.own}
        shut = [tab_id for tab_id in old if tab_id not in self.present]
        moved = {
            tab_id: [u for u in self.tabs[tab_id]["urls"] if not _same(u, url)]
            for tab_id, url in old.items()
            if tab_id in self.present
        }
        moved = {tab_id: urls for tab_id, urls in moved.items() if urls}
        if shut or moved:
            counts = [f"{len(shut)} 个被关掉"] * bool(shut) + [f"{len(moved)} 个换了地址"] * bool(moved)
            hosts = sorted({_host(u) for urls in moved.values() for u in urls} - {""})
            lines.append("开始前就开着的标签页里，" + "、".join(counts) + (f"（{'、'.join(hosts)}）" if hosts else ""))
        personal = sorted({h for e in new.values() for h in map(_host, e["urls"]) if h and self._personal(h)})
        if personal:
            lines.append(f"这次运行开的标签页打开过 {'、'.join(personal)}：agent 打不开这类网站")
        return lines
