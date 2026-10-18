// Price charts with axes, moving averages, volume and a hover readout.
// Data: wamo_chart_{KR,US}.json (loaded once per market, on first use).
const NS = 'http://www.w3.org/2000/svg';
// Colours validated for the dark chart surface (dataviz validator); 150-day is
// also dashed so it never relies on colour alone.
export const MA = [
  { n: 20, color: '#c98500', label: '20일선' },
  { n: 50, color: '#3987e5', label: '50일선' },
  { n: 150, color: '#199e70', label: '150일선', dash: true },
  { n: 200, color: '#d55181', label: '200일선' },
];
const cache = new Map();

export function loadChartData(market) {
  if (!cache.has(market)) {
    cache.set(market, fetch(`wamo_chart_${market}.json`, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw Error('HTTP'); return r.json(); })
      .catch(() => { cache.delete(market); return null; }));
  }
  return cache.get(market);
}

export function stockSeries(data, symbol) {
  const s = data?.stocks?.[symbol];
  if (!s || !Array.isArray(s.c) || s.c.length < 30) return null;
  const dates = data.dates.slice(s.s, s.s + s.c.length);
  const bench = (data.bench || []).slice(s.s, s.s + s.c.length);
  const vols = new Array(s.c.length - s.v.length).fill(null).concat(s.v);
  return { dates, closes: s.c, volumes: vols, volStart: s.c.length - s.v.length, bench, benchLabel: data.benchLabel || '지수' };
}

export function sma(values, n) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/** Direction of a moving average: % change over `back` sessions. */
export function slope(series, back, band) {
  const end = series.length - 1, now = series[end], then = series[end - back];
  if (now == null || then == null || !then) return null;
  const pct = (now / then - 1) * 100;
  return { pct, dir: pct > band ? 'up' : pct < -band ? 'down' : 'flat' };
}
const SLOPE = { 20: [5, 0.5], 50: [10, 0.5], 150: [20, 0.3], 200: [20, 0.3] };
const ARROW = { up: '↗', flat: '→', down: '↘' };

function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || 10 * mag;
  const start = Math.ceil(lo / step) * step, ticks = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}
function node(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.append(n);
  return n;
}
function text(parent, x, y, value, attrs = {}) { const t = node('text', { x, y, ...attrs }, parent); t.textContent = value; return t; }
const fmtPrice = (v, market) => v == null ? '—' : v.toLocaleString('ko-KR', { maximumFractionDigits: market === 'KR' ? 0 : 2, minimumFractionDigits: market === 'KR' ? 0 : 2 });
const fmtAxis = (v, market) => market === 'KR' && Math.abs(v) >= 10000 ? `${(v / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}만` : v.toLocaleString('ko-KR', { maximumFractionDigits: market === 'KR' ? 0 : 2 });
const fmtVol = v => v >= 1e8 ? `${(v / 1e8).toFixed(1)}억` : v >= 1e4 ? `${Math.round(v / 1e4).toLocaleString('ko-KR')}만` : `${Math.round(v)}`;
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;

function el(tag, txt, cls) { const n = document.createElement(tag); if (txt != null) n.textContent = txt; if (cls) n.className = cls; return n; }

/**
 * Full chart: price + moving averages (top), volume with 50-day average (bottom),
 * date axis, price axis on the right, legend with MA direction, hover readout.
 */
