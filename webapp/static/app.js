/**
 * TradingAgents Dashboard Single Page Application
 */

const state = {
  account: null,
  watchlist: [],
  selectedWatchlist: new Set(),
  liveOrders: [],
  llmConfig: null,
  settings: {
    auto_trade: false,
    schedule_enabled: false,
    schedule_interval_minutes: 1440,
  },
  runs: [],
  inFlightTickers: [],
  inFlightDetail: [],
  inFlightConsoleFilter: 'ALL',
  activeFilter: 'ALL',
  activeModalRunId: null,
  activeModalTab: 'summary',
  activeModalRun: null,
  activeModalSummary: null,
  activeModalCalls: [],
  llmFilterKind: 'ALL',
  llmSearchQuery: '',
  modalPollInterval: null,
  liveConsolePollInterval: null,
};
window.state = state;

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

function formatStepDuration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds) || seconds < 0) return '';
  const totalSec = Math.round(seconds);
  if (totalSec < 60) return `${totalSec}s`;
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
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

async function fetchLlmConfig() {
  try {
    state.llmConfig = await api('/api/llm-config');
    renderLlmConfig();
  } catch (err) {
    console.error('LLM config fetch error:', err);
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
    fetchLlmConfig(),
    fetchWatchlist(),
    fetchRuns(),
    fetchLiveOrders(),
    fetchInFlightDetail(),
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

function renderLlmConfig() {
  const cfg = state.llmConfig;
  if (!cfg) return;

  // Background polls must preserve pending key edits in the open modal.
  if (document.getElementById('settings-modal')?.classList.contains('hidden')) {
    resetLlmApiKeyControl(cfg);
  }

  // Navbar provider badge. Form fields are populated in openSettingsModal() / after
  // save, not here, so background polls never clobber in-progress edits.
  const badge = document.getElementById('llm-badge-provider');
  const provider = cfg.provider || '';
  if (badge) {
    const label = provider === '' ? 'Default (.env)' : (provider === 'google' ? 'Google Gemini' : provider);
    badge.textContent = label + (cfg.api_key_set ? ' ✓' : ' ⚠️');
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
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">Watchlist is empty. Add a symbol above.</td></tr>`;
    onWatchlistSelectChange();
    return;
  }

  // Preserve any currently checked symbols across background polls
  const previouslySelected = new Set(
    Array.from(document.querySelectorAll('.watchlist-row-select:checked')).map((cb) => cb.value)
  );

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
      const isChecked = previouslySelected.has(sym);
      const pos = posMap[sym];
      const posText = pos
        ? `<span class="badge badge-paper">Owned: ${pos.qty} shs</span>`
        : `<span class="text-dim text-xs">Flat</span>`;

      return `
      <tr>
        <td style="width: 38px; text-align: center;">
          <input type="checkbox" class="watchlist-row-select watchlist-select-item" value="${sym}" ${isChecked ? 'checked' : ''} onchange="onWatchlistSelectChange()" />
        </td>
        <td style="width: 110px; text-align: center;">
          <label class="switch" style="width: 32px; height: 18px;" title="Auto-Trade for ${sym}">
            <input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="toggleWatchlistEnabled('${sym}', this.checked)" />
            <span class="slider round" style="border-radius: 18px;"></span>
          </label>
        </td>
        <td class="symbol-cell">${sym}</td>
        <td>${posText}</td>
        <td class="text-muted text-xs">${escapeHtml(item.notes || '-')}</td>
        <td class="text-right">
          <button class="btn btn-secondary btn-sm" onclick="triggerSingleRun('${sym}')" ${inFlight ? 'disabled' : ''}>
            ${inFlight ? '<span class="spinner" style="width: 10px; height: 10px; margin-right: 4px;"></span> Running' : '▶ Run'}
          </button>
        </td>
      </tr>
    `;
    })
    .join('');

  onWatchlistSelectChange();
}

function toggleSelectAllWatchlist(checked) {
  const isChecked = typeof checked === 'boolean' ? checked : Boolean(checked && checked.checked);
  const checkboxes = document.querySelectorAll('.watchlist-row-select, .watchlist-select-item');
  checkboxes.forEach((cb) => {
    cb.checked = isChecked;
  });
  onWatchlistSelectChange();
}

function onWatchlistSelectChange() {
  const checkboxes = Array.from(document.querySelectorAll('.watchlist-row-select, .watchlist-select-item'));
  const checkedBoxes = Array.from(document.querySelectorAll('.watchlist-row-select:checked, .watchlist-select-item:checked'));
  const btn = document.getElementById('btn-delete-selected') || document.getElementById('btn-watchlist-batch-delete');
  const countSpan = document.getElementById('watchlist-selected-count');
  const selectAll = document.getElementById('watchlist-select-all');

  const total = checkboxes.length;
  const count = checkedBoxes.length;

  if (countSpan) {
    countSpan.textContent = count;
  }

  if (btn) {
    btn.disabled = count === 0;
  }

  if (selectAll) {
    selectAll.checked = total > 0 && count === total;
    selectAll.indeterminate = count > 0 && count < total;
  }
}

async function deleteSelectedWatchlist() {
  const checkedBoxes = Array.from(document.querySelectorAll('.watchlist-row-select:checked, .watchlist-select-item:checked'));
  const symbols = checkedBoxes.map((cb) => cb.value).filter(Boolean);
  if (symbols.length === 0) return;

  const btn = document.getElementById('btn-delete-selected') || document.getElementById('btn-watchlist-batch-delete');
  if (btn) btn.disabled = true;

  try {
    const res = await api('/api/watchlist/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ symbols }),
    });
    if (res && res.deleted) {
      showToast(`Removed ${res.count ?? symbols.length} symbol${(res.count ?? symbols.length) > 1 ? 's' : ''} from watchlist`, 'info');
    } else {
      showToast(`Removed ${symbols.length} symbol${symbols.length > 1 ? 's' : ''} from watchlist`, 'info');
    }
  } catch (err) {
    console.error('Batch delete endpoint failed, attempting fallback:', err);
    try {
      const results = await Promise.allSettled(
        symbols.map((sym) => api(`/api/watchlist/${encodeURIComponent(sym)}`, { method: 'DELETE' }))
      );
      const failed = results.filter((r) => r.status === 'rejected');
      if (failed.length > 0) {
        showToast(`Failed to delete ${failed.length} symbol(s)`, 'error');
      } else {
        showToast(`Removed ${symbols.length} symbol${symbols.length > 1 ? 's' : ''} from watchlist`, 'info');
      }
    } catch (fallbackErr) {
      console.error('Batch delete fallback error:', fallbackErr);
      showToast(fallbackErr.message || 'Error deleting symbols', 'error');
    }
  } finally {
    await fetchWatchlist();
  }
}

// Compatibility aliases
const batchDeleteWatchlist = deleteSelectedWatchlist;
const toggleWatchlistSelectAll = toggleSelectAllWatchlist;
const toggleWatchlistSelectItem = (sym, checked) => { onWatchlistSelectChange(); };
const updateWatchlistSelectionUI = onWatchlistSelectChange;
function renderInFlightBanner() {
  renderInFlightLiveConsole();
}

function getTickerColorStyle(ticker) {
  if (!ticker) return { bg: 'rgba(6, 182, 212, 0.15)', text: '#67E8F9', border: 'rgba(6, 182, 212, 0.4)' };
  const palette = [
    { bg: 'rgba(6, 182, 212, 0.15)', text: '#67E8F9', border: 'rgba(6, 182, 212, 0.4)' },    // Cyan
    { bg: 'rgba(168, 85, 247, 0.15)', text: '#C084FC', border: 'rgba(168, 85, 247, 0.4)' },  // Purple
    { bg: 'rgba(34, 197, 94, 0.15)', text: '#4ADE80', border: 'rgba(34, 197, 94, 0.4)' },    // Green
    { bg: 'rgba(245, 158, 11, 0.15)', text: '#FCD34D', border: 'rgba(245, 158, 11, 0.4)' },  // Amber
    { bg: 'rgba(236, 72, 153, 0.15)', text: '#F472B6', border: 'rgba(236, 72, 153, 0.4)' },  // Pink
    { bg: 'rgba(59, 130, 246, 0.15)', text: '#60A5FA', border: 'rgba(59, 130, 246, 0.4)' },  // Blue
  ];
  let hash = 0;
  for (let i = 0; i < ticker.length; i++) {
    hash = (hash << 5) - hash + ticker.charCodeAt(i);
    hash |= 0;
  }
  return palette[Math.abs(hash) % palette.length];
}

function setInFlightConsoleFilter(filter) {
  state.inFlightConsoleFilter = filter;
  renderInFlightLiveConsole();
}

