from __future__ import annotations

import sys
import types

import pytest

from hermes_llm import HermesHostLLMProvider, HermesLLMResponse


class CallRecorder:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def fake_auxiliary(monkeypatch):
    recorder = CallRecorder({
        "content": "remembered answer",
        "usage": {"total_tokens": 11, "prompt_tokens": 7, "completion_tokens": 4},
        "model": "fake-model",
        "finish_reason": "stop",
        "tool_calls": [{"id": "tool-1"}],
    })
    agent_module = types.ModuleType("agent")
    aux_module = types.ModuleType("agent.auxiliary_client")
    aux_module.async_call_llm = recorder
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux_module)
    return recorder


def test_complete_routes_to_hermes_auxiliary_with_task_and_default_auto_resolution(fake_auxiliary):
    provider = HermesHostLLMProvider(llm_config={"mode": "hermes", "task": "hy_memory", "temperature": 0.1, "max_tokens": 32, "timeout": 12})

    response = provider.complete("probe")

    assert isinstance(response, HermesLLMResponse)
    assert response.content == "remembered answer"
    assert response.tokens_used == 11
    assert response.prompt_tokens == 7
    assert response.completion_tokens == 4
    assert response.model == "fake-model"
    assert response.finish_reason == "stop"
    assert response.tool_calls == [{"id": "tool-1"}]
    assert fake_auxiliary.calls == [{
        "task": "hy_memory",
        "messages": [{"role": "user", "content": "probe"}],
        "temperature": 0.1,
        "max_tokens": 32,
        "tools": None,
        "timeout": 12,
        "extra_body": {},
    }]


def test_complete_messages_preserves_messages_tools_and_explicit_provider_config(fake_auxiliary):
    provider = HermesHostLLMProvider(llm_config={
        "task": "hy_memory",
        "provider": "openrouter",
        "model": "tencent/hy3-preview",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "runtime-key",
        "temperature": 0.3,
        "max_tokens": 64,
        "timeout": 20,
        "extra_body": {"reasoning": {"enabled": False}},
    })
    messages = [{"role": "system", "content": "extract"}, {"role": "user", "content": "fact"}]
    tools = [{"type": "function", "function": {"name": "store", "parameters": {"type": "object"}}}]

    response = provider.complete_messages(messages=messages, tools=tools, tool_choice="auto")

    assert response.content == "remembered answer"
    assert fake_auxiliary.calls[-1] == {
        "task": "hy_memory",
        "provider": "openrouter",
        "model": "tencent/hy3-preview",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "runtime-key",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 64,
        "tools": tools,
        "timeout": 20,
        "extra_body": {"reasoning": {"enabled": False}},
    }


def test_string_and_object_return_shapes_are_normalized(monkeypatch):
    string_recorder = CallRecorder("plain text")
    agent_module = types.ModuleType("agent")
    aux_module = types.ModuleType("agent.auxiliary_client")
    aux_module.async_call_llm = string_recorder
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux_module)

    assert HermesHostLLMProvider().complete("x").content == "plain text"

    object_response = types.SimpleNamespace(
        content="object text",
        usage=types.SimpleNamespace(total_tokens=3, prompt_tokens=1, completion_tokens=2),
        model="object-model",
        finish_reason="length",
    )
    object_recorder = CallRecorder(object_response)
    aux_module.async_call_llm = object_recorder

    response = HermesHostLLMProvider().chat([{"role": "user", "content": "x"}])
    assert response.content == "object text"
    assert response.tokens_used == 3
    assert response.prompt_tokens == 1
    assert response.completion_tokens == 2
    assert response.model == "object-model"
    assert response.finish_reason == "length"


def test_forced_tool_choice_is_rejected_before_calling_hermes(fake_auxiliary):
    provider = HermesHostLLMProvider()

    with pytest.raises(ValueError, match="tool_choice"):
        provider.complete_messages(messages=[{"role": "user", "content": "x"}], tools=[], tool_choice={"type": "function", "function": {"name": "forced"}})

    assert fake_auxiliary.calls == []


def test_stats_track_calls_tokens_and_errors(fake_auxiliary):
    provider = HermesHostLLMProvider()

    provider.complete("ok")
    with pytest.raises(ValueError):
        provider.complete_messages(messages=[{"role": "user", "content": "x"}], tool_choice="required")

    stats = provider.get_stats()
    assert stats["total_calls"] == 1
    assert stats["total_tokens"] == 11
    assert stats["errors"] == 1
    assert stats["avg_tokens_per_call"] == 11
    assert stats["error_rate"] == 0.5
