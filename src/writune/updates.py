"""HTTP package updates in the original directory; no Git or pip at runtime."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import zipfile
from writune.files.atomic import atomic_write
from writune.runtime import background_options, windowless_python
from writune.tasks.process_lock import ProcessLock

METADATA_URL = 'https://api.github.com/repos/jung233/Writune/commits/main'
ARCHIVE_URL = 'https://codeload.github.com/jung233/Writune/zip/{revision}'
MANIFEST = 'update-manifest.json'
MAX_DOWNLOAD = 32 * 1024 * 1024

def source_root() -> Path:
    package = Path(__file__).resolve().parent
    project = package.parent.parent
    if package.parent.name == 'src' and (project / 'pyproject.toml').is_file():
        return project
    return package

def package_dir(root: Path) -> Path:
    editable = root / 'src' / 'writune'
    return editable if (editable / '__init__.py').is_file() else root

def runtime_dir(root: Path) -> Path:
    return root / '.writune-runtime' / 'updates'

def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _write(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, sort_keys=True).encode('utf-8'))

def file_digest(name: str, data: bytes) -> str:
    # Source archives use LF even when a Windows installation uses CRLF.
    if PurePosixPath(name).suffix in {'.py', '.json', '.txt', '.md', '.yaml', '.yml', '.toml'}:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()

def package_hashes(package: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(package.rglob('*')):
        relative = path.relative_to(package)
        if ('__pycache__' in relative.parts or '.writune-runtime' in relative.parts
                or path.suffix in {'.pyc', '.pyo'} or relative.as_posix() == MANIFEST):
            continue
        if path.is_symlink():
            raise ValueError('Package links cannot be updated automatically')
        if path.is_file():
            hashes[relative.as_posix()] = file_digest(relative.as_posix(), path.read_bytes())
    return hashes

def update_status(root: Path | None = None) -> dict:
    return _read(runtime_dir(root or source_root()) / 'status.json') or {'status': 'not_checked'}

def pending(root: Path | None) -> bool:
    if root is None or not (runtime_dir(root) / 'pending.json').is_file():
        return False
    probe = ProcessLock(runtime_dir(root) / 'checker.lock')
    if probe.acquire():
        probe.release()
        return False
    return True

@contextmanager
def installation_call(root: Path | None):
    if root is None:
        yield
        return
    lock = ProcessLock(runtime_dir(root) / 'calls.lock')
    deadline = time.monotonic() + 120
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError('Writune update is still applying; retry this call')
        time.sleep(.05)
    try:
        yield
    finally:
        lock.release()

def kick(settings) -> None:
    if not settings.auto_update:
        return
    root = source_root()
    try:
        if not (package_dir(root) / MANIFEST).is_file() or pending(root):
            return
        if time.time() - update_status(root).get('checked_at', 0) < settings.update_check_seconds:
            return
        subprocess.Popen(
            [windowless_python(), '-m', 'writune.updates', '--root', str(root),
             '--interval', str(settings.update_check_seconds)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **background_options())
    except (OSError, ValueError):
        return

def _download(url: str, limit: int = MAX_DOWNLOAD) -> bytes:
    request = Request(url, headers={'User-Agent': 'Writune-Updater/1'})
    with urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Update download is too large')
    return data

def _checked_manifest(value: dict) -> dict:
    if value.get('schema') != 1 or not isinstance(value.get('compatibility'), dict):
        raise ValueError('Unsupported update manifest')
    files = value.get('files')
    if not isinstance(files, dict) or not files or MANIFEST in files:
        raise ValueError('Invalid package file manifest')
    for name, digest in files.items():
        if not isinstance(name, str):
            raise ValueError('Invalid package filename')
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name
                or path.as_posix() != name or '.writune-runtime' in path.parts
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Invalid package file entry')
    if not {'__init__.py', 'updates.py', 'service.py', 'tasks/worker.py'} <= files.keys():
        raise ValueError('Incomplete package update')
    return value

def _unpack(data: bytes, stage: Path) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        selected = {}
        total = 0
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = PurePosixPath(member.filename).parts
            if len(parts) < 4 or parts[1:3] != ('src', 'writune'):
                continue
            name = '/'.join(parts[3:])
            if ('..' in parts or ':' in name or '\\' in name or PurePosixPath(name).is_absolute()
                    or name in selected or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe archive entry')
            total += member.file_size
            if total > MAX_DOWNLOAD:
                raise ValueError('Unpacked update is too large')
            selected[name] = member
        if MANIFEST not in selected:
            raise ValueError('Update manifest is missing')
        manifest = _checked_manifest(json.loads(archive.read(selected[MANIFEST])))
        if set(selected) != set(manifest['files']) | {MANIFEST}:
            raise ValueError('Update files do not match the manifest')
        for name, member in selected.items():
            data = archive.read(member)
            if name != MANIFEST and file_digest(name, data) != manifest['files'][name]:
                raise ValueError('Update checksum mismatch')
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return manifest

def _validate(stage: Path) -> None:
    code = (
        'import sys,compileall;sys.path.insert(0,sys.argv[1]);'
        'assert compileall.compile_dir(sys.argv[1],quiet=2);'
        'from writune.mcp_server import create_server;create_server();'
        'from writune.tasks.worker import Worker;'
        'from writune.updates import UpdatingService,worker_registration,pending;'
        'from writune.runtime import worker_command')
    subprocess.run([sys.executable, '-c', code, str(stage.parent)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=45, **background_options())

def _apply(package: Path, stage: Path, old: dict, new: dict) -> None:
    names = set(old['files']) | set(new['files']) | {MANIFEST}
    backup = {name: (package / name).read_bytes() if (package / name).is_file() else None for name in names}
    changed = []
    try:
        for name in sorted(names - {MANIFEST}):
            if name in new['files']:
                atomic_write(package / name, (stage / name).read_bytes())
            else:
                (package / name).unlink(missing_ok=True)
            changed.append(name)
        atomic_write(package / MANIFEST, (stage / MANIFEST).read_bytes())
        changed.append(MANIFEST)
    except BaseException:
        for name in reversed(changed):
            data = backup[name]
            if data is None:
                (package / name).unlink(missing_ok=True)
            else:
                atomic_write(package / name, data)
        raise
    for cache in package.rglob('*.pyc'):
        if '__pycache__' in cache.relative_to(package).parts:
            try:
                cache.unlink()
            except OSError:
                pass

def check_and_update(root: Path, interval: float = 21600) -> dict:
    runtime = runtime_dir(root)
    checker = ProcessLock(runtime / 'checker.lock')
    if not checker.acquire():
        return update_status(root)
    intent = runtime / 'pending.json'
    worker_locks = []
    calls = None
    previous = update_status(root)
    state = {'status': 'checking', 'checked_at': time.time()}
    try:
        if time.time() - previous.get('checked_at', 0) < interval:
            state = previous
            return state
        _write(runtime / 'status.json', state)
        package = package_dir(root)
        old = _checked_manifest(_read(package / MANIFEST))
        if package_hashes(package) != old['files']:
            state['status'] = 'local_changes'
            return state
        metadata = json.loads(_download(METADATA_URL, 1024 * 1024))
        target = metadata.get('sha', '')
        if not isinstance(target, str) or not re.fullmatch('[0-9a-f]{40}', target):
            raise ValueError('Invalid update revision')
        state['available_revision'] = target
        if previous.get('current_revision') == target:
            state.update(status='up_to_date', current_revision=target)
            return state
        with tempfile.TemporaryDirectory(prefix='writune-update-') as folder:
            stage = Path(folder) / 'writune'
            new = _unpack(_download(ARCHIVE_URL.format(revision=target)), stage)
            if old['compatibility'] != new['compatibility']:
                state['status'] = 'manual_install_required'
                return state
            if old['files'] == new['files']:
                state.update(status='up_to_date', current_revision=target)
                return state
            _validate(stage)
            _write(intent, {'revision': target})
            state['status'] = 'draining'
            _write(runtime / 'status.json', state)
            calls = ProcessLock(runtime / 'calls.lock')
            deadline = time.monotonic() + 90
            while not calls.acquire():
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                time.sleep(.1)
            for path in sorted((runtime / 'workers').glob('*.lock')):
                lock = ProcessLock(path)
                while not lock.acquire():
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    time.sleep(.1)
                worker_locks.append(lock)
            if package_hashes(package) != old['files']:
                state['status'] = 'local_changes'
                return state
            _apply(package, stage, old, new)
            state.update(status='updated', current_revision=target, applied_at=time.time())
            return state
    except Exception:
        state['status'] = 'check_failed'
        return state
    finally:
        intent.unlink(missing_ok=True)
        for lock in worker_locks:
            lock.release()
        if calls is not None:
            calls.release()
        try:
            _write(runtime / 'status.json', state)
        finally:
            checker.release()

def worker_registration(settings):
    root = source_root()
    if root is None:
        return None
    key = hashlib.sha256(str(settings.db_path).encode('utf-8')).hexdigest()
    return ProcessLock(runtime_dir(root) / 'workers' / f'{key}.lock')

class UpdatingService:
    """Keep MCP transport alive while later calls use current package files."""
    def __init__(self, settings, start_worker=True):
        from writune.service import Service
        self.settings = settings
        self.start_worker = start_worker
        self.root = source_root()
        self.service = Service(settings, start_worker=start_worker)
        self.fingerprint = self._fingerprint()
        if settings.auto_update and self.root is not None:
            threading.Thread(target=_watch, args=(settings,), daemon=True,
                             name='writune-update-check').start()

    def _fingerprint(self):
        if self.root is None:
            return None
        source = package_dir(self.root)
        return tuple((str(p.relative_to(source)), p.stat().st_mtime_ns, p.stat().st_size)
                     for p in sorted(source.rglob('*.py')))

    def __getattr__(self, name):
        value = getattr(self.service, name)
        if not callable(value):
            return value
        def call(*args, **kwargs):
            kick(self.settings)
            with installation_call(self.root):
                if self._fingerprint() != self.fingerprint:
                    return self._fresh_call(name, args, kwargs)
                return value(*args, **kwargs)
        return call

    def _fresh_call(self, name, args, kwargs):
        payload = self.settings.model_dump(mode='json')
        for key, provider in self.settings.providers.items():
            payload['providers'][key]['api_key'] = provider.api_key.get_secret_value()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('WRITUNE_')}
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        code = (
            'import sys,json;sys.path.insert(0,sys.argv[1]);'
            'from writune.config import Settings;from writune.service import Service;'
            'p=json.load(sys.stdin);s=Service(Settings(**p["settings"]),start_worker=p["worker"]);'
            'r=getattr(s,p["method"])(*p["args"],**p["kwargs"]);'
            'print(json.dumps(r,ensure_ascii=False,default=str))')
        request = dict(settings=payload, worker=self.start_worker, method=name, args=args, kwargs=kwargs)
        result = subprocess.run(
            [windowless_python(), '-c', code, str(package_dir(self.root).parent)],
            input=json.dumps(request, default=str), capture_output=True, text=True,
            encoding='utf-8', env=env, timeout=max(120, self.settings.total_timeout+30),
            **background_options())
        if result.returncode:
            raise RuntimeError('Updated Writune service call failed; original task files retained')
        return json.loads(result.stdout)

def _watch(settings):
    while True:
        time.sleep(settings.update_check_seconds)
        kick(settings)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=21600)
    args = parser.parse_args()
    if source_root() != args.root.resolve():
        raise SystemExit('Update target must be the running installation')
    check_and_update(args.root.resolve(), args.interval)

if __name__ == '__main__':
    main()
