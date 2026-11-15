"""Apply a Writune result to a controlled target file."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic import atomic_write
from .docx import replace_docx_placeholder
from .text import DecodedText, decode_text, dominant_newline, replace_newlines
from .workspace import Workspace
from .temporary import temporary_root

_SUPPORTED = {".txt", ".md", ".tex", ".docx"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, workspace: Workspace | None) -> bytes:
    checked = workspace.check(path, write=False) if workspace else path.resolve(strict=False)
    return checked.read_bytes()


def _manifest_path(output_path: Path, target_path: Path, placeholder_id: str, workspace: Workspace | None = None) -> Path:
    identity = hashlib.sha256(
        (str(output_path) + "\0" + str(target_path) + "\0" + placeholder_id).encode("utf-8")
    ).hexdigest()
    root = workspace.task_root if workspace else temporary_root()
    return root / 'apply-records' / f'{identity}.json'


def _already_applied(
    manifest_path: Path,
    target_path: Path,
    output_path: Path,
    target_digest: str,
    placeholder_id: str,
    result_digest: str,
    workspace: Workspace | None,
) -> dict | None:
    try:
        manifest_bytes = _read(manifest_path, workspace)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        actual_output = _read(output_path, workspace)
    except (FileNotFoundError, PermissionError, OSError, ValueError, UnicodeDecodeError):
        return None
    if (
        manifest.get("target_path") == str(target_path)
        and manifest.get("output_path") == str(output_path)
        and manifest.get("placeholder_id") == placeholder_id
        and manifest.get("result_sha256") == result_digest
        and manifest.get("output_sha256") == _sha256(actual_output)
        and (
            target_digest == manifest.get("output_sha256")
            if target_path == output_path
            else target_digest == manifest.get("source_sha256")
        )
    ):
        return {
            "target_path": str(target_path),
            "output_path": str(output_path),
            "placeholder_id": placeholder_id,
            "output_sha256": manifest["output_sha256"],
            "idempotent": True,
        }
    return None


def _text_replacement(target_data: bytes, result_data: bytes, placeholder: str, target_encoding=None) -> bytes:
    original = decode_text(target_data)
    if target_encoding and not original.bom:
        original=DecodedText(target_data.decode(target_encoding),target_encoding,b'')
    result = decode_text(result_data)
    count = original.text.count(placeholder)
    if count != 1:
        raise ValueError(f"expected exactly one placeholder; found {count}")
    insertion = replace_newlines(result.text, dominant_newline(original.text))
    updated = original.text.replace(placeholder, insertion, 1)
    return original.encode(updated)


def _validated_docx(path: Path):
    from docx import Document

    try:
        Document(path)
    except Exception as exc:
        raise ValueError("generated DOCX failed validation") from exc


def apply_result(
    result_path: str | Path,
    target_path: str | Path,
    placeholder_id: str,
    output_path: str | Path | None = None,
    overwrite: bool = False,
    workspace: Workspace | None = None,
    target_encoding: str | None = None,
) -> dict:
    """Insert a result at exactly one placeholder and atomically publish it.

    Existing destinations are preserved unless ``overwrite=True``.  A verified
    prior apply returns its existing digest, making retries safe, including
    in-place replacement.
    """
    if not isinstance(placeholder_id, str) or not placeholder_id or "{{" in placeholder_id or "}}" in placeholder_id:
        raise ValueError("placeholder_id must be a non-empty identifier")
    raw_target = Path(target_path).expanduser()
    target = workspace.check(raw_target, write=False) if workspace else raw_target.resolve(strict=False)
    raw_result = Path(result_path).expanduser()
    result = workspace.check(raw_result, write=False) if workspace else raw_result.resolve(strict=False)
    if not target.is_file():
        raise FileNotFoundError(target)
    if not result.is_file():
        raise FileNotFoundError(result)

    suffix = target.suffix.lower()
    if suffix not in _SUPPORTED:
        raise ValueError(f"unsupported target file type: {target.suffix}")
    if output_path is None:
        identity = hashlib.sha256(str(target).encode()).hexdigest()[:16]
        output_root = result.parent if workspace and result.is_relative_to(workspace.task_root) else temporary_root() / 'applied'
        output = output_root / f"{target.stem}_polished-{identity}{target.suffix}"
    else:
        raw_output = Path(output_path).expanduser()
        output = workspace.check(raw_output, write=True) if workspace else raw_output.resolve(strict=False)
    if output.suffix.lower() != suffix:
        raise ValueError("output file type must match target file type")
    if workspace:
        output = workspace.check(output, write=True)

    target_data = _read(target, workspace)
    result_data = _read(result, workspace)
    result_digest = _sha256(result_data)
    marker = f"{{{{WRITUNE:{placeholder_id}}}}}"
    manifest_path = _manifest_path(output, target, placeholder_id, workspace)

    # Check a durable apply record before looking for a marker: with in-place
    # replacement the marker has already been removed by the first call.
    repeated = _already_applied(
        manifest_path,
        target,
        output,
        _sha256(target_data),
        placeholder_id,
        result_digest,
        workspace,
    )
    if repeated is not None:
        return repeated

    try:
        if suffix == ".docx":
            output_data = replace_docx_placeholder(target_data, marker, decode_text(result_data).text)
            validator = _validated_docx
        else:
            output_data = _text_replacement(target_data, result_data, marker,target_encoding)
            validator = None
    except ValueError:
        # If publication succeeded but the metadata write was interrupted, a
        # matching output digest can still prove the result is already applied.
        if output.is_file() and output != target:
            try:
                existing = _read(output, workspace)
                if suffix == ".docx":
                    expected = replace_docx_placeholder(target_data, marker, decode_text(result_data).text)
                    same = _sha256(existing) == _sha256(expected)
                else:
                    same = existing == _text_replacement(target_data, result_data, marker,target_encoding)
                if same:
                    return {
                        "target_path": str(target),
                        "output_path": str(output),
                        "placeholder_id": placeholder_id,
                        "output_sha256": _sha256(existing),
                        "idempotent": True,
                    }
            except (ValueError, OSError):
                pass
        raise

    if output == target and not overwrite:
        raise FileExistsError(f"refusing to overwrite target without overwrite=True: {target}")
    if output.exists() and not overwrite:
        # A failed metadata write may leave the already-correct deterministic
        # text output in place; accept it only after comparing the full bytes.
        if output.read_bytes() == output_data:
            digest = _sha256(output_data)
            _write_manifest(
                manifest_path,
                target,
                output,
                placeholder_id,
                _sha256(target_data),
                result_digest,
                digest,
                workspace,
            )
            return {
                "target_path": str(target),
                "output_path": str(output),
                "placeholder_id": placeholder_id,
                "output_sha256": digest,
                "idempotent": True,
            }
        raise FileExistsError(f"output already exists: {output}")

    output_digest = _sha256(output_data)
    # Commit the proof first. It only counts after the output bytes match its
    # digest, so an interrupted publication cannot be mistaken for success.
    _write_manifest(
        manifest_path,
        target,
        output,
        placeholder_id,
        _sha256(target_data),
        result_digest,
        output_digest,
        workspace,
    )

    def validate_candidate(candidate: Path) -> None:
        if _sha256(_read(target, workspace)) != _sha256(target_data):
            raise RuntimeError("target changed while applying the result")
        if validator is not None:
            validator(candidate)

    atomic_write(
        output,
        output_data,
        overwrite=overwrite,
        workspace=workspace,
        validator=validate_candidate,
    )
    return {
        "target_path": str(target),
        "output_path": str(output),
        "placeholder_id": placeholder_id,
        "output_sha256": output_digest,
        "idempotent": False,
    }


def _write_manifest(
    manifest_path: Path,
    target_path: Path,
    output_path: Path,
    placeholder_id: str,
    source_digest: str,
    result_digest: str,
    output_digest: str,
    workspace: Workspace | None,
) -> None:
    data = {
        "target_path": str(target_path),
        "output_path": str(output_path),
        "placeholder_id": placeholder_id,
        "source_sha256": source_digest,
        "result_sha256": result_digest,
        "output_sha256": output_digest,
    }
    atomic_write(
        manifest_path,
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        overwrite=True,
        workspace=workspace,
    )
