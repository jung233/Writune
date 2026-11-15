"""Opt-in live stdio acceptance; configured credentials stay in WRITUNE_HOME."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from docx import Document
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from writune.config import Settings
from writune.runtime import windowless_python

async def main():
    settings=Settings.load()
    root=settings.allowed_workspaces[0]
    root.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='writune-mcp-live-',dir=root))
    draft=root/'secondary-draft.txt'
    draft.write_text('The results suggest an association between the measured variables, but they do not establish a causal relationship.',encoding='utf-8')
    template=root/'live-template.docx'
    doc=Document();doc.add_heading('Live MCP acceptance',0);doc.add_paragraph('{{WRITUNE:TEXT}}');doc.save(template)
    params=StdioServerParameters(command=windowless_python(),args=['-m','writune','serve'],env=os.environ.copy())
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools=await session.list_tools()
            async def call(name,args):
                result=await session.call_tool(name,args)
                if result.isError:
                    raise RuntimeError('MCP tool failed: '+name)
                return result.structuredContent or json.loads(result.content[0].text)
            submitted=await call('writune_submit',{'source_path':str(draft),'language':'en','scene':'sci_paper','primary_model':settings.en_secondary})
            tid=submitted['task_id']
            for _ in range(100):
                status=await call('writune_status',{'task_id':tid})
                if status['progress']==1:
                    break
                await asyncio.sleep(2)
            result=await call('writune_result',{'task_id':tid})
            assert not result.get('text')
            applied=await call('writune_apply',{'task_id':tid,'target_path':str(template),'placeholder_id':'TEXT','output_path':str(root/'live-polished.docx'),'overwrite':True})
            finished=Document(applied['output_path'])
            assert not any('{{WRITUNE:' in p.text for p in finished.paragraphs)
            summary={'discovered_tools':[t.name for t in tools.tools],'result':result,'docx_output':applied['output_path']}
            (root/'mcp-acceptance.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(json.dumps(summary))

if __name__=='__main__':
    asyncio.run(main())
