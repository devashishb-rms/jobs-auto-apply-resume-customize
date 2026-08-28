# System Design & Architecture — JD-Aware Resume Tailoring & Adaptive Apply Engine
### jobs_auto_apply v3 — Enhanced Architecture Specification

**Status**: Proposed. **Audience**: implementer (Claude Code, a fresh session), and the system owner (Devashish Bhattacharjee) for review.
**Companion documents**: `IMPLEMENTATION_PROMPT.md` (same directory) is the executable build prompt derived from this spec. The published HTML version of this document (with rendered diagrams) is the human-readable companion — this file is the token-cheap, agent-readable source of truth; treat it as authoritative over the HTML if the two ever diverge.

---

## PART A — SYSTEM DESIGN & ARCHITECTURE (target depth: ~20 pages)

## A0. Executive Summary

The current `jobs_auto_apply` codebase (v2) searches, filters, scores, and applies to jobs across six India-focused boards using a single static résumé and a five-stage deterministic-first answer engine. It is functionally solid but has one structural gap relevant to this enhancement: **every application uses the same résumé regardless of the job**, and there is no mechanism to adapt the résumé's language to a specific JD's keywords — the single highest-leverage factor in ATS keyword-match scoring and recruiter skim-relevance.

This document specifies **v3**: an additive architecture that inserts a **Resume Tailoring Subsystem** into the existing pipeline, between job-scoring and apply, plus three cross-cutting enhancements the user explicitly required:

1. **A Resume Variant Library** — a content-addressed store of previously generated tailored résumés, keyed by JD-embedding similarity, so near-duplicate JDs (extremely common — dozens of "Solution Architect" postings share 80%+ of their requirements) reuse an existing variant instead of paying LLM cost again. This is the primary cost-control mechanism.
2. **An Adaptive Human-Intervention Signal Engine** — replaces blind fixed-count retries with a decision function that fuses multiple signals (recent human activity, time-of-day, challenge type, historical response latency) to decide, per blocking event, whether to wait-and-poll, notify-and-defer, or abandon-and-log. This directly serves the "don't get blocked, minimize retries, minimize human friction" requirement.
3. **A formal Platform Adapter contract** (Python `Protocol`) that every site package must implement, replacing today's ad hoc per-site function signatures. This is what makes the system genuinely Open/Closed (OCP) — adding a 7th job board becomes "implement one interface," not "read six existing packages and hope you match their undocumented conventions."

The résumé tailoring pipeline is deliberately **not** a naive "call GPT-4 to rewrite the whole résumé for every job." That would be slow (30–90s/job), expensive (~$0.02–0.08/job → $2–8 per 100 applications at cloud-LLM rates, non-trivial at scale), and risky (higher hallucination surface, more chances to introduce an ATS-breaking format or an unverifiable claim). Instead it is a **tiered, mostly-local, mostly-deterministic pipeline** — full design in §A4.

---

## A1. Design Principles (and how they're actually applied here, not just named)

| Principle | Concrete application in this design |
|---|---|
| **SRP** (Single Responsibility) | `jd_extractor.py` only extracts structured requirements from raw JD text. `resume_variant_store.py` only reads/writes the variant library. `tailoring_engine.py` only produces new content. `ats_validator.py` only validates format compliance. None of these modules know about Playwright, HTTP, or any specific job site. |
| **OCP** (Open/Closed) | New job boards implement `PlatformAdapter` (a `Protocol`) — the apply orchestrator, tailoring subsystem, and retry engine never change when a 7th platform is added. New tailoring strategies (e.g. swap the local Ollama model for a cheaper one) implement `TailoringStrategy` — swappable via config, zero changes to callers. |
| **DIP** (Dependency Inversion) | The apply orchestrator depends on the `PlatformAdapter` and `TailoringStrategy` *interfaces*, not concrete implementations. Concrete adapters are wired at startup from config — this is what makes the "microservices principles without microservices overhead" claim in §A3 real rather than aspirational. |
| **DRY** | The Naukri/Hirist/Instahyre/Cutshort chatbot-question-answering logic already suffers some duplication in v2 (each site package half-reimplements field discovery). v3's `ats_validator.py` and `jd_extractor.py` are written once and shared by all six adapters — this is a structural DRY improvement, not just a place to avoid repeating *new* code. |
| **SoC** (Separation of Concerns) | Four concerns that were previously blended inside per-site `apply.py` files are pulled out: *what to say* (tailoring), *whether it's safe to proceed* (human-signal engine), *whether the output is well-formed* (ATS validator), and *how to click through this specific site's DOM* (adapter). A site adapter's `apply()` method becomes almost entirely DOM mechanics — it receives an already-tailored, already-validated résumé and just needs to upload it. |
| **YAGNI over cargo-cult microservices** | The user asked for "microservices kind of architecture/principles" and "independent scalability of modules." This document interprets that as: **loosely coupled modules behind clean interfaces, independently testable and independently swappable, deployed as one process** — not literal separate containers/services communicating over a network. Splitting a single-user, single-VM, browser-automation tool into real network microservices would add latency, operational surface, and *cost* (the opposite of a stated goal) for zero benefit at this scale. This tradeoff is deliberate and documented as an explicitly-avoided anti-pattern in §A9 and Annexure §B1. |

---

## A2. High-Level Architecture

**Diagram 1 — System overview.** (Rendered as an SVG figure in the HTML companion; described here as a labeled flow for token-cheap agent reading.)

