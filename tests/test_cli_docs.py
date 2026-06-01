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


def test_readme_documents_local_read_only_dashboard():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "${HERMES_HOME:-$HOME/.hermes}/plugins/hy_memory/cli.py" in readme
    assert "python cli.py dashboard" not in readme
    assert "http://127.0.0.1:8765" in readme
    assert "read-only" in readme or "只读" in readme
    assert "history.db.memory_history" in readme
    assert "local active vector metadata" in readme
    assert "vector_db/chroma.sqlite3" in readme
    assert "operation/audit" in readme
    assert "cache.db.memory_operations" in readme
    assert "sticky top navigation" in readme
    assert "paginated at 25 rows per page" in readme
    assert "line-clamped/truncated" in readme
    assert "click a Content cell" in readme
    assert "type-colored KIND/EVENT badges" in readme
    assert "cache.db.pipeline_logs" in readme
    assert "cache.db.system_metrics" in readme
    assert "/api/history-records" in readme
    assert "Raw / History Memory Records" in readme
    assert "command-surface overview" in readme
    assert "History raw L1" in readme
    assert "Current L3 records" in readme
    assert "History L1 / L3" not in readme
    assert "docs/screenshots/dashboard-overview.png" in readme
    assert "docs/screenshots/dashboard-usage.png" in readme
    assert "docs/screenshots/dashboard-records.png" in readme
    for screenshot in [
        "docs/screenshots/dashboard-overview.png",
        "docs/screenshots/dashboard-usage.png",
        "docs/screenshots/dashboard-records.png",
    ]:
        path = PROJECT_ROOT / screenshot
        assert path.exists(), screenshot
        assert path.stat().st_size > 10_000, screenshot
    assert "l1_raw" in readme
    assert "l3_" in readme


def test_bundled_skill_documents_installed_dashboard_path():
    skill = (PROJECT_ROOT / "resources" / "skills" / "hy-memory-curation" / "SKILL.md").read_text(encoding="utf-8")

    assert "${HERMES_HOME:-$HOME/.hermes}/plugins/hy_memory/cli.py" in skill
    assert "python cli.py dashboard" not in skill
    assert "not from the development repository" in skill
    assert "Raw / History Memory Records" in skill
    assert "local active vector metadata" in skill
    assert "operation/audit" in skill
    assert "sticky top navigation" in skill
    assert "paginated at 25 rows per page" in skill
    assert "line-clamped/truncated" in skill
    assert "click a Content cell" in skill
    assert "type-colored KIND/EVENT badges" in skill
    assert "l1_raw" in skill
    assert "l3_" in skill
    assert "command-surface overview" in skill
    assert "History raw L1" in skill
    assert "Current L3 records" in skill
    assert "History L1 / L3" not in skill
    assert "copy-id controls" not in skill


def test_gitignore_excludes_local_generated_files():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/reference/" in gitignore
    assert "__pycache__/" in gitignore
    assert ".pytest_cache/" in gitignore
