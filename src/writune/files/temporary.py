"""User-scoped scratch storage below the operating system temporary directory."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def temporary_root() -> Path:
    identity = hashlib.sha256(os.path.normcase(str(Path.home().resolve())).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()).resolve() / f"writune-{identity}"


def task_directory(database: Path) -> Path:
    identity = hashlib.sha256(os.path.normcase(str(database.resolve())).encode()).hexdigest()[:16]
    return temporary_root() / identity / "tasks"
