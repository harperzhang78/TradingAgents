# SPEC: Lighter Dark Theme + Per-Step Analysis Time

Two small, self-contained enhancements to the trading dashboard webapp. Keep changes
minimal and consistent with existing code style. Do NOT rewrite the whole CSS or app.js —
make targeted edits.

## Context
- Backend: FastAPI app in `webapp/`. Frontend: `webapp/static/app.js` + `webapp/static/style.css` (also mirrored in `webapp/templates/index.html`).
- The decision card / run summary is built by `summarize_run()` in `webapp/summary.py`. It returns
  `{"overall", "confidence", "steps":[{n,title,who,what,key_find,llm_count,tools,...}]}` (5 steps).
  The frontend renders it in `renderModalSummary` in `webapp/static/app.js` (the `.decision-timeline`
  with `.timeline-step` rows, each having `.step-header-row`, `.step-title`, `.chip-stat` pills).
- Every LLM/tool call row in `webapp/db.py` (table `llm_calls`) has: `seq`, `node`, `agent`, `kind`,
  `tool_name`, `ok`, `latency_ms`, and `ts` (ISO timestamp string). `get_llm_calls(run_id)` returns
  them ordered by seq. `summarize_run()` already receives `llm_calls` and buckets them into steps
  via `_is_step_call(c, step_num)` (step 1..5).
- Run rows have `started_at` and `completed_at` (ISO strings).

## Change 1 — Lighten the dark theme (still a dark theme)
In `webapp/static/style.css`, the palette is defined in `:root` at the top. Lighten the surfaces a
noticeable but tasteful amount so it feels less harsh/pitch-black, while clearly remaining a dark theme:
- Raise the main background `--bg-main` (currently `#0B0F19`) to a slightly lighter deep slate, e.g. `#111826` or `#131B2B`.
- Raise `--bg-card` (currently `#111827`) to e.g. `#1A2436` / `#1C2740`.
- Raise `--bg-card-hover`, `--bg-input`, `--border-color`, `--border-highlight` proportionally lighter.
- Keep `--text-main` bright and `--text-muted`/`--text-dim` readable on the new backgrounds (may nudge
  `--text-dim` slightly lighter). Preserve accent/success/danger/warning/info colors.
- Also update the few **hardcoded** dark hexes that would clash if left as-is (search for `#0B0F19`,
  `#111827`, `#0E1524`, `#030712`, `#1e293b`, `#111827` in `.run-item-card`, `.card-header`,
  `.autocomplete-dropdown`, `.terminal-logs`, `.pre-call`, `.metrics-row` etc.) and bump them up to
  match the new lighter palette. Keep `.terminal-logs`/`.pre-call` the darkest (they are code/log views).
- Ensure contrast stays comfortable (WCAG-ish) — text must remain clearly readable.
- After CSS edits, bump the cache-busting version string in BOTH `webapp/templates/index.html` and
  `webapp/static/index.html` (currently `?v=20260921_v4`) to `?v=20260921_v5` for both `style.css`
  and `app.js` links.

## Change 2 — Show time spent per analysis step on the decision card
Goal: each of the 5 steps in the decision card should display how long that step took (e.g. "34s" or
"1m 12s"), computed from the recorded LLM/tool call timestamps.

- In `summarize_run()` (`webapp/summary.py`), for each step 1..5 compute a `duration` (seconds) from
  the step's `llm_calls`. Robust approach: parse each call's `ts`; for a step's calls, elapsed ≈
  (max ts − min ts) across the step's calls plus the last call's `latency_ms`/1000 — but if a step has
  only one call (or `ts` missing), fall back to summing that step's `latency_ms`/1000. If a step has
  no calls / no timing data, set duration to None. Assign the result to each step dict as `"duration"`
  (a float of seconds, or None). Put a small helper like `_step_duration(seconds)` to format into
  human-readable (e.g. 45s / 1m 12s) — or store raw seconds and format in the frontend. Your choice,
  but make it robust to missing/malformed `ts` (ISO 8601, possibly with 'Z').
- In the frontend `renderModalSummary` (`webapp/static/app.js`), render the duration per step as a small
  `.chip-stat` pill in each step's `.step-meta-pills` row, e.g. a chip with a clock icon "⏱ 34s" (or
  "—" / hide when the step is pending or has no timing data). For `pending` steps (state == 'pending')
  do NOT show a duration. Keep it visually consistent with the existing `.chip-stat` pills.
- Add/extend unit tests in the appropriate `tests/` file that verify `summarize_run` populates a
  per-step `duration` from synthetic `llm_calls` with `ts`/`latency_ms`, and that a step with no calls
  yields duration None. Keep tests isolated (use the existing `isolate_test_db` fixture pattern).

## Definition of done
1. Full test suite passes: run `cd ~/Projects/TradingAgents && .venv/bin/pytest -q` (baseline is 1051
   passed, 2 skipped — you may add a few more).
2. No syntax errors in CSS/JS (the app must still boot).
3. Theme is lighter but still clearly dark; contrast readable.
4. Each decision-card step shows its elapsed time as a pill; pending steps show no time.
5. Cache-bust version bumped in both index.html files.

Report a concise summary of the exact edits you made (files + what changed) when done.
