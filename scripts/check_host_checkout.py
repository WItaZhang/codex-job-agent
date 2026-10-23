"""Check shipped host launchers in a disposable clean clone (never run in your normal data checkout).

Usage: uv run python scripts/check_host_checkout.py
Creates only SYNTHETIC data/local, initializes intent through stdio MCP, and reads tools/intent.
No discovery, model, browser, account or application call. Host CLI interaction is a separate check.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def handshake(command, args, cwd, env, initialize):
    params = StdioServerParameters(command=command, args=args, cwd=str(cwd), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            greeting = await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            assert len(names) == 47
            assert {"discover_jobs", "select_today", "build_apply_list", "start_applying"} <= set(names)
            if initialize:
                result = await session.call_tool("initialize_intent", {"path": "data/local/intent.yaml"})
                assert not result.is_error, result.content
            result = await session.call_tool("get_intent", {})
            assert not result.is_error, result.content
            return {"tools": names, "protocol": greeting.protocol_version, "intent_read": True}


def main():
    if (ROOT / "data/local").exists():
        raise SystemExit("Use a fresh disposable clone: data/local must not exist")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Use a clean committed checkout")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lock_before = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    data = ROOT / "data/local"
    data.mkdir(parents=True, mode=0o700)
    shutil.copyfile(ROOT / "docs/examples/intent.example.yaml", data / "intent.yaml")
    nested = ROOT / "tests"
    claude = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["intent-agent"]
    codex = tomllib.loads((ROOT / ".codex/config.toml").read_text())["mcp_servers"]["intent-agent"]
    results = []
    with tempfile.TemporaryDirectory(prefix="synthetic-host-home-") as home:
        # Keep no provider keys, profile locations or ambient proxy variables.
        env = {"PATH": os.environ["PATH"], "HOME": home, "UV_OFFLINE": "1"}
        for host, config in (("claude", claude), ("codex", codex)):
            for cwd in (ROOT, nested):
                result = asyncio.run(handshake(config["command"], config["args"], cwd, env, not results))
                results.append(
                    {
                        "host_config": host,
                        "cwd": "." if cwd == ROOT else "tests",
                        "command": [config["command"], *config["args"]],
                        **result,
                    }
                )
    assert lock_before == hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    assert not (nested / "data").exists()
    print(
        json.dumps(
            {
                "head": head,
                "synthetic": True,
                "scope": "stdio protocol, not host interaction",
                "lock_unchanged": True,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
