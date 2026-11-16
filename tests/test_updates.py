"""HTTP update integration tests use an in-process server and no Git executable."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import zipfile

import pytest
from writune import updates
from writune.tasks.process_lock import ProcessLock

def manifest(package, compatibility=None, version='0.1.0'):
    value = {'schema':1, 'version':version, 'compatibility':compatibility or {'dependencies':[]},
             'files':updates.package_hashes(package)}
    (package / updates.MANIFEST).write_text(json.dumps(value), encoding='utf-8')
    return value

def zip_package(package, version='0.1.1'):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                data=path.read_bytes()
                if path.suffix=='.py':
                    data=data.replace(b'\r\n',b'\n')
                archive.writestr('writune/'+path.relative_to(package).as_posix(),data)
        info=f'writune-{version}.dist-info/'
        archive.writestr(info+'METADATA',f'Metadata-Version: 2.4\nName: writune\nVersion: {version}\n')
        archive.writestr(info+'WHEEL','Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info+'RECORD','')
    return output.getvalue()

@pytest.fixture(params=['wheel','editable'])
def installation(tmp_path,monkeypatch,request):
    monkeypatch.setenv('PATH','')
    root=tmp_path/'installation'
    package=root/'src'/'writune' if request.param=='editable' else root/'writune'
    source=Path(__file__).resolve().parents[1]/'src'/'writune'
    shutil.copytree(source,package,ignore=shutil.ignore_patterns('__pycache__','.writune-runtime',updates.MANIFEST))
    if request.param=='editable':
        (root/'pyproject.toml').write_text('[project]\nname="writune"\n')
    else:
        root=package
    manifest(package)
    if request.param=='wheel':
        info=package.parent/'writune-0.1.0.dist-info'
        info.mkdir()
        (info/'METADATA').write_text('Metadata-Version: 2.4\nName: writune\nVersion: 0.1.0\n')
        (info/'RECORD').write_text('writune/service.py,,\n../../../bin/writune,,\n')
    remote=tmp_path/'remote'/'writune'
    shutil.copytree(package,remote)
    state={'version':'0.1.1','archive':zip_package(remote),'requests':[]}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append(self.path)
            metadata={'info':{'version':state['version']},'urls':[{
                'packagetype':'bdist_wheel','filename':f'writune-{state["version"]}-py3-none-any.whl',
                'url':'https://files.pythonhosted.org/packages/writune.whl',
                'digests':{'sha256':state.get('digest_override',hashlib.sha256(state['archive']).hexdigest())},
                'yanked':state.get('yanked',False)}]}
            body=json.dumps(metadata).encode() if self.path=='/metadata' else state['archive']
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    original_download=updates._download
    def download(url,limit=updates.MAX_DOWNLOAD):
        route='/metadata' if url==updates.METADATA_URL else '/archive'
        return original_download(base+route,limit)
    monkeypatch.setattr(updates,'_download',download)
    original_run=subprocess.run
    def only_python(command,**kwargs):
        assert 'python' in Path(command[0]).name.lower(), 'Updater must not launch Git or pip'
        return original_run(command,**kwargs)
    monkeypatch.setattr(updates.subprocess,'run',only_python)
    def publish(suffix='\nHTTP_UPDATE_MARKER=True\n',compatibility=None):
        path=remote/'service.py'
        path.write_text(path.read_text(encoding='utf-8')+suffix,encoding='utf-8')
        manifest(remote,compatibility,version=state['version'])
        state['archive']=zip_package(remote,state['version'])
    yield root,package,remote,state,publish
    server.shutdown();server.server_close();thread.join()

def test_http_update_reuses_original_package_without_git(installation):
    root,package,remote,state,publish=installation
    publish()
    result=updates.check_and_update(root,0)
    assert result['status']=='updated'
    assert result['current_version']=='0.1.1'
    assert 'HTTP_UPDATE_MARKER' in (package/'service.py').read_text()
    assert updates.package_hashes(package)==updates._read(package/updates.MANIFEST)['files']
    assert not list(updates.runtime_dir(root).rglob('service.py'))
    assert updates.check_and_update(root)['status']=='updated'
    assert updates.check_and_update(root,0)['status']=='up_to_date'
    assert len(state['requests'])==3

def test_local_edits_are_preserved(installation):
    root,package,remote,state,publish=installation
    publish()
    path=package/'service.py';path.write_text(path.read_text()+'\n# local edit\n')
    assert updates.check_and_update(root,0)['status']=='local_changes'
    assert not state['requests']
    assert path.read_text().endswith('# local edit\n')

def test_dependency_change_requires_install(installation):
    root,package,remote,state,publish=installation
    before=updates.package_hashes(package)
    publish(compatibility={'dependencies':['new-dependency']})
    assert updates.check_and_update(root,0)['status']=='manual_install_required'
    assert updates.package_hashes(package)==before

@pytest.mark.parametrize('failure',['syntax','checksum'])
def test_bad_candidate_leaves_package_untouched(installation,failure):
    root,package,remote,state,publish=installation
    before=updates.package_hashes(package)
    publish(suffix='\ndef syntax error\n' if failure=='syntax' else '\nVALID=True\n')
    if failure=='checksum':
        (remote/'service.py').write_text('tampered after manifest creation')
        state['archive']=zip_package(remote,state['version'])
    assert updates.check_and_update(root,0)['status']=='check_failed'
    assert updates.package_hashes(package)==before
    assert not updates.pending(root)

def test_apply_failure_rolls_back(installation,monkeypatch):
    root,package,remote,state,publish=installation
    publish()
    before=updates.package_hashes(package)
    original=updates.atomic_write
    failed=False
    def write(path,data,*args,**kwargs):
        nonlocal failed
        if Path(path)==package/'service.py' and not failed:
            failed=True
            raise OSError('simulated write failure')
        return original(path,data,*args,**kwargs)
    monkeypatch.setattr(updates,'atomic_write',write)
    assert updates.check_and_update(root,0)['status']=='check_failed'
    assert failed and updates.package_hashes(package)==before

def test_update_waits_for_active_worker(installation):
    root,package,remote,state,publish=installation
    publish()
    before=updates.package_hashes(package)
    lock=ProcessLock(updates.runtime_dir(root)/'workers'/'test.lock');assert lock.acquire()
    result=[]
    task=threading.Thread(target=lambda:result.append(updates.check_and_update(root,0)));task.start()
    try:
        deadline=time.monotonic()+15
        while not updates.pending(root):
            assert task.is_alive() and time.monotonic()<deadline
            time.sleep(.025)
        assert updates.package_hashes(package)==before
    finally:
        lock.release();task.join(15)
    assert not task.is_alive() and result[0]['status']=='updated'

def test_existing_service_switches_to_updated_package(installation,tmp_path,monkeypatch):
    from writune.config import Settings
    root,package,remote,state,publish=installation
    monkeypatch.setattr(updates,'source_root',lambda:root)
    service=updates.UpdatingService(Settings(home_dir=tmp_path/'home',data_dir=tmp_path/'data',auto_update=False),start_worker=False)
    path=remote/'service.py'
    path.write_text(path.read_text().replace("return {'context_id':cid}","return {'context_id':'http_'+cid}"))
    publish()
    assert service.create_context(language='en')['context_id'].startswith('ctx_')
    assert updates.check_and_update(root,0)['status']=='updated'
    assert service.create_context(language='en')['context_id'].startswith('http_ctx_')

def test_source_detection_without_git_or_owner(installation,monkeypatch):
    root,package,remote,state,publish=installation
    monkeypatch.setattr(updates,'__file__',str(package/'updates.py'))
    assert updates.source_root()==root
    assert not (root/'.git').exists()
    assert not (root/'.writune-owner').exists()

def test_checker_is_single_flight_and_stale_intent_is_ignored(tmp_path):
    lock=ProcessLock(updates.runtime_dir(tmp_path)/'checker.lock');assert lock.acquire()
    try:
        assert updates.check_and_update(tmp_path,0)['status']=='not_checked'
    finally:
        lock.release()
    updates._write(updates.runtime_dir(tmp_path)/'pending.json',{'version':'old'})
    assert not updates.pending(tmp_path)

def test_shipped_manifest_matches_package():
    package=Path(__file__).resolve().parents[1]/'src'/'writune'
    assert updates.package_hashes(package)==updates._checked_manifest(updates._read(package/updates.MANIFEST))['files']

def test_text_hashes_accept_cross_platform_newlines_without_weakening_binary_checks():
    assert updates.file_digest('module.py',b'a\r\nb\r\n')==updates.file_digest('module.py',b'a\nb\n')
    assert updates.file_digest('image.bin',b'a\r\n')!=updates.file_digest('image.bin',b'a\n')

def test_launch_without_git_or_owner_is_throttled_and_disableable(tmp_path,monkeypatch):
    from writune.config import Settings
    (tmp_path/updates.MANIFEST).write_text('{}')
    monkeypatch.setattr(updates,'source_root',lambda:tmp_path)
    launches=[]
    monkeypatch.setattr(updates.subprocess,'Popen',lambda *a,**kw:launches.append((a,kw)))
    settings=Settings(auto_update=True,update_check_seconds=60)
    updates.kick(settings)
    assert len(launches)==1
    assert launches[0][1]['stdout']==subprocess.DEVNULL
    updates._write(updates.runtime_dir(tmp_path)/'status.json',{'checked_at':time.time()})
    updates.kick(settings);assert len(launches)==1
    settings.auto_update=False
    updates.kick(settings);assert len(launches)==1

def test_worker_drains_before_claiming_more_tasks(tmp_path,monkeypatch):
    from tests.mock_responses import MockResponsesServer,make_settings
    from writune.service import Service
    from writune.tasks.worker import Worker
    from writune.tasks import lifecycle
    draining=False
    monkeypatch.setattr(updates,'source_root',lambda:None)
    monkeypatch.setattr(updates,'kick',lambda settings:None)
    monkeypatch.setattr(updates,'pending',lambda root:draining)
    restarted=[]
    monkeypatch.setattr(lifecycle,'ensure_worker',lambda *args:restarted.append(True))
    with MockResponsesServer(response_delay=.2) as server:
        settings=make_settings(tmp_path,server.url,max_concurrency=1)
        service=Service(settings,start_worker=False)
        first=service.submit(text='The first sentence is ready for polishing.')['task_id']
        second=service.submit(text='The second sentence is ready for polishing.')['task_id']
        worker=Worker(settings)
        async def run():
            nonlocal draining
            task=asyncio.create_task(worker.run())
            try:
                deadline=time.monotonic()+10
                while service.db.get(first)['status']!='running':
                    assert time.monotonic()<deadline
                    await asyncio.sleep(.01)
                draining=True
                while not getattr(worker,'update_drained',False):
                    assert time.monotonic()<deadline
                    await asyncio.sleep(.01)
                assert service.db.get(first)['status']=='completed'
                assert service.db.get(second)['status']=='queued'
                draining=False
                await asyncio.wait_for(task,5)
            finally:
                draining=False;worker.stop();await asyncio.wait_for(task,5)
        asyncio.run(run())
    assert restarted==[True]


def test_download_hash_mismatch_preserves_installation(installation):
    root,package,remote,state,publish=installation
    publish()
    before=updates.package_hashes(package)
    state['digest_override']='0'*64
    assert updates.check_and_update(root,0)['status']=='check_failed'
    assert updates.package_hashes(package)==before


def test_older_pypi_release_never_downgrades(installation):
    root,package,remote,state,publish=installation
    state['version']='0.0.9'
    before=updates.package_hashes(package)
    assert updates.check_and_update(root,0)['status']=='up_to_date'
    assert updates.package_hashes(package)==before
    assert state['requests']==['/metadata']


def test_update_refreshes_installed_distribution_metadata(installation):
    root,package,remote,state,publish=installation
    publish()
    assert updates.check_and_update(root,0)['status']=='updated'
    if package.parent.name=='src':
        return
    info=package.parent/'writune-0.1.1.dist-info'
    assert 'Version: 0.1.1' in (info/'METADATA').read_text()
    record=(info/'RECORD').read_text()
    assert 'writune/service.py,sha256=' in record
    assert '../../../bin/writune' in record
    assert 'writune-0.1.0.dist-info' not in record
    assert not (package.parent/'writune-0.1.0.dist-info').exists()


def test_candidate_rejects_untrusted_download_origin():
    metadata={'info':{'version':'0.2.0'},'urls':[{
        'packagetype':'bdist_wheel','filename':'writune-0.2.0-py3-none-any.whl',
        'url':'https://untrusted.example/writune.whl','digests':{'sha256':'a'*64}}]}
    with pytest.raises(ValueError,match='Invalid PyPI'):
        updates._candidate(metadata,'0.1.0')


def test_metadata_switch_failure_rolls_back_package_and_metadata(installation,monkeypatch):
    root,package,remote,state,publish=installation
    if package.parent.name=='src':
        pytest.skip('Editable installs do not switch distribution metadata')
    publish()
    before=updates.package_hashes(package)
    original=Path.rename
    def rename(path,target):
        if path.name=='new' and str(target).endswith('writune-0.1.1.dist-info'):
            raise OSError('simulated metadata switch failure')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    assert updates.check_and_update(root,0)['status']=='check_failed'
    assert updates.package_hashes(package)==before
    assert (package.parent/'writune-0.1.0.dist-info'/'METADATA').is_file()
    assert not (package.parent/'writune-0.1.1.dist-info').exists()


def test_yanked_release_requires_manual_review(installation):
    root,package,remote,state,publish=installation
    publish()
    state['yanked']=True
    before=updates.package_hashes(package)
    assert updates.check_and_update(root,0)['status']=='manual_install_required'
    assert updates.package_hashes(package)==before
