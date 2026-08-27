"use strict";
/* 経済ボード。JSONを読んで盤面とグラフを出す。
   計算の流儀（bp / % / pt の使い分け、判定のしきい値）は collector/analyze.py に合わせてある。 */

const DAY = 86400;
const PERIODS = [
  ["1日", 1], ["1週", 7], ["1ヶ月", 31], ["四半期", 92],
  ["1年", 366], ["5年", 1827], ["10年", 3653], ["全期間", 0],
];
const DEFAULT_PERIOD = "1年";
const MODE_DELTA = "開始日からの動き";
const MODE_INDEX = "開始日を100とした指数";
const MODE_RAW = "実数（そのままの値）";
const MODES = [MODE_DELTA, MODE_INDEX, MODE_RAW];
const DEFAULT_SERIES = ["jgb10y", "jgb2y", "usdjpy"];
const FLATTEN_RATIO = 5;   // 同じ軸で値幅がこの倍率を超えたら小さい方は平らに見える
const BOJ_LINE_LIMIT = 20; // 縦線がこれを超える期間では引かない

const state = {
  meta: null, series: null, board: null, boj: [],
  byId: {}, selected: new Set(DEFAULT_SERIES), series_checks: {},
  mode: MODE_DELTA, period: DEFAULT_PERIOD,
  from: null, to: null, showBoj: true, plot: null, plotted: [],
};

const $ = (id) => document.getElementById(id);
const iso = (dayNum) => new Date(dayNum * DAY * 1000).toISOString().slice(0, 10);
const dayOf = (isoStr) => Math.round(Date.parse(isoStr + "T00:00:00Z") / 1000 / DAY);

/* ------------------------------------------------------------------ 保存 */
/* 選んだ状態をこのPCに覚えさせる。次に開いた時に同じ画面から始められる。
   保存先はブラウザのlocalStorageで、外には出ない。 */
const SAVE_KEY = "keizai-board/v1";

function save() {
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify({
      selected: [...state.selected],
      mode: state.mode,
      period: state.period,
      from: state.from, to: state.to,
      showBoj: state.showBoj,
      diagHorizon: $("diag-horizon")?.value,
      openFamilies: [...document.querySelectorAll(".families details")]
        .filter((d) => d.open).map((d) => d.querySelector(".fam-name").textContent),
      world: { indicator: w.indicator, countries: [...w.selected], period: w.period },
    }));
  } catch (err) {
    // 保存できなくても動作には影響させない（プライベートモードなど）
  }
}

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(SAVE_KEY) || "null");
  } catch (err) {
    return null;
  }
}

function clearSaved() {
  try {
    localStorage.removeItem(SAVE_KEY);
  } catch (err) {
    /* 消せなくても読み込み時に既定へ倒れる */
  }
  location.reload();
}

/* ------------------------------------------------------------------ 読み込み */
async function boot() {
  try {
    const [meta, series, board, boj] = await Promise.all(
      ["meta", "series", "board", "boj"].map((n) =>
        fetch(`data/${n}.json`, { cache: "no-cache" }).then((r) => {
          if (!r.ok) throw new Error(`data/${n}.json が読めない (${r.status})`);
          return r.json();
        })));
    state.meta = meta;
    state.series = series;
    state.board = board;
    state.boj = boj.meetings || [];
    meta.series.forEach((s) => { state.byId[s.id] = s; });
  } catch (err) {
    $("status").textContent = "読み込みに失敗: " + err.message;
    return;
  }
  // 系列は増減するので、保存されたIDのうち今もあるものだけ拾う。
  const saved = loadSaved();
  if (saved) {
    const alive = (saved.selected || []).filter((id) => state.byId[id]);
    if (alive.length) state.selected = new Set(alive);
    if (MODES.includes(saved.mode)) state.mode = saved.mode;
    if (typeof saved.showBoj === "boolean") state.showBoj = saved.showBoj;
    state.savedOpen = saved.openFamilies || null;
  }

  buildControls();
  if (saved && saved.diagHorizon) {
    const sel = $("diag-horizon");
    if ([...sel.options].some((o) => o.value === saved.diagHorizon)) sel.value = saved.diagHorizon;
  }
  renderDiagnosis();
  renderTables();
  renderFooter();

  if (saved && saved.period === null && saved.from && saved.to) {
    setRange(saved.from, saved.to, null);
  } else if (saved && PERIODS.some(([l]) => l === saved.period)) {
    setPeriod(saved.period);
  } else {
    setPeriod(DEFAULT_PERIOD);
  }
  $("status").textContent = `${state.board.generatedAt} 更新`;
}

