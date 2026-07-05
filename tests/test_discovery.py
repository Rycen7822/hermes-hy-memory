from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HERMES_AGENT = Path('/home/xu/.hermes/hermes-agent')
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_probe(tmp_path: Path) -> dict:
    home = tmp_path / 'hermes-home'
    plugin_dir = home / 'plugins'
    plugin_dir.mkdir(parents=True)
    (plugin_dir / 'hy_memory').symlink_to(PROJECT_ROOT, target_is_directory=True)

    script = r'''
import json
from plugins.memory import discover_memory_providers, load_memory_provider
providers = discover_memory_providers()
provider = load_memory_provider('hy_memory')
print(json.dumps({
    'providers': providers,
    'provider_class': type(provider).__name__ if provider else None,
    'name': provider.name if provider else None,
    'tool_names': [schema.get('name') for schema in provider.get_tool_schemas()] if provider else [],
    'available': provider.is_available() if provider else None,
}))
'''
    env = os.environ.copy()
    env['HERMES_HOME'] = str(home)
    env['PYTHONPATH'] = str(HERMES_AGENT)
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=str(tmp_path),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_user_plugin_is_discovered_and_loaded(tmp_path):
    data = run_probe(tmp_path)

    names = {item[0] for item in data['providers']}
    assert 'hy_memory' in names
    assert data['provider_class'] == 'HyMemoryProvider'
    assert data['name'] == 'hy_memory'
    assert data['available'] is True  # managed worker runtime makes the plugin loadable without parent-process hy_memory SDK


def test_provider_exposes_initial_status_tool(tmp_path):
    data = run_probe(tmp_path)

    assert data['tool_names'] == ['hy_memory']
