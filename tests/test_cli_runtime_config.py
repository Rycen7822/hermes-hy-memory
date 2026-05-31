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


def test_cli_init_writes_managed_runtime_and_config_show_reports_it(tmp_path):
    init = run_cli("init", "--runtime-mode", "managed_venv", "--runtime-auto-install", "false", "--non-interactive", hermes_home=tmp_path)
    assert init.returncode == 0, init.stderr
    assert "runtime" in init.stdout
    assert "hy_memory/runtime/venv" in init.stdout

    saved = json.loads((tmp_path / "hy_memory.json").read_text(encoding="utf-8"))
    assert saved["runtime"]["mode"] == "managed_venv"
    assert saved["runtime"]["auto_install"] is False

    show = run_cli("config", "show", hermes_home=tmp_path)
    assert show.returncode == 0, show.stderr
    data = json.loads(show.stdout)
    assert data["runtime"]["mode"] == "managed_venv"
    assert data["runtime"]["auto_install"] is False
    assert data["runtime"]["venv_path"] == str(tmp_path / "hy_memory" / "runtime" / "venv")


def test_cli_config_set_supports_runtime_paths(tmp_path):
    init = run_cli("init", "--non-interactive", hermes_home=tmp_path)
    assert init.returncode == 0, init.stderr

    set_result = run_cli("config", "set", "runtime.mode", "in_process", hermes_home=tmp_path)
    assert set_result.returncode == 0, set_result.stderr

    show = run_cli("config", "show", hermes_home=tmp_path)
    data = json.loads(show.stdout)
    assert data["runtime"]["mode"] == "in_process"
