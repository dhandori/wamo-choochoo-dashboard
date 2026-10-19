import { renderFTD, ftdSummary } from './ftd.js?v=20261019-3';
import { renderLeaders, leadersSummary } from './leaders.js?v=20261019-2';
import { CAP_FLOORS, SORT_KEYS, validSnapshot, acceptSnapshot, selectStocks, marketStatus, safeUrl, numeric, createRefresher, aiOverview, aiMembership, AI_SECTORS, AI_REVIEWED, signalChanges, priorSignal, hasComparison } from './model.mjs?v=20261019-6';
import { helpLabel, helpButton, closeHelp } from './help.js?v=20261019-1';
import { loadTrend, trendTabLabel, renderWeather, renderTrendMetrics, renderTrend, getSubtab, setSubtab, trendBlock, trendLoadFailed } from './trend.js?v=20261019-ui1';
import { runnerButton, initRunner, getRefreshStatus } from './runner.js?v=20261019-2';
import { renderSchedule } from './schedule.js?v=20261019-1';
import { loadChartData, stockSeries, priceChart, rsChart, MA } from './chart.js?v=20261019-ui1';
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
const defaults = { tab: 'near', distance: 10, high: 'either', mode: 'all', query: '', rs: false, stage: false, sort: 'default', confirmed: false, industry: null, page: 1, aiSector:'all', aiSignal:'all', aiPeriod:'5d',aiView:'cards',newOnly:false, sort2:'none', minCap:0 };
const PERIODS={'1d':['당일','medianChangePct','changeCount'],'5d':['5일','medianReturn5d','return5dCount'],'20d':['20일','medianReturn20d','return20dCount'],'60d':['60일','medianReturn60d','return60dCount'],'120d':['120일','medianReturn120d','return120dCount'],'200d':['200일','medianReturn200d','return200dCount']};
const CHANGE_LABELS={near:'신고가권 진입',rs:'RS 80 돌파',stage:'Stage 2 진입'};
const EXIT_LABELS={near:'신고가권 이탈',rs:'RS 80 하회',stage:'Stage 2 이탈'};
const params = new URLSearchParams(location.search);
const state = { ...defaults, market: params.get('market') === 'US' ? 'US' : 'KR', tab: ['near','high','industries','movers','ai','trend','ftd','leaders'].includes(params.get('tab'))?params.get('tab'):'near' };
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
const price = s => `${s.market === 'KR' ? '₩' : '$'}${fmt(s.close, s.market === 'KR' ? 0 : 2)}`;
const capCurrency = () => state.market === 'KR' ? '원' : '달러';
function marketCap(s, withCurrency = false) {
  const cap = s.marketCap;
  if (!numeric(cap) || cap <= 0) return '미제공';
  const unit = cap >= 1e12 ? [1e12, '조'] : cap >= 1e8 ? [1e8, '억'] : cap >= 1e4 ? [1e4, '만'] : [1, ''];
  return `${fmt(cap / unit[0], unit[0] === 1 ? 0 : 2)}${unit[1]}${withCurrency ? (s.market === 'KR' ? ' 원' : ' 달러') : ''}`;
}
function capNode(s, tag = 'span') {
  const node = el(tag, marketCap(s), 'market-cap');
  node.title = numeric(s.marketCap) && s.marketCap > 0 ? `${fmt(s.marketCap, 0)} ${s.market === 'KR' ? '원' : '달러'}` : '시가총액 미제공';
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
    return el('span', prominent ? '네이버증권 연결 미제공' : '미제공', prominent ? 'unavailable' : 'row-unavailable');
  }
  const a = safeLink(prominent ? '네이버증권 ↗' : '네이버 ↗', url, prominent ? 'naver-primary' : 'naver-row');
  a.setAttribute('aria-label', `${s.name} 네이버증권 새 탭`);
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
    const up = values.at(-1) >= values[0], color = up ? '#55d5b1' : '#fa8d96';
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
  return [s.intradayHigh52 ? '장중 신고가' : null, s.closeHigh52 ? '종가 신고가' : null,
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
function stat(label, value, cls = '') {
  const box = el('div', null, 'stat'), caption = el('span'); caption.append(helpLabel(label));
  box.append(caption, el('strong', value, cls)); return box;
}
// Wait briefly for the chart data so the drawer does not jump after opening.
function detail(s) {
  closeHelp();
  const market = s.market || state.market;
  const timeout = new Promise(resolve => setTimeout(() => resolve(undefined), 1500));
  Promise.race([loadChartData(market), timeout]).then(data => renderDetail(s, data));
}
function renderDetail(s, data) {
  const d = $('detail');
  const heading = el('h2', s.name); heading.id = 'stock-detail-title';
  d.replaceChildren(el('div', `${s.symbol} · ${s.market}${s.exchange ? ' / ' + s.exchange : ''}`, 'eyebrow'), heading, el('p', `${s.industry} · ${s.asOf} 거래일 종가`));
  const membership=aiMembership(s);
  if(membership)d.append(el('p',`AI 연관 분류: ${AI_SECTORS.filter(g=>membership.sectors.includes(g.id)).map(g=>g.name).join(' · ')} / ${membership.role}`,'ai-detail'));
  const quote = el('div', null, 'detail-quote');
  quote.append(el('strong', price(s)), el('span', pct(s.changePct), tone(s.changePct)), naverLink(s, true)); d.append(quote);
  const chart = el('section', null, 'chart-panel');
  chart.append(el('div', '최근 60거래일 · 종가 추이', 'chart-caption'), spark(s, true)); d.append(chart);
  if (data !== null) fillDetailChart(chart, s, data);
  const grid = el('div', null, 'detail-grid');
  [['시가총액', marketCap(s, true)], ['5일 수익률', pct(s.return5d), tone(s.return5d)], ['20일 수익률', pct(s.return20d), tone(s.return20d)], ['60일 수익률', pct(s.return60d), tone(s.return60d)], ['120일 수익률', pct(s.return120d), tone(s.return120d)], ['200일 수익률', pct(s.return200d), tone(s.return200d)],
    ['52주 고점 거리', numeric(s.highDistancePct) ? distance(s) : '이력 부족'], ['52주 장중 고점', fmt(s.high52, 2)],
    ['상대 거래량', volume(s)], ['RS', fmt(s.rs, 0)], ['Stage 2', s.stage2 === true ? '충족' : s.stage2 === false ? '미충족' : '미제공']]
    .forEach(([label, value, cls]) => grid.append(stat(label, value, cls)));
  d.append(grid);
  const signalBox = el('div', null, 'detail-signals');
  const reasons = signals(s); (reasons.length ? reasons : ['설정된 특징주 신호 없음']).forEach(t => signalBox.append(el('span', t, 'badge'))); d.append(signalBox);
  const transition=changes(s),comparison=snapshot.markets[state.market].comparison;
  const changeNote=el('section',null,'detail-changes');changeNote.append(helpLabel('전일 변화'));
  const eventLabels=[...transition.entered.map(k=>CHANGE_LABELS[k]),...transition.exited.map(k=>EXIT_LABELS[k])];
  changeNote.append(el('p',transition.comparable.length?`${comparison.asOf} → ${s.asOf} · ${eventLabels.join(' · ')||'비교 가능한 조건의 진입·이탈 없음'}`:'직전 거래일의 비교 자료가 부족합니다.'));
  d.append(changeNote);
  const evidence = el('section', null, 'evidence'); evidence.append(el('h3', '공식 공시 / 확인 자료'));
  const cats = Array.isArray(s.catalysts) ? s.catalysts : [];
  if (!cats.length) evidence.append(el('p', '등록된 공식 자료가 없습니다.'));
  cats.forEach(c => { const box = el('div', null, 'news-item'); box.append(safeLink(c.title, c.url), el('small', c.date)); evidence.append(box); });
  const news = Array.isArray(s.news) ? s.news : [];
  if (news.length) {
    evidence.append(el('h3', '관련 헤드라인'));
    news.forEach(n => {
      const date = n.date ? n.date.includes('T') ? new Date(n.date).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) + ' KST' : n.date : '날짜 미제공';
      const box = el('div', null, 'news-item'); box.append(safeLink(n.title, n.url), el('small', `${date} · ${n.publisher || ''}`)); evidence.append(box);
    });
  }
  evidence.append(el('p', '공시·헤드라인과 가격 움직임의 인과관계는 확인되지 않았습니다.', 'evidence-note')); d.append(evidence);
  d.append(el('p', `가격 출처: ${s.source} · 산업 분류: ${s.industrySource}`, 'source-note'));
  $('drawer').showModal(); $('drawer').scrollTop = 0;
}
let chartDays = 60;
function fillDetailChart(panel, s, ready) {
  const market = s.market || state.market;
  const apply = data => {
    const series = data && stockSeries(data, s.symbol);
    if (!series || !panel.isConnected) return;
    const draw = () => {
      const head = el('div', null, 'chart-head');
      head.append(el('div', `일봉 · 최근 ${chartDays}거래일`, 'chart-caption'));
      const toggle = el('div', null, 'chart-toggle');
      [[60, '3개월'], [120, '6개월']].forEach(([n, label]) => toggle.append(button(label, chartDays === n, () => { chartDays = n; draw(); })));
      head.append(toggle);
      const body = [head, priceChart(series, { market, days: chartDays })];
      if (chartDays > series.closes.length - 199) body.push(el('p', `200일선은 저장된 가격 이력(${series.closes.length}거래일) 한계로 최근 ${series.closes.length - 199}거래일만 그려져요.`, 'chart-note'));
      const rs = rsChart(series, { days: chartDays });
      if (rs) body.push(rs);
      panel.replaceChildren(...body);
    };
    draw();
  };
  if (ready) apply(ready); else loadChartData(market).then(apply);
}
function table(rows) {
  if (!rows.length) return el('div', '조건에 맞는 종목이 없습니다. 필터를 조정해 보세요.', 'empty');
  const pageRows = rows.slice((state.page - 1) * 30, state.page * 30);
  const longDays=['60d','120d','200d'].includes(state.aiPeriod)?parseInt(state.aiPeriod,10):60,longKey=`return${longDays}d`;
  const wrap = el('div', null, 'table-wrap'), table = el('table'), head = el('thead'), tr = el('tr');
  [state.tab==='ai'?'종목 / AI 연결 분야':'종목 / 산업', `시총 (${capCurrency()})`, '종가', '당일', '5일', ...(state.tab==='ai'?['20일',`${longDays}일`]:[]), '고점까지', '거래량 배수', 'RS', '60일 추이', '바로가기'].forEach(t => { const th = el('th'); th.scope = 'col'; th.setAttribute('aria-label', t); th.append(helpLabel(t)); tr.append(th); });
  head.append(tr); table.append(head); const body = el('tbody');
  pageRows.forEach(s => {
    const row = el('tr'), name = el('td'); name.append(stockButton(s));
    if (['high','movers','ai'].includes(state.tab)) name.append(el('span', signals(s).join(' · '), 'signal'));
    row.append(name, capNode(s, 'td'), el('td', price(s)), el('td', pct(s.changePct), tone(s.changePct)), el('td', pct(s.return5d), tone(s.return5d)));
    if(state.tab==='ai')row.append(el('td',pct(s.return20d),tone(s.return20d)),el('td',pct(s[longKey]),tone(s[longKey])));
    row.append(el('td', distance(s)), el('td', volume(s)), el('td', fmt(s.rs, 0)));
    const chart = el('td'); chart.append(spark(s)); const link = el('td'); link.append(naverLink(s)); row.append(chart, link); body.append(row);
  });
  table.append(body); wrap.append(table);
  const cards = el('div', null, 'stock-cards');
  pageRows.forEach(s => {
    const card = el('article', null, 'stock-card'), top = el('div', null, 'stock-card-top'); top.append(stockButton(s), naverLink(s));
    const quote = el('div', null, 'card-quote'); quote.append(el('strong', price(s)), el('span', pct(s.changePct), tone(s.changePct)), spark(s));
    const stats = el('div', null, 'card-stats'); stats.append(stat('5일', pct(s.return5d), tone(s.return5d)), stat('고점까지', distance(s)), stat('거래량 배수', volume(s)), stat('RS', fmt(s.rs, 0)));
    if(state.tab==='ai')stats.append(stat('20일',pct(s.return20d),tone(s.return20d)),stat(`${longDays}일`,pct(s[longKey]),tone(s[longKey])));
    const cap = el('div', null, 'card-cap'); cap.append(el('span', '시총'), capNode(s), el('span', capCurrency()));
    const chartBox = el('div', null, 'card-chart'); chartBox.dataset.symbol = s.symbol;
    card.append(top, cap, quote, chartBox, stats);
    if (['high','movers','ai'].includes(state.tab)) card.append(el('div', signals(s).join(' · '), 'signal'));
    cards.append(card);
  });
  const container = el('div'); container.append(wrap, cardLegend(), cards);
  requestAnimationFrame(() => fillCardCharts(container));
  return container;
}
const rankGroups = (a, b) => Number(b.confirmed) - Number(a.confirmed) || Number(b.memberCount >= 3) - Number(a.memberCount >= 3) || (b.medianReturn5d ?? -Infinity) - (a.medianReturn5d ?? -Infinity) || b.nearHighCount - a.nearHighCount;
function openIndustry(g) {
  Object.assign(state, { tab: 'industries', industry: g, query: '', rs: false, stage: false, newOnly:false, page: 1 }); $('search').value = ''; render();
}
function renderRail(m) {
  const rail = $('industry-rail'); rail.replaceChildren(); rail.hidden = state.tab==='ai'||state.tab === 'industries' && !state.industry;
  if (rail.hidden) return;
  rail.append(el('div', 'INDUSTRY FLOW', 'eyebrow'), el('h3', '산업 흐름'), el('p', '전체 대상 · 3종목 이상 그룹', 'rail-caption'));
  const groups = m.industries.filter(g => g.memberCount >= 3).sort(rankGroups).slice(0, 6);
  const scale = Math.max(1, ...groups.map(g => Math.abs(g.medianReturn5d || 0)));
  if (!groups.length) rail.append(el('p', '비교 가능한 산업 그룹이 없습니다.'));
  // On phones the strip scrolls sideways under the tabs; on desktop it is the side rail.
  const strip = el('div', null, 'rail-strip'); rail.append(strip);
  groups.forEach(g => {
    const b = button('', false, () => openIndustry(g)); b.className = 'rail-group';
    const top = el('div', null, 'rail-group-top'); top.append(el('strong', g.name), el('span', pct(g.medianReturn5d), tone(g.medianReturn5d)));
    const bar = el('div', null, 'flow-bar'); bar.setAttribute('aria-hidden', 'true');
    if (numeric(g.medianReturn5d)) {
      const fill = el('span', null, tone(g.medianReturn5d));
      const width = Math.abs(g.medianReturn5d) / scale * 50;
      fill.style.width = `${width}%`; fill.style.left = `${g.medianReturn5d < 0 ? 50 - width : 50}%`; bar.append(fill);
    }
    b.append(top, bar, el('small', `5일 중앙값 · ${g.memberCount}종목 · 신고가권 ${g.nearHighCount}`), el('span', g.confirmed ? '동반 강세 확인' : '관찰', `flow-status ${g.confirmed ? 'confirmed' : ''}`)); strip.append(b);
  });
  rail.append(button('전체 산업 보기 →', false, () => { Object.assign(state, { tab: 'industries', industry: null, query: '', rs: false, stage: false, newOnly:false, page: 1 }); $('search').value = ''; render(); }));
}
function checkbox(key, text, parent) {
  const label = el('label', null, 'checkbox-control'), input = el('input'); input.type = 'checkbox'; input.checked = state[key]; input.onchange = () => change(key, input.checked);
  label.append(input, document.createTextNode(` ${text}`));
  const wrap = el('div', null, 'checkbox-help'); wrap.append(label, helpButton(text, key, true)); parent.append(wrap);
}
function resetFilters() {
  for (const key of ['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'sort2', 'minCap', 'confirmed', 'page','aiSector','aiSignal','aiPeriod','newOnly']) state[key] = defaults[key];
  saveSort(); $('search').value = ''; render();
}
function renderControls() {
  const controls = $('controls'); controls.replaceChildren();
  if (state.industry) controls.append(button('← 전체 산업', false, () => { state.industry = null; state.page = 1; render(); }));
  const group = el('div', null, 'filter-group');
  if (state.tab === 'near') {
    const label = el('span', null, 'filter-label'); label.append(helpLabel('고점 거리')); group.append(label); [5, 10, 15].forEach(v => group.append(button(`${v}% 이내`, state.distance === v, () => change('distance', v))));
  }
  if (state.tab === 'high') [['either', '모두'], ['intraday', '장중 고점'], ['close', '종가 고점']].forEach(([v, text]) => group.append(button(text, state.high === v, () => change('high', v))));
  if (state.tab === 'movers') [['all', '전체'], ['up', '상승 ≥3%'], ['down', '하락 ≤−3%'], ['volume', '거래량 ≥1.5×']].forEach(([v, text]) => group.append(button(text, state.mode === v, () => change('mode', v))));
  if(state.tab==='ai') {
    const sectors=el('select');sectors.setAttribute('aria-label','AI 분야');
    [{id:'all',name:'AI 전체'},...AI_SECTORS].forEach(g=>{const o=el('option',g.name);o.value=g.id;o.selected=state.aiSector===g.id;sectors.append(o);});
    sectors.onchange=()=>change('aiSector',sectors.value);group.append(sectors);
    const signal=el('select');signal.setAttribute('aria-label','AI 종목 조건');
    [['all','모든 AI 종목'],['near','신고가권 10% 이내'],['high','당일 신고가'],['up','상승 ≥3%'],['down','하락 ≤−3%'],['volume','거래량 ≥1.5×']].forEach(([value,text])=>{const o=el('option',text);o.value=value;o.selected=state.aiSignal===value;signal.append(o);});
    signal.onchange=()=>change('aiSignal',signal.value);group.append(signal);
  }
  if (group.childNodes.length) controls.append(group);
  if (state.tab === 'industries' && !state.industry) {
    checkbox('confirmed', '확인된 그룹만', controls);
  } else {
    const options = el('div', null, 'filter-group'); checkbox('rs', 'RS ≥80', options); checkbox('stage', 'Stage 2', options); controls.append(options);
    controls.append(sortPanel());
  }
  if (['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'sort2', 'minCap', 'confirmed','aiSector','aiSignal','aiPeriod','newOnly'].some(key => state[key] !== defaults[key])) {
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
  const big=state.market==='KR'?1e12:1e10;
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
function trendBar(g) {
  const wrap=el('div',null,'trend-breadth'),label=el('div',null,'trend-label');
  label.append(el('span','50일선 위'),el('strong',numeric(g.trendPct)?`${fmt(g.trendPct,0)}%`:'—'),el('span',`${g.trendAbove}/${g.trendCount}`));
  const bar=el('div',null,'trend-track'),fill=el('span');fill.style.width=`${g.trendPct||0}%`;bar.append(fill);bar.setAttribute('aria-hidden','true');
  const delta=numeric(g.trendDelta)?`${g.trendDelta>0?'+':''}${fmt(g.trendDelta)}%p`:'—';
  const foot=el('div',null,'trend-change');foot.append(el('span','5일 전 대비'),el('span',delta,tone(g.trendDelta)),el('span',`비교 ${g.trendPairCount}종목`));
  wrap.append(label,bar,foot);return wrap;
}
function heatmap(groups) {
  const wrap=el('div',null,'heatmap-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','AI 기간별 수익률 비교 · 가로 스크롤 가능');
  const table=el('table',null,'ai-heatmap'),caption=el('caption','AI 분야별 기간 수익률 중앙값','sr-only'),head=el('thead'),tr=el('tr');
  const corner=el('th','분야 / 유효 종목');corner.scope='col';tr.append(corner);
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
  const [period,key,countKey]=PERIODS[state.aiPeriod];
  const top=el('div',null,'ai-heading'),caption=el('div');
  caption.append(el('h3','AI 분야별 흐름'),el('p','현재 시장의 전체 AI 대상 · 아래 종목 필터와 독립'));
  const medianHelp=el('span',null,'ai-heading-help');medianHelp.append(helpLabel('중앙값'),helpLabel('추세 확산도'));caption.querySelector('h3').append(medianHelp);
  const modes=el('div',null,'view-switch');modes.setAttribute('aria-label','AI 보기 방식');
  [['cards','카드'],['compare','기간 비교']].forEach(([value,label])=>{const b=button(label,state.aiView===value,()=>change('aiView',value));b.setAttribute('aria-pressed',String(state.aiView===value));modes.append(b);});
  top.append(caption,modes);panel.append(top);
  const toggles=el('div',null,'filter-group');toggles.setAttribute('aria-label','AI 분야 비교 기간');
  Object.entries(PERIODS).forEach(([v,[label]])=>{const b=button(label,state.aiPeriod===v,()=>change('aiPeriod',v));b.setAttribute('aria-pressed',String(state.aiPeriod===v));toggles.append(b);});
  const groups=[{...t,id:'all',name:'AI 전체',description:'기업 중복 없이 집계'},...overview.groups];
  if(state.aiView==='compare'){
    panel.append(heatmap(groups));
    panel.append(el('p','셀 선택 → 해당 분야·기간 종목 보기 · 청록 + / 분홍 − · 색 농도는 같은 기간 안에서 비교','heatmap-note'));
  } else {
  const toolbar=el('div',null,'ai-toolbar');toolbar.append(toggles,el('span','수익률 높은 분야부터 · 카드 선택으로 종목 보기'));panel.append(toolbar);
  groups.splice(1,groups.length-1,...overview.groups.sort((a,b)=>(b[key]??-Infinity)-(a[key]??-Infinity)));
  const scale=Math.max(1,...groups.map(g=>Math.abs(g[key]||0))),grid=el('div',null,'ai-grid');
  groups.forEach(g=>{
    const b=button('',state.aiSector===g.id,()=>change('aiSector',g.id));b.className=`ai-sector-card${state.aiSector===g.id?' active':''}`;b.dataset.aiSector=g.id;b.setAttribute('aria-pressed',String(state.aiSector===g.id));
    const title=el('div',null,'ai-card-title');title.append(el('strong',g.name),el('span',`${g.count}종목`));
    const ret=el('div',null,'ai-card-return');ret.append(el('strong',pct(g[key]),tone(g[key])),el('span',`${period} 중앙값 · ${g[countKey]}종목`));
    const bar=el('div',null,'flow-bar');bar.setAttribute('aria-hidden','true');
    if(numeric(g[key])){const fill=el('span',null,tone(g[key])),width=Math.abs(g[key])/scale*50;fill.style.width=`${width}%`;fill.style.left=`${g[key]<0?50-width:50}%`;bar.append(fill);}
    b.append(title,el('p',g.description,'ai-card-description'),ret,bar,el('small',`당일 상승 ${g.advancingCount}/${g.changeCount} · 고점 10% 내 ${g.nearHighCount}/${g.eligible52w}`),trendBar(g));
    if(!g.count)b.append(el('span','현재 검증 가격 없음','ai-missing'));
    grid.append(b);
  });panel.append(grid);
  }
  const disclosure=el('details',null,'ai-method');disclosure.append(el('summary',`분류·집계 기준 · 관리 목록 ${t.catalogCount}종목 중 ${t.count}종목 표시${overview.missing.length?` · 미포함 ${overview.missing.length}`:''}`));
  disclosure.append(el('p','AI 사업·공급망 연결성을 기준으로 따로 관리하는 관찰 목록입니다. AI 매출 비중이나 투자 매력도 순위가 아니며, 전력·장비·S/W에는 간접 노출 기업도 포함됩니다. 시장의 모든 AI 종목을 포괄하지 않습니다.'));
  disclosure.append(el('p','분야별 수익률은 유효한 종목 수익률의 중앙값으로, 시가총액 가중 지수 수익률이 아닙니다. 여러 분야에 속한 기업은 각 분야에 표시하되 AI 전체에서는 한 번만 집계합니다. Alphabet은 GOOGL만 사용합니다. 5·20·60·120·200일은 거래일 기준으로, 해당 거래일 전 종가 대비 수익률입니다. 200일 수익률에는 종가 201개가 필요하며, 이력이 부족한 종목은 해당 기간의 집계에서 제외합니다. 현재 관찰 목록 기준의 과거 수익률로 당시 구성 종목의 지수 수익률이나 200일 이동평균선과는 다릅니다. 데이터 없는 값은 0으로 계산하지 않습니다.'));
  disclosure.append(el('p','추세 확산도는 종가가 50거래일 종가 평균보다 높은 종목의 비율입니다. 현재 비율은 유효한 50일 이력이 있는 종목, 5일 전 대비 변화(%p)는 양 시점 모두 계산되는 동일 종목만 사용합니다. 거래일 누락·이력 부족 종목은 제외하며 비율 옆에 실제 분모를 표시합니다. 현재 구성 종목 기준이며 자금 유입량을 뜻하지 않습니다.'));
  disclosure.append(el('p',`가격은 기존 시장 데이터와 함께 갱신합니다. 신규 AI 관련 기업은 관리 목록에 추가해야 하며 자동 편입되지 않습니다. 목록 점검일 ${AI_REVIEWED}.`));
  if(overview.missing.length)disclosure.append(el('p',`현재 가격 데이터 미포함: ${overview.missing.map(s=>s.symbol).join(', ')}. 기존 스크리닝 대상 밖이거나 검증 가격이 없는 경우이며 집계에서 제외합니다.`));
  const refs=el('p','분류 범위 참고: ');
  [['NVIDIA 데이터센터','https://www.nvidia.com/en-us/data-center/'],['Vertiv 전원·냉각','https://www.vertiv.com/en-emea/solutions/ai-hub/'],['Lumentum 광통신','https://www.lumentum.com/en/products/data-center'],['Microsoft AI 보안','https://www.microsoft.com/en-us/security/business/solutions/security-for-ai']].forEach(([label,url],i)=>{if(i)refs.append(document.createTextNode(' · '));refs.append(safeLink(label,url));});
  disclosure.append(refs);panel.append(disclosure);
}
function renderMeta(m) {
  const status = marketStatus(m), messages = [];
  if (status === 'STALE') messages.push(`${m.asOf || '미제공'} 거래일 가격을 유지합니다. 예상 최근 거래일 ${m.expectedSession}의 갱신을 기다리고 있습니다.`);
  if (status === 'PARTIAL' && !m.coverage.excluded) messages.push('일부 새 가격의 검증 범위가 부족해 이전 검증 데이터를 유지합니다. 데이터 기준에서 상세 사유를 확인하세요.');
  if (refreshFailed) messages.push('가격 파일을 다시 확인하지 못해 마지막 검증 데이터를 유지합니다.');
  $('notice').hidden = messages.length === 0; $('notice').textContent = messages.join(' ');
  $('date-info').replaceChildren(el('span', `${m.asOf || '미제공'} 거래일 종가`, 'session-date'), el('span', ({ CURRENT: '최신 거래일', PARTIAL: '일부 제외', STALE: '지난 거래일', UNAVAILABLE: '데이터 없음' })[status], `badge status-${status.toLowerCase()}`));
  const refreshButton = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); refreshButton.className = 'refresh-button'; refreshButton.disabled = refreshing; $('date-info').append(refreshButton, runnerButton(state.market));
  const near = m.stocks.filter(s => numeric(s.highDistancePct) && s.highDistancePct <= 10), highs = m.stocks.filter(s => s.intradayHigh52 === true || s.closeHigh52 === true);
  const groups = m.industries.filter(g => g.confirmed), moves = selectStocks(m, { tab: 'movers' });
  const ai=aiOverview(m,state.market).total;
  const counts = { near: near.length, high: highs.length, industries: groups.length, movers: moves.length,ai:ai.count,trend:trendTabLabel(state.market),ftd:ftdSummary(trendBlock(state.market),m.expectedSession,trendLoadFailed()||marketStatus(m)==='STALE'),leaders:leadersSummary(trendBlock(state.market)) };
  const countCriteria = { near: '전체 스크리닝 대상 중 고점 대비 10% 이내', high: '전체 스크리닝 대상 중 장중 또는 종가 신고가', industries: '전체 스크리닝 대상 중 동반 강세 확인 그룹', movers: '전체 스크리닝 대상 중 변동 또는 거래량 특징주',ai:'검증 가격이 있는 AI 관련 종목 · 중복 제외',trend:'시장 날씨 · RS선 · 팔 때 경고 · 실적발표 · 신호 성적표',ftd:'지수별 하락 국면 → 반등 → FTD → 유지·실패',leaders:'선도주 출현 · 업종 RS 순위 · 선도주 목록' };
  document.querySelectorAll('[data-tab]').forEach(b => {
    let n = b.querySelector('.tab-count');
    if (!n) { n = el('span', null, 'tab-count'); b.append(n); }
    const count = counts[b.dataset.tab].toLocaleString();
    n.textContent = b.dataset.tab === 'industries' ? `강세 ${count}` : b.dataset.tab === 'near' ? `10% 내 ${count}` : count;
    b.title = `${countCriteria[b.dataset.tab]} · ${count}`;
    b.setAttribute('aria-label', `${b.firstChild.textContent} · ${b.title}`);
  });
  $('metrics').replaceChildren();
  [['52주 신고가권', near.length, '고점 대비 10% 이내'], ['당일 신고가', highs.length, '장중 또는 종가 경신'], ['산업 동반 강세', groups.length, '확인된 그룹'], ['특징주', moves.length, '상승 · 하락 · 거래량']].forEach(([label, value, sub]) => { const box = el('div', null, 'metric'), caption=el('span'); caption.append(helpLabel(label)); box.append(caption, el('strong', value.toLocaleString()), el('small', sub)); $('metrics').append(box); });
  if(state.tab==='ai'){
    $('metrics').replaceChildren();
    [['AI 관찰 종목',`${ai.count}`,`관리 목록 ${ai.catalogCount}종목`],['당일 상승 비율',numeric(ai.advancingPct)?`${fmt(ai.advancingPct,0)}%`:'—',`유효 ${ai.changeCount}종목 중 ${ai.advancingCount}종목`],['AI 신고가권',`${ai.nearHighCount}`,`52주 비교 ${ai.eligible52w}종목 · 10% 이내`],['AI 당일 신고가',`${ai.newHighCount}`,'장중 또는 종가 · 중복 제외']].forEach(([label,value,sub])=>{const box=el('div',null,'metric'),caption=el('span');caption.append(helpLabel(label));box.append(caption,el('strong',value),el('small',sub));$('metrics').append(box);});
  }
  if(state.tab==='trend')renderTrendMetrics($('metrics'),state.market);
  $('metrics').setAttribute('aria-label',state.tab==='ai'?'전체 AI 관찰 목록 집계':state.tab==='trend'?'추세 신호 요약':'전체 스크리닝 대상 집계');
  $('coverage-summary').textContent = `전체 스크리닝 ${m.coverage.valid}종목 · 52주 비교 ${m.coverage.eligible52w}종목${m.coverage.excluded ? ' · 제외 ' + m.coverage.excluded + '종목' : ''} · 데이터 기준`;
  const computed = new Date(snapshot.generatedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
  $('coverage-detail').replaceChildren(el('p', `가격 출처: ${m.source} | 대상 ${m.coverage.total} · 유효 ${m.coverage.valid} · 제외 ${m.coverage.excluded}`), el('p', `가격 거래일 ${m.asOf || '—'} · 화면 계산 ${computed} KST (실시간 가격 아님)`), el('p', `예상 최근 거래일 ${m.expectedSession} · 다음 갱신 ${new Date(m.nextScheduledFor).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`), el('p', '고점 거리: 최근 252개 거래일 장중 고점 대비 종가의 거리. 당일 신고가: 이전 251개 관측치 고점을 초과(동률 제외). 거래량: 당일 / 이전 50거래일 평균. RS·Stage 2는 선택 조건입니다.'), el('p', '산업 동반 강세 기준: 서로 다른 3종목 이상, 당일 상승 2종목 이상, 5일 상승 2종목 이상이면서 전체의 50% 이상, 5일 수익률 중앙값 양수, 신고가권 2종목 이상 또는 거래량 1.5배 이상 2종목 이상. 미분류 그룹 제외.'));
  m.warnings.forEach(w => $('coverage-detail').append(el('p', w)));
}
function renderChanges(m) {
  const panel=$('session-changes'),filter=$('signal-filter');
  // A cached prior template may load these assets before the next snapshot build.
  if(!panel||!filter)return;
  panel.replaceChildren();panel.hidden=state.tab==='industries'&&!state.industry;filter.replaceChildren();
  if(panel.hidden)return;
  checkbox('newOnly','새로 포착만',filter);filter.querySelector('input').disabled=!hasComparison(m);
  if(!hasComparison(m)){
    panel.append(el('summary','전일 변화 · 비교 자료 대기'),el('p',`직전 거래일 ${m.comparison?.expectedSession||'—'} 자료가 확인되면 표시합니다. 자료 누락을 신규 진입이나 이탈로 계산하지 않습니다.`));return;
  }
  const universe=selectStocks(m,{tab:state.tab==='ai'?'ai':'all',aiSector:state.aiSector,industry:state.industry,query:state.query,sort:'name'});
  const rows=universe.map(s=>({stock:s,...changes(s)})),entered=rows.filter(r=>r.entered.length),exited=rows.filter(r=>r.exited.length);
  const summary=el('summary');summary.append(el('span','전일 변화'),el('span',`신규 ${entered.length}`,'change-count'),el('span',`이탈 ${exited.length}`,'exit-count'),el('span',`${m.comparison.asOf.slice(5)} → ${m.asOf.slice(5)}`,'change-dates'));panel.append(summary);
  const guide=el('div',null,'changes-guide');guide.append(helpLabel('전일 변화'),el('span',`${state.tab==='ai'?'선택 AI 분야':state.industry?'선택 산업':'현재 시장'} · 검색 반영 · RS·Stage 2 등 종목 조건 적용 전 · 종목 중복 제외`));panel.append(guide);
  panel.append(el('p',`비교 가능: 신고가권 ${rows.filter(r=>r.comparable.includes('near')).length} · RS ${rows.filter(r=>r.comparable.includes('rs')).length} · Stage 2 ${rows.filter(r=>r.comparable.includes('stage')).length}종목. 신고가권은 고점 ${state.tab==='near'?state.distance:10}% 이내 기준입니다. 신규 목록은 ‘새로 포착만’ 필터로 확인하세요.`));
  const list=el('div',null,'exit-list');
  if(!exited.length)list.append(el('p','비교 가능한 종목 중 조건 이탈이 없습니다.'));
  exited.forEach(({stock:s,exited:keys})=>{const b=button('',false,()=>detail(s));b.className='exit-item';b.append(el('strong',shortName(s)),el('span',keys.map(k=>EXIT_LABELS[k]).join(' · ')));list.append(b);});
  panel.append(list);
}
function render() {
  closeHelp();
  if (!snapshot) {
    $('content').replaceChildren(el('div', '검증된 가격 데이터를 불러오는 중입니다.', 'empty'));
    const retry = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); retry.className = 'refresh-button'; $('date-info').replaceChildren(retry); return;
  }
  const m = snapshot.markets[state.market];
  document.querySelectorAll('[data-market]').forEach(b => { b.classList.toggle('active', b.dataset.market === state.market); b.setAttribute('aria-pressed', String(b.dataset.market === state.market)); });
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b.dataset.tab === state.tab); b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'); });
  document.body.dataset.view = state.tab;
  renderMeta(m); renderWeather($('weather'), state.market, openTrend); paintSchedule();
  const search=$('search').closest('.search'); if(search) search.hidden=['trend','ftd','leaders'].includes(state.tab);
  if (['trend','ftd','leaders'].includes(state.tab)) { renderTrendTab(); return; }
  renderControls(); renderRail(m);renderAI(m);renderChanges(m);
  const labels = { near: ['52주 신고가권', '252거래일 고점에 가까운 종목 · 당일 경신은 별도 탭에서 확인'], high: ['당일 신고가', '표시된 거래일에 이전 251개 관측치 고점을 실제로 넘긴 종목'], industries: ['인더스트리 액션', '여러 종목의 동반 강세 우선 · 5일 중앙값 순'], movers: ['특징주', '고점 위치와 관계없이 당일 변동과 거래량이 두드러진 종목'],ai:['AI 섹터','AI 공급망과 서비스의 흐름 · 상승·하락 모두 포함 · 한국/미국 전환 가능'] };
  $('section-title').textContent = state.industry ? state.industry.name : labels[state.tab][0]; $('section-description').textContent = labels[state.tab][1];
  const content = $('content'), pagination = $('pagination'); content.replaceChildren(); pagination.replaceChildren();
  if (state.tab === 'industries' && !state.industry) {
    const groups = m.industries.filter(g => (!state.confirmed || g.confirmed) && (!state.query || g.name.toLowerCase().includes(state.query.toLowerCase()))).sort(rankGroups);
    $('result-summary').textContent = `${groups.length}개 산업 그룹 · 전체 스크리닝 대상 기준`;
    const grid = el('div', null, 'industry-grid');
    groups.forEach(g => {
      const card = button('', false, () => openIndustry(g)); card.className = 'industry-card';
      card.append(el('h3', g.name), el('strong', pct(g.medianReturn5d), `return ${tone(g.medianReturn5d)}`), el('p', `5일 중앙값 · ${g.memberCount}종목`), el('p', `당일 중앙값 ${pct(g.medianChangePct)} · 5일 상승 ${g.advancing5dCount ?? '—'}/${g.memberCount}`), el('p', `신고가권 ${g.nearHighCount} · 장중 신고가 ${g.newHighCount || 0}`), el('p', `당일 상승 ${g.advancingCount}/${g.memberCount} · 거래량 증가 ${g.volumeConfirmedCount}`), el('span', g.confirmed ? '동반 강세 확인' : '관찰 그룹', `flow-status ${g.confirmed ? 'confirmed' : ''}`)); grid.append(card);
    });
    content.append(groups.length ? grid : el('div', '조건에 맞는 산업 그룹이 없습니다.', 'empty'));
  } else {
    const rows = selectStocks(m, { ...state, tab: state.industry ? 'industry' : state.tab }), pages = Math.max(1, Math.ceil(rows.length / 30));
    state.page = Math.min(state.page, pages); content.append(table(rows));
    $('result-summary').textContent = `${rows.length}종목 · ${state.newOnly?'새로 포착된 종목 · 현재 조건 적용':'현재 조건 적용'}${state.industry ? ' · 선택 산업 구성원' : ''}${state.tab==='ai'?` · ${AI_SECTORS.find(g=>g.id===state.aiSector)?.name||'AI 전체'} · 중복 제외`:''}`;
    const toList = () => { const t = $('section-title'); const top = t?.getBoundingClientRect().top ?? 0; if (top < 0) window.scrollTo({ top: window.scrollY + top - 8, behavior: 'instant' }); };
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
// After switching tabs from far down the page, bring the tab bar back into view
// instead of leaving the reader in the middle of a different list.
function keepTabsInView() {
  const tabs = document.querySelector('.tabs'); if (!tabs) return;
  const top = tabs.getBoundingClientRect().top;
  if (top < 0) window.scrollTo({ top: window.scrollY + top - 8, behavior: 'instant' });
}
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
    $('section-description').textContent = 'Follow-Through Day · 하락 추세 이후의 반등 확인';
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
  $('section-description').textContent = '지금 사도 되는 시장인지, 어떤 종목이 지수보다 강한지, 어떤 종목이 꺾이고 있는지 한눈에 봅니다';
  renderTrend($('content'), state.market, { openStock: openStockById, onSubtab: syncUrl });
}
document.querySelectorAll('[data-market]').forEach(b => b.onclick = () => {
  const tab=['ai','trend','ftd','leaders'].includes(state.tab)?state.tab:'near',aiPeriod=state.aiPeriod,aiView=state.aiView;
  Object.assign(state, { ...defaults, market: b.dataset.market,tab,aiPeriod,aiView }); loadSort(state.market); $('search').value = '';
  syncUrl(); render();
});
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => {
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
  else if (failed) { $('notice').hidden = false; $('notice').textContent = '가격 파일을 불러오지 못했습니다. 잠시 후 데이터 확인을 눌러 주세요.'; }
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
  renderSchedule($('schedule'), getRefreshStatus(), trendBlock, state.market);
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
