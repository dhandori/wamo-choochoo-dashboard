// 천장 신호: 일중전환(장중 반전)과 클라이맥스 런. wamo_trend.json의 markets[시장].climax만 읽습니다.
// 계산 규칙은 wamo_climax.py에 있고, 이 화면은 그 결과를 지수 차트·기록표·종목 목록으로 보여 줍니다.
import { MA } from './chart.js?v=20261024-dd1';
import { addTerms, helpButton } from './help.js?v=20261024-dd1';
import { glossary } from './terms.mjs?v=20261024-dd1';

const NS = 'http://www.w3.org/2000/svg';
const isNum = n => typeof n === 'number' && Number.isFinite(n);
const fmt = (n, d = 1) => isNum(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = n => isNum(n) ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—';
const tone = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;
function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function node(tag, attrs = {}, parent) { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.append(n); return n; }
function txt(parent, x, y, value, attrs = {}) { const t = node('text', { x, y, ...attrs }, parent); t.textContent = value; return t; }
function btn(text, active, action, cls = '') { const b = el('button', text, `${active ? 'active ' : ''}${cls}`.trim()); b.type = 'button'; b.onclick = action; return b; }

// 화면에 나오는 이름 [긴 이름, 짧은 이름, 색 구분]
export const KIND = {
  TOP_WARNING: ['천장 경고', '천장 경고', 'bad'],
  CLIMAX: ['클라이맥스 런', '클라이맥스', 'warn'],
  TOP: ['일중전환 · 천장형', '천장형 반전', 'warn'],
  BOTTOM: ['일중전환 · 바닥형', '바닥형 반전', 'lead'],
};
// [짧은 이름, 색, 설명, 큰 글씨 한 줄, 아이콘]
const STATE = {
  TOP_WARNING: ['천장 경고', 'bad', '클라이맥스 런 뒤에 천장형 일중전환이 나왔어요. 상승이 끝났을 가능성을 가장 강하게 의심하는 자리예요.', '천장 경고 · 클라이맥스 뒤 고점 찍고 밀림', '⚠'],
  CLIMAX: ['클라이맥스 진행 중', 'warn', '짧은 기간에 지수가 가파르게 올랐어요. 아직 꺾이지는 않았지만 끝물 징후가 겹쳐 있어요.', '클라이맥스 런 진행 중 (끝물 급등)', '🔥'],
  TOP: ['천장형 반전', 'warn', '최근 5거래일 안에 장중 고점을 새로 찍고 밀려 끝난 날이 있었어요.', '일중전환 있었음 · 천장형 (고점 찍고 밀림)', '▼'],
  BOTTOM: ['바닥형 반전', 'lead', '최근 5거래일 안에 장중 저점을 깨고 말아 올린 날이 있었어요.', '일중전환 있었음 · 바닥형 (저점 깨고 말아 올림)', '▲'],
  NONE: ['특이 신호 없음', 'none', '최근 10거래일 동안 클라이맥스 런이나 일중전환이 없었어요.', '일중전환 없었음', '—'],
};

const TERMS = {
  cx_climaxRun: ['클라이맥스 런', '이미 오르던 종목이 끝에 가서 갑자기 수직으로 치솟는 구간입니다. 종목은 15거래일 안에 최저 종가보다 25% 넘게 오르고(지수는 12%), 아래 징후 중 2개 이상(지수는 3개)이 겹치면 클라이맥스 런으로 봅니다. ① 1년 중 최대 하루 상승 ② 1년 중 최대 5일 상승 ③ 10거래일 중 7일 이상 상승 ④ 소진 갭 ⑤ 50일선·200일선과 크게 벌어짐(종목 25%·70%, 지수 10%·25%) ⑥ 1년 중 최대 거래량. 오닐과 미너비니가 "오를 때 나눠 팔라"고 하는 자리입니다.'],
  cx_reversalTop: ['일중전환 · 천장형', '장중에 최근 50거래일 최고가를 새로 찍었는데, 종가는 전날보다 낮고 그날 범위의 아래쪽 절반에서 끝난 날입니다. 하루 움직임이 평소(최근 20거래일 평균)보다 작으면 세지 않습니다. 넓은 범위, 대량 거래, 저가 근처 마감, 갭 상승 뒤 반전, 장중 큰 되밀림 중 2개 이상이면 "강"입니다. 종목은 전날 종가가 50일선 위였던 경우만 봅니다.'],
  cx_reversalBottom: ['일중전환 · 바닥형', '장중에 최근 20거래일 최저가를 깼는데, 종가는 전날보다 높고 그날 범위의 위쪽 40% 안에서 끝난 날입니다. 털어내기 뒤 되돌림 반등 신호입니다. 종목은 전날 종가가 200일선 위였던 경우(상승 추세 속 조정)만 봅니다.'],
  cx_topWarning: ['천장 경고', '클라이맥스 런이 나온 날부터 10거래일 안에 천장형 일중전환이 나온 경우입니다. 두 신호가 겹칠 때 상승 추세가 끝났을 가능성이 가장 높다고 봅니다. 그래도 강한 주도주는 다시 오르기도 하므로 일부 차익 실현이나 손절선 올리기로 대응하는 경우가 많습니다.'],
  cx_closePos: ['종가 위치', '그날 저가를 0%, 고가를 100%로 놓았을 때 종가가 어디서 끝났는지입니다. 20%면 저가 근처, 80%면 고가 근처에서 끝났다는 뜻입니다.'],
  cx_rangeRatio: ['평소 대비 범위', '그날 (고가 − 저가) ÷ 전날 종가를, 최근 20거래일 평균과 비교한 배수입니다. 1.5배면 평소보다 50% 넓게 움직였다는 뜻입니다.'],
  cx_exhaustionGap: ['소진 갭', '그날 저가가 전날 고가보다 높아 차트에 빈칸이 생긴 상승입니다. 오래 오른 뒤 나오면 마지막 매수세가 몰린 흔적(끝물)으로 봅니다. 종목은 전날 고가보다 0.5% 넘게 높게 시작해 그 위에서 끝난 날도 셉니다.'],
  cx_runGain: ['급등폭', '최근 15거래일 안의 가장 낮은 종가에서 클라이맥스 판정일 종가까지 오른 비율입니다.'],
  cx_fromPeak: ['고점 대비', '클라이맥스 구간에서 찍은 장중 최고가보다 지금 종가가 몇 % 아래인지입니다.'],
  cx_after: ['5일·20일 뒤', '신호가 나온 날 종가와 비교해 5거래일·20거래일 뒤 종가가 몇 % 움직였는지입니다. 20일 최저는 그 20거래일 동안 가장 깊게 빠진 장중 저가입니다.'],
};
addTerms(TERMS);
const term = (key, text) => helpButton(text || TERMS[key][0], key);

// ------------------------------------------------------------ 지수 캔들 차트
function indexChart(rep, market) {
  const cs = (rep.candles || []).filter(c => isNum(c.h) && isNum(c.l) && isNum(c.c));
  if (cs.length < 20) return el('p', '차트 자료가 부족해요.', 'tr-help');
  const days = cs.length;
  const narrow = window.innerWidth < 700;
  // Volume panel under the candles (green = 양봉, red = 음봉), only when the index reports volume.
  const hasVol = cs.filter(c => isNum(c.v) && c.v > 0).length >= cs.length * 0.8;
  const VOLH = hasVol ? (narrow ? 46 : 58) : 0, GAP = hasVol ? 10 : 0;
  const W = narrow ? 360 : 680, H = (narrow ? 250 : 300) + VOLH + GAP;
  const pad = { l: 8, r: narrow ? 52 : 60, t: 18, b: 24 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b - VOLH - GAP;
  const ma = [20, 50].map(n => ({ ...MA.find(m => m.n === n), values: (rep.ma?.[n] || []).slice(-days) }));
  const vals = cs.flatMap(c => [c.h, c.l]).concat(ma.flatMap(m => m.values.filter(isNum)));
  let lo = Math.min(...vals), hi = Math.max(...vals); const m = (hi - lo) * 0.08; lo -= m; hi += m;
  const slot = plotW / days, x = k => pad.l + slot * (k + 0.5), y = v => pad.t + (1 - (v - lo) / (hi - lo)) * plotH;
  const wrap = el('div', null, 'pc cx-chart');
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg detail-chart', role: 'img', 'aria-label': `${rep.label} 최근 ${days}거래일 캔들 차트와 천장 신호 표시` });
  // 클라이맥스 구간: 차트 맨 위 노란 띠(캔들을 가리지 않게)
  const zone = new Set(rep.climaxDates || []);
  cs.forEach((c, k) => { if (zone.has(c.d)) node('rect', { x: x(k) - slot / 2, y: 3, width: slot + 0.4, height: 6, class: 'cx-zone' }, svg); });
  // 가로 눈금 + 오른쪽 가격축
  const span = hi - lo, raw = span / 5, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(v => v * mag).find(s => s >= raw);
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
    node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: 'pc-grid' }, svg);
    txt(svg, W - pad.r + 6, y(t) + 4, t.toLocaleString('ko-KR', { maximumFractionDigits: t < 100 ? 2 : 0 }), { class: 'pc-axis' });
  }
  // 날짜축
  const axisY = H - pad.b;
  node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  const marks = narrow ? 4 : 6, every = Math.max(1, Math.round((days - 1) / (marks - 1)));
  for (let k = 0; k < days; k += every) {
    if (days - 1 - k < every / 2 && k !== 0) continue;
    node('line', { x1: x(k), x2: x(k), y1: axisY, y2: axisY + 4, class: 'pc-baseline' }, svg);
    txt(svg, x(k), axisY + 16, md(cs[k].d), { class: 'pc-axis', 'text-anchor': k === 0 ? 'start' : 'middle' });
  }
  txt(svg, x(days - 1), axisY + 16, md(cs[days - 1].d), { class: 'pc-axis', 'text-anchor': 'end' });
  // 이동평균선
  ma.forEach(line => {
    const pts = line.values.map((v, k) => isNum(v) ? `${x(k)},${y(v)}` : null).filter(Boolean);
    if (pts.length > 1) node('polyline', { points: pts.join(' '), stroke: line.color, class: `pc-ma ma${line.n}` }, svg);
  });
  // 캔들 (시가가 없는 날은 심지와 종가 눈금만)
  const bw = Math.max(1.6, slot * 0.62);
  cs.forEach((c, k) => {
    const prev = cs[k - 1]?.c, open = isNum(c.o) && c.o > 0 ? c.o : null;
    const down = open != null ? c.c < open : prev != null && c.c < prev;
    node('line', { x1: x(k), x2: x(k), y1: y(c.h), y2: y(c.l), class: `cx-wick ${down ? 'down' : 'up'}` }, svg);
    if (open != null) {
      const top = y(Math.max(open, c.c)), h = Math.max(1, Math.abs(y(open) - y(c.c)));
      node('rect', { x: x(k) - bw / 2, y: top, width: bw, height: h, class: `cx-body ${down ? 'down' : 'up'}` }, svg);
    } else node('line', { x1: x(k), x2: x(k) + bw / 2, y1: y(c.c), y2: y(c.c), class: `cx-wick ${down ? 'down' : 'up'}` }, svg);
  });
  // 거래량 막대: 캔들과 같은 색, 오른쪽에 최대 거래량 눈금, 주황 선 = 50일 평균 거래량
  if (hasVol) {
    const vTop = pad.t + plotH + GAP, vMax = Math.max(...cs.map(c => c.v || 0));
    const vy = v => axisY - v / vMax * VOLH;
    node('line', { x1: pad.l, x2: pad.l + plotW, y1: vTop, y2: vTop, class: 'pc-grid' }, svg);
    txt(svg, W - pad.r + 6, vTop + 8, volText(vMax), { class: 'pc-axis' });
    txt(svg, W - pad.r + 6, axisY - 3, '거래량', { class: 'pc-axis cx-vol-label' });
    cs.forEach((c, k) => {
      if (!isNum(c.v) || c.v <= 0) return;
      const prev = cs[k - 1]?.c, open = isNum(c.o) && c.o > 0 ? c.o : null;
      const down = open != null ? c.c < open : prev != null && c.c < prev;
      node('rect', { x: x(k) - bw / 2, y: vy(c.v), width: bw, height: Math.max(0.8, axisY - vy(c.v)), class: `cx-vol ${down ? 'down' : 'up'}` }, svg);
    });
    const avg = cs.map((c, k) => { const w = cs.slice(Math.max(0, k - 49), k + 1).map(z => z.v).filter(v => isNum(v) && v > 0); return k >= 19 && w.length ? w.reduce((a, b) => a + b, 0) / w.length : null; });
    const pts = avg.map((v, k) => v == null ? null : `${x(k)},${vy(v)}`).filter(Boolean);
    if (pts.length > 1) node('polyline', { points: pts.join(' '), class: 'cx-vol-avg' }, svg);
  }
  // 신호 표시: ▼ 천장형(천장 경고는 크게), ▲ 바닥형
  const byDate = new Map(); (rep.events || []).forEach(e => { if (e.type !== 'CLIMAX') byDate.set(e.date, e); });
  cs.forEach((c, k) => {
    const e = byDate.get(c.d); if (!e) return;
    const s = e.type === 'TOP_WARNING' ? 7 : 5;
    if (e.type === 'BOTTOM') {
      const yy = y(c.l) + 5;
      node('path', { d: `M${x(k)},${yy} l${-s},${s * 1.5} h${s * 2} z`, class: 'cx-mark bottom' }, svg);
    } else {
      const yy = y(c.h) - 5;
      node('path', { d: `M${x(k)},${yy} l${-s},${-s * 1.5} h${s * 2} z`, class: `cx-mark ${e.type === 'TOP_WARNING' ? 'warning' : 'top'}` }, svg);
    }
  });
  // 가장 최근 천장 경고에 날짜 이름표
  const lastWarn = (rep.events || []).find(e => e.type === 'TOP_WARNING' && cs.some(c => c.d === e.date));
  if (lastWarn) {
    const k = cs.findIndex(c => c.d === lastWarn.date), c = cs[k];
    const right = x(k) > pad.l + plotW * 0.75;
    txt(svg, x(k) + (right ? -10 : 10), Math.max(pad.t + 10, y(c.h) - 8), `${md(c.d)} 천장 경고`, { class: 'cx-label', 'text-anchor': right ? 'end' : 'start' });
  }
  wrap.append(svg);
  // 마우스 · 손가락 읽기
  const cross = node('line', { y1: pad.t, y2: axisY, class: 'pc-cross', visibility: 'hidden' }, svg);
  const tip = el('div', null, 'pc-tip'); tip.hidden = true; wrap.append(tip);
  const hit = node('rect', { x: pad.l, y: pad.t, width: plotW, height: axisY - pad.t, fill: 'transparent' }, svg);
  const show = evt => {
    const box = svg.getBoundingClientRect(), px = (evt.clientX - box.left) / box.width * W;
    const k = Math.max(0, Math.min(days - 1, Math.floor((px - pad.l) / slot)));
    const c = cs[k], prev = cs[k - 1]?.c;
    cross.setAttribute('x1', x(k)); cross.setAttribute('x2', x(k)); cross.setAttribute('visibility', 'visible');
    tip.replaceChildren(el('b', c.d));
    const row = (label, value, color) => { const r = el('div'); const sw = el('i'); sw.style.background = color; r.append(sw, el('span', label), el('strong', value)); tip.append(r); };
    const p = v => isNum(v) && v > 0 ? v.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '—';
    row('종가', `${p(c.c)}${isNum(prev) ? ` (${pct((c.c / prev - 1) * 100)})` : ''}`, '#eaf0f8');
    row('시가 · 고가 · 저가', `${p(c.o)} · ${p(c.h)} · ${p(c.l)}`, '#5b6f88');
    if (hasVol && isNum(c.v)) { const pv = cs[k - 1]?.v; row('거래량', `${volText(c.v)}${isNum(pv) && pv > 0 ? ` (전날의 ${fmt(c.v / pv, 2)}배)` : ''}`, isNum(c.o) && c.c < c.o ? 'var(--down)' : 'var(--up)'); }
    ma.forEach(line => row(line.label, p(line.values[k]), line.color));
    const e = byDate.get(c.d);
    if (e) row('신호', `${KIND[e.type]?.[0] || ''} · 종가 위치 ${fmt(e.closePos, 0)}%`, e.type === 'BOTTOM' ? 'var(--green)' : 'var(--red)');
    if (zone.has(c.d)) row('구간', '클라이맥스 런 조건 충족', 'var(--amber)');
    tip.hidden = false;
    const left = x(k) / W * box.width;
    tip.style.left = `${left > box.width * 0.6 ? left - tip.offsetWidth - 12 : left + 12}px`;
  };
  hit.addEventListener('pointermove', show); hit.addEventListener('pointerdown', show);
  hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });
  // 범례
  const lg = el('div', null, 'pc-legend small');
  const key = (cls, label) => { const k = el('span', null, `pc-key ${cls}`); k.append(el('i'), el('b', label)); lg.append(k); };
  ma.forEach(line => { const k = el('span', null, 'pc-key'); const i = el('i'); i.style.background = line.color; k.append(i, el('b', line.label)); lg.append(k); });
  if (hasVol) { key('cx-key-volup', '거래량 · 양봉'); key('cx-key-voldown', '거래량 · 음봉'); key('cx-key-volavg', '50일 평균 거래량'); }
  key('cx-key-warning', '▼ 천장 경고 (클라이맥스 + 고점 찍고 밀림)'); key('cx-key-top', '▼ 천장형 반전 (고점 찍고 밀림)'); key('cx-key-bottom', '▲ 바닥형 반전 (저점 깨고 말아 올림)'); key('cx-key-zone', '맨 위 노란 띠 = 클라이맥스 런 (끝물 급등) 구간');
  lg.append(el('span', `최근 ${days}거래일 · 오른쪽 숫자는 지수`, 'pc-hint'));
  wrap.append(lg);
  return wrap;
}

