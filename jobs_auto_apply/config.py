from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dataclasses_jsonschema import JsonSchemaMixin


@dataclass
class UserConfig(JsonSchemaMixin):
    name: str
    email: str
    phone: str
    linkedin: str
    expected_display_name: str
    github: str = ""
    # Dialing code for the candidate's phone (e.g. "+91", "+1"). Used only to format
    # the phone number in cover-letter signatures. Leave blank to print the number
    # exactly as entered (no country code assumed).
    phone_country_code: str = ""


@dataclass
class CompensationConfig(JsonSchemaMixin):
    current_ctc_lpa: float = 0.0
    current_fixed_lpa: float = 0.0
    current_variable_lpa: float = 0.0
    current_esops_lpa: float = 0.0
    expected_ctc_lpa: float = 0.0


@dataclass
class ProfileConfig(JsonSchemaMixin):
    headline: str = ""
    years_experience: int = 0
    core_skills: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    skip_companies: list[str] = field(default_factory=list)
    # Role filters are fully config-driven and domain-neutral so the tool works for
    # any field (law, finance, design, engineering, …). Define what to skip via
    # skip_role_keywords / skip_role_patterns and what to keep via keep_role_keywords.
    skip_role_keywords: list[str] = field(default_factory=list)
    # "Keep" anchors — a title matching any of these is kept even if it also matches a
    # skip rule. Empty by default; set to the terms that define your target roles
    # (e.g. "attorney", "counsel" for law; "backend", "platform" for engineering).
    keep_role_keywords: list[str] = field(default_factory=list)
    # Extra raw regex patterns to skip (full power, matched against the title,
    # case-insensitive). Use for anything the keyword/built-in groups can't express.
    skip_role_patterns: list[str] = field(default_factory=list)
    # Skills/domains you have NO experience in. A job is skipped when its TITLE is
    # about one of these AND does not also name a skill you do have (core_skills /
    # skill_years > 0). Empty by default; populate it for your own field.
    skip_no_experience_skills: list[str] = field(default_factory=list)


@dataclass
class CoverLetterConfig(JsonSchemaMixin):
    mode: str = "dynamic"  # dynamic | template
    include_ctc: bool = True
    max_words: int = 200
    reference_path: str = "profile/cover_letter_reference.txt"


@dataclass
class ResumeConfig(JsonSchemaMixin):
    path: str
    sync_to_wellfound: bool = False
    sync_to_naukri: bool = True
    naukri_sync_interval_minutes: int = 30
    sync_to_hirist: bool = True
    hirist_sync_interval_minutes: int = 30


@dataclass
class PlatformRuntimeConfig(JsonSchemaMixin):
    """Generic per-platform runtime config: ``enabled``/``cookies_file`` plus
    that platform's own typed filters dataclass (e.g.
    ``naukri.config.NaukriFiltersConfig``).

    Replaces the pre-refactor pattern of one hand-written ``<Platform>FiltersConfig``
    dataclass + one named ``AppConfig`` field per platform (``AppConfig.wellfound``,
    ``AppConfig.naukri``, ...) with ``AppConfig.platforms: dict[str,
    PlatformRuntimeConfig]``, built generically in ``load_config()`` from the
    auto-discovered ``PlatformSpec`` registry (see ``platform_adapter.py``) — so
    adding platform #8 needs no new dataclass or field here, only that platform's
    own ``<platform>/config.py`` + ``PLATFORM = PlatformSpec(...)``.

    Judgment call on ``filters``' type: ``dataclasses_jsonschema`` (this project's
    schema-generation library) needs concrete types to emit a JSON Schema, and a
    truly heterogeneous "whichever dataclass this platform's PlatformSpec says"
    value can't be expressed as a fixed Union without hardcoding every platform's
    filters type here — exactly the per-platform coupling this refactor removes.
    ``Any`` is used instead; ``scripts/generate_config_schema.py`` still runs
    cleanly (see that script's comment), and each platform's own filters
    dataclass is still fully typed and still a ``JsonSchemaMixin`` in its own
    package for anyone who wants its schema directly.
    """

    enabled: bool
    cookies_file: str
    filters: Any


@dataclass
class LLMConfig(JsonSchemaMixin):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = "job-answers"
    verifier_model: str = "job-verify"
    verifier_enabled: bool = True
    temperature: float = 0.05
    max_tokens: int = 256
    keep_alive: str = "30m"  # keep the model resident between calls (avoids reloads)
    max_concurrency: int = 2  # in-flight local Ollama calls (1 if RAM-constrained)
    auto_save: bool = True
    auto_answer_pending: bool = True
    retry_pending_jobs: bool = True
    prompt_pending_questions: bool = False
    min_confidence: float = 0.92
    min_confidence_rag_agree: float = 0.88
    vector_agree_score: float = 0.80  # similarity floor for vector+LLM agreement
    min_confidence_new_experience: float = 0.96
    min_confidence_persist: float = 0.98
    plain_text_confidence: float = 0.35
    # Cap applied to an LLM answer's effective confidence when NO independent source
    # (RAG rule, similar past answer, or verifier) corroborates it. The raw number a
    # small model self-reports is unreliable, so an uncorroborated answer is still
    # used for the current fill but capped below the persist threshold so it is not
    # written to memory on the model's word alone.
    uncorroborated_confidence_cap: float = 0.6
    rag_agree_input_types: list[str] = field(
        default_factory=lambda: [
            "single_choice",
            "yes_no_checkbox",
            "ctc_numeric",
            "years_numeric",
            "number",
            "pincode",
            "date",
        ]
    )
    use_faiss_memory: bool = True
    rag_top_k: int = 3
    # Minimum composite similarity score a retrieved prior answer must reach to be
    # returned by retrieve_similar_answers. 0.0 disables the cutoff (return all top-k).
    rag_min_score: float = 0.5
    vector_auto_answer_score: float = 0.92
    embeddings_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    faiss_index_dir: str = "data/faiss"


