from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from writune.config import ProviderConfig, Settings
from writune.polishing.prompt import Prompt
from writune.providers import ProviderError, ResponsesProvider


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[tuple[float, bytes]]):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for delay, chunk in self.chunks:
            if delay:
                await asyncio.sleep(delay)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class CancellationStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.first_chunk_sent = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        yield _event(
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "item_id": "m",
                "output_index": 0,
                "content_index": 0,
                "logprobs": [],
                "delta": "partial",
            }
        )
        self.first_chunk_sent.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


def _event(event: dict) -> bytes:
    return ("data: " + json.dumps(event) + "\n\n").encode("utf-8")


def _settings(**overrides) -> Settings:
    values = {
        "providers": {
            "test": ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("test-key"))
        },
        "first_token_timeout": 0.04,
        "inactivity_timeout": 0.04,
        "total_timeout": 0.5,
        "tps_warmup_tokens": 100,
        "tps_warmup_seconds": 1,
        "tps_window": 0.1,
    }
    values.update(overrides)
    return Settings(**values)


async def _run_with_stream(stream: DelayedStream, settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = ResponsesProvider(
        settings.providers["test"], settings, transport=httpx.MockTransport(handler)
    )
    await provider.generate("model-test", Prompt(prefix="", input="source"))


@pytest.mark.asyncio
async def test_first_text_timeout_ignores_response_created() -> None:
    stream = DelayedStream(
        [
            (
                0,
                _event(
                    {
                        "type": "response.created",
                        "sequence_number": 0,
                        "response": {
                            "id": "r",
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
            ),
            (
                0.08,
                _event({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "late"}),
            ),
        ]
    )
    with pytest.raises(ProviderError) as raised:
        await _run_with_stream(stream, _settings())
    assert raised.value.code == "FIRST_TOKEN_TIMEOUT"
    assert stream.closed


@pytest.mark.asyncio
async def test_inactivity_timeout_closes_stream_after_text_stalls() -> None:
    stream = DelayedStream(
        [
            (0, _event({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "part"})),
            (
                0.09,
                _event({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "ial"}),
            ),
        ]
    )
    settings = _settings(first_token_timeout=0.3, inactivity_timeout=0.04)
    with pytest.raises(ProviderError) as raised:
        await _run_with_stream(stream, settings)
    assert raised.value.code == "INACTIVITY_TIMEOUT"
    assert stream.closed


@pytest.mark.asyncio
async def test_total_attempt_timeout_bounds_unproductive_provider_events() -> None:
    stream = DelayedStream(
        [
            (0, _event({"type": "response.created", "response": {"id": "r"}})),
            (
                0.2,
                _event({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "late"}),
            ),
        ]
    )
    settings = _settings(first_token_timeout=0.4, total_timeout=0.04)
    with pytest.raises(ProviderError) as raised:
        await _run_with_stream(stream, settings)
    assert raised.value.code == "TOTAL_TIMEOUT"
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_active_sdk_stream() -> None:
    stream = CancellationStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    settings = _settings(first_token_timeout=0.4, inactivity_timeout=1)
    provider = ResponsesProvider(
        settings.providers["test"], settings, transport=httpx.MockTransport(handler)
    )
    task = asyncio.create_task(provider.generate("model-test", Prompt(prefix="", input="source")))
    await asyncio.wait_for(stream.first_chunk_sent.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
