"""The code verdict on one version (specs/resume-tailor.md section 4): hard checks on the tex, the evaluation's
schema, and when to stop, go on or ask the applicant. Pure functions; the model never decides these."""

import re

from .config import Checks, Template

DIMENSIONS = ["role_focus", "transferability", "tech_purpose", "language", "appeal", "format"]
NUMBER = re.compile(r"\d+(?:\.\d+)?")
DECISION = re.compile(r"^\s*[-*]\s*(I\d+)\s*[:：]\s*(部分采纳|不采纳|采纳)", re.M)
ISSUE_ID = re.compile(r"^I\d+$")
ARROWS = re.compile(r"→|\\rightarrow|\\to\b|\\longrightarrow")
DOUBLED_VERBS = re.compile(
    r"\\item\s+(Designed and built|Designed and developed|Developed and implemented|Designed and implemented|"
    r"Built and deployed|Developed and deployed)\b",
    re.I,
)
BOLD = re.compile(r"\\textbf\{([^{}]*)\}")
# What a version may not add beyond v00 (the applicant's own, trusted tex): commands that read files or build
# command names, any control sequence with @ (internal names such as \@@input), and file paths that leave the
# empty build folder. Each is allowed when the same text already appears in v00, so a template's own
# \input{glyphtounicode} keeps working. Compilation runs in an empty temporary folder, so a plain relative name
# can only reach the TeX distribution.
FILE_COMMANDS = re.compile(
    r"\\(?:input|include(?!graphics)|verbatiminput|verbatimtabinput|VerbatimInput|listinginput|lstinputlisting|"
    r"inputminted|openin|read\b|readline|InputIfFileExists|IfFileExists|CatchFile\w*|csname|catcode|write18|"
    r"ShellEscape|directlua|luaexec|import|subimport|includepdf|includestandalone|subfile\w*|scantokens|"
    r"endlinechar|makeatletter|[A-Za-z]*@[A-Za-z@]*)"
)
PATH = re.compile(r"(?<![\w:/.])(?:~/|\.\./|/(?:[\w.-]+/)+[\w.-]*)")
GRAPHICS = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def strip_comments(tex: str) -> str:
    """Drops comments: a % preceded by an even number of backslashes (\\\\% is a line break, then a comment)."""
    return "\n".join(re.sub(r"((?:^|[^\\])(?:\\\\)*)%.*$", r"\1", line) for line in tex.splitlines())


def _blocks(tex: str, template: Template) -> list[str]:
    env = re.escape(template.items_env)
    return re.findall(rf"\\begin\{{{env}\}}(.*?)\\end\{{{env}\}}", tex, re.S)


def item_numbers(tex: str, template: Template) -> set[str]:
    out: set[str] = set()
    for block in _blocks(tex, template):
        out.update(NUMBER.findall(block))
    return out


def _entry(template: Template) -> str:
    return r"\\(?:" + "|".join(map(re.escape, template.entry_macros)) + r")\{"


def _section_items(tex: str, name: str, template: Template) -> str | None:
    """The items of the entry whose macro line names `name`, up to the next entry or section."""
    m = re.search(_entry(template) + r"[^\n]*?" + re.escape(name), tex)
    if not m:
        return None
    rest = tex[m.end() :]
    nxt = re.search(_entry(template) + r"|\\section", rest)
    blocks = _blocks(rest[: nxt.start()] if nxt else rest, template)
    return blocks[0] if blocks else ""


def check_tex(tex: str, base_tex: str | None, approved: set[str], checks: Checks, template: Template) -> list[str]:
    """Reasons this tex breaks a hard check; comments never count. `base_tex` is v00 (None for v00 itself)."""
    tex = strip_comments(tex)
    reasons = []
    if base_tex is not None:
        reasons += _added_file_access(tex, strip_comments(base_tex))
    for name in checks.must_keep:
        if not re.search(_entry(template) + r"[^\n]*" + re.escape(name), tex):
            reasons.append(f"Missing the {name} entry (it must stay)")
    for name in checks.approved_only_sections:
        items = _section_items(tex, name, template)
        if items is None:
            reasons.append(f"Cannot find the {name} entry to check its numbers")
            continue
        bad = [w for w in re.findall(r"\S*\d\S*", items) if not set(NUMBER.findall(w)) <= approved]
        if bad:
            reasons.append(f"{name} items use numbers the applicant has not approved: {', '.join(bad[:5])}")
    if base_tex is not None:
        allowed = item_numbers(strip_comments(base_tex), template) | approved
        new = sorted(item_numbers(tex, template) - allowed)
        if new:
            reasons.append(
                f"Numbers the base version does not have: {', '.join(new)} (numbers come from the applicant's "
                "material; add confirmed ones to approved_numbers)"
            )
    if checks.no_arrows and ARROWS.search(tex):
        reasons.append("Uses an arrow symbol (ATS may misread it); write 'from X to Y'")
    if checks.no_doubled_verbs:
        for m in DOUBLED_VERBS.finditer(tex):
            reasons.append(f"Doubled verbs: {m.group(1)} (one strong verb per item)")
    if checks.max_bold_per_item or checks.no_full_bold:
        for block in _blocks(tex, template):
            for item in re.split(r"\\item\b", block)[1:]:
                item = item.strip()
                if not item:
                    continue
                bolds = BOLD.findall(item)
                if checks.max_bold_per_item and len(bolds) > checks.max_bold_per_item:
                    reasons.append(f"More than {checks.max_bold_per_item} bold phrases in one item: {item[:40]}...")
                plain = BOLD.sub("", item)
                if checks.no_full_bold and bolds and not re.sub(r"[\s.,;:]", "", plain):
                    reasons.append(f"A whole item in bold: {item[:40]}...")
    return reasons


