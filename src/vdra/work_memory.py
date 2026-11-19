"""Stable vdra command and OpenViking startup entry point."""

import asyncio
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from . import vdra_state as storage
from .recall_observability import OBSERVATION, note_problem, observe, timed_await
from .vdra_recall import (
    REQUEST,
    REQUEST_DEADLINE,
    assemble_work_context,
    prepare_prefetched,
)
from .vdra_state import ROOT as ROOT
from .vdra_state import atomic_json as atomic_json
from .vdra_state import clean as clean
from .vdra_state import cli

REVISION = "vdra-0.1.0a2"
MAX_CONTEXT_TOKENS = 1500


def install_session_hooks(SessionService, Session):
    native_session = SessionService.session
    native_commit = Session.commit_async

    def session(self, ctx, session_id=None, *, session_uri=None):
        cfg = storage.config()
        # Resolve local aliases even with recall disabled to avoid reviving the old backlog.
        if (
            not session_uri
            and ctx.account_id == cfg.get("account")
            and ctx.user.user_id == cfg.get("user")
        ):
            session_id = cfg.get("session_aliases", {}).get(session_id, session_id)
        return native_session(self, ctx, session_id, session_uri=session_uri)

    async def commit(self, *args, **kwargs):
        cfg = storage.config()
        if (
            storage.enabled(self.ctx, cfg)
            and storage.SESSION_ID.fullmatch(self.session_id)
            and cfg.get("native_working_memory")
            and kwargs.get("enable_working_memory") is None
        ):
            kwargs["enable_working_memory"] = True
        return await native_commit(self, *args, **kwargs)

    SessionService.session = session
    Session.commit_async = commit


def install_keyword_hint(SearchService, endpoint):
    native_check = SearchService.ensure_keywords_search_supported

    async def check(self, ctx):
        try:
            await native_check(self, ctx)
        except endpoint.InvalidArgumentError as exc:
            if storage.enabled(ctx) and str(exc).startswith(
                "Keyword search is not supported"
            ):
                raise endpoint.InvalidArgumentError(
                    "Ranked keyword search is unavailable on this backend. Use grep with a scoped "
                    "viking:// URI for exact text, or search_type='semantic' for ranked retrieval."
                ) from exc
            raise

    SearchService.ensure_keywords_search_supported = check
    for tool in endpoint.mcp._tool_manager.list_tools():
        if tool.name == "search":
            tool.description += (
                " Ranked keyword mode requires a supported full-text backend."
                " On the local backend, use scoped grep for exact text."
            )
            break


def install_context_hooks(pipeline, *entrypoints):
    from openviking.retrieve.context_assembler.models import AssembleResult
    from openviking_cli.utils.logger import get_logger

    native_assemble = pipeline.assemble_context
    native_gather = pipeline.gather_candidates
    native_prefetch = pipeline.prefetch_contents

    async def gather(**kwargs):
        if REQUEST.get() is None:
            return await native_gather(**kwargs)
        return await timed_await("native_candidate_gather", native_gather(**kwargs))

    async def prefetch(**kwargs):
        if REQUEST.get() is None:
            return await native_prefetch(**kwargs)
        return await timed_await(
            "native_candidate_read", prepare_prefetched(native_prefetch, **kwargs)
        )

    async def assemble(**kwargs):
        ctx, params = kwargs["ctx"], kwargs["params"]
        if params.search_type == "keywords" or not storage.enabled(ctx):
            return await native_assemble(**kwargs)

        if params.max_tokens > MAX_CONTEXT_TOKENS:
            kwargs["params"] = replace(params, max_tokens=MAX_CONTEXT_TOKENS)

        start = time.monotonic()
        request_id = uuid.uuid4().hex
        observation = {"problems": []}
        observation_token = OBSERVATION.set(observation)
        result = None
        try:
            # Leave headroom for the client before it can fall back to unfiltered search.
            budget = (
                max(0.1, float(storage.config().get("context_deadline_seconds", 3.0)))
                - 0.1
            )
            deadline = start + budget
            deadline_token = REQUEST_DEADLINE.set(deadline)
            try:
                result = await asyncio.wait_for(
                    assemble_work_context(native_assemble, **kwargs),
                    timeout=max(0.01, deadline - time.monotonic()),
                )
            finally:
                REQUEST_DEADLINE.reset(deadline_token)
        except Exception as exc:
            get_logger(__name__).warning(
                "Work context unavailable (%s); omitting automatic context",
                type(exc).__name__,
            )
            timed_out = isinstance(exc, TimeoutError)
            note_problem("server_deadline" if timed_out else "context_assembly", exc)
            result = AssembleResult(
                stats={
                    "used_tokens": 0,
                    "empty_reason": "precision_gate_timeout"
                    if timed_out
                    else "precision_gate_unavailable",
                }
            )
        finally:
            try:
                if result is not None:
                    record = observe(
                        storage.ROOT,
                        request_id,
                        params.session_id,
                        (time.monotonic() - start) * 1000,
                        result.stats,
                        len(result.entries),
                        observation["problems"],
                    )
                    result.stats["recall_outcome"] = {
                        **{
                            key: record[key]
                            for key in ("status", "request_id", "elapsed_ms")
                        },
                        "adapter_revision": REVISION,
                    }
            finally:
                OBSERVATION.reset(observation_token)
        return result

    assemble._work_memory = True
    pipeline.gather_candidates = gather
    pipeline.prefetch_contents = prefetch
    for module in (pipeline, *entrypoints):
        module.assemble_context = assemble


def install():
    import openviking.retrieve.context_assembler as assembly
    from openviking.retrieve.context_assembler import pipeline
    from openviking.server import mcp_endpoint
    from openviking.server.routers import search as search_router
    from openviking.service.search_service import SearchService
    from openviking.service.session_service import SessionService
    from openviking.session.session import Session

    if getattr(pipeline.assemble_context, "_work_memory", False):
        return

    install_keyword_hint(SearchService, mcp_endpoint)
    install_session_hooks(SessionService, Session)

    cfg = storage.config()
    if cfg.get("account") and cfg.get("user"):
        from openviking.storage.viking_fs import VikingFS

        from .memory_edit_guard import install as install_edit_guard
        from .memory_structure_guard import install as install_structure_guard
        from .project_card_guard import install as install_project_card_guard

        identity = {"account_id": cfg["account"], "user_id": cfg["user"]}
        install_project_card_guard(VikingFS, **identity)
        install_structure_guard(VikingFS, **identity)
        fact_uris = [
            p["facts_uri"]
            for p in cfg.get("projects", {}).values()
            if p.get("facts_uri")
        ]
        install_edit_guard(mcp_endpoint, **identity, fact_uris=fact_uris)

    install_context_hooks(pipeline, assembly, search_router, mcp_endpoint)
    atomic_json(
        storage.ROOT / "maintenance-runtime.json",
        {
            "revision": REVISION,
            "pid": os.getpid(),
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "project_card_body_limit_bytes": 8192,
            "overflow_action": "archive_then_refuse_unreviewed_main_write",
            "privacy_structure_guard": True,
        },
    )


if __name__ == "__main__":
    cli()
