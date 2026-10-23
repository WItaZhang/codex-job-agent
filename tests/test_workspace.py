"""Synthetic R1 workspace contract; no real credentials, browsers or network."""

import subprocess
from pathlib import Path

import pytest
import yaml

from intent_job_agent.config import Settings
from intent_job_agent.domain import InvariantError
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox


def workspace(tmp_path):
    from intent_job_agent.workspace import initialize, load_workspace

    root = tmp_path / "synthetic-workspace"
    initialize(root)
    return root, load_workspace(root)


def test_workspace_idempotent_and_cwd_independent(tmp_path, monkeypatch):
    from intent_job_agent.workspace import initialize, load_workspace

    root, settings = workspace(tmp_path)
    sentinel = root / "user-sentinel.txt"
    sentinel.write_text("synthetic existing protection and authorization sentinel")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    initialize(root)
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert load_workspace(root).storage == settings.storage
    assert settings.execution_mode == "local_only"
    assert Path(settings.storage.data_dir).is_absolute()
    assert not (other / "data").exists()
    assert not list(root.rglob("profile.json"))
    assert not list(root.rglob(".env"))


def test_corrupt_or_escaping_workspace_never_falls_back(tmp_path):
    from intent_job_agent.workspace import initialize, load_workspace

    root, _ = workspace(tmp_path)
    path = root / "settings.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["execution_mode"] = "legacy"
    path.write_text(yaml.safe_dump(raw))
    original = path.read_bytes()
    for action in (lambda: load_workspace(root), lambda: Settings.load(path), lambda: initialize(root)):
        with pytest.raises(InvariantError):
            action()
    assert path.read_bytes() == original
    assert not list(root.rglob("*.sqlite"))


