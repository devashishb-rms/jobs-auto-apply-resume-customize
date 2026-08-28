"""Resume Variant Library — SQLite similarity store (SDA §A8).

SRP: this module only reads/writes the variant library. It knows nothing
about Playwright, HTTP, or tailoring generation logic.

Uses the same ``sentence-transformers/all-MiniLM-L6-v2`` embeddings model v2
already loads in ``embeddings.py`` for answer-memory (§A4.1) — no second
embeddings model is added. Chosen over v2's usual JSON-file pattern
deliberately: this table needs similarity search over structured metadata and
will grow to hundreds of rows (§A8's own documented exception).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import AppConfig
from ..embeddings import get_embeddings
from ..profile_data import load_resume_facts
from .jd_extractor import StructuredJD

logger = logging.getLogger("job_apply")

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_variants (
    variant_id TEXT PRIMARY KEY,
    structured_jd_json TEXT NOT NULL,
    jd_embedding BLOB NOT NULL,
    resume_pdf_path TEXT NOT NULL,
    ats_keyword_coverage REAL NOT NULL,
    base_resume_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reuse_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    tags TEXT
);
CREATE INDEX IF NOT EXISTS idx_base_resume_version ON resume_variants(base_resume_version);
"""


@dataclass
class VariantMatch:
    variant_id: str
    resume_pdf_path: Path
    ats_keyword_coverage: float
    similarity: float


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def base_resume_version(config: AppConfig) -> str:
    """Hash of resume_facts.yaml content — invalidates stale variants
    automatically when the source résumé changes (§A8)."""
    path = config.base_dir / "profile" / "resume_facts.yaml"
    try:
        raw = path.read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(raw).hexdigest()[:16]


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_structured_jd(structured_jd: StructuredJD, config: AppConfig) -> list[float] | None:
    emb = get_embeddings(config.llm.embeddings_model)
    if emb is None:
        return None
    try:
        return emb.embed_query(structured_jd.embedding_text())
    except Exception:
        logger.warning("variant_store: embedding failed", exc_info=True)
        return None


def find_similar(structured_jd: StructuredJD, config: AppConfig) -> VariantMatch | None:
    """Tier 0 (§A4.1): cosine-search variants with the CURRENT base_resume_version.
    Old variants remain in the table (audit trail) but are never matched once
    resume_facts.yaml changes — zero explicit cache-invalidation logic (§A8)."""
    vector = embed_structured_jd(structured_jd, config)
    if vector is None:
        return None
    version = base_resume_version(config)
    db_path = config.resume_variants_db_path
    if not db_path.exists():
        return None

    threshold = config.tailoring.reuse_threshold
    best: VariantMatch | None = None
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT variant_id, jd_embedding, resume_pdf_path, ats_keyword_coverage "
                "FROM resume_variants WHERE base_resume_version = ?",
                (version,),
            ).fetchall()
    except Exception:
        logger.warning("variant_store: find_similar query failed", exc_info=True)
        return None

    for variant_id, blob, pdf_path, coverage in rows:
        sim = _cosine(vector, _unpack(blob))
        if sim >= threshold and (best is None or sim > best.similarity):
            best = VariantMatch(
                variant_id=variant_id,
                resume_pdf_path=Path(pdf_path),
                ats_keyword_coverage=coverage,
                similarity=sim,
            )
    return best


def mark_reused(variant_id: str, config: AppConfig) -> None:
    db_path = config.resume_variants_db_path
    if not db_path.exists():
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                "UPDATE resume_variants SET reuse_count = reuse_count + 1, last_used_at = ? WHERE variant_id = ?",
                (now, variant_id),
            )
    except Exception:
        logger.debug("variant_store: mark_reused failed (non-fatal)", exc_info=True)


def save(
    structured_jd: StructuredJD,
    *,
    resume_pdf_path: Path,
    ats_keyword_coverage: float,
    config: AppConfig,
    tags: str = "",
) -> str | None:
    vector = embed_structured_jd(structured_jd, config)
    if vector is None:
        return None
    version = base_resume_version(config)
    variant_id = hashlib.sha256(
        (structured_jd.embedding_text() + version).encode("utf-8")
    ).hexdigest()[:24]
    now = datetime.now(timezone.utc).isoformat()
    db_path = config.resume_variants_db_path
    try:
        with _lock, _connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resume_variants "
                "(variant_id, structured_jd_json, jd_embedding, resume_pdf_path, ats_keyword_coverage, "
                "base_resume_version, created_at, reuse_count, last_used_at, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT reuse_count FROM resume_variants WHERE variant_id = ?), 0), ?, ?)",
                (
                    variant_id,
                    json.dumps(
                        {
                            "must_have": structured_jd.must_have,
                            "nice_to_have": structured_jd.nice_to_have,
                            "keywords": structured_jd.keywords,
                            "seniority": structured_jd.seniority,
                            "tone": structured_jd.tone,
                        }
                    ),
                    _pack(vector),
                    str(resume_pdf_path),
                    float(ats_keyword_coverage),
                    version,
                    now,
                    variant_id,
                    now,
                    tags,
                ),
            )
    except Exception:
        logger.warning("variant_store: save failed", exc_info=True)
        return None
    return variant_id
