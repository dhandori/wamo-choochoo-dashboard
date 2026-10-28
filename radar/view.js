import { renderFTD, ftdSummary } from './ftd.js?v=20261026-rn1';
import { renderLeaders, leadersSummary } from './leaders.js?v=20261026-rn1';
import { CAP_FLOORS, SORT_KEYS, validSnapshot, acceptSnapshot, selectStocks, marketStatus, safeUrl, numeric, createRefresher, aiOverview, aiMembership, AI_SECTORS, AI_REVIEWED, signalChanges, priorSignal, hasComparison } from './model.mjs?v=20261028-h60';
import { helpLabel, helpButton, closeHelp } from './help.js?v=20261028-h60';
import { loadTrend, trendTabLabel, renderWeather, renderStrip, renderTrendMetrics, renderTrend, getSubtab, setSubtab, trendBlock, trendLoadFailed } from './trend.js?v=20261026-sp1';
import { runnerButton, initRunner, getRefreshStatus } from './runner.js?v=20261026-rn1';
import { renderSchedule } from './schedule.js?v=20261026-rn1';
import { renderSpMovers } from './spmovers.js?v=20261026-sp1';
import { loadChartData, stockSeries, priceChart, benchChart, rsChart, MA } from './chart.js?v=20261028-at1';
const CARD_MA = MA.filter(m => m.n === 50 || m.n === 200);
const phone = () => window.matchMedia('(max-width: 760px)').matches;
let cardObserver = null;
function cardLegend() {
  const lg = el('div', null, 'pc-legend small card-legend');
  [['#eaf0f8', '종가'], ...CARD_MA.map(m => [m.color, m.label])].forEach(([c, t]) => { const k = el('span', null, 'pc-key'); const i = el('i'); i.style.background = c; k.append(i, el('b', t)); lg.append(k); });
  lg.append(el('span', '최근 60거래일', 'pc-hint'));
  return lg;
}
// Phone cards: swap the tiny sparkline for an axis chart when the card scrolls into view.
function fillCardCharts(root) {
  if (!phone()) return;
  cardObserver?.disconnect();
  const boxes = [...root.querySelectorAll('.card-chart[data-symbol]')];
  if (!boxes.length) return;
  const market = state.market;
  cardObserver = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).map(e => e.target);
    if (!visible.length) return;
    visible.forEach(b => cardObserver.unobserve(b));
    loadChartData(market).then(data => visible.forEach(box => {
      const series = data && stockSeries(data, box.dataset.symbol);
      if (!series || !box.isConnected) return;
      box.replaceChildren(priceChart(series, { market, days: 60, compact: true, mas: CARD_MA }));
      box.closest('.stock-card')?.classList.add('has-chart');
    }));
  }, { rootMargin: '400px 0px' });
  boxes.forEach(b => cardObserver.observe(b));
}

const $ = id => document.getElementById(id);
const defaults = { smallGroups: false, tab: 'trend', distance: 10, high: 'either', mode: 'all', query: '', rs: false, stage: false, sort: 'default', confirmed: false, industry: null, page: 1, aiSector:'all', aiSignal:'all', aiPeriod:'5d',aiView:'cards',newOnly:false, sort2:'none', minCap:0 };
const PERIODS={'1d':['당일','medianChangePct','changeCount'],'5d':['5일','medianReturn5d','return5dCount'],'20d':['20일','medianReturn20d','return20dCount'],'60d':['60일','medianReturn60d','return60dCount'],'120d':['120일','medianReturn120d','return120dCount'],'200d':['200일','medianReturn200d','return200dCount']};
const CHANGE_LABELS={near:'신고가권 진입',rs:'RS 80 돌파',stage:'Stage 2 진입'};
const EXIT_LABELS={near:'신고가권 이탈',rs:'RS 80 아래로',stage:'Stage 2 이탈'};
const params = new URLSearchParams(location.search);
// Japan/Taiwan are Lite markets: price screens only (no trend/FTD/leaders/AI tabs yet).
const MARKET_NAMES = { KR: '한국', US: '미국', JP: '일본', TW: '대만' };
const LITE = new Set(['JP', 'TW']);
const LITE_OFF = new Set();   // every tab now works for Japan/Taiwan too
// 읽는 순서: ① 시장(지금 사도 되는 장인가) → ② 업종(어느 업종이 앞서가나) → ③ 종목. 탭은 이 세 묶음으로 보여 준다.
const STEP_OF = { trend: 'market', ftd: 'market', leaders: 'sector', industries: 'sector', ai: 'sector', near: 'stock', high: 'stock', high60: 'stock', movers: 'stock' };
const STEP_LABEL = { market: '① 시장 판단', sector: '② 주도 업종', stock: '③ 종목 찾기' };
const lastTabOfStep = { market: 'trend', sector: 'leaders', stock: 'near' };   // 단계 칸을 누르면 그 단계에서 마지막으로 보던 탭으로
const CURRENCY = { KR: ['₩', '원', 0], US: ['$', '달러', 2], JP: ['¥', '엔', 0], TW: ['NT$', '대만달러', 2] };
const state = { ...defaults, market: MARKET_NAMES[params.get('market')] ? params.get('market') : 'KR', tab: ['near','high','high60','industries','movers','ai','trend','ftd','leaders'].includes(params.get('tab'))?params.get('tab'):'trend' };
let snapshot = null, refreshFailed = false, refreshing = false;
// Sort settings (cap floor, 1st/2nd key) are remembered per market in this browser.
const SORT_STORE='wamo-sort-v1';
function loadSort(market){try{const all=JSON.parse(localStorage.getItem(SORT_STORE)||'{}')[market]||{};const floors=CAP_FLOORS[market].map(([v])=>v);
  if(floors.includes(all.minCap))state.minCap=all.minCap;if(all.sort==='default'||SORT_KEYS[all.sort])state.sort=all.sort;if(all.sort2==='none'||SORT_KEYS[all.sort2])state.sort2=all.sort2;}catch{}}