export function priceChart(series, { market = 'KR', days = 60, compact = false, mas = MA, showVolume = !compact } = {}) {
  const n = series.closes.length, start = Math.max(0, n - days);
  const maSeries = mas.map(m => ({ ...m, values: sma(series.closes, m.n) }));
  const idx = []; for (let i = start; i < n; i++) idx.push(i);
  // Narrow screens get a smaller drawing area so axis text stays readable.
  const narrow = !compact && typeof window !== 'undefined' && window.innerWidth < 700;
  const W = compact ? 300 : narrow ? 360 : 680, H = compact ? 118 : narrow ? 260 : 330;
  const pad = { l: compact ? 4 : 8, r: compact ? 46 : narrow ? 50 : 60, t: compact ? 8 : 12, b: compact ? 18 : 24 };
  const volH = showVolume ? Math.round(H * 0.2) : 0, gap = showVolume ? 8 : 0;
  const plotW = W - pad.l - pad.r, priceH = H - pad.t - pad.b - volH - gap;
  const values = idx.flatMap(i => [series.closes[i], ...maSeries.map(m => m.values[i])]).filter(v => v != null);
  let lo = Math.min(...values), hi = Math.max(...values);
  const margin = (hi - lo) * 0.06 || hi * 0.02; lo -= margin; hi += margin;
  const x = k => pad.l + (idx.length === 1 ? plotW / 2 : k / (idx.length - 1) * plotW);
  const y = v => pad.t + (1 - (v - lo) / (hi - lo)) * priceH;

  const wrap = el('div', null, `pc ${compact ? 'pc-compact' : ''}`);
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: compact ? 'pc-svg pc-svg-compact' : 'pc-svg detail-chart', role: 'img',
    'aria-label': `최근 ${idx.length}거래일 종가와 이동평균선` });
  // Grid + price axis (right)
  const ticks = niceTicks(lo, hi, compact ? 3 : 5);
  const lastY = y(series.closes[n - 1]);
  ticks.forEach(t => {
    node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: 'pc-grid' }, svg);
    if (Math.abs(y(t) - lastY) > 13) text(svg, W - pad.r + 6, y(t) + 4, fmtAxis(t, market), { class: 'pc-axis' });
  });
  // Date axis (bottom)
  const dateCount = compact ? 3 : narrow ? 4 : 6;
  const step = Math.max(1, Math.round((idx.length - 1) / (dateCount - 1)));
  const dateIdx = []; for (let k = 0; k < idx.length; k += step) dateIdx.push(k);
  if (dateIdx.at(-1) !== idx.length - 1) { if (idx.length - 1 - dateIdx.at(-1) < step / 2) dateIdx.pop(); dateIdx.push(idx.length - 1); }
  const axisY = H - pad.b;
  node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  dateIdx.forEach((k, j) => {
    node('line', { x1: x(k), x2: x(k), y1: axisY, y2: axisY + 4, class: 'pc-baseline' }, svg);
    text(svg, x(k), axisY + (compact ? 13 : 16), md(series.dates[idx[k]]), { class: 'pc-axis', 'text-anchor': j === 0 ? 'start' : k === idx.length - 1 ? 'end' : 'middle' });
  });
  // Volume
  if (showVolume) {
    const vols = idx.map(i => series.volumes[i] || 0), vmax = Math.max(...vols, 1);
    const vTop = pad.t + priceH + gap, bw = Math.max(1, plotW / idx.length * 0.7);
    const avg50 = sma(series.volumes.map(v => v || 0), 50);
    idx.forEach((i, k) => {
      const v = series.volumes[i] || 0, h = v / vmax * volH;
      const up = i > 0 && series.closes[i] >= series.closes[i - 1];
      node('rect', { x: x(k) - bw / 2, y: vTop + volH - h, width: bw, height: Math.max(0, h), class: up ? 'pc-vol up' : 'pc-vol down' }, svg);
    });
    // Only where 50 real volume days exist (older volumes are not shipped).
    const avgPts = idx.map((i, k) => avg50[i] != null && i - 49 >= (series.volStart || 0) ? `${x(k)},${vTop + volH - Math.min(avg50[i], vmax) / vmax * volH}` : null).filter(Boolean);
    if (avgPts.length > 1) node('polyline', { points: avgPts.join(' '), class: 'pc-vol-avg' }, svg);
    text(svg, W - pad.r + 6, vTop + 9, '거래량', { class: 'pc-axis' });
  }
  // Moving averages, then price on top
  maSeries.forEach(m => {
    const pts = idx.map((i, k) => m.values[i] != null ? `${x(k)},${y(m.values[i])}` : null).filter(Boolean);
    if (pts.length > 1) node('polyline', { points: pts.join(' '), stroke: m.color, class: `pc-ma ma${m.n}` }, svg);
  });
  node('polyline', { points: idx.map((i, k) => `${x(k)},${y(series.closes[i])}`).join(' '), class: 'pc-price' }, svg);
  const lastK = idx.length - 1, last = series.closes[idx[lastK]];
  node('circle', { cx: x(lastK), cy: y(last), r: compact ? 2.5 : 3.5, class: 'pc-last' }, svg);
  const tag = node('g', {}, svg);
  node('rect', { x: W - pad.r + 2, y: y(last) - 8, width: pad.r - 3, height: 16, rx: 3, class: 'pc-last-tag' }, tag);
  text(tag, W - pad.r + 6, y(last) + 4, fmtAxis(last, market), { class: 'pc-last-text' });
  wrap.append(svg);

  // Legend with latest MA values and direction
  const legend = el('div', null, 'pc-legend');
  const item = (color, label, value, dir, cls = '') => {
    // cls 'dash' draws a dashed key to match the dashed line.
    const s = el('span', null, `pc-key ${cls}`); const sw = el('i'); sw.style.background = color;
    s.append(sw, el('b', label)); if (value) s.append(el('span', value));
    if (dir) s.append(el('em', `${ARROW[dir.dir]} ${dir.pct > 0 ? '+' : ''}${dir.pct.toFixed(1)}%`, `dir-${dir.dir}`));
    legend.append(s);
  };
  item('#eaf0f8', '종가', fmtPrice(last, market), null, 'price');
  maSeries.forEach(m => {
    const v = m.values[n - 1];
    const [back, band] = SLOPE[m.n] || [10, 0.5];
    item(m.color, m.label, v == null ? '이력 부족' : fmtPrice(v, market), v == null ? null : slope(m.values, back, band), m.dash ? 'dash' : '');
  });
  if (!compact) wrap.append(legend);

  // Hover readout
  if (!compact) {
    const cross = node('line', { y1: pad.t, y2: axisY, class: 'pc-cross', visibility: 'hidden' }, svg);
    const dot = node('circle', { r: 4, class: 'pc-cross-dot', visibility: 'hidden' }, svg);
    const tip = el('div', null, 'pc-tip'); tip.hidden = true; wrap.append(tip);
    const hit = node('rect', { x: pad.l, y: pad.t, width: plotW, height: axisY - pad.t, fill: 'transparent' }, svg);
    const show = evt => {
      const box = svg.getBoundingClientRect(), px = (evt.clientX - box.left) / box.width * W;
      const k = Math.max(0, Math.min(idx.length - 1, Math.round((px - pad.l) / plotW * (idx.length - 1))));
      const i = idx[k], cx = x(k);
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', cx); dot.setAttribute('cy', y(series.closes[i])); dot.setAttribute('visibility', 'visible');
      tip.replaceChildren(el('b', series.dates[i]));
      const chg = i > 0 ? (series.closes[i] / series.closes[i - 1] - 1) * 100 : null;
      const row = (label, value, color) => { const r = el('div'); const sw = el('i'); sw.style.background = color; r.append(sw, el('span', label), el('strong', value)); tip.append(r); };
      row('종가', `${fmtPrice(series.closes[i], market)}${chg == null ? '' : ` (${chg > 0 ? '+' : ''}${chg.toFixed(1)}%)`}`, '#eaf0f8');
      maSeries.forEach(m => row(m.label, fmtPrice(m.values[i], market), m.color));
      if (series.volumes[i] != null) row('거래량', fmtVol(series.volumes[i]), '#5b6f88');
      tip.hidden = false;
      const left = cx / W * box.width;
      tip.style.left = `${left > box.width * 0.6 ? left - tip.offsetWidth - 12 : left + 12}px`;
    };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerdown', show);
    hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });
  }
  return wrap;
}

