"""Copy legacy artifacts to temporary storage; leave historical originals intact."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from writune.files.atomic import atomic_write


def migrate_legacy_artifacts(settings, db):
    legacy = (settings.data_dir / 'tasks').resolve()
    destination = settings.task_dir.resolve()
    if not legacy.is_dir() or legacy == destination:
        return
    # Claiming tasks also takes this transaction; running tasks are left intact.
    with db.connect(immediate=True) as connection:
        rows = connection.execute("SELECT * FROM tasks WHERE status!='running'").fetchall()
        for row in rows:
            tid = row['task_id']
            if not re.fullmatch(r'[0-9a-f]{32}', tid):
                continue
            old = legacy / tid
            if old.is_symlink() or old.resolve().parent != legacy or not old.is_dir():
                continue
            if Path(row['original_path']).resolve() != old.resolve() / 'original.txt':
                continue
            new = destination / tid
            if new.is_symlink() or new.resolve().parent != destination:
                raise PermissionError('Unsafe temporary task destination')
            shutil.copytree(old, new, dirs_exist_ok=True, symlinks=True)
            updates = {}
            for field in ('original_path', 'result_path', 'manifest_path'):
                if row[field] and Path(row[field]).resolve().parent == old.resolve():
                    updates[field] = str(new / Path(row[field]).name)
            manifest = new / 'result.json'
            if manifest.is_file() and not manifest.is_symlink():
                content = json.loads(manifest.read_text(encoding='utf-8'))
                if updates.get('result_path'):
                    content['result_path'] = updates['result_path']
                atomic_write(manifest,json.dumps(content,ensure_ascii=False,indent=2).encode(),overwrite=True)
            connection.execute('UPDATE tasks SET '+','.join(name+'=?' for name in updates)+' WHERE task_id=?',(*updates.values(),tid))
