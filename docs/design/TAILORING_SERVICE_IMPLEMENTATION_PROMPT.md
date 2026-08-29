# Implementation prompt — paste this whole file's content into a fresh Claude Code session

You are building a new, separate web service inside the existing repo at the current working
directory: `jobs-auto-apply-main_v4`. Do not assume anything about the codebase from this
prompt alone — read what's actually there first.

## Step 0 — Load context

1. Read `docs/design/tailoring-service-implementation-plan.md` in full — the authoritative
   plan for what you're building. Section references below refer to it.
2. Read `docs/design/multi-tenant-onboarding-options.md` for *why* the plan is shaped this
   way (Option A credential model specifically — no job-portal credentials, ever, anywhere in
   this feature).
3. Read `jobs_auto_apply/tailoring/` in full — `engine.py`, `ats_validator.py`, `render.py`,
   `variant_store.py`, `jd_extractor.py`, `models.py`. This is the engine you're wrapping in a
   web service, not rewriting. Understand its current call shape: it's currently invoked from
   `apply_runner.py` with a scraped `JobListing` (which has `.description` etc.) and a
   single-tenant `AppConfig`/local filesystem (`data/resume_variants.db`,
   `profile/resume_facts.yaml`). You will need a thin adapter layer so it can be called with a
   pasted JD string and per-tenant data instead — see §1/§4 of the plan for what changes vs.
   what's reused verbatim.
4. Confirm local Ollama is set up (`ollama list` should show `job-answers`/`job-verify`) — this
   new service reuses those exact models. Do not add a cloud LLM dependency anywhere.

## Non-negotiable constraints

- **No job-portal credentials, anywhere, ever, in this feature.** This service tailors
  résumés and hands back a PDF. It never asks for, stores, or touches a Naukri/LinkedIn/Indeed/
  etc. login. If you find yourself building anything that looks like "log into a job site on
  the user's behalf," stop — that's Option D from the design doc, explicitly out of scope and
  not decided on.
- **Every table is `user_id`-scoped from day one** (§2 of the plan), even though the
  infrastructure is a single shared box at this scale. Getting tenant isolation right in the
  data model now is cheap; retrofitting it later on live user data is not.
- **No passwords, anywhere.** Magic-link email auth only (§3). There should be no password
  field, no password hash column, nothing resembling password storage in this codebase.
- **Zero cloud LLM calls** — same constraint as the CLI tool's tailoring feature. Every
  generative step goes through the existing local Ollama models.
- **Keep infrastructure proportionate to the stated scale (&lt;20 users)** — no Celery/Redis,
  no Kubernetes, no premature horizontal scaling. §4 of the plan specifically asks for a
  DB-polling background worker, not a queue service. Don't over-engineer this; the plan's
  reasoning for why is explicit — follow it, don't second-guess it into something bigger.
- **This is a separate service from the existing CLI tool.** Do not modify `jobs_auto_apply/
  cli.py`, `config.py`, or anything the personal job-apply automation depends on, except where
  §1 of the plan explicitly calls for a small adapter so `tailoring/engine.py` can be called
  with different inputs (a pasted JD string, per-tenant storage paths) — and even then, prefer
  adding a new thin wrapper function over changing the existing one's signature, so the CLI
  tool's current behavior is provably unaffected. Verify this at the end (see Definition of
  Done).
- **New dependencies** (FastAPI is already a dependency; you'll likely need `sqlalchemy` or
  similar for Postgres, `jinja2` for templates, `boto3` for S3, an SES-sending library) go into
  `requirements.txt` and `pyproject.toml` in the same commit that introduces the import —
  Annexure §B6 of the original SDA documents a real crash this exact mistake caused earlier in
  this project. Don't repeat it.

## Build order (work through phases sequentially, verify each before moving on)

Follow the plan's §7 table exactly:

