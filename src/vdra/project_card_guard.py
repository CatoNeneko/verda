"""Offline candidate guard for oversized OpenViking project fact-card writes.

Install only from an owner-controlled startup adapter after review. The guard
preserves native VikingFS methods for all out-of-scope writes and archives a
sanitized, complete oversized project-card body before refusing that write.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

THRESHOLD_BYTES = 8 * 1024
PROJECT_CARD_URI = re.compile(
    r"^viking://user/(?P<user>[^/]+)/memories/entities/projects/(?P<project>[^/]+)\.md$"
)
MEMORY_FIELDS_TRAILER = re.compile(
    r"(?:\n\n)?<!--\s*MEMORY_FIELDS\s*\n(?P<json>.*?)\n-->\s*$", re.S
)


def _target(uri: Any, ctx: Any, account_id: str, user_id: str) -> re.Match[str] | None:
    if not isinstance(uri, str) or ctx is None:
        return None
    if getattr(ctx, "account_id", None) != account_id:
        return None
    user = getattr(getattr(ctx, "user", None), "user_id", None)
    if user != user_id:
        return None
    match = PROJECT_CARD_URI.fullmatch(uri)
    if match is None or match.group("user") != user_id:
        return None
    return match


def _as_text(content: str | bytes) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _body_size(text: str) -> int:
    match = MEMORY_FIELDS_TRAILER.search(text)
    if match is not None:
        try:
            fields = json.loads(match.group("json"))
        except (json.JSONDecodeError, TypeError):
            fields = None
        if isinstance(fields, dict):
            text = text[: match.start()].strip()
    return len(text.encode("utf-8"))


async def _sanitized_text(fs: Any, text: str, *, ctx: Any, uri: str) -> str:
    sanitizer = getattr(fs, "_privacy_sanitizer", None)
    if sanitizer is None:
        return text
    result = await sanitizer.sanitize(text, ctx=ctx, uri=uri)
    if getattr(result, "ambiguous_references", ()):
        from openviking_cli.exceptions import InvalidArgumentError

        raise InvalidArgumentError(
            "Content contains a Privacy value mapped to multiple fields; "
            "insert the intended owner-bound reference explicitly",
            details={
                "uri": uri,
                "candidate_references": [
                    list(candidates) for candidates in result.ambiguous_references
                ],
            },
        )
    return result.content


async def _archive_then_reject(
    fs: Any,
    *,
    uri: str,
    project: str,
    safe_content: bytes,
    requested_content: bytes,
    ctx: Any,
    raw_write_bytes: Callable[..., Any],
    raw_read_bytes: Callable[..., Any],
    threshold: int,
) -> None:
    from openviking_cli.exceptions import InvalidArgumentError

    digest = hashlib.sha256(safe_content).hexdigest()
    archive_uri = (
        f"viking://user/{ctx.user.user_id}/resources/archive/openviking/"
        f"project-overflow/{project}/{digest}/requested.raw.md"
    )
    # Saving through the original byte writer retains its native ACL, parent
    # creation, encryption, path-lock and lease behavior. Content was sanitized
    # above because the native byte writer intentionally does not sanitize.
    try:
        saved = await raw_read_bytes(archive_uri, ctx=ctx)
    except Exception as exc:
        if not (
            isinstance(exc, FileNotFoundError)
            or getattr(exc, "code", None) == "NOT_FOUND"
        ):
            raise
        saved = None
    if saved is not None and saved != safe_content:
        raise IOError(
            "content-addressed project-card archive URI contains different bytes"
        )
    if saved is None:
        await raw_write_bytes(archive_uri, safe_content, ctx=ctx)
        saved = await raw_read_bytes(archive_uri, ctx=ctx)
    if saved != safe_content or hashlib.sha256(saved).hexdigest() != digest:
        raise IOError(
            "project-card overflow archive readback did not match its content hash"
        )

    manifest_uri = archive_uri.rsplit("/", 1)[0] + "/manifest.json"
    manifest = {
        "sourceURI": uri,
        "requested_sha256": hashlib.sha256(requested_content).hexdigest(),
        "archive_sha256": digest,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "state": "not_applied_review_required",
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        existing_manifest = await raw_read_bytes(manifest_uri, ctx=ctx)
    except Exception as exc:
        if not (
            isinstance(exc, FileNotFoundError)
            or getattr(exc, "code", None) == "NOT_FOUND"
        ):
            raise
        existing_manifest = None
    if existing_manifest is None:
        await raw_write_bytes(manifest_uri, manifest_bytes, ctx=ctx)
    else:
        try:
            previous = json.loads(existing_manifest.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            previous = None
        if not (
            isinstance(previous, dict)
            and previous.get("sourceURI") == uri
            and previous.get("requested_sha256")
            == hashlib.sha256(requested_content).hexdigest()
            and previous.get("archive_sha256") == digest
            and previous.get("state") == "not_applied_review_required"
        ):
            raise IOError(
                "content-addressed project-card manifest URI contains different content"
            )
    message = (
        "Project fact-card write was not applied. The complete privacy-sanitized "
        "request was archived for review; organize it into the main card and "
        "topic cards, then submit a deliberate smaller update."
    )
    raise InvalidArgumentError(
        message,
        details={
            "archive_uri": archive_uri,
            "byte_count": len(safe_content),
            "sha256": digest,
            "threshold_bytes": threshold,
            "guide": "Review the complete archive, split by topic, then write an intentional main-card update.",
        },
    )


def install(
    viking_fs_class: type,
    *,
    account_id: str,
    user_id: str,
    threshold_bytes: int = THRESHOLD_BYTES,
) -> type:
    """Idempotently wrap the class's direct text and byte write entry points."""
    if threshold_bytes <= 0:
        raise ValueError("threshold_bytes must be positive")
    existing = getattr(viking_fs_class, "_project_card_overflow_guard", None)
    config = (account_id, user_id, threshold_bytes)
    if existing is not None:
        if existing["config"] != config:
            raise RuntimeError(
                "VikingFS already has a project-card guard with different settings"
            )
        return viking_fs_class

    raw_write_text = viking_fs_class.write_file
    raw_write_bytes = viking_fs_class.write_file_bytes
    raw_read_bytes = viking_fs_class.read_file_bytes

    @wraps(raw_write_text)
    async def guarded_write_file(
        self: Any,
        uri: str,
        content: str | bytes,
        ctx: Any = None,
        lease_ref: Any = None,
        auto_pathlock: bool = True,
    ) -> Any:
        match = _target(uri, ctx, account_id, user_id)
        text = _as_text(content) if match else None
        if match and text is None:
            from openviking.storage.acl import AclAction
            from openviking_cli.exceptions import InvalidArgumentError

            await self._ensure_access(uri, ctx, action=AclAction.WRITE)
            raise InvalidArgumentError(
                "Project fact-card content must be valid UTF-8 text; the write was not applied.",
                details={"threshold_bytes": threshold_bytes, "reason": "invalid_utf8"},
            )
        if match and text is not None:
            # Check source WRITE before sanitizer work or archive side effects.
            from openviking.storage.acl import AclAction

            await self._ensure_access(uri, ctx, action=AclAction.WRITE)
            text = await _sanitized_text(self, text, ctx=ctx, uri=uri)
            safe_bytes = text.encode("utf-8")
            if _body_size(text) > threshold_bytes:
                await _archive_then_reject(
                    self,
                    uri=uri,
                    project=match.group("project"),
                    safe_content=safe_bytes,
                    requested_content=(
                        content.encode("utf-8") if isinstance(content, str) else content
                    ),
                    ctx=ctx,
                    raw_write_bytes=raw_write_bytes.__get__(self, type(self)),
                    raw_read_bytes=raw_read_bytes.__get__(self, type(self)),
                    threshold=threshold_bytes,
                )
            # Keep native text behavior, including its own privacy sanitizer,
            # path lock, ACL, parent handling and lease propagation.
        return await raw_write_text(
            self,
            uri,
            content,
            ctx=ctx,
            lease_ref=lease_ref,
            auto_pathlock=auto_pathlock,
        )

    @wraps(raw_write_bytes)
    async def guarded_write_file_bytes(
        self: Any,
        uri: str,
        content: bytes,
        ctx: Any = None,
        lease_ref: Any = None,
        auto_pathlock: bool = True,
    ) -> Any:
        match = _target(uri, ctx, account_id, user_id)
        text = _as_text(content) if match else None
        if match and text is None:
            from openviking.storage.acl import AclAction
            from openviking_cli.exceptions import InvalidArgumentError

            await self._ensure_access(uri, ctx, action=AclAction.WRITE)
            raise InvalidArgumentError(
                "Project fact-card content must be valid UTF-8 text; the write was not applied.",
                details={"threshold_bytes": threshold_bytes, "reason": "invalid_utf8"},
            )
        if match and text is not None:
            from openviking.storage.acl import AclAction

            await self._ensure_access(uri, ctx, action=AclAction.WRITE)
            # Do not alter bytes on normal writes: the coordinator may have
            # already computed hashes/index inputs from these exact bytes.
            if _body_size(text) > threshold_bytes:
                safe_text = await _sanitized_text(self, text, ctx=ctx, uri=uri)
                safe_bytes = safe_text.encode("utf-8")
                await _archive_then_reject(
                    self,
                    uri=uri,
                    project=match.group("project"),
                    safe_content=safe_bytes,
                    requested_content=content,
                    ctx=ctx,
                    raw_write_bytes=raw_write_bytes.__get__(self, type(self)),
                    raw_read_bytes=raw_read_bytes.__get__(self, type(self)),
                    threshold=threshold_bytes,
                )
            # Small bytes pass through exactly as supplied; only oversized
            # requests need sanitizer work before they can be archived safely.
        return await raw_write_bytes(
            self,
            uri,
            content,
            ctx=ctx,
            lease_ref=lease_ref,
            auto_pathlock=auto_pathlock,
        )

    guarded_write_file._project_card_overflow_guard = config
    guarded_write_file_bytes._project_card_overflow_guard = config
    viking_fs_class.write_file = guarded_write_file
    viking_fs_class.write_file_bytes = guarded_write_file_bytes
    viking_fs_class._project_card_overflow_guard = {"config": config}
    return viking_fs_class