@dataclass
class PlatformDelaysConfig(JsonSchemaMixin):
    """Fixed UI waits per platform (ms). Lower = faster; raise if clicks miss."""

    instahyre_ms: int = 200
    instahyre_advance_ms: int = 200
    hirist_step_ms: int = 300
    cutshort_step_ms: int = 300
    naukri_chatbot_step_ms: int = 400
    naukri_chip_poll_ms: int = 120


@dataclass
class ApplicationConfig(JsonSchemaMixin):
    jobs_per_platform: int = 0
    max_jobs_per_run: int = 0
    require_review: bool = False
    review_dir: str = "data/review"
    delay_seconds_min: int = 0
    delay_seconds_max: int = 0
    apply_workers: int = 10
    naukri_apply_workers: int = 10
    hirist_apply_workers: int = 10
    instahyre_apply_workers: int = 5  # parallel Instahyre tabs (collect+apply path)
    cutshort_apply_workers: int = 3
    indeed_apply_workers: int = 5
    parallel_platforms: bool = False  # run cookie platforms concurrently (separate browsers)
    skip_external_ats: bool = True
    dry_run: bool = False
    follow_external_from_wellfound: bool = False
    skip_location_blocked: bool = True
    skip_ineligible_salary: bool = True
    min_inr_salary_lpa: float = 25.0
    apply_retries: int = 1
    retry_backoff_ms: int = 1500
    interactive_questions: bool = True
    confirm_new_answers: bool = True
    rag_answer_questions: bool = True
    one_job_per_company: bool = True
    enrich_workers: int = 4
    pipeline_apply: bool = True
    platform_delays: PlatformDelaysConfig = field(default_factory=PlatformDelaysConfig)
    # --- v3 feature flags (SDA §A13 phased rollout) — both OFF by default so
    # existing v2 behavior (static résumé, flat retry) is unchanged until the
    # user explicitly reviews and flips these. ---
    # Resume Tailoring Subsystem (§A4). When false, the apply flow never touches
    # jobs_auto_apply/tailoring/ at all.
    resume_tailoring_enabled: bool = False
    # When tailoring is enabled: true = generate+validate a tailored résumé and
    # log what would have been uploaded, but still upload the existing static
    # resume.pdf (risk-free review mode, SDA §A13 Phase 2). Only set false once
    # you've reviewed shadow-mode output and trust it to actually swap the file.
    resume_tailoring_shadow_mode: bool = True
    # Adaptive Human-Intervention Signal Engine (§A5). When false, apply_runner's
    # original flat apply_retries/retry_backoff_ms behavior is unchanged.
    adaptive_retry_enabled: bool = False


@dataclass
class BrowserConfig(JsonSchemaMixin):
    headless: bool = False
    slow_mo_ms: int = 100
    # Which Playwright driver runs the automation: "chromium" | "firefox" | "webkit".
    # Only "chromium" supports `use_chrome_profile` (real Chrome cookie/profile reuse)
    # and `chrome_channel`; those are ignored for firefox/webkit. Note "webkit" is
    # Playwright's own WebKit build, not a way to drive real Apple Safari.app.
    engine: str = "chromium"
    # Reuse your installed Chrome profile (already signed into Google / Wellfound / Uplers)
    # — chromium engine only.
    use_chrome_profile: bool = True
    chrome_user_data_dir: str = ""  # empty = auto-detect OS default Chrome folder
    chrome_profile_name: str = "Default"  # Default, Profile 1, Profile 2, ...
    chrome_channel: str = "chrome"  # use system Google Chrome (not bundled Chromium) — chromium engine only


@dataclass
class AuthConfig(JsonSchemaMixin):
    # browser = sign in with Google once in a real browser (passkey/2FA works); session is saved
    # cookies = legacy manual cookie export
    method: str = "browser"
    sessions_dir: str = "data/sessions"
    login_timeout_seconds: int = 300


