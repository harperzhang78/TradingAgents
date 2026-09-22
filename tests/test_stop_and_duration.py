"""Cooperative analysis cancellation and elapsed time reporting."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from webapp import db
from webapp.app import app
from webapp.runner import AnalysisRunner

client = TestClient(app)


@pytest.fixture
def runner(monkeypatch):
    instance = AnalysisRunner()
    monkeypatch.setattr('webapp.api.runner', instance)
    yield instance
    instance._executor.shutdown(wait=True)


def seed_active(runner, run_id='active'):
    db.create_run(run_id, 'AAPL', '2026-09-21')
    runner._active_runs[run_id] = {
        'ticker': 'AAPL', 'started_at': datetime.now(timezone.utc).isoformat(),
        'log_buffer': '',
    }
    runner._in_flight_tickers.add('AAPL')


def test_stop_run_api(runner):
    seed_active(runner)
    response = client.post('/api/runs/active/stop')
    assert response.status_code == 200
    assert response.json()['success'] is True
    assert runner._active_runs['active']['cancelled'] is True
    assert db.get_run('active')['status'] == 'running'
    assert client.post('/api/runs/missing/stop').status_code == 404
    db.create_run('finished', 'MSFT', '2026-09-21')
    db.update_run_status('finished', 'completed')
    assert client.post('/api/runs/finished/stop').status_code == 404


def test_cancelled_job_cleans_up(runner, monkeypatch):
    seed_active(runner)
    lookup = MagicMock()
    monkeypatch.setattr('webapp.runner.get_alpaca_trading_client', lookup)
    runner.stop_run('active')
    runner._execute_job('active', 'AAPL', '2026-09-21', 'manual')
    run = db.get_run('active')
    assert run['status'] == 'cancelled'
    assert run['completed_at']
    assert 'cancelled' in run['log_output']
    assert not runner.get_in_flight_tickers()
    assert not runner.stop_run('active')
    lookup.assert_not_called()


def test_cancel_between_steps(runner, monkeypatch):
    seed_active(runner)
    def lookup():
        runner.stop_run('active')
        raise RuntimeError('No account')
    monkeypatch.setattr('webapp.runner.get_alpaca_trading_client', lookup)
    parse = MagicMock()
    monkeypatch.setattr('webapp.runner.parse_trader_decision', parse)
    runner._execute_job('active', 'AAPL', '2026-09-21', 'manual')
    assert db.get_run('active')['status'] == 'cancelled'
    parse.assert_not_called()


def test_duration_calculation(monkeypatch):
    now = datetime.now(timezone.utc)
    runs = [
        {'id': 'done', 'status': 'completed', 'started_at': '2026-09-21T10:00:00+00:00', 'completed_at': '2026-09-21T10:03:12+00:00'},
        {'id': 'running', 'status': 'running', 'started_at': (now - timedelta(seconds=45)).isoformat()},
        {'id': 'unknown', 'status': 'failed', 'started_at': now.isoformat()},
    ]
    monkeypatch.setattr('webapp.api.get_runs', lambda **kwargs: runs)
    response = client.get('/api/runs')
    assert response.status_code == 200
    done, running, unknown = response.json()
    assert done['duration_seconds'] == 192
    assert 45 <= running['duration_seconds'] < 48
    assert unknown['duration_seconds'] is None


def test_in_flight_elapsed(runner):
    seed_active(runner)
    runner._active_runs['active']['started_at'] = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
    assert 65 <= runner.get_in_flight_details()[0]['elapsed_seconds'] < 68


def test_frontend_pause_resume_buttons():
    js = Path('webapp/static/app.js').read_text()
    assert '⏸️ Pause</button>' in js
    assert '▶ Resume</strong></button>' in js
    assert '/api/runs/${encodeURIComponent(runId)}/pause' in js
    assert '/api/runs/${encodeURIComponent(runId)}/resume' in js
    assert '/api/runs/${encodeURIComponent(runId)}/stop' in js
    assert "method: 'POST'" in js


def test_frontend_duration_display():
    js = Path('webapp/static/app.js').read_text()
    assert 'function formatDuration(seconds)' in js
    assert 'formatDuration(run.duration_seconds)' in js
    assert 'formatDuration(f.elapsed_seconds)' in js


def test_graph_cancels_between_phases():
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from webapp.runner import AnalysisCancelled
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.create_run_state = MagicMock(return_value={})
    graph.propagator = MagicMock()
    graph.propagator.get_graph_args.return_value = {}
    graph.checkpoint_input = lambda value: value
    graph.graph = MagicMock()
    phases = []
    def steps(*args, **kwargs):
        phases.append('first')
        yield {'messages': []}
        phases.append('second')
        yield {'messages': []}
    graph.graph.stream.side_effect = steps
    def check():
        if phases:
            raise AnalysisCancelled()
    with pytest.raises(AnalysisCancelled):
        graph._run_graph('AAPL', '2026-09-21', check_cancelled=check)
    assert phases == ['first']


def test_pause_job_and_resume(runner, monkeypatch):
    from tradingagents.llm_clients.llm_trace import AnalysisPaused
    seed_active(runner)
    db.append_run_log('active', 'previous logs\n')
    assert client.post('/api/runs/active/pause').status_code == 200
    with pytest.raises(AnalysisPaused):
        runner._check_cancelled('active')
    runner._execute_job('active', 'AAPL', '2026-09-21', 'manual')
    paused = db.get_run('active')
    assert paused['status'] == 'paused'
    assert paused['completed_at']
    assert not runner.is_ticker_running('AAPL')
    submit = MagicMock()
    monkeypatch.setattr(runner._executor, 'submit', submit)
    response = client.post('/api/runs/active/resume')
    assert response.status_code == 200
    resumed = db.get_run('active')
    assert resumed['status'] == 'running'
    assert resumed['completed_at'] is None
    assert resumed['log_output'].startswith('previous logs\n')
    assert 'Resuming analysis...' in resumed['log_output']
    assert runner.get_run_memory_logs('active') == resumed['log_output']
    assert runner.is_ticker_running('AAPL')
    runner._check_cancelled('active')
    submit.assert_called_once_with(runner._execute_job, 'active', 'AAPL', '2026-09-21', 'manual')
    assert client.post('/api/runs/active/resume').status_code == 400


def test_resume_rejects_conflicts(runner):
    assert client.post('/api/runs/missing/resume').status_code == 404
    assert client.post('/api/runs/missing/pause').status_code == 404
    seed_active(runner)
    db.create_run('paused', 'AAPL', '2026-09-21')
    db.update_run_status('paused', 'paused')
    assert client.post('/api/runs/paused/resume').status_code == 400
    assert db.get_run('paused')['status'] == 'paused'


def test_resume_submission_failure_restores_paused(runner, monkeypatch):
    db.create_run('paused', 'AAPL', '2026-09-21')
    db.update_run_status('paused', 'paused', completed_at='2026-09-21T10:00:00+00:00')
    monkeypatch.setattr(runner._executor, 'submit', MagicMock(side_effect=RuntimeError('closed')))
    with pytest.raises(RuntimeError):
        runner.resume_run('paused', 'AAPL', '2026-09-21', 'manual')
    assert db.get_run('paused')['status'] == 'paused'
    assert not runner.is_ticker_running('AAPL')
    assert not runner._active_runs


@pytest.mark.parametrize('flag,exception_name', [('paused', 'AnalysisPaused'), ('cancelled', 'AnalysisCancelled')])
def test_trace_interrupts_retry_sleep(monkeypatch, flag, exception_name):
    from tradingagents.llm_clients import llm_trace as trace
    trace.start_capture(run_id='retry')
    invoke = MagicMock(side_effect=RuntimeError('429 rate limit'))
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        trace.set_run_interrupted_flag('retry', flag, True)
    monkeypatch.setattr(trace.time, 'sleep', sleep)
    try:
        with pytest.raises(getattr(trace, exception_name)):
            trace._execute_with_retry(invoke, [], None, 'model', 'quick', {})
        invoke.assert_called_once()
        assert sleeps == [0.1]
    finally:
        trace.stop_capture()


def test_spies_skip_calls_when_paused():
    from tradingagents.llm_clients import llm_trace as trace
    trace.start_capture(run_id='paused')
    underlying = MagicMock()
    spies = [trace.LLMSpy(underlying), trace.ToolNodeSpy(underlying),
             trace.StructuredSpy(underlying, dict, trace.LLMSpy(underlying))]
    try:
        trace.set_run_interrupted_flag('paused', 'paused', True)
        for spy in spies:
            with pytest.raises(trace.AnalysisPaused):
                spy.invoke({})
        underlying.invoke.assert_not_called()
    finally:
        trace.stop_capture()
    trace.check_interrupted()


def test_interrupt_reaches_langgraph_worker():
    from langgraph.graph import StateGraph, START, END
    from typing import TypedDict
    from tradingagents.llm_clients import llm_trace as trace

    class State(TypedDict):
        value: int

    def node(state):
        trace.check_interrupted()
        return {'value': state['value'] + 1}

    graph = StateGraph(State)
    graph.add_node('node', node)
    graph.add_edge(START, 'node')
    graph.add_edge('node', END)
    trace.start_capture(run_id='graph')
    try:
        trace.set_run_interrupted_flag('graph', 'paused', True)
        with pytest.raises(trace.AnalysisPaused):
            list(graph.compile().stream({'value': 0}))
    finally:
        trace.stop_capture()
