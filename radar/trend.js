import { loadChartData, stockSeries, priceChart, lineChart, mirrorBars, MA } from './chart.js?v=20261020-asia1';
import { climaxSection, climaxCount } from './climax.js?v=20261022-lq2';
// Trend-following view: market weather, RS-line leaders, sell warnings, climax/reversal tops,
// earnings dates and the signal scorecard. Reads wamo_trend.json only.
const LIGHT = { GREEN: '공격 가능', YELLOW: '조심', RED: '현금 비중 확대', UNKNOWN: '판단 보류' };
// [key, full label, short label for phones]
const SUBTABS = [['weather', '시장 날씨', '날씨'], ['climax', '천장 신호', '천장'], ['rs', 'RS선 선행 신고가', 'RS선'], ['sell', '팔 때 경고', '팔 때'], ['earnings', '실적발표 임박', '실적'], ['score', '신호 성적표', '성적표']];
const SUB_KEYS = SUBTABS.map(([k]) => k);
let data = null, loading = null, failed = false, sellFilter = 'HIGH';
let subtab = SUB_KEYS.includes(new URLSearchParams(location.search).get('sub')) ? new URLSearchParams(location.search).get('sub') : 'weather';
export const getSubtab = () => subtab;
export function setSubtab(key) { if (SUB_KEYS.includes(key)) subtab = key; }

