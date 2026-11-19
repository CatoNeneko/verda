"""Pure, deterministic recall policy shared by local memory callers.

This module intentionally has no runtime, storage, provider, or OpenViking
dependencies. QueryPlan precomputes the query-side features once so evidence
checks can reuse them across source candidates and paragraph clauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping
from urllib.parse import unquote

from .fact_terms import evidence_topics, fact_topics

MIN_LEXICAL_SUPPORT = 0.16
STRONG_LEXICAL_SUPPORT = 0.60
NATIVE_SEMANTIC_FLOOR = 0.82
SUPPLEMENT_RATIO = 0.85


# ----------------------------- query vocabulary ----------------------------

_TERM_STOP = {
    "现在",
    "之前",
    "这个",
    "那个",
    "一个",
    "如何",
    "什么",
    "已经",
    "进行",
    "需要",
    "可以",
    "我们",
    "本龙",
    "用户",
    "助手",
    "历史",
    "记忆",
    "source",
    "summary",
    "the",
    "and",
    "for",
    "with",
}


def _raw_terms(text: str) -> set[str]:
    """Candidate-v11 tokenization, kept private for module initialization."""
    text = re.sub(r"\[历史[^\]]*\]", "", str(text or "").lower())
    words = set(re.findall(r"[a-z][a-z0-9_+-]{2,}", text))
    words.update(re.findall(r"(?<![a-z0-9])(?:fp|fn|tp|tn|lr|ce|kl)(?![a-z0-9])", text))
    words.update(re.findall(r"(?<!\d)\d{2,}(?!\d)", text))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words - _TERM_STOP


_FOCUS_STOP = frozenset(
    _raw_terms(
        "之前 现在 刚才 今天 明天 这个 那个 这些 那些 什么 怎么 如何 为什么 是否 有没有 能不能 会不会 已经 需要 希望 应该 可以 还是 同时 一下 继续 主人 本龙 用户 助手 历史 记忆 系统 信息 内容 消息 事情 问题 文件 本机 本地 项目 对话 聊天 使用 进行 检查 完成 更新 改进 修复 处理 说明 认为 感觉 觉得 知道 相关 不了 不是 不能 不要 的话 了吗 了没 哪个 哪些 在哪 这里 那里 说的 提到 谢谢 晚安 你好 早上好 openviking codex python json api model memory token tokens"
    )
)
_DOMAIN_TERMS = frozenset(
    _raw_terms("毕业论文 论文 BUSI AMOS Hinge nnU-Net nnUNet OpenViking")
)

_RESUME_RE = re.compile(
    r"^(?:(?:那就|那|好[的吧]?[,， ]*)?(?:继续|接着)(?:吧|做|推进|执行|修复|处理|上次的任务)?|(?:文件|结果|进度)(?:呢|在哪|怎么样|如何)|(?:改|做|修|弄|完成)好?了(?:吗|没)|(?:恢复|找回)(?:上次的)?(?:任务|进度|上下文)|(?:上次|之前)(?:做|进行)到哪了?)[。？?！! ]*$",
    re.S,
)
_CASUAL_RE = re.compile(
    r"^(?:你好|早上好|下午好|晚上好|晚安|谢谢(?:你|啦)?|好的|知道了|明白了|嗯|哈哈+|[哈嘿哼]+|你是(?:大)?肥龙)[。？?！!~～ ]*$"
)

_HISTORY_SUBJECT_RE = re.compile(
    r"之前|上次|以前|原来|原定|当时|当初|曾经|后来|那次|旧对话|旧代码|记得|约定|结论|验收|结果|经验|教训|故障|中断|预检|检查过|优化后|耗时|提速|数值|断点|保留|封存|暂停|永久删除|误报|等召回|种子|辅助头|评测器|HD95|计算效率|机制设计|科学门槛|[MNOP]轮|以后.{0,20}要求|讨论里|讨论中的纠正",
    re.I,
)
_RETENTION_HISTORY_RE = re.compile(
    r"(?:遗忘|忘记|丢失|丢了).{0,18}(?:什么|哪些|哪条|哪项|事实|记录|信息|要求|决定|约定|进度)|"
    r"(?:什么|哪些|哪条|哪项|重要事实|记录|信息|要求|决定|约定|进度).{0,18}(?:遗忘|忘记|丢失|丢了)|"
    r"(?:遗忘|忘记).{0,12}(?:机制|策略|方法|设计)|(?:机制|策略|方法).{0,12}(?:遗忘|忘记)",
    re.I,
)
_PERSONAL_SUBJECT_RE = re.compile(
    r"人格|自称|称呼|偏好|喜欢|习惯|账本|本机.{0,6}模型|模型.{0,6}暂停"
)
_CURRENT_STATE_RE = re.compile(
    r"(?:目前|当前|现在|此刻).{0,28}(?:活跃|非活跃|不活跃|运行|执行|在线|离线|状态|暂停|停止|完成|空闲)|(?:活跃|运行).{0,12}(?:状态|中|不是)",
    re.I,
)
_CURRENT_REQUEST_RE = re.compile(
    r"^(?:我的要求(?:是|为)|(?:本次|这次|当前)(?:的)?(?:要求|目标|需求)(?:是|为)|目标是|需求是|我希望|我打算|我想要)",
    re.I,
)
_CURRENT_DECLARATION_RE = re.compile(
    r"^(?:从现在起|从今天起|我现在(?:决定|要求|希望)|现在我(?:决定|要求|希望)|把我这条新|这条新偏好|新指令|按这条新指令|from now on|starting today|i now (?:decide|want|authorize)|remember this new preference|これから|今から|新しい指示)",
    re.I,
)
_CURRENT_RULE_SUBJECT_RE = re.compile(
    r"规则|约束|流程|原则|权限|默认|要求|偏好|习惯", re.I
)
_QUESTION_FORM_RE = re.compile(
    r"[?？]|是什么|有哪些|什么(?:规则|要求|偏好|目标|方法|结构|配置|指标|结论)?|如何|怎么样|是否|能否|可否"
)
_FRESH_WORK_RE = re.compile(
    r"^(?:请|帮忙|帮|现在|这次|这轮|今晚|先|不要|别|同时|继续|尽可能|尽量|做好|把|修复|检查|整理|优化|完成|我给你|授权|我希望|希望)"
)
_LOOKUP_VERB_RE = re.compile(
    r"查|检索|找回|查找|回顾|根据.{0,12}(?:记录|讨论|任务卡)|(?:记录|讨论|维护记录|任务卡).{0,12}(?:中|里|所载|记载)",
    re.I,
)
_ENGLISH_PAST_RECALL_RE = re.compile(
    r"\b(?:what|which|who|where|when|how)\b.{0,100}\b(?:did|was|were|had)\b.{0,100}"
    r"\b(?:user|history|historical|record|note|deployment|deployed|discussion|bottleneck|restriction|run|queue|route|monitor|computer|project|task|training|change|remove|runtime|version|technology|collection|identified)\b"
    r"|\bdid\b.{0,140}\b(?:ask|want|use|go|change|remove|identify|constrain)\b",
    re.I,
)
_ENGLISH_SOURCE_REFERENCE_RE = re.compile(
    r"\b(?:according to|historical|deployment note|training history|project record|user asked|user wanted|authorization (?:record|history|note|constraints))\b",
    re.I,
)
_JAPANESE_HISTORY_RE = re.compile(
    r"記録|履歴|以前|当時|過去|前回|ユーザー.{0,8}(?:要求|希望|指定|決定)|記載|議論|導入|展開"
)
_RECOVERY_EXTRA_RE = re.compile(r"归档|回顾|找回|recover|archiv", re.I)
_PROJECT_ARTIFACT_LOOKUP_RE = re.compile(
    r"(?:给出|查|检索|找回|列出|说明|核对).{0,70}(?:任务|实验|训练|配置|参数|seed|batch|学习率|精度)",
    re.I,
)
_NAMED_PROJECT_QUESTION_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9.+_-]{1,}\b.{0,16}(?:项目|代码|讨论|实验|训练)", re.I
)

_NUMERIC_DATE_TIME_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d{2}(?::\d{2})?")
_NUMBER_RE = re.compile(r"(?<!\d)\d{2,}(?!\d)")
_STAGE_RE = re.compile(r"(?<![a-zA-Z0-9_])([MNOP])(?![a-zA-Z0-9_])", re.I)


def terms(text: str) -> set[str]:
    """Return candidate-v11 lexical tokens for text."""
    return _raw_terms(text)


def focus_terms(query: str) -> set[str]:
    query = str(query or "").lower()
    if query.strip(" 。？?!！") in {"论文", "毕业论文"}:
        return terms(query)
    substitutions = (
        (
            r"垃圾(?:信息|消息|内容)?|无关(?:信息|消息|内容)?|上下文污染|噪声",
            "垃圾 无关 污染 过滤",
        ),
        (r"遗忘机制|忘记机制|怎么遗忘", "过期 归档 降权"),
        (r"为了什么|原(?:本|定|来)(?:的)?目标", "目标"),
        (r"为什么(?:要|会)(?:看|研究|选择|评估|探索)|有什么用|用来做什么", "目标"),
        (r"用量账本", "账本"),
        (r"结束", "完成"),
        (r"部分重建", "部分重构"),
        (r"等召回", "匹配召回"),
        (r"慢(?:了)?(?:十|10)倍|性能瓶颈", "耗时 瓶颈"),
        (
            r"前([一二三四五六七八九])项",
            lambda m: "前" + str("一二三四五六七八九".index(m[1]) + 1) + "项",
        ),
    )
    for pattern, replacement in substitutions:
        query = re.sub(pattern, replacement, query)
    query = re.sub(
        r"记忆系统|记忆插件|工作记忆|长期记忆|毕业论文|论文|openviking|codex",
        " ",
        query,
    )
    query = re.sub(
        r"哪一次|哪一条|哪一天|哪一份|哪一个|哪次|哪条|哪天|哪份|之前|上次|以前|现在|原来|当时|当初|后来|为什么|怎么|如何|是否|有没有|能不能|会不会|有无|什么|哪些|多少|我们|本龙|用户|助手|需要|希望|应该|可以|不能|不要|已经|进行|这个|那个|一些|一下|还是|同时|关于|有关|要看|看看|看一下|到底|证明|稳定|全部|仍然|继续|比较|数字|结果|结论|最终|仍|的|吗|呢|了|有|在|和|与|及|对|要|能|不|还|是|就",
        " ",
        query,
    )
    focus = terms(query) - _FOCUS_STOP
    specific = focus - terms("确定 规则 记录 流程 方法 方式")
    return specific or focus


def evidence_terms(text: str) -> set[str]:
    for old, new in (
        ("等召回", "匹配召回"),
        ("部分重建", "部分重构"),
        ("结束", "完成"),
    ):
        text = str(text or "").replace(old, new)
    return terms(text)


def _alias_pattern(name: str) -> str:
    pattern = re.escape(name)
    if name[0].isascii() and name[0].isalnum():
        pattern = r"(?<![a-zA-Z0-9_])" + pattern
    if name[-1].isascii() and name[-1].isalnum():
        pattern += r"(?![a-zA-Z0-9_])"
    return pattern


def _project_from_aliases(
    text: str, cfg: Mapping[str, Any] | None, *, single_project: bool = False
) -> str:
    scores = []
    text = str(text or "")
    for key, project in (cfg or {}).get("projects", {}).items():
        score = sum(
            max(1, len(alias))
            for alias in project.get("aliases", [])
            if isinstance(alias, str)
            and alias.strip()
            and re.search(_alias_pattern(alias), text, re.I)
        )
        if score:
            scores.append((score, key))
    scores.sort(reverse=True)
    return (
        scores[0][1]
        if scores
        and (len(scores) == 1 or (not single_project and scores[0][0] > scores[1][0]))
        else ""
    )


def document_project(text: str, cfg: Mapping[str, Any] | None) -> str:
    """Compatibility wrapper for alias-based project detection in text."""
    return _project_from_aliases(text, cfg)


def _project_query(query: str, cfg: Mapping[str, Any] | None) -> tuple[str, bool]:
    project = _project_from_aliases(query, cfg)
    if project:
        return project, True
    # Preserve candidate behavior: only a unique/highest alias match is
    # confident. The returned project is empty when aliases tie.
    return "", False


def foreign_historical_intent(query: str) -> bool:
    return bool(
        (
            _ENGLISH_PAST_RECALL_RE.search(query)
            or _ENGLISH_SOURCE_REFERENCE_RE.search(query)
            or _JAPANESE_HISTORY_RE.search(query)
        )
        and re.search(r"[A-Za-z]|[\u3040-\u30ff]", query)
    )


def _history_cue(query: str) -> bool:
    """Shared source/past vocabulary used by every historical consumer."""
    # A lookup subject such as "preferences" or "pause" does not itself ask
    # for old events. Keep explicit past/source intent separate from topic.
    return bool(
        requested_date(query)
        or _ENGLISH_PAST_RECALL_RE.search(query)
        or _ENGLISH_SOURCE_REFERENCE_RE.search(query)
        or _JAPANESE_HISTORY_RE.search(query)
        or re.search(
            r"之前|上次|以前|原来|当时|当初|曾经|后来|历史|旧版|取代|变更|沿革|演变|回顾|previous|history|historical|archive|archiv",
            query,
            re.I,
        )
    )


def _current_declaration(query: str) -> bool:
    if _CURRENT_DECLARATION_RE.search(query):
        return True
    if not (_CURRENT_REQUEST_RE.search(query) or _CURRENT_STATE_RE.search(query)):
        return False
    # "I want to retrieve the earlier record" is a lookup; a new goal or
    # current-state assertion is not. This precedence is shared by consumers.
    past = _history_cue(query)
    question = bool(_QUESTION_FORM_RE.search(query))
    explicit_lookup = bool(_LOOKUP_VERB_RE.search(query) and (past or question)) or (
        past and question
    )
    return not explicit_lookup


def historical_query(query: str) -> bool:
    """Shared historical/source classifier; new declarations take precedence."""
    query = str(query or "")
    return not _current_declaration(query) and (
        _history_cue(query) or bool(_RETENTION_HISTORY_RE.search(query))
    )


def should_recover_history(query: str) -> bool:
    """Shared past intent plus retention's explicit archive/recovery verbs."""
    query = str(query or "")
    return not _current_declaration(query) and (
        _history_cue(query)
        or bool(_RETENTION_HISTORY_RE.search(query))
        or bool(_RECOVERY_EXTRA_RE.search(query))
    )


