#!/usr/bin/env python3
"""Single-writer lock: while a lock exists, only the session named in it may change code.

Hook mode (no arguments, PreToolUse JSON on stdin): refuses, for every other session, file edits that
land on paths git does not ignore, git commands that move branches, commits, the index or the working
tree, `uv add/remove/lock`, and shell writes (redirects, tee, rm/mv/cp/touch, sed -i) onto such paths.
Ignored paths (data/local/, logs/, .venv/ ...) stay writable for the daily sessions.

The lock lives in the git common dir, so it covers every worktree and is never committed.

  python3 .claude/hooks/code_owner.py claim [label]   make this session the owner
  python3 .claude/hooks/code_owner.py status
  python3 .claude/hooks/code_owner.py release
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

LOCK_NAME = "code-owner"
FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GIT_ALWAYS = {
    "add", "am", "apply", "checkout", "cherry-pick", "clean", "commit", "merge", "mv", "pull", "push",
    "rebase", "reset", "restore", "revert", "rm", "switch",
}
GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
UV_WRITES = {"add", "remove", "lock"}
WRITE_ALL_ARGS = {"rm", "touch", "truncate", "tee", "mv"}
WRITE_LAST_ARG = {"cp", "install", "ln"}
IN_PLACE = {"sed", "perl"}
WRAPPERS = {"command", "env", "exec", "nice", "nohup", "sudo", "time", "xargs"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
OPERATORS = set("&|;()<>\n")


def session_id(hook_input=None):
    """The desktop app's session id survives restarts of the CLI; fall back to the CLI's own id."""
    return (
        os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
        or (hook_input or {}).get("session_id")
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or ""
    )


def common_dir(cwd):
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd, capture_output=True, text=True,
    )
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 else None


def read_lock(git_dir):
    path = git_dir / LOCK_NAME
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines() + ["", ""]
    return {"session": lines[0].strip(), "label": lines[1].strip()}


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_code(raw, cwd, git_dir):
    """True when `raw` resolves inside this repository (any worktree) to a path git does not ignore."""
    path = Path(os.path.expanduser(raw))
    path = (path if path.is_absolute() else Path(cwd) / path).resolve()
    if _inside(path, git_dir):
        return True
    if not _inside(path, git_dir.parent):
        return False
    probe = path.parent
    while not probe.is_dir():
        probe = probe.parent
    ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=probe, capture_output=True)
    return ignored.returncode != 0


def _tokens(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return [t.strip("'\"") for t in re.findall(r"\n|[;&|()<>]+|[^\s;&|()<>]+", command)]


def segments(command):
    """Simple commands of a shell script, split at operators and newlines, heredoc bodies left out.

    Redirect operators stay inside their command, followed by their target.
    """
    tokens, parts, current = _tokens(command), [], []
    heredocs, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("<<", "<<-") and i + 1 < len(tokens):
            heredocs.append(tokens[i + 1])
            i += 2
            continue
        if "\n" in token and heredocs:
            delimiter = heredocs.pop(0)
            i += 1
            while i < len(tokens) and not (tokens[i] == delimiter and "\n" in tokens[i - 1]):
                i += 1
        if token in REDIRECTS:
            current.append(token)
        elif token and set(token) <= OPERATORS:
            if current:
                parts.append(current)
            current = []
        else:
            current.append(token)
        i += 1
    if current:
        parts.append(current)
    return parts


def git_change(args):
    """The git subcommand in `args` when it changes repository state, else None."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    flags = {a for a in rest if a.startswith("-")}
    positional = [a for a in rest if not a.startswith("-")]
    if sub in GIT_ALWAYS:
        return sub
    if sub == "stash" and (not rest or rest[0] not in ("list", "show")):
        return sub
    if sub == "worktree" and rest and rest[0] not in ("list",):
        return f"worktree {rest[0]}"
    if sub == "branch":
        listing = {"-l", "--list", "-a", "--all", "-r", "--remotes", "--contains", "--no-contains",
                   "--merged", "--no-merged", "--points-at", "--show-current"}
        moving = {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
                  "-u", "--set-upstream-to", "--unset-upstream", "--edit-description"}
        if flags & moving or (positional and not flags & listing):
            return sub
    if sub == "tag":
        listing = {"-l", "--list", "--contains", "--no-contains", "--points-at", "--merged", "--no-merged"}
        if flags & {"-d", "--delete"} or (positional and not flags & listing):
            return sub
    return None