/* ------------------------------------------------------------------ 部品 */
function buildControls() {
  const row = $("period-row");
  PERIODS.forEach(([label]) => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="radio" name="period" autocomplete="off" value="${label}"> ${label}`;
    l.querySelector("input").addEventListener("change", () => setPeriod(label));
    row.appendChild(l);
  });

  const mode = $("mode");
  MODES.forEach((m) => mode.add(new Option(m, m)));
  mode.value = state.mode;
  mode.addEventListener("change", () => { state.mode = mode.value; draw(); save(); });

  const boj = $("show-boj");
  boj.checked = state.showBoj;
  boj.addEventListener("change", (e) => { state.showBoj = e.target.checked; draw(); save(); });
  $("from").addEventListener("change", onDateInput);
  $("to").addEventListener("change", onDateInput);
  $("copy").addEventListener("click", copyBoard);
  $("reset").addEventListener("click", clearSaved);

  const horizon = $("diag-horizon");
  state.board.horizons.daily.forEach((h) => horizon.add(new Option(h, h)));
  horizon.value = state.board.diagnosis.horizon;
  horizon.addEventListener("change", () => { renderDiagnosis(); save(); });
  // 所見はCI側で1ヶ月ぶんを計算済み。他の期間はブラウザ側で出し直す。

  buildFamilyPickers();
}

function buildFamilyPickers() {
  // 一列に並べると指数と前年比が混ざって読めないので、分類ごとに折りたたむ。
  const host = $("pick-daily");
  host.className = "families";
  $("pick-monthly").remove();
  state.familyBoxes = [];

  const clearAll = document.createElement("button");
  clearAll.type = "button";
  clearAll.className = "clear-all";
  clearAll.textContent = "全解除";
  clearAll.title = "選んでいる系列を全部外す";
  clearAll.addEventListener("click", () => {
    state.selected.clear();
    Object.values(state.series_checks).forEach((c) => { c.checked = false; });
    state.familyBoxes.forEach((f) => updateFamilyCount(f.box, f.fam));
    draw();
    save();
  });
  host.appendChild(clearAll);

  state.meta.families.forEach((fam) => {
    const box = document.createElement("details");
    const head = document.createElement("summary");
    const list = document.createElement("div");
    list.className = "pickers";

    fam.ids.forEach((id) => {
      const s = state.byId[id];
      if (!s) return;
      const l = document.createElement("label");
      l.style.color = s.color;
      l.title = s.note || s.label;
      l.innerHTML = `<input type="checkbox" autocomplete="off" value="${id}"> ${s.short}`;
      const check = l.querySelector("input");
      check.checked = state.selected.has(id);
      check.addEventListener("change", () => {
        if (check.checked) state.selected.add(id); else state.selected.delete(id);
        updateFamilyCount(box, fam);
        draw();
        save();
      });
      list.appendChild(l);
      state.series_checks[id] = check;
    });

    box.appendChild(head);
    box.appendChild(list);
    // 選んでいる系列が入っている分類は開いておく。それ以外は畳む。
    box.open = state.savedOpen
      ? state.savedOpen.includes(fam.name)
      : fam.ids.some((id) => state.selected.has(id));
    box.addEventListener("toggle", save);
    host.appendChild(box);
    box._head = head;
    state.familyBoxes.push({ box, fam });
    updateFamilyCount(box, fam);
  });
}

function updateFamilyCount(box, fam) {
  const on = fam.ids.filter((id) => state.selected.has(id)).length;
  box._head.innerHTML = `<span class="fam-name">${fam.name}</span>` +
    `<span class="fam-count">${on ? on + " 選択中" : fam.ids.length + " 件"}</span>`;
  box.classList.toggle("has-on", on > 0);
}

/* ------------------------------------------------------------------ 期間 */
function anchorDay() {
  let last = -Infinity;
  state.meta.dailyIds.forEach((id) => {
    const s = state.series[id];
    if (s && s.t.length) last = Math.max(last, s.t[s.t.length - 1]);
  });
  return isFinite(last) ? last : dayOf(state.board.generatedAt);
}

function earliestDay() {
  let first = Infinity;
  Object.values(state.series).forEach((s) => {
    if (s.t.length) first = Math.min(first, s.t[0]);
  });
  return isFinite(first) ? first : anchorDay();
}

function setPeriod(label) {
  const days = PERIODS.find(([l]) => l === label)[1];
  const end = anchorDay();
  const start = days ? end - days : earliestDay();
  setRange(start, end, label);
}

function setRange(start, end, preset) {
  if (start > end) [start, end] = [end, start];
  state.from = start;
  state.to = end;
  state.period = preset || null;
  document.querySelectorAll('input[name=period]').forEach((r) => {
    r.checked = r.value === preset;
  });
  const lo = earliestDay(), hi = anchorDay();
  const f = $("from"), t = $("to");
  f.min = t.min = iso(lo); f.max = t.max = iso(hi);
  f.value = iso(start); t.value = iso(end);
  $("range-label").textContent = `${end - start}日間${preset ? "" : "（自由指定）"}`;
  draw();
  save();
}

function onDateInput() {
  const a = $("from").value, b = $("to").value;
  if (!a || !b) return;
  setRange(dayOf(a), dayOf(b), null);
}

/* ------------------------------------------------------------------ 変換 */
function windowPoints(id) {
  const s = state.series[id];
  if (!s) return { t: [], v: [] };
  const t = [], v = [];
  for (let i = 0; i < s.t.length; i++) {
    if (s.t[i] >= state.from && s.t[i] <= state.to) { t.push(s.t[i]); v.push(s.v[i]); }
  }
  return { t, v };
}

function transform(meta, values) {
  // 前年比や倍の水準は、指数化しても変化幅にしても意味が薄いのでそのまま描く。
  if (meta.alwaysRaw) return { ys: values.slice(), unit: meta.group };
  const base = values[0];
  if (state.mode === MODE_INDEX) {
    if (!base) return { ys: values.map(() => 0), unit: "開始日=100" };
    return { ys: values.map((v) => v / base * 100), unit: "開始日=100" };
  }
  if (state.mode === MODE_DELTA) {
    if (meta.diff === "bp") {
      return { ys: values.map((v) => (v - base) * 100), unit: "開始日からの動き（bp）" };
    }
    if (!base) return { ys: values.map(() => 0), unit: "開始日からの動き（%）" };
    return { ys: values.map((v) => (v - base) / base * 100), unit: "開始日からの動き（%）" };
  }
  return { ys: values.slice(), unit: meta.group };
}

function changeText(meta, latest, past) {
  if (meta.diff === "bp") return fmtSigned((latest - past) * 100, 1) + "bp";
  if (meta.diff === "pt") return fmtSigned(latest - past, Math.max(meta.decimals, 1)) + "pt";
  if (!past) return "-";
  return fmtSigned((latest - past) / past * 100, 2) + "%";
}

const fmtSigned = (n, d) => (n >= 0 ? "+" : "") + n.toFixed(d);
const fmtValue = (meta, v) =>
  v.toLocaleString("ja-JP", { minimumFractionDigits: meta.decimals,
                              maximumFractionDigits: meta.decimals }) + meta.suffix;

/* ------------------------------------------------------------------ 描画 */
function draw() {
  const host = $("chart");
  if (state.plot) { state.plot.destroy(); state.plot = null; }
  host.innerHTML = "";
  state.plotted = [];

  const chosen = [...state.selected].map((id) => state.byId[id]).filter(Boolean);
  if (!chosen.length) {
    host.innerHTML = '<p class="status">系列を選ぶとグラフが出る</p>';
    $("chart-notes").textContent = "";
    return;
  }

  // x軸は選んだ系列の日付の和集合。月次は日次と日付が合わないので穴を空けて繋ぐ。
  const daySet = new Set();
  const prepared = [];
  chosen.forEach((meta) => {
    const { t, v } = windowPoints(meta.id);
    if (!t.length) return;
    const { ys, unit } = transform(meta, v);
    const map = new Map();
    t.forEach((d, i) => map.set(d, ys[i]));
    const rawMap = new Map();
    t.forEach((d, i) => rawMap.set(d, v[i]));
    t.forEach((d) => daySet.add(d));
    prepared.push({ meta, map, rawMap, unit, span: Math.max(...ys) - Math.min(...ys), n: t.length });
  });
  if (!prepared.length) {
    host.innerHTML = '<p class="status">この期間にデータが無い</p>';
    $("chart-notes").textContent = "";
    return;
  }

  const days = [...daySet].sort((a, b) => a - b);
  const xs = days.map((d) => d * DAY);
  const data = [xs];
  const uSeries = [{}];
  let leftUnit = null, rightUnit = null;
  const extraUnits = [];

  prepared.forEach((p) => {
    let scale;
    if (leftUnit === null || p.unit === leftUnit) { leftUnit = p.unit; scale = "L"; }
    else if (rightUnit === null || p.unit === rightUnit) { rightUnit = p.unit; scale = "R"; }
    else { if (!extraUnits.includes(p.unit)) extraUnits.push(p.unit); scale = "R"; }

    data.push(days.map((d) => (p.map.has(d) ? p.map.get(d) : null)));
    uSeries.push({
      label: p.meta.label + (scale === "R" ? "［右軸］" : ""),
      stroke: p.meta.color,
      width: 1.6,
      scale,
      spanGaps: true,
      // 指数は実線、前年比は破線。色が近くても線の形で見分けられる。
      dash: (p.meta.cycle === "D" || p.meta.solid) ? undefined : [6, 4],
      points: { show: p.n <= 5, size: 7 },
    });
    state.plotted.push({ ...p, scale });
  });

  const css = getComputedStyle(document.body);
  const grid = { stroke: css.getPropertyValue("--grid").trim(), width: 1 };
  const axisColor = css.getPropertyValue("--muted").trim();

  const axes = [
    { stroke: axisColor, grid, ticks: grid },
    { stroke: axisColor, grid, ticks: grid, scale: "L", label: leftUnit || "" },
  ];
  if (rightUnit) {
    axes.push({ stroke: axisColor, grid: { show: false }, ticks: grid,
                scale: "R", side: 1, label: rightUnit });
  }

  const opts = {
    width: host.clientWidth || 900,
    height: Math.max(360, Math.round(window.innerHeight * 0.42)),
    series: uSeries,
    axes,
    scales: { x: { time: true }, L: {}, R: {} },
    legend: { show: false },
    cursor: { drag: { x: true, y: false }, focus: { prox: 24 } },
    hooks: {
      setCursor: [onCursor],
      setSelect: [onSelect],
      draw: [drawBojLines],
    },
  };
  state.plot = new uPlot(opts, data, host);
  host.ondblclick = () => setPeriod(state.period || DEFAULT_PERIOD);

  $("chart-notes").textContent = buildNotes(extraUnits).join("　※");
  window.onresize = () => {
    if (state.plot) state.plot.setSize({ width: host.clientWidth, height: state.plot.height });
  };
}

function buildNotes(extraUnits) {
  const notes = [];
  const inWindow = state.boj.filter((m) => {
    const d = dayOf(m.start);
    return d >= state.from && d <= state.to;
  });
  if (state.showBoj && inWindow.length > BOJ_LINE_LIMIT) {
    notes.push(`日銀会合${inWindow.length}回は多すぎるため縦線は省略`);
  }
  if (extraUnits.length) {
    notes.push("単位が3種類以上あり実数では比べにくい。指数表示を推奨");
  }
  // 同じ軸に載った系列の値幅が開きすぎていないか見る。
  const byScale = {};
  state.plotted.forEach((p) => { (byScale[p.scale] = byScale[p.scale] || []).push(p); });
  Object.values(byScale).forEach((group) => {
    if (group.length < 2) return;
    const big = group.reduce((a, b) => (a.span > b.span ? a : b));
    const small = group.reduce((a, b) => (a.span < b.span ? a : b));
    if (!(small.span > 0) || big.span / small.span < FLATTEN_RATIO) return;
    const advice = small.meta.group !== big.meta.group
      ? `「${MODE_RAW}」に切り替えると別々の軸で読める`
      : `「${MODE_DELTA}」に切り替えると比べやすい`;
    notes.push(`${small.meta.label}は${big.meta.label}の1/${(big.span / small.span).toFixed(0)}`
      + `の値幅しかなくこの縮尺では平らに見える。${advice}`);
  });
  return notes.length ? [""].concat(notes).slice(1) : [];
}

function drawBojLines(u) {
  if (!state.showBoj) return;
  const inWindow = state.boj.filter((m) => {
    const d = dayOf(m.start);
    return d >= state.from && d <= state.to;
  });
  if (!inWindow.length || inWindow.length > BOJ_LINE_LIMIT) return;
  const today = new Date().toISOString().slice(0, 10);
  const ctx = u.ctx;
  const color = getComputedStyle(document.body).getPropertyValue("--boj").trim();
  ctx.save();
  ctx.beginPath();
  ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
  ctx.clip();
  ctx.globalAlpha = 0.55;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  inWindow.forEach((m) => {
    const x = u.valToPos(dayOf(m.start) * DAY, "x", true);
    ctx.setLineDash(m.start > today ? [3, 3] : []);
    ctx.beginPath();
    ctx.moveTo(x, u.bbox.top);
    ctx.lineTo(x, u.bbox.top + u.bbox.height);
    ctx.stroke();
  });
  ctx.restore();
}

/* ------------------------------------------------------------------ カーソル */
function onCursor(u) {
  const i = u.cursor.idx;
  const out = $("readout");
  if (i == null) { out.textContent = "グラフ上にカーソルを置くとその日の値が出る"; return; }
  const day = Math.round(u.data[0][i] / DAY);

  if (u.select && u.select.width > 4) {
    const a = Math.round(u.posToVal(u.select.left, "x") / DAY);
    const b = Math.round(u.posToVal(u.select.left + u.select.width, "x") / DAY);
    out.textContent = spanText(a, b);
    return;
  }
  const parts = state.plotted.map((p) => {
    const v = nearestRaw(p, day);
    return v === null ? null : `${p.meta.label} ${fmtValue(p.meta, v)}`;
  }).filter(Boolean);
  out.textContent = `${iso(day)}　|　${parts.join("　")}`;
}

function nearestRaw(p, day) {
  if (p.rawMap.has(day)) return p.rawMap.get(day);
  // 月次は日付が飛ぶので、その日以前で一番近い値を拾う。
  let best = null, bestDay = -Infinity;
  p.rawMap.forEach((v, d) => { if (d <= day && d > bestDay) { bestDay = d; best = v; } });
  return best;
}

function spanText(a, b) {
  if (a > b) [a, b] = [b, a];
  const parts = state.plotted.map((p) => {
    const from = nearestRaw(p, a), to = nearestRaw(p, b);
    if (from === null || to === null || from === to) return null;
    return `${p.meta.label} ${changeText(p.meta, to, from)}`;
  }).filter(Boolean);
  return `${iso(a)} 〜 ${iso(b)}（${b - a}日間）　|　${parts.join("　")}`;
}

function onSelect(u) {
  if (!u.select || u.select.width < 6) return;
  const a = Math.round(u.posToVal(u.select.left, "x") / DAY);
  const b = Math.round(u.posToVal(u.select.left + u.select.width, "x") / DAY);
  if (Math.abs(b - a) < 3) return;
  u.setSelect({ width: 0, height: 0 }, false);
  setRange(a, b, null);
}

/* ------------------------------------------------------------------ 盤面 */
function renderTables() {
  fillTable($("daily-table"), state.board.daily, state.board.horizons.daily, true);
  const heads = state.board.horizons.monthly;
  fillTable($("monthly-table"), state.board.monthly, heads, false, state.board.horizons.quarterly);
}

function fillTable(table, rows, heads, withSigma, altHeads) {
  const cols = ["指標", "最新値", withSigma ? "日付" : "時点", ...heads];
  if (withSigma) cols.push("異常度");
  table.innerHTML = "";
  const thead = table.createTHead().insertRow();
  cols.forEach((c) => { const th = document.createElement("th"); th.textContent = c; thead.appendChild(th); });

  const body = table.createTBody();
  rows.forEach((r) => {
    const meta = state.byId[r.id];
    const tr = body.insertRow();
    const name = tr.insertCell();
    name.className = "name";
    name.style.color = meta.color;
    name.textContent = meta.label;
    name.title = meta.note || meta.label;
    if (r.empty) {
      tr.insertCell().textContent = "データ無し";
      for (let i = 2; i < cols.length; i++) tr.insertCell().textContent = "-";
      return;
    }
    const val = tr.insertCell(); val.className = "value"; val.textContent = r.valueText;
    const when = tr.insertCell();
    when.textContent = r.period;
    if (isStale(meta, r)) { when.className = "stale"; when.title = `${r.staleDays}日前のデータ`; }

    const names = (meta.cycle === "Q" && altHeads) ? altHeads : heads;
    names.forEach((h) => {
      const cell = tr.insertCell();
      cell.textContent = r.changes[h] ?? "-";
      cell.title = h;
      const n = r.raw[h];
      if (n != null && n !== 0) cell.className = n > 0 ? "up" : "down";
    });
    if (withSigma) {
      const z = tr.insertCell();
      z.textContent = r.z == null ? "-" : Math.abs(r.z).toFixed(1) + "σ";
      if (r.z != null && Math.abs(r.z) >= state.board.sigmaAlert) {
        z.className = "hot";
        z.title = "平常の日次変動から大きく外れている";
      }
    }
  });
}

function isStale(meta, r) {
  if (meta.cycle === "D") return (r.staleDays || 0) >= state.board.staleDays;
  const limit = state.board.staleMonths[meta.cycle] ?? 3;
  return (r.monthsBehind || 0) >= limit;
}

/* 所見。CIが1ヶ月ぶんを計算済みなので、他の期間を選んだ時だけブラウザ側で作り直す。 */
function renderDiagnosis() {
  const horizon = $("diag-horizon").value;
  const d = horizon === state.board.diagnosis.horizon
    ? state.board.diagnosis : diagnose(horizon);
  $("diag-headline").textContent = d.headline;
  $("diag-reason").textContent = d.reason;
  $("diag-facts").textContent = d.facts.join(" ／ ");
  const ul = $("diag-context");
  ul.innerHTML = "";
  (d.context || []).forEach((c) => {
    const li = document.createElement("li"); li.textContent = c; ul.appendChild(li);
  });
  const alerts = state.board.alerts;
  $("diag-alert").textContent = alerts.length ? "平常より大きい動き: " + alerts.join(" / ") : "";

  const today = new Date().toISOString().slice(0, 10);
  const next = state.boj.find((m) => m.end >= today);
  $("boj-next").textContent = next
    ? `次の日銀会合 ${next.start}（あと${Math.round((Date.parse(next.start) - Date.parse(today)) / 86400000)}日）` : "";
}

const BP_MOVE = 10, BP_FLAT = 5, FX_MOVE = 0.5, GLOBAL_GAP = 8;

function pick(id, horizon) {
  const r = state.board.daily.find((x) => x.id === id);
  return r && !r.empty ? r.raw[horizon] ?? null : null;
}

function diagnose(horizon) {
  const d2 = pick("jgb2y", horizon), d10 = pick("jgb10y", horizon);
  const d30 = pick("jgb30y", horizon), us10 = pick("ust10y", horizon);
  const fx = pick("usdjpy", horizon);
  const facts = [];
  const add = (l, v, f) => { if (v != null) facts.push(l + " " + f(v)); };
  add("日本10年", d10, (v) => fmtSigned(v, 1) + "bp");
  add("2年", d2, (v) => fmtSigned(v, 1) + "bp");
  add("30年", d30, (v) => fmtSigned(v, 1) + "bp");
  add("米10年", us10, (v) => fmtSigned(v, 1) + "bp");
  add("ドル円", fx, (v) => fmtSigned(v, 2) + "%");

  const ctx = state.board.diagnosis.context;
  const out = (headline, reason) => ({ headline, reason, facts, context: ctx, horizon });
  if (d10 == null) return out("判定できない（10年国債のデータが無い）", "");

  let curve = "";
  if (d30 != null && d2 != null) {
    const c = d30 - d2;
    curve = c > BP_FLAT ? "カーブはスティープ化（超長期が短期より上）。"
      : c < -BP_FLAT ? "カーブはフラット化（短期が超長期より上）。" : "カーブの傾きはほぼ不変。";
  }
  if (Math.abs(d10) < BP_MOVE) {
    return out("長期金利は横ばい圏",
      `日本10年の${horizon}変化が±${BP_MOVE}bp未満。${curve}`);
  }
  const up = d10 > 0, dir = up ? "上昇" : "低下";
  if (us10 != null && us10 * d10 > 0 && Math.abs(d10 - us10) < GLOBAL_GAP) {
    return out(`世界的な金利${dir}に連動している可能性`,
      `日本10年 ${fmtSigned(d10, 1)}bp に対し米10年 ${fmtSigned(us10, 1)}bp と差が${GLOBAL_GAP}bp未満。`
      + `日本固有の材料(財政・日銀)だけでは説明しきれない。${curve}`);
  }
  if (d2 != null && d2 * d10 > 0 && Math.abs(d2) >= BP_FLAT) {
    if (fx != null && ((up && fx < -FX_MOVE) || (!up && fx > FX_MOVE))) {
      return out(`日銀の利${up ? "上" : "下"}げ期待が主因の形`,
        `2年 ${fmtSigned(d2, 1)}bp と10年 ${fmtSigned(d10, 1)}bp が同方向に動き、`
        + `同時に${up ? "円高" : "円安"}(${fmtSigned(fx, 2)}%)。`
        + `政策金利の織り込みが変化した時の典型的な形。${curve}`);
    }
    return out(`短期金利を伴う金利${dir}`,
      `2年 ${fmtSigned(d2, 1)}bp も同方向。政策期待の変化が効いている可能性。`
      + `ただし為替がその形に整合していないため断定はできない。${curve}`);
  }
  if (d2 != null && Math.abs(d2) < BP_FLAT) {
    if (d30 != null && Math.abs(d30) > Math.abs(d10)) {
      return out("財政・国債需給・長期インフレ懸念が主因の形",
        `2年は ${fmtSigned(d2, 1)}bp とほぼ動かない一方、30年が ${fmtSigned(d30, 1)}bp と`
        + `10年(${fmtSigned(d10, 1)}bp)より大きく動いている。日銀の政策期待では説明しにくく、`
        + `国債の需給や長期のインフレ観に効く材料(増発・減税・積極財政)を見る場面。${curve}`);
    }
    return out(`短期金利を伴わない長期金利${dir}`,
      `2年が ${fmtSigned(d2, 1)}bp とほぼ動かないまま10年が ${fmtSigned(d10, 1)}bp。`
      + `日銀の政策期待以外の材料を疑う場面。${curve}`);
  }
  return out(`長期金利が${dir}（主因は特定できない）`,
    `日本10年 ${fmtSigned(d10, 1)}bp。他の系列と整合する型が無い。${curve}`);
}

/* ------------------------------------------------------------------ その他 */
async function copyBoard() {
  try {
    const md = await fetch("data/board.md", { cache: "no-cache" }).then((r) => r.text());
    await navigator.clipboard.writeText(md);
    $("status").textContent = `盤面をコピーした（${md.split("\n").length}行）`;
  } catch (err) {
    $("status").textContent = "コピーできなかった: " + err.message;
  }
}

function renderFooter() {
  const ul = $("sources");
  state.meta.sources.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<a href="${s.url}" rel="noopener">${s.name}</a> — ${s.series.join("・")}`;
    ul.appendChild(li);
  });
  $("generated").textContent =
    `データ生成: ${state.meta.generatedAt}　／　金利の差分はbp、為替・株・商品は変化率%、`
    + `前年比などの水準はポイント（pt）で表している。`;
}

