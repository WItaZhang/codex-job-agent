"""Gmail prerequisites and a bounded, empty search through the pinned MCP provider.

Only the provider reads credential contents. Default inspection is offline; connection
checks belong to the user's authorized business/check scope, never to initialization.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from contextvars import ContextVar
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from .config import Settings
from .domain import InvariantError

GUIDE = "docs/gmail-setup.md"
_IN_PROBE = ContextVar("gmail_probe", default=False)


class _ProbeLogFilter(logging.Filter):
    def filter(self, record):
        # Malformed provider messages can appear in SDK validation tracebacks.
        # Suppress only this probe's async context, preserving other MCP clients.
        return not _IN_PROBE.get()


for _logger in ("mcp.client.stdio", "mcp.client.session"):
    logging.getLogger(_logger).addFilter(_ProbeLogFilter())

MESSAGES = {
    "disabled": "Gmail disabled; email verification needs manual handling and email applications are unavailable.",
    "not_configured": "Create your own OAuth client and follow the Gmail setup guide.",
    "authorization_required": "Complete or renew Gmail authorization in your local terminal, then check again.",
    "not_checked": "Files exist; run the authorized connection check before claiming Gmail is ready.",
    "missing_program": "Install Node.js with npx, then check again.",
    "connection_failed": "Gmail connection could not be verified; check configuration/network and retry.",
    "timeout": "Gmail check timed out; check connectivity or package installation, then retry.",
    "ready": "Gmail empty search succeeded; this does not verify browser, model login or an application.",
}


def paths(settings: Settings) -> dict[str, str]:
    """One resolution rule for inspection, authorization and the actual provider."""
    return {
        env: str(Path(os.environ.get(env) or configured).expanduser().resolve())
        for env, configured in (
            ("GMAIL_OAUTH_PATH", settings.apply.gmail.oauth_path),
            ("GMAIL_CREDENTIALS_PATH", settings.apply.gmail.credentials_path),
        )
    }


def server_config(settings: Settings) -> dict:
    """Use an empty cwd: upstream otherwise copies a cwd OAuth file over configured keys."""
    resolved = paths(settings)
    return {
        "command": sys.executable,
        "args": [
            "-m",
            "intent_job_agent.gmail_setup",
            "serve",
            "--package",
            settings.apply.mcp.gmail,
            "--oauth-path",
            resolved["GMAIL_OAUTH_PATH"],
            "--credentials-path",
            resolved["GMAIL_CREDENTIALS_PATH"],
        ],
    }


def probe(settings: Settings) -> CallToolResult:
    """SDK teardown also bounds shutdown and kills the provider process group."""
    settings.require_external("gmail_connection_check")

    async def run():
        with tempfile.TemporaryDirectory(prefix="intent-gmail-check-") as cwd, open(os.devnull, "w") as errors:
            server = StdioServerParameters(
                command="npx", args=["-y", settings.apply.mcp.gmail], env={**os.environ, **paths(settings)}, cwd=cwd
            )
            with anyio.fail_after(settings.apply.gmail.timeout_seconds):
                async with stdio_client(server, errlog=errors) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        await session.list_tools()
                        return await session.call_tool(
                            "search_emails", {"query": settings.apply.gmail.probe_query, "maxResults": 1}
                        )

    token = _IN_PROBE.set(True)
    try:
        return anyio.run(run)
    finally:
        _IN_PROBE.reset(token)


def _probe_status(reply: CallToolResult) -> str:
    texts = [item.text for item in reply.content if isinstance(item, TextContent)]
    output = " ".join(texts).lower()
    if any(
        term in output
        for term in (
            "invalid_grant",
            "invalid credentials",
            "insufficient permission",
            "insufficient authentication scopes",
            "no access, refresh token",
            "unauthenticated",
            "login required",
        )
    ):
        return "authorization_required"
    # 1.1.11 reports API errors as normal text, without isError. Only an empty
    # text result from our mutually exclusive date query is a successful check.
    if (
        reply.is_error
        or reply.result_type != "complete"
        or not texts
        or len(texts) != len(reply.content)
        or output.strip()
    ):
        return "connection_failed"
    return "ready"


def status(settings: Settings, *, connect: bool = False) -> dict:
    enabled = settings.apply.gmail.enabled and settings.execution_mode != "local_only"
    result = {"enabled": enabled, "guide": GUIDE, "checked": False}
    state = "disabled"
    if enabled:
        try:
            resolved = paths(settings)
            result["paths"] = resolved
            client = Path(resolved["GMAIL_OAUTH_PATH"]).is_file()
            credentials = Path(resolved["GMAIL_CREDENTIALS_PATH"]).is_file()
            result["files"] = {"oauth_client": client, "credentials": credentials}
            if not client:
                state = "not_configured"
            elif not credentials:
                state = "authorization_required"
            elif not shutil.which("npx"):
                state = "missing_program"
            elif not connect:
                state = "not_checked"
            else:
                result["checked"] = True
                state = _probe_status(probe(settings))
        except TimeoutError:
            state = "timeout"
        except Exception:  # Never return provider exceptions or credential/response contents.
            state = "connection_failed"
    return {**result, "status": state, "ready": state == "ready", "next_action": MESSAGES[state]}


def _provider(package: str, env: dict[str, str], *, auth: bool = False) -> int:
    with tempfile.TemporaryDirectory(prefix="intent-gmail-") as cwd:
        return subprocess.run(
            ["npx", "-y", package, *(["auth"] if auth else [])],
            cwd=cwd,
            env={**os.environ, **env},
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or explicitly authorize/check the optional Gmail service")
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "check", "auth"):
        commands.add_parser(command)
    serve = commands.add_parser("serve", help=argparse.SUPPRESS)
    for option in ("package", "oauth-path", "credentials-path"):
        serve.add_argument("--" + option, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _provider(
                args.package,
                {
                    "GMAIL_OAUTH_PATH": args.oauth_path,
                    "GMAIL_CREDENTIALS_PATH": args.credentials_path,
                },
            )
        from .workspace import resolve_settings

        settings = resolve_settings(config=args.config)
        if args.command == "auth":
            settings.require_external("gmail_authorization")
            before = status(settings)
            if before["status"] in ("disabled", "not_configured", "missing_program", "connection_failed"):
                print(json.dumps(before, ensure_ascii=False))
                return 1
            if not shutil.which("npx"):
                print(MESSAGES["missing_program"])
                return 1
            return _provider(settings.apply.mcp.gmail, paths(settings), auth=True)
        result = status(settings, connect=args.command == "check")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ready"] or result["status"] == "disabled" else 1
    except (InvariantError, OSError, ValueError):
        print(f"Gmail setup could not run. See {GUIDE}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
