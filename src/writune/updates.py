"""HTTP package updates in the original directory; no Git or pip at runtime."""
from __future__ import annotations
import argparse
import base64
import csv
from email.parser import BytesParser
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
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from writune.files.atomic import atomic_write
from writune.runtime import background_options, windowless_python
from writune.tasks.process_lock import ProcessLock

METADATA_URL = 'https://pypi.org/pypi/writune/json'
MANIFEST = 'update-manifest.json'
MAX_DOWNLOAD = 32 * 1024 * 1024


class ManualInstallRequired(ValueError):
    """A published release needs a normal package installation."""


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

def _candidate(metadata: dict, current: str) -> tuple[str, dict | None]:
    target = str(metadata['info']['version'])
    version = Version(target)
    if version.is_prerelease or version.is_devrelease or version <= Version(current):
        return target, None
    for item in metadata.get('urls', []):
        if (item.get('packagetype') != 'bdist_wheel' or item.get('yanked')
                or item.get('filename') != f'writune-{version}-py3-none-any.whl'):
            continue
        requirement = item.get('requires_python') or metadata['info'].get('requires_python')
        if requirement and not SpecifierSet(requirement).contains('.'.join(map(str, sys.version_info[:3]))):
            continue
        url = urlsplit(item.get('url', ''))
        digest = item.get('digests', {}).get('sha256', '')
        if (url.scheme != 'https' or url.hostname != 'files.pythonhosted.org'
                or url.username or url.password or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Invalid PyPI download metadata')
        return target, item
    raise ManualInstallRequired('No compatible stable universal wheel is available')


def _unpack(data: bytes, stage: Path, version: str) -> dict:
    """Extract a verified pure-Python wheel; never execute wheel installation hooks."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        selected = {}
        metadata_files = {}
        total = 0
        info_prefix = f'writune-{Version(version)}.dist-info/'
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = member.filename
            parts = PurePosixPath(name).parts
            if (not name or '..' in parts or ':' in name or '\\' in name
                    or PurePosixPath(name).is_absolute() or '/'.join(parts) != name
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe wheel entry')
            if name.startswith('writune/'):
                target = selected
                relative = name[len('writune/'):]
            elif name.startswith(info_prefix):
                target = metadata_files
                relative = name[len(info_prefix):]
            else:
                raise ManualInstallRequired('Wheel layout requires a normal package installation')
            if relative in target or not relative:
                raise ValueError('Duplicate wheel entry')
            total += member.file_size
            if total > MAX_DOWNLOAD:
                raise ValueError('Unpacked update is too large')
            target[relative] = member
        if MANIFEST not in selected or not {'METADATA', 'WHEEL', 'RECORD'} <= metadata_files.keys():
            raise ValueError('Wheel metadata or update manifest is missing')
        wheel = BytesParser().parsebytes(archive.read(metadata_files['WHEEL']))
        core = BytesParser().parsebytes(archive.read(metadata_files['METADATA']))
        if (wheel.get('Root-Is-Purelib', '').lower() != 'true'
                or not wheel.get('Wheel-Version', '').startswith('1.')
                or core.get('Name', '').lower() != 'writune'
                or Version(core.get('Version', '')) != Version(version)):
            raise ValueError('Incompatible wheel metadata')
        manifest = _checked_manifest(json.loads(archive.read(selected[MANIFEST])))
        if Version(manifest['version']) != Version(version):
            raise ValueError('Wheel and update manifest versions disagree')
        if set(selected) != set(manifest['files']) | {MANIFEST}:
            raise ValueError('Update files do not match the manifest')
        for name, member in selected.items():
            payload = archive.read(member)
            if name != MANIFEST and file_digest(name, payload) != manifest['files'][name]:
                raise ValueError('Update checksum mismatch')
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        for name, member in metadata_files.items():
            if name in {'RECORD.jws', 'RECORD.p7s'}:
                continue
            path = stage.parent / 'dist-info' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))
    return manifest


def _sync_distribution_metadata(package: Path, stage: Path, old: dict, new: dict) -> None:
    """Keep pip/importlib metadata and uninstall records aligned with an in-place update."""
    if package.parent.name == 'src':
        return  # Editable project metadata is managed by its source build.
    distributions = []
    for path in package.parent.glob('writune-*.dist-info'):
        if path.is_symlink() or not (path / 'METADATA').is_file():
            continue
        core = BytesParser().parsebytes((path / 'METADATA').read_bytes())
        if core.get('Name', '').lower() == 'writune':
            distributions.append((path, core))
    if not distributions:
        return  # Standalone source copies have no installed distribution metadata.
    if len(distributions) != 1:
        raise ManualInstallRequired('Multiple installed Writune distributions require pip repair')
    previous, core = distributions[0]
    if Version(core['Version']) != Version(old['version']):
        raise ManualInstallRequired('Installed distribution metadata does not match the package')
    destination = package.parent / f'writune-{Version(new["version"])}.dist-info'
    if destination.exists():
        raise ManualInstallRequired('Target distribution metadata already exists')
    staged_metadata = stage.parent / 'dist-info'
    retained = []
    record = previous / 'RECORD'
    if not record.is_file():
        raise ManualInstallRequired('The installed distribution has no uninstall record')
    if record.is_file():
        for row in csv.reader(io.StringIO(record.read_text(encoding='utf-8'))):
            if row and not row[0].startswith(('writune/', previous.name + '/')):
                retained.append(row)
    with tempfile.TemporaryDirectory(prefix='.writune-metadata-', dir=package.parent, ignore_cleanup_errors=True) as folder:
        work = Path(folder)
        prepared = work / 'new'
        import shutil
        shutil.copytree(staged_metadata, prepared)
        rows = retained
        for relative in sorted(set(new['files']) | {MANIFEST}):
            payload = (package / relative).read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
            rows.append(['writune/' + relative, 'sha256=' + digest, str(len(payload))])
        for path in sorted(prepared.rglob('*')):
            if path.is_file() and path.name != 'RECORD':
                payload = path.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip('=')
                rows.append([destination.name + '/' + path.relative_to(prepared).as_posix(), 'sha256=' + digest, str(len(payload))])
        rows.append([destination.name + '/RECORD', '', ''])
        output = io.StringIO(newline='')
        csv.writer(output).writerows(rows)
        (prepared / 'RECORD').write_text(output.getvalue(), encoding='utf-8', newline='')
        backup = work / 'old'
        previous.rename(backup)
        try:
            prepared.rename(destination)
        except BaseException:
            backup.rename(previous)
            raise

def _validate(stage: Path) -> None:
    code = (
        'import sys,compileall;sys.path.insert(0,sys.argv[1]);'
        'assert compileall.compile_dir(sys.argv[1],quiet=2);'
        'from writune.mcp_server import create_server;create_server();'
        'from writune.tasks.worker import Worker;'
        'from writune.updates import UpdatingService,worker_registration,pending;'
        'from writune.runtime import worker_command;from writune.cli import app')
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
        _sync_distribution_metadata(package, stage, old, new)
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
        state['installed_version'] = old['version']
        metadata = json.loads(_download(METADATA_URL, 1024 * 1024))
        target, candidate = _candidate(metadata, old['version'])
        state['available_version'] = target
        if candidate is None:
            state.update(status='up_to_date', current_version=old['version'])
            return state
        with tempfile.TemporaryDirectory(prefix='writune-update-') as folder:
            stage = Path(folder) / 'writune'
            wheel = _download(candidate['url'])
            if hashlib.sha256(wheel).hexdigest() != candidate['digests']['sha256']:
                raise ValueError('PyPI wheel checksum mismatch')
            new = _unpack(wheel, stage, target)
            if old['compatibility'] != new['compatibility']:
                state['status'] = 'manual_install_required'
                return state
            if old['files'] == new['files']:
                state.update(status='up_to_date', current_version=old['version'])
                return state
            _validate(stage)
            _write(intent, {'version': target})
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
            state.update(status='updated', current_version=target, applied_at=time.time())
            return state
    except ManualInstallRequired as exc:
        state['status'] = 'manual_install_required'
        state['reason'] = str(exc)
        return state
    except Exception as exc:
        state['status'] = 'check_failed'
        state['error_code'] = type(exc).__name__
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
