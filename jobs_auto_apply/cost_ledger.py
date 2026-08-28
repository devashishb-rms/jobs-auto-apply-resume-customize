"""Append-only LLM call ledger (SDA §A7, §B3).

One SQLite table (``data/cost_ledger.db``), one row per LLM invocation.
Every generative call in this design runs on local Ollama, so
``estimated_cost_usd`` is structurally 0 for the default configuration — the
ledger's real value is compute-time and call-count *observability* (is
tailoring calling the LLM more than expected? is one platform's chatbot
triggering excessive Tier-2 calls?), not cost accounting in the cloud-LLM
sense. The schema still carries ``estimated_cost_usd`` per-call so this
generalizes cleanly if a future config ever swaps in a paid model, without a
schema change.

Deliberately minimal — one table, no aggregation service, matching the
project's cost-consciousness at every other layer (§B3).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("job_apply")

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    module TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    job_key TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_module ON llm_calls(module);
CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp ON llm_calls(timestamp);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def record_call(
    config,
    *,
    module: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    estimated_cost_usd: float = 0.0,
    job_key: str = "",
) -> None:
    """Append one row. Never raises — a ledger write must never break a call it's observing."""
    try:
        db_path = config.cost_ledger_db_path
        with _lock, _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO llm_calls (timestamp, module, model, input_tokens, output_tokens, "
                "duration_ms, estimated_cost_usd, job_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    module,
                    model,
                    int(input_tokens),
                    int(output_tokens),
                    int(duration_ms),
                    float(estimated_cost_usd),
                    job_key,
                ),
            )
    except Exception:
        logger.debug("cost_ledger.record_call failed (non-fatal)", exc_info=True)


def summary(config) -> dict:
    """Total calls / compute time / cost, grouped by module — §A7's target metrics."""
    db_path = config.cost_ledger_db_path
    if not db_path.exists():
        return {"total_calls": 0, "total_duration_ms": 0, "total_cost_usd": 0.0, "by_module": {}}
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT module, COUNT(*), SUM(duration_ms), SUM(estimated_cost_usd) FROM llm_calls GROUP BY module"
            ).fetchall()
    except Exception:
        logger.debug("cost_ledger.summary failed (non-fatal)", exc_info=True)
        return {"total_calls": 0, "total_duration_ms": 0, "total_cost_usd": 0.0, "by_module": {}}

    by_module = {
        module: {"calls": count, "duration_ms": dur or 0, "cost_usd": cost or 0.0} for module, count, dur, cost in rows
    }
    return {
        "total_calls": sum(m["calls"] for m in by_module.values()),
        "total_duration_ms": sum(m["duration_ms"] for m in by_module.values()),
        "total_cost_usd": sum(m["cost_usd"] for m in by_module.values()),
        "by_module": by_module,
    }


def cost_report_text(config) -> str:
    data = summary(config)
    if data["total_calls"] == 0:
        return "No LLM calls recorded yet (data/cost_ledger.db is empty or missing)."
    lines = [
        f"Total LLM calls: {data['total_calls']}",
        f"Total compute time: {data['total_duration_ms'] / 1000:.1f}s",
        f"Total estimated cloud cost: ${data['total_cost_usd']:.4f} (always $0 for local-only Ollama calls)",
        "",
        "By module:",
    ]
    for module, m in sorted(data["by_module"].items(), key=lambda kv: -kv[1]["calls"]):
        lines.append(f"  {module}: {m['calls']} call(s), {m['duration_ms'] / 1000:.1f}s, ${m['cost_usd']:.4f}")
    return "\n".join(lines)
