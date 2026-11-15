from __future__ import annotations

import asyncio
import time
from pathlib import Path

from pydantic import SecretStr

from writune.config import ProviderConfig
from writune.service import Service
from writune.tasks.worker import Worker

from .mock_responses import MockResponsesServer, current_paragraph, make_settings


async def _wait_until(predicate, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met before the timeout")
        await asyncio.sleep(0.025)


def test_worker_enforces_global_provider_and_model_concurrency(tmp_path: Path) -> None:
    provider_keys = {"alpha-local-key": "alpha", "beta-local-key": "beta"}
    with MockResponsesServer(provider_keys=provider_keys, response_delay=0.12) as server:
        providers = {
            "alpha": ProviderConfig(
                base_url=server.url,
                api_key=SecretStr("alpha-local-key"),
                max_concurrency=2,
                model_concurrency={"red": 1, "blue": 1},
            ),
            "beta": ProviderConfig(
                base_url=server.url,
                api_key=SecretStr("beta-local-key"),
                max_concurrency=2,
                model_concurrency={"green": 2},
            ),
        }
        settings = make_settings(
            tmp_path,
            server.url,
            providers=providers,
            en_primary="alpha/red",
            en_secondary="beta/green",
            max_concurrency=4,
            worker_idle_seconds=2,
        )
        service = Service(settings, start_worker=False)
        models = [
            "alpha/red",
            "beta/green",
            "alpha/blue",
            "beta/green",
            "alpha/red",
            "alpha/blue",
            "beta/green",
            "alpha/red",
            "alpha/blue",
        ]
        submitted = service.submit_batch(
            items=[
                {"text": f"A sentence to polish in job {index}.", "primary_model": model}
                for index, model in enumerate(models)
            ]
        )
        worker = Worker(settings, owner="concurrency-test-worker")

        async def run_worker() -> None:
            run_task = asyncio.create_task(worker.run())
            try:
                await _wait_until(
                    lambda: all(
                        service.db.get(task_id)["status"] == "completed"
                        for task_id in submitted["task_ids"]
                    )
                )
            finally:
                worker.stop()
                await asyncio.wait_for(run_task, timeout=4)

        asyncio.run(run_worker())

        assert server.peak_active == 4, [
            {
                "status": service.db.get(task_id)["status"],
                "outcome": service.db.get(task_id)["outcome"],
                "error_code": service.db.get(task_id)["error_code"],
                "error_message": service.db.get(task_id)["error_message"],
                "attempt_count": service.db.get(task_id)["attempt_count"],
            }
            for task_id in submitted["task_ids"]
        ]
        assert server.peak_by_provider["alpha"] <= 2
        assert server.peak_by_provider["beta"] <= 2
        assert server.peak_by_model["red"] <= 1
        assert server.peak_by_model["blue"] <= 1
        assert server.peak_by_model["green"] <= 2
        assert server.peak_by_provider_model[("alpha", "red")] <= 1
        assert server.peak_by_provider_model[("alpha", "blue")] <= 1
        assert server.peak_by_provider_model[("beta", "green")] <= 2


def test_markdown_batch_merges_twenty_concurrent_results_in_source_order(
    tmp_path: Path,
) -> None:
    markers = (
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
        "foxtrot",
        "golf",
        "hotel",
        "india",
        "juliet",
        "kilo",
        "lima",
        "mike",
        "november",
        "oscar",
        "papa",
        "quebec",
        "romeo",
        "sierra",
        "tango",
    )
    paragraphs = [
        f"Paragraph token-{marker} keeps its original position in the document."
        for marker in markers
    ]
    markdown = "\n\n".join(paragraphs) + "\n"
    with MockResponsesServer(
        response_delay=0.01,
        text_delays={"token-alpha": 0.35},
    ) as server:
        settings = make_settings(
            tmp_path,
            server.url,
            max_concurrency=4,
            worker_idle_seconds=2,
        )
        service = Service(settings, start_worker=False)
        source = settings.allowed_workspaces[0] / "twenty-sections.md"
        source.write_text(markdown, encoding="utf-8")
        submitted = service.submit_batch(markdown_path=source)
        assert submitted["accepted_count"] == 20
        worker = Worker(settings, owner="markdown-order-worker")

        async def run_worker() -> None:
            run_task = asyncio.create_task(worker.run())
            try:
                await _wait_until(
                    lambda: all(
                        service.db.get(task_id)["status"] == "completed"
                        for task_id in submitted["task_ids"]
                    )
                )
            finally:
                worker.stop()
                await asyncio.wait_for(run_task, timeout=4)

        asyncio.run(run_worker())

        with service.db.connect() as connection:
            rows = connection.execute(
                "SELECT task_id, position, original_path FROM tasks "
                "WHERE batch_id=? ORDER BY position",
                (submitted["batch_id"],),
            ).fetchall()
        source_segments = [
            Path(row["original_path"]).read_text(encoding="utf-8")
            for row in rows
        ]
        assert len(rows) == 20
        assert [row["task_id"] for row in rows] == submitted["task_ids"]
        assert [row["position"] for row in rows] == list(range(20))
        assert all(
            f"token-{marker}" in segment
            for marker, segment in zip(markers, source_segments)
        )
        assert len(server.requests) == 20
        response_order = sorted(server.requests, key=lambda request: request["finished_at"])
        assert "token-alpha" not in current_paragraph(response_order[0]["body"])
        assert server.peak_active > 1

        merged_path = settings.allowed_workspaces[0] / "merged.md"
        merged = service.merge_batch(submitted["batch_id"], merged_path)
        assert merged["paragraph_count"] == 20
        assert merged["fallback_count"] == 0
        merged_text = merged_path.read_text(encoding="utf-8")
        assert merged_text == "".join(source_segments)
        marker_positions = [merged_text.index(f"token-{marker}") for marker in markers]
        assert marker_positions == sorted(marker_positions)


def test_running_cancellation_releases_the_provider_connection_and_slot(
    tmp_path: Path,
) -> None:
    with MockResponsesServer(hold_first=True) as server:
        settings = make_settings(
            tmp_path,
            server.url,
            max_concurrency=1,
            worker_idle_seconds=3,
            providers={
                "mock": ProviderConfig(
                    base_url=server.url,
                    api_key=SecretStr("local-test-key"),
                    max_concurrency=1,
                    model_concurrency={"primary": 1},
                )
            },
        )
        service = Service(settings, start_worker=False)
        first = service.submit(text="This first request will be cancelled.")
        second = service.submit(text="This request must use the released slot.")
        worker = Worker(settings, owner="cancel-test-worker")

        async def run_worker_and_cancel() -> None:
            run_task = asyncio.create_task(worker.run())
            try:
                assert await asyncio.to_thread(server.wait_for_requests, 1, 5), {
                    "first": service.db.get(first["task_id"]),
                    "second": service.db.get(second["task_id"]),
                }
                assert service.db.get(first["task_id"])["status"] == "running"
                service.cancel(first["task_id"])
                await _wait_until(
                    lambda: service.db.get(first["task_id"])["status"] == "cancelled"
                )
                await _wait_until(
                    lambda: service.db.get(second["task_id"])["status"] == "completed"
                )
            finally:
                server.release()
                worker.stop()
                await asyncio.wait_for(run_task, timeout=4)

        asyncio.run(run_worker_and_cancel())

        assert server.wait_for_requests(2, timeout=2)
        assert service.db.get(first["task_id"])["status"] == "cancelled"
        assert service.db.get(second["task_id"])["outcome"] == "polished"
        with service.db.connect() as connection:
            cancelled_attempts = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE task_id=? AND error_code='CANCELLED'",
                (first["task_id"],),
            ).fetchone()[0]
        assert cancelled_attempts == 1
        assert [request["model"] for request in server.requests] == ["primary", "primary"]


