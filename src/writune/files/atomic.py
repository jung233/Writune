"""Atomic byte writes with optional workspace authorization and validation."""

from __future__ import annotations

import os
import tempfile
import stat
from pathlib import Path
from typing import Callable

from .workspace import Workspace


def atomic_write(
    path: str | os.PathLike[str],
    data: bytes,
    overwrite: bool = True,
    workspace: Workspace | None = None,
    validator: Callable[[Path], None] | None = None,
) -> Path:
    """Write bytes beside the destination, validate, then atomically publish.

    When ``overwrite`` is false, a hard-link publication gives a no-clobber
    atomic create.  The temporary file is always removed on failure.
    """
    if not isinstance(data, bytes):
        raise TypeError("atomic_write data must be bytes")
    destination = workspace.check(path, write=True) if workspace else Path(path).expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after creating parents, so a symlink introduced in the path is
    # not silently followed outside the authorized roots.
    if workspace:
        checked = workspace.check(destination, write=True)
        if checked != destination:
            destination = checked

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if validator is not None:
            validator(temporary)
        if overwrite and destination.exists() and os.name!='nt':
            os.chmod(temporary,stat.S_IMODE(destination.stat().st_mode))

        # Validate the resolved target again immediately before publication.
        if workspace:
            checked = workspace.check(destination, write=True)
            if checked != destination:
                raise PermissionError("destination changed while writing")
        if overwrite:
            os.replace(temporary, destination)
        else:
            # Same-directory hard links are atomic and fail if destination
            # already exists, unlike an exists-check followed by replacement.
            os.link(temporary, destination)
            temporary.unlink()
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except (OSError, TypeError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
        return destination
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
