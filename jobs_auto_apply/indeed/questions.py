"""Generic discover/fill for Indeed Apply's "employer questions" step.

NOT verified against a real Indeed Apply session — the native "Indeed Apply"
wizard (hosted on smartapply.indeed.com, opened inline or in a popup/iframe from
the job page) sits behind Indeed's login wall, and live DOM inspection was
blocked by a Cloudflare "Verify you are human" checkpoint even on anonymous,
logged-out search pages (see search.py's module docstring). Bypassing that
checkpoint is out of scope for this tool.

Rather than guess Indeed-specific CSS classes from memory (which would be no
more reliable than generic HTML semantics, and would rot the moment Indeed
renames a class), this module discovers questions using plain, standard-HTML
association rules that hold for almost any accessible web form:

  * ``<label for="id">`` -> the input/select/textarea with that id
  * a ``<fieldset>``/``role="radiogroup"`` with a ``<legend>``/``aria-label`` ->
    one radio/checkbox question with each option's own label as an option string
  * an input with no ``<label for>`` falls back to its ``aria-label`` /
    ``placeholder`` / nearest preceding text node

This is intentionally small — no chip-range coercion, no CTC/notice-period
mapping like Hirist/Naukri have accumulated from real observed forms. Add that
kind of Indeed-specific repair logic only after the user's first real
`python main.py run --platform indeed` surfaces concrete field names/labels that
need it (the same way every other site package in this repo grew its quirks).
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

logger = logging.getLogger("job_apply")

Surface = Page | Frame


class QuestionDiscoveryError(RuntimeError):
    """Raised when discover_indeed_questions cannot reliably determine whether the
    current apply step has employer screening questions.

    This is deliberately distinct from "discovery ran and found zero questions"
    (a plain ``[]`` return). The caller in indeed/apply.py must never treat a
    discovery *error* the same as "no questions" — falling through to
    ``_click_continue`` (whose button regex matches ``Submit``) on a swallowed
    error could submit an application with real, unanswered screening questions
    still on the page. See jobs_auto_apply/hirist/questions.py's
    discover_hirist_questions for the established pattern this follows.
    """

_GENERIC_NOISE = re.compile(r"^(loading|please wait)$", re.I)

_DISCOVER_JS = """
() => {
  function clean(s) { return (s || "").replace(/\\s+/g, " ").trim(); }
  function visible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function labelFor(el) {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return clean(lbl.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.innerText.replace(el.value || "", ""));
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const describedBy = el.getAttribute("aria-describedby");
    if (describedBy) {
      const d = document.getElementById(describedBy);
      if (d) return clean(d.innerText);
    }
    return clean(el.placeholder || "");
  }

  const results = [];
  const seen = new Set();

  // Radio / checkbox groups: a fieldset (or role=radiogroup/group) with a legend.
  const groups = document.querySelectorAll(
    'fieldset, [role="radiogroup"], [role="group"]'
  );
  for (const group of groups) {
    if (!visible(group)) continue;
    const legend = group.querySelector("legend") || group.querySelector('[id$="-legend"], [class*="legend" i]');
    let label = legend ? clean(legend.innerText) : clean(group.getAttribute("aria-label") || "");
    if (!label || seen.has(label) || _GENERIC_NOISE_re_test(label)) continue;
    const radios = [...group.querySelectorAll('input[type="radio"]')].filter(visible);
    const checks = [...group.querySelectorAll('input[type="checkbox"]')].filter(visible);
    if (radios.length >= 1) {
      seen.add(label);
      results.push({
        kind: "radio",
        label,
        options: radios.map((r) => labelFor(r)).filter(Boolean),
      });
    } else if (checks.length >= 1) {
      seen.add(label);
      results.push({
        kind: checks.length > 1 ? "checkbox_group" : "checkbox",
        label,
        options: checks.map((c) => labelFor(c)).filter(Boolean),
      });
    }
  }

  // Loose text / number / select inputs not already covered by a group above.
  for (const el of document.querySelectorAll(
    'input[type="text"], input[type="number"], input[type="tel"], input:not([type]), textarea, select'
  )) {
    if (!visible(el)) continue;
    if (el.closest('fieldset, [role="radiogroup"], [role="group"]')) continue;
    const label = labelFor(el);
    if (!label || seen.has(label)) continue;
    seen.add(label);
    const tag = el.tagName.toLowerCase();
    if (tag === "select") {
      results.push({
        kind: "select",
        label,
        options: [...el.options].map((o) => clean(o.textContent)).filter(Boolean),
      });
    } else {
      results.push({
        kind: el.type === "number" ? "number" : "text",
        label,
        placeholder: el.placeholder || "",
      });
    }
  }

  return results;
}
""".replace(
    "_GENERIC_NOISE_re_test(label)", "/^(loading|please wait)$/i.test(label)"
)

_FILL_JS = """
(pairs) => {
  function clean(s) { return (s || "").replace(/\\s+/g, " ").trim(); }
  function visible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function labelFor(el) {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return clean(lbl.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.innerText.replace(el.value || "", ""));
    return clean(el.getAttribute("aria-label") || el.placeholder || "");
  }
  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertText", data: String(value) }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  function optMatches(want, opt) {
    const w = clean(want).toLowerCase();
    const o = clean(opt).toLowerCase();
    if (!o) return false;
    if (o === w || o.includes(w) || w.includes(o)) return true;
    if (o === "yes") return /\\byes\\b/i.test(want) && !/\\bno\\b/i.test(want);
    if (o === "no") return /\\bno\\b/i.test(want);
    return false;
  }

  const results = [];
  for (const { label, answer, kind } of pairs) {
    let filled = false;
    if (kind === "radio" || kind === "checkbox" || kind === "checkbox_group") {
      const selector = kind === "radio" ? 'input[type="radio"]' : 'input[type="checkbox"]';
      const groups = document.querySelectorAll('fieldset, [role="radiogroup"], [role="group"]');
      for (const group of groups) {
        const legend = group.querySelector("legend") || group.querySelector('[id$="-legend"], [class*="legend" i]');
        const gLabel = legend ? clean(legend.innerText) : clean(group.getAttribute("aria-label") || "");
        if (!gLabel || !(gLabel === label || gLabel.includes(label) || label.includes(gLabel))) continue;
        const inputs = [...group.querySelectorAll(selector)].filter(visible);
        const wants = clean(answer).toLowerCase().split(/[,;|]/).map((s) => s.trim()).filter(Boolean);
        for (const input of inputs) {
          const opt = labelFor(input);
          if (wants.some((w) => optMatches(w, opt))) {
            const target = input.closest("label") || input;
            target.click();
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            filled = true;
            if (kind === "radio") break;
          }
        }
        if (filled) break;
      }
    } else if (kind === "select") {
      const selects = [...document.querySelectorAll("select")].filter(visible);
      for (const sel of selects) {
        if (labelFor(sel) !== label) continue;
        const opt = [...sel.options].find((o) => optMatches(answer, o.textContent));
        if (opt) {
          sel.value = opt.value;
          sel.dispatchEvent(new Event("input", { bubbles: true }));
          sel.dispatchEvent(new Event("change", { bubbles: true }));
          filled = true;
        }
        break;
      }
    } else {
      const fields = [...document.querySelectorAll(
        'input[type="text"], input[type="number"], input[type="tel"], input:not([type]), textarea'
      )].filter(visible);
      for (const el of fields) {
        if (labelFor(el) !== label) continue;
        el.focus();
        setNativeValue(el, answer);
        filled = true;
        break;
      }
    }
    results.push({ label, filled });
  }
  return results;
}
"""


async def discover_indeed_questions(surface: Surface) -> list[dict[str, Any]]:
    """Discover employer screening-question fields on the current apply step.

    A returned ``[]`` must always mean "genuinely no questions here" — never
    "discovery failed and we couldn't tell". On the known-transient
    "Execution context was destroyed" error (the surface navigated/re-rendered
    mid-evaluate, e.g. right after clicking Continue), retry once. Any other
    failure is raised as QuestionDiscoveryError so the caller can abort this
    apply step for manual/pending review instead of silently proceeding to
    submit — see QuestionDiscoveryError's docstring.
    """
    try:
        return await surface.evaluate(_DISCOVER_JS)
    except PlaywrightError as exc:
        if "Execution context was destroyed" not in str(exc):
            raise QuestionDiscoveryError(f"Indeed question discovery failed: {exc}") from exc
        logger.debug("Indeed discovery retried after navigation: %s", exc)
        with contextlib.suppress(Exception):
            await surface.wait_for_load_state("domcontentloaded", timeout=2000)
        await surface.wait_for_timeout(250)
    try:
        return await surface.evaluate(_DISCOVER_JS)
    except Exception as exc:
        raise QuestionDiscoveryError(f"Indeed question discovery failed after retry: {exc}") from exc


async def fill_indeed_questions(
    surface: Surface,
    questions: list[dict[str, Any]],
    answers: dict[str, str],
) -> list[str]:
    """Fill discovered fields with resolved answers. Returns labels that failed to fill."""
    pairs = []
    for field in questions:
        label = str(field.get("label", "")).strip()
        answer = answers.get(label, "").strip()
        if not label or not answer:
            continue
        pairs.append({"label": label, "answer": answer, "kind": str(field.get("kind", "text"))})
    if not pairs:
        return []
    try:
        outcomes = await surface.evaluate(_FILL_JS, pairs)
    except Exception as exc:
        logger.debug("Indeed question fill failed: %s", exc)
        return [p["label"] for p in pairs]
    return [str(o["label"]) for o in outcomes if not o.get("filled")]
