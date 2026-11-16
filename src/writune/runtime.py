"""Windowless Windows subprocess entry points, shared by workers and MCP checks."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def windowless_python(executable=None):
    python = Path(executable or sys.executable)
    if os.name == 'nt':
        candidate = python.with_name('pythonw.exe')
        if candidate.is_file():
            return str(candidate)
    return str(python)


def background_options():
    if os.name != 'nt':
        return {'start_new_session': True}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {
        'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        'startupinfo': startup,
    }


def worker_command(reservation, settings_path):
    """Fence worker imports before Python imports any updateable package code."""
    from writune.updates import source_root, runtime_dir
    root = source_root()
    args = ['--reservation', reservation, '--settings', str(settings_path)]
    if root is None:
        return [windowless_python(), '-m', 'writune.tasks.worker', *args]
    # This bootstrap uses only stdlib and survives an in-place package change.
    # Worker.run registers its lifetime lock before releasing this import fence.
    code = '''import os,sys,time
from pathlib import Path
path=Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
stream=path.open('a+b')
deadline=time.monotonic()+120
while True:
    try:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0:
                stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        break
    except OSError:
        if time.monotonic()>=deadline:
            raise TimeoutError('Writune startup update fence timed out')
        time.sleep(.05)
sys._writune_startup_lock=stream
sys.argv=['writune.tasks.worker',*sys.argv[2:]]
from writune.tasks.worker import main
main()
'''
    return [windowless_python(), '-c', code, str(runtime_dir(root) / 'calls.lock'), *args]
