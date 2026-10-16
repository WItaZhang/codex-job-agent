"""Read back the final page of a job's tab over CDP (specs/form-fill.plan.md, section 1).

The script run in the page (EXTRACT_JS) only collects raw nodes; `normalize` turns them into ReadFields in Python.
Nothing here navigates, clicks, types or closes a tab. Everything is generic: no site names or site selectors."""

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from ..domain import InvariantError
from .models import ReadField

# Runs in each frame. Returns {"url", "controls": [...], "pairs": [...]}; "order" is the document order in the frame.
EXTRACT_JS = r"""() => {
  const NO_TEXT = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SELECT", "OPTION", "TEXTAREA", "INPUT",
    "BUTTON", "IFRAME", "SVG"]);
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);
  const INLINE = new Set(["SPAN", "B", "STRONG", "EM", "I", "SMALL", "ABBR", "SUP", "SUB", "BR", "MARK", "U"]);
  const EDITABLE = '[contenteditable=""],[contenteditable="true"],[contenteditable="plaintext-only"]';
  const WIDGETS = '[role=combobox],[role=listbox],[role=radiogroup],' + EDITABLE;
  const CONTROLS = 'input,select,textarea,' + WIDGETS;
  const root = document.body || document.documentElement;

  const order = new Map();
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.currentNode, n = 0; el; el = walker.nextNode()) order.set(el, n++);

  const shown = (el) => {
    if (!el || el.nodeType !== 1 || el.getClientRects().length === 0) return false;
    return getComputedStyle(el).visibility !== "hidden";
  };
  const block = (el) => {
    const d = getComputedStyle(el).display;
    return !d.startsWith("inline") && d !== "contents";
  };
  const textOf = (top) => {
    if (!top) return "";
    const parts = [];
    const visit = (node) => {
      if (node.nodeType === 3) { parts.push(node.data); return; }
      if (node.nodeType !== 1 || NO_TEXT.has(node.tagName.toUpperCase())) return;
      if (node.getAttribute("aria-hidden") === "true") return;
      if (node !== top && node.matches(WIDGETS)) return;
      const s = getComputedStyle(node);
      if (s.display === "none" || s.visibility === "hidden") return;
      const b = block(node);
      if (b) parts.push(" ");
      for (const c of node.childNodes) visit(c);
      if (b) parts.push(" ");
    };
    visit(top);
    return parts.join("").replace(/\s+/g, " ").trim();
  };
  const byIds = (ids) => (ids || "").split(/\s+/).filter(Boolean)
    .map((id) => textOf(document.getElementById(id))).filter(Boolean).join(" ");

  // Visible text nodes in document order, for "nearest preceding text".
  const texts = [];
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let t = tw.nextNode(); t; t = tw.nextNode()) {
    const p = t.parentElement;
    if (!p || !t.data.trim() || p.closest("script,style,noscript,template,select,option,textarea,button")) continue;
    if (p.closest('[aria-hidden="true"]') || !shown(p)) continue;
    texts.push(t);
  }
  const preceding = (el) => {
    let lo = 0, hi = texts.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (texts[mid].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) { found = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    if (found < 0) return "";
    const own = texts[found].parentElement;
    let box = own;
    while (box && box !== root && !block(box)) box = box.parentElement;
    const t = textOf(box || own);
    return t.length <= 300 ? t : textOf(own).slice(0, 300);
  };

  const inForm = (el, sel) => Array.from((el.form || root).querySelectorAll(sel));
  const container = (el) => {
    const name = el.getAttribute("name");
    if ((el.type === "radio" || el.type === "checkbox") && name) {
      const members = inForm(el, `input[type="${el.type}"][name="${CSS.escape(name)}"]`);
      if (members.length > 1) {
        let box = el.parentElement;
        while (box && !members.every((m) => box.contains(m))) box = box.parentElement;
        if (box) return box;
      }
    }
    return el.closest("label") || el;
  };
  const labelsOf = (el) => {
    const id = el.getAttribute("id");
    const forLabels = id ? Array.from(document.querySelectorAll(`label[for="${CSS.escape(id)}"]`)) : [];
    const wrap = el.closest("label");
    const fieldset = el.closest("fieldset");
    const legend = fieldset ? fieldset.querySelector(":scope > legend") : null;
    return {
      label_for: forLabels.map(textOf).filter(Boolean).join(" "),
      labelledby: byIds(el.getAttribute("aria-labelledby")),
      aria_label: el.getAttribute("aria-label") || "",
      wrapping: wrap && !forLabels.includes(wrap) ? textOf(wrap) : "",
      legend: textOf(legend),
      preceding: preceding(container(el)),
      placeholder: el.getAttribute("placeholder") || el.getAttribute("aria-placeholder") || "",
    };
  };
  const required = (el) => !!el.required || el.getAttribute("aria-required") === "true";
  const ariaOptions = (box, roleSel, flag) => box ? Array.from(box.querySelectorAll(roleSel)).map((o) => ({
    text: textOf(o), value: o.getAttribute("data-value") || "", selected: o.getAttribute(flag) === "true",
  })) : [];
  const popup = (el) => {
    for (const id of ((el.getAttribute("aria-controls") || "") + " " + (el.getAttribute("aria-owns") || ""))
      .split(/\s+/).filter(Boolean)) {
      const box = document.getElementById(id);
      if (box) return box;
    }
    return null;
  };
  const display = (el) => {
    let box = el.parentElement;
    for (let i = 0; box && i < 3; i++, box = box.parentElement) {
      if (box.querySelector("label") || box.querySelectorAll(CONTROLS).length > 1) break;
      const t = textOf(box);
      if (t) return t;
    }
    return "";
  };

  const comboboxes = Array.from(root.querySelectorAll("[role=combobox]"));
  const popups = new Set(comboboxes.map(popup).filter(Boolean));
  const controls = [];
  for (const el of root.querySelectorAll(CONTROLS)) {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = tag === "input" ? (el.type || "text").toLowerCase() : tag === "select" ? el.type : "";
    if (tag === "input" && SKIP_TYPES.has(type)) continue;
    const loose = type === "file" || type === "radio" || type === "checkbox";
    const visible = shown(el) || (loose && (Array.from(el.labels || []).some(shown) || shown(el.parentElement)));
    if (!visible) continue;
    const host = el.parentElement && el.parentElement.closest(WIDGETS);
    if (host) continue;  // read as part of the enclosing widget
    if (role === "listbox" && popups.has(el)) continue;
    if (role === "radiogroup" && el.querySelector("input[type=radio]")) continue;
    const node = {
      order: order.get(el), tag, type, role, id: el.getAttribute("id") || "", name: el.getAttribute("name") || "",
      labels: labelsOf(el), value: "", options: [], required: required(el),
    };
    if (el.matches(EDITABLE) && role !== "combobox") {
      node.editable = true;
      node.value = el.innerText;
    } else if (role === "combobox") {
      const inner = tag === "input" ? el : el.querySelector("input");
      node.value = inner ? inner.value : textOf(el);
      node.display = node.value ? "" : display(el);
      node.options = ariaOptions(popup(el), "[role=option]", "aria-selected");
    } else if (role === "listbox") {
      node.options = ariaOptions(el, "[role=option]", "aria-selected");
    } else if (role === "radiogroup") {
      node.options = ariaOptions(el, "[role=radio]", "aria-checked");
      node.value = node.options.filter((o) => o.selected).map((o) => o.text).join("; ");
    } else if (tag === "select") {
      node.options = Array.from(el.options).map((o) => ({ text: o.text, value: o.value, selected: o.selected }));
      node.value = node.options.filter((o) => o.selected).map((o) => o.text).join("; ");
    } else if (tag === "textarea") {
      node.value = el.value;
    } else if (type === "file") {
      node.value = Array.from(el.files || []).map((f) => f.name).join(", ");
    } else if (type === "radio" || type === "checkbox") {
      const name = node.name;
      const box = el.closest("[role=radiogroup],[role=group],fieldset");
      node.group = name ? "n:" + name : box ? "c:" + order.get(box) : "";
      const gbox = el.closest("[role=radiogroup],[role=group]");
      node.group_labels = {
        labelledby: gbox ? byIds(gbox.getAttribute("aria-labelledby")) : "",
        aria_label: gbox ? gbox.getAttribute("aria-label") || "" : "",
      };
      if (gbox && gbox.getAttribute("aria-required") === "true") node.required = true;
      node.checked = !!el.checked;
      node.option_label = Array.from(el.labels || []).map(textOf).filter(Boolean).join(" ")
        || el.getAttribute("aria-label") || byIds(el.getAttribute("aria-labelledby")) || el.value || "";
    } else if (type === "password") {
      node.value = el.value ? "[已填]" : "";
    } else {
      node.value = el.value;
    }
    controls.push(node);
  }

  const pairs = [];
  const used = new Set();
  const hasControl = (el) => el.matches(CONTROLS) || el.querySelector(CONTROLS) !== null;
  const addPair = (q, valueEls, source) => {
    if (used.has(q) || !shown(q) || hasControl(q) || valueEls.some(hasControl)) return;
    used.add(q);
    valueEls.forEach((v) => used.add(v));
    pairs.push({ order: order.get(q), question: textOf(q), value: valueEls.map(textOf).join(" "), source });
  };
  for (const dt of root.querySelectorAll("dt")) {
    const dd = dt.nextElementSibling;
    if (dd && dd.tagName === "DD") addPair(dt, [dd], "dt");
  }
  for (const tr of root.querySelectorAll("tr")) {
    const th = tr.querySelectorAll(":scope > th"), td = Array.from(tr.querySelectorAll(":scope > td"));
    if (th.length === 1 && td.length >= 1) addPair(th[0], td, "th");
  }
  for (const el of root.querySelectorAll("*")) {
    if (used.has(el) || NO_TEXT.has(el.tagName.toUpperCase())) continue;
    if (!Array.from(el.children).every((c) => INLINE.has(c.tagName.toUpperCase()))) continue;
    const v = el.nextElementSibling;
    if (!v || used.has(v)) continue;
    const t = textOf(el);
    const orphanLabel = el.tagName === "LABEL" && !el.control;
    if (!t || t.length > 200 || !(/[:?：？]$/.test(t) || orphanLabel)) continue;
    const vt = textOf(v);
    if (vt.length > 500 || /[:：]$/.test(vt)) continue;
    addPair(el, [v], "label");
  }
  return { url: location.href, controls, pairs };
}"""

