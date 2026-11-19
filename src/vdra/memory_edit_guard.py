"""Owner-installed CAS guard and bounded lock wait for memory MCP edits."""

from __future__ import annotations

import hashlib
import asyncio
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Iterable

HINT_THRESHOLD_BYTES = 8 * 1024
EDIT_LOCK_WAIT_SECONDS = 2.0
# Scope by native client, exact target and executing task. Context inherited by
# indexing/background tasks must not change their native locking behavior.
_LOCK_WAIT_SCOPE = ContextVar("memory_edit_lock_wait_scope", default=None)
_APPEND_LOCK_STATE = ContextVar("memory_append_lock_state", default=None)
_LOGGER = logging.getLogger(__name__)
ORGANIZATION_HINT = (
    " The project fact card is over 8 KiB; consider organizing it by topic after "
    "the current edits finish. The requested edit was written successfully."
)


@contextmanager
def _edit_lock_wait(fs: Any, uri: str, ctx: Any, *, api: str = "batch"):
    vfs = getattr(fs, "_viking_fs", None)
    if vfs is None:
        yield
        return
    client = vfs._async_agfs
    path = vfs._uri_to_path(uri, ctx=ctx)
    token = _LOCK_WAIT_SCOPE.set((client, (path,), asyncio.current_task(), uri, api))
    try:
        yield
    finally:
        _LOCK_WAIT_SCOPE.reset(token)


