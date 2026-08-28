# Implementation prompt — paste this whole file's content into a fresh Claude Code session

You are implementing an enhancement to an existing, working Python codebase at the current
working directory: `jobs-auto-apply-main_v2` (a Playwright-based job auto-apply agent for
Naukri/Hirist/Wellfound/Instahyre/Cutshort/Uplers). Do not assume anything about the codebase
from this prompt alone — the codebase is the ground truth.

## Step 0 — Load context (do this before writing any code)

1. Read `docs/design/resume-tailoring-sda.md` in full. This is the authoritative system design
   for what you're about to build — every module name, interface, tier, and constraint below
   refers back to it. Section references like "§A4.1" in this prompt mean that section of that
   file.
2. Skim the existing codebase structure: `jobs_auto_apply/config.py`, `jobs_auto_apply/cli.py`,
   `jobs_auto_apply/apply_runner.py`, `jobs_auto_apply/browser.py`, one full site package (e.g.
   `jobs_auto_apply/naukri/`), and `jobs_auto_apply/answers/` (the existing five-stage answer
   engine — your tailoring engine's tier structure in §A4.1 deliberately mirrors this proven
   pattern; read it as a template for how "cheap deterministic first, LLM last resort, never
   fabricate" is already implemented in this codebase, so you're extending an established
   convention, not inventing a new one).
3. Confirm the local Ollama setup already works: `ollama list` should show `job-answers` and
   `job-verify` models. The tailoring engine's Tier 2 reuses these — do not add a new model or
   a cloud LLM dependency anywhere in this feature. If Ollama isn't set up, tell the user and
   stop rather than silently falling back to a cloud API.

## Non-negotiable constraints (violating any of these is a failed implementation)

- **Zero cloud LLM calls anywhere in this feature.** Every generative step uses the local Ollama
  models already configured. If you find yourself wanting a bigger/smarter model for quality,
  the answer is a better local model or a better prompt, not a cloud API call — this is a hard
  cost/architecture requirement from the SDA (§A4.4), not a soft preference.
- **Fail-closed fact-checking on every generated résumé claim.** No skill, employer, title, date,
  or numeric claim may appear in generated content unless it's verifiably present in
  `profile/resume_facts.yaml` / `profile/application_facts.yaml`. Build the mechanical check
  described in §A4.3 — do not rely on prompting alone.
- **Never block an apply on a tailoring failure.** If tailoring or validation fails after the
  bounded repair attempts in §A4.2, fall back to the existing static `resume.pdf` and log a
  `tailoring_degraded` event. A slightly-less-optimized résumé beats a missed application.
- **Zero blind retries against human-only blocking events** (CAPTCHA, OTP email, Google 2FA
  push). This is §A5.3's core rule and the most safety-critical constraint in this whole
  feature — implement it exactly as specified, don't "simplify" it into a generic retry loop.
- **Ship everything behind feature flags, defaulting OFF**, per §A13's phased rollout:
  `application.resume_tailoring_enabled: false` and `application.adaptive_retry_enabled: false`
  in config. The existing v2 behavior (static résumé, flat retry) must remain fully intact and
  be the default until the user explicitly reviews and flips these on.
- **No new hand-duplicated config file.** If you need new config keys, add them once via
  §A10's layered `config/base.yaml` + `config/overlays/*.yaml` pattern — do not create a fourth
  parallel copy of the existing three-file config sprawl documented in Annexure §B1. If
  implementing full config layering is too large a detour, at minimum add the new keys to the
  existing `AppConfig` dataclasses in `config.py` (following its current pattern exactly) and
  flag config layering as a follow-up in your final report — do not silently skip it without
  saying so.
- **Every new third-party dependency goes into `requirements.txt` in the same commit that
  introduces the import**, and into `pyproject.toml`'s core `dependencies` if it's a runtime
  (not dev-only) dependency. Annexure §B6 documents a real crash this exact mistake caused
  earlier in this project — do not repeat it. (Likely new deps: a PDF renderer such as
  `weasyprint` or `reportlab` for §A4.3's `render.py`. `sqlite3` is stdlib, not a new
  dependency.)

## Build order (work through these phases sequentially; verify each before moving on)

### Phase 1 — Platform Adapter contract (§A6, §B4) — pure refactor, zero behavior change

1. Create `jobs_auto_apply/platform_adapter.py` with a `runtime_checkable Protocol` named
   `PlatformAdapter` per §A6 (`collect_listings`, `apply`, `verify_session`,
   `detect_blocking_event`).
2. For each of the six existing site packages, add a thin adapter object/class satisfying this
   Protocol, wrapping the *existing* functions (`apply_batch`, `collect_job_listings`, etc.) —
   do not rewrite the underlying Playwright DOM logic, which already works and was validated
   during this project's own Docker/AWS deployment.