@dataclass
class WhatsAppConfig(JsonSchemaMixin):
    """Send pending questions to WhatsApp Web and read your replies.

    Uses an unofficial Playwright-driven WhatsApp Web session (no server/tunnel).
    Link once via QR (python main.py whatsapp-login); the session persists in
    profile_dir. Note: automating WhatsApp Web is against WhatsApp's ToS — use
    at your own risk with a number you're willing to expose to that risk.
    """

    enabled: bool = False
    # inline   = ask questions over WhatsApp at the end of each run (run holds the session)
    # listener = a separate `whatsapp-listen` daemon owns the session; runs just defer
    #            questions and the always-on listener asks + retries as replies arrive.
    mode: str = "inline"
    phone: str = ""  # destination number incl. country code, e.g. 919876543210 (your own = message yourself)
    profile_dir: str = "data/whatsapp_profile"
    headless: bool = False
    reply_timeout_seconds: int = 900  # how long to wait for a reply per question
    poll_interval_seconds: int = 5
    login_timeout_seconds: int = 180  # time to scan the QR on first link
    listen_idle_seconds: int = 20  # how often the listener re-checks for new pending questions
    skip_keyword: str = "skip"  # reply this to skip a question
    drop_keyword: str = "drop"  # reply this to abandon the job(s)
    ignore_keyword: str = "ignore"  # reply this to mark question N/A forever


@dataclass
class TelegramConfig(JsonSchemaMixin):
    """Send pending questions to a Telegram bot and read your replies.

    Official, free Bot API via long polling — no server, webhook, or tunnel, and
    no ToS/ban risk. Create a bot with @BotFather, paste the token here, then run
    `python main.py telegram-login` and send /start to your bot once.
    """

    enabled: bool = False
    # inline   = ask questions at the end of each run
    # listener = `serve` runs Telegram in-process; or run `telegram-listen` standalone
    mode: str = "inline"
    bot_token: str = ""
    chat_id: str = ""  # auto-captured by telegram-login if left blank
    reply_timeout_seconds: int = 900
    listen_idle_seconds: int = 20
    skip_keyword: str = "skip"
    drop_keyword: str = "drop"
    ignore_keyword: str = "ignore"
    # Message this word any time (not as a reply to a question) to have the bot
    # re-send all currently open pending questions so you can answer them now.
    pending_keyword: str = "pending"


@dataclass
class PathsConfig(JsonSchemaMixin):
    application_facts: str = "profile/application_facts.yaml"
    user_memory: str = "data/user_memory.json"
    pending_questions: str = "data/pending_questions.json"
    naukri_resume_sync: str = "data/naukri_resume_sync.json"
    hirist_resume_sync: str = "data/hirist_resume_sync.json"
    # User-specific Q&A corrections used by scripts/cleanup_user_memory.py.
    memory_corrections: str = "data/memory_corrections.json"


@dataclass
class AnswersPolicyConfig(JsonSchemaMixin):
    notice_join_threshold_days: int = 15
    default_year_chip_options: list[str] = field(
        default_factory=lambda: [
            "No experience",
            "<6 years",
            "6-8 years",
            "8+ years",
        ]
    )


@dataclass
class StateConfig(JsonSchemaMixin):
    applied_jobs_file: str = "data/applied_jobs.json"
    log_file: str = "data/run.log"
    # Periodically wipe accumulated run state (logs, applied-jobs history,
    # technical failures, pending questions) so the bot starts fresh and will
    # re-apply to jobs again. User-curated state (login sessions, learned
    # answers/user_memory, drop-keyword blocklist) is never purged.
    purge_enabled: bool = True
    purge_interval_days: int = 7


@dataclass
class WorkdayAddressConfig(JsonSchemaMixin):
    line1: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""


@dataclass
class WorkdayConfig(JsonSchemaMixin):
    password: str = ""  # used for sign-in / create-account on Workday career sites
    how_did_you_hear: str = "LinkedIn"
    skip_voluntary_disclosures: bool = True
    max_form_pages: int = 10
    address: WorkdayAddressConfig = field(default_factory=WorkdayAddressConfig)


@dataclass
class TailoringConfig(JsonSchemaMixin):
    """Resume Tailoring Subsystem knobs (SDA §A4, §A8). No effect unless
    application.resume_tailoring_enabled is true."""

    # Tier 0: cosine-similarity floor above which an existing variant is reused
    # verbatim instead of re-tailoring (§A4.1).
    reuse_threshold: float = 0.86
    # Tier 3: bounded wait for a Telegram reply to a clarifying question before
    # falling back to the safe default (omit the unresolved claim).
    clarify_timeout_seconds: int = 600
    max_clarify_questions: int = 2
    # §A4.2 step 6: bounded auto-repair loops before falling back to the base résumé.
    max_ats_repair_loops: int = 2
    # §A4.3: target must-have keyword coverage before accepting a tailored résumé.
    min_keyword_coverage: float = 0.70
    # Which local Ollama model Tier 2 uses. Empty = reuse llm.model (job-answers) —
    # see engine.py module docstring for why this project defaults to reuse
    # rather than requiring a new Modelfile.resume-tailor to be built first.
    ollama_model: str = ""
    variants_dir: str = "data/resume_variants"
    variants_db: str = "data/resume_variants.db"
    cost_ledger_db: str = "data/cost_ledger.db"
    # Underlying embeddings model — must match llm.embeddings_model (reuse the
    # one instance already loaded for answer-memory, per §A4.1; not user-configurable
    # separately on purpose to guarantee "zero new embeddings model").