boot();

/* ==================================================================
   他国比較。日本のブロックとは独立して動く。
   指標を1つ選んで国を並べるので、単位が揃い変換モードが要らない。
   ================================================================== */

const W_PERIODS = [["1年", 366], ["3年", 1096], ["5年", 1827], ["10年", 3653], ["全期間", 0]];
const W_DEFAULT_PERIOD = "5年";
const W_DEFAULT_COUNTRIES = ["jp", "us", "de", "gb", "kr"];
const W_STALE_MONTHS = 6;
const HIT_RADIUS = 26;  // カーソルと線がこの範囲内なら「その線に触れている」とみなす   // これ以上古い最新値は色を変えて断る

const w = {
  meta: null, byCountry: {}, indicator: null,
  selected: new Set(W_DEFAULT_COUNTRIES), period: W_DEFAULT_PERIOD,
  plot: null, plotted: [], tip: null, highlighted: undefined,
};

async function bootWorld() {
  try {
    w.meta = await fetch("data/world.json", { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`world.json が読めない (${r.status})`);
      return r.json();
    });
  } catch (err) {
    $("w-note").textContent = "他国比較の読み込みに失敗: " + err.message;
    return;
  }
  w.meta.countries.forEach((c) => { w.byCountry[c.key] = c; });
  w.indicator = w.meta.indicators[0].key;

  const saved = (loadSaved() || {}).world;
  if (saved) {
    if (w.meta.indicators.some((i) => i.key === saved.indicator)) w.indicator = saved.indicator;
    const alive = (saved.countries || []).filter((k) => w.byCountry[k]);
    if (alive.length) w.selected = new Set(alive);
    if (W_PERIODS.some(([l]) => l === saved.period)) w.period = saved.period;
  }

  buildWorldControls();
  drawWorld();
}

