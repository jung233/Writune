from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from writune.files import Workspace, apply_result, atomic_write


def _workspace(tmp_path: Path) -> Workspace:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    user_root = tmp_path / "project"
    user_root.mkdir()
    return Workspace(allowed_roots=[user_root], task_root=task_root)


def test_workspace_rejects_traversal_and_unauthorized_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(PermissionError):
        workspace.check("../outside.txt")
    with pytest.raises(PermissionError):
        workspace.check(tmp_path / "secret.txt")
    with pytest.raises(PermissionError):
        workspace.check(tmp_path / "secret.txt", write=True)


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace.allowed_roots[0] / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PermissionError):
        workspace.check(link / "secret.txt")
    with pytest.raises(PermissionError):
        workspace.check(link / "new.txt", write=True)


def test_workspace_rejects_canonicalized_junction_escape(tmp_path: Path, monkeypatch) -> None:
    """Exercise canonical-path enforcement without requiring OS symlink rights."""
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    junction = workspace.allowed_roots[0] / "junction"
    alias = junction / "secret.txt"
    real_canonical = Path.resolve

    def resolve_with_junction(path: Path, strict: bool = False) -> Path:
        try:
            relative = path.relative_to(junction)
        except ValueError:
            pass
        else:
            return outside / relative
        return real_canonical(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_junction)
    with pytest.raises(PermissionError):
        workspace.check(alias)
    with pytest.raises(PermissionError):
        workspace.check(alias, write=True)


def test_atomic_write_is_no_clobber_when_overwrite_is_false(tmp_path: Path) -> None:
    target = tmp_path / "value.bin"
    atomic_write(target, b"first", overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_write(target, b"second", overwrite=False)
    assert target.read_bytes() == b"first"


def test_atomic_write_no_clobber_is_race_safe(tmp_path: Path) -> None:
    target = tmp_path / "raced.bin"
    barrier = Barrier(2)

    def publish(value: bytes) -> str:
        barrier.wait()
        try:
            atomic_write(target, value, overwrite=False)
            return "published"
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, (b"left", b"right")))

    assert sorted(outcomes) == ["exists", "published"]
    assert target.read_bytes() in {b"left", b"right"}


def test_atomic_write_validates_before_replacing(tmp_path: Path) -> None:
    target = tmp_path / "value.txt"
    target.write_bytes(b"old")

    def reject(_path: Path) -> None:
        raise ValueError("invalid candidate")

    with pytest.raises(ValueError, match="invalid candidate"):
        atomic_write(target, b"new", overwrite=True, validator=reject)
    assert target.read_bytes() == b"old"