# Runs in each frame for the screenshot blackout. One node per element under body (open shadow roots count as the
# host's text): its index, its parent's index, a CSS path to it and the text it shows. Inputs and selects stand for
# their value; a password's value is never taken.
CONTACT_JS = r"""() => {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const nodes = [];
  // Paths start at body: Playwright's screenshot mask does not match selectors that start at the html element.
  const path = (el) => {
    const parts = [];
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      let n = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) n++;
      parts.unshift(`:nth-child(${n})`);
    }
    return ["body", ...parts].join(" > ");
  };
  const walk = (el, parent) => {
    const tag = el.tagName.toUpperCase();
    if (SKIP.has(tag)) return;
    let text = el.textContent || "";
    if (tag === "INPUT" || tag === "TEXTAREA") {
      text = (el.type || "").toLowerCase() === "password" ? "" : (el.value || "");
      text += " " + (el.getAttribute("placeholder") || "");
    } else if (tag === "SELECT") {
      text = Array.from(el.selectedOptions || []).map((o) => o.text).join(" ");
    }
    if (el.shadowRoot) text += " " + (el.shadowRoot.textContent || "");
    const i = nodes.length;
    nodes.push({ i, parent, path: path(el), text });
    if (tag !== "SELECT") for (const c of el.children) walk(c, i);
  };
  if (!document.body) throw new Error("no body");
  walk(document.body, -1);
  return nodes;
}"""
SCREENSHOT_NOTE = "screenshot-not-saved.txt"

