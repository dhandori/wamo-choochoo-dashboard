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
  // b === 2: 이 종목은 보조 지수(한국은 KOSDAQ)와 비교한다. 보조 지수가 없는 옛 자료는 주 지수로.
  const second = s.b === 2 && Array.isArray(data.bench2);
  const bench = ((second ? data.bench2 : data.bench) || []).slice(s.s, s.s + s.c.length);
  const vols = new Array(s.c.length - s.v.length).fill(null).concat(s.v);
  // h·l·o = 최근 120거래일의 고가·저가·시가(일봉 캔들). 옛 자료에는 없고, 시가는 수집을 시작한 날부터 있다.
  const pad = arr => Array.isArray(arr) && arr.length <= s.c.length ? new Array(s.c.length - arr.length).fill(null).concat(arr) : null;
  const highs = pad(s.h), lows = pad(s.l), opens = highs && lows ? pad(s.o) : null;
  // a = [ATR(14일), 종가 대비 %] — 갱신기가 고가·저가로 계산해 둔 평균 변동폭(없으면 null).
  return { dates, closes: s.c, volumes: vols, volStart: s.c.length - s.v.length, highs: highs && lows ? highs : null, lows: highs && lows ? lows : null, opens, bench, benchLabel: (second ? data.bench2Label : data.benchLabel) || '지수', atr: Array.isArray(s.a) ? s.a : null };
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
const priceDigits = (v, market) => market === 'KR' ? 0 : market === 'JP' ? (Number.isInteger(v) ? 0 : 1) : 2;
const fmtPrice = (v, market) => v == null ? '—' : v.toLocaleString('ko-KR', { maximumFractionDigits: priceDigits(v, market), minimumFractionDigits: priceDigits(v, market) });
const fmtAxis = (v, market) => (market === 'KR' || market === 'JP') && Math.abs(v) >= 10000 ? `${(v / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}만` : v.toLocaleString('ko-KR', { maximumFractionDigits: market === 'KR' ? 0 : 2 });
const fmtVol = v => v >= 1e8 ? `${(v / 1e8).toFixed(1)}억` : v >= 1e4 ? `${Math.round(v / 1e4).toLocaleString('ko-KR')}만` : `${Math.round(v)}`;
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;

function el(tag, txt, cls) { const n = document.createElement(tag); if (txt != null) n.textContent = txt; if (cls) n.className = cls; return n; }

/** 하루치 봉. 고가·저가가 있으면 일봉, 시가까지 있으면 캔들(몸통), 없으면 고가·저가 막대에 종가 눈금. */
export function barAt(series, i) {
  const h = series.highs?.[i], l = series.lows?.[i], c = series.closes[i];
  if (h == null || l == null || c == null) return null;
  const o = series.opens?.[i];
  const prev = i > 0 ? series.closes[i - 1] : null;
  // 몸통이 있으면 시가 대비, 없으면 전일 종가 대비로 색을 정한다(한국식: 상승 빨강, 하락 파랑).
  const rise = o != null ? c >= o : prev == null || c >= prev;
  return { o: o ?? null, h, l, c, rise };
}

/**
 * Full chart: daily candles + moving averages (top), volume with 50-day average (bottom),
 * date axis, price axis on the right, legend with MA direction, hover readout.
 * levels: 가로선 [{ value, label, cls }] (예: 피벗·손절선). shade: 오른쪽에서 몇 거래일을 옅게 칠할지(베이스 구간).
 * 고가·저가가 없는 옛 자료는 종가 선으로 그린다.
 */
