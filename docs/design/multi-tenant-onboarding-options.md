# Multi-Tenant Job-Seeker Platform — Design Options for Discussion

**Status**: exploratory, for your review and decision. Nothing here is built or deployed —
this document exists so we can make an informed call together, not to pre-commit to an approach.

**Vision stated**: thousands of job seekers share resumes with the platform; the system
applies to hundreds of relevant jobs per seeker via AI-customized résumés, across hundreds
of job platforms/company career sites.

**Why this needs a real decision, not just more code**: the current codebase is a
single-user, local-first tool — one person's résumé, one person's config, running on their
own machine. What's described above is a different product: it means holding *other real
people's* PII, résumés, and — if built the most literal way — their job-portal login
credentials, at scale. That's a different risk class (a breach exposes thousands of people,
not one), a different legal posture (ToS across "hundreds" of platforms, data protection
obligations once you're a data controller for other people's information), and a different
engineering problem (multi-tenant isolation, horizontal scaling, per-tenant browser
automation). This document lays out the real options so the tradeoffs are visible before
anything gets built.

---

## 1. The central hard problem: credentials

Everything else in this design is secondary to one question: **how does the system act on a
job platform on a user's behalf without the platform itself becoming a giant, centralized
liability if breached?** Four options, roughly in order of increasing capability and
increasing liability:

### Option A — No credentials at all: "Tailoring as a Service"
The platform never touches job-portal logins. A user uploads a résumé + facts once; submits
job descriptions (manually, or via their own self-hosted copy of this tool calling out to a
central API); gets back a tailored, ATS-safe résumé PDF. **Actual applying stays on the
user's own machine/VM** — exactly the self-hosted deployment already built and documented
tonight (Docker + AWS playbooks). The SaaS layer is purely the AI-tailoring engine (the
`tailoring/` subsystem already built) plus account/résumé management.

- **Data held**: résumés, facts, generated tailored variants. No passwords, no session
  cookies, no job-portal access at all.
- **Liability if breached**: real (résumé PII is still sensitive) but bounded — nothing that
  lets an attacker act as the user anywhere.
- **What it doesn't deliver**: the "we apply for you" promise — users still run and operate
  their own apply agent. Materially less magical, materially safer, and ships fastest since
  the core engine already exists.

### Option B — Session-cookie delegation (not passwords)
User logs into each job platform **themselves**, in their own browser, then exports the
resulting session cookie (this exact mechanism — `auth.method: cookies` — already exists in
this codebase today). The platform stores the cookie, not the password. A stolen cookie is
still misusable, but: (a) it's narrower in scope than a password (can't be used to reset the
account, change the email, etc. on most platforms), (b) it self-expires, and (c) the user
never has to type their real password into a third-party system at all.

- **Data held**: session cookies per user per platform (encrypted at rest, decrypted only at
  the moment of use inside an isolated worker).
- **Liability if breached**: meaningful — a breach could let an attacker act as many users on
  many job platforms until cookies expire/get revoked. Needs a real secrets-vault approach
  (see §3), not a database column.
- **What it delivers**: the actual "we apply for you" experience, with a materially smaller
  blast radius than raw password storage.

### Option C — OAuth delegation where platforms support it
For the subset of platforms offering "Sign in with Google" (Wellfound/Uplers already use this
pattern), the user authorizes via OAuth; the platform stores a scoped, individually-revocable
refresh token — never a password, and the user can revoke access from their own Google
account settings at any time without contacting us.

- **Coverage**: partial — most of the 7 platforms in this codebase are cookie/password-based,
  not OAuth. Doesn't generalize to "hundreds of platforms" on its own.
- **Best used**: as a supplement to Option B for the subset of platforms where it's available
  — the better answer specifically for those, not a full solution alone.

### Option D — Full custodial automation (stored passwords, centrally executed)
The platform stores real login credentials (encrypted) and runs the automation centrally.
Highest capability, matches the vision most literally, and also the highest liability: a
breach here is a breach of thousands of people's actual job-platform passwords — the kind of
incident that ends up in security news and regulatory inquiries. This is not a "the encryption
makes it fine" problem; centralized password custody at this scale is a genuinely different
risk category, and most SaaS platforms that reach this point do so only after (a) a security
audit / SOC2-style posture, (b) legal review of ToS exposure across every integrated platform,
(c) an explicit, informed consent flow, and (d) usually starting from a small invite-only
beta rather than "thousands of users" on day one.

