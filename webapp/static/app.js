/**
 * TradingAgents Dashboard Single Page Application
 */

const state = {
  account: null,
  watchlist: [],
  liveOrders: [],
  settings: {
    auto_trade: false,
    schedule_enabled: false,
    schedule_interval_minutes: 1440,
  },
  runs: [],
  inFlightTickers: [],
  activeFilter: 'ALL',
  activeModalRunId: null,
  logPollInterval: null,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCurrency(val) {
  if (val === null || val === undefined || isNaN(val)) return '$0.00';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
}

function formatPercent(val) {
  if (val === null || val === undefined || isNaN(val)) return '0.00%';
  const num = typeof val === 'string' ? parseFloat(val) : val;
  const sign = num > 0 ? '+' : '';
  return `${sign}${(num * 100).toFixed(2)}%`;
}

function formatDate(isoStr) {
  if (!isoStr) return 'N/A';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// ---------------------------------------------------------------------------
// API Client
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  try {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      let errMsg = `Request failed: ${res.status}`;
      try {
        const errJson = await res.json();
        if (errJson.detail) errMsg = errJson.detail;
      } catch {}
      throw new Error(errMsg);
    }
    return await res.json();
  } catch (err) {
    console.error(`API error on ${path}:`, err);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Data Fetching & Sync
// ---------------------------------------------------------------------------

async function fetchAccount() {
  try {
    state.account = await api('/api/account');
    renderAccount();
  } catch (err) {
    console.warn('Account fetch warning:', err);
  }
}

async function fetchWatchlist() {
  try {
    state.watchlist = await api('/api/watchlist');
    renderWatchlist();
  } catch (err) {
    console.error('Watchlist fetch error:', err);
  }
}

async function fetchSettings() {
  try {
    state.settings = await api('/api/settings');
    renderSettings();
  } catch (err) {
    console.error('Settings fetch error:', err);
  }
}

async function fetchRuns() {
  try {
    state.runs = await api('/api/runs');
    state.inFlightTickers = await api('/api/runs/in-flight');
    renderInFlightBanner();
    renderRuns();
    renderWatchlist(); // Re-render watchlist to reflect in-flight status
  } catch (err) {
    console.error('Runs fetch error:', err);
  }
async function fetchLiveOrders() {
  try {
    state.liveOrders = await api('/api/orders/live');
    renderLiveOrders();
  } catch (err) {
    console.warn('Live orders fetch warning:', err);
  }
}

async function refreshAll() {
  await Promise.all([
    fetchAccount(),
    fetchSettings(),
    fetchWatchlist(),
    fetchRuns(),
    fetchLiveOrders(),
  ]);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderAccount() {
  const acct = state.account;
  if (!acct) return;

  document.getElementById('stat-equity').textContent = formatCurrency(acct.equity);
  document.getElementById('stat-cash').textContent = formatCurrency(acct.cash);
  document.getElementById('stat-buying-power').textContent = `BP: ${formatCurrency(acct.buying_power)}`;
  document.getElementById('stat-account-status').textContent = `Status: ${acct.status}`;

  const paperBadge = document.getElementById('paper-badge');
  if (acct.paper) {
    paperBadge.className = 'badge badge-paper';
    paperBadge.textContent = 'PAPER TRADING';
  } else {
    paperBadge.className = 'badge badge-live';
    paperBadge.textContent = '⚠️ LIVE TRADING';
  }

  // Positions Table
  const tbody = document.getElementById('positions-tbody');
  const badge = document.getElementById('positions-badge');
  const count = document.getElementById('stat-positions-count');

  badge.textContent = `${acct.positions.length} open`;
  count.textContent = acct.positions.length;

  if (acct.positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">Flat — no open positions held.</td></tr>`;
    return;
  }

  tbody.innerHTML = acct.positions
    .map((p) => {
      const plClass = (p.unrealized_pl || 0) >= 0 ? 'text-success' : 'text-danger';
      return `
      <tr>
        <td class="symbol-cell">${p.symbol}</td>
        <td>${p.qty}</td>
        <td>${formatCurrency(p.avg_entry_price)}</td>
        <td>${formatCurrency(p.current_price)}</td>
        <td>${formatCurrency(p.market_value)}</td>
        <td class="${plClass}">
          ${formatCurrency(p.unrealized_pl)} (${formatPercent(p.unrealized_plpc)})
        </td>
      </tr>
    `;
    })
    .join('');
}

function renderLiveOrders() {
  const orders = state.liveOrders || [];
  const tbody = document.getElementById('orders-tbody');
  const badge = document.getElementById('orders-badge');

  if (!tbody) return;
  if (badge) badge.textContent = `${orders.length} active`;

  if (orders.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">Flat — no active orders.</td></tr>`;
    return;
  }

  tbody.innerHTML = orders
    .map((o) => {
      // Determine order type tag
      const oclass = (o.order_class || '').toLowerCase();
      const otypeRaw = (o.order_type || 'limit').toLowerCase();
      let otype = (oclass === 'bracket' ? 'BRACKET' : otypeRaw).toUpperCase();
      let tagClass = 'limit';
      if (oclass === 'bracket') tagClass = 'bracket';
      else if (otypeRaw === 'market') tagClass = 'market';
      else if (otypeRaw.includes('stop')) tagClass = 'stop';

      const sideUpper = (o.side || 'BUY').toUpperCase();
      const sideBadgeClass = sideUpper === 'BUY' ? 'badge-buy' : 'badge-sell';

      // Status badge with distinct styling for pending_new vs new
      const statusLower = (o.status || '').toLowerCase();
      let statusBadge = `<span class="badge">${(o.status || 'NEW').toUpperCase()}</span>`;
      if (statusLower === 'pending_new') {
        statusBadge = `<span class="badge badge-status-pending-new">PENDING NEW</span>`;
      } else if (statusLower === 'new') {
        statusBadge = `<span class="badge badge-status-new">NEW</span>`;
      }

      const limitStr = o.limit_price !== null && o.limit_price !== undefined ? formatCurrency(o.limit_price) : '-';
      const stopStr = o.stop_price !== null && o.stop_price !== undefined ? formatCurrency(o.stop_price) : '-';
      const targetStr = o.take_profit !== null && o.take_profit !== undefined ? formatCurrency(o.take_profit) : '-';
      const qtyStr = o.qty !== null && o.qty !== undefined ? o.qty : '-';
      const placedStr = formatDate(o.created_at);

      return `
      <tr>
        <td class="symbol-cell">
          ${o.symbol}
          <span class="badge-tag badge-tag-${tagClass}">${otype}</span>
        </td>
        <td><span class="badge ${sideBadgeClass}">${sideUpper}</span></td>
        <td>${qtyStr}</td>
        <td>${limitStr}</td>
        <td>${stopStr}</td>
        <td>${targetStr}</td>
        <td>${statusBadge}</td>
        <td class="text-xs text-muted">${placedStr}</td>
        <td class="text-right">
          <button class="btn btn-danger btn-sm" onclick="cancelLiveOrder('${o.id}')" title="Cancel order">
            Cancel
          </button>
        </td>
      </tr>
    `;
    })
    .join('');
}

function renderSettings() {
  const autoTrade = !!state.settings.auto_trade;
  const toggle = document.getElementById('auto-trade-toggle');
  const label = document.getElementById('auto-trade-label');
  const modalToggle = document.getElementById('settings-auto-trade');
  const modalLabel = document.getElementById('modal-auto-trade-label');

  toggle.checked = autoTrade;
  modalToggle.checked = autoTrade;

  if (autoTrade) {
    label.textContent = 'Auto-Trade: ON';
    label.style.color = '#34D399';
    modalLabel.textContent = 'Auto-Trade: ON (Orders will execute live/paper)';
  } else {
    label.textContent = 'Auto-Trade: OFF';
    label.style.color = 'var(--text-muted)';
    modalLabel.textContent = 'Auto-Trade: OFF (Advisory Mode)';
  }

  // Scheduler settings in modal
  document.getElementById('settings-sched-enabled').checked = !!state.settings.schedule_enabled;
  document.getElementById('settings-interval').value = String(state.settings.schedule_interval_minutes || 1440);

  const schedInfo = document.getElementById('stat-schedule-info');
  if (state.settings.schedule_enabled) {
    const mins = state.settings.schedule_interval_minutes;
    schedInfo.textContent = mins >= 60 ? `Schedule: Every ${mins / 60}h` : `Schedule: Every ${mins}m`;
  } else {
    schedInfo.textContent = 'Schedule: Manual (Off)';
  }
}

function renderWatchlist() {
  const items = state.watchlist;
  const tbody = document.getElementById('watchlist-tbody');
  const badge = document.getElementById('watchlist-badge');
  const count = document.getElementById('stat-watchlist-count');

  const activeCount = items.filter((i) => i.enabled).length;
  badge.textContent = `${items.length} symbols (${activeCount} active)`;
  count.textContent = activeCount;

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted">Watchlist is empty. Add a symbol above.</td></tr>`;
    return;
  }

  // Match with Alpaca positions for quick context
  const posMap = {};
  if (state.account && state.account.positions) {
    state.account.positions.forEach((p) => {
      posMap[p.symbol] = p;
    });
  }

  tbody.innerHTML = items
    .map((item) => {
      const sym = item.symbol;
      const inFlight = state.inFlightTickers.includes(sym);
      const pos = posMap[sym];
      const posText = pos
        ? `<span class="badge badge-paper">Owned: ${pos.qty} shs</span>`
        : `<span class="text-dim text-xs">Flat</span>`;

      return `
      <tr>
        <td style="width: 50px;">
          <label class="switch" style="width: 32px; height: 18px;">
            <input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="toggleWatchlistEnabled('${sym}', this.checked)" />
            <span class="slider round" style="border-radius: 18px;"></span>
          </label>
        </td>
        <td class="symbol-cell">${sym}</td>
        <td>${posText}</td>
        <td class="text-muted text-xs">${item.notes || '-'}</td>
        <td class="text-right">
          <button class="btn btn-secondary btn-sm" onclick="triggerSingleRun('${sym}')" ${inFlight ? 'disabled' : ''}>
            ${inFlight ? '<span class="spinner" style="width: 10px; height: 10px; margin-right: 4px;"></span> Running' : '▶ Run'}
          </button>
          <button class="btn btn-danger btn-sm" onclick="removeWatchlistSymbol('${sym}')" title="Remove" style="margin-left: 4px;">
            ✕
          </button>
        </td>
      </tr>
    `;
    })
    .join('');
}

function renderInFlightBanner() {
  const banner = document.getElementById('in-flight-banner');
  const text = document.getElementById('in-flight-text');
  const links = document.getElementById('in-flight-links');

  if (state.inFlightTickers.length === 0) {
    banner.classList.add('hidden');
    return;
  }

  banner.classList.remove('hidden');
  text.textContent = `Analysis in flight for ${state.inFlightTickers.join(', ')}...`;

  // Find active run IDs
  const activeRuns = state.runs.filter((r) => r.status === 'running');
  links.innerHTML = activeRuns
    .map(
      (r) =>
        `<button class="btn btn-secondary btn-sm" onclick="openLogsModal('${r.id}')">View ${r.ticker} Logs</button>`
    )
    .join('');
}

function renderRuns() {
  const container = document.getElementById('runs-container');
  let runs = [...state.runs];

  // Apply Filter
  if (state.activeFilter !== 'ALL') {
    runs = runs.filter((r) => {
      const act = (r.recommendation && r.recommendation.action) || '';
      return act.toUpperCase() === state.activeFilter;
    });
  }

  if (runs.length === 0) {
    container.innerHTML = `<div class="text-center py-8 text-muted">No runs found for filter "${state.activeFilter}".</div>`;
    return;
  }

  container.innerHTML = runs
    .map((run) => {
      const rec = run.recommendation;
      const order = run.order;
      const isRunning = run.status === 'running';
      const isFailed = run.status === 'failed';

      let actionBadge = `<span class="badge">PENDING</span>`;
      if (isRunning) {
        actionBadge = `<span class="badge badge-running"><span class="spinner" style="width: 10px; height: 10px; margin-right: 4px;"></span> RUNNING</span>`;
      } else if (isFailed) {
        actionBadge = `<span class="badge badge-sell">FAILED</span>`;
      } else if (rec && rec.action) {
        const act = rec.action.toUpperCase();
        if (act === 'BUY') actionBadge = `<span class="badge badge-buy">BUY</span>`;
        else if (act === 'SELL') actionBadge = `<span class="badge badge-sell">SELL</span>`;
        else actionBadge = `<span class="badge badge-hold">HOLD</span>`;
      }

      // Metrics row
      let metricsHtml = '';
      if (rec) {
        const entry = rec.entry_price ? formatCurrency(rec.entry_price) : 'Market';
        const stop = rec.stop_loss ? formatCurrency(rec.stop_loss) : 'N/A';
        const target = rec.price_target ? formatCurrency(rec.price_target) : 'N/A';
        const sizing = rec.position_sizing || '5% of portfolio';
        const held =
          rec.current_position_qty && rec.current_position_qty > 0
            ? `${rec.current_position_qty} shs`
            : 'Flat (0)';

        metricsHtml = `
        <div class="metrics-row">
          <div class="metric-box"><div class="metric-label">Rating</div><div class="metric-val">${rec.rating || 'N/A'}</div></div>
          <div class="metric-box"><div class="metric-label">Entry</div><div class="metric-val">${entry}</div></div>
          <div class="metric-box"><div class="metric-label">Stop Loss</div><div class="metric-val">${stop}</div></div>
          <div class="metric-box"><div class="metric-label">Target</div><div class="metric-val">${target}</div></div>
          <div class="metric-box"><div class="metric-label">Sizing</div><div class="metric-val">${sizing}</div></div>
          <div class="metric-box"><div class="metric-label">Held at Run</div><div class="metric-val">${held}</div></div>
        </div>
      `;
      }

      // Order execution box
      let orderBoxHtml = '';
      if (order) {
        if (order.status === 'submitted') {
          orderBoxHtml = `
          <div class="order-box order-box-submitted">
            <span>✅ <strong>Alpaca Order Submitted:</strong> ${order.side.toUpperCase()} ${order.qty || ''} ${order.ticker} (ID: ${order.alpaca_order_id || order.id})</span>
            <span class="text-xs text-muted">${formatDate(order.submitted_at)}</span>
          </div>`;
        } else if (order.status === 'skipped') {
          orderBoxHtml = `
          <div class="order-box order-box-skipped">
            <span>⏸️ <strong>Order Skipped:</strong> ${order.skip_reason || 'Advisory mode'}</span>
          </div>`;
        } else if (order.status === 'failed') {
          orderBoxHtml = `
          <div class="order-box order-box-failed">
            <span>❌ <strong>Order Failed:</strong> ${order.error_message || 'Submission error'}</span>
          </div>`;
        }
      }

      // Reasoning box
      let reasoningHtml = '';
      if (rec && rec.reasoning) {
        reasoningHtml = `
        <div class="reasoning-box">
          <strong>Trader Rationale:</strong> ${escapeHtml(rec.reasoning)}
        </div>
      `;
      } else if (run.error) {
        reasoningHtml = `
        <div class="reasoning-box" style="border-left-color: var(--danger); color: #FCA5A5;">
          <strong>Error:</strong> ${escapeHtml(run.error)}
        </div>
      `;
      }

      return `
      <div class="run-item-card">
        <div class="run-card-header">
          <div class="run-ticker-group">
            <span class="run-ticker">${run.ticker}</span>
            ${actionBadge}
            <span class="run-meta">• ${formatDate(run.started_at)} • Date: ${run.trade_date} • ${run.trigger}</span>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${run.id}')">
            📜 View Logs
          </button>
        </div>
        ${metricsHtml}
        ${orderBoxHtml}
        ${reasoningHtml}
      </div>
    `;
    })
    .join('');
}

function escapeHtml(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ---------------------------------------------------------------------------
// Actions & Handlers
// ---------------------------------------------------------------------------

async function toggleAutoTrade(enabled) {
  try {
    const updated = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ auto_trade: enabled }),
    });
    state.settings = updated;
    renderSettings();
    showToast(
      enabled
        ? 'Auto-Trade enabled: recommendations will submit live/paper orders to Alpaca'
        : 'Auto-Trade disabled: recommendations are advisory only',
      enabled ? 'success' : 'info'
    );
  } catch (err) {
    showToast(`Failed to update auto-trade: ${err.message}`, 'error');
    renderSettings();
  }
}

