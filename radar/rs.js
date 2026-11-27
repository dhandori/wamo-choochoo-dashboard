// RS 한눈에 (2026-11-18 사용자 요청): 종목 상세 창과 산업을 골랐을 때 RS선·RS 점수·시장 기준선과 '왜 중요한가' 풀이.
// 자료는 wamo_chart_{시장}.json(종목별 최근 260거래일 종가와 지수) 하나로 화면에서 계산한다. 수집 단계는 바꾸지 않는다.
// - 1년 RS 점수: 오닐식 가중 수익률(최근 3개월 40%, 6·9·12개월 각 20%)을 차트 자료 안 종목끼리 1~99 등수로 매긴다.
//   선도주 탭(wamo_trend.py oneil_raw)과 시장 상황판(industry-strength update.py)과 같은 가중이다.
// - 시장 기준선: 지수의 같은 가중 수익률을 종목들 사이에 한 칸 끼워 넣었을 때의 점수. 이보다 높으면 '시장보다 강한' 종목이다.
// - 단기 RS: 표의 'RS' 칸(5·20·60일 20·40·40%)과 같은 값이며, 지수의 단기 기준선도 같은 방법으로 붙인다.
// - 업종: 구성 종목을 같은 비중으로 묶은 지수 ÷ 시장 지수(RS선)와, 그 지수의 1년 RS 점수(종목 잣대).
import { loadChartData, sma } from './chart.js';

const NS = 'http://www.w3.org/2000/svg';
const W1Y = [[63, 0.4], [126, 0.2], [189, 0.2], [252, 0.2]];
const WSHORT = [[5, 0.2], [20, 0.4], [60, 0.4]];

function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function node(tag, attrs, parent) { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.append(n); return n; }
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';

// 가중 수익률. weights 의 가장 짧은 기간 기록이 없으면 null, 긴 기간이 없으면 있는 기간끼리 가중을 다시 나눈다(상황판과 같다).
function weighted(closes, weights) {
  const n = closes.length, last = closes[n - 1];
  if (!isNum(last)) return null;
  let num = 0, den = 0;
  for (const [w, wt] of weights) {
    const old = closes[n - 1 - w];
    if (n - 1 - w < 0 || !isNum(old) || !old) { if (w === weights[0][0]) return null; continue; }
    num += (last / old - 1) * wt; den += wt;
  }
  return den ? num / den : null;
}
// 지수 배열은 중간에 빈 값이 있을 수 있어 앞 값으로 채운다.
function ffill(a) { let p = null; return (a || []).map(v => (isNum(v) ? (p = v) : p)); }

function scores(values) {   // {key: raw} → {key: 1~99} (같은 값은 평균 순위, 상황판의 rank(pct=True)와 같다)
  const keys = Object.keys(values).filter(k => isNum(values[k]));
  const sorted = keys.map(k => values[k]).sort((a, b) => a - b), n = sorted.length, out = {};
  for (const k of keys) {
    const v = values[k]; let lo = 0, hi = n;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] < v) lo = mid + 1; else hi = mid; }
    let eq = lo; while (eq < n && sorted[eq] === v) eq++;
    const rank = (lo + 1 + eq) / 2;
    out[k] = Math.min(99, Math.max(1, Math.ceil(rank / n * 99)));
  }
  return { out, sorted };
}
function insertScore(sorted, v) {   // 지수(시장)를 종목 무리에 한 칸 끼워 넣은 점수
  if (!isNum(v) || !sorted.length) return null;
  let below = 0; while (below < sorted.length && sorted[below] < v) below++;
  return Math.min(99, Math.max(1, Math.ceil((below + 1) / (sorted.length + 1) * 99)));
}