function buildWorldControls() {
  const ind = $("w-indicators");
  w.meta.indicators.forEach((i) => {
    const l = document.createElement("label");
    l.title = i.note || "";
    l.innerHTML = `<input type="radio" name="w-ind" autocomplete="off" value="${i.key}"> ${i.name}`;
    const r = l.querySelector("input");
    r.checked = i.key === w.indicator;
    r.addEventListener("change", () => {
      if (r.checked) { w.indicator = i.key; drawWorld(); save(); }
    });
    ind.appendChild(l);
  });

  const host = $("w-countries");
  w.meta.countries.forEach((c) => {
    const l = document.createElement("label");
    l.style.color = c.color;
    l.innerHTML = `<input type="checkbox" autocomplete="off" value="${c.key}"> ${c.name}`;
    const box = l.querySelector("input");
    box.checked = w.selected.has(c.key);
    box.addEventListener("change", () => {
      if (box.checked) w.selected.add(c.key); else w.selected.delete(c.key);
      drawWorld();
      save();
    });
    host.appendChild(l);
  });

  const per = $("w-periods");
  W_PERIODS.forEach(([label]) => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="radio" name="w-period" autocomplete="off" value="${label}"> ${label}`;
    const r = l.querySelector("input");
    r.checked = label === w.period;
    r.addEventListener("change", () => {
      if (r.checked) { w.period = label; drawWorld(); save(); }
    });
    per.appendChild(l);
  });
}

function wIndicatorMeta() {
  return w.meta.indicators.find((i) => i.key === w.indicator);
}

function wSeries(countryKey) {
  const per = w.meta.series[w.indicator] || {};
  return per[countryKey] || null;
}

function wWindow(series) {
  const days = W_PERIODS.find(([l]) => l === w.period)[1];
  if (!days || !series.d.length) return series;
  const last = dayOf(series.d[series.d.length - 1]);
  const d = [], v = [];
  for (let i = 0; i < series.d.length; i++) {
    if (dayOf(series.d[i]) >= last - days) { d.push(series.d[i]); v.push(series.v[i]); }
  }
  return { d, v };
}

function wMonthsBehind(iso) {
  const a = new Date(iso), b = new Date();
  return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
}

function drawWorld() {
  const meta = wIndicatorMeta();
  $("w-note").textContent = meta.note || "";

  // 上段：最新値の順位。国が多くても読めるので、選択に関わらず全部出す。
  const rows = [];
  w.meta.countries.forEach((c) => {
    const s = wSeries(c.key);
    if (!s || !s.v.length) return;
    rows.push({ c, value: s.v[s.v.length - 1], when: s.d[s.d.length - 1] });
  });
  rows.sort((a, b) => b.value - a.value);

  const rank = $("w-rank");
  rank.innerHTML = "";
  const max = rows.length ? Math.max(...rows.map((r) => Math.abs(r.value))) : 1;
  rows.forEach((r) => {
    const behind = wMonthsBehind(r.when);
    const div = document.createElement("div");
    div.className = "row" + (behind >= W_STALE_MONTHS ? " stale" : "");
    div.innerHTML =
      `<span class="label" style="color:${r.c.color}">${r.c.name}</span>` +
      `<span class="track"><span class="fill" style="width:${Math.abs(r.value) / max * 100}%;` +
      `background:${r.c.color}"></span></span>` +
      `<span class="num">${r.value.toFixed(meta.decimals)}${meta.unit}` +
      `<span class="when">${r.when.slice(0, 7)}</span></span>`;
    div.title = `${r.c.name} ${r.when} 時点`;
    rank.appendChild(div);
  });

  // 下段：推移
  const host = $("w-chart");
  if (w.plot) { w.plot.destroy(); w.plot = null; }
  host.innerHTML = "";
  w.plotted = [];

  const chosen = [...w.selected].map((k) => w.byCountry[k]).filter(Boolean);
  const daySet = new Set();
  const prepared = [];
  chosen.forEach((c) => {
    const s = wSeries(c.key);
    if (!s) return;
    const win = wWindow(s);
    if (win.d.length < 2) return;
    const map = new Map();
    win.d.forEach((iso, i) => { const n = dayOf(iso); daySet.add(n); map.set(n, win.v[i]); });
    prepared.push({ c, map });
  });
  if (!prepared.length) {
    host.innerHTML = '<p class="status">国を選ぶとグラフが出る</p>';
    return;
  }

  const days = [...daySet].sort((a, b) => a - b);
  const data = [days.map((d) => d * DAY)];
  const uSeries = [{}];
  prepared.forEach((p) => {
    data.push(days.map((d) => (p.map.has(d) ? p.map.get(d) : null)));
    uSeries.push({ label: p.c.name, stroke: p.c.color, width: 1.6, spanGaps: true });
    w.plotted.push(p);
  });

  const css = getComputedStyle(document.body);
  const grid = { stroke: css.getPropertyValue("--grid").trim(), width: 1 };
  const axisColor = css.getPropertyValue("--muted").trim();

  const tip = document.createElement("div");
  tip.className = "tip";
  tip.style.display = "none";
  host.appendChild(tip);
  w.tip = tip;

  w.plot = new uPlot({
    width: host.clientWidth || 900,
    height: Math.max(320, Math.round(window.innerHeight * 0.34)),
    series: uSeries,
    axes: [{ stroke: axisColor, grid, ticks: grid },
           { stroke: axisColor, grid, ticks: grid, label: `${meta.name}（${meta.unit}）` }],
    scales: { x: { time: true } },
    // 色と国名の対応が常に見えていないと、線がどの国か分からない。
    legend: { show: true, live: true },
    cursor: { focus: { prox: 24 } },
    hooks: { setCursor: [onWorldCursor] },
  }, data, host);

  window.addEventListener("resize", () => {
    if (w.plot) w.plot.setSize({ width: host.clientWidth, height: w.plot.height });
  }, { once: true });
}