async function triggerSingleRun(symbol) {
  try {
    const res = await api('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ ticker: symbol }),
    });
    showToast(`Analysis started for ${symbol}`, 'success');
    await refreshAll();
    if (res.run_id) {
      openLogsModal(res.run_id);
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function triggerRunAllWatchlist() {
  try {
    const res = await api('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ all_watchlist: true }),
    });
    showToast(`Triggered analysis on all active watchlist tickers`, 'success');
    await refreshAll();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function addWatchlistSymbol(symbol, notes) {
  try {
    await api('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, notes }),
    });
    showToast(`Added ${symbol.toUpperCase()} to watchlist`, 'success');
    await fetchWatchlist();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function removeWatchlistSymbol(symbol) {
  if (!confirm(`Remove ${symbol} from watchlist?`)) return;
  try {
    await api(`/api/watchlist/${symbol}`, { method: 'DELETE' });
    showToast(`Removed ${symbol} from watchlist`, 'info');
    await fetchWatchlist();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function toggleWatchlistEnabled(symbol, enabled) {
  try {
    await api(`/api/watchlist/${symbol}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
    await fetchWatchlist();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function cancelLiveOrder(orderId) {
  if (!confirm('Are you sure you want to cancel this order?')) return;
  try {
    await api(`/api/orders/live/${orderId}/cancel`, { method: 'POST' });
    showToast('Order cancellation submitted', 'success');
    await fetchLiveOrders();
    setTimeout(async () => {
      await fetchLiveOrders();
      await fetchAccount();
    }, 600);
  } catch (err) {
    showToast(`Failed to cancel order: ${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Log Viewer Modal
// ---------------------------------------------------------------------------

async function openLogsModal(runId) {
  state.activeModalRunId = runId;
  const modal = document.getElementById('logs-modal');
  modal.classList.remove('hidden');

  await updateModalLogs();

  if (state.logPollInterval) clearInterval(state.logPollInterval);
  state.logPollInterval = setInterval(async () => {
    if (!state.activeModalRunId) {
      clearInterval(state.logPollInterval);
      return;
    }
    await updateModalLogs();
  }, 2000);
}

function closeLogsModal() {
  state.activeModalRunId = null;
  if (state.logPollInterval) {
    clearInterval(state.logPollInterval);
    state.logPollInterval = null;
  }
  document.getElementById('logs-modal').classList.add('hidden');
}

async function updateModalLogs() {
  if (!state.activeModalRunId) return;
  try {
    const run = await api(`/api/runs/${state.activeModalRunId}`);
    document.getElementById('modal-title').textContent = `Run ${run.ticker} (${run.trade_date})`;
    const badge = document.getElementById('modal-status-badge');
    badge.textContent = run.status.toUpperCase();
    badge.className = `badge ${run.status === 'completed' ? 'badge-buy' : run.status === 'running' ? 'badge-running' : 'badge-sell'}`;

    const summaryBox = document.getElementById('modal-summary');
    let recSummary = 'No recommendation produced yet.';
    if (run.recommendation) {
      recSummary = `Recommendation: <strong>${run.recommendation.action || 'HOLD'}</strong> (${run.recommendation.rating || 'N/A'}), Entry: ${formatCurrency(run.recommendation.entry_price)}, Target: ${formatCurrency(run.recommendation.price_target)}, Stop: ${formatCurrency(run.recommendation.stop_loss)}`;
    }
    let orderSummary = '';
    if (run.order) {
      orderSummary = `<br>Order Execution: ${run.order.status.toUpperCase()} (${run.order.skip_reason || run.order.alpaca_order_id || 'Submitted'})`;
    }
    summaryBox.innerHTML = `<strong>Status:</strong> ${run.status} • <strong>Started:</strong> ${formatDate(run.started_at)}${orderSummary}<br>${recSummary}`;

    const pre = document.getElementById('modal-logs-pre');
    pre.textContent = run.log_output || '(Waiting for output stream...)';

    if (document.getElementById('modal-autoscroll').checked) {
      pre.scrollTop = pre.scrollHeight;
    }

    if (run.status !== 'running') {
      // Done running, stop fast poll
      if (state.logPollInterval) {
        clearInterval(state.logPollInterval);
        state.logPollInterval = null;
      }
    }
  } catch (err) {
    console.error('Failed to update logs modal:', err);
  }
}

// ---------------------------------------------------------------------------
// Settings Modal
// ---------------------------------------------------------------------------

function openSettingsModal() {
  document.getElementById('settings-auto-trade').checked = !!state.settings.auto_trade;
  document.getElementById('settings-sched-enabled').checked = !!state.settings.schedule_enabled;
  document.getElementById('settings-interval').value = String(state.settings.schedule_interval_minutes || 1440);
  document.getElementById('settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
}

async function saveSettingsFromModal() {
  const autoTrade = document.getElementById('settings-auto-trade').checked;
  const schedEnabled = document.getElementById('settings-sched-enabled').checked;
  const interval = parseInt(document.getElementById('settings-interval').value, 10);

  try {
    const updated = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        auto_trade: autoTrade,
        schedule_enabled: schedEnabled,
        schedule_interval_minutes: interval,
      }),
    });
    state.settings = updated;
    renderSettings();
    closeSettingsModal();
    showToast('Settings saved successfully', 'success');
  } catch (err) {
    showToast(`Failed to save settings: ${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Event Listeners Initialization
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  // Auto-trade toggle
  document.getElementById('auto-trade-toggle').addEventListener('change', (e) => {
    toggleAutoTrade(e.target.checked);
  });

  // Refresh button
  document.getElementById('btn-refresh').addEventListener('click', () => {
    refreshAll();
    showToast('Dashboard refreshed', 'info');
  });

  // Settings modal
  document.getElementById('btn-settings').addEventListener('click', openSettingsModal);
  document.getElementById('settings-close').addEventListener('click', closeSettingsModal);
  document.getElementById('btn-cancel-settings').addEventListener('click', closeSettingsModal);
  document.getElementById('btn-save-settings').addEventListener('click', saveSettingsFromModal);

  // Run all active
  document.getElementById('btn-run-all').addEventListener('click', triggerRunAllWatchlist);

  // Add symbol form
  document.getElementById('add-watchlist-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const symInput = document.getElementById('input-symbol');
    const notesInput = document.getElementById('input-notes');
    const symbol = symInput.value.trim().toUpperCase();
    const notes = notesInput.value.trim();
    if (symbol) {
      addWatchlistSymbol(symbol, notes);
      symInput.value = '';
      notesInput.value = '';
    }
  });

  // Filter buttons
  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.activeFilter = btn.getAttribute('data-filter');
      renderRuns();
    });
  });

  // Logs modal close
  document.getElementById('modal-close').addEventListener('click', closeLogsModal);
  document.getElementById('modal-btn-close').addEventListener('click', closeLogsModal);

  // Refresh active orders button
  const btnRefreshOrders = document.getElementById('btn-refresh-orders');
  if (btnRefreshOrders) {
    btnRefreshOrders.addEventListener('click', async () => {
      await fetchLiveOrders();
      showToast('Active orders refreshed', 'info');
    });
  }

  // Initial load
  refreshAll();

  // Background polling every 8 seconds for runs & account
  setInterval(() => {
    fetchRuns();
    fetchAccount();
  }, 8000);

  // Background polling every 30 seconds for live orders
  setInterval(() => {
    fetchLiveOrders();
  }, 30000);
});