function volText(v) {
  return v >= 1e8 ? `${fmt(v / 1e8, 1)}억` : v >= 1e4 ? `${fmt(v / 1e4, 0)}만` : fmt(v, 0);
}

function pointList(points, date) {
  const ul = el('ul', null, 'tr-reasons');
  (points || []).forEach(p => { const li = el('li'); li.append(el('b', p.label), el('span', p.detail)); ul.append(li); });
  if (!ul.children.length) { const li = el('li'); li.append(el('b', '기본 조건만 충족'), el('span', `${date ? md(date) + ' ' : ''}추가로 붙은 강도 점수는 없어요.`)); ul.append(li); }
  return ul;
}

function indexCard(rep, market, b) {
  const card = el('article', null, 'tr-card cx-index');
  if (rep.status !== 'OK') { card.append(el('h3', rep.label), el('p', '지수 자료가 없어 계산하지 못했어요.')); return card; }
  const [, cls, text, headline, icon] = STATE[rep.state] || STATE.NONE;
  const head = el('div', null, 'cx-index-head');
  head.append(el('h3', `${rep.label} · ${md(rep.asOf)} 종가`));
  // 큰 상태 막대: 있었는지 없었는지가 먼저 보이게
  const banner = el('div', null, `cx-banner ${cls}`);
  const line = el('div', null, 'cx-banner-line'); line.append(el('span', icon, 'cx-banner-icon'), el('strong', headline));
  banner.append(line, el('p', text));
  card.append(head, banner);
  // 같은 지수의 분산일을 바로 옆에서 함께 봄 (시장 날씨와 같은 숫자)
  const dist = b?.index?.symbol === rep.symbol ? b?.weather?.distribution : b?.index2?.symbol === rep.symbol ? b.index2.distribution : null;
  if (dist?.status === 'OK') {
    const box = el('div', null, `cx-dist ${dist.count >= 6 ? 'bad' : dist.count >= 4 ? 'mid' : 'good'}`);
    box.append(el('span', '분산일'), el('b', `${dist.count}번`), el('small', `최근 25거래일${isNum(dist.score) ? ` · 위험 점수 ${fmt(dist.score, 1)}` : ''} · ${dist.count >= 6 ? '많음' : dist.count >= 4 ? '주의' : '적음'}`));
    card.append(box);
  }
  // 셀링 클라이맥스(급락 + 1년 최대 거래대금): 바닥 후보. 시장 거래대금 카드와 같은 숫자.
  const sc = (b?.liquidity?.groups || []).find(g => g.symbol === rep.symbol)?.sellingClimax || null;
  if (sc) {
    const box = el('div', null, 'cx-dist lead');
    box.append(el('span', '셀링 클라이맥스'), el('b', `${md(sc.date)} ${pct(sc.changePct)}`), el('small', `급락 + 1년 최대 거래량(50일 평균의 ${fmt(sc.valueVsAvg50, 1)}배) · 그 뒤 ${pct(sc.sinceThenPct)} · 바닥 후보 · FTD를 기다리는 중`));
    card.append(box);
  }
  const now = [];
  if (rep.topWarning) now.push(`${md(rep.topWarning.date)} 천장형 반전 · ${md(rep.topWarning.runStart)}부터 ${pct(rep.topWarning.gainPct)} 급등 뒤`);
  if (rep.climax) now.push(`클라이맥스 런: ${md(rep.climax.runStart)}부터 ${pct(rep.climax.gainPct)} · 고점 대비 ${pct(rep.climax.fromPeakPct)}`);
  (rep.reversals || []).slice(-2).forEach(r => now.push(`${md(r.date)} ${KIND[r.kind][0]} · 종가 위치 ${fmt(r.closePos, 0)}% · ${pct(r.changePct)}`));
  if (now.length) { const ul = el('ul', null, 'cx-now'); now.forEach(t => ul.append(el('li', t))); banner.append(ul); }
  card.append(indexChart(rep, market));
  // 지난 1년 기록
  const ev = rep.events || [];
  const tops = ev.filter(e => e.type === 'TOP_WARNING' && isNum(e.worst20));
  const hurt = tops.filter(e => e.worst20 <= -5).length;
  const box = el('details', null, 'cx-history');
  box.append(el('summary', ev.length ? `지난 1년 신호 기록 ${ev.length}건${tops.length ? ` · 천장 경고 ${tops.length}번 중 ${hurt}번은 20거래일 안에 5% 넘게 빠졌어요` : ''}` : '지난 1년 신호 기록 없음'));
  if (ev.length) {
    const t = el('table', null, 'cx-table'), h = el('tr');
    ['날짜', '신호', '종가 위치', '5일 뒤', '20일 뒤', '20일 최저'].forEach(s => h.append(el('th', s)));
    t.append(h);
    ev.forEach(e => {
      const tr = el('tr', null, `cx-ev ${e.type.toLowerCase()}`);
      const name = KIND[e.type]?.[1] || e.type;
      tr.append(el('td', e.date.slice(2).replaceAll('-', '.')), el('td', `${name}${e.strong ? ' · 강' : ''}${e.type === 'CLIMAX' ? ` (${pct(e.gainPct)})` : ''}`),
        el('td', isNum(e.closePos) ? `${fmt(e.closePos, 0)}%` : '—'),
        el('td', pct(e.after?.['5']), tone(e.after?.['5'])), el('td', pct(e.after?.['20']), tone(e.after?.['20'])),
        el('td', pct(e.worst20), tone(e.worst20)));
      tr.title = (e.points || []).map(p => `${p.label}: ${p.detail}`).join('\n');
      t.append(tr);
    });
    const scroll = el('div', null, 'cx-table-wrap'); scroll.append(t); box.append(scroll);
    box.append(el('p', '줄에 마우스를 올리면 그날 어떤 조건이 겹쳤는지 보여요. 클라이맥스는 새 구간이 시작된 첫날만 적어요.', 'tr-help'));
  }
  card.append(box);
  return card;
}

