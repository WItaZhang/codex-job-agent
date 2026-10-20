"""Read back the final page of a job's tab over CDP (specs/form-fill.plan.md, section 1).

The script run in the page (EXTRACT_JS) only collects raw nodes; `normalize` turns them into ReadFields in Python.
Nothing here navigates, clicks, types or closes a tab. Everything is generic: no site names or site selectors."""

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from ..domain import InvariantError
from .columns import mask_snapshot
from .compare import HIDDEN as HIDDEN_TEXT
from .matching import NAVIGATION, is_empty, norm
from .models import PageState, ReadField
from .recorder import Snapshot

# Runs in each frame. Returns {"url", "scope", "controls": [...], "pairs": [...], "actions": [...], "notices": [...],
# "step", "headings"}; "order" is the document order in the frame. An open modal dialog makes the rest of the page
# inert, so only the dialog is read ("scope": "dialog"), unless nothing in it can be read. "actions" are the visible
# button texts there, "notices" the page's headings, alerts and status lines (both for check 4). "step" is the text of
# the current step (aria-current="step"), "headings" the visible headings read, and each control and pair carries
# its "section", the nearest heading above it (the page's title and the fields' identity, spec 4.3).
EXTRACT_JS = r"""() => {
  const NO_TEXT = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SELECT", "OPTION", "TEXTAREA", "INPUT",
    "BUTTON", "IFRAME", "SVG"]);
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);
  const INLINE = new Set(["SPAN", "B", "STRONG", "EM", "I", "SMALL", "ABBR", "SUP", "SUB", "BR", "MARK", "U"]);
  const EDITABLE = '[contenteditable=""],[contenteditable="true"],[contenteditable="plaintext-only"]';
  const WIDGETS = '[role=combobox],[role=listbox],[role=radiogroup],[role=checkbox],[role=switch],' + EDITABLE;
  const LISTBOX_BUTTONS = 'button[aria-haspopup="listbox"],[role=button][aria-haspopup="listbox"]';
  const CONTROLS = 'input,select,textarea,' + WIDGETS + ',' + LISTBOX_BUTTONS;
  const TOGGLES = 'button[aria-pressed],[role=button][aria-pressed]';
  const HEADINGS = 'h1,h2,h3,h4,h5,h6,[role=heading]';
  const ACTIONS = 'button,input[type=submit],input[type=button],[role=button]';
  const NOTICES = HEADINGS + ',[role=alert],[role=status]';
  const GROUPS = 'fieldset,[role=group],[role=radiogroup]';

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
      if (node.tagName.toUpperCase() === "BR") { parts.push(" "); return; }
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
  const buttonText = (b) => ((b.tagName === "INPUT" ? b.value : b.innerText) || b.getAttribute("aria-label") || "")
    .replace(/\s+/g, " ").trim();
  const byIds = (ids) => (ids || "").split(/\s+/).filter(Boolean)
    .map((id) => textOf(document.getElementById(id))).filter(Boolean).join(" ");
  const leaf = (el) => Array.from(el.children).every((c) => INLINE.has(c.tagName.toUpperCase()));
  const ownText = (el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.data.trim());
  const hasControl = (el) => el.matches(CONTROLS) || el.querySelector(CONTROLS + "," + TOGGLES) !== null;
  const required = (el) => !!el.required || el.getAttribute("aria-required") === "true";
  // A CSS path from body, as CONTACT_JS writes it: the element to hide when its field is a contact field (5.6).
  const pathOf = (el) => {
    const parts = [];
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (!e.parentElement) return "";
      let n = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) n++;
      parts.unshift(`:nth-child(${n})`);
    }
    return ["body", ...parts].join(" > ");
  };

  const read = (root) => {
    // Visible text nodes in document order, for "nearest preceding text".
    const texts = [];
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let t = tw.nextNode(); t; t = tw.nextNode()) {
      const p = t.parentElement;
      if (!p || !t.data.trim() || p.closest("script,style,noscript,template,select,option,textarea,button")) continue;
      if (p.closest('[aria-hidden="true"]') || !shown(p)) continue;
      texts.push(t);
    }
    // Visible headings in document order, for each field's section (the nearest one above it).
    const heads = Array.from(root.querySelectorAll(HEADINGS)).filter(shown)
      .map((h) => [order.get(h), textOf(h).slice(0, 120)]).filter(([, t]) => t);
    const sectionOf = (el) => {
      const at = order.get(el);
      let found = "";
      for (const [o, t] of heads) { if (o < at) found = t; else break; }
      return found;
    };
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
    // The smallest box around all radios or checkboxes of the same name (null for a lone one).
    const commonBox = (el) => {
      const name = el.getAttribute("name");
      if (!name) return null;
      const members = inForm(el, `input[type="${el.type}"][name="${CSS.escape(name)}"]`);
      if (members.length < 2) return null;
      let box = el.parentElement;
      while (box && !members.every((m) => box.contains(m))) box = box.parentElement;
      return box;
    };
    const groupBox = (el) => commonBox(el) || el.closest(GROUPS);
    // The group's own title: the first heading-like text inside its box, before its first option.
    const innerTitle = (box) => {
      if (!box) return "";
      const first = box.querySelector("input[type=radio],input[type=checkbox]");
      if (!first) return "";
      for (const e of box.querySelectorAll("legend,label,p,div,span," + HEADINGS)) {
        if (!(e.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
        if (e.contains(first) || hasControl(e) || !leaf(e) || !shown(e)) continue;
        if (e.tagName === "LABEL" && e.control) continue;  // an option's own label
        const t = textOf(e);
        if (t) return t;
      }
      return "";
    };
    const container = (el) =>
      ((el.type === "radio" || el.type === "checkbox") && commonBox(el)) || el.closest("label") || el;
    const labelsOf = (el) => {
      const id = el.getAttribute("id");
      const forLabels = id ? Array.from(document.querySelectorAll(`label[for="${CSS.escape(id)}"]`)) : [];
      const wrap = el.closest("label");
      const fieldset = el.closest("fieldset");
      const legend = fieldset ? fieldset.querySelector(":scope > legend") : null;
      // A required mark inside the field's label, legend or wrapping label, even one hidden from screen readers.
      const star = [...forLabels, wrap, legend].some((l) => !!l
        && Array.from(l.querySelectorAll("*")).some((e) => e.children.length === 0 && e.textContent.trim() === "*"));
      return {
        star,
        label_for: forLabels.map(textOf).filter(Boolean).join(" "),
        labelledby: byIds(el.getAttribute("aria-labelledby")),
        aria_label: el.getAttribute("aria-label") || "",
        wrapping: wrap && !forLabels.includes(wrap) ? textOf(wrap) : "",
        legend: textOf(legend),
        preceding: preceding(container(el)),
        placeholder: el.getAttribute("placeholder") || el.getAttribute("aria-placeholder") || "",
      };
    };
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

    // Pickers: a search box (enterkeyhint=search, type=search, role=searchbox) whose value is the items picked, shown
    // as options of a list beside it in the same field; what was typed into the box is not a value. The list is read
    // as part of the picker, not on its own.
    const consumed = new Set();
    const picked = new Map();
    for (const el of root.querySelectorAll("input")) {
      const t = (el.type || "text").toLowerCase(), r = (el.getAttribute("role") || "").toLowerCase();
      if (!(el.getAttribute("enterkeyhint") === "search" || t === "search" || r === "searchbox")) continue;
      const label = (el.labels && el.labels[0]) || null;
      let box = el.parentElement;
      if (label) { while (box && !box.contains(label)) box = box.parentElement; }
      else box = el.closest("[role=group],fieldset") || el.parentElement;
      if (!box || box === root) continue;
      const others = Array.from(box.querySelectorAll("input,select,textarea"))
        .filter((x) => x !== el && !SKIP_TYPES.has((x.type || "").toLowerCase()));
      if (others.length) continue;  // the box is a whole section, not this one field
      const own = popup(el);
      const lists = Array.from(box.querySelectorAll("[role=listbox]")).filter((l) => l !== own);
      lists.forEach((l) => consumed.add(l));
      // Only items shown: a list of suggestions kept hidden in the same box is not what was picked.
      picked.set(el, lists.filter(shown).flatMap((l) => Array.from(l.querySelectorAll("[role=option]")))
        .filter(shown).map(textOf).filter(Boolean));
    }

    // Yes / No style button groups (aria-pressed): one control per parent with two or more such buttons. A native
    // input kept beside them for the form's sake is read as part of the group, not on its own.
    const toggles = new Map();
    for (const b of root.querySelectorAll(TOGGLES)) {
      if (!shown(b) || !b.parentElement) continue;
      if (!toggles.has(b.parentElement)) toggles.set(b.parentElement, []);
      toggles.get(b.parentElement).push(b);
    }
    for (const [box, buttons] of Array.from(toggles)) if (buttons.length < 2) toggles.delete(box);
    const beside = (el) => toggles.has(el.parentElement)
      || (!!el.parentElement && toggles.has(el.parentElement.parentElement));

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
      if (role === "listbox" && (popups.has(el) || consumed.has(el))) continue;
      if (role === "radiogroup" && el.querySelector("input[type=radio]")) continue;
      if ((type === "radio" || type === "checkbox") && beside(el)) continue;
      const labels = labelsOf(el);
      // A listbox button is a field only when a label names it; menus in the page's header are not.
      if (el.matches(LISTBOX_BUTTONS) && !labels.label_for && !labels.labelledby) continue;
      // An icon-only listbox button beside another control is that control's toggle, not a field of its own.
      if (el.matches(LISTBOX_BUTTONS) && !buttonText(el) && el.parentElement
        && Array.from(el.parentElement.querySelectorAll(CONTROLS)).some((c) => c !== el)) continue;
      const node = {
        order: order.get(el), tag, type, role, id: el.getAttribute("id") || "", name: el.getAttribute("name") || "",
        labels, value: "", options: [], required: required(el), section: sectionOf(el), path: pathOf(el),
      };
      if (el.matches(LISTBOX_BUTTONS)) {
        node.listbox_button = true;
        node.value = buttonText(el);
      } else if (picked.has(el)) {
        node.picker = true;
        node.value = picked.get(el).join("; ");
      } else if (el.matches(EDITABLE) && role !== "combobox") {
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
      } else if ((role === "checkbox" || role === "switch") && tag !== "input") {
        node.checked = el.getAttribute("aria-checked") === "true";
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
        node.labels.inner = innerTitle(groupBox(el));
        // The title of the box holding this one check box alone (a consent paragraph above its "I agree").
        const own = el.closest("fieldset,[role=group]");
        node.own_title = own && own.querySelectorAll(CONTROLS).length === 1 ? innerTitle(own) : "";
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
    for (const [box, buttons] of toggles) {
      controls.push({
        order: order.get(buttons[0]), tag: "button", type: "", role: "togglegroup", id: box.getAttribute("id") || "",
        name: "", labels: labelsOf(box), value: "", required: required(box), section: sectionOf(buttons[0]),
        path: pathOf(box),
        options: buttons.map((b) => ({
          text: buttonText(b), value: "", selected: b.getAttribute("aria-pressed") === "true",
        })),
      });
    }

    // Question/value pairs shown as text. Text inside a field (its parent, or its group, holds a control) is that
    // field's title or description, never a question of its own.
    const pairs = [];
    const used = new Set();
    const inField = (el) => {
      const p = el.parentElement;
      if (p && p !== root && p !== document.body && hasControl(p)) return true;
      const g = el.closest(GROUPS);
      return !!g && root.contains(g) && hasControl(g);
    };
    const addPair = (q, valueEls, source, joiner = " ") => {
      if (used.has(q) || !shown(q) || hasControl(q) || valueEls.some(hasControl) || inField(q)) return;
      used.add(q);
      valueEls.forEach((v) => used.add(v));
      pairs.push({
        order: order.get(q), question: textOf(q), value: valueEls.map(textOf).filter(Boolean).join(joiner), source,
        section: sectionOf(q), path: valueEls.length === 1 ? pathOf(valueEls[0]) : pathOf(valueEls[0].parentElement),
      });
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
      if (!leaf(el)) continue;
      const v = el.nextElementSibling;
      if (!v || used.has(v)) continue;
      const t = textOf(el).replace(/\s*\*+$/, "");  // a required mark does not end the question
      const orphanLabel = el.tagName === "LABEL" && !el.control;
      if (!t || t.length > 200 || !(/[:?：？]$/.test(t) || orphanLabel)) continue;
      const vt = textOf(v);
      if (vt.length > 500 || /[:：]$/.test(vt) || /^\*+$/.test(vt)) continue;  // the required mark is not a value
      addPair(el, [v], "label");
    }
    // "One row, two parts": a block whose only content is a question text and a value, as review pages list them.
    // Only where nothing is left to type or choose (a review page or step): on a form the answers are in its
    // controls, and rows of text there are details about the job. A row counts when a sibling row has the same
    // shape, or when it follows a small heading on its own; a block holding pairs already read is a section.
    const fillable = controls.some((c) => !(c.type === "checkbox" || c.type === "file" || c.role === "checkbox"
      || c.role === "switch"));
    // A question part may sit in blocks of its own (one child each) around its text, as rich-text labels do.
    // Alerts and status lines are notices, never questions.
    const single = (el) => {
      for (let e = el; ; ) {
        if (e.matches('[role=alert],[role=status],[aria-live]')) return false;
        if (leaf(e)) return true;
        const kids = Array.from(e.children).filter(shown);
        if (kids.length !== 1 || ownText(e)) return false;
        e = kids[0];
      }
    };
    const parts = (el) => {
      const kids = Array.from(el.children).filter(shown);
      if (kids.length !== 2 || ownText(el) || hasControl(el)) return null;
      const [q, v] = kids;
      if (NO_TEXT.has(q.tagName.toUpperCase()) || !single(q)) return null;
      const qt = textOf(q), vt = textOf(v);
      if (!qt || !vt || qt.length > 3000 || vt.length > 1000 || /[:：]$/.test(vt)) return null;
      return [q, v, qt, vt];
    };
    const headed = (el) => {
      let box = el;  // a row wrapped in boxes of its own counts as the outermost of them
      while (box.parentElement && box.parentElement !== root && box.parentElement.children.length === 1) {
        box = box.parentElement;
      }
      const prev = box.previousElementSibling;
      const first = prev && prev.firstElementChild;
      return !!prev && (prev.matches(HEADINGS) || (!!first && first.matches(HEADINGS)));
    };
    const rows = fillable ? [] : Array.from(root.querySelectorAll("div,li,section"))
      .map((el) => [el, parts(el)]).filter(([, row]) => row);
    const rowEls = new Set(rows.map(([el]) => el));
    for (const [el, [q, v, qt, vt]] of rows.reverse()) {  // inner rows first: a block holding them is a section
      if (Array.from(v.querySelectorAll("*")).some((x) => used.has(x))) continue;
      const siblings = Array.from(el.parentElement ? el.parentElement.children : []);
      const listed = siblings.some((s) => s !== el && rowEls.has(s));
      if (listed || (headed(el) && qt.length <= 120 && vt.length <= 300)) addPair(q, [v], "row");
    }

    // A heading over a list of items (a review page's picked skills): the items are its value.
    for (const list of fillable ? [] : root.querySelectorAll("ul,ol")) {
      const h = list.previousElementSibling;
      if (!h || !h.matches(HEADINGS) || !shown(list) || hasControl(list)) continue;
      // Only inside a group the heading titles: a job description's headings over bullet lists are not answers.
      if (!list.parentElement || !list.parentElement.matches("[role=group],fieldset,[aria-labelledby]")) continue;
      const items = Array.from(list.children).filter((li) => li.tagName === "LI" && shown(li));
      if (!items.length || items.some((li) => li.querySelector("ul,ol"))) continue;
      addPair(h, items, "list", "; ");
    }

    const actions = [];
    for (const b of root.querySelectorAll(ACTIONS)) {
      const t = shown(b) ? buttonText(b) : "";
      if (t && actions.length < 100) actions.push(t);
    }
    return { controls, pairs, actions, headings: heads.slice(0, 40).map(([, t]) => t) };
  };

  const modal = () => {
    let found = [];
    try { found = Array.from(document.querySelectorAll(":modal")); } catch (e) { found = []; }
    if (!found.length) found = Array.from(document.querySelectorAll('dialog[open],[aria-modal="true"]'));
    found = found.filter((d) => shown(d) && !d.closest("[inert]"));
    return found.length ? found[found.length - 1] : null;
  };
  const page = document.body || document.documentElement;
  const dialog = modal();
  let scope = "page", out = null;
  if (dialog) {
    out = read(dialog);
    if (out.controls.length || out.pairs.length) scope = "dialog";
  }
  if (scope === "page") out = read(page);
  const notices = [];
  for (const n of page.querySelectorAll(NOTICES)) {
    const t = shown(n) ? textOf(n) : "";
    if (t && notices.length < 100) notices.push(t.slice(0, 300));
  }
  const current = Array.from(page.querySelectorAll('[aria-current="step"]')).find(shown);
  const step = current ? textOf(current).slice(0, 120) : "";
  return {
    url: location.href, scope, controls: out.controls, pairs: out.pairs, actions: out.actions, notices, step,
    headings: out.headings,
  };
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

# Runs in the main frame: the page as simplified HTML (no scripts, styles or templates), written from the live DOM
# without changing it. The elements at the given paths (fields matched to a contact entry) have their text and
# value-like attributes written as HIDDEN, so a contact value masking by value cannot see never reaches the file.
SERIALIZE_JS = r"""([paths, hidden]) => {
  const hide = new Set();
  for (const p of paths) { try { const e = document.querySelector(p); if (e) hide.add(e); } catch (err) {} }
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "LINK"]);
  const VOID = new Set(["AREA", "BASE", "BR", "COL", "EMBED", "HR", "IMG", "INPUT", "META", "SOURCE", "TRACK", "WBR"]);
  const VALUED = new Set(["value", "title", "aria-label", "aria-valuetext", "aria-valuenow", "placeholder", "alt"]);
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const out = [];
  const walk = (node, masked) => {
    if (node.nodeType === 3) { out.push(masked ? "" : esc(node.data)); return; }
    if (node.nodeType !== 1 || SKIP.has(node.tagName)) return;
    const own = masked || hide.has(node);
    const tag = node.tagName.toLowerCase();
    out.push("<" + tag);
    for (const a of Array.from(node.attributes)) {
      const v = own && VALUED.has(a.name.toLowerCase()) ? hidden : a.value;
      out.push(" " + a.name + '="' + esc(v).replace(/"/g, "&quot;") + '"');
    }
    out.push(">");
    if (own && !masked && !VOID.has(node.tagName)) out.push(esc(hidden));
    for (const c of Array.from(node.childNodes)) walk(c, own);
    if (!VOID.has(node.tagName)) out.push("</" + tag + ">");
  };
  walk(document.documentElement, false);
  return "<!doctype html>" + out.join("");
}"""

QUESTION_SOURCES = ("label_for", "labelledby", "aria_label", "wrapping", "legend", "preceding", "placeholder")
# A date entered in parts (spin buttons labelled Month and Year): the parts of one fieldset are one field, titled by
# its legend ("From", "To"), its value the parts in page order ("6/2026").
DATE_PARTS = frozenset({"month", "day", "year", "mm", "dd", "yyyy", "yy"})
GROUP_SOURCES = ("group_labelledby", "group_aria_label", "legend", "inner", "preceding")

# Check 4 (slice-1 plan, section 2): buttons that still send the form or move it on, and words saying it was received.
SUBMIT_WORDS = re.compile(r"\bsubmit\b|send application|finish application|complete application", re.I)
NEXT_WORDS = re.compile(r"^(next|continue|review|save and continue|save & continue)\b", re.I)
RECEIVED_WORDS = re.compile(
    r"thanks? (you )?for (applying|your application)|application (has been |was )?(submitted|received|sent)"
    r"|we('ve| have) received your application|you('ve| have) (successfully )?applied|successfully (submitted|applied)",
    re.I,
)


# Page chrome that is not a field (decision 23): widget text and block titles that read as a label when empty.
WIDGET_TEXT = frozenset({"application", "items selected", "or", "options expanded", "options collapsed"})
SECTION_TOGGLE = re.compile(
    r"^(work (history|experience)|education|websites?|languages?|certifications?)( optional)? \d+$"
)


def is_page_chrome(question: str, value: str, required: bool = False) -> bool:
    """A step bar ("completed step 1 of 6"), or empty widget text or a block title ("items selected", "or", "Work
    History (Optional) 1") that is not required. Widget text with a value is a field: "Options Expanded" holding the
    skills picked."""
    q = norm(question)
    if NAVIGATION.match(q):
        return True
    if required or not is_empty(value):
        return False
    return q in WIDGET_TEXT or bool(SECTION_TOGGLE.match(q))


class Redactor(Protocol):
    def mask(self, text: str) -> str: ...

    def mask_json(self, obj): ...


class ReadbackError(RuntimeError):
    """The final page could not be found or read."""


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _question(text) -> tuple[str, bool]:
    """Cleaned question text, and whether it carried a required asterisk (before or after it). Markdown bold left
    as text (**Gender**) is unwrapped."""
    q = re.sub(r"\*\*(.+?)\*\*", r"\1", _clean(text))
    starred = False
    while q and q[-1] in ":：*":
        starred = starred or q[-1] == "*"
        q = q[:-1].rstrip()
    while q and q[0] == "*":
        starred = True
        q = q[1:].lstrip()
    return q, starred


def _pick(sources: dict, order: tuple[str, ...]) -> tuple[str, bool]:
    for key in order:
        q, starred = _question(sources.get(key))
        if q:
            return q, starred
    return "", False


def _control_kind(node: dict) -> str:
    tag, role, typ = node.get("tag", ""), node.get("role", ""), node.get("type", "")
    if node.get("listbox_button"):  # a button that opens a list of options and shows the one picked
        return "select"
    if role == "combobox":
        return "combobox"
    if role in ("radiogroup", "togglegroup"):
        return "radio"
    if role in ("checkbox", "switch"):
        return "checkbox"
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
    labels = node.get("labels") or {}
    question, starred = _pick(labels, QUESTION_SOURCES)
    texts, chosen = _options(node)
    if kind == "checkbox":
        value = "Yes" if node.get("checked") else ""
    elif kind == "radio" and node.get("role") not in ("radiogroup", "togglegroup"):
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
        "required": bool(node.get("required")) or starred or bool(labels.get("star")),
        "section": _clean(node.get("section")),
        "group_title": _question(labels.get("legend"))[0],
        "path": str(node.get("path") or ""),
    }


PART_EMPTY = "?"  # an empty part of a date entered in parts ("?/2024": the month is empty)


def _merge_dates(drafts: list[dict]) -> list[dict]:
    """Month / Day / Year parts in one fieldset -> one "date" field named by the legend (DATE_PARTS), its parts in
    page order. A part left empty while another is filled shows as PART_EMPTY: the date is not complete."""
    out: list[dict] = []
    for d in drafts:
        title = d.pop("group_title", "")
        if not (title and d["control"] == "text" and d["question"].strip().lower() in DATE_PARTS):
            out.append(d)
            continue
        prev = out[-1] if out else None
        if prev is not None and prev.get("_date") == title:
            prev["_parts"].append(d["value"])
            prev["required"] = prev["required"] or d["required"]
            continue
        out.append({**d, "question": title, "control": "date", "_date": title, "_parts": [d["value"]]})
    for d in out:
        d.pop("_date", None)
        parts = d.pop("_parts", None)
        if parts is not None:
            d["value"] = "/".join(v or PART_EMPTY for v in parts) if any(parts) else ""
    return out


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
        "required": any(m.get("required") or (m.get("labels") or {}).get("star") for m in members) or starred,
        "section": _clean(first.get("section")),
        "path": str(first.get("path") or ""),
    }


def _split_checkbox(node: dict) -> dict:
    """One of several check boxes sharing a name and a label: its own box's title is its question, when the box
    holds it alone (two consents, each "I agree"); otherwise its label is (one per work segment)."""
    draft = _single(node)
    title, _ = _question(node.get("own_title"))
    if title and title != _clean(node.get("option_label")):
        draft["question"] = title
    return draft


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
        if kind in ("radio", "checkbox") and node.get("tag") == "input" and key:
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
    out = []
    for item in items:  # check boxes sharing a name and a label are one field repeated per segment, not a group
        _, what, payload = item
        if what == "group" and len(payload) > 1 and _control_kind(payload[0]) == "checkbox":
            if len({_clean(m.get("option_label")) for m in payload}) == 1:
                out.extend(((fi, m.get("order", 0)), "split", m) for m in payload)
                continue
        out.append(item)
    return out


def _lone_checkbox(members: list[dict]) -> bool:
    """A single checkbox is read on its own (checked = "Yes"), unless its group box has a title of its own: then the
    title is the question and the box's label the option (a consent paragraph with an "I agree" box)."""
    if len(members) != 1 or _control_kind(members[0]) != "checkbox":
        return False
    inner, _ = _question((members[0].get("labels") or {}).get("inner"))
    return not inner or inner == _clean(members[0].get("option_label"))


def normalize(raw: dict, sections: bool = False) -> tuple[ReadField, ...]:
    """Raw nodes from EXTRACT_JS (one entry per frame) -> ReadFields numbered p1.1, p1.2 ... in page order.
    `sections`: also keep each field's section and count the same question in the same section (the recorder's
    identity, spec 4.3); without it the fields are slice 1's."""
    return _read_fields(raw, sections)[0]


def _read_fields(raw: dict, sections: bool) -> tuple[tuple[ReadField, ...], tuple[str, ...], tuple[int, ...]]:
    """normalize's fields, with each one's element path and frame."""
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
    for (fi, _), what, payload in items:
        if what == "group":
            drafts.append(_single(payload[0]) if _lone_checkbox(payload) else _group(payload))
        elif what == "control":
            drafts.append(_single(payload))
        elif what == "split":
            drafts.append(_split_checkbox(payload))
        else:
            question, _ = _question(payload.get("question"))
            value = _clean(payload.get("value"))
            if question and value:
                drafts.append(
                    {
                        "question": question,
                        "control": "review",
                        "value": value,
                        "options": (),
                        "required": False,
                        "section": _clean(payload.get("section")),
                        "path": str(payload.get("path") or ""),
                    }
                )
        if drafts and "frame" not in drafts[-1]:
            drafts[-1]["frame"] = fi

    drafts = _merge_dates(drafts)
    drafts = [d for d in drafts if not is_page_chrome(d["question"], d["value"], d["required"])]
    seen = {(d["question"].lower(), d["value"].lower()) for d in drafts if d["control"] != "review"}
    kept = [d for d in drafts if d["control"] != "review" or (d["question"].lower(), d["value"].lower()) not in seen]
    counts: dict[tuple, int] = {}
    paths, frames = [], []
    for d in kept:
        paths.append(d.pop("path", ""))
        frames.append(d.pop("frame", 0))
        section = d.pop("section", "")
        origin = "review_text" if d["control"] == "review" else "control"
        if sections:
            key = (origin, norm(section), norm(d["question"]))
            counts[key] = counts.get(key, 0) + 1
            d.update(section=section, occurrence=counts[key])
        d["origin"] = origin
    fields = tuple(ReadField(id=f"p1.{n}", **d) for n, d in enumerate(kept, start=1))
    return fields, tuple(paths), tuple(frames)


def snapshot(raw: dict, tab: str, read_at: str) -> Snapshot:
    """One read of a tab for the recorder: the fields with their sections, the page's title (the current step when
    the page shows one, else every heading read, in order) and whether a password field was shown."""
    fields, paths, field_frames = _read_fields(raw, sections=True)
    frames = [f for f in raw.get("frames", []) if isinstance(f, dict) and not f.get("error")]
    step = next((_clean(f.get("step")) for f in frames if _clean(f.get("step"))), "")
    headings = tuple(h for f in frames for h in map(_clean, f.get("headings") or []) if h)
    password = any(
        isinstance(node, dict) and node.get("type") == "password" for f in frames for node in f.get("controls") or []
    )
    return Snapshot(
        tab=tab,
        url=str(raw.get("url") or ""),
        marker=(step,) if step else headings,
        fields=fields,
        read_at=read_at,
        password=password,
        paths=paths,
        frames=field_frames,
    )


def _texts(frame: dict, key: str) -> list[str]:
    texts = frame.get(key) or []
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise InvariantError(f"frame {key} must be a list of texts")
    return [_clean(t) for t in texts]


def page_state(raw: dict) -> PageState:
    """Where the form stands, from EXTRACT_JS's actions and notices: the submit or next buttons still shown, and the
    first heading, alert or status line saying the application was received."""
    if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list):
        raise InvariantError("raw page must be an object with a frames list")
    actions, received = [], ""
    for frame in raw["frames"]:
        if not isinstance(frame, dict):
            raise InvariantError("frame must be an object")
        if frame.get("error"):
            continue
        for text in _texts(frame, "actions"):
            if (SUBMIT_WORDS.search(text) or NEXT_WORDS.search(text)) and text not in actions:
                actions.append(text)
        for text in _texts(frame, "notices"):
            if not received and RECEIVED_WORDS.search(text):
                received = text[:200]
    return PageState(actions=tuple(actions), received=received)


@dataclass(frozen=True)
class FinalPage:
    """The final page as read back: its fields, where the form stands, and the same read as a snapshot for the
    recorder (slice 2a)."""

    fields: tuple[ReadField, ...]
    state: PageState
    snapshot: Snapshot | None = None


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


BLACK = (
    "background: #000 !important; color: #000 !important; -webkit-text-fill-color: #000 !important;"
    " border-color: #000 !important; text-shadow: none !important; caret-color: transparent !important;"
)


def blackout_css(paths: list[str]) -> str:
    """A stylesheet painting the given elements, their contents and placeholders black. Styling the element itself
    follows it wherever it is drawn: an overlay on the page cannot cover a modal <dialog>, which sits in the
    browser's top layer above everything else."""
    return "\n".join(f"{p}, {p} *, {p}::placeholder {{ {BLACK} }}" for p in paths)


def serialize(page, hide=()) -> str:
    """The page's main frame as simplified HTML, with the fields at `hide` ((frame, path)) hidden (SERIALIZE_JS)."""
    paths = [path for frame, path in hide if frame == 0 and path]
    return page.main_frame.evaluate(SERIALIZE_JS, [paths, HIDDEN_TEXT])


def _masked_screenshot(page, redactor: Redactor, hide=()) -> tuple[bytes | None, str]:
    """A full-page screenshot with every element showing a contact value, and every field at `hide` ((frame, path)
    of the fields matched to a contact entry), painted black for the shot only (Playwright adds the stylesheet for
    the shot and takes it away; the page is left as it is), or None and why when that cannot be done (5.6)."""
    try:
        if any(frame != 0 for frame, _ in hide):
            return None, "子框架里有对到联系方式的栏，涂不到"
        paths = contact_paths(page.main_frame.evaluate(CONTACT_JS), redactor)
        paths += [path for _, path in hide if path and path not in paths]
        for frame in page.frames[1:]:
            if contact_paths(frame.evaluate(CONTACT_JS), redactor):
                return None, "子框架里有联系方式，涂不到"
        if any(page.locator(path).count() != 1 for path in paths):  # the page changed under us
            return None, "页面在截图前变了"
        return page.screenshot(full_page=True, style=blackout_css(paths) or None), ""
    except Exception as exc:  # noqa: BLE001 - no blackout, no screenshot
        return None, type(exc).__name__


def _read_live(port: int, target: dict, redactor: Redactor, sheet) -> tuple:
    from playwright.sync_api import sync_playwright

    # Leaving the context stops the Playwright driver, which drops the CDP connection. browser.close() is never
    # called, so the applying Chrome and its tabs stay as they are.
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = _find_page(browser, target)
        raw = _extract(page)
        snap = snapshot(raw, tab=str(target.get("id", "")), read_at=datetime.now().isoformat(timespec="seconds"))
        masked, hide, _ = mask_snapshot(snap, redactor, sheet)
        return raw, masked, serialize(page, hide), *_masked_screenshot(page, redactor, hide)


def read_final_page(port: int, target: dict, out_dir: Path, redactor: Redactor, sheet=None) -> FinalPage:
    """Read the job tab as it is and save page.html, screenshot.png and readback.json, masked by value and by column
    (`sheet`: the answer sheet, for the fields matched to a contact entry; spec 5.6); contact values in the
    screenshot are painted black. A screenshot that cannot be blacked out is not saved; screenshot-not-saved.txt
    says so. Disconnects without closing the browser or the page."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Playwright's sync API refuses to run inside an asyncio loop (the MCP server's), so it gets its own thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        raw, snap, html, shot, why = pool.submit(_read_live, int(port), target, redactor, sheet or {}).result()
    state = page_state(raw)
    fields = tuple(f.model_copy(update={"id": f"p1.{n}"}) for n, f in enumerate(snap.fields, start=1))
    fields = tuple(f.model_copy(update={"section": "", "occurrence": 1}) for f in fields)
    (out / "page.html").write_text(redactor.mask(simplify_html(html)), encoding="utf-8")
    if shot is None:
        (out / SCREENSHOT_NOTE).write_text(redactor.mask(f"截图未存：打码失败（{why}）\n"), encoding="utf-8")
    else:
        (out / "screenshot.png").write_bytes(shot)
    dumped = [f.model_dump(mode="json") for f in fields]
    (out / "readback.json").write_text(_dump(redactor.mask_json(dumped)), encoding="utf-8")
    return FinalPage(fields, state, snap)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