const isNum = n => typeof n === 'number' && Number.isFinite(n);
const fmt = (n, d = 1) => isNum(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = n => isNum(n) ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—';
const tone = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function btn(text, active, action, cls = '') { const b = el('button', text, `${active ? 'active ' : ''}${cls}`.trim()); b.type = 'button'; b.onclick = action; return b; }
const block = market => data?.markets?.[market] || null;
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;

export function loadTrend(force = false) {
  if (loading) return loading;
  if (data && !force) return Promise.resolve(data);
  loading = fetch('wamo_trend.json', { cache: 'no-store' })
    .then(r => { if (!r.ok) throw Error('HTTP'); return r.json(); })
    .then(json => { if (json && json.markets) { data = json; failed = false; } return data; })
    .catch(() => { failed = true; return data; })
    .finally(() => { loading = null; });
  return loading;
}

export function trendTabLabel(market) {
  const b = block(market);
  return b ? (LIGHT[b.weather?.level] || '—') : failed ? '대기' : '…';
}

// Same order and weights as wamo_ftd.DIST_RULE (heavy 1.5 · normal 1 · light 0.5 · hidden 1).
const DIST_KINDS = [['HEAVY', '꽉 찬 음봉', 1.5], ['NORMAL', '보통', 1], ['LIGHT', '긴 아랫꼬리', 0.5], ['HIDDEN', '위장(윗꼬리)', 1]];
const DIST_SHORT = { HEAVY: '꽉 찬 음봉', NORMAL: '보통', LIGHT: '긴 아랫꼬리', HIDDEN: '위장 분산일' };
const DIST_NAME = { HEAVY: '꽉 찬 음봉 · 위험', NORMAL: '보통 분산일', LIGHT: '긴 아랫꼬리 · 저가에서 회복 · 덜 위험', HIDDEN: '위장 분산일 · 올랐지만 아래쪽 마감' };

// Tiny candle for one day with its volume underneath.
// Top: wick = high~low, body = open~close (green = 양봉 close above open, red = 음봉).
// Bottom: today's volume bar under the candle and the day before as a faint bar on the left,
// each coloured by its own candle. Older stored days may lack the open: then the wick, a close
// tick and grey volume bars are drawn.
function miniCandle(x, kind) {
  const SVGNS = 'http://www.w3.org/2000/svg', W = 24, CH = 26, VT = 30, VH = 12, H = VT + VH, pad = 2, cx = 16;
  const svg = document.createElementNS(SVGNS, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'tr-candle');
  svg.setAttribute('aria-hidden', 'true');
  const add = (tag, attrs) => { const n = document.createElementNS(SVGNS, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); svg.append(n); return n; };
  const dir = c => c && isNum(c.open) && isNum(c.close) ? (c.close < c.open ? 'down' : 'up') : 'flat';
  const c = x.candle;
  if (c && isNum(c.high) && isNum(c.low) && isNum(c.close) && c.high > c.low) {
    const y = v => pad + (c.high - v) / (c.high - c.low) * (CH - pad * 2);
    add('line', { x1: cx, y1: y(c.high), x2: cx, y2: y(c.low), class: 'tr-candle-wick' });
    if (isNum(c.open)) {
      const top = y(Math.max(c.open, c.close)), bottom = y(Math.min(c.open, c.close));
      add('rect', { x: cx - 4.5, y: top, width: 9, height: Math.max(1.5, bottom - top), class: `tr-candle-${dir(c)}` });
    } else add('line', { x1: cx, y1: y(c.close), x2: cx + 6, y2: y(c.close), class: 'tr-candle-close' });
  }
  if (isNum(x.volume) && x.volume > 0) {
    const max = Math.max(x.volume, isNum(x.prevVolume) ? x.prevVolume : 0);
    const bar = (v, x0, w, cls) => { const h = Math.max(1, v / max * VH); add('rect', { x: x0, y: H - h, width: w, height: h, class: cls }); };
    add('line', { x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5, class: 'tr-vol-base' });
    if (isNum(x.prevVolume) && x.prevVolume > 0) bar(x.prevVolume, 2, 6, `tr-vol tr-vol-${dir(x.prevCandle)} prev`);
    bar(x.volume, cx - 4.5, 9, `tr-vol tr-vol-${dir(c)}`);
  }
  svg.classList.add(kind);
  return svg;
}

function help(text) { const s = el('p', text, 'tr-help'); return s; }

function stockName(item, openStock) {
  const b = btn('', false, () => openStock(item.id), 'stock-link tr-stock');
  b.append(el('strong', item.name), el('small', `${item.symbol} · ${item.industry || ''}`));
  return b;
}

// ------------------------------------------------------------ weather strip
export function renderWeather(container, market, onOpen) {
  if (!container) return;
  container.replaceChildren();
  const b = block(market);
  if (!b) {
    container.hidden = !failed && !data;
    if (failed || data) {
      container.className = 'tr-weather level-unknown';
      container.append(el('span', null, 'tr-dot'), el('div', '시장 날씨 자료를 준비하고 있어요. 다음 자동 갱신 때 채워집니다.', 'tr-weather-text'));
    }
    return;
  }
  const w = b.weather || {};
  container.hidden = false;
  container.className = `tr-weather level-${(w.level || 'UNKNOWN').toLowerCase()}`;
  const head = el('div', null, 'tr-weather-head');
  head.append(el('span', null, 'tr-dot'), el('span', '시장 날씨', 'tr-weather-kicker'), el('strong', w.label || '판단 보류'));
  const text = el('div', w.advice || '', 'tr-weather-text');
  const chips = el('div', null, 'tr-chips');
  const dist = w.distribution || {}, br = w.breadth || {}, hl = w.highsLows || {}, idx = b.index || {};
  const score = key => (w.factors || []).find(f => f.key === key)?.score;
  const VERDICT = { 1: '좋음', 0: '보통', '-1': '나쁨' };
  const chip = (label, full, short, sc) => {
    const c = el('span', null, `tr-chip ${sc > 0 ? 'good' : sc < 0 ? 'bad' : sc === 0 ? 'mid' : ''}`);
    c.title = `${label}: ${full}${sc == null ? '' : ` (${VERDICT[sc]})`}`;
    c.setAttribute('aria-label', c.title);
    c.append(el('i', null, 'tr-chip-dot'), el('small', label), el('b', full, 'v-full'), el('b', short, 'v-short'));
    chips.append(c);
  };
  const idx2 = b.index2, dist2 = idx2?.distribution;
  const arrow = d => ({ up: '↗', flat: '→', down: '↘' })[d] || '·';
  const si = score('index'), ss = score('slope');
  chip('지수', [idx, idx2].filter(Boolean).map(i => `${i.label} ${pct(i.changePct)}`).join(' · '),
    { 1: '이평선 위', 0: '50일선 아래', '-1': '200일선 아래' }[si] ?? '—', si);
  chip('기울기', [idx, idx2].filter(Boolean).map(i => `${idx2 ? i.label + ' ' : ''}50${arrow(i.ma50Dir)} 200${arrow(i.ma200Dir)}`).join(' · '),
    { 1: '상승', 0: '꺾임', '-1': '하락' }[ss] ?? '—', ss);
  const dok = [dist, dist2].filter(d => d?.status === 'OK');
  const dcount = dok.map(d => d.count);
  const riskNote = dok.some(d => isNum(d.score) && d.score !== d.count) ? ` · 위험 점수 ${dok.map(d => fmt(d.score, 1)).join(' · ')}` : '';
  chip('분산일', dcount.length ? `${dcount.join(' · ')} / 25일${riskNote}` : '자료 없음', dcount.length ? `${dcount.join('·')}` : '—', score('distribution'));
  chip('50일선 위', br.status === 'OK' ? `${fmt(br.above50Pct, 0)}% 종목` : '—', br.status === 'OK' ? `${fmt(br.above50Pct, 0)}%` : '—', score('breadth'));
  // Turnover verdict: a chip on wide screens; on phones it rides on the advice line so the strip keeps two chip rows.
  const lq = b.liquidity;
  if (lq?.status === 'OK' && lq.groups?.length) {
    const worst = lq.groups.reduce((a, g) => (g.score < a.score ? g : a), lq.groups[0]);
    const SHORT = { HEALTHY: '건강', THIN: '힘 빠짐', DISTRIBUTION: '분산', EXHAUSTION: '소진', FLAT: '보합' };
    if (window.matchMedia('(max-width:760px)').matches) text.append(el('span', ` 거래량: ${lq.groups.map(g => `${g.label} ${SHORT[g.trend.key] || g.trend.label}`).join(' · ')}`, 'tr-weather-lq'));
    else {
      chip('거래량', lq.groups.map(g => `${g.label} ${SHORT[g.trend.key] || g.trend.label}`).join(' · '), SHORT[worst.trend.key] || worst.trend.label, score('liquidity'));
      chips.lastChild.title = `거래량: ${lq.summary}`;
    }
  }
  chip('신고·신저', hl.status === 'OK' ? `${hl.newHighs} · ${hl.newLows}` : '—', hl.status === 'OK' ? `${hl.newHighs}·${hl.newLows}` : '—', score('highsLows'));
  const more = btn('자세히', false, onOpen, 'tr-more');
  more.setAttribute('aria-label', '시장 날씨 자세히 보기');
  head.append(more);
  container.append(head, text, chips);
}

// --------------------------------------------------------------- metrics
export function renderTrendMetrics(container, market) {
  const b = block(market);
  container.replaceChildren();
  const add = (label, value, sub) => { const box = el('div', null, 'metric'); box.append(el('span', label), el('strong', value, /^[\d—.,%+-]+$/.test(value) ? null : 'metric-word'), el('small', sub)); container.append(box); };
  if (!b) { add('시장 날씨', '—', '자료 준비 중'); return; }
  add('시장 날씨', LIGHT[b.weather?.level] || '—', `점수 ${b.weather?.score ?? '—'} · ${b.asOf} 종가`);
  add('RS선 선행 신고가', `${b.rsLeaders?.leadingCount ?? 0}`, `RS선 신고가 전체 ${b.rsLeaders?.count ?? 0}종목`);
  add('강한 경고', `${b.sellWarnings?.highCount ?? 0}`, `주의 포함 ${b.sellWarnings?.count ?? 0}종목`);
  const up = b.earnings?.upcoming || [];
  add('실적발표 14일 내', b.earnings?.status === 'OK' ? `${b.earnings.total ?? up.length}` : '—', b.earnings?.status === 'OK' ? `추세 종목 ${up.filter(x => x.inTrend).length}` : ({KR:'한국',JP:'일본',TW:'대만'})[market] ? `${({KR:'한국',JP:'일본',TW:'대만'})[market]}은 미제공` : '일정 자료 대기');
}

// ------------------------------------------------------------ detail view
function weatherDetail(b, market) {
  const w = b.weather || {}, wrap = el('section', null, 'tr-weather-detail');
  const factors = el('div', null, 'tr-factor-list');
  (w.factors || []).forEach(f => {
    const row = el('div', null, `tr-factor ${f.score > 0 ? 'good' : f.score < 0 ? 'bad' : ''}`);
    row.append(el('span', f.score > 0 ? '좋음' : f.score < 0 ? '나쁨' : '보통', 'tr-factor-tag'), el('strong', f.label), el('span', f.text));
    factors.append(row);
  });
  const grid = el('div', null, 'tr-cards');
  // Index
  const idx = b.index || {}, idx2 = b.index2, c1 = el('article', null, 'tr-card');
  c1.append(el('h3', idx2 ? `지수 추세 (${idx.label} · ${idx2.label})` : '지수 추세'));
  [idx, idx2].filter(Boolean).forEach(i => {
    const line = el('div', null, 'tr-index-line');
    const big = el('div', null, 'tr-big'); big.append(el('b', fmt(i.close, 2)), el('span', pct(i.changePct), tone(i.changePct)));
    const word = d => ({ up: '↗ 상승', flat: '→ 평평', down: '↘ 하락' })[d] || '—';
    const sl = (v, d, back) => d ? `${word(d)} (${v > 0 ? '+' : ''}${fmt(v)}% / ${back}일)` : '계산 불가';
    line.append(el('small', i.label), big,
      el('p', `가격: 50일선 ${i.aboveMa50 ? '위' : '아래'} · 200일선 ${i.aboveMa200 ? '위' : '아래'}`),
      el('p', `기울기: 50일선 ${sl(i.ma50Slope, i.ma50Dir, 10)} · 200일선 ${sl(i.ma200Slope, i.ma200Dir, 20)}`, 'tr-slope'));
    c1.append(line);
  });
  c1.append(help(idx.synthetic ? '지수 자료를 받지 못해 화면 속 종목들의 중앙값으로 만든 대체 지수예요.' : `가격: ${idx2 ? '두 지수 모두 50·200일선 위면 좋음, 하나라도 200일선 아래면 나쁨' : '50일선과 200일선 위에 있으면 좋음, 200일선 아래면 나쁨'}. 기울기: 50일선은 10일, 200일선은 20일 동안 얼마나 변했는지로 봐요 (50일선 ±0.5%, 200일선 ±0.3%보다 작으면 평평).`));
  // Distribution days
  const dist = w.distribution || {}, c2 = el('article', null, 'tr-card');
  c2.append(el('h3', '분산일 (기관이 판 흔적)'));
  const distRows = [[idx.label, dist], [idx2?.label, idx2?.distribution]].filter(([, d]) => d?.status === 'OK');
  if (distRows.length) {
    distRows.forEach(([label, d]) => {
      const big = el('div', null, 'tr-big'); big.append(el('b', `${d.count}`), el('span', `${distRows.length > 1 ? label + ' · ' : ''}최근 25거래일`));
      if (isNum(d.score)) big.append(el('span', `위험 점수 ${fmt(d.score, 1)}`, `tr-dist-score ${d.score >= 6 ? 'bad' : d.score >= 4 ? 'mid' : 'good'}`));
      c2.append(big);
      const k = d.kinds || {};
      if (d.kinds) {
        const mix = el('div', null, 'tr-dist-mix');
        DIST_KINDS.forEach(([key, name, w]) => { const c = el('span', null, `tr-dist-chip ${key.toLowerCase()}`); c.append(el('i'), el('b', name), el('span', `${k[key] || 0}번 · ×${w}`)); mix.append(c); });
        c2.append(mix);
      }
      const dots = el('div', null, 'tr-dots');
      (d.days || []).forEach(x => {
        const kind = x.kind ? x.kind.toLowerCase() : '';
        const s = el('span', null, `tr-d ${x.distribution ? (x.expired ? 'expired' : `hit ${kind}`) : ''}`);
        s.title = `${x.date} ${pct(x.changePct)}${x.distribution ? ` · ${DIST_NAME[x.kind] || '분산일'}${isNum(x.closePos) ? ` · 종가 위치 ${x.closePos}%` : ''}${x.expired ? ' (지워짐)' : ''}` : ''}`;
        dots.append(s);
      });
      c2.append(dots);
      const days = d.days || [];
      if (days.length) { const ax = el('div', null, 'tr-dots-axis'); ax.append(el('span', md(days[0].date)), el('span', d.kinds ? '테두리만 = 지워진 날' : '빨강 = 분산일 · 테두리 = 지워진 분산일'), el('span', md(days.at(-1).date))); c2.append(ax); }
      // 지금 세고 있는 분산일: 날짜 · 등락 · 종가가 그날 범위 어디서 끝났는지
      const live = days.filter(x => x.distribution && !x.expired && x.kind);
      if (live.length) {
        const box = el('div', null, 'tr-dist-list');
        box.append(el('b', '지금 세는 분산일'), el('small', '그림: 위 = 그날 캔들, 아래 = 거래량(왼쪽 흐린 막대 = 전날) · 초록 = 양봉, 빨강 = 음봉 · 종가 위치: 0% = 그날 저가에서 끝남, 100% = 고가에서 끝남'));
        const head = el('p', null, 'tr-dist-row tr-dist-head');
        ['', '날짜', '등락', '위치', '거래량', '종류'].forEach(t => head.append(el('span', t)));
        box.append(head);
        live.slice().reverse().forEach(x => {
          const row = el('p', null, `tr-dist-row ${x.kind.toLowerCase()}`);
          row.append(miniCandle(x, (x.kind || '').toLowerCase()), el('time', md(x.date)), el('span', pct(x.changePct), tone(x.changePct)), el('span', isNum(x.closePos) ? `${x.closePos}%` : '—'), el('span', isNum(x.volumeRatio) ? `${fmt(x.volumeRatio, 2)}배` : '—', 'tr-dist-vol'), el('strong', DIST_SHORT[x.kind]));
          box.append(row);
        });
        c2.append(box);
      }
      // 지워진 분산일마다 이유를 한 줄씩 적어 둠
      const gone = days.filter(x => x.distribution && x.expired);
      if (gone.length) {
        const box = el('div', null, 'tr-expired');
        box.append(el('b', `지워진 분산일 ${gone.length}개`));
        gone.forEach(x => {
          const why = x.expiredOn
            ? `${md(x.date)} 분산일(종가 ${fmt(x.close, 2)}, ${pct(x.changePct)}) → ${md(x.expiredOn)} 종가 ${fmt(x.expiredClose, 2)}로 ${pct(x.expiredRisePct)} 올라 5% 기준을 넘어 지웠어요.`
            : `${md(x.date)} 분산일(${pct(x.changePct)}) → 그 뒤 지수가 그날 종가보다 5% 넘게 올라 지웠어요.`;
          box.append(el('p', why));
        });
        c2.append(box);
      }
    });
  } else c2.append(el('p', '자료 없음'));
  c2.append(help(dist.note || ''));
  // Breadth
  const br = w.breadth || {}, c3 = el('article', null, 'tr-card');
  c3.append(el('h3', '시장 체력 (50일선 위 종목 비율)'));
  if (br.status === 'OK') {
    const big = el('div', null, 'tr-big'); big.append(el('b', `${fmt(br.above50Pct, 0)}%`), el('span', `5일 ${br.above50Change5d > 0 ? '+' : ''}${fmt(br.above50Change5d, 0)}%p`, tone(br.above50Change5d))); c3.append(big);
    const s = br.series || [];
    c3.append(lineChart(s, { lines: [{ key: 'above50', label: '50일선 위', color: 'var(--green)' }, { key: 'above200', label: '200일선 위', color: '#9aacc3', dash: true }] }));
    const lg = el('div', null, 'pc-legend small');
    [['var(--green)', `50일선 위 ${fmt(br.above50Pct, 0)}%`, ''], ['#9aacc3', `200일선 위 ${fmt(br.above200Pct, 0)}%`, 'dash']].forEach(([c, t, cls]) => { const k = el('span', null, `pc-key ${cls}`); const i = el('i'); i.style.background = c; k.append(i, el('b', t)); lg.append(k); });
    c3.append(lg);
  } else c3.append(el('p', '자료 없음'));
  c3.append(help('60% 넘으면 건강, 40% 아래면 약한 시장이에요. 지수는 오르는데 이 비율이 떨어지면 소수 대형주만 끌고 가는 장이에요.'));
  // Highs vs lows
  const hl = w.highsLows || {}, c4 = el('article', null, 'tr-card');
  c4.append(el('h3', '신고가 vs 신저가'));
  if (hl.status === 'OK') {
    const big = el('div', null, 'tr-big'); big.append(el('b', `${hl.newHighs}`, 'up'), el('span', '·'), el('b', `${hl.newLows}`, 'down')); c4.append(big);
    c4.append(mirrorBars(hl.series || [], { upKey: 'newHighs', downKey: 'newLows', upLabel: '신고가', downLabel: '신저가' }));
    const lg = el('div', null, 'pc-legend small');
    [['var(--green)', '위: 신고가 종목 수'], ['var(--red)', '아래: 신저가 종목 수']].forEach(([c, t]) => { const k = el('span', null, 'pc-key'); const i = el('i'); i.style.background = c; k.append(i, el('b', t)); lg.append(k); });
    c4.append(lg);
  } else c4.append(el('p', '자료 없음'));
  c4.append(help('1년(252거래일) 중 가장 높은 가격을 새로 쓴 종목 수와 가장 낮은 가격을 새로 쓴 종목 수를 날짜별로 비교해요.'));
  grid.append(c1, c2, c3, c4);
  wrap.append(factors, liquidityCard(b, market), grid);
  return wrap;
}

// ------------------------------------------------------------ market turnover (liquidity)
const SVGNS = 'http://www.w3.org/2000/svg';
function node(tag, attrs = {}, parent) { const n = document.createElementNS(SVGNS, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); if (parent) parent.append(n); return n; }
function text(parent, x, y, value, attrs = {}) { const t = node('text', { x, y, ...attrs }, parent); t.textContent = value; return t; }
const TONE = { 1: 'good', 0: 'mid', '-1': 'bad' };
let lqPick = {};   // which index the card shows, per market (in-memory)

function volumeChart(series) {
  // 60 bars = index official volume as a multiple of its 50-day average (units differ by index).
  // Colour = index up/down that day; orange line = 20-day average ÷ 50-day average; dashed = 1.0 (normal).
  const narrow = window.innerWidth < 760;
  const W = narrow ? 420 : 1000, H = narrow ? 240 : 210, pad = { l: 6, r: narrow ? 46 : 58, t: 10, b: 22 }, plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const n = series.length, slot = plotW / n, bw = Math.max(2, slot * 0.66);
  const top = Math.max(2, Math.ceil(Math.max(...series.map(p => p.x50 || 0)) * 2) / 2);
  const x = k => pad.l + slot * (k + 0.5), y = v => pad.t + (1 - v / top) * plotH;
  const svg = node('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pc-svg lq-svg', role: 'img', 'aria-label': '최근 60거래일 지수 거래량 (50일 평균 대비 배수)' });
  for (let t = 0.5; t <= top + 1e-9; t += 0.5) {
    node('line', { x1: pad.l, x2: pad.l + plotW, y1: y(t), y2: y(t), class: t === 1 ? 'pc-grid lq-one' : 'pc-grid' }, svg);
    text(svg, W - pad.r + 6, y(t) + 4, `${fmt(t, 1)}배`, { class: t === 1 ? 'pc-axis lq-one-label' : 'pc-axis' });
  }
  const axisY = H - pad.b; node('line', { x1: pad.l, x2: pad.l + plotW, y1: axisY, y2: axisY, class: 'pc-baseline' }, svg);
  series.forEach((p, k) => { if (isNum(p.x50)) node('rect', { x: x(k) - bw / 2, y: y(p.x50), width: bw, height: Math.max(1, axisY - y(p.x50)), class: `lq-bar ${p.up ? 'up' : 'down'}` }, svg); });
  const pts = series.map((p, k) => isNum(p.avg20x50) ? `${x(k)},${y(p.avg20x50)}` : null).filter(Boolean);
  if (pts.length > 1) node('polyline', { points: pts.join(' '), stroke: MA.find(m => m.n === 20)?.color || '#f0a35a', class: 'pc-ma lq-avg' }, svg);
  const marks = narrow ? [0, Math.floor((n - 1) / 2), n - 1] : [0, Math.floor((n - 1) / 4), Math.floor((n - 1) / 2), Math.floor(3 * (n - 1) / 4), n - 1];
  marks.forEach((k, j) => text(svg, x(k), axisY + 15, md(series[k].date), { class: 'pc-axis', 'text-anchor': j === 0 ? 'start' : j === marks.length - 1 ? 'end' : 'middle' }));
  return svg;
}