const memo = new WeakMap();
// 시장 하나의 RS 표: 종목별 1년·단기 점수와 지수 기준선.
export function rsTable(data) {
  if (!data?.stocks || !Array.isArray(data.dates)) return null;
  if (memo.has(data)) return memo.get(data);
  const y1 = {}, sh = {};
  for (const [sym, s] of Object.entries(data.stocks)) {
    if (!Array.isArray(s.c) || s.c.length < 64) continue;
    // 마지막 거래일 종가가 아닌(거래정지·누락) 종목은 등수에서 뺀다.
    if (s.s + s.c.length !== data.dates.length) continue;
    y1[sym] = weighted(s.c, W1Y); sh[sym] = weighted(s.c, WSHORT);
  }
  const a = scores(y1), b = scores(sh);
  const bench = ffill(data.bench), bench2 = Array.isArray(data.bench2) ? ffill(data.bench2) : null;
  const base = (arr, label) => arr ? { y1: insertScore(a.sorted, weighted(arr, W1Y)), short: insertScore(b.sorted, weighted(arr, WSHORT)), label: label || '지수' } : null;
  const t = { y1: a.out, short: b.out, sortedY1: a.sorted, n: a.sorted.length,
    base: { 1: base(bench, data.benchLabel), 2: base(bench2, data.bench2Label) }, bench, bench2 };
  memo.set(data, t);
  return t;
}

// ---------- 판정 문장
function verdict(score, base, up, who) {
  if (!isNum(score)) return ['', 'RS 점수를 계산할 기록이 아직 부족합니다. 1년 RS 점수는 최근 종가가 64거래일 넘게 있어야 매깁니다.'];
  if (!isNum(base)) return [up ? 'good' : up === false ? 'weak' : '', `1년 RS 점수는 ${score}점입니다.`];
  const gap = score - base;
  if (gap > 0 && up !== false) return ['good', `${who}은 시장보다 강합니다. 1년 RS 점수 ${score}점이 시장 기준선 ${base}점보다 ${gap}점 높고, RS선도 50일 평균 위에 있습니다.`];
  if (gap > 0) return ['mid', `1년으로 보면 시장보다 강하지만 최근 힘이 빠지고 있습니다. RS 점수 ${score}점은 시장 기준선 ${base}점보다 높지만, RS선이 50일 평균 아래로 내려왔습니다.`];
  if (up === true) return ['mid', `아직은 시장보다 약하지만 최근 따라붙고 있습니다. RS 점수 ${score}점은 시장 기준선 ${base}점보다 ${-gap}점 낮지만, RS선은 50일 평균 위로 올라섰습니다.`];
  return ['weak', `${who}은 시장보다 약합니다. 1년 RS 점수 ${score}점이 시장 기준선 ${base}점보다 ${-gap}점 낮습니다. 강의 기준으로는 주도주 후보에서 뺍니다.`];
}

function gauge(score, base) {
  const g = el('div', null, 'rsg'); g.setAttribute('role', 'img');
  g.setAttribute('aria-label', `1년 RS 점수 ${score}점${isNum(base) ? `, 시장 기준선 ${base}점` : ''}`);
  const pos = v => `${Math.max(1, Math.min(99, v))}%`;
  const track = el('div', null, 'rsg-track');
  if (isNum(base)) { const w = el('i', null, 'rsg-weak'); w.style.width = pos(base); const l = el('i', null, 'rsg-base'); l.style.left = pos(base); track.append(w, l); }
  const dot = el('b', null, 'rsg-dot'); dot.style.left = pos(score); track.append(dot);
  const lab = el('div', null, 'rsg-lab'); const me = el('span', String(score), 'me'); me.style.left = pos(score); lab.append(me);
  g.append(track, lab);
  if (isNum(base)) { const low = el('div', null, 'rsg-lab low'); const s = el('span', `시장 ${base}`); s.style.left = pos(base); low.append(s); g.append(low); }
  const ends = el('div', null, 'rsg-ends'); ends.append(el('span', '1 · 약함'), el('span', '99 · 강함')); g.append(ends);
  return g;
}

function fact(label, value, sub, cls) {
  const d = el('div'); d.append(el('em', label), el('b', value, cls || ''), el('small', sub)); return d;
}

