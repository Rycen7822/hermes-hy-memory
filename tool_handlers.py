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
    "Update an existing structured HY Memory record. Search/list first and use a structured memory_id; raw ids from add are storage records and must be deleted/re-added instead of updated.",
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
    "List HY Memory records for the active user scope. Optional session_id is applied as a tool-side post-filter for SDK list payloads.",
    {
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "session_id": {"type": "string"},
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

ACTION_TO_TOOL_NAME = {
    "add": "hy_memory_add",
    "search": "hy_memory_search",
    "get": "hy_memory_get",
    "update": "hy_memory_update",
    "delete": "hy_memory_delete",
    "list": "hy_memory_list",
    "status": "hy_memory_status",
}

HY_MEMORY_SCHEMA = _schema(
    "hy_memory",
    "Manage explicit HY Memory operations through one action-based tool. Use action='search' before update/delete, action='add' to save stable facts, and action='status' for provider diagnostics.",
    {
        "action": {
            "type": "string",
            "enum": list(ACTION_TO_TOOL_NAME),
            "description": "HY Memory operation to perform.",
        },
        "content": {"type": "string", "description": "Memory text to add, or replacement content for update."},
        "messages": {"type": "array", "description": "OpenAI-style messages to add."},
        "metadata": {"type": "object", "description": "Optional JSON metadata for add or scoped operations."},
        "query": {"type": "string", "description": "Search query for action='search'."},
        "memory_id": {"type": "string", "description": "Exact HY Memory id for get/update/delete."},
        "all": {"type": "boolean", "description": "For action='delete', delete all memories in scope only when confirm=true."},
        "confirm": {"type": "boolean", "description": "Required with all=true for bulk delete."},
        "deep": {"type": "boolean", "description": "For action='status', run explicit SDK/vector/embedder/LLM health checks."},
        "user_id": {"type": "string"},
        "agent_id": {"type": "string", "description": "Agent scope. Empty string searches across agents where supported."},
        "session_id": {"type": "string"},
        "memory_at": {"type": "string", "description": "Optional ISO timestamp for action='add'."},
        "limit": {"type": "integer", "description": "Maximum result count for search/list."},
        "offset": {"type": "integer", "description": "List offset for action='list'."},
        "order": {"type": "string", "enum": ["desc", "asc"], "description": "List order for action='list'."},
        "min_score": {"type": "number"},
        "profile_limit": {"type": "integer"},
        "profile_min_score": {"type": "number"},
        "reader": {"type": "string"},
        "include_raw": {"type": "boolean", "description": "Include raw backend payloads for search/list debugging."},
    },
    ["action"],
)

LEGACY_TOOL_SCHEMAS = [ADD_SCHEMA, SEARCH_SCHEMA, GET_SCHEMA, UPDATE_SCHEMA, DELETE_SCHEMA, LIST_SCHEMA, STATUS_SCHEMA]
TOOL_SCHEMAS = [HY_MEMORY_SCHEMA]


def get_tool_schemas(*, include_legacy: bool = False) -> List[Dict[str, Any]]:
    if include_legacy:
        return list(TOOL_SCHEMAS) + list(LEGACY_TOOL_SCHEMAS)
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


def _add_result_id(payload: Mapping[str, Any]) -> str:
    return str(payload.get("memory_id") or payload.get("raw_memory_id") or payload.get("id") or "")


def _add_has_error(payload: Mapping[str, Any]) -> bool:
    return any(payload.get(key) not in (None, "", False) for key in ("error", "error_code", "error_message"))


def _add_error_message(payload: Mapping[str, Any]) -> str:
    for key in ("error_message", "error"):
        value = payload.get(key)
        if value not in (None, "", False):
            return str(value)
    if payload.get("error_code") not in (None, "", False):
        return f"HY Memory add returned error_code={payload.get('error_code')}"
    return "HY Memory add failed"


def _normalize_add_payload(result: Any) -> Dict[str, Any]:
    if not isinstance(result, Mapping):
        return {
            "success": False,
            "partial_success": False,
            "searchable": False,
            "error": "Invalid HY Memory add result",
            "error_message": "HY Memory add returned a non-object result",
            "raw_result": repr(result),
        }
    payload = dict(result)
    memory_id = _add_result_id(payload)
    if memory_id:
        payload["memory_id"] = memory_id
        payload.setdefault("raw_memory_id", memory_id)
    if _add_has_error(payload):
        payload["success"] = False
        payload["partial_success"] = bool(memory_id)
        payload["searchable"] = False
        payload.setdefault("error_message", _add_error_message(payload))
    else:
        payload.setdefault("success", True)
        payload.setdefault("partial_success", False)
        if payload.get("success") is False:
            payload.setdefault("searchable", False)
    return payload


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


def _list_memory_items(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    raw_items = raw.get("memories")
    if raw_items is None and isinstance(raw.get("vdb"), dict):
        raw_items = raw["vdb"].get("memories")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _is_raw_shadow_memory(memory: Any) -> bool:
    if not isinstance(memory, Mapping):
        return False
    raw_value = memory.get("raw")
    raw = dict(raw_value) if isinstance(raw_value, Mapping) else {}
    layer = str(memory.get("layer") or memory.get("memory_layer") or raw.get("layer") or raw.get("memory_layer") or "").lower()
    status = str(memory.get("status") or raw.get("status") or "").lower()
    memory_id = _memory_id(memory).lower()
    return layer == "l1_raw" or status == "shadow" or memory_id.startswith("l1_raw")


def _structured_ids_from_results(results: Any) -> List[str]:
    ids: List[str] = []
    seen = set()
    if not isinstance(results, list):
        return ids
    for item in results:
        if not isinstance(item, Mapping) or _is_raw_shadow_memory(item):
            continue
        memory_id = _memory_id(item)
        if memory_id and memory_id not in seen:
            seen.add(memory_id)
            ids.append(memory_id)
    return ids


def _session_matches(item: Mapping[str, Any], session_id: str) -> bool:
    if not session_id:
        return True
    metadata_value = item.get("metadata")
    raw_value = item.get("raw")
    metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
    raw = dict(raw_value) if isinstance(raw_value, Mapping) else {}
    raw_metadata_value = raw.get("metadata")
    raw_metadata = dict(raw_metadata_value) if isinstance(raw_metadata_value, Mapping) else {}
    candidates = [item.get("session_id"), metadata.get("session_id"), raw.get("session_id"), raw_metadata.get("session_id")]
    return any(str(candidate or "") == session_id for candidate in candidates)


def _augment_add_visibility(adapter: Any, payload: Dict[str, Any], data: Any, *, user_id: str, agent_id: str, session_id: str, defaults: Mapping[str, Any]) -> Dict[str, Any]:
    if payload.get("success") is False or payload.get("partial_success"):
        payload.setdefault("searchable", False)
        return payload
    if not isinstance(data, str):
        payload.setdefault("structured_memory_ids", [])
        payload.setdefault("structured_count", 0)
        payload.setdefault("searchable", False)
        return payload
    query = " ".join(data.split())[:500]
    if not query:
        payload.setdefault("structured_memory_ids", [])
        payload.setdefault("structured_count", 0)
        payload.setdefault("searchable", False)
        return payload
    try:
        result = adapter.search(
            query,
            user_ids=[user_id] if user_id else [],
            agent_ids=_scope_list(agent_id),
            session_ids=_scope_list(session_id),
            limit=10,
            min_score=0,
            profile_limit=10,
            profile_min_score=0,
            reader=str(defaults.get("reader", "")),
        )
        structured_ids = _structured_ids_from_results(result.get("results", []) if isinstance(result, Mapping) else [])
        payload["structured_memory_ids"] = structured_ids
        payload["structured_count"] = len(structured_ids)
        payload["searchable"] = bool(structured_ids)
    except Exception as exc:
        payload["structured_memory_ids"] = []
        payload["structured_count"] = 0
        payload["searchable"] = False
        payload["visibility_check_error"] = str(exc)
    return payload


def handle_tool_call(adapter: Any, defaults: Mapping[str, Any], tool_name: str, args: Mapping[str, Any]) -> str:
    args = dict(args or {})

    try:
        if tool_name == "hy_memory":
            action = str(args.get("action") or "").strip().lower()
            if not action:
                return tool_error("Missing required parameter: action")
            legacy_tool_name = ACTION_TO_TOOL_NAME.get(action)
            if not legacy_tool_name:
                return tool_error(f"Unknown HY Memory action: {action}")
            args.pop("action", None)
            tool_name = legacy_tool_name

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
            user_id = _scope_value(args, defaults, "user_id")
            agent_id = _scope_value(args, defaults, "agent_id")
            session_id = _scope_value(args, defaults, "session_id")
            result = adapter.add(
                data,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                metadata=_metadata(args.get("metadata")),
                memory_at=args.get("memory_at"),
            )
            payload = _normalize_add_payload(result)
            return _json(_augment_add_visibility(adapter, payload, data, user_id=user_id, agent_id=agent_id, session_id=session_id, defaults=defaults))

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
            existing = adapter.get(memory_id)
            if not existing:
                return _json({"success": False, "error_code": "memory_not_found", "memory_id": memory_id})
            if _is_raw_shadow_memory(existing):
                return _json({
                    "success": False,
                    "error_code": "raw_id_not_structured",
                    "raw_memory_id": memory_id,
                    "message": "Raw/shadow memory ids are storage records, not recall records. Search or list first and update structured ids; delete and re-add raw-only records.",
                })
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
            session_id = str(args.get("session_id") or "")
            raw_items = _list_memory_items(raw)
            if session_id:
                raw_items = [item for item in raw_items if _session_matches(item, session_id)]
            memories = [_compact_memory(item, include_raw=bool(args.get("include_raw"))) for item in raw_items]
            return _json({"memories": memories, "count": len(memories), "raw": raw})

        return tool_error(f"Unknown tool: {tool_name}")
    except Exception as exc:
        return tool_error(str(exc))