function liquidityCard(b, market) {
  const q = b.liquidity, card = el('article', null, 'tr-card lq-card');
  card.append(el('h3', '시장 거래량 (거래소 전체 · 돈의 흐름)'));
  const groups = q?.groups || [];
  if (!q || q.status !== 'OK' || !groups.length) { card.append(el('p', q?.note || '거래량 자료는 다음 자동 갱신 때 채워져요.', 'tr-help')); return card; }
  // 1. one verdict per index, side by side — the conclusion comes first
  const verdicts = el('div', null, 'lq-verdicts');
  groups.forEach(g => {
    const v = el('div', null, `lq-verdict ${TONE[g.score]}`);
    const top = el('div', null, 'lq-verdict-top');
    top.append(el('span', g.label, 'lq-verdict-index'), el('strong', g.trend.label));
    if (g.liquidity.key !== 'NORMAL') top.append(el('span', g.liquidity.label, `lq-pill ${TONE[g.liquidity.score]}`));
    v.append(top, el('span', `최근 5일 · 지수 ${pct(g.trend.priceChange5dPct)} · 거래량 ${pct(g.trend.flowChange5dPct)}`, 'lq-verdict-sub'), el('p', g.trend.text));
    verdicts.append(v);
  });
  card.append(verdicts);
  // 2. pick an index for the detail
  const key = groups.some(g => g.key === lqPick[market]) ? lqPick[market] : groups[0].key;
  const g = groups.find(x => x.key === key);
  const detail = el('div', null, 'lq-detail');
  const headRow = el('div', null, 'lq-detail-head');
  if (groups.length > 1) {
    const seg = el('div', null, 'rail-seg lq-seg'); seg.setAttribute('aria-label', '지수 고르기');
    groups.forEach(x => { const bt = btn(x.label, x.key === key, () => { lqPick[market] = x.key; card.replaceWith(liquidityCard(b, market)); }); bt.setAttribute('aria-pressed', String(x.key === key)); seg.append(bt); });
    headRow.append(seg);
  }
  const tv = el('div', null, 'tr-big'); tv.append(el('b', `${fmt(g.today.vsAvg50, 2)}배`), el('span', `${md(g.asOf)} ${g.label} 거래량 · 50일 평균 대비 · 20일 평균의 ${fmt(g.today.vsAvg20, 2)}배${g.today.surge ? ' · 급증' : ''}`));
  headRow.append(tv);
  detail.append(headRow, volumeChart(g.series || []));
  const lg = el('div', null, 'pc-legend small');
  [['var(--green)', '지수 오른 날'], ['var(--red)', '지수 내린 날'], [MA.find(m => m.n === 20)?.color || '#f0a35a', '20일 평균'], ['#8aa0b9', '점선 1.0배 = 50일 평균']].forEach(([c, tx]) => { const k = el('span', null, 'pc-key'); const i = el('i'); i.style.background = c; k.append(i, el('b', tx)); lg.append(k); });
  lg.append(el('span', `${g.label} 공식 거래량 (거래소 전체) · 최근 ${(g.series || []).length}거래일`, 'pc-hint'));
  detail.append(lg);
  // 3. tiles: liquidity (exchange-wide), up/down money (screened stocks), selling climax
  const tiles = el('div', null, 'lq-tiles');
  const lq = g.liquidity, t2 = el('div', null, `lq-tile ${TONE[lq.score]}`);
  t2.append(el('span', '유동성 · 20일 ÷ 50일 평균 거래량', 'lq-tile-label'), el('strong', lq.label), el('small', isNum(lq.ratio20to50) ? `${fmt(lq.ratio20to50 * 100, 0)}% · 거래소 전체` : '—'), el('p', lq.text));
  tiles.append(t2);
  const ud = g.upDown;
  if (ud) {
    const udTone = isNum(ud.avg10) ? (ud.avg10 >= 1.2 ? 'good' : ud.avg10 <= 0.8 ? 'bad' : 'mid') : 'mid';
    const t1 = el('div', null, `lq-tile ${udTone}`);
    t1.append(el('span', '상승 ÷ 하락 거래대금 · 화면 속 종목', 'lq-tile-label'), el('strong', isNum(ud.today) ? `${fmt(ud.today, 2)}배` : '—'),
      el('small', `오늘 · 최근 10일 평균 ${isNum(ud.avg10) ? fmt(ud.avg10, 2) + '배' : '—'} · 돈 들어온 날 ${ud.inflowDays10}/10 · ${g.members}종목`),
      el('p', '오른 종목에 몰린 돈 ÷ 내린 종목에 몰린 돈이에요. 1보다 크면 돈이 들어온 날이에요. 지수는 오르는데 계속 1 아래면 소수 대형주만 끌고 가는 장이에요.'));
    tiles.append(t1);
  }
  if (g.sellingClimax) {
    const sc = g.sellingClimax, t3 = el('div', null, 'lq-tile lead');
    t3.append(el('span', '셀링 클라이맥스 · 바닥 후보', 'lq-tile-label'), el('strong', `${md(sc.date)} · ${pct(sc.changePct)}`),
      el('small', `그날 거래량 50일 평균의 ${fmt(sc.valueVsAvg50, 1)}배 · 1년 최대 · 그 뒤 ${pct(sc.sinceThenPct)}`),
      el('p', '급락하면서 1년 중 가장 많이 거래된 날이에요. 공포 매도가 한꺼번에 나온 흔적이라 바닥 후보로 보고, FTD가 나오는지 기다려요.'));
    tiles.append(t3);
  }
  detail.append(tiles);
  card.append(detail);
  // 4. money-flow industries (screened stocks, whole market)
  const ind = q.industries || {};
  if ((ind.inflow || []).length || (ind.outflow || []).length) {
    const flow = el('div', null, 'lq-flow');
    const col = (title, items, cls) => {
      const c = el('div', null, `lq-flow-col ${cls}`); c.append(el('b', title));
      if (!items.length) c.append(el('small', '해당 없음'));
      items.slice(0, 5).forEach(it => {
        const row = el('div', null, 'lq-flow-row');
        row.append(el('span', it.name, 'lq-flow-name'), el('span', `${it.shareChangePp > 0 ? '+' : ''}${fmt(it.shareChangePp, 1)}%p`, 'lq-flow-delta'), el('span', `비중 ${fmt(it.sharePct, 1)}%`, 'lq-flow-share'), el('span', `5일 ${pct(it.return5d)}`, tone(it.return5d)));
        row.title = it.name;
        c.append(row);
      });
      return c;
    };
    flow.append(col('돈이 몰리는 업종', ind.inflow || [], 'in'), col('돈이 빠지는 업종', ind.outflow || [], 'out'));
    card.append(el('h4', '돈이 몰리는 업종 · 최근 5일 거래대금 비중 변화 (화면 속 종목)', 'lq-sub'), flow);
    card.append(help('비중이 늘면서 값도 오르는 업종이 선도업종 후보예요. 비중은 느는데 값이 내리면 큰손이 그 업종에서 팔고 나가는 중일 수 있어요.'));
  }
  card.append(help(q.note || ''));
  return card;
}