const WHY = {
  stock: [
    ['RS는 \'반 평균\'과 견준 성적입니다.', '같은 기간 시장(지수)보다 더 올랐는지를 봅니다. 많이 오른 종목은 비싸 보이지만, 주도주는 시장보다 강한 종목 가운데서 나옵니다. 덜 오른 종목은 싸서가 아니라 약해서 덜 올랐을 수 있습니다.'],
    ['RS선과 RS 점수는 보는 것이 다릅니다.', 'RS선은 주가를 지수로 나눈 선이고, 선이 오르면 시장보다 강하다는 뜻이라 O/X로 읽습니다. 1년 RS 점수는 최근 3개월 40%, 6·9·12개월 각 20%로 섞은 수익률을 시장 안 다른 종목과 견준 1~99 등수입니다. 최근 석 달에 무게를 가장 많이 둡니다.'],
    ['기준은 점수 크기가 아니라 시장 기준선입니다.', '시장 기준선은 지수를 같은 잣대로 쟀을 때 나오는 점수입니다. 이보다 낮으면 시장보다 약한 종목이고, 높으면 주도주 후보입니다. 기준선 위라면 95점과 80점을 같은 후보로 봅니다. 점수가 지나치게 높으면 단기 고점일 수도 있습니다.'],
    ['RSI와는 다른 지표입니다.', 'RSI는 자기 과거와 견줘 최근에 너무 올랐는지, 너무 빠졌는지를 보고, RS는 시장과 견줍니다. RSI가 높다고 강한 종목이라는 뜻은 아닙니다.'],
    ['RS 하나로 정하지 않습니다.', '업종이 같이 오르는지, 외국인·기관이 사는지, 추세가 우상향인지를 함께 봅니다. 여러 신호가 같은 방향을 가리킬 때 주도주일 가능성이 커집니다. 개인 위주로 혼자 급등한 테마주는 RS가 높아도 보류합니다.'],
  ],
  industry: [
    ['업종 RS는 이 산업이 시장보다 강한지를 봅니다.', '기관과 외국인은 종목 하나보다 업종 단위로 돈을 넣는 경우가 많습니다. 그래서 진짜 추세는 업종 전체의 움직임(인더스트리 액션)에서 먼저 보입니다.'],
    ['RS선과 RS 점수는 보는 것이 다릅니다.', '업종 RS선은 구성 종목을 같은 비중으로 묶은 지수를 시장 지수로 나눈 선이고, 선이 오르면 시장보다 강하다는 뜻입니다. 1년 RS 점수는 그 묶음 지수를 종목 하나처럼 보고 시장 안 종목과 견준 1~99 등수입니다. 그래서 종목 점수, 시장 기준선과 같은 잣대로 읽을 수 있습니다.'],
    ['기준은 점수 크기가 아니라 시장 기준선입니다.', '업종 점수가 시장 기준선보다 낮으면 그 업종은 시장보다 약합니다. 기준선 위라면 점수 차이로 줄을 세울 필요는 없습니다.'],
    ['업종 1등이 곧 강한 종목은 아닙니다.', '업종 안에서 1등이어도 시장보다 약할 수 있습니다. 판단 순서는 시장 날씨, 업종이 같이 오르는지, 종목이 지수보다 강한지입니다. 아래 종목을 누르면 종목 창에서 시장 기준선과 견줘 볼 수 있습니다.'],
  ],
};
function why(kind) {
  const d = el('details', null, 'rs-why'); d.append(el('summary', 'RS가 왜 중요한가'));
  WHY[kind].forEach(([h, t]) => { const p = el('p'); p.append(el('b', h), document.createTextNode(' ' + t)); d.append(p); });
  return d;
}

function box(kind, { score, base, up, hiDist, who, extraFacts = [], chart = null, title }) {
  const [cls, text] = verdict(score, base?.y1, up, who);
  const sec = el('section', null, `rs-box ${cls}`.trim()); sec.setAttribute('aria-label', 'RS 한눈에');
  sec.append(el('h3', title || '📐 RS · 시장보다 강한가', 'rs-title'), el('p', text, 'rs-verdict'));
  if (isNum(score)) sec.append(gauge(score, base?.y1));
  const facts = el('div', null, 'rs-facts');
  if (isNum(score)) facts.append(fact('1년 RS 점수', String(score), kind === 'industry' ? '묶음 지수를 시장 안 종목과 견준 1~99' : '시장 안 종목끼리 1~99'));
  if (isNum(base?.y1)) facts.append(fact('시장 기준선', String(base.y1), `${base.label}를 같은 잣대로 잰 점수`));
  if (up != null) facts.append(fact('RS선 흐름', up ? '50일 평균 위' : '50일 평균 아래', up ? '최근 시장보다 강해지는 중' : '최근 시장보다 약해지는 중', up ? 'good' : 'weak'));
  if (isNum(hiDist)) facts.append(fact('RS선 1년 최고 대비', hiDist >= -0.05 ? '신고가' : `${hiDist.toFixed(1)}%`, hiDist >= -0.5 ? '시장 대비 강도가 1년 중 최고' : '1년 최고치보다 아래'));
  extraFacts.forEach(f => facts.append(f));
  if (facts.childElementCount) sec.append(facts);
  if (chart) sec.append(chart);
  sec.append(why(kind));
  return sec;
}

