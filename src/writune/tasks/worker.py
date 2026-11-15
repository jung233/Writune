"""Lease-owned lazy worker. Requests are individually cancellable asyncio tasks."""
from __future__ import annotations
import argparse
import asyncio
import json
import signal
import time
import uuid
from pathlib import Path
from writune.config import Settings
from writune.service import Service,split_model,read_text_exact
from writune.files.atomic import atomic_write
from writune.polishing.prompts import build_prompt
from writune.polishing.protected_spans import protect
from writune.polishing.validation import validate
from writune.providers.registry import get_provider
from writune.tasks.process_lock import ProcessLock

class Worker:
    def __init__(self,settings,owner=None):
        self.settings=settings
        self.service=Service(settings,start_worker=False)
        self.db=self.service.db
        self.owner=owner or uuid.uuid4().hex
        self.active={}
        self.stopping=False
        self.provider_sems={n:asyncio.Semaphore(p.max_concurrency) for n,p in settings.providers.items()}
        self.model_sems={}

    def stop(self):
        self.stopping=True

    async def run(self):
        lock=ProcessLock(self.settings.data_dir/'worker.lock')
        if not lock.acquire():
            return
        try:
            await self._run_owned()
        finally:
            lock.release()

    async def _run_owned(self):
        self.service.cleanup()
        with self.db.connect(immediate=True) as c:
            r=c.execute("SELECT * FROM workers WHERE name='main'").fetchone()
            if r and r['owner']!=self.owner and r['lease_until']>time.time():
                return
            import os
            c.execute("INSERT OR REPLACE INTO workers VALUES('main',?,?,?)",(self.owner,time.time()+self.settings.lease_seconds,os.getpid()))
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):
            try:
                loop.add_signal_handler(sig,self.stop)
            except (NotImplementedError,RuntimeError):
                pass
        idle=time.monotonic()
        try:
            while not self.stopping:
                with self.db.connect(immediate=True) as c:
                    r=c.execute("SELECT owner FROM workers WHERE name='main'").fetchone()
                    if not r or r[0]!=self.owner:
                        break
                    c.execute("UPDATE workers SET lease_until=? WHERE name='main' AND owner=?",(time.time()+self.settings.lease_seconds,self.owner))
                    c.execute("UPDATE tasks SET lease_until=? WHERE owner=? AND status='running'",(time.time()+self.settings.lease_seconds,self.owner))
                    cancelled={r[0] for r in c.execute("SELECT task_id FROM tasks WHERE owner=? AND cancel_requested=1",(self.owner,))}
                for tid,t in list(self.active.items()):
                    if tid in cancelled:
                        t.cancel()
                    if t.done():
                        try:
                            t.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            # No raw prompts, provider responses or secrets in logs.
                            self.db.finish(tid,self.owner,status='failed',outcome='none',finished_at=time.time(),error_code='LOCAL_ERROR',error_message='Local worker operation failed; original snapshot retained')
                        del self.active[tid]
                while len(self.active)<self.settings.max_concurrency:
                    row=self.db.claim(self.owner,self.settings)
                    if not row:
                        break
                    self.active[row['task_id']]=asyncio.create_task(self.execute(row))
                    idle=time.monotonic()
                if self.active:
                    idle=time.monotonic()
                elif time.monotonic()-idle>=self.settings.worker_idle_seconds:
                    # Check queue and release reservation in one write transaction.
                    with self.db.connect(immediate=True) as c:
                        pending=c.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]
                        if not pending:
                            c.execute("DELETE FROM workers WHERE name='main' AND owner=?",(self.owner,))
                            return
                await asyncio.sleep(min(.2,self.settings.lease_seconds/4))
        finally:
            for t in self.active.values():
                t.cancel()
            if self.active:
                await asyncio.gather(*self.active.values(),return_exceptions=True)
            with self.db.connect(immediate=True) as c:
                c.execute("UPDATE tasks SET status='queued',owner=NULL,lease_until=NULL WHERE owner=? AND status='running'",(self.owner,))
                c.execute("DELETE FROM workers WHERE name='main' AND owner=?",(self.owner,))

    async def execute(self,row):
        tid=row['task_id'];payload=json.loads(row['payload'])
        try:
            original=read_text_exact(row['original_path'])
        except FileNotFoundError:
            self.db.finish(tid,self.owner,status='failed',outcome='none',finished_at=time.time(),error_code='TASK_FILES_EXPIRED',error_message='Temporary task files were removed; submit the source again')
            return
        folder=Path(row['original_path']).parent
        error_code=row['error_code'];error_message=row['error_message']
        text=original;outcome='original_fallback';metrics={}
        max_attempts=1+payload.get('retry_count',self.settings.retry_count)
        try:
            protected=protect(original,terms=payload.get('context',{}).get('terminology',[]))
            while self.db.get(tid)['attempt_count']<max_attempts:
                current=self.db.get(tid)
                if current['cancel_requested']:
                    raise asyncio.CancelledError
                ref=row['primary_model'] if current['attempt_count']==0 else row['secondary_model'] or row['primary_model']
                name,model=split_model(ref)
                config=self.settings.providers[name]
                resolved=config.resolve_model(model)
                if resolved.custom_templates:
                    templates=dict(self.settings.custom_templates)
                    for key,value in resolved.custom_templates.items():
                        templates[key]={**templates[key],**value} if isinstance(value,dict) and isinstance(templates.get(key),dict) else value
                    prompt=build_prompt(protected.text,language=row['language'],scene=row['scene'],style=payload['style'],context=payload.get('context'),local_context=payload.get('local_context',''),custom_templates=templates)
                else:
                    prompt=build_prompt(protected.text,language=row['language'],scene=row['scene'],style=payload['style'],context=payload.get('context'),local_context=payload.get('local_context',''),custom_templates=self.settings.custom_templates)
                limit=config.model_concurrency.get(model,config.max_concurrency)
                sem=self.model_sems.setdefault((name,model),asyncio.Semaphore(limit))
                async with self.provider_sems[name],sem:
                    number=self.db.attempt_start(tid,self.owner,name,model,max_attempts)
                    if number is None:
                        raise asyncio.CancelledError
                    started=time.monotonic()
                    adapter=None
                    try:
                        adapter=get_provider(name,self.settings,model=model)
                        generation=await adapter.generate(model,prompt,payload.get('options'))
                        candidate=protected.restore(generation.text)
                        validate(original,candidate)
                        text=candidate;outcome='polished'
                        metrics={k:getattr(generation,k,None) for k in ('input_tokens','output_tokens','cached_input_tokens','cache_write_tokens','first_token_latency','generation_tps','total_elapsed')}
                        self.record_attempt(tid,number,success=True,**metrics,tps_estimated=getattr(generation,'tps_estimated',True))
                        error_code=None;error_message=None
                        break
                    except asyncio.CancelledError:
                        user_cancelled=self.db.get(tid)['cancel_requested']
                        code='CANCELLED' if user_cancelled else 'WORKER_STOPPED'
                        partial=dict(getattr(adapter,'last_metrics',{}))
                        partial['total_elapsed']=time.monotonic()-started
                        self.record_attempt(tid,number,success=False,error_code=code,**partial)
                        if not user_cancelled:
                            with self.db.connect(immediate=True) as c:
                                c.execute("UPDATE tasks SET possible_duplicate_billing=1,error_code='WORKER_STOPPED',error_message='Worker stopped an active request; upstream completion unknown' WHERE task_id=? AND owner=? AND status='running'",(tid,self.owner))
                        raise
                    except Exception as exc:
                        error_code=getattr(exc,'code','VALIDATION_FAILED' if exc.__class__.__name__=='ValidationError' else 'PROVIDER_ERROR')
                        error_message='Output validation failed' if error_code=='VALIDATION_FAILED' else 'Upstream attempt failed ('+str(error_code)+')'
                        partial=dict(getattr(adapter,'last_metrics',{}))
                        partial['total_elapsed']=time.monotonic()-started
                        self.record_attempt(tid,number,success=False,error_code=error_code,**partial)
                        with self.db.connect(immediate=True) as c:
                            c.execute("UPDATE tasks SET error_code=?,error_message=?,retry_reason=? WHERE task_id=? AND owner=? AND status='running'",(error_code,error_message,error_code,tid,self.owner))
                        if not getattr(exc,'retryable',True):
                            break
            current=self.db.get(tid)
            if current['cancel_requested']:
                raise asyncio.CancelledError
            if outcome!='polished' and error_code is None:
                error_code='ATTEMPT_BUDGET_EXHAUSTED';error_message='Attempt budget exhausted; original delivered'
            result=folder/'result.txt';manifest=folder/'result.json'
            content={'task_id':tid,'status':'completed','outcome':outcome,'result_path':str(result),'polished':outcome=='polished','fallback_used':outcome=='original_fallback','error':{'code':error_code,'message':error_message,'attempts':current['attempt_count']} if error_code else None,'possible_duplicate_billing':bool(current['possible_duplicate_billing']),'tps_estimated':getattr(generation,'tps_estimated',True) if outcome=='polished' else None,'metrics':metrics}
            # Fence publication as well as the record update. A recovered stale worker
            # must never overwrite a newer owner's result files.
            with self.db.connect(immediate=True) as c:
                live=c.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone()
                if live['owner']!=self.owner or live['status']!='running':
                    return
                if live['cancel_requested']:
                    raise asyncio.CancelledError
                atomic_write(result,text.encode('utf-8'),overwrite=True)
                atomic_write(manifest,json.dumps(content,ensure_ascii=False,indent=2).encode(),overwrite=True)
                values=dict(status='completed',outcome=outcome,finished_at=time.time(),result_path=str(result),manifest_path=str(manifest),error_code=error_code,error_message=error_message,owner=None,lease_until=None,**metrics)
                c.execute('UPDATE tasks SET '+','.join(k+'=?' for k in values)+' WHERE task_id=?',(*values.values(),tid))
        except asyncio.CancelledError:
            current=self.db.get(tid)
            if current['cancel_requested']:
                self.db.finish(tid,self.owner,status='cancelled',outcome='none',finished_at=time.time())
            # Graceful worker shutdown requeues in finally; attempts remain bounded.
            raise

    def record_attempt(self,tid,number,**values):
        values.update(finished_at=time.time())
        with self.db.connect(immediate=True) as c:
            live=c.execute("SELECT 1 FROM tasks WHERE task_id=? AND owner=? AND status='running'",(tid,self.owner)).fetchone()
            if live:
                c.execute('UPDATE attempts SET '+','.join(k+'=?' for k in values)+' WHERE task_id=? AND attempt=? AND finished_at IS NULL',(*values.values(),tid,number))

def main():
    p=argparse.ArgumentParser();p.add_argument('--reservation');p.add_argument('--settings')
    args=p.parse_args()
    settings=Settings.load(args.settings) if args.settings else Settings.load()
    if args.settings:
        # Loaded credentials live only in this process after startup.
        import shutil
        snapshot=Path(args.settings).resolve()
        launches=(settings.data_dir/'launches').resolve()
        if snapshot.parent==launches and snapshot.name==args.reservation:
            shutil.rmtree(snapshot)
    asyncio.run(Worker(settings,args.reservation).run())

if __name__=='__main__':
    main()
