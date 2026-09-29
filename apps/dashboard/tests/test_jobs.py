"""Unit tests for Dashboard in-process background jobs."""

from __future__ import annotations

import time

from app import jobs


def test_start_job_ok():
    def work():
        return "finished"

    job_id = jobs.start_job("test_ok", work)
    assert job_id
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = jobs.get_job(job_id)
        if status and status["status"] in {"ok", "error"}:
            break
        time.sleep(0.02)
    assert status is not None
    assert status["id"] == job_id
    assert status["kind"] == "test_ok"
    assert status["status"] == "ok"
    assert status["message"] == "finished"
    assert status["started_at"]
    assert status["finished_at"]


def test_start_job_error():
    def work():
        raise RuntimeError("boom")

    job_id = jobs.start_job("test_err", work)
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = jobs.get_job(job_id)
        if status and status["status"] in {"ok", "error"}:
            break
        time.sleep(0.02)
    assert status is not None
    assert status["status"] == "error"
    assert "boom" in status["message"]


def test_list_and_latest_by_kind():
    jobs.start_job("kind_a", lambda: "a")
    jobs.start_job("kind_b", lambda: "b")
    deadline = time.time() + 2
    while time.time() < deadline:
        latest = jobs.latest_job("kind_a")
        if latest and latest["status"] == "ok":
            break
        time.sleep(0.02)
    listed = jobs.list_jobs("kind_a")
    assert listed
    assert all(j["kind"] == "kind_a" for j in listed)
    assert jobs.latest_job("kind_a")["kind"] == "kind_a"
    assert jobs.get_job("missing") is None