```
[Per-site Search/Filter/Score]  (unchanged from v2 — apply_filters.py, role_filter.py, job_selection.py)
        │  JobListing + JD text
        ▼
[JD Extractor]  — structured requirements: must-have skills, nice-to-have skills, seniority signal,
                   ATS keyword list, tone signal (startup/enterprise), ranked by frequency+prominence
        │  StructuredJD
        ▼
[Resume Variant Store: similarity lookup]  — embed StructuredJD, cosine-search against
                   existing variants (same as FAISS pattern already used for answer-memory in v2)
        │
        ├── HIT (similarity ≥ reuse_threshold, default 0.86) ──────────────┐
        │                                                                   │
        └── MISS ──▶ [Tailoring Engine]                                    │
                       │  tiered: rule-based section reorder + keyword      │
                       │  injection FIRST (near-zero cost) → local LLM      │
                       │  (Ollama) polish pass ONLY on sections below a     │
                       │  keyword-match threshold → optional 1-2 clarifying │
                       │  questions via Telegram (batched, timeout-bounded) │
                       ▼                                                    │
                    [ATS Validator]  — 2-page enforcement, format lint,     │
                       keyword-density check, no-fabrication check          │
                       (cross-references claims against resume_facts.yaml)  │
                       │  PASS ──────────────────────────────────────────────┤
                       │  FAIL (max 2 auto-repair loops, then flag+skip)     │
                       ▼                                                    ▼
                    [store new variant]  ─────────────────────▶ [TailoredResume ready]
                                                                          │
                                                                          ▼
                                              [Apply Orchestrator + Circuit Breaker
                                               + Human-Intervention Signal Engine]
                                                                          │
                                                                          ▼
                                              [Platform Adapter.apply()]  (existing
                                               per-site Playwright code, now behind
                                               a formal interface — §A6)
```

**What's new vs. v2, at a glance:**

| Layer | v2 | v3 |
|---|---|---|
| Résumé | One static `resume.pdf`, uploaded unchanged everywhere | JD-tailored variant, reused when similar, generated when novel |
| Retry policy | Flat `apply_retries` count + fixed backoff, identical for every failure type | Signal-fused decision per blocking event (§A5) |
| Platform integration contract | Implicit — each site package's functions happen to be called the same way by `cli.py` | Formal `PlatformAdapter` Protocol (§A6) |
| Cost accounting | None | Explicit token/dollar budget tracked per run and per 100-application milestone (§A7) |
| Config | Three parallel hand-maintained YAML files (`config.yaml`, `config.docker.yaml`, `config.vm.yaml`) — a real problem identified during this project (Annexure §B1) | Layered base+overlay config (§A10) |

---

## A3. Module Decomposition ("microservices principles, monolith deployment")

Each module below is a **Python package with a narrow public interface**, independently unit-testable, independently swappable via a registered implementation of its `Protocol`. They communicate via **in-process function calls with typed dataclasses**, not HTTP/queues — this is the cost/latency-conscious choice explained in §A1.

```
jobs_auto_apply/
  tailoring/
    __init__.py            # public: tailor_resume(job, structured_jd, config) -> TailoredResume
    jd_extractor.py         # SRP: JD text -> StructuredJD
    variant_store.py        # SRP: similarity lookup + persistence (SQLite, see §A8)
    engine.py                # SRP: orchestrates rule-based + LLM tiers (TailoringStrategy Protocol)
    ats_validator.py         # SRP: format/length/keyword/fabrication checks
    render.py                 # SRP: StructuredResume -> ATS-safe PDF (single-column, no tables/text-boxes)
    clarify.py                 # SRP: batches ≤2 clarifying questions, reuses v2's Telegram plumbing
  resilience/
    __init__.py             # public: should_proceed(event: BlockingEvent) -> Decision
    signals.py                # SRP: signal collectors (see §A5.2)
    policy.py                  # SRP: decision function, pure and unit-testable with no I/O
    circuit_breaker.py          # SRP: per-platform failure-rate breaker (existing pattern, formalized)
  platform_adapter.py       # PlatformAdapter Protocol + registry (DIP boundary, §A6)
  cost_ledger.py             # SRP: token/dollar accounting, cheap SQLite append log (§A7)
  config/
    loader.py                 # layered base+overlay resolution (§A10)
```

This is a **modular monolith**, not a distributed system. Each package could be extracted to a real microservice later *without changing its public interface* if a future need genuinely justified it (e.g. running tailoring on a shared GPU box for multiple users) — that is what "independently scalable" means here in a way that's honest about today's single-user, single-VM deployment reality, rather than a service mesh nobody needs yet.

---

## A4. Resume Tailoring Subsystem — Deep Dive

### A4.1 Why tiered, not "always call an LLM"

A cloud LLM full-résumé rewrite costs roughly 1,500–3,000 input tokens (JD + résumé context) + 800–1,200 output tokens per call. At Claude/GPT-4-class pricing that's ~$0.02–0.06 per résumé; at 100 applications/day that's $2–6/day, $60–180/month — for a system whose stated goal is *low cost*. It is also 100% avoidable for the ~60-80% of JDs (observed pattern: senior-architecture postings across Naukri/Hirist cluster heavily around 8-12 recurring requirement templates) that are near-duplicates of one already tailored.

