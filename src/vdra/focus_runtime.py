"""Integrate explicit task records with the existing state and evidence pipeline."""

from .work_focus import (
    active_issues,
    add_edge,
    add_issue,
    evidence_matches,
    one_hop,
    set_focus,
)


def focus_text(state):
    focus = state.get("focus", {})
    parts = []
    if focus.get("goal"):
        parts.append(
            "当前任务焦点（"
            + focus.get("source", "unknown")
            + "）："
            + focus["goal"][:300]
        )
    for item in active_issues(state)[:5]:
        parts.append(
            "未决事项 ["
            + item["id"]
            + "；"
            + item["status"]
            + "]："
            + item["description"][:160]
        )
    return "\n".join(parts)


def apply_record(state, session, action, data, clean):
    data = dict(data)
    for key in ("goal", "description", "evidence_quote", "scope"):
        if key in data:
            data[key] = clean(data[key])
    if action == "focus":
        result = set_focus(state, **data)
        if state.get("user_request") != result["focus"]["goal"]:
            result.pop("checkpoint", None)
        result["user_request"] = result["focus"]["goal"]
        result["request_at"] = result["focus"]["updated_at"]
        return result
    if action == "issue":
        return add_issue(state, session_id=session, **data)
    if action == "link":
        return add_edge(state, **data)
    raise ValueError("unsupported task record action")


def checkpoint_focus(state, session, data):
    goal = state.get("user_request") or data["summary"]
    state = set_focus(state, goal=goal[:1200], source="checkpoint")
    for description in data.get("pending", []):
        state = add_issue(state, session_id=session, description=description[:1200])
    return state


async def linked_entries(
    *, state, project, seeds, plan, cfg, service, ctx, excluded, root, notes=None
):
    """One hop only. Permissions, source hashes and topical evidence remain required."""
    from openviking.retrieve.context_assembler.models import AssembledEntry

    from .fact_evidence import digest, prepare_body, project_for_uri, source_text
    from .vdra_state import read_states
    from .recall_policy import STRONG_LEXICAL_SUPPORT, historical_query

    # Reuse the same project relation records across chats, without replaying
    # their task snapshots or importing another chat's unfinished work.
    edges = list(state.get("edges", []))
    if project:
        try:
            states = read_states()
            for other in states.values():
                if isinstance(other, dict) and other.get("project") == project:
                    edges.extend(other.get("edges", []))
        except (OSError, ValueError):
            pass
    historical = historical_query(plan.raw)
    graph = {"edges": edges}
    out = []
    seen = {e.uri for e in seeds}
    reads = {}
    attempts = 0
    if notes is None:
        notes = {}
    for seed in seeds[:3]:
        if seed.origin == "work_snapshot":
            continue
        if any(
            seed.uri == x or seed.uri.startswith(x.rstrip("/") + "/") for x in excluded
        ):
            continue
        try:
            seed_assessment = plan.source_assessment(
                seed.uri, seed.category, seed.text, cfg
            )
        except (AttributeError, KeyError, TypeError):
            continue
        if not seed_assessment.accepted:
            continue
        seed_support = plan.support(seed.text)
        if seed.origin == "project_facts":
            if seed_support < STRONG_LEXICAL_SUPPORT:
                continue
        elif not plan.accepts_supplement(seed.text):
            continue
        try:
            adjacent = one_hop(
                graph,
                seed.uri,
                historical=historical,
                as_of=getattr(plan, "as_of", None),
            )
        except ValueError:
            # Native retrieval can return URI forms outside the relation schema.
            continue
        for edge in adjacent:
            if attempts >= 4 or len(out) >= 2:
                return out
            if edge.get("relation") == "supersedes" and not edge.get("scope"):
                continue
            # Contradictions need explicit investigation rather than silently
            # being treated as positive corroboration in routine auto recall.
            if edge["relation"] == "contradicts":
                continue
            if edge.get("status") == "unverified" or edge.get(
                "evidence_status"
            ) not in {"current", "historical"}:
                continue
            if not historical and (
                edge.get("status") != "current"
                or edge.get("evidence_status") != "current"
            ):
                continue
            source, target = edge["source_uri"], edge["target_uri"]
            uri = target if source == seed.uri else source
            if uri in seen or any(
                uri == x or uri.startswith(x.rstrip("/") + "/") for x in excluded
            ):
                continue
            if project and project_for_uri(uri, cfg) != project:
                continue
            evidence_uri = edge.get("evidence_uri")
            # Cross-project provenance never drives automatic recall and is
            # rejected before any extra read.
            if evidence_uri and any(
                evidence_uri == x or evidence_uri.startswith(x.rstrip("/") + "/")
                for x in excluded
            ):
                continue
            if not evidence_uri or (
                project and project_for_uri(evidence_uri, cfg) != project
            ):
                continue
            attempts += 1
            try:
                for endpoint in (source, target, evidence_uri):
                    if endpoint not in reads:
                        reads[endpoint] = source_text(
                            await service.fs.read(endpoint, ctx=ctx)
                        )
            except Exception:
                # Native ACL/read failures are a hard miss for this edge.
                continue
            if (
                digest(reads[source]) != edge["source_hash"]
                or digest(reads[target]) != edge["target_hash"]
            ):
                continue
            if not evidence_matches(edge, reads[evidence_uri]):
                continue
            text = reads[uri]
            category = uri.split("/memories/", 1)[1].split("/", 1)[0]
            if category not in {"entities", "events", "preferences", "experiences"}:
                continue
            if not plan.source_assessment(uri, category, text, cfg).accepted:
                continue
            body = prepare_body(text, uri, plan)
            if not body or not plan.accepts_supplement(body):
                continue
            relation_note = (
                "[导航关系："
                + source
                + " -"
                + edge["relation"]
                + "-> "
                + target
                + (("; 范围：" + edge["scope"]) if edge.get("scope") else "")
                + "; 证据="
                + edge.get("evidence_date", "?")
                + "/"
                + edge.get("evidence_status", "?")
                + "; 关系状态="
                + edge.get("status", "?")
                + "]"
            )
            if edge.get("valid_from"):
                relation_note = relation_note[:-1] + (
                    "；生效="
                    + edge["valid_from"]
                    + "；失效="
                    + (edge.get("valid_until") or "未登记")
                    + "]"
                )
            notes[uri] = relation_note
            out.append(
                AssembledEntry(
                    uri=uri,
                    category=category,
                    score=seed.score,
                    detail="full",
                    text=body,
                    origin="explicit_relation",
                )
            )
            seen.add(uri)
    return out
