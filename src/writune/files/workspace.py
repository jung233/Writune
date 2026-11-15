"""Workspace path authorization with canonical symlink checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _canonical(path: Path) -> Path:
    """Resolve existing symlinks while allowing the final path not to exist."""
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(root))))
    except ValueError:
        return False
    return common == os.path.normcase(str(root))


@dataclass
class Workspace:
    """An allowlist of user workspaces plus Writune's private task directory.

    Relative paths are resolved from ``task_root``.  The returned path is the
    canonical path, so callers operate on the same destination that was
    authorized even when an input contains symlink components.
    """

    allowed_roots: list[Path]
    task_root: Path
    _roots: tuple[Path, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.task_root = _canonical(Path(self.task_root))
        roots = [_canonical(Path(root)) for root in self.allowed_roots]
        roots.append(self.task_root)
        # Keep insertion order while removing equivalent canonical roots.
        unique: list[Path] = []
        for root in roots:
            if not any(os.path.normcase(str(root)) == os.path.normcase(str(old)) for old in unique):
                unique.append(root)
        self.allowed_roots = unique
        self._roots = tuple(unique)

    def check(self, path: str | os.PathLike[str], write: bool = False) -> Path:
        """Return a canonical authorized path or raise ``PermissionError``.

        Traversal components are rejected even when normalization would happen
        to land back inside an allowed directory.  Symlinks are resolved before
        checking containment, which prevents a path inside a workspace from
        escaping through an existing link.
        """
        raw = os.fspath(path)
        if not isinstance(raw, str):
            raise TypeError("path must be text or path-like text")
        if "\x00" in raw:
            raise ValueError("NUL bytes are not valid in paths")
        if ".." in raw.replace("\\", "/").split("/"):
            raise PermissionError("path traversal is not allowed")

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.task_root / candidate
        canonical = _canonical(candidate)
        if not any(_is_within(canonical, root) for root in self._roots):
            mode = "write" if write else "read"
            raise PermissionError(f"unauthorized {mode} path: {path}")
        return canonical
