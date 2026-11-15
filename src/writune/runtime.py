"""Windowless Windows subprocess entry points, shared by workers and MCP checks."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def windowless_python(executable=None):
    python = Path(executable or sys.executable)
    if os.name == 'nt':
        candidate = python.with_name('pythonw.exe')
        if candidate.is_file():
            return str(candidate)
    return str(python)


def background_options():
    if os.name != 'nt':
        return {'start_new_session': True}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {
        'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        'startupinfo': startup,
    }