def test_workspace_paths_are_distinct_and_contained(tmp_path):
    from intent_job_agent.workspace import initialize, load_workspace

    root, a = workspace(tmp_path)
    second = tmp_path / "second"
    initialize(second)
    b = load_workspace(second)
    assert a.storage.db_path != b.storage.db_path
    raw = yaml.safe_load((root / "settings.yaml").read_text())
    raw["apply"]["letters_dir"] = "../outside"
    (root / "settings.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(InvariantError):
        load_workspace(root)


@pytest.mark.parametrize(
    "name,args",
    [
        ("check_board", ("greenhouse", "synthetic")),
        ("fetch_boards", ()),
        ("discover_jobs", ()),
        ("start_form_fill", ("synthetic.yaml",)),
        ("start_applying", ("batch", "summary")),
        ("prepare_resume", (["job"],)),
        ("resume_applying", (["job"], "summary")),
        ("save_cover_letter", ("job", "resume", "synthetic text")),
        ("release_careful_companies", (["Synthetic"], "synthetic request", "user")),
    ],
)
def test_direct_toolbox_rejects_before_any_side_effect(tmp_path, monkeypatch, name, args):
    _, settings = workspace(tmp_path)
    store = Store(settings.storage.db_path, settings)
    box = Toolbox(settings, store)

    def forbidden(*args, **kwargs):
        pytest.fail("real side effect or private input reached")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(box, "_ready_applicant", forbidden)
    monkeypatch.setattr(box, "_recover", forbidden)
    with pytest.raises(InvariantError, match="local-only"):
        getattr(box, name)(*args)
    assert store.activity() is None
    store.db.close()


def test_local_status_never_recovers_or_loads_env(tmp_path, monkeypatch):
    from intent_job_agent.vendor.applypilot import config

    _, settings = workspace(tmp_path)
    store = Store(settings.storage.db_path, settings)
    box = Toolbox(settings, store)

    def forbidden(*args, **kwargs):
        pytest.fail("recovery or credential loading reached")

    monkeypatch.setattr(box, "_recover", forbidden)
    monkeypatch.setattr(config, "load_env", forbidden)
    assert box.apply_status()["execution_mode"] == "local_only"
    assert box.get_apply_setup()["ready"] is False
    assert box.get_discovery_setup()["execution_mode"] == "local_only"
    store.db.close()


def test_direct_adapter_rejects_before_vendor_configuration(tmp_path, monkeypatch):
    from intent_job_agent import apply_bridge, applypilot_bridge

    _, settings = workspace(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("vendor configure reached")

    monkeypatch.setattr(applypilot_bridge, "configure", forbidden)
    with pytest.raises(InvariantError, match="local-only"):
        apply_bridge.ensure_chrome(settings)
    with pytest.raises(InvariantError, match="local-only"):
        applypilot_bridge.run_sources(tmp_path, settings, ["jobspy"], False)


def test_explicit_legacy_config_preserves_relative_paths(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "configs/default.yaml"
    (tmp_path / "legacy.yaml").write_bytes(source.read_bytes())
    monkeypatch.chdir(tmp_path)
    settings = Settings.load("legacy.yaml")
    assert settings.execution_mode == "legacy"
    assert settings.storage.db_path == "data/local/agent.sqlite"


def test_all_registered_nonlocal_tools_reject_direct_calls(tmp_path):
    from intent_job_agent.tools import LOCAL_READ_TOOLS, TOOL_TIERS

    _, settings = workspace(tmp_path)
    store = Store(settings.storage.db_path, settings)
    box = Toolbox(settings, store)
    assert len(TOOL_TIERS) == 47  # adding a tool requires reviewing the R1 side-effect inventory
    assert LOCAL_READ_TOOLS <= TOOL_TIERS.keys()
    for name in TOOL_TIERS.keys() - LOCAL_READ_TOOLS:
        # Guard must run before even interpreting arguments/looking up personal input.
        with pytest.raises(InvariantError, match="local-only"):
            getattr(box, name)()
    store.db.close()


@pytest.mark.parametrize(
    "method,args",
    [
        ("ensure_chrome", ()),
        ("open_tabs", ()),
        ("targets", ()),
        ("fill", (None,)),
        ("read", ({}, Path("/synthetic-unused"), None)),
        ("tab_state", ({},)),
    ],
)
def test_direct_form_fill_adapter_is_closed(tmp_path, monkeypatch, method, args):
    from intent_job_agent import apply_bridge
    from intent_job_agent.form_fill.runner import FormFillRunner

    _, settings = workspace(tmp_path)
    runner = FormFillRunner(tmp_path, settings, tmp_path / "absent-synthetic-profile")
    monkeypatch.setattr(apply_bridge, "configure", lambda *a: pytest.fail("configured browser"))
    with pytest.raises(InvariantError, match="local-only"):
        getattr(runner, method)(*args)


def test_real_model_and_application_adapter_closed(tmp_path, monkeypatch):
    from intent_job_agent import apply_bridge, llm

    _, settings = workspace(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("process launched"))
    with pytest.raises(InvariantError, match="local-only"):
        apply_bridge.ChromeRunner(tmp_path, settings, tmp_path / "absent")(None)
    with pytest.raises(InvariantError, match="local-only"):
        apply_bridge.run_application(None, tmp_path, settings, tmp_path / "absent", 9999)
    with pytest.raises(InvariantError, match="local-only"):
        llm.make_client(settings)


def test_existing_unmanaged_directory_is_not_overwritten(tmp_path):
    from intent_job_agent.workspace import initialize

    (tmp_path / "keep").write_text("synthetic")
    with pytest.raises(InvariantError):
        initialize(tmp_path)
    assert (tmp_path / "keep").read_text() == "synthetic"
    assert not (tmp_path / "settings.yaml").exists()


def test_workspace_symlink_escape_rejected(tmp_path):
    from intent_job_agent.workspace import load_workspace

    root, _ = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "apply").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvariantError):
        load_workspace(root)


def test_cli_path_conflict_and_missing_workspace(tmp_path):
    import sys

    for module, args in [("mcp_server", []), ("form_fill.cli", ["report", "synthetic"])]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                f"intent_job_agent.{module}",
                *args,
                "--config",
                "legacy.yaml",
                "--workspace",
                str(tmp_path),
            ],
            capture_output=True,
        )
        assert result.returncode == 2
        assert b"Traceback" not in result.stderr
        result = subprocess.run(
            [sys.executable, "-m", f"intent_job_agent.{module}", *args, "--workspace", str(tmp_path)],
            capture_output=True,
        )
        assert result.returncode == 2
        assert b"intent-job-agent init" in result.stderr
        assert b"Traceback" not in result.stderr