function onWorldCursor(u) {
  const out = $("w-readout");
  const idx = u.cursor.idx;
  if (idx == null || u.cursor.top == null || u.cursor.top < 0) {
    out.textContent = "グラフの線にカーソルを重ねると、その線の国名と値が出る";
    if (w.tip) w.tip.style.display = "none";
    return;
  }
  const meta = wIndicatorMeta();
  const day = Math.round(u.data[0][idx] / DAY);

  // カーソルのY座標に一番近い線を1本だけ選ぶ。
  // 全部並べても「どの線がどの国か」の答えにならない。
  let hit = null, hitDist = Infinity;
  for (let si = 1; si < u.series.length; si++) {
    const value = u.data[si][idx];
    if (value == null) continue;
    // cursor.top は描画領域基準のCSSピクセル。valToPos も同じ基準で取る。
    const y = u.valToPos(value, u.series[si].scale);
    const dist = Math.abs(y - u.cursor.top);
    if (dist < hitDist) { hitDist = dist; hit = { si, value, y }; }
  }

  if (!hit || hitDist > HIT_RADIUS) {
    out.textContent = "グラフの線にカーソルを重ねると、その線の国名と値が出る";
    if (w.tip) w.tip.style.display = "none";
    highlightWorld(null);
    return;
  }

  const country = w.plotted[hit.si - 1].c;
  const text = hit.value.toFixed(meta.decimals) + meta.unit;
  out.textContent = `${country.name}　${text}　（${iso(day)}）`;
  highlightWorld(hit.si);

  if (!w.tip) return;
  w.tip.innerHTML =
    `<span class="dot" style="background:${country.color}"></span>` +
    `<span class="who">${country.name}</span>` +
    `<span class="val">${text}</span>` +
    `<span class="when">${iso(day)}</span>`;
  w.tip.style.display = "flex";

  // 1行しかないので線を隠さない。カーソルの右上へ少しずらす。
  const box = w.tip.getBoundingClientRect();
  const width = u.over.clientWidth;
  const right = u.cursor.left + 14;
  w.tip.style.left = (right + box.width > width ? u.cursor.left - box.width - 14 : right) + "px";
  w.tip.style.top = Math.max(0, hit.y - box.height - 10) + "px";
}

function highlightWorld(seriesIndex) {
  // 触っている線を太く、他を薄くする。どれを指しているか目でも分かるように。
  if (!w.plot) return;
  if (w.highlighted === seriesIndex) return;
  w.highlighted = seriesIndex;
  for (let si = 1; si < w.plot.series.length; si++) {
    const on = seriesIndex === null || si === seriesIndex;
    w.plot.setSeries(si, { width: on ? (si === seriesIndex ? 2.6 : 1.6) : 1.6 }, false);
  }
  w.plot.setSeries(seriesIndex, { focus: seriesIndex !== null });
}

bootWorld();