def requires_live_evidence(query: str) -> bool:
    return bool(
        re.search(
            r"(?:现在|当前|目前|此刻).{0,25}(?:运行|在跑|在线|离线|正常|进程|显卡空闲)|(?:服务|插件).{0,12}(?:现在|当前|目前).{0,12}(?:正常|在线|离线)|还在(?:跑|运行|执行)",
            str(query or ""),
            re.I,
        )
    )


def needs_resume(query: str) -> bool:
    query = str(query or "")
    return bool(
        _RESUME_RE.fullmatch(query)
        or re.fullmatch(
            r"(?:那|现在|这次|这轮)?(?:弄完|做完|修完|处理完|搞定|好|全都完成)(?:了)?(?:吗|没|没有)[?？。 ]*",
            query,
        )
        or re.fullmatch(r"(?:那就|那)?(?:继续|接着)(?!(?:说|聊|讲|讨论)).{0,35}", query)
        or re.fullmatch(
            r"(?:那就)?按(?:刚才|之前|上次|你说的).{0,18}(?:做|来|继续)[吧。!！ ]*",
            query,
        )
    )


def retrieval_needed(
    query: str, cfg: Mapping[str, Any] | None = None, *, project_fact_lookup: bool = False
) -> bool:
    query = str(query or "")
    if re.match(r"^(?:我(?:说|指|问)的是|你(?:理解错|误解|答偏))", query):
        return False
    if needs_resume(query):
        return True
    if _current_declaration(query):
        return False
    if re.search(
        r"(?:先别|不要|不必|无需|不用).{0,20}(?:查|检索|找回|回顾)(?:旧|历史|之前)?(?:记录|资料|对话)?",
        query,
    ):
        return False
    # Retention language is often an external capability claim (e.g. a
    # linked tool is said to "never forget"). Treat it as user-history intent
    # only when the prompt names lost/forgotten records or explicitly asks
    # about a forgetting mechanism. A bare promise is not a memory lookup.
    historical = bool(
        _HISTORY_SUBJECT_RE.search(query)
        or _RETENTION_HISTORY_RE.search(query)
        or _PERSONAL_SUBJECT_RE.search(query)
        or re.search(r"\d+条.{0,8}(?:分类|归档|导入)", query)
    )
    if not historical and (
        requires_live_evidence(query)
        or _CURRENT_STATE_RE.search(query)
        or _CURRENT_REQUEST_RE.search(query)
    ):
        return False
    if project_fact_lookup:
        return True
    if _LOOKUP_VERB_RE.search(query) and _QUESTION_FORM_RE.search(query):
        return True
    if _ENGLISH_SOURCE_REFERENCE_RE.search(query) or _ENGLISH_PAST_RECALL_RE.search(
        query
    ):
        return True
    if _JAPANESE_HISTORY_RE.search(query) and re.search(
        r"[?？]|何|どれ|どこ|どう|いくつ|挙げ", query
    ):
        return True
    project = document_project(query, cfg)
    if project and _PROJECT_ARTIFACT_LOOKUP_RE.search(query):
        return True
    if _NAMED_PROJECT_QUESTION_RE.search(query) and (
        _QUESTION_FORM_RE.search(query) or _LOOKUP_VERB_RE.search(query)
    ):
        return True
    standalone_code = re.search(r"(?:帮|给|请).{0,8}(?:写|生成|实现)", query)
    history = re.search(r"之前|上次|以前|原来|记得|沿用|现有|仓库|旧代码", query)
    if standalone_code and not history and not document_project(query, cfg):
        return False
    if _FRESH_WORK_RE.search(query) and not re.search(
        r"之前|上次|当时|原来|后来|记得|约定|原定|结论|验收|结果|故障|教训|等召回|误报|[?？]|吗|哪些|多少|为什么|怎么|什么要求",
        query,
    ):
        return False
    question = bool(_QUESTION_FORM_RE.search(query))
    personal_question = question and bool(
        _PERSONAL_SUBJECT_RE.search(query) or _CURRENT_RULE_SUBJECT_RE.search(query)
    )
    project = document_project(query, cfg)
    project_fact_question = question and bool(project)
    project_label_query = bool(project) and any(
        query.strip(" \t\r\n。？！?") == alias.strip()
        for alias in cfg.get("projects", {}).get(project, {}).get("aliases", [])
    )
    return bool(
        historical or personal_question or project_fact_question or project_label_query
    )