// RS선 상태: 마지막 값이 50일 평균 위인지, 1년 최고 대비 %
function rsState(rs) {
  const valid = rs.filter(isNum);
  if (valid.length < 50) return { up: null, hiDist: null };
  const avg = sma(valid, 50).at(-1), last = valid.at(-1);
  const year = valid.slice(-252), hi = Math.max(...year);
  return { up: last > avg, hiDist: year.length >= 200 ? (last / hi - 1) * 100 : null };
}

// ---------- 종목
export function stockRsBox(s, data) {
  const t = rsTable(data), c = data?.stocks?.[s.symbol];
  const second = c?.b === 2 && t?.base?.[2];
  const base = t ? (second ? t.base[2] : t.base[1]) : null;
  const extra = [];
  if (isNum(s.rs)) extra.push(fact('단기 RS (표의 RS 칸)', String(Math.round(s.rs)), `1주·1개월·3개월 기준${isNum(base?.short) ? ` · 시장 ${base.short}` : ''}`, isNum(base?.short) ? (s.rs > base.short ? 'good' : 'weak') : ''));
  let up = null, hiDist = null;
  if (c && t) {
    const bench = (second ? t.bench2 : t.bench).slice(c.s, c.s + c.c.length);
    ({ up, hiDist } = rsState(c.c.map((v, i) => isNum(v) && bench[i] ? v / bench[i] : null)));
  }
  if (!t || !c) {
    const sec = el('section', null, 'rs-box'); sec.append(el('h3', '📐 RS · 시장보다 강한가', 'rs-title'),
      el('p', `차트 자료에 이 종목이 없어 1년 RS 점수와 시장 기준선을 계산하지 못했습니다.${isNum(s.rs) ? ` 단기 RS는 ${Math.round(s.rs)}점입니다.` : ''}`, 'rs-verdict'), why('stock'));
    return sec;
  }
  return box('stock', { score: t.y1[s.symbol], base, up, hiDist, who: '이 종목', extraFacts: extra });
}

// 상세 창에 자리부터 잡고, 차트 자료가 오면 채운다.
export function stockRsSlot(s, market, ready) {
  const slot = el('div', null, 'rs-slot');
  const fill = data => { if (data) slot.replaceChildren(stockRsBox(s, data)); else slot.replaceChildren(); };
  if (ready) fill(ready); else { slot.append(el('p', 'RS 계산 중…', 'rs-load')); loadChartData(market).then(fill); }
  return slot;
}

// ---------- 업종
function basket(data, symbols) {
  const n = data.dates.length, lvl = new Array(n).fill(null), used = [];
  const series = symbols.map(sym => data.stocks[sym]).filter(c => c && Array.isArray(c.c) && c.c.length >= 64);
  if (!series.length) return null;
  series.forEach(c => used.push(c));
  let level = null;
  for (let i = 0; i < n; i++) {
    let sum = 0, k = 0;
    for (const c of series) {
      const j = i - c.s, a = c.c[j - 1], b = c.c[j];
      if (j >= 1 && j < c.c.length && isNum(a) && isNum(b) && a) { sum += b / a - 1; k++; }
    }
    if (level == null) { if (series.some(c => i - c.s >= 0 && i - c.s < c.c.length)) level = 100; }
    else if (k) level *= 1 + sum / k;
    lvl[i] = level;
  }
  return { lvl, count: used.length, second: series.filter(c => c.b === 2).length > series.length / 2 };
}