const MINI_MA = MA.filter(m => m.n === 50 || m.n === 200);
function miniChart(item) {
  const box = el('div', null, 'tr-mini'); box.dataset.symbol = item.symbol;
  box.append(el('span', '차트 불러오는 중…', 'tr-mini-wait'));
  return box;
}
function miniLegend() {
  const lg = el('div', null, 'pc-legend small');
  [['#eaf0f8', '종가'], ...MINI_MA.map(m => [m.color, m.label])].forEach(([c, t]) => { const k = el('span', null, 'pc-key'); const i = el('i'); i.style.background = c; k.append(i, el('b', t)); lg.append(k); });
  lg.append(el('span', '최근 60거래일 · 오른쪽 숫자는 가격 · 이름을 누르면 큰 차트', 'pc-hint'));
  return lg;
}
function fillMiniCharts(content, market) {
  loadChartData(market).then(data => {
    content.querySelectorAll('.tr-mini[data-symbol]').forEach(box => {
      const series = data && stockSeries(data, box.dataset.symbol);
      box.replaceChildren(series ? priceChart(series, { market, days: 60, compact: true, mas: MINI_MA }) : el('span', '차트 자료 없음', 'tr-mini-wait'));
    });
  });
}

function emptyBox(text) { return el('div', text, 'empty'); }