def test_first_token_timeout_retries_with_the_secondary_model(tmp_path: Path) -> None:
    with MockResponsesServer(model_delays={"first-token-slow": 0.4}) as server:
        settings = make_settings(
            tmp_path,
            server.url,
            first_token_timeout=0.12,
            total_timeout=3,
            inactivity_timeout=1,
            retry_count=1,
            worker_idle_seconds=2,
        )
        service = Service(settings, start_worker=False)
        submitted = service.submit(
            text="This sentence should finish with the backup model.",
            primary_model="mock/first-token-slow",
            secondary_model="mock/secondary",
        )
        worker = Worker(settings, owner="first-token-timeout-worker")

        async def run_worker() -> None:
            run_task = asyncio.create_task(worker.run())
            try:
                await _wait_until(
                    lambda: service.db.get(submitted["task_id"])["status"] == "completed"
                )
            finally:
                worker.stop()
                await asyncio.wait_for(run_task, timeout=4)

        asyncio.run(run_worker())

        assert server.wait_for_requests(2, timeout=5)
        result = service.result(submitted["task_id"], inline_text=True)
        assert result["outcome"] == "polished"
        assert result["text"] == "This sentence should finish with the backup model."
        assert [request["model"] for request in server.requests] == [
            "first-token-slow",
            "secondary",
        ]
        with service.db.connect() as connection:
            attempts = connection.execute(
                "SELECT attempt, model, success, error_code FROM attempts "
                "WHERE task_id=? ORDER BY attempt",
                (submitted["task_id"],),
            ).fetchall()
        assert [(row["attempt"], row["model"], row["success"]) for row in attempts] == [
            (1, "first-token-slow", 0),
            (2, "secondary", 1),
        ]
        assert attempts[0]["error_code"] == "FIRST_TOKEN_TIMEOUT"
        assert attempts[1]["error_code"] is None


