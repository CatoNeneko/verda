"""Memory retrieval, evidence selection, and bounded context assembly."""

import asyncio
import hashlib
import re
import time
from contextvars import ContextVar
from dataclasses import replace

from . import vdra_state as storage
from .fact_evidence import (
    archive_only,
    covered_fact_requests,
    evidence_label,
    prepare_body,
    select_facts,
)
from .focus_runtime import focus_text, linked_entries
from .recall_observability import note_problem, timed_await
from .recall_policy import (
    MIN_LEXICAL_SUPPORT,
    NATIVE_SEMANTIC_FLOOR,
    STRONG_LEXICAL_SUPPORT,
    SUPPLEMENT_RATIO,
    analyze,
    foreign_historical_intent,
    terms,
)

REQUEST = ContextVar("work_memory_request", default=None)
REQUEST_DEADLINE = ContextVar("work_memory_deadline", default=None)
_DRAINING_NATIVE = set()


async def budgeted(stage, awaitable, deadline, cap):
    remaining = deadline - time.monotonic()
    budget = min(float(cap), remaining)
    if budget <= 0:
        close = getattr(awaitable, "close", None)
        if close:
            close()
        raise TimeoutError(stage + " budget exhausted")
    return await asyncio.wait_for(timed_await(stage, awaitable), timeout=budget)


async def bounded_native(awaitable, deadline):
    # wait_for also waits for cancellation cleanup, which can overrun the hook.
    if _DRAINING_NATIVE or deadline <= time.monotonic():
        awaitable.close()
        raise TimeoutError("native retrieval unavailable within deadline")
    task = asyncio.create_task(timed_await("native_retrieval", awaitable))
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=max(0, deadline - time.monotonic())
        )
        if done:
            return task.result()
        raise TimeoutError("native retrieval budget exhausted")
    finally:
        if not task.done():
            _DRAINING_NATIVE.add(task)

            def drained(finished):
                _DRAINING_NATIVE.discard(finished)
                if not finished.cancelled():
                    finished.exception()

            task.add_done_callback(drained)
            task.cancel()


def semantic_candidate_body(uri, text):
    if (
        not isinstance(uri, str)
        or not isinstance(text, str)
        or not text.strip()
        or len(text) > 4000
    ):
        return False
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    body = re.sub(r"<!--\s*MEMORY_FIELDS\b.*?-->", "", body, flags=re.S).strip()
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith("#")
            or line == uri
            or line == uri.rsplit("/", 1)[-1]
        ):
            continue
        lines.append(line)
    useful = "\n".join(lines).strip()
    return bool(useful and useful not in {uri, uri.rsplit("/", 1)[-1]})


def semantic_handoff_allows(request, uri, text):
    trusted = (request or {}).get("semantic_uris", {})
    try:
        score = float(trusted.get(uri, 0))
    except (TypeError, ValueError):
        return False
    return (
        score >= NATIVE_SEMANTIC_FLOOR
        and foreign_historical_intent((request or {}).get("query", ""))
        and semantic_candidate_body(uri, text)
    )


def entry_signature(entry):
    return hashlib.sha256((entry.uri + "\n" + entry.text).encode()).hexdigest()


def concise_summary(text):
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    parts = re.split(r"(?m)^## ", text)
    sections = {}
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        sections[title.strip().casefold()] = body.strip()
    selected = []
    for title, label, cap in [
        ("files & context", "来源文件", 220),
        ("current state", "摘要状态", 120),
        ("open issues", "待核实问题", 120),
        ("next steps", "待核实的下一步", 140),
        ("task & goals", "目标", 100),
    ]:
        if title in sections:
            line = next(
                (x.strip(" _-") for x in sections[title].splitlines() if x.strip()), ""
            )
            if line:
                selected.append(label + "：" + line[:cap])
    if selected:
        return "\n".join(selected)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    useful = [
        x
        for x in lines
        if re.search(
            r"目标|约束|决定|更正|完成|待办|下一步|禁止|不要|证据|文件|/|goal|constraint|decision|pending|artifact",
            x,
            re.I,
        )
    ]
    return "\n".join((useful or lines)[:12])


