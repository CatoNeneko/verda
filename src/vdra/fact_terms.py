"""Stable bilingual names for project fact subjects, independent of values."""

import re

DATE_FIELD_NAMES = r"date|valid[_ -]from|valid[_ -]until|日期"
SECTION_FIELD_RE = re.compile(
    rf"^[ \t]*(?:[-*+][ \t]+)?(?:{DATE_FIELD_NAMES}|status|kind|状态|有效性)"
    r"[：:][ \t]*.+$",
    re.I,
)
_HEADING_RE = re.compile(r"^#{1,6}\s+")
_BARE_VALUE_RE = re.compile(
    r"(?:[+-]?\d+(?:[./:]\d+)*%?|[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+"
    r"|[A-Z][A-Z0-9]{1,15})"
)


# Match a subject before matching its value. For example, a fixed seed should
# never be treated as evidence for an unset batch size on the same project card.
_TOPICS = {
    "dataset_split": r"\b(?:data(?:set)?|train[ -]test) (?:split|partition)(?:s|ing)?\b|数据(?:集)?(?:划分|切分)",
    "seed": r"\brandom[ -]seed\b|随机种子|训练种子",
    "batch_size": r"\bbatch[ -]sizes?\b|批大小|批量大小|训练批量",
    "selection_metric": (
        r"\b(?:checkpoint|model|validation) selection metric\b"
        r"|\boptimi[sz]e\b.{0,60}\bchoos\w*\b.{0,40}\b(?:model|checkpoint)\b"
        r"|选(?:组|模|择检查点)指标|验证(?:集)?选择指标"
    ),
    "confidence": r"\bconfidence threshold\b|置信(?:度)?阈值",
    "selection_protocol": (
        r"\b(?:experiment(?:al)?|baseline(?: selection)?) protocol\b"
        r"|\bprotocol\b[^.!?。\n]{0,40}\b(?:governs|for|applies to) baseline selection\b"
        r"|实验协议|基线选择协议"
    ),
    "normalization": r"\b(?:input |data )?normali[sz]ation\b|(?:输入|数据)?归一化|正規化",
    "reference": r"\b(?:reference|control) model\b|参考模型|对照模型|対照モデル",
    "accepted_artifact": (
        r"\b(?:accepted|approved) (?:model(?: file| artifact)?|artifact|checkpoint)\b"
        r"|(?:已)?验收(?:的)?(?:模型(?:文件)?|产物|检查点)"
    ),
    "coordinate_frame": r"\bcoordinate frame\b|坐标系|参考坐标",
    "staging_configuration": (
        r"\bstaging(?: environment)? (?:uses (?:the )?|runs with (?:the )?)?configuration\b"
        r"|\bconfiguration\b[^.!?。\n]{0,35}\b(?:used )?for staging\b|预发布(?:环境)?配置"
    ),
    "cache_refresh": (
        r"\bcache refresh(?!\w)|\brefresh(?:es|ing)? (?:the )?cache\b"
        r"|\bperiodic refresh\b|缓存刷新"
    ),
    "namespace": r"\bnamespaces?\b|命名空间",
    "distribution_target": r"\bdistribution target\b|发行目标|分发目标",
    "rerun_policy": (
        r"\b(?:rerun.{0,40}(?:approval|authorization|permission|policy)"
        r"|(?:approval|authorization|permission|policy).{0,50}rerun)\b"
        r"|(?:重跑|重试).{0,12}(?:批准|授权|许可|规则)|(?:批准|授权|许可).{0,12}重跑"
    ),
    "weight_decay": r"\bweight decay\b|权重衰减",
    "model_vendor": r"\b(?:language model|llm) (?:provider|vendor)\b|(?:语言模型|大模型)供应商",
}
_PATTERNS = {name: re.compile(pattern, re.I) for name, pattern in _TOPICS.items()}


def fact_topics(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in _PATTERNS.items() if pattern.search(text))


def evidence_topics(text: str) -> frozenset[str]:
    """A heading can label a scalar or identifier. Prose must name the fact."""
    body = [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip())
        and not _HEADING_RE.match(stripped)
        and not SECTION_FIELD_RE.match(stripped)
    ]
    if len(body) == 1:
        value = body[0].strip("`")
        if value not in {"TBD", "TODO", "NA"} and _BARE_VALUE_RE.fullmatch(value):
            return fact_topics(text)
    return fact_topics("\n".join(body))
