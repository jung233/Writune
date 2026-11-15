import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


def _registration():
    path = Path(__file__).resolve().parents[1] / 'scripts/register_mcp.py'
    spec = importlib.util.spec_from_file_location('writune_registration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_registration_preserves_comments_other_servers_and_is_idempotent(tmp_path):
    import tomlkit
    register = _registration().register
    config = tmp_path/'config.toml'
    original = '# My existing settings\nmodel = "existing-model"\n[mcp_servers.other]\ncommand = "other-tool" # keep this comment\n'
    config.write_text(original)
    executable = tmp_path/'writune'; executable.touch()
    first = register('codex', config, executable, tmp_path/'config-home')
    assert Path(first['backup_path']).read_text() == original
    text = config.read_text()
    assert '# My existing settings' in text and '# keep this comment' in text
    content = tomlkit.parse(text)
    assert content['model'] == 'existing-model'
    assert content['mcp_servers']['other']['command'] == 'other-tool'
    assert content['mcp_servers']['writune']['enabled']
    assert register('codex', config, executable, tmp_path/'config-home')['changed'] is False


def test_claude_registration_preserves_existing_settings_and_conflicts(tmp_path):
    register = _registration().register
    config = tmp_path/'.claude.json'
    config.write_text(json.dumps({'projects': {'project': {'custom': True}}, 'mcpServers': {'other': {'command': 'other'}}}))
    executable = tmp_path/'writune'; executable.touch()
    register('claude-code', config, executable, tmp_path/'config-home')
    data = json.loads(config.read_text())
    assert data['projects']['project']['custom']
    assert data['mcpServers']['other']['command'] == 'other'
    assert data['mcpServers']['writune']['type'] == 'stdio'
    other = tmp_path/'different-writune'; other.touch()
    before = config.read_bytes()
    with pytest.raises(ValueError, match='different writune'):
        register('claude-code', config, other, tmp_path/'config-home')
    assert config.read_bytes() == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows windowless entry point')
def test_windows_registration_uses_pythonw_without_a_shell(tmp_path):
    register = _registration().register
    executable = tmp_path/'writune.exe'; executable.touch()
    pythonw = tmp_path/'pythonw.exe'; pythonw.touch()
    config = tmp_path/'client.json'
    register('claude-code', config, executable, tmp_path/'config-home')
    entry = json.loads(config.read_text())['mcpServers']['writune']
    assert entry['command'] == str(pythonw.resolve())
    assert entry['args'][:3] == ['-m','writune','serve']


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell installer on Windows')
def test_installer_refuses_to_touch_an_existing_unowned_environment(tmp_path):
    folder = tmp_path/'existing-environment'; folder.mkdir()
    marker = folder/'important.txt'; marker.write_bytes(b'existing environment bytes')
    script = Path(__file__).resolve().parents[1]/'scripts/install.ps1'
    process = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-VenvPath',str(folder)],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
    assert process.returncode != 0
    assert marker.read_bytes() == b'existing environment bytes'
    assert list(folder.iterdir()) == [marker]
