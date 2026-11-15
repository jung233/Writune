"""Local-only Responses API server used by queue and worker integration tests."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from writune.config import ProviderConfig, Settings


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def current_paragraph(request: dict[str, Any]) -> str:
    """Return the variable paragraph, retaining the protection placeholders."""

    marker = "[CURRENT PARAGRAPH]"
    for value in _strings(request):
        if marker in value:
            return value.split(marker, 1)[1].lstrip("\r\n")

    # Providers may place the paragraph in a separate input field while using
    # the marker only in instructions. Keep this fallback tolerant of either
    # a string or the Responses API's structured input representation.
    candidates = list(_strings(request.get("input")))
    if candidates:
        return candidates[-1]
    raise AssertionError("mock request did not contain the current paragraph")


class MockResponsesServer:
    """Threaded HTTP mock for POST /v1/responses; it never contacts the network."""

    def __init__(
        self,
        *,
        fail_models: set[str] | None = None,
        hold_all: bool = False,
        hold_first: bool = False,
        response_delay: float = 0,
        model_delays: dict[str, float] | None = None,
        text_delays: dict[str, float] | None = None,
        provider_keys: dict[str, str] | None = None,
    ) -> None:
        self.fail_models = set(fail_models or ())
        self.response_delay = response_delay
        self.model_delays = dict(model_delays or {})
        self.text_delays = dict(text_delays or {})
        self.provider_keys = dict(provider_keys or {})
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._release = threading.Event()
        if not hold_all and not hold_first:
            self._release.set()
        self._hold_all = hold_all
        self._hold_first = hold_first
        self._first_request_id: int | None = None
        self._requests: list[dict[str, Any]] = []
        self._active = 0
        self._peak_active = 0
        self._active_by_provider: Counter[str] = Counter()
        self._peak_by_provider: Counter[str] = Counter()
        self._active_by_model: Counter[str] = Counter()
        self._peak_by_model: Counter[str] = Counter()
        self._active_by_provider_model: Counter[tuple[str, str]] = Counter()
        self._peak_by_provider_model: Counter[tuple[str, str]] = Counter()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                if not self.path.endswith("/responses"):
                    self.send_error(404)
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length).decode("utf-8"))
                    model = str(request.get("model", ""))
                    text = current_paragraph(request)
                    key = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                    provider = outer.provider_keys.get(key, "mock")
                except Exception:
                    self.send_error(400)
                    return

                matching_text_delays = [
                    delay
                    for fragment, delay in outer.text_delays.items()
                    if fragment in text
                ]
                delay = max(
                    [outer.model_delays.get(model, outer.response_delay)]
                    + matching_text_delays
                )
                with outer._condition:
                    request_id = len(outer._requests)
                    request_record = {
                        "model": model,
                        "provider": provider,
                        "body": request,
                        "started_at": time.monotonic(),
                        "finished_at": None,
                    }
                    outer._requests.append(request_record)
                    outer._active += 1
                    outer._active_by_provider[provider] += 1
                    outer._active_by_model[model] += 1
                    outer._active_by_provider_model[(provider, model)] += 1
                    outer._peak_active = max(outer._peak_active, outer._active)
                    outer._peak_by_provider[provider] = max(
                        outer._peak_by_provider[provider],
                        outer._active_by_provider[provider],
                    )
                    outer._peak_by_model[model] = max(
                        outer._peak_by_model[model],
                        outer._active_by_model[model],
                    )
                    pair = (provider, model)
                    outer._peak_by_provider_model[pair] = max(
                        outer._peak_by_provider_model[pair],
                        outer._active_by_provider_model[pair],
                    )
                    if outer._hold_first and outer._first_request_id is None:
                        outer._first_request_id = request_id
                    should_hold = outer._hold_all or (
                        outer._hold_first and request_id == outer._first_request_id
                    )
                    outer._condition.notify_all()

                try:
                    if should_hold:
                        outer._release.wait(timeout=15)
                    if delay:
                        time.sleep(delay)
                    if model in outer.fail_models:
                        payload = json.dumps(
                            {
                                "error": {
                                    "message": "Configured local mock failure",
                                    "type": "server_error",
                                }
                            }
                        ).encode("utf-8")
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        try:
                            self.wfile.write(payload)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return

                    events = [
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_local_mock",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        {"type": "response.output_text.delta", "delta": text},
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_local_mock",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "id": "msg_local_mock",
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": text,
                                                "annotations": [],
                                            }
                                        ],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 12,
                                    "output_tokens": max(1, len(text.split())),
                                    "total_tokens": 12 + max(1, len(text.split())),
                                },
                            },
                        },
                    ]
                    body = "".join(
                        "event: " + event["type"] + "\n"
                        + "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                        for event in events
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                finally:
                    with outer._condition:
                        request_record["finished_at"] = time.monotonic()
                        outer._active -= 1
                        outer._active_by_provider[provider] -= 1
                        outer._active_by_model[model] -= 1
                        outer._active_by_provider_model[(provider, model)] -= 1
                        outer._condition.notify_all()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self.url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    @property
    def peak_active(self) -> int:
        with self._lock:
            return self._peak_active

    @property
    def peak_by_provider(self) -> dict[str, int]:
        with self._lock:
            return dict(self._peak_by_provider)

    @property
    def peak_by_model(self) -> dict[str, int]:
        with self._lock:
            return dict(self._peak_by_model)

    @property
    def peak_by_provider_model(self) -> dict[tuple[str, str], int]:
        with self._lock:
            return dict(self._peak_by_provider_model)

    def wait_for_requests(self, count: int, timeout: float = 5) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self._requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def release(self) -> None:
        self._release.set()

    def close(self) -> None:
        self.release()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def __enter__(self) -> "MockResponsesServer":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def make_settings(
    root: Path,
    server_url: str,
    *,
    providers: dict[str, ProviderConfig] | None = None,
    en_primary: str = "mock/primary",
    en_secondary: str = "mock/secondary",
    zh_primary: str | None = None,
    zh_secondary: str | None = None,
    **overrides: Any,
) -> Settings:
    """Build isolated settings with dummy credentials and a local base URL."""

    home = root / "home"
    data_dir = root / "data"
    workspace = root / "workspace"
    for path in (home, data_dir, workspace):
        path.mkdir(parents=True, exist_ok=True)
    if providers is None:
        providers = {
            "mock": ProviderConfig(
                base_url=server_url,
                api_key=SecretStr("local-test-key"),
                max_concurrency=4,
            )
        }
    values: dict[str, Any] = {
        "home_dir": home,
        "data_dir": data_dir,
        "allowed_workspaces": [workspace],
        "providers": providers,
        "en_primary": en_primary,
        "en_secondary": en_secondary,
        "zh_primary": zh_primary if zh_primary is not None else en_primary,
        "zh_secondary": zh_secondary if zh_secondary is not None else en_secondary,
        "max_concurrency": 4,
        "max_queue_size": 200,
        "retry_count": 1,
        "worker_idle_seconds": 0.2,
        "lease_seconds": 5,
        "connect_timeout": 2,
        "first_token_timeout": 5,
        "inactivity_timeout": 5,
        "total_timeout": 10,
    }
    values.update(overrides)
    return Settings(**values)


__all__ = ["MockResponsesServer", "current_paragraph", "make_settings"]
