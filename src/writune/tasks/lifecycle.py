"""SQLite startup reservation and detached cross-platform worker process."""
import os
import subprocess
import sys
import time
import uuid
from .process_lock import ProcessLock
from writune.runtime import windowless_python, background_options

def process_alive(pid):
    if not pid:
        return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.OpenProcess.restype=wintypes.HANDLE
        api.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle:
            return ctypes.get_last_error()==5
        try:
            code=wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def ensure_worker(settings, db):
    token=uuid.uuid4().hex
    with db.connect(immediate=True) as c:
        r=c.execute("SELECT * FROM workers WHERE name='main'").fetchone()
        probe=ProcessLock(settings.data_dir/'worker.lock')
        if not probe.acquire():
            # A live but delayed process retains sockets and concurrency slots.
            return False
        probe.release()
        if r and r['lease_until']>time.time() and (r['pid'] is None or process_alive(r['pid'])):
            return False
        if r:
            c.execute("UPDATE tasks SET lease_until=0 WHERE owner=? AND status='running'",(r['owner'],))
        c.execute("INSERT OR REPLACE INTO workers VALUES('main',?,?,NULL)",(token,time.time()+settings.lease_seconds))
    # The serialized launch snapshot is authoritative. Ambient WRITUNE_* values
    # must not silently override explicit Settings values in the child process.
    env={key:value for key,value in os.environ.items() if not key.upper().startswith('WRITUNE_')}
    # Private launch snapshots use the same config/.env separation as user config.
    # Each reservation has its own files, so concurrent launches cannot overwrite keys.
    runtime=settings.data_dir/'launches'/token
    try:
        runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
        snapshot=settings.model_copy(deep=True)
        snapshot.save(runtime)
        env['WRITUNE_HOME']=str(runtime)
        p=subprocess.Popen([windowless_python(),'-m','writune.tasks.worker','--reservation',token,'--settings',str(runtime)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,close_fds=True,**background_options())
        with db.connect(immediate=True) as c:
            c.execute("UPDATE workers SET pid=? WHERE name='main' AND owner=?",(p.pid,token))
        return True
    except BaseException:
        import shutil
        if runtime.parent.resolve()==(settings.data_dir/'launches').resolve() and runtime.name==token and runtime.exists() and not runtime.is_symlink():
            shutil.rmtree(runtime)
        with db.connect(immediate=True) as c:
            c.execute("DELETE FROM workers WHERE name='main' AND owner=?",(token,))
        raise
