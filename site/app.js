const state = {
  latest: null,
  index: null,
  sourceAccuracy: null,
  selectedCode: null,
  selectedRange: "7",
  sortKey: "premium_rate_pct",
  sortDirection: "desc",
  trendCache: new Map(),
  chartRequest: 0,
};

const $ = (selector) => document.querySelector(selector);
const fmtPct = (value) => value == null ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const fmtNum = (value, digits = 4) => value == null ? "--" : Number(value).toFixed(digits);
const tone = (value) => value > 0 ? "up" : value < 0 ? "down" : "flat";
const categoryName = { stock: "股票", index: "指数", oversea: "海外", overseas: "海外", apac: "亚太", commodity: "商品", other: "其他" };
const sortName = {
  market_rank: "原始排名", fund: "基金", purchase: "申购状态", off_market_value: "场外估值",
  on_market_price: "场内价", source: "今日数据源", premium_rate_pct: "实时溢价率",
};

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
  const rows = state.latest.rows;
  select.innerHTML = rows.map(row => `<option value="${row.code}">${row.code} ${escapeHtml(row.name)}</option>`).join("");
  select.value = state.selectedCode;
}

function filteredRows() {
  const query = $("#search").value.trim().toLowerCase();
  const category = $("#category-filter").value;
  const positiveOnly = $("#positive-only").checked;
  const purchasableOnly = $("#purchasable-only").checked;
  const rows = state.latest.rows.filter(row => {
    const matchesQuery = !query || row.code.includes(query) || row.name.toLowerCase().includes(query);
    const rowCategory = categoryName[row.category] ? row.category : "other";
    return matchesQuery
      && (category === "all" || rowCategory === category)
      && (!positiveOnly || row.premium_rate_pct > 0)
      && (!purchasableOnly || purchaseStatus(row).kind === "available");
  });
  return sortRows(rows);
}

function purchaseStatus(row) {
  const label = String(row.purchase_info || "").trim();
  if (!label || label === "开放申购") return { kind: "available", label: "开放申购", order: 0 };
  if (/限/.test(label)) return { kind: "limited", label, order: 1 };
  if (/暂停|不可|关闭|不开放/.test(label)) return { kind: "paused", label, order: 2 };
  return { kind: "other", label, order: 3 };
}

function sortValue(row, key) {
  const source = state.sourceAccuracy?.funds?.[row.code];
  if (key === "market_rank") return state.latest.rows.indexOf(row);
  if (key === "fund") return `${row.name}\u0000${row.code}`;
  if (key === "purchase") return `${purchaseStatus(row).order}\u0000${purchaseStatus(row).label}`;
  if (key === "source") return source?.best_source == null ? null : Number(source.best_source);
  return row[key];
}

function compareValues(left, right) {
  const leftMissing = left == null || Number.isNaN(left);
  const rightMissing = right == null || Number.isNaN(right);
  if (leftMissing || rightMissing) return leftMissing === rightMissing ? 0 : leftMissing ? 1 : -1;
  if (typeof left === "string" || typeof right === "string") {
    return String(left).localeCompare(String(right), "zh-CN", { numeric: true, sensitivity: "base" });
  }
  return left - right;
}

function sortRows(rows) {
  const direction = state.sortDirection === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, state.sortKey);
    const right = sortValue(b, state.sortKey);
    const leftMissing = left == null || Number.isNaN(left);
    const rightMissing = right == null || Number.isNaN(right);
    if (leftMissing || rightMissing) return leftMissing === rightMissing ? a.code.localeCompare(b.code) : leftMissing ? 1 : -1;
    const compared = compareValues(left, right);
    return compared * direction || a.code.localeCompare(b.code);
  });
}

