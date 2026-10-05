const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('site/app.js', 'utf8').split('$("#search").addEventListener')[0];

function chart(range, points, dailyVolume, calendar) {
  const elements = new Map();
  const element = key => {
    if (!elements.has(key)) elements.set(key, { innerHTML: '', textContent: '', clientWidth: 900, setAttribute() {}, querySelectorAll: () => [] });
    return elements.get(key);
  };
  const context = vm.createContext({ document: { querySelector: element }, Intl, Date });
  vm.runInContext(source, context);
  context.input = {range, points, dailyVolume, calendar};
  vm.runInContext(`state.selectedRange = input.range; state.index = {volume_trading_dates: input.calendar};
    drawChart(input.points.map(p => ({date: new Date(p[0]), tradingDate: p[0].slice(0,10), value: p[1]})), null,
    {daily_volume: input.dailyVolume, volume_checked_through: '2026-09-30'});`, context);
  return {html: element('#trend-chart').innerHTML, detail: element('#volume-detail').textContent, range: element('#chart-range').textContent};
}

test('intraday premium does not duplicate daily volume', () => {
  const result = chart('7', [['2026-09-30T10:00:00+08:00', 2], ['2026-09-30T15:00:00+08:00', 3]], [['2026-09-30', 28273, 49056.27]], ['2026-09-30']);
  assert.equal((result.html.match(/class="volume-bar"/g) || []).length, 1);
  assert.equal((result.html.match(/class="chart-point"/g) || []).length, 2);
  assert.match(result.detail, /2.8273 万份/);
});

test('missing volume stays distinct from zero, single point still renders', () => {
  const result = chart('7', [['2026-09-30T15:00:00+08:00', 3]], [['2026-09-29', 0, 0]], ['2026-09-29', '2026-09-30']);
  assert.equal((result.html.match(/class="volume-bar"/g) || []).length, 1);
  assert.equal((result.html.match(/class="volume-missing"/g) || []).length, 1);
  assert.match(result.detail, /不代表成交量为零/);
  assert.match(result.html, /class="chart-point"/);
});

test('range uses exchange days including missing premium sessions', () => {
  const result = chart('1', [['2026-09-29T15:00:00+08:00', 3]], [['2026-09-30', 100, 200]], ['2026-09-29', '2026-09-30']);
  assert.match(result.range, /1 个交易日 · 0 个溢价快照/);
  assert.match(result.html, /当前范围暂无溢价快照/);
  assert.match(result.detail, /0.01 万份/);
});

test('seven-day range includes the exact last seven exchange sessions', () => {
  const days = Array.from({length: 10}, (_, i) => `2026-09-${21 + i}`);
  const result = chart('7', [], days.map(day => [day, 100, 200]), days);
  assert.equal((result.html.match(/class="volume-bar"/g) || []).length, 7);
  assert.match(result.range, /2026-09-24 至 2026-09-30/);
  assert.doesNotMatch(result.html, /NaN/);
});
