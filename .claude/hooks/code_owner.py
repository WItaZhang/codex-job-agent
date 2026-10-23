#!/usr/bin/env python3
"""One code lock per checkout: only the session that claimed a checkout may change code in it.

Hook mode (no arguments, PreToolUse JSON on stdin): refuses file edits that land on paths git does not
ignore, git commands that move branches, commits, the index or the working tree, `uv add/remove/lock`, and
shell writes (redirects, tee, rm/mv/cp/touch, sed -i) onto such paths, unless this session holds the lock
of the checkout the change lands in. In development mode a checkout nobody has claimed takes no code changes. Fresh clones do not enable this mode. Commands
that change what every checkout shares (push, branch, tag, worktree) and writes inside .git count as
changes to the main checkout. Ignored paths (data/local/, logs/, .venv/ ...) stay writable for the daily
sessions.

Each lock lives in its checkout's own git dir (.git for the main checkout, .git/worktrees/<name> for a
worktree), so every worktree is claimed on its own and no lock is ever committed.

  python3 .claude/hooks/code_owner.py enable-dev     opt this local repository into development locking
  python3 .claude/hooks/code_owner.py claim [label]   make this session the owner of the current checkout
  python3 .claude/hooks/code_owner.py status          every checkout, its branch and who owns it
  python3 .claude/hooks/code_owner.py release         free the current checkout (then nobody may change it)
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

LOCK_NAME = "code-owner"
DEV_MODE = "code-owner-development"
FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GIT_ALWAYS = {
    "add", "am", "apply", "checkout", "cherry-pick", "clean", "commit", "merge", "mv", "pull", "push",
    "rebase", "reset", "restore", "revert", "rm", "switch",
}
GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
SHARED_GIT = {"push", "branch", "tag", "worktree"}  # shared by every checkout: counts as the main one
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
        or ("codex:" + os.environ["CODEX_THREAD_ID"] if os.environ.get("CODEX_THREAD_ID") else "")
    )


def git_paths(directory):
    """(top level, git dir, common dir) of the checkout containing `directory`; None outside any work tree."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir"],
            cwd=directory, capture_output=True, text=True,
        )
    except OSError:
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 3:
        return None
    return tuple(Path(line).resolve() for line in lines)


def where_name(git_dir, common):
    return "主目录" if git_dir == common else f"worktree {git_dir.name}"


def checkouts(common):
    """(git dir, work tree) of the main checkout and of every worktree."""
    yield common, common.parent
    for git_dir in sorted((common / "worktrees").glob("*")):
        pointer = git_dir / "gitdir"
        if pointer.is_file():
            yield git_dir, Path(pointer.read_text(encoding="utf-8").strip()).parent


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


def code_checkout(raw, cwd, common):
    """Git dir of the checkout whose lock governs writing `raw`, when `raw` is code of this repository.

    Code is a path git does not ignore, in the main checkout or in any worktree; anything inside .git
    belongs to the main checkout. Paths outside this repository, and ignored paths, give None.
    """
    path = Path(os.path.expanduser(raw))
    path = (path if path.is_absolute() else Path(cwd) / path).resolve()
    if _inside(path, common):
        return common
    probe = path if path.is_dir() else path.parent
    while not probe.is_dir():
        probe = probe.parent
    found = git_paths(probe)
    if not found or found[2] != common:
        return None
    ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=probe, capture_output=True)
    return found[1] if ignored.returncode != 0 else None


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


def git_directory(prefix, args, cwd):
    """Directory a git command runs in, after its -C options.

    None when GIT_DIR / GIT_WORK_TREE or --git-dir / --work-tree send git somewhere this hook does not follow.
    """
    if any(word.split("=", 1)[0] in ("GIT_DIR", "GIT_WORK_TREE") for word in prefix):
        return None
    i, where = 0, Path(cwd)
    while i < len(args) and args[i].startswith("-"):
        flag = args[i]
        if flag.split("=", 1)[0] in ("--git-dir", "--work-tree"):
            return None
        if flag == "-C" and i + 1 < len(args):
            where = (where / os.path.expanduser(args[i + 1])).resolve()
        i += 2 if flag in GIT_GLOBAL_WITH_VALUE else 1
    return where


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


def bash_changes(command, cwd, common):
    """Code changes `command` would make, as (short description, git dir of the checkout it lands in)."""
    for seg in segments(command):
        words, targets = [], []
        for j, token in enumerate(seg):
            if token in REDIRECTS:
                continue
            (targets if j and seg[j - 1] in REDIRECTS else words).append(token)
        for target in targets:
            git_dir = code_checkout(target, cwd, common)
            if git_dir:
                yield f"写入 {target}", git_dir
        start = _command_start(words)
        if start >= len(words):
            continue
        name, rest = os.path.basename(words[start]), words[start + 1:]
        args = [a for a in rest if a and not a.startswith("-")]
        if name == "cd":
            cwd = str((Path(cwd) / os.path.expanduser(args[0] if args else "~")).resolve())
        elif name == "git":
            sub = git_change(rest)
            if not sub:
                continue
            where = git_directory(words[:start], rest, cwd)
            found = git_paths(where) if where and where.is_dir() else None
            if where is None:
                yield f"git {sub}", common
            elif found and found[2] == common:
                yield f"git {sub}", common if sub.split()[0] in SHARED_GIT else found[1]
        elif name == "uv" and rest and rest[0] in UV_WRITES:
            found = git_paths(cwd)
            if found and found[2] == common:
                yield f"uv {rest[0]}", found[1]
        elif name in WRITE_ALL_ARGS or name in WRITE_LAST_ARG or name in IN_PLACE:
            if name in IN_PLACE:
                if not any(a.startswith(("-i", "-pi")) or a == "--in-place" for a in rest):
                    continue
                args = [a for a in args if (Path(cwd) / os.path.expanduser(a)).is_file()]
            elif name in WRITE_LAST_ARG:
                args = args[-1:]
            for arg in args:
                git_dir = code_checkout(arg, cwd, common)
                if git_dir:
                    yield f"{name} {arg}", git_dir