function renderInFlightLiveConsole() {
  const banner = document.getElementById('in-flight-banner');
  const text = document.getElementById('in-flight-text');
  const badgesGroup = document.getElementById('in-flight-badges-group');
  const nodeBadge = document.getElementById('in-flight-node-badge');
  const callsBadge = document.getElementById('in-flight-calls-badge');
  const stream = document.getElementById('live-console-stream');
  const links = document.getElementById('in-flight-links');
  const consoleTabs = document.getElementById('live-console-tabs');
  const consoleSub = document.getElementById('live-console-sub');

  const inFlights = state.inFlightDetail || [];
  const rawTickers = state.inFlightTickers || [];

  if (inFlights.length === 0 && rawTickers.length === 0) {
    if (banner) banner.classList.add('hidden');
    return;
  }

  if (banner) banner.classList.remove('hidden');

  // Collect all unique active tickers preserving order
  const activeTickers = [];
  inFlights.forEach((f) => {
    if (f.ticker && !activeTickers.includes(f.ticker)) {
      activeTickers.push(f.ticker);
    }
  });
  rawTickers.forEach((t) => {
    if (t && !activeTickers.includes(t)) {
      activeTickers.push(t);
    }
  });

  // 1) The banner headline shows all in-flight tickers (e.g. 'Analysis in flight for NVDA, AAPL')
  const tickerHeadline = activeTickers.length > 0 ? activeTickers.join(', ') : 'stocks';
  if (text) {
    text.textContent = `Analysis in flight for ${tickerHeadline}...`;
  }

  // 2) The node and calls badges display information for all active runs (or tabs/pills per ticker)
  if (badgesGroup) {
    if (inFlights.length > 0) {
      badgesGroup.innerHTML = inFlights
        .map((f, idx) => {
          const nodeBadgeId = idx === 0 ? ' id="in-flight-node-badge"' : '';
          const callsBadgeId = idx === 0 ? ' id="in-flight-calls-badge"' : '';
          const tickerLabel = inFlights.length > 1 ? `<strong class="ticker-pill-sym">${escapeHtml(f.ticker)}:</strong> ` : '';
          const color = getTickerColorStyle(f.ticker);
          return `
            <div class="in-flight-ticker-pill" data-ticker="${escapeHtml(f.ticker || '')}">
              <span${nodeBadgeId} class="badge badge-running" style="border-color: ${color.border};">
                ${tickerLabel}🧠 ${escapeHtml(f.current_node || 'Running')}
              </span>
              <span${callsBadgeId} class="badge badge-tag" style="border-color: ${color.border};">
                ${f.call_count || 0} calls
              </span>
            </div>
          `;
        })
        .join('');
    } else {
      badgesGroup.innerHTML = activeTickers
        .map((t, idx) => {
          const nodeBadgeId = idx === 0 ? ' id="in-flight-node-badge"' : '';
          const callsBadgeId = idx === 0 ? ' id="in-flight-calls-badge"' : '';
          const tickerLabel = activeTickers.length > 1 ? `<strong class="ticker-pill-sym">${escapeHtml(t)}:</strong> ` : '';
          const color = getTickerColorStyle(t);
          return `
            <div class="in-flight-ticker-pill" data-ticker="${escapeHtml(t)}">
              <span${nodeBadgeId} class="badge badge-running" style="border-color: ${color.border};">
                ${tickerLabel}🧠 Initializing
              </span>
              <span${callsBadgeId} class="badge badge-tag" style="border-color: ${color.border};">
                0 calls
              </span>
            </div>
          `;
        })
        .join('');
    }
  } else {
    // Fallback if badgesGroup container is absent
    if (inFlights.length > 0) {
      if (nodeBadge) {
        nodeBadge.innerHTML = inFlights
          .map((f) => inFlights.length > 1 ? `<strong>${escapeHtml(f.ticker)}:</strong> 🧠 ${escapeHtml(f.current_node || 'Running')}` : `🧠 ${escapeHtml(f.current_node || 'Running')}`)
          .join(' | ');
      }
      if (callsBadge) {
        callsBadge.innerHTML = inFlights
          .map((f) => inFlights.length > 1 ? `<strong>${escapeHtml(f.ticker)}:</strong> ${f.call_count || 0} calls` : `${f.call_count || 0} calls`)
          .join(' | ');
      }
    } else {
      if (nodeBadge) {
        nodeBadge.innerHTML = activeTickers.length > 1
          ? activeTickers.map((t) => `<strong>${escapeHtml(t)}:</strong> 🧠 Initializing`).join(' | ')
          : '🧠 Initializing';
      }
      if (callsBadge) {
        callsBadge.innerHTML = activeTickers.length > 1
          ? activeTickers.map((t) => `<strong>${escapeHtml(t)}:</strong> 0 calls`).join(' | ')
          : '0 calls';
      }
    }
  }

  // 3) The quick modal buttons clearly label the ticker (e.g. '📋 NVDA Summary', '🤖 NVDA Calls', '📋 AAPL Summary', etc.)
  if (links) {
    if (inFlights.length > 0) {
      links.innerHTML = inFlights
        .map((f) => {
          const sym = escapeHtml(f.ticker || 'Run');
          const count = f.call_count || 0;
          return (
            `<button class="btn btn-primary btn-sm" onclick="openLogsModal('${escapeHtml(f.run_id)}', 'summary')">📋 ${sym} Summary</button> ` +
            `<button class="btn btn-secondary btn-sm" onclick="openLogsModal('${escapeHtml(f.run_id)}', 'llm-calls')">🤖 ${sym} Calls (${count})</button>`
          );
        })
        .join(' ');
    } else {
      links.innerHTML = '';
    }
  }

  // 4) The live stream console distinguishes logs/calls per ticker
  const allCalls = [];
  inFlights.forEach((f) => {
    const ticker = f.ticker || '';
    (f.latest_calls || []).forEach((c) => {
      allCalls.push({
        ...c,
        ticker: c.ticker || ticker,
        run_id: f.run_id,
      });
    });
  });

  // Sort calls chronologically
  allCalls.sort((a, b) => {
    const tsA = a.ts || '';
    const tsB = b.ts || '';
    if (tsA && tsB) {
      const cmp = tsA.localeCompare(tsB);
      if (cmp !== 0) return cmp;
    }
    return (a.seq || 0) - (b.seq || 0);
  });

  // Validate active console filter against in-flight tickers
  if (
    state.inFlightConsoleFilter &&
    state.inFlightConsoleFilter !== 'ALL' &&
    !activeTickers.includes(state.inFlightConsoleFilter)
  ) {
    state.inFlightConsoleFilter = 'ALL';
  }

  // Filter tabs in console header (when multiple active stocks)
  if (consoleTabs) {
    if (activeTickers.length > 1) {
      consoleTabs.classList.remove('hidden');
      const curFilter = state.inFlightConsoleFilter || 'ALL';
      const allCount = allCalls.length;
      let tabsHtml = `<button class="console-filter-tab ${curFilter === 'ALL' ? 'active' : ''}" onclick="setInFlightConsoleFilter('ALL')">All (${allCount})</button>`;
      activeTickers.forEach((t) => {
        const count = allCalls.filter((c) => c.ticker === t).length;
        const isActive = curFilter === t;
        const color = getTickerColorStyle(t);
        const activeStyle = isActive ? `border-color: ${color.border}; color: ${color.text}; background: ${color.bg};` : '';
        tabsHtml += `<button class="console-filter-tab ${isActive ? 'active' : ''}" style="${activeStyle}" onclick="setInFlightConsoleFilter('${escapeHtml(t)}')">${escapeHtml(t)} (${count})</button>`;
      });
      consoleTabs.innerHTML = tabsHtml;
    } else {
      consoleTabs.innerHTML = '';
      consoleTabs.classList.add('hidden');
    }
  }

  if (consoleSub) {
    if (activeTickers.length > 0) {
      consoleSub.textContent = `Streaming active pipeline: ${activeTickers.join(', ')}`;
    } else {
      consoleSub.textContent = 'Polling active pipeline...';
    }
  }

  let displayCalls = allCalls;
  if (state.inFlightConsoleFilter && state.inFlightConsoleFilter !== 'ALL') {
    displayCalls = allCalls.filter((c) => c.ticker === state.inFlightConsoleFilter);
  }

  if (stream) {
    if (displayCalls.length > 0) {
      const wasAtBottom = stream.scrollHeight - stream.scrollTop <= stream.clientHeight + 40;
      stream.innerHTML = displayCalls
        .map((c) => {
          const icon = c.kind === 'tool' ? '⚡' : '🤖';
          const label = c.tool_name ? `${c.agent} → ${c.tool_name}` : `${c.agent}`;
          const timeStr = c.ts ? c.ts.substring(11, 19) : '';
          const color = getTickerColorStyle(c.ticker);
          const tickerTag = c.ticker
            ? `<span class="live-stream-ticker-badge" style="background: ${color.bg}; color: ${color.text}; border-color: ${color.border};">${escapeHtml(c.ticker)}</span>`
            : '';
          return `<div class="live-stream-row">
            <span class="text-dim text-xs">[${timeStr}]</span>
            ${tickerTag}
            <span>${icon}</span>
            <span style="font-weight: 600;">${escapeHtml(label)}</span>
            <span class="text-dim text-xs">(${c.latency_ms || 0}ms)</span>
          </div>`;
        })
        .join('');
      if (wasAtBottom) {
        stream.scrollTop = stream.scrollHeight;
      }
    } else {
      const targetLabel = state.inFlightConsoleFilter && state.inFlightConsoleFilter !== 'ALL'
        ? state.inFlightConsoleFilter
        : (activeTickers.length > 0 ? activeTickers.join(', ') : 'active agents');
      stream.innerHTML = `<div class="live-stream-row text-dim text-xs">Waiting for agent LLM or tool calls for ${escapeHtml(targetLabel)}...</div>`;
    }
  }
}

async function fetchInFlightDetail() {
  try {
    const detail = await api('/api/runs/in-flight-detail');
    state.inFlightDetail = detail || [];
    if (Array.isArray(detail)) {
      const tickers = detail.map((d) => d.ticker).filter(Boolean);
      if (tickers.length > 0 || state.inFlightTickers.length > 0) {
        state.inFlightTickers = tickers;
      }
    }
    renderInFlightLiveConsole();
  } catch (err) {
    console.warn('In-flight-detail poll error:', err);
  }
}