def authorization_lookup(query: str) -> bool:
    return bool(
        re.search(
            r"授权|获准|允许|同意|批准|权限|限制|(?:能|可).{0,12}(?:启动|删除|续接|继续|使用)"
            r"|\b(?:authorization|permission|approval)\b",
            str(query or ""),
        )
    )


def _numeric_values(query: str) -> frozenset[str]:
    return frozenset(_NUMBER_RE.findall(_NUMERIC_DATE_TIME_RE.sub(" ", query)))


def _stage_values(text: str) -> frozenset[str]:
    return frozenset(x.lower() for x in _STAGE_RE.findall(text))


def _lexical_support(
    wanted: frozenset[str], text: str, numeric: frozenset[str], stages: frozenset[str]
) -> tuple[float, bool, bool]:
    numeric_valid = not numeric or bool(numeric.intersection(_NUMBER_RE.findall(text)))
    mentioned_stages = _stage_values(text)
    stage_valid = stages <= mentioned_stages
    if not numeric_valid or not stage_valid or not wanted:
        return 0.0, numeric_valid, stage_valid
    evidence = evidence_terms(text)
    specific = wanted - _DOMAIN_TERMS
    actual = specific or wanted
    hits = len(actual & evidence)
    if hits < min(2, len(actual)):
        return 0.0, numeric_valid, stage_valid
    score = hits / len(actual)
    return (score if score >= MIN_LEXICAL_SUPPORT else 0.0), numeric_valid, stage_valid


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    lexical_support: float
    section_support: float
    numeric_valid: bool
    stage_valid: bool
    audit_reason: str = ""

    @property
    def support(self) -> float:
        return max(self.lexical_support, self.section_support)


