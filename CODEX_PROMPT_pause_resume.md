Implement Pause/Resume/Delete lifecycle for TradingAgents analyses:

## Task 1: Immediate Cooperative Pausing & Cancellation

In `tradingagents/llm_clients/llm_trace.py`:
- Define custom exception classes:
  - `class AnalysisCancelled(Exception): pass`
  - `class AnalysisPaused(Exception): pass`
- Define a thread-local checker:
  ```python
  def check_interrupted() -> None:
      """Check if the current run has been cancelled or paused."""
      if not is_capturing():
          return
      run_id = getattr(_local, "run_id", None)
      if not run_id:
          return
      with _global_lock:
          info = _active_runs_registry.get(run_id)
          if info:
              if info.get("cancelled"):
                  raise AnalysisCancelled("Analysis cancelled by user")
              if info.get("paused"):
                  raise AnalysisPaused("Analysis paused by user")

  def set_run_interrupted_flag(run_id: str, flag_name: str, value: bool) -> None:
      with _global_lock:
          if run_id in _active_runs_registry:
              _active_runs_registry[run_id][flag_name] = value
  ```
- Call `check_interrupted()` inside:
  - `_execute_with_retry` (at the start and inside any sleep/retry loops so they are immediately interrupted)
  - `ToolNodeSpy.invoke` (at the start)
  - `StructuredSpy.invoke` (at the start)
- This allows immediate interruption of blocking LLM calls, tool calls, and retries.

## Task 2: Backend Pause & Resume & Delete

In `webapp/runner.py`:
- Import `AnalysisCancelled` and `AnalysisPaused` from `tradingagents.llm_clients.llm_trace`.
- In `AnalysisRunner`:
  - Update `stop_run(self, run_id: str) -> bool` to:
    - Set `"cancelled"` to True in `self._active_runs` and also call `set_run_interrupted_flag(run_id, "cancelled", True)`.
  - Add `pause_run(self, run_id: str) -> bool`:
    - Set `"paused"` to True in `self._active_runs` and call `set_run_interrupted_flag(run_id, "paused", True)`.
  - Update `_check_cancelled(self, run_id: str) -> None`:
    - Raise `AnalysisCancelled` if cancelled, or `AnalysisPaused` if paused.
  - In `_execute_job`:
    - Set `config["checkpoint_enabled"] = True` before creating the `TradingAgentsGraph` instance so checkpoints are always captured.
    - Catch `AnalysisPaused` exception:
      - Log `"⏸️ Analysis paused at user request. State recorded."`
      - Update run status to `"paused"` in DB (using `update_run_status(run_id, "paused", completed_at=datetime.now(timezone.utc).isoformat())`)
    - Catch `AnalysisCancelled`:
      - Log `"🚫 Analysis cancelled at user request."`
      - Update run status to `"cancelled"` in DB.
  - Add `resume_run(self, run_id: str, ticker: str, trade_date: str, trigger: str) -> tuple[bool, str]`:
    - Verify ticker is not currently running.
    - Put ticker in `self._in_flight_tickers`.
    - Update run status to `"running"` in DB (clear `completed_at`).
    - Load previous logs from DB, append `"[HH:MM:SS] 🔄 Resuming analysis...\n"`, and set it as `log_buffer` in `self._active_runs[run_id]`.
    - Set `"cancelled"` and `"paused"` to False in `self._active_runs[run_id]`.
    - Submit `self._execute_job` to the executor.

## Task 3: API endpoints

In `webapp/api.py`:
- Replace the `POST /api/runs/{run_id}/stop` endpoint with `POST /api/runs/{run_id}/pause`:
  - Calls `runner.pause_run(run_id)`. Returns success/failure.
- Add `POST /api/runs/{run_id}/resume`:
  - Fetch run from DB. Verify status is `"paused"`.
  - Check `runner.is_ticker_running(run["ticker"])`. If so, raise 400.
  - Call `runner.resume_run(...)`.
- Add `POST /api/runs/{run_id}/stop` (or keep it/alias it to pause if needed, but let's have a stop endpoint too if tests require it, or just keep it as cancellation). Wait, let's keep `stop` endpoint as stop/cancel (calls `runner.stop_run()`), and add `pause` as a separate action.
  Wait, the prompt says "改Stop按钮为Pause按钮... 用户可以Resume这个Analysis". So let's have both `/stop` (calling stop_run) and `/pause` (calling pause_run) in api.py, so both are supported!

## Task 4: Frontend UI Updates

In `webapp/static/app.js`:
- In the in-flight banner, change the Stop button next to each in-flight ticker pill to a Pause button. On click, call `pauseAnalysis(runId)`.
- In run card rendering (`renderRuns`):
  - If status is `"running"`, show a "⏸️ Pause" button that calls `pauseAnalysis(run.id)`.
  - If status is `"paused"`:
    - Show badge: `<span class="badge badge-paused" style="background-color: rgba(245, 158, 11, 0.15); color: #F59E0B; border: 1px solid rgba(245, 158, 11, 0.4);">PAUSED</span>`.
    - Show a prominent "**▶ Resume**" button (using `btn-success btn-sm`) next to Summary button that calls `resumeAnalysis(run.id)`.
    - Render the trashcan 🗑 delete button so users can delete a paused analysis card!
- Define `pauseAnalysis(runId)`:
  - POST to `/api/runs/{runId}/pause`.
  - Show toast: `"Pause requested."`.
- Define `resumeAnalysis(runId)`:
  - POST to `/api/runs/{runId}/resume`.
  - Show toast: `"Resuming analysis..."`.
- Ensure all other status display and duration timing work correctly.

Ensure tests are updated and pass.
