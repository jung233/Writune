import asyncio
import os
import subprocess
import sys
import time
from writune.service import Service
from writune.tasks.process_lock import ProcessLock
from writune.tasks.worker import Worker
from writune.tasks.lifecycle import ensure_worker
from tests.mock_responses import MockResponsesServer,make_settings

def _run_python(code,path):
    return subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

def test_process_lock_is_exclusive_across_processes_and_crash_releases(tmp_path):
    path=tmp_path/'worker.lock'
    lock=ProcessLock(path)
    code='from writune.tasks.process_lock import ProcessLock; import sys; lock=ProcessLock(sys.argv[1]); print(lock.acquire())'
    assert lock.acquire()
    assert _run_python(code,path).stdout.strip()=='False'
    lock.release()
    assert _run_python(code,path).stdout.strip()=='True'
    crashed=_run_python('from writune.tasks.process_lock import ProcessLock; import sys,os; lock=ProcessLock(sys.argv[1]); assert lock.acquire(); os._exit(0)',path)
    assert crashed.returncode==0
    assert lock.acquire()
    lock.release()

def test_expired_heartbeat_does_not_spawn_competing_semaphores(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    service=Service(settings,start_worker=False)
    lock=ProcessLock(settings.data_dir/'worker.lock')
    assert lock.acquire()
    try:
        with service.db.connect(immediate=True) as c:
            c.execute("INSERT INTO workers VALUES('main','paused-worker',0,1234)")
        assert ensure_worker(settings,service.db) is False
        asyncio.run(Worker(settings,'competitor').run())
        with service.db.connect() as c:
            assert c.execute("SELECT owner FROM workers WHERE name='main'").fetchone()[0]=='paused-worker'
    finally:
        lock.release()

def test_priority_positions_match_claim_order(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    low=service.submit(text='Low priority.',priority=-1)
    high=service.submit(text='High priority.',priority=5)
    normal=service.submit(text='Normal priority.')
    assert service.status(task_id=high['task_id'])['queue_position']==1
    assert [service.db.claim('owner',service.settings)['task_id'] for _ in range(3)]==[high['task_id'],normal['task_id'],low['task_id']]

def test_eta_accounts_for_running_and_same_batch_provider_model_pressure(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.providers['mock'].max_concurrency=2
    settings.providers['mock'].model_concurrency={'primary':1}
    service=Service(settings,start_worker=False)
    with service.db.connect(immediate=True) as c:
        c.execute("INSERT INTO attempts(provider,model,language,scene,finished_at,success,first_token_latency,generation_tps) VALUES('mock','primary','en','general_prose',1,1,2,5)")
    first=service.submit(text='Same text.')
    service.db.claim('busy',settings)
    batch=service.submit_batch(items=[{'text':'Same text.'},{'text':'Same text.'}])
    eta=[service.db.get(t)['estimated_seconds'] for t in batch['task_ids']]
    assert first['estimated_seconds']<eta[0]<eta[1]
    assert batch['estimated_seconds']==eta[1]

def test_graceful_stop_cancels_active_request_then_resumes_with_bounded_budget(tmp_path):
    with MockResponsesServer(hold_all=True) as server:
        settings=make_settings(tmp_path,server.url,worker_idle_seconds=.05)
        service=Service(settings,start_worker=False)
        task=service.submit(text='A complete paragraph.')
        first=Worker(settings,'first')
        async def stop_while_running():
            running=asyncio.create_task(first.run())
            assert await asyncio.to_thread(server.wait_for_requests,1,5)
            first.stop()
            await asyncio.wait_for(running,5)
        asyncio.run(stop_while_running())
        row=service.db.get(task['task_id'])
        assert row['status']=='queued' and row['attempt_count']==1
        assert row['possible_duplicate_billing']==1
        server.release()
        asyncio.run(asyncio.wait_for(Worker(settings,'second').run(),8))
        result=service.result(task['task_id'])
        assert result['status']=='completed' and result['outcome']=='polished'
        assert service.db.get(task['task_id'])['attempt_count']==2
        assert [r['model'] for r in server.requests]==['primary','secondary']