// ------------------------------------------------------------ 종목 목록
function miniCandles(candles, mark) {
  const W = 168, H = 64, pad = 9, n = candles.length;
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'cx-strip', 'aria-hidden': 'true' });
  const v = candles.flatMap(c => [c.high, c.low]).filter(isNum); if (!v.length) return svg;
  const lo = Math.min(...v), hi = Math.max(...v), slot = W / n;
  const y = val => pad + (hi - val) / (hi - lo || 1) * (H - pad * 2);
  candles.forEach((c, k) => {
    if (!isNum(c.high) || !isNum(c.low) || !isNum(c.close)) return;
    const cx = slot * (k + 0.5), prev = candles[k - 1]?.close;
    const down = isNum(c.open) ? c.close < c.open : isNum(prev) && c.close < prev;
    if (k === mark) {
      node('rect', { x: slot * k, y: 0, width: slot, height: H, class: 'cx-strip-mark' }, svg);
      node('path', { d: `M${cx},${y(c.high) - 2} l-3.5,-5 h7 z`, class: 'cx-mark top' }, svg);
    }
    // 고가~저가 막대 + 종가 눈금(오른쪽). 종목 이력에는 시가가 없어 몸통 대신 종가만 표시
    node('line', { x1: cx, x2: cx, y1: y(c.high), y2: y(c.low), class: `cx-wick bar ${down ? 'down' : 'up'}` }, svg);
    node('line', { x1: cx, x2: cx + slot * 0.38, y1: y(c.close), y2: y(c.close), class: `cx-wick bar ${down ? 'down' : 'up'}` }, svg);
  });
  return svg;
}

