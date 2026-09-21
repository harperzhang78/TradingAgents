# Spec: Progressive step display in the run summary (while a run is in progress)

## Problem
When a run has `status == "running"`, opening the Summary modal currently shows ALL 5
steps, each populated with **hardcoded fallback "key findings"** (e.g. step 2 shows the
generic "Debated growth catalysts…" text, step 4 shows "PM decision: Neutral / HOLD — …",
step 3 shows a HOLD text, step 5 shows advisory text). This is misleading: it looks like
every step already produced results, when in fact most have not even started yet.

## Goal
While a run is running, the summary timeline must reflect **actual progress**:
- Show steps **one by one as the pipeline reaches them**.
- A step that has **not been reached yet** must NOT show a fabricated key finding. It should
  appear as **pending / TBD / disabled** (dimmed, clearly "not started yet").
- The step the pipeline is **currently working on** should be visually marked **active / in
  progress** (e.g. a spinner + "IN PROGRESS" badge).
- Steps that **already completed** show their real key findings (existing behavior).

## Scope
1. **Backend — `webapp/summary.py` (`summarize_run`)**
   The API (`webapp/api.py::get_run_summary`) already feeds the live in-progress calls into
   `summarize_run` (via `get_llm_calls` / `runner.get_run_live_calls`). So all logic belongs
   inside `summarize_run`, driven by `run["status"]`.

   When `status == "running"` (or `"advisory"` while still in progress), compute a per-step
   progress state and tag each step dict with a new key **`"state"`** of one of:
   - `"done"`  — this step has finished (real content available).
   - `"active"`— this is the step currently being executed.
   - `"pending"`— the pipeline has not reached this step yet.

   Determining which steps are reached (use the SAME node→step mapping already in this file,
   `_map_node_to_step`, and the same per-step call grouping used for `llm_count`):
   - A step is **reached** if it has **at least one recorded call** (llm or tool) for its nodes.
   - Let `active_step` = the **highest-numbered step that is reached**.
     - If no step has any calls yet, `active_step` = 1.
   - Steps with `n < active_step` → `"done"`.
   - Step `n == active_step` → `"active"`.
   - Steps with `n > active_step` → `"pending"`.
   - Step 5 (Execution/Advisory) is derived from order/recommendation, not from llm_calls:
     while running it should be `"pending"` unless an order/recommendation was actually recorded.

   Key-finding text under the running state:
   - `"done"` steps: keep the existing real key-finding extraction. If, unexpectedly, no real
     content is available, use a neutral placeholder (e.g. "Completed") — **never** the old
     fabricated verdict text.
   - `"active"` step: if a non-empty response for that step is already recorded, use it;
     otherwise use a short "In progress…" style placeholder.
   - `"pending"` steps: set `key_find` to `""` (empty) — the frontend renders the "not started"
     placeholder. Do NOT fill these with the old fallback strings.
   - Do **not** run the existing `status == "failed"` failed/skipped block when running.

   Keep the returned shape backward-compatible: each step keeps `n`, `title`, `who`, `what`,
   `key_find`, `llm_count` (and `tools` for step 1); just **add** the new `state` key. Do not
   break the completed/failed paths (their steps may simply omit `state`, or you may set them
   all to `"done"` except existing `failed`/`skipped` handling).

2. **Frontend — `webapp/static/app.js` (`renderModalSummary`) + `webapp/static style.css`**
   The modal already polls and re-renders while `run.status === "running"`, so rendering the
   new states is enough.
   - For each step, read `step.state`:
     - `"active"` → add class `step-active`; show a spinner and an "IN PROGRESS" badge in the
       step header; show `key_find` if present, else an "In progress…" line.
     - `"pending"` → add class `step-pending`; dim/disable it (reduced opacity, dashed border);
       show a muted "Not started yet" placeholder instead of a key-finding box.
     - `"done"` (or no `state`) → current rendering, unchanged.
   - Add CSS: `.step-active` (accent highlight, e.g. a subtle pulsing left border or glow) and
     `.step-pending` (muted). Reuse existing spinner markup/classes already in the file.
   - Bump the static asset version query strings in `webapp/templates/index.html` (and any
     `?v=`/`?t=` references in `webapp/static/index.html`) so the browser loads the new
     `app.js`/`style.css` (the note references `?v=20260921_v2`; bump to a new value).

## Verification
- Extend `tests/test_dashboard_fixes.py` (or add a focused test file) with unit tests for the
  NEW running behavior:
  - A run with `status="running"` and only step-1 calls → steps: [done, active/pending...];
    specifically step 1 reached, and no step shows a fabricated key finding; the highest
    reached step is `"active"`, later steps `"pending"` with empty `key_find`.
  - A run with `status="running"` and NO calls → step 1 `"active"`, steps 2-5 `"pending"`.
  - A run with `status="running"` and calls through step 3 → steps 1-2 `"done"`, 3 `"active"`,
    4-5 `"pending"`.
  - Completed/failed runs are unaffected (existing tests still pass).
- Run the full suite: `cd ~/Projects/TradingAgents && .venv/bin/pytest -q` — all must pass.

## Constraints
- Deterministic, no new LLM calls, no new external deps.
- Do not change the API route signatures or response envelope.
- Keep it small and surgical.