export function priceChart(series, { market = 'KR', days = 60, compact = false, mas = MA, showVolume = !compact, levels = [], shade = 0, width, height } = {}) {
  const n = series.closes.length, start = Math.max(0, n - days);
  const maSeries = mas.map(m => ({ ...m, values: sma(series.closes, m.n) }));
  const idx = []; for (let i = start; i < n; i++) idx.push(i);
  // 창 안의 봉이 대부분 있으면 일봉으로 그린다(거래가 없던 날만 비운다).
  const bars = idx.map(i => barAt(series, i));
  const candles = bars.filter(Boolean).length >= idx.length * 0.8;
  const bodies = candles && bars.some(b => b && b.o != null);
  const lines = (levels || []).filter(l => l && Number.isFinite(l.value));
  // Narrow screens get a smaller drawing area so axis text stays readable.
  const narrow = !compact && typeof window !== 'undefined' && window.innerWidth < 700;
  const W = width || (compact ? 300 : narrow ? 360 : 680), H = height || (compact ? 118 : narrow ? 260 : 330);
  const pad = { l: compact ? 4 : 8, r: compact ? 54 : narrow ? 56 : 60, t: compact ? 8 : 12, b: compact ? 18 : 24 };
  const volH = showVolume ? Math.round(H * 0.2) : 0, gap = showVolume ? 8 : 0;
  const plotW = W - pad.l - pad.r, priceH = H - pad.t - pad.b - volH - gap;
  const values = idx.flatMap((i, k) => [series.closes[i], ...(candles && bars[k] ? [bars[k].h, bars[k].l] : []), ...maSeries.map(m => m.values[i])])
    .concat(lines.map(l => l.value)).filter(v => v != null);
  let lo = Math.min(...values), hi = Math.max(...values);
  const margin = (hi - lo) * 0.06 || hi * 0.02; lo -= margin; hi += margin;
  const x = k => pad.l + (idx.length === 1 ? plotW / 2 : k / (idx.length - 1) * plotW);
  const y = v => pad.t + (1 - (v - lo) / (hi - lo)) * priceH;

  const wrap = el('div', null, `pc ${compact ? 'pc-compact' : ''}`);
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: compact ? 'pc-svg pc-svg-compact' : 'pc-svg detail-chart', role: 'img',
    'aria-label': `최근 ${idx.length}거래일 ${candles ? '일봉' : '종가'}과 이동평균선${lines.length ? `, ${lines.map(l => l.label).join('·')}` : ''}` });
  if (candles) svg.dataset.kind = bodies ? 'candle' : 'bar';
  // 베이스 구간(오른쪽 shade 거래일)을 옅게 칠해 패턴이 어디서 만들어졌는지 보여 준다.
  if (shade > 0 && shade < idx.length) {
    const x0 = x(idx.length - 1 - shade) - (plotW / idx.length) / 2;
    node('rect', { x: Math.max(pad.l, x0), y: pad.t, width: pad.l + plotW - Math.max(pad.l, x0), height: priceH, class: 'pc-shade' }, svg);
  }
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
  // 피벗·손절선 같은 가로선. 오른쪽 축 자리에 이름을 적는다.
  lines.forEach(l => {
    node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(l.value), y2: y(l.value), class: `pc-level ${l.cls || ''}` }, svg);
    if (!compact) text(svg, pad.l + 4, y(l.value) - 4, `${l.label} ${fmtPrice(l.value, market)}`, { class: `pc-level-text ${l.cls || ''}` });
  });
  if (candles) {
    const cw = plotW / idx.length, bw = Math.max(1, Math.min(9, cw * 0.66));
    bars.forEach((b, k) => {
      if (!b) return;
      const g = node('g', { class: `pc-candle ${b.rise ? 'rise' : 'fall'}` }, svg);
      node('line', { x1: x(k), x2: x(k), y1: y(b.h), y2: y(b.l), class: 'pc-wick' }, g);
      if (b.o != null) {
        const top = y(Math.max(b.o, b.c)), bottom = y(Math.min(b.o, b.c));
        node('rect', { x: x(k) - bw / 2, y: top, width: bw, height: Math.max(1, bottom - top), class: 'pc-body' }, g);
      } else {
        // 시가가 없는 날: 오른쪽 눈금이 종가(HLC 막대).
        node('line', { x1: x(k), x2: x(k) + Math.max(1.5, bw / 2), y1: y(b.c), y2: y(b.c), class: 'pc-wick' }, g);
      }
    });
  } else {
    node('polyline', { points: idx.map((i, k) => `${x(k)},${y(series.closes[i])}`).join(' '), class: 'pc-price' }, svg);
  }
  const lastK = idx.length - 1, last = series.closes[idx[lastK]];
  if (!candles) node('circle', { cx: x(lastK), cy: y(last), r: compact ? 2.5 : 3.5, class: 'pc-last' }, svg);
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
  item('#eaf0f8', candles ? '일봉 종가' : '종가', fmtPrice(last, market), null, 'price');
  maSeries.forEach(m => {
    const v = m.values[n - 1];
    const [back, band] = SLOPE[m.n] || [10, 0.5];
    item(m.color, m.label, v == null ? '기록 부족' : fmtPrice(v, market), v == null ? null : slope(m.values, back, band), m.dash ? 'dash' : '');
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
      const b = candles ? bars[k] : null;
      if (b) {
        if (b.o != null) row('시가', fmtPrice(b.o, market), '#8a9db5');
        row('고가', fmtPrice(b.h, market), '#8a9db5');
        row('저가', fmtPrice(b.l, market), '#8a9db5');
      }
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

/** Benchmark index line for the same window as the price chart above it.
 *  종목 차트와 같은 기간의 지수 흐름을 나란히 보여 줘서 상대강도를 눈으로 확인한다.
 *  기간 수익률(종목 vs 지수)을 캡션에 함께 적는다. */
export function benchChart(series, { days = 60 } = {}) {
  const n = series.closes.length, start = Math.max(0, n - days), idx = [];
  for (let i = start; i < n; i++) if (series.bench[i] != null) idx.push(i);
  if (idx.length < 10) return null;
  const vals = idx.map(i => series.bench[i]);
  const narrow = typeof window !== 'undefined' && window.innerWidth < 700;
  const W = narrow ? 360 : 680, H = narrow ? 120 : 130, pad = { l: 8, r: narrow ? 44 : 60, t: 10, b: 22 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  let lo = Math.min(...vals), hi = Math.max(...vals); const m = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1; lo -= m; hi += m;
  const x = k => pad.l + k / (idx.length - 1) * plotW, y = v => pad.t + (1 - (v - lo) / (hi - lo)) * plotH;
  const wrap = el('div', null, 'pc pc-bench');
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg', role: 'img', 'aria-label': `${series.benchLabel} 지수 · 종목 차트와 같은 기간` });
  niceTicks(lo, hi, 3).forEach(t => { node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: 'pc-grid' }, svg); text(svg, W - pad.r + 6, y(t) + 4, t.toLocaleString('ko-KR', { maximumFractionDigits: 0 }), { class: 'pc-axis' }); });
  const axisY = H - pad.b; node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  [0, Math.floor((idx.length - 1) / 2), idx.length - 1].forEach((k, j) => text(svg, x(k), axisY + 15, md(series.dates[idx[k]]), { class: 'pc-axis', 'text-anchor': ['start', 'middle', 'end'][j] }));
  node('polyline', { points: vals.map((v, k) => `${x(k)},${y(v)}`).join(' '), class: 'pc-benchline' }, svg);
  node('circle', { cx: x(idx.length - 1), cy: y(vals.at(-1)), r: 3.5, class: 'pc-last' }, svg);
  wrap.append(svg);
  const first = series.closes[idx[0]], last = series.closes[idx.at(-1)];
  const stockRet = first && last != null ? (last / first - 1) * 100 : null;
  const benchRet = (vals.at(-1) / vals[0] - 1) * 100;
  const sgn = v => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
  const cap = el('div', null, 'pc-caption');
  cap.append(el('b', `${series.benchLabel} · 같은 기간`));
  if (stockRet != null) {
    const diff = stockRet - benchRet;
    cap.append(el('span', ` 기간 수익률: 종목 ${sgn(stockRet)} · ${series.benchLabel} ${sgn(benchRet)} → `));
    cap.append(el('strong', `지수 대비 ${diff > 0 ? '+' : ''}${diff.toFixed(1)}%p`, diff > 0 ? 'up' : diff < 0 ? 'down' : 'neutral'));
  } else cap.append(el('span', ` 기간 수익률 ${sgn(benchRet)}`));
  wrap.prepend(cap);
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
  cap.append(el('b', `RS선 (주가 ÷ ${series.benchLabel})`), el('span', ` 그래프 첫날 = 100 · ${atHigh ? '오늘 1년 중 최고치 ●' : yearVals.length >= 200 ? `1년 최고치보다 ${Math.abs(gapPct).toFixed(1)}% 아래` : '1년 기록 부족'}`));
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
