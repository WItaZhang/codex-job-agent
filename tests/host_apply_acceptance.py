"""Package-external G10 harness. Only synthetic temporary data; never a product entry point."""

import argparse
import functools
import json
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from intent_job_agent import apply_bridge, browser_session
from intent_job_agent.mcp_server import build_server
from intent_job_agent.store import Store
from intent_job_agent.tools import TOOL_TIERS, Toolbox

from . import test_gate_handoff as gates
from .apply_fakes import FormFillFake
from .test_slice6 import add_jobs, write_letters


def emit(root, kind, **payload):
    with (root / "events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"kind": kind, "at": time.monotonic(), **payload}, ensure_ascii=False) + "\n")


def require_root(root):
    root = root.resolve()
    allowed = (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())
    if not any(root.is_relative_to(parent) for parent in allowed):
        raise ValueError("Only an explicitly marked synthetic temporary directory is allowed")
    if (root / "SYNTHETIC_G10").read_text() != "no real business\n":
        raise ValueError("Synthetic marker required")
    return root


def poison(root):
    def denied(*args, **kwargs):
        emit(root, "sink_refused")
        raise AssertionError("Synthetic test forbids external network/browser/agent")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    apply_bridge.run_application = denied
    apply_bridge.render_letter_pdf = denied
    browser_session.ensure = denied
    original = subprocess.Popen

    def process(args, *rest, **kwargs):
        # Product owner identity may read ps; no other subprocess is needed by these fake sinks.
        if isinstance(args, (list, tuple)) and Path(args[0]).name == "ps":
            return original(args, *rest, **kwargs)
        return denied()

    subprocess.Popen = process
    try:
        socket.create_connection(("127.0.0.1", 9))
    except AssertionError:
        emit(root, "network_poison_positive_control")
    else:
        raise AssertionError("Network poison failed")


class AuditedRunner(gates.GateRunner):
    def __init__(self, root, pause):
        super().__init__()
        self.root, self.pause = root, pause
        self.blocked, self.proceed = threading.Event(), threading.Event()
        self.submits = 0

    def __call__(self, spec):
        if spec.mode == "submit":
            self.submits += 1
            if self.pause and self.submits == 2:
                self.blocked.set()
                emit(self.root, "await_status_poll", job_id=spec.job_id)
                if not self.proceed.wait(60):
                    raise AssertionError("Synthetic runner was not polled")
        emit(self.root, "runner", job_id=spec.job_id, company=spec.company, mode=spec.mode)
        return super().__call__(spec)


def fixture(root, *, seed=False, pause=False):
    root = require_root(root)
    if seed:
        if (root / "data").exists():
            raise ValueError("Refuse to overwrite an existing fixture")
        box = gates.box.__wrapped__(root)
    else:
        settings = gates.settings()
        box = Toolbox(settings, Store(root / "agent.sqlite", settings), data_dir=root / "data")
    box.settings = box.settings.model_copy(
        update={"apply": box.settings.apply.model_copy(update={"review_first": 0, "notify": False})}
    )
    box.apply_runner = AuditedRunner(root, pause)
    box.form_fill_runner = FormFillFake(box)
    box.letter_renderer = lambda text, path, name, day: path.write_bytes(b"%PDF-1.4 SYNTHETIC")
    box.apply_in_background = pause
    box.notifier = lambda *args: None
    if seed:
        ids = add_jobs(box, ["Synthetic Alpha", "Stripe, Inc.", "Synthetic Beta", "Synthetic Gamma"])
        write_letters(box, ids)
        assert box.get_apply_setup()["ready"]
        emit(root, "fixture", ids=ids, ready=True)
    return box


def instrument(box, root):
    for name in TOOL_TIERS:
        fn = getattr(box, name)

        def wrap(fn, name):
            @functools.wraps(fn)
            def call(*args, **kwargs):
                emit(root, "tool_call", name=name, args=args, kwargs=kwargs)
                try:
                    result = fn(*args, **kwargs)
                    emit(root, "tool_result", name=name, result=result)
                    return result
                finally:
                    if name == "apply_status" and box.apply_runner.blocked.is_set():
                        emit(root, "poll_releases_fake_runner")
                        box.apply_runner.proceed.set()
                    (root / "db-state.json").write_text(
                        json.dumps({"applications": box.store.applications(), "attempts": box.store.apply_attempts()}),
                        encoding="utf-8",
                    )

            return call

        setattr(box, name, wrap(fn, name))


def dry_poll(root):
    box = fixture(root, seed=True, pause=True)
    poison(root)
    instrument(box, root)
    batch = box.prepare_apply_batch()
    box.start_applying(batch["batch_id"], batch["summary"])
    assert box.apply_runner.blocked.wait(10), "second submit did not reach the barrier"
    assert len([s for s in box.apply_runner.calls if s.mode == "submit"]) == 1
    box.apply_status()  # The same tool call the host must use releases only the fake timing barrier.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = box.apply_status()
        if status["status"] != "running":
            break
        time.sleep(0.01)
    assert status["status"] == "done"
    assert len(box.apply_runner.calls) == 4
    assert box.store.applications()["j01"]["status"] == "waiting"
    emit(root, "dry_poll_passed")
    print(json.dumps({"passed": True, "same_batch": batch["batch_id"], "runner_calls": 4}))


def dry_run(root):
    box = fixture(root, seed=True)
    poison(root)
    first = box.prepare_apply_batch(stop_before_submit=True)
    assert not first["missing_letters"] and not first["problems"]
    box.start_applying(first["batch_id"], first["summary"])
    box.apply_status()
    assert len(box.apply_runner.calls) == 4
    assert all(spec.mode == "stop" for spec in box.apply_runner.calls)
    ids = [s.job_id for s in box.apply_runner.calls]
    box.mark_applications(ids, "pending", actor="delegated_coordinator")
    second = box.prepare_apply_batch()
    box.start_applying(second["batch_id"], second["summary"])
    box.apply_status()
    tail = box.apply_runner.calls[4:]
    assert len(tail) == 4
    assert all(s.mode == ("stop" if s.company == "Stripe, Inc." else "submit") for s in tail)
    states = box.store.applications()
    assert states["j01"]["status"] == "waiting"
    assert all(states[j]["status"] == "applied" for j in ("j00", "j02", "j03"))
    emit(root, "dry_run_passed", applications=states)
    print(json.dumps({"passed": True, "runner_calls": len(box.apply_runner.calls), "synthetic": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "dry-run", "dry-poll", "serve"])
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = require_root(args.root)
    if args.mode == "dry-poll":
        dry_poll(root)
    elif args.mode == "dry-run":
        dry_run(root)
    elif args.mode == "seed":
        fixture(root, seed=True)
    else:
        box = fixture(root, pause=True)
        poison(root)
        instrument(box, root)
        build_server(box).run("stdio")


if __name__ == "__main__":
    main()