def rank_candidates(candidates):
    request = REQUEST.get()
    if not request:
        return candidates
    cfg = storage.config()
    query = request.get("focus", request["query"])
    project = request.get("project", "")
    plan = request.get("plan") or analyze(query, cfg, project_hint=project)
    fact_uris = {item.get("facts_uri") for item in cfg.get("projects", {}).values()}
    ranked = []
    for c in candidates:
        if archive_only(c.base_uri):
            continue
        semantic_score = 0.0
        try:
            raw_score = float(c.score)
        except (TypeError, ValueError):
            raw_score = 0.0
        if (
            plan.foreign
            and raw_score >= NATIVE_SEMANTIC_FLOOR
            and semantic_candidate_body(c.base_uri, c.abstract)
        ):
            semantic_score = raw_score
        if c.base_uri in fact_uris or (
            "/projects/" in c.base_uri and len(c.abstract) > 4000
        ):
            selected = select_facts(c.abstract, plan)
            if selected:
                c = replace(c, abstract=selected)
            elif not semantic_score:
                continue
        # Historical events remain available for actual historical questions.
        # A previous suggestion must never look like a freshly measured diagnosis.
        label = evidence_label(c.abstract, c.base_uri)
        if label:
            c = replace(c, abstract=label + "\n\n" + c.abstract)
        text = c.base_uri.rsplit("/", 1)[-1] + " " + c.abstract
        overlap = plan.support(text)
        if not overlap and not semantic_score:
            continue
        coverage = covered_fact_requests(c.abstract, plan)
        if plan.fact_requests and not coverage:
            continue
        source = plan.source_assessment(c.base_uri, c.category, text, cfg)
        if not source.accepted:
            continue
        if plan.foreign and overlap < STRONG_LEXICAL_SUPPORT and not semantic_score:
            continue
        support = overlap if overlap else semantic_score
        score = plan.rank_score(
            c.ranked_score, support, c.base_uri, text, source, labelled=bool(label)
        )
        ranked.append([score, c, terms(text), 0.0, support, semantic_score, coverage])
    if ranked and not plan.fact_requests:
        best = max(item[4] for item in ranked)
        ranked = [
            item
            for item in ranked
            if item[4] >= max(MIN_LEXICAL_SUPPORT, best * SUPPLEMENT_RATIO)
        ]
        # A reviewed project card already contains dated sources and qualifications.
        # Do not append older event fragments on the same weak lexical evidence.
        if any("/rooms/" in item[1].base_uri and item[4] >= 0.33 for item in ranked):
            ranked = [item for item in ranked if item[1].category != "events"]
    picked = []
    covered = set(request.get("pinned_coverage", ()))
    while ranked:
        ranked.sort(
            key=lambda item: (len(item[6] - covered), item[0] - 0.18 * item[3]),
            reverse=True,
        )
        best = ranked.pop(0)
        if best[3] > 0.78 and not best[6] - covered:
            continue
        picked.append(best)
        covered.update(best[6])
        for item in ranked:
            aa, bb = best[2], item[2]
            item[3] = max(item[3], len(aa & bb) / max(1, len(aa | bb)))
    semantic_uris = request.setdefault("semantic_uris", {})
    for item in picked:
        semantic_score = item[5]
        if semantic_score >= NATIVE_SEMANTIC_FLOOR and item[4] == semantic_score:
            semantic_uris[item[1].base_uri] = semantic_score
    return [replace(item[1], ranked_score=item[0]) for item in picked]


def clip_entry(entry, budget):
    from openviking.retrieve.context_assembler.render import fragment_tokens

    if fragment_tokens(entry) <= budget:
        return entry
    original = entry.text
    lo, hi = 0, len(original)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = replace(entry, text=original[:mid] + "\n[展开原文见来源URI]")
        if fragment_tokens(trial) <= budget:
            lo = mid
        else:
            hi = mid - 1
    if lo < 40:
        return None
    return replace(entry, text=original[:lo] + "\n[展开原文见来源URI]")


async def prepare_prefetched(original_prefetch, **kwargs):
    contents = await original_prefetch(**kwargs)
    request = REQUEST.get()
    if not request:
        return contents
    cfg = storage.config()
    plan = request.get("plan") or analyze(
        request["query"], cfg, project_hint=request.get("project", "")
    )

    facts = {p.get("facts_uri") for p in cfg.get("projects", {}).values()}
    for uri, text in list(contents.items()):
        body = storage.clean(text)
        verified = not plan.fact_requests and semantic_handoff_allows(
            request, uri, body
        )
        contents[uri] = prepare_body(
            body,
            uri,
            plan,
            project_fact=not verified and uri in facts,
            verified_semantic=verified,
            lexical_only=verified,
        )
    return contents


