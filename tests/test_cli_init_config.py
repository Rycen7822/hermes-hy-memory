from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, hermes_home: Path):
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "cli.py"), *args, "--hermes-home", str(hermes_home)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_init_writes_normalized_config_without_secret_values(tmp_path):
    result = run_cli(
        "init",
        "--llm-mode", "hermes",
        "--embedder-provider", "openai",
        "--embedder-model", "BAAI/bge-m3",
        "--embedder-base-url", "https://api.siliconflow.cn/v1",
        "--embedder-dims", "1024",
        "--vector-store", "chroma",
        "--collection-name", "hermes_memories",
        "--non-interactive",
        hermes_home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "MEMORY_EMBEDDER_API_KEY" in result.stdout
    assert "MEMORY_LLM_API_KEY" in result.stdout
    assert "secret" not in result.stdout.lower()
    saved = json.loads((tmp_path / "hy_memory.json").read_text(encoding="utf-8"))
    assert saved["llm"]["mode"] == "hermes"
    assert saved["llm"]["task"] == "hy_memory"
    assert saved["embedder"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert saved["embedder"]["embedding_dims"] == 1024
    assert saved["vector_store"]["collection_name"] == "hermes_memories"
    assert "apiKey" not in json.dumps(saved)
    assert "api_key" not in json.dumps(saved)


def test_config_show_redacts_and_config_set_updates_allowed_dotted_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDER_API_KEY", "embed-runtime-secret")
    init = run_cli("init", "--non-interactive", hermes_home=tmp_path)
    assert init.returncode == 0, init.stderr

    set_result = run_cli("config", "set", "embedder.model", "BAAI/bge-m3", hermes_home=tmp_path)
    assert set_result.returncode == 0, set_result.stderr
    show = run_cli("config", "show", hermes_home=tmp_path)

    assert show.returncode == 0, show.stderr
    data = json.loads(show.stdout)
    assert data["embedder"]["model"] == "BAAI/bge-m3"
    assert data["llm"]["max_tokens"] == 1024
    assert data["embedder"]["api_key"] == "[REDACTED]"
    assert "embed-runtime-secret" not in show.stdout


def test_config_set_rejects_unknown_paths(tmp_path):
    result = run_cli("config", "set", "unknown.path", "x", hermes_home=tmp_path)

    assert result.returncode != 0
    assert "unknown.path" in result.stderr


def test_cli_status_accepts_deep_flag_even_when_sdk_missing(tmp_path):
    result = run_cli("status", "--deep", hermes_home=tmp_path)

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["deep"] is True
    assert "checks" in data