3. `detect_blocking_event()` is the one genuinely new per-adapter method. Start with a generic,
   shared implementation (checks the current page for common challenge indicators: text
   patterns like "captcha", "verify it's you", "unusual activity"; presence of a recaptcha/
   hcaptcha iframe; HTTP 429 status seen recently) usable by all six adapters as a sane default,
   then add a Wellfound-specific refinement per Annexure §B5 (treat "session verified but 0
   listings found on a normally-populated search" as a blocking-event signal — this is a real,
   previously-observed failure mode, not speculative).
4. Register all six adapters in a registry in `platform_adapter.py`. Add a startup-time
   assertion (fail fast, not mid-run) that every enabled platform's adapter actually satisfies
   the Protocol.
5. **Verify**: existing CLI commands (`login`, `verify`, `run --platform naukri` in dry-run
   mode) still behave identically to before this phase. This phase must not change observable
   behavior at all — it only formalizes existing seams.

### Phase 2 — Resilience engine (§A5, §B2) — feature-flagged, additive

1. Create the `jobs_auto_apply/resilience/` package: `signals.py`, `policy.py`,
   `circuit_breaker.py`, per §A3's module list.
2. `policy.py`'s `should_proceed(event, signals) -> Decision` must be a **pure function** (no
   I/O, no wall-clock reads inside it — signals are computed by `signals.py` and passed in) so
   it's trivially unit-testable. Implement the exact decision table in §A5.3.
3. `signals.py` collects the five signals in §A5.2's table. For `human_recent_activity_seconds`,
   use the simplest reliable source available in this deployment context: last-modified
   timestamp of a heartbeat file touched by an active RDP/SSH session, or last Telegram reply
   timestamp from `pending_questions.py`'s existing history — pick whichever is simpler to wire
   up correctly and say which you chose and why in your final report.
4. `circuit_breaker.py` formalizes the existing informal pattern (rolling failure rate per
   platform) — check whether `apply_runner.py`/`technical_failures.py` already track something
   close to this before building it from scratch; reuse what exists.
5. Wire this into `apply_runner.py` **behind `application.adaptive_retry_enabled`** — when
   false (default), existing flat-retry behavior is unchanged; when true, blocking events
   detected via Phase 1's `detect_blocking_event()` route through `should_proceed()` instead of
   the flat retry count.
6. **Verify**: with the flag off, run a dry-run pass and confirm behavior is byte-for-byte
   identical to pre-Phase-2. With the flag on, write unit tests for `policy.py` covering every
   branch in §A5.3's decision table (this is cheap — it's a pure function, no mocking needed
   beyond constructing signal dataclasses).

### Phase 3 — Resume Tailoring Subsystem (§A4, §A8) — feature-flagged, shadow mode first

1. Create the `jobs_auto_apply/tailoring/` package per §A3: `jd_extractor.py`,
   `variant_store.py`, `engine.py`, `ats_validator.py`, `render.py`, `clarify.py`.
2. `jd_extractor.py`: implement per §A4.2 step 2 — cheap keyword/frequency extraction plus one
   short local-LLM call (JSON output, small token budget) for seniority/tone classification.
   Reuse `jobs_auto_apply/llm_answers.py`'s existing Ollama-calling pattern rather than writing
   a new one.
3. `variant_store.py`: SQLite schema exactly as specified in §A8, using the same
   `sentence-transformers/all-MiniLM-L6-v2` embeddings model v2 already loads in
   `embeddings.py` for answer-memory (do not add a second embeddings model).
4. `engine.py`: implement Tiers 1–3 exactly as specified in §A4.1. Tier 2's LLM prompt must
   include the same "never invent employers/PAN/UAN/credentials"-style constraint already
   present in `ollama/Modelfile.job-answers` — write a parallel constraint for résumé content
   (never invent skills/employers/metrics not in the fact files) and consider whether a new
   Ollama Modelfile (e.g. `Modelfile.resume-tailor`) with this baked-in system prompt is
   warranted, following the existing `job-answers`/`job-verify` pattern, or whether reusing
   `job-answers` with a different runtime prompt is sufficient — your call, document the
   reasoning.
5. `ats_validator.py`: implement every check in §A4.3's table, including the fail-closed
   fact-check against `resume_facts.yaml`/`application_facts.yaml`.
6. `render.py`: pick and integrate a PDF renderer (add to `requirements.txt` per the
   constraints above). Single-column, standard headers, exactly-2-pages logic per §A4.3.
7. `clarify.py`: reuse `pending_questions.py` and the existing Telegram plumbing — do not build
   a second notification mechanism. Cap at 2 questions per résumé, bounded timeout, safe
   default (omit the claim) on timeout, exactly as §A4.1 Tier 3 specifies.