async def resume_snapshot(
    service, ctx, sid, raw, state, cfg, deadline, retrieval_degraded
):
    from openviking.retrieve.context_assembler.models import AssembledEntry

    native_snapshot = ""
    checkpoint = state.get("checkpoint", {})
    if not checkpoint:
        try:
            session = await budgeted(
                "resume_session_lookup",
                service.sessions.get(sid, ctx, auto_create=False),
                deadline,
                0.35,
            )
            context = await budgeted(
                "resume_context_read",
                session.get_context_for_search(raw, max_messages=0),
                deadline,
                0.35,
            )
            native_snapshot = storage.clean(context.get("latest_archive_overview", ""))
        except TimeoutError as exc:
            retrieval_degraded.append("resume_context_budget_exhausted")
            note_problem("working_memory_read", exc)
        except Exception as exc:
            note_problem("working_memory_read", exc)
    pieces = ["会话工作快照；用于接续任务，历史进度不等于当前执行状态。"]
    focused = focus_text(state)
    if focused:
        pieces.append(storage.clean(focused))
    elif state.get("user_request") and state["user_request"] != raw:
        pieces.append(
            "最近用户要求（"
            + state.get("request_at", "")
            + "）："
            + state["user_request"][:220]
        )
    if checkpoint:
        pieces.append(
            "主代理工作检查点（"
            + checkpoint["verified_at"]
            + "；"
            + checkpoint["status"]
            + "）："
            + checkpoint["summary"][:800]
            + "\n待办："
            + "；".join(checkpoint.get("pending", []))
            + "\n证据："
            + "；".join(checkpoint["artifacts"][:3])
        )
    elif native_snapshot:
        pieces.append(
            "会话摘要（助手报告，待独立验证）：\n" + concise_summary(native_snapshot)
        )
    if len(pieces) > 1:
        content = "\n\n".join(pieces)
        uri = f"viking://user/{cfg['user']}/memories/entities/work-state/{sid}.md"
        try:
            await budgeted(
                "resume_snapshot_write",
                service.sessions._viking_fs.write_file(
                    uri=uri, content=content, ctx=ctx
                ),
                deadline,
                0.25,
            )
        except TimeoutError as exc:
            retrieval_degraded.append("resume_snapshot_write_budget_exhausted")
            note_problem("resume_snapshot_write", exc)
        except Exception as exc:
            note_problem("resume_snapshot_write", exc)
        entry = AssembledEntry(
            uri=uri,
            category="entities",
            score=1,
            detail="full",
            text=content,
            origin="work_snapshot",
        )
        return clip_entry(entry, cfg.get("snapshot_tokens", 550)), bool(native_snapshot)
    return None, bool(native_snapshot)


