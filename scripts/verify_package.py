"""Install the built wheel into an isolated import target and verify its payload."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

root=Path(__file__).resolve().parents[1]
target=Path(tempfile.mkdtemp(prefix='writune-wheel-verify-'))
wheel=next((root/'dist').glob('*.whl'))
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
subprocess.run([sys.executable,'-m','pip','install','--no-deps','--upgrade','--target',str(target),str(wheel)],check=True,creationflags=flags)
env=os.environ.copy();env['PYTHONPATH']=str(target)
code='import writune; from pathlib import Path; from writune.mcp_server import create_server; assert Path(writune.__file__).resolve().is_relative_to(Path.cwd()); print(writune.__file__); print(create_server().name)'
subprocess.run([sys.executable,'-c',code],cwd=target,env=env,check=True,creationflags=flags)
subprocess.run([sys.executable,'-m','writune','--help'],cwd=target,env=env,check=True,creationflags=flags)
assert (target/'share/writune/skills/writune-polish/SKILL.md').is_file()
sources=[str(p.relative_to(root)) for p in (root/'src').rglob('*.py')]
ignored=subprocess.run(['git','check-ignore',*sources],cwd=root,capture_output=True,text=True,creationflags=flags)
assert ignored.returncode==1, 'Source files must not be ignored: '+ignored.stdout
with zipfile.ZipFile(wheel) as archive:
    assert not any('/.local/' in n or n.endswith('/.env') for n in archive.namelist())
with tarfile.open(next((root/'dist').glob('*.tar.gz'))) as archive:
    names=archive.getnames()
    assert any(n.endswith('/scripts/install.sh') for n in names)
    assert any(n.endswith('/.agents/skills/writune-polish/SKILL.md') for n in names)
    assert not any('/.local/' in n or n.endswith('/.env') for n in names)
print(json.dumps({'wheel':wheel.name,'isolated_import':True,'skill_bundled':True,'source_package':True}))