QUESTION_SOURCES = ("label_for", "labelledby", "aria_label", "wrapping", "legend", "preceding", "placeholder")
GROUP_SOURCES = ("group_labelledby", "group_aria_label", "legend", "preceding")


class Redactor(Protocol):
    def mask(self, text: str) -> str: ...

    def mask_json(self, obj): ...


class ReadbackError(RuntimeError):
    """The final page could not be found or read."""


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _question(text) -> tuple[str, bool]:
    """Cleaned question text, and whether it carried a required asterisk."""
    q = _clean(text)
    starred = False
    while q and q[-1] in ":：*":
        starred = starred or q[-1] == "*"
        q = q[:-1].rstrip()
    return q, starred


def _pick(sources: dict, order: tuple[str, ...]) -> tuple[str, bool]:
    for key in order:
        q, starred = _question(sources.get(key))
        if q:
            return q, starred
    return "", False


def _control_kind(node: dict) -> str:
    tag, role, typ = node.get("tag", ""), node.get("role", ""), node.get("type", "")
    if role == "combobox":
        return "combobox"
    if role == "radiogroup":
        return "radio"
    if tag == "select" or role == "listbox":
        return "select"
    if tag == "textarea" or node.get("editable"):
        return "textarea"
    if typ in ("file", "checkbox", "radio"):
        return typ
    return "text"


