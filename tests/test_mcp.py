from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from writune.runtime import windowless_python
from mcp.client.stdio import StdioServerParameters, stdio_client

from writune.config import ProviderConfig, Settings
from tests.mock_responses import MockResponsesServer, make_settings


@pytest.mark.asyncio
async def test_real_stdio_handshake_discovers_typed_tools_and_calls_service(tmp_path):
    home = tmp_path / "home"
    settings = Settings(
        data_dir=tmp_path / "data",
        allowed_workspaces=[tmp_path],
        providers={"primary": ProviderConfig(base_url="https://api.example/v1", api_key="test-secret")},
        zh_primary="primary/zh-model",
        en_primary="primary/en-model",
    )
    settings.save(home)
    parameters = StdioServerParameters(
        command=windowless_python(),
        args=["-m", "writune.mcp_server"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={"WRITUNE_HOME": str(home)},
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Writune"
            listed = await session.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            expected = {
                "writune_create_context",
                "writune_submit",
                "writune_submit_batch",
                "writune_status",
                "writune_result",
                "writune_apply",
                "writune_export",
                "writune_cancel",
                "writune_health",
            }
            assert expected <= set(by_name)
            assert 'writune_merge_batch' in by_name
            assert 'max_concurrency' in by_name['writune_submit_batch'].inputSchema['properties']
            segmentation = by_name['writune_submit_batch'].inputSchema['properties']['segmentation']
            assert segmentation['enum'] == ['paragraph', 'section', 'document']
            assert by_name["writune_submit"].inputSchema["type"] == "object"
            assert by_name["writune_result"].outputSchema["type"] == "object"
            assert by_name["writune_status"].outputSchema["properties"]["status"]["enum"] == [
                "queued",
                "running",
                "completed",
                "failed",
                "cancelled",
            ]

            context_result = await session.call_tool(
                "writune_create_context",
                {
                    "language": "en",
                    "scene": "sci_paper",
                    "section": "Results",
                    "topic": "A test study",
                    "context_summary": "A locally stored test context",
                    "writing_goal": "Improve clarity",
                    "terminology": ["GHRH"],
                    "constraints": ["Preserve all numbers"],
                },
            )
            assert not context_result.isError
            context_payload = context_result.structuredContent
            assert context_payload and context_payload["context_id"].startswith("ctx_")

            health_result = await session.call_tool("writune_health", {})
            assert not health_result.isError
            health = health_result.structuredContent
            assert health["initialized"] is True
            assert health["providers"]["primary"]["configured"] is True
            assert "test-secret" not in json.dumps(health)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_models", "primary_model", "secondary_model", "expected_outcome"),
    [
        (set(), "mock/primary", "mock/secondary", "polished"),
        ({"primary-fail", "secondary"}, "mock/primary-fail", "mock/secondary", "original_fallback"),
    ],
)
async def test_real_stdio_file_submit_result_and_apply(
    tmp_path,
    fail_models,
    primary_model,
    secondary_model,
    expected_outcome,
):
    with MockResponsesServer(fail_models=fail_models) as upstream:
        settings = make_settings(
            tmp_path,
            upstream.url,
            en_primary=primary_model,
            en_secondary=secondary_model,
            retry_count=1,
            worker_idle_seconds=0.2,
            lease_seconds=5,
            max_concurrency=1,
        )
        home = tmp_path / "home"
        settings.save(home)
        workspace = settings.allowed_workspaces[0]
        source = workspace / "draft.txt"
        target = workspace / "manuscript.md"
        original = "GHRH was associated with TP53 (p = 0.03; 95% CI: 1.2–2.1) in 42 samples [3, 4]."
        source.write_text(original, encoding="utf-8")
        target.write_text("Before {{WRITUNE:SECTION_1}} after\n", encoding="utf-8")
        parameters = StdioServerParameters(
            command=windowless_python(),
            args=["-m", "writune.mcp_server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={"WRITUNE_HOME": str(home)},
        )

        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                submitted = await session.call_tool(
                    "writune_submit",
                    {
                        "source_path": str(source),
                        "language": "en",
                        "scene": "sci_paper",
                        "primary_model": primary_model,
                        "secondary_model": secondary_model,
                    },
                )
                assert not submitted.isError, submitted
                task_id = submitted.structuredContent["task_id"]
                assert submitted.structuredContent["status"] == "queued"

                status = None
                for _ in range(400):
                    response = await session.call_tool("writune_status", {"task_id": task_id})
                    assert not response.isError, response
                    status = response.structuredContent
                    if status["status"] in {"completed", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.05)
                assert status is not None and status["status"] == "completed", status

                result = await session.call_tool("writune_result", {"task_id": task_id})
                assert not result.isError, result
                payload = result.structuredContent
                assert payload["status"] == "completed"
                assert payload["outcome"] == expected_outcome
                assert payload["result_path"]
                assert payload.get("text") is None
                assert "text" not in payload
                assert result.content == []
                assert payload["fallback_used"] is (expected_outcome == "original_fallback")
                if expected_outcome == "original_fallback":
                    assert payload["error"]["attempts"] == 2

                applied = await session.call_tool(
                    "writune_apply",
                    {
                        "task_id": task_id,
                        "target_path": str(target),
                        "placeholder_id": "SECTION_1",
                    },
                )
                assert not applied.isError, applied
                output_path = Path(applied.structuredContent["output_path"])
                assert output_path.read_text(encoding="utf-8") == f"Before {original} after\n"