function renderRuns() {
  const container = document.getElementById('runs-container');
  let runs = [...state.runs];

  // Apply Filter
  if (state.activeFilter !== 'ALL') {
    runs = runs.filter((r) => {
      const act = (r.recommendation && r.recommendation.action) || '';
      const rating = ((r.recommendation && r.recommendation.rating) || '').trim().toUpperCase();
      return (rating === 'REVIEW' ? rating : act.toUpperCase()) === state.activeFilter;
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
      const isRunning = (run.status || '').toLowerCase() === 'running';
      const isFailed = (run.status || '').toLowerCase() === 'failed';
      const isAdvisory = (run.status || '').toLowerCase() === 'advisory';
      const actionUpper = (rec && rec.action) ? rec.action.trim().toUpperCase() : '';
      const ratingUpper = (rec && rec.rating) ? rec.rating.trim().toUpperCase() : '';
      const isReview = ratingUpper === 'REVIEW';
      const isHold = (ratingUpper === 'HOLD' || ratingUpper === 'NEUTRAL') && actionUpper !== 'BUY' && actionUpper !== 'SELL';
      const isNonActionable = isReview || isHold;
      const reviewHint = isReview
        ? '<span class="text-muted text-xs">Requires manual review (non-actionable)</span>'
        : '<span class="text-muted text-xs">HOLD — no order will be placed</span>';
      const orderSideUpper = (order && order.side) ? order.side.trim().toUpperCase() : '';

      // An order is already live executed if it has status 'submitted'
      const isSubmitted = Boolean(order && order.status === 'submitted');

      // Execution button determination:
      // Any non-running run with a recommendation or order can show an execution action:
      // - If already submitted: show '⚡ Re-execute Order' (btn-confirm-reexecute)
      // - If failed: show '⚡ Retry & Execute' (btn-success)
      // - If advisory/unexecuted: show prominent '⚡ Confirm & Execute' (btn-success)
      let executeBtnLabel = '⚡ Confirm & Execute';
      let executeBtnClass = 'btn-success';
      let canExecute = false;

      const hasRec = Boolean(rec && (rec.action || rec.rating || (rec.entry_price !== null && rec.entry_price !== undefined)));
      const hasOrder = Boolean(order && (orderSideUpper === 'BUY' || orderSideUpper === 'SELL'));

      if (!isRunning && !isNonActionable && (hasRec || hasOrder || isAdvisory)) {
        canExecute = true;
        if (isSubmitted) {
          executeBtnLabel = '⚡ Re-execute Order';
          executeBtnClass = 'btn-confirm-reexecute';
        } else if (order && order.status === 'failed') {
          executeBtnLabel = '⚡ Retry & Execute';
          executeBtnClass = 'btn-success';
        } else {
          executeBtnLabel = '⚡ Confirm & Execute';
          executeBtnClass = 'btn-success';
        }
      }

      let actionBadge = `<span class="badge">PENDING</span>`;
      if (isRunning) {
        actionBadge = `<span class="badge badge-running"><span class="spinner" style="width: 10px; height: 10px; margin-right: 4px;"></span> RUNNING</span>`;
      } else if (isFailed && !rec) {
        actionBadge = `<span class="badge badge-sell">FAILED</span>`;
      } else if (isReview) {
        actionBadge = '<span class="badge badge-review">REVIEW</span>';
      } else if (isHold) {
        actionBadge = '<span class="badge badge-hold">HOLD</span>';
      } else if (actionUpper || ratingUpper) {
        const displayAct = actionUpper || ratingUpper;
        if (displayAct === 'BUY' || displayAct === 'OVERWEIGHT') {
          actionBadge = `<span class="badge badge-buy">${escapeHtml(rec.action ? rec.action.toUpperCase() : rec.rating.toUpperCase())}</span>`;
        } else if (displayAct === 'SELL' || displayAct === 'UNDERWEIGHT') {
          actionBadge = `<span class="badge badge-sell">${escapeHtml(rec.action ? rec.action.toUpperCase() : rec.rating.toUpperCase())}</span>`;
        } else {
          actionBadge = `<span class="badge badge-hold">${escapeHtml(displayAct)}</span>`;
        }
      } else if (orderSideUpper) {
        if (orderSideUpper === 'BUY') actionBadge = `<span class="badge badge-buy">BUY</span>`;
        else if (orderSideUpper === 'SELL') actionBadge = `<span class="badge badge-sell">SELL</span>`;
        else actionBadge = `<span class="badge">${escapeHtml(orderSideUpper)}</span>`;
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

      // Order execution box (contains status badge and single context-appropriate Execute button)
      let orderBoxHtml = '';
      if (order) {
        if (order.status === 'submitted') {
          orderBoxHtml = `
          <div class="order-box order-box-submitted">
            <div>
              <strong>Order Executed:</strong> ${order.side.toUpperCase()} ${order.qty || ''} shares @ ${order.limit_price ? formatCurrency(order.limit_price) : 'MKT'}
              <span class="text-xs">(${order.order_type.toUpperCase()} • Bracket • Alpaca ID: ${order.alpaca_order_id || 'Submitted'})</span>
            </div>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
              <span class="badge badge-buy">SUBMITTED</span>
              ${isReview ? reviewHint : `<button class="btn btn-confirm-reexecute btn-sm btn-confirm-execute" onclick="openExecuteModal('${run.id}')" title="Order was already submitted. Click to review or re-execute.">
                ⚡ Re-execute
              </button>`}
            </div>
          </div>
        `;
        } else if (order.status === 'skipped') {
          orderBoxHtml = `
          <div class="order-box order-box-skipped">
            <div>
              <strong>Skipped:</strong> ${escapeHtml(order.skip_reason || 'Order skipped')}
            </div>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
              <span class="badge">SKIPPED</span>
              ${isNonActionable ? reviewHint : `<button class="btn btn-success btn-sm btn-confirm-execute" onclick="openExecuteModal('${run.id}')" title="Confirm &amp; Execute order to Alpaca">
                ⚡ Confirm &amp; Execute
              </button>`}
            </div>
          </div>
        `;
        } else if (order.status === 'failed') {
          orderBoxHtml = `
          <div class="order-box order-box-failed">
            <div>
              <strong>Order Error:</strong> ${escapeHtml(order.error_message || 'Submission failed')}
            </div>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
              <span class="badge badge-sell">FAILED</span>
              ${isNonActionable ? reviewHint : `<button class="btn btn-success btn-sm btn-confirm-execute" onclick="openExecuteModal('${run.id}')" title="Retry &amp; Execute order to Alpaca">
                ⚡ Retry &amp; Execute
              </button>`}
            </div>
          </div>
        `;
        }
      } else if (isNonActionable && !isRunning) {
        orderBoxHtml = `<div class="order-box order-box-advisory">${reviewHint}</div>`;
      } else if (canExecute) {
        orderBoxHtml = `
        <div class="order-box order-box-advisory">
          <div>
            <strong>Advisory Mode:</strong> Recommendation ready for review (no order submitted yet)
          </div>
          <div style="display: flex; gap: 0.5rem; align-items: center;">
            <span class="badge badge-hold">ADVISORY</span>
            ${isReview ? reviewHint : `<button class="btn btn-success btn-sm btn-confirm-execute" onclick="openExecuteModal('${run.id}')" title="Confirm &amp; Execute order to Alpaca">
              ⚡ Confirm &amp; Execute
            </button>`}
          </div>
        </div>
      `;
      }

      // Reasoning
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
            ${isAdvisory ? '<span class="badge badge-hold">ADVISORY</span>' : ''}
            <span class="run-meta">• ${formatDate(run.started_at)} • Date: ${run.trade_date} • ${run.trigger}</span>
          </div>
          <div class="run-card-actions" style="display: flex; gap: 0.35rem; align-items: center;">
            <button class="btn btn-primary btn-sm" onclick="openLogsModal('${run.id}', 'summary')">
              📋 Summary
            </button>
            <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${run.id}', 'llm-calls')">
              🤖 LLM Calls
            </button>
            <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${run.id}', 'raw-log')">
              📜 Log
            </button>
            ${!isRunning ? `<button class="btn btn-danger btn-sm" onclick="deleteRun('${run.id}')" title="Delete run" aria-label="Delete run">🗑</button>` : ''}
          </div>
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
  if (text === null || text === undefined) return '';
  return String(text)
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

// ---------------------------------------------------------------------------
// Symbol Autocomplete Controller
// ---------------------------------------------------------------------------

const autocompleteState = {
  isOpen: false,
  query: '',
  suggestions: [],
  activeIndex: -1,
  debounceTimer: null,
  cache: new Map(),
};

function highlightMatch(text, query) {
  if (!text) return '';
  if (!query) return escapeHtml(text);
  const q = query.trim();
  if (!q) return escapeHtml(text);
  const escapedQ = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp(`(${escapedQ})`, 'gi');
  return escapeHtml(text).replace(regex, '<span class="autocomplete-highlight">$1</span>');
}

function openAutocompleteDropdown() {
  const dropdown = document.getElementById('symbol-autocomplete-list');
  const symInput = document.getElementById('input-symbol');
  if (!dropdown) return;
  dropdown.classList.remove('hidden');
  autocompleteState.isOpen = true;
  if (symInput) symInput.setAttribute('aria-expanded', 'true');
}

function closeAutocompleteDropdown() {
  const dropdown = document.getElementById('symbol-autocomplete-list');
  const symInput = document.getElementById('input-symbol');
  if (!dropdown) return;
  dropdown.classList.add('hidden');
  autocompleteState.isOpen = false;
  autocompleteState.activeIndex = -1;
  if (symInput) {
    symInput.setAttribute('aria-expanded', 'false');
    symInput.removeAttribute('aria-activedescendant');
  }
}

function renderAutocompleteSuggestions(items, query) {
  const dropdown = document.getElementById('symbol-autocomplete-list');
  if (!dropdown) return;

  autocompleteState.suggestions = items || [];
  autocompleteState.activeIndex = -1;

  if (!items || items.length === 0) {
    dropdown.innerHTML = '<div class="autocomplete-empty">No matching symbols found</div>';
    openAutocompleteDropdown();
    return;
  }

  const html = items
    .map((item, idx) => {
      const symHtml = highlightMatch(item.symbol, query);
      const nameHtml = highlightMatch(item.name || '', query);
      return `
      <div
        class="autocomplete-item"
        role="option"
        id="symbol-opt-${idx}"
        data-index="${idx}"
        data-symbol="${escapeHtml(item.symbol)}"
        data-name="${escapeHtml(item.name || '')}"
        aria-selected="false"
      >
        <span class="autocomplete-symbol">${symHtml}</span>
        <span class="autocomplete-name" title="${escapeHtml(item.name || '')}">${nameHtml}</span>
      </div>`;
    })
    .join('');

  dropdown.innerHTML = html;
  openAutocompleteDropdown();
}

function updateActiveAutocompleteItem() {
  const dropdown = document.getElementById('symbol-autocomplete-list');
  const symInput = document.getElementById('input-symbol');
  if (!dropdown) return;

  const items = dropdown.querySelectorAll('.autocomplete-item');
  items.forEach((el, idx) => {
    if (idx === autocompleteState.activeIndex) {
      el.classList.add('active');
      el.setAttribute('aria-selected', 'true');
      if (symInput) symInput.setAttribute('aria-activedescendant', el.id);
      el.scrollIntoView({ block: 'nearest' });
    } else {
      el.classList.remove('active');
      el.setAttribute('aria-selected', 'false');
    }
  });

  if (autocompleteState.activeIndex === -1 && symInput) {
    symInput.removeAttribute('aria-activedescendant');
  }
}

function selectAutocompleteItem(item) {
  if (!item) return;
  const symInput = document.getElementById('input-symbol');
  const notesInput = document.getElementById('input-notes');

  if (symInput) {
    symInput.value = item.symbol;
  }
  if (notesInput && !notesInput.value.trim() && item.name) {
    notesInput.value = item.name;
  }
  closeAutocompleteDropdown();
  if (symInput) {
    symInput.focus();
  }
}

async function fetchAutocompleteSuggestions(query) {
  const q = query.trim();
  if (!q) {
    closeAutocompleteDropdown();
    autocompleteState.suggestions = [];
    return;
  }

  const cacheKey = q.toLowerCase();
  if (autocompleteState.cache.has(cacheKey)) {
    const cached = autocompleteState.cache.get(cacheKey);
    renderAutocompleteSuggestions(cached, q);
    return;
  }

  try {
    const res = await api(`/api/symbols/search?q=${encodeURIComponent(q)}&limit=10`);
    const results = Array.isArray(res) ? res : [];
    autocompleteState.cache.set(cacheKey, results);
    const symInput = document.getElementById('input-symbol');
    if (symInput && symInput.value.trim().toLowerCase() === cacheKey) {
      renderAutocompleteSuggestions(results, q);
    }
  } catch (err) {
    console.warn('Autocomplete lookup failed:', err);
  }
}

function initSymbolAutocomplete() {
  const symInput = document.getElementById('input-symbol');
  const dropdown = document.getElementById('symbol-autocomplete-list');
  const wrap = document.getElementById('symbol-autocomplete-wrap');
  if (!symInput || !dropdown) return;

  symInput.addEventListener('input', (e) => {
    const val = e.target.value;
    if (autocompleteState.debounceTimer) {
      clearTimeout(autocompleteState.debounceTimer);
    }
    if (!val.trim()) {
      closeAutocompleteDropdown();
      return;
    }
    autocompleteState.debounceTimer = setTimeout(() => {
      fetchAutocompleteSuggestions(val);
    }, 150);
  });

  symInput.addEventListener('focus', () => {
    const val = symInput.value.trim();
    if (val) {
      fetchAutocompleteSuggestions(val);
    }
  });

  symInput.addEventListener('keydown', (e) => {
    if (!autocompleteState.isOpen) {
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && symInput.value.trim()) {
        fetchAutocompleteSuggestions(symInput.value.trim());
      }
      return;
    }

    const itemsCount = autocompleteState.suggestions.length;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (itemsCount === 0) return;
      autocompleteState.activeIndex = (autocompleteState.activeIndex + 1) % itemsCount;
      updateActiveAutocompleteItem();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (itemsCount === 0) return;
      autocompleteState.activeIndex =
        autocompleteState.activeIndex <= 0 ? itemsCount - 1 : autocompleteState.activeIndex - 1;
      updateActiveAutocompleteItem();
    } else if (e.key === 'Enter') {
      if (autocompleteState.activeIndex >= 0 && autocompleteState.suggestions[autocompleteState.activeIndex]) {
        e.preventDefault();
        selectAutocompleteItem(autocompleteState.suggestions[autocompleteState.activeIndex]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAutocompleteDropdown();
    } else if (e.key === 'Tab') {
      if (autocompleteState.activeIndex >= 0 && autocompleteState.suggestions[autocompleteState.activeIndex]) {
        selectAutocompleteItem(autocompleteState.suggestions[autocompleteState.activeIndex]);
      } else {
        closeAutocompleteDropdown();
      }
    }
  });

  dropdown.addEventListener('mousedown', (e) => {
    const itemEl = e.target.closest('.autocomplete-item');
    if (!itemEl) return;
    e.preventDefault();
    const idx = parseInt(itemEl.dataset.index, 10);
    if (!isNaN(idx) && autocompleteState.suggestions[idx]) {
      selectAutocompleteItem(autocompleteState.suggestions[idx]);
    }
  });

  dropdown.addEventListener('mouseover', (e) => {
    const itemEl = e.target.closest('.autocomplete-item');
    if (!itemEl) return;
    const idx = parseInt(itemEl.dataset.index, 10);
    if (!isNaN(idx) && idx !== autocompleteState.activeIndex) {
      autocompleteState.activeIndex = idx;
      updateActiveAutocompleteItem();
    }
  });

  document.addEventListener('click', (e) => {
    if (wrap && !wrap.contains(e.target)) {
      closeAutocompleteDropdown();
    }
  });
}

async function deleteRun(runId) {
  const run = state.runs.find((item) => item.id === runId);
  if (!run) return;
  if (!confirm(`Delete ${run.ticker} run for ${run.trade_date} (${formatDate(run.started_at)}) and its history?`)) return;
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' });
    showToast(`Deleted ${run.ticker} run`, 'success');
    await refreshAll();
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
// Log Viewer & Insight Modal
// ---------------------------------------------------------------------------

function switchModalTab(tabName) {
  state.activeModalTab = tabName;
  document.querySelectorAll('.modal-tab-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
  });
  document.querySelectorAll('.tab-pane').forEach((pane) => {
    pane.classList.add('hidden');
  });
  const activePane = document.getElementById(`pane-${tabName}`);
  if (activePane) activePane.classList.remove('hidden');

  if (tabName === 'summary') {
    renderModalSummary();
  } else if (tabName === 'llm-calls') {
    filterAndRenderLLMCalls();
  } else if (tabName === 'raw-log') {
    const pre = document.getElementById('modal-logs-pre');
    if (pre && document.getElementById('modal-autoscroll')?.checked) {
      pre.scrollTop = pre.scrollHeight;
    }
  }
}

async function openLogsModal(runId, initialTab = 'summary') {
  if (!runId) return;

  if (state.modalPollInterval) {
    clearInterval(state.modalPollInterval);
    state.modalPollInterval = null;
  }

  state.activeModalRunId = runId;
  state.activeModalRun = null;
  state.activeModalSummary = null;
  state.activeModalCalls = null; // null indicates loading state

  // Reset modal UI to loading states
  const titleEl = document.getElementById('modal-title');
  if (titleEl) titleEl.textContent = `Loading run details...`;
  const badge = document.getElementById('modal-status-badge');
  if (badge) {
    badge.textContent = '...';
    badge.className = 'badge';
  }
  const callsBadge = document.getElementById('modal-calls-badge');
  if (callsBadge) callsBadge.textContent = '...';

  const summaryBox = document.getElementById('modal-summary');
  if (summaryBox) summaryBox.innerHTML = '';
  const pre = document.getElementById('modal-logs-pre');
  if (pre) pre.textContent = 'Loading logs...';

  // Reset execution action button
  const btnHeader = document.getElementById('modal-btn-confirm-execute');
  if (btnHeader) { btnHeader.classList.add('hidden'); btnHeader.style.display = 'none'; }

  switchModalTab(initialTab);

  const modal = document.getElementById('logs-modal');
  if (modal) modal.classList.remove('hidden');

  await updateModalData();

  // If run is running, poll for live updates
  if (state.activeModalRun && state.activeModalRun.status === 'running' && !state.modalPollInterval) {
    state.modalPollInterval = setInterval(async () => {
      if (!state.activeModalRunId) {
        clearInterval(state.modalPollInterval);
        state.modalPollInterval = null;
        return;
      }
      await updateModalData();
    }, 2500);
  }
}

function closeLogsModal() {
  state.activeModalRunId = null;
  state.activeModalRun = null;
  state.activeModalSummary = null;
  state.activeModalCalls = [];
  if (state.modalPollInterval) {
    clearInterval(state.modalPollInterval);
    state.modalPollInterval = null;
  }
  const btnHeader = document.getElementById('modal-btn-confirm-execute');
  if (btnHeader) { btnHeader.classList.add('hidden'); btnHeader.style.display = 'none'; }

  const modal = document.getElementById('logs-modal');
  if (modal) modal.classList.add('hidden');
}

function openExecuteModalFromDetails() {
  if (state.activeModalRunId) {
    openExecuteModal(state.activeModalRunId);
  }
}
window.openExecuteModalFromDetails = openExecuteModalFromDetails;

function formatPayload(payload) {
  if (payload === null || payload === undefined) return '(None)';
  if (typeof payload === 'string') {
    const trimmed = payload.trim();
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        const parsed = JSON.parse(trimmed);
        return JSON.stringify(parsed, null, 2);
      } catch {
        // Not valid JSON, return as string
      }
    }
    return payload;
  }
  if (typeof payload === 'object') {
    try {
      return JSON.stringify(payload, null, 2);
    } catch {
      return String(payload);
    }
  }
  return String(payload);
}

async function updateModalData() {
  const runId = state.activeModalRunId;
  if (!runId) return;

  try {
    const [runRes, summaryRes, callsRes] = await Promise.allSettled([
      api(`/api/runs/${runId}`),
      api(`/api/runs/${runId}/summary`),
      api(`/api/runs/${runId}/llm-calls`),
    ]);

    // Check if the user closed the modal or switched runs while requests were in flight
    if (state.activeModalRunId !== runId) return;

    // 1. Process Run Details & Logs
    try {
      if (runRes.status === 'fulfilled' && runRes.value) {
        const run = runRes.value;
        state.activeModalRun = run;
        const isReview = ((run.recommendation && run.recommendation.rating) || '').trim().toUpperCase() === 'REVIEW';
        document.getElementById('modal-review-badge')?.classList.toggle('hidden', !isReview);
        document.getElementById('modal-review-hint')?.classList.toggle('hidden', !isReview || (run.status || '').toLowerCase() === 'running');

        const titleEl = document.getElementById('modal-title');
        if (titleEl) {
          titleEl.textContent = `Run ${run.ticker || 'N/A'} (${run.trade_date || ''})`;
        }

        const badge = document.getElementById('modal-status-badge');
        if (badge) {
          const st = (run.status || 'unknown').toUpperCase();
          badge.textContent = st;
          badge.className = `badge ${
            (run.status || '').toLowerCase() === 'completed'
              ? 'badge-buy'
              : (run.status || '').toLowerCase() === 'running'
              ? 'badge-running'
              : (run.status || '').toLowerCase() === 'advisory'
              ? 'badge-hold'
              : 'badge-sell'
          }`;
        }

        // Update header execution button in Run Details modal
        const btnHeader = document.getElementById('modal-btn-confirm-execute');
        if (run && !isReview && (run.status || '').toLowerCase() !== 'running') {
          const isSub = Boolean(run.order && run.order.status === 'submitted');
          const isErr = Boolean(run.order && run.order.status === 'failed');
          const btnLabel = isSub ? '⚡ Re-execute Order' : (isErr ? '⚡ Retry & Execute' : '⚡ Confirm & Execute');
          const btnClass = isSub ? 'btn btn-confirm-reexecute btn-confirm-execute btn-sm' : 'btn btn-success btn-confirm-execute btn-sm';

          if (btnHeader) {
            btnHeader.innerHTML = btnLabel;
            btnHeader.className = btnClass;
            btnHeader.classList.remove('hidden');
            btnHeader.style.display = 'inline-flex';
          }
        } else {
          if (btnHeader) {
            btnHeader.classList.add('hidden');
            btnHeader.style.display = 'none';
          }
        }

        // Update raw log tab summary box
        const summaryBox = document.getElementById('modal-summary');
        if (summaryBox) {
          let recSummary = 'No recommendation produced yet.';
          if (run.recommendation) {
            recSummary = `Recommendation: <strong>${escapeHtml(
              isReview ? 'REVIEW' : run.recommendation.action || 'HOLD'
            )}</strong> (${escapeHtml(run.recommendation.rating || 'N/A')}), Entry: ${formatCurrency(
              run.recommendation.entry_price
            )}, Target: ${formatCurrency(run.recommendation.price_target)}, Stop: ${formatCurrency(
              run.recommendation.stop_loss
            )}`;
          }
          let orderSummary = '';
          if (run.order) {
            orderSummary = `<br>Order Execution: ${escapeHtml(
              (run.order.status || '').toUpperCase()
            )} (${escapeHtml(
              run.order.skip_reason || run.order.alpaca_order_id || 'Submitted'
            )})`;
          } else if ((run.status || '').toLowerCase() === 'advisory') {
            orderSummary = '<br>Order Execution: <strong>ADVISORY</strong> (No order generated; auto-trade disabled)';
          }
          summaryBox.innerHTML = `<strong>Status:</strong> ${escapeHtml(
            (run.status || '').toUpperCase()
          )} • <strong>Started:</strong> ${formatDate(run.started_at)}${orderSummary}<br>${recSummary}`;
        }

        // Update raw logs pre
        const pre = document.getElementById('modal-logs-pre');
        if (pre) {
          pre.textContent = run.log_output || '(Waiting for output stream...)';
          if (document.getElementById('modal-autoscroll')?.checked) {
            pre.scrollTop = pre.scrollHeight;
          }
        }
      } else {
        console.error('Failed to fetch run details:', runRes.reason);
        const titleEl = document.getElementById('modal-title');
        if (titleEl && !state.activeModalRun) {
          titleEl.textContent = `Run ${runId.substring(0, 8)}`;
        }
        const badge = document.getElementById('modal-status-badge');
        if (badge && !state.activeModalRun) {
          badge.textContent = 'ERROR';
          badge.className = 'badge badge-sell';
        }
      }
    } catch (runErr) {
      console.error('Error processing run details:', runErr);
    }

    // 2. Process Decision Summary (Tab 1)
    try {
      if (summaryRes.status === 'fulfilled' && summaryRes.value) {
        state.activeModalSummary = summaryRes.value;
        renderModalSummary();
      } else {
        console.error('Failed to fetch summary:', summaryRes.reason);
        const overallText = document.getElementById('summary-overall-text');
        const timeline = document.getElementById('modal-timeline');
        if (overallText && !state.activeModalSummary) {
          overallText.textContent = state.activeModalRun
            ? `Analysis for ${state.activeModalRun.ticker}`
            : 'Summary unavailable';
        }
        if (timeline && !state.activeModalSummary) {
          timeline.innerHTML = `<div class="text-center py-4 text-muted">No structured decision steps available for this run.</div>`;
        }
      }
    } catch (sumErr) {
      console.error('Error rendering summary:', sumErr);
    }

    // 3. Process LLM & Tool Calls (Tab 2)
    try {
      if (callsRes.status === 'fulfilled') {
        state.activeModalCalls = Array.isArray(callsRes.value) ? callsRes.value : [];
        const callsBadge = document.getElementById('modal-calls-badge');
        if (callsBadge) callsBadge.textContent = state.activeModalCalls.length;
        filterAndRenderLLMCalls();
      } else {
        console.error('Failed to fetch LLM calls:', callsRes.reason);
        state.activeModalCalls = [];
        const callsBadge = document.getElementById('modal-calls-badge');
        if (callsBadge) callsBadge.textContent = '0';
        filterAndRenderLLMCalls();
      }
    } catch (callsErr) {
      console.error('Error rendering LLM calls:', callsErr);
    }

    // Refresh overall meta stats with verified call count
    // Refresh overall summary banner with verified call count and action button
    if (state.activeModalSummary) {
      renderModalSummary();
    }

    // If run is completed, advisory, or failed, stop polling interval
    if (state.activeModalRun && (state.activeModalRun.status || '').toLowerCase() !== 'running' && state.modalPollInterval) {
      clearInterval(state.modalPollInterval);
      state.modalPollInterval = null;
    }
  } catch (err) {
    console.error('Error updating modal data:', err);
  }
}

function renderModalSummary() {
  const sum = state.activeModalSummary;
  const overallText = document.getElementById('summary-overall-text');
  const overallMeta = document.getElementById('summary-overall-meta');
  const timeline = document.getElementById('modal-timeline');

  if (!sum) {
    if (overallText) {
      overallText.innerHTML = '<span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:6px; width:14px; height:14px;"></span> Loading decision summary...';
    }
    if (overallMeta) overallMeta.innerHTML = '';
    if (timeline) {
      timeline.innerHTML = '<div class="text-center py-6 text-muted"><span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:6px; width:14px; height:14px;"></span> Loading decision timeline...</div>';
    }
    return;
  }

  if (overallText) overallText.textContent = sum.overall || 'Analysis completed';
  if (overallMeta) {
    const count = Array.isArray(state.activeModalCalls) ? state.activeModalCalls.length : 0;
    overallMeta.innerHTML = `
      <span>Confidence: <strong class="badge badge-paper">${escapeHtml(sum.confidence || 'Neutral')}</strong></span>
      <span>•</span>
      <span>${count} LLM &amp; Tool Calls Recorded</span>
    `;
  }

  if (!timeline) return;
  const steps = Array.isArray(sum.steps) ? sum.steps : [];
  if (steps.length === 0) {
    timeline.innerHTML = '<div class="text-center py-4 text-muted">No decision steps recorded.</div>';
    return;
  }

  timeline.innerHTML = steps
    .map((step, idx) => {
      if (!step || typeof step !== 'object') return '';
      const stepNum = step.n !== undefined ? step.n : idx + 1;
      const isFailed = Boolean(step.failed);
      const isSkipped = Boolean(step.skipped);
      const stepState = step.state || (isFailed ? 'failed' : isSkipped ? 'skipped' : 'done');
      const isActive = stepState === 'active';
      const isPending = stepState === 'pending';

      const stepClasses = ['timeline-step'];
      if (isFailed) stepClasses.push('step-failed');
      if (isSkipped) stepClasses.push('step-skipped');
      if (isActive) stepClasses.push('step-active');
      if (isPending) stepClasses.push('step-pending');

      let statsPills = '';
      if (step.llm_count !== undefined && step.llm_count > 0) {
        statsPills += `<span class="chip-stat">🤖 ${step.llm_count} LLM call${step.llm_count === 1 ? '' : 's'}</span>`;
      }
      if (Array.isArray(step.tools) && step.tools.length > 0) {
        statsPills += step.tools
          .map((t) => `<span class="chip-stat chip-tool">⚡ ${escapeHtml(t)}</span>`)
          .join('');
      } else if (typeof step.tools === 'string' && step.tools) {
        statsPills += `<span class="chip-stat chip-tool">⚡ ${escapeHtml(step.tools)}</span>`;
      }
      if (!isPending && step.duration !== undefined && step.duration !== null) {
        const durStr = formatStepDuration(step.duration);
        if (durStr) {
          statsPills += `<span class="chip-stat chip-duration">⏱ ${escapeHtml(durStr)}</span>`;
        }
      }

      let headerBadges = '';
      if (isFailed) {
        headerBadges += '<span class="badge badge-status-err">FAILED</span>';
      }
      if (isSkipped) {
        headerBadges += '<span class="badge badge-skipped">Skipped</span>';
      }
      if (isActive) {
        headerBadges += '<span class="badge badge-running"><span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:4px; width:10px; height:10px; border-width:1.5px;"></span>IN PROGRESS</span>';
      }

      let findingBox = '';
      if (isPending) {
        findingBox = `
          <div class="step-pending-placeholder text-muted text-xs">
            <span>⏳ Not started yet</span>
          </div>
        `;
      } else if (isActive) {
        if (step.key_find && !step.key_find.toLowerCase().startsWith('in progress')) {
          findingBox = `
            <div class="key-find-box">
              <span class="text-dim text-xs" style="text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">Key Finding</span>
              <span class="key-find-quote">"${escapeHtml(step.key_find)}"</span>
            </div>
          `;
        } else {
          findingBox = `
            <div class="step-active-placeholder text-muted text-xs">
              <span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:6px; width:12px; height:12px; border-width:2px;"></span>
              <span>${escapeHtml(step.key_find || 'In progress…')}</span>
            </div>
          `;
        }
      } else if (step.key_find) {
        findingBox = `
          <div class="key-find-box">
            <span class="text-dim text-xs" style="text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px;">Key Finding</span>
            <span class="key-find-quote">"${escapeHtml(step.key_find)}"</span>
          </div>
        `;
      }

      return `
      <div class="${stepClasses.join(' ')}">
        <div class="step-number-bubble">${escapeHtml(stepNum)}</div>
        <div class="step-content">
          <div class="step-header-row">
            <span class="step-title">${escapeHtml(step.title || 'Decision Step')}</span>
            <span class="step-who">${escapeHtml(step.who || '')}</span>
            ${headerBadges}
          </div>
          ${isFailed && step.error ? `<div class="step-error-box text-danger text-xs"><strong>Error:</strong> ${escapeHtml(step.error)}</div>` : ''}
          <div class="step-what">${escapeHtml(step.what || '')}</div>
          ${findingBox}
          ${statsPills ? `<div class="step-meta-pills">${statsPills}</div>` : ''}
        </div>
      </div>
    `;
    })
    .join('');
}

function filterAndRenderLLMCalls() {
  const list = document.getElementById('llm-calls-list');
  if (!list) return;

  if (state.activeModalCalls === null) {
    list.innerHTML = '<div class="text-center py-6 text-muted"><span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:6px; width:14px; height:14px;"></span> Loading LLM calls...</div>';
    return;
  }

  const allCalls = Array.isArray(state.activeModalCalls) ? state.activeModalCalls : [];

  // Update pill counts
  const countAll = allCalls.length;
  const countLLM = allCalls.filter((c) => c && c.kind === 'llm').length;
  const countTool = allCalls.filter((c) => c && c.kind === 'tool').length;

  const elAll = document.getElementById('count-all');
  const elLLM = document.getElementById('count-llm');
  const elTool = document.getElementById('count-tool');
  if (elAll) elAll.textContent = countAll;
  if (elLLM) elLLM.textContent = countLLM;
  if (elTool) elTool.textContent = countTool;

  const query = (state.llmSearchQuery || '').toLowerCase().trim();
  const kindFilter = state.llmFilterKind || 'ALL';

  let filtered = allCalls.filter((c) => {
    if (!c || typeof c !== 'object') return false;
    if (kindFilter !== 'ALL' && c.kind !== kindFilter) return false;
    if (!query) return true;
    const matchAgent = (c.agent || c.node || '').toLowerCase().includes(query);
    const matchTool = (c.tool_name || '').toLowerCase().includes(query);
    const matchModel = (c.model || '').toLowerCase().includes(query);
    let matchReq = false;
    try {
      matchReq =
        typeof c.request === 'string'
          ? c.request.toLowerCase().includes(query)
          : JSON.stringify(c.request || '').toLowerCase().includes(query);
    } catch {}
    let matchResp = false;
    try {
      matchResp =
        typeof c.response === 'string'
          ? c.response.toLowerCase().includes(query)
          : JSON.stringify(c.response || '').toLowerCase().includes(query);
    } catch {}
    return matchAgent || matchTool || matchModel || matchReq || matchResp;
  });

  if (filtered.length === 0) {
    if (allCalls.length === 0) {
      list.innerHTML = `<div class="text-center py-6 text-muted">No LLM or tool calls recorded for this run.</div>`;
    } else {
      list.innerHTML = `<div class="text-center py-6 text-muted">No calls matched the current filter/search.</div>`;
    }
    return;
  }

  list.innerHTML = filtered
    .map((c, idx) => {
      if (!c || typeof c !== 'object') return '';
      const isTool = c.kind === 'tool';
      const kindBadge = isTool
        ? `<span class="badge badge-kind-tool">TOOL</span>`
        : `<span class="badge badge-kind-llm">LLM</span>`;
      const toolBadge = c.tool_name
        ? `<span class="badge badge-tool-name">${escapeHtml(c.tool_name)}</span>`
        : '';
      const statusBadge = c.ok
        ? `<span class="badge badge-status-ok">OK</span>`
        : `<span class="badge badge-status-err">ERROR</span>`;
      const latencyStr = `${c.latency_ms || 0}ms`;
      const seqStr = c.seq !== undefined ? c.seq : idx + 1;

      return `
      <details class="llm-call-card">
        <summary class="llm-call-header">
          <div class="call-header-left">
            <span class="call-seq">#${escapeHtml(seqStr)}</span>
            <span class="call-agent">${escapeHtml(c.agent || c.node || 'Unknown')}</span>
            ${kindBadge}
            ${toolBadge}
            <span class="text-xs text-dim">${escapeHtml(c.model || '')}</span>
          </div>
          <div class="call-header-right">
            <span class="call-meta-item">${latencyStr}</span>
            ${statusBadge}
          </div>
        </summary>
        <div class="llm-call-body">
          <div class="pre-box-wrap">
            <div class="pre-box-title">Request Prompt / Input</div>
            <pre class="pre-call">${escapeHtml(formatPayload(c.request))}</pre>
          </div>
          <div class="pre-box-wrap">
            <div class="pre-box-title">Response / Output</div>
            <pre class="pre-call">${escapeHtml(formatPayload(c.response))}</pre>
          </div>
          ${c.error ? `<div class="text-danger text-xs"><strong>Error:</strong> ${escapeHtml(c.error)}</div>` : ''}
        </div>
      </details>
    `;
    })
    .join('');
}


// ---------------------------------------------------------------------------
// Settings Modal
// ---------------------------------------------------------------------------

function openSettingsModal() {
  document.getElementById('settings-auto-trade').checked = !!state.settings.auto_trade;
  document.getElementById('settings-sched-enabled').checked = !!state.settings.schedule_enabled;
  document.getElementById('settings-interval').value = String(state.settings.schedule_interval_minutes || 1440);
  // Fill LLM config form from current effective config
  if (state.llmConfig) setLlmFormValues(state.llmConfig);
  // Reset test result
  const resultEl = document.getElementById('llm-test-result');
  if (resultEl) { resultEl.textContent = ''; resultEl.className = 'llm-test-result'; }
  document.getElementById('settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
}

function resetLlmApiKeyControl(cfg) {
  const apiKeyEl = document.getElementById('llm-api-key');
  document.getElementById('btn-clear-api-key').classList.toggle('hidden', !cfg.api_key_set);
  apiKeyEl.dataset.cleared = 'false';
  apiKeyEl.placeholder = 'API key (optional if in .env)';
}

function clearLlmApiKey() {
  const apiKeyEl = document.getElementById('llm-api-key');
  apiKeyEl.value = '';
  apiKeyEl.dataset.cleared = 'true';
  apiKeyEl.placeholder = 'Cleared — click Save to remove saved key';
  document.getElementById('btn-clear-api-key').classList.add('hidden');
}

function setLlmFormValues(cfg) {
  resetLlmApiKeyControl(cfg);
  const providerEl = document.getElementById('llm-provider');
  const keyEl = document.getElementById('llm-api-key');
  const baseUrlEl = document.getElementById('llm-base-url');
  const deepEl = document.getElementById('llm-deep-model');
  const quickEl = document.getElementById('llm-quick-model');
  if (!providerEl) return;

  const provider = cfg.provider || '';
  providerEl.value = provider; // select falls back to the first option if the value is absent
  if (keyEl) keyEl.value = ''; // always start blank so user only types a new key
  if (baseUrlEl) baseUrlEl.value = cfg.base_url || '';
  if (deepEl) deepEl.value = cfg.deep_model || '';
  if (quickEl) quickEl.value = cfg.quick_model || '';

  // Show/hide the API key field based on whether provider needs one
  const keyGroup = document.getElementById('llm-api-key').closest('.form-group');
  const needsKey = ['google','openai','anthropic','xai','deepseek','qwen','qwen-cn','glm','glm-cn','minimax','minimax-cn','openrouter','mistral','kimi','groq','nvidia','azure'].includes(provider);
  if (keyGroup) keyGroup.classList.toggle('hidden', !needsKey);
}

async function saveSettingsFromModal() {
  const autoTrade = document.getElementById('settings-auto-trade').checked;
  const schedEnabled = document.getElementById('settings-sched-enabled').checked;
  const interval = parseInt(document.getElementById('settings-interval').value, 10);

  // Blank keys preserve the saved key unless Clear was explicitly selected.
  const llmPayload = {};
  const provider = document.getElementById('llm-provider').value;
  const apiKeyEl = document.getElementById('llm-api-key');
  const apiKey = apiKeyEl.value.trim();
  const baseUrl = document.getElementById('llm-base-url').value.trim();
  const deepModel = document.getElementById('llm-deep-model').value.trim();
  const quickModel = document.getElementById('llm-quick-model').value.trim();
  if (document.getElementById('llm-provider').value !== (state.llmConfig?.provider || '')) llmPayload.provider = provider || '';
  if (apiKeyEl.dataset.cleared === 'true') llmPayload.api_key = '';
  else if (apiKey) llmPayload.api_key = apiKey;
  llmPayload.base_url = baseUrl;
  llmPayload.deep_model = deepModel;
  llmPayload.quick_model = quickModel;

  try {
    const results = await Promise.all([
      api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          auto_trade: autoTrade,
          schedule_enabled: schedEnabled,
          schedule_interval_minutes: interval,
        }),
      }),
      (Object.keys(llmPayload).length > 0)
        ? api('/api/llm-config', { method: 'POST', body: JSON.stringify(llmPayload) })
        : Promise.resolve(null),
    ]);
    state.settings = results[0];
    if (results[1]) state.llmConfig = results[1];
    renderSettings();
    renderLlmConfig();
    closeSettingsModal();
    showToast(Object.keys(llmPayload).length > 0 ? 'Settings & LLM config saved' : 'Settings saved', 'success');
  } catch (err) {
    showToast(`Failed to save: ${err.message}`, 'error');
  }
}

