import asyncio
from pathlib import Path

import pytest

from tests.mock_responses import make_settings
from writune.service import Service
from writune.tasks.worker import Worker
from writune.providers.errors import ProviderError


def test_large_context_warns_without_rejecting_task(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    small=service.submit(text='A short passage.')
    assert small['estimated_input_tokens']>0
    assert not small['warnings']
    context=service.create_context(context_summary='中'*200_001)
    large=service.submit(text='A short passage.',context_id=context['context_id'])
    assert large['estimated_input_tokens']>=200_000
    assert large['token_count_estimated']
    assert 'POSSIBLE_CONTEXT_WINDOW_EXCEEDED' in large['warnings'][0]
    assert service.db.get(large['task_id'])['status']=='queued'


def test_batch_context_warning_uses_individual_request_not_sum(tmp_path):
    service=Service(make_settings(tmp_path,'http://127.0.0.1:9/v1'),start_worker=False)
    small_batch=service.submit_batch(items=['中'*105_000,'文'*105_000])
    assert small_batch['estimated_input_tokens']<200_000
    assert not small_batch['warnings']
    large_batch=service.submit_batch(items=['中'*200_001,'Short passage.'])
    assert large_batch['accepted_count']==2
    assert large_batch['estimated_input_tokens']>=200_000
    assert large_batch['task_ids'][0] in large_batch['warnings'][0]


@pytest.mark.parametrize('code',['REPETITIVE_OUTPUT','OUTPUT_LIMIT_EXCEEDED'])
def test_guard_keeps_partial_output_for_review_without_retry(tmp_path,monkeypatch,code):
    settings=make_settings(tmp_path,'http://127.0.0.1:9/v1')
    service=Service(settings,start_worker=False)
    submitted=service.submit(text='Original passage with 42 samples.')
    class GuardedProvider:
        last_metrics={}
        async def generate(self,*args):
            error=ProviderError(code,'Stopped for review',False)
            error.partial_text='Partial repeated prose.'
            error.partial_reasoning='Observed reasoning.'
            raise error
    monkeypatch.setattr('writune.tasks.worker.get_provider',lambda *args,**kwargs:GuardedProvider())
    worker=Worker(settings,owner='review-test')
    row=service.db.claim(worker.owner,settings)
    asyncio.run(worker.execute(row))
    result=service.result(submitted['task_id'])
    assert result['review_required']
    assert result['outcome']=='original_fallback'
    assert Path(result['partial_output_path']).read_text()=='Partial repeated prose.'
    assert Path(result['partial_reasoning_path']).read_text()=='Observed reasoning.'
    assert Path(result['result_path']).read_text()=='Original passage with 42 samples.'
    assert result['error']['attempts']==1
    assert 'text' not in result