let filter = null;
function stockSection(c, openStock, rerender) {
  const wrap = el('div', null, 'cx-stocks');
  const counts = c.counts || {};
  const options = [['TOP_WARNING', counts.TOP_WARNING || 0], ['CLIMAX', counts.CLIMAX || 0], ['TOP', counts.TOP || 0], ['BOTTOM', counts.BOTTOM || 0]];
  if (!filter || !options.some(([k]) => k === filter)) filter = (options.find(([, n]) => n > 0) || ['TOP_WARNING'])[0];
  const head = el('div', null, 'cx-stock-head');
  head.append(el('h3', '종목 · 지금 걸린 신호'));
  const seg = el('div', null, 'tr-seg cx-seg');
  options.forEach(([k, n]) => seg.append(btn(`${KIND[k][1]} ${n}`, filter === k, () => { filter = k; rerender(); }, `cx-f-${k.toLowerCase()}`)));
  head.append(seg);
  wrap.append(head);
  const items = (c.items || []).filter(x => (x.kinds || []).includes(filter));
  if (!items.length) { wrap.append(el('div', `지금은 ${KIND[filter][0]} 종목이 없어요.`, 'empty')); return wrap; }
  const list = el('div', null, 'tr-list');
  items.forEach(x => {
    const row = el('article', null, `tr-row cx-row kind-${x.kind.toLowerCase()}`);
    const main = el('div', null, 'tr-row-main');
    const name = btn('', false, () => openStock(x.id), 'stock-link tr-stock'); name.append(el('strong', x.name), el('small', `${x.symbol} · ${x.industry || ''}`));
    const tags = el('div', null, 'cx-tags');
    (x.kinds || []).forEach(k => tags.append(el('span', KIND[k][1], `tr-tag ${KIND[k][2]}`)));
    main.append(name, tags);
    const facts = el('div', null, 'tr-facts');
    const cl = x.climax, rv = x.reversal;
    [['종가 변화', pct(x.changePct), tone(x.changePct)],
      ['급등폭', cl ? pct(cl.gainPct) : '—'],
      ['50일선 이격', cl && isNum(cl.ext50) ? pct(cl.ext50) : '—'],
      [rv ? '반전일' : '고점 대비', rv ? (x.daysAgo === 0 ? '오늘' : `${x.daysAgo}일 전`) : cl ? pct(cl.fromPeakPct) : '—', rv ? '' : tone(cl?.fromPeakPct)]]
      .forEach(([k, v, cls]) => { const f = el('span'); f.append(el('small', k), el('b', v, cls)); facts.append(f); });
    const side = el('div', null, 'tr-side cx-side');
    const why = el('div', null, 'cx-why');
    if (cl) { why.append(el('p', `클라이맥스 런 · ${md(cl.runStart)}부터 ${pct(cl.gainPct)}`, 'cx-why-title')); why.append(pointList(cl.points)); }
    if (rv) {
      why.append(el('p', `${KIND[rv.kind][0]}${rv.strong ? ' · 강' : ''} · ${md(rv.date)} · 종가 위치 ${fmt(rv.closePos, 0)}% · ${pct(rv.changePct)}`, 'cx-why-title'));
      why.append(pointList(rv.points, rv.date));
    }
    const strip = el('figure', null, 'cx-strip-wrap');
    const candles = x.recentCandles || [];
    const mark = rv ? candles.length - 1 - x.daysAgo : -1;
    strip.append(miniCandles(candles, mark), el('figcaption', `최근 ${candles.length}거래일 · 막대 = 고가~저가, 옆 눈금 = 종가${rv ? ' · ▼ = 반전일' : ''}`));
    side.append(why, strip);
    row.append(main, facts, side);
    list.append(row);
  });
  wrap.append(list);
  return wrap;
}