def test_explicit_config_in_workspace_never_ignores_selected_file(tmp_path):
    root, _ = workspace(tmp_path)
    with pytest.raises((InvariantError, OSError)):
        Settings.load(root / "does-not-exist.yaml")
    custom = root / "custom.yaml"
    custom.write_bytes((root / "settings.yaml").read_bytes())
    with pytest.raises(InvariantError, match="settings.yaml"):
        Settings.load(custom)
    assert Settings.load(root / "settings.yaml").execution_mode == "local_only"


def test_canonical_config_symlink_cannot_downgrade_workspace(tmp_path):
    root, _ = workspace(tmp_path)
    source = Path(__file__).resolve().parents[1] / "configs/default.yaml"
    legacy = tmp_path / "legacy.yaml"
    legacy.write_bytes(source.read_bytes())
    canonical = root / "settings.yaml"
    canonical.unlink()
    canonical.symlink_to(legacy)
    with pytest.raises(InvariantError):
        Settings.load(canonical)


@pytest.mark.parametrize("kind", ["corrupt", "symlink"])
def test_workspace_marker_must_be_regular_and_valid(tmp_path, kind):
    from intent_job_agent.workspace import load_workspace

    root, _ = workspace(tmp_path)
    marker = root / "workspace.json"
    if kind == "corrupt":
        marker.write_text("{broken")
    else:
        external = tmp_path / "marker.json"
        external.write_bytes(marker.read_bytes())
        marker.unlink()
        marker.symlink_to(external)
    with pytest.raises(InvariantError):
        Settings.load(root / "settings.yaml")
    with pytest.raises(InvariantError):
        load_workspace(root)


def test_sink_spies_zero_even_with_valid_synthetic_inputs(tmp_path, monkeypatch):
    import os
    import threading

    import httpx

    from intent_job_agent import apply_bridge, notify, watchdog
    from intent_job_agent.form_fill import readback
    from intent_job_agent.vendor.applypilot import config

    from .test_slice6 import write_applicant

    root, settings = workspace(tmp_path)
    write_applicant(root)  # valid synthetic personal files, so rejection is not missing-data validation
    (root / ".env").write_text("R1_SYNTHETIC_SENTINEL=must-not-enter-process\n")
    store = Store(settings.storage.db_path, settings)
    store.begin_activity((99999999, "synthetic-dead"), "resume")
    before = dict(store.activity())
    env = dict(os.environ)
    box = Toolbox(settings, store)

    def forbidden(*args, **kwargs):
        pytest.fail("real sink reached")

    for obj, name in [
        (subprocess, "Popen"),
        (subprocess, "run"),
        (threading.Thread, "start"),
        (httpx.Client, "request"),
        (apply_bridge, "cdp_alive"),
        (readback, "list_targets"),
        (notify, "mac"),
        (watchdog, "kill_agent"),
        (watchdog, "kill_orphans"),
        (config, "load_env"),
    ]:
        monkeypatch.setattr(obj, name, forbidden)
    for name, args in [
        ("discover_jobs", ()),
        ("fetch_boards", ()),
        ("check_board", ("greenhouse", "synthetic")),
        ("start_applying", ("synthetic", "summary")),
        ("start_form_fill", ("synthetic.yaml",)),
        ("prepare_resume", (["synthetic"],)),
        ("resume_applying", (["synthetic"], "summary")),
        ("save_cover_letter", ("synthetic", "ml", "synthetic")),
        ("stop_applying", ()),
    ]:
        with pytest.raises(InvariantError, match="local-only"):
            getattr(box, name)(*args)
    for method in (box.fetch_boards, box.discover_jobs):
        with pytest.raises(InvariantError, match="local-only"):
            method(wait=True)
    assert box.apply_status()["execution_mode"] == "local_only"
    box.get_discovery_setup()
    assert dict(store.activity()) == before
    assert dict(os.environ) == env
    store.db.close()


