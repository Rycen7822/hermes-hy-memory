from __future__ import annotations

import importlib.util
import time
from pathlib import Path

from capture import build_capture_messages, sanitize_memory_context
from formatting import format_prefetch_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    def __init__(self, config):
        self.config = config
        self.search_calls = []
        self.add_calls = []
        self.closed = False

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return {
            "results": [
                {"memory_id": "m1", "content": "User likes terse status updates.", "score": 0.823, "layer": "profile"},
                {"memory_id": "m2", "content": "A" * 500, "score": 0.7, "layer": "normal"},
            ]
        }

    def add(self, data, **kwargs):
        self.add_calls.append((data, kwargs))
        return {"success": True, "memory_id": f"m{len(self.add_calls)}"}

    def status(self):
        return {"configured": True}

    def close(self):
        self.closed = True


def load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "hy_memory_provider_lifecycle_test",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_format_prefetch_context_is_compact_and_not_wrapped():
    text = format_prefetch_context(
        [
            {"memory_id": "m1", "content": "Keep summaries short", "score": 0.85, "layer": "profile"},
            {"memory_id": "m2", "content": "Old", "score": 0.4, "evolution_chain": [{"content": "Older"}, {"content": "Newest"}]},
        ],
        user_id="u1",
        max_chars=400,
    )

    assert text.startswith("## HY Memory")
    assert "<memory-context>" not in text
    assert "[profile] [score 85%] Keep summaries short (id: m1)" in text
    assert "Evolution: Older -> Newest" in text


def test_capture_sanitizer_removes_memory_context_tags():
    raw = "before <memory-context>secret recall</memory-context> after <relevant-memories>old</relevant-memories>"

    assert sanitize_memory_context(raw) == "before after"


def test_build_capture_messages_skips_trivial_and_cleans_messages():
    assert build_capture_messages("ok", "thanks") is None

    messages = build_capture_messages(
        "<memory-context>recall</memory-context> Please remember project path",
        "Stored it.",
    )
    assert messages == [
        {"role": "user", "content": "Please remember project path"},
        {"role": "assistant", "content": "Stored it."},
    ]


def test_provider_prefetch_sync_turn_memory_write_and_shutdown(tmp_path):
    module = load_provider_module()
    module.HyMemoryClientAdapter = FakeAdapter
    provider = module.HyMemoryProvider()
    provider.initialize("sess-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="u1")

    provider.queue_prefetch("coding style", session_id="sess-1")
    prefetched = provider.prefetch("coding style", session_id="sess-1")
    assert "User likes terse status updates" in prefetched
    assert module.HyMemoryClientAdapter is FakeAdapter
    assert provider._adapter.search_calls[0][1]["user_ids"] == ["u1"]

    provider.sync_turn(
        "<memory-context>injected</memory-context> real user fact",
        "assistant response",
        session_id="sess-1",
    )
    provider.on_memory_write("add", "memory", "explicit durable fact", metadata={"tool_name": "memory"})
    provider.shutdown()

    assert provider._adapter.closed is True
    assert len(provider._adapter.add_calls) == 2
    assert provider._adapter.add_calls[0][0] == [
        {"role": "user", "content": "real user fact"},
        {"role": "assistant", "content": "assistant response"},
    ]
    assert provider._adapter.add_calls[0][1]["metadata"]["type"] == "conversation_turn"
    assert provider._adapter.add_calls[1][0] == "explicit durable fact"
    assert provider._adapter.add_calls[1][1]["metadata"]["target"] == "memory"


def test_provider_skips_writes_for_subagent_context(tmp_path):
    module = load_provider_module()
    module.HyMemoryClientAdapter = FakeAdapter
    provider = module.HyMemoryProvider()
    provider.initialize("sess-1", hermes_home=str(tmp_path), agent_context="subagent")

    provider.sync_turn("important", "response", session_id="sess-1")
    provider.on_memory_write("add", "memory", "explicit durable fact")
    provider.shutdown()

    assert provider._adapter.add_calls == []


def test_session_switch_updates_session_scope_and_clears_prefetch_on_reset(tmp_path):
    module = load_provider_module()
    module.HyMemoryClientAdapter = FakeAdapter
    provider = module.HyMemoryProvider()
    provider.initialize("sess-1", hermes_home=str(tmp_path))
    provider.queue_prefetch("old", session_id="sess-1")
    assert provider.prefetch("old", session_id="sess-1")

    provider.on_session_switch("sess-2", parent_session_id="sess-1", reset=True)
    assert provider.prefetch("old", session_id="sess-1") == ""

    provider.sync_turn("new fact", "new response")
    provider.shutdown()
    assert provider._adapter.add_calls[0][1]["session_id"] == "sess-2"
