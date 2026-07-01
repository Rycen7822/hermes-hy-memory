from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from config import load_hy_memory_config
from client_adapter import build_sdk_config_dict
from runtime import ManagedHyMemoryWorkerClient


def _fake_config() -> SimpleNamespace:
    return SimpleNamespace(
        mode="pro",
        llm={"mode": "hermes"},
        runtime={
            "mode": "managed_venv",
            "venv_path": "/tmp/hy-memory-test-venv",
            "worker_script": "/tmp/hy-memory-worker.py",
            "package": "hy-memory",
            "auto_install": False,
            "python": sys.executable,
            "vdb_pool_size": 2,
        },
    )


class FakeWorkerProcess:
    def __init__(self, pid: int, *, fail_call: bool = False):
        self.pid = pid
        self.started = True
        self.closed = False
        self.fail_call = fail_call
        self.messages: list[dict] = []

    def request(self, message: dict):
        self.messages.append(message)
        if message.get("type") == "init":
            return {"initialized": True, "pid": self.pid}
        if message.get("type") == "call" and self.fail_call:
            self.started = False
            raise RuntimeError("HY Memory worker exited unexpectedly code=-11; stderr=")
        return {"method": message.get("method"), "pid": self.pid}

    def close(self) -> None:
        self.closed = True
        self.started = False


def _client_with_factory(created: list[FakeWorkerProcess], *, fail_first_call: bool = False) -> ManagedHyMemoryWorkerClient:
    def factory(command, llm_provider_factory):
        proc = FakeWorkerProcess(len(created) + 1, fail_call=fail_first_call and not created)
        created.append(proc)
        return proc

    client = ManagedHyMemoryWorkerClient(
        _fake_config(),
        {"vector_store": {"provider": "chroma"}},
        process_factory=factory,
    )
    client.runtime.command = lambda: [sys.executable, "/tmp/hy-memory-worker.py"]  # type: ignore[method-assign]
    return client


def test_runtime_vdb_pool_size_is_profile_configurable(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "hy_memory.json").write_text(
        json.dumps({"runtime": {"vdb_pool_size": 2}}),
        encoding="utf-8",
    )

    config = load_hy_memory_config(hermes_home, runtime={"agent_identity": "default", "session_id": "s"})

    assert config.runtime["vdb_pool_size"] == 2


def test_sdk_config_preserves_configured_bge_m3_dimensions_for_collection_suffix(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "hy_memory.json").write_text(
        json.dumps(
            {
                "embedder": {
                    "provider": "openai",
                    "model": "BAAI/bge-m3",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "embedding_dims": 1024,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_hy_memory_config(hermes_home, runtime={"agent_identity": "default", "session_id": "s"})
    sdk_config = build_sdk_config_dict(config)

    assert sdk_config["vector_store"]["embedding_dims"] == 1024
    assert sdk_config["embedder"]["embedding_dims"] == 0


def test_worker_init_limits_chroma_vdb_executor(monkeypatch):
    worker = importlib.import_module("hy_memory_worker")

    fake_hy_memory = types.ModuleType("hy_memory")
    fake_hy_memory.__path__ = []
    fake_data = types.ModuleType("hy_memory.data")
    fake_data.__path__ = []
    fake_chroma = types.ModuleType("hy_memory.data.vector_store_chroma")

    class OldExecutor:
        def __init__(self):
            self.shutdown_calls = []

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    old_executor = OldExecutor()
    fake_chroma._VDB_POOL_SIZE = 64
    fake_chroma._vdb_executor = old_executor
    fake_data.vector_store_chroma = fake_chroma
    fake_hy_memory.data = fake_data

    monkeypatch.setitem(sys.modules, "hy_memory", fake_hy_memory)
    monkeypatch.setitem(sys.modules, "hy_memory.data", fake_data)
    monkeypatch.setitem(sys.modules, "hy_memory.data.vector_store_chroma", fake_chroma)

    status = worker._configure_chroma_vdb_pool(
        {"vector_store": {"provider": "chroma"}},
        {"vdb_pool_size": 2},
    )

    assert status["status"] == "ok"
    assert status["pool_size"] == 2
    assert status["previous_pool_size"] == 64
    assert fake_chroma._VDB_POOL_SIZE == 2
    assert fake_chroma._vdb_executor._max_workers == 2
    assert old_executor.shutdown_calls == [{"wait": False, "cancel_futures": True}]
    fake_chroma._vdb_executor.shutdown(wait=False, cancel_futures=True)


def test_dead_cached_worker_is_recreated_before_next_call():
    created: list[FakeWorkerProcess] = []
    client = _client_with_factory(created)

    first = client.search("q", user_ids=["u"])
    created[0].started = False
    second = client.search("q", user_ids=["u"])

    assert first == {"method": "search", "pid": 1}
    assert second == {"method": "search", "pid": 2}
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].messages[0]["type"] == "init"


def test_read_only_call_retries_once_after_worker_exit():
    created: list[FakeWorkerProcess] = []
    client = _client_with_factory(created, fail_first_call=True)

    result = client.list_memories(user_id="u", agent_id="a")

    assert result == {"method": "list_memories", "pid": 2}
    assert len(created) == 2
    assert created[0].closed is True


def test_non_idempotent_call_is_not_retried_after_worker_exit():
    created: list[FakeWorkerProcess] = []
    client = _client_with_factory(created, fail_first_call=True)

    with pytest.raises(RuntimeError, match="non-idempotent method 'add'"):
        client.add("content", user_id="u", agent_id="a", session_id="s")

    assert len(created) == 1
    assert created[0].closed is True
    assert client._process is None
