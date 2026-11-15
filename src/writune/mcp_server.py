"""Official FastMCP adapter for Writune's synchronous service layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from writune.config import Settings
from writune.schemas import (
    ApplyResponse,
    BatchItem,
    BatchResponse,
    CancelResponse,
    ContextCreated,
    ExportResponse,
    MergeResponse,
    HealthResponse,
    ResultResponse,
    StatsResponse,
    StatusResponse,
    SubmitResponse,
)
from writune.service import Service
from writune.tasks.protocol import TaskExtensionAdapter


def create_server(service: Service | None = None, *, home: str | Path | None = None,tasks_adapter: TaskExtensionAdapter | None = None) -> FastMCP:
    """Build a FastMCP instance. Service construction stays lazy for stdio startup."""

    instance = service

    def get_service() -> Service:
        nonlocal instance
        if instance is None:
            instance = Service(Settings.load(home), start_worker=True)
        return instance

    server = FastMCP(
        "Writune",
        instructions=(
            "Polish text through durable Writune tasks. Prefer source_path and result_path "
            "handoff; result text is returned only when inline_text=true."
        ),
    )

    @server.tool(
        name="writune_create_context",
        description="Create or update a stable writing context for related polishing tasks.",
    )
    def writune_create_context(
        language: Literal["zh", "en"] | None = None,
        scene: str | None = None,
        section: str | None = None,
        topic: str | None = None,
        context_summary: str | None = None,
        writing_goal: str | None = None,
        terminology: list[str] | None = None,
        constraints: list[str] | None = None,
        context_id: str | None = None,
    ) -> ContextCreated:
        """Save document-level context and return its stable context_id."""

        context: dict[str, Any] = {}
        if language is not None or context_id is None:
            context['language']=language or 'en'
        if scene is not None or context_id is None:
            context['scene']=scene or 'general_prose'
        for key, value in (
            ("section", section),
            ("topic", topic),
            ("context_summary", context_summary),
            ("writing_goal", writing_goal),
            ("terminology", terminology),
            ("constraints", constraints),
        ):
            if value is not None:
                context[key] = value
        return ContextCreated.model_validate(get_service().create_context(context_id=context_id, **context))

    @server.tool(
        name="writune_submit",
        description="Queue one paragraph from text or an authorized source file; returns immediately.",
    )
    def writune_submit(
        text: str | None = None,
        source_path: str | None = None,
        source_encoding: str | None = None,
        language: Literal["zh", "en"] | None = None,
        scene: str | None = None,
        context_id: str | None = None,
        primary_model: str | None = None,
        secondary_model: str | None = None,
        style: str | None = None,
        priority: int | None = None,
        idempotency_key: str | None = None,
        profile: str | None = None,
        local_context: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SubmitResponse:
        """Submit a task without waiting for upstream generation."""

        return SubmitResponse.model_validate(
            get_service().submit(
                text=text,
                source_path=source_path,
                source_encoding=source_encoding,
                language=language,
                scene=scene,
                context_id=context_id,
                primary_model=primary_model,
                secondary_model=secondary_model,
                style=style,
                priority=priority,
                idempotency_key=idempotency_key,
                profile=profile,
                local_context=local_context,
                options=options,
            )
        )

    @server.tool(
        name="writune_submit_batch",
        description="Queue paragraphs, authorized source files, or an automatically segmented Markdown file.",
    )
    def writune_submit_batch(
        items: list[BatchItem] | None = None,
        source_paths: list[str] | None = None,
        markdown_path: str | None = None,
        source_encoding: str | None = None,
        language: Literal["zh", "en"] | None = None,
        scene: str | None = None,
        context_id: str | None = None,
        primary_model: str | None = None,
        secondary_model: str | None = None,
        style: str | None = None,
        priority: int | None = None,
        idempotency_key: str | None = None,
        profile: str | None = None,
        local_context: str | None = None,
        options: dict[str, Any] | None = None,
        max_concurrency: int | None = None,
    ) -> BatchResponse:
        """Submit a batch while preserving paragraph order."""

        provided_modes = sum(value is not None for value in (items, source_paths, markdown_path))
        if provided_modes != 1:
            raise ValueError("provide exactly one of items, source_paths, or markdown_path")
        item_dicts = [item.model_dump(exclude_none=True,exclude_unset=True) for item in items] if items is not None else None
        return BatchResponse.model_validate(
            get_service().submit_batch(
                items=item_dicts,
                source_paths=source_paths,
                markdown_path=markdown_path,
                source_encoding=source_encoding,
                max_concurrency=max_concurrency,
                language=language,
                scene=scene,
                context_id=context_id,
                primary_model=primary_model,
                secondary_model=secondary_model,
                style=style,
                priority=priority,
                idempotency_key=idempotency_key,
                profile=profile,
                local_context=local_context,
                options=options,
            )
        )

    @server.tool(
        name="writune_status",
        description="Get progress for exactly one task_id or batch_id without returning result text.",
    )
    def writune_status(
        task_id: str | None = None,
        batch_id: str | None = None,
    ) -> StatusResponse:
        """Return queue progress and compact error codes."""

        if bool(task_id) == bool(batch_id):
            raise ValueError("provide exactly one of task_id or batch_id")
        return StatusResponse.model_validate(get_service().status(task_id=task_id, batch_id=batch_id))

    @server.tool(
        name="writune_result",
        description="Get result paths and outcome; include inline_text only when explicitly needed.",
    )
    def writune_result(task_id: str, inline_text: bool = False) -> ResultResponse:
        """Return result metadata, optionally including the polished text."""

        return ResultResponse.model_validate(get_service().result(task_id, inline_text=inline_text))

    @server.tool(
        name="writune_apply",
        description="Insert a completed result at one Writune placeholder in an authorized file.",
    )
    def writune_apply(
        task_id: str,
        target_path: str,
        placeholder_id: str,
        output_path: str | None = None,
        overwrite: bool = False,
        target_encoding: str | None = None,
    ) -> ApplyResponse:
        """Apply a task result deterministically to a target document."""

        return ApplyResponse.model_validate(
            get_service().apply(
                task_id=task_id,
                target_path=target_path,
                placeholder_id=placeholder_id,
                output_path=output_path,
                overwrite=overwrite,
                target_encoding=target_encoding,
            )
        )

    @server.tool(
        name="writune_export",
        description="Copy a completed result to a caller-authorized workspace path for controlled file handoff.",
    )
    def writune_export(task_id: str, output_path: str, overwrite: bool = False) -> ExportResponse:
        """Export a result file into an allowed workspace directory."""

        return ExportResponse.model_validate(
            get_service().export(task_id=task_id, output_path=output_path, overwrite=overwrite)
        )

    @server.tool(
        name="writune_cancel",
        description="Request cancellation of a queued or running polishing task.",
    )
    def writune_cancel(task_id: str) -> CancelResponse:
        """Cancel a task and report its current state."""

        return CancelResponse.model_validate(get_service().cancel(task_id))

    @server.tool(name='writune_merge_batch',description='Merge completed paragraphs in original order into an authorized file; retain Markdown separators and encoding.')
    def writune_merge_batch(batch_id: str, output_path: str, separator: str | None = None, overwrite: bool = False) -> MergeResponse:
        return MergeResponse.model_validate(get_service().merge_batch(batch_id,output_path,separator,overwrite))

    @server.tool(
        name="writune_health",
        description="Inspect configuration, provider credentials, worker, queue, recent errors, and storage.",
    )
    async def writune_health(probe: bool = False) -> HealthResponse:
        """Return a concise local health summary without exposing credentials."""

        import asyncio
        result=await asyncio.to_thread(get_service().health,probe)
        return HealthResponse.model_validate(result)

    if tasks_adapter is not None:
        tasks_adapter.install(server,get_service)
    return server


mcp = create_server()


def run(home: str | Path | None = None) -> None:
    """Run the MCP stdio transport. ``home`` selects Writune's config directory."""

    if home is not None:
        os.environ["WRITUNE_HOME"] = str(Path(home).expanduser())
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover - exercised by stdio subprocess tests
    run()