function rsSection(b, openStock) {
  const r = b.rsLeaders || {}, wrap = el('div');
  wrap.append(help((r.note || '') + ' 최근 5거래일 안에 RS선 신고가를 찍은 종목이고, 표시의 "오늘/며칠 전"이 그 날짜예요.'));
  const items = r.items || [];
  if (!items.length) return wrap.append(emptyBox('오늘은 RS선 신고가 종목이 없어요.')), wrap;
  const list = el('div', null, 'tr-list');
  items.forEach(x => {
    const row = el('article', null, 'tr-row');
    const main = el('div', null, 'tr-row-main'); main.append(stockName(x, openStock));
    const when = x.rsLineHigh ? '오늘' : `${x.rsHighDaysAgo}일 전`;
    const tag = el('span', `${x.leading ? '주가보다 먼저' : '주가와 함께'} · ${when}`, `tr-tag ${x.leading ? 'lead' : ''}`);
    tag.title = `RS선 1년 신고가: ${when}`;
    const facts = el('div', null, 'tr-facts');
    [['종가 변화', pct(x.changePct), tone(x.changePct)], ['고점까지', isNum(x.highDistancePct) ? `${fmt(x.highDistancePct)}%` : '—'], ['RS 순위', fmt(x.rs, 0)], ['단계', x.stage ? `Stage ${x.stage}` : '—']]
      .forEach(([k, v, c]) => { const f = el('span'); f.append(el('small', k), el('b', v, c)); facts.append(f); });
    main.append(tag);
    row.append(main, facts, miniChart(x));
    list.append(row);
  });
  wrap.append(miniLegend(), list);
  return wrap;
}

