from __future__ import annotations

import sys
import types

from client_adapter import HyMemoryClientAdapter
from config import load_hy_memory_config, save_hy_memory_config
from hy_memory_llm_patch import HyMemoryLLMPatch


class OriginalLLMProvider:
    pass


class OtherLLMProvider:
    pass


def install_fake_hy_memory_tree(monkeypatch, *, include_client: bool = False):
    modules = {}
    for name in [
        "hy_memory",
        "hy_memory.agent",
        "hy_memory.agent.llm_provider",
        "hy_memory.agent.mem_agent",
        "hy_memory.agent.reconciler",
        "hy_memory.agent.extractor",
        "hy_memory.agent.abstractor",
        "hy_memory.agent.summarizer",
        "hy_memory.pipelines",
        "hy_memory.pipelines.system2_writer",
    ]:
        module = types.ModuleType(name)
        if name in {"hy_memory", "hy_memory.agent", "hy_memory.pipelines"}:
            module.__path__ = []
        modules[name] = module
        monkeypatch.setitem(sys.modules, name, module)

    modules["hy_memory.agent.llm_provider"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.agent"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.agent.mem_agent"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.agent.reconciler"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.agent.extractor"].LLMProvider = OtherLLMProvider
    modules["hy_memory.agent.abstractor"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.agent.summarizer"].LLMProvider = OriginalLLMProvider
    modules["hy_memory.pipelines.system2_writer"].LLMProvider = OriginalLLMProvider

    if include_client:
        class FakeHyMemoryClient:
            created = []

            @classmethod
            def from_config(cls, config_dict, mode=None):
                cls.created.append({
                    "config_dict": config_dict,
                    "mode": mode,
                    "llm_provider": modules["hy_memory.agent.llm_provider"].LLMProvider,
                    "mem_agent_provider": modules["hy_memory.agent.mem_agent"].LLMProvider,
                })
                return cls()

            def close(self):
                pass

        modules["hy_memory"].HyMemoryClient = FakeHyMemoryClient
        return modules, FakeHyMemoryClient
    return modules, None


def test_llm_patch_replaces_known_original_references_and_restores(monkeypatch):
    modules, _ = install_fake_hy_memory_tree(monkeypatch)
    patch = HyMemoryLLMPatch(lambda *args, **kwargs: {"provider": "hermes"})

    status = patch.install()

    assert status["installed"] is True
    assert "hy_memory.agent.llm_provider.LLMProvider" in status["patched"]
    assert "hy_memory.agent.abstractor.LLMProvider" in status["patched"]
    assert modules["hy_memory.agent.llm_provider"].LLMProvider() == {"provider": "hermes"}
    assert modules["hy_memory.agent.mem_agent"].LLMProvider() == {"provider": "hermes"}
    assert modules["hy_memory.agent.extractor"].LLMProvider is OtherLLMProvider

    second_status = patch.install()
    assert second_status["patched"] == status["patched"]

    restore_status = patch.restore()
    assert restore_status["restored"] is True
    assert modules["hy_memory.agent.llm_provider"].LLMProvider is OriginalLLMProvider
    assert modules["hy_memory.agent.mem_agent"].LLMProvider is OriginalLLMProvider
    assert modules["hy_memory.agent.extractor"].LLMProvider is OtherLLMProvider
    assert patch.restore()["restored"] is False


def test_client_adapter_installs_patch_before_hy_memory_client_construction(monkeypatch, tmp_path):
    modules, fake_client = install_fake_hy_memory_tree(monkeypatch, include_client=True)
    save_hy_memory_config({"runtime": {"mode": "in_process"}}, tmp_path)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(cfg, llm_provider_factory=lambda *args, **kwargs: "hermes-provider")

    adapter.get_client()

    created = fake_client.created[0]
    assert created["llm_provider"]() == "hermes-provider"
    assert created["mem_agent_provider"]() == "hermes-provider"
    assert adapter.status()["llm_mode"] == "hermes"
    assert adapter.status()["llm_patch_installed"] is True
    assert "hy_memory.agent.llm_provider.LLMProvider" in adapter.status()["llm_patch_targets"]

    adapter.close()
    assert modules["hy_memory.agent.llm_provider"].LLMProvider is OriginalLLMProvider


def test_client_adapter_direct_mode_does_not_patch(monkeypatch, tmp_path):
    modules, fake_client = install_fake_hy_memory_tree(monkeypatch, include_client=True)
    save_hy_memory_config({"runtime": {"mode": "in_process"}, "llm": {"mode": "direct"}}, tmp_path)
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    adapter = HyMemoryClientAdapter(cfg, llm_provider_factory=lambda *args, **kwargs: "hermes-provider")

    adapter.get_client()

    assert fake_client.created[0]["llm_provider"] is OriginalLLMProvider
    assert modules["hy_memory.agent.llm_provider"].LLMProvider is OriginalLLMProvider
    assert adapter.status()["llm_mode"] == "direct"
    assert adapter.status()["llm_patch_installed"] is False
