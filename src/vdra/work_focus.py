"""Small, deterministic task-focus records for work-state.json.

Storage and authorization deliberately stay with the caller. This module only
validates and transforms the per-session JSON object; it never reads files,
resolves URIs, or calls a model.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import unquote

SCHEMA = 1
MAX_ISSUES = 32
MAX_EDGES = 64
MAX_TEXT = 1200
MAX_URI = 2048
ISSUE_STATES = {"open", "blocked", "resolved"}
EDGE_RELATIONS = {
    "supports",
    "contradicts",
    "related",
    "derived_from",
    "supersedes",
    "depends_on",
}
EDGE_VALIDITY = {"current", "historical", "superseded", "unverified"}
_URI = re.compile(r"^viking://[^\s?#]+$")


def _text(value: Any, limit: int = MAX_TEXT, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError("text must not be empty")
    if len(value) > limit:
        raise ValueError(f"text exceeds {limit} characters")
    return value


def _uri(value: Any) -> str:
    value = _text(value, MAX_URI, required=True)
    if not _URI.fullmatch(value) or "\\" in value or "\x00" in value:
        raise ValueError("URI must be a normalized viking:// URI")
    decoded = value
    # Repeated decoding catches encoded separators and double-encoded traversal.
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if re.search(r"%(?:2e|2f|5c|23|3f)", decoded, re.I):
        raise ValueError("URI contains unsafe residual encoding")
    if "?" in decoded or "#" in decoded or "\\" in decoded:
        raise ValueError("URI must not contain query, fragment, or backslash")
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise ValueError("URI path traversal is not allowed")
    return value


def stable_issue_id(
    session_id: str, description: str, focus_id: str | None = None
) -> str:
    """Stable within a session, focus, and description; independent of insertion order."""
    session_id = _text(session_id, 256, required=True)
    description = _text(description, required=True)
    digest = hashlib.sha256(
        (session_id + "\0" + (focus_id or "") + "\0" + description.casefold()).encode()
    ).hexdigest()[:16]
    return "iss-" + digest


def set_focus(
    state: Mapping[str, Any],
    *,
    goal: str,
    evidence: list[str] | None = None,
    source: str = "user",
) -> dict[str, Any]:
    """Set current focus. Source is explicit; inferred/model-created focus is unsupported."""
    if source not in {"user", "checkpoint"}:
        raise ValueError("focus source must be user or checkpoint")
    clean_evidence = [_uri(uri) for uri in (evidence or [])]
    if len(clean_evidence) > 8:
        raise ValueError("at most 8 evidence URIs are allowed")
    goal = _text(goal, required=True)
    focus_id = stable_focus_id(goal)
    result = dict(state)
    result["focus"] = {
        "schema": SCHEMA,
        "id": focus_id,
        "goal": goal,
        "evidence": list(dict.fromkeys(clean_evidence)),
        "source": source,
        "updated_at": _now(),
    }
    return result


def stable_focus_id(goal: str) -> str:
    goal = _text(goal, required=True)
    return "foc-" + hashlib.sha256(goal.casefold().encode()).hexdigest()[:16]


def add_issue(
    state: Mapping[str, Any],
    *,
    session_id: str,
    description: str,
    status: str = "open",
    evidence: list[str] | None = None,
    issue_id: str | None = None,
    focus_id: str | None = None,
) -> dict[str, Any]:
    if status not in ISSUE_STATES:
        raise ValueError("invalid issue status")
    description = _text(description, required=True)
    issues = [
        dict(item) for item in state.get("issues", []) if isinstance(item, Mapping)
    ]
    existing = next(
        (
            index
            for index, item in enumerate(issues)
            if issue_id and item.get("id") == issue_id
        ),
        None,
    )
    if existing is not None:
        previous_focus = issues[existing].get("focus_id")
        if focus_id is not None and focus_id != previous_focus:
            raise ValueError("existing issue belongs to another focus")
        focus_id = previous_focus
    elif focus_id is None:
        focus_id = (
            state.get("focus", {}).get("id")
            if isinstance(state.get("focus"), Mapping)
            else None
        )
    if focus_id is not None and not re.fullmatch(r"foc-[a-f0-9]{16}", focus_id):
        raise ValueError("invalid focus id")
    ident = issue_id or stable_issue_id(session_id, description, focus_id)
    if not re.fullmatch(r"iss-[a-f0-9]{16}", ident):
        raise ValueError("invalid stable issue id")
    refs = list(dict.fromkeys(_uri(x) for x in (evidence or [])))
    if status == "resolved" and not refs:
        raise ValueError("resolving an issue requires explicit evidence URI")
    result = dict(state)
    updated = {
        "id": ident,
        "focus_id": focus_id,
        "description": description,
        "status": status,
        "evidence": refs,
        "updated_at": _now(),
    }
    if not issue_id:
        existing = next(
            (index for index, item in enumerate(issues) if item.get("id") == ident),
            None,
        )
    if (
        existing is None
        and sum(item.get("focus_id") == focus_id for item in issues) >= MAX_ISSUES
    ):
        raise ValueError(
            "issue capacity reached for this focus; archive or review records explicitly"
        )
    if existing is None:
        issues.append(updated)
    else:
        issues[existing] = updated
    result["issues"] = issues
    return result


def add_edge(
    state: Mapping[str, Any],
    *,
    source_uri: str,
    target_uri: str,
    relation: str,
    source_hash: str,
    target_hash: str,
    evidence_uri: str | None = None,
    evidence_hash: str | None = None,
    evidence_quote: str | None = None,
    evidence_date: str | None = None,
    evidence_status: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    """Record a directed, hash-checked relation with optional explicit provenance.

    Legacy callers remain compatible. New sourced edges must supply the full
    evidence bundle; missing legacy status is never inferred from creation time.
    """
    if relation not in EDGE_RELATIONS:
        raise ValueError("unsupported explicit relation")
    source_uri, target_uri = _uri(source_uri), _uri(target_uri)
    for digest in (source_hash, target_hash):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("source hashes must be lowercase SHA-256 hex")
    supplied = [
        evidence_uri,
        evidence_hash,
        evidence_quote,
        evidence_date,
        evidence_status,
        status,
    ]
    if relation == "supersedes" and (not isinstance(scope, str) or not scope.strip()):
        raise ValueError("supersedes relation requires an explicit scope")
    if scope is not None:
        scope = _text(scope, 240, required=True)
    if any(value is not None for value in supplied):
        if any(value is None for value in supplied):
            raise ValueError("sourced edges require the complete evidence bundle")
        evidence_uri = _uri(evidence_uri)
        if not re.fullmatch(r"[a-f0-9]{64}", evidence_hash):
            raise ValueError("evidence hash must be lowercase SHA-256 hex")
        evidence_quote = _text(evidence_quote, 400, required=True)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", evidence_date):
            raise ValueError("evidence date must be YYYY-MM-DD")
        datetime.strptime(evidence_date, "%Y-%m-%d")
        if evidence_status not in EDGE_VALIDITY or status not in EDGE_VALIDITY:
            raise ValueError("invalid evidence or relation status")
        # Historical evidence is a valid historical record. The recall layer
        # must gate it on historical intent instead of erasing it here.
        if relation == "supersedes" and evidence_status not in {
            "current",
            "historical",
        }:
            raise ValueError("supersedes requires current or historical evidence")
    if valid_from is not None or valid_until is not None:
        if evidence_uri is None or valid_from is None:
            raise ValueError(
                "dated relations require a sourced bundle and a start date"
            )
        start = _iso_date(valid_from)
        if valid_until is not None and _iso_date(valid_until) <= start:
            raise ValueError("valid_until must be after valid_from")
    edge = {
        "source_uri": source_uri,
        "target_uri": target_uri,
        "relation": relation,
        "source_hash": source_hash,
        "target_hash": target_hash,
        "created_at": _now(),
    }
    if evidence_uri is not None:
        edge.update(
            {
                "evidence_uri": evidence_uri,
                "evidence_hash": evidence_hash,
                "evidence_quote": evidence_quote,
                "evidence_date": evidence_date,
                "evidence_status": evidence_status,
                "status": status,
            }
        )
    if scope is not None:
        edge["scope"] = scope
    if valid_from is not None:
        edge["valid_from"] = valid_from
    if valid_until is not None:
        edge["valid_until"] = valid_until
    result = dict(state)
    edges = [dict(item) for item in state.get("edges", []) if isinstance(item, Mapping)]
    key = (source_uri, target_uri, relation, valid_from)
    edges = [
        item
        for item in edges
        if (
            item.get("source_uri"),
            item.get("target_uri"),
            item.get("relation"),
            item.get("valid_from"),
        )
        != key
    ]
    if len(edges) >= MAX_EDGES:
        raise ValueError("edge capacity reached; archive or review records explicitly")
    edges.append(edge)
    result["edges"] = edges
    return result


def evidence_matches(edge: Mapping[str, Any], text: str) -> bool:
    """Require the cited exact excerpt to exist in the hash-matching source."""
    if not isinstance(text, str) or not isinstance(edge, Mapping):
        return False
    quote = edge.get("evidence_quote")
    expected = edge.get("evidence_hash")
    actual = hashlib.sha256(text.encode()).hexdigest()
    return (
        isinstance(quote, str)
        and bool(quote)
        and isinstance(expected, str)
        and actual == expected
        and quote in text
    )


def _iso_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("validity date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def one_hop(
    state: Mapping[str, Any],
    uri: str,
    *,
    historical: bool = False,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return adjacent directed records without reversing their assertions.

    Incoming supports remain source->target; consumers may show the source as
    evidence for a seeded conclusion but must retain that original direction.
    Supersedes edges never expose the old target on current queries.
    """
    uri = _uri(uri)
    out = []
    if as_of is not None:
        historical = True
    search_date = as_of if historical else datetime.now(timezone.utc).date()
    for edge in state.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        start, end = edge.get("valid_from"), edge.get("valid_until")
        if start is None:
            if end is not None or as_of is not None:
                continue
        else:
            try:
                begins = _iso_date(start)
                ends = _iso_date(end) if end is not None else None
            except ValueError:
                continue
            if ends is not None and ends <= begins:
                continue
            if search_date and (
                search_date < begins or (ends is not None and search_date >= ends)
            ):
                continue
        relation = edge.get("relation")
        current = edge.get("status", "unverified") == "current"
        if edge.get("source_uri") == uri:
            if relation == "supersedes" and not historical:
                continue
            if not current and not historical:
                continue
            out.append(dict(edge))
        elif edge.get("target_uri") == uri:
            if relation not in {
                "supports",
                "contradicts",
                "related",
                "derived_from",
                "depends_on",
                "supersedes",
            }:
                continue
            if relation == "supersedes" and not historical:
                # A historical node may still lead to its current replacement.
                out.append(dict(edge))
                continue
            if not current and not historical:
                continue
            out.append(dict(edge))
    return out


def active_issues(
    state: Mapping[str, Any], focus_id: str | None = None
) -> list[dict[str, Any]]:
    if focus_id is None:
        focus = state.get("focus")
        focus_id = focus.get("id") if isinstance(focus, Mapping) else None
    return [
        dict(x)
        for x in state.get("issues", [])
        if isinstance(x, Mapping)
        and x.get("status") in {"open", "blocked"}
        and focus_id is not None
        and x.get("focus_id") == focus_id
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
