"""Scoped HY Memory LLMProvider injection for Hermes-hosted LLM mode."""

from __future__ import annotations

import importlib
import sys
from typing import Any, Callable, Dict, List, Tuple

KNOWN_LLM_MODULES = [
    "hy_memory.agent.mem_agent",
    "hy_memory.agent.reconciler",
    "hy_memory.agent.extractor",
    "hy_memory.agent.reflector",
    "hy_memory.agent.abstractor",
    "hy_memory.agent.summarizer",
    "hy_memory.agent.emotion_analyzer",
    "hy_memory.agent.intention_detector",
    "hy_memory.pipelines.system2_writer",
    "hy_memory.pipelines.system2_agent",
]


class HyMemoryLLMPatch:
    """Replace HY Memory SDK LLMProvider references with a Hermes provider factory."""

    def __init__(self, provider_factory: Callable[..., Any]):
        self.provider_factory = provider_factory
        self._originals: Dict[Tuple[str, str], Any] = {}
        self._patched: List[str] = []
        self._missing: List[str] = []
        self._installed = False

    def install(self) -> Dict[str, Any]:
        if self._installed:
            return self.status()

        llm_module = importlib.import_module("hy_memory.agent.llm_provider")
        original = getattr(llm_module, "LLMProvider")
        replacement = self._replacement_class()

        self._patch_attr(llm_module, "LLMProvider", original, replacement)
        self._patch_module_name("hy_memory.agent", original, replacement, optional=True)

        for module_name in KNOWN_LLM_MODULES:
            self._patch_module_name(module_name, original, replacement, optional=True)

        for name, module in list(sys.modules.items()):
            if not (name.startswith("hy_memory.agent.") or name.startswith("hy_memory.pipelines.")):
                continue
            self._patch_attr(module, "LLMProvider", original, replacement)

        self._installed = True
        return self.status()

    def restore(self) -> Dict[str, Any]:
        if not self._installed and not self._originals:
            return self.status(restored=False)
        for (module_name, attr_name), original in reversed(list(self._originals.items())):
            module = sys.modules.get(module_name)
            if module is not None:
                setattr(module, attr_name, original)
        self._originals.clear()
        self._patched.clear()
        self._installed = False
        return self.status(restored=True)

    def status(self, *, restored: bool = False) -> Dict[str, Any]:
        return {
            "installed": self._installed,
            "patched": list(self._patched),
            "missing": list(dict.fromkeys(self._missing)),
            "restored": restored,
        }

    def _replacement_class(self):
        provider_factory = self.provider_factory

        class HermesLLMProviderProxy:
            def __new__(cls, *args: Any, **kwargs: Any):
                return provider_factory(*args, **kwargs)

        HermesLLMProviderProxy.__name__ = "HermesLLMProviderProxy"
        return HermesLLMProviderProxy

    def _patch_module_name(self, module_name: str, original: Any, replacement: Any, *, optional: bool) -> None:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            if optional:
                self._missing.append(module_name)
                return
            raise
        self._patch_attr(module, "LLMProvider", original, replacement)

    def _patch_attr(self, module: Any, attr_name: str, original: Any, replacement: Any) -> None:
        if getattr(module, attr_name, None) is not original:
            return
        key = (module.__name__, attr_name)
        if key not in self._originals:
            self._originals[key] = original
            self._patched.append(f"{module.__name__}.{attr_name}")
        setattr(module, attr_name, replacement)