def _command_start(seg):
    """Index of the command word, past variable assignments and wrappers such as env or xargs."""
    i = 0
    while i < len(seg):
        word = seg[i]
        if "=" in word and word.split("=", 1)[0].isidentifier():
            i += 1
        elif os.path.basename(word) in WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
        else:
            return i
    return i


def bash_change(command, cwd, git_dir):
    """A short description of the first code change `command` would make, else None."""
    for seg in segments(command):
        words, targets = [], []
        for j, token in enumerate(seg):
            if token in REDIRECTS:
                continue
            (targets if j and seg[j - 1] in REDIRECTS else words).append(token)
        hit = next((t for t in targets if is_code(t, cwd, git_dir)), None)
        if hit:
            return f"写入 {hit}"
        start = _command_start(words)
        if start >= len(words):
            continue
        name, rest = os.path.basename(words[start]), words[start + 1:]
        args = [a for a in rest if a and not a.startswith("-")]
        if name == "cd":
            cwd = str((Path(cwd) / os.path.expanduser(args[0] if args else "~")).resolve())
        elif name == "git":
            sub = git_change(rest)
            if sub:
                return f"git {sub}"
        elif name == "uv" and rest and rest[0] in UV_WRITES:
            return f"uv {rest[0]}"
        elif name in WRITE_ALL_ARGS or name in WRITE_LAST_ARG or name in IN_PLACE:
            if name in IN_PLACE:
                if not any(a.startswith(("-i", "-pi")) or a == "--in-place" for a in rest):
                    continue
                args = [a for a in args if (Path(cwd) / os.path.expanduser(a)).is_file()]
            elif name in WRITE_LAST_ARG:
                args = args[-1:]
            hit = next((a for a in args if is_code(a, cwd, git_dir)), None)
            if hit:
                return f"{name} {hit}"
    return None


def hook():
    data = json.load(sys.stdin)
    cwd = data.get("cwd") or os.getcwd()
    git_dir = common_dir(cwd)
    lock = read_lock(git_dir) if git_dir else None
    if not lock or not lock["session"] or session_id(data) == lock["session"]:
        return 0
    tool, args = data.get("tool_name", ""), data.get("tool_input") or {}
    change = None
    if tool in FILE_TOOLS:
        target = args.get("file_path") or args.get("notebook_path") or ""
        if target and is_code(target, cwd, git_dir):
            change = f"{tool} {target}"
    elif tool == "Bash":
        change = bash_change(args.get("command") or "", cwd, git_dir)
    if not change:
        return 0
    owner = lock["label"] or lock["session"]
    print(
        f"已拒绝（{change}）：这个仓库的代码只能在会话「{owner}」里改。"
        "本会话不改代码、不切分支、不提交；需要改代码时，告诉用户到那个会话去做。"
        "不要自己改锁或绕过这条限制；换会话由用户决定"
        "（在新会话里运行 python3 .claude/hooks/code_owner.py claim）。",
        file=sys.stderr,
    )
    return 2


def cli(argv):
    git_dir = common_dir(os.getcwd())
    if not git_dir:
        print("不在 git 仓库里。", file=sys.stderr)
        return 1
    path, lock = git_dir / LOCK_NAME, read_lock(git_dir)
    if argv[0] == "claim":
        me = session_id()
        if not me:
            print("拿不到会话 id：请在 Claude Code 会话里运行。", file=sys.stderr)
            return 1
        label = " ".join(argv[1:]) or me
        path.write_text(f"{me}\n{label}\n", encoding="utf-8")
        print(f"代码锁归属：{label}（{me}）")
    elif argv[0] == "release":
        path.unlink(missing_ok=True)
        print("代码锁已解除：所有会话都能改代码。")
    elif argv[0] == "status":
        if lock:
            mine = "（就是本会话）" if lock["session"] == session_id() else ""
            print(f"代码锁归属：{lock['label'] or lock['session']}（{lock['session']}）{mine}")
        else:
            print("没有代码锁：所有会话都能改代码。")
    else:
        print(__doc__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) if len(sys.argv) > 1 else hook())