@dataclass(frozen=True, slots=True)
class SupplementAssessment:
    accepted: bool
    support: float
    floor: float
    reason: str

    def __bool__(self) -> bool:
        return self.accepted


@dataclass(frozen=True, slots=True)
class SourceAssessment:
    project: str
    accepted: bool
    reason: str


_QUANTITY_RE = re.compile(r"(?<![0-9A-Za-z_.])[+-]?\d+(?:\.\d+)?%?(?![0-9A-Za-z_.])")
_FACT_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_+-]*(?:/\d+)?", re.I)
_EXPLICIT_IDENTIFIER_RE = re.compile(
    r"\b(?:[A-Z]{2,}[0-9]*|[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+"
    r"|[a-zA-Z][a-zA-Z0-9]*-[0-9][a-zA-Z0-9-]*)\b"
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        "january february march april may june july august september october november december".split(),
        1,
    )
}
_MONTH_NAME = r"(?:" + "|".join(_MONTHS) + r")"
_ENGLISH_DATE = rf"{_MONTH_NAME}\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}"
_CHINESE_DATE = r"\d{4}年\s*\d{1,2}月\s*\d{1,2}日"
_AS_OF_RE = re.compile(
    rf"(?:截至|截止到|\bas of\b|\bon\b)\s*"
    rf"(?P<date>\d{{4}}-\d{{2}}-\d{{2}}|{_ENGLISH_DATE}|{_CHINESE_DATE})(?!\d)",
    re.I,
)
_DATE_WORDS_RE = re.compile(
    rf"\b{_MONTH_NAME}\s+\d{{1,2}}\b(?:,\s*\d{{4}})?|{_CHINESE_DATE}",
    re.I,
)
_PROJECT_LOOKUP_RE = re.compile(
    r"^\s*(?:please\s+)?(?:what|which|where|when|how|does|did|is|are|"
    r"list|find|give|retrieve|show|check|verify|compare|look up|report|confirm)\b"
    r"|^\s*(?:请(?:帮忙)?\s*)?(?:查证|核对|列出|找出|查找|给出|对照(?!模型|组)|比较)",
    re.I,
)
_EXPLICIT_FACT_BREAK_RE = re.compile(r"[；;]|\n(?=\s*(?:[-*]\s|\d+[.、)]\s*))")
_FACT_BREAK_RE = re.compile(r"[,，、]|\band\b|\bplus\b|以及|和|及", re.I)
_ENUMERATED_LOOKUP_RE = re.compile(r"^\s*(?:please\s+)?list\b|分别|逐项|列出", re.I)


