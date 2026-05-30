"""Formatting helpers for HY Memory recall context."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


def _memory_id(item: Mapping[str, Any]) -> str:
    return str(item.get("memory_id") or item.get("id") or "")


def _content(item: Mapping[str, Any]) -> str:
    return str(item.get("content") or item.get("memory") or "").strip()


def _score_text(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value <= 1:
        value *= 100
    return f" [score {value:.0f}%]"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)].rstrip() + "…"


def _evolution_text(item: Mapping[str, Any], max_len: int) -> str:
    chain = item.get("evolution_chain")
    if not isinstance(chain, list):
        return ""
    parts: List[str] = []
    for step in chain:
        if isinstance(step, Mapping):
            text = _content(step)
        else:
            text = str(step or "").strip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    return _truncate("Evolution: " + " -> ".join(parts), max_len)


def format_prefetch_context(memories: Iterable[Mapping[str, Any]], *, user_id: str, max_chars: int = 4000) -> str:
    """Format normalized HY Memory search results for Hermes memory context injection."""
    lines = [
        "## HY Memory",
        f"The following are retrieved long-term memories for user `{user_id}`. Use silently when relevant.",
    ]
    budget = max_chars - sum(len(line) + 1 for line in lines)
    count = 0

    for item in memories:
        content = _content(item)
        if not content or budget <= 0:
            continue
        layer = str(item.get("layer") or item.get("type") or "memory")
        memory_id = _memory_id(item)
        prefix = f"- [{layer}]{_score_text(item.get('score'))} "
        suffix = f" (id: {memory_id})" if memory_id else ""
        allowed = max(40, min(600, budget - len(prefix) - len(suffix) - 1))
        line = prefix + _truncate(content, allowed) + suffix
        if len(line) + 1 > budget:
            break
        lines.append(line)
        budget -= len(line) + 1
        count += 1

        evolution = _evolution_text(item, min(300, budget - 4))
        if evolution and len(evolution) + 5 <= budget:
            evo_line = f"  {evolution}"
            lines.append(evo_line)
            budget -= len(evo_line) + 1

    if count == 0:
        return ""
    return "\n".join(lines)