def _options(node: dict) -> tuple[list[str], list[str]]:
    """(option texts, selected texts). A select's placeholder option (empty value) is left out."""
    texts, chosen = [], []
    for o in node.get("options") or []:
        if not isinstance(o, dict):
            raise InvariantError("option must be an object")
        text = _clean(o.get("text"))
        if not text or (node.get("tag") == "select" and o.get("value") == ""):
            continue
        texts.append(text)
        if o.get("selected"):
            chosen.append(text)
    return texts, chosen


def _single(node: dict) -> dict:
    kind = _control_kind(node)
    question, starred = _pick(node.get("labels") or {}, QUESTION_SOURCES)
    texts, chosen = _options(node)
    if kind == "checkbox":
        value = "Yes" if node.get("checked") else ""
    elif kind == "radio" and node.get("role") != "radiogroup":
        texts = [_clean(node.get("option_label"))]
        value = texts[0] if node.get("checked") else ""
    elif kind == "select" and node.get("tag") == "select":
        value = "; ".join(chosen)
    else:
        value = _clean(node.get("value")) or _clean(node.get("display")) or "; ".join(chosen)
    return {
        "question": question,
        "control": kind,
        "value": _clean(value),
        "options": tuple(texts),
        "required": bool(node.get("required")) or starred,
    }


def _group(members: list[dict]) -> dict:
    first = members[0]
    sources = {**(first.get("labels") or {})}
    for key, value in (first.get("group_labels") or {}).items():
        sources[f"group_{key}"] = value
    question, starred = _pick(sources, GROUP_SOURCES)
    labels = [_clean(m.get("option_label")) for m in members]
    return {
        "question": question,
        "control": _control_kind(first),
        "value": "; ".join(label for m, label in zip(members, labels, strict=True) if m.get("checked") and label),
        "options": tuple(label for label in labels if label),
        "required": any(m.get("required") for m in members) or starred,
    }


def _frame_items(fi: int, frame: dict) -> list[tuple[tuple, str, dict]]:
    controls, pairs = frame.get("controls") or [], frame.get("pairs") or []
    if not isinstance(controls, list) or not isinstance(pairs, list):
        raise InvariantError("frame controls and pairs must be lists")
    items, groups = [], {}
    for node in controls:
        if not isinstance(node, dict):
            raise InvariantError("control must be an object")
        kind = _control_kind(node)
        key = node.get("group") or ""
        if kind in ("radio", "checkbox") and node.get("role") != "radiogroup" and key:
            if (kind, key) not in groups:
                groups[(kind, key)] = []
                items.append(((fi, node.get("order", 0)), "group", groups[(kind, key)]))
            groups[(kind, key)].append(node)
        else:
            items.append(((fi, node.get("order", 0)), "control", node))
    for pair in pairs:
        if not isinstance(pair, dict):
            raise InvariantError("pair must be an object")
        items.append(((fi, pair.get("order", 0)), "pair", pair))
    return items


