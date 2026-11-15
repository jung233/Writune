import time

from tests.mock_responses import make_settings
from writune.service import Service
from writune.tasks.database import Database
from writune.tasks.eta import estimate


def _history(service, model, count=1, *, success=1, tps=5):
    with service.db.connect(immediate=True) as connection:
        connection.executemany(
            "INSERT INTO attempts(provider,model,language,scene,finished_at,success,"
            "first_token_latency,generation_tps,total_elapsed) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ('mock', model, 'en', 'general_prose', time.time() + i, success, 2, tps, 3)
                for i in range(count)
            ],
        )


def test_submission_persists_estimator_confidence(tmp_path):
    service = Service(make_settings(tmp_path, 'http://127.0.0.1:9/v1', retry_count=0), start_worker=False)
    _history(service, 'good', count=10)

    submitted = service.submit(text='A measured paragraph.', primary_model='mock/good', secondary_model='')

    assert submitted['estimate_confidence'] == 'high'
    assert service.db.get(submitted['task_id'])['estimate_confidence'] == 'high'


def test_batch_eta_accounts_for_batch_concurrency_limit(tmp_path):
    settings = make_settings(tmp_path, 'http://127.0.0.1:9/v1', retry_count=0, max_concurrency=4)
    service = Service(settings, start_worker=False)
    _history(service, 'primary')

    batch = service.submit_batch(
        items=['First paragraph.', 'Second paragraph.', 'Third paragraph.'],
        max_concurrency=1,
    )
    estimates = [service.db.get(task_id)['estimated_seconds'] for task_id in batch['task_ids']]

    assert estimates[0] < estimates[1] < estimates[2]


def test_eta_routes_a_queued_retry_to_its_next_model(tmp_path):
    settings = make_settings(tmp_path, 'http://127.0.0.1:9/v1', retry_count=0, max_concurrency=4)
    settings.providers['mock'].model_concurrency = {'primary': 4, 'backup': 1}
    service = Service(settings, start_worker=False)
    _history(service, 'primary')
    _history(service, 'backup')
    retry = service.submit(
        text='A retry waiting for the backup model.',
        primary_model='mock/primary',
        secondary_model='mock/backup',
    )
    claimed = service.db.claim('first-owner', settings)
    assert claimed['task_id'] == retry['task_id']
    assert service.db.attempt_start(retry['task_id'], 'first-owner', 'mock', 'primary', 2) == 1
    with service.db.connect(immediate=True) as connection:
        connection.execute(
            "UPDATE attempts SET success=0,error_code='PROVIDER_ERROR',finished_at=? WHERE task_id=?",
            (time.time(), retry['task_id']),
        )
        connection.execute(
            "UPDATE tasks SET status='queued',owner=NULL,lease_until=NULL WHERE task_id=?",
            (retry['task_id'],),
        )

    primary_eta, _ = estimate(
        service.db, 'mock', 'primary', 'en', 'general_prose', 'A new paragraph.',
        settings.max_concurrency, settings=settings, retry_count=0,
    )
    backup_eta, _ = estimate(
        service.db, 'mock', 'backup', 'en', 'general_prose', 'A new paragraph.',
        settings.max_concurrency, settings=settings, retry_count=0,
    )

    assert backup_eta > primary_eta


def test_status_recomputes_queue_eta_and_keeps_running_eta_above_zero(tmp_path):
    service = Service(make_settings(tmp_path, 'http://127.0.0.1:9/v1', retry_count=0, max_concurrency=4), start_worker=False)
    _history(service, 'primary')
    first = service.submit(text='The first queued paragraph.', primary_model='mock/primary', secondary_model='')
    second = service.submit(text='The second queued paragraph.', primary_model='mock/primary', secondary_model='')
    initial_eta = second['estimated_seconds']

    service.db.update(first['task_id'], status='completed', outcome='polished', finished_at=time.time())
    refreshed = service.status(task_id=second['task_id'])['estimated_seconds_remaining']
    assert refreshed < initial_eta

    service.db.update(
        second['task_id'],
        status='running',
        started_at=time.time() - 100,
        provider='mock',
        actual_model='primary',
    )
    running_eta = service.status(task_id=second['task_id'])['estimated_seconds_remaining']
    assert running_eta == 1.0


def test_database_migrates_estimate_confidence_column(tmp_path):
    import sqlite3

    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.executescript(
            'CREATE TABLE tasks(task_id TEXT PRIMARY KEY, status TEXT, priority INTEGER, '
            'created_at REAL, position INTEGER, estimated_seconds REAL);'
            'CREATE TABLE batches(batch_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,fingerprint TEXT NOT NULL);'
        )

    database = Database(path)
    with database.connect() as connection:
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(tasks)')}
        assert 'estimate_confidence' in columns
