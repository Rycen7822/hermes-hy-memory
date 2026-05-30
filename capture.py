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


def sanitize_memory_context(text: Any) -> str:
    """Remove injected memory-context blocks and normalize whitespace."""
    cleaned = _CONTEXT_RE.sub(" ", str(text or ""))
    return _SPACE_RE.sub(" ", cleaned).strip()


def _is_trivial(text: str) -> bool:
    if len(text) < 3:
        return True
    return bool(_TRIVIAL_RE.match(text))


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