/** RS line (close ÷ benchmark) with its own axis and the 52-week high marked. */
export function rsChart(series, { days = 60 } = {}) {
  const n = series.closes.length;
  const rs = series.closes.map((c, i) => series.bench[i] ? c / series.bench[i] : null);
  const start = Math.max(0, n - days), idx = [];
  for (let i = start; i < n; i++) if (rs[i] != null) idx.push(i);
  if (idx.length < 10) return null;
  const base = rs[idx[0]], vals = idx.map(i => rs[i] / base * 100);
  const narrow = typeof window !== 'undefined' && window.innerWidth < 700;
  const W = narrow ? 360 : 680, H = narrow ? 110 : 120, pad = { l: 8, r: narrow ? 44 : 60, t: 10, b: 22 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  let lo = Math.min(...vals), hi = Math.max(...vals); const m = (hi - lo) * 0.1 || 1; lo -= m; hi += m;
  const x = k => pad.l + k / (idx.length - 1) * plotW, y = v => pad.t + (1 - (v - lo) / (hi - lo)) * plotH;
  const wrap = el('div', null, 'pc pc-rs');
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg', role: 'img', 'aria-label': 'RS선 (지수 대비 강도)' });
  niceTicks(lo, hi, 3).forEach(t => { node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: 'pc-grid' }, svg); text(svg, W - pad.r + 6, y(t) + 4, t.toFixed(0), { class: 'pc-axis' }); });
  const axisY = H - pad.b; node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  [0, Math.floor((idx.length - 1) / 2), idx.length - 1].forEach((k, j) => text(svg, x(k), axisY + 15, md(series.dates[idx[k]]), { class: 'pc-axis', 'text-anchor': ['start', 'middle', 'end'][j] }));
  node('polyline', { points: vals.map((v, k) => `${x(k)},${y(v)}`).join(' '), class: 'pc-rsline' }, svg);
  const yearStart = Math.max(0, n - 252), yearVals = rs.slice(yearStart, n).filter(v => v != null);
  const yearMax = Math.max(...yearVals), atHigh = yearVals.length >= 200 && rs[n - 1] >= yearMax * (1 - 1e-9);
  const gapPct = (rs[n - 1] / yearMax - 1) * 100;
  node('circle', { cx: x(idx.length - 1), cy: y(vals.at(-1)), r: 3.5, class: atHigh ? 'pc-rs-high' : 'pc-last' }, svg);
  wrap.append(svg);
  const cap = el('div', null, 'pc-caption');
  cap.append(el('b', `RS선 (주가 ÷ ${series.benchLabel})`), el('span', ` 그래프 첫날 = 100 · ${atHigh ? '오늘 1년 중 최고치 ●' : yearVals.length >= 200 ? `1년 최고치보다 ${Math.abs(gapPct).toFixed(1)}% 아래` : '1년 이력 부족'}`));
  wrap.prepend(cap);
  return wrap;
}

