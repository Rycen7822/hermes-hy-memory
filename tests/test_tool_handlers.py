from __future__ import annotations

import json

from tool_handlers import get_tool_schemas, handle_tool_call


class FakeAdapter:
    def __init__(self, list_payload=None):
        self.calls = []
        self.list_payload = list_payload or {"memories": [{"memory_id": "m1", "content": "stored fact"}]}

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return {"results": [{"memory_id": "m1", "content": "stored fact", "score": 0.91, "layer": "normal", "tags": ["x"]}]}

    def add(self, data, **kwargs):
        self.calls.append(("add", data, kwargs))
        return {"success": True, "memory_id": "m2"}

    def get(self, memory_id):
        self.calls.append(("get", memory_id, {}))
        return {"memory_id": memory_id, "content": "stored fact"}

    def update(self, memory_id, content):
        self.calls.append(("update", memory_id, {"content": content}))
        return {"success": True, "memory_id": memory_id}

    def delete(self, memory_id):
        self.calls.append(("delete", memory_id, {}))
        return {"success": True, "deleted_count": 1}

    def delete_all(self, **kwargs):
        self.calls.append(("delete_all", None, kwargs))
        return {"success": True, "deleted_count": 3}

    def list_memories(self, **kwargs):
        self.calls.append(("list_memories", None, kwargs))
        return self.list_payload

    def status(self, deep=False):
        self.calls.append(("status", None, {"deep": deep}))
        return {"configured": True, "mode": "pro", "deep": deep}


def defaults():
    return {"user_id": "u-default", "agent_id": "a-default", "session_id": "s-default", "top_k": 10, "min_score": 0.4, "profile_limit": 5, "profile_min_score": 0.4, "reader": ""}


def parse(result):
    return json.loads(result)


def test_tool_schemas_are_namespaced_and_complete():
    names = [schema["name"] for schema in get_tool_schemas()]

    assert names == ["hy_memory"]
    schema = get_tool_schemas()[0]
    params = schema["parameters"]
    assert params["required"] == ["action"]
    assert params["properties"]["action"]["enum"] == ["add", "search", "get", "update", "delete", "list", "status"]


def test_aggregate_tool_requires_known_action():
    missing = parse(handle_tool_call(FakeAdapter(), defaults(), "hy_memory", {}))
    unknown = parse(handle_tool_call(FakeAdapter(), defaults(), "hy_memory", {"action": "merge"}))

    assert "action" in missing["error"]
    assert "Unknown HY Memory action" in unknown["error"]


def test_aggregate_search_dispatches_to_existing_behavior():
    adapter = FakeAdapter()

    result = parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "search", "query": "hello", "limit": 99, "min_score": 0.5}))

    assert result["count"] == 1
    assert result["results"][0]["id"] == "m1"
    assert adapter.calls[0] == ("search", "hello", {"user_ids": ["u-default"], "agent_ids": ["a-default"], "session_ids": None, "limit": 50, "min_score": 0.5, "profile_limit": 5, "profile_min_score": 0.4, "reader": ""})


def test_aggregate_add_get_update_delete_list_status_dispatch():
    adapter = FakeAdapter()

    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "add", "content": "fact"}))["memory_id"] == "m2"
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "get", "memory_id": "m1"}))["memory"]["content"] == "stored fact"
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "update", "memory_id": "m1", "content": "new"}))["memory_id"] == "m1"
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "delete", "memory_id": "m1"}))["deleted_count"] == 1
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "list", "limit": 5}))["count"] == 1
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "status", "deep": True}))["deep"] is True

    assert [call[0] for call in adapter.calls] == [
        "add",
        "search",
        "get",
        "get",
        "update",
        "delete",
        "list_memories",
        "status",
    ]


def test_search_uses_active_scope_and_clamps_limit():
    adapter = FakeAdapter()
    result = parse(handle_tool_call(adapter, defaults(), "hy_memory_search", {"query": "hello", "limit": 99, "min_score": 0.5}))

    assert result["count"] == 1
    assert result["results"][0]["id"] == "m1"
    assert adapter.calls[0] == ("search", "hello", {"user_ids": ["u-default"], "agent_ids": ["a-default"], "session_ids": None, "limit": 50, "min_score": 0.5, "profile_limit": 5, "profile_min_score": 0.4, "reader": ""})


def test_search_requires_query():
    result = parse(handle_tool_call(FakeAdapter(), defaults(), "hy_memory_search", {}))

    assert "error" in result
    assert "query" in result["error"]


def test_add_accepts_content_or_messages():
    adapter = FakeAdapter()
    content_result = parse(handle_tool_call(adapter, defaults(), "hy_memory_add", {"content": "fact", "metadata": {"source": "test"}}))
    messages_result = parse(handle_tool_call(adapter, defaults(), "hy_memory_add", {"messages": [{"role": "user", "content": "hello"}]}))

    assert content_result["memory_id"] == "m2"
    assert content_result["structured_memory_ids"] == ["m1"]
    assert content_result["structured_count"] == 1
    assert content_result["searchable"] is True
    assert messages_result["memory_id"] == "m2"
    assert messages_result["structured_memory_ids"] == []
    assert messages_result["structured_count"] == 0
    assert messages_result["searchable"] is False
    assert adapter.calls[0] == ("add", "fact", {"user_id": "u-default", "agent_id": "a-default", "session_id": "s-default", "metadata": {"source": "test"}, "memory_at": None})
    assert adapter.calls[1] == ("search", "fact", {"user_ids": ["u-default"], "agent_ids": ["a-default"], "session_ids": ["s-default"], "limit": 10, "min_score": 0, "profile_limit": 10, "profile_min_score": 0, "reader": ""})
    assert adapter.calls[2][1] == [{"role": "user", "content": "hello"}]


