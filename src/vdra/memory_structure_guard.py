"""Offline candidate guard for Privacy references in project-memory structure.

This module is intentionally not installed by itself. Call :func:`install` from
an owner-controlled bootstrap after review. It never resolves a Privacy
reference and does not change the native sanitizer's matching behavior.
"""

from __future__ import annotations

import re
from functools import wraps
from typing import Any
from urllib.parse import unquote

PRIVACY_MARKER = "{{ov_privacy:"
_PROJECTS_ROOT = "memories/entities/projects"
_MARKDOWN_SUFFIXES = (".md", ".markdown")
_GENERATED_NAMES = frozenset({".abstract.md", ".overview.md"})
_EXCLUDED_DIRS = frozenset({"archive", "history"})
_FACT_NAMESPACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*[._]")
_VIKING_URI_ENDS = frozenset(" \t\r\n<>()[\\]\"',")


def _owner_user_id(ctx: Any) -> str | None:
    user = getattr(ctx, "user", None)
    value = getattr(user, "user_id", None)
    return value if isinstance(value, str) and value else None


def _target_uri(uri: Any, ctx: Any, account_id: str, user_id: str) -> bool:
    """Limit checks to this account's current user's authored project Markdown."""
    if not isinstance(uri, str) or ctx is None:
        return False
    if getattr(ctx, "account_id", None) != account_id or _owner_user_id(ctx) != user_id:
        return False

    # VikingFS accepts the home alias in some direct-call paths. Resolve only
    # that unambiguous alias locally; do not reinterpret explicit other users.
    prefix = "viking://~/"
    if uri.startswith(prefix):
        uri = f"viking://user/{user_id}/" + uri[len(prefix) :]

    root = f"viking://user/{user_id}/{_PROJECTS_ROOT}/"
    if not uri.startswith(root):
        return False
    relative = uri[len(root) :]
    if not relative or relative.endswith("/") or "?" in relative or "#" in relative:
        return False

    components = [unquote(part) for part in relative.split("/")]
    if any(not part or part in {".", ".."} for part in components):
        return False
    leaf = components[-1]
    if leaf.casefold() in _GENERATED_NAMES:
        return False
    if not leaf.casefold().endswith(_MARKDOWN_SUFFIXES):
        return False
    if any(part.casefold() in _EXCLUDED_DIRS for part in components[:-1]):
        return False
    # Also exclude conventional top-level archive/history files, which some
    # project layouts use instead of dedicated directories.
    if leaf.rsplit(".", 1)[0].casefold() in _EXCLUDED_DIRS:
        return False
    return True


def _fact_label_segments(text: str):
    """Yield bracketed fact-label segments from every line in the card.

    The first ASCII or fullwidth metadata semicolon, or a closing bracket,
    ends the fact label. Incomplete delimiters are tolerated because Privacy
    replacement may have damaged them. Ordinary bracketed prose is filtered
    later by the required ASCII namespace prefix.
    """
    for raw_line in text.splitlines():
        line = raw_line.lstrip(" \t")
        if not line.startswith("["):
            continue
        start = 1
        candidates = [
            pos
            for pos in (
                line.find(";", start),
                line.find("；", start),
                line.find("]", start),
            )
            if pos >= 0
        ]
        end = min(candidates) if candidates else len(line)
        yield line[start:end]


def _has_machine_namespace(segment: str) -> bool:
    # Dot namespaces (e.g. ``thesis.``) and underscore field labels (e.g.
    # ``wait_for_``) are both machine structures. Only this leading prefix is
    # constrained to ASCII; the suffix may contain Unicode.
    return bool(_FACT_NAMESPACE_RE.match(segment))


def _markdown_link_destinations(text: str):
    """Yield syntactic Markdown destinations, including incomplete links.

    Only destinations beginning with ``viking://`` are relevant. Incomplete
    delimiters are accepted to fail closed when a Privacy reference damaged a
    previously valid destination.
    """
    index = 0
    while True:
        opener = text.find("](", index)
        if opener < 0:
            break
        pos = opener + 2
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == "<":
            start = pos + 1
            end = start
            while end < len(text) and text[end] not in ">\r\n":
                end += 1
            yield text[start:end]
            index = end + 1
            continue

        start = pos
        end = start
        depth = 0
        escaped = False
        while end < len(text) and text[end] not in "\r\n" and not text[end].isspace():
            char = text[end]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        yield text[start:end]
        index = max(end + 1, opener + 2)

    # CommonMark reference definitions and angle autolinks are also link
    # destinations. Their incomplete final delimiter is tolerated.
    for match in re.finditer(
        r"(?m)^ {0,3}\[[^\]\r\n]+\]:[ \t]*<?(viking://[^\s>\r\n]*)", text
    ):
        yield match.group(1)
    for match in re.finditer(r"<(?P<uri>viking://[^>\r\n]*)(?:>|$)", text):
        yield match.group("uri")


def _viking_uri_fragments(text: str):
    """Yield bare or linked outer Viking URI tokens without parsing references.

    A complete Privacy placeholder contains an inner ``viking://`` scheme, but
    no second ``{{ov_privacy:`` marker after that scheme. It therefore passes;
    an outer URI with a nested placeholder contains the marker in the same
    token and is rejected.
    """
    # An inner scheme belongs to the complete opaque reference, even when two
    # references are adjacent. Only outer URI starts are navigation candidates.
    references = [m.span() for m in re.finditer(r"\{\{ov_privacy:[^{}\r\n]*\}\}", text)]
    index = 0
    while True:
        start = text.find("viking://", index)
        if start < 0:
            return
        enclosing = next(
            (end for begin, end in references if begin <= start < end), None
        )
        if enclosing is not None:
            index = enclosing
            continue
        end = start + len("viking://")
        while end < len(text) and text[end] not in _VIKING_URI_ENDS:
            end += 1
        yield text[start:end]
        index = max(end, start + len("viking://"))


