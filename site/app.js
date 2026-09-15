const state = {
  latest: null,
  index: null,
  sourceAccuracy: null,
  selectedCode: null,
  selectedRange: "60",
  trendCache: new Map(),
  chartRequest: 0,
};

const $ = (selector) => document.querySelector(selector);
const fmtPct = (value) => value == null ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const fmtNum = (value, digits = 4) => value == null ? "--" : Number(value).toFixed(digits);
const tone = (value) => value > 0 ? "up" : value < 0 ? "down" : "flat";
const categoryName = { stock: "股票", index: "指数", oversea: "海外", overseas: "海外", apac: "亚太", commodity: "商品", other: "其他" };

async function loadData() {
  const stamp = Date.now();
  const [latest, index, sourceAccuracy] = await Promise.all([
    fetch(`data/latest.json?v=${stamp}`).then(checkResponse).then(r => r.json()),
    fetch(`data/index.json?v=${stamp}`).then(checkResponse).then(r => r.json()),
    fetch(`data/source-accuracy.json?v=${stamp}`).then(checkResponse).then(r => r.json()),
  ]);
  state.latest = latest;
  state.index = index;
  state.sourceAccuracy = sourceAccuracy;
  state.selectedCode = latest.rows.find(row => row.premium_rate_pct != null)?.code || null;
  renderAll();
}

function checkResponse(response) {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response;
}

function renderAll() {
  renderSummary();
  renderFundOptions();
  renderTable();
  renderChart();
  renderSourceAccuracy();
}

function renderSummary() {
  const rows = state.latest.rows.filter(row => row.premium_rate_pct != null);
  const premiums = rows.map(row => row.premium_rate_pct).sort((a, b) => a - b);
  const positive = premiums.filter(value => value > 0).length;
  const midpoint = Math.floor(premiums.length / 2);
  const median = premiums.length % 2 ? premiums[midpoint] : (premiums[midpoint - 1] + premiums[midpoint]) / 2;
  const max = rows.reduce((best, row) => row.premium_rate_pct > best.premium_rate_pct ? row : best, rows[0]);

  $("#fund-count").textContent = state.latest.row_count;
  $("#positive-count").textContent = positive;
  $("#positive-ratio").textContent = `占有效数据 ${((positive / rows.length) * 100).toFixed(1)}%`;
  $("#max-premium").textContent = fmtPct(max.premium_rate_pct);
  $("#max-premium-name").textContent = `${max.code} ${max.name}`;
  $("#median-premium").textContent = fmtPct(median);
  const captured = new Date(state.latest.captured_at);
  $("#updated-at").textContent = `${captured.toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" })} ${state.latest.slot}`;
  $("#status-dot").classList.add("ok");
}

function renderFundOptions() {
  const select = $("#fund-select");
  const rows = state.latest.rows.filter(row => row.premium_rate_pct != null);
  select.innerHTML = rows.map(row => `<option value="${row.code}">${row.code} ${escapeHtml(row.name)}</option>`).join("");
  select.value = state.selectedCode;
}

function filteredRows() {
  const query = $("#search").value.trim().toLowerCase();
  const category = $("#category-filter").value;
  const positiveOnly = $("#positive-only").checked;
  return state.latest.rows.filter(row => {
    const matchesQuery = !query || row.code.includes(query) || row.name.toLowerCase().includes(query);
    const rowCategory = categoryName[row.category] ? row.category : "other";
    return matchesQuery && (category === "all" || rowCategory === category) && (!positiveOnly || row.premium_rate_pct > 0);
  });
}

function renderTable() {
  const rows = filteredRows();
  $("#ranking-body").innerHTML = rows.map((row, index) => {
    const source = state.sourceAccuracy?.funds?.[row.code];
    const sourceDetail = source?.sources?.[source.best_source];
    return `
    <tr data-code="${row.code}" class="${row.code === state.selectedCode ? "selected" : ""}">
      <td class="rank">${index + 1}</td>
      <td class="fund-name"><strong>${escapeHtml(row.name)}</strong><small>${row.code} · ${categoryName[row.category] || "其他"}</small></td>
      <td class="purchase">${escapeHtml(row.purchase_info || "开放申购")}</td>
      <td class="num">${fmtNum(row.off_market_value)}<span class="change ${tone(row.off_market_change_pct)}">${fmtPct(row.off_market_change_pct)}</span></td>
      <td class="num">${fmtNum(row.on_market_price, 3)}<span class="change ${tone(row.on_market_change_pct)}">${fmtPct(row.on_market_change_pct)}</span></td>
      <td class="num source-cell">${source ? `数据源${source.best_source}<span class="change">昨偏差 ${fmtPct(sourceDetail?.yesterday_deviation_pct)}</span>` : "--"}</td>
      <td class="num"><span class="premium ${tone(row.premium_rate_pct)}">${fmtPct(row.premium_rate_pct)}</span></td>
    </tr>`;
  }).join("");
  $("#result-count").textContent = `显示 ${rows.length} / ${state.latest.rows.length} 只基金`;
}

