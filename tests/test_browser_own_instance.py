"""The applicant's own Chrome instance (decision 33, spec 4.10 item 6): synthetic directories, fake processes."""

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from intent_job_agent import browser_session as bs
from intent_job_agent import watchdog
from intent_job_agent.form_fill import checkpoint
from intent_job_agent.vendor.applypilot import config

from .conftest import settings

COMPAT = {
    "--window-size=1024,768",
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--deny-permission-prompts",
    "--disable-notifications",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--password-store=basic",
    "--disable-features=InfiniteSessionRestore,PasswordManagerOnboarding",
}


def write_applicant(root: Path, own=None):
    folder = root / "applicant"
    folder.mkdir(parents=True, exist_ok=True)
    raw = {
        "profile": "profile.json",
        "resumes": [{"id": "main", "pdf": "main.pdf", "text": "main.md"}],
        "careful_companies": [],
    }
    if own is not None:
        raw["chrome_user_data_dir"] = str(own)
    (folder / "applicant.yaml").write_text(yaml.safe_dump(raw))


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    own = tmp_path / "ChromeJobs"
    own.mkdir()
    cfg = settings()
    db = root / "coord.db"
    cfg = cfg.model_copy(update={"storage": cfg.storage.model_copy(update={"data_dir": str(root), "db_path": str(db)})})
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE apply_running(id,owner_pid,owner_started,kind,run_id,batch_id,phase)")
    procs = {os.getpid(): watchdog.Proc(os.getpid(), 1, 1, "owner", "synthetic-server")}
    monkeypatch.setattr(watchdog, "processes", lambda: procs)
    monkeypatch.setattr(config, "get_chrome_path", lambda: "synthetic-chrome")
    monkeypatch.setattr(bs, "verify", lambda *a, **k: None)
    launched, windows = [], []

    def popen(args, **kwargs):
        profile = Path(next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")))
        if "--remote-debugging-port=0" not in args:
            windows.append(args)
            return SimpleNamespace(pid=1, poll=lambda: 0, terminate=lambda: None, wait=lambda **k: 0)
        pid = 20000 + len(launched)
        (profile / "DevToolsActivePort").write_text(f"43123\n/devtools/browser/guid-{pid}\n")
        procs[pid] = watchdog.Proc(pid, 1, pid, f"start-{pid}", " ".join(args))
        launched.append(args)
        return SimpleNamespace(pid=pid, poll=lambda: None, terminate=lambda: None, wait=lambda **k: None)

    monkeypatch.setattr(bs.subprocess, "Popen", popen)
    return SimpleNamespace(
        cfg=cfg, root=root, db=db, own=own, home=home, procs=procs, launched=launched, windows=windows
    )


def run_owner(db, run_id="run-1"):
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM apply_running")
        conn.execute("INSERT INTO apply_running VALUES(1,?,'owner','step',?,NULL,NULL)", (os.getpid(), run_id))


def test_without_setting_the_dedicated_profile_is_unchanged(world):
    assert bs.profile_dir(world.cfg) == world.root / "browser/profile"
    write_applicant(world.root)
    assert bs.scope(world.cfg)[2] == str(world.root / "browser/profile")
    run_owner(world.db)
    binding = bs.ensure(world.cfg)
    assert binding.profile == str(world.root / "browser/profile")
    assert COMPAT <= set(world.launched[0])


def test_ensure_launches_own_instance_with_minimal_arguments(world):
    write_applicant(world.root, world.own)
    run_owner(world.db)
    binding = bs.ensure(world.cfg)
    assert binding.profile == str(world.own)
    args = world.launched[0]
    assert f"--user-data-dir={world.own}" in args
    assert "--profile-directory=Default" in args
    assert not COMPAT & set(args)
    assert "--enable-automation" not in args
    assert not (world.root / "browser/owned-profile.json").exists()
    assert not (world.root / "browser/profile").exists()
    assert sorted(p.name for p in world.own.iterdir()) == ["DevToolsActivePort"]  # written by Chrome only


def test_tilde_is_expanded(world):
    write_applicant(world.root, "~/Jobs Chrome")
    (world.home / "Jobs Chrome").mkdir()
    assert bs.profile_dir(world.cfg) == world.home / "Jobs Chrome"


@pytest.mark.parametrize(
    "case",
    ["relative", "missing", "symlink", "daily", "daily-profile", "daily-linux", "workspace-browser"],
)
def test_unsafe_directories_are_refused(world, case):
    target = {
        "relative": Path("ChromeJobs"),
        "missing": world.own.parent / "absent",
        "symlink": world.own.parent / "link",
        "daily": world.home / "Library/Application Support/Google/Chrome",
        "daily-profile": world.home / "Library/Application Support/Google/Chrome/Profile 2",
        "daily-linux": world.home / ".config/google-chrome",
        "workspace-browser": world.root / "browser/mine",
    }[case]
    if case == "symlink":
        target.symlink_to(world.own)
    elif case not in ("relative", "missing"):
        target.mkdir(parents=True)
    write_applicant(world.root, target)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.profile_dir(world.cfg)
    run_owner(world.db)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.ensure(world.cfg)
    assert world.launched == []


def test_open_instance_records_identity_and_the_run_adopts_it(world):
    write_applicant(world.root, world.own)
    opened = bs.open_instance(world.cfg)
    assert (opened.owner_pid, opened.run_id) == (0, "")
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_owner(opened, expected=bs.scope(world.cfg))  # no page control outside a run
    run_owner(world.db)
    adopted = bs.ensure(world.cfg)
    assert (adopted.pid, adopted.generation, adopted.endpoint) == (opened.pid, opened.generation, opened.endpoint)
    assert adopted.run_id == "run-1"
    bs.validate_owner(adopted, expected=bs.scope(world.cfg))
    assert len(world.launched) == 1


def test_open_instance_when_running_only_opens_a_window(world):
    write_applicant(world.root, world.own)
    first = bs.open_instance(world.cfg)
    again = bs.open_instance(world.cfg)
    assert again == first
    assert len(world.launched) == 1
    assert world.windows == [["synthetic-chrome", f"--user-data-dir={world.own}"]]


def test_unrecorded_live_instance_is_not_adopted(world):
    write_applicant(world.root, world.own)
    world.procs[777] = watchdog.Proc(777, 1, 777, "x", f"Google Chrome --user-data-dir={world.own}")
    with pytest.raises(bs.BrowserIdentityLost, match="intent-job-agent chrome"):
        bs.open_instance(world.cfg)
    run_owner(world.db)
    with pytest.raises(bs.BrowserIdentityLost, match="intent-job-agent chrome"):
        bs.ensure(world.cfg)
    assert world.launched == []


def test_switching_to_own_instance_leaves_the_dedicated_browser_alone(world):
    write_applicant(world.root)
    run_owner(world.db)
    old = bs.ensure(world.cfg)
    write_applicant(world.root, world.own)
    new = bs.ensure(world.cfg)
    assert new.profile == str(world.own)
    assert old.pid in world.procs  # never signalled
    assert new.generation != old.generation
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_generation(old, expected=bs.scope(world.cfg))
    assert bs.inspect(world.cfg) == new


def test_dedicated_binding_is_refused_while_own_instance_is_configured(world):
    run_owner(world.db)
    old = bs.ensure(world.cfg)
    write_applicant(world.root, world.own)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_os(old, expected=bs.scope(world.cfg))
    assert bs.inspect(world.cfg) is None


def test_code_never_closes_own_instance(world, monkeypatch):
    write_applicant(world.root, world.own)
    run_owner(world.db)
    binding = bs.ensure(world.cfg)
    monkeypatch.setattr(bs.os, "kill", lambda *a: pytest.fail("signalled the user's own Chrome"))
    with pytest.raises(bs.BrowserIdentityLost):
        bs.close(binding, expected=bs.scope(world.cfg), timeout=0.1)


def test_chrome_command_opens_own_instance(world, monkeypatch, capsys):
    from intent_job_agent import workspace

    monkeypatch.setattr(workspace, "resolve_settings", lambda **k: world.cfg)
    assert workspace.main(["chrome"]) == 2  # no chrome_user_data_dir yet
    assert "chrome_user_data_dir" in capsys.readouterr().err
    write_applicant(world.root, world.own)
    assert workspace.main(["chrome"]) == 0
    assert f"Chrome: {world.own}" in capsys.readouterr().out
    assert len(world.launched) == 1


def test_real_browser_on_own_folder_with_a_space(tmp_path):
    """Real headless Chromium, a synthetic own folder whose path has a space; only about:blank."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from intent_job_agent.store import Store

    own = tmp_path / "Jobs Chrome"
    own.mkdir()
    write_applicant(tmp_path, own)
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "storage": cfg.storage.model_copy(update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "coord.db")})
        }
    )
    store = Store(cfg.storage.db_path, cfg)
    store.begin_activity(watchdog.me(), "form_fill", run_id="synthetic-own")
    store.db.close()
    with sync_api.sync_playwright() as p:
        cfg = cfg.model_copy(
            update={
                "apply": cfg.apply.model_copy(
                    update={"browser_executable": p.chromium.executable_path, "browser_headless": True}
                )
            }
        )
        binding = bs.ensure(cfg)
        try:
            assert binding.profile == str(own)
            bs.validate_owner(binding, expected=bs.scope(cfg))
            browser = bs.connect(p, binding, expected=bs.scope(cfg))
            assert browser.contexts[0].pages is not None
            browser.close()
            assert bs.ensure(cfg).pid == binding.pid
        finally:
            os.kill(binding.pid, 15)


def test_checkpoint_service_checks_the_same_profile():
    assert "browser_profile" in checkpoint.Config.model_fields
