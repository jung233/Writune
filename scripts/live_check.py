"""Opt-in live acceptance run using credentials from a private WRITUNE_HOME.

Never embeds a key or provider URL in source. Output is metadata only.
"""
import json
import time
import tempfile
from pathlib import Path
from writune.config import Settings
from writune.service import Service

def main():
    settings=Settings.load()
    service=Service(settings)
    workspace=settings.allowed_workspaces[0]
    workspace.mkdir(parents=True,exist_ok=True)
    workspace=Path(tempfile.mkdtemp(prefix='writune-live-',dir=workspace))
    context=service.create_context(language='en',scene='sci_paper',section='Results',constraints=['Preserve reported numbers and association language'])
    draft=workspace/'draft.txt'
    draft.write_text('In the analysis, GHRH expression was associated with glucose levels (P = 0.02; n = 30). These findings indicate an association and do not establish causality.',encoding='utf-8')
    zh=workspace/'draft-zh.txt'
    zh.write_text('本研究分析了30名参与者的数据，结果显示两项指标存在关联（P = 0.02）。这些结果提示相关性，但不能据此推断因果关系。',encoding='utf-8')
    start=time.monotonic()
    batch=service.submit_batch(items=[{'source_path':str(draft),'context_id':context['context_id'],'language':'en','scene':'sci_paper'},{'source_path':str(zh),'language':'zh','scene':'chinese_journal'}])
    summary={'submit_elapsed':time.monotonic()-start,'batch_id':batch['batch_id'],'tasks':[]}
    deadline=time.monotonic()+settings.total_timeout*(1+settings.retry_count)+60
    while time.monotonic()<deadline:
        status=service.status(batch_id=batch['batch_id'])
        if status['progress']==1:
            break
        time.sleep(2)
    for i,tid in enumerate(batch['task_ids']):
        result=service.result(tid)
        summary['tasks'].append(result)
        if result['status']=='completed':
            template=workspace/f'template-{i}.md'
            template.write_text('# Live acceptance\n\n{{WRITUNE:PARA}}\n',encoding='utf-8')
            service.apply(tid,str(template),'PARA',overwrite=True)
    summary['stats']=service.stats()
    output=workspace/'acceptance.json'
    output.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True))

if __name__=='__main__':
    main()
