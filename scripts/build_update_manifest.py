"""Regenerate the shipped HTTP update manifest before building or publishing."""
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path
import tomllib


def build(root: Path) -> dict:
    package = root / 'src' / 'writune'
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    metadata = project['project']
    resources = package / 'resources'
    skill = root / '.agents' / 'skills' / 'writune-polish'
    if skill.is_dir():
        shutil.copytree(skill, resources / 'skills' / 'writune-polish', dirs_exist_ok=True)
    documentation = resources / 'docs'
    documentation.mkdir(parents=True, exist_ok=True)
    for name in ('INSTALL_PROMPT.md', 'README.md', 'README.en.md',
                 'MANUAL_INSTALL.md', 'MANUAL_INSTALL.zh-CN.md'):
        source = root / name
        if source.is_file():
            shutil.copyfile(source, documentation / name)
    files = {}
    for path in sorted(package.rglob('*')):
        relative = path.relative_to(package)
        if ('__pycache__' in relative.parts or '.writune-runtime' in relative.parts
                or path.suffix in {'.pyc', '.pyo'} or relative.as_posix() == 'update-manifest.json'):
            continue
        if path.is_file():
            data = path.read_bytes()
            if path.suffix in {'.py', '.json', '.txt', '.md', '.yaml', '.yml', '.toml'}:
                data = data.replace(b'\r\n', b'\n')
            files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    manifest = {
        'schema': 1, 'version': metadata['version'], 'files': files,
        'compatibility': {key: metadata.get(key) for key in
                          ('dependencies', 'requires-python', 'scripts', 'entry-points')},
    }
    manifest['compatibility']['resources_layout'] = 2
    (package / 'update-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    result = build(Path(__file__).resolve().parents[1])
    print(f"Prepared HTTP update manifest for {len(result['files'])} package files")