function renderSourceAccuracy() {
  const item = state.sourceAccuracy?.funds?.[state.selectedCode];
  if (!item) {
    $("#best-source").textContent = "暂无数据";
    $("#source-note").textContent = "该基金暂无昨日数据源准确度记录。";
    $("#source-body").innerHTML = "";
    return;
  }
  $("#best-source").textContent = `数据源 ${item.best_source}`;
  const sourceDates = state.sourceAccuracy.source_dates || {};
  const mainSourceDate = Object.entries(sourceDates).sort((a, b) => b[1] - a[1])[0]?.[0];
  const stale = mainSourceDate && item.source_date !== mainSourceDate
    ? ` 该基金当前使用的是最近可用评估日 ${item.source_date || "--"}，晚于其他基金更新。`
    : "";
  $("#source-note").textContent = `依据 ${item.source_date || "--"} 正式净值校验，系统在 ${state.sourceAccuracy.display_date || "今日"} 自动采用偏差绝对值最小的数据源 ${item.best_source}。${stale}`;
  $("#source-body").innerHTML = ["1", "2", "3"].map(source => {
    const detail = item.sources[source] || {};
    const selected = source === item.best_source;
    return `<tr class="${selected ? "best" : ""}">
      <td><strong>数据源 ${source}</strong>${selected ? '<span class="best-mark">昨日最准</span>' : ""}</td>
      <td class="num ${tone(detail.simulated_change_pct)}">${fmtPct(detail.simulated_change_pct)}</td>
      <td class="num">${fmtPct(detail.yesterday_deviation_pct)}</td>
      <td class="num">${fmtPct(detail.month_avg_deviation_pct)}</td>
    </tr>`;
  }).join("");
}

function renderChart() {
  const code = state.selectedCode;
  const current = state.latest.rows.find(row => row.code === code);
  $("#chart-title").textContent = current ? `${current.code} ${current.name}` : "选择基金查看走势";
  $("#chart-latest").textContent = current ? `最新溢价率 ${fmtPct(current.premium_rate_pct)}` : "--";
  const request = ++state.chartRequest;
  const svg = $("#trend-chart");
  const empty = $("#chart-empty");
  svg.innerHTML = "";
  empty.hidden = false;
  empty.textContent = "正在加载历史走势…";
  if (!code) return;

  loadTrend(code).then(fund => {
    if (request !== state.chartRequest) return;
    const allPoints = (fund?.points || [])
      .filter(point => point[1] != null)
      .map(point => ({ date: new Date(point[0]), value: point[1], tradingDate: shanghaiDate(point[0]) }))
      .filter(point => !Number.isNaN(point.date.getTime()));
    drawChart(filterPointsByRange(allPoints), current, fund);
  }).catch(error => {
    if (request !== state.chartRequest) return;
    $("#chart-range").textContent = "历史数据加载失败";
    empty.hidden = false;
    empty.textContent = `无法读取该基金走势：${error.message}`;
  });
}

async function loadTrend(code) {
  if (!state.trendCache.has(code)) {
    const promise = fetch(`data/trends/${code}.json?v=${encodeURIComponent(state.index.generated_at || "")}`)
      .then(checkResponse)
      .then(response => response.json())
      .catch(error => {
        state.trendCache.delete(code);
        throw error;
      });
    state.trendCache.set(code, promise);
  }
  return state.trendCache.get(code);
}

function shanghaiDate(value) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(value));
  const byType = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function filterPointsByRange(points) {
  if (state.selectedRange === "all") return points;
  const dayCount = Number(state.selectedRange);
  const availableDates = [...new Set(points.map(point => point.tradingDate))];
  const includedDates = new Set(availableDates.slice(-dayCount));
  return points.filter(point => includedDates.has(point.tradingDate));
}

