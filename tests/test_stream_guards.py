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


def _completed(text: str, *, usage: dict | None = None) -> bytes:
    response = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }
    if usage is not None:
        response["usage"] = usage
    return _event({"type": "response.completed", "response": response})


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
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
            "test": ProviderConfig(
                base_url="https://example.test/v1",
                api_key=SecretStr("test-key"),
            )
        },
        "first_token_timeout": 2,
        "inactivity_timeout": 2,
        "total_timeout": 0.01,
        "min_tps": 100,
        "tps_window": 0.001,
        "tps_warmup_tokens": 0,
        "tps_warmup_seconds": 1,
        "consecutive_slow_windows": 1,
    }
    values.update(overrides)
    return Settings(**values)


async def _run(
    stream: ByteStream,
    *,
    prompt: Prompt | None = None,
    settings: Settings | None = None,
    provider_config: ProviderConfig | None = None,
) -> tuple[ResponsesProvider, object]:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    config = provider_config or (settings or _settings()).providers["test"]
    current_settings = settings or _settings()
    provider = ResponsesProvider(config, current_settings, transport=httpx.MockTransport(handler))
    generation = await provider.generate("model-test", prompt or Prompt(prefix="", input="one two three four"))
    return provider, generation


def _visible_delta(text: str) -> bytes:
    return _event(
        {
            "type": "response.output_text.delta",
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        }
    )


def _repeating_output() -> str:
    # A 128-character period repeated four times: 512 output characters and
    # 128 estimated output tokens.
    period = "".join(f"w{index:02d} " for index in range(32))
    assert len(period) == 128
    return period * 4


@pytest.mark.asyncio
async def test_repeating_suffix_is_rejected_with_partial_visible_text() -> None:
    partial = "already visible "
    repeated = _repeating_output()
    stream = ByteStream([_visible_delta(partial + repeated)])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="one two three four"))

    assert raised.value.code == "REPETITIVE_OUTPUT"
    assert raised.value.retryable is False
    assert raised.value.partial_text == partial + repeated
    assert stream.closed


@pytest.mark.asyncio
async def test_nonrepeating_output_at_twenty_times_input_is_allowed() -> None:
    output = " ".join(f"token{index:03d}" for index in range(100))
    stream = ByteStream([_visible_delta(output), _completed(output)])

    _, generation = await _run(stream)

    assert generation.text == output
    assert stream.closed


@pytest.mark.asyncio
async def test_full_request_input_includes_instructions_in_guard_ratio() -> None:
    prefix = " ".join(f"instruction{index:02d}" for index in range(20))
    repeated = _repeating_output()
    stream = ByteStream([_visible_delta(repeated), _completed(repeated)])
    settings = _settings()
    config = ProviderConfig(
        base_url="https://example.test/v1",
        api_key=SecretStr("test-key"),
        capabilities={"instructions": True},
    )

    _, generation = await _run(
        stream,
        prompt=Prompt(prefix=prefix, input="source"),
        settings=settings,
        provider_config=config,
    )

    assert generation.text == repeated
    assert stream.closed


@pytest.mark.asyncio
async def test_output_limit_rejects_nonrepeating_output_at_one_hundred_times_input() -> None:
    output = " ".join(f"unique{index:03d}" for index in range(500))
    stream = ByteStream([_visible_delta(output)])
    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    ))

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="one two three four"))

    assert raised.value.code == "OUTPUT_LIMIT_EXCEEDED"
    assert raised.value.retryable is False
    assert raised.value.partial_text == output
    assert stream.closed


@pytest.mark.asyncio
async def test_long_unbroken_ascii_output_cannot_bypass_estimated_token_ratio() -> None:
    output = "x" * 800
    stream = ByteStream([_visible_delta(output)])
    settings = _settings()
    provider = ResponsesProvider(
        settings.providers["test"],
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        ),
    )

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == "OUTPUT_LIMIT_EXCEEDED"
    assert raised.value.partial_text == output
    assert stream.closed


@pytest.mark.asyncio
async def test_completed_only_output_still_enforces_output_limit() -> None:
    output = "x" * 800
    stream = ByteStream([_completed(output)])
    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    ))

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == "OUTPUT_LIMIT_EXCEEDED"
    assert raised.value.partial_text == output
    assert stream.closed


@pytest.mark.asyncio
async def test_upstream_usage_can_enforce_final_output_limit() -> None:
    stream = ByteStream(
        [
            _visible_delta("ok"),
            _completed("ok", usage={"input_tokens": 1, "output_tokens": 100}),
        ]
    )
    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    ))

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert raised.value.code == "OUTPUT_LIMIT_EXCEEDED"
    assert raised.value.partial_text == "ok"
    assert stream.closed


@pytest.mark.asyncio
async def test_done_only_repeating_output_is_checked_after_reconciliation() -> None:
    repeated = _repeating_output()
    stream = ByteStream(
        [
            _event(
                {
                    "type": "response.output_text.done",
                    "item_id": "m",
                    "output_index": 0,
                    "content_index": 0,
                    "text": repeated,
                }
            ),
            _completed(repeated),
        ]
    )
    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    ))

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="one two three four"))

    assert raised.value.code == "REPETITIVE_OUTPUT"
    assert raised.value.partial_text == repeated
    assert stream.closed


@pytest.mark.asyncio
async def test_reasoning_output_counts_for_guard_and_stays_separate() -> None:
    repeated = _repeating_output()
    stream = ByteStream(
        [
            _visible_delta("visible answer "),
            _event(
                {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": "r",
                    "output_index": 1,
                    "content_index": 0,
                    "delta": repeated,
                }
            ),
        ]
    )
    settings = _settings()
    provider = ResponsesProvider(settings.providers["test"], settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    ))

    with pytest.raises(ProviderError) as raised:
        await provider.generate("model-test", Prompt(prefix="", input="one two three four"))

    assert raised.value.code == "REPETITIVE_OUTPUT"
    assert raised.value.partial_text == "visible answer "
    assert raised.value.partial_reasoning == repeated
    assert stream.closed