/** Plain line chart with axes for percentages (market breadth). */
export function lineChart(points, { lines, min = 0, max = 100, unit = '%', height = 130 } = {}) {
  const W = 300, H = height, pad = { l: 4, r: 36, t: 8, b: 20 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const x = k => pad.l + k / Math.max(1, points.length - 1) * plotW, y = v => pad.t + (1 - (v - min) / (max - min)) * plotH;
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg', role: 'img', 'aria-label': lines.map(l => l.label).join(', ') });
  niceTicks(min, max, 4).forEach(t => { node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: t === 50 ? 'pc-grid mid' : 'pc-grid' }, svg); text(svg, W - pad.r + 6, y(t) + 4, `${t}${unit}`, { class: 'pc-axis' }); });
  const axisY = H - pad.b; node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  const marks = [0, Math.floor((points.length - 1) / 2), points.length - 1];
  marks.forEach((k, j) => text(svg, x(k), axisY + 15, md(points[k].date), { class: 'pc-axis', 'text-anchor': ['start', 'middle', 'end'][j] }));
  lines.forEach(l => {
    const pts = points.map((p, k) => p[l.key] != null ? `${x(k)},${y(p[l.key])}` : null).filter(Boolean);
    if (pts.length > 1) node('polyline', { points: pts.join(' '), stroke: l.color, class: `pc-line ${l.dash ? 'dash' : ''}` }, svg);
  });
  return svg;
}

/** Mirrored bar chart around a zero line: positives up (green), negatives down (red). */
export function mirrorBars(points, { upKey, downKey, upLabel, downLabel, height = 130 } = {}) {
  const W = 300, H = height, pad = { l: 4, r: 36, t: 8, b: 20 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const top = Math.max(1, ...points.map(p => Math.max(p[upKey], p[downKey])));
  const ticks = niceTicks(0, top, 2), scaleMax = Math.max(top, ticks.at(-1) || top);
  const mid = pad.t + plotH / 2, unit = (plotH / 2) / scaleMax;
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg', role: 'img', 'aria-label': `${upLabel}, ${downLabel}` });
  ticks.forEach(t => {
    [mid - t * unit, mid + t * unit].forEach((yy, j) => {
      if (t === 0 && j) return;
      node('line', { x1: pad.l, x2: pad.l + plotW, y1: yy, y2: yy, class: t === 0 ? 'pc-baseline' : 'pc-grid' }, svg);
      text(svg, W - pad.r + 6, yy + 4, `${t}`, { class: 'pc-axis' });
    });
  });
  const slot = plotW / points.length, bw = Math.min(22, slot * 0.62);
  points.forEach((p, k) => {
    const cx = pad.l + slot * (k + 0.5);
    const u = node('rect', { x: cx - bw / 2, y: mid - p[upKey] * unit, width: bw, height: p[upKey] * unit, rx: 1.5, class: 'pc-bar up' }, svg);
    const d = node('rect', { x: cx - bw / 2, y: mid, width: bw, height: p[downKey] * unit, rx: 1.5, class: 'pc-bar down' }, svg);
    const t = node('title', {}, u); t.textContent = `${p.date} ${upLabel} ${p[upKey]} · ${downLabel} ${p[downKey]}`;
    d.append(t.cloneNode(true));
  });
  const marks = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  marks.forEach((k, j) => text(svg, pad.l + slot * (k + 0.5), H - 5, md(points[k].date), { class: 'pc-axis', 'text-anchor': 'middle' }));
  return svg;
}
