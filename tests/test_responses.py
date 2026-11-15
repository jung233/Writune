from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from writune.config import ProviderConfig, Settings
from writune.polishing.prompt import Prompt
from writune.providers import ProviderError, ResponsesProvider


def _event(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


def _completed(text: str, *, usage: dict | None = None, status: str = "completed") -> bytes:
    response = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "model-test",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "annotations": [], "text": text}],
            }
        ],
        "usage": usage
        or {
            "input_tokens": 14,
            "input_tokens_details": {"cached_tokens": 5, "cache_creation_input_tokens": 2},
            "output_tokens": 3,
            "total_tokens": 17,
        },
    }
    return _event({"type": "response.completed", "response": response})


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _settings(**overrides) -> Settings:
    values = {
        "providers": {
            "primary": ProviderConfig(
                base_url="https://example.test/v1",
                api_key=SecretStr("secret-test-key"),
            )
        },
        "first_token_timeout": 1,
        "inactivity_timeout": 1,
        "total_timeout": 2,
        "tps_warmup_tokens": 100,
        "tps_warmup_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_streamed_generation_reports_usage_and_gates_optional_fields() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": 1,
                        "model": "model-test",
                        "output": [],
                        "parallel_tool_calls": True,
                        "tool_choice": "auto",
                        "tools": [],
                        "status": "in_progress",
                    },
                }
            ),
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_test",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Polished",
                }
            ),
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_test",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": " text.",
                }
            ),
            _event(
                {
                    "type": "response.output_text.done",
                    "item_id": "msg_test",
                    "output_index": 0,
                    "content_index": 0,
                    "text": "Polished text.",
                }
            ),
            _completed("Polished text."),
        ]
    )
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    config = ProviderConfig(
        base_url="https://example.test/v1",
        api_key=SecretStr("secret-test-key"),
        capabilities={"instructions": True},
        parameters={"temperature": 0.2, "reasoning": {"effort": "low"}},
        cache={"enabled": True, "prompt_cache_retention": "24h"},
    )
    provider = ResponsesProvider(config, _settings(), transport=httpx.MockTransport(handler))
    generation = await provider.generate("model-test", Prompt(prefix="stable rules", input="source"))

    assert generation.text == "Polished text."
    assert generation.input_tokens == 14
    assert generation.output_tokens == 3
    assert generation.cached_input_tokens == 5
    assert generation.cache_write_tokens == 2
    assert generation.first_token_latency >= 0
    assert generation.total_elapsed >= generation.first_token_latency
    assert generation.tps_estimated is True
    assert provider.last_metrics["input_tokens"] == 14
    assert provider.last_metrics["output_tokens"] == 3
    assert provider.last_metrics["cached_input_tokens"] == 5
    assert provider.last_metrics["cache_write_tokens"] == 2
    assert provider.last_metrics["total_elapsed"] >= provider.last_metrics["first_token_latency"]
    assert all(not isinstance(value, str) for value in provider.last_metrics.values())
    assert requests == [{"model": "model-test", "input": "source", "stream": True, "instructions": "stable rules"}]
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "AUTHENTICATION_ERROR", False),
        (429, "RATE_LIMITED", True),
        (500, "UPSTREAM_ERROR", True),
    ],
)
async def test_http_failures_are_sanitized_and_not_retried(status: int, code: str, retryable: bool) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"message": "secret-test-key was echoed"}})

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert "secret-test-key" not in raised.value.message
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "code"),
    [
        ([_completed("")], "EMPTY_RESPONSE"),
        (
            [
                _event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_test",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "partial",
                    }
                )
            ],
            "TRUNCATED_RESPONSE",
        ),
        (
            [_event({"type": "response.failed", "response": {"error": {"message": "private detail"}}})],
            "UPSTREAM_FAILED",
        ),
    ],
)
async def test_empty_truncated_and_failed_streams_are_errors(chunks: list[bytes], code: str) -> None:
    stream = ByteStream(chunks)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == code
    assert "private detail" not in raised.value.message
    assert stream.closed
    if code == "TRUNCATED_RESPONSE":
        assert provider.last_metrics["output_tokens"] > 0
        assert provider.last_metrics["first_token_latency"] is not None
        assert provider.last_metrics["total_elapsed"] >= 0


