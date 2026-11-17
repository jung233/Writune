"""Typed request and response models shared by the CLI and MCP adapter."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextCreated(Schema):
    context_id: str = Field(description="Stable identifier for the saved writing context")


class SubmitResponse(Schema):
    task_id: str
    estimated_input_tokens: int | None = None
    token_count_estimated: bool = True
    warnings: list[str] = Field(default_factory=list)
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    estimated_seconds: float | None = None
    estimate_confidence: Literal["low", "medium", "high"] = "low"
    queue_position: int | None = None
    worker_start_pending: bool = Field(
        default=False,
        description="True when the task is persisted but the worker could not be started; a later status call retries startup.",
    )


class BatchResponse(Schema):
    batch_id: str
    estimated_input_tokens: int | None = Field(default=None, description='Largest individual task input estimate, not batch sum')
    token_count_estimated: bool = True
    warnings: list[str] = Field(default_factory=list)
    task_ids: list[str]
    accepted_count: int
    estimated_seconds: float | None = None


class TaskError(Schema):
    task_id: str | None = None
    code: str | None = None


class StatusResponse(Schema):
    task_id: str | None = None
    batch_id: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: float = Field(ge=0, le=1)
    queue_position: int | None = None
    running_count: int = 0
    completed_count: int = 0
    estimated_seconds_remaining: float | None = None
    error_summary: list[TaskError] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


class ResultError(Schema):
    code: str | None = None
    message: str | None = None
    attempts: int | None = None


class ResultResponse(Schema):
    task_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    outcome: Literal["polished", "original_fallback", "none"] | None = None
    result_path: str | None = None
    manifest_path: str | None = None
    possible_duplicate_billing: bool = False
    polished: bool = False
    fallback_used: bool = False
    review_required: bool = False
    partial_output_path: str | None = None
    partial_reasoning_path: str | None = None
    error: ResultError | None = None
    text: str | None = Field(default=None, description="Present only when inline_text=true")


class ApplyResponse(Schema):
    target_path: str
    output_path: str
    placeholder_id: str
    output_sha256: str
    idempotent: bool


class ExportResponse(Schema):
    task_id: str
    output_path: str


class MergeResponse(Schema):
    batch_id: str
    output_path: str
    paragraph_count: int
    fallback_count: int


class CancelResponse(Schema):
    task_id: str
    cancel_requested: bool
    status: Literal["queued", "running", "completed", "failed", "cancelled"]


class ProviderHealth(Schema):
    configured: bool
    availability: str


class StorageHealth(Schema):
    database: str
    ok: bool


class WorkerHealth(Schema):
    alive: bool
    responsive: bool = False
    state: Literal["running", "unresponsive", "stopped"] = "stopped"
    pid: int | None = None


class HealthResponse(Schema):
    initialized: bool
    providers: dict[str, ProviderHealth]
    worker: WorkerHealth
    active_tasks: int
    queue_length: int
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)
    storage: StorageHealth


class ModelStats(Schema):
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    scene: str | None = None
    requests: int = 0
    success_rate: float | None = None
    failure_rate: float | None = None
    estimated_tps_requests: int | None = None
    generation_duration: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    sample_window: int = 50
    measured_usage_requests: int = 0
    cost: dict[str, Any] | None = None
    average_tps: float | None = None
    first_token_latency: float | None = None
    total_elapsed: float | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None


class StatsResponse(Schema):
    models: list[ModelStats] = Field(default_factory=list)
    failure_reasons: list[dict[str, Any]] = Field(default_factory=list)


class BatchItem(Schema):
    """One batch paragraph; task-level options may override batch defaults."""

    text: str | None = None
    source_path: str | None = None
    source_encoding: str | None = None
    language: Literal["zh", "en"] | None = None
    scene: str | None = None
    context_id: str | None = None
    primary_model: str | None = None
    secondary_model: str | None = None
    style: str | None = None
    priority: int | None = None
    idempotency_key: str | None = None
    profile: str | None = None
    local_context: str = ""
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "BatchItem":
        if (self.text is None) == (self.source_path is None):
            raise ValueError("each batch item must provide exactly one of text or source_path")
        return self
