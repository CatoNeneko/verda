"""Deterministic evidence selection; no generation, embeddings or source writes."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

from .fact_terms import DATE_FIELD_NAMES, SECTION_FIELD_RE
from .recall_policy import MIN_LEXICAL_SUPPORT, QueryPlan

# These are document-section roles, independent of the user's wording. Facts
# and provenance remain together on disk; only fact sections enter recall.
_METADATA_HEADINGS = frozenset(
    {
        "来源",
        "来源与追溯",
        "原始来源",
        "出处",
        "出处与追溯",
        "参考资料",
        "参考文献",
        "导航",
        "source",
        "sources",
        "reference",
        "references",
        "provenance",
        "navigation",
    }
)


def is_evidence_section(heading):
    for line in str(heading or "").splitlines():
        name = re.sub(r"^#{1,6}\s+", "", line.strip()).strip(" ：:").casefold()
        if name in _METADATA_HEADINGS:
            return False
    return True


def evidence_content(text):
    """Filter structured metadata sections without changing stored sources."""
    headings = []
    out = []
    for line in text.splitlines(keepends=True):
        match = re.match(r"^(#{1,6})\s+(.+)", line.strip())
        if match:
            depth = len(match[1])
            headings = [h for h in headings if len(h.split(" ", 1)[0]) < depth]
            headings.append(line.strip())
        if is_evidence_section("\n".join(headings)):
            out.append(line)
    return "".join(out).strip()


SUPERSEDED = re.compile(
    r"已被.{0,80}取代|已由.{0,80}取代|(?:有效性|状态)[：:]\s*已取代|[（(]已取代[）)]|已撤销|已失效|superseded|revoked",
    re.I,
)
_SNAPSHOT_TAG = re.compile(
    r"^\s*\[(?:历史(?:阶段)?快照|historical snapshot)[^\]]*\]", re.I
)
_EXPLICIT_STATUS = re.compile(
    r"^\s*(?:(?:[-*+]\s*)?(?:有效性|状态|status)[：:]\s*"
    r"(?:已取代|已撤销|已失效|已过期|superseded|revoked|expired)|"
    r"(?:[-*+]\s*)?\[(?=[^\]]*(?:已取代|已撤销|已失效|已过期|superseded|revoked|expired))[^\]]+\])",
    re.I,
)
_DATE_FIELD_RE = re.compile(
    rf"^[ \t]*(?:[-*+][ \t]+)?(?P<field>{DATE_FIELD_NAMES})[：:][ \t]*"
    r"(?P<date>\d{4}-\d{2}-\d{2})[ \t]*$",
    re.I | re.M,
)
_END_DATE_RE = re.compile(
    r"(?:\b(?:superseded|revoked|expired)\s+on\s+"
    r"|(?:取代|失效|过期)日期[：:]\s*)(\d{4}-\d{2}-\d{2})\b", re.I
)
SUGGESTION = re.compile(
    r"建议|提议|调研|调查|计划|讨论中|尚未(?:实现|实施|验证|实测)|待验证|hypothesis|proposal",
    re.I,
)


def evidence_label(text, uri):
    """Only use declared provenance/status; never infer validity from age alone."""
    name = uri.rsplit("/", 1)[-1]
    declared = "\n".join(text.splitlines()[:4])
    # Status is scoped to the selected section. A current card may mention an
    # earlier value was replaced without making the current card obsolete.
    dated = re.search(r"/events/(\d{4})/(\d{2})/(\d{2})/", uri)
    if dated:
        date = "-".join(dated.groups())
        kind = (
            "历史建议；待验证"
            if SUGGESTION.search(name + " " + declared)
            else "历史事件"
        )
        return f"[来源类型：{kind}；记录日期：{date}；不证明当前状态或当前执行授权]"
    if _source_snapshot_superseded(text):
        return "[有效性：已取代；仅供历史查证，不作当前执行要求]"
    for line in text.splitlines()[:8]:
        stripped = re.sub(r"^#{1,6}\s*", "", line.strip())
        if _EXPLICIT_STATUS.match(stripped) or (
            line.lstrip().startswith("#") and SUPERSEDED.search(stripped)
        ):
            return "[有效性：已取代；仅供历史查证，不作当前执行要求]"
    return ""


def _source_snapshot_superseded(text):
    """A leading historical-snapshot declaration can scope the whole card."""
    _, chunks = paragraphs_with_headings(text)
    if not chunks:
        return False
    heading, body = chunks[0]
    return (
        not heading
        and bool(_SNAPSHOT_TAG.match(body))
        and bool(SUPERSEDED.search(body))
    )


def _section_superseded(heading, body):
    if SUPERSEDED.search(heading):
        return True
    for line in body.splitlines():
        if _EXPLICIT_STATUS.match(line):
            return True
        if not SECTION_FIELD_RE.match(line):
            break
    return False


def section_applicable(heading, body, plan):
    superseded = _section_superseded(heading, body)
    if superseded and not plan.historical:
        return False
    if plan.historical and plan.as_of is None:
        return True
    dates: dict[str, list[date]] = {}
    try:
        for match in _DATE_FIELD_RE.finditer(body):
            field = re.sub(r"[ -]", "_", match["field"].casefold())
            dates.setdefault(field, []).append(date.fromisoformat(match["date"]))
        ends = dates.get("valid_until", [])
        if superseded:
            match = _END_DATE_RE.search(body)
            if match:
                ends.append(date.fromisoformat(match[1]))
    except ValueError:
        return False
    starts = dates.get("valid_from") or dates.get("date", []) + dates.get("日期", [])
    if not starts and not ends:
        return True
    target_date = plan.as_of or datetime.now(timezone.utc).date()
    return all(start <= target_date for start in starts) and all(
        target_date < end for end in ends
    )


def paragraphs_with_headings(text):
    """Carry enclosing headings with a paragraph; do not relabel old text latest."""
    text = evidence_content(text)
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"<!--\s*MEMORY_FIELDS\b.*?-->", "", text, flags=re.S)
    scopes = [(0, "", [])]
    title = ""
    intro = []
    out = []

    def append_paragraph(lines):
        value = "\n".join(lines).strip()
        if not value:
            return
        heading = "\n".join(label for _, label, _ in scopes if label)
        if (
            not heading
            and not intro
            and re.match(r"^(?:共同)?来源[：:]|^维护说明[：:]", value)
            and len(value) < 700
        ):
            intro.append(value)
        elif all(SECTION_FIELD_RE.match(line) for line in value.splitlines()):
            scopes[-1][2].append(value)
        else:
            metadata = [field for _, _, fields in scopes for field in fields]
            out.append((heading, "\n".join([*metadata, value])))

    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        body = []
        for line in lines:
            match = re.match(r"^(#{1,6})\s+(.+)", line)
            if match:
                if body:
                    append_paragraph(body)
                    body = []
                depth = len(match[1])
                if depth == 1 and not title:
                    title = line
                while scopes[-1][0] >= depth:
                    scopes.pop()
                scopes.append((depth, line if depth > 1 else "", []))
            else:
                body.append(line)
        append_paragraph(body)
    return "\n\n".join([x for x in [title, *intro] if x]), out


def select_facts(text: str, plan: QueryPlan, *, lexical_only: bool = False) -> str:
    head, chunks = paragraphs_with_headings(text)
    if _source_snapshot_superseded(text) and not plan.historical:
        return ""
    requests = plan.fact_requests
    score_section = plan.support if lexical_only else plan.section_support
    scored = []
    for index, (heading, body) in enumerate(chunks):
        scoped = "\n\n".join(x for x in (heading, body) if x)
        if not section_applicable(heading, body, plan):
            continue
        score = score_section(scoped)
        if score:
            coverage = plan.covered_requests(scoped) if requests else frozenset()
            scored.append((score, index, scoped, coverage))
    if not scored:
        return ""
    if not requests:
        floor = max(MIN_LEXICAL_SUPPORT, max(row[0] for row in scored) * 0.8)
        scored = [row for row in scored if row[0] >= floor]
    picked = []
    covered: set[int] = set()
    limit = len(requests) or 2
    while scored and len(picked) < limit:
        best = max(scored, key=lambda row: (len(row[3] - covered), row[0], -row[1]))
        newly_covered = best[3] - covered
        if requests and not newly_covered:
            break
        picked.append(best)
        covered.update(newly_covered)
        scored.remove(best)
    return (
        "\n\n".join(x for x in [head, *(row[2] for row in picked)] if x)
        if picked
        else ""
    )


def covered_fact_requests(text: str, plan: QueryPlan) -> set[int]:
    """Count support only within complete delivered evidence sections."""
    found = set()
    if plan.fact_requests:
        for heading, body in paragraphs_with_headings(text)[1]:
            if "[展开原文见来源URI]" in body:
                continue
            if not section_applicable(heading, body, plan):
                continue
            found.update(plan.covered_requests(heading + "\n" + body))
    return found


def prepare_body(
    text: str,
    uri: str,
    plan: QueryPlan,
    *,
    project_fact: bool = False,
    verified_semantic: bool = False,
    lexical_only: bool = False,
) -> str:
    text = evidence_content(text)
    if not text:
        return ""
    historical = plan.historical
    label = evidence_label(text, uri)
    if "/events/" in uri and plan.requires_live and not historical:
        return ""
    if project_fact or "/projects/" in uri:
        source_is_superseded = _source_snapshot_superseded(text)
        if source_is_superseded and not historical:
            return ""
        selected = select_facts(text, plan, lexical_only=lexical_only)
        if selected:
            text = selected
        elif verified_semantic and len(text) <= 1600 and not source_is_superseded:
            # An authorized, hash-checked, bounded passage can lack lexical
            # overlap in a foreign-language query. Preserve that passage only;
            # native scores and whole-card bodies never set this flag.
            if any(
                not section_applicable(heading, body, plan)
                for heading, body in paragraphs_with_headings(text)[1]
            ):
                return ""
        else:
            return ""
        # Re-evaluate after section selection so the validity belongs to the
        # actual selected evidence rather than the first unrelated section.
        label = evidence_label(text, uri)
        if source_is_superseded and historical:
            label = "[有效性：已取代；仅供历史查证，不作当前执行要求]"
    if label and label not in text:
        text = label + "\n\n" + text
    return text


def archive_only(uri):
    return bool(
        re.search(
            r"/(?:archive|work-state)/|/(?:palace-index|source-index|classification-index)\.md$",
            uri,
        )
    )


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def source_text(raw: str | bytes) -> str:
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    from .vdra_state import clean

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return clean(MemoryFileUtils.read(raw).content)


def project_for_uri(uri: str, config: dict) -> str:
    if (
        not uri.endswith(".md")
        or uri.rsplit("/", 1)[-1].startswith(".")
        or archive_only(uri)
    ):
        return ""
    for name, project in config.get("projects", {}).items():
        main = project.get("facts_uri", "")
        if main and (uri == main or uri.startswith(main.removesuffix(".md") + "/")):
            return name
    return ""
