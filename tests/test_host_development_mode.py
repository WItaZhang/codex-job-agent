"""Run the real hook in an independent SYNTHETIC repository, without changing developer locks."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def repository(tmp_path):
    root = tmp_path / "fresh clone"
    hook = root / ".claude/hooks/code_owner.py"
    hook.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / ".claude/hooks/code_owner.py", hook)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root, hook


def invoke(root, hook, *args, session="synthetic-owner", tool="Edit", inputs=None):
    env = dict(os.environ)
    for key in ("CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        env.pop(key, None)
    env["CODEX_THREAD_ID"] = session
    return subprocess.run(
        [sys.executable, str(hook), *args],
        cwd=root,
        env=env,
        input=json.dumps(
            {
                "cwd": str(root),
                "tool_name": tool,
                "tool_input": inputs or {"file_path": str(root / "configs/default.yaml")},
            }
        ),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_new_user_can_configure_and_upgrade_without_claim(tmp_path):
    root, hook = repository(tmp_path)
    assert invoke(root, hook).returncode == 0
    assert invoke(root, hook, tool="Bash", inputs={"command": "git pull"}).returncode == 0


def test_explicit_development_mode_and_release_preserve_lock_rules(tmp_path):
    root, hook = repository(tmp_path)
    assert invoke(root, hook, "enable-dev").returncode == 0
    assert invoke(root, hook).returncode == 2
    assert invoke(root, hook, "claim", "synthetic-slice").returncode == 0
    assert invoke(root, hook).returncode == 0
    assert invoke(root, hook, session="other").returncode == 2
    assert invoke(root, hook, "release").returncode == 0
    assert invoke(root, hook).returncode == 2


def test_legacy_lock_remains_protected_after_release(tmp_path):
    root, hook = repository(tmp_path)
    (root / ".git/code-owner").write_text("codex:synthetic-owner\nlegacy\n")
    assert invoke(root, hook, session="other").returncode == 2
    assert invoke(root, hook, "release").returncode == 0
    assert invoke(root, hook).returncode == 2


def test_managed_repository_does_not_leave_new_worktree_unprotected(tmp_path):
    root, hook = repository(tmp_path)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "synthetic",
        ],
        cwd=root,
        check=True,
    )
    assert invoke(root, hook, "enable-dev").returncode == 0
    tree = tmp_path / "second"
    subprocess.run(["git", "worktree", "add", "-qb", "synthetic-second", str(tree)], cwd=root, check=True)
    result = invoke(root, hook, inputs={"file_path": str(tree / "source.py")})
    assert result.returncode == 2


def test_host_local_files_are_ignored_without_global_excludes(tmp_path):
    root, _ = repository(tmp_path)
    shutil.copyfile(ROOT / ".gitignore", root / ".gitignore")
    for name in (".claude/settings.local.json", ".DS_Store", "nested/.DS_Store", "node_modules/pkg/index.js"):
        result = subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", name], cwd=root, check=False
        )
        assert result.returncode == 0, name