def normalize(raw: dict) -> tuple[ReadField, ...]:
    """Raw nodes from EXTRACT_JS (one entry per frame) -> ReadFields numbered p1.1, p1.2 ... in page order."""
    if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list):
        raise InvariantError("raw page must be an object with a frames list")
    items = []
    for fi, frame in enumerate(raw["frames"]):
        if not isinstance(frame, dict):
            raise InvariantError("frame must be an object")
        if not frame.get("error"):
            items.extend(_frame_items(fi, frame))
    items.sort(key=lambda item: item[0])  # stable: ties keep extraction order

    drafts = []
    for _, what, payload in items:
        if what == "group":
            lone_checkbox = len(payload) == 1 and _control_kind(payload[0]) == "checkbox"
            drafts.append(_single(payload[0]) if lone_checkbox else _group(payload))
        elif what == "control":
            drafts.append(_single(payload))
        else:
            question, _ = _question(payload.get("question"))
            value = _clean(payload.get("value"))
            if question and value:
                drafts.append(
                    {"question": question, "control": "review", "value": value, "options": (), "required": False}
                )

    seen = {(d["question"].lower(), d["value"].lower()) for d in drafts if d["control"] != "review"}
    kept = [d for d in drafts if d["control"] != "review" or (d["question"].lower(), d["value"].lower()) not in seen]
    return tuple(
        ReadField(id=f"p1.{n}", origin="review_text" if d["control"] == "review" else "control", **d)
        for n, d in enumerate(kept, start=1)
    )


# --- tabs -------------------------------------------------------------------------------------------------------

IGNORED_SCHEMES = ("chrome-extension://", "devtools://")


def _pages(targets: list[dict]) -> list[dict]:
    return [t for t in targets if t.get("type") == "page" and not str(t.get("url", "")).startswith(IGNORED_SCHEMES)]


def _same_url(a: str, b: str) -> bool:
    def key(u: str) -> str:
        return u.split("#", 1)[0].rstrip("/")

    return key(a) == key(b)


def _shared_segments(a: str, b: str) -> int:
    """Leading path segments two URLs share, or -1 when the hosts differ."""
    sa, sb = urlsplit(a), urlsplit(b)
    if sa.netloc.lower() != sb.netloc.lower():
        return -1
    n = 0
    for x, y in zip([p for p in sa.path.split("/") if p], [p for p in sb.path.split("/") if p], strict=False):
        if x != y:
            break
        n += 1
    return n


def find_job_tab(before: list[dict], after: list[dict], last_url: str | None) -> dict | None:
    """The job's tab among CDP /json/list targets, or None when it cannot be told apart (tab_lost)."""
    old = {t.get("id") for t in before}
    pages = _pages(after)
    new = [t for t in pages if t.get("id") not in old]
    if len(new) == 1:
        return new[0]
    if not last_url:
        return None
    pool = new or pages
    exact = [t for t in pool if _same_url(t.get("url", ""), last_url)]
    if len(exact) == 1:
        return exact[0]
    if exact or not new:
        return None
    scored = [(_shared_segments(t.get("url", ""), last_url), t) for t in new]
    best = max(score for score, _ in scored)
    winners = [t for score, t in scored if score == best]
    return winners[0] if best >= 1 and len(winners) == 1 else None


def list_targets(port: int) -> list[dict]:
    """CDP targets of the applying Chrome."""
    with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/list", timeout=3) as resp:
        data = json.load(resp)
    if not isinstance(data, list):
        raise ReadbackError("unexpected /json/list response")
    return data


# --- reading the live page --------------------------------------------------------------------------------------


def simplify_html(html: str) -> str:
    """Drop scripts, styles and stylesheet links; the rest stays for a person to look at."""
    html = re.sub(r"<(script|style|noscript)\b.*?</\1\s*>", "", html, flags=re.S | re.I)
    return re.sub(r"<link\b[^>]*>", "", html, flags=re.I)


def _find_page(browser, target: dict):
    pages = [p for ctx in browser.contexts for p in ctx.pages]
    for p in pages:
        try:
            session = p.context.new_cdp_session(p)
            try:
                info = session.send("Target.getTargetInfo")
            finally:
                session.detach()
        except Exception:  # noqa: BLE001 - fall back to the URL below
            continue
        if info.get("targetInfo", {}).get("targetId") == target.get("id"):
            return p
    same = [p for p in pages if _same_url(p.url, target.get("url", ""))]
    if len(same) == 1:
        return same[0]
    raise ReadbackError("job tab not found in the browser")


