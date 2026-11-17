"""One shared business layer for MCP, CLI and the durable worker."""
from __future__ import annotations
import hashlib
import base64
import json
import shutil
import time
import uuid
from pathlib import Path
from writune.config import Settings
from writune.tasks.database import Database
from writune.tasks.eta import estimate
from writune.files.workspace import Workspace
from writune.files.atomic import atomic_write

def split_model(ref):
    if not isinstance(ref,str) or '/' not in ref:
        raise ValueError('Model must be provider/model')
    provider,model=ref.split('/',1)
    if not provider.strip() or not model.strip():
        raise ValueError('Model must be provider/model')
    return provider,model

def read_text_exact(path,encoding='utf-8'):
    """Decode without universal-newline conversion of the original snapshot."""
    with Path(path).open('r',encoding=encoding,newline='') as stream:
        return stream.read()

class Service:
    def __init__(self, settings=None, start_worker=True):
        self.settings=settings or Settings.load()
        self.start_worker=start_worker
        self.settings.data_dir.mkdir(parents=True,exist_ok=True)
        self.settings.home_dir.mkdir(parents=True,exist_ok=True)
        self.task_root=self.settings.task_dir
        self.task_root.mkdir(parents=True,exist_ok=True)
        self.workspace=Workspace(self.settings.allowed_workspaces,self.task_root)
        self.db=Database(self.settings.db_path)
        from writune.tasks.artifacts import migrate_legacy_artifacts
        migrate_legacy_artifacts(self.settings,self.db)
        try:
            self.settings.data_dir.chmod(0o700)
            self.task_root.chmod(0o700)
            self.settings.db_path.chmod(0o600)
        except OSError:
            pass

    def create_context(self, **kwargs):
        previous=kwargs.pop('context_id',None)
        if previous:
            with self.db.connect() as c:
                r=c.execute('SELECT content FROM contexts WHERE context_id=?',(previous,)).fetchone()
            if not r:
                raise ValueError('CONTEXT_NOT_FOUND')
            content=json.loads(r[0]); content.update(kwargs)
        else:
            content=kwargs
        raw=json.dumps(content,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        cid='ctx_'+hashlib.sha256(raw.encode()).hexdigest()
        with self.db.connect(immediate=True) as c:
            c.execute('INSERT OR IGNORE INTO contexts VALUES(?,?,?)',(cid,raw,time.time()))
        return {'context_id':cid}

    def _prepare(self, *, text=None, source_path=None, source_encoding=None, language=None,scene=None,context_id=None,primary_model=None,secondary_model=None,style=None,priority=None,idempotency_key=None,profile=None,local_context=None,options=None,**extra):
        if extra:
            raise ValueError('Unknown submit options: '+','.join(extra))
        if (text is None)==(source_path is None):
            raise ValueError('Provide exactly one of text or source_path')
        source_bytes=None
        if source_path is not None:
            source_bytes=self.workspace.check(source_path).read_bytes()
            if source_encoding:
                text=source_bytes.decode(source_encoding)
            elif source_bytes.startswith((b'\xff\xfe',b'\xfe\xff',b'\x00\x00\xfe\xff',b'\xef\xbb\xbf')):
                from writune.files.text import decode_text
                text=decode_text(source_bytes).text
            else:
                try:
                    text=source_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    raise ValueError('Source is not UTF-8 or BOM-marked Unicode; provide source_encoding explicitly') from None
        if not isinstance(text,str) or not text.strip():
            raise ValueError('EMPTY_INPUT')
        context={}
        if context_id:
            with self.db.connect() as c:
                r=c.execute('SELECT content FROM contexts WHERE context_id=?',(context_id,)).fetchone()
            if not r:
                raise ValueError('CONTEXT_NOT_FOUND')
            context=json.loads(r[0])
        if profile and profile not in self.settings.profiles:
            raise ValueError('PROFILE_NOT_FOUND')
        prof=self.settings.profiles.get(profile,{}) if profile else {}
        language=language or prof.get('language') or context.get('language') or ('zh' if any('\u4e00'<=x<='\u9fff' for x in text) else 'en')
        if language not in ('zh','en'):
            raise ValueError('Language must be zh or en')
        defaults=getattr(self.settings,'language_defaults',{}).get(language,{})
        primary_model=primary_model or prof.get('primary_model') or defaults.get('primary_model') or getattr(self.settings,language+'_primary') or getattr(self.settings,'default_primary_model','')
        secondary_model=secondary_model if secondary_model is not None else prof.get('secondary_model',defaults.get('secondary_model',getattr(self.settings,language+'_secondary') or getattr(self.settings,'default_secondary_model','')))
        for ref in (primary_model,secondary_model):
            if ref and split_model(ref)[0] not in self.settings.providers:
                raise ValueError('PROVIDER_NOT_FOUND')
        if not primary_model:
            raise ValueError('Configure a primary model before submitting')
        scene=scene or prof.get('scene') or context.get('scene') or defaults.get('scene') or self.settings.default_scene
        style=style or prof.get('style') or defaults.get('style') or self.settings.default_style
        priority=priority if priority is not None else prof.get('priority',defaults.get('priority',0))
        local_context=local_context if local_context is not None else prof.get('local_context',defaults.get('local_context',''))
        inherited_options={**defaults.get('options',{}),**prof.get('options',{})}
        # Validate the profile synchronously without performing network inference.
        from writune.polishing.prompts import build_prompt
        build_prompt(text,language=language,scene=scene,style=style,context=context,local_context=local_context,custom_templates=self.settings.custom_templates)
        if options is not None and not isinstance(options,dict):
            raise ValueError('options must be an object')
        return dict(text=text,source_path=str(source_path) if source_path else None,source_encoding=source_encoding,source_bytes_b64=base64.b64encode(source_bytes).decode('ascii') if source_bytes is not None else None,language=language,scene=scene,style=style,context_id=context_id,context=context,primary_model=primary_model,secondary_model=secondary_model,priority=int(priority),idempotency_key=idempotency_key,local_context=local_context,options={**inherited_options,**(options or {})},retry_count=self.settings.retry_count)

    def _insert(self, payloads, batch_id=None,batch_key=None,batch_concurrency=None,batch_delivery=None):
        out=[]; written=[]
        try:
            with self.db.connect(immediate=True) as c:
                if batch_id:
                    batch_fingerprint=hashlib.sha256(json.dumps({'items':payloads,'max_concurrency':batch_concurrency,'delivery':batch_delivery},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    previous=c.execute('SELECT * FROM batches WHERE idempotency_key=?',(batch_key,)).fetchone() if batch_key else None
                    if previous:
                        if previous['fingerprint']!=batch_fingerprint:
                            raise ValueError('IDEMPOTENCY_CONFLICT')
                        return [dict(r) for r in c.execute('SELECT * FROM tasks WHERE batch_id=? ORDER BY position',(previous['batch_id'],))]
                    c.execute('INSERT INTO batches(batch_id,idempotency_key,fingerprint,max_concurrency,delivery) VALUES(?,?,?,?,?)',(batch_id,batch_key,batch_fingerprint,batch_concurrency,json.dumps(batch_delivery) if batch_delivery else None))
                queued=c.execute("SELECT COUNT(*) FROM tasks WHERE status='queued'").fetchone()[0]
                new=[]
                for index,p in enumerate(payloads):
                    fingerprint=hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    old=c.execute('SELECT * FROM tasks WHERE idempotency_key=?',(p['idempotency_key'],)).fetchone() if p['idempotency_key'] else None
                    if old:
                        if batch_id and old['batch_id']!=batch_id:
                            raise ValueError('IDEMPOTENCY_CONFLICT')
                        if old['fingerprint']!=fingerprint:
                            raise ValueError('IDEMPOTENCY_CONFLICT')
                        out.append((index,dict(old))); continue
                    new.append((index,p,fingerprint))
                if queued+len(new)>self.settings.max_queue_size:
                    raise ValueError('QUEUE_FULL')
                for index,p,fingerprint in new:
                    tid=uuid.uuid4().hex
                    folder=self.task_root/tid;folder.mkdir();written.append(folder)
                    try:
                        folder.chmod(0o700)
                    except OSError:
                        pass
                    original=folder/'original.txt'
                    atomic_write(original,p['text'].encode('utf-8'),overwrite=False)
                    if p.get('source_bytes_b64') is not None:
                        atomic_write(folder/'original.source',base64.b64decode(p['source_bytes_b64']),overwrite=False)
                    provider,model=split_model(p['primary_model'])
                    eta,confidence=estimate(self.db,provider,model,p['language'],p['scene'],p['text'],self.settings.max_concurrency,queued,settings=self.settings,connection=c,batch_id=batch_id,batch_concurrency=batch_concurrency,retry_count=p.get('retry_count',self.settings.retry_count))
                    # Store only a snapshot, not a source-file dependency.
                    stored=dict(p);stored.pop('text');stored.pop('source_bytes_b64',None)
                    row=dict(task_id=tid,batch_id=batch_id,position=index,created_at=time.time(),status='queued',priority=p['priority'],idempotency_key=p['idempotency_key'],fingerprint=fingerprint,payload=json.dumps(stored,ensure_ascii=False),original_path=str(original),language=p['language'],scene=p['scene'],context_id=p['context_id'],source_path=p['source_path'],primary_model=p['primary_model'],secondary_model=p['secondary_model'],estimated_seconds=eta,estimate_confidence=confidence)
                    c.execute('INSERT INTO tasks('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
                    out.append((index,row))
            if self.start_worker:
                try:
                    self._wake()
                except Exception:
                    # Persistence is the acceptance boundary; status/next submit
                    # can retry startup without creating another copy of this task.
                    self._worker_start_failed=True
        except BaseException:
            # Committed records must retain their original snapshot even if spawn fails.
            for folder in written:
                with self.db.connect() as c:
                    exists=c.execute('SELECT 1 FROM tasks WHERE task_id=?',(folder.name,)).fetchone()
                if not exists:
                    shutil.rmtree(folder)
            raise
        return [r for _,r in sorted(out)]

    def _wake(self):
        from writune.tasks.lifecycle import ensure_worker,process_alive
        ensure_worker(self.settings,self.db)
        with self.db.connect() as c:
            worker=c.execute("SELECT pid FROM workers WHERE name='main'").fetchone()
        self._worker_start_failed=not bool(worker and worker['pid'] and process_alive(worker['pid']))

    def submit(self, **kwargs):
        r=self._insert([self._prepare(**kwargs)])[0]
        return {**self._submitted(r),**self._context_estimate(r)}

    def _context_estimate(self, row):
        """Advisory only: estimate each configured model's complete protected prompt."""
        from writune.polishing.prompts import build_prompt
        from writune.polishing.protected_spans import protect
        from writune.providers.tps import TokenCounter
        payload=json.loads(row['payload'])
        original=read_text_exact(row['original_path'])
        protected=protect(original,terms=payload.get('context',{}).get('terminology',[]))
        counts=[];estimated=False
        for ref in {row['primary_model'],row['secondary_model']} - {None,''}:
            name,model=split_model(ref)
            provider=self.settings.providers[name].resolve_model(model)
            templates=dict(self.settings.custom_templates)
            for key,value in provider.custom_templates.items():
                templates[key]={**templates[key],**value} if isinstance(value,dict) and isinstance(templates.get(key),dict) else value
            prompt=build_prompt(protected.text,language=row['language'],scene=row['scene'],style=payload['style'],context=payload.get('context'),local_context=payload.get('local_context',''),custom_templates=templates)
            counter=TokenCounter(provider.tokenizer)
            request_text=prompt.prefix+'\n\n'+prompt.input
            count=counter.count(request_text)
            if counter.estimated:
                count=max(count,(len(request_text)+3)//4)
            counts.append(count)
            estimated=estimated or counter.estimated
        count=max(counts,default=0)
        warnings=['POSSIBLE_CONTEXT_WINDOW_EXCEEDED: estimated input is at least 200K tokens; main model should verify primary and fallback models support long context and allow room for output. Task creation continues.'] if count>=200_000 else []
        return {'estimated_input_tokens':count,'token_count_estimated':estimated,'warnings':warnings}

    def _submitted(self,r):
        with self.db.connect() as c:
            pos=c.execute("SELECT position FROM (SELECT task_id,ROW_NUMBER() OVER (ORDER BY priority DESC,created_at,position,rowid) position FROM tasks WHERE status='queued') WHERE task_id=?",(r['task_id'],)).fetchone()
            pos=pos[0] if pos else 0
        return {'task_id':r['task_id'],'status':r['status'],'estimated_seconds':r.get('estimated_seconds'),'estimate_confidence':r.get('estimate_confidence') or 'low','queue_position':pos,'worker_start_pending':getattr(self,'_worker_start_failed',False)}

    def submit_batch(self,items=None,source_paths=None,markdown_path=None,max_concurrency=None,source_encoding=None,segmentation='paragraph',**kwargs):
        delivery=None
        if segmentation not in ('paragraph','section','document'):
            raise ValueError('segmentation must be paragraph, section, or document')
        if segmentation!='paragraph' and markdown_path is None:
            raise ValueError('Nondefault segmentation is supported only with markdown_path')
        if max_concurrency is not None and (isinstance(max_concurrency,bool) or not isinstance(max_concurrency,int) or max_concurrency<1):
            raise ValueError('Batch max_concurrency must be a positive integer')
        if sum(x is not None for x in (items,source_paths,markdown_path))!=1:
            raise ValueError('Provide one batch input mode')
        if markdown_path is not None:
            from writune.polishing.segmentation import segment_markdown
            from writune.files.text import DecodedText,decode_text
            raw=self.workspace.check(markdown_path).read_bytes()
            decoded=decode_text(raw)
            if source_encoding and not decoded.bom:
                decoded=DecodedText(raw.decode(source_encoding),source_encoding,b'')
            parts=segment_markdown(decoded.text,segmentation=segmentation)
            delivery={'mode':'markdown','encoding':decoded.encoding,'bom':base64.b64encode(decoded.bom).decode('ascii')}
            items=[{'text':p} for p in parts]
        elif source_paths is not None:
            items=[{'source_path':p} for p in source_paths]
        if not items:
            raise ValueError('EMPTY_BATCH')
        bid=uuid.uuid4().hex
        base_key=kwargs.pop('idempotency_key',None)
        prepared=[]
        for i,item in enumerate(items):
            data={**kwargs,**({'text':item} if isinstance(item,str) else item)}
            if source_encoding:
                data.setdefault('source_encoding',source_encoding)
            if base_key:
                data.setdefault('idempotency_key',f'{base_key}:{i}')
            prepared.append(self._prepare(**data))
        rows=self._insert(prepared,bid,base_key,max_concurrency,delivery)
        if self.start_worker:
            try:
                self._wake()
            except Exception:
                self._worker_start_failed=True
        # A replay of an entire batch returns its persisted batch identity.
        ids={r.get('batch_id') for r in rows}
        if len(ids)==1 and next(iter(ids)):
            bid=next(iter(ids))
        estimates=[r.get('estimated_seconds') for r in rows]
        context_estimates=[self._context_estimate(row) for row in rows]
        return {
            'batch_id':bid,
            'task_ids':[r['task_id'] for r in rows],
            'accepted_count':len(rows),
            'estimated_seconds':max(estimates) if all(x is not None for x in estimates) else None,
            'estimated_input_tokens':max((item['estimated_input_tokens'] for item in context_estimates),default=0),
            'token_count_estimated':any(item['token_count_estimated'] for item in context_estimates),
            'warnings':[f"{row['task_id']}: {warning}" for row,item in zip(rows,context_estimates) for warning in item['warnings']],
        }

    def status(self,task_id=None,batch_id=None):
        if bool(task_id)==bool(batch_id):
            raise ValueError('Provide task_id or batch_id')
        if task_id:
            rows=[self.db.get(task_id)]
        else:
            with self.db.connect() as c:
                rows=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE batch_id=? ORDER BY position',(batch_id,))]
            if not rows:
                raise ValueError('BATCH_NOT_FOUND')
        rows=[self._check_artifacts(r) for r in rows]
        if self.start_worker and any(r['status'] in ('queued','running') for r in rows):
            try:
                self._wake()
            except Exception:
                self._worker_start_failed=True
        completed=sum(r['status']=='completed' for r in rows)
        running=sum(r['status']=='running' for r in rows)
        terminal=all(r['status'] in ('completed','cancelled','failed') for r in rows)
        status=rows[0]['status'] if task_id else ('completed' if terminal and completed==len(rows) else 'failed' if terminal and any(r['status']=='failed' for r in rows) else 'cancelled' if terminal else 'running' if running else 'queued')
        now=time.time()
        remaining=[]
        active_attempt_starts={}
        if not terminal:
            with self.db.connect() as c:
                for row in rows:
                    if row['status']!='running':
                        continue
                    attempt=c.execute('SELECT started_at FROM attempts WHERE task_id=? AND finished_at IS NULL ORDER BY attempt DESC LIMIT 1',(row['task_id'],)).fetchone()
                    active_attempt_starts[row['task_id']]=attempt['started_at'] if attempt else None
        for r in rows:
            if r['status'] in ('queued','running'):
                payload=json.loads(r['payload'])
                reference=(r['provider']+'/'+r['actual_model']) if r['status']=='running' and r['provider'] and r['actual_model'] else (r['secondary_model'] or r['primary_model']) if r['attempt_count'] else r['primary_model']
                provider,model=split_model(reference)
                eta,_=estimate(self.db,provider,model,r['language'],r['scene'],read_text_exact(r['original_path']),self.settings.max_concurrency,settings=self.settings,task_id=r['task_id'],batch_id=r['batch_id'],retry_count=payload.get('retry_count',self.settings.retry_count),attempt_count=r['attempt_count'],attempt_in_progress=active_attempt_starts.get(r['task_id']) is not None)
                if eta is None:
                    remaining.append(None)
                elif r['status']=='running':
                    started=active_attempt_starts.get(r['task_id']) or r['started_at'] or now
                    remaining.append(max(1.0,eta-max(0.0,now-started)))
                else:
                    remaining.append(eta)
        return {'task_id':task_id,'batch_id':batch_id,'status':status,'progress':sum(r['status'] in ('completed','cancelled','failed') for r in rows)/len(rows),'queue_position':self._submitted(rows[0])['queue_position'],'running_count':running,'completed_count':completed,'estimated_seconds_remaining':0 if terminal else max(remaining) if remaining and all(v is not None for v in remaining) else None,'error_summary':[{'task_id':r['task_id'],'code':r['error_code']} for r in rows if r['error_code']],'task_ids':[r['task_id'] for r in rows]}

    def result(self,task_id,inline_text=False):
        r=self._check_artifacts(self.db.get(task_id))
        out={k:r[k] for k in ('task_id','status','outcome','result_path','manifest_path','possible_duplicate_billing')}
        out.update(polished=r['outcome']=='polished',fallback_used=r['outcome']=='original_fallback',error={'code':r['error_code'],'message':r['error_message'],'attempts':r['attempt_count']} if r['error_code'] else None)
        if r['manifest_path'] and Path(r['manifest_path']).is_file():
            manifest=json.loads(read_text_exact(r['manifest_path']))
            out.update({key:manifest[key] for key in ('review_required','partial_output_path','partial_reasoning_path') if key in manifest})
        if inline_text and r['result_path']:
            out['text']=read_text_exact(self.workspace.check(r['result_path']))
        return out

    def _check_artifacts(self,row):
        required=row['result_path'] if row['status']=='completed' else row['original_path'] if row['status']=='queued' else None
        if required and not Path(required).is_file():
            with self.db.connect(immediate=True) as c:
                c.execute("UPDATE tasks SET status='failed',outcome='none',result_path=NULL,manifest_path=NULL,error_code='TASK_FILES_EXPIRED',error_message='Temporary task files were removed; submit the source again',finished_at=? WHERE task_id=? AND status=?",(time.time(),row['task_id'],row['status']))
            return self.db.get(row['task_id'])
        return row

    def apply(self,task_id,target_path,placeholder_id,output_path=None,overwrite=False,target_encoding=None):
        from writune.files import apply_result
        r=self._check_artifacts(self.db.get(task_id))
        if r['status']!='completed':
            raise ValueError('RESULT_NOT_READY')
        return apply_result(r['result_path'],target_path,placeholder_id,output_path,overwrite,self.workspace,target_encoding=target_encoding)

    def export(self,task_id,output_path,overwrite=False):
        r=self._check_artifacts(self.db.get(task_id))
        if r['status']!='completed':
            raise ValueError('RESULT_NOT_READY')
        target=self.workspace.check(output_path,write=True)
        atomic_write(target,self.workspace.check(r['result_path']).read_bytes(),overwrite=overwrite)
        return {'task_id':task_id,'output_path':str(target)}

    def merge_batch(self,batch_id,output_path,separator=None,overwrite=False):
        with self.db.connect() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE batch_id=? ORDER BY position',(batch_id,))]
            batch=c.execute('SELECT delivery FROM batches WHERE batch_id=?',(batch_id,)).fetchone()
        if not rows:
            raise ValueError('BATCH_NOT_FOUND')
        rows=[self._check_artifacts(r) for r in rows]
        if any(r['status']!='completed' for r in rows):
            raise ValueError('BATCH_NOT_READY')
        target=self.workspace.check(output_path,write=True)
        texts=[read_text_exact(self.workspace.check(r['result_path'])) for r in rows]
        delivery=json.loads(batch[0]) if batch and batch[0] else {}
        if delivery.get('mode')=='markdown' and separator is None:
            import re
            from writune.files.text import DecodedText,dominant_newline,replace_newlines
            for index,row in enumerate(rows):
                original=read_text_exact(row['original_path'])
                if row['outcome']=='original_fallback':
                    texts[index]=original
                    continue
                before=re.match(r'\s*',original).group(0)
                after=re.search(r'\s*$',original).group(0)
                texts[index]=before+replace_newlines(texts[index].strip(),dominant_newline(original))+after
            data=DecodedText('',delivery['encoding'],base64.b64decode(delivery['bom'])).encode(''.join(texts))
        else:
            data=(separator if separator is not None else '\n\n').join(texts).encode('utf-8')
        atomic_write(target,data,overwrite=overwrite)
        return {'batch_id':batch_id,'output_path':str(target),'paragraph_count':len(rows),'fallback_count':sum(r['outcome']=='original_fallback' for r in rows)}

    def cancel(self,task_id):
        with self.db.connect(immediate=True) as c:
            r=c.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if not r:
                raise ValueError('TASK_NOT_FOUND')
            if r['status']=='queued':
                c.execute("UPDATE tasks SET cancel_requested=1,status='cancelled',finished_at=? WHERE task_id=?",(time.time(),task_id))
            elif r['status']=='running':
                c.execute('UPDATE tasks SET cancel_requested=1 WHERE task_id=?',(task_id,))
        return {'task_id':task_id,'cancel_requested':r['status'] in ('queued','running','cancelled'),'status':self.db.get(task_id)['status']}

    def health(self,probe=False):
        with self.db.connect() as c:
            counts={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status')}
            worker=c.execute("SELECT owner,lease_until,pid FROM workers WHERE name='main'").fetchone()
            errors=[dict(r) for r in c.execute('SELECT task_id,error_code FROM tasks WHERE error_code IS NOT NULL ORDER BY created_at DESC LIMIT 5')]
        from writune.tasks.process_lock import ProcessLock
        worker_lock=ProcessLock(self.settings.data_dir/'worker.lock')
        locked=not worker_lock.acquire()
        worker_lock.release()
        responsive=bool(worker and worker['lease_until']>time.time())
        providers={name:{'configured':bool(p.base_url and p.api_key.get_secret_value()),'availability':'not_probed'} for name,p in self.settings.providers.items()}
        if probe:
            import asyncio
            from writune.providers.registry import get_provider
            from writune.polishing.prompts import Prompt
            async def check(name,config):
                refs=[self.settings.en_primary,self.settings.zh_primary,self.settings.en_secondary,self.settings.zh_secondary]
                model=next((ref.split('/',1)[1] for ref in refs if ref.startswith(name+'/')),next(iter(config.models),'') )
                if not model:
                    return name,'no_probe_model'
                try:
                    bounded=self.settings.model_copy(update={'total_timeout':min(self.settings.total_timeout,15),'first_token_timeout':min(self.settings.first_token_timeout,10)})
                    async with asyncio.timeout(15):
                        await get_provider(name,bounded,model=model).generate(model,Prompt(prefix='Return only OK.',input='Reply OK.'))
                    return name,'available'
                except Exception as exc:
                    return name,'unavailable:'+getattr(exc,'code','PROBE_FAILED')
            async def checks():
                return await asyncio.gather(*(check(name,p) for name,p in self.settings.providers.items()))
            for name,availability in asyncio.run(checks()):
                providers[name]['availability']=availability
        return {'initialized':bool(self.settings.providers and self.settings.en_primary and self.settings.zh_primary),'providers':providers,'worker':{'alive':locked or responsive,'responsive':responsive,'state':'running' if responsive else 'unresponsive' if locked else 'stopped','pid':worker['pid'] if worker else None},'active_tasks':counts.get('running',0),'queue_length':counts.get('queued',0),'recent_errors':errors,'storage':{'database':str(self.settings.db_path),'ok':True}}

    def stats(self,recent_window=50):
        if not isinstance(recent_window,int) or not 1<=recent_window<=10000:
            raise ValueError('recent_window must be 1..10000')
        with self.db.connect() as c:
            prefix='WITH recent AS (SELECT *, ROW_NUMBER() OVER(PARTITION BY provider,model,language,scene ORDER BY id DESC) AS sample_number FROM attempts WHERE finished_at IS NOT NULL) '
            rows=[dict(r) for r in c.execute(prefix+'SELECT provider,model,language,scene,COUNT(*) requests,AVG(success) success_rate,1-AVG(success) failure_rate,AVG(generation_tps) average_tps,SUM(tps_estimated) estimated_tps_requests,AVG(first_token_latency) first_token_latency,AVG(total_elapsed) total_elapsed,AVG(total_elapsed-first_token_latency) generation_duration,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL THEN 1 ELSE 0 END) measured_usage_requests,SUM(cached_input_tokens) cached_input_tokens,SUM(cache_write_tokens) cache_write_tokens FROM recent WHERE sample_number<=? GROUP BY provider,model,language,scene',(recent_window,))]
            errors=[dict(r) for r in c.execute(prefix+'SELECT error_code,COUNT(*) count FROM recent WHERE error_code IS NOT NULL AND sample_number<=? GROUP BY error_code',(recent_window,))]
        from writune.telemetry.metrics import configured_cost
        for row in rows:
            row['sample_window']=recent_window
            row['cost']=configured_cost(row,self.settings)
        return {'models':rows,'failure_reasons':errors}

    def cleanup(self,all_history=False):
        cutoff=time.time()+1 if all_history else time.time()-self.settings.task_ttl_hours*3600
        count=0
        with self.db.connect(immediate=True) as c:
            rows=c.execute("SELECT task_id FROM tasks WHERE status IN ('completed','cancelled','failed') AND finished_at<?",(cutoff,)).fetchall()
            for r in rows:
                folder=self.workspace.check(self.task_root/r[0],write=True)
                if folder.exists():
                    shutil.rmtree(folder)
                c.execute('DELETE FROM attempts WHERE task_id=?',(r[0],))
                c.execute('DELETE FROM tasks WHERE task_id=?',(r[0],));count+=1
            c.execute('DELETE FROM batches WHERE batch_id NOT IN (SELECT DISTINCT batch_id FROM tasks WHERE batch_id IS NOT NULL)')
            removed_contexts=c.execute('DELETE FROM contexts WHERE created_at<? AND context_id NOT IN (SELECT context_id FROM tasks WHERE context_id IS NOT NULL)',(cutoff,)).rowcount
        launches=self.settings.data_dir/'launches'
        if launches.exists():
            for folder in launches.iterdir():
                if folder.is_dir() and folder.stat().st_mtime<cutoff:
                    checked=self.workspace.check(folder,write=True) if folder.is_relative_to(self.task_root) else folder.resolve()
                    if checked.parent==launches.resolve() and not folder.is_symlink():
                        shutil.rmtree(checked)
        return {'removed_tasks':count,'removed_contexts':removed_contexts}