def has_structure_collision(text: str) -> bool:
    """Whether a Privacy reference occupies a machine fact label or Viking URI."""
    if not isinstance(text, str) or PRIVACY_MARKER not in text:
        return False

    for label in _fact_label_segments(text):
        if _has_machine_namespace(label) and PRIVACY_MARKER in label:
            return True

    # Scan both Markdown destinations and bare navigation URIs. The URI scan
    # subsumes the destination scan but retaining the Markdown parser documents
    # and tests the supported link forms, including malformed delimiters.
    for destination in _markdown_link_destinations(text):
        if (
            destination.casefold().startswith("viking://")
            and PRIVACY_MARKER in destination
        ):
            return True
    for uri in _viking_uri_fragments(text):
        if PRIVACY_MARKER in uri:
            return True
    return False


def _raise_structure_collision() -> None:
    from openviking_cli.exceptions import InvalidArgumentError

    raise InvalidArgumentError(
        "Privacy sanitization would alter a machine label or Viking link destination. "
        "Use a natural-language public label or retrieve by title.",
        details={"reason": "privacy_structure_collision"},
    )


def _check(uri: Any, ctx: Any, text: Any, *, account_id: str, user_id: str) -> None:
    if _target_uri(uri, ctx, account_id, user_id) and isinstance(text, str):
        if has_structure_collision(text):
            _raise_structure_collision()


def install(
    viking_fs_class: type,
    *,
    account_id: str,
    user_id: str,
    coordinator_class: type | None = None,
) -> type:
    """Wrap coordinator and final VikingFS writes; safe to call repeatedly.

    Install after the native classes are available. It composes with existing
    wrappers (including ``project_card_guard``) by wrapping their current
    methods and does not replace ACL, lease, path-lock or CAS implementations.
    """
    config = (account_id, user_id)
    if not account_id or not user_id:
        raise ValueError("account_id and user_id are required")

    if coordinator_class is None:
        from openviking.storage.content_write import ContentWriteCoordinator

        coordinator_class = ContentWriteCoordinator

    existing = getattr(viking_fs_class, "_privacy_structure_guard", None)
    if existing is not None and existing != config:
        raise RuntimeError(
            "VikingFS already has a privacy structure guard for another owner"
        )
    existing_coordinator = getattr(coordinator_class, "_privacy_structure_guard", None)
    if existing_coordinator is not None and existing_coordinator != config:
        raise RuntimeError(
            "ContentWriteCoordinator already has a privacy structure guard for another owner"
        )

    if existing is None:
        raw_write_text = viking_fs_class.write_file
        raw_write_bytes = viking_fs_class.write_file_bytes

        @wraps(raw_write_text)
        async def guarded_write_file(
            self: Any,
            uri: str,
            content: str | bytes,
            ctx: Any = None,
            lease_ref: Any = None,
            auto_pathlock: bool = True,
        ) -> Any:
            if _target_uri(uri, ctx, account_id, user_id):
                from openviking.storage.acl import AclAction

                # Preserve authorization order and avoid parent-directory side
                # effects if the post-sanitization structure is rejected.
                await self._ensure_access(uri, ctx, action=AclAction.WRITE)
                if isinstance(content, str):
                    sanitizer = getattr(self, "_privacy_sanitizer", None)
                    if sanitizer is not None:
                        try:
                            result = await sanitizer.sanitize(content, ctx=ctx, uri=uri)
                        except Exception:
                            # A preview must preserve the native sanitizer's
                            # failure ordering; do not swallow and invoke it again.
                            raise
                        else:
                            _check(
                                uri,
                                ctx,
                                result.content,
                                account_id=account_id,
                                user_id=user_id,
                            )
                    else:
                        _check(
                            uri, ctx, content, account_id=account_id, user_id=user_id
                        )
                elif isinstance(content, bytes):
                    try:
                        _check(
                            uri,
                            ctx,
                            content.decode("utf-8"),
                            account_id=account_id,
                            user_id=user_id,
                        )
                    except UnicodeDecodeError:
                        pass
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
            if _target_uri(uri, ctx, account_id, user_id):
                from openviking.storage.acl import AclAction

                # Bytes are the already-rendered final body for append/edit and
                # batch paths. Inspect them as supplied; do not rewrite bytes,
                # which would desynchronize CAS hashes and downstream plans.
                await self._ensure_access(uri, ctx, action=AclAction.WRITE)
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                _check(uri, ctx, text, account_id=account_id, user_id=user_id)
            return await raw_write_bytes(
                self,
                uri,
                content,
                ctx=ctx,
                lease_ref=lease_ref,
                auto_pathlock=auto_pathlock,
            )

        guarded_write_file._privacy_structure_guard = config
        guarded_write_file_bytes._privacy_structure_guard = config
        viking_fs_class.write_file = guarded_write_file
        viking_fs_class.write_file_bytes = guarded_write_file_bytes
        viking_fs_class._privacy_structure_guard = config

    if existing_coordinator is None:
        raw_sanitize_text = coordinator_class._sanitize_text

        @wraps(raw_sanitize_text)
        async def guarded_sanitize_text(
            self: Any,
            content: str,
            *,
            uri: str,
            ctx: Any,
        ) -> str:
            sanitized = await raw_sanitize_text(self, content, uri=uri, ctx=ctx)
            _check(uri, ctx, sanitized, account_id=account_id, user_id=user_id)
            return sanitized

        guarded_sanitize_text._privacy_structure_guard = config
        coordinator_class._sanitize_text = guarded_sanitize_text
        coordinator_class._privacy_structure_guard = config

    return viking_fs_class