function lineSvg(dates, rs, avg, days) {
  const n = rs.length, start = Math.max(0, n - days), idx = [];
  for (let i = start; i < n; i++) if (isNum(rs[i])) idx.push(i);
  if (idx.length < 10) return null;
  const b0 = rs[idx[0]], v = idx.map(i => rs[i] / b0 * 100), m = idx.map(i => isNum(avg[i]) ? avg[i] / b0 * 100 : null);
  const narrow = typeof window !== 'undefined' && window.innerWidth < 700;
  const W = narrow ? 360 : 680, H = 130, pad = { l: 8, r: narrow ? 40 : 52, t: 10, b: 22 }, pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
  const all = v.concat(m.filter(isNum)); let lo = Math.min(...all, 100), hi = Math.max(...all, 100); const mg = (hi - lo) * 0.1 || 1; lo -= mg; hi += mg;
  const x = k => pad.l + k / (idx.length - 1) * pw, y = val => pad.t + (1 - (val - lo) / (hi - lo)) * ph;
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg rs-line-svg', role: 'img', 'aria-label': '업종 RS선' });
  node('line', { x1: pad.l, x2: pad.l + pw, y1: y(100), y2: y(100), class: 'pc-grid mid' }, svg);
  const tx = (xx, yy, s, a) => { const t = node('text', { x: xx, y: yy, class: 'pc-axis', ...(a ? { 'text-anchor': a } : {}) }, svg); t.textContent = s; };
  [lo + mg, hi - mg].forEach(t => tx(W - pad.r + 6, y(t) + 4, t.toFixed(0)));
  tx(W - pad.r + 6, y(100) + 4, '100');
  [0, Math.floor((idx.length - 1) / 2), idx.length - 1].forEach((k, j) => tx(x(k), H - 6, md(dates[idx[k]]), ['start', 'middle', 'end'][j]));
  const mp = m.map((val, k) => isNum(val) ? `${x(k)},${y(val)}` : null).filter(Boolean);
  if (mp.length > 1) node('polyline', { points: mp.join(' '), class: 'rs-avg' }, svg);
  node('polyline', { points: v.map((val, k) => `${x(k)},${y(val)}`).join(' '), class: 'pc-rsline' }, svg);
  node('circle', { cx: x(idx.length - 1), cy: y(v.at(-1)), r: 3.5, class: 'pc-last' }, svg);
  return svg;
}

export function industryRsBox(g, data) {
  const t = rsTable(data);
  const syms = (g.members || []).map(id => String(id).split(':').pop());
  const bk = t && basket(data, syms);
  if (!t || !bk) {
    const sec = el('section', null, 'rs-box'); sec.append(el('h3', `📐 ${g.name} · 업종 RS`, 'rs-title'), el('p', '차트 자료에 이 산업 종목이 없어 업종 RS를 계산하지 못했습니다.', 'rs-verdict'), why('industry'));
    return sec;
  }
  const second = bk.second && t.base[2];
  const bench = second ? t.bench2 : t.bench, base = second ? t.base[2] : t.base[1];
  const rs = bk.lvl.map((v, i) => isNum(v) && bench[i] ? v / bench[i] : null);
  const avg = new Array(rs.length).fill(null);
  { const vi = []; rs.forEach((v, i) => { if (isNum(v)) vi.push(i); }); const a = sma(vi.map(i => rs[i]), 50); vi.forEach((i, k) => { avg[i] = a[k]; }); }
  const { up, hiDist } = rsState(rs);
  const raw = weighted(bk.lvl.filter(isNum), W1Y);
  const score = insertScore(t.sortedY1, raw);
  const wrap = el('div', null, 'rs-chart');
  wrap.append(el('div', `업종 RS선 · 같은 비중 ${bk.count}종목 ÷ ${base.label} · 최근 6개월, 첫날 = 100 · 선이 오르면 시장보다 강해지는 중 · 점선은 50일 평균`, 'rs-cap'));
  const svg = lineSvg(data.dates, rs, avg, 126); if (svg) wrap.append(svg);
  const extra = bk.count < 3 ? [fact('묶은 종목 수', `${bk.count}종목`, '종목이 적어 한두 종목 움직임에 크게 흔들립니다', 'weak')] : [];
  return box('industry', { score, base, up, hiDist, who: '이 산업', chart: wrap, extraFacts: extra, title: `📐 ${g.name} · 업종 RS` });
}

export function industryRsSlot(g, market) {
  const slot = el('div', null, 'rs-slot');
  slot.append(el('p', '업종 RS 계산 중…', 'rs-load'));
  loadChartData(market).then(data => { slot.replaceChildren(...(data ? [industryRsBox(g, data)] : [])); });
  return slot;
}
