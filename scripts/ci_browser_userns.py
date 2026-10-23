"""Ephemeral GitHub Linux CI only: exact Playwright Chromium userns permission.

Not a product installer or an authentication boundary. Never disables Chromium's sandbox.
Based on Chromium docs/security/apparmor-userns-restrictions.md; no wildcard attachment.
"""

import argparse
import hashlib
import os
import platform
import re
import stat
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path


def check_runner(env, system):
    if system != "Linux" or any(
        env.get(k) != v
        for k, v in {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux"}.items()
    ):
        raise ValueError("Only ephemeral GitHub-hosted Linux CI is supported; no local policy changes")


def policy(binary, run_id, attempt):
    # Reject instead of trying to escape AppArmor pattern/variable/quote syntax.
    if not re.fullmatch(r"/home/runner/\.cache/ms-playwright/chromium-[0-9]+/chrome-linux/chrome", binary):
        raise ValueError("Expected the exact installed Playwright Chromium path without patterns or escaping")
    if not all(re.fullmatch(r"[0-9]+", value) for value in (run_id, attempt)):
        raise ValueError("Invalid CI run identity")
    name = f"intent-job-agent-ci-chromium-{run_id}-{attempt}"
    return name, f'abi <abi/4.0>,\nprofile {name} "{binary}" flags=(unconfined) {{\n  userns,\n}}\n'


def validate_binary(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or path.resolve(strict=True) != path:
        raise ValueError("Browser path must be canonical without symlinks")
    if not stat.S_ISREG(path.stat().st_mode) or not os.access(path, os.X_OK):
        raise ValueError("Browser must be an installed executable regular file")


def command(*args):
    subprocess.run(["sudo", "-n", *args], check=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove"))
    args = parser.parse_args()
    check_runner(os.environ, platform.system())
    if version("playwright") != "1.56.0":
        raise ValueError("Review CI policy before changing the locked Playwright version")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        binary = Path(pw.chromium.executable_path)
    name, content = policy(str(binary), os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    validate_binary(binary)
    target = Path("/etc/apparmor.d") / name
    if args.action == "remove":
        if not target.exists() and not target.is_symlink():
            print("No policy from this CI run to remove")
            return
        if target.is_symlink() or target.read_text() != content or target.stat().st_uid != 0:
            raise ValueError("Refuse to remove an unexpected policy")
        command("/usr/sbin/apparmor_parser", "-R", str(target))
        command("/bin/rm", "--", str(target))
        print(f"Removed only {name}")
        return
    if target.exists() or target.is_symlink():
        raise ValueError("Refuse to overwrite an existing policy")
    with tempfile.TemporaryDirectory(prefix="synthetic-ci-userns-") as tmp:
        source = Path(tmp) / "policy"
        source.write_text(content)
        command("/usr/bin/install", "-o", "root", "-g", "root", "-m", "0644", str(source), str(target))
    info = target.stat()
    if target.is_symlink() or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o644):
        raise ValueError("Policy ownership/mode verification failed")
    if target.read_text() != content:
        raise ValueError("Installed policy content mismatch")
    # Load this one profile, not a global AppArmor reload or a sysctl modification.
    command("/usr/sbin/apparmor_parser", "-r", str(target))
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(f"Installed {name}; exact binary={binary}; sha256={digest}; Chromium sandbox flags unchanged")


if __name__ == "__main__":
    main()
