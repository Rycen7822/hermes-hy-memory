from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from client_adapter import (
    HyMemoryClientAdapter,
    build_sdk_config_dict,
    normalize_search_memories,
)
from config import load_hy_memory_config


class FakeHyMemoryClient:
    created = []

    def __init__(self, config_dict=None, mode=None):
        self.config_dict = config_dict
        self.mode = mode
        self.calls = []
        self.closed = False

    @classmethod
    def from_config(cls, config_dict, mode=None):
        client = cls(config_dict=config_dict, mode=mode)
        cls.created.append(client)
        return client

    def add(self, data, **kwargs):
        self.calls.append(("add", data, kwargs))
        return {"success": True, "memory_id": "m1"}

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return {"normal": [{"memory_id": "m1", "content": query, "score": 0.9}]}

    def get(self, memory_id):
        self.calls.append(("get", memory_id, {}))
        return {"memory_id": memory_id, "content": "hello"}

    def update(self, memory_id, content):
        self.calls.append(("update", memory_id, {"content": content}))
        return {"success": True, "memory_id": memory_id}

    def delete(self, memory_id):
        self.calls.append(("delete", memory_id, {}))
        return {"success": True, "deleted_count": 1}

    def delete_all(self, **kwargs):
        self.calls.append(("delete_all", None, kwargs))
        return {"success": True, "deleted_count": 2}

    def list_memories(self, **kwargs):
        self.calls.append(("list_memories", None, kwargs))
        return {"memories": []}

    def get_metrics(self, **kwargs):
        self.calls.append(("get_metrics", None, kwargs))
        return {"ok": True}

    def get_write_status(self, request_id):
        self.calls.append(("get_write_status", request_id, {}))
        return {"request_id": request_id, "status": "done"}

    def close(self):
        self.closed = True


@pytest.fixture
def fake_hy_memory(monkeypatch):
    FakeHyMemoryClient.created.clear()

    class OriginalLLMProvider:
        pass

    root = types.ModuleType("hy_memory")
    root.__path__ = []
    root.HyMemoryClient = FakeHyMemoryClient
    agent = types.ModuleType("hy_memory.agent")
    agent.__path__ = []
    llm_provider = types.ModuleType("hy_memory.agent.llm_provider")
    llm_provider.LLMProvider = OriginalLLMProvider
    agent.LLMProvider = OriginalLLMProvider
    monkeypatch.setitem(sys.modules, "hy_memory", root)
    monkeypatch.setitem(sys.modules, "hy_memory.agent", agent)
    monkeypatch.setitem(sys.modules, "hy_memory.agent.llm_provider", llm_provider)
    return root


def test_normalize_search_memories_flattens_openclaw_style_groups():
    raw = {
        "profile": [{"memory_id": "p"}],
        "proactive": [{"memory_id": "a"}],
        "normal": [{"memory_id": "n"}],
        "custom": [{"memory_id": "c"}],
        "ignored": {"memory_id": "x"},
    }

    assert [item["memory_id"] for item in normalize_search_memories(raw)] == ["p", "a", "n", "c"]
    assert normalize_search_memories([{"memory_id": "list"}]) == [{"memory_id": "list"}]
    assert normalize_search_memories(None) == []


def test_build_sdk_config_dict_uses_profile_scoped_paths(tmp_path):
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})

    sdk_config = build_sdk_config_dict(cfg)

    assert sdk_config["vector_store"]["provider"] == "chroma"
    assert sdk_config["vector_store"]["collection_name"] == "hermes_memories"
    assert sdk_config["vector_store"]["persist_directory"] == str(tmp_path / "hy_memory" / "data" / "vector_db")
    assert sdk_config["cache"]["db_path"] == str(tmp_path / "hy_memory" / "data" / "cache.db")
    assert sdk_config["history"]["db_path"] == str(tmp_path / "hy_memory" / "data" / "history.db")
    assert sdk_config["graph_store"]["db_path"] == str(tmp_path / "hy_memory" / "data" / "kuzu_db")
    assert sdk_config["mode"] == "pro"


def test_adapter_lazy_initializes_client_and_forwards_calls(tmp_path, fake_hy_memory):
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder", "session_id": "sess"})
    adapter = HyMemoryClientAdapter(cfg)

    assert not adapter.client_initialized
    result = adapter.add("hello", user_id="u1", agent_id="a1", session_id="s1", metadata={"source": "test"})

    assert result["memory_id"] == "m1"
    assert adapter.client_initialized
    assert len(FakeHyMemoryClient.created) == 1
    client = FakeHyMemoryClient.created[0]
    assert client.mode == "pro"
    assert client.calls[0] == ("add", "hello", {"user_id": "u1", "agent_id": "a1", "session_id": "s1", "metadata": {"source": "test"}, "memory_at": None})

    search = adapter.search("query", user_ids=["u1"], agent_ids=["a1"], session_ids=["s1"], limit=3, min_score=0.5, profile_limit=2, profile_min_score=0.6, reader="hybrid")
    assert search["results"][0]["content"] == "query"
    assert client.calls[-1][0] == "search"
    assert client.calls[-1][2]["user_ids"] == ["u1"]

    assert adapter.get("m1")["content"] == "hello"
    assert adapter.update("m1", "new")["success"] is True
    assert adapter.delete("m1")["deleted_count"] == 1
    assert adapter.delete_all(user_id="u1", agent_ids=["a1"], session_ids=["s1"])["deleted_count"] == 2
    assert adapter.list_memories(user_id="u1", agent_id="a1", limit=5, offset=0, order="desc")["memories"] == []
    assert adapter.status()["client_initialized"] is True

    adapter.close()
    assert client.closed is True
