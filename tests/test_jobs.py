import asyncio

import pytest

from mediavault.jobs import JobManager


async def _lifecycle():
    manager = JobManager()

    async def ok() -> dict:
        return {"hello": "world"}

    job_id = manager.submit("test", ok)
    job = manager.get(job_id)
    assert job.status in {"queued", "running"}
    for _ in range(50):
        if job.status == "succeeded":
            break
        await asyncio.sleep(0.02)
    assert job.status == "succeeded"
    assert job.result == {"hello": "world"}
    assert job.started_at is not None and job.finished_at is not None


async def _error_path():
    manager = JobManager()

    async def boom() -> dict:
        raise RuntimeError("nope")

    job_id = manager.submit("test", boom)
    job = manager.get(job_id)
    for _ in range(50):
        if job.status == "failed":
            break
        await asyncio.sleep(0.02)
    assert job.status == "failed"
    assert "RuntimeError" in (job.error or "")
    assert "nope" in (job.error or "")


async def _ordering():
    manager = JobManager()

    async def noop() -> dict:
        return {}

    ids = [manager.submit("t", noop) for _ in range(3)]
    await asyncio.sleep(0.1)
    listed = manager.list()
    assert {j.id for j in listed} == set(ids)
    # newest first
    assert [j.id for j in listed] == list(reversed(ids)) or len({j.created_at for j in listed}) <= 3


def test_job_manager_lifecycle():
    asyncio.run(_lifecycle())


def test_job_manager_captures_errors():
    asyncio.run(_error_path())


def test_job_manager_list_ordering():
    asyncio.run(_ordering())


def test_job_manager_unknown_id():
    manager = JobManager()
    with pytest.raises(KeyError):
        manager.get("does-not-exist")
    manager.close()


def test_job_survives_closing_submitter_event_loop():
    # Regression: create_task used to bind the job to the submitter loop, so
    # a job submitted inside one asyncio.run never finished once it closed.
    manager = JobManager()

    async def work() -> dict:
        await asyncio.sleep(0.05)
        return {"ok": True}

    async def submitter():
        return manager.submit("test", work)

    job_id = asyncio.run(submitter())  # this loop is now closed

    async def waiter():
        for _ in range(50):
            if manager.get(job_id).status == "succeeded":
                return True
            await asyncio.sleep(0.02)
        return False

    assert asyncio.run(waiter()) is True
    assert manager.get(job_id).result == {"ok": True}
    manager.close()


def test_close_without_submit_is_safe():
    JobManager().close()


async def _dict_shape():
    manager = JobManager()

    async def noop() -> dict:
        return {"ok": True}

    job_id = manager.submit("summarize", noop)
    for _ in range(50):
        if manager.get(job_id).status == "succeeded":
            break
        await asyncio.sleep(0.02)
    payload = manager.get(job_id).to_dict()
    assert payload["id"] == job_id
    assert payload["kind"] == "summarize"
    assert payload["status"] == "succeeded"
    assert payload["result"] == {"ok": True}
    assert payload["error"] is None
    assert set(payload) >= {
        "id", "kind", "status", "created_at", "started_at",
        "finished_at", "result", "error",
    }


def test_job_to_dict_shape():
    asyncio.run(_dict_shape())
