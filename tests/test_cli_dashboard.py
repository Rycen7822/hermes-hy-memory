from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dashboard import validate_dashboard_bind


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cli_dashboard_help_documents_local_read_only_options():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "cli.py"), "dashboard", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run local read-only HY Memory dashboard" in result.stdout
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--no-open" in result.stdout


def test_dashboard_bind_validation_rejects_remote_hosts():
    with pytest.raises(ValueError, match="localhost"):
        validate_dashboard_bind("0.0.0.0", 8765)

    with pytest.raises(ValueError, match="localhost"):
        validate_dashboard_bind("192.168.1.5", 8765)


def test_dashboard_bind_validation_rejects_bad_ports():
    with pytest.raises(ValueError, match="port"):
        validate_dashboard_bind("127.0.0.1", -1)

    with pytest.raises(ValueError, match="port"):
        validate_dashboard_bind("127.0.0.1", 70000)


def test_dashboard_bind_validation_allows_localhost_and_ephemeral_port():
    validate_dashboard_bind("127.0.0.1", 0)
    validate_dashboard_bind("localhost", 8765)
