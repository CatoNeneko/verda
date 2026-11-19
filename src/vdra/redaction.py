"""Redact familiar credentials and agent transport metadata from recall text."""

import re

BLOCK = re.compile(
    r"<(environment_context|external_codex_apps_open_page|send_user_message_question_reply|openviking-context|INSTRUCTIONS)\b[^>]*>.*?</\1>",
    re.S,
)
SECRET = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|(?:ghp|github_pat|hf)_[a-z0-9_]{16,}|eyJ[a-zA-Z0-9_-]{16,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+|-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----|(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\s*[=:]\s*[\"\x27]?[^\s\"\x27,;]{8,})",
    re.S,
)
