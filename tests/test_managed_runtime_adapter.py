from __future__ import annotations

import sys
import types

import pytest

from client_adapter import HyMemoryClientAdapter
from config import load_hy_memory_config, save_hy_memory_config


class FakeManagedClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    def add(self, data, **kwargs):
        self.calls.append(("add", data, kwargs))
        return {"memory_id": "managed-1"}

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return {"profile": [{"id": "m1", "content": "hit"}]}

    def status(self):
        return {"worker": "ok", "runtime": "managed_venv"}

    def close(self):
        self.closed = True


class FakeHyMemoryClient:
    created = []

    @classmethod
    def from_config(cls, config_dict, mode=None):
        client = cls()
        client.config_dict = config_dict
        client.mode = mode
        cls.created.append(client)
        return client

    def add(self, data, **kwargs):
        return {"memory_id": "m1", "data": data, "kwargs": kwargs}

    def close(self):
        pass


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


def test_adapter_default_managed_runtime_uses_worker_factory_without_importing_sdk(tmp_path):
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder", "session_id": "sess"})
    created = []

    def factory(config, llm_provider_factory=None):
        created.append((config, llm_provider_factory))
        return FakeManagedClient()

    adapter = HyMemoryClientAdapter(cfg, runtime_client_factory=factory)

    assert adapter.is_ready() is True
    result = adapter.add("hello", user_id="u1", agent_id="a1", session_id="s1")
    assert result["memory_id"] == "managed-1"
    assert result["raw_memory_id"] == "managed-1"
    assert result["success"] is True
    assert result["partial_success"] is False
    assert len(created) == 1
    assert created[0][0] is cfg
    assert adapter.client_initialized is True

    status = adapter.status()
    assert status["runtime"]["mode"] == "managed_venv"
    assert status["runtime"]["client"] == "worker"
    assert status["sdk_available"] is True

    adapter.close()
    assert adapter.client_initialized is False


def test_adapter_in_process_runtime_keeps_existing_sdk_import_path(tmp_path, fake_hy_memory):
    save_hy_memory_config({"runtime": {"mode": "in_process"}}, tmp_path)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder", "session_id": "sess"})

    adapter = HyMemoryClientAdapter(cfg)
    result = adapter.add("hello", user_id="u1", agent_id="a1", session_id="s1")

    assert result["memory_id"] == "m1"
    assert adapter.status()["runtime"]["mode"] == "in_process"