def _strip_lookup_dates(text: str) -> str:
    return _DATE_WORDS_RE.sub(" ", _AS_OF_RE.sub(" ", text))


def requested_date(query: str) -> date | None:
    dates = set()
    for match in _AS_OF_RE.finditer(query):
        value = match["date"]
        try:
            if "-" in value:
                parsed = date.fromisoformat(value)
            elif "年" in value:
                parsed = date(*map(int, re.findall(r"\d+", value)))
            else:
                month, day, year = re.split(r"[,\s]+", value.casefold())
                parsed = date(int(year), _MONTHS[month], int(day))
        except ValueError:
            return None
        dates.add(parsed)
    if len(dates) == 1:
        return dates.pop()
    return None


def quantities(text: str) -> frozenset[str]:
    return frozenset(_QUANTITY_RE.findall(_NUMERIC_DATE_TIME_RE.sub(" ", text)))


@dataclass(frozen=True, slots=True)
class FactRequest:
    terms: frozenset[str]
    quantities: frozenset[str]
    stages: frozenset[str]
    identifiers: frozenset[str]
    topics: frozenset[str] = frozenset()

    def support(self, text: str) -> float:
        if not self.quantities <= quantities(text):
            return 0.0
        if self.identifiers and not self.identifiers <= set(
            _FACT_IDENTIFIER_RE.findall(text.casefold())
        ):
            return 0.0
        if self.topics:
            return float(
                self.topics <= evidence_topics(text) and self.stages <= _stage_values(text)
            )
        return _lexical_support(self.terms, text, frozenset(), self.stages)[0]