function saveSort(){try{const all=JSON.parse(localStorage.getItem(SORT_STORE)||'{}');all[state.market]={minCap:state.minCap,sort:state.sort,sort2:state.sort2};localStorage.setItem(SORT_STORE,JSON.stringify(all));}catch{}}
loadSort(state.market);
const fmt = (n, d = 1) => numeric(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = n => numeric(n) ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—';
const tone = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
const distance = s => numeric(s.highDistancePct) ? `${fmt(s.highDistancePct)}%` : '—';
const volume = s => numeric(s.volumeRatio) ? `${fmt(s.volumeRatio, 2)}×` : '—';
// 숫자 옆 보조 막대: 숫자는 그대로 두고, 크기를 한눈에 보이게만 한다.
const clamp01 = x => Math.max(0, Math.min(1, x));
function bar(ratio, cls, centered = false, left = 0) {
  const track = document.createElement('i'); track.className = `cell-bar${centered ? ' centered' : ''}`; track.setAttribute('aria-hidden', 'true');
  const fill = document.createElement('b'); fill.className = cls; fill.style.width = `${(clamp01(ratio) * (centered ? 50 : 100)).toFixed(1)}%`; fill.style.left = `${left}%`;
  track.append(fill); return track;
}
// 등락률: 가운데 기준선에서 오르면 오른쪽(빨강), 내리면 왼쪽(파랑). scale%에서 가득 찬다.
const moveBar = (n, scale) => numeric(n) ? bar(Math.abs(n) / scale, n >= 0 ? 'bar-up' : 'bar-down', true, n >= 0 ? 50 : 50 - clamp01(Math.abs(n) / scale) * 50) : null;
// 고점까지: 고점에 붙을수록 가득(15% 이상 떨어지면 빈 막대).
const highBar = s => numeric(s.highDistancePct) ? bar(1 - s.highDistancePct / 15, s.highDistancePct <= 5 ? 'bar-good' : 'bar-plain') : null;
const rsBar = s => numeric(s.rs) ? bar(s.rs / 99, s.rs >= 80 ? 'bar-good' : 'bar-plain') : null;
const volBar = s => numeric(s.volumeRatio) ? bar(s.volumeRatio / 3, s.volumeRatio >= 1.5 ? 'bar-hot' : 'bar-plain') : null;
function numCell(text, cls, barNode, tag = 'td') { const c = el(tag, null, `num-cell ${cls || ''}`.trim()); c.append(el('span', text)); if (barNode) c.append(barNode); return c; }
const price = s => { const [sym, , d] = CURRENCY[s.market] || CURRENCY.US; return `${sym}${fmt(s.close, s.market === 'JP' && !Number.isInteger(s.close) ? 1 : d)}`; };
const capCurrency = () => (CURRENCY[state.market] || CURRENCY.US)[1];
function marketCap(s, withCurrency = false) {
  const cap = s.marketCap;
  if (!numeric(cap) || cap <= 0) return '정보 없음';
  const unit = cap >= 1e12 ? [1e12, '조'] : cap >= 1e8 ? [1e8, '억'] : cap >= 1e4 ? [1e4, '만'] : [1, ''];
  const v = cap / unit[0], digits = unit[0] === 1 ? 0 : unit[1] === '억' ? (v >= 100 ? 0 : 1) : 2;
  return `${fmt(v, digits)}${unit[1]}${withCurrency ? ' ' + (CURRENCY[s.market] || CURRENCY.US)[1] : ''}`;
}
function capNode(s, tag = 'span') {
  const node = el(tag, marketCap(s), 'market-cap');
  node.title = numeric(s.marketCap) && s.marketCap > 0 ? `${fmt(s.marketCap, 0)} ${(CURRENCY[s.market] || CURRENCY.US)[1]}` : '시가총액 정보 없음';
  return node;
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function button(text, active, action) {
  const b = el('button', text, active ? 'active' : '');
  b.type = 'button'; b.onclick = action;
  return b;
}
function change(key, value) {
  state[key] = value; state.page = 1;
  if(['minCap','sort','sort2'].includes(key))saveSort();
  if(state.tab==='ai') {
    if(key==='sort'&&/^return(60|120|200)d$/.test(value))state.aiPeriod=value.slice(6);
    if(key==='aiPeriod'&&/^return(5|20|60|120|200)d$/.test(state.sort))state.sort='default';
  }
  render();
}
function safeLink(text, url, cls) {
  const safe = safeUrl(url);
  if (!safe) return el('span', text, `${cls || ''} unavailable`);
  const a = el('a', text, cls);
  a.href = safe; a.target = '_blank'; a.rel = 'noopener noreferrer';
  return a;
}
function naverLink(s, prominent = false) {
  const url = safeUrl(s.naverUrl);
  if (!url || !/^https:\/\/m\.stock\.naver\.com\/(domestic|worldstock)\/stock\/[A-Za-z0-9.]+\/total$/.test(url)) {
    // Naver has no Taiwan pages; Yahoo Finance does (same source as the prices).
    const yahoo = safeUrl(s.chartUrl);
    if (yahoo && /^https:\/\/finance\.yahoo\.com\/quote\/[A-Za-z0-9.^-]+\/?$/.test(yahoo)) {
      const y = safeLink(prominent ? '야후 파이낸스 ↗' : '야후 ↗', yahoo, prominent ? 'naver-primary' : 'naver-row');
      y.setAttribute('aria-label', `${s.name} 야후 파이낸스 새 탭`);
      return y;
    }
    return el('span', prominent ? '네이버증권 링크 없음' : '없음', prominent ? 'unavailable' : 'row-unavailable');
  }
  const a = safeLink(prominent ? '네이버증권 ↗' : '네이버 ↗', url, prominent ? 'naver-primary' : 'naver-row');
  a.setAttribute('aria-label', `${s.name} 네이버증권 새 탭`);
  return a;
}
// TradingView chart link. Exchange prefixes: KR→KRX, JP→TSE, TW→TWSE; US uses the real exchange when known.
const TV_PREFIX = { KR: 'KRX', JP: 'TSE', TW: 'TWSE' };
function tvLink(s, prominent = false) {
  let symbol = String(s.symbol || '').trim().toUpperCase();
  if (!symbol || !/^[A-Z0-9.-]{1,12}$/.test(symbol)) return null;
  const market = s.market || state.market;
  if (market === 'US') symbol = symbol.replace(/-/g, '.');
  const exchange = String(s.exchange || '').toUpperCase();
  const prefix = market === 'US' ? (['NASDAQ', 'NYSE', 'AMEX'].includes(exchange) ? exchange : null) : TV_PREFIX[market];
  const full = prefix ? `${prefix}:${symbol}` : symbol;
  const a = safeLink(prominent ? '트레이딩뷰 ↗' : 'TV ↗', `https://kr.tradingview.com/chart/?symbol=${encodeURIComponent(full)}`, prominent ? 'tv-primary' : 'tv-row');
  a.setAttribute('aria-label', `${s.name} 트레이딩뷰 차트 새 탭`);
  return a;
}
function spark(s, large = false) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const width = large ? 560 : 120, height = large ? 140 : 40;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.classList.add(large ? 'detail-chart' : 'spark');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '최근 60거래일 종가 흐름 · 점선은 60일 전 종가');
  const values = (s.sparkline || []).filter(numeric);
  if (values.length > 1) {
    const low = Math.min(...values), range = Math.max(...values) - low || 1;
    const px = (v, i) => [i / (values.length - 1) * (width - 4) + 2, height - 5 - (v - low) / range * (height - 10)];
    const up = values.at(-1) >= values[0], color = up ? '#ff6176' : '#5ea8ff';
    // Dotted baseline at the first close: above it = gained over the window.
    const base = document.createElementNS(svg.namespaceURI, 'line'), y0 = px(values[0], 0)[1];
    base.setAttribute('x1', 0); base.setAttribute('x2', width); base.setAttribute('y1', y0); base.setAttribute('y2', y0);
    base.setAttribute('stroke', '#5a6d86'); base.setAttribute('stroke-width', '1'); base.setAttribute('stroke-dasharray', '2 3'); svg.append(base);
    const path = document.createElementNS(svg.namespaceURI, 'polyline');
    path.setAttribute('points', values.map((v, i) => px(v, i).join(',')).join(' '));
    path.setAttribute('fill', 'none'); path.setAttribute('stroke', color); path.setAttribute('stroke-linejoin', 'round');
    path.setAttribute('stroke-width', large ? '2' : '1.6'); svg.append(path);
    const dot = document.createElementNS(svg.namespaceURI, 'circle'), [cx, cy] = px(values.at(-1), values.length - 1);
    dot.setAttribute('cx', cx); dot.setAttribute('cy', cy); dot.setAttribute('r', large ? 3 : 2.2); dot.setAttribute('fill', color); svg.append(dot);
  }
  return svg;
}
function shortName(s) {
  return s.market === 'US' ? s.name.replace(/\s*[-,]?\s*(?:Class [A-Z] )?(?:Common Stock|Ordinary Shares).*$/i, '').trim() || s.name : s.name;
}
function signals(s) {
  const high52 = s.intradayHigh52 === true || s.closeHigh52 === true;
  return [s.intradayHigh52 ? '장중 신고가' : null, s.closeHigh52 ? '종가 신고가' : null,
    !high52 && (s.intradayHigh60 === true || s.closeHigh60 === true) ? '60일 신고가' : null,
    s.changePct >= 3 ? '당일 상승 3% 이상' : s.changePct <= -3 ? '당일 하락 3% 이상' : null,
    s.volumeRatio >= 1.5 ? '거래량 1.5배 이상' : null].filter(Boolean);
}
function changes(s) {
  const m=snapshot.markets[state.market];
  return signalChanges(s,priorSignal(m,s),state.tab==='near'?state.distance:10);
}
function changeBadges(s) {
  const wrap=el('span',null,'change-badges');
  changes(s).entered.forEach(key=>{const badge=el('span',CHANGE_LABELS[key],'change-badge');badge.title=`직전 거래일 대비 ${CHANGE_LABELS[key]}${key==='near'?` · 고점 ${state.tab==='near'?state.distance:10}% 이내`:''}`;wrap.append(badge);});
  return wrap;
}
function stockButton(s) {
  const b = button('', false, () => detail(s)); b.className = 'stock-link'; b.title = s.name;
  const member=state.tab==='ai'?aiMembership(s):null;
  b.append(el('strong', shortName(s)), el('small', `${s.symbol} · ${member?member.role:s.industry}`));
  if(member)b.append(el('span',AI_SECTORS.filter(g=>member.sectors.includes(g.id)).map(g=>g.name).join(' · '),'ai-tags'));
  b.append(changeBadges(s));
  return b;
}
function stat(label, value, cls = '', barNode = null) {
  const box = el('div', null, 'stat'), caption = el('span'); caption.append(helpLabel(label));
  box.append(caption, el('strong', value, cls)); if (barNode) box.append(barNode); return box;
}
// 외국인·기관 추정 순매수(원)를 억·조 단위로 읽기 쉽게. 숫자 없는 기간은 '—'로 그대로 보여 준다.
const flowKrw = v => {
  if (!numeric(v)) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  const a = Math.abs(v);
  if (a >= 1e12) return `${sign}${fmt(a / 1e12, 2)}조원`;
  if (a >= 1e8) return `${sign}${fmt(a / 1e8, a >= 1e10 ? 0 : 1)}억원`;
  if (a >= 1e4) return `${sign}${fmt(a / 1e4, 0)}만원`;
  return `${sign}${fmt(a, 0)}원`;
};
// KR 종목 상세의 수급 카드. 갱신기가 수급을 붙인 종목(후보군)에만 나타난다.
function flowSection(s) {
  const f = s.investorFlow;
  if (!f) return null;
  const sec = el('section', null, 'flow-box');
  const head = el('div', null, 'flow-box-head');
  head.append(el('h3', '외국인·기관 순매수 (추정)'), el('span', `${f.asOf || s.asOf} 기준 · 거래일 합산${numeric(f.foreignHoldPct) ? ` · 외국인 보유율 ${fmt(f.foreignHoldPct, 2)}%` : ''}`, 'flow-box-sub'));
  sec.append(head);
  const grid = el('div', null, 'flow-box-grid');
  [['외국인', 'foreign'], ['기관 합계', 'inst']].forEach(([label, key]) => {
    const row = el('div', null, 'flow-box-row');
    row.append(el('b', label));
    const cells = el('div', null, 'flow-box-cells');
    [[5, '5일'], [20, '20일'], [60, '60일']].forEach(([n, periodLabel]) => {
      const v = f[`${key}Net${n}d`];
      const cell = el('span', null, 'flow-box-cell');
      cell.append(el('small', periodLabel), el('strong', flowKrw(v), numeric(v) ? tone(v) : 'neutral'));
      const pct = f[`${key}Pct${n}d`];
      if (numeric(pct)) cell.append(el('em', `시총 대비 ${pct > 0 ? '+' : ''}${fmt(pct, 2)}%`));
      cells.append(cell);
    });
    row.append(cells); grid.append(row);
  });
  sec.append(grid);
  sec.append(el('p', '순매수수량 × 당일 종가로 계산한 추정 금액이라 거래소 공식 결제 금액과 다를 수 있어요. 연기금·사모 같은 세부 수급은 자료가 없어 만들지 않아요. 수급은 스크리닝 후보 종목에만 수집합니다.', 'flow-box-note'));
  return sec;
}
// Wait briefly for the chart data so the drawer does not jump after opening.
function detail(s) {
  closeHelp();
  const market = s.market || state.market;
  const timeout = new Promise(resolve => setTimeout(() => resolve(undefined), 1500));
  Promise.race([loadChartData(market), timeout]).then(data => renderDetail(s, data));
}
// 상세창 닫기 버튼은 제목줄(화면 위 고정) 안으로 옮겨 쓴다. 같은 요소를 매번 다시 붙이므로 id와 핸들러가 유지된다.
const closeButton = $('close-drawer');
// 한눈에 보는 선별 조건 네 가지: RS · 고점까지 · 거래량 · Stage 2. 아래 수치 표와 같은 값이고, 좋은 쪽만 색을 입힌다.
function detailChips(s) {
  const chips = el('div', null, 'detail-chips'); chips.setAttribute('aria-label', '핵심 선별 조건');
  const stage = s.stage2 === true ? '충족' : s.stage2 === false ? '미충족' : '정보 없음';
  [[`RS ${fmt(s.rs, 0)}`, numeric(s.rs) ? (s.rs >= 80 ? 'good' : s.rs < 50 ? 'weak' : '') : 'weak'],
    [`고점까지 ${numeric(s.highDistancePct) ? distance(s) : '기록 부족'}`, numeric(s.highDistancePct) ? (s.highDistancePct <= 5 ? 'good' : s.highDistancePct > 15 ? 'weak' : '') : 'weak'],
    [`거래량 ${volume(s)}`, numeric(s.volumeRatio) ? (s.volumeRatio >= 1.5 ? 'hot' : s.volumeRatio < 0.7 ? 'weak' : '') : 'weak'],
    [`Stage 2 ${stage}`, s.stage2 === true ? 'good' : s.stage2 === false ? 'weak' : 'weak']].forEach(([t, c]) => chips.append(el('span', t, `chip ${c}`.trim())));
  return chips;
}
function renderDetail(s, data) {
  const d = $('detail');
  // 제목줄: 종목명·분류·닫기. 스크롤해도 창 위에 남는다.
  const head = el('header', null, 'detail-head'), titles = el('div', null, 'detail-titles');
  const heading = el('h2', s.name); heading.id = 'stock-detail-title';
  titles.append(el('div', `${s.symbol} · ${s.market}${s.exchange ? ' / ' + s.exchange : ''}`, 'eyebrow'), heading, el('p', `${s.industry} · ${s.asOf} 거래일 종가`));
  const membership=aiMembership(s);
  if(membership)titles.append(el('p',`AI 연관 분류: ${AI_SECTORS.filter(g=>membership.sectors.includes(g.id)).map(g=>g.name).join(' · ')} / ${membership.role}`,'ai-detail'));
  head.append(titles, closeButton);
  d.replaceChildren(head);
  // 가격 줄: 종가·등락 → 핵심 조건 네 칩 → 바로가기.
  const quote = el('div', null, 'detail-quote');
  quote.append(el('strong', price(s)), el('span', pct(s.changePct), tone(s.changePct)), detailChips(s));
  const links = el('div', null, 'detail-links'); links.append(naverLink(s, true));
  const tvDetail = tvLink(s, true); if (tvDetail) links.append(tvDetail);
  quote.append(links); d.append(quote);
  // 본문: 왼쪽 차트, 오른쪽 수치·수급·신호·전일 변화. 좁은 화면에서는 수치가 차트보다 먼저 온다.
  const body = el('div', null, 'detail-body');
  const chart = el('section', null, 'chart-panel');
  chart.append(el('div', '최근 60거래일 · 종가 추이', 'chart-caption'), spark(s, true));
  // 핵심 수치는 네 묶음으로: 수익률(작은 칸 6개) · 고점 위치 · 강도·거래 · 변동성·손절(차트 자료가 오면 채움).
  const side = el('div', null, 'detail-side');
  side.append(returnsSection(s), positionSection(s), strengthSection(s), riskSection(data && stockSeries(data, s.symbol), s));
  const extra = el('div', null, 'detail-extra');
  const flowBox = flowSection(s); if (flowBox) extra.append(flowBox);
  const signalBox = el('div', null, 'detail-signals'); signalBox.setAttribute('aria-label', '특징주 신호');
  const reasons = signals(s); (reasons.length ? reasons : ['특징주 신호 없음']).forEach(t => signalBox.append(el('span', t, 'badge'))); extra.append(signalBox);
  const transition=changes(s),comparison=snapshot.markets[state.market].comparison;
  const changeNote=el('section',null,'detail-changes');changeNote.append(helpLabel('전일 변화'));
  const eventLabels=[...transition.entered.map(k=>CHANGE_LABELS[k]),...transition.exited.map(k=>EXIT_LABELS[k])];
  changeNote.append(el('p',transition.comparable.length?`${comparison.asOf} → ${s.asOf} · ${eventLabels.join(' · ')||'조건에 새로 들거나 빠진 것 없음'}`:'직전 거래일 자료가 없어 비교할 수 없습니다.'));
  extra.append(changeNote);
  body.append(chart, side, extra); d.append(body);
  if (data !== null) fillDetailChart(chart, s, data);   // 패널이 문서에 붙은 뒤에 그려야 한다(isConnected 검사)
  const evidence = el('section', null, 'evidence detail-evidence'); evidence.append(el('h3', '공시·공식 자료'));
  const cats = Array.isArray(s.catalysts) ? s.catalysts : [];
  if (!cats.length) evidence.append(el('p', '등록된 공식 자료가 없습니다.'));
  cats.forEach(c => { const box = el('div', null, 'news-item'); box.append(safeLink(c.title, c.url), el('small', c.date)); evidence.append(box); });
  const news = Array.isArray(s.news) ? s.news : [];
  if (news.length) {
    evidence.append(el('h3', '관련 헤드라인'));
    news.forEach(n => {
      const date = n.date ? n.date.includes('T') ? new Date(n.date).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) + ' KST' : n.date : '날짜 없음';
      const box = el('div', null, 'news-item'); box.append(safeLink(n.title, n.url), el('small', `${date} · ${n.publisher || ''}`)); evidence.append(box);
    });
  }
  evidence.append(el('p', '공시·뉴스가 이 주가 움직임의 원인이라고 확인된 것은 아닙니다.', 'evidence-note')); d.append(evidence);
  d.append(el('p', `가격 출처: ${s.source} · 산업 분류: ${s.industrySource}`, 'source-note'));
  $('drawer').showModal(); d.scrollTop = 0;
}
// 상세창 수치 묶음. 한 줄 = 이름 | 값 | (보조 막대·설명). 줄마다 class 'stat'이라 용어 도움말이 그대로 붙는다.
function kvSection(title, rows, cls = '') {
  const sec = el('section', null, `kv-sec ${cls}`.trim()); sec.append(el('h3', title, 'kv-title'));
  rows.filter(Boolean).forEach(r => sec.append(r)); return sec;
}
function kvRow(label, value, { cls = '', bar = null, sub = null, note = null } = {}) {
  const row = el('div', null, 'stat kv'), name = el('span', null, 'kv-name'); name.append(helpLabel(label));
  const val = el('div', null, 'kv-val'); val.append(el('strong', value, cls)); if (sub) val.append(el('small', sub, 'kv-sub'));
  row.append(name, val); if (bar) { bar.classList.add('kv-bar'); row.append(bar); }
  if (note) row.append(el('em', note, 'stat-hint'));
  return row;
}
function returnsSection(s) {
  const sec = el('section', null, 'kv-sec'); sec.append(el('h3', '수익률', 'kv-title'));
  const grid = el('div', null, 'ret-grid');
  [['당일', 'changePct', 8], ['5일', 'return5d', 15], ['20일', 'return20d', 30], ['60일', 'return60d', 60], ['120일', 'return120d', 80], ['200일', 'return200d', 100]].forEach(([label, key, scale]) => {
    const tile = stat(label, pct(s[key]), tone(s[key]), moveBar(s[key], scale)); tile.classList.add('ret-tile'); tile.dataset.key = key; grid.append(tile);
  });
  sec.append(grid); return sec;
}
function positionSection(s) {
  const new60 = s.intradayHigh60 === true || s.closeHigh60 === true, new52 = s.intradayHigh52 === true || s.closeHigh52 === true;
  return kvSection('고점 위치', [
    kvRow('52주 고점까지', numeric(s.highDistancePct) ? distance(s) : '기록 부족', { cls: new52 ? 'good' : '', bar: highBar(s), sub: numeric(s.high52) ? `고점 ${fmt(s.high52, 2)}${new52 ? ' · 오늘 신고가' : ''}` : null }),
    kvRow('60일 고점까지', numeric(s.high60DistancePct) ? `${fmt(s.high60DistancePct)}%` : '—', { cls: new60 ? 'good' : '', sub: numeric(s.high60) ? `고점 ${fmt(s.high60, 2)}${new60 ? ' · 오늘 60일 신고가' : ''}` : null }),
  ]);
}
function strengthSection(s) {
  return kvSection('강도·거래', [
    kvRow('RS', fmt(s.rs, 0), { cls: numeric(s.rs) && s.rs >= 80 ? 'good' : '', bar: rsBar(s) }),
    kvRow('거래량 배수', volume(s), { bar: volBar(s) }),
    kvRow('Stage 2', s.stage2 === true ? '충족' : s.stage2 === false ? '미충족' : '정보 없음', { cls: s.stage2 === true ? 'good' : '' }),
    kvRow('시가총액', marketCap(s, true)),
  ]);
}
// 변동성·손절: ATR(14일)와 두 가지 손절 기준. 종가를 매수가로 가정한다.
// ① −8% 한도(미너비니 최대 손절) ② 2×ATR(그 종목의 보통 흔들림 밖). ATR은 차트 자료(고가·저가)에서 와서 늦으면 나중에 채운다.
const STOP_CAP = 8, ATR_MULT = 2, RISK_PCT = 1;
function riskSection(series, s) {
  const [sym, , d] = CURRENCY[s.market || state.market] || CURRENCY.US, money = v => `${sym}${fmt(v, d)}`;
  const close = numeric(s.close) && s.close > 0 ? s.close : null, a = series?.atr, hasAtr = !!(a && numeric(a[0]) && numeric(a[1]));
  const rows = [kvRow('ATR(14일)', hasAtr ? money(a[0]) : '—', { sub: hasAtr ? `하루 평균 ${fmt(a[1])}% 움직임` : '차트 자료가 오면 채워져요' }),
    kvRow(`기준 손절 −${STOP_CAP}%`, close ? money(close * (1 - STOP_CAP / 100)) : '—', { sub: '어떤 종목이든 넘지 않는 한도' })];
  if (close && hasAtr) {
    const atrPct = a[1] * ATR_MULT, over = atrPct > STOP_CAP, used = Math.min(atrPct, STOP_CAP);
    rows.push(kvRow(`ATR 손절 (${ATR_MULT}×ATR)`, money(close - a[0] * ATR_MULT), { cls: over ? 'bad' : 'good', sub: `−${fmt(atrPct)}%`,
      note: over ? `${STOP_CAP}%보다 넓어요 → ${STOP_CAP}%에서 자르고, 변동성이 커서 비중을 줄이거나 더 좁은 자리에서만 사요.` : `${STOP_CAP}% 안이에요 → 이 폭이면 보통의 흔들림에는 안 털려요.` }));
    rows.push(kvRow(`계좌 위험 ${RISK_PCT}% 기준 비중`, `${fmt(RISK_PCT * 100 / used, 0)}%`, { sub: `손절 폭 ${fmt(used)}% 기준 최대 비중` }));
  }
  return kvSection('변동성·손절 (종가 = 매수가 가정)', rows, 'risk-sec');
}
let chartDays = 60;
function fillDetailChart(panel, s, ready) {
  const market = s.market || state.market;
  const apply = data => {
    const series = data && stockSeries(data, s.symbol);
    if (!series || !panel.isConnected) return;
    const risk = panel.closest('.detail-body')?.querySelector('.risk-sec'); if (risk) risk.replaceWith(riskSection(series, s));
    const draw = () => {
      const head = el('div', null, 'chart-head');
      head.append(el('div', `일봉 · 최근 ${chartDays}거래일`, 'chart-caption'));
      const toggle = el('div', null, 'chart-toggle');
      [[60, '3개월'], [120, '6개월']].forEach(([n, label]) => toggle.append(button(label, chartDays === n, () => { chartDays = n; draw(); })));
      head.append(toggle);
      const body = [head, priceChart(series, { market, days: chartDays })];
      if (chartDays > series.closes.length - 199) body.push(el('p', `저장된 가격 기록이 ${series.closes.length}거래일뿐이라 200일선은 최근 ${series.closes.length - 199}거래일만 그려져요.`, 'chart-note'));
      // 같은 기간의 소속 시장 지수: 종목 차트와 위아래로 비교해 RS를 눈으로 확인한다.
      const bench = benchChart(series, { days: chartDays });
      if (bench) body.push(bench);
      const rs = rsChart(series, { days: chartDays });
      if (rs) body.push(rs);
      panel.replaceChildren(...body);
    };
    draw();
  };
  if (ready) apply(ready); else loadChartData(market).then(apply);
}
function table(rows) {
  if (!rows.length) return el('div', '조건에 맞는 종목이 없습니다. 필터를 바꿔 보세요.', 'empty');
  const pageRows = rows.slice((state.page - 1) * 30, state.page * 30);
  const longDays=['60d','120d','200d'].includes(state.aiPeriod)?parseInt(state.aiPeriod,10):60,longKey=`return${longDays}d`;
  const wrap = el('div', null, 'table-wrap'), table = el('table'), head = el('thead'), tr = el('tr');
  [state.tab==='ai'?'종목 / AI 연결 분야':'종목 / 산업', `시총 (${capCurrency()})`, '종가', '당일', '5일', ...(state.tab==='ai'?['20일',`${longDays}일`]:[]), '고점까지', '거래량 배수', 'RS', '60일 추이', '바로가기'].forEach(t => { const th = el('th'); th.scope = 'col'; th.setAttribute('aria-label', t); th.append(helpLabel(t)); tr.append(th); });
  head.append(tr); table.append(head); const body = el('tbody');
  pageRows.forEach(s => {
    const row = el('tr'), name = el('td'); name.append(stockButton(s));
    if (['high','high60','movers','ai'].includes(state.tab)) name.append(el('span', signals(s).join(' · '), 'signal'));
    row.append(name, capNode(s, 'td'), el('td', price(s)), numCell(pct(s.changePct), tone(s.changePct), moveBar(s.changePct, 8)), numCell(pct(s.return5d), tone(s.return5d), moveBar(s.return5d, 15)));
    if(state.tab==='ai')row.append(numCell(pct(s.return20d),tone(s.return20d),moveBar(s.return20d,30)),numCell(pct(s[longKey]),tone(s[longKey]),moveBar(s[longKey],60)));
    row.append(numCell(distance(s), '', highBar(s)), numCell(volume(s), '', volBar(s)), numCell(fmt(s.rs, 0), '', rsBar(s)));
    const chart = el('td'); chart.append(spark(s)); const link = el('td'); const links = el('span', null, 'row-links'); links.append(naverLink(s)); const tvRow = tvLink(s); if (tvRow) links.append(tvRow); link.append(links); row.append(chart, link); body.append(row);
  });
  table.append(body); wrap.append(table);
  const cards = el('div', null, 'stock-cards');
  pageRows.forEach(s => {
    const card = el('article', null, 'stock-card'), top = el('div', null, 'stock-card-top'); const cardLinks = el('span', null, 'card-links'); cardLinks.append(naverLink(s)); const tvCard = tvLink(s); if (tvCard) cardLinks.append(tvCard); top.append(stockButton(s), cardLinks);
    const quote = el('div', null, 'card-quote'); quote.append(el('strong', price(s)), numCell(pct(s.changePct), tone(s.changePct), moveBar(s.changePct, 8), 'span'), spark(s));
    const stats = el('div', null, 'card-stats'); stats.append(stat('5일', pct(s.return5d), tone(s.return5d), moveBar(s.return5d, 15)), stat('고점까지', distance(s), '', highBar(s)), stat('거래량 배수', volume(s), '', volBar(s)), stat('RS', fmt(s.rs, 0), '', rsBar(s)));
    if(state.tab==='ai')stats.append(stat('20일',pct(s.return20d),tone(s.return20d),moveBar(s.return20d,30)),stat(`${longDays}일`,pct(s[longKey]),tone(s[longKey]),moveBar(s[longKey],60)));
    const cap = el('div', null, 'card-cap'); cap.append(el('span', '시총'), capNode(s), el('span', capCurrency()));
    const chartBox = el('div', null, 'card-chart'); chartBox.dataset.symbol = s.symbol;
    card.append(top, cap, quote, chartBox, stats);
    if (['high','high60','movers','ai'].includes(state.tab)) card.append(el('div', signals(s).join(' · '), 'signal'));
    cards.append(card);
  });
  const container = el('div'); container.append(wrap, cardLegend(), cards);
  requestAnimationFrame(() => { fillCardCharts(container); fitTable(wrap); });
  return container;
}
// 표가 가로 스크롤 없이 다 들어가면(.fits) 세로 스크롤 중에도 컬럼 제목 줄이 화면 위에 붙어 따라온다.
// 표가 옆으로 넘치는 좁은 화면에서는 가로 스크롤을 그대로 유지한다.
function fitTable(wrap) { if (wrap?.isConnected) wrap.classList.toggle('fits', wrap.scrollWidth <= wrap.clientWidth + 1); }
window.addEventListener('resize', () => document.querySelectorAll('.table-wrap').forEach(fitTable), { passive: true });
// 3종목 미만 그룹은 보통 숨기지만, 2차전지 대장(삼성SDI·LG엔솔)만 있는 '배터리 셀'은 꼭 보여 준다.
const ALWAYS_SHOW = new Set(['배터리 셀']);
const bigEnough = g => g.memberCount >= 3 || (ALWAYS_SHOW.has(g.name) && g.memberCount >= 2);
// 산업 흐름 칸: 당일 / 5일 / 20일 중앙값. 기본은 당일 — 오늘 돈이 들어온 산업이 먼저 보여야 한다.
const RAIL_PERIODS = { '1d': ['당일', 'medianChangePct', 'changePct'], '5d': ['5일', 'medianReturn5d', 'return5d'], '20d': ['20일', 'medianReturn20d', 'return20d'] };
const RAIL_STORE = 'wamo-rail-period';
let railPeriod = '1d';
try { if (RAIL_PERIODS[localStorage.getItem(RAIL_STORE)]) railPeriod = localStorage.getItem(RAIL_STORE); } catch {}
function railValue(m, g, period) {
  const [, groupKey, stockKey] = RAIL_PERIODS[period];
  if (numeric(g[groupKey])) return g[groupKey];
  // Older snapshots lack the 20-day median: take it from the members instead.
  const byId = m._byId || (m._byId = new Map(m.stocks.map(s => [s.id, s])));
  const nums = (g.members || []).map(id => byId.get(id)?.[stockKey]).filter(numeric).sort((x, y) => x - y);
  if (!nums.length) return null;
  const mid = nums.length >> 1;
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}
function openIndustry(g) {
  Object.assign(state, { tab: 'industries', industry: g, query: '', rs: false, stage: false, newOnly:false, page: 1 }); $('search').value = ''; render();
}
function renderRail(m) {
  const rail = $('industry-rail'); rail.replaceChildren(); rail.hidden = state.tab==='ai'||state.tab === 'industries' && !state.industry;
  if (rail.hidden) return;
  const head = el('div', null, 'rail-head');
  head.append(el('h3', '산업 흐름'));
  const seg = el('div', null, 'rail-seg'); seg.setAttribute('aria-label', '산업 흐름 기간');
  Object.entries(RAIL_PERIODS).forEach(([key, [label]]) => { const b = button(label, railPeriod === key, () => { railPeriod = key; try { localStorage.setItem(RAIL_STORE, key); } catch {} renderRail(m); }); b.setAttribute('aria-pressed', String(railPeriod === key)); seg.append(b); });
  head.append(seg);
  rail.append(el('div', 'INDUSTRY FLOW', 'eyebrow'), head, el('p', `${RAIL_PERIODS[railPeriod][0]} 수익률 중앙값 높은 순 · 3종목 이상 산업 (배터리 셀은 항상 표시)`, 'rail-caption'));
  const value = g => railValue(m, g, railPeriod);
  const groups = m.industries.filter(g => bigEnough(g) && numeric(value(g)))
    .sort((a, b) => value(b) - value(a) || Number(b.confirmed) - Number(a.confirmed) || b.nearHighCount - a.nearHighCount).slice(0, 6);
  const scale = Math.max(1, ...groups.map(g => Math.abs(value(g) || 0)));
  if (!groups.length) rail.append(el('p', '비교할 수 있는 산업이 없습니다.'));
  // On phones the strip scrolls sideways under the tabs; on desktop it is the side rail.
  const strip = el('div', null, 'rail-strip'); rail.append(strip);
  groups.forEach(g => {
    const b = button('', false, () => openIndustry(g)); b.className = 'rail-group';
    const v = value(g);
    const top = el('div', null, 'rail-group-top'); const nm = el('strong', g.name); nm.title = g.name; top.append(nm, el('span', pct(v), tone(v)));
    const bar = el('div', null, 'flow-bar'); bar.setAttribute('aria-hidden', 'true');
    if (numeric(v)) {
      const fill = el('span', null, tone(v));
      const width = Math.abs(v) / scale * 50;
      fill.style.width = `${width}%`; fill.style.left = `${v < 0 ? 50 - width : 50}%`; bar.append(fill);
    }
    const others = Object.entries(RAIL_PERIODS).filter(([k]) => k !== railPeriod).map(([k, [label]]) => `${label} ${pct(railValue(m, g, k))}`).join(' · ');
    b.append(top, bar, el('small', `${others} · ${g.memberCount}종목 · 신고가권 ${g.nearHighCount}`), el('span', g.confirmed ? '동반 강세' : '관찰 중', `flow-status ${g.confirmed ? 'confirmed' : ''}`)); strip.append(b);
  });
  rail.append(button('전체 산업 보기 →', false, () => { Object.assign(state, { tab: 'industries', industry: null, query: '', rs: false, stage: false, newOnly:false, page: 1 }); $('search').value = ''; render(); }));
}
function checkbox(key, text, parent) {
  const label = el('label', null, 'checkbox-control'), input = el('input'); input.type = 'checkbox'; input.checked = state[key]; input.onchange = () => change(key, input.checked);
  label.append(input, document.createTextNode(` ${text}`));
  const wrap = el('div', null, 'checkbox-help'); wrap.append(label, helpButton(text, key, true)); parent.append(wrap);
}
function resetFilters() {
  for (const key of ['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'sort2', 'minCap', 'confirmed', 'smallGroups', 'page','aiSector','aiSignal','aiPeriod','newOnly']) state[key] = defaults[key];
  saveSort(); $('search').value = ''; render();
}
function renderControls() {
  const controls = $('controls'); controls.replaceChildren();
  if (state.industry) controls.append(button('← 전체 산업', false, () => { state.industry = null; state.page = 1; render(); }));
  const group = el('div', null, 'filter-group');
  if (state.tab === 'near') {
    const label = el('span', null, 'filter-label'); label.append(helpLabel('고점 거리')); group.append(label); [5, 10, 15].forEach(v => group.append(button(`${v}% 이내`, state.distance === v, () => change('distance', v))));
  }
  if (['high', 'high60'].includes(state.tab)) [['either', '모두'], ['intraday', '장중 고점'], ['close', '종가 고점']].forEach(([v, text]) => group.append(button(text, state.high === v, () => change('high', v))));
  if (state.tab === 'movers') [['all', '전체'], ['up', '상승 ≥3%'], ['down', '하락 ≤−3%'], ['volume', '거래량 ≥1.5×']].forEach(([v, text]) => group.append(button(text, state.mode === v, () => change('mode', v))));
  if(state.tab==='ai') {
    const sectors=el('select');sectors.setAttribute('aria-label','AI 분야');
    const listed=new Set(aiOverview(snapshot.markets[state.market],state.market).groups.filter(g=>g.catalogCount>0).map(g=>g.id));
    if(state.aiSector!=='all'&&!listed.has(state.aiSector))state.aiSector='all';
    [{id:'all',name:'AI 전체'},...AI_SECTORS.filter(g=>listed.has(g.id))].forEach(g=>{const o=el('option',g.name);o.value=g.id;o.selected=state.aiSector===g.id;sectors.append(o);});
    sectors.onchange=()=>change('aiSector',sectors.value);group.append(sectors);
    const signal=el('select');signal.setAttribute('aria-label','AI 종목 조건');
    [['all','모든 AI 종목'],['near','신고가권 10% 이내'],['high','당일 신고가'],['up','상승 ≥3%'],['down','하락 ≤−3%'],['volume','거래량 ≥1.5×']].forEach(([value,text])=>{const o=el('option',text);o.value=value;o.selected=state.aiSignal===value;signal.append(o);});
    signal.onchange=()=>change('aiSignal',signal.value);group.append(signal);
  }
  if (group.childNodes.length) controls.append(group);
  if (state.tab === 'industries' && !state.industry) {
    checkbox('confirmed', '동반 강세 산업만', controls);
    const small = el('label', null, 'checkbox-control'), input = el('input'); input.type = 'checkbox'; input.checked = state.smallGroups;
    input.onchange = () => change('smallGroups', input.checked); small.append(input, document.createTextNode(' 1~2종목 산업도 보기')); controls.append(small);
  } else {
    const options = el('div', null, 'filter-group'); checkbox('rs', 'RS ≥80', options); checkbox('stage', 'Stage 2', options); controls.append(options);
    // Folded on phones so the list starts near the top; the summary line keeps the applied order visible.
    const fold = el('details', null, 'sort-fold'); let open = !window.matchMedia('(max-width:760px)').matches;
    try { const saved = localStorage.getItem('wamo-sort-open'); if (saved) open = saved === '1'; } catch {}
    fold.open = open; fold.addEventListener('toggle', () => { try { localStorage.setItem('wamo-sort-open', fold.open ? '1' : '0'); } catch {} });
    const first = state.sort === 'default' ? defaultSortLabel() : SORT_KEYS[state.sort]?.[0], second = state.sort2 !== 'none' && state.sort2 !== state.sort ? SORT_KEYS[state.sort2]?.[0] : null;
    const sum = el('summary'); sum.append(el('b', '정렬·시총 '), el('span', [state.minCap > 0 ? `시총 ${capLabel()}` : '시총 전체', first, second].filter(Boolean).join(' → ')));
    fold.append(sum, sortPanel()); controls.append(fold);
  }
  if (['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'sort2', 'minCap', 'confirmed', 'smallGroups','aiSector','aiSignal','aiPeriod','newOnly'].some(key => state[key] !== defaults[key])) {
    const reset = button('필터 초기화', false, resetFilters); reset.id = 'reset-filters'; controls.append(reset);
  }
}
// What "기본 정렬" means on each tab, in words.
function defaultSortLabel(){return state.tab==='ai'?'선택 기간 수익률 높은 순':state.tab==='near'?'신고가에 가까운 순':state.tab==='movers'?'당일 변동 큰 순':'5일 수익률 높은 순';}
function sortKeysForTab(){const extra=state.tab==='ai'?['return60d','return120d','return200d']:[];return ['rs','highDistancePct','marketCap','marketCapAsc','changePct','absolute','return5d','return20d',...extra,'volumeRatio','name'];}
function capLabel(){return (CAP_FLOORS[state.market].find(([v])=>v===state.minCap)||[0,'전체'])[1];}
// Step-by-step sort: ① market-cap floor → ② first key → ③ tie-break key.
function sortPanel(){
  const box=el('div',null,'sort-panel'),allowed=sortKeysForTab();
  // A key only some tabs offer (e.g. 120일 on the AI tab) falls back when you leave that tab.
  if(state.sort!=='default'&&!allowed.includes(state.sort))state.sort='default';
  if(state.sort2!=='none'&&!allowed.includes(state.sort2))state.sort2='none';
  // One row: cap chips → 1st key → tie-break → presets. The summary sentence is gone;
  // the selects already read as the order they apply in.
  const capRow=el('div',null,'sort-row sort-cap');capRow.append(el('span','시총','sort-step'));
  CAP_FLOORS[state.market].forEach(([v,label])=>capRow.append(button(label,state.minCap===v,()=>change('minCap',v))));
  if(state.minCap>0)capRow.title='시총 자료가 없는 종목은 빠져요';
  box.append(capRow);
  const pick=(key,title,includeNone)=>{const wrap=el('label',null,'sort-pick');wrap.append(el('span',title,'sort-step'));const sel=el('select');sel.setAttribute('aria-label',title);
    const opts=includeNone?[['none','2차 정렬 없음']]:[['default',`기본 · ${defaultSortLabel()}`]];
    sortKeysForTab().forEach(k=>opts.push([k,SORT_KEYS[k][0]]));
    opts.forEach(([value,text])=>{const o=el('option',text);o.value=value;o.selected=state[key]===value;sel.append(o);});
    sel.onchange=()=>change(key,sel.value);wrap.append(sel);return wrap;};
  const keys=el('div',null,'sort-row sort-keys');keys.append(pick('sort','정렬',false),pick('sort2','같으면',true));
  const big={KR:1e12,US:1e10,JP:1e12,TW:1e11}[state.market];
  const presets=[[`대형주 중 RS 강한 순`,{minCap:big,sort:'rs',sort2:'highDistancePct'}],['RS 강한 순 → 신고가 가까운 순',{minCap:0,sort:'rs',sort2:'highDistancePct'}],['신고가 가까운 순 → RS',{minCap:0,sort:'highDistancePct',sort2:'rs'}]];
  const preset=el('label',null,'sort-pick sort-presets');preset.append(el('span','빠른 설정','sort-step'));
  const psel=el('select');psel.setAttribute('aria-label','빠른 설정');
  const none=el('option','선택…');none.value='';psel.append(none);
  presets.forEach(([label,set],i)=>{const o=el('option',label);o.value=String(i);o.selected=state.minCap===set.minCap&&state.sort===set.sort&&state.sort2===set.sort2;psel.append(o);});
  if(![...psel.options].some(o=>o.value&&o.selected))none.selected=true;
  psel.onchange=()=>{const set=presets[Number(psel.value)]?.[1];if(!set)return;Object.assign(state,set,{page:1});saveSort();render();};
  preset.append(psel);keys.append(preset);box.append(keys);
  return box;
}
// 인더스트리 액션: one compact ranked row per industry instead of tall cards.
// The period toggle is shared with the 산업 흐름 rail so both agree.
function industryList(m) {
  const value = g => railValue(m, g, railPeriod);
  const q = state.query.toLowerCase();
  const all = m.industries.filter(g => (!state.confirmed || g.confirmed) && (!q || g.name.toLowerCase().includes(q)));
  const shown = all.filter(g => state.smallGroups || bigEnough(g));
  shown.sort((a, b) => (value(b) ?? -Infinity) - (value(a) ?? -Infinity) || b.nearHighCount - a.nearHighCount);
  const hidden = all.length - shown.length;
  $('result-summary').textContent = `${shown.length}개 산업 · ${RAIL_PERIODS[railPeriod][0]} 중앙값 높은 순${hidden ? ` · 1~2종목 산업 ${hidden}개 숨김` : ''}`;
  const wrap = el('div', null, 'ind-list-wrap');
  const head = el('div', null, 'ind-list-head');
  const seg = el('div', null, 'rail-seg'); seg.setAttribute('aria-label', '순위 기준 기간');
  Object.entries(RAIL_PERIODS).forEach(([key, [label]]) => { const b = button(`${label} 기준`, railPeriod === key, () => { railPeriod = key; try { localStorage.setItem(RAIL_STORE, key); } catch {} render(); }); b.setAttribute('aria-pressed', String(railPeriod === key)); seg.append(b); });
  head.append(seg, el('span', '중앙값은 산업 안 종목 수익률을 줄 세웠을 때 가운데 값이라 대형주 하나에 휘둘리지 않아요', 'ind-list-note'));
  wrap.append(head);
  if (!shown.length) { wrap.append(el('div', '조건에 맞는 산업이 없습니다.', 'empty')); return wrap; }
  const table = el('div', null, 'ind-table'); table.setAttribute('role', 'table');
  const cols = [['#', 'c-rank'], ['산업', 'c-name'], ['종목', 'c-num'], ['당일', 'c-ret p-1d'], ['5일', 'c-ret p-5d'], ['20일', 'c-ret p-20d'], ['5일 상승', 'c-adv'], ['신고가권', 'c-num'], ['거래량↑', 'c-num']];
  const hr = el('div', null, 'ind-row ind-header'); hr.setAttribute('role', 'row');
  cols.forEach(([t, c]) => { const cell = el('span', t, `${c}${c.includes(`p-${railPeriod}`) ? ' sel' : ''}`); cell.setAttribute('role', 'columnheader'); hr.append(cell); });
  table.append(hr);
  const scale = Math.max(1, ...shown.map(g => Math.abs(value(g) || 0)));
  shown.forEach((g, i) => {
    const row = button('', false, () => openIndustry(g)); row.className = `ind-row${g.confirmed ? ' confirmed' : ''}`; row.setAttribute('role', 'row');
    const name = el('span', null, 'c-name'); name.append(el('strong', g.name));
    if (g.confirmed) name.append(el('em', '동반 강세', 'ind-chip'));
    const v = value(g), bar = el('i', null, 'ind-bar'); const fill = el('b', null, tone(v)); fill.style.width = `${Math.min(100, Math.abs(v || 0) / scale * 100)}%`; bar.append(fill); name.append(bar);
    const ret = (key, period) => el('span', pct(railValue(m, g, period)), `c-ret p-${period} ${tone(railValue(m, g, period))}${period === railPeriod ? ' sel' : ''}`);
    const adv = el('span', null, 'c-adv'); const a5 = g.advancing5dCount ?? 0;
    const track = el('i', null, 'adv-track'); const af = el('b'); af.style.width = `${g.memberCount ? a5 / g.memberCount * 100 : 0}%`; track.append(af);
    adv.append(el('span', `${a5}/${g.memberCount}`), track);
    row.append(el('span', String(i + 1), 'c-rank'), name, el('span', `${g.memberCount}`, 'c-num c-members'), ret('medianChangePct', '1d'), ret('medianReturn5d', '5d'), ret('medianReturn20d', '20d'), adv,
      el('span', `${g.nearHighCount}`, `c-num${g.nearHighCount ? ' hot' : ''}`), el('span', `${g.volumeConfirmedCount}`, `c-num${g.volumeConfirmedCount ? ' hot' : ''}`));
    row.setAttribute('aria-label', `${i + 1}위 ${g.name} · ${g.memberCount}종목 · ${RAIL_PERIODS[railPeriod][0]} ${pct(v)}`);
    table.append(row);
  });
  wrap.append(table);
  wrap.append(el('p', '동반 강세는 3종목 이상인 산업에서 오늘과 최근 5일 모두 여러 종목이 함께 오르고, 신고가권이나 거래량이 늘어난 종목이 2개 이상인 경우예요. 신고가권은 52주 고점에서 10% 이내인 종목 수, 거래량↑는 거래량이 평소의 1.5배 이상인 종목 수예요.', 'ind-foot'));
  return wrap;
}
function trendBar(g) {
  const wrap=el('div',null,'trend-breadth'),label=el('div',null,'trend-label');
  label.append(el('span','50일선 위'),el('strong',numeric(g.trendPct)?`${fmt(g.trendPct,0)}%`:'—'),el('span',`${g.trendAbove}/${g.trendCount}`));
  const bar=el('div',null,'trend-track'),fill=el('span');fill.style.width=`${g.trendPct||0}%`;bar.append(fill);bar.setAttribute('aria-hidden','true');
  const delta=numeric(g.trendDelta)?`${g.trendDelta>0?'+':''}${fmt(g.trendDelta)}%p`:'—';
  const foot=el('div',null,'trend-change');foot.append(el('span','5일 전 대비'),el('span',delta,tone(g.trendDelta)),el('span',`${g.trendPairCount}종목 비교`));
  wrap.append(label,bar,foot);return wrap;
}
function heatmap(groups) {
  const wrap=el('div',null,'heatmap-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','AI 기간별 수익률 비교 · 가로 스크롤 가능');
  const table=el('table',null,'ai-heatmap'),caption=el('caption','AI 분야별 기간 수익률 중앙값','sr-only'),head=el('thead'),tr=el('tr');
  const corner=el('th','분야 / 계산된 종목 수');corner.scope='col';tr.append(corner);
  Object.entries(PERIODS).forEach(([period,[label]])=>{const th=el('th');th.scope='col';const b=button(label,state.aiPeriod===period,()=>{state.sort='default';change('aiPeriod',period);});b.setAttribute('aria-label',`${label} 비교 정렬`);b.setAttribute('aria-pressed',String(state.aiPeriod===period));th.append(b);tr.append(th);});
  head.append(tr);table.append(caption,head);const body=el('tbody');
  groups.forEach(g=>{
    const row=el('tr'),th=el('th');th.scope='row';
    const name=button(g.name,state.aiSector===g.id,()=>change('aiSector',g.id));name.setAttribute('aria-label',`${g.name} 종목 보기`);name.setAttribute('aria-pressed',String(state.aiSector===g.id));th.append(name);row.append(th);
    Object.entries(PERIODS).forEach(([period,[label,key,countKey]])=>{
      const td=el('td'),value=g[key],selected=state.aiSector===g.id&&state.aiPeriod===period;
      const b=button('',selected,()=>{state.aiSector=g.id;state.sort='default';change('aiPeriod',period);});b.className=`heat-cell ${tone(value)}${selected?' selected':''}`;b.dataset.sector=g.id;b.dataset.period=period;
      b.setAttribute('aria-label',`${g.name} ${label} ${pct(value)} · 유효 ${g[countKey]}종목`);b.setAttribute('aria-pressed',String(selected));
      b.append(el('strong',pct(value)),el('small',`${g[countKey]}종목`));
      if(numeric(value)){
        const scale=Math.max(1,...groups.map(group=>Math.abs(group[key]||0))),alpha=.05+.24*Math.abs(value)/scale;
        b.style.background=`rgba(${value>=0?'98,215,181':'252,145,154'},${alpha})`;
      } else b.classList.add('no-data');
      td.append(b);row.append(td);
    });body.append(row);
  });table.append(body);wrap.append(table);return wrap;
}
function renderAI(m) {
  const panel=$('ai-overview');if(!panel)return;panel.replaceChildren();panel.hidden=state.tab!=='ai';
  if(panel.hidden)return;
  const overview=aiOverview(m,state.market),t=overview.total;
  overview.groups=overview.groups.filter(g=>g.catalogCount>0);   // e.g. Taiwan has no AI software names
  const [period,key,countKey]=PERIODS[state.aiPeriod];
  const top=el('div',null,'ai-heading'),caption=el('div');
  caption.append(el('h3','AI 분야별 흐름'),el('p','현재 시장의 AI 관련 종목 전체 기준 · 아래 종목 필터와는 따로 계산해요'));
  const medianHelp=el('span',null,'ai-heading-help');medianHelp.append(helpLabel('중앙값'),helpLabel('추세 확산도'));caption.querySelector('h3').append(medianHelp);
  const modes=el('div',null,'view-switch');modes.setAttribute('aria-label','AI 보기 방식');
  [['cards','카드'],['compare','기간 비교']].forEach(([value,label])=>{const b=button(label,state.aiView===value,()=>change('aiView',value));b.setAttribute('aria-pressed',String(state.aiView===value));modes.append(b);});
  top.append(caption,modes);panel.append(top);
  const toggles=el('div',null,'filter-group');toggles.setAttribute('aria-label','AI 분야 비교 기간');
  Object.entries(PERIODS).forEach(([v,[label]])=>{const b=button(label,state.aiPeriod===v,()=>change('aiPeriod',v));b.setAttribute('aria-pressed',String(state.aiPeriod===v));toggles.append(b);});
  const groups=[{...t,id:'all',name:'AI 전체',description:'같은 기업은 한 번만 셉니다'},...overview.groups];
  if(state.aiView==='compare'){
    panel.append(heatmap(groups));
    panel.append(el('p','칸을 누르면 그 분야·기간의 종목이 보여요 · 청록은 상승, 분홍은 하락 · 색 진하기는 같은 기간끼리 비교한 것이에요','heatmap-note'));
  } else {
  const toolbar=el('div',null,'ai-toolbar');toolbar.append(toggles,el('span','수익률 높은 분야부터 · 카드를 누르면 종목이 보여요'));panel.append(toolbar);
  groups.splice(1,groups.length-1,...overview.groups.sort((a,b)=>(b[key]??-Infinity)-(a[key]??-Infinity)));
  const scale=Math.max(1,...groups.map(g=>Math.abs(g[key]||0))),grid=el('div',null,'ai-grid');
  groups.forEach(g=>{
    const b=button('',state.aiSector===g.id,()=>change('aiSector',g.id));b.className=`ai-sector-card${state.aiSector===g.id?' active':''}`;b.dataset.aiSector=g.id;b.setAttribute('aria-pressed',String(state.aiSector===g.id));
    const title=el('div',null,'ai-card-title');title.append(el('strong',g.name),el('span',`${g.count}종목`));
    const ret=el('div',null,'ai-card-return');ret.append(el('strong',pct(g[key]),tone(g[key])),el('span',`${period} 중앙값 · ${g[countKey]}종목`));
    const bar=el('div',null,'flow-bar');bar.setAttribute('aria-hidden','true');
    if(numeric(g[key])){const fill=el('span',null,tone(g[key])),width=Math.abs(g[key])/scale*50;fill.style.width=`${width}%`;fill.style.left=`${g[key]<0?50-width:50}%`;bar.append(fill);}
    b.append(title,el('p',g.description,'ai-card-description'),ret,bar,el('small',`당일 상승 ${g.advancingCount}/${g.changeCount} · 고점 10% 이내 ${g.nearHighCount}/${g.eligible52w}`),trendBar(g));
    if(!g.count)b.append(el('span','확인된 가격 없음','ai-missing'));
    grid.append(b);
  });panel.append(grid);
  }
  const disclosure=el('details',null,'ai-method');disclosure.append(el('summary',`분류·집계 기준 · 관리 목록 ${t.catalogCount}종목 중 ${t.count}종목 표시${overview.missing.length?` · 빠진 종목 ${overview.missing.length}`:''}`));
  disclosure.append(el('p','AI 사업·공급망 연결성을 기준으로 따로 관리하는 관찰 목록입니다. AI 매출 비중이나 투자 매력도 순위가 아니며, 전력·장비·S/W에는 간접 노출 기업도 포함됩니다. 시장의 모든 AI 종목을 포괄하지 않습니다.'));
  disclosure.append(el('p','분야별 수익률은 계산할 수 있는 종목들의 수익률 중앙값이며, 시가총액으로 가중한 지수 수익률이 아닙니다. 여러 분야에 속한 기업은 분야마다 보여 주지만 AI 전체에서는 한 번만 셉니다. Alphabet은 GOOGL만 씁니다. 5·20·60·120·200일 수익률은 그만큼의 거래일 전 종가와 비교한 값입니다. 200일 수익률에는 종가 201개가 필요해서, 기록이 모자란 종목은 그 기간 계산에서 뺍니다. 지금의 관찰 목록으로 과거를 계산한 값이라 당시 구성 기준의 지수 수익률이나 200일 이동평균선과는 다릅니다. 데이터가 없는 값은 0으로 치지 않습니다.'));
  disclosure.append(el('p','추세 확산도는 종가가 최근 50거래일 평균보다 높은 종목의 비율입니다. 현재 비율은 50일 기록이 있는 종목만, 5일 전 대비 변화(%p)는 두 시점 모두 계산되는 같은 종목만 써서 구합니다. 거래일이 비거나 기록이 모자란 종목은 빼고, 비율 옆에 실제로 센 종목 수를 함께 적습니다. 지금 구성 종목 기준이며 돈이 얼마나 들어왔는지를 뜻하지는 않습니다.'));
  disclosure.append(el('p',`가격은 다른 시장 데이터와 함께 갱신됩니다. 새 AI 관련 기업은 자동으로 들어오지 않고 관리 목록에 직접 추가해야 합니다. 목록 점검일 ${AI_REVIEWED}.`));
  if(overview.missing.length)disclosure.append(el('p',`가격 데이터가 없어 빠진 종목: ${overview.missing.map(s=>s.symbol).join(', ')}. 스크리닝 대상 밖이거나 확인된 가격이 없어 계산에서 뺐습니다.`));
  const refs=el('p','분류 범위 참고: ');
  [['NVIDIA 데이터센터','https://www.nvidia.com/en-us/data-center/'],['Vertiv 전원·냉각','https://www.vertiv.com/en-emea/solutions/ai-hub/'],['Lumentum 광통신','https://www.lumentum.com/en/products/data-center'],['Microsoft AI 보안','https://www.microsoft.com/en-us/security/business/solutions/security-for-ai']].forEach(([label,url],i)=>{if(i)refs.append(document.createTextNode(' · '));refs.append(safeLink(label,url));});
  disclosure.append(refs);panel.append(disclosure);
}
function renderMeta(m) {
  const status = marketStatus(m), messages = [];
  if (status === 'STALE') messages.push(`${m.asOf || '—'} 거래일 가격을 그대로 보여 주고 있습니다. 최근 거래일(${m.expectedSession}) 자료가 들어오기를 기다리는 중입니다.`);
  if (status === 'PARTIAL' && !m.coverage.excluded) messages.push('새 가격 중 일부를 확인하지 못해 이전에 확인된 데이터를 그대로 보여 줍니다. 자세한 이유는 ‘데이터 범위와 기준’에서 볼 수 있어요.');
  if (refreshFailed) messages.push('가격 파일을 다시 불러오지 못해 마지막으로 확인된 데이터를 보여 줍니다.');
  $('notice').hidden = messages.length === 0; $('notice').textContent = messages.join(' ');
  $('date-info').replaceChildren(el('span', `${m.asOf || '—'} 거래일 종가`, 'session-date'), el('span', ({ CURRENT: '최신 거래일', PARTIAL: '일부 제외', STALE: '지난 거래일 자료', UNAVAILABLE: '데이터 없음' })[status], `badge status-${status.toLowerCase()}`));
  const refreshButton = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); refreshButton.className = 'refresh-button'; refreshButton.disabled = refreshing; $('date-info').append(refreshButton); $('date-info').append(runnerButton(state.market));
  const near = m.stocks.filter(s => numeric(s.highDistancePct) && s.highDistancePct <= 10), highs = m.stocks.filter(s => s.intradayHigh52 === true || s.closeHigh52 === true);
  const highs60 = m.stocks.filter(s => s.intradayHigh60 === true || s.closeHigh60 === true);
  const groups = m.industries.filter(g => g.confirmed), moves = selectStocks(m, { tab: 'movers' });
  const ai=aiOverview(m,state.market).total;
  const counts = { near: near.length, high: highs.length, high60: highs60.length, industries: groups.length, movers: moves.length,ai:ai.count,trend:trendTabLabel(state.market),ftd:ftdSummary(trendBlock(state.market),m.expectedSession,trendLoadFailed()||marketStatus(m)==='STALE'),leaders:leadersSummary(trendBlock(state.market)) };
  const countCriteria = { near: '전체 스크리닝 대상 중 고점 대비 10% 이내', high: '전체 스크리닝 대상 중 장중 또는 종가 52주 신고가', high60: '전체 스크리닝 대상 중 장중 또는 종가 60일 신고가', industries: '전체 스크리닝 대상 중 동반 강세 확인 그룹', movers: '전체 스크리닝 대상 중 변동 또는 거래량 특징주',ai:'가격이 확인된 AI 관련 종목 · 중복 제외',trend:'시장 날씨 · RS선 · 팔 때 경고 · 천장 신호 · 실적발표 · 신호 성적표',ftd:'지수별 하락 국면 → 반등 → FTD → 유지·실패',leaders:'선도주 출현 · 업종 RS 순위 · 선도주 목록' };
  document.querySelectorAll('[data-tab]').forEach(b => {
    let n = b.querySelector('.tab-count');
    if (!n) { n = el('span', null, 'tab-count'); b.append(n); }
    const count = counts[b.dataset.tab].toLocaleString();
    n.textContent = b.dataset.tab === 'industries' ? `강세 ${count}` : b.dataset.tab === 'near' ? `10% 내 ${count}` : count;
    b.title = `${countCriteria[b.dataset.tab]} · ${count}`;
    b.setAttribute('aria-label', `${b.firstChild.textContent} · ${b.title}`);
  });
  $('metrics').replaceChildren();
  const universeN = m.stocks.length, groupN = m.industries.length;
  [['52주 신고가권', near.length, '고점 대비 10% 이내', universeN, '종목', 'bar-accent'], ['당일 신고가', highs.length, '장중 또는 종가 경신', universeN, '종목', 'bar-accent'], ['산업 동반 강세', groups.length, '함께 오르는 산업', groupN, '산업', 'bar-accent'], ['특징주', moves.length, '상승 · 하락 · 거래량', universeN, '종목', 'bar-plain']].forEach(([label, value, sub, total, unit, barCls]) => {
    const box = el('div', null, 'metric'), caption=el('span'); caption.append(helpLabel(label));
    const share = total ? value / total : 0;
    const foot = el('div', null, 'metric-share'); foot.append(bar(share, barCls), el('em', total ? `${unit === '산업' ? '전체 산업' : '전체'} ${total.toLocaleString()} 중 ${fmt(share * 100, share < 0.1 ? 1 : 0)}%` : ''));
    box.append(caption, el('strong', value.toLocaleString()), el('small', sub), foot); $('metrics').append(box); });
  if(state.tab==='ai'){
    $('metrics').replaceChildren();
    [['AI 관찰 종목',`${ai.count}`,`관리 목록 ${ai.catalogCount}종목`],['당일 상승 비율',numeric(ai.advancingPct)?`${fmt(ai.advancingPct,0)}%`:'—',`${ai.changeCount}종목 중 ${ai.advancingCount}종목 상승`],['AI 신고가권',`${ai.nearHighCount}`,`고점 10% 이내 · ${ai.eligible52w}종목 중`],['AI 당일 신고가',`${ai.newHighCount}`,'장중 또는 종가 · 중복 제외']].forEach(([label,value,sub])=>{const box=el('div',null,'metric'),caption=el('span');caption.append(helpLabel(label));box.append(caption,el('strong',value),el('small',sub));$('metrics').append(box);});
  }
  if(state.tab==='trend')renderTrendMetrics($('metrics'),state.market);
  $('metrics').setAttribute('aria-label',state.tab==='ai'?'전체 AI 관찰 목록 집계':state.tab==='trend'?'추세 신호 요약':'전체 스크리닝 대상 집계');
  $('coverage-summary').textContent = `스크리닝 대상 ${m.coverage.valid}종목 · 52주 고점 비교 ${m.coverage.eligible52w}종목${m.coverage.excluded ? ' · 제외 ' + m.coverage.excluded + '종목' : ''} · 데이터 범위와 기준`;
  const computed = new Date(snapshot.generatedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
  $('coverage-detail').replaceChildren(el('p', `가격 출처: ${m.source} | 전체 ${m.coverage.total} · 사용 ${m.coverage.valid} · 제외 ${m.coverage.excluded}`), el('p', `가격 기준 거래일 ${m.asOf || '—'} · 계산 시각 ${computed} KST (실시간 가격이 아니에요)`), el('p', `최근 거래일 ${m.expectedSession} · 다음 갱신 ${new Date(m.nextScheduledFor).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`), el('p', '고점까지: 최근 252거래일 장중 최고가와 종가의 차이. 당일 신고가: 직전 251거래일의 최고가를 넘긴 경우(같은 값은 제외). 거래량 배수: 당일 거래량 ÷ 직전 50거래일 평균. RS·Stage 2는 골라서 거는 조건입니다.'), el('p', '산업 동반 강세 기준: 3종목 이상인 산업에서 당일 상승 2종목 이상, 5일 상승 2종목 이상이면서 전체의 절반 이상, 5일 수익률 중앙값 플러스, 그리고 신고가권 2종목 이상 또는 거래량 1.5배 이상 2종목 이상. 분류되지 않은 종목은 제외.'));
  m.warnings.forEach(w => $('coverage-detail').append(el('p', w)));
}
function renderChanges(m) {
  const panel=$('session-changes'),filter=$('signal-filter');
  // A cached prior template may load these assets before the next snapshot build.
  if(!panel||!filter)return;
  panel.replaceChildren();panel.hidden=state.tab==='industries'&&!state.industry;filter.replaceChildren();
  if(panel.hidden)return;
  checkbox('newOnly','새로 들어온 종목만',filter);filter.querySelector('input').disabled=!hasComparison(m);
  if(!hasComparison(m)){
    panel.append(el('summary','전일 변화 · 비교 자료 기다리는 중'),el('p',`직전 거래일 ${m.comparison?.expectedSession||'—'} 자료가 들어오면 보여 드려요. 자료가 빠진 것을 새로 들어오거나 빠진 것으로 세지 않아요.`));return;
  }
  const universe=selectStocks(m,{tab:state.tab==='ai'?'ai':'all',aiSector:state.aiSector,industry:state.industry,query:state.query,sort:'name'});
  const rows=universe.map(s=>({stock:s,...changes(s)})),entered=rows.filter(r=>r.entered.length),exited=rows.filter(r=>r.exited.length);
  const summary=el('summary');summary.append(el('span','전일 변화'),el('span',`신규 ${entered.length}`,'change-count'),el('span',`이탈 ${exited.length}`,'exit-count'),el('span',`${m.comparison.asOf.slice(5)} → ${m.asOf.slice(5)}`,'change-dates'));panel.append(summary);
  const guide=el('div',null,'changes-guide');guide.append(helpLabel('전일 변화'),el('span',`${state.tab==='ai'?'선택 AI 분야':state.industry?'선택 산업':'현재 시장'} · 검색어 반영 · RS·Stage 2 같은 조건은 걸기 전 기준 · 같은 종목은 한 번만`));panel.append(guide);
  panel.append(el('p',`비교할 수 있는 종목: 신고가권 ${rows.filter(r=>r.comparable.includes('near')).length} · RS ${rows.filter(r=>r.comparable.includes('rs')).length} · Stage 2 ${rows.filter(r=>r.comparable.includes('stage')).length}종목. 신고가권은 고점에서 ${state.tab==='near'?state.distance:10}% 이내 기준이에요. 새로 들어온 종목은 ‘새로 들어온 종목만’ 필터로 볼 수 있어요.`));
  const list=el('div',null,'exit-list');
  if(!exited.length)list.append(el('p','조건에서 빠진 종목이 없습니다.'));
  exited.forEach(({stock:s,exited:keys})=>{const b=button('',false,()=>detail(s));b.className='exit-item';b.append(el('strong',shortName(s)),el('span',keys.map(k=>EXIT_LABELS[k]).join(' · ')));list.append(b);});
  panel.append(list);
}
// 휴대폰에서 탭 줄이 옆으로 넘칠 때, 선택한 탭이 화면 밖에 숨지 않게 그 줄만 옆으로 움직인다(세로 스크롤은 건드리지 않음).
function revealActiveTab() {
  const b = document.querySelector('.tabs button.active'), g = b?.closest('.step-tabs') || b?.closest('.tab-group');
  if (!g || g.scrollWidth <= g.clientWidth + 1) return;
  const br = b.getBoundingClientRect(), gr = g.getBoundingClientRect();
  if (br.left >= gr.left && br.right <= gr.right) return;
  g.scrollLeft += br.left - gr.left - (gr.width - br.width) / 2;
}
function render() {
  closeHelp();
  if (!snapshot) {
    $('content').replaceChildren(el('div', '가격 데이터를 불러오는 중입니다.', 'empty'));
    const retry = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); retry.className = 'refresh-button'; $('date-info').replaceChildren(retry); return;
  }
  if (LITE.has(state.market) && LITE_OFF.has(state.tab)) state.tab = 'near';
  const m = snapshot.markets[state.market];
  document.querySelectorAll('[data-tab]').forEach(b => { const off = LITE.has(state.market) && LITE_OFF.has(b.dataset.tab); b.disabled = off; b.classList.toggle('tab-off', off); b.title = off ? `${MARKET_NAMES[state.market]} 시장은 아직 이 탭이 없어요 (신고가권·당일 신고가·인더스트리 액션·특징주만 제공)` : b.title; });
  if (!m) {
    document.querySelectorAll('[data-market]').forEach(b => { b.classList.toggle('active', b.dataset.market === state.market); b.setAttribute('aria-pressed', String(b.dataset.market === state.market)); });
    $('weather').hidden = true; if ($('index-strip')) $('index-strip').hidden = true; $('metrics').replaceChildren(); $('controls').replaceChildren(); $('industry-rail').hidden = true; $('pagination').replaceChildren(); $('result-summary').textContent = '';
    $('date-info').replaceChildren(el('span', '자료 준비 중', 'badge status-unavailable'));
    markStep();
    $('section-title').textContent = `${MARKET_NAMES[state.market]} 시장`; $('section-description').textContent = '';
    $('content').replaceChildren(el('div', `${MARKET_NAMES[state.market]} 자료는 평일 오후 4시 40분 첫 자동 갱신 뒤부터 보여요. 그 전에는 다른 시장을 확인해 주세요.`, 'empty'));
    paintSchedule(); return;
  }
  document.querySelectorAll('[data-market]').forEach(b => { b.classList.toggle('active', b.dataset.market === state.market); b.setAttribute('aria-pressed', String(b.dataset.market === state.market)); });
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b.dataset.tab === state.tab); b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'); });
  markStep();
  revealActiveTab();
  document.body.dataset.view = state.tab;
  renderMeta(m); renderStrip($('index-strip'), state.market); renderWeather($('weather'), state.market, openTrend); paintSchedule();
  const search=$('search').closest('.search'); if(search) search.hidden=['trend','ftd','leaders'].includes(state.tab);
  if (['trend','ftd','leaders'].includes(state.tab)) { if ($('sp-movers')) $('sp-movers').hidden = true; renderTrendTab(); return; }
  renderControls(); renderRail(m);renderAI(m);renderChanges(m);
  renderSpMovers($('sp-movers'), { market: state.market, tab: state.tab, onPick: t => { const s = (m.stocks || []).find(x => x.ticker === t); if (s) detail(s); else window.open(`https://finance.yahoo.com/quote/${encodeURIComponent(t.replace('.', '-'))}`, '_blank', 'noopener'); } });
  const labels = { near: ['52주 신고가권', '52주(252거래일) 고점에 가까운 종목 · 오늘 고점을 새로 쓴 종목은 ‘당일 신고가’ 탭에서 봐요'], high: ['당일 신고가', '이 거래일에 52주 고점을 실제로 넘긴 종목'], high60: ['60일 신고가', '이 거래일에 최근 60거래일(약 3개월) 고점을 넘긴 종목 · 52주보다 짧은 돌파라 조정 뒤 다시 올라오는 종목을 일찍 잡지만 가짜 돌파도 많아요'], industries: ['인더스트리 액션', '산업별 수익률 중앙값 순위 · 줄을 누르면 구성 종목이 보여요'], movers: ['특징주', '고점 위치와 관계없이 당일 변동과 거래량이 두드러진 종목'],ai:['AI 섹터','AI 공급망·서비스 종목의 흐름 · 오른 종목과 내린 종목 모두 표시 · 시장은 맨 위 버튼으로 바꿔요'] };
  $('section-title').textContent = state.industry ? state.industry.name : labels[state.tab][0]; $('section-description').textContent = labels[state.tab][1];
  const content = $('content'), pagination = $('pagination'); content.replaceChildren(); pagination.replaceChildren();
  if (state.tab === 'industries' && !state.industry) {
    content.append(industryList(m));
  } else {
    const rows = selectStocks(m, { ...state, tab: state.industry ? 'industry' : state.tab }), pages = Math.max(1, Math.ceil(rows.length / 30));
    state.page = Math.min(state.page, pages); content.append(table(rows));
    $('result-summary').textContent = `${rows.length}종목 · ${state.newOnly?'새로 들어온 종목 · 현재 조건 기준':'현재 조건 기준'}${state.industry ? ' · 선택한 산업 종목' : ''}${state.tab==='ai'?` · ${AI_SECTORS.find(g=>g.id===state.aiSector)?.name||'AI 전체'} · 중복 제외`:''}`;
    const toList = () => { const t = $('section-title'); const tabsH = document.querySelector('.tabs')?.getBoundingClientRect().height ?? 0; const top = (t?.getBoundingClientRect().top ?? 0) - tabsH; if (top < 0) window.scrollTo({ top: window.scrollY + top - 8, behavior: 'instant' }); };
    const prev = button('이전', false, () => { state.page--; render(); toList(); }), next = button('다음', false, () => { state.page++; render(); toList(); });
    prev.disabled = state.page <= 1; next.disabled = state.page >= pages;
    pagination.append(prev, el('span', `${rows.length}종목 · ${state.page} / ${pages}`), next);
    if(state.tab!=='ai'){const jump = el('a', '산업 흐름 보기 ↓', 'rail-jump'); jump.href = '#industry-rail'; $('result-summary').append(jump);}
  }
}
function switchTab(tab) {
  Object.assign(state, { tab, rs: false, stage: false, newOnly:false, query: '', industry: null, page: 1 }); $('search').value = ''; syncUrl(); render();
  keepTabsInView();
}
function openTrend() {
  setSubtab('weather');
  Object.assign(state, { tab: 'trend', rs: false, stage: false, newOnly:false, query: '', industry: null, page: 1 }); $('search').value = ''; syncUrl(); render();
  keepTabsInView();
}
// 현재 탭이 속한 단계 칸을 켜고, 그 단계에서 마지막으로 본 탭을 기억한다. 제목 위에는 단계 이름을 적는다.
function markStep() {
  const step = STEP_OF[state.tab] || 'market';
  lastTabOfStep[step] = state.tab;
  document.querySelectorAll('.tabs .tab-group').forEach(g => { const on = g.dataset.step === step; g.classList.toggle('active', on); g.querySelector('.step-head')?.setAttribute('aria-current', on ? 'step' : 'false'); });
  const label = $('section-step'); if (label) label.textContent = STEP_LABEL[step];
}
// After switching tabs from far down the page, bring the list back to its start
// instead of leaving the reader in the middle of a different list. The tab bar is
// sticky, so "in view" means its natural position (just under the header), not the stuck one.
function keepTabsInView() {
  const tabs = document.querySelector('.tabs'); if (!tabs) return;
  const prev = tabs.previousElementSibling, naturalTop = prev ? prev.getBoundingClientRect().bottom + window.scrollY : tabs.getBoundingClientRect().top + window.scrollY;
  if (window.scrollY > naturalTop) window.scrollTo({ top: Math.max(0, naturalTop - 4), behavior: 'instant' });
}
// 고정된 탭 줄 높이를 CSS 변수로 알려 주어, 추세 하위 탭과 표 머리줄이 그 아래에 붙게 한다.
(() => {
  const tabs = document.querySelector('.tabs'); if (!tabs) return;
  const set = () => document.documentElement.style.setProperty('--tabs-h', `${Math.ceil(tabs.getBoundingClientRect().height)}px`);
  set(); if ('ResizeObserver' in window) new ResizeObserver(set).observe(tabs); window.addEventListener('resize', set, { passive: true });
})();
document.querySelectorAll('[data-step-go]').forEach(b => b.onclick = () => {
  const step = b.dataset.stepGo, tab = lastTabOfStep[step];
  if (!tab || tab === state.tab) { keepTabsInView(); return; }
  if (tab === 'trend') setSubtab('weather');
  switchTab(tab);
});
function openStockById(id) {
  const s = snapshot?.markets?.[state.market]?.stocks?.find(x => x.id === id);
  if (s) detail(s);
  else { $('notice').hidden = false; $('notice').textContent = '이 종목은 오늘 레이더 목록에 없어 상세 화면을 열 수 없어요.'; }
}
function renderTrendTab() {
  $('controls').replaceChildren(); $('pagination').replaceChildren();
  const rail = $('industry-rail'); rail.replaceChildren(); rail.hidden = true;
  const ai = $('ai-overview'); if (ai) { ai.replaceChildren(); ai.hidden = true; }
  const changes = $('session-changes'); if (changes) { changes.replaceChildren(); changes.hidden = true; }
  $('signal-filter')?.replaceChildren();
  $('result-summary').textContent = '';
  if (state.tab === 'ftd') {
    $('section-title').textContent = '시장 전환·FTD';
    $('section-description').textContent = 'Follow-Through Day · 하락 뒤 반등이 진짜인지 확인';
    const m=snapshot.markets[state.market];
    renderFTD($('content'),state.market,trendBlock(state.market),{expected:m.expectedSession,failed:trendLoadFailed()||marketStatus(m)==='STALE',openLeaders:()=>switchTab('leaders')});
    return;
  }
  if (state.tab === 'leaders') {
    $('section-title').textContent = '선도주·선도업종';
    $('section-description').textContent = '선도주가 나오는지, 어떤 업종이 시장보다 강한지, 누가 앞장서는지';
    const m=snapshot.markets[state.market],b=trendBlock(state.market);
    renderLeaders($('content'),state.market,b,{openStock:openStockById,openFTD:()=>switchTab('ftd'),stale:!!b&&!!m.expectedSession&&b.asOf!==m.expectedSession});
    return;
  }
  $('section-title').textContent = '추세 신호';
  $('section-description').textContent = '지금 사도 되는 시장인지, 어떤 종목이 지수보다 강한지, 어떤 종목이 꺾이거나 끝물인지 한눈에 봅니다';
  renderTrend($('content'), state.market, { openStock: openStockById, onSubtab: syncUrl });
}
document.querySelectorAll('[data-market]').forEach(b => b.onclick = () => {
  const tab=['ai','trend','ftd','leaders'].includes(state.tab)&&!LITE_OFF.has(state.tab)?state.tab:'near',aiPeriod=state.aiPeriod,aiView=state.aiView;
  Object.assign(state, { ...defaults, market: b.dataset.market,tab,aiPeriod,aiView }); loadSort(state.market); $('search').value = '';
  syncUrl(); render();
});
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => {
  if (b.disabled) return;
  if (b.dataset.tab === 'trend' && state.tab !== 'trend') setSubtab('weather');
  Object.assign(state, { tab: b.dataset.tab, rs: false, stage: false, newOnly:false, query: '', industry: null, page: 1 }); $('search').value = '';syncUrl(); render();
  keepTabsInView();
});
// Floating "맨 위로" button for long lists.
(() => {
  const up = el('button', '↑', 'to-top'); up.type = 'button'; up.setAttribute('aria-label', '맨 위로'); up.hidden = true;
  up.onclick = () => window.scrollTo({ top: 0, behavior: 'smooth' });
  document.body.append(up);
  let ticking = false;
  window.addEventListener('scroll', () => { if (ticking) return; ticking = true; requestAnimationFrame(() => { up.hidden = window.scrollY < 900; ticking = false; }); }, { passive: true });
})();
function syncUrl(){const url=new URL(location.href);url.searchParams.set('market',state.market);url.searchParams.set('tab',state.tab);if(state.tab==='trend')url.searchParams.set('sub',getSubtab());else url.searchParams.delete('sub');history.replaceState(null,'',url);}
$('search').oninput = e => change('query', e.target.value);
$('close-drawer').onclick = () => $('drawer').close();
$('drawer').addEventListener('close', closeHelp);
$('drawer').onclick = e => { if (e.target === $('drawer')) $('drawer').close(); };
try { const embedded = JSON.parse($('wamo-snapshot')?.textContent || 'null'); if (validSnapshot(embedded)) snapshot = embedded; } catch {}
try { snapshot = acceptSnapshot(snapshot, JSON.parse(localStorage.getItem('wamo-verified-v1') || 'null')); } catch {}
render();
const performRefresh = createRefresher(() => snapshot, ({ snapshot: next, failed }) => {
  snapshot = next; refreshFailed = failed;
  if (snapshot) { try { localStorage.setItem('wamo-verified-v1', JSON.stringify(snapshot)); } catch {} render(); }
  else if (failed) { $('notice').hidden = false; $('notice').textContent = '가격 파일을 불러오지 못했습니다. 잠시 후 ‘데이터 확인’을 눌러 주세요.'; }
});
function refresh() {
  refreshing = true; updateRefreshButton();
  loadTrend(true).then(() => render());
  return performRefresh().finally(() => { refreshing = false; updateRefreshButton(); });
}
initRunner({ done: async () => { await refresh(); await loadTrend(true); render(); },
  marketStatus: market => snapshot?.markets?.[market] ? marketStatus(snapshot.markets[market]) : null,
  statusChanged: () => paintSchedule() });
