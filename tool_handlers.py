"""Explicit HY Memory tool schemas and dispatch helpers."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from tools.registry import tool_error


def _schema(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or []},
    }


ADD_SCHEMA = _schema(
    "hy_memory_add",
    "Store an explicit memory in HY Memory. Provide either content or OpenAI-style messages.",
    {
        "content": {"type": "string", "description": "Memory text to store."},
        "messages": {"type": "array", "description": "OpenAI-style messages to store."},
        "metadata": {"type": "object", "description": "Optional JSON metadata."},
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "session_id": {"type": "string"},
        "memory_at": {"type": "string", "description": "Optional ISO timestamp."},
    },
)

SEARCH_SCHEMA = _schema(
    "hy_memory_search",
    "Search HY Memory by meaning. Uses the active Hermes user scope unless user_id is provided.",
    {
        "query": {"type": "string", "description": "Search query."},
        "limit": {"type": "integer", "description": "Maximum results, 1-50."},
        "min_score": {"type": "number"},
        "profile_limit": {"type": "integer"},
        "profile_min_score": {"type": "number"},
        "reader": {"type": "string"},
        "user_id": {"type": "string"},
        "agent_id": {"type": "string", "description": "Agent scope. Empty string searches across agents."},
        "session_id": {"type": "string"},
        "include_raw": {"type": "boolean"},
    },
    ["query"],
)

GET_SCHEMA = _schema(
    "hy_memory_get",
    "Fetch one HY Memory record by exact memory_id obtained from search/list.",
    {"memory_id": {"type": "string", "description": "Exact HY Memory id."}},
    ["memory_id"],
)

UPDATE_SCHEMA = _schema(
    "hy_memory_update",
    "Update an existing HY Memory record. Search first and use an exact memory_id; do not fabricate ids.",
    {
        "memory_id": {"type": "string"},
        "content": {"type": "string", "description": "Replacement memory content."},
    },
    ["memory_id", "content"],
)

DELETE_SCHEMA = _schema(
    "hy_memory_delete",
    "Delete a HY Memory record by exact id, or bulk delete only with all=true and confirm=true.",
    {
        "memory_id": {"type": "string"},
        "all": {"type": "boolean", "description": "Delete all memories in the selected scope."},
        "confirm": {"type": "boolean", "description": "Required when all=true."},
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "session_id": {"type": "string"},
    },
)

LIST_SCHEMA = _schema(
    "hy_memory_list",
    "List HY Memory records for the active user scope.",
    {
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
        "order": {"type": "string", "enum": ["desc", "asc"]},
    },
)

STATUS_SCHEMA = _schema(
    "hy_memory_status",
    "Show HY Memory provider status and optional deep backend health checks.",
    {"deep": {"type": "boolean", "description": "When true, run explicit SDK/vector/embedder/LLM health checks."}},
)

TOOL_SCHEMAS = [ADD_SCHEMA, SEARCH_SCHEMA, GET_SCHEMA, UPDATE_SCHEMA, DELETE_SCHEMA, LIST_SCHEMA, STATUS_SCHEMA]


def get_tool_schemas() -> List[Dict[str, Any]]:
    return list(TOOL_SCHEMAS)


def _json(data: Mapping[str, Any]) -> str:
    return json.dumps(dict(data), ensure_ascii=False)


def _int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _scope_value(args: Mapping[str, Any], defaults: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None:
        value = defaults.get(key, "")
    return str(value or "")


def _scope_list(value: str) -> Optional[List[str]]:
    return [value] if value else None


def _memory_id(item: Mapping[str, Any]) -> str:
    return str(item.get("memory_id") or item.get("id") or "")


def _compact_memory(item: Mapping[str, Any], *, include_raw: bool = False) -> Dict[str, Any]:
    compact = {
        "id": _memory_id(item),
        "content": item.get("content") or item.get("memory") or "",
        "score": item.get("score"),
        "layer": item.get("layer"),
        "memory_at": item.get("memory_at"),
        "tags": item.get("tags"),
    }
    if include_raw:
        compact["raw"] = dict(item)
    return compact


def _metadata(value: Any) -> Optional[Dict[str, Any]]:
    return dict(value) if isinstance(value, dict) else None


def _messages(value: Any) -> Optional[List[Dict[str, str]]]:
    if not isinstance(value, list):
        return None
    messages: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        messages.append({"role": str(item.get("role") or "user"), "content": content})
    return messages or None


def handle_tool_call(adapter: Any, defaults: Mapping[str, Any], tool_name: str, args: Mapping[str, Any]) -> str:
    args = args or {}

    try:
        if tool_name == "hy_memory_status":
            return _json(adapter.status(deep=bool(args.get("deep"))))

        if tool_name == "hy_memory_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return tool_error("Missing required parameter: query")
            user_id = _scope_value(args, defaults, "user_id")
            agent_id = _scope_value(args, defaults, "agent_id")
            session_id = str(args.get("session_id") or "")
            limit = _int(args.get("limit"), int(defaults.get("top_k", 10)), minimum=1, maximum=50)
            result = adapter.search(
                query,
                user_ids=[user_id] if user_id else [],
                agent_ids=_scope_list(agent_id),
                session_ids=_scope_list(session_id),
                limit=limit,
                min_score=_float(args.get("min_score"), float(defaults.get("min_score", 0.4))),
                profile_limit=_int(args.get("profile_limit"), int(defaults.get("profile_limit", 5)), minimum=0, maximum=50),
                profile_min_score=_float(args.get("profile_min_score"), float(defaults.get("profile_min_score", 0.4))),
                reader=str(args.get("reader") if args.get("reader") is not None else defaults.get("reader", "")),
            )
            memories = [_compact_memory(item, include_raw=bool(args.get("include_raw"))) for item in result.get("results", [])]
            payload: Dict[str, Any] = {"results": memories, "count": len(memories)}
            if args.get("include_raw"):
                payload["raw"] = result.get("raw")
            return _json(payload)

        if tool_name == "hy_memory_add":
            content = str(args.get("content") or "").strip()
            messages = _messages(args.get("messages"))
            if not content and not messages:
                return tool_error("Missing required parameter: content or messages")
            data = messages if messages else content
            return _json(adapter.add(
                data,
                user_id=_scope_value(args, defaults, "user_id"),
                agent_id=_scope_value(args, defaults, "agent_id"),
                session_id=_scope_value(args, defaults, "session_id"),
                metadata=_metadata(args.get("metadata")),
                memory_at=args.get("memory_at"),
            ))

        if tool_name == "hy_memory_get":
            memory_id = str(args.get("memory_id") or "").strip()
            if not memory_id:
                return tool_error("Missing required parameter: memory_id")
            return _json({"memory": adapter.get(memory_id)})

        if tool_name == "hy_memory_update":
            memory_id = str(args.get("memory_id") or "").strip()
            content = str(args.get("content") or "").strip()
            if not memory_id:
                return tool_error("Missing required parameter: memory_id")
            if not content:
                return tool_error("Missing required parameter: content")
            return _json(adapter.update(memory_id, content))

        if tool_name == "hy_memory_delete":
            delete_all = bool(args.get("all"))
            if delete_all:
                if not args.get("confirm"):
                    return tool_error("Bulk delete requires confirm=true")
                agent_id = str(args.get("agent_id") or "")
                session_id = str(args.get("session_id") or "")
                return _json(adapter.delete_all(
                    user_id=_scope_value(args, defaults, "user_id"),
                    agent_ids=_scope_list(agent_id),
                    session_ids=_scope_list(session_id),
                ))
            memory_id = str(args.get("memory_id") or "").strip()
            if not memory_id:
                return tool_error("Missing required parameter: memory_id")
            return _json(adapter.delete(memory_id))

        if tool_name == "hy_memory_list":
            raw = adapter.list_memories(
                user_id=_scope_value(args, defaults, "user_id"),
                agent_id=_scope_value(args, defaults, "agent_id") or None,
                limit=_int(args.get("limit"), 50, minimum=1, maximum=200),
                offset=_int(args.get("offset"), 0, minimum=0, maximum=1_000_000),
                order=str(args.get("order") or "desc") if str(args.get("order") or "desc") in {"desc", "asc"} else "desc",
            )
            raw_items = raw.get("memories", []) if isinstance(raw, dict) else []
            if not isinstance(raw_items, list):
                raw_items = []
            memories = [_compact_memory(item, include_raw=bool(args.get("include_raw"))) for item in raw_items if isinstance(item, dict)]
            return _json({"memories": memories, "count": len(memories), "raw": raw})

        return tool_error(f"Unknown tool: {tool_name}")
    except Exception as exc:
        return tool_error(str(exc))
