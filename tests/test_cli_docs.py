from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cli_status_runs_without_hy_memory_sdk(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "cli.py"), "status", "--hermes-home", str(tmp_path)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert '"configured": true' in result.stdout
    assert '"client_initialized": false' in result.stdout


def test_smoke_script_skips_cleanly_when_sdk_missing(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "smoke_hy_memory.py"), "--skip-if-unconfigured", "--hermes-home", str(tmp_path)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SKIP" in result.stdout or "PASS" in result.stdout


def test_readme_documents_install_enable_verify_and_restart():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "hermes plugins enable hy_memory" in readme
    assert "hermes config set memory.provider hy_memory" in readme
    assert "auxiliary.hy_memory" in readme
    assert "llm.mode" in readme and "hermes" in readme
    assert "MEMORY_EMBEDDER_API_KEY" in readme
    assert "OpenClaw" in readme and "baseUrl" in readme and "embedding_dims" in readme
    assert "restart" in readme.lower() or "reset" in readme.lower()
    assert "scripts/smoke_hy_memory.py --skip-if-unconfigured" in readme


def test_gitignore_excludes_local_generated_files():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/reference/" in gitignore
    assert "__pycache__/" in gitignore
    assert ".pytest_cache/" in gitignore
