"""Bounded recall outcome logging, without prompts, memory bodies or credentials."""

from __future__ import annotations
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

OBSERVATION = ContextVar("recall_observation", default=None)
_LOGGERS = {}


def note_problem(stage, exc):
    current = OBSERVATION.get()
    if current is not None:
        current.setdefault("problems", []).append(
            {"stage": stage, "error_type": type(exc).__name__}
        )


async def timed_await(stage, awaitable):
    """Time one awaited phase; keep only stage names and elapsed durations."""
    current = OBSERVATION.get()
    started = time.monotonic()
    previous = current.get("active_stage", "") if current is not None else ""
    if current is not None:
        current["active_stage"] = stage
    try:
        return await awaitable
    finally:
        if current is not None:
            phases = current.setdefault("phase_ms", {})
            phases[stage] = round(
                phases.get(stage, 0.0) + (time.monotonic() - started) * 1000, 2
            )
            current["active_stage"] = previous


def outcome(stats, count, problems=()):
    reason = stats.get("empty_reason") or stats.get("work_memory", {}).get(
        "empty_reason", ""
    )
    if reason == "precision_gate_timeout":
        return "server_deadline"
    if stats.get("work_memory", {}).get("degraded_timeout"):
        return "degraded_timeout"
    if reason == "precision_gate_unavailable" or stats.get("retrieval_errors"):
        return "retrieval_error"
    if any(p["stage"] == "lexical_index" for p in problems):
        return "index_error"
    if problems:
        return "retrieval_error"
    if count:
        return "hit"
    if stats.get("work_memory", {}).get("repeat_suppressed", 0):
        return "empty_dedup"
    if reason in {"no_historical_subject", "requires_live_evidence"}:
        return "empty_policy"
    return "empty_no_match"


def observe(root: Path, request_id, session_id, elapsed_ms, stats, count, problems=()):
    status = outcome(stats, count, problems)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "recall_result",
        "status": status,
        "request_id": request_id,
        "session_id": session_id
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id or "")
        else "",
        "elapsed_ms": round(elapsed_ms, 2),
        "used_tokens": int(stats.get("used_tokens", 0)),
        "entries": count,
    }
    # Native retrieval_errors may contain full provider replies. Retain count
    # and exception class only; never copy their message into the diagnostic.
    if stats.get("retrieval_errors"):
        errors = stats["retrieval_errors"]
        record["retrieval_error_count"] = (
            len(errors) if isinstance(errors, (list, tuple)) else 1
        )
    if problems:
        record["error_type"] = problems[0]["error_type"]
        record["problem_stages"] = sorted({p["stage"] for p in problems})
    if stats.get("work_memory", {}).get("degraded_timeout"):
        record["degraded_timeout"] = stats["work_memory"]["degraded_timeout"]
    phase_ms = OBSERVATION.get()
    if isinstance(phase_ms, dict):
        if phase_ms.get("phase_ms"):
            record["phase_ms"] = phase_ms["phase_ms"]
        if phase_ms.get("active_stage"):
            record["active_stage_at_exit"] = phase_ms["active_stage"]
    try:
        path = root / "logs/work-recall.jsonl"
        key = str(path)
        if key not in _LOGGERS:
            path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.Logger("owned-openviking-recall", logging.INFO)
            handler = RotatingFileHandler(
                path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            _LOGGERS[key] = logger
        _LOGGERS[key].info(json.dumps(record, ensure_ascii=False))
    except OSError:
        # Diagnostics must not turn a successful recall into an application error.
        pass
    return record