function renderSortHeaders() {
  document.querySelectorAll("th.sortable").forEach(header => {
    const button = header.querySelector("[data-sort]");
    const active = button.dataset.sort === state.sortKey;
    const mark = button.querySelector(".sort-mark");
    header.setAttribute("aria-sort", active ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none");
    mark.textContent = active ? (state.sortDirection === "asc" ? "↑" : "↓") : "";
    button.title = active
      ? `当前${state.sortDirection === "asc" ? "升序" : "降序"}，点击切换`
      : `按${sortName[button.dataset.sort]}排序`;
  });
}

function renderTable() {
  const rows = filteredRows();
  renderSortHeaders();
  $("#ranking-body").innerHTML = rows.map((row, index) => {
    const source = state.sourceAccuracy?.funds?.[row.code];
    const sourceDetail = source?.sources?.[source.best_source];
    const purchase = purchaseStatus(row);
    const marketRank = state.latest.rows.indexOf(row) + 1;
    return `
    <tr data-code="${row.code}" class="${row.code === state.selectedCode ? "selected" : ""} purchase-${purchase.kind}">
      <td class="rank">${marketRank}</td>
      <td class="fund-name"><strong>${escapeHtml(row.name)}</strong><small>${row.code} · ${categoryName[row.category] || "其他"}</small></td>
      <td class="purchase"><span class="purchase-badge ${purchase.kind}">${escapeHtml(purchase.label)}</span></td>
      <td class="num">${fmtNum(row.off_market_value)}<span class="change ${tone(row.off_market_change_pct)}">${fmtPct(row.off_market_change_pct)}</span></td>
      <td class="num">${fmtNum(row.on_market_price, 3)}<span class="change ${tone(row.on_market_change_pct)}">${fmtPct(row.on_market_change_pct)}</span></td>
      <td class="num source-cell">${source ? `数据源${source.best_source}<span class="change">昨偏差 ${fmtPct(sourceDetail?.yesterday_deviation_pct)}</span>` : "--"}</td>
      <td class="num"><span class="premium ${tone(row.premium_rate_pct)}">${fmtPct(row.premium_rate_pct)}</span></td>
    </tr>`;
  }).join("");
  $("#result-count").textContent = `显示 ${rows.length} / ${state.latest.rows.length} 只基金 · ${sortName[state.sortKey]}${state.sortDirection === "asc" ? "升序" : "降序"}`;
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
  $("#volume-detail").textContent = "日成交量加载中…";
  $("#volume-note").textContent = "";
  $("#volume-body").innerHTML = "";
  if (!code) return;

  loadTrend(code).then(fund => {
    if (request !== state.chartRequest) return;
    const allPoints = (fund?.points || [])
      .filter(point => point[1] != null)
      .map(point => ({ date: new Date(point[0]), value: point[1], tradingDate: shanghaiDate(point[0]) }))
      .filter(point => !Number.isNaN(point.date.getTime()));
    drawChart(allPoints, current, fund);
  }).catch(error => {
    if (request !== state.chartRequest) return;
    $("#chart-range").textContent = "历史数据加载失败";
    $("#volume-detail").textContent = "日成交量加载失败，请重试。";
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

function chartDates(points, fund) {
  // Calendar includes sessions without premium snapshots or symbol volume bars.
  const dates = [...new Set([
    ...(state.index.volume_trading_dates || []),
    ...points.map(point => point.tradingDate),
    ...(fund?.daily_volume || []).map(row => row[0]),
  ])].sort();
  return state.selectedRange === "all" ? dates : dates.slice(-Number(state.selectedRange));
}

function inWan(value) {
  return (value / 1e4).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function compactUnits(value) {
  return (value / 1e4).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function volumeDescription(day, row) {
  return row
    ? `${day} · 成交量 ${inWan(row[1])} 万份 · 成交额 ${inWan(row[2])} 万元`
    : `${day} · 未返回日成交记录（不代表成交量为零）`;
}

function drawChart(allPoints, current, fund) {
  const days = chartDates(allPoints, fund);
  const included = new Set(days);
  const points = allPoints.filter(point => included.has(point.tradingDate));
  const volumes = new Map((fund?.daily_volume || []).map(row => [row[0], row]));
  const rows = days.map(day => volumes.get(day)).filter(Boolean);
  const rangeLabel = state.selectedRange === "all" ? "全部历史" : state.selectedRange === "1" ? "当日" : `最近 ${state.selectedRange} 个交易日`;
  $("#chart-range").textContent = days.length
    ? `${rangeLabel} · ${days.length} 个交易日 · ${points.length} 个溢价快照 · ${days[0]} 至 ${days.at(-1)}`
    : `${rangeLabel} · 暂无历史记录`;
  const intradayDays = fund?.intraday_retention_days || state.index?.intraday_retention_days || 60;
  $("#history-note").textContent = `溢价率：最近 ${intradayDays} 个交易日保留每 30 分钟快照；更早历史按每日最后一个快照压缩保存。`;
  const checked = fund?.volume_checked_through;
  $("#volume-note").textContent = checked
    ? `成交量来源：通达信 · 已查询至 ${checked} · 仅收盘后更新（北京时间 16:10，18:10 补跑）。当前范围 ${rows.length}/${days.length} 天有记录；缺失以虚线标记，不填零。`
    : "该基金暂无日成交量数据；成交量仅在收盘后更新。";
  const latestDay = days.at(-1);
  $("#volume-detail").textContent = latestDay ? volumeDescription(latestDay, volumes.get(latestDay)) : "暂无日成交量数据";
  $("#volume-body").innerHTML = [...days].reverse().map(day => {
    const row = volumes.get(day);
    return `<tr><td>${day}</td><td class="num">${row ? inWan(row[1]) : "未返回记录"}</td><td class="num">${row ? inWan(row[2]) : "—"}</td></tr>`;
  }).join("");

  const svg = $("#trend-chart");
  const empty = $("#chart-empty");
  if (!days.length) {
    svg.innerHTML = "";
    empty.hidden = false;
    empty.textContent = "当前范围暂无历史数据。";
    return;
  }
  empty.hidden = true;
  const width = Math.max(560, $("#chart-wrap").clientWidth);
  const height = 400;
  const margin = { top: 25, right: 24, left: 66 };
  const premiumBottom = 228, volumeTop = 282, volumeBottom = 366;
  const innerW = width - margin.left - margin.right;
  const dayWidth = innerW / days.length;
  const dayIndices = new Map(days.map((day, index) => [day, index]));
  const center = day => margin.left + (dayIndices.get(day) + .5) * dayWidth;
  // Within each equal-width trading session, position only actual snapshots.
  const groups = new Map(days.map(day => [day, points.filter(point => point.tradingDate === day)]));
  const x = point => {
    const group = groups.get(point.tradingDate);
    return center(point.tradingDate) + (group.length > 1 ? (group.indexOf(point) / (group.length - 1) - .5) * dayWidth * .75 : 0);
  };
  let min = Math.min(0, ...points.map(point => point.value));
  let max = Math.max(0, ...points.map(point => point.value));
  const pad = Math.max((max - min) * .14, .5);
  min -= pad; max += pad;
  const y = value => margin.top + (max - value) * (premiumBottom - margin.top) / (max - min);
  const ticks = Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4);
  const maxVolume = Math.max(1, ...rows.map(row => row[1]));
  const vy = value => volumeBottom - value / maxVolume * (volumeBottom - volumeTop);
  const barWidth = Math.max(1, Math.min(42, dayWidth * .55));
  const labelIndices = [...new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])];
  const path = points.map((point, index) => {
    const gap = index && dayIndices.get(point.tradingDate) - dayIndices.get(points[index - 1].tradingDate) > 1;
    return `${!index || gap ? "M" : "L"}${x(point).toFixed(1)},${y(point.value).toFixed(1)}`;
  }).join(" ");
  const lastPoint = points.at(-1);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `
    <text class="chart-label" x="${margin.left}" y="14">溢价率（%）</text>
    ${ticks.map(value => `<line class="chart-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${y(value)}" y2="${y(value)}"/><text class="chart-label" x="${margin.left - 9}" y="${y(value) + 4}" text-anchor="end">${value.toFixed(1)}%</text>`).join("")}
    ${path ? `<path class="chart-line" d="${path}"/>` : `<text class="chart-label" x="${width / 2}" y="120" text-anchor="middle">当前范围暂无溢价快照</text>`}
    ${points.length <= 120 ? points.map(point => `<circle class="chart-point" cx="${x(point)}" cy="${y(point.value)}" r="3.5"><title>${point.date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} ${fmtPct(point.value)}</title></circle>`).join("") : ""}
    ${lastPoint ? `<text class="chart-value" x="${x(lastPoint) - 7}" y="${Math.max(30, y(lastPoint.value) - 10)}" text-anchor="end">${fmtPct(lastPoint.value)}</text>` : ""}
    <text class="chart-label" x="${margin.left}" y="266">日成交量（万份）</text>
    ${[0, maxVolume / 2, maxVolume].map(value => `<line class="chart-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${vy(value)}" y2="${vy(value)}"/><text class="chart-label" x="${margin.left - 9}" y="${vy(value) + 4}" text-anchor="end">${compactUnits(value)}</text>`).join("")}
    ${days.map(day => {
      const row = volumes.get(day);
      return `<g class="volume-day" data-day="${day}" tabindex="0" aria-label="${escapeHtml(volumeDescription(day, row))}"><title>${escapeHtml(volumeDescription(day, row))}</title>
      ${row ? `<rect class="volume-bar" x="${center(day) - barWidth / 2}" y="${vy(row[1])}" width="${barWidth}" height="${volumeBottom - vy(row[1])}" rx="2"/>` : `<line class="volume-missing" x1="${center(day) - barWidth / 2}" x2="${center(day) + barWidth / 2}" y1="${volumeBottom - 2}" y2="${volumeBottom - 2}"/>`}
      <rect class="volume-hit" x="${center(day) - dayWidth / 2}" y="${volumeTop}" width="${dayWidth}" height="${volumeBottom - volumeTop + 3}"/></g>`;
    }).join("")}
    ${labelIndices.map(index => `<text class="chart-label" x="${center(days[index])}" y="393" text-anchor="${index === 0 ? "start" : index === days.length - 1 ? "end" : "middle"}">${days[index].slice(5).replace("-", "/")}</text>`).join("")}`;
  svg.querySelectorAll(".volume-day").forEach(group => {
    const show = () => { $("#volume-detail").textContent = volumeDescription(group.dataset.day, volumes.get(group.dataset.day)); };
    group.addEventListener("pointerenter", show);
    group.addEventListener("click", show);
    group.addEventListener("focus", show);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

$("#search").addEventListener("input", renderTable);
$("#category-filter").addEventListener("change", renderTable);
$("#positive-only").addEventListener("change", renderTable);
$("#purchasable-only").addEventListener("change", renderTable);
document.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => {
  const key = button.dataset.sort;
  if (state.sortKey === key) {
    state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
  } else {
    state.sortKey = key;
    state.sortDirection = "asc";
  }
  renderTable();
}));
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