export const climaxCount = b => {
  const c = b?.climax; if (!c || c.status !== 'OK') return '—';
  const idx = (c.indexes || []).some(r => r.state === 'TOP_WARNING');
  return `${idx ? '! ' : ''}${c.counts?.TOP_WARNING ?? 0}`;
};

// 읽는 법: 이 칸의 목적(클라이맥스 런 잡기)과 용어를 그림과 쉬운 말로 늘 펼쳐 둔다.
function guideIcon(kind) {
  const svg = node('svg', { viewBox: '0 0 44 40', class: 'cx-guide-icon', 'aria-hidden': 'true' });
  const candle = (x, hi, lo, open, close) => {
    const down = close > open;   // y grows downward: bigger y = lower price
    node('line', { x1: x, x2: x, y1: hi, y2: lo, class: `cx-wick ${down ? 'down' : 'up'}` }, svg);
    node('rect', { x: x - 3, y: Math.min(open, close), width: 6, height: Math.max(1.5, Math.abs(close - open)), class: `cx-body ${down ? 'down' : 'up'}` }, svg);
  };
  if (kind === 'climax') { [[6, 30, 36, 35, 31], [14, 25, 32, 31, 26], [22, 17, 27, 26, 18], [30, 8, 19, 18, 9], [38, 2, 10, 9, 3]].forEach(a => candle(...a)); node('rect', { x: 1, y: 0, width: 42, height: 3, class: 'cx-zone' }, svg); }
  if (kind === 'top') { candle(12, 14, 26, 24, 16); candle(26, 4, 30, 18, 27); node('line', { x1: 2, x2: 42, y1: 13, y2: 13, class: 'cx-guide-level' }, svg); }
  if (kind === 'warning') { [[6, 30, 36, 35, 31], [14, 20, 30, 29, 21], [22, 9, 21, 20, 10]].forEach(a => candle(...a)); candle(34, 2, 26, 9, 22); node('rect', { x: 1, y: 0, width: 26, height: 3, class: 'cx-zone' }, svg); }
  if (kind === 'bottom') { candle(12, 12, 24, 14, 22); candle(26, 10, 36, 22, 13); node('line', { x1: 2, x2: 42, y1: 25, y2: 25, class: 'cx-guide-level' }, svg); }
  return svg;
}

