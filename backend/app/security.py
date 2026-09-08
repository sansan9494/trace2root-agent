from __future__ import annotations

import re


PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|system)\s+(instructions?|prompts?)"),
    re.compile(r"(?i)(reveal|print|show|leak)\s+(the\s+)?(system\s+prompt|hidden\s+instructions?)"),
    re.compile(r"(?i)prompt[_ -]?injection[_ -]?(succeeded|success)"),
    re.compile(r"忽略.{0,16}(系统|先前|之前).{0,12}(指令|提示|规则)"),
    re.compile(r"(泄露|输出|显示).{0,12}(系统提示|隐藏指令|system prompt)", re.IGNORECASE),
    re.compile(r"(调用|执行).{0,12}(restart_service|send_msg|删除|重启|回滚)"),
)


def sanitize_untrusted_evidence(text: str) -> tuple[str, list[str]]:
    """Replace instruction-like lines before retrieved evidence reaches an LLM."""
    flags: list[str] = []
    safe_lines: list[str] = []
    for line in text.splitlines():
        matched = [pattern.pattern for pattern in PROMPT_INJECTION_PATTERNS if pattern.search(line)]
        if matched:
            flags.extend(matched)
            safe_lines.append("[疑似提示注入内容已隔离]")
        else:
            safe_lines.append(line)
    return "\n".join(safe_lines), sorted(set(flags))