def refusal(change, git_dir, common, lock):
    where = where_name(git_dir, common)
    shared = "push、branch、tag、worktree 命令和 .git 里的写入都算主目录的改动。" if git_dir == common else ""
    if lock and lock["session"]:
        owner = lock["label"] or lock["session"]
        return (
            f"已拒绝（{change}）：{where}的代码只能在会话「{owner}」里改。{shared}"
            "本会话不改那里的代码、不在那里切分支或提交；由协调者安排独立检出或与持锁会话交接。"
            "不得覆盖其他会话的锁或绕过隔离。"
        )
    if git_dir == common:
        return (
            f"已拒绝（{change}）：主目录没有持锁会话，这里不改代码。{shared}"
            "开发在 worktree 会话里做（会话里先运行 python3 .claude/hooks/code_owner.py claim <名字>）；"
            "合并回 main 由协调者安排持锁主目录会话执行 /merge。"
            "不要自己 claim 主目录或绕过这条限制。"
        )
    return (
        f"已拒绝（{change}）：{where}还没有持锁会话。"
        "如果协调者已将本切片分配给本会话，先在这里运行 python3 .claude/hooks/code_owner.py claim <名字>；"
        "否则不要改这里的代码。"
    )


def development_enabled(common):
    """Local-only opt-in; an existing lock also identifies a legacy managed repository."""
    return any((directory / DEV_MODE).exists() or (directory / LOCK_NAME).exists()
               for directory, _ in checkouts(common))


def hook():
    data = json.load(sys.stdin)
    cwd = data.get("cwd") or os.getcwd()
    here = git_paths(Path(__file__).resolve().parent) or git_paths(cwd)
    if not here:
        return 0
    common = here[2]
    if not development_enabled(common):
        return 0
    tool, args = data.get("tool_name", ""), data.get("tool_input") or {}
    if tool in FILE_TOOLS:
        target = args.get("file_path") or args.get("notebook_path") or ""
        git_dir = code_checkout(target, cwd, common) if target else None
        changes = [(f"{tool} {target}", git_dir)] if git_dir else []
    elif tool == "Bash":
        changes = bash_changes(args.get("command") or "", cwd, common)
    else:
        return 0
    me = session_id(data)
    for change, git_dir in changes:
        lock = read_lock(git_dir)
        if lock and lock["session"] and lock["session"] == me:
            continue
        print(refusal(change, git_dir, common, lock), file=sys.stderr)
        return 2
    return 0


def status_lines(common, current):
    """One line per checkout: which one, its branch, its work tree and who holds its lock."""
    me = session_id()
    for git_dir, tree in checkouts(common):
        head_file = git_dir / "HEAD"
        head = head_file.read_text(encoding="utf-8").strip() if head_file.is_file() else ""
        branch = head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else head[:12] or "?"
        lock = read_lock(git_dir)
        if lock and lock["session"]:
            mine = "（就是本会话）" if lock["session"] == me else ""
            holder = f"持锁「{lock['label'] or lock['session']}」{mine}"
        else:
            holder = "没有持锁会话"
        marker = "  ← 当前" if git_dir == current else ""
        yield f"{where_name(git_dir, common)}（{branch}，{tree}）：{holder}{marker}"


def cli(argv):
    here = git_paths(os.getcwd())
    if not here:
        print("不在 git 仓库的工作区里。", file=sys.stderr)
        return 1
    _, git_dir, common = here
    where, path = where_name(git_dir, common), git_dir / LOCK_NAME
    if argv[0] == "enable-dev":
        existing = read_lock(git_dir)
        if existing and existing["session"] != session_id():
            print("Development checkout belongs to another session", file=sys.stderr)
            return 1
        (common / DEV_MODE).touch(exist_ok=True)
        print("Local development lock enforcement enabled; claim before edits")
    elif argv[0] == "claim":
        me = session_id()
        if not me:
            print("拿不到会话 id：请在 Claude Code 或 Codex 会话里运行。", file=sys.stderr)
            return 1
        existing = read_lock(git_dir)
        if existing and existing["session"] != me:
            print("代码锁已由其他会话持有；拒绝覆盖。", file=sys.stderr)
            return 1
        label = " ".join(argv[1:]) or me
        if existing is None:
            try:
                with path.open("x", encoding="utf-8") as lock_file:
                    lock_file.write(f"{me}\n{label}\n")
            except FileExistsError:
                print("代码锁已被其他会话创建；拒绝覆盖。", file=sys.stderr)
                return 1
        (common / DEV_MODE).touch(exist_ok=True)
        print(f"{where}的代码锁归属：{label}（{me}）")
    elif argv[0] == "release":
        existing = read_lock(git_dir)
        if existing and existing["session"] != session_id():
            print("代码锁属于其他会话；拒绝解除。", file=sys.stderr)
            return 1
        if existing:
            (common / DEV_MODE).touch(exist_ok=True)
        path.unlink(missing_ok=True)
        print(f"{where}的代码锁已解除：在有会话 claim 之前，谁都不能改{where}的代码。")
    elif argv[0] == "status":
        for line in status_lines(common, git_dir):
            print(line)
    else:
        print(__doc__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) if len(sys.argv) > 1 else hook())