async def assemble_work_context(original, *, service, ctx, params):
    cfg = storage.config()
    if (
        not storage.enabled(ctx, cfg)
        or params.image_url
        or params.search_type != "semantic"
    ):
        return await original(service=service, ctx=ctx, params=params)
    from openviking.retrieve.context_assembler.models import (
        AssembledEntry,
        AssembleResult,
    )
    from openviking.retrieve.context_assembler.render import (
        fragment_tokens,
        render_context,
    )

    raw = params.query.strip()
    sid = params.session_id or ""
    deadline = (
        REQUEST_DEADLINE.get()
        or time.monotonic()
        + max(0.1, float(cfg.get("context_deadline_seconds", 3.0)))
        - 0.1
    )
    plan = analyze(
        raw,
        cfg,
        project_hint=storage.choose_project(raw, sid, ctx.actor_peer_id or "", cfg),
    )
    project = plan.project
    resume = plan.resume

    scoped = bool(params.filter)
    allow_pins = not scoped and (
        params.quotas is None or params.quotas.get("entities", 0) > 0
    )
    state = {}
    pins = []
    native_snapshot_used = False
    result = AssembleResult(stats={})
    retrieval_degraded = []
    valid_sid = bool(storage.SESSION_ID.fullmatch(sid))
    if valid_sid:
        try:
            state = storage.mutate_state(sid, raw, project)
        except (OSError, ValueError) as exc:
            note_problem("working_memory_state", exc)
    # Greetings and content-free prompts have no historical retrieval need.
    if not plan.allow_recall or (not plan.focus and not resume):
        result.stats = {
            "used_tokens": 0,
            "work_memory": {
                "project": project,
                "snapshot": False,
                "project_facts": False,
                "retrieval_vlm_calls": 0,
                "empty_reason": "requires_live_evidence"
                if plan.requires_live and not plan.historical
                else "no_historical_subject",
                "policy_reason": plan.reason,
            },
        }
        return result
    # Resume gets the task snapshot. A named lookup can get a relevant fact card instead.
    if resume and valid_sid and allow_pins:
        snapshot, native_snapshot_used = await resume_snapshot(
            service, ctx, sid, raw, state, cfg, deadline, retrieval_degraded
        )
        if snapshot:
            pins.append(snapshot)
    if project and not resume and allow_pins:
        uri = cfg["projects"][project]["facts_uri"]
        try:
            text = await budgeted(
                "project_facts_read", service.fs.read(uri, ctx=ctx), deadline, 0.75
            )
            if isinstance(text, bytes):
                text = text.decode()
            text = select_facts(storage.clean(text), plan)
            if text:
                entry = clip_entry(
                    AssembledEntry(
                        uri=uri,
                        category="entities",
                        score=1,
                        detail="full",
                        text=text,
                        origin="project_facts",
                    ),
                    cfg.get("facts_tokens", 550),
                )
                if entry:
                    pins.append(entry)
        except TimeoutError as exc:
            retrieval_degraded.append("project_facts_budget_exhausted")
            note_problem("project_facts_read", exc)
        except Exception as exc:
            note_problem("project_facts_read", exc)
    if plan.fact_requests:
        pins = [
            entry
            for entry in pins
            if not any(
                entry.uri == root or entry.uri.startswith(root.rstrip("/") + "/")
                for root in params.exclude_uris
            )
        ]
    remaining = max(0, params.max_tokens - sum(fragment_tokens(e) for e in pins))
    # Pure continuation returns its task snapshot; it must not pad empty slots
    # with globally similar stories. Other requests keep their actual subject.
    pinned_coverage = set().union(
        *(covered_fact_requests(entry.text, plan) for entry in pins)
    )
    fact_covers_subject = (
        len(pinned_coverage) == len(plan.fact_requests)
        if plan.fact_requests
        else any(
            entry.origin == "project_facts"
            and plan.support(entry.text) >= STRONG_LEXICAL_SUPPORT
            for entry in pins
        )
    )

    fact_covers_subject = fact_covers_subject and plan.as_of is None
    relation_notes = {}
    retrieval_request = {
        "query": raw,
        "focus": raw,
        "project": project,
        "project_confident": plan.project_confident,
        "plan": plan,
        "semantic_uris": {},
        "pinned_coverage": pinned_coverage,
    }
    if (not resume or scoped or not allow_pins) and not fact_covers_subject:
        marker = REQUEST.set(retrieval_request)
        try:
            request_budget = deadline - time.monotonic()
            if request_budget > 0.05:
                result = await bounded_native(
                    original(
                        service=service,
                        ctx=ctx,
                        params=replace(
                            params,
                            max_tokens=max(64, remaining),
                            query_expansion="off",
                            rewrite=False,
                        ),
                    ),
                    deadline - 0.2,
                )
            else:
                raise TimeoutError("native retrieval budget exhausted")
        except TimeoutError:
            retrieval_degraded.append("native_retrieval_budget_exhausted")
            note_problem("native_retrieval_budget", TimeoutError())
        finally:
            REQUEST.reset(marker)
    if (
        not fact_covers_subject
        and remaining >= 128
        and not scoped
        and allow_pins
        and any(e.origin != "work_snapshot" for e in [*pins, *result.entries])
    ):
        try:
            links = await budgeted(
                "explicit_relation_read",
                linked_entries(
                    state=state,
                    project=project,
                    seeds=[*pins, *result.entries],
                    plan=plan,
                    cfg=cfg,
                    service=service,
                    ctx=ctx,
                    excluded=params.exclude_uris,
                    notes=relation_notes,
                ),
                deadline,
                0.35,
            )
            result.entries.extend(links)
            result.stats["explicit_relations"] = {
                "accepted": len(links),
                "hops": 1,
                "vlm_calls": 0,
            }
        except TimeoutError as exc:
            retrieval_degraded.append("explicit_relation_budget_exhausted")
            note_problem("explicit_relation_read", exc)
        except Exception as exc:
            note_problem("explicit_relation_read", exc)
    seq = state.get("recall_seq", 0)
    served = state.get("served", {})
    cooldown = 0 if plan.fact_requests else params.dedup_turns
    entries = []
    seen = set()
    signatures = []
    suppressed = 0
    used_tokens = 0
    reviewed = max(
        (plan.support(entry.text) for entry in pins if entry.origin == "project_facts"),
        default=0.0,
    )
    delivered_coverage = set()
    for entry in [*pins, *result.entries]:
        if len(entries) >= min(5, params.limit):
            break
        if entry.uri in seen:
            continue
        entry = replace(entry, text=storage.clean(entry.text))
        semantic_allowed = entry.origin != "work_snapshot" and semantic_handoff_allows(
            retrieval_request, entry.uri, entry.text
        )
        if entry.origin != "work_snapshot":
            prepared = prepare_body(
                entry.text,
                entry.uri,
                plan,
                verified_semantic=semantic_allowed,
            )
            if not prepared:
                continue
            entry = replace(entry, text=prepared)
        if entry.origin != "work_snapshot" and (
            archive_only(entry.uri) or not entry.text.strip() or entry.detail == "uri"
        ):
            continue
        if any(
            entry.uri == root or entry.uri.startswith(root.rstrip("/") + "/")
            for root in params.exclude_uris
        ):
            continue
        if entry.origin not in {"project_facts", "work_snapshot"}:
            if not plan.accepts_supplement(
                entry.text + " " + entry.uri.rsplit("/", 1)[-1],
                reviewed,
                semantic_allowed,
            ):
                continue
        if plan.fact_requests and (
            not plan.source_assessment(
                entry.uri, entry.category, entry.text, cfg
            ).accepted
            or not covered_fact_requests(entry.text, plan) - delivered_coverage
        ):
            continue
        if entry.uri in relation_notes:
            entry = replace(
                entry,
                text=storage.clean(relation_notes[entry.uri]) + "\n\n" + entry.text,
            )
        elif plan.as_of is not None:
            entry = replace(
                entry, text="[检索候选；该日期的有效性未核实]\n\n" + entry.text
            )
        signature = entry_signature(entry)
        if (
            not resume
            and cooldown > 0
            and signature in served
            and 0 <= seq - served[signature] < cooldown
        ):
            suppressed += 1
            continue
        clipped = clip_entry(entry, params.max_tokens - used_tokens)
        if clipped:
            covered_by_entry = covered_fact_requests(clipped.text, plan)
            if plan.fact_requests and not covered_by_entry - delivered_coverage:
                continue
            delivered_coverage.update(covered_by_entry)
            entries.append(clipped)
            used_tokens += fragment_tokens(clipped)
            seen.add(entry.uri)
            signatures.append(signature)
    if valid_sid and cooldown > 0:
        try:
            storage.record_served(sid, seq, signatures)
        except (OSError, ValueError):
            pass
    if plan.fact_requests:
        missing = [
            index + 1
            for index in range(len(plan.fact_requests))
            if index not in delivered_coverage
        ]
        result.stats["fact_coverage"] = {
            "requested": len(plan.fact_requests),
            "missing": missing,
        }
        if missing and entries:
            numbers = "、".join(str(index) for index in missing)
            marker = f"\n\n[记忆候选未覆盖第{numbers}项；请核对原始来源。]"
            annotated = replace(entries[-1], text=entries[-1].text + marker)
            new_total = (
                used_tokens - fragment_tokens(entries[-1]) + fragment_tokens(annotated)
            )
            if new_total <= params.max_tokens:
                entries[-1] = annotated
                used_tokens = new_total
    result.entries = entries
    result.rendered = render_context(entries) if params.render else ""
    result.digest = ""
    result.stats["used_tokens"] = used_tokens
    result.stats["work_memory"] = {
        "project": project,
        "snapshot": any(e.origin == "work_snapshot" for e in entries),
        "native_summary": native_snapshot_used,
        "project_facts": any(e.origin == "project_facts" for e in entries),
        "query_anchored": resume and bool(state.get("user_request")),
        "retrieval_vlm_calls": 0,
        "repeat_suppressed": suppressed,
        "precision_gate": True,
        "policy_reason": plan.reason,
    }
    if retrieval_degraded:
        result.stats["work_memory"]["degraded_timeout"] = retrieval_degraded
    assert result.stats["used_tokens"] <= params.max_tokens
    return result