@dataclass
class ResilienceConfig(JsonSchemaMixin):
    """Adaptive Human-Intervention Signal Engine knobs (SDA §A5). No effect
    unless application.adaptive_retry_enabled is true."""

    # Active hours window (local time, 24h) used for signals.time_of_day_in_active_hours.
    active_hours_start: int = 11
    active_hours_end: int = 22
    # Below this, human_recent_activity_seconds is treated as "plausibly at the
    # keyboard right now" per §A5.3's WAIT_AND_POLL branch.
    human_recent_activity_threshold_seconds: int = 300
    # Used when no real reply-latency history exists yet (see signals.py docstring
    # for why this project doesn't build a full latency-tracking store in this pass).
    default_reply_latency_seconds: int = 120
    wait_poll_max_seconds: int = 120
    # Reserved, not yet consumed: apply_runner.py's WAIT_AND_POLL currently does
    # one bounded asyncio.sleep(wait_poll_max_seconds) rather than a real re-check
    # loop (a page re-check across an await boundary risks acting on a stale
    # Playwright page reference — see apply_runner.py's _apply_decision comment).
    # This knob is for a future true polling implementation, not dead by oversight.
    wait_poll_interval_seconds: int = 5
    # Circuit breaker (§B2, formalizing the existing informal pattern).
    circuit_breaker_window_minutes: int = 30
    circuit_breaker_failure_rate_threshold: float = 0.5
    circuit_breaker_min_attempts: int = 5
    circuit_breaker_cooldown_minutes: int = 30


@dataclass
class AppConfig(JsonSchemaMixin):
    user: UserConfig
    resume: ResumeConfig
    profile: ProfileConfig
    compensation: CompensationConfig
    cover_letter: CoverLetterConfig
    cover_note: str
    # Every discovered job-board platform's {enabled, cookies_file, filters} — see
    # PlatformRuntimeConfig's docstring. Keyed by PlatformSpec.name ("wellfound",
    # "naukri", ...); load_config() populates this generically from whatever
    # platform_adapter.discover_platforms() finds, with no hardcoded platform list.
    platforms: dict[str, PlatformRuntimeConfig]
    workday: WorkdayConfig
    llm: LLMConfig
    whatsapp: WhatsAppConfig
    telegram: TelegramConfig
    application: ApplicationConfig
    browser: BrowserConfig
    auth: AuthConfig
    state: StateConfig
    paths: PathsConfig
    answers: AnswersPolicyConfig
    tailoring: TailoringConfig = field(default_factory=TailoringConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)

    def __post_init__(self) -> None:
        # base_dir is not part of the YAML config, it's derived from the config file path
        self.base_dir: Path = Path.cwd()

    @property
    def resume_path(self) -> Path:
        # A per-task override lets the resume-tailoring subsystem (v3, feature-
        # flagged off by default) swap in a JD-tailored PDF for the duration of a
        # single apply call without any site package needing to change how it
        # reads this property. See resume_context.py. No override active (the
        # default, and the only possibility while resume_tailoring_enabled is
        # false) means this is byte-for-byte the original behavior.
        from .resume_context import get_active_resume_override

        override = get_active_resume_override()
        if override is not None:
            return override
        return self.base_dir / self.resume.path

    @property
    def cover_letter_reference_path(self) -> Path:
        return self.base_dir / self.cover_letter.reference_path

    @property
    def applied_jobs_path(self) -> Path:
        return self.base_dir / self.state.applied_jobs_file

    @property
    def log_path(self) -> Path:
        return self.base_dir / self.state.log_file

    @property
    def purge_state_path(self) -> Path:
        # Tracks when run state was last purged so the 7-day cycle survives restarts.
        return self.base_dir / "data" / "purge_state.json"

    @property
    def user_memory_path(self) -> Path:
        return self.base_dir / self.paths.user_memory

    @property
    def pending_questions_path(self) -> Path:
        return self.base_dir / self.paths.pending_questions

    @property
    def naukri_resume_sync_path(self) -> Path:
        return self.base_dir / self.paths.naukri_resume_sync

    @property
    def hirist_resume_sync_path(self) -> Path:
        return self.base_dir / self.paths.hirist_resume_sync

    @property
    def memory_corrections_path(self) -> Path:
        return self.base_dir / self.paths.memory_corrections

    @property
    def resume_variants_dir(self) -> Path:
        return self.base_dir / self.tailoring.variants_dir

    @property
    def resume_variants_db_path(self) -> Path:
        return self.base_dir / self.tailoring.variants_db

    @property
    def cost_ledger_db_path(self) -> Path:
        return self.base_dir / self.tailoring.cost_ledger_db

    @property
    def whatsapp_profile_path(self) -> Path:
        p = self.base_dir / self.whatsapp.profile_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def drop_keywords_path(self) -> Path:
        # User-managed title blocklist (e.g. added by replying "drop <keyword>"
        # over Telegram). Merged into profile.skip_role_keywords at load time.
        return self.base_dir / "data" / "drop_keywords.json"

    @property
    def telegram_chat_path(self) -> Path:
        return self.base_dir / "data" / "telegram_chat.json"

    @property
    def telegram_offset_path(self) -> Path:
        # Persist the getUpdates offset so a `serve --reload` restart resumes
        # from the last processed update instead of skipping past replies that
        # arrived while the worker was reloading.
        return self.base_dir / "data" / "telegram_offset.json"

    @property
    def auth_sessions_dir(self) -> Path:
        p = self.base_dir / self.auth.sessions_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sessions_dir(self) -> Path:
        """Alias used by browser session manager."""
        return self.auth_sessions_dir

    def cookies_path(self, platform: str) -> Path:
        if platform not in self.platforms:
            raise ValueError(f"Unknown platform: {platform}")
        return self.base_dir / self.platforms[platform].cookies_file


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _user_config(data: dict[str, Any]) -> UserConfig:
    name = str(data.get("name", "")).strip()
    # Default the Wellfound nav display name to the first name when not provided.
    display = str(data.get("expected_display_name", "")).strip()
    if not display and name:
        display = name.split()[0]
    return UserConfig(
        name=name,
        email=str(data.get("email", "")),
        phone=str(data.get("phone", "")),
        linkedin=str(data.get("linkedin", "")),
        expected_display_name=display,
        github=str(data.get("github", "")),
        phone_country_code=str(data.get("phone_country_code", "")).strip(),
    )


