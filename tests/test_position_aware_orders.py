"""Position-aware order sizing and decision display regressions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from webapp.execution import build_order


@pytest.mark.parametrize('held_qty', [None, 0, -5])
@pytest.mark.parametrize('decision', [
    {'action': 'Sell'}, {'action': 'Hold', 'rating': 'Underweight'},
    {'rating': 'Sell'},
])
def test_flat_sell_has_no_order(monkeypatch, held_qty, decision):
    def unexpected_lookup(*args):
        pytest.fail('Flat sells should be skipped before fetching a price')
    monkeypatch.setattr('webapp.execution.fetch_last_close_price', unexpected_lookup)
    assert build_order('GOOG', decision, 100000, held_qty=held_qty) is None


@pytest.mark.parametrize('held_qty, expected', [(10, 10), (100, 50), (0.5, 0.5)])
def test_sell_limit_clamps_to_holdings(held_qty, expected):
    order = build_order('GOOG', {'action': 'Sell', 'entry_price': 100},
                        100000, current_price=100, held_qty=held_qty)
    assert order['qty'] == expected
    assert order['side'] == 'sell'


@pytest.mark.parametrize('entry, current', [(None, 100), (200, 100), (100, None)])
def test_market_sell_clamps_to_holdings(monkeypatch, entry, current):
    monkeypatch.setattr('webapp.execution.fetch_last_close_price', lambda _: None)
    order = build_order('GOOG', {'action': 'Sell', 'entry_price': entry},
                        100000, current_price=current, held_qty=10)
    assert order['otype'] == 'market'
    assert order['qty'] == 10
    assert order['notional'] is None


def test_flat_buy_is_unchanged():
    order = build_order('GOOG', {'action': 'Buy', 'entry_price': 100},
                        100000, current_price=100, held_qty=0)
    assert order['side'] == 'buy'
    assert order['qty'] == 50


def test_avoid_entry_label_in_both_languages():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    source = (Path(__file__).parents[1] / 'webapp/static/app.js').read_text()
    helpers = source[source.index('function isAvoidEntry('):source.index('function formatCurrency(')]
    script = 'const state = {settings: {lang: "en"}};\n' + helpers + '''
const results = [0, null, undefined, 10].map(qty => isAvoidEntry({action: 'Sell', current_position_qty: qty}));
results.push(isAvoidEntry({action: 'Buy', current_position_qty: 0}));
results.push(avoidEntryLabel());
state.settings.lang = 'zh';
results.push(avoidEntryLabel());
console.log(JSON.stringify(results));
'''
    output = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    assert json.loads(output.stdout) == [True, True, True, False, False,
                                        'Avoid Entry (no position)', '避免入场（无持仓）']
    assert 'isAvoidEntry(run.recommendation) ? avoidEntryLabel()' in source
    assert 'else if (isAvoidEntry(rec))' in source
