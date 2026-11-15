from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from writune.service import Service

from .mock_responses import MockResponsesServer, make_settings


_SUBMITTER = """
import json
import sys
from writune.config import Settings
from writune.service import Service

settings = Settings.model_validate(json.loads(sys.argv[1]))
task = Service(settings, start_worker=True).submit(
    text=sys.argv[2],
    primary_model=sys.argv[3],
    secondary_model=sys.argv[4],
)
print(json.dumps(task))
"""


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied means the process exists but cannot be queried.
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            return bool(get_exit_code(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            close_handle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for(predicate, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.025)
    return bool(predicate())


@pytest.mark.parametrize(
    ("failed_models", "primary", "secondary", "expected_outcome"),
    [
        (set(), "mock/primary", "mock/secondary", "polished"),
        (
            {"primary-fail", "secondary"},
            "mock/primary-fail",
            "mock/secondary",
            "original_fallback",
        ),
    ],
)
def test_lazy_subprocess_worker_survives_submitter_exit_and_applies_result(
    tmp_path: Path,
    failed_models: set[str],
    primary: str,
    secondary: str,
    expected_outcome: str,
) -> None:
    with MockResponsesServer(fail_models=failed_models, hold_all=True) as server:
        settings = make_settings(
            tmp_path,
            server.url,
            retry_count=1,
            worker_idle_seconds=0.15,
            lease_seconds=5,
            max_concurrency=1,
        )
        settings_data = settings.model_dump(mode="json")
        for name, provider in settings.providers.items():
            settings_data["providers"][name]["api_key"] = provider.api_key.get_secret_value()

        workspace = settings.allowed_workspaces[0]
        target = workspace / "manuscript.md"
        original = (
            "GHRH was associated with TP53 (p = 0.03; 95% CI: 1.2–2.1) "
            "in 42 samples [3, 4]."
        )
        target.write_text("Before {{WRITUNE:SECTION_1}} after\n", encoding="utf-8")
        env = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (source_root, env.get("PYTHONPATH", "")) if part
        )

        submitter = subprocess.run(
            [
                sys.executable,
                "-c",
                _SUBMITTER,
                json.dumps(settings_data),
                original,
                primary,
                secondary,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert submitter.returncode == 0, submitter.stderr
        task = json.loads(submitter.stdout.strip())
        task_id = task["task_id"]

        service = Service(settings, start_worker=False)
        # The provider holds the response open until after the short-lived
        # submitter has exited. The request therefore belongs to the detached
        # worker and remains active beyond the submit call's process lifetime.
        assert server.wait_for_requests(1, timeout=8), service.db.get(task_id)
        assert service.db.get(task_id)["status"] == "running"
        with service.db.connect() as connection:
            worker_row = connection.execute(
                "SELECT pid, owner FROM workers WHERE name='main'"
            ).fetchone()
        assert worker_row is not None
        assert worker_row["pid"] is not None
        assert _pid_is_running(worker_row["pid"])

        request_body = json.dumps(server.requests[0]["body"], ensure_ascii=False)
        assert "GHRH" not in request_body
        assert "TP53" not in request_body
        assert "0.03" not in request_body
        assert "42 samples" not in request_body
        assert "⟦WTN-" in request_body

        server.release()
        assert _wait_for(lambda: service.db.get(task_id)["status"] == "completed")
        result = service.result(task_id, inline_text=True)
        assert result["outcome"] == expected_outcome
        assert result["text"] == original
        assert result["fallback_used"] is (expected_outcome == "original_fallback")
        if expected_outcome == "original_fallback":
            assert result["error"] is not None
            assert result["error"]["attempts"] == 2
            assert Counter(request["model"] for request in server.requests) == {
                "primary-fail": 1,
                "secondary": 1,
            }
        else:
            assert Counter(request["model"] for request in server.requests) == {"primary": 1}

        applied = service.apply(task_id, target, "SECTION_1")
        applied_text = Path(applied["output_path"]).read_text(encoding="utf-8")
        assert applied_text == f"Before {original} after\n"
        assert _wait_for(
            lambda: not _pid_is_running(worker_row["pid"])
            or _worker_reservation_released(service),
            timeout=5,
        )


def _worker_reservation_released(service: Service) -> bool:
    with service.db.connect() as connection:
        return (
            connection.execute(
                "SELECT COUNT(*) FROM workers WHERE name='main'"
            ).fetchone()[0]
            == 0
        )