function sellSection(b, openStock, rerender) {
  const s = b.sellWarnings || {}, wrap = el('div');
  wrap.append(help(s.note || ''));
  const filters = el('div', null, 'tr-seg');
  [['HIGH', `강한 경고 ${s.highCount ?? 0}`], ['ALL', `전체 ${s.count ?? 0}`]].forEach(([k, t]) => filters.append(btn(t, sellFilter === k, () => { sellFilter = k; rerender(); })));
  wrap.append(filters);
  const items = (s.items || []).filter(x => sellFilter === 'ALL' || x.severity === 'HIGH');
  if (!items.length) return wrap.append(emptyBox('조건에 맞는 경고가 없어요.')), wrap;
  const list = el('div', null, 'tr-list');
  items.forEach(x => {
    const row = el('article', null, `tr-row sev-${x.severity.toLowerCase()}`);
    const main = el('div', null, 'tr-row-main'); main.append(stockName(x, openStock), el('span', x.severity === 'HIGH' ? '강한 경고' : '주의', `tr-tag ${x.severity === 'HIGH' ? 'bad' : 'warn'}`));
    const facts = el('div', null, 'tr-facts');
    [['종가 변화', pct(x.changePct), tone(x.changePct)], ['고점까지', isNum(x.highDistancePct) ? `${fmt(x.highDistancePct)}%` : '—'], ['RS 순위', fmt(x.rs, 0)]]
      .forEach(([k, v, c]) => { const f = el('span'); f.append(el('small', k), el('b', v, c)); facts.append(f); });
    const reasons = el('ul', null, 'tr-reasons');
    x.reasons.forEach(r => { const li = el('li'); li.append(el('b', r.label), el('span', r.detail)); reasons.append(li); });
    const side = el('div', null, 'tr-side'); side.append(reasons, miniChart(x));
    row.append(main, facts, side);
    list.append(row);
  });
  wrap.append(miniLegend(), list);
  return wrap;
}

