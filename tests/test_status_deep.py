from __future__ import annotations

import json
import sys
import types

from client_adapter import HyMemoryClientAdapter
from config import load_hy_memory_config, save_hy_memory_config
from hermes_llm import HermesLLMResponse
from tool_handlers import handle_tool_call


class OriginalLLMProvider:
    pass


class FakeDeepClient:
    def __init__(self):
        self._vector_store = object()
        self._embed_service = types.SimpleNamespace(embed=self.embed)
        self.closed = False

    async def embed(self, text, **kwargs):
        return [0.1, 0.2, 0.3]

    def list_memories(self, **kwargs):
        return {"memories": []}

    def close(self):
        self.closed = True


class FakeHyMemoryClient:
    created = []

    @classmethod
    def from_config(cls, config_dict, mode=None):
        client = FakeDeepClient()
        cls.created.append({"client": client, "config_dict": config_dict, "mode": mode})
        return client


class FakeLLMProvider:
    def complete_messages(self, messages, max_tokens=None, temperature=None, tools=None, tool_choice=None):
        return HermesLLMResponse(content="ok", tokens_used=2, prompt_tokens=1, completion_tokens=1, model="fake")


def install_fake_sdk(monkeypatch):
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


def test_shallow_status_is_local_only_and_redacted(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMORY_EMBEDDER_API_KEY", raising=False)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(cfg)
    monkeypatch.setattr(adapter, "get_client", lambda: (_ for _ in ()).throw(AssertionError("shallow status initialized client")))

    status = adapter.status()

    assert status["configured"] is True
    assert status["client_initialized"] is False
    assert status["llm_mode"] == "hermes"
    assert status["llm_task"] == "hy_memory"
    assert "checks" not in status
    assert status["embedder"]["api_key_env"] == "MEMORY_EMBEDDER_API_KEY"
    assert "api_key" not in status["embedder"]


def test_deep_status_checks_sdk_vector_embedder_and_hermes_llm(monkeypatch, tmp_path):
    FakeHyMemoryClient.created.clear()
    install_fake_sdk(monkeypatch)
    monkeypatch.setenv("MEMORY_EMBEDDER_API_KEY", "embed-secret")
    save_hy_memory_config({"runtime": {"mode": "in_process"}}, tmp_path)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(cfg, llm_provider_factory=lambda *args, **kwargs: FakeLLMProvider())

    status = adapter.status(deep=True)

    assert status["deep"] is True
    assert status["checks"]["sdk_import"]["status"] == "ok"
    assert status["checks"]["vector_store"]["status"] == "ok"
    assert status["checks"]["embedder"] == {"status": "ok", "dims": 3}
    assert status["checks"]["llm"] == {"status": "ok", "mode": "hermes", "task": "hy_memory"}
    assert "embed-secret" not in json.dumps(status)
    assert FakeHyMemoryClient.created

    adapter.close()


def test_managed_deep_status_starts_worker_when_runtime_is_ready(monkeypatch, tmp_path):
    calls = {"runtime_factory": 0}

    class ReadyRuntime:
        def __init__(self, config):
            self.config = config

        def status(self, *, check_sdk=False):
            return {
                "mode": "managed_venv",
                "venv_exists": True,
                "sdk_available": True,
                "worker_script_exists": True,
                "worker_started": False,
                "worker_pid": None,
            }

    def runtime_factory(config, llm_provider_factory=None):
        calls["runtime_factory"] += 1
        return FakeDeepClient()

    monkeypatch.setenv("MEMORY_EMBEDDER_API_KEY", "embed-secret")
    monkeypatch.setattr("client_adapter.ManagedVenvRuntime", ReadyRuntime)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(
        cfg,
        llm_provider_factory=lambda *args, **kwargs: FakeLLMProvider(),
        runtime_client_factory=runtime_factory,
    )

    status = adapter.status(deep=True)

    assert calls["runtime_factory"] == 1
    assert status["checks"]["worker"]["status"] == "ok"
    assert status["checks"]["vector_store"]["status"] == "ok"
    assert status["checks"]["embedder"] == {"status": "ok", "dims": 3}
    assert status["runtime"]["worker_started"] is True
    assert "managed_runtime_not_started" not in json.dumps(status)

    adapter.close()


def test_managed_deep_status_does_not_install_or_start_when_runtime_missing(monkeypatch, tmp_path):
    class MissingRuntime:
        def __init__(self, config):
            self.config = config

        def status(self, *, check_sdk=False):
            return {
                "mode": "managed_venv",
                "venv_exists": False,
                "sdk_available": False,
                "worker_script_exists": True,
            }

    def runtime_factory(*args, **kwargs):
        raise AssertionError("deep status must not start or install a missing runtime")

    monkeypatch.setenv("MEMORY_EMBEDDER_API_KEY", "embed-secret")
    monkeypatch.setattr("client_adapter.ManagedVenvRuntime", MissingRuntime)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(
        cfg,
        llm_provider_factory=lambda *args, **kwargs: FakeLLMProvider(),
        runtime_client_factory=runtime_factory,
    )

    status = adapter.status(deep=True)

    assert status["checks"]["worker"] == {"status": "skipped", "reason": "runtime_not_installed"}
    assert status["checks"]["vector_store"] == {"status": "skipped", "reason": "runtime_not_installed"}
    assert status["checks"]["embedder"] == {"status": "skipped", "reason": "runtime_not_installed"}
    assert "managed_runtime_not_started" not in json.dumps(status)

    adapter.close()


def test_deep_status_reports_missing_embedder_key_without_calling_network(monkeypatch, tmp_path):
    install_fake_sdk(monkeypatch)
    monkeypatch.delenv("MEMORY_EMBEDDER_API_KEY", raising=False)
    save_hy_memory_config({"runtime": {"mode": "in_process"}}, tmp_path)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(cfg, llm_provider_factory=lambda *args, **kwargs: FakeLLMProvider())

    status = adapter.status(deep=True)

    assert status["checks"]["embedder"]["status"] == "missing_api_key"
    assert status["checks"]["llm"]["status"] == "ok"

    adapter.close()


def test_status_tool_forwards_deep_flag():
    class FakeAdapter:
        def __init__(self):
            self.deep_values = []

        def status(self, deep=False):
            self.deep_values.append(deep)
            return {"configured": True, "deep": deep}

    adapter = FakeAdapter()

    payload = json.loads(handle_tool_call(adapter, {}, "hy_memory_status", {"deep": True}))

    assert payload == {"configured": True, "deep": True}
    assert adapter.deep_values == [True]
