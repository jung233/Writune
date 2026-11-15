"""Short-lived SQLite connections and transactionally coordinated queue."""
from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY, batch_id TEXT, position INTEGER DEFAULT 0,
 created_at REAL NOT NULL, started_at REAL, finished_at REAL,
 status TEXT NOT NULL, outcome TEXT DEFAULT 'none', priority INTEGER DEFAULT 0,
 idempotency_key TEXT UNIQUE, fingerprint TEXT, payload TEXT NOT NULL,
 original_path TEXT NOT NULL, result_path TEXT, manifest_path TEXT,
 language TEXT, scene TEXT, context_id TEXT, source_path TEXT,
 primary_model TEXT, secondary_model TEXT, actual_model TEXT, provider TEXT,
 attempt_count INTEGER DEFAULT 0, retry_reason TEXT, error_code TEXT,error_message TEXT,
 input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0,cached_input_tokens INTEGER DEFAULT 0,
 cache_write_tokens INTEGER DEFAULT 0, first_token_latency REAL,generation_tps REAL,total_elapsed REAL,
 estimated_seconds REAL, estimate_confidence TEXT NOT NULL DEFAULT 'low',
 owner TEXT, lease_until REAL, cancel_requested INTEGER DEFAULT 0,
 recovery_count INTEGER DEFAULT 0, possible_duplicate_billing INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS queue_idx ON tasks(status,priority DESC,created_at,position);
CREATE TABLE IF NOT EXISTS contexts(context_id TEXT PRIMARY KEY,content TEXT NOT NULL,created_at REAL);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,task_id TEXT,attempt INTEGER,provider TEXT,model TEXT,
 language TEXT,scene TEXT,started_at REAL,finished_at REAL,success INTEGER,error_code TEXT,
 first_token_latency REAL,output_tokens INTEGER,generation_tps REAL,total_elapsed REAL,
 input_tokens INTEGER,cached_input_tokens INTEGER,cache_write_tokens INTEGER,tps_estimated INTEGER);
CREATE TABLE IF NOT EXISTS workers(name TEXT PRIMARY KEY,owner TEXT,lease_until REAL,pid INTEGER);
CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,fingerprint TEXT NOT NULL);
'''

class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(SCHEMA)
            task_columns={r[1] for r in c.execute('PRAGMA table_info(tasks)')}
            if 'estimate_confidence' not in task_columns:
                c.execute("ALTER TABLE tasks ADD COLUMN estimate_confidence TEXT NOT NULL DEFAULT 'low'")
            columns={r[1] for r in c.execute('PRAGMA table_info(batches)')}
            if 'max_concurrency' not in columns:
                c.execute('ALTER TABLE batches ADD COLUMN max_concurrency INTEGER')
            if 'delivery' not in columns:
                c.execute('ALTER TABLE batches ADD COLUMN delivery TEXT')

    @contextmanager
    def connect(self, immediate=False):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=30000')
        try:
            if immediate:
                c.execute('BEGIN IMMEDIATE')
            yield c
            if immediate:
                c.commit()
        except BaseException:
            if c.in_transaction:
                c.rollback()
            raise
        finally:
            c.close()

    def get(self, task_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
        if row is None:
            raise ValueError('TASK_NOT_FOUND')
        return dict(row)

    def update(self, task_id, **values):
        # Column names originate only in internal code.
        with self.connect(immediate=True) as c:
            c.execute('UPDATE tasks SET '+','.join(k+'=?' for k in values)+' WHERE task_id=?',(*values.values(),task_id))

    def recover(self, c, retry_count):
        now=time.time()
        rows=c.execute("SELECT * FROM tasks WHERE status='running' AND lease_until<?",(now,)).fetchall()
        for r in rows:
            # Every dispatched attempt is counted before contacting the provider.
            # Exhausted jobs are reclaimed only to publish their original fallback.
            dispatched=c.execute('SELECT 1 FROM attempts WHERE task_id=? AND finished_at IS NULL',(r['task_id'],)).fetchone() is not None
            c.execute("UPDATE tasks SET status='queued',owner=NULL,lease_until=NULL,recovery_count=recovery_count+1,possible_duplicate_billing=MAX(possible_duplicate_billing,?),error_code='WORKER_LOST',error_message='Worker lease expired; upstream completion unknown' WHERE task_id=?",(int(dispatched),r['task_id']))
            c.execute("UPDATE attempts SET success=0,error_code='WORKER_LOST',finished_at=?,total_elapsed=?-started_at WHERE task_id=? AND finished_at IS NULL",(now,now,r['task_id']))

    def claim(self, owner, settings):
        with self.connect(immediate=True) as c:
            self.recover(c,settings.retry_count)
            active=c.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
            if active>=settings.max_concurrency:
                return None
            candidates=c.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY priority DESC,created_at,position,rowid").fetchall()
            r=None
            for candidate in candidates:
                if candidate['batch_id']:
                    batch=c.execute('SELECT max_concurrency FROM batches WHERE batch_id=?',(candidate['batch_id'],)).fetchone()
                    used=c.execute("SELECT COUNT(*) FROM tasks WHERE batch_id=? AND status='running'",(candidate['batch_id'],)).fetchone()[0]
                    if batch and batch[0] and used>=batch[0]:
                        continue
                reference=candidate['primary_model'] if not candidate['attempt_count'] else candidate['secondary_model'] or candidate['primary_model']
                provider,model=reference.split('/',1)
                config=settings.providers[provider]
                # A queued request must not consume all global slots just waiting
                # on a single saturated provider/model semaphore.
                provider_used=c.execute("SELECT COUNT(*) FROM tasks WHERE status='running' AND provider=?",(provider,)).fetchone()[0]
                model_used=c.execute("SELECT COUNT(*) FROM tasks WHERE status='running' AND provider=? AND actual_model=?",(provider,model)).fetchone()[0]
                if provider_used>=config.max_concurrency or model_used>=config.model_concurrency.get(model,config.max_concurrency):
                    continue
                r=candidate
                break
            if r is None:
                return None
            c.execute("UPDATE tasks SET status='running',owner=?,lease_until=?,started_at=COALESCE(started_at,?),provider=?,actual_model=? WHERE task_id=?",(owner,time.time()+settings.lease_seconds,time.time(),provider,model,r['task_id']))
            return dict(c.execute('SELECT * FROM tasks WHERE task_id=?',(r['task_id'],)).fetchone())

    def attempt_start(self, task_id, owner, provider, model, max_attempts):
        with self.connect(immediate=True) as c:
            r=c.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if r['owner']!=owner or r['status']!='running' or r['cancel_requested'] or r['attempt_count']>=max_attempts:
                return None
            n=r['attempt_count']+1
            now=time.time()
            c.execute('UPDATE tasks SET attempt_count=?,actual_model=?,provider=? WHERE task_id=?',(n,model,provider,task_id))
            c.execute('INSERT INTO attempts(task_id,attempt,provider,model,language,scene,started_at) VALUES(?,?,?,?,?,?,?)',(task_id,n,provider,model,r['language'],r['scene'],now))
            return n

    def finish(self, task_id, owner, **values):
        with self.connect(immediate=True) as c:
            r=c.execute('SELECT status,owner,cancel_requested FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if r['owner']!=owner or r['status']!='running':
                return False
            if r['cancel_requested']:
                values={'status':'cancelled','outcome':'none','finished_at':time.time()}
            values.update(owner=None,lease_until=None)
            c.execute('UPDATE tasks SET '+','.join(k+'=?' for k in values)+' WHERE task_id=?',(*values.values(),task_id))
            return True
