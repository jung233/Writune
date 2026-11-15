import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from tests.mock_responses import MockResponsesServer, make_settings
from writune.service import Service
from writune.tasks.worker import Worker


def test_task_outputs_use_system_temp_even_with_persistent_data_dir(tmp_path):
    settings = make_settings(tmp_path, 'http://127.0.0.1:9/v1')
    service = Service(settings, start_worker=False)
    task = service.submit(text='A complete draft.')
    original = Path(service.db.get(task['task_id'])['original_path'])
    assert original.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not original.is_relative_to(settings.data_dir)
    assert Service(settings, start_worker=False).task_root == service.task_root
    other = settings.model_copy(update={'data_dir': tmp_path / 'other-data'})
    assert other.task_dir != settings.task_dir


def test_default_apply_and_records_leave_no_files_next_to_manuscript(tmp_path):
    with MockResponsesServer() as server:
        settings = make_settings(tmp_path, server.url, worker_idle_seconds=.05)
        service = Service(settings, start_worker=False)
        task = service.submit(text='The original sentence remains clear.')
        asyncio.run(Worker(settings).run())
        target = settings.allowed_workspaces[0] / 'report.md'
        target.write_text('# Report\n\n{{WRITUNE:BODY}}', encoding='utf-8')
        first = service.apply(task['task_id'], target, 'BODY')
        second = service.apply(task['task_id'], target, 'BODY')
        assert second['idempotent'] and first['output_sha256'] == second['output_sha256']
        output = Path(first['output_path'])
        assert output.parent == Path(service.db.get(task['task_id'])['original_path']).parent
        assert output.is_relative_to(Path(tempfile.gettempdir()).resolve())
        assert list(target.parent.iterdir()) == [target]
        final = target.with_name('final.md')
        service.apply(task['task_id'], target, 'BODY', output_path=final)
        assert set(target.parent.iterdir()) == {target, final}


def test_os_cleanup_is_reported_and_never_reissues_provider_requests(tmp_path):
    service = Service(make_settings(tmp_path, 'http://127.0.0.1:9/v1'), start_worker=False)
    task = service.submit(text='A complete original.')
    row = service.db.get(task['task_id'])
    service.db.update(task['task_id'], status='completed', outcome='polished', result_path=row['original_path'])
    shutil.rmtree(Path(row['original_path']).parent)
    result = service.result(task['task_id'])
    assert result['status'] == 'failed' and result['error']['code'] == 'TASK_FILES_EXPIRED'
    assert result['result_path'] is None
    assert service.status(task_id=task['task_id'])['progress'] == 1
    assert service.db.get(task['task_id'])['attempt_count'] == 0


def test_missing_queued_source_finishes_as_expired(tmp_path):
    settings = make_settings(tmp_path, 'http://127.0.0.1:9/v1', worker_idle_seconds=.05)
    service = Service(settings, start_worker=False)
    task = service.submit(text='A complete original.')
    Path(service.db.get(task['task_id'])['original_path']).unlink()
    asyncio.run(Worker(settings).run())
    assert service.result(task['task_id'])['error']['code'] == 'TASK_FILES_EXPIRED'


def test_legacy_task_paths_upgrade_without_deleting_historical_originals(tmp_path):
    settings = make_settings(tmp_path, 'http://127.0.0.1:9/v1')
    service = Service(settings, start_worker=False)
    task = service.submit(text='Preserve historical draft.')
    tid = task['task_id']
    original = Path(service.db.get(tid)['original_path'])
    legacy = settings.data_dir / 'tasks' / tid
    legacy.mkdir(parents=True)
    shutil.copyfile(original, legacy / 'original.txt')
    (legacy / 'result.txt').write_text('A historical result.', encoding='utf-8')
    (legacy / 'result.json').write_text(json.dumps({'result_path': str(legacy / 'result.txt')}), encoding='utf-8')
    service.db.update(tid, status='completed', outcome='polished', original_path=str(legacy / 'original.txt'), result_path=str(legacy / 'result.txt'), manifest_path=str(legacy / 'result.json'))
    upgraded = Service(settings, start_worker=False)
    result = upgraded.result(tid, inline_text=True)
    assert result['text'] == 'A historical result.'
    assert Path(result['result_path']).is_relative_to(settings.task_dir)
    assert (legacy / 'original.txt').read_text() == 'Preserve historical draft.'
    assert (legacy / 'result.txt').is_file()