// 종목에서 일중전환을 쓰는 법 + 이 시장 성적표(5거래일 뒤) 숫자는 그때그때 자료에서 읽는다.
function stockUse(b) {
  const sec = el('section', null, 'cx-guide-use');
  sec.append(el('h4', '종목에서 일중전환 쓰는 법'));
  sec.append(el('p', '일중전환은 지수보다 종목에서 덜 맞아요. 종목은 하루 변동이 크고, 실적·뉴스 갭이 캔들 모양을 흐트러뜨리기 때문이에요. 그래서 종목에서는 일중전환 하나만 보고 팔지 않고, 아래 조건이 겹칠 때 무게를 크게 둬요.'));
  const ol = el('ol', null, 'cx-guide-checks');
  ['이미 많이 올랐어요 (클라이맥스 런)', '거래량이 평소보다 많아요', '종가가 그날 저가 근처에서 끝났어요', '다음 날 그 저가를 깨요'].forEach(t => ol.append(el('li', t)));
  sec.append(ol);
  const sig = b.scorecard?.signals || {}, stat = k => sig[k]?.stats?.['5'];
  const line = (name, st) => st?.n ? `${name} ${st.n}건 · 5거래일 뒤 떨어진 비율 ${fmt(st.hitRate, 0)}% · 지수보다 ${isNum(st.avgExcess) ? `${st.avgExcess > 0 ? '+' : ''}${fmt(st.avgExcess, 1)}%p` : '—'}` : null;
  const rows = [line('일중전환(천장형)만', stat('revTop')), line('천장 경고 (클라이맥스 뒤 일중전환)', stat('climaxTop'))].filter(Boolean);
  if (rows.length) {
    const box = el('div', null, 'cx-guide-stats');
    box.append(el('b', `이 시장 종목 기록 · ${b.scorecard?.since ? `${b.scorecard.since.slice(2).replaceAll('-', '.')}부터` : ''}`));
    rows.forEach(t => box.append(el('p', t)));
    box.append(el('small', '‘지수보다’가 마이너스면 같은 기간 지수보다 더 빠졌다는 뜻 · 기록이 짧아 방향만 참고하세요'));
    sec.append(box);
  }
  return sec;
}