8. Wire the whole subsystem into the apply flow **in shadow mode first**: behind
   `application.resume_tailoring_enabled: true` + a separate `application.resume_tailoring_shadow_mode: true`
   (default true when tailoring is enabled) — generates and validates a tailored résumé, logs
   what would have been uploaded and its ATS-validation result, but still uploads the existing
   static `resume.pdf`. This lets the user review variant quality risk-free, exactly per §A13
   Phase 2, before a second flag (`resume_tailoring_shadow_mode: false`) actually swaps the
   uploaded file.
9. **Verify**: run `python main.py run --platform hirist --verbose` in dry-run mode with
   tailoring enabled+shadow-mode on. Confirm: variants get created in
   `data/resume_variants.db`, PDFs land in `data/resume_variants/`, no fabricated claims appear
   (spot-check a few generated PDFs by hand against `resume_facts.yaml`), and the static résumé
   is still what's actually referenced for upload (shadow mode = no behavior change to the real
   upload path yet).

### Phase 4 — Cost ledger (§A7, §B3) — small, do this alongside Phase 3

1. Create `jobs_auto_apply/cost_ledger.py`: one SQLite table, one write per LLM call (in
   `jd_extractor.py`, `engine.py`'s Tier 2, and optionally instrument the existing answer
   engine's LLM calls too, since the ledger's value is system-wide observability per §A7 —
   your call whether to retrofit the existing answer engine's calls in this pass or leave that
   as a documented follow-up).
2. Add a `python main.py cost-report` CLI command (or extend an existing command) that prints
   a summary: total LLM calls, estimated compute time, by module — matching the target metrics
   table in §A7.

### Phase 5 — Config wiring, tests, documentation

1. Add every new config key (`resume_tailoring_enabled`, `resume_tailoring_shadow_mode`,
   `adaptive_retry_enabled`, `reuse_threshold`, `resume_clarify_timeout_seconds`, etc.) to
   `AppConfig` in `config.py`, following its existing dataclass pattern, with sensible defaults
   matching the "OFF by default" constraint above.
2. Regenerate `config.schema.json` via the existing `scripts/generate_config_schema.py` (same
   pattern already established in this project).
3. Update `config.example.yaml` with the new keys, documented with the same comment style
   already used throughout that file.
4. Write unit tests for: `resilience/policy.py` (every decision branch, pure function, no
   mocking needed), `tailoring/ats_validator.py` (feed it known-good and known-bad résumé
   content, assert correct pass/fail and correct fabrication detection), and
   `tailoring/variant_store.py` (similarity matching returns expected hits/misses at the
   threshold boundary).
5. Add a short section to `README.md` documenting the new feature flags and what they do,
   matching the README's existing documentation style and depth for other features.

## Definition of done

- All five phases complete, each independently verified as described above.
- `python -c "from jobs_auto_apply.config import load_config; ..."` still loads cleanly with
  both the default config (new flags off) and a test config with the new flags on.
- A full dry-run (`python main.py run --platform all --verbose`, `dry_run: true`) completes
  without crashing, with both flags off (must match pre-change behavior) and with both flags
  on in shadow mode (must produce variant/ledger artifacts without changing the actual applied
  résumé).
- No cloud LLM calls anywhere — grep the diff for any new `openai`/`anthropic`/other cloud SDK
  import or API call and confirm there are none.
- `requirements.txt` and `pyproject.toml` are consistent with every new import (re-run the
  exact check that caught Annexure §B6's bug: fresh venv, `pip install -r requirements.txt`
  only, confirm `python -c "import jobs_auto_apply.cli"` succeeds with no dev-extras installed).
- Final report to the user: what was built, what was deliberately deferred (e.g. if full config
  layering from §A10 was descoped, say so explicitly), what the user needs to do next (review
  shadow-mode output, decide when to flip `resume_tailoring_shadow_mode: false`), and any
  judgment calls you made where the SDA left room for interpretation (e.g. the human-activity
  signal source in Phase 2 step 3, or the Modelfile decision in Phase 3 step 4).

## Tone / working style

Work through the phases autonomously and sequentially — don't stop to ask permission between
phases unless you hit a genuine ambiguity the SDA doesn't resolve (in which case, make the most
sensible call consistent with the SDA's stated principles in §A1/§A9, document your reasoning,
and continue — don't block on it). Prioritize correctness and adherence to the non-negotiable
constraints above over speed, but do work efficiently: this codebase already has strong
existing patterns (the five-stage answer engine, the Chrome-profile session-reuse browser
layer, the config dataclass system) — extend them, don't reinvent them.
