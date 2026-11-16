"""Install the built wheel into an isolated import target and verify its payload."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

def main():
    root=Path(__file__).resolve().parents[1]
    version=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    wheel=root/'dist'/f'writune-{version}-py3-none-any.whl'
    source=root/'dist'/f'writune-{version}.tar.gz'
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with tempfile.TemporaryDirectory(prefix='writune-wheel-verify-') as folder:
        target=Path(folder)
        subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(target),str(wheel)],check=True,creationflags=flags)
        env=dict(os.environ,PYTHONPATH=str(target),WRITUNE_AUTO_UPDATE='false')
        code=('import writune;from pathlib import Path;'
              'from writune.mcp_server import create_server;'
              'from writune.integration import skill_source;'
              'assert Path(writune.__file__).resolve().is_relative_to(Path.cwd());'
              'assert skill_source().is_relative_to(Path.cwd());'
              'assert (skill_source()/"SKILL.md").is_file();print(create_server().name)')
        subprocess.run([sys.executable,'-c',code],cwd=target,env=env,check=True,creationflags=flags)
        subprocess.run([sys.executable,'-m','writune','--version'],cwd=target,env=env,check=True,creationflags=flags)
        subprocess.run([sys.executable,'-m','writune','mcp','show','--client','codex','--home',str(target/'config')],cwd=target,env=env,check=True,creationflags=flags)
        assert (target/'writune/resources/skills/writune-polish/references/workflows.md').is_file()
    with zipfile.ZipFile(wheel) as archive:
        assert not any('/.local/' in n or n.endswith('/.env') for n in archive.namelist())
    with tarfile.open(source) as archive:
        names=archive.getnames()
        assert any(n.endswith('/src/writune/integration.py') for n in names)
        assert any(n.endswith('/.agents/skills/writune-polish/SKILL.md') for n in names)
        assert not any(n.endswith(('/scripts/install.sh','/scripts/install.ps1','/scripts/register_mcp.py')) for n in names)
        assert not any('/.local/' in n or n.endswith('/.env') for n in names)
    print(json.dumps({'wheel':wheel.name,'isolated_import':True,'skill_bundled':True,'source_package':True}))


if __name__=='__main__':
    main()