function earningsSection(b, openStock) {
  const e = b.earnings || {}, wrap = el('div');
  wrap.append(help(e.note || ''));
  const items = e.upcoming || [];
  if (e.status !== 'OK') return wrap.append(emptyBox(e.note || '실적 일정 자료가 없어요.')), wrap;
  if (!items.length) return wrap.append(emptyBox('앞으로 14일 안에 실적을 발표하는 화면 속 종목이 없어요.')), wrap;
  const byDate = new Map();
  items.forEach(x => { if (!byDate.has(x.date)) byDate.set(x.date, []); byDate.get(x.date).push(x); });
  const list = el('div', null, 'tr-cal');
  for (const [date, rows] of byDate) {
    const day = el('section', null, 'tr-cal-day');
    const head = el('header'); head.append(el('b', `D-${rows[0].daysUntil}`), el('span', date)); day.append(head);
    rows.sort((a, c) => Number(c.inTrend) - Number(a.inTrend)).forEach(x => {
      const row = el('div', null, `tr-cal-row ${x.inTrend ? 'trend' : ''}`);
      row.append(stockName(x, openStock), el('span', x.time, 'tr-time'));
      if (x.inTrend) row.append(el('span', '추세 종목', 'tr-tag lead'));
      day.append(row);
    });
    list.append(day);
  }
  wrap.append(list);
  if (e.fetchedAt) wrap.append(el('small', `${e.source} · 확인 ${new Date(e.fetchedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`, 'tr-legend'));
  return wrap;
}

