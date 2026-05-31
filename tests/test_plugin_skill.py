from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "hy_memory_plugin_skill_test",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillOnlyContext:
    def __init__(self):
        self.skills = []

    def register_skill(self, name, path, description=""):
        self.skills.append((name, Path(path), description))


class MemoryProviderContext:
    def __init__(self):
        self.providers = []

    def register_memory_provider(self, provider):
        self.providers.append(provider)


def test_register_exposes_bundled_curation_skill_for_standalone_plugin_context():
    module = load_plugin_module()
    ctx = SkillOnlyContext()

    module.register(ctx)

    assert len(ctx.skills) == 1
    name, path, description = ctx.skills[0]
    assert name == "hy-memory-curation"
    assert path == PROJECT_ROOT / "resources" / "skills" / "hy-memory-curation" / "SKILL.md"
    assert path.exists()
    assert "proactively" in description.lower()
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\nname: hy-memory-curation\n")
    assert "## Post-task Proactive Curation" in content
    assert "hy_memory_add" in content


def test_register_still_registers_memory_provider_for_memory_loader_context():
    module = load_plugin_module()
    ctx = MemoryProviderContext()

    module.register(ctx)

    assert len(ctx.providers) == 1
    assert ctx.providers[0].name == "hy_memory"


def test_plugin_manifest_is_standalone_so_bundled_skill_can_load():
    manifest = (PROJECT_ROOT / "plugin.yaml").read_text(encoding="utf-8")

    assert "kind: standalone" in manifest