def test_atomic_write_checks_workspace_before_creating(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    unauthorized = tmp_path / "secret.bin"
    with pytest.raises(PermissionError):
        atomic_write(unauthorized, b"private", workspace=workspace)
    assert not unauthorized.exists()


@pytest.mark.parametrize(
    ("suffix", "prefix", "encoding"),
    [
        (".txt", b"\xef\xbb\xbf", "utf-8"),
        (".md", b"", "utf-8"),
        (".tex", b"", "utf-8"),
    ],
)
def test_text_apply_preserves_bom_and_newlines(
    tmp_path: Path, suffix: str, prefix: bytes, encoding: str
) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / f"draft{suffix}"
    result = workspace.task_root / "result.txt"
    marker = "{{WRITUNE:SECTION_1}}"
    target.write_bytes(prefix + f"Before\r\n{marker}\r\nAfter\r\n".encode(encoding))
    result.write_text("Polished first line\nSecond line", encoding="utf-8")

    applied = apply_result(result, target, "SECTION_1", workspace=workspace)
    output = Path(applied["output_path"])
    data = output.read_bytes()

    assert data.startswith(prefix)
    assert b"Before\r\nPolished first line\r\nSecond line\r\nAfter\r\n" in data
    assert marker.encode(encoding) not in data

def test_legacy_unrepresentable_replacement_is_rejected_without_transcoding(tmp_path):
    workspace=_workspace(tmp_path)
    target=workspace.allowed_roots[0]/'legacy.txt'
    original=b'caf\xe9 {{WRITUNE:X}}\r\n'
    target.write_bytes(original)
    result=workspace.task_root/'result.txt'
    result.write_text('中文',encoding='utf-8')
    with pytest.raises(ValueError,match='target encoding'):
        apply_result(result,target,'X',workspace=workspace)
    assert target.read_bytes()==original
    assert not target.with_name('legacy_polished.txt').exists()

@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows junction check')
def test_actual_windows_junction_cannot_escape_workspace(tmp_path):
    import os
    import subprocess
    workspace=_workspace(tmp_path)
    outside=tmp_path/'external-junction-target'
    outside.mkdir()
    secret=outside/'private.txt'
    secret.write_text('outside workspace',encoding='utf-8')
    link=workspace.allowed_roots[0]/'junction'
    result=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        pytest.skip('This filesystem does not allow directory junction creation')
    try:
        with pytest.raises(PermissionError):
            workspace.check(link/'private.txt')
        with pytest.raises(PermissionError):
            workspace.check(link/'new.txt',write=True)
    finally:
        # Remove only the junction entry, never recurse through its target.
        os.rmdir(link)
    assert secret.read_text(encoding='utf-8')=='outside workspace'


def test_apply_rejects_missing_or_duplicate_placeholders(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.md"
    result = workspace.task_root / "result.txt"
    result.write_text("Done", encoding="utf-8")

    target.write_text("nothing here", encoding="utf-8")
    with pytest.raises(ValueError, match="found 0"):
        apply_result(result, target, "MISSING", workspace=workspace)

    target.write_text("{{WRITUNE:X}} and {{WRITUNE:X}}", encoding="utf-8")
    with pytest.raises(ValueError, match="found 2"):
        apply_result(result, target, "X", workspace=workspace)


def test_apply_is_idempotent_and_does_not_overwrite_existing_output(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.md"
    result = workspace.task_root / "result.txt"
    target.write_text("A {{WRITUNE:X}} B\n", encoding="utf-8")
    result.write_text("polished", encoding="utf-8")

    first = apply_result(result, target, "X", workspace=workspace)
    second = apply_result(result, target, "X", workspace=workspace)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert Path(first["output_path"]).read_text(encoding="utf-8") == "A polished B\n"

    target.write_text("Updated {{WRITUNE:X}} B\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply_result(result, target, "X", workspace=workspace)

    other = workspace.task_root / "other.txt"
    other.write_text("different", encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply_result(other, target, "X", workspace=workspace)


def test_apply_sidecar_does_not_hide_output_or_result_changes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.md"
    result = workspace.task_root / "result.txt"
    target.write_text("Start {{WRITUNE:X}} end\n", encoding="utf-8")
    result.write_text("first result", encoding="utf-8")

    applied = apply_result(result, target, "X", workspace=workspace)
    output = Path(applied["output_path"])
    first_output = output.read_bytes()
    output.write_bytes(b"edited after apply")
    with pytest.raises(FileExistsError):
        apply_result(result, target, "X", workspace=workspace)
    assert output.read_bytes() == b"edited after apply"

    output.write_bytes(first_output)
    result.write_text("different result", encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply_result(result, target, "X", workspace=workspace)
    assert output.read_bytes() == first_output


def test_in_place_sidecar_does_not_hide_target_or_result_changes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.md"
    result = workspace.task_root / "result.txt"
    target.write_text("{{WRITUNE:X}}", encoding="utf-8")
    result.write_text("first result", encoding="utf-8")
    apply_result(result, target, "X", output_path=target, overwrite=True, workspace=workspace)

    target.write_text("manually changed output", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_result(result, target, "X", output_path=target, overwrite=True, workspace=workspace)
    target.write_text("first result", encoding="utf-8")
    result.write_text("different result", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_result(result, target, "X", output_path=target, overwrite=True, workspace=workspace)


def test_in_place_apply_requires_overwrite_and_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.tex"
    result = workspace.task_root / "result.txt"
    target.write_text("\\section{A}\n{{WRITUNE:X}}\n", encoding="utf-8")
    result.write_text(r"\textbf{polished}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        apply_result(result, target, "X", output_path=target, workspace=workspace)
    first = apply_result(result, target, "X", output_path=target, overwrite=True, workspace=workspace)
    second = apply_result(result, target, "X", output_path=target, overwrite=True, workspace=workspace)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert target.read_text(encoding="utf-8") == "\\section{A}\n\\textbf{polished}\n"


def test_apply_rejects_unauthorized_result_and_output_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = workspace.allowed_roots[0] / "draft.md"
    target.write_text("{{WRITUNE:X}}", encoding="utf-8")
    unauthorized = tmp_path / "secret.txt"
    unauthorized.write_text("secret", encoding="utf-8")
    with pytest.raises(PermissionError):
        apply_result(unauthorized, target, "X", workspace=workspace)
    result = workspace.task_root / "result.txt"
    result.write_text("polished", encoding="utf-8")
    with pytest.raises(PermissionError):
        apply_result(result, target, "X", output_path=unauthorized, workspace=workspace)