### Phase 1 — Backend skeleton + magic-link auth
Set this up as a new top-level directory in the repo (e.g. `service/` — your call on the exact
name, but keep it clearly separate from `jobs_auto_apply/`). FastAPI app, Postgres
schema/migrations (a lightweight migration tool like `alembic` is fine, or hand-rolled SQL if
simpler at this scale — your call), the `users`/`magic_link_tokens` tables from §2, SES
integration for sending the link. **Verify**: sign up with a real email address (you'll need
SES sending set up and verified — if that's not available in this environment, build and test
the token-generation/validation logic directly with unit tests, and clearly flag that live SES
sending needs to be verified by the user once deployed).

### Phase 2 — Résumé/profile upload
`resume_profiles` table, S3 integration (or a local-disk stand-in with a clear TODO to swap to
S3 if AWS credentials/bucket aren't available in this environment — flag this clearly rather
than silently skipping it). A simple upload endpoint + form. **Verify**: upload a PDF, confirm
it round-trips (upload → stored → retrievable).

### Phase 3 — Tailoring job submission + background worker
This is the core of the feature. Build the adapter layer that lets `tailoring/engine.py` run
against a pasted JD string and per-tenant storage instead of a scraped `JobListing` and the
single-tenant local files it currently assumes. The `tailoring_jobs` table from §2. The
DB-polling background worker from §4 — an asyncio loop inside the same FastAPI process, not a
separate service. **Verify**: submit a real job description through the (even minimal, at this
point) system, confirm a tailored PDF comes back, and spot-check it against the *existing*
`ats_validator.py` checks (2-page limit, no fabricated claims) — this is the one thing that
absolutely must still work correctly, since it's the entire value proposition.

### Phase 4 — Web UI
Server-rendered pages per §5 of the plan (`/`, `/auth/verify`, `/profile`, `/tailor`,
`/jobs`). Jinja2 templates, no separate frontend build step. **Verify**: a full walkthrough
through the browser — sign up, upload a résumé, submit a JD, get a download link, download a
real tailored PDF.

### Phase 5 — AWS deployment
Extend the existing `Dockerfile`/`docker-compose.yml` pattern from this repo's earlier Docker
deployment work (read those files first) to add this new service as one more container/compose
entry, alongside — not replacing — the existing personal job-apply tool's deployment. Document
the AWS resources needed (EC2 instance, RDS Postgres instance, S3 bucket, SES verified
identity) as a clear setup checklist for the user, since provisioning actual AWS
infrastructure is a real, billed action the user needs to explicitly approve and run
themselves (or ask their assistant to run interactively with them) — do not attempt to
provision real AWS resources autonomously as part of this build; this phase's deliverable is
the deployment *code and instructions*, not a live running service.

## Definition of done

- All 5 phases complete, each verified as described above.
- A fresh read of `jobs_auto_apply/cli.py`, `jobs_auto_apply/config.py`, and
  `jobs_auto_apply/tailoring/engine.py`'s original public function signature confirms nothing
  about the existing personal CLI tool's behavior changed — run the existing test suite
  (`PYTHONPATH=. python -m pytest tests/ -v`, expect 31/31 still passing) and a dry-run CLI
  command to confirm.
- No password field/column exists anywhere in the new service's schema.
- No job-portal credential field exists anywhere in the new service's schema or code.
- Every new table has a `user_id` column establishing tenant scope.
- No cloud LLM SDK import or API call anywhere in the new code (grep to confirm, same check
  used earlier in this project).
- New dependencies are in both `requirements.txt` and `pyproject.toml`.
- Final report: what was built, what needed a live AWS/SES credential you didn't have (be
  specific about what the user needs to provide/configure), any judgment calls made where the
  plan left room for interpretation, and the exact commands/URL the user needs to try it
  locally before any real deployment.

## Tone / working style

Work through the phases sequentially, committing after each verified phase (matching this
project's established git history style — clear messages, not one giant commit). If you hit a
genuine ambiguity the plan doesn't resolve, make the most sensible call consistent with the
plan's stated scale/simplicity principles in §0 and document your reasoning — don't block on
minor judgment calls. Do stop and flag clearly (don't guess past it) if you hit something that
would require real AWS credentials, a real SES-verified sending domain, or any other live
external resource you don't have in this environment — build everything that can be verified
without those, and be explicit about what's left for the user to provide.
