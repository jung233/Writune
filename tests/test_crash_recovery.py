"""Actual worker-process crash recovery, including persisted request budgets."""
import os
import signal
import time
import pytest
from tests.mock_responses import MockResponsesServer,make_settings
from writune.service import Service
from writune.tasks.lifecycle import process_alive

def _kill_test_worker(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.OpenProcess.restype=wintypes.HANDLE
        api.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.OpenProcess(1,False,pid)
        assert handle,'Cannot open the worker created by this test'
        try:
            assert api.TerminateProcess(handle,17)
        finally:
            api.CloseHandle(handle)
    else:
        os.kill(pid,signal.SIGKILL)

def _until(predicate,timeout=12):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(.05)
    assert predicate()

def test_hard_crashed_worker_is_replaced_and_uses_only_remaining_attempt(tmp_path):
    with MockResponsesServer(hold_all=True) as server:
        settings=make_settings(tmp_path,server.url,lease_seconds=30,worker_idle_seconds=.1)
        service=Service(settings,start_worker=True)
        task=service.submit(text='The original paragraph remains safe after a process crash.')
        tid=task['task_id']
        assert server.wait_for_requests(1,timeout=10)
        with service.db.connect() as c:
            worker=c.execute("SELECT owner,pid FROM workers WHERE name='main'").fetchone()
        row=service.db.get(tid)
        assert row['owner']==worker['owner'] and row['attempt_count']==1
        pid=worker['pid']
        try:
            _kill_test_worker(pid)
            _until(lambda:not process_alive(pid),5)
            server.release()
            # The original lease is still valid, so this proves dead-PID recovery
            # rather than waiting for or manually expiring that lease.
            assert row['lease_until']>time.time()
            service.status(task_id=tid)
            _until(lambda:service.db.get(tid)['status']=='completed')
            result=service.result(tid)
            assert result['outcome']=='polished' and result['possible_duplicate_billing']
            assert service.db.get(tid)['attempt_count']==2
            assert [request['model'] for request in server.requests]==['primary','secondary']
            with service.db.connect() as c:
                attempts=c.execute('SELECT error_code FROM attempts WHERE task_id=? ORDER BY attempt',(tid,)).fetchall()
            assert attempts[0][0]=='WORKER_LOST' and attempts[1][0] is None
        finally:
            server.release()
            service.cancel(tid)