function paintSchedule() {
  renderSchedule($('schedule'), getRefreshStatus(), trendBlock, state.market, snapshot);
  // Summary line stays useful when the box is folded: show any run in progress.
  const wrap = $('schedule-wrap'), sum = wrap?.querySelector('summary'), st = getRefreshStatus() || {};
  const busy = ['KR','US'].filter(k => ['RUNNING'].includes(st[k]?.status) || st[k]?.moversStatus === 'RUNNING' || st[k]?.radarStatus === 'RUNNING');
  if (sum) sum.textContent = busy.length ? `갱신 일정·진행 상황 · ${busy.map(k => k === 'KR' ? '한국' : '미국').join('·')} 갱신 중` : '갱신 일정·진행 상황';
}
(() => { const w = $('schedule-wrap'); if (!w) return;
  // Folded by default: the first screen is for the market, not the run log.
  try { w.open = localStorage.getItem('wamo-schedule-open') === '1'; } catch { w.open = false; }
  w.addEventListener('toggle', () => { try { localStorage.setItem('wamo-schedule-open', w.open ? '1' : '0'); } catch {} }); })();
function updateRefreshButton() {
  document.querySelectorAll('.refresh-button').forEach(b => { b.disabled = refreshing; b.textContent = refreshing ? '확인 중…' : '데이터 확인'; });
}
refresh();
setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 5 * 60 * 1000);
window.addEventListener('focus', () => { render(); refresh(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { render(); refresh(); } });