def test_empty_worker_exits_after_its_idle_timeout(tmp_path: Path) -> None:
    with MockResponsesServer() as server:
        settings = make_settings(
            tmp_path,
            server.url,
            worker_idle_seconds=0.05,
            lease_seconds=1,
        )
        worker = Worker(settings, owner="idle-test-worker")

        asyncio.run(asyncio.wait_for(worker.run(), timeout=2))

        with worker.db.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM workers WHERE name='main'"
            ).fetchone()[0] == 0
        assert server.requests == []


def test_expired_lease_recovers_but_does_not_exceed_attempt_budget(
    tmp_path: Path,
) -> None:
    with MockResponsesServer() as server:
        settings = make_settings(tmp_path, server.url, retry_count=1)
        service = Service(settings, start_worker=False)
        original = "The snapshot contains 18 participants and remains unchanged."
        submitted = service.submit(text=original)
        lost = service.db.claim("lost-worker", settings)
        assert lost is not None
        assert service.db.attempt_start(
            submitted["task_id"], "lost-worker", "mock", "primary", 2
        ) == 1
        assert service.db.attempt_start(
            submitted["task_id"], "lost-worker", "mock", "secondary", 2
        ) == 2
        service.db.update(submitted["task_id"], lease_until=time.time() - 1)

        recovered_worker = Worker(settings, owner="recovered-worker")
        recovered = service.db.claim("recovered-worker", settings)
        assert recovered is not None
        assert recovered["task_id"] == submitted["task_id"]
        assert recovered["recovery_count"] == 1
        assert recovered["possible_duplicate_billing"] == 1
        assert recovered["error_code"] == "WORKER_LOST"

        asyncio.run(recovered_worker.execute(recovered))

        row = service.db.get(submitted["task_id"])
        assert row["status"] == "completed"
        assert row["outcome"] == "original_fallback"
        assert row["attempt_count"] == 2
        assert row["error_code"] == "WORKER_LOST"
        assert Path(row["result_path"]).read_text(encoding="utf-8") == original
        with service.db.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE task_id=?",
                (submitted["task_id"],),
            ).fetchone()[0] == 2
        assert server.requests == []
