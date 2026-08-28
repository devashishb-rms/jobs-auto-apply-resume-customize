from __future__ import annotations

import logging
import re
from typing import Any

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..application_questions import enrich_field_for_llm, infer_field_input_type
from ..page_load import prepare_interactive_page

logger = logging.getLogger("job_apply")

_DIALOG = (
    '[role="dialog"], [class*="modal" i], [class*="Modal"], '
    '[class*="apply" i][class*="popup" i], [class*="drawer" i]'
)

_SKIP_LABEL = re.compile(
    r"terms|privacy|cookie|upload your resume|drag and drop|"
    r"similar jobs|users love|find your dream",
    re.I,
)


def _label_for_control(el_info: dict[str, Any]) -> str:
    for key in ("label", "ariaLabel", "placeholder", "name"):
        value = str(el_info.get(key) or "").strip()
        if value and len(value) >= 2 and not _SKIP_LABEL.search(value):
            return value[:200]
    return ""


_DISCOVER_JS = """
(dialogSel) => {
  const roots = [];
  const dialogs = document.querySelectorAll(dialogSel);
  if (dialogs.length) {
    roots.push(dialogs[dialogs.length - 1]);
  } else {
    roots.push(document.body);
  }
  const out = [];
  const seen = new Set();
  for (const root of roots) {
    const controls = root.querySelectorAll(
      'textarea, input[type="text"], input[type="number"], input[type="tel"], ' +
      'input[type="email"], input:not([type]), select, input[type="radio"], input[type="checkbox"]'
    );
    for (const el of controls) {
      if (el.type === 'file' || el.type === 'hidden' || el.disabled) continue;
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;

      let label = '';
      if (el.id) {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) label = (lab.innerText || '').trim();
      }
      if (!label) {
        const wrap = el.closest('label');
        if (wrap) label = (wrap.innerText || '').trim();
      }
      if (!label) {
        const prev = el.closest('div, fieldset, li, section');
        if (prev) {
          const heading = prev.querySelector('label, legend, h3, h4, p, span');
          if (heading) label = (heading.innerText || '').trim().split('\\n')[0];
        }
      }

      const ariaLabel = el.getAttribute('aria-label') || '';
      const placeholder = el.getAttribute('placeholder') || '';
      const name = el.getAttribute('name') || '';
      const kind =
        el.tagName === 'SELECT' ? 'select' :
        el.type === 'radio' ? 'radio' :
        el.type === 'checkbox' ? 'checkbox' :
        el.type === 'number' ? 'number' :
        el.tagName === 'TEXTAREA' ? 'textarea' : 'text';

      let options = [];
      if (kind === 'select') {
        options = [...el.options].map(o => (o.textContent || '').trim()).filter(Boolean);
      } else if (kind === 'radio' || kind === 'checkbox') {
        const groupName = el.name;
        if (groupName) {
          options = [...root.querySelectorAll(`input[type="${el.type}"][name="${CSS.escape(groupName)}")`)]
            .map(inp => {
              const lab = inp.closest('label') || (inp.id && document.querySelector(`label[for="${CSS.escape(inp.id)}"]`));
              return ((lab && lab.innerText) || inp.value || '').trim();
            })
            .filter(Boolean);
        }
      }

      const key = `${kind}|${label}|${name}|${ariaLabel}|${placeholder}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        kind,
        label,
        ariaLabel,
        placeholder,
        name,
        options: options.slice(0, 20),
        value: (el.value || '').trim(),
      });
    }
  }
  return out;
}
"""


async def discover_cutshort_questions(page: Page, *, prepped: bool = False) -> list[dict[str, Any]]:
    if not prepped:
        await prepare_interactive_page(page, fast=True)
    try:
        raw = await page.evaluate(_DISCOVER_JS, _DIALOG)
    except Exception as exc:
        logger.debug("Cutshort question discovery failed: %s", exc)
        return []

    fields: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        label = _label_for_control(item)
        if not label:
            continue
        kind = str(item.get("kind") or "text")
        options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
        field: dict[str, Any] = {
            "label": label,
            "kind": kind,
            "options": options,
            "platform": "cutshort",
        }
        field["input_type"] = infer_field_input_type(field)
        enrich_field_for_llm(field)
        fields.append(field)
    return fields


async def fill_cutshort_questions(
    page: Page,
    questions: list[dict[str, Any]],
    answers: dict[str, str],
) -> list[str]:
    """Fill discovered fields. Returns labels that could not be filled."""
    unfilled: list[str] = []
    scope = page.locator(_DIALOG).last if await page.locator(_DIALOG).count() > 0 else page

    for field in questions:
        label = str(field.get("label", "")).strip()
        answer = str(answers.get(label, "")).strip()
        if not label or not answer:
            if label and not answer:
                unfilled.append(label)
            continue
        kind = str(field.get("kind", "text"))
        ok = False
        try:
            if kind in ("radio", "checkbox"):
                opt = scope.get_by_label(re.compile(re.escape(answer[:40]), re.I))
                if await opt.count() == 0:
                    opt = scope.get_by_text(re.compile(rf"^{re.escape(answer)}$", re.I))
                if await opt.count() > 0:
                    await opt.first.click(timeout=3000)
                    ok = True
            elif kind == "select":
                sel = scope.locator("select").first
                if await sel.count() > 0:
                    try:
                        await sel.select_option(label=answer)
                        ok = True
                    except Exception:
                        await sel.select_option(value=answer)
                        ok = True
            else:
                # Prefer control associated with the label text.
                control = scope.get_by_label(re.compile(re.escape(label[:50]), re.I))
                if await control.count() == 0:
                    control = scope.locator("textarea, input[type='text'], input[type='number'], input:not([type])")
                if await control.count() > 0:
                    target = control.first
                    await target.click(timeout=3000)
                    await target.fill(answer)
                    ok = True
        except PlaywrightTimeout:
            ok = False
        except Exception as exc:
            logger.debug("Cutshort fill failed for %r: %s", label[:40], exc)
            ok = False
        if not ok:
            unfilled.append(label)
    return unfilled


async def click_cutshort_advance(page: Page) -> str | None:
    patterns: tuple[tuple[str, str], ...] = (
        (r"submit application", "submit"),
        (r"^submit$", "submit"),
        (r"^apply$", "apply"),
        (r"apply now", "apply"),
        (r"^continue$", "continue"),
        (r"^next$", "next"),
        (r"^confirm$", "confirm"),
        (r"^done$", "done"),
        (r"^finish$", "finish"),
    )
    scope = page.locator(_DIALOG).last if await page.locator(_DIALOG).count() > 0 else page
    for pattern, label in patterns:
        btn = scope.get_by_role("button", name=re.compile(pattern, re.I))
        if await btn.count() == 0:
            btn = scope.locator("button, input[type=submit], a[role=button]").filter(
                has_text=re.compile(pattern, re.I)
            )
        count = await btn.count()
        for i in range(count - 1, -1, -1):
            candidate = btn.nth(i)
            try:
                if not await candidate.is_visible():
                    continue
                text = ((await candidate.inner_text()) or "").strip().lower()
                if "upload resume" in text or "candidate login" in text:
                    continue
                await candidate.click(timeout=4000)
                return label
            except PlaywrightTimeout:
                continue
    return None