async function testLlmConnection() {
  const provider = document.getElementById('llm-provider').value;
  const keyInput = document.getElementById('llm-api-key').value.trim();
  const baseUrl = document.getElementById('llm-base-url').value.trim();
  const deepModel = document.getElementById('llm-deep-model').value.trim();
  const quickModel = document.getElementById('llm-quick-model').value.trim();
  const model = quickModel || deepModel || null; // test with quick model (fast) or deep

  const resultEl = document.getElementById('llm-test-result');
  const btn = document.getElementById('btn-test-llm');
  resultEl.textContent = 'Testing…';
  resultEl.className = 'llm-test-result';
  btn.disabled = true;
  try {
    const res = await api('/api/llm-config/test', {
      method: 'POST',
      body: JSON.stringify({ provider: provider || 'google', api_key: keyInput || null, base_url: baseUrl || null, model }),
    });
    resultEl.textContent = '✅ ' + (res.message || 'Connected');
    resultEl.className = 'llm-test-result llm-test-ok';
  } catch (err) {
    resultEl.textContent = '❌ ' + (err.message || 'Failed');
    resultEl.className = 'llm-test-result llm-test-err';
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Execute Recommendation Modal & Handlers
// ---------------------------------------------------------------------------

function openExecuteModal(runId) {
  const run = state.runs.find((r) => r.id === runId) || (state.activeModalRun && state.activeModalRun.id === runId ? state.activeModalRun : null);
  if (!run) {
    showToast('Run not found', 'error');
    return;
  }
  const rec = run.recommendation;
  const order = run.order;
  if (!rec && !order) {
    showToast('No recommendation or order found for this run', 'error');
    return;
  }
  const isSubmitted = Boolean(order && order.status === 'submitted');

  const action = ((rec && rec.action) || '').toUpperCase();
  const rating = ((rec && rec.rating) || '').toUpperCase();
  const isNonActionable = rating === 'REVIEW' || ((rating === 'HOLD' || rating === 'NEUTRAL') && action !== 'BUY' && action !== 'SELL');

  if (isNonActionable && !isSubmitted) {
    const alertEl2 = document.getElementById('exec-modal-alert');
    if (alertEl2) {
      alertEl2.innerHTML = `⛔ <strong>Non-actionable rating (${rating || 'HOLD'}):</strong> This recommendation cannot be executed as an order. A ${rating || 'HOLD'} decision means no trade is placed.`;
      alertEl2.style.color = '';
      alertEl2.className = 'alert alert-danger';
    }
    const submitBtn = document.getElementById('exec-modal-submit');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '⚠️ Non-actionable — cannot execute'; }
    const modal = document.getElementById('execute-modal');
    if (modal) modal.classList.remove('hidden');
    document.getElementById('exec-run-id').value = runId;
    return;
  }

  let side = 'buy';
  if (order && (order.side === 'buy' || order.side === 'sell')) {
    side = order.side.toLowerCase();
  } else if (action === 'SELL' || rating === 'SELL' || rating === 'UNDERWEIGHT') {
    side = 'sell';
  } else {
    side = 'buy';
  }

  const ticker = run.ticker || (rec && rec.ticker) || (order && order.ticker) || '';

  // Limit price: order limit, or rec entry_price, or 0
  const limitPrice = (order && order.limit_price) ? Number(order.limit_price) : (rec && rec.entry_price ? Number(rec.entry_price) : 0);

  // Shares: order qty, or calculate from equity & 5% default sizing
  let qty = (order && order.qty) ? Number(order.qty) : 0;
  if (!qty || qty <= 0) {
    const equity = (state.account && state.account.equity) ? Number(state.account.equity) : 100000;
    const refP = limitPrice > 0 ? limitPrice : 100;
    qty = Math.max(1, Math.floor((equity * 0.05) / refP));
  }

  const stopLoss = (order && order.stop_price) ? Number(order.stop_price) : (rec && rec.stop_loss ? Number(rec.stop_loss) : '');
  const target = (order && order.take_profit_price) ? Number(order.take_profit_price) : (rec && rec.price_target ? Number(rec.price_target) : '');

  // Header & badge elements
  const titleText = isSubmitted
    ? `Re-execute / Additional Order — ${ticker}`
    : `Confirm & Execute Order — ${ticker}`;
  document.getElementById('exec-modal-title').textContent = titleText;
  const badgeEl = document.getElementById('exec-modal-badge');
  badgeEl.textContent = side.toUpperCase();
  badgeEl.className = side === 'buy' ? 'badge badge-buy' : 'badge badge-sell';

  // Modal alert
  const alertEl = document.getElementById('exec-modal-alert');
  if (alertEl) {
    if (isSubmitted) {
      alertEl.innerHTML = `⚠️ <strong>Note:</strong> An order was already submitted for this run (Alpaca ID: ${order.alpaca_order_id || 'Submitted'}). Submitting will place an additional bracket limit order to Alpaca.`;
    } else {
      alertEl.innerHTML = `Submit this recommendation as an order to Alpaca paper/live trading. You can review and adjust bracket order parameters below.`;
    }
  }

  // Populate inputs
  document.getElementById('exec-run-id').value = runId;
  document.getElementById('exec-rec-id').value = (rec && rec.id) || '';
  const sideEl = document.getElementById('exec-side');
  if (sideEl) {
    sideEl.value = side;
    sideEl.onchange = () => {
      const curSide = sideEl.value;
      badgeEl.textContent = curSide.toUpperCase();
      badgeEl.className = curSide === 'buy' ? 'badge badge-buy' : 'badge badge-sell';
      const sideMetricEl = document.getElementById('exec-modal-side-metric');
      if (sideMetricEl) {
        sideMetricEl.textContent = curSide.toUpperCase();
        sideMetricEl.style.color = curSide === 'buy' ? '#10B981' : '#EF4444';
      }
    };
  }
  document.getElementById('exec-qty').value = qty;
  document.getElementById('exec-limit').value = limitPrice > 0 ? limitPrice.toFixed(2) : '';
  document.getElementById('exec-stop').value = stopLoss ? Number(stopLoss).toFixed(2) : '';
  document.getElementById('exec-target').value = target ? Number(target).toFixed(2) : '';

  // Metrics summary
  const estTotal = qty * (limitPrice > 0 ? limitPrice : 0);
  const estTotalFormatted = estTotal > 0 ? formatCurrency(estTotal) : 'N/A';
  const entryFormatted = limitPrice > 0 ? formatCurrency(limitPrice) : 'MKT';
  const stopFormatted = stopLoss ? formatCurrency(stopLoss) : 'None';
  const targetFormatted = target ? formatCurrency(target) : 'None';

  const paramsEl = document.getElementById('exec-modal-params');
  paramsEl.innerHTML = `
    <div class="metric-box"><div class="metric-label">Ticker</div><div class="metric-val">${ticker}</div></div>
    <div class="metric-box"><div class="metric-label">Side</div><div class="metric-val" id="exec-modal-side-metric" style="color: ${side === 'buy' ? '#10B981' : '#EF4444'}">${side.toUpperCase()}</div></div>
    <div class="metric-box"><div class="metric-label">Rating</div><div class="metric-val">${(rec && rec.rating) || 'N/A'}</div></div>
    <div class="metric-box"><div class="metric-label">Entry Limit</div><div class="metric-val">${entryFormatted}</div></div>
    <div class="metric-box"><div class="metric-label">Stop Loss</div><div class="metric-val">${stopFormatted}</div></div>
    <div class="metric-box"><div class="metric-label">Target</div><div class="metric-val">${targetFormatted}</div></div>
    <div class="metric-box"><div class="metric-label">Est. Value</div><div class="metric-val">${estTotalFormatted}</div></div>
  `;

  // Reset button state
  const submitBtn = document.getElementById('exec-modal-submit');
  const cancelBtn = document.getElementById('exec-modal-cancel');
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.innerHTML = isSubmitted ? '⚡ Submit Additional Order' : '⚡ Confirm &amp; Submit Order';
  }
  if (cancelBtn) cancelBtn.disabled = false;

  const modal = document.getElementById('execute-modal');
  if (modal) modal.classList.remove('hidden');
}