def _platform_delays(data: dict[str, Any]) -> PlatformDelaysConfig:
    raw = data if isinstance(data, dict) else {}
    return PlatformDelaysConfig(
        instahyre_ms=int(raw.get("instahyre_ms", 200)),
        instahyre_advance_ms=int(raw.get("instahyre_advance_ms", 200)),
        hirist_step_ms=int(raw.get("hirist_step_ms", 300)),
        cutshort_step_ms=int(raw.get("cutshort_step_ms", 300)),
        naukri_chatbot_step_ms=int(raw.get("naukri_chatbot_step_ms", 400)),
        naukri_chip_poll_ms=int(raw.get("naukri_chip_poll_ms", 120)),
    )


def _platform_runtime_config(
    raw: dict[str, Any],
    key: str,
    *,
    default_cookies: str,
    filter_fn,
    enabled_default: bool = True,
) -> PlatformRuntimeConfig:
    """Generic replacement for the pre-refactor hand-written per-platform
    ``_platform_config(raw, "naukri", default_cookies=..., filter_fn=_naukri_filters)``
    call sites — same parsing behavior (including the "section missing → disabled,
    filters parsed from an empty dict so every default still applies" branch),
    just parameterized by whichever ``filter_fn``/``default_cookies`` the caller
    (now the auto-discovered ``PlatformSpec``) provides instead of one hardcoded
    function per platform.
    """
    section = _section(raw, key)
    if not section:
        return PlatformRuntimeConfig(enabled=False, cookies_file=default_cookies, filters=filter_fn({}))
    return PlatformRuntimeConfig(
        enabled=bool(section.get("enabled", enabled_default)),
        cookies_file=str(section.get("cookies_file", default_cookies)),
        filters=filter_fn(_section(section, "filters") or section),
    )


def _profile_config(data: dict[str, Any]) -> ProfileConfig:
    return ProfileConfig(
        headline=str(data.get("headline", "")),
        years_experience=int(data.get("years_experience", 0)),
        core_skills=list(data.get("core_skills", [])),
        target_roles=list(data.get("target_roles", [])),
        locations=list(data.get("locations", [])),
        skip_companies=list(data.get("skip_companies", [])),
        skip_role_keywords=list(data.get("skip_role_keywords", [])),
        keep_role_keywords=list(data.get("keep_role_keywords", [])),
        skip_role_patterns=list(data.get("skip_role_patterns", [])),
        skip_no_experience_skills=list(data.get("skip_no_experience_skills", [])),
    )


def _compensation_config(data: dict[str, Any]) -> CompensationConfig:
    return CompensationConfig(
        current_ctc_lpa=float(data.get("current_ctc_lpa", 0)),
        current_fixed_lpa=float(data.get("current_fixed_lpa", 0)),
        current_variable_lpa=float(data.get("current_variable_lpa", 0)),
        current_esops_lpa=float(data.get("current_esops_lpa", 0)),
        expected_ctc_lpa=float(data.get("expected_ctc_lpa", 0)),
    )


def _cover_letter_config(data: dict[str, Any]) -> CoverLetterConfig:
    return CoverLetterConfig(
        mode=str(data.get("mode", "dynamic")),
        include_ctc=bool(data.get("include_ctc", True)),
        max_words=int(data.get("max_words", 200)),
        reference_path=str(data.get("reference_path", "profile/cover_letter_reference.txt")),
    )


def _workday_config(data: dict[str, Any]) -> WorkdayConfig:
    addr = _section(data, "address")
    return WorkdayConfig(
        password=str(data.get("password", "")),
        how_did_you_hear=str(data.get("how_did_you_hear", "LinkedIn")),
        skip_voluntary_disclosures=bool(data.get("skip_voluntary_disclosures", True)),
        max_form_pages=int(data.get("max_form_pages", 10)),
        address=WorkdayAddressConfig(
            line1=str(addr.get("line1", "")),
            city=str(addr.get("city", "")),
            state=str(addr.get("state", "")),
            postal_code=str(addr.get("postal_code", "")),
            country=str(addr.get("country", "")),
        ),
    )


