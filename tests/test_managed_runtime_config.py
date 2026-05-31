from __future__ import annotations

import json

from config import load_hy_memory_config, save_hy_memory_config


def test_default_runtime_is_managed_venv_with_profile_scoped_paths(tmp_path):
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder", "session_id": "sess"})

    assert cfg.runtime["mode"] == "managed_venv"
    assert cfg.runtime["package"] == "hy-memory"
    assert cfg.runtime["auto_install"] is True
    assert cfg.runtime["venv_path"] == str(tmp_path / "hy_memory" / "runtime" / "venv")
    assert cfg.runtime["worker_script"].endswith("hy_memory_worker.py")
    assert cfg.runtime["python"]


def test_runtime_config_accepts_openclaw_style_aliases_and_persists_no_secrets(tmp_path):
    save_hy_memory_config({
        "runtime": {
            "mode": "in_process",
            "venvPath": "custom-venv",
            "autoInstall": "false",
            "package": "hy-memory[qdrant]",
        }
    }, tmp_path)

    saved = json.loads((tmp_path / "hy_memory.json").read_text(encoding="utf-8"))
    assert saved["runtime"]["venv_path"] == "custom-venv"
    assert saved["runtime"]["auto_install"] == "false"
    assert "api_key" not in json.dumps(saved)

    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    assert cfg.runtime["mode"] == "in_process"
    assert cfg.runtime["venv_path"] == str(tmp_path / "custom-venv")
    assert cfg.runtime["auto_install"] is False
    assert cfg.runtime["package"] == "hy-memory[qdrant]"
