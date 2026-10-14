import { validSnapshot, acceptSnapshot, selectStocks, marketStatus, safeUrl, numeric, createRefresher } from './model.mjs';

const $ = id => document.getElementById(id);
const defaults = { tab: 'near', distance: 10, high: 'either', mode: 'all', query: '', rs: false, stage: false, sort: 'default', confirmed: false, industry: null, page: 1 };
const state = { ...defaults, market: new URLSearchParams(location.search).get('market') === 'US' ? 'US' : 'KR' };
let snapshot = null, refreshFailed = false, refreshing = false;
const fmt = (n, d = 1) => numeric(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = n => numeric(n) ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—';
const tone = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
const distance = s => numeric(s.highDistancePct) ? `${fmt(s.highDistancePct)}%` : '—';
const volume = s => numeric(s.volumeRatio) ? `${fmt(s.volumeRatio, 2)}×` : '—';
const price = s => `${s.market === 'KR' ? '₩' : '$'}${fmt(s.close, s.market === 'KR' ? 0 : 2)}`;

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
function change(key, value) { state[key] = value; state.page = 1; render(); }
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
  const width = large ? 560 : 80, height = large ? 140 : 30;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.classList.add(large ? 'detail-chart' : 'spark');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '최근 60거래일 종가 흐름');
  const values = (s.sparkline || []).filter(numeric);
  if (values.length > 1) {
    const low = Math.min(...values), range = Math.max(...values) - low || 1;
    const path = document.createElementNS(svg.namespaceURI, 'polyline');
    path.setAttribute('points', values.map((v, i) => `${i / (values.length - 1) * width},${height - 5 - (v - low) / range * (height - 10)}`).join(' '));
    path.setAttribute('fill', 'none'); path.setAttribute('stroke', values.at(-1) >= values[0] ? '#55d5b1' : '#fa8d96');
    path.setAttribute('stroke-width', large ? '2' : '1.5'); svg.append(path);
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
function stockButton(s) {
  const b = button('', false, () => detail(s)); b.className = 'stock-link'; b.title = s.name;
  b.append(el('strong', shortName(s)), el('small', `${s.symbol} · ${s.industry}`));
  return b;
}
function stat(label, value, cls = '') {
  const box = el('div', null, 'stat'); box.append(el('span', label), el('strong', value, cls)); return box;
}
function detail(s) {
  const d = $('detail');
  d.replaceChildren(el('div', `${s.symbol} · ${s.market}${s.exchange ? ' / ' + s.exchange : ''}`, 'eyebrow'), el('h2', s.name), el('p', `${s.industry} · ${s.asOf} 거래일 종가`));
  const quote = el('div', null, 'detail-quote');
  quote.append(el('strong', price(s)), el('span', pct(s.changePct), tone(s.changePct)), naverLink(s, true)); d.append(quote);
  const chart = el('section', null, 'chart-panel');
  chart.append(el('div', '최근 60거래일 · 종가 추이', 'chart-caption'), spark(s, true)); d.append(chart);
  const grid = el('div', null, 'detail-grid');
  [['5일 수익률', pct(s.return5d), tone(s.return5d)], ['20일 수익률', pct(s.return20d), tone(s.return20d)],
    ['52주 고점 거리', numeric(s.highDistancePct) ? distance(s) : '이력 부족'], ['52주 장중 고점', fmt(s.high52, 2)],
    ['상대 거래량', volume(s)], ['RS', fmt(s.rs, 0)], ['Stage 2', s.stage2 === true ? '충족' : s.stage2 === false ? '미충족' : '미제공']]
    .forEach(([label, value, cls]) => grid.append(stat(label, value, cls)));
  d.append(grid);
  const signalBox = el('div', null, 'detail-signals');
  const reasons = signals(s); (reasons.length ? reasons : ['설정된 특징주 신호 없음']).forEach(t => signalBox.append(el('span', t, 'badge'))); d.append(signalBox);
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
function table(rows) {
  if (!rows.length) return el('div', '조건에 맞는 종목이 없습니다. 필터를 조정해 보세요.', 'empty');
  const pageRows = rows.slice((state.page - 1) * 30, state.page * 30);
  const wrap = el('div', null, 'table-wrap'), table = el('table'), head = el('thead'), tr = el('tr');
  ['종목 / 산업', '종가', '당일', '5일', '고점까지', '거래량 배수', 'RS', '60일', '바로가기'].forEach(t => tr.append(el('th', t)));
  head.append(tr); table.append(head); const body = el('tbody');
  pageRows.forEach(s => {
    const row = el('tr'), name = el('td'); name.append(stockButton(s));
    if (state.tab === 'high' || state.tab === 'movers') name.append(el('span', signals(s).join(' · '), 'signal'));
    row.append(name, el('td', price(s)), el('td', pct(s.changePct), tone(s.changePct)), el('td', pct(s.return5d), tone(s.return5d)), el('td', distance(s)), el('td', volume(s)), el('td', fmt(s.rs, 0)));
    const chart = el('td'); chart.append(spark(s)); const link = el('td'); link.append(naverLink(s)); row.append(chart, link); body.append(row);
  });
  table.append(body); wrap.append(table);
  const cards = el('div', null, 'stock-cards');
  pageRows.forEach(s => {
    const card = el('article', null, 'stock-card'), top = el('div', null, 'stock-card-top'); top.append(stockButton(s), naverLink(s));
    const quote = el('div', null, 'card-quote'); quote.append(el('strong', price(s)), el('span', pct(s.changePct), tone(s.changePct)), spark(s));
    const stats = el('div', null, 'card-stats'); stats.append(stat('5일', pct(s.return5d), tone(s.return5d)), stat('고점까지', distance(s)), stat('거래량 배수', volume(s)), stat('RS', fmt(s.rs, 0)));
    card.append(top, quote, stats);
    if (state.tab === 'high' || state.tab === 'movers') card.append(el('div', signals(s).join(' · '), 'signal'));
    cards.append(card);
  });
  const container = el('div'); container.append(wrap, cards); return container;
}
const rankGroups = (a, b) => Number(b.confirmed) - Number(a.confirmed) || Number(b.memberCount >= 3) - Number(a.memberCount >= 3) || (b.medianReturn5d ?? -Infinity) - (a.medianReturn5d ?? -Infinity) || b.nearHighCount - a.nearHighCount;
function openIndustry(g) {
  Object.assign(state, { tab: 'industries', industry: g, query: '', rs: false, stage: false, sort: 'default', page: 1 }); $('search').value = ''; render();
}
function renderRail(m) {
  const rail = $('industry-rail'); rail.replaceChildren(); rail.hidden = state.tab === 'industries' && !state.industry;
  if (rail.hidden) return;
  rail.append(el('div', 'INDUSTRY FLOW', 'eyebrow'), el('h3', '산업 흐름'), el('p', '전체 대상 · 3종목 이상 그룹', 'rail-caption'));
  const groups = m.industries.filter(g => g.memberCount >= 3).sort(rankGroups).slice(0, 6);
  const scale = Math.max(1, ...groups.map(g => Math.abs(g.medianReturn5d || 0)));
  if (!groups.length) rail.append(el('p', '비교 가능한 산업 그룹이 없습니다.'));
  groups.forEach(g => {
    const b = button('', false, () => openIndustry(g)); b.className = 'rail-group';
    const top = el('div', null, 'rail-group-top'); top.append(el('strong', g.name), el('span', pct(g.medianReturn5d), tone(g.medianReturn5d)));
    const bar = el('div', null, 'flow-bar'); bar.setAttribute('aria-hidden', 'true');
    if (numeric(g.medianReturn5d)) {
      const fill = el('span', null, tone(g.medianReturn5d));
      const width = Math.abs(g.medianReturn5d) / scale * 50;
      fill.style.width = `${width}%`; fill.style.left = `${g.medianReturn5d < 0 ? 50 - width : 50}%`; bar.append(fill);
    }
    b.append(top, bar, el('small', `5일 중앙값 · ${g.memberCount}종목 · 신고가권 ${g.nearHighCount}`), el('span', g.confirmed ? '동반 강세 확인' : '관찰', `flow-status ${g.confirmed ? 'confirmed' : ''}`)); rail.append(b);
  });
  rail.append(button('전체 산업 보기 →', false, () => { Object.assign(state, { tab: 'industries', industry: null, query: '', rs: false, stage: false, page: 1 }); $('search').value = ''; render(); }));
}
function checkbox(key, text, parent) {
  const label = el('label', null, 'checkbox-control'), input = el('input'); input.type = 'checkbox'; input.checked = state[key]; input.onchange = () => change(key, input.checked);
  label.append(input, document.createTextNode(` ${text}`)); parent.append(label);
}
function resetFilters() {
  for (const key of ['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'confirmed', 'page']) state[key] = defaults[key];
  $('search').value = ''; render();
}
function renderControls() {
  const controls = $('controls'); controls.replaceChildren();
  if (state.industry) controls.append(button('← 전체 산업', false, () => { state.industry = null; state.page = 1; render(); }));
  const group = el('div', null, 'filter-group');
  if (state.tab === 'near') {
    group.append(el('span', '고점 거리', 'filter-label')); [5, 10, 15].forEach(v => group.append(button(`${v}% 이내`, state.distance === v, () => change('distance', v))));
  }
  if (state.tab === 'high') [['either', '모두'], ['intraday', '장중 고점'], ['close', '종가 고점']].forEach(([v, text]) => group.append(button(text, state.high === v, () => change('high', v))));
  if (state.tab === 'movers') [['all', '전체'], ['up', '상승 ≥3%'], ['down', '하락 ≤−3%'], ['volume', '거래량 ≥1.5×']].forEach(([v, text]) => group.append(button(text, state.mode === v, () => change('mode', v))));
  if (group.childNodes.length) controls.append(group);
  if (state.tab === 'industries' && !state.industry) {
    checkbox('confirmed', '확인된 그룹만', controls);
  } else {
    const options = el('div', null, 'filter-group'); checkbox('rs', 'RS ≥80', options); checkbox('stage', 'Stage 2', options); controls.append(options);
    const select = el('select'); select.setAttribute('aria-label', '정렬');
    [['default', '기본 정렬'], ['changePct', '당일 상승률'], ['absolute', '당일 절대 변동'], ['return5d', '5일 수익률'], ['volumeRatio', '거래량 배수'], ['rs', 'RS'], ['name', '종목명']].forEach(([value, text]) => { const o = el('option', text); o.value = value; o.selected = state.sort === value; select.append(o); });
    select.onchange = () => change('sort', select.value); controls.append(select);
  }
  if (['distance', 'high', 'mode', 'query', 'rs', 'stage', 'sort', 'confirmed'].some(key => state[key] !== defaults[key])) {
    const reset = button('필터 초기화', false, resetFilters); reset.id = 'reset-filters'; controls.append(reset);
  }
}
function renderMeta(m) {
  const status = marketStatus(m), messages = [];
  if (status === 'STALE') messages.push(`${m.asOf || '미제공'} 거래일 가격을 유지합니다. 예상 최근 거래일 ${m.expectedSession}의 갱신을 기다리고 있습니다.`);
  if (status === 'PARTIAL' && !m.coverage.excluded) messages.push('일부 새 가격의 검증 범위가 부족해 이전 검증 데이터를 유지합니다. 데이터 기준에서 상세 사유를 확인하세요.');
  if (refreshFailed) messages.push('가격 파일을 다시 확인하지 못해 마지막 검증 데이터를 유지합니다.');
  $('notice').hidden = messages.length === 0; $('notice').textContent = messages.join(' ');
  $('date-info').replaceChildren(el('span', `${m.asOf || '미제공'} 거래일 종가`, 'session-date'), el('span', ({ CURRENT: '최신 거래일', PARTIAL: '일부 제외', STALE: '지난 거래일', UNAVAILABLE: '데이터 없음' })[status], `badge status-${status.toLowerCase()}`));
  const refreshButton = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); refreshButton.className = 'refresh-button'; refreshButton.disabled = refreshing; $('date-info').append(refreshButton);
  const near = m.stocks.filter(s => numeric(s.highDistancePct) && s.highDistancePct <= 10), highs = m.stocks.filter(s => s.intradayHigh52 === true || s.closeHigh52 === true);
  const groups = m.industries.filter(g => g.confirmed), moves = selectStocks(m, { tab: 'movers' });
  const counts = { near: near.length, high: highs.length, industries: groups.length, movers: moves.length };
  const countCriteria = { near: '전체 스크리닝 대상 중 고점 대비 10% 이내', high: '전체 스크리닝 대상 중 장중 또는 종가 신고가', industries: '전체 스크리닝 대상 중 동반 강세 확인 그룹', movers: '전체 스크리닝 대상 중 변동 또는 거래량 특징주' };
  document.querySelectorAll('[data-tab]').forEach(b => {
    let n = b.querySelector('.tab-count');
    if (!n) { n = el('span', null, 'tab-count'); b.append(n); }
    const count = counts[b.dataset.tab].toLocaleString();
    n.textContent = b.dataset.tab === 'industries' ? `강세 ${count}` : count;
    b.title = `${countCriteria[b.dataset.tab]} · ${count}`;
    b.setAttribute('aria-label', `${b.firstChild.textContent} · ${b.title}`);
  });
  $('metrics').replaceChildren();
  [['52주 신고가권', near.length, '고점 대비 10% 이내'], ['당일 신고가', highs.length, '장중 또는 종가 경신'], ['산업 동반 강세', groups.length, '확인된 그룹'], ['특징주', moves.length, '상승 · 하락 · 거래량']].forEach(([label, value, sub]) => { const box = el('div', null, 'metric'); box.append(el('span', label), el('strong', value.toLocaleString()), el('small', sub)); $('metrics').append(box); });
  $('coverage-summary').textContent = `전체 스크리닝 ${m.coverage.valid}종목 · 52주 비교 ${m.coverage.eligible52w}종목${m.coverage.excluded ? ' · 제외 ' + m.coverage.excluded + '종목' : ''} · 데이터 기준`;
  const computed = new Date(snapshot.generatedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
  $('coverage-detail').replaceChildren(el('p', `가격 출처: ${m.source} | 대상 ${m.coverage.total} · 유효 ${m.coverage.valid} · 제외 ${m.coverage.excluded}`), el('p', `가격 거래일 ${m.asOf || '—'} · 화면 계산 ${computed} KST (실시간 가격 아님)`), el('p', `예상 최근 거래일 ${m.expectedSession} · 다음 갱신 ${new Date(m.nextScheduledFor).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`), el('p', '고점 거리: 최근 252개 거래일 장중 고점 대비 종가의 거리. 당일 신고가: 이전 251개 관측치 고점을 초과(동률 제외). 거래량: 당일 / 이전 50거래일 평균. RS·Stage 2는 선택 조건입니다.'), el('p', '산업 동반 강세 기준: 서로 다른 3종목 이상, 당일 상승 2종목 이상, 5일 상승 2종목 이상이면서 전체의 50% 이상, 5일 수익률 중앙값 양수, 신고가권 2종목 이상 또는 거래량 1.5배 이상 2종목 이상. 미분류 그룹 제외.'));
  m.warnings.forEach(w => $('coverage-detail').append(el('p', w)));
}
function render() {
  if (!snapshot) {
    $('content').replaceChildren(el('div', '검증된 가격 데이터를 불러오는 중입니다.', 'empty'));
    const retry = button(refreshing ? '확인 중…' : '데이터 확인', false, () => refresh()); retry.className = 'refresh-button'; $('date-info').replaceChildren(retry); return;
  }
  const m = snapshot.markets[state.market];
  document.querySelectorAll('[data-market]').forEach(b => { b.classList.toggle('active', b.dataset.market === state.market); b.setAttribute('aria-pressed', String(b.dataset.market === state.market)); });
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b.dataset.tab === state.tab); b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'); });
  renderMeta(m); renderControls(); renderRail(m);
  const labels = { near: ['52주 신고가권', '252거래일 고점에 가까운 종목 · 당일 경신은 별도 탭에서 확인'], high: ['당일 신고가', '표시된 거래일에 이전 251개 관측치 고점을 실제로 넘긴 종목'], industries: ['인더스트리 액션', '여러 종목의 동반 강세 우선 · 5일 중앙값 순'], movers: ['특징주', '고점 위치와 관계없이 당일 변동과 거래량이 두드러진 종목'] };
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
    $('result-summary').textContent = `${rows.length}종목 · 현재 조건 적용${state.industry ? ' · 선택 산업 구성원' : ''}`;
    const prev = button('이전', false, () => { state.page--; render(); }), next = button('다음', false, () => { state.page++; render(); });
    prev.disabled = state.page <= 1; next.disabled = state.page >= pages;
    pagination.append(prev, el('span', `${rows.length}종목 · ${state.page} / ${pages}`), next);
    const jump = el('a', '산업 흐름 보기 ↓', 'rail-jump'); jump.href = '#industry-rail'; $('result-summary').append(jump);
  }
}
document.querySelectorAll('[data-market]').forEach(b => b.onclick = () => {
  Object.assign(state, { ...defaults, market: b.dataset.market }); $('search').value = '';
  const url = new URL(location.href); url.searchParams.set('market', state.market); history.replaceState(null, '', url); render();
});
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => {
  Object.assign(state, { tab: b.dataset.tab, rs: false, stage: false, query: '', industry: null, page: 1, sort: 'default' }); $('search').value = ''; render();
});
$('search').oninput = e => change('query', e.target.value);
$('close-drawer').onclick = () => $('drawer').close();
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
  return performRefresh().finally(() => { refreshing = false; updateRefreshButton(); });
}
function updateRefreshButton() {
  document.querySelectorAll('.refresh-button').forEach(b => { b.disabled = refreshing; b.textContent = refreshing ? '확인 중…' : '데이터 확인'; });
}
refresh();
setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 5 * 60 * 1000);
window.addEventListener('focus', () => { render(); refresh(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { render(); refresh(); } });
