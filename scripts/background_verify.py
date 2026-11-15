"""Run final checks without opening a console on Windows.

Launch with pythonw.exe on Windows. Every child uses CREATE_NO_WINDOW.
"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parents[1]
logs=root/'.local'
logs.mkdir(exist_ok=True)
python=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
def source_digest():
    digest=hashlib.sha256()
    for folder in ('src','tests','scripts'):
        for path in sorted((root/folder).rglob('*.py')):
            if '__pycache__' not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
    digest.update((root/'pyproject.toml').read_bytes())
    return digest.hexdigest()

started=time.time()
initial_digest=source_digest()
record=logs/'background-verification.json'
record.write_text(json.dumps({'status':'running','started_at':started,'source_digest':initial_digest}),encoding='utf-8')
results={}
checks=[('tests',['-m','pytest','-q','--tb=short']),('build',['-m','build'])]
home=root/'.local/live/config'
if home.is_dir():
    checks.append(('doctor',['-m','writune','doctor','--home',str(home)]))
for name,args in checks:
    with (logs/f'background-{name}.log').open('w',encoding='utf-8') as out:
        p=subprocess.run([str(python),*args],cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,creationflags=flags)
        results[name]=p.returncode
with (logs/'background-package.log').open('w',encoding='utf-8') as out:
    p=subprocess.run([str(python),str(root/'scripts/verify_package.py')],cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,creationflags=flags)
    results['package']=p.returncode
record.write_text(json.dumps({'status':'passed' if not any(results.values()) else 'failed','checks':results,'started_at':started,'finished_at':time.time(),'source_digest':source_digest(),'source_unchanged':initial_digest==source_digest()},indent=2),encoding='utf-8')