**Tier 0 — Similarity reuse (near-zero cost).** Embed the `StructuredJD` (not raw JD text — the structured, deduplicated requirement list, which produces a tighter, more comparable embedding) using the same `sentence-transformers/all-MiniLM-L6-v2` model v2 already runs locally for answer-memory (§ zero new dependency, zero new cost — it's on the box already). Cosine-search against the Variant Library. Above `reuse_threshold` (default 0.86, tunable), reuse the existing variant verbatim — this is the dominant path in steady state and costs one embedding computation (~5ms CPU, no LLM call at all).

**Tier 1 — Rule-based reorder + keyword injection (near-zero cost, no LLM).** Below the reuse threshold, before touching an LLM: reorder the `core_skills`/bullet emphasis within existing, *already-true* résumé content to surface JD-matching keywords earlier (ATS parsers and human skimmers both weight position), and inject exact-phrase JD keywords into a "Key Skills" line **only when that skill is already present in `resume_facts.yaml`** — this is a deterministic substring/synonym match, not generation, so it cannot fabricate. This alone materially improves ATS keyword-match score for JDs that differ from the base résumé only in phrasing (e.g. "Gen AI" vs "Generative AI" vs "LLM engineering").

**Tier 2 — Local LLM polish (low cost, on-box).** Only for JD requirement clusters *not* satisfiable by Tier 1 (i.e., genuinely novel emphasis — e.g. a JD stressing "P&L ownership" that the résumé states differently), invoke the same local Ollama `job-answers` model v2 already runs for screening-question answers. Prompt is scoped to **one section at a time** (professional summary, or one experience bullet block), never a whole-document rewrite — this bounds both token cost and hallucination surface, and lets the `ats_validator` (§A4.3) check each generated fragment independently before it's assembled. Constraint baked into the system prompt, mirroring v2's existing `Modelfile.job-answers` discipline ("never invent employers/PAN/UAN/credentials"): **never state a skill, employer, metric, or claim not present in `resume_facts.yaml`/`application_facts.yaml`** — this is enforced twice, once as an instruction and once as a post-hoc fact-check pass in the validator (defense in depth, matching v2's existing generator+verifier pattern for answers).

**Tier 3 — Human clarification (rare, ≤2 questions).** If, after Tiers 1–2, the JD contains a requirement the fact base cannot resolve either way (e.g. JD asks for "active security clearance" and no field in `application_facts.yaml` covers it), queue **at most two** batched questions via the existing Telegram plumbing (v2 already has `pending_questions.py` + Telegram Q&A — reused, not rebuilt). Bounded wait (`resume_clarify_timeout_seconds`, default 600s) with a documented default-safe fallback (omit the unresolved claim rather than guess) if no reply arrives — this keeps the apply pipeline from stalling indefinitely on one job, directly serving the "minimize retries / minimize blocking on human" goal from a different angle than §A5 (this is *content* clarification, §A5 is *access/auth* blocking).

### A4.2 Data flow for a single job (Diagram 2 in the HTML companion — sequence diagram)

1. Job passes existing v2 scoring/filter (`job_selection.py`, `apply_filters.py`) — unchanged.
2. `jd_extractor.extract(job.description)` → `StructuredJD{must_have: [...], nice_to_have: [...], keywords: [...], seniority: str, tone: str}`. Implementation: cheap — regex/keyword-frequency + a single short local-LLM call (structured JSON output, ~150 output tokens, same discipline as v2's `job-answers` model) to classify seniority/tone; no cloud call.
3. `variant_store.find_similar(structured_jd, threshold)` → `VariantMatch | None`.
4. If match: `resume = variant.resume` (Tier 0, done — proceed to §A4.3 for a cheap re-validation only, not full re-tailoring).
5. If no match: `engine.tailor(base_resume, structured_jd)` runs Tiers 1→2→(3 if needed), producing `TailoredResume`.
6. `ats_validator.validate(resume)` → `ValidationResult{ok: bool, issues: [...]}`. On failure, up to 2 auto-repair loops (re-run the specific failing check's fix, e.g. trim to 2 pages by dropping lowest-relevance bullet first) before falling back to the base résumé and logging a `tailoring_degraded` event (never block the apply on a tailoring failure — a slightly-less-optimized résumé beats a missed application).
7. `variant_store.save(structured_jd_embedding, resume, metadata)` — new variant persisted for future reuse.
8. `render.to_pdf(resume)` → ATS-safe PDF, written to `data/resume_variants/<variant_id>.pdf`.
9. Apply orchestrator hands this path to the platform adapter's résumé-upload step instead of the static `resume.pdf` path.

### A4.3 ATS-Safety Constraints (enforced mechanically, not just prompted)

| Constraint | Enforcement mechanism |
|---|---|
| Exactly 2 pages | `render.py` measures rendered content height against a fixed single-column template at 11pt; if content overflows, trims lowest-JD-relevance bullets (scored by keyword overlap) until it fits; if content underflows page 2 by >40%, that's flagged (too sparse — reuse more base-résumé detail) rather than padded with filler |
| ATS-parseable format | Single-column layout, standard section headers (`Summary`, `Experience`, `Skills`, `Education` — no creative renaming), no tables/text-boxes/headers-footers-for-body-text/icons, no multi-column skill grids — these are the documented ATS parser failure modes (Workday/Greenhouse/Taleo-class parsers reliably mis-order or drop content from multi-column layouts) |
| No fabrication | Every generated sentence is checked: named skills must appear in `technical_skills` or `core_skills`; named employers/titles/dates must match `experience[]` entries verbatim; numeric claims (percentages, team sizes, budget figures) must match a source bullet in `resume_facts.yaml` — if the tailoring engine produces a claim that fails this check, it's stripped, not left in (fail closed) |
| Keyword coverage | Compute a simple TF-based match score between `StructuredJD.keywords` and the tailored résumé text; target ≥70% must-have keyword coverage; below that, one more Tier-2 pass targeted only at the missing keywords before accepting |
| File format | PDF (universally ATS-safe) generated from structured data (WeasyPrint or a minimal ReportLab template — no dependency on a browser/Playwright for this, keeping résumé rendering decoupled from the automation layer per SoC) |

### A4.4 Cost model for this subsystem specifically

Per 100 applications, assuming realistic clustering (steady-state reuse rate ~65% after the first ~2 weeks of use, per the "senior-architecture JDs cluster heavily" observation):

| Path | Applications | Cost per application | Subtotal |
|---|---|---|---|
| Tier 0 (reuse) | 65 | ~0.005s CPU, $0 | $0 |
| Tier 1 only (rule-based, no LLM) | 20 | ~0.05s CPU, $0 | $0 |
| Tier 2 (local LLM, 1-3 section calls) | 14 | ~2-8s local compute, $0 (Ollama, already-owned hardware) | $0 |
| Tier 3 (human clarify) | 1 | $0 (Telegram message) | $0 |

**Total incremental cost: $0 in LLM API fees** — because the design deliberately keeps every generative step on the already-running local Ollama instance, never calling a cloud model. The only cost is compute time (seconds, on a box that's otherwise idle between apply passes) and disk (`data/resume_variants/` — a few hundred KB per variant, trivially small at any realistic scale). This is the direct architectural answer to the "low cost, minimum LLM tokens" requirement — not a prompting trick, a structural one.

---

## A5. Adaptive Human-Intervention Signal Engine

This is the architectural answer to: *"optimize the number of retries vs. waiting time for human intervention... don't retry again and again where human intervention is necessary... take the right call based on signals."*

### A5.1 The core problem with v2's retry model

v2's `apply_runner.py` has a single knob: `apply_retries` (flat count, default 1) with fixed `retry_backoff_ms`. It does not distinguish *why* an apply attempt failed — a transient DOM timing issue (worth an immediate retry), a CAPTCHA (worth waiting for a human, not retrying blindly — retrying against a CAPTCHA wall is exactly the bot-signature behavior that gets accounts flagged), and a dead session (worth neither — needs a fresh login, which itself needs a human). Treating all three the same is the architectural gap.

### A5.2 Signal collection (Diagram 3 in the HTML companion — decision-tree diagram)

`BlockingEvent{type: CAPTCHA | OTP_EMAIL | GOOGLE_2FA_PUSH | SESSION_DEAD | TRANSIENT_DOM | RATE_LIMITED, platform: str, job_key: str}` is raised by a platform adapter when it detects one of these conditions (detection is adapter-specific — e.g. presence of a `recaptcha` iframe, a "verify it's you" text pattern, an HTTP 429). The signal engine then gathers:

| Signal | Source | What it indicates |
|---|---|---|
| `human_recent_activity_seconds` | Last SSH/RDP session activity on the VM (read from `who`/`last` or a lightweight heartbeat file the RDP session touches), or last reply timestamp to a Telegram prompt | If < 5 min, human is plausibly at the keyboard *right now* — worth a short poll-wait rather than an async notify-and-defer |
| `time_of_day_in_active_hours` | Config-defined active window (default derived from the 11am daily start time + a few hours) | Outside active hours, a human is unlikely to respond soon — skip straight to notify-and-defer or abandon, don't burn a retry budget waiting |
| `historical_reply_latency_p50` | Rolling stat from `cost_ledger`/`pending_questions` history of past Telegram-reply turnaround | If the user historically replies in ~2 minutes, a longer wait budget is justified than if they historically take hours |
| `challenge_type_resolvability` | Static table: `GOOGLE_2FA_PUSH` and `OTP_EMAIL` are inherently human-only (never auto-resolvable, never worth a bot-side retry loop); `TRANSIENT_DOM` is inherently auto-resolvable (never worth bothering a human) | Determines which branch of the decision tree even applies |
| `platform_recent_failure_rate` | Circuit breaker's rolling window (existing pattern, formalized — see `circuit_breaker.py`) | If this platform has failed >N% of attempts in the last M minutes, back off the whole platform regardless of this specific event — this is what actually prevents "bot-like" hammering that gets an account flagged |

### A5.3 Decision policy (pure function, `resilience/policy.py`)

```
should_proceed(event, signals) -> Decision:
    if event.type in {TRANSIENT_DOM, RATE_LIMITED}:
        # inherently bot-resolvable — bounded exponential retry, NOT a human-wait
        return RETRY(max_attempts=2, backoff=exponential(base=2s, cap=30s))

    if event.type in {CAPTCHA, OTP_EMAIL, GOOGLE_2FA_PUSH}:
        # inherently human-only — never blind-retry against these (this IS the
        # bot-detection signature the user is worried about)
        if signals.human_recent_activity_seconds < 300 and signals.time_of_day_in_active_hours:
            return WAIT_AND_POLL(timeout=min(120s, 3 * signals.historical_reply_latency_p50))
        else:
            return NOTIFY_AND_DEFER(channel=telegram, requeue_job=True)
            # job goes to pending_retry.py's existing requeue mechanism (v2 already
            # has this for answered-question jobs — reused, not rebuilt) and is
            # retried on the NEXT scheduled run, not in a tight loop now

    if event.type == SESSION_DEAD:
        # never worth retrying the same run — always needs a fresh login
        return NOTIFY_AND_DEFER(channel=telegram, requeue_job=False)
        # surfaces "re-run `python main.py login --platform X`" — matches v2's
        # existing login flow, no new auth mechanism invented

    return ABANDON_WITH_LOG(reason=event.type)
```

Crucially: **zero blind retries against anything a bot cannot resolve.** This single rule is the direct architectural fix for "don't retry again and again where human intervention is necessary... may lead to the platform blocking you." The circuit breaker (existing pattern, now formalized as its own module) adds a second layer: even *resolvable* failures, if they cluster past a rate threshold on one platform, pause that platform for a cooldown window rather than continuing to hammer it — this is what protects against the platform's own rate-limiting/bot-heuristics regardless of per-event correctness.

### A5.4 Why this minimizes both retries AND human friction simultaneously

The naive approaches fail one side or the other: pure fixed-retry-then-give-up ignores that the human might be instantly available (wastes the opportunity, job gets abandoned unnecessarily); pure always-wait-for-human maximizes friction (pages the user for every transient DOM hiccup). The signal-fusion approach routes each event type to the *cheapest sufinformant channel*: bot-resolvable issues never bother a human; human-only issues never waste bot-side retry budget; and among human-only issues, it further distinguishes "you're probably at the keyboard right now" (short synchronous wait — best case, near-zero perceived friction) from "you're probably not" (async notify, job requeues for the next scheduled run — no dangling wait, no wasted VM runtime, which also directly serves the cost/auto-shutdown goal from the AWS deployment work already done).

---

## A6. Platform Adapter Contract (OCP boundary)

```python
class PlatformAdapter(Protocol):
    name: str
    def collect_listings(self, config: AppConfig, limit: int) -> list[JobListing]: ...
    def apply(self, page: Page, job: JobListing, resume_path: Path, config: AppConfig) -> ApplyResult: ...
    def detect_blocking_event(self, page: Page) -> BlockingEvent | None: ...  # NEW in v3
    def verify_session(self, page: Page) -> bool: ...
```

Every existing v2 site package (`naukri/`, `hirist/`, `wellfound/`, `instahyre/`, `cutshort/`, `uplers/`) is refactored to expose a module-level object satisfying this Protocol (a thin adapter class wrapping the existing `apply_batch`/`collect_job_listings` functions — **no rewrite of the underlying Playwright DOM logic**, which already works; only the *seams* are formalized). `detect_blocking_event` is the one genuinely new per-adapter responsibility, and it's small — a handful of DOM-pattern checks per site, informed by what each site's real challenge pages look like (to be filled in empirically during implementation, with safe generic fallback detection — e.g. any page containing "captcha"/"verify"/"unusual activity" text patterns — for sites not yet specifically tuned).

Adding platform #7 becomes: implement this Protocol, register it in `platform_adapter.py`'s registry, add its `PlatformConfig` dataclass. The apply orchestrator, tailoring subsystem, and resilience engine require zero changes — this is OCP made concrete, not asserted.

---

## A7. Cost & Token Budget Model (system-wide, not just tailoring)

`cost_ledger.py` — an append-only SQLite table (`data/cost_ledger.db`), one row per LLM invocation: `{timestamp, module, model, input_tokens, output_tokens, estimated_cost_usd, job_key}`. Since every generative call in this design runs on local Ollama, `estimated_cost_usd` is structurally $0 for the default configuration — the ledger's real value is **compute-time and call-count observability** (are we calling the LLM more than expected? is one platform's chatbot triggering excessive Tier-2 tailoring calls due to unusually verbose JDs?), which is what actually lets a user *notice* a regression before it becomes a cost or latency problem, and is a foundation that generalizes cleanly if a future config swaps in a cloud model for higher quality (the ledger already tracks `estimated_cost_usd` per-provider, ready for that day without a schema change).

**Full-run 100-application budget (target, to be validated empirically post-implementation):**

| Metric | Target |
|---|---|
| Wall-clock time for 100 applications | ≤ 3 hours (dominated by Playwright DOM interaction + delay_seconds jitter, not by tailoring — Tier 0/1 add near-zero latency, Tier 2 adds 2-8s only for the ~14% of novel JDs) |
| LLM calls per 100 applications | ~15-20 Tier-2 tailoring calls + existing v2 answer-engine calls (unchanged) |
| Cloud LLM cost | $0 (local-only by default) |
| Human interruptions per 100 applications | Target < 5, and only for genuinely human-only events (CAPTCHA/2FA/OTP) — never for content decisions Tier 1-2 can resolve deterministically |

---

## A8. Resume Variant Library — Storage Schema

SQLite (`data/resume_variants.db`) — chosen over the JSON-file pattern v2 uses elsewhere specifically *because* this table needs similarity search over structured metadata and will grow to hundreds of rows; a single growing JSON file (v2's existing pattern for `applied_jobs.json`/`user_memory.json`) is fine for that use case's write pattern but is the wrong tool here (this is a deliberate, judged exception to "match existing patterns," not an accidental inconsistency — noted explicitly per the project's own engineering-judgment norms).

```sql
CREATE TABLE resume_variants (
    variant_id TEXT PRIMARY KEY,          -- content hash of structured_jd + base_resume_version
    structured_jd_json TEXT NOT NULL,     -- the StructuredJD this variant was tailored for
    jd_embedding BLOB NOT NULL,           -- 384-dim MiniLM vector, for cosine similarity search
    resume_pdf_path TEXT NOT NULL,        -- data/resume_variants/<variant_id>.pdf
    ats_keyword_coverage REAL NOT NULL,   -- 0..1, from §A4.3 validation
    base_resume_version TEXT NOT NULL,    -- hash of resume_facts.yaml at generation time (invalidates
                                            -- stale variants automatically if the source résumé changes)
    created_at TEXT NOT NULL,
    reuse_count INTEGER DEFAULT 0,        -- incremented on every Tier-0 hit — surfaces which variants
                                            -- are earning their keep, useful for future pruning
    last_used_at TEXT,
    tags TEXT                              -- e.g. "solution-architect,ai-leadership,fintech" — free-text,
                                            -- surfaced in a future `python main.py resumes` CLI command
);
CREATE INDEX idx_base_resume_version ON resume_variants(base_resume_version);
```

`base_resume_version` invalidation matters: if the user edits `profile/resume_facts.yaml` (new job, new skill), every existing variant becomes stale (built from outdated facts). Rather than silently serving stale content, `variant_store.find_similar()` filters to the *current* `base_resume_version` — old variants remain in the table (audit trail, and cheap to keep) but are never matched, so a résumé edit safely and automatically triggers fresh Tier-1/2 generation on next use, with zero explicit cache-invalidation logic to get wrong.

---

## A9. Anti-Patterns Explicitly Avoided

| Anti-pattern | Why it's tempting here | Why this design avoids it |
|---|---|---|
| **God object** — one `TailoringManager` class doing extraction+matching+generation+validation+rendering | Feels simpler to write once | Split into 5 single-purpose modules (§A3) — each independently testable, each replaceable |
| **Premature microservices** | The prompt explicitly asks for "microservices kind of architecture" | Interpreted as internal modularity (§A1, §A3), not network services — literal microservices would add latency and infra cost against the stated cost goal, for a single-user tool with no independent-team-ownership pressure that microservices actually solve for |
| **Retry storm** | Naive "just retry more on failure" instinct | §A5's signal-fused policy explicitly forbids blind retry against human-only blocking events — this is the single most important anti-pattern this design corrects, because it's also a *safety* issue (account-ban risk), not just an efficiency one |
| **Silent fabrication** | LLM-generated résumé content is the highest-risk surface in this whole feature — an invented metric or skill is worse than no tailoring at all | Fail-closed fact-checking in `ats_validator.py` (§A4.3), mirroring v2's existing "never guess" discipline in the answer engine |
| **Unbounded LLM cost creep** | Easy to reach for "just call the LLM" at every decision point | Tiered pipeline (§A4.1) makes the LLM the *last* resort, not the first, exactly mirroring v2's existing answer-engine waterfall philosophy — this design extends an already-proven pattern rather than inventing a new philosophy |
| **Config sprawl** | This project already has 3 hand-copied YAML variants (identified as a live problem during this engagement — Annexure §B1) | §A10 layered config fixes this structurally rather than adding a 4th variant for tailoring settings |
| **Tight coupling to Playwright inside business logic** | Easiest path: put tailoring calls directly inside each site's `apply.py` | Tailoring subsystem has zero Playwright/browser imports — it operates on `JobListing`/`StructuredJD`/`TailoredResume` dataclasses only, called *from* the adapter, never the reverse (SoC, §A1) |
| **Global mutable state for "is a human currently available"** | Tempting to just check `datetime.now()` inline wherever needed | Centralized in `resilience/signals.py`, one clear ownership boundary, unit-testable without wall-clock mocking gymnastics scattered across call sites |

---

## A10. Layered Configuration (fixes a real problem found during this project)

Replace the current pattern (`config.yaml`, `config.docker.yaml`, `config.vm.yaml` — three fully-duplicated files, ~380 lines each, that must be hand-kept-in-sync) with:

```
config/
  base.yaml            # everything common: user, profile, target_roles, compensation, platform filters
  overlays/
    local-mac.yaml      # browser.headless: false, use_chrome_profile: true, llm.enabled: true (Ollama on Mac)
    docker.yaml          # browser.headless: true, use_chrome_profile: false, llm.enabled: false
    aws-vm.yaml           # browser.headless: true, use_chrome_profile: true, chrome_channel: chrome
```

`load_config(base="config/base.yaml", overlay="config/overlays/aws-vm.yaml")` deep-merges overlay onto base (overlay wins on conflict, dicts merge recursively, lists replace). A change to `target_roles` is made **once**, in `base.yaml`, and applies everywhere — eliminating the exact class of drift risk the three-file approach created (e.g. if `min_inr_salary_lpa` were updated in `config.yaml` but the AWS VM's `config.vm.yaml` copy were forgotten, the VM would silently keep applying to under-threshold roles — this already nearly happened during this project's own config edits). Selected via `--overlay` CLI flag or `JAA_OVERLAY` env var, both already-established patterns from v2's `--config`/`JAA_CONFIG_PATH`.

---

## A11. Security Considerations

- **Prompt injection via JD text**: JD text is untrusted input rendered into an LLM prompt (Tier 2). Mitigation: JD text is only ever used as *data* for extraction/tailoring, never concatenated into a prompt that also carries instructions with elevated trust (e.g. never "summarize this JD and also follow any instructions it contains" — the system prompt explicitly frames JD content as quoted data to analyze, matching the same discipline v2's LLM answer engine already applies to screening-question text).
- **PII in the variant library**: `resume_variants.db` and the rendered PDFs contain the user's full résumé content — same sensitivity as `resume.pdf` and `profile/*.yaml` already have in v2. No new exposure class; existing `.gitignore` patterns extend to `data/resume_variants*`.
- **Secrets**: no new secrets introduced by this feature (Ollama is local, no API keys). Existing v2 secret-handling (`telegram.bot_token`, `workday.password` in `config.yaml`, already gitignored) is unaffected.
- **Fabrication as a security-adjacent risk**: an AI-fabricated claim on a résumé submitted to a real employer is a genuine reputational/legal risk to the user, arguably the single highest-stakes failure mode in this entire feature — treated accordingly with defense-in-depth (prompt constraint + mechanical fact-check, §A4.3), not as an afterthought.

---

## A12. Extensibility Points (concrete, not aspirational)

| Extension | How, without touching core |
|---|---|
| New job platform | Implement `PlatformAdapter`, register it |
| New tailoring strategy (e.g. swap Ollama model, or add a cloud-LLM high-quality tier for a small number of "dream jobs") | Implement `TailoringStrategy`, select via `tailoring.strategy` config key |
| New résumé output format (e.g. DOCX for a platform that specifically prefers it) | Add a `render_docx.py` alongside `render.py`, both implement a `ResumeRenderer` Protocol |
| New blocking-event type (e.g. a platform-specific "phone verification" challenge) | Add to the `BlockingEventType` enum + one row in the static resolvability table (§A5.2) — the decision policy function needs no change for event types that map to existing branches |

---

## A13. Rollout Plan

1. **Phase 1 (foundation, no behavior change)**: `PlatformAdapter` Protocol + adapter wrappers around existing per-site code (pure refactor, existing tests/manual runs must behave identically). Layered config (§A10).
2. **Phase 2 (tailoring, additive + feature-flagged)**: `tailoring/` package, gated behind `application.resume_tailoring_enabled: false` by default. Runs in **shadow mode** first — generates and validates tailored résumés, logs what *would* have been uploaded, but still uploads the static `resume.pdf` — so the human can review variant quality risk-free before trusting it live.
3. **Phase 3 (resilience)**: `resilience/` package, replacing `apply_runner.py`'s flat retry with the signal-fused policy. Also feature-flagged initially (`application.adaptive_retry_enabled`), falls back to v2's existing flat-retry behavior when off, so this can ship independently of Phase 2.
4. **Phase 4 (go-live)**: flip both flags on after a supervised dry-run review, matching the exact "dry_run: true first" discipline already established for this project's AWS deployment.

---
---

## PART B — ANNEXURE: v2 Architectural Issues, and How v3 Corrects Them (target depth: ~10 pages)

This annexure is grounded in issues **actually found** during this project's build-out (config bugs, deployment failures, and design gaps surfaced while dockerizing and deploying v2 to AWS), not hypothetical critique — each item below is either a bug this project directly hit and fixed, or a structural gap the user's new requirements expose.

### B1. Configuration sprawl (directly observed, three times, during this project)

**Issue**: v2's `config.py` supports one config file per invocation. To support Mac-local, Docker, and AWS-VM deployments, this project created `config.yaml`, `config.docker.yaml`, `config.vm.yaml` — three ~380-line files sharing ~90% identical content (user profile, target roles, platform filters), differing only in `browser.*`/`llm.*`/`application.*` deployment-specific knobs. This is a maintenance liability: any change to shared content (e.g. tuning `keep_role_keywords`) must be manually replicated three times, and *was* a live risk during this project (compensation/role-filter tuning happened once, on the Mac config, and had to be manually remembered and re-applied to the Docker and VM configs).

**Root cause**: `config.py` has no concept of layered/overlay configuration — `AppConfig` is built from a single flat YAML dict, dataclass-by-dataclass, with no merge semantics.

**v3 correction**: §A10's base+overlay model. This is not a new idea invented for this document — it's the standard 12-factor-adjacent pattern (`.env` + environment overrides) applied to this project's actual YAML config system, closing the gap directly.

### B2. Flat retry policy with no failure-type awareness

**Issue**: `apply_runner.py`'s `apply_retries`/`retry_backoff_ms` apply uniformly regardless of *why* an attempt failed. A dead browser (`BrowserClosed`), a CAPTCHA, and a transient click-miss are all "a failure," retried identically.

**Root cause**: `_apply_job_with_retries` was designed (reasonably, for v2's scope) around one dominant failure mode — flaky DOM interaction — and never extended as new failure modes (bot-detection, session expiry) became relevant at scale.

**v3 correction**: §A5's signal-fused `BlockingEvent`/`Decision` model. This is the single largest architectural gap this project surfaced, both because it's explicitly in the user's new requirements and because it's a *safety* issue: retrying against a CAPTCHA is itself a bot-detection signal, meaning v2's naive retry loop could, in the worst case, actively *cause* the account-blocking outcome it should be avoiding.

### B3. No cost/token observability (a real gap once resume tailoring is added)

**Issue**: v2 has `llm.max_concurrency`/`min_confidence`/etc. as *policy* knobs but no *measurement* of what those policies actually cost in tokens or calls over time. This was invisible risk even in v2 (silent LLM-call growth would go unnoticed), and becomes directly load-bearing once resume tailoring (a new, higher-token-cost feature) is added — without a ledger, there's no way to verify the "near-zero cost" claim in §A4.4 empirically, only assert it.

**v3 correction**: §A7's `cost_ledger.py`. Minimal (one SQLite table, one write per LLM call) — deliberately not over-engineered into a full metrics/observability stack, matching the project's cost-consciousness at every other layer.

### B4. Implicit, not formal, platform integration contract

**Issue**: Each of `naukri/`, `hirist/`, `wellfound/`, `instahyre/`, `cutshort/`, `uplers/` exposes functions (`apply_batch`, `collect_job_listings`, etc.) that `cli.py` calls by *name*, via a hand-maintained `PLATFORM_APPLY` dict. There's no `Protocol`/ABC enforcing that a new platform actually implements everything the orchestrator expects — a missing function or wrong signature is only caught at runtime, for that specific platform, potentially mid-run.

**Root cause**: v2 grew organically, one platform at a time; the informal-but-consistent pattern across six packages was "good enough" until a formal contract became load-bearing for OCP-style extensibility, which is now an explicit requirement.

**v3 correction**: §A6's `PlatformAdapter` Protocol, enforced with `runtime_checkable` + a registry-time assertion (fail fast at startup, not mid-run on job #47).

### B5. Wellfound/Uplers headless fragility (directly observed during this project's Docker smoke test)

**Issue**: During this project's own Docker deployment work, Wellfound's headless Chromium session (no real Chrome profile) verified as "logged in" but rendered a signup/marketing page instead of the real job search — 0 listings found, silently. This is a real, reproduced failure mode, not a hypothetical: bundled headless Chromium (no real browser fingerprint, no persistent real-Chrome profile) is more readily detected/soft-blocked by Wellfound's anti-bot measures than a real Chrome profile in headed *or* real-headless mode.

**Root cause**: v2's `browser.py` treats "headless" as a single boolean with no awareness that different sites have meaningfully different bot-detection sensitivity — the same headless config that works fine for Naukri/Hirist (cookie-based, apparently less aggressive detection) silently degrades Wellfound.

**v3 correction**: This is exactly what `detect_blocking_event()` (§A6) is for — Wellfound's adapter should treat "verified logged in but 0 listings on a normally-populated search" as a `BlockingEvent` (a soft-block signal), routing through §A5's policy (likely `NOTIFY_AND_DEFER`, since this isn't resolvable by retrying) rather than silently reporting "0 new jobs" as if that were a normal, successful outcome — v2's current behavior actively hides this failure mode from the user, which is worse than failing loudly.

### B6. Dependency misclassification caused a hard runtime crash (directly observed and fixed during this project)

**Issue**: `dataclasses-jsonschema`, imported directly by `config.py` (every config load depends on it), was listed only under `pyproject.toml`'s `dev` extras and entirely absent from `requirements.txt`. This worked by accident on the Mac (a dev-extras install happened to include it) and crashed immediately and completely in the Docker image (which installs only `requirements.txt`).

**Root cause**: no automated check distinguishing "runtime dependency" from "dev-tool dependency" — the distinction was maintained purely by developer memory, which failed once a second install path (Docker) existed.

**v3 correction**: fixed directly in this project already (moved to core `dependencies` in `pyproject.toml`, added to `requirements.txt`). Documented here as a pattern to guard against going forward: any new v3 module with a new third-party dependency (e.g. `sqlite3` is stdlib — no new dependency; a PDF renderer like `weasyprint` **is** new) must be added to `requirements.txt` in the same commit that introduces the import, and the Docker/VM smoke-test step (already an established practice from this project) is the mechanical check that catches a regression before it ships.

### B7. Host/port and env-var wiring bugs (directly observed and fixed during this project)

**Issue**: `main.py serve` defaults to `127.0.0.1:8765`; the Dockerfile/compose setup assumed `0.0.0.0:8000` without passing explicit flags, and separately, `JAA_INTERVAL_MINUTES`/`JAA_PLATFORM` env vars were only read in `--reload` (dev) mode — silently inert in the plain `docker compose up` path this project actually uses.

**Root cause**: `serve`'s CLI-flag defaults and its env-var-driven factory (`create_app_from_env`) evolved as two separate configuration paths that were never reconciled — a classic "two ways to configure the same thing, only one of them actually wired up for the deployment that matters" gap.

**v3 correction**: not a v3 architectural change per se (already fixed directly in `Dockerfile`), but the **layered config system (§A10)** is the structural prevention for this *class* of bug going forward — a single source of truth for "how is this deployment configured" removes the two-parallel-paths condition that let this drift in the first place.

### B8. Single Chrome-profile serialization as an unstated scalability ceiling

**Issue**: When `use_chrome_profile: true`, only one process may hold that profile at a time (`_use_chrome_profile` excludes Chrome-profile platforms from `parallel_platforms`). This is correct and necessary, but it's an *implicit* architectural constraint, not a documented one — nothing in v2 makes clear that "want more apply throughput" cannot be solved by just raising `apply_workers` once real-Chrome-profile reuse is in play (as it now is for the AWS VM deployment, across all six platforms).

**v3 correction**: documented explicitly here as a known, permanent ceiling of the "reuse a real Chrome profile" approach (§A2's table implicitly acknowledges this — tailoring/resilience don't change it). The only way to scale apply throughput past this ceiling is either accepting the headless-Chromium-without-profile fragility (§B5) for the cookie-based platforms specifically (already how `PARALLEL_COOKIE_PLATFORMS` works in v2) or running multiple independent Chrome profiles (multiple logged-in identities) — explicitly out of scope for this single-user tool and not recommended (ToS and detection risk compound with more automated identities, not less).

### B9. No formal "is this résumé about to fabricate something" safeguard existed prior to this feature (net-new risk this document had to design against, not a pre-existing bug)

**Issue**: n/a in v2 (v2 doesn't generate résumé content at all — this is a new risk class introduced *by* the feature this document specifies, not a latent v2 bug).

**Why it's in this annexure anyway**: because the same "never guess, fail closed" philosophy that makes v2's answer engine trustworthy (§A4.1 of the *original* codebase's design, referenced throughout this document) had to be *deliberately re-derived and re-applied* to résumé generation rather than assumed to transfer automatically — an LLM generating résumé prose is a meaningfully different risk surface than an LLM selecting from a small set of pre-validated screening-question answers, and treating them as equivalent-risk would have been a design mistake this document explicitly avoided (§A4.3, §A11).

---

## Summary Table: Every User-Stated Requirement → Where It's Addressed

| Requirement | Section |
|---|---|
| JD-aware resume tailoring, 1-2 clarifying questions | §A4.1 (Tier 3), §A4.2 |
| Exactly 2 pages, ATS-friendly | §A4.3 |
| Store/reuse tailored resumes for similar JDs | §A4.1 (Tier 0), §A8 |
| Scalable and extensible | §A3, §A6, §A12 |
| Safe/secure, low LLM cost | §A11, §A4.4, §A7 |
| Minimum retries per job, optimized time/RAM/CPU | §A5, §A4.4 |
| SOLID/DRY/SRP/OCP, SoC, modularity | §A1, §A3, §A6 |
| Avoid anti-patterns | §A9 |
| Not get blocked by portals | §A5.3, §A5.4, §B5 |
| Maximize shortlisting/callback chances | §A4 (whole subsystem) |
| Optimize cost/time for 100 applications | §A4.4, §A7 |
| Minimize human intervention, right retry-vs-wait call using signals | §A5 (whole section) |
