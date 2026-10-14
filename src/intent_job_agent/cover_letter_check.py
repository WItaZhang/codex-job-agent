"""Cover-letter checks (spec 4.10 item 4). Pure.

The word lists and the "starts with Dear" rule come from ApplyPilot's `scoring/validator.py` at 4a8d521
(AGPL-3.0-only, see NOTICE.md), in its strict mode: banned words are errors. Changes for this project: only the
em dash is rejected (en dashes in ranges such as "May-June" are fine), the word range comes from the config, the
letter must not start with model preamble, and every number must appear in the selected resume or the posting.
"""

import re

BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
    # Cover-letter-specific additions
    "this demonstrates", "this reflects", "i have experience with",
    "furthermore", "additionally", "moreover",
]  # fmt: skip

LEAK_PHRASES: list[str] = [
    "i am sorry", "i apologize", "i will try", "let me try",
    "i am at a loss", "i am truly sorry", "apologies for",
    "i keep fabricating", "i will have to admit", "one final attempt",
    "one last time", "if it fails again", "persistent errors",
    "i am having difficulty", "i made an error", "my mistake",
    "here is the corrected", "here is the revised", "here is the updated",
    "here is my", "below is the", "as requested",
    "note:", "disclaimer:", "important:",
    "i have rewritten", "i have removed", "i have fixed",
    "i have replaced", "i have updated", "i have corrected",
    "per your feedback", "based on your feedback", "as per the instructions",
    "the following resume", "the resume below",
    "the following cover letter", "the letter below",
]  # fmt: skip

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {match.group(0).rstrip(".,") for match in _NUMBER.finditer(text)}


def check_letter(text: str, resume_text: str, job_text: str, min_words: int, max_words: int) -> list[str]:
    """Problems with a letter; empty means it passes."""
    problems = []
    lower = text.lower()
    if not text.strip().lower().startswith("dear"):
        problems.append("Must start with the greeting ('Dear ...'), with nothing before it.")
    if "—" in text:
        problems.append("Contains an em dash (—); use a comma or a period.")
    banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lower)]
    if banned:
        problems.append(f"Banned words: {', '.join(banned)}")
    leaks = [p for p in LEAK_PHRASES if p in lower]
    if leaks:
        problems.append(f"Model self-talk: {', '.join(leaks)}")
    words = len(text.split())
    if not min_words <= words <= max_words:
        problems.append(f"{words} words; write {min_words}-{max_words} words.")
    sources = _numbers(resume_text) | _numbers(job_text)
    unknown = sorted(n for n in _numbers(text) if n not in sources)
    if unknown:
        problems.append(f"Numbers not in the resume or the posting: {', '.join(unknown)}")
    return problems