function scoreSection(b, openStock) {
  const sc = b.scorecard || {}, wrap = el('div');
  wrap.append(help(sc.note || ''));
  if (sc.status !== 'OK') return wrap.append(emptyBox('아직 기록된 신호가 없어요. 매일 자동으로 쌓입니다.')), wrap;
  wrap.append(el('p', `${sc.since} 부터 신호 ${sc.eventCount}건 기록 · 5·20·60거래일이 지나야 해당 칸이 채워져요.`, 'tr-sub'));
  const grid = el('div', null, 'tr-score-grid');
  Object.entries(sc.signals || {}).forEach(([key, s]) => {
    const card = el('article', null, `tr-card tr-score ${s.side}`);
    card.append(el('h3', s.label), el('p', s.why, 'tr-why'));
    const t = el('table', null, 'tr-score-table'), head = el('tr');
    ['보유 기간', '건수', '평균 수익', s.side === 'sell' ? '이후 하락 비율' : '오른 비율', '지수 대비'].forEach(h => head.append(el('th', h)));
    t.append(head);
    ['5', '20', '60'].forEach(h => {
      const st = s.stats?.[h] || { n: 0 }, tr = el('tr');
      tr.append(el('td', `${h}거래일`), el('td', `${st.n}`));
      if (!st.n) tr.append(el('td', '쌓이는 중', 'muted'), el('td', '—'), el('td', '—'));
      else tr.append(el('td', pct(st.avg), tone(st.avg)), el('td', `${fmt(st.hitRate, 0)}%`), el('td', pct(st.avgExcess), tone(st.avgExcess)));
      t.append(tr);
    });
    card.append(t);
    if (s.recent?.length) {
      const rec = el('details', null, 'tr-recent'); rec.append(el('summary', `최근 신호 ${s.recent.length}건`));
      s.recent.forEach(r => {
        const line = el('div', null, 'tr-recent-row'), n = btn(r.name, false, () => openStock(r.id), 'tr-link');
        line.append(el('span', r.date.slice(5)), n, el('span', isNum(r.ret?.['5']) ? `5일 ${pct(r.ret['5'])}` : '진행 중', tone(r.ret?.['5'])));
        rec.append(line);
      });
      card.append(rec);
    }
    grid.append(card);
  });
  wrap.append(grid);
  return wrap;
}

export function renderTrend(content, market, { openStock, onSubtab }) {
  content.replaceChildren();
  const b = block(market);
  if (!b) {
    content.append(emptyBox(failed ? '추세 자료를 불러오지 못했어요. 다음 자동 갱신 뒤 다시 확인해 주세요.' : '추세 자료를 불러오는 중이에요.'));
    return;
  }
  // The sub-navigation sticks to the top; switching only redraws the body below it.
  const anchor = el('div', null, 'tr-anchor');
  const seg = el('nav', null, 'tr-subtabs'); seg.setAttribute('aria-label', '추세 신호 보기');
  const counts = {
    weather: ({ GREEN: '공격 가능', YELLOW: '조심', RED: '현금 확대' })[b.weather?.level] || '—',
    rs: b.rsLeaders?.leadingCount ?? 0,
    sell: b.sellWarnings?.highCount ?? 0,
    climax: climaxCount(b),
    earnings: b.earnings?.status === 'OK' ? (b.earnings.upcoming || []).filter(x => x.inTrend).length : '—',
    score: b.scorecard?.eventCount ?? 0,
  };
  const body = el('section', null, 'tr-body');
  const draw = () => {
    seg.querySelectorAll('button').forEach(t => { const on = t.dataset.sub === subtab; t.classList.toggle('active', on); t.setAttribute('aria-pressed', String(on)); });
    body.replaceChildren();
    if (subtab === 'weather') body.append(weatherDetail(b, market));
    if (subtab === 'rs') body.append(rsSection(b, openStock));
    if (subtab === 'sell') body.append(sellSection(b, openStock, draw));
    if (subtab === 'climax') body.append(climaxSection(b, market, openStock, draw));
    if (subtab === 'earnings') body.append(earningsSection(b, openStock));
    if (subtab === 'score') body.append(scoreSection(b, openStock));
    fillMiniCharts(body, market);
  };
  SUBTABS.forEach(([k, label, short]) => {
    const t = btn('', false, () => {
      if (subtab === k) return;
      subtab = k; draw(); onSubtab?.(k);
      // Keep the reader where they were: bar at the top, new content right under it.
      const top = anchor.getBoundingClientRect().top;
      if (top < 0) window.scrollTo({ top: window.scrollY + top, behavior: 'instant' });
    });
    t.dataset.sub = k;
    t.append(el('span', label, 'lbl-full'), el('span', short, 'lbl-short'), el('span', `${counts[k]}`, `tab-count ${k === 'weather' ? 'weather-' + (b.weather?.level || 'UNKNOWN').toLowerCase() : ''}`));
    seg.append(t);
  });
  content.append(anchor, seg, body);
  draw();
  content.append(el('p', `${b.asOf} 종가 기준 · 계산 ${new Date(b.builtAt || data.generatedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST · 지수 ${b.index?.source || '—'} · 대상 ${b.universe}종목 · 투자 권유가 아니라 확인용 신호예요.`, 'tr-foot'));
}

// Read-only access for the separate FTD workspace.
export const trendBlock = market => block(market);
export const trendLoadFailed = () => failed;