def _added_file_access(tex: str, base: str) -> list[str]:
    """Constructs that could compile other files into the PDF and that v00 does not already have, verbatim."""

    def fragment(m: re.Match) -> str:
        return tex[m.start() : tex.find("\n", m.end()) if "\n" in tex[m.end() :] else len(tex)]

    added = sorted({m.group(0) for m in FILE_COMMANDS.finditer(tex) if fragment(m) not in base})
    added += sorted({m.group(0) for m in PATH.finditer(tex) if m.group(0) not in base})
    added += sorted({g for g in GRAPHICS.findall(tex) if (g.startswith(("/", "~")) or ".." in g) and g not in base})
    if not added:
        return []
    return [f"Commands or paths that read other files are not allowed: {', '.join(added)} (keep the tex "
            "self-contained; only what v00 already has may stay)"]  # fmt: skip


def pages_problem(pages: int | None) -> list[str]:
    if pages is None:
        return ["Not rendered"]
    return [] if pages == 1 else [f"More than one page: {pages} pages"]


def evaluation_problems(ev) -> list[str]:
    """Schema problems of the evaluator's output (it is never edited, only redone)."""
    errs = []
    scores = ev.get("scores") if isinstance(ev, dict) else None
    if not isinstance(scores, dict):
        errs.append("evaluation has no scores")
    else:
        errs += [f"missing dimension {d}" for d in DIMENSIONS if d not in scores]
        for d, v in scores.items():
            if d not in DIMENSIONS:
                errs.append(f"unknown dimension {d}")
                continue
            if not isinstance(v, dict):
                errs.append(f"dimension {d} is malformed")
                continue
            s = v.get("score")
            if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 5:
                errs.append(f"dimension {d} score must be an integer 1-5, got {s!r}")
            if not isinstance(v.get("reason"), str) or not v["reason"].strip():
                errs.append(f"dimension {d} has no reason")
    issues = ev.get("issues") if isinstance(ev, dict) else None
    if not isinstance(issues, list):
        errs.append("evaluation has no issues list")
        issues = []
    seen = set()
    for it in issues:
        iid = it.get("id") if isinstance(it, dict) else None
        label = iid if isinstance(iid, str) else repr(iid)
        if not isinstance(iid, str) or not ISSUE_ID.match(iid):
            errs.append(f"bad issue id {label}")
        elif iid in seen:
            errs.append(f"duplicate issue id {iid}")
        else:
            seen.add(iid)
        if not isinstance(it, dict):
            continue
        if it.get("severity") not in ("major", "minor"):
            errs.append(f"{label} severity must be major or minor, got {it.get('severity')!r}")
        for f in ("location", "problem", "suggestion"):
            if not isinstance(it.get(f), str) or not it[f].strip():
                errs.append(f"{label} has no {f}")
    if not errs:
        ids = [it["id"] for it in issues]
        if ids != [f"I{n}" for n in range(1, len(ids) + 1)]:
            errs.append("issue ids must run I1, I2, ... without gaps")
    return errs


def total(ev: dict) -> int:
    return sum(v["score"] for v in ev["scores"].values())


def verdict(
    k: int,
    ev: dict,
    response: str | None,
    history: list[int | None],
    evaluations: int,
    max_evaluations: int,
) -> dict:
    """stop | continue | ask_user | fail for version k with a schema-valid evaluation. `history` holds the totals of
    v00..v(k-1) (None when unknown); `evaluations` counts every evaluation of the run so far."""
    t = total(ev)
    if all(v["score"] >= 4 for v in ev["scores"].values()) and not any(i["severity"] == "major" for i in ev["issues"]):
        return _emit("stop", ["Every dimension is 4 or more and nothing is major"], t)
    if response is None:
        return _emit("continue", ["Write the response to each issue"], t)
    decisions = dict(DECISION.findall(response))
    missing = [i["id"] for i in ev["issues"] if i["id"] not in decisions]
    if missing:
        return _emit("fail", ["The response does not handle: " + ", ".join(missing)], t)
    accepted = any(d in ("采纳", "部分采纳") for d in decisions.values())
    if k >= 2 and not accepted:
        p1, p2 = history[k - 1], history[k - 2]
        if p1 is not None and p2 is not None and t <= p1 <= p2:
            return _emit("ask_user", [f"Two rounds without a higher total ({p2}, {p1}, {t}) and nothing accepted"], t)
    if k >= 3 and None not in history[:k]:
        earlier = max(history[: k - 2])
        if max([*history[k - 2 : k], t]) <= earlier:
            return _emit(
                "ask_user", [f"Three rounds below the earlier best ({earlier}); the stuck issues likely need facts"], t
            )
    if evaluations >= max_evaluations:
        return _emit("ask_user", [f"Reached the limit of {max_evaluations} evaluations for this run"], t)
    return _emit("continue", ["A dimension is below 4 or an issue is major"], t)


def _emit(status: str, reasons: list[str], t: int | None) -> dict:
    return {"status": status, "reasons": reasons, "total": t}


def skills_only_edit(base: str, edited: str) -> bool:
    """Keep everything outside the existing skills section byte-for-byte identical."""
    heading = re.compile(r"(?mi)^[ \t]*\\(?:sectionrule|section\*?)\{([^{}]+)\}")

    def bounds(text: str) -> tuple[str, str] | None:
        headings = list(heading.finditer(text))
        skills = [i for i, match in enumerate(headings) if match[1].lower() in {"skills", "technical skills"}]
        if len(skills) != 1:
            return None
        i = skills[0]
        end = headings[i + 1].start() if i + 1 < len(headings) else text.find(r"\end{document}", headings[i].end())
        return (text[: headings[i].end()], text[end:]) if end >= 0 else None

    original = bounds(base)
    return original is not None and original == bounds(edited) and edited != base
