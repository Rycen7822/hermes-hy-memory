"""Conversation capture sanitization helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_CONTEXT_RE = re.compile(
    r"<(memory-context|hy-memory-context|relevant-memories)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_SPACE_RE = re.compile(r"\s+")
_TRIVIAL_RE = re.compile(r"^(ok|okay|thanks?|thank you|好的|谢谢|嗯|收到|明白)[.!。！\s]*$", re.IGNORECASE)
_DATED_TASK_NARRATIVE_RE = re.compile(r"\bOn \d{4}-\d{2}-\d{2}, the user requested\b", re.IGNORECASE)
_PHASE_LOG_RE = re.compile(r"\b(Phase|阶段)\s*[0-9一二三四五六七八九十]+\s*(completed|done|完成|结束)\b", re.IGNORECASE)
_TRANSIENT_ARTIFACT_RE = re.compile(r"\b(commit SHA|PR #\d+|issue #\d+|pull request #\d+)\b", re.IGNORECASE)
_LOCAL_PROGRESS_RE = re.compile(r"\b(note\.md|problems\.md|升级规划\d*\.md)\b", re.IGNORECASE)
_DURABLE_HINT_RE = re.compile(r"\b(prefers?|preference|remember|stable|convention|workflow|lesson|quirk|project uses)\b|偏好|记住|稳定|惯例|工作流", re.IGNORECASE)


def sanitize_memory_context(text: Any) -> str:
    """Remove injected memory-context blocks and normalize whitespace."""
    cleaned = _CONTEXT_RE.sub(" ", str(text or ""))
    return _SPACE_RE.sub(" ", cleaned).strip()


def _is_trivial(text: str) -> bool:
    if len(text) < 3:
        return True
    return bool(_TRIVIAL_RE.match(text))


def _has_durable_hint(text: str) -> bool:
    return bool(_DURABLE_HINT_RE.search(text))


def _is_task_log_noise(text: str, role: str) -> bool:
    if not text:
        return False
    if role == "assistant":
        if _DATED_TASK_NARRATIVE_RE.search(text) or _PHASE_LOG_RE.search(text) or _TRANSIENT_ARTIFACT_RE.search(text):
            return True
        return bool(_LOCAL_PROGRESS_RE.search(text) and not _has_durable_hint(text))
    if role == "user":
        return bool(_LOCAL_PROGRESS_RE.search(text) and not _has_durable_hint(text))
    return False


def build_capture_messages(
    user_content: str,
    assistant_content: str,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[Dict[str, str]]]:
    """Build a cleaned two-message turn payload for HY Memory auto capture."""
    user = sanitize_memory_context(user_content)
    assistant = sanitize_memory_context(assistant_content)

    if messages:
        latest_assistant = ""
        latest_user = ""
        for message in reversed(messages):
            role = message.get("role") if isinstance(message, dict) else None
            content = sanitize_memory_context(message.get("content") if isinstance(message, dict) else "")
            if not content:
                continue
            if not latest_assistant and role == "assistant":
                latest_assistant = content
                continue
            if latest_assistant and role == "user":
                latest_user = content
                break
        user = latest_user or user
        assistant = latest_assistant or assistant

    if _is_task_log_noise(user, "user"):
        user = ""
    if _is_task_log_noise(assistant, "assistant"):
        assistant = ""

    if _is_trivial(user) and _is_trivial(assistant):
        return None
    if not user and not assistant:
        return None

    payload: List[Dict[str, str]] = []
    if user and not _is_trivial(user):
        payload.append({"role": "user", "content": user})
    if assistant and not _is_trivial(assistant):
        payload.append({"role": "assistant", "content": assistant})
    return payload or None