**Recommendation**: don't start here. If the product validates via Options A/B, this becomes
a later, deliberate decision — not a default MVP shape.

---

## 2. Tenant data model — three shapes

### Shape 1 — Classic multi-tenant database
One Postgres instance, every table carries a `tenant_id`, one deployment serves everyone.
Standard SaaS pattern, cheapest to operate per-user, most scalable, but concentrates all
tenant data (and, if Option D is ever chosen, all credentials) in one place — the single
biggest blast-radius shape.

### Shape 2 — Isolated per-tenant deployment
Each job seeker effectively gets their own instance — literally what's built tonight (one
`config.yaml`, one `profile/`, running in its own container/VM), orchestrated/provisioned by
a thin management layer that spins instances up and down. No single database of everyone's
data or credentials exists at all — a breach of one tenant's instance doesn't expose anyone
else's. Costs more per user (more infra than a shared DB), and "thousands of isolated VMs" is
a real operational problem to solve, but it directly sidesteps the "one big breach" risk by
construction rather than by promising good encryption practices.

### Shape 3 — Hybrid (the usual real-world answer)
Central Postgres for **non-sensitive-ish** metadata (accounts, subscription state, résumé
version pointers, application history, analytics) — genuinely benefits from being centralized
and queryable. **Sensitive material** (credentials/cookies if Option B/C/D is ever adopted)
lives in a dedicated secrets vault (AWS Secrets Manager / HashiCorp Vault / similar), isolated
from the metadata store, decrypted only inside a short-lived, per-tenant execution context at
the moment of use. This is the standard shape for "SaaS platform that must hold some
customer secrets" — separate the boring queryable data from the dangerous secret data, and
protect them differently.

---

## 3. Recommended phased MVP scope

Rather than one big-bang build, a sequence where each phase is independently shippable, adds
capability, and only takes on more liability once the previous phase has validated demand:

| Phase | What it is | Credential model | Data held | Ships from |
|---|---|---|---|---|
| **0** | Ship tonight's tool as self-hosted / open-source | None — user's own machine | Nothing centralized | Already ~built |
| **1** | "Résumé Tailoring API" — hosted AI tailoring, self-hosted applying | Option A (none) | Résumés, facts, tailored variants (Postgres, Shape 1 is fine here — no secrets) | Reuses `tailoring/` as-is |
| **2** | Managed apply automation, invite-only beta | Option B (+ C where available), Shape 3 (vault-isolated) | Cookies/tokens in vault, metadata in Postgres | New: worker orchestration, vault integration |
| **3** | Broader scale, more platforms, general availability | Same as Phase 2, hardened | Same, at scale | After Phase 2 validates the security posture in practice |

**Why start at Phase 1, not Phase 2 or the full vision**: it ships the most differentiated,
hardest-to-replicate part of the product (the tailoring engine, already built) with close to
zero new liability, and it's a real, monetizable, testable product on its own — not a
placeholder. It also naturally produces the usage data (how many tailoring requests, which
JD patterns are common, how much local-LLM compute a request actually needs) that makes
sizing Phase 2's infrastructure a real estimate instead of a guess.

---

## 4. Open questions for you to decide (not answerable by architecture alone)

1. Is "we apply on your behalf" (Options B/C/D) actually the product, or would "AI tailors
   your résumé, you still click apply" (Option A) satisfy the core value proposition for a
   first version? This is the single highest-leverage scope decision.
2. If Option B/C/D: what's the acceptable legal exposure across "hundreds" of platforms'
   ToS? Worth a real legal read before committing engineering time, not after.
3. Target user: is this "help me personally + close friends/family" (small n, lower stakes,
   could reasonably start at Shape 2 / Option B directly) or genuinely "thousands of
   strangers" (needs the fuller Phase 0→3 sequencing and a real security/legal posture
   first)?
4. Monetization model (if any) — affects infra sizing and whether Phase 1 alone is a viable
   standalone product or just a stepping stone.

Nothing above is implemented. Whenever you want to move on any of it, the natural next step
is picking a phase and an option from §1/§2, at which point this becomes a real SDA document
with diagrams, following the same process as the résumé-tailoring feature.
