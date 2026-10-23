"""Execute the shipped launcher commands in a moved synthetic checkout; never start a model."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launcher(host):
    if host == "claude":
        return json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["intent-agent"]
    return tomllib.loads((ROOT / ".codex/config.toml").read_text())["mcp_servers"]["intent-agent"]


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("nested", [False, True])
def test_launcher_uses_checkout_root_and_locked_dependencies(tmp_path, host, nested):
    root = tmp_path / "synthetic moved checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    cwd = root / "nested" if nested else root
    cwd.mkdir(exist_ok=True)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_uv = binaries / "uv"
    fake_uv.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
    fake_uv.chmod(0o700)
    config = launcher(host)
    result = subprocess.run(
        [config["command"], *config["args"]],
        cwd=cwd,
        env={"PATH": f"{binaries}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        os.path.realpath(root),
        "run",
        "--locked",
        "intent-job-agent-mcp",
        "--config",
        "configs/default.yaml",
    ]


def test_common_definitions_remain_relative_symlinks():
    for name, target in {
        "AGENTS.md": "CLAUDE.md",
        ".agents/skills": ".claude/skills",
        ".codex/agents/job-tagger.md": ".claude/agents/job-tagger.md",
        ".codex/agents/cover-letter-writer.md": ".claude/agents/cover-letter-writer.md",
    }.items():
        link = ROOT / name
        assert link.is_symlink()
        assert not Path(os.readlink(link)).is_absolute()
        assert link.resolve() == (ROOT / target).resolve()
