import asyncio
import sys
from pathlib import Path
import pytest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from tests.mock_responses import MockResponsesServer,make_settings
from writune.runtime import windowless_python

@pytest.mark.asyncio
async def test_actual_stdio_batch_concurrency_and_markdown_merge(tmp_path):
    with MockResponsesServer(response_delay=.1) as mock:
        settings=make_settings(tmp_path,mock.url);settings.save()
        workspace=settings.allowed_workspaces[0]
        source=workspace/'draft.md'
        original=b'\xef\xbb\xbfFirst paragraph.\r\n\r\nSecond paragraph.\r\n'
        source.write_bytes(original)
        parameters=StdioServerParameters(command=windowless_python(),args=['-m','writune','serve','--home',str(settings.home_dir)])
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                batch=await session.call_tool('writune_submit_batch',{'markdown_path':str(source),'max_concurrency':1})
                assert not batch.isError
                bid=batch.structuredContent['batch_id']
                async with asyncio.timeout(15):
                    while True:
                        status=await session.call_tool('writune_status',{'batch_id':bid})
                        assert not status.isError
                        if status.structuredContent['progress']==1:
                            break
                        await asyncio.sleep(.1)
                output=source.with_name('merged.md')
                merged=await session.call_tool('writune_merge_batch',{'batch_id':bid,'output_path':str(output)})
                assert not merged.isError and merged.structuredContent['paragraph_count']==2
                assert output.read_bytes()==original
                assert mock.peak_active==1
