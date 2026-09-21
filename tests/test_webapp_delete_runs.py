"""Run history deletion uses the isolated database from conftest."""

import pytest
from fastapi.testclient import TestClient

from webapp.app import app
from webapp import db
from webapp.runner import runner

client = TestClient(app)


def seed_run(run_id, status="completed", order_status="skipped"):
    db.create_run(run_id, "AAPL", "2026-09-20")
    db.update_run_status(run_id, status)
    rec_id = db.create_recommendation(
        run_id, "AAPL", "2026-09-20", "BUY", None,
        100, 90, 120, "5%", "Test recommendation",
    )
    db.create_order_record(
        run_id, rec_id, "AAPL", "buy", "market", 1,
        None, None, None, None, order_status,
    )
    db.insert_llm_calls(run_id, [{"seq": 1, "response": "Test response"}])


def assert_counts(run_id, expected):
    with db.get_db() as conn:
        for table in ("recommendations", "orders", "llm_calls"):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,),
            ).fetchone()[0] == expected


def test_delete_run_cascades():
    seed_run("delete-me")
    seed_run("keep-me")
    assert_counts("delete-me", 1)
    assert db.delete_run("delete-me") is True
    assert db.get_run("delete-me") is None
    assert_counts("delete-me", 0)
    assert db.get_run("keep-me") is not None
    assert_counts("keep-me", 1)
    assert db.delete_run("delete-me") is False


@pytest.mark.parametrize("status", ["completed", "failed", "advisory"])
def test_delete_run_api(status):
    seed_run("delete-me", status=status)
    response = client.delete("/api/runs/delete-me")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "run_id": "delete-me"}
    assert db.get_run("delete-me") is None
    assert_counts("delete-me", 0)


def test_delete_missing_run():
    assert client.delete("/api/runs/missing").status_code == 404


def test_delete_running_run():
    seed_run("running", status="running")
    response = client.delete("/api/runs/running")
    assert response.status_code == 400
    assert "running" in response.json()["detail"]
    assert db.get_run("running") is not None
    assert_counts("running", 1)


def test_delete_in_flight_run(monkeypatch):
    seed_run("active")
    monkeypatch.setattr(runner, "get_in_flight_details", lambda: [{"run_id": "active"}])
    assert client.delete("/api/runs/active").status_code == 400
    assert db.get_run("active") is not None
    assert_counts("active", 1)


@pytest.mark.parametrize("newer_order", [False, True])
def test_delete_submitted_run(newer_order):
    seed_run("submitted", order_status="submitted")
    if newer_order:
        db.create_order_record(
            "submitted", None, "AAPL", "buy", "market", 1,
            None, None, None, None, "failed",
        )
    response = client.delete("/api/runs/submitted")
    assert response.status_code == 400
    assert "submitted order" in response.json()["detail"]
    assert db.get_run("submitted") is not None
    with db.get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM orders WHERE run_id = 'submitted'",
        ).fetchone()[0] == (2 if newer_order else 1)
