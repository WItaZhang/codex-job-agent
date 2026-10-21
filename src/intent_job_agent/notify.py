"""The one push notification (spec 4.10 item 10, section 10 item 99): when a step or a resume ends and something waits
for the user - jobs held at a gate, forms filled and stopped before submit - a macOS notification says how many,
which kind of gate, and the batch number and company of each held job. Never an e-mail, a phone number or an
address. Company names come from job data (untrusted, spec 4.8): they are cleaned, cut short and passed to osascript
as arguments, never written into the script."""

import re
import subprocess
import sys

# How the notification names a gate: what the user has to do (decision 23).
KINDS = {
    "login_issue": "要登录",
    "sso_required": "要登录",
    "captcha": "验证码",
    "email_verification": "邮箱验证码",
}
KIND_ORDER = ("要登录", "验证码", "邮箱验证码")
LISTED = 5  # held jobs named in the body; the rest are counted
NAME_CHARS = 20
SCRIPT = ("on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def clean(text: str, limit: int = NAME_CHARS) -> str:
    """Untrusted text as one short plain line: control characters become spaces, then it is cut to `limit`."""
    text = " ".join(_CONTROL.sub(" ", str(text)).split())
    return text if len(text) <= limit else text[:limit] + "…"


def notice(held, waiting: int, step: str) -> tuple[str, str] | None:
    """(title, body) for what waits for the user when `step` ends, or None when nothing does. `held`: (batch
    number, company, result code) of each job held at a gate, in batch order; `waiting`: how many forms are filled
    and stopped before submit."""
    held = list(held)
    if not held and not waiting:
        return None
    parts = [f"{len(held)} 个等你处理"] if held else []
    parts += [f"{waiting} 个填好等你检查"] if waiting else []
    title = "投递：" + "，".join(parts)
    if not held:
        return title, f"{step}做完"
    counts = {kind: 0 for kind in KIND_ORDER}
    for _, _, reason in held:
        counts[KINDS.get(reason, "要登录")] += 1
    kinds = "、".join(f"{count} 个{kind}" for kind, count in counts.items() if count)
    names = "、".join(f"{n} {clean(company)}" for n, company, _ in held[:LISTED])
    more = f" 等 {len(held)} 个" if len(held) > LISTED else ""
    return title, f"{kinds}：{names}{more}"


def mac(title: str, body: str) -> bool:
    """Show the notification on macOS. Never raises and never retries: a notification that cannot be shown (Focus,
    no permission) must not stop applying; the chat still lists everything."""
    if sys.platform != "darwin":
        return False
    argv = ["osascript", *(part for line in SCRIPT for part in ("-e", line)), title, body]
    try:
        subprocess.run(argv, check=True, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