def _llm_config(data: dict[str, Any]) -> LLMConfig:
    return LLMConfig(
        enabled=bool(data.get("enabled", False)),
        base_url=str(data.get("base_url", "http://127.0.0.1:11434")).strip(),
        model=str(data.get("model", "job-answers")),
        verifier_model=str(data.get("verifier_model", "job-verify")),
        verifier_enabled=bool(data.get("verifier_enabled", True)),
        temperature=float(data.get("temperature", 0.05)),
        max_tokens=int(data.get("max_tokens", 256)),
        keep_alive=str(data.get("keep_alive", "30m")),
        max_concurrency=int(data.get("max_concurrency", 2)),
        auto_save=bool(data.get("auto_save", True)),
        auto_answer_pending=bool(data.get("auto_answer_pending", True)),
        retry_pending_jobs=bool(data.get("retry_pending_jobs", True)),
        prompt_pending_questions=bool(data.get("prompt_pending_questions", False)),
        min_confidence=float(data.get("min_confidence", 0.92)),
        min_confidence_rag_agree=float(data.get("min_confidence_rag_agree", 0.88)),
        vector_agree_score=float(data.get("vector_agree_score", 0.80)),
        min_confidence_new_experience=float(data.get("min_confidence_new_experience", 0.96)),
        min_confidence_persist=float(data.get("min_confidence_persist", 0.98)),
        plain_text_confidence=float(data.get("plain_text_confidence", 0.35)),
        uncorroborated_confidence_cap=float(data.get("uncorroborated_confidence_cap", 0.6)),
        rag_agree_input_types=list(
            data.get(
                "rag_agree_input_types",
                [
                    "single_choice",
                    "yes_no_checkbox",
                    "ctc_numeric",
                    "years_numeric",
                    "number",
                    "pincode",
                    "date",
                ],
            )
        ),
        use_faiss_memory=bool(data.get("use_faiss_memory", True)),
        rag_top_k=int(data.get("rag_top_k", 3)),
        rag_min_score=float(data.get("rag_min_score", 0.0)),
        vector_auto_answer_score=float(data.get("vector_auto_answer_score", 0.92)),
        embeddings_model=str(data.get("embeddings_model", "sentence-transformers/all-MiniLM-L6-v2")),
        faiss_index_dir=str(data.get("faiss_index_dir", "data/faiss")),
    )


def _whatsapp_config(data: dict[str, Any]) -> WhatsAppConfig:
    return WhatsAppConfig(
        enabled=bool(data.get("enabled", False)),
        mode=str(data.get("mode", "inline")).strip().lower(),
        phone=re.sub(r"\D", "", str(data.get("phone", ""))),
        profile_dir=str(data.get("profile_dir", "data/whatsapp_profile")),
        headless=bool(data.get("headless", False)),
        reply_timeout_seconds=int(data.get("reply_timeout_seconds", 900)),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 5)),
        login_timeout_seconds=int(data.get("login_timeout_seconds", 180)),
        listen_idle_seconds=int(data.get("listen_idle_seconds", 20)),
        skip_keyword=str(data.get("skip_keyword", "skip")),
        drop_keyword=str(data.get("drop_keyword", "drop")),
        ignore_keyword=str(data.get("ignore_keyword", "ignore")),
    )


def _telegram_config(data: dict[str, Any]) -> TelegramConfig:
    return TelegramConfig(
        enabled=bool(data.get("enabled", False)),
        mode=str(data.get("mode", "inline")).strip().lower(),
        bot_token=str(data.get("bot_token", "")).strip(),
        chat_id=str(data.get("chat_id", "")).strip(),
        reply_timeout_seconds=int(data.get("reply_timeout_seconds", 900)),
        listen_idle_seconds=int(data.get("listen_idle_seconds", 20)),
        skip_keyword=str(data.get("skip_keyword", "skip")),
        drop_keyword=str(data.get("drop_keyword", "drop")),
        ignore_keyword=str(data.get("ignore_keyword", "ignore")),
        pending_keyword=str(data.get("pending_keyword", "pending")),
    )


def _tailoring_config(data: dict[str, Any]) -> TailoringConfig:
    return TailoringConfig(
        reuse_threshold=float(data.get("reuse_threshold", 0.86)),
        clarify_timeout_seconds=int(data.get("clarify_timeout_seconds", 600)),
        max_clarify_questions=int(data.get("max_clarify_questions", 2)),
        max_ats_repair_loops=int(data.get("max_ats_repair_loops", 2)),
        min_keyword_coverage=float(data.get("min_keyword_coverage", 0.70)),
        ollama_model=str(data.get("ollama_model", "")),
        variants_dir=str(data.get("variants_dir", "data/resume_variants")),
        variants_db=str(data.get("variants_db", "data/resume_variants.db")),
        cost_ledger_db=str(data.get("cost_ledger_db", "data/cost_ledger.db")),
    )