function closeExecuteModal() {
  const modal = document.getElementById('execute-modal');
  if (modal) modal.classList.add('hidden');
}

async function submitRecommendationOrder() {
  const runId = document.getElementById('exec-run-id').value;
  const side = document.getElementById('exec-side').value || 'buy';
  const qtyVal = parseInt(document.getElementById('exec-qty').value, 10);
  const limitVal = parseFloat(document.getElementById('exec-limit').value);
  const stopVal = parseFloat(document.getElementById('exec-stop').value);
  const targetVal = parseFloat(document.getElementById('exec-target').value);

  if (!runId) {
    showToast('Invalid run ID', 'error');
    return;
  }
  if (!qtyVal || qtyVal < 1) {
    showToast('Quantity must be at least 1 share', 'error');
    return;
  }
  if (!limitVal || limitVal <= 0) {
    showToast('Limit price must be greater than $0', 'error');
    return;
  }

  const run = state.runs.find((r) => r.id === runId) || (state.activeModalRun && state.activeModalRun.id === runId ? state.activeModalRun : null);
  const isReexecute = Boolean(run && run.order && run.order.status === 'submitted');

  const payload = {
    qty: qtyVal,
    limit_price: limitVal,
    side: side,
    order_type: 'limit',
    reexecute: isReexecute,
  };
  if (!isNaN(stopVal) && stopVal > 0) {
    payload.stop_price = stopVal;
  }
  if (!isNaN(targetVal) && targetVal > 0) {
    payload.take_profit_price = targetVal;
  }

  const submitBtn = document.getElementById('exec-modal-submit');
  const cancelBtn = document.getElementById('exec-modal-cancel');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner" style="width: 12px; height: 12px; margin-right: 6px;"></span> Submitting...';
  }
  if (cancelBtn) cancelBtn.disabled = true;

  try {
    const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/execute-recommendation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.message || `Server returned ${res.status}`);
    }

    closeExecuteModal();
    showToast(
      `Order submitted to Alpaca! ID: ${data.alpaca_order_id || data.order_id || 'Accepted'} (${data.side.toUpperCase()} ${data.qty} ${data.symbol})`,
      'success'
    );

    // Refresh all views immediately
    await fetchRuns();
    renderRuns();
    if (state.activeModalRunId === runId) {
      await updateModalData();
    }
  } catch (err) {
    console.error('Order submission error:', err);
    showToast(`Order submission failed: ${err.message}`, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '⚡ Confirm &amp; Submit Order';
    }
    if (cancelBtn) cancelBtn.disabled = false;
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

  document.getElementById('btn-clear-api-key').addEventListener('click', clearLlmApiKey);

  // LLM test connection button
  const btnTestLlm = document.getElementById('btn-test-llm');
  if (btnTestLlm) btnTestLlm.addEventListener('click', testLlmConnection);

  // Run all active
  const btnRunAll = document.getElementById('btn-run-all');
  if (btnRunAll) btnRunAll.addEventListener('click', triggerRunAllWatchlist);

  // Batch delete selected watchlist
  const btnDelSelected = document.getElementById('btn-delete-selected') || document.getElementById('btn-watchlist-batch-delete');
  if (btnDelSelected) btnDelSelected.addEventListener('click', deleteSelectedWatchlist);

  // Watchlist select all
  const cbSelectAll = document.getElementById('watchlist-select-all');
  if (cbSelectAll) cbSelectAll.addEventListener('change', (e) => toggleSelectAllWatchlist(e.target.checked));

  // Initialize symbol autocomplete
  initSymbolAutocomplete();

  // Add symbol form
  document.getElementById('add-watchlist-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const symInput = document.getElementById('input-symbol');
    const notesInput = document.getElementById('input-notes');
    let symbol = symInput.value.trim().toUpperCase();
    let notes = notesInput.value.trim();

    // If an item was actively highlighted in autocomplete, use it
    if (autocompleteState.suggestions && autocompleteState.suggestions.length > 0) {
      const typed = symInput.value.trim().toLowerCase();
      if (autocompleteState.activeIndex >= 0 && autocompleteState.suggestions[autocompleteState.activeIndex]) {
        const item = autocompleteState.suggestions[autocompleteState.activeIndex];
        symbol = item.symbol;
        if (!notes && item.name) notes = item.name;
      } else {
        // If user typed a company name (e.g. "Apple" or "Microsoft"), resolve to matching symbol
        const exactNameMatch = autocompleteState.suggestions.find(
          (s) => s.name.toLowerCase() === typed || s.name.toLowerCase().startsWith(typed)
        );
        if (exactNameMatch && symbol !== exactNameMatch.symbol) {
          symbol = exactNameMatch.symbol;
          if (!notes && exactNameMatch.name) notes = exactNameMatch.name;
        }
      }
    }

    closeAutocompleteDropdown();
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

  // Logs modal tabs
  document.querySelectorAll('.modal-tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab');
      if (tab) switchModalTab(tab);
    });
  });

  // LLM Calls Search & Filter
  const searchInput = document.getElementById('llm-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      state.llmSearchQuery = e.target.value;
      filterAndRenderLLMCalls();
    });
  }

  document.querySelectorAll('.filter-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.filter-pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      state.llmFilterKind = pill.getAttribute('data-kind') || 'ALL';
      filterAndRenderLLMCalls();
    });
  });

  // Execute modal close events
  const execModalClose = document.getElementById('exec-modal-close');
  if (execModalClose) execModalClose.addEventListener('click', closeExecuteModal);
  const execModalCancel = document.getElementById('exec-modal-cancel');
  if (execModalCancel) execModalCancel.addEventListener('click', closeExecuteModal);
  const execModal = document.getElementById('execute-modal');
  if (execModal) {
    execModal.addEventListener('click', (e) => {
      if (e.target.id === 'execute-modal') closeExecuteModal();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!document.getElementById('execute-modal')?.classList.contains('hidden')) {
        closeExecuteModal();
      } else if (!document.getElementById('logs-modal')?.classList.contains('hidden')) {
        closeLogsModal();
      }
    }
  });

  // Logs modal close
  document.getElementById('modal-close').addEventListener('click', closeLogsModal);
  document.getElementById('modal-btn-close').addEventListener('click', closeLogsModal);
  const logsModal = document.getElementById('logs-modal');
  if (logsModal) {
    logsModal.addEventListener('click', (e) => {
      if (e.target.id === 'logs-modal') closeLogsModal();
    });
  }

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
  fetchInFlightDetail();

  // Live in-flight console polling every 3.5 seconds
  setInterval(() => {
    fetchInFlightDetail();
  }, 3500);

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

