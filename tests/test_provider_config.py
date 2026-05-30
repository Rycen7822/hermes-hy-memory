from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_provider_class():
    spec = importlib.util.spec_from_file_location(
        "hy_memory_provider_under_test",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.HyMemoryProvider


def test_provider_exposes_config_schema_and_save_config(tmp_path):
    provider = load_provider_class()()

    by_key = {item["key"]: item for item in provider.get_config_schema()}
    assert by_key["mode"]["choices"] == ["lite", "pro", "ultra"]
    assert by_key["llm_api_key"]["env_var"] == "MEMORY_LLM_API_KEY"

    provider.save_config({"mode": "lite", "llm_api_key": "not-written"}, str(tmp_path))
    saved = json.loads((tmp_path / "hy_memory.json").read_text(encoding="utf-8"))
    assert saved == {"mode": "lite"}