def make_lock_wait(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wait at acquisition only, before native CAS validation or content I/O.

    Never replay batch_write: an exception after its commit is ambiguous. Native
    lock cancellation cleanup, ACL checks, CAS, and lease handoff remain intact.
    """

    @wraps(original)
    async def acquire(
        self, paths, timeout_secs=0.0, owner_lease_ref=None, *, fs_ctx=None
    ):
        scope = _LOCK_WAIT_SCOPE.get()
        scoped = (
            scope is not None
            and scope[0] is self
            and scope[1] == tuple(paths)
            and scope[2] is asyncio.current_task()
            and timeout_secs == 0.0
            and owner_lease_ref is None
            and scope[4] == "batch"
        )
        if not scoped:
            return await original(
                self, paths, timeout_secs, owner_lease_ref, fs_ctx=fs_ctx
            )
        from openviking.storage.errors import LockAcquisitionError

        start = time.monotonic()
        try:
            lease = await original(
                self, paths, EDIT_LOCK_WAIT_SECONDS, owner_lease_ref, fs_ctx=fs_ctx
            )
        except LockAcquisitionError as exc:
            exc._memory_edit_lock_wait_ms = int(EDIT_LOCK_WAIT_SECONDS * 1000)
            _LOGGER.warning(
                "memory_edit_lock status=busy uri=%s elapsed_ms=%d",
                scope[3],
                round((time.monotonic() - start) * 1000),
            )
            raise
        elapsed_ms = round((time.monotonic() - start) * 1000)
        if elapsed_ms >= 50:
            _LOGGER.info(
                "memory_edit_lock status=acquired uri=%s elapsed_ms=%d",
                scope[3],
                elapsed_ms,
            )
        return lease

    acquire._memory_edit_lock_wait = True
    return acquire


def make_single_lock_wait(original: Callable[..., Any]) -> Callable[..., Any]:
    """Apply the same bounded wait to the explicitly scoped append's exact lock."""

    @wraps(original)
    async def acquire(
        self, path, timeout_secs=0.0, owner_lease_ref=None, *, fs_ctx=None
    ):
        scope = _LOCK_WAIT_SCOPE.get()
        append_state = _APPEND_LOCK_STATE.get()
        scoped = (
            scope is not None
            and scope[0] is self
            and scope[1] == (path,)
            and scope[2] is asyncio.current_task()
            and scope[4] == "single"
            and append_state is not None
            and append_state["armed"]
            and not append_state["acquired"]
            and timeout_secs == 0.0
            and owner_lease_ref is None
        )
        if not scoped:
            return await original(
                self, path, timeout_secs, owner_lease_ref, fs_ctx=fs_ctx
            )
        from openviking.storage.errors import LockAcquisitionError

        start = time.monotonic()
        try:
            lease = await original(
                self, path, EDIT_LOCK_WAIT_SECONDS, owner_lease_ref, fs_ctx=fs_ctx
            )
        except LockAcquisitionError as exc:
            exc._memory_edit_lock_wait_ms = int(EDIT_LOCK_WAIT_SECONDS * 1000)
            append_state["failed"] = True
            _LOGGER.warning(
                "memory_edit_lock status=busy uri=%s elapsed_ms=%d",
                scope[3],
                round((time.monotonic() - start) * 1000),
            )
            raise
        elapsed_ms = round((time.monotonic() - start) * 1000)
        append_state["acquired"] = True
        if elapsed_ms >= 50:
            _LOGGER.info(
                "memory_edit_lock status=acquired uri=%s elapsed_ms=%d",
                scope[3],
                elapsed_ms,
            )
        return lease

    acquire._memory_edit_lock_wait = True
    return acquire


def install_lock_wait() -> None:
    from openviking.pyagfs import AsyncAGFSClient

    original = AsyncAGFSClient.pathlock_acquire_exact_batch
    if getattr(original, "_memory_edit_lock_wait", False) is not True:
        AsyncAGFSClient.pathlock_acquire_exact_batch = make_lock_wait(original)
    original_single = AsyncAGFSClient.pathlock_acquire_exact
    if getattr(original_single, "_memory_edit_lock_wait", False) is not True:
        AsyncAGFSClient.pathlock_acquire_exact = make_single_lock_wait(original_single)


def _raw_content(result: Any) -> tuple[str, str | bytes]:
    """Normalize FSService._read_content's (resolved_uri, raw_content) result."""
    if not isinstance(result, tuple) or len(result) != 2:
        raise TypeError("fs._read_content must return (resolved_uri, raw_content)")
    resolved_uri, content = result
    if not isinstance(resolved_uri, str) or not isinstance(content, (str, bytes)):
        raise TypeError("fs._read_content returned an unsupported snapshot")
    return resolved_uri, content


def _raw_bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _parent_uri(uri: str) -> str:
    parent, sep, _name = uri.rpartition("/")
    if not sep or not parent:
        raise ValueError("memory file URI has no parent directory")
    return parent


def _conflict(conflict_error: type[Exception], uri: str, reason: str) -> Exception:
    return conflict_error(
        f"Memory file changed during edit ({reason}); re-read it and retry: {uri}"
    )


def make_guarded_edit(
    original: Callable[..., Any],
    *,
    endpoint: Any,
    account_id: str,
    user_id: str,
    fact_uris: Iterable[str] = (),
) -> Callable[..., Any]:
    """Build a signature-compatible edit handler around the existing endpoint."""
    fact_uri_set = frozenset(fact_uris)

    @wraps(original)
    async def guarded_edit(
        uri: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        wait: bool = False,
    ) -> str:
        ctx = endpoint._get_ctx()
        resolved_uri = endpoint._resolve_mcp_workspace_uri(uri, ctx)
        if (
            ctx.account_id != account_id
            or ctx.user.user_id != user_id
            or not endpoint.classify_uri(resolved_uri).is_memory
        ):
            return await original(
                uri=uri,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
                wait=wait,
            )

        if not old_string:
            raise endpoint.InvalidArgumentError("old_string must not be empty")
        service = endpoint.get_service()
        fs = service.fs

        raw_uri_before, raw_before = _raw_content(
            await fs._read_content(resolved_uri, ctx)
        )
        current = await fs.read_visible(raw_uri_before, ctx=ctx)
        if not isinstance(current, str):
            raise TypeError("fs.read_visible must return text")
        raw_uri_after, raw_after = _raw_content(
            await fs._read_content(raw_uri_before, ctx)
        )
        if raw_uri_before != raw_uri_after or raw_before != raw_after:
            raise _conflict(endpoint.ConflictError, resolved_uri, "snapshot changed")

        occurrences = current.count(old_string)
        if occurrences == 0:
            hint = ""
            if current.replace("\r\n", "\n").count(old_string.replace("\r\n", "\n")):
                hint = (
                    " old_string matches only after normalizing CRLF/LF line endings."
                )
            raise endpoint.InvalidArgumentError(
                f"old_string not found in {resolved_uri}.{hint} "
                "Re-read the file with the read tool to get its current content."
            )
        if occurrences > 1 and not replace_all:
            raise endpoint.InvalidArgumentError(
                f"old_string matches {occurrences} locations in {resolved_uri}. "
                "Include more surrounding context to make it unique, or set replace_all=true."
            )
        updated = current.replace(old_string, new_string)
        if updated == current:
            return f"No changes: {resolved_uri} already matches the requested edit."

        expected_sha256 = hashlib.sha256(_raw_bytes(raw_before)).hexdigest()
        with _edit_lock_wait(fs, resolved_uri, ctx):
            result = await fs.batch_write(
                root_uri=_parent_uri(resolved_uri),
                operations=[
                    {
                        "uri": resolved_uri,
                        "content": updated,
                        "mode": "replace",
                        "expected_sha256": expected_sha256,
                    }
                ],
                ctx=ctx,
                wait=wait,
                skip_conflicts=False,
            )
        message = f"Edited {resolved_uri}." + endpoint._indexing_hint(result)
        project_card = re.fullmatch(
            r"viking://user/[^/]+/memories/entities/projects/[^/]+\.md", resolved_uri
        )
        if (resolved_uri in fact_uri_set or project_card) and len(
            updated.encode("utf-8")
        ) > HINT_THRESHOLD_BYTES:
            message += ORGANIZATION_HINT
        return message

    return guarded_edit


def make_guarded_write(
    original: Callable[..., Any], *, endpoint: Any, account_id: str, user_id: str
) -> Callable[..., Any]:
    """Bound-lock only owner MCP appends to memory URIs; preserve native write path."""

    @wraps(original)
    async def guarded_write(
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        acl: Any = None,
    ) -> str:
        ctx = endpoint._get_ctx()
        resolved_uri = endpoint._resolve_mcp_workspace_uri(uri, ctx)
        if (
            mode != "append"
            or ctx.account_id != account_id
            or ctx.user.user_id != user_id
            or not endpoint.classify_uri(resolved_uri).is_memory
        ):
            return await original(
                uri=uri, content=content, mode=mode, wait=wait, acl=acl
            )
        service = endpoint.get_service()
        append_state = {"armed": True, "acquired": False, "failed": False}
        state_token = _APPEND_LOCK_STATE.set(append_state)
        try:
            with _edit_lock_wait(service.fs, resolved_uri, ctx, api="single"):
                # Exactly one native write call. No retry is safe after an ambiguous error.
                result = await original(
                    uri=uri, content=content, mode=mode, wait=wait, acl=acl
                )
            if append_state["failed"]:
                error = getattr(result, "error", None)
                if error is not None and getattr(error, "code", None) == "CONFLICT":
                    error.details.update(
                        stage="before_content_write",
                        write_applied=False,
                        lock_wait_limit_ms=int(EDIT_LOCK_WAIT_SECONDS * 1000),
                        recovery="Re-read the target, then retry the append.",
                    )
            return result
        finally:
            _APPEND_LOCK_STATE.reset(state_token)

    return guarded_write


def _make_wire_handler(func: Callable[..., Any], endpoint: Any) -> Callable[..., Any]:
    """Mirror mcp_endpoint._mcp_error_results for an already registered tool."""

    @wraps(func)
    async def wire_handler(*args: Any, **kwargs: Any) -> Any:
        from openviking.storage.errors import ResourceBusyError

        try:
            result = await func(*args, **kwargs)
        except endpoint.OpenVikingError as exc:
            result = endpoint._mcp_failure(exc)
        except ResourceBusyError as exc:
            from openviking.server.error_mapping import map_exception

            error = map_exception(exc)
            # Only the acquisition marked above proves this request did not
            # apply content; never infer that from an arbitrary write error.
            wait_ms = getattr(exc.__cause__, "_memory_edit_lock_wait_ms", None)
            if wait_ms is not None:
                error.details.update(
                    stage="before_content_write",
                    write_applied=False,
                    lock_wait_limit_ms=wait_ms,
                    recovery="Re-read the target, then retry the exact edit.",
                )
            result = endpoint._mcp_failure(error)
        if not isinstance(result, endpoint._MCPToolFailure):
            return result
        message = str(result)
        structured = {"result": message}
        if result.error is not None:
            structured["error"] = {
                "code": result.error.code,
                "message": result.error.message,
                "details": result.error.details,
            }
        return endpoint.CallToolResult(
            content=[endpoint.TextContent(type="text", text=message)],
            structuredContent=structured,
            isError=True,
        )

    return wire_handler


def install(
    endpoint: Any,
    *,
    account_id: str,
    user_id: str,
    fact_uris: Iterable[str] = (),
) -> Callable[..., Any]:
    """Patch only the in-memory endpoint module and registered MCP edit callable.

    This helper is provided for an owner-controlled startup adapter. It does not
    alter registration metadata/schema and must not be invoked against the live
    process during an active write stream.
    """
    from openviking.core.namespace import classify_uri
    from openviking_cli.exceptions import ConflictError

    endpoint.classify_uri = classify_uri
    endpoint.ConflictError = ConflictError
    original = endpoint.edit
    if getattr(original, "_memory_edit_guard", False) is True:
        return original
    install_lock_wait()
    guarded = make_guarded_edit(
        original,
        endpoint=endpoint,
        account_id=account_id,
        user_id=user_id,
        fact_uris=fact_uris,
    )
    guarded._memory_edit_guard = True
    endpoint.edit = guarded

    original_write = endpoint.write
    if getattr(original_write, "_memory_append_lock_guard", False) is not True:
        guarded_write = make_guarded_write(
            original_write, endpoint=endpoint, account_id=account_id, user_id=user_id
        )
        guarded_write._memory_append_lock_guard = True
        endpoint.write = guarded_write
        registered_write = next(
            (
                tool
                for tool in endpoint.mcp._tool_manager.list_tools()
                if tool.name == "write"
            ),
            None,
        )
        if registered_write is None:
            endpoint.write = original_write
            endpoint.edit = original
            raise RuntimeError("registered MCP write tool was not found")
        registered_write.fn = _make_wire_handler(guarded_write, endpoint)

    registered = next(
        (
            tool
            for tool in endpoint.mcp._tool_manager.list_tools()
            if tool.name == "edit"
        ),
        None,
    )
    if registered is None:
        endpoint.edit = original
        raise RuntimeError("registered MCP edit tool was not found")
    registered.fn = _make_wire_handler(guarded, endpoint)
    return guarded