def _without_project_aliases(text: str, aliases) -> str:
    names = [name for name in aliases if isinstance(name, str) and name.strip()]
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(_alias_pattern(name), " ", text, flags=re.I)
    return text


def split_fact_requests(text: str, project: str) -> tuple[FactRequest, ...]:
    """Extract named project fact requests, including explicit missing subjects."""
    subject = re.sub(r"^\s*(?:for\b|的)\s*", "", text, flags=re.I).lstrip(" \t,:，：")
    if not project or not (
        _PROJECT_LOOKUP_RE.search(subject)
        or (_QUESTION_FORM_RE.search(subject) and fact_topics(subject))
    ):
        return ()
    # Keep questions mixing present and past on the existing path.
    if _history_cue(text) and re.search(r"当前|现在|目前|\bcurrent\b", text, re.I):
        return ()
    chunks = _EXPLICIT_FACT_BREAK_RE.split(subject)
    if len(chunks) == 1:
        topics = fact_topics(subject)
        if not topics:
            return ()
        if len(topics) > 1 or _ENUMERATED_LOOKUP_RE.search(subject):
            parts = [chunk for chunk in _FACT_BREAK_RE.split(subject) if chunk.strip()]
            if len(parts) > 1 and any(len(fact_topics(chunk)) > 1 for chunk in parts):
                return ()
            chunks = parts
    if not 1 <= len(chunks) <= 5:
        return ()
    requests = []
    for chunk in chunks:
        chunk = re.sub(r"^\s*(?:[-*]\s+|\d+[.、)]\s*)", "", chunk).strip()
        chunk = _PROJECT_LOOKUP_RE.sub(" ", chunk)
        chunk = re.sub(r"分别", " ", chunk)
        topics = fact_topics(chunk)
        wanted = frozenset(focus_terms(chunk))
        if not topics and len(wanted - _DOMAIN_TERMS) < 2:
            return ()
        if topics:
            identifiers = frozenset(
                word.casefold() for word in _EXPLICIT_IDENTIFIER_RE.findall(chunk)
            )
        else:
            identifiers = frozenset(
                word for word in _FACT_IDENTIFIER_RE.findall(chunk.casefold())
                if word in wanted or "/" in word
            )
        requests.append(
            FactRequest(wanted, quantities(chunk), _stage_values(chunk), identifiers, topics)
        )
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    raw: str
    project: str
    project_confident: bool
    resume: bool
    allow_recall: bool
    historical: bool
    foreign: bool
    requires_live: bool
    recover_history: bool
    reason: str
    terms: frozenset[str]
    _focus: frozenset[str] = field(repr=False)
    _numeric: frozenset[str] = field(repr=False)
    _stages: frozenset[str] = field(repr=False)
    _clauses: tuple[frozenset[str], ...] = field(repr=False)
    fact_requests: tuple[FactRequest, ...] = ()
    as_of: date | None = None

    @property
    def focus(self) -> frozenset[str]:
        return self._focus

    def covered_requests(self, text: str) -> frozenset[int]:
        return frozenset(
            index
            for index, request in enumerate(self.fact_requests)
            if request.support(text) >= STRONG_LEXICAL_SUPPORT
        )

    def evidence(self, text: str) -> EvidenceAssessment:
        if self.fact_requests:
            score = self.support(text)
            return EvidenceAssessment(
                score,
                score,
                bool(score),
                bool(score),
                "fact_request_match" if score else "no_fact_request_match",
            )
        score, numeric_valid, stage_valid = _lexical_support(
            self._focus, str(text or ""), self._numeric, self._stages
        )
        section = self.section_support(text, _gates=(numeric_valid, stage_valid, score))
        reason = (
            "numeric_mismatch"
            if not numeric_valid
            else "stage_mismatch"
            if not stage_valid
            else "lexical_support"
            if score
            else "no_query_support"
        )
        return EvidenceAssessment(score, section, numeric_valid, stage_valid, reason)

    def support(self, text: str) -> float:
        """Lexical support with numeric and stage gates."""
        if self.fact_requests:
            return max(
                request.support(str(text or "")) for request in self.fact_requests
            )
        return _lexical_support(
            self._focus, str(text or ""), self._numeric, self._stages
        )[0]

    def section_support(
        self, text: str, *, _gates: tuple[bool, bool, float] | None = None
    ) -> float:
        """Score a section against its applicable request."""
        if self.fact_requests:
            return self.support(text)
        if _gates is None:
            whole_score, numeric_valid, stage_valid = _lexical_support(
                self._focus, str(text or ""), self._numeric, self._stages
            )
        else:
            numeric_valid, stage_valid, whole_score = _gates
        if not numeric_valid or not stage_valid or not whole_score:
            return 0.0
        return max(
            whole_score,
            max(
                (
                    _lexical_support(clause, str(text or ""), frozenset(), frozenset())[
                        0
                    ]
                    for clause in self._clauses
                    if len(clause) >= 3
                ),
                default=0.0,
            ),
        )

    def source_project(self, uri: str, text: str, cfg: Mapping[str, Any] | None) -> str:
        """Resolve configured source URI, then filename aliases, then body aliases."""
        if not isinstance(uri, str):
            return ""
        projects = (cfg or {}).get("projects", {})
        for key, project in projects.items():
            for field_name in ("facts_uri", "current_task_uri"):
                main = project.get(field_name, "")
                if main and (
                    uri == main or uri.startswith(str(main).removesuffix(".md") + "/")
                ):
                    return key
        if self.fact_requests:
            return ""
        filename = unquote(uri.rsplit("/", 1)[-1]).casefold()
        file_scores = []
        for key, project in projects.items():
            aliases = [
                a.casefold() for a in project.get("aliases", []) if isinstance(a, str)
            ]
            score = sum(
                max(1, len(alias)) for alias in aliases if alias and alias in filename
            )
            if score:
                file_scores.append((score, key))
        file_scores.sort(reverse=True)
        if file_scores and (
            len(file_scores) == 1 or file_scores[0][0] > file_scores[1][0]
        ):
            return file_scores[0][1]
        return _project_from_aliases(text, cfg)

    def source_assessment(
        self, uri: str, category: str, text: str, cfg: Mapping[str, Any] | None
    ) -> SourceAssessment:
        project = self.source_project(uri, text, cfg)
        if self.fact_requests and project != self.project:
            return SourceAssessment(project, False, "project_scope_unverified")
        if category == "events" and not self.historical:
            return SourceAssessment(project, False, "event_requires_history_intent")
        if re.search(
            r"授权|获准|批准|准许", uri.rsplit("/", 1)[-1]
        ) and not authorization_lookup(self.raw):
            return SourceAssessment(
                project, False, "authorization_requires_explicit_lookup"
            )
        if self.foreign and not literal_anchors_match(
            self.raw, text, cfg or {}, self.project
        ):
            return SourceAssessment(project, False, "explicit_identifier_mismatch")
        if self.project_confident and self.project:
            if (project and project != self.project) or (
                self.foreign and project != self.project
            ):
                return SourceAssessment(project, False, "different_project")
        return SourceAssessment(project, True, "source_scope_matches")

    def rank_score(
        self,
        native_score: float,
        support: float,
        uri: str,
        text: str,
        source: SourceAssessment,
        *,
        labelled: bool = False,
    ) -> float:
        """One owner for source priors; diversity is applied by the assembler."""
        score = 0.35 * native_score + 0.65 * support
        if labelled and not self.historical:
            score -= 0.12
        if self.project_confident and self.project and source.project == self.project:
            score += 0.05
        if "/rooms/" in uri:
            score += 0.08
        infrastructure = re.search(
            r"回填规模|回填启动|导入决定|磁盘占用|接入决定|记忆命令实现计划", text
        )
        infrastructure_query = re.search(
            r"导入|回填|磁盘|安装|部署|占用|日志", self.raw
        )
        if infrastructure and not infrastructure_query:
            score -= 0.25
        return score

    def supplement_assessment(
        self, text: str, reviewed_support: float = 0, semantic: bool = False
    ) -> SupplementAssessment:
        ev = self.evidence(text)
        try:
            reviewed = max(0.0, float(reviewed_support))
        except (TypeError, ValueError):
            reviewed = 0.0
        floor = max(MIN_LEXICAL_SUPPORT, SUPPLEMENT_RATIO * reviewed)
        score = ev.lexical_support
        accepted = ev.numeric_valid and ev.stage_valid and (semantic or score >= floor)
        if not ev.numeric_valid:
            reason = "reject:numeric_mismatch"
        elif not ev.stage_valid:
            reason = "reject:stage_mismatch"
        elif semantic:
            reason = "accept:verified_semantic_source"
        elif not score:
            reason = "reject:no_query_support"
        elif score < floor:
            reason = "reject:below_support_floor"
        elif semantic:
            reason = "accept:semantic_and_lexical_support"
        else:
            reason = "accept:lexical_support"
        return SupplementAssessment(accepted, score, floor, reason)

    def accepts_supplement(
        self, text: str, reviewed_support: float = 0, semantic: bool = False
    ) -> bool:
        """Apply one support floor; use supplement_assessment for its audit reason."""
        return self.supplement_assessment(text, reviewed_support, semantic).accepted


