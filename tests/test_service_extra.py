from pathlib import Path
from writune.service import Service
from tests.mock_responses import make_settings

def test_context_edits_are_versioned_and_task_snapshot_is_immutable(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    first=service.create_context(language='en',scene='sci_paper',section='Results')
    task=service.submit(text='An observed association.',context_id=first['context_id'])
    second=service.create_context(context_id=first['context_id'],section='Discussion')
    assert first!=second
    assert service.create_context(language='en',scene='sci_paper',section='Results')==first
    import json
    assert json.loads(service.db.get(task['task_id'])['payload'])['context']['section']=='Results'

def test_merge_preserves_submission_order_and_counts_fallbacks(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    batch=service.submit_batch(items=[{'text':'first'},{'text':'second'}])
    for tid,outcome in zip(batch['task_ids'],['original_fallback','polished']):
        row=service.db.get(tid)
        result=Path(row['original_path']).with_name('result.txt')
        result.write_bytes(Path(row['original_path']).read_bytes())
        service.db.update(tid,status='completed',outcome=outcome,result_path=str(result))
    output=service.settings.allowed_workspaces[0]/'merged.md'
    r=service.merge_batch(batch['batch_id'],output)
    assert output.read_text()=='first\n\nsecond'
    assert r['fallback_count']==1

def test_cold_eta_unknown_and_history_estimate_is_measured(tmp_path):
    from writune.tasks.eta import estimate
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    args=(service.db,'mock','good','en','general_prose','A paragraph.')
    assert estimate(*args)==(None,'low')
    with service.db.connect(immediate=True) as c:
        c.execute("INSERT INTO attempts(provider,model,language,scene,finished_at,success,first_token_latency,generation_tps) VALUES('mock','good','en','general_prose',1,1,2,5)")
    eta,confidence=estimate(*args)
    assert eta>=2 and confidence=='medium'

def test_batch_idempotency_rejects_changed_shape(tmp_path):
    import pytest
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    first=service.submit_batch(items=[{'text':'first'}],idempotency_key='batch-key')
    assert service.submit_batch(items=[{'text':'first'}],idempotency_key='batch-key')['batch_id']==first['batch_id']
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'):
        service.submit_batch(items=[{'text':'first'},{'text':'second'}],idempotency_key='batch-key')
    with service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==1

def test_committed_submit_is_accepted_when_worker_spawn_fails(tmp_path,monkeypatch):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=True)
    def fail():
        raise OSError('spawn failure')
    monkeypatch.setattr(service,'_wake',fail)
    task=service.submit(text='A paragraph.')
    assert task['status']=='queued' and task['worker_start_pending']
    assert Path(service.db.get(task['task_id'])['original_path']).exists()

def test_stale_owner_cannot_rewrite_recovery_attempt(tmp_path):
    from writune.tasks.worker import Worker
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    task=service.submit(text='A paragraph.')
    row=service.db.claim('old',service.settings)
    assert service.db.attempt_start(row['task_id'],'old','mock','primary',2)==1
    service.db.update(row['task_id'],lease_until=0)
    assert service.db.claim('new',service.settings)
    Worker(service.settings,'old').record_attempt(row['task_id'],1,success=True)
    with service.db.connect() as c:
        record=c.execute('SELECT * FROM attempts').fetchone()
    assert record['error_code']=='WORKER_LOST' and record['success']==0
    assert service.db.get(task['task_id'])['possible_duplicate_billing']==1

def test_snapshot_and_fallback_keep_crlf_exact(tmp_path):
    from writune.tasks.worker import Worker
    import asyncio
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1',retry_count=0)
    service=Service(settings,start_worker=False)
    source=settings.allowed_workspaces[0]/'draft.txt'
    original=b'This is an original line.\r\nA second original line.\r\n'
    source.write_bytes(original)
    task=service.submit(source_path=source)
    row=service.db.get(task['task_id'])
    assert Path(row['original_path']).read_bytes()==original
    claimed=service.db.claim('fallback',settings)
    asyncio.run(Worker(settings,'fallback').execute(claimed))
    result=service.result(task['task_id'])
    assert result['outcome']=='original_fallback'
    assert Path(result['result_path']).read_bytes()==original

def test_cleanup_all_preserves_active_tasks_and_their_contexts(tmp_path):
    import time
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    context=service.create_context(language='en',section='Results')['context_id']
    unused=service.create_context(language='zh')['context_id']
    active=service.submit(text='Active text.',context_id=context)
    terminal=service.submit(text='A terminal task.')
    service.db.update(terminal['task_id'],status='cancelled',finished_at=time.time())
    removed=service.cleanup(all_history=True)
    assert removed=={'removed_tasks':1,'removed_contexts':1}
    assert service.db.get(active['task_id'])['status']=='queued'
    with service.db.connect() as c:
        assert [r[0] for r in c.execute('SELECT context_id FROM contexts')]==[context]

def test_profile_precedence_and_invalid_model_are_checked_before_queuing(tmp_path):
    import pytest
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.profiles={'paper':{'primary_model':'mock/profile','scene':'sci_paper','style':'academic'}}
    service=Service(settings,start_worker=False)
    task=service.submit(text='A paragraph.',profile='paper',primary_model='mock/explicit')
    row=service.db.get(task['task_id'])
    assert row['primary_model']=='mock/explicit' and row['scene']=='sci_paper'
    with pytest.raises(ValueError,match='provider/model'):
        service.submit(text='An invalid model.',primary_model='mock/')
    with pytest.raises(TypeError):
        service.submit(text='An invalid context.',local_context=['invalid'])

def test_recovery_cannot_increase_the_submitted_retry_budget(tmp_path):
    from writune.tasks.worker import Worker
    import asyncio
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1',retry_count=0)
    service=Service(settings,start_worker=False)
    task=service.submit(text='A complete original paragraph.')
    changed=settings.model_copy(deep=True)
    changed.retry_count=5
    row=service.db.claim('owner',changed)
    asyncio.run(Worker(changed,'owner').execute(row))
    assert service.db.get(task['task_id'])['attempt_count']==1
    assert service.result(task['task_id'])['outcome']=='original_fallback'

def test_source_snapshot_keeps_original_bom_bytes_separately(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    source=service.settings.allowed_workspaces[0]/'with-bom.txt'
    content=b'\xef\xbb\xbfA UTF-8 document.\r\n'
    source.write_bytes(content)
    submitted=service.submit(source_path=source)
    original=Path(service.db.get(submitted['task_id'])['original_path'])
    assert original.with_name('original.source').read_bytes()==content
    assert original.read_bytes()==content[3:]
