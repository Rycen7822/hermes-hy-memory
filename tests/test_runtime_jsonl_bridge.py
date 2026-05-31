from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

from hermes_llm import HermesLLMResponse
from hy_memory_worker import ParentBridgeLLMProvider
from runtime import JsonlWorkerProcess


def write_fake_worker(path: Path) -> None:
    path.write_text(textwrap.dedent(r'''
        import json
        import sys

        def send(obj):
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        def read():
            line = sys.stdin.readline()
            if not line:
                raise SystemExit(0)
            return json.loads(line)

        while True:
            message = read()
            msg_id = message.get("id")
            if message.get("type") == "shutdown":
                send({"type": "response", "id": msg_id, "result": {"closed": True}})
                raise SystemExit(0)
            if message.get("type") == "init":
                send({"type": "response", "id": msg_id, "result": {"initialized": True}})
                continue
            if message.get("type") == "call" and message.get("method") == "add":
                send({
                    "type": "llm_request",
                    "id": "llm-1",
                    "payload": {
                        "prompt": "extract memory",
                        "max_tokens": 17,
                        "temperature": 0.1,
                        "tools": [{"type": "function", "function": {"name": "store"}}],
                        "tool_choice": "auto"
                    }
                })
                llm_response = read()
                send({
                    "type": "response",
                    "id": msg_id,
                    "result": {
                        "memory_id": "m-worker",
                        "llm_content": llm_response["result"]["content"],
                        "llm_model": llm_response["result"]["model"],
                        "kwargs": message.get("kwargs", {})
                    }
                })
                continue
            send({"type": "response", "id": msg_id, "error": "unexpected"})
    '''), encoding="utf-8")


class FakeLLMProvider:
    def __init__(self):
        self.calls = []

    def complete(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return HermesLLMResponse(content="parent hermes ok", tokens_used=9, prompt_tokens=4, completion_tokens=5, model="gpt-5.4-mini")


def test_jsonl_worker_process_handles_llm_callback_roundtrip(tmp_path):
    worker = tmp_path / "fake_worker.py"
    write_fake_worker(worker)
    llm = FakeLLMProvider()
    proc = JsonlWorkerProcess([sys.executable, str(worker)], llm_provider_factory=lambda: llm)

    try:
        init = proc.request({"type": "init", "sdk_config": {}, "mode": "pro"})
        assert init == {"initialized": True}
        result = proc.request({
            "type": "call",
            "method": "add",
            "args": ["hello"],
            "kwargs": {"user_id": "u1", "agent_id": "a1", "session_id": "s1"},
        })
    finally:
        proc.close()

    assert result["memory_id"] == "m-worker"
    assert result["llm_content"] == "parent hermes ok"
    assert result["llm_model"] == "gpt-5.4-mini"
    assert result["kwargs"]["user_id"] == "u1"
    assert llm.calls == [{
        "prompt": "extract memory",
        "max_tokens": 17,
        "temperature": 0.1,
        "tools": [{"type": "function", "function": {"name": "store"}}],
        "tool_choice": "auto",
    }]


def test_parent_bridge_llm_provider_supports_complete_messages(monkeypatch):
    payloads = []

    def fake_request_parent_llm(payload):
        payloads.append(payload)
        return {
            "content": "structured ok",
            "tokens_used": 11,
            "prompt_tokens": 5,
            "completion_tokens": 6,
            "model": "gpt-5.4-mini",
            "finish_reason": "stop",
        }

    monkeypatch.setattr("hy_memory_worker.request_parent_llm", fake_request_parent_llm)
    provider = ParentBridgeLLMProvider()
    messages = [{"role": "system", "content": "extract"}, {"role": "user", "content": "fact"}]

    response = asyncio.run(provider.complete_messages(messages=messages, max_tokens=33, temperature=0.2))

    assert response.content == "structured ok"
    assert response.model == "gpt-5.4-mini"
    assert provider.total_calls == 1
    assert payloads == [{
        "messages": messages,
        "prompt": "extract\nfact",
        "max_tokens": 33,
        "temperature": 0.2,
        "stop": None,
        "tools": None,
        "tool_choice": None,
    }]