function guide(b) {
  const box = el('details', null, 'cx-guide');
  // 펼침이 기본, 접어 두면 이 브라우저에서는 계속 접힌 채로 (기억 못 해도 화면은 정상)
  let closed = false; try { closed = localStorage.getItem('wamo.cxGuideClosed') === '1'; } catch (e) { /* storage off */ }
  box.open = !closed;
  box.addEventListener('toggle', () => { try { localStorage.setItem('wamo.cxGuideClosed', box.open ? '0' : '1'); } catch (e) { /* storage off */ } });
  box.append(el('summary', '읽는 법 · 이 칸은 클라이맥스 런을 잡으려고 만든 곳이에요'));
  box.append(el('p', '오르던 종목이나 지수가 마지막에 수직으로 치솟는 구간(클라이맥스 런)은 상승의 끝물인 경우가 많아요. 그 구간을 먼저 잡고, 그 뒤에 "고점을 찍고 밀린 날"이 나오는지 지켜보는 게 이 칸의 목적이에요. 일중전환은 하루 안에 방향이 뒤집힌 날을 말해요. 장중에 새 고점(또는 새 저점)을 찍었다가 반대쪽으로 끝난 날이에요.', 'cx-guide-lead'));
  const rows = [
    ['climax', 'warn', '🔥 클라이맥스 런', '끝물 급등', '짧은 기간에 수직으로 치솟은 구간이에요. 종목은 15거래일 안에 +25% 넘게(지수는 +12%) 오르고, 하루 최대 상승 · 쉬지 않는 상승 · 빈칸 갭 · 이평선과 크게 벌어짐 · 1년 최대 거래량 같은 끝물 징후가 겹친 경우예요.', '새로 사지 않고, 오를 때 조금씩 나눠 팔기 시작할 자리'],
    ['top', 'warn', '▼ 천장형 반전 (일중전환)', '고점 찍고 밀린 날', '장중에는 최근 50거래일 최고가를 새로 넘었는데, 종가는 전날보다 낮고 그날 범위의 아래쪽 절반에서 끝난 날이에요. 사려는 힘보다 파는 힘이 이겼다는 흔적이에요.', '혼자 나오면 약한 신호 · 클라이맥스 뒤에 나오면 강한 신호'],
    ['warning', 'bad', '⚠ 천장 경고', '클라이맥스 + 고점 찍고 밀림', '클라이맥스 런이 나온 뒤 10거래일 안에 천장형 반전이 나온 경우예요. 두 신호가 겹쳐서 상승이 끝났을 가능성을 가장 강하게 의심하는 자리예요.', '남은 물량 정리를 검토할 자리 (가장 강한 매도 경고)'],
    ['bottom', 'lead', '▲ 바닥형 반전 (일중전환)', '저점 깨고 말아 올린 날', '장중에는 최근 20거래일 최저가를 깼는데, 종가는 전날보다 높고 그날 범위의 위쪽에서 끝난 날이에요. 파는 힘이 바닥났을 수 있다는 흔적이에요.', '하락이 끝나 가는지 확인하는 용도 · 이것만 보고 사지는 않기'],
  ];
  const grid = el('div', null, 'cx-guide-grid');
  rows.forEach(([icon, tone, name, short, text, act]) => {
    const card = el('div', null, `cx-guide-card ${tone}`);
    const head = el('div', null, 'cx-guide-head'); const t = el('div'); t.append(el('strong', name), el('span', short, 'cx-guide-short'));
    head.append(guideIcon(icon), t);
    card.append(head, el('p', text), el('p', `→ ${act}`, 'cx-guide-act'));
    grid.append(card);
  });
  box.append(grid);
  box.append(stockUse(b));
  const more = el('p', null, 'tr-help'); more.append('밑줄 친 말을 누르면 정확한 숫자 기준이 나와요: ', term('cx_climaxRun'), ' · ', term('cx_reversalTop'), ' · ', term('cx_topWarning'), ' · ', term('cx_reversalBottom'), ' · ', term('cx_closePos'));
  box.append(more);
  return box;
}

export function climaxSection(b, market, openStock, rerender) {
  const c = b.climax, wrap = el('div', null, 'cx');
  if (!c || c.status !== 'OK') { wrap.append(el('div', c?.note || '천장 신호 자료는 다음 자동 갱신 때 채워져요.', 'empty')); return wrap; }
  wrap.append(guide(b));
  const grid = el('div', null, 'cx-index-grid');
  (c.indexes || []).forEach(rep => grid.append(indexCard(rep, market, b)));
  if ((c.indexes || []).length) wrap.append(grid);
  wrap.append(stockSection(c, openStock, rerender));
  wrap.append(el('p', c.note || '', 'tr-help'));
  wrap.append(glossary(TERMS, Object.keys(TERMS)));
  return wrap;
}
