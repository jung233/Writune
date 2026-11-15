from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from writune.config import Settings
from writune.service import Service

from .mock_responses import MockResponsesServer, make_settings


def _submit_from_process(settings_json: str, process_index: int, barrier, results) -> None:
    try:
        settings = Settings.model_validate_json(settings_json)
        service = Service(settings, start_worker=False)
        barrier.wait(timeout=30)
        shared = service.submit(
            text="All processes submit this same idempotent payload.",
            idempotency_key="shared-cross-process-request",
        )
        task_ids = [shared["task_id"]]
        for item_index in range(2):
            submitted = service.submit(
                text=f"Unique process {process_index} item {item_index}.",
                idempotency_key=f"process-{process_index}-item-{item_index}",
            )
            task_ids.append(submitted["task_id"])
        results.put({"process_index": process_index, "task_ids": task_ids})
    except BaseException as error:
        results.put(
            {
                "process_index": process_index,
                "error": f"{type(error).__name__}: {error}",
            }
        )


def _service(tmp_path: Path, server: MockResponsesServer, **settings_options) -> Service:
    return Service(make_settings(tmp_path, server.url, **settings_options), start_worker=False)


def test_concurrent_claims_are_atomic_and_obey_the_global_limit(tmp_path: Path) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server, max_concurrency=3)
        service.submit_batch(items=[f"Queued paragraph {index}." for index in range(12)])

        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = list(
                pool.map(
                    lambda index: service.db.claim(f"owner-{index}", service.settings),
                    range(12),
                )
            )

        claimed = [row for row in claims if row is not None]
        assert len(claimed) == 3
        assert len({row["task_id"] for row in claimed}) == 3
        with service.db.connect() as connection:
            running = connection.execute(
                "SELECT task_id, COUNT(*) AS copies FROM tasks "
                "WHERE status='running' GROUP BY task_id"
            ).fetchall()
            queued = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='queued'"
            ).fetchone()[0]
        assert len(running) == 3
        assert all(row["copies"] == 1 for row in running)
        assert queued == 9


def test_queue_bound_rejects_an_extra_submission_without_partial_rows(tmp_path: Path) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server, max_queue_size=2)
        service.submit(text="First queued paragraph.")
        service.submit(text="Second queued paragraph.")

        with pytest.raises(ValueError, match="QUEUE_FULL"):
            service.submit(text="This paragraph must not be inserted.")

        with service.db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='queued'"
            ).fetchone()[0] == 2


def test_idempotency_replays_the_task_and_rejects_a_conflicting_payload(
    tmp_path: Path,
) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server)
        first = service.submit(text="Stable input.", idempotency_key="request-1")
        replay = service.submit(text="Stable input.", idempotency_key="request-1")

        assert replay["task_id"] == first["task_id"]
        with service.db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1

        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            service.submit(text="Changed input.", idempotency_key="request-1")
        assert service.db.get(first["task_id"])["status"] == "queued"


def test_independent_processes_submit_without_losing_idempotent_or_unique_tasks(
    tmp_path: Path,
) -> None:
    worker_count = 6
    with MockResponsesServer() as server:
        settings = make_settings(tmp_path, server.url)
        parent_service = Service(settings, start_worker=False)
        settings_data = settings.model_dump(mode="json")
        for name, provider in settings.providers.items():
            settings_data["providers"][name]["api_key"] = provider.api_key.get_secret_value()
        settings_json = json.dumps(settings_data)

        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(worker_count)
        results = context.Queue()
        processes = [
            context.Process(
                target=_submit_from_process,
                args=(settings_json, process_index, barrier, results),
            )
            for process_index in range(worker_count)
        ]
        try:
            for process in processes:
                process.start()

            outcomes = [results.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(timeout=10)
            assert all(not process.is_alive() for process in processes)
            assert all(process.exitcode == 0 for process in processes)
            assert all("error" not in outcome for outcome in outcomes), outcomes

            shared_ids = {outcome["task_ids"][0] for outcome in outcomes}
            unique_ids = [
                task_id
                for outcome in outcomes
                for task_id in outcome["task_ids"][1:]
            ]
            assert len(shared_ids) == 1
            assert len(unique_ids) == worker_count * 2
            assert len(set(unique_ids)) == worker_count * 2
            assert shared_ids.isdisjoint(unique_ids)

            with parent_service.db.connect() as connection:
                rows = connection.execute(
                    "SELECT task_id, idempotency_key FROM tasks ORDER BY idempotency_key"
                ).fetchall()
            assert len(rows) == worker_count * 2 + 1
            assert len({row["idempotency_key"] for row in rows}) == worker_count * 2 + 1
            assert {row["task_id"] for row in rows} == shared_ids | set(unique_ids)
            assert server.requests == []
        finally:
            for process in processes:
                if process.pid is not None and process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=3)
            results.close()
            results.join_thread()