def analyze(
    query: str, cfg: Mapping[str, Any] | None = None, project_hint: str = ""
) -> QueryPlan:
    raw = str(query or "")
    project, confident = _project_query(raw, cfg)
    if not project and project_hint:
        project, confident = str(project_hint), False
    aliases = (
        (cfg or {}).get("projects", {}).get(project, {}).get("aliases", [])
        if confident
        else []
    )
    intent = _without_project_aliases(raw, aliases)
    resume = needs_resume(raw)
    live = requires_live_evidence(intent)
    as_of = requested_date(intent)
    historical = historical_query(intent) or as_of is not None
    foreign = foreign_historical_intent(intent)
    lookup_text = _strip_lookup_dates(raw)
    requests = ()
    if (
        not resume
        and confident
        and _project_from_aliases(raw, cfg, single_project=True) == project
    ):
        requests = split_fact_requests(_strip_lookup_dates(intent), project)
    needed = retrieval_needed(raw, cfg, project_fact_lookup=bool(requests))
    if as_of and project and not _current_declaration(intent):
        needed = True
    if _CASUAL_RE.fullmatch(raw):
        needed, reason = False, "casual"
    elif live and not historical:
        needed, reason = False, "live_state"
    elif _current_declaration(intent):
        needed, reason = False, "current_declaration"
    elif resume:
        needed, reason = True, "resume"
    elif needed:
        reason = (
            "historical_or_project_lookup"
            if historical or project
            else "personal_or_explicit_lookup"
        )
    else:
        reason = "no_recall_intent"
    focus = frozenset(focus_terms(lookup_text))
    clauses = (
        tuple(
            frozenset(focus_terms(part))
            for part in re.split(r"[,，;；?？]", lookup_text)
        )
        if re.search(r"[\u4e00-\u9fff]", raw)
        else (focus,)
    )
    return QueryPlan(
        raw,
        project,
        confident,
        resume,
        bool(needed),
        historical,
        foreign,
        live,
        should_recover_history(intent),
        reason,
        frozenset(terms(raw)),
        focus,
        _numeric_values(lookup_text),
        _stage_values(lookup_text),
        clauses,
        requests if needed else (),
        as_of,
    )


