# Implementation Plan — Résumé Tailoring Web Service (Phase 1)

**Decisions locked in** (from the multi-tenant onboarding discussion):
- **Credential model**: Option A — no job-portal credentials ever touch this service. It tailors résumés; applying stays on each user's own self-hosted copy of the existing tool.
- **Scale**: small first version — personal + friends/family, <20 people.
- **Access**: simple web UI (upload résumé + paste a JD, get a tailored PDF back).
- **Auth**: email + magic link. No passwords stored anywhere.
- **Hosting**: AWS, same account as tonight's EC2 work.

Because the scale is genuinely small, this plan deliberately avoids infrastructure sized for "thousands" — no Celery/Redis queue, no Kubernetes, no multi-region anything. Right-sized for &lt;20 users, designed so the *data model* doesn't have to be redone if it later grows (tenant-scoped tables from day one), even though the *infrastructure* can stay simple until there's real demand to justify more.

---

## 1. What gets reused vs. built new

**Reused as-is** (this is the whole point — the hardest part of this product already exists and is tested):
- `jobs_auto_apply/tailoring/` — the full tiered engine (similarity reuse → rule-based → local LLM → clarify), `ats_validator.py`, `render.py`. Called as a library, not through the CLI.
- `jobs_auto_apply/embeddings.py` — the MiniLM model for JD similarity.
- Local Ollama (`job-answers`/`job-verify`) — same models, same $0-cloud-cost design.
- The `Dockerfile`/`docker-compose.yml` patterns from tonight's deployment work.

**Built new**:
- A FastAPI web app (separate service, imports `tailoring/` as a library — does not touch the existing CLI tool's `config.yaml`/`profile/` at all)
- Postgres for multi-tenant data (users, résumé profiles, tailoring jobs)
- Magic-link auth
- A minimal web UI (server-rendered, no separate frontend build pipeline — keeps this shippable fast at this scale)
- S3 for résumé/output PDF storage

---

## 2. Data model

```sql
users (id, email UNIQUE, created_at)

magic_link_tokens (token, user_id, expires_at, used_at)
-- 15-minute expiry, single-use, no password ever stored anywhere

resume_profiles (
  id, user_id,
  resume_facts_json,        -- structured, mirrors profile/resume_facts.yaml's shape
  application_facts_json,   -- mirrors profile/application_facts.yaml's shape
  resume_pdf_s3_key,
  created_at, updated_at
)
-- one active profile per user for v1 — matches the existing CLI tool's single-profile model

tailoring_jobs (
  id, user_id, resume_profile_id,
  jd_text, status,             -- pending | processing | done | failed
  result_pdf_s3_key,
  tier_used, ats_keyword_coverage,
  error_message,
  created_at, completed_at
)
```

Every table is `user_id`-scoped from day one — even though the infra below is a single shared box, the *data* is already correctly tenant-isolated, so growth later is a scaling change, not a data-model rewrite.

---

## 3. Auth flow (magic link)

1. User enters email on the landing page.
2. Backend generates a random token, stores it in `magic_link_tokens` with a 15-minute expiry, emails a link via **AWS SES** (natural choice given AWS hosting — cheap, no separate vendor).
3. User clicks the link → backend validates the token (unused, unexpired) → issues a signed session cookie → redirects into the app.
4. No password field exists anywhere in this system. Directly extends Option A's "hold as little sensitive material as possible" philosophy to the app layer itself.

**Setup dependency**: SES needs a verified sending domain/email before this works — flagged as a setup step, not a code task.

---

## 4. Backend flow (tailoring job)

1. User uploads a résumé PDF + fills a short form (mirrors `resume_facts.yaml`/`application_facts.yaml`'s key fields) → stored in `resume_profiles`, PDF to S3.
2. User pastes a job description, submits → row created in `tailoring_jobs` with `status=pending`.
3. A simple background worker (an `asyncio` loop inside the same FastAPI process, polling `tailoring_jobs` for `pending` rows every few seconds — **not** a separate queue service; proportionate to &lt;20 users' request volume) picks up the job, calls `tailoring.engine` the same way the CLI's `apply_runner.py` does today, writes the resulting PDF to S3, updates `status=done` (or `failed` with `error_message`).
4. UI polls (or a simple server-sent-events endpoint) for job status, shows a download link once `done`.

**Why a DB-polling worker instead of Celery/Redis**: at this scale, a job table + a polling loop is genuinely simpler to operate (one process, no extra service to run/monitor/secure) and does the same job. Revisit only if real usage volume ever makes polling latency or DB load an actual problem — not before.

---

## 5. Web UI

Server-rendered (FastAPI + Jinja2 templates), no separate JS build step:
- `/` — landing page, email input → triggers magic link
- `/auth/verify` — magic link landing, sets session
- `/profile` — upload résumé PDF, fill facts form
- `/tailor` — paste a JD, submit
- `/jobs` — list of past tailoring jobs + status + download links

Deliberately minimal — the value is the tailoring quality, not UI polish, for a first version with people who already trust you.

---

## 6. Deployment (AWS)

A **separate, small stack** from tonight's personal job-apply VM — keeps "my personal automation tool" and "a service other people use" cleanly apart, so a mistake in one doesn't touch the other.

| Component | Choice | Why |
|---|---|---|
| Compute | One EC2 `t3.medium` (2 vCPU/4GB) | Runs the FastAPI app (Docker) + Ollama — same sizing logic as the earlier Docker deployment discussion; bursty small-scale load fits one box |
| Database | RDS Postgres `db.t3.micro` | Managed, low-maintenance, AWS free-tier eligible — avoids running Postgres yourself on the same box as the app |
| Storage | S3 bucket | Résumé uploads + tailored output PDFs — durable, cheap, doesn't consume EC2 disk |
| Email | SES | Magic-link delivery |
| Container | Extends tonight's `Dockerfile`/`docker-compose.yml` pattern | One more service added to the existing compose file, not a new deployment paradigm |

---

## 7. Build phases

| Phase | Delivers | Verification |
|---|---|---|
| 1 | FastAPI skeleton, Postgres schema/migrations, magic-link auth end-to-end (no tailoring yet) | Sign up with a real email, receive the link, land in an authenticated session |
| 2 | Résumé/profile upload → S3 + Postgres | Upload a PDF, confirm it's retrievable |
| 3 | Tailoring job submission + background worker wrapping the existing `tailoring/engine.py` (adapted: JD pasted as text instead of scraped from a `JobListing`, tenant-scoped storage instead of local files) | Submit a real JD, get back a tailored PDF, spot-check it against the existing ATS-safety checks |
| 4 | Web UI tying it together | A full walkthrough: sign up → upload résumé → submit JD → download tailored PDF, done entirely through the browser |
| 5 | AWS deployment (EC2 + RDS + S3 + SES), smoke test with your own account + one friend's | Two real people successfully use it end to end |

Same review discipline as tonight's work: build in Sonnet, independent review pass (Opus for the auth/data-isolation-sensitive parts specifically, since that's the "complex logic, other people's data" piece this project's model-mix guidance is meant for) before anything goes live with a second real user's data.

---

## 8. Explicitly deferred (not in this phase)

- Any job-portal credential handling (Options B/C/D) — this phase never touches them.
- Multiple résumé profiles per user, team/org accounts, billing.
- A queue service, horizontal scaling, or multi-region anything — revisit only once real usage data says it's needed.
- A polished frontend framework — the server-rendered UI is intentionally minimal for v1.