def test_batch_keeps_input_order_and_positions(tmp_path: Path) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server)
        submitted = service.submit_batch(
            items=[
                {"text": "Third in source order.", "priority": 0},
                {"text": "Second in source order.", "priority": 4},
                {"text": "First in source order.", "priority": 4},
            ]
        )

        assert submitted["accepted_count"] == 3
        assert len(submitted["task_ids"]) == 3
        with service.db.connect() as connection:
            rows = connection.execute(
                "SELECT task_id, position, original_path FROM tasks "
                "WHERE batch_id=? ORDER BY position",
                (submitted["batch_id"],),
            ).fetchall()
        assert [row["task_id"] for row in rows] == submitted["task_ids"]
        assert [row["position"] for row in rows] == [0, 1, 2]
        assert [Path(row["original_path"]).read_text(encoding="utf-8") for row in rows] == [
            "Third in source order.",
            "Second in source order.",
            "First in source order.",
        ]


def test_failed_batch_is_atomic_when_queue_capacity_is_insufficient(
    tmp_path: Path,
) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server, max_queue_size=2)

        with pytest.raises(ValueError, match="QUEUE_FULL"):
            service.submit_batch(items=["one", "two", "three"])

        with service.db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert list(service.task_root.iterdir()) == []


def test_batch_idempotency_conflict_does_not_insert_earlier_items(
    tmp_path: Path,
) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server)
        existing = service.submit(text="Previously submitted second item.", idempotency_key="batch:1")

        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            service.submit_batch(
                items=["A new first item.", "A different second item."],
                idempotency_key="batch",
            )

        with service.db.connect() as connection:
            rows = connection.execute(
                "SELECT task_id, idempotency_key FROM tasks"
            ).fetchall()
        assert [row["task_id"] for row in rows] == [existing["task_id"]]
        assert [row["idempotency_key"] for row in rows] == ["batch:1"]
        assert len(list(service.task_root.iterdir())) == 1


def test_submissions_from_a_mutated_source_file_keep_independent_snapshots(
    tmp_path: Path,
) -> None:
    with MockResponsesServer() as server:
        service = _service(tmp_path, server)
        source = service.settings.allowed_workspaces[0] / "draft.md"
        source.write_text("Version one has 11 samples.", encoding="utf-8")
        first = service.submit(source_path=source)

        source.write_text("Version two has 22 samples.", encoding="utf-8")
        second = service.submit(source_path=source)

        first_row = service.db.get(first["task_id"])
        second_row = service.db.get(second["task_id"])
        first_snapshot = Path(first_row["original_path"])
        second_snapshot = Path(second_row["original_path"])
        assert first_snapshot != second_snapshot
        assert first_snapshot.read_text(encoding="utf-8") == "Version one has 11 samples."
        assert second_snapshot.read_text(encoding="utf-8") == "Version two has 22 samples."
        assert source.read_text(encoding="utf-8") == "Version two has 22 samples."
        assert "text" not in json.loads(first_row["payload"])
        assert first_row["source_path"] == second_row["source_path"] == str(source)
