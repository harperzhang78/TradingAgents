# UI 文案简化

原则：界面标签尽量精简，去掉冗余词，但含义不能丢失。

## 具体改动

### index.html（模板）

1. 顶部 stat 卡片标题：
   - `Portfolio Equity` → `Equity`
   - `Cash Balance` → `Cash`
   - `Open Positions` → `Positions`
   - `Active Watchlist` → `Watchlist`

2. Watchlist 表格：
   - `Current Holding` → `Holding`

3. Positions 卡片：
   - `Alpaca Current Positions` → `Positions`
   - 表头：`Market Value` → `Value`，`Unrealized P&L` → `P&L`

4. Active Orders 卡片：
   - `Active Orders` → `Orders`

5. Recommendations 卡片：
   - `Recommendations & Execution History` → `Recommendations`

6. Modal：
   - `Configuration & Scheduler Settings` → `Settings`
   - `Auto-Trade Execution Mode` → `Auto-Trade`（label 文字）
   - `Background Watchlist Scheduler` → `Scheduler`（label 文字）
   - `Analysis Frequency` → `Frequency`
   - `Shares (Quantity)` → `Shares`
   - `Limit Price ($)` → `Limit ($)`
   - `Stop Loss ($) (optional)` → `Stop ($) (opt)`
   - `Take Profit ($) (optional)` → `Target ($) (opt)`
   - `Run Details & Logs` → `Run Details`

### app.js（动态生成 HTML）

7. Run 卡片 meta：
   - `Total time:` → `Total:`

8. Run 卡片 metrics row：
   - `Stop Loss` → `Stop`
   - `Held at Run` → `Held`

9. Order box：
   - `Order Executed:` → `Executed:`
   - `Order Cancelled:` → `Cancelled:`
   - `Order Error:` → `Error:`
   - `Advisory Mode:` → `Advisory:`
   - `Trader Rationale:` → `Rationale:`

10. Modal raw log tab 中的 summary：
    - `Order Execution:` → `Order:`

11. Modal summary tab：
    - `Confidence:` → `Conf:`
    - `LLM & Tool Calls Recorded` → `LLM Calls`

12. Decision timeline steps：
    - `Key Finding`（label）→ `Finding`

13. Execute modal metrics row：
    - `Entry Limit` → `Entry`
    - `Stop Loss` → `Stop`

14. 其他冗余 description（form-desc 小字说明）：保留不改，因为那些是辅助说明，不影响主界面简洁度。

## 注意事项
- 所有改动只改文案/标签文字，不改任何逻辑、class name、id、功能
- 不要改 CSS
- 保持中英文切换（lang zh）的对应翻译也做相应简化
- 改完确保 1154 个测试仍然通过