def _resilience_config(data: dict[str, Any]) -> ResilienceConfig:
    return ResilienceConfig(
        active_hours_start=int(data.get("active_hours_start", 11)),
        active_hours_end=int(data.get("active_hours_end", 22)),
        human_recent_activity_threshold_seconds=int(data.get("human_recent_activity_threshold_seconds", 300)),
        default_reply_latency_seconds=int(data.get("default_reply_latency_seconds", 120)),
        wait_poll_max_seconds=int(data.get("wait_poll_max_seconds", 120)),
        wait_poll_interval_seconds=int(data.get("wait_poll_interval_seconds", 5)),
        circuit_breaker_window_minutes=int(data.get("circuit_breaker_window_minutes", 30)),
        circuit_breaker_failure_rate_threshold=float(data.get("circuit_breaker_failure_rate_threshold", 0.5)),
        circuit_breaker_min_attempts=int(data.get("circuit_breaker_min_attempts", 5)),
        circuit_breaker_cooldown_minutes=int(data.get("circuit_breaker_cooldown_minutes", 30)),
    )


def load_config(path: Path) -> AppConfig:
    """Load and parse a single config file into an AppConfig.

    Known deviation from SDA §A10 (layered base+overlay config): not
    implemented. This loads exactly one flat file — the three separate
    deployment configs (config.yaml, config.docker.yaml, config.vm.yaml) that
    motivated §A10 in the first place still exist as hand-maintained,
    fully-duplicated files. The Phase 5 implementation report flagged this
    explicitly as a deferred item (the implementation prompt's own fallback
    allowed adding new keys to the existing dataclass pattern instead, which
    is what happened here), but it wasn't annotated in-code until this
    comment — added after a follow-up SDA-conformance review flagged the
    omission. Implementing real base+overlay merging remains the recommended
    next step to actually close SDA Annexure §B1.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config file: {path}")

    base_dir = path.parent.resolve()
    user = _section(raw, "user")
    resume = _section(raw, "resume")
    application = _section(raw, "application")
    browser = _section(raw, "browser")
    auth = _section(raw, "auth")
    state = _section(raw, "state")
    workday = _workday_config(_section(raw, "workday"))
    profile = _profile_config(_section(raw, "profile"))
    compensation = _compensation_config(_section(raw, "compensation"))
    cover_letter = _cover_letter_config(_section(raw, "cover_letter"))
    llm = _llm_config(_section(raw, "llm"))
    whatsapp = _whatsapp_config(_section(raw, "whatsapp"))
    telegram = _telegram_config(_section(raw, "telegram"))
    paths_raw = _section(raw, "paths")
    answers_raw = _section(raw, "answers")
    paths = PathsConfig(
        application_facts=str(paths_raw.get("application_facts", "profile/application_facts.yaml")),
        user_memory=str(paths_raw.get("user_memory", "data/user_memory.json")),
        pending_questions=str(paths_raw.get("pending_questions", "data/pending_questions.json")),
        naukri_resume_sync=str(paths_raw.get("naukri_resume_sync", "data/naukri_resume_sync.json")),
        hirist_resume_sync=str(paths_raw.get("hirist_resume_sync", "data/hirist_resume_sync.json")),
        memory_corrections=str(paths_raw.get("memory_corrections", "data/memory_corrections.json")),
    )
    answers_policy = AnswersPolicyConfig(
        notice_join_threshold_days=int(answers_raw.get("notice_join_threshold_days", 15)),
        default_year_chip_options=list(
            answers_raw.get(
                "default_year_chip_options",
                ["No experience", "<6 years", "6-8 years", "8+ years"],
            )
        ),
    )

    # Which platform keys to look for in the YAML, and which dataclass to parse
    # each one's `filters:` block into, come from the auto-discovered PlatformSpec
    # registry (platform_adapter.discover_platforms()) instead of a hardcoded
    # per-platform if/elif chain — this is what lets platform #8 add itself to
    # config parsing just by existing as a package with `PLATFORM = PlatformSpec(...)`.
    # Imported lazily (not at module top) because platform_adapter.py's discovery
    # scan imports every platform package, and those packages import AppConfig
    # from *this* module — importing it eagerly at module scope would deadlock
    # the two modules on each other mid-definition.
    from .platform_adapter import discover_platforms

    specs = discover_platforms()

    # Legacy flat config (wellfound-only): the original tool's config.yaml had a
    # top-level `cookies:` section instead of a `wellfound:` block, and every
    # *other* platform defaulted to disabled (they didn't exist yet when this
    # format was current). Preserved byte-for-byte for old configs still using it.
    legacy_flat = "wellfound" not in raw and "cookies" in raw

    platforms: dict[str, PlatformRuntimeConfig] = {}
    for name, spec in specs.items():
        if legacy_flat and name == "wellfound":
            platforms["wellfound"] = PlatformRuntimeConfig(
                enabled=True,
                cookies_file=str(_section(raw, "cookies").get("file", "cookies.json")),
                filters=spec.parse_filters(_section(raw, "filters")),
            )
            continue
        platforms[name] = _platform_runtime_config(
            raw,
            name,
            default_cookies=spec.default_cookies_file,
            filter_fn=spec.parse_filters,
            enabled_default=(spec.filters_enabled_default if not legacy_flat else False),
        )

    config = AppConfig(
        user=_user_config(user),
        resume=ResumeConfig(
            path=str(resume.get("path", "resume.pdf")),
            sync_to_wellfound=bool(resume.get("sync_to_wellfound", False)),
            sync_to_naukri=bool(resume.get("sync_to_naukri", True)),
            naukri_sync_interval_minutes=int(resume.get("naukri_sync_interval_minutes", 30)),
            sync_to_hirist=bool(resume.get("sync_to_hirist", True)),
            hirist_sync_interval_minutes=int(resume.get("hirist_sync_interval_minutes", 30)),
        ),
        profile=profile,
        compensation=compensation,
        cover_letter=cover_letter,
        cover_note=str(raw.get("cover_note", "")).strip(),
        platforms=platforms,
        workday=workday,
        llm=llm,
        whatsapp=whatsapp,
        telegram=telegram,
        application=ApplicationConfig(
            jobs_per_platform=int(application.get("jobs_per_platform", 0)),
            max_jobs_per_run=int(application.get("max_jobs_per_run", application.get("jobs_per_platform", 0))),
            require_review=bool(application.get("require_review", False)),
            review_dir=str(application.get("review_dir", "data/review")),
            delay_seconds_min=int(application.get("delay_seconds_min", 0)),
            delay_seconds_max=int(application.get("delay_seconds_max", 0)),
            apply_workers=int(application.get("apply_workers", 10)),
            naukri_apply_workers=int(application.get("naukri_apply_workers", 10)),
            hirist_apply_workers=int(application.get("hirist_apply_workers", 10)),
            instahyre_apply_workers=int(application.get("instahyre_apply_workers", 5)),
            cutshort_apply_workers=int(application.get("cutshort_apply_workers", 3)),
            indeed_apply_workers=int(application.get("indeed_apply_workers", 5)),
            parallel_platforms=bool(application.get("parallel_platforms", False)),
            skip_external_ats=bool(application.get("skip_external_ats", True)),
            dry_run=bool(application.get("dry_run", False)),
            follow_external_from_wellfound=bool(application.get("follow_external_from_wellfound", False)),
            skip_location_blocked=bool(application.get("skip_location_blocked", True)),
            skip_ineligible_salary=bool(application.get("skip_ineligible_salary", True)),
            min_inr_salary_lpa=float(application.get("min_inr_salary_lpa", 25)),
            apply_retries=int(application.get("apply_retries", 1)),
            retry_backoff_ms=int(application.get("retry_backoff_ms", 1500)),
            interactive_questions=bool(application.get("interactive_questions", True)),
            confirm_new_answers=bool(application.get("confirm_new_answers", True)),
            rag_answer_questions=bool(application.get("rag_answer_questions", True)),
            one_job_per_company=bool(application.get("one_job_per_company", True)),
            enrich_workers=int(application.get("enrich_workers", 4)),
            pipeline_apply=bool(application.get("pipeline_apply", True)),
            platform_delays=_platform_delays(application.get("platform_delays", {})),
            resume_tailoring_enabled=bool(application.get("resume_tailoring_enabled", False)),
            resume_tailoring_shadow_mode=bool(application.get("resume_tailoring_shadow_mode", True)),
            adaptive_retry_enabled=bool(application.get("adaptive_retry_enabled", False)),
        ),
        browser=BrowserConfig(
            headless=bool(browser.get("headless", False)),
            slow_mo_ms=int(browser.get("slow_mo_ms", 100)),
            engine=str(browser.get("engine", "chromium")),
            use_chrome_profile=bool(browser.get("use_chrome_profile", True)),
            chrome_user_data_dir=str(browser.get("chrome_user_data_dir", "")),
            chrome_profile_name=str(browser.get("chrome_profile_name", "Default")),
            chrome_channel=str(browser.get("chrome_channel", "chrome")),
        ),
        auth=AuthConfig(
            method=str(auth.get("method", "browser")),
            sessions_dir=str(auth.get("sessions_dir", "data/sessions")),
            login_timeout_seconds=int(auth.get("login_timeout_seconds", 300)),
        ),
        state=StateConfig(
            applied_jobs_file=str(state.get("applied_jobs_file", "data/applied_jobs.json")),
            log_file=str(state.get("log_file", "data/run.log")),
            purge_enabled=bool(state.get("purge_enabled", True)),
            purge_interval_days=int(state.get("purge_interval_days", 7)),
        ),
        paths=paths,
        answers=answers_policy,
        tailoring=_tailoring_config(_section(raw, "tailoring")),
        resilience=_resilience_config(_section(raw, "resilience")),
    )
    # Set base_dir explicitly
    config.base_dir = base_dir

    # Merge the user's persisted drop-keyword blocklist (e.g. added by replying
    # "drop <keyword>" over Telegram) into the role-skip keywords so every future
    # run filters those titles out, exactly like config-defined skip_role_keywords.
    from .drop_keywords import load_drop_keywords

    extra_skip = load_drop_keywords(config)
    if extra_skip:
        existing = {k.strip().lower() for k in config.profile.skip_role_keywords}
        config.profile.skip_role_keywords.extend(k for k in extra_skip if k.lower() not in existing)
    return config