def relevance(query: str, text: str) -> float:
    """Compatibility wrapper preserving candidate-v11 relevance semantics."""
    return analyze(query).support(text)


def fact_relevance(query: str, text: str) -> float:
    """Compatibility wrapper for whole-query-gated clause support."""
    return analyze(query).evidence(text).support


CASUAL = _CASUAL_RE


def literal_anchors_match(query, text, cfg, project):
    """An explicit product, code identifier, or version needs actual evidence."""
    query = _strip_lookup_dates(query)
    aliases = {
        a.casefold()
        for p in cfg.get("projects", {}).values()
        for a in p.get("aliases", [])
    }
    common = {
        "according",
        "what",
        "which",
        "who",
        "where",
        "when",
        "how",
        "why",
        "the",
        "is",
        "are",
        "was",
        "were",
        "did",
        "does",
        "can",
        "could",
        "should",
        "would",
        "from",
        "after",
        "before",
        "please",
        "in",
    }
    # Language descriptors are translated concepts, rather than literal code or
    # product identifiers. Capitalization must not defeat multilingual matching.
    common.update(
        {
            "english",
            "chinese",
            "japanese",
            "korean",
            "mandarin",
            "french",
            "german",
            "spanish",
            "arabic",
            "russian",
            "italian",
            "portuguese",
        }
    )
    names = (
        {
            s.casefold()
            for s in re.findall(
                r"\b[A-Z][A-Za-z0-9_+-]{2,}\b|\b[a-zA-Z]+_[a-zA-Z0-9_]+\b", query
            )
        }
        - aliases
        - common
    )
    versions = re.findall(r"(?<!\d)\d+(?:\.\d+){2,}(?!\d)", query)
    haystack = text.casefold()
    if any(name not in haystack for name in names):
        return False
    if any(
        not re.search(r"(?<![\d.])" + re.escape(v) + r"(?![\d.])", text)
        for v in versions
    ):
        return False
    numeric = re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d{2}(?::\d{2})?", " ", query)
    values = set(re.findall(r"(?<!\d)\d{2,}(?!\d)", numeric))
    return not values or bool(
        values.intersection(re.findall(r"(?<!\d)\d{2,}(?!\d)", text))
    )
