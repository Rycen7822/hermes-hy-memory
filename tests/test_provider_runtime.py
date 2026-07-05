from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_provider_class():
    spec = importlib.util.spec_from_file_location(
        "hy_memory_provider_runtime_test",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HyMemoryProvider


def test_provider_uses_full_tool_surface_and_adapter_status(tmp_path):
    provider = load_provider_class()()
    provider.initialize("sess-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="u1")

    names = [schema["name"] for schema in provider.get_tool_schemas()]
    assert names == ["hy_memory"]

    status = json.loads(provider.handle_tool_call("hy_memory", {"action": "status"}))
    assert status["user_id"] == "u1"
    assert status["agent_id"] == "coder"
    assert status["data_dir"] == str(tmp_path / "hy_memory")
    assert status["bundled_skill"] == {
        "qualified_name": "hy_memory:hy-memory-curation",
        "listed_in_skills_list": False,
        "load_hint": "skill_view(name='hy_memory:hy-memory-curation')",
    }


def test_provider_system_prompt_exposes_qualified_skill_and_raw_id_rule(tmp_path):
    provider = load_provider_class()()
    provider.initialize("sess-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="u1")

    prompt = provider.system_prompt_block()

    assert "hy_memory(action=" in prompt
    assert "hy_memory:hy-memory-curation" in prompt
    assert "plugin skills are qualified-only" in prompt
    assert "raw" in prompt.lower()
    assert "structured" in prompt.lower()