// Global aliases and window bindings for inline HTML onclick handlers & cross-compatibility
window.openRunModal = openLogsModal;
window.openLogsModal = openLogsModal;
window.closeLogsModal = closeLogsModal;
window.openExecuteModal = openExecuteModal;
window.confirmAndExecute = openExecuteModal;
window.confirmAndExecuteOrder = openExecuteModal;
window.closeExecuteModal = closeExecuteModal;
window.submitRecommendationOrder = submitRecommendationOrder;
window.switchModalTab = switchModalTab;
window.updateModalData = updateModalData;
window.renderSummary = renderModalSummary;
window.renderModalSummary = renderModalSummary;
window.renderLLMCalls = filterAndRenderLLMCalls;
window.filterAndRenderLLMCalls = filterAndRenderLLMCalls;
window.triggerSingleRun = triggerSingleRun;
window.triggerRunAllWatchlist = triggerRunAllWatchlist;
window.removeWatchlistSymbol = removeWatchlistSymbol;
window.cancelLiveOrder = cancelLiveOrder;
window.toggleWatchlistEnabled = toggleWatchlistEnabled;
window.toggleAutoTrade = toggleAutoTrade;
window.refreshAll = refreshAll;
window.setInFlightConsoleFilter = setInFlightConsoleFilter;
window.renderInFlightLiveConsole = renderInFlightLiveConsole;
window.getTickerColorStyle = getTickerColorStyle;
window.toggleWatchlistSelectAll = toggleSelectAllWatchlist;
window.toggleSelectAllWatchlist = toggleSelectAllWatchlist;
window.batchDeleteWatchlist = deleteSelectedWatchlist;
window.deleteSelectedWatchlist = deleteSelectedWatchlist;
window.toggleWatchlistSelectItem = toggleWatchlistSelectItem;
window.onWatchlistSelectChange = onWatchlistSelectChange;
window.updateWatchlistSelectionUI = updateWatchlistSelectionUI;
window.initSymbolAutocomplete = initSymbolAutocomplete;
window.autocompleteState = autocompleteState;
window.fetchAutocompleteSuggestions = fetchAutocompleteSuggestions;
window.selectAutocompleteItem = selectAutocompleteItem;
window.closeAutocompleteDropdown = closeAutocompleteDropdown;
