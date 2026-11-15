"""Register Writune in client configs, preserving other settings and comments."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from writune.files.atomic import atomic_write


def register(client: str, config: Path, executable: Path, home: Path, replace=False):
    executable = executable.expanduser().resolve(strict=True)
    config = config.expanduser().resolve()
    home = home.expanduser().resolve()
    original = config.read_bytes() if config.exists() else None
    text = original.decode('utf-8-sig') if original is not None else ''
    command = str(executable)
    args = ['serve', '--home', str(home)]
    if os.name == 'nt' and executable.name.lower() in {'writune.exe', 'python.exe', 'pythonw.exe'}:
        pythonw = executable.with_name('pythonw.exe')
        if not pythonw.is_file():
            raise ValueError('Windows MCP requires pythonw.exe in the Writune environment')
        command = str(pythonw)
        args = ['-m', 'writune', *args]
    if client == 'codex':
        import tomlkit
        document = tomlkit.parse(text)
        servers = document.setdefault('mcp_servers', tomlkit.table())
        previous = servers.get('writune')
    else:
        document = json.loads(text) if text.strip() else {}
        if not isinstance(document, dict):
            raise ValueError('Client configuration must be a JSON object')
        servers = document.setdefault('mcpServers', {})
        previous = servers.get('writune')
    if previous is not None and (previous.get('command') != command or list(previous.get('args', [])) != args) and not replace:
        raise ValueError('A different writune entry exists; inspect it and use --replace to update only that entry')
    entry = previous if previous is not None else (tomlkit.table() if client == 'codex' else {})
    entry['command'] = command
    entry['args'] = args
    if client == 'codex':
        entry['enabled'] = True
        entry.setdefault('startup_timeout_sec', 20)
        entry.setdefault('tool_timeout_sec', 60)
    else:
        entry['type'] = 'stdio'
    servers['writune'] = entry
    if client == 'codex':
        output = tomlkit.dumps(document).encode('utf-8')
    else:
        output = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if original == output:
        return {'client': client, 'config_path': str(config), 'changed': False, 'backup_path': None}
    backup = None
    if original is not None:
        backup = Path(tempfile.mkdtemp(prefix='writune-mcp-backup-')) / config.name
        atomic_write(backup, original, overwrite=False)
    config.parent.mkdir(parents=True, exist_ok=True)
    if (config.read_bytes() if config.exists() else None) != original:
        raise RuntimeError('Client configuration changed during registration; rerun after inspecting the change')
    atomic_write(config, output, overwrite=True)
    return {'client': client, 'config_path': str(config), 'changed': True, 'backup_path': str(backup) if backup else None}


def install_skill(client: str, source: Path):
    base = Path.home() / ('.agents' if client == 'codex' else '.claude') / 'skills'
    target = base / 'writune-polish'
    for path in source.rglob('*'):
        if path.is_file():
            destination = target / path.relative_to(source)
            if destination.exists() and destination.read_bytes() != path.read_bytes():
                raise ValueError(f'Existing customized skill retained: {destination}')
    shutil.copytree(source, target, dirs_exist_ok=True)
    return str(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=['codex', 'claude-code'], required=True)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--install-skill', action='store_true')
    args = parser.parse_args()
    default = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'config.toml' if args.client == 'codex' else Path.home()/'.claude.json'
    result = register(args.client, args.config or default, args.executable, args.home, args.replace)
    if args.install_skill:
        result['skill_path'] = install_skill(args.client, Path(__file__).resolve().parents[1]/'.agents/skills/writune-polish')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
