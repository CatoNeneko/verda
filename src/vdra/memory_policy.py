"""Reversible recall-only retention. No model calls, deletion or vector writes."""

import asyncio
from dataclasses import replace
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
import json
import math

from .vdra_recall import REQUEST, rank_candidates
from .vdra_state import ROOT
import re

RECOVERED = ContextVar("recovered_memories", default=None)
POLICY = ROOT / "retention-policy.json"
IMPORTANT = re.compile(
    r"长期|重要|牢记|记住|偏好|决定|决策|结论|原因|教训|经验|禁止|暂停|封存|不要|不得|必须|以后|始终|训练结束后|关闭.*停止",
    re.I,
)
ORDINARY = re.compile(r"草稿|临时讨论|随口|中间方案|排障尝试|测试输出|临时日志", re.I)
TEMPORARY = re.compile(
    r"正在训练|训练正在|正在运行|当前分支|临时状态|进行中的任务|training is running|currently running",
    re.I,
)


def decide(category, text, uri, created, now, override=None):
    override = override or {}
    kind = override.get("kind")
    if override.get("protected") or kind == "protected":
        return "protected", 1.0
    if kind is None:
        # Unknown categories and mixed memories are preserved by default.
        if category != "events" or IMPORTANT.search(text):
            return "protected", 1.0
        if TEMPORARY.search(text) and len(text) <= 600:
            kind = "temporary"
        elif ORDINARY.search(text):
            kind = "ordinary"
        else:
            return "protected", 1.0
    if kind == "protected":
        return "protected", 1.0
    expires = override.get("expires_at")
    if expires:
        deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if now >= deadline:
            return "expired", 0.0
    if created is None:
        return "unknown", 1.0
    age = max(0, (now - created).total_seconds() / 86400)
    if kind == "temporary":
        return (
            ("expired", 0.0) if age >= override.get("ttl_days", 1) else ("active", 1.0)
        )
    if kind != "ordinary":
        return "unknown", 1.0
    if age >= override.get("archive_days", 90):
        return "archived", 0.0
    return "active", max(
        0.25, math.exp(-math.log(2) * age / override.get("half_life_days", 30))
    )


def install():
    from openviking.retrieve.context_assembler import pipeline
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    original = pipeline.gather_candidates
    if getattr(original, "_retention_policy", False):
        return

    async def gather(**kwargs):
        # Overfetch within the existing search to let eligible memories fill gaps.
        request = dict(kwargs)
        request["limit"] = min(60, max(1, kwargs["limit"]) * 3)
        if kwargs.get("quotas"):
            request["quotas"] = {k: min(30, v * 3) for k, v in kwargs["quotas"].items()}
        candidates, stats = await original(**request)
        try:
            config = json.loads(POLICY.read_text()) if POLICY.exists() else {}
        except (OSError, ValueError):
            return candidates[: kwargs["limit"]], stats
        limit = min(
            kwargs["limit"],
            max(1, int(config.get("context_max_items", kwargs["limit"]))),
        )
        provenance_file = POLICY.parent / "history-memory-uris.json"
        try:
            imported = (
                json.loads(provenance_file.read_text())
                if provenance_file.exists()
                else {}
            )
        except (OSError, ValueError):
            imported = {}
        protection_file = POLICY.parent / "history-protected-uris.json"
        try:
            protected = (
                json.loads(protection_file.read_text())
                if protection_file.exists()
                else {}
            )
        except (OSError, ValueError):
            protected = {}
        recovered = {}
        RECOVERED.set(recovered)
        if not config.get("enabled", True):
            return candidates[:limit], stats
        from .recall_policy import should_recover_history

        plan = (REQUEST.get() or {}).get("plan")
        historical = (
            plan.recover_history
            if plan is not None
            else any(should_recover_history(q) for q in kwargs["queries"])
        )
        semaphore = asyncio.Semaphore(2)
        counts = {
            "protected": 0,
            "expired": 0,
            "archived": 0,
            "active": 0,
            "unknown": 0,
        }
        now = datetime.now(timezone.utc)

        async def apply(c):
            if "/memories/" not in c.base_uri:
                return c
            if c.is_directory:
                return None if c.category == "events" else c
            # Non-event categories cannot decay without an exact URI override.
            override = {
                "ttl_days": config.get("temporary_default_ttl_days", 1),
                "archive_days": config.get("ordinary_archive_days", 90),
                "half_life_days": config.get("ordinary_half_life_days", 30),
                **config.get("overrides", {}).get(c.base_uri, {}),
            }
            if c.base_uri in protected:
                override["protected"] = True
            if c.category != "events" and c.base_uri not in config.get("overrides", {}):
                counts["protected"] += 1
                return c
            try:
                async with semaphore:
                    raw = await kwargs["service"].fs.read(c.base_uri, ctx=c.read_ctx)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                memory = MemoryFileUtils.read(raw, uri=c.base_uri)
                created = memory.extra_fields.get("created_at")
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created is None:
                    event_date = re.search(
                        r"/events/(\d{4})/(\d{2})/(\d{2})/", c.base_uri
                    )
                    if event_date:
                        created = datetime(
                            *(int(x) for x in event_date.groups()),
                            tzinfo=timezone(timedelta(hours=8)),
                        )
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                state, factor = decide(
                    c.category, memory.content, c.base_uri, created, now, override
                )
            except Exception:
                state, factor = "unknown", 1.0
            if c.base_uri in imported:
                recovered[c.base_uri] = "historical-import"
                c = replace(c, abstract="[历史导入，需按原始时间理解] " + c.abstract)
            counts[state] += 1
            if state in ("expired", "archived"):
                if not historical:
                    return None
                # Explicitly label recovered states so they cannot look current.
                recovered[c.base_uri] = state
                return replace(
                    c, abstract=f"[历史记忆：{state}，不代表当前状态] " + c.abstract
                )
            return replace(
                c, ranked_score=c.ranked_score - abs(c.ranked_score) * (1 - factor)
            )

        filtered = [
            c for c in await asyncio.gather(*(apply(c) for c in candidates)) if c
        ]
        filtered.sort(key=lambda c: c.ranked_score, reverse=True)
        filtered = rank_candidates(filtered)
        stats["retention"] = {
            **counts,
            "historical_lookup": historical,
            "permanent_delete": False,
        }
        return filtered[:limit], stats

    original_prefetch = pipeline.prefetch_contents

    async def prefetch(**kwargs):
        contents = await original_prefetch(**kwargs)
        for uri, state in (RECOVERED.get() or {}).items():
            if uri in contents:
                marker = f"[历史记忆：{state}，不代表当前状态]"
                body, count = re.subn(
                    r"(?im)^(#{1,3}[ \t]+Summary[ \t]*\n|Summary:[ \t]*)",
                    lambda m: m.group(0) + marker + "\n",
                    contents[uri],
                    count=1,
                )
                contents[uri] = body if count else marker + "\n" + body
        return contents

    pipeline.prefetch_contents = prefetch
    gather._retention_policy = True
    pipeline.gather_candidates = gather
