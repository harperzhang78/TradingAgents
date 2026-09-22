# 需求：决策矛盾修复 + 交互式“为什么这样决策”对话框

项目：`/home/gene/Projects/TradingAgents`（FastAPI + vanilla JS SPA，端口 8037，systemd 服务 `trading-dashboard.service`）。
相关代码：`webapp/execution.py`（`build_order`、`parse_trader_decision`、`execute_recommendation`）、`webapp/runner.py`（`_execute_job`）、`webapp/api.py`（REST 端点）、`webapp/summary.py`（`summarize_run` 生成各步骤的 timeline）、`webapp/static/app.js`（卡片徽标 + modal 渲染）、`webapp/db.py`（llm_calls、recommendation、runs 表）。

## 背景 / 要解决的矛盾
某次 NVDA run 中，各 agent 结论不一致：Research Manager = `Underweight`，**Trader = `Action: Hold`**（明确“无持仓，避免入场”），Portfolio Manager = `Rating: Overweight`。
`build_order()` 在 `action in ("HOLD","")` 时回退到 PM 的 `rating`，于是 `HOLD` + `Overweight` 被当成“可执行 BUY”，下了一张 $5,000 市价买单。而前端卡片徽标用 Trader 的 `action` 显示 HOLD，造成“显示 HOLD 却下了 BUY”的困惑。

## Part 1 — 修复：Trader 的显式 Action 必须是权威（P0）
- 在 `build_order()`（以及任何从 action/rating 推方向的逻辑，含 `execute_recommendation` 与 runner 的下单路径）中区分两种情况：
  1. Trader **显式**给出 `Action: HOLD` 或 `Action: REVIEW`（非空）→ **必须不生成订单**（return None），即使 PM `rating` 是 BUY/Overweight 或 SELL/Underweight。这是用户“避免入场/观望”的最终决定。
  2. Trader **未给** action（action 为空/缺失）→ 才允许回退到 PM `rating`（保留现有 buy/sell/hold 语义）。
- 发生“action 显式为 HOLD/REVIEW 但 rating 方向相反”时，在 run log 里写一条**明确**的中文/英文日志说明，例如：
  `⚠️ 冲突：Trader Action=HOLD 但 PM Rating=Overweight — 以 Trader 的 HOLD 为准，未下单（无持仓，避免入场）。`
- 保持现有的“空仓 SELL 不下单（Avoid Entry）”保护逻辑不变。
- 前端徽标逻辑需与后端保持一致：显式 HOLD/REVIEW 的 run 不应出现“可执行/已下 BUY 单”的误导。让徽标、Rating、Order 三处一致（HOLD + 说明“以 Trader 决定为准”）。

## Part 2 — 交互式“为什么这样决策”对话框（P1）
在每个决策卡片（run card，以及 Run Details modal）加一个可交互的小对话框，让用户就“这个决定为什么这样下 / 每一步的主要决策是什么”提问。
- 后端新增端点（如 `POST /api/runs/{run_id}/ask`，body: `{ "question": "..." }`）：
  - 从该 run 已落库的数据组装上下文：`summarize_run` 的各步骤 timeline（每步 agent 的主要结论）、recommendation（action/rating/entry/stop/target/reasoning）、以及 `llm_calls` 里关键 agent（Research Manager / Trader / Portfolio Manager / 各 Analyst）的 response 摘要。
  - 复用 dashboard 配置的 LLM（参考 `webapp/llm_settings.py` 的 provider/key/base_url/model，与 runner 一致的注入方式）对“question + 上述上下文”做一次 LLM 调用，返回自然语言解答。
  - 若 run 无 LLM 配置或调用失败，返回 5xx/友好错误信息（前端提示“无法回答，请先在 Settings 配置 LLM”）。
  - 上下文注意截断/去重，控制 token（每个 agent 取要点而非全文）。
- 前端：在 run card 底部加“💬 问问 AI 为什么”按钮，点开一个小聊天面板（问题输入框 + 回答气泡，支持连续多问）；或在 Run Details modal 新增一个 tab。回答用 LLM 返回文本，显示 agent 相关引用（可选）。保持与现有风格/语言设置（en/zh）一致。

## 验收
- 复现：造一个 `action=HOLD, rating=Overweight` 的数据 → `build_order` 返回 None，不下单，日志有明确冲突说明。
- 空 action + rating=Buy → 仍然 BUY（保留回退语义）。
- `/api/runs/{id}/ask` 能对某 run 的问题返回合理的自然语言解答。
- 跑通测试套件（`pytest`，当前基线 1,154 passed / 2 skipped），为新逻辑加少量单测。
- 改完 `sudo systemctl restart trading-dashboard.service`（**不要**手动另起 uvicorn 占 8037）。
- 前端徽标/Order 三处一致，无“HOLD 却 BUY”的误导。