def _extract(page) -> dict:
    frames = []
    for frame in page.frames:  # main frame first
        try:
            frames.append(frame.evaluate(EXTRACT_JS))
        except Exception as exc:  # noqa: BLE001 - a frame that cannot be read is noted, not fatal
            frames.append({"url": frame.url, "error": type(exc).__name__})
    return {"url": page.url, "frames": frames}


def contact_paths(nodes, redactor: Redactor) -> list[str]:
    """CSS paths of the smallest elements showing a contact value (CONTACT_JS nodes): an element is blacked out when
    its text still holds a value after the text of its children that hold one is taken out."""
    if not isinstance(nodes, list):
        raise InvariantError("contact nodes must be a list")
    for n in nodes:
        if not (
            isinstance(n, dict)
            and isinstance(n.get("i"), int)
            and isinstance(n.get("parent"), int)
            and isinstance(n.get("path"), str)
            and isinstance(n.get("text"), str)
        ):
            raise InvariantError("contact node must have an index, a parent, a path and a text")

    def holds(text: str) -> bool:
        return redactor.mask(text) != text

    hit = [n for n in nodes if holds(n["text"])]
    children: dict[int, list[str]] = {}
    for n in hit:
        children.setdefault(n["parent"], []).append(n["text"])
    paths = []
    for n in hit:
        rest = n["text"]
        for text in children.get(n["i"], []):
            rest = rest.replace(text, " ", 1)
        if holds(rest):
            paths.append(n["path"])
    return paths


def _masked_screenshot(page, redactor: Redactor) -> tuple[bytes | None, str]:
    """A full-page screenshot with every element showing a contact value painted black for the shot only (the page
    is left as it is), or None and why when that cannot be done (specs/form-fill.md 5.6)."""
    try:
        paths = contact_paths(page.main_frame.evaluate(CONTACT_JS), redactor)
        for frame in page.frames[1:]:
            if contact_paths(frame.evaluate(CONTACT_JS), redactor):
                return None, "子框架里有联系方式，涂不到"
        masks = [page.locator(path) for path in paths]
        if any(mask.count() != 1 for mask in masks):  # the page changed under us
            return None, "页面在截图前变了"
        return page.screenshot(full_page=True, mask=masks, mask_color="#000000"), ""
    except Exception as exc:  # noqa: BLE001 - no blackout, no screenshot
        return None, type(exc).__name__


def _read_live(port: int, target: dict, redactor: Redactor) -> tuple[dict, str, bytes | None, str]:
    from playwright.sync_api import sync_playwright

    # Leaving the context stops the Playwright driver, which drops the CDP connection. browser.close() is never
    # called, so the applying Chrome and its tabs stay as they are.
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = _find_page(browser, target)
        raw, html = _extract(page), page.content()
        return raw, html, *_masked_screenshot(page, redactor)


def read_final_page(port: int, target: dict, out_dir: Path, redactor: Redactor) -> tuple[ReadField, ...]:
    """Read the job tab as it is and save page.html, screenshot.png, readback.json and raw.json (all redacted;
    contact values in the screenshot are painted black). A screenshot that cannot be blacked out is not saved;
    screenshot-not-saved.txt says so. Disconnects without closing the browser or the page."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Playwright's sync API refuses to run inside an asyncio loop (the MCP server's), so it gets its own thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        raw, html, shot, why = pool.submit(_read_live, int(port), target, redactor).result()
    fields = normalize(raw)
    (out / "page.html").write_text(redactor.mask(simplify_html(html)), encoding="utf-8")
    if shot is None:
        (out / SCREENSHOT_NOTE).write_text(redactor.mask(f"截图未存：打码失败（{why}）\n"), encoding="utf-8")
    else:
        (out / "screenshot.png").write_bytes(shot)
    dumped = [f.model_dump(mode="json") for f in fields]
    (out / "readback.json").write_text(_dump(redactor.mask_json(dumped)), encoding="utf-8")
    (out / "raw.json").write_text(_dump(redactor.mask_json(raw)), encoding="utf-8")
    return fields


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
