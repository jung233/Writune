"""Regression cases discovered by auditing the original development document."""
import asyncio
import json
import os
import stat
from pathlib import Path
import pytest
from tests.mock_responses import make_settings,MockResponsesServer
from writune.config import ProviderConfig
from writune.service import Service
from writune.tasks.worker import Worker

def test_batch_concurrency_persists_and_claim_skips_a_saturated_batch(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    service=Service(settings,start_worker=False)
    limited=service.submit_batch(items=['First paragraph.','Second paragraph.'],max_concurrency=1,idempotency_key='batch')
    independent=service.submit(text='An independent paragraph.')
    assert service.db.claim('worker',settings)['task_id']==limited['task_ids'][0]
    assert service.db.claim('worker',settings)['task_id']==independent['task_id']
    assert service.db.claim('worker',settings) is None
    replay=service.submit_batch(items=['First paragraph.','Second paragraph.'],max_concurrency=1,idempotency_key='batch')
    assert replay['batch_id']==limited['batch_id']
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'):
        service.submit_batch(items=['First paragraph.','Second paragraph.'],max_concurrency=2,idempotency_key='batch')

def test_saturated_provider_does_not_block_other_providers(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.providers['mock'].max_concurrency=1
    settings.providers['other']=ProviderConfig(base_url='http://127.0.0.1:9/v1',api_key='test',max_concurrency=2)
    service=Service(settings,start_worker=False)
    slow=service.submit_batch(items=['Slow one.','Slow two.','Slow three.'])
    other=service.submit(text='Other provider.',primary_model='other/fast')
    assert service.db.claim('worker',settings)['task_id']==slow['task_ids'][0]
    assert service.db.claim('worker',settings)['task_id']==other['task_id']
    assert service.db.claim('worker',settings) is None

def test_model_limit_does_not_block_another_model(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.providers['mock'].model_concurrency={'primary':1}
    service=Service(settings,start_worker=False)
    first=service.submit(text='Primary first.')
    service.submit(text='Primary waiting.')
    different=service.submit(text='Other model.',primary_model='mock/fast')
    assert service.db.claim('worker',settings)['task_id']==first['task_id']
    assert service.db.claim('worker',settings)['task_id']==different['task_id']

def test_old_database_batch_schema_is_upgraded(tmp_path):
    import sqlite3
    from writune.tasks.database import Database
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE batches(batch_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,fingerprint TEXT NOT NULL)')
        c.execute("INSERT INTO batches VALUES('old','key','fingerprint')")
    database=Database(path)
    with database.connect() as c:
        row=c.execute('SELECT * FROM batches').fetchone()
        assert row['batch_id']=='old' and row['max_concurrency'] is None

@pytest.mark.skipif(os.name=='nt',reason='POSIX permission preservation')
def test_atomic_overwrite_preserves_document_mode(tmp_path):
    from writune.files.atomic import atomic_write
    target=tmp_path/'report.txt';target.write_bytes(b'old');target.chmod(0o640)
    atomic_write(target,b'new')
    assert stat.S_IMODE(target.stat().st_mode)==0o640

@pytest.mark.parametrize('encoding,bom',[('utf-8',b'\xef\xbb\xbf'),('utf-16-le',b'\xff\xfe')])
def test_markdown_merge_preserves_source_bom_separators_and_crlf(tmp_path,encoding,bom):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    source=service.settings.allowed_workspaces[0]/'draft.md'
    original='\r\nFirst paragraph.\r\n\r\nSecond paragraph.\r\n'
    source.write_bytes(bom+original.encode(encoding))
    batch=service.submit_batch(markdown_path=source)
    for tid in batch['task_ids']:
        row=service.db.get(tid);snapshot=Path(row['original_path'])
        result=snapshot.with_name('result.txt')
        result.write_text(snapshot.read_text().strip(),encoding='utf-8')
        service.db.update(tid,status='completed',outcome='polished',result_path=str(result))
    target=source.with_name('merged.md')
    service.merge_batch(batch['batch_id'],target)
    assert target.read_bytes()==source.read_bytes()

def test_stats_uses_recent_window_and_only_explicit_reported_price(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    with service.db.connect(immediate=True) as c:
        for success in [0]*60+[1]*50:
            c.execute("INSERT INTO attempts(provider,model,language,scene,finished_at,success,input_tokens,output_tokens,cached_input_tokens) VALUES('mock','primary','en','general_prose',1,?,100,20,40)",(success,))
    row=service.stats()['models'][0]
    assert row['requests']==50 and row['success_rate']==1
    assert row['cost'] is None
    service.settings.providers['mock'].prices={'primary':{'input_per_million':2,'output_per_million':4,'cached_input_per_million':1,'currency':'USD'}}
    priced=service.stats()['models'][0]
    assert priced['cost']['amount']==pytest.approx(.012)

def test_failed_worker_snapshot_removes_reservation_and_credentials(tmp_path,monkeypatch):
    from writune.tasks.lifecycle import ensure_worker
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    service=Service(settings,start_worker=False)
    def fail(*args,**kwargs):
        raise OSError('unwritable launch snapshot')
    monkeypatch.setattr(type(settings),'save',fail)
    with pytest.raises(OSError):
        ensure_worker(settings,service.db)
    with service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM workers').fetchone()[0]==0
    assert not list((settings.data_dir/'launches').iterdir())

@pytest.mark.asyncio
async def test_mcp_batch_inherits_defaults_and_context_update_keeps_language(tmp_path):
    from writune.mcp_server import create_server
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    server=create_server(service)
    first=service.create_context(language='zh',scene='chinese_journal',section='Results')
    await server.call_tool('writune_create_context',{'context_id':first['context_id'],'section':'Discussion'})
    with service.db.connect() as c:
        contents=[json.loads(r[0]) for r in c.execute('SELECT content FROM contexts')]
    assert any(row.get('section')=='Discussion' and row['language']=='zh' and row['scene']=='chinese_journal' for row in contents)
    await server.call_tool('writune_submit_batch',{'items':[{'text':'A paragraph.'}],'options':{'temperature':.2},'local_context':'Shared local context.','max_concurrency':1})
    with service.db.connect() as c:
        row=c.execute('SELECT payload FROM tasks').fetchone()
    data=json.loads(row[0])
    assert data['options']=={'temperature':.2} and data['local_context']=='Shared local context.'

def test_configured_model_stats_schema_matches_service(tmp_path):
    from writune.schemas import StatsResponse
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    with service.db.connect(immediate=True) as c:
        c.execute("INSERT INTO attempts(provider,model,language,scene,finished_at,success) VALUES('mock','primary','en','general_prose',1,1)")
    assert StatsResponse.model_validate(service.stats()).models[0].sample_window==50

def test_task_profile_resolves_priority_local_context_and_options(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.profiles={'paper':{'priority':7,'options':{'temperature':.1},'local_context':'A stable local hint.'}}
    service=Service(settings,start_worker=False)
    task=service.submit(text='A paragraph.',profile='paper',options={'max_output_tokens':300})
    payload=json.loads(service.db.get(task['task_id'])['payload'])
    assert payload['priority']==7 and payload['local_context']=='A stable local hint.'
    assert payload['options']=={'temperature':.1,'max_output_tokens':300}
    overridden=service.submit(text='A paragraph.',profile='paper',priority=0,local_context='',options={'temperature':.2})
    payload=json.loads(service.db.get(overridden['task_id'])['payload'])
    assert payload['priority']==0 and payload['local_context']=='' and payload['options']['temperature']==.2

def test_worker_launch_snapshot_is_not_overridden_by_ambient_environment(tmp_path,monkeypatch):
    from writune.tasks.lifecycle import ensure_worker
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1',max_concurrency=4)
    service=Service(settings,start_worker=False)
    captured={}
    class FakeProcess:
        pid=12345
    def launch(*args,**kwargs):
        captured.update(kwargs)
        return FakeProcess()
    monkeypatch.setenv('WRITUNE_MAX_CONCURRENCY','1')
    monkeypatch.setenv('WRITUNE_API_KEY','ambient-secret')
    monkeypatch.setattr('writune.tasks.lifecycle.subprocess.Popen',launch)
    ensure_worker(settings,service.db)
    assert captured['env']['WRITUNE_HOME']
    assert 'WRITUNE_MAX_CONCURRENCY' not in captured['env']
    assert 'WRITUNE_API_KEY' not in captured['env']

@pytest.mark.parametrize('prefix',['以下是润色后的文本：','润色后的版本：','下面是修改后的正文：'])
def test_chinese_explanatory_preamble_is_not_delivered_as_prose(prefix):
    from writune.polishing.validate import validate
    from writune.polishing.errors import ValidationError
    with pytest.raises(ValidationError) as error:
        validate('本研究发现指标之间存在关联。',prefix+'本研究发现指标之间存在关联。')
    assert error.value.code=='UNWANTED_PREAMBLE'

def test_context_terminology_masks_case_sensitive_gene_symbols_and_protein_names():
    from writune.polishing.protect import protect
    source='Ghrh and Pomc expression and growth hormone levels were associated.'
    result=protect(source,terms=['Ghrh','Pomc','growth hormone'])
    assert 'Ghrh' not in result.text and 'Pomc' not in result.text and 'growth hormone' not in result.text
    assert result.restore(result.text)==source
    assert 'were associated' in result.text

def test_tokenizer_count_tokens_method_uses_its_numeric_result():
    from writune.providers.tps import TokenCounter
    class ExactTokenizer:
        def count_tokens(self,text):
            return 17
    counter=TokenCounter(ExactTokenizer())
    assert counter.count('Some text.')==17 and counter.estimated is False

def test_model_timeout_map_overrides_provider_scalar_and_nested_styles_persist(tmp_path):
    from writune.config import Settings
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    provider=settings.providers['mock']
    provider.first_token_timeout=30
    provider.models={'fast':{'timeouts':{'first_token_timeout':2}}}
    assert provider.resolve_model('fast').first_token_timeout==2
    settings.custom_templates={'styles':{'house':'Use precise concise language.'}}
    settings.save()
    assert Settings.load(settings.home_dir).custom_templates==settings.custom_templates

def test_language_defaults_global_model_and_explicit_overrides(tmp_path):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    settings.en_primary=''
    settings.default_primary_model='mock/global'
    settings.language_defaults={'en':{'style':'formal','options':{'temperature':.3}}}
    service=Service(settings,start_worker=False)
    task=service.submit(text='An English paragraph.',language='en')
    row=service.db.get(task['task_id']);payload=json.loads(row['payload'])
    assert row['primary_model']=='mock/global' and payload['style']=='formal'
    assert payload['options']=={'temperature':.3}

def test_init_rejects_dangling_fallback_reference(tmp_path):
    from typer.testing import CliRunner
    from writune.cli import app
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    payload=settings.model_dump(mode='json')
    payload['providers']['mock']['api_key']='test-only-key'
    payload['en_secondary']='missing/model'
    config=tmp_path/'init.json';config.write_text(json.dumps(payload),encoding='utf-8')
    response=CliRunner().invoke(app,['init','--non-interactive','--config-file',str(config),'--home',str(tmp_path/'separate-home')])
    assert response.exit_code!=0 and 'fallback' in response.output

@pytest.mark.parametrize('encoding',['utf-16','gbk'])
def test_source_encoding_and_original_byte_snapshot(tmp_path,encoding):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    source=service.settings.allowed_workspaces[0]/'draft.txt'
    text='研究结果显示两项指标存在关联。\r\n'
    source.write_bytes(text.encode(encoding))
    submitted=service.submit(source_path=source,source_encoding=encoding if encoding=='gbk' else None)
    original=Path(service.db.get(submitted['task_id'])['original_path'])
    assert original.read_bytes()==text.encode('utf-8')
    assert original.with_name('original.source').read_bytes()==source.read_bytes()

def test_gbk_target_apply_retains_surrounding_bytes(tmp_path):
    from writune.files import apply_result
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    target=service.settings.allowed_workspaces[0]/'report.txt'
    target.write_bytes('前文\r\n{{WRITUNE:BODY}}\r\n后文'.encode('gbk'))
    result=service.task_root/'sample.txt';result.write_text('润色正文',encoding='utf-8')
    applied=apply_result(result,target,'BODY',workspace=service.workspace,target_encoding='gbk')
    assert Path(applied['output_path']).read_bytes()=='前文\r\n润色正文\r\n后文'.encode('gbk')

def test_future_tasks_adapter_is_explicit_and_keeps_the_shared_service(tmp_path):
    from writune.mcp_server import create_server
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    received=[]
    class FutureAdapter:
        def install(self,server,factory):
            received.append((server.name,factory()))
    assert create_server(service,tasks_adapter=FutureAdapter()).name=='Writune'
    assert received==[('Writune',service)]

def test_two_first_token_timeouts_deliver_original_then_complete_report(tmp_path):
    with MockResponsesServer(model_delays={'slow-primary':.45,'slow-secondary':.45}) as server:
        settings=make_settings(tmp_path,server.url,first_token_timeout=.12,total_timeout=2,worker_idle_seconds=.05)
        service=Service(settings,start_worker=False)
        original='This complete original paragraph is preserved.'
        task=service.submit(text=original,primary_model='mock/slow-primary',secondary_model='mock/slow-secondary')
        asyncio.run(asyncio.wait_for(Worker(settings,'timeout-fallback').run(),8))
        result=service.result(task['task_id'])
        assert result['outcome']=='original_fallback' and result['error']['code']=='FIRST_TOKEN_TIMEOUT'
        assert result['error']['attempts']==2
        target=settings.allowed_workspaces[0]/'report.md'
        target.write_text('# Report\n\n{{WRITUNE:BODY}}\n',encoding='utf-8')
        applied=service.apply(task['task_id'],target,'BODY')
        assert Path(applied['output_path']).read_text(encoding='utf-8')=='# Report\n\n'+original+'\n'

def test_cli_submit_batch_and_merge_use_shared_service(tmp_path,monkeypatch):
    from typer.testing import CliRunner
    from writune.cli import app
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    monkeypatch.setattr('writune.cli._service',lambda *args,**kwargs:service)
    source=service.settings.allowed_workspaces[0]/'source.md';source.write_text('First.\n\nSecond.',encoding='utf-8')
    result=CliRunner().invoke(app,['submit-batch','--markdown-path',str(source),'--max-concurrency','1'])
    assert result.exit_code==0,result.output
    batch=json.loads(result.output)
    for tid in batch['task_ids']:
        row=service.db.get(tid)
        service.db.update(tid,status='completed',outcome='original_fallback',result_path=row['original_path'])
    output=source.with_name('merged.md')
    result=CliRunner().invoke(app,['merge-batch',batch['batch_id'],str(output)])
    assert result.exit_code==0,result.output
    assert output.read_bytes()==source.read_bytes()

def test_health_availability_probe_is_explicit_and_reports_actual_provider_result(tmp_path):
    with MockResponsesServer() as server:
        service=Service(make_settings(tmp_path,server.url),start_worker=False)
        assert service.health()['providers']['mock']['availability']=='not_probed'
        assert not server.requests
        assert service.health(probe=True)['providers']['mock']['availability']=='available'
        assert len(server.requests)==1

def test_model_overrides_cannot_smuggle_credentials_into_config_json():
    from writune.config import ModelConfig
    with pytest.raises(ValueError):
        ModelConfig(api_key='not-a-model-setting')
