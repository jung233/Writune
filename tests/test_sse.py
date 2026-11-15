from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from writune.config import ProviderConfig, Settings
from writune.polishing.prompt import Prompt
from writune.providers import ResponsesProvider


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _frame(event: dict) -> bytes:
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")


@pytest.mark.asyncio
async def test_sdk_sse_parser_handles_json_and_utf8_split_across_chunks() -> None:
    text = "润色済み texto"
    frames = [
        _frame({"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": text}),
        _frame(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "model-test",
                    "output": [
                        {
                            "id": "m",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "annotations": [], "text": text}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            }
        ),
    ]
    wire = b"".join(frames)
    # Deliberately split inside both the JSON event and a multi-byte UTF-8 codepoint.
    split_at = wire.index("润".encode("utf-8")) + 1
    stream = ChunkedStream([wire[:split_at], wire[split_at : split_at + 7], wire[split_at + 7 :]])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    settings = Settings(
        providers={"test": ProviderConfig(base_url="https://example.test/v1", api_key=SecretStr("test-key"))},
        first_token_timeout=1,
        inactivity_timeout=1,
        total_timeout=2,
        tps_warmup_tokens=100,
        tps_warmup_seconds=1,
    )
    provider = ResponsesProvider(
        settings.providers["test"], settings, transport=httpx.MockTransport(handler)
    )
    result = await provider.generate("model-test", Prompt(prefix="", input="source"))

    assert result.text == text
    assert stream.closed
