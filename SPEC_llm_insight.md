# SPEC: LLM Insight & Live Decision View (TradingAgents Web Dashboard)

## Goal
Let the dashboard user (a) **read every LLM call and response** made during a single
decision run, and (b) **watch decisions as they run** in a live view, with the
decision-making process summarized into clear, user-friendly steps and reasons.

Two workstreams:
- **A. Capture**: instrument the agent pipeline so that, for each run, every LLM
  call (which agent, full prompt sent, full response received, which data tools/APIs
  it used, model, timing, success/fail) is recorded and queryable.
- **B. Present**: new backend endpoints + a new UI that (1) shows a **summarized,
  plain-English decision pipeline** for any run, (2) exposes the **full raw LLM
  calls/responses** for a run (expandable), and (3) shows **in-flight runs live**
  (which agent is active now, calls appearing in real time).

Keep the existing dark theme, the existing run-logs modal, and full backward
compatibility with the CLI (`execute_order.py`, `main.py`) and existing web endpoints.
Do not commit secrets.

---

## Workstream A — Instrumentation (capture every LLM + tool call)

### A1. A per-run collector
Add a **thread-local collector** (new module, e.g. `tradingagents/llm_clients/llm_trace.py`
or `webapp/llm_trace.py`) with a small, dependency-free API:

- `start_capture(agent_labels: list[str] | None = None)` — begin capturing in the
  **current thread** (called at the start of a run's worker thread).
- `stop_capture()` — end capturing, clear the thread-local.
- `record(node, kind, request, response, ok, model, latency_ms, error)` — append one
  captured call to the current run's in-memory list. `kind` is one of
  `"llm"` (a model call) or `"tool"` (a data/API call the model requested).
- `get_captured()` — return the list for the current thread (used by the runner).
- A **node-name hook**: `set_current_node(name)` so the most recent LangGraph node
  (e.g. "Market Analyst", "Trader") is stamped onto subsequent records. The runner
  sets this as each node completes (it can derive it from the streamed state chunks —
  see the existing `debug` streaming loop in `graph/trading_graph.py` `_run_graph`).

The collector MUST be **no-op and zero-cost when capture is disabled** (i.e. when not
inside an active run) so that CLI/backtest usage is unaffected.

### A2. Wrap the LLMs
All agents obtain their LLMs from `graph/setup.py` `GraphSetup.setup_graph(...)`, which
calls the agent factories (e.g. `create_market_analyst(self.quick_thinking_llm)`,
`create_research_manager(self.deep_thinking_llm)`, ...). Inside each agent factory the
agent builds `llm = client.get_llm()` and, where structured output is used,
`structured_llm = bind_structured(llm, Schema, agent_name)` (see
`agents/utils/structured.py` `bind_structured` / `invoke_structured_or_freetext`).

Instrument at the **single choke points** rather than editing every agent file:

1. Wrap every `llm` the graph uses so that `llm.invoke(...)` is logged. The cleanest
   single place is to wrap the LLM instances **where the agents receive them**.
   Preferred: in `GraphSetup.setup_graph`, after the agent factories are created, the
   factories already close over `self.quick_thinking_llm` / `self.deep_thinking_llm`.
   The **recommended, minimal-diff approach**: add a thin **logging wrapper**
   (e.g. `tracing_llm = LLMSpy(llm, label)` exposing the same `invoke` /
   `with_structured_output` surface) and have each agent factory use the wrapped
   instance. Because the two LLMs are shared, the wrapper's `invoke` must record
   using `set_current_node` (thread-local) to know *which* agent made the call — the
   label alone ("quick"/"deep") is not enough.

   Concretely: `LLMSpy.invoke(input)` records a `kind="llm"` entry with the outgoing
   prompt (serialize the input: if it is a list of messages, capture each
   role+content; if a ChatPromptValue, use `.to_messages()`) and the response
   (`.content`, and, if present, the structured `parsed`/tool output), then delegates
   to the real `invoke`. Its `with_structured_output(schema)` returns a **wrapped**
   structured object whose `invoke` likewise records (capturing the schema/tool name
   plus the parsed result). Keep the same method surface the agents call.

   **Attribution**: since both the structured wrapper and the plain LLM are invoked
   from inside a given LangGraph node's function, stamp `set_current_node(node_name)`
   at the top of each agent node function (the nodes already know their own name —
   see `graph/setup.py` node names and the analyst specs). The wrapper reads the
   thread-local node name. If you prefer not to edit every node, you can instead set
   the current node from the streaming chunk in `_run_graph` (the `for chunk in
   self.graph.stream(...)` loop already knows `chunk` keys = node names). **Pick one
   consistent approach and make sure `node` is populated on every record.**

2. Capture **tool / data API calls**. The model calls tools via LangChain
   `ToolNode` / tool calls. Where possible, record each tool invocation the model
   requests (tool name + args + result, `kind="tool"`) so the user can see, e.g.,
   "Market Analyst called get_stock_data(NVDA) and get_indicators(...)". If the
   provider's tool calls are surfaced as `tool_calls` / `AIMessage.tool_calls` in
   `llm.invoke` responses, hook that in the same wrapper. If it is not practical to
   capture every tool result, at minimum record the **tool names + arguments** the
   model requested (from the response `tool_calls`), which is enough for the summary.
   Mark clearly if only the request (not the result) is captured.

Every captured entry is a JSON-serializable dict, e.g.:
```
{
  "seq": 1,
  "node": "Market Analyst",
  "agent": "Market Analyst",          # human label
  "kind": "llm" | "tool",
  "model": "qwen38",
  "tier": "quick" | "deep",
  "tool_name": null | "get_stock_data",  # for kind=tool
  "request": { "messages": [ {"role":"system","content":"..."}, ... ] } | { "tool":..., "args":{...} },
  "response": "full text or structured payload",
  "ok": true,
  "latency_ms": 4321,
  "error": null,
  "ts": "2026-09-20T01:02:03Z"
}
```
Keep `request`/`response` bounded (truncate very long blobs to a cap, e.g. 100k
chars each, with a `truncated` flag) so the DB/UI don't blow up on huge prompts.

### A3. Persist per run
- New SQLite table `llm_calls`:
  `(id INTEGER PK, run_id TEXT, seq INTEGER, node TEXT, agent TEXT, kind TEXT,
   model TEXT, tier TEXT, tool_name TEXT, request TEXT, response TEXT, ok INTEGER,
   latency_ms INTEGER, error TEXT, ts TEXT)`, FK run_id → runs(id) ON DELETE CASCADE.
  Add `get_llm_calls(run_id)` (ordered by seq) to `webapp/db.py`.
- In `webapp/runner.py`, in `_execute_job`: call `start_capture()` at the top of the
  worker thread (so the thread-local is bound to *this* run), run the pipeline, and
  on completion (in `finally` after `stop_capture()`) **dump `get_captured()` into the
  `llm_calls` table** for `run_id` (batch insert). Wrap in try/except so a capture
  failure never breaks the run.

---

## Workstream B — Present

### B1. Summarize the decision into user steps
Add a **deterministic** (no extra LLM call — derive from the captured state/entries)
summarizer that turns one run into a readable step list. New module, e.g.
`webapp/summary.py` `summarize_run(run, llm_calls, recommendation, order) -> dict`.

Output shape (all strings in **plain English**, non-technical):
```
{
  "overall": "BUY NVDA — 15 shares @ ~$221 (bracket: stop $205, target $265)",
  "confidence": "Overweight",
  "steps": [
    { "n": 1, "title": "Gathered market & fundamentals",
      "who": "Market, Sentiment, News & Fundamentals Analysts",
      "what": "Pulled price/technical, news, and balance-sheet data.",
      "key_find": "Stock near 52-wk high; revenue up 60% YoY.",
      "llm_count": 14, "tools": ["get_stock_data","get_indicators","get_news", ...] },
    { "n": 2, "title": "Debate: Bull vs Bear",
      "who": "Bull Researcher vs Bear Researcher → Research Manager",
      "what": "Bull argued X; Bear argued Y; manager sided with Bull.",
      "key_find": "Bull won — strong data-center demand outweighs valuation concern.",
      "llm_count": 6 },
    { "n": 3, "title": "Trader sized the position",
      "who": "Trader",
      "what": "Chose BUY with 5% of portfolio.",
      "key_find": "Entry $221, stop $205, target $265.", "llm_count": 1 },
    { "n": 4, "title": "Risk team stress-tested it",
      "who": "Aggressive / Conservative / Neutral Analysts → Portfolio Manager",
      "what": "Aggressive liked it; Conservative flagged concentration.",
      "key_find": "PM approved: final decision = Overweight / BUY.", "llm_count": 4 },
    { "n": 5, "title": "Execution",
      "who": "Dashboard",
      "what": "Auto-trade was ON/OFF; order submitted/skipped.",
      "key_find": "Submitted 15-share bracket to Alpaca (paper) / skipped (advisory)." }
  ]
}
```
The step `key_find` strings should be extracted from the actual captured content
(e.g. the Research Manager's `investment_plan`, the Trader's `trader_investment_plan`,
the Portfolio Manager's `final_trade_decision`, and the one-line report headers).
Keep extraction robust to missing fields.

### B2. New backend endpoints (in `webapp/api.py`)
- `GET /api/runs/{run_id}/llm-calls` → list of captured call dicts (ordered by seq).
- `GET /api/runs/{run_id}/summary` → the `summarize_run(...)` dict.
- `GET /api/runs/in-flight-detail` → for each **in-flight** run right now: ticker,
  run_id, status, current active node (from the runner's live tracking), the **live
  in-memory `llm_calls`** so far (count + latest few), so the UI can stream progress.
  (Runner already keeps `_active_runs`; expose a per-run live capture buffer there too —
  i.e. `get_run_live_calls(run_id)` returning the thread's captured list live, plus
  `get_current_node(run_id)`.)

### B3. New UI (vanilla HTML/JS/CSS, same dark theme)
1. **Decision step view** — when a run is **complete**, the run-detail modal
   (the existing `#logs-modal`) gets a **tab bar**: "Summary" | "LLM Calls" | "Raw Log".
   - **Summary** tab: renders the `summarize_run` output as a vertical
     step/timeline (numbered, each with title / who / what / key_find / small stats
     like "14 LLM calls, 5 data tools"). Prominent overall-decision banner at top.
   - **LLM Calls** tab: a list; each row shows `#seq`, agent/node, kind badge
     (LLM green / TOOL blue), model, latency, ok/error. Click a row to **expand** a
     `<details>` showing the full **request** (prompt) and **response** in a
     read-only `<pre>`. Provide a search box to filter by agent or keyword, and a
     "LLM only" / "tools only" toggle.
   - **Raw Log** tab: the existing live `<pre>` log (unchanged).
   Add a "View full API & LLM calls" affordance in the in-flight banner / runs list so
   the user can open a running run.
2. **Running-decisions live view** — extend the in-flight banner (or add a small
   panel) that, while a run is in progress, shows a **live console**: the current
   stage/agent (e.g. "🧠 Research Manager debating…") and a scrolling feed of LLM/tool
   calls appearing in real time (poll `/api/runs/in-flight-detail` every ~3–5s while
   the modal is open or the banner is visible; stop when the run completes). When the
   run finishes, offer "Open full details" → switches the modal to the Summary tab.

Keep the UI self-contained (no build step, no new JS/CSS dependencies). Reuse existing
CSS classes where possible.

---

## Constraints & gotchas (already known)
- LLMs are **shared** instances (`quick_thinking_llm`, `deep_thinking_llm`); capture
  must be **thread-local** and keyed by the LangGraph **node** for correct attribution.
- `invoke` is called both as `plain_llm.invoke(prompt)` and `structured_llm.invoke(prompt)`
  (see `agents/utils/structured.py`); **both paths must be captured**.
- Responses may be structured Pydantic objects or plain strings; normalize to text for
  display but keep the structured payload available for the 'parsed' view.
- Do NOT break the CLI path: with no capture active, the wrappers must behave exactly
  like the original LLM (same return value, same exceptions).
- vLLM `qwen38` calls are slow — the live console must tolerate gaps between calls
  (do not assume real-time streaming; it's periodic poll).
- alpaca-py v0.44 / FastAPI specifics are already handled elsewhere; don't disturb
  `execution.py` order logic.
- Keep `.env`/secrets out of git; new tables live in the existing
  `webapp/data/trading_dashboard.db`.

## Verification checklist (do all of these and report)
1. Server starts; `/api/health` 200; all pre-existing endpoints still work.
2. Trigger a real run for one ticker (e.g. NVDA) via `POST /api/runs`. While it runs,
   `GET /api/runs/in-flight-detail` returns the run with a growing `llm_calls` list and
   a non-empty `current_node`.
3. After it completes, `GET /api/runs/{id}/llm-calls` returns a **complete, ordered**
   set of entries covering the analysts, both researchers + Research Manager, the Trader,
   the three risk debators + Portfolio Manager — each with a populated `node`, a
   non-empty `request` and `response`, and (where the model used tools) `tool` entries.
4. `GET /api/runs/{id}/summary` returns a coherent step list whose `key_find` text
   matches what the agents actually said (sanity: quote a real sentence from the PM /
   Research Manager output).
5. Open the dashboard, run a ticker, and confirm: the **live console** updates while the
   run is in flight, and on completion the modal's **Summary** and **LLM Calls** tabs
   render correctly (expand a call to see full prompt+response).
6. Confirm a **CLI** run (`execute_order.py NVDA <date> --dry-run`) still works and
   produces no capture side effects / no errors.
7. Stop any test server you started; leave the dashboard running under systemd.

Report: which files you changed, what the capture wrapper looks like, and paste the
`/api/runs/{id}/summary` output + a sample `/llm-calls` entry for the NVDA run, plus
any caveats.