function drawChart(points, current, fund) {
  const distinctDays = new Set(points.map(point => point.tradingDate)).size;
  const rangeLabel = state.selectedRange === "all" ? "全部历史" : state.selectedRange === "1" ? "当日" : `最近 ${state.selectedRange} 个交易日`;
  const dateSpan = points.length ? `${points[0].tradingDate} 至 ${points.at(-1).tradingDate}` : "";
  $("#chart-range").textContent = points.length
    ? `${rangeLabel} · ${distinctDays} 个交易日 · ${points.length} 个快照 · ${dateSpan}`
    : `${rangeLabel} · 暂无历史快照`;
  const intradayDays = fund?.intraday_retention_days || state.index?.intraday_retention_days || 60;
  $("#history-note").textContent = `最近 ${intradayDays} 个交易日保留每 30 分钟快照；更早历史按每日最后一个快照压缩保存。`;

  const svg = $("#trend-chart");
  const empty = $("#chart-empty");
  if (points.length < 2) {
    svg.innerHTML = "";
    empty.hidden = false;
    empty.textContent = points.length ? "当前范围只有一个快照，暂时无法形成走势线。" : "当前范围暂无历史快照。";
    return;
  }
  empty.hidden = true;
  const width = Math.max(620, $("#chart-wrap").clientWidth);
  const height = 270;
  const margin = { top: 20, right: 22, bottom: 34, left: 52 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  let min = Math.min(0, ...points.map(point => point.value));
  let max = Math.max(0, ...points.map(point => point.value));
  const pad = Math.max((max - min) * .14, .5);
  min -= pad; max += pad;
  const x = index => margin.left + (points.length === 1 ? innerW / 2 : index * innerW / (points.length - 1));
  const y = value => margin.top + (max - value) * innerH / (max - min);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.length - 1)},${height - margin.bottom} L${x(0)},${height - margin.bottom} Z`;
  const ticks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
  const labelIndices = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `
    <defs><linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffa800" stop-opacity=".24"/><stop offset="1" stop-color="#ffa800" stop-opacity="0"/></linearGradient></defs>
    ${ticks.map(value => `<line class="chart-grid ${Math.abs(value) < .001 ? "chart-zero" : ""}" x1="${margin.left}" x2="${width - margin.right}" y1="${y(value)}" y2="${y(value)}"/><text class="chart-label" x="${margin.left - 9}" y="${y(value) + 4}" text-anchor="end">${value.toFixed(1)}%</text>`).join("")}
    <path class="chart-area" d="${area}"/><path class="chart-line" d="${path}"/>
    ${points.length <= 120 ? points.map((point, index) => `<circle class="chart-point" cx="${x(index)}" cy="${y(point.value)}" r="4"><title>${point.date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} ${fmtPct(point.value)}</title></circle>`).join("") : ""}
    ${labelIndices.map(index => `<text class="chart-label" x="${x(index)}" y="${height - 9}" text-anchor="${index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}">${points[index].date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai", hour12: false })}</text>`).join("")}
    <text class="chart-value" x="${x(points.length - 1) - 7}" y="${Math.max(14, y(points.at(-1).value) - 10)}" text-anchor="end">${fmtPct(points.at(-1).value)}</text>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

$("#search").addEventListener("input", renderTable);
$("#category-filter").addEventListener("change", renderTable);
$("#positive-only").addEventListener("change", renderTable);
$("#fund-select").addEventListener("change", event => { state.selectedCode = event.target.value; renderTable(); renderChart(); renderSourceAccuracy(); });
document.querySelectorAll("[data-range]").forEach(button => button.addEventListener("click", () => {
  state.selectedRange = button.dataset.range;
  document.querySelectorAll("[data-range]").forEach(item => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  renderChart();
}));
$("#ranking-body").addEventListener("click", event => {
  const row = event.target.closest("tr[data-code]");
  if (!row) return;
  state.selectedCode = row.dataset.code;
  $("#fund-select").value = state.selectedCode;
  renderTable(); renderChart(); renderSourceAccuracy();
  $(".trend-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});
window.addEventListener("resize", () => state.latest && renderChart());

loadData().catch(error => {
  $("#updated-at").textContent = "数据加载失败";
  $("#chart-empty").hidden = false;
  $("#chart-empty").textContent = `无法读取看板数据：${error.message}`;
  console.error(error);
});