def test_two_workspaces_do_not_share_execution_mode(tmp_path, monkeypatch):
    from intent_job_agent import applypilot_bridge

    from .conftest import settings as legacy_settings

    root, closed = workspace(tmp_path)
    legacy = legacy_settings()
    legacy_store = Store(tmp_path / "legacy.sqlite", legacy)
    closed_store = Store(closed.storage.db_path, closed)
    first = Toolbox(legacy, legacy_store, tmp_path / "legacy-data")
    second = Toolbox(closed, closed_store)
    calls = []
    monkeypatch.setattr(applypilot_bridge, "run_sources", lambda *args: calls.append(args) or {"status": "done"})
    monkeypatch.setattr(first, "discovery_status", lambda: {"status": "done"})
    with pytest.raises(InvariantError, match="local-only"):
        second.discover_jobs(wait=True)
    assert first.discover_jobs(wait=True) == {"status": "done"}
    assert len(calls) == 1 and calls[0][1] is legacy
    with pytest.raises(InvariantError, match="local-only"):
        second.discover_jobs(wait=True)
    legacy_store.db.close()
    closed_store.db.close()


def test_offline_report_uses_workspace_or_explicit_data_override(tmp_path, monkeypatch):
    from intent_job_agent.form_fill import cli
    from intent_job_agent.workspace import initialize

    root, _ = workspace(tmp_path)
    other = tmp_path / "other"
    initialize(other)
    seen = []
    monkeypatch.setattr(cli, "run_report", lambda run, data, settings: seen.append(data) or data / "summary.md")
    assert cli.main(["report", "synthetic", "--workspace", str(root)]) == 0
    assert cli.main(["report", "synthetic", "--workspace", str(root), "--data-dir", str(other)]) == 0
    assert seen == [root.resolve(), other.resolve()]


def test_local_materials_reject_external_references(tmp_path):
    from .test_slice6 import write_applicant

    root, settings = workspace(tmp_path)
    write_applicant(root)
    external = tmp_path / "outside.md"
    external.write_text("synthetic outside workspace")
    path = root / "applicant/applicant.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["cover_letter_context"] = [str(external)]
    path.write_text(yaml.safe_dump(raw))
    store = Store(settings.storage.db_path, settings)
    with pytest.raises(InvariantError, match="inside"):
        Toolbox(settings, store).get_cover_letter_materials()
    store.db.close()


def test_offline_report_rejects_profile_escape(tmp_path):
    from intent_job_agent.form_fill.cli import load_sheet

    from .test_slice6 import write_applicant

    root, settings = workspace(tmp_path)
    write_applicant(root)
    path = root / "applicant/applicant.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["profile"] = str(tmp_path / "outside-synthetic-profile.json")
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(InvariantError, match="inside"):
        load_sheet(root, settings)


def test_broken_yaml_preserved_and_actionable(tmp_path):
    from intent_job_agent.workspace import initialize, load_workspace

    root, _ = workspace(tmp_path)
    config = root / "settings.yaml"
    config.write_text("broken: [")
    with pytest.raises(InvariantError):
        load_workspace(root)
    with pytest.raises(InvariantError):
        initialize(root)
    assert config.read_text() == "broken: ["
    assert not (root / "agent.sqlite").exists()


def test_init_permission_error_has_no_partial_workspace(tmp_path, monkeypatch):
    from intent_job_agent.workspace import initialize

    target = tmp_path / "blocked"

    def denied(*args, **kwargs):
        raise PermissionError("synthetic denied directory")

    monkeypatch.setattr(Path, "rename", denied)
    with pytest.raises(InvariantError, match="permissions"):
        initialize(target)
    assert not target.exists()
    assert not list(tmp_path.glob(".intent-init-*"))


def test_direct_run_job_rejects_before_creating_output(tmp_path):
    from types import SimpleNamespace

    from intent_job_agent.form_fill.runner import run_job

    _, settings = workspace(tmp_path)
    ctx = SimpleNamespace(settings=settings, run_dir=tmp_path / "never-created")
    with pytest.raises(InvariantError, match="local-only"):
        run_job(SimpleNamespace(id="synthetic"), ctx)
    assert not ctx.run_dir.exists()