@pytest.mark.asyncio
async def test_cache_fields_are_capability_gated_and_prefix_key_is_stable() -> None:
    bodies: list[dict] = []
    stream = ByteStream(
        [
            _event({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "ok"}),
            _completed("ok"),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    config = ProviderConfig(
        base_url="https://example.test/v1",
        api_key=SecretStr("secret-test-key"),
        capabilities={"cache": {"prompt_cache_key": True, "prompt_cache_retention": True}},
        cache={"enabled": True, "retention": "24h"},
    )
    provider = ResponsesProvider(config, _settings(), transport=httpx.MockTransport(handler))
    await provider.generate("model-test", Prompt(prefix="rules", input="one"))

    assert bodies[0]["prompt_cache_retention"] == "24h"
    assert len(bodies[0]["prompt_cache_key"]) == 64


@pytest.mark.asyncio
async def test_model_specific_capabilities_and_parameters_apply_to_direct_generation() -> None:
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        stream = ByteStream(
            [
                _event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "m",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "ok",
                    }
                ),
                _completed("ok"),
            ]
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    config = ProviderConfig(
        base_url="https://example.test/v1",
        api_key=SecretStr("secret-test-key"),
        capabilities={"temperature": False, "reasoning": False},
        parameters={"temperature": 0.2, "reasoning": {"effort": "medium"}},
        models={
            "model-a": {
                "capabilities": {"temperature": True, "reasoning": True},
                "parameters": {"temperature": 0.7, "reasoning": {"effort": "low"}},
            }
        },
    )
    provider = ResponsesProvider(config, _settings(), transport=httpx.MockTransport(handler))

    await provider.generate("model-a", Prompt(prefix="rules", input="one"))
    await provider.generate("model-b", Prompt(prefix="rules", input="two"))

    assert bodies[0]["temperature"] == 0.7
    assert bodies[0]["reasoning"] == {"effort": "low"}
    assert "temperature" not in bodies[1]
    assert "reasoning" not in bodies[1]


@pytest.mark.asyncio
async def test_text_done_without_delta_does_not_satisfy_first_text_watchdog() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.done",
                    "item_id": "msg_test",
                    "output_index": 0,
                    "content_index": 0,
                    "text": "final only",
                }
            ),
            _completed("final only"),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == "NO_TEXT_DELTA"
    assert stream.closed


@pytest.mark.asyncio
async def test_reserved_request_options_cannot_override_stream_or_add_headers() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "ok",
                }
            ),
            _completed("ok"),
        ]
    )
    requests: list[httpx.Request] = []
    capabilities = {
        key: True
        for key in (
            "model",
            "input",
            "stream",
            "instructions",
            "extra_body",
            "extra_headers",
            "extra_query",
        )
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(
            base_url="https://example.test/v1",
            api_key=SecretStr("secret-test-key"),
            capabilities=capabilities,
        ),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    await provider.generate(
        "configured/model",
        Prompt(prefix="stable instructions", input="configured source"),
        options={
            "model": "untrusted/model",
            "input": "untrusted source",
            "stream": False,
            "instructions": "untrusted instructions",
            "extra_body": {"model": "also-untrusted", "stream": False},
            "extra_headers": {"x-untrusted": "present"},
            "extra_query": {"untrusted": "present"},
        },
    )

    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    assert body["model"] == "configured/model"
    assert body["input"] == "configured source"
    assert body["stream"] is True
    assert body["instructions"] == "stable instructions"
    assert "extra_body" not in body
    assert request.headers.get("x-untrusted") is None
    assert request.url.query == b""


@pytest.mark.asyncio
async def test_unsupported_cache_parameters_are_omitted() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "ok",
                }
            ),
            _completed("ok"),
        ]
    )
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(
            base_url="https://example.test/v1",
            api_key=SecretStr("secret-test-key"),
            capabilities={"cache": {"prompt_cache_key": False, "prompt_cache_retention": False}},
            cache={
                "enabled": True,
                "parameters": {
                    "prompt_cache_key": "unconfigured-support",
                    "prompt_cache_retention": "24h",
                    "cache_control": {"type": "ephemeral"},
                },
            },
        ),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    await provider.generate("model-test", Prompt(prefix="stable", input="source"))

    assert len(bodies) == 1
    assert "prompt_cache_key" not in bodies[0]
    assert "prompt_cache_retention" not in bodies[0]
    assert "cache_control" not in bodies[0]


@pytest.mark.asyncio
async def test_generic_done_event_does_not_replace_responses_completed() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "partial",
                }
            ),
            _event({"type": "done"}),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code in {"TRUNCATED_RESPONSE", "PROVIDER_ERROR"}
    assert stream.closed


@pytest.mark.asyncio
async def test_done_text_matching_late_deltas_and_multiple_content_slots_join() -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.done",
                    "item_id": "m0",
                    "output_index": 0,
                    "content_index": 0,
                    "text": "first slot",
                }
            ),
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m0",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "first ",
                }
            ),
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m0",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "slot",
                }
            ),
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m1",
                    "output_index": 1,
                    "content_index": 0,
                    "delta": " second",
                }
            ),
            _completed("first slot second"),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    generation = await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert generation.text == "first slot second"
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("done_text", "completed_text"),
    [("different text", "partial"), (None, "complete response")],
)
async def test_inconsistent_streamed_text_is_rejected(done_text: str | None, completed_text: str) -> None:
    chunks = [
        _event(
            {
                "type": "response.output_text.delta",
                "item_id": "m",
                "output_index": 0,
                "content_index": 0,
                "delta": "partial",
            }
        )
    ]
    if done_text is not None:
        chunks.append(
            _event(
                {
                    "type": "response.output_text.done",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "text": done_text,
                }
            )
        )
    chunks.append(_completed(completed_text))
    stream = ByteStream(chunks)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == "INCONSISTENT_TEXT"
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [("failed", "UPSTREAM_FAILED"), ("incomplete", "INCOMPLETE_RESPONSE")],
)
async def test_completed_event_with_failed_response_status_is_rejected(status: str, code: str) -> None:
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.delta",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "partial",
                }
            ),
            _completed("partial", status=status),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("secret-test-key")),
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == code
    assert provider.last_metrics["output_tokens"] == 3
    assert stream.closed
