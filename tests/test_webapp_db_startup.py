"""Startup cleanup must not interrupt runs owned by an existing server."""

import errno
import socket
from unittest.mock import MagicMock

import pytest

from webapp import db


@pytest.mark.parametrize("use_default", [False, True])
def test_init_db_preserves_running_runs_when_port_is_in_use(monkeypatch, use_default):
    before = db.create_run("active", "AAPL", "2026-09-21")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        monkeypatch.setattr(db, "DEFAULT_PORT", port)
        monkeypatch.setattr(db.sys, "argv", ["server"] if use_default else ["server", "--port", str(port)])
        # Repeated failed startups must remain harmless and still initialize defaults.
        with db.get_db() as conn:
            conn.execute("DELETE FROM settings")
        for _ in range(2):
            db.init_db()
        assert db.get_setting("lang") == "en"
        after = db.get_run("active")
        assert {key: after[key] for key in before} == before


def test_init_db_cleans_stale_runs_when_port_is_available(monkeypatch):
    # Port zero lets the OS choose an available port without a check/use race in the test.
    monkeypatch.setattr(db.sys, "argv", ["server", "--port", "0"])
    db.create_run("stale", "AAPL", "2026-09-21")
    db.create_run("done", "MSFT", "2026-09-21")
    db.update_run_status("done", "completed", completed_at="2026-09-21T00:00:00Z")
    done = db.get_run("done")

    db.init_db()

    stale = db.get_run("stale")
    assert stale["status"] == "failed"
    assert stale["completed_at"] is not None
    assert stale["error"] == "Run interrupted: server restarted while execution was in progress"
    assert db.get_run("done") == done


@pytest.mark.parametrize("args, expected", [
    ([], db.DEFAULT_PORT),
    (["--host", "0.0.0.0", "--port", "9123"], 9123),
    (["--port=9124"], 9124),
    (["--port"], db.DEFAULT_PORT),
    (["--port", "invalid"], db.DEFAULT_PORT),
    (["--port", "65536"], db.DEFAULT_PORT),
    (["--port", "-1"], db.DEFAULT_PORT),
])
def test_server_port_arguments(monkeypatch, args, expected):
    monkeypatch.setattr(db.sys, "argv", ["server", *args])
    assert db._server_port() == expected


def test_init_db_preserves_runs_and_closes_probe_on_bind_error(monkeypatch):
    db.create_run("active", "AAPL", "2026-09-21")
    socket_factory = MagicMock()
    probe = socket_factory.return_value.__enter__.return_value
    probe.bind.side_effect = OSError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(db.socket, "socket", socket_factory)

    db.init_db()

    assert db.get_run("active")["status"] == "running"
    socket_factory.return_value.__exit__.assert_called_once()