def test_delete_requires_exact_id_or_confirmed_bulk_delete():
    adapter = FakeAdapter()

    assert "error" in parse(handle_tool_call(adapter, defaults(), "hy_memory_delete", {}))
    assert "error" in parse(handle_tool_call(adapter, defaults(), "hy_memory_delete", {"all": True}))

    single = parse(handle_tool_call(adapter, defaults(), "hy_memory_delete", {"memory_id": "m1"}))
    bulk = parse(handle_tool_call(adapter, defaults(), "hy_memory_delete", {"all": True, "confirm": True, "agent_id": "a1"}))

    assert single["deleted_count"] == 1
    assert bulk["deleted_count"] == 3
    assert adapter.calls[-1] == ("delete_all", None, {"user_id": "u-default", "agent_ids": ["a1"], "session_ids": None})


def test_get_update_list_status_dispatch():
    adapter = FakeAdapter()

    assert parse(handle_tool_call(adapter, defaults(), "hy_memory_get", {"memory_id": "m1"}))["memory"]["content"] == "stored fact"
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory_update", {"memory_id": "m1", "content": "new"}))["memory_id"] == "m1"
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory_list", {"limit": 5}))["count"] == 1
    assert parse(handle_tool_call(adapter, defaults(), "hy_memory_status", {}))["mode"] == "pro"
    assert [call[0] for call in adapter.calls] == ["get", "get", "update", "list_memories", "status"]
    assert adapter.calls[1] == ("get", "m1", {})
    assert adapter.calls[-1] == ("status", None, {"deep": False})


def test_list_accepts_hy_memory_sdk_vdb_bucket_shape():
    adapter = FakeAdapter(list_payload={"vdb": {"memories": [{"memory_id": "m-sdk", "content": "sdk fact"}], "total": 1}, "elapsed_ms": 2.3})

    result = parse(handle_tool_call(adapter, defaults(), "hy_memory_list", {"limit": 5}))

    assert result["count"] == 1
    assert result["memories"][0]["id"] == "m-sdk"
    assert result["raw"]["vdb"]["total"] == 1


def test_add_partial_error_is_not_reported_as_success():
    class PartialAddAdapter(FakeAdapter):
        def add(self, data, **kwargs):
            self.calls.append(("add", data, kwargs))
            return {
                "success": True,
                "memory_id": "raw-1",
                "error_code": 502,
                "error_message": "[LLM_ERROR] Connection error.",
            }

    result = parse(handle_tool_call(PartialAddAdapter(), defaults(), "hy_memory_add", {"content": "durable fact"}))

    assert result["success"] is False
    assert result["partial_success"] is True
    assert result["memory_id"] == "raw-1"
    assert result["raw_memory_id"] == "raw-1"
    assert result["searchable"] is False
    assert result["error_code"] == 502


def test_update_rejects_raw_shadow_memory_before_mutation():
    class RawAdapter(FakeAdapter):
        def get(self, memory_id):
            self.calls.append(("get", memory_id, {}))
            return {"memory_id": memory_id, "content": "raw", "layer": "l1_raw", "status": "shadow"}

        def update(self, memory_id, content):
            raise AssertionError("raw ids must not be mutated")

    adapter = RawAdapter()

    result = parse(handle_tool_call(adapter, defaults(), "hy_memory_update", {"memory_id": "raw-1", "content": "new"}))

    assert result["success"] is False
    assert result["error_code"] == "raw_id_not_structured"
    assert result["raw_memory_id"] == "raw-1"
    assert adapter.calls == [("get", "raw-1", {})]


def test_update_allows_structured_memory_id():
    class StructuredAdapter(FakeAdapter):
        def get(self, memory_id):
            self.calls.append(("get", memory_id, {}))
            return {"memory_id": memory_id, "content": "old", "layer": "l4_identity"}

    adapter = StructuredAdapter()

    result = parse(handle_tool_call(adapter, defaults(), "hy_memory_update", {"memory_id": "structured-1", "content": "new"}))

    assert result["success"] is True
    assert result["memory_id"] == "structured-1"
    assert adapter.calls == [("get", "structured-1", {}), ("update", "structured-1", {"content": "new"})]


def test_list_filters_session_id_from_vdb_payload():
    schemas = {schema["name"]: schema for schema in get_tool_schemas()}
    assert "session_id" in schemas["hy_memory"]["parameters"]["properties"]
    adapter = FakeAdapter(
        list_payload={
            "vdb": {
                "memories": [
                    {"memory_id": "keep", "content": "in session", "metadata": {"session_id": "s-keep"}},
                    {"memory_id": "drop", "content": "other session", "metadata": {"session_id": "s-drop"}},
                    {"memory_id": "keep-raw", "content": "nested session", "raw": {"metadata": {"session_id": "s-keep"}}},
                ],
                "total": 3,
            }
        }
    )

    result = parse(handle_tool_call(adapter, defaults(), "hy_memory", {"action": "list", "limit": 5, "session_id": "s-keep"}))

    assert result["count"] == 2
    assert [item["id"] for item in result["memories"]] == ["keep", "keep-raw"]
