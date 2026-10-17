// Trend-following view: market weather, RS-line leaders, sell warnings,
// earnings dates and the signal scorecard. Reads wamo_trend.json only.
const LIGHT = { GREEN: '공격 가능', YELLOW: '조심', RED: '현금 비중 확대', UNKNOWN: '판단 보류' };
const SUBTABS = [['rs', 'RS선 선행 신고가'], ['sell', '팔 때 경고'], ['earnings', '실적발표 임박'], ['score', '신호 성적표']];
let data = null, loading = null, failed = false, subtab = 'rs', sellFilter = 'HIGH';

const isNum = n => typeof n === 'number' && Number.isFinite(n);
const fmt = (n, d = 1) => isNum(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = n => isNum(n) ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—';
const tone = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
function btn(text, active, action, cls = '') { const b = el('button', text, `${active ? 'active ' : ''}${cls}`.trim()); b.type = 'button'; b.onclick = action; return b; }
const block = market => data?.markets?.[market] || null;

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

function svgLine(series, { width = 220, height = 54, color = 'var(--green)', min, max, extra = [] } = {}) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('class', 'tr-svg'); svg.setAttribute('aria-hidden', 'true');
  const all = [series, ...extra.map(e => e.series)].flat().filter(isNum);
  if (all.length < 2) return svg;
  const lo = min ?? Math.min(...all), hi = max ?? Math.max(...all), span = hi - lo || 1;
  const path = (vals, stroke, dash) => {
    const pts = vals.map((v, i) => isNum(v) ? `${(i / (vals.length - 1)) * width},${height - 3 - ((v - lo) / span) * (height - 6)}` : null).filter(Boolean);
    const p = document.createElementNS(ns, 'polyline');
    p.setAttribute('points', pts.join(' ')); p.setAttribute('fill', 'none'); p.setAttribute('stroke', stroke);
    p.setAttribute('stroke-width', '1.6'); p.setAttribute('stroke-linejoin', 'round'); if (dash) p.setAttribute('stroke-dasharray', '3 3');
    svg.append(p);
  };
  extra.forEach(e => path(e.series, e.color, e.dash));
  path(series, color);
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
  const chip = (label, value, score) => { const c = el('span', null, `tr-chip ${score > 0 ? 'good' : score < 0 ? 'bad' : ''}`); c.append(el('small', label), el('b', value)); chips.append(c); };
  const score = key => (w.factors || []).find(f => f.key === key)?.score ?? 0;
  chip(idx.label || '지수', `${pct(idx.changePct)} · ${idx.aboveMa50 ? '50일선 위' : '50일선 아래'}`, score('index'));
  chip('분산일', dist.status === 'OK' ? `${dist.count} / 25일` : '자료 없음', score('distribution'));
  chip('50일선 위 종목', br.status === 'OK' ? `${fmt(br.above50Pct, 0)}%` : '—', score('breadth'));
  chip('신고가 · 신저가', hl.status === 'OK' ? `${hl.newHighs} · ${hl.newLows}` : '—', score('highsLows'));
  const more = btn('자세히 보기 →', false, onOpen, 'tr-more');
  container.append(head, text, chips, more);
}

// --------------------------------------------------------------- metrics
export function renderTrendMetrics(container, market) {
  const b = block(market);
  container.replaceChildren();
  const add = (label, value, sub) => { const box = el('div', null, 'metric'); box.append(el('span', label), el('strong', value), el('small', sub)); container.append(box); };
  if (!b) { add('시장 날씨', '—', '자료 준비 중'); return; }
  add('시장 날씨', LIGHT[b.weather?.level] || '—', `점수 ${b.weather?.score ?? '—'} · ${b.asOf} 종가`);
  add('RS선 선행 신고가', `${b.rsLeaders?.leadingCount ?? 0}`, `RS선 신고가 전체 ${b.rsLeaders?.count ?? 0}종목`);
  add('강한 경고', `${b.sellWarnings?.highCount ?? 0}`, `주의 포함 ${b.sellWarnings?.count ?? 0}종목`);
  const up = b.earnings?.upcoming || [];
  add('실적발표 14일 내', b.earnings?.status === 'OK' ? `${b.earnings.total ?? up.length}` : '—', b.earnings?.status === 'OK' ? `추세 종목 ${up.filter(x => x.inTrend).length}` : market === 'KR' ? '한국은 미제공' : '일정 자료 대기');
}

// ------------------------------------------------------------ detail view
function weatherDetail(b) {
  const w = b.weather || {}, wrap = el('section', null, 'tr-weather-detail');
  const factors = el('div', null, 'tr-factor-list');
  (w.factors || []).forEach(f => {
    const row = el('div', null, `tr-factor ${f.score > 0 ? 'good' : f.score < 0 ? 'bad' : ''}`);
    row.append(el('span', f.score > 0 ? '좋음' : f.score < 0 ? '나쁨' : '보통', 'tr-factor-tag'), el('strong', f.label), el('span', f.text));
    factors.append(row);
  });
  const grid = el('div', null, 'tr-cards');
  // Index
  const idx = b.index || {}, c1 = el('article', null, 'tr-card');
  c1.append(el('h3', idx.label || '지수'), el('div', null, 'tr-big'));
  c1.querySelector('.tr-big').append(el('b', fmt(idx.close, 2)), el('span', pct(idx.changePct), tone(idx.changePct)));
  c1.append(el('p', `50일선 ${idx.aboveMa50 ? '위' : '아래'} · 200일선 ${idx.aboveMa200 ? '위' : '아래'} · 200일선 ${idx.ma200Rising ? '오르는 중' : '내려가는 중'}`));
  c1.append(help(idx.synthetic ? '지수 자료를 받지 못해 화면 속 종목들의 중앙값으로 만든 대체 지수예요.' : '지수가 50일선 위에 있으면 단기 추세가 살아 있다는 뜻이에요.'));
  // Distribution days
  const dist = w.distribution || {}, c2 = el('article', null, 'tr-card');
  c2.append(el('h3', '분산일 (기관이 판 흔적)'));
  if (dist.status === 'OK') {
    const big = el('div', null, 'tr-big'); big.append(el('b', `${dist.count}`), el('span', '/ 최근 25거래일')); c2.append(big);
    const dots = el('div', null, 'tr-dots');
    (dist.days || []).forEach(d => { const s = el('span', null, `tr-d ${d.distribution ? (d.expired ? 'expired' : 'hit') : ''}`); s.title = `${d.date} ${pct(d.changePct)}${d.distribution ? d.expired ? ' · 분산일(지워짐)' : ' · 분산일' : ''}`; dots.append(s); });
    c2.append(dots);
  } else c2.append(el('p', '자료 없음'));
  c2.append(help(dist.note || ''));
  // Breadth
  const br = w.breadth || {}, c3 = el('article', null, 'tr-card');
  c3.append(el('h3', '시장 체력 (50일선 위 종목 비율)'));
  if (br.status === 'OK') {
    const big = el('div', null, 'tr-big'); big.append(el('b', `${fmt(br.above50Pct, 0)}%`), el('span', `5일 ${br.above50Change5d > 0 ? '+' : ''}${fmt(br.above50Change5d, 0)}%p`, tone(br.above50Change5d))); c3.append(big);
    const s = br.series || [];
    c3.append(svgLine(s.map(p => p.above50), { min: 0, max: 100, extra: [{ series: s.map(p => p.above200), color: '#7f93ad', dash: true }] }));
    c3.append(el('small', `초록 실선: 50일선 위 · 회색 점선: 200일선 위 (${fmt(br.above200Pct, 0)}%) · 최근 ${s.length}거래일`, 'tr-legend'));
  } else c3.append(el('p', '자료 없음'));
  c3.append(help('60% 넘으면 건강, 40% 아래면 약한 시장이에요. 지수는 오르는데 이 비율이 떨어지면 소수 대형주만 끌고 가는 장이에요.'));
  // Highs vs lows
  const hl = w.highsLows || {}, c4 = el('article', null, 'tr-card');
  c4.append(el('h3', '신고가 vs 신저가'));
  if (hl.status === 'OK') {
    const big = el('div', null, 'tr-big'); big.append(el('b', `${hl.newHighs}`, 'up'), el('span', '·'), el('b', `${hl.newLows}`, 'down')); c4.append(big);
    const bars = el('div', null, 'tr-hl');
    const top = Math.max(1, ...(hl.series || []).map(p => Math.max(p.newHighs, p.newLows)));
    (hl.series || []).forEach(p => {
      const col = el('div', null, 'tr-hl-col'); col.title = `${p.date} 신고가 ${p.newHighs} · 신저가 ${p.newLows}`;
      const up = el('span', null, 'tr-hl-up'), dn = el('span', null, 'tr-hl-dn');
      up.style.height = `${p.newHighs / top * 100}%`; dn.style.height = `${p.newLows / top * 100}%`;
      col.append(up, dn, el('small', p.date.slice(5))); bars.append(col);
    });
    c4.append(bars);
  } else c4.append(el('p', '자료 없음'));
  c4.append(help('위(초록)는 1년 중 가장 높은 가격을 새로 쓴 종목 수, 아래(빨강)는 가장 낮은 가격을 새로 쓴 종목 수예요.'));
  grid.append(c1, c2, c3, c4);
  wrap.append(factors, grid);
  return wrap;
}

function emptyBox(text) { return el('div', text, 'empty'); }

function rsSection(b, openStock) {
  const r = b.rsLeaders || {}, wrap = el('div');
  wrap.append(help(r.note || ''));
  const items = r.items || [];
  if (!items.length) return wrap.append(emptyBox('오늘은 RS선 신고가 종목이 없어요.')), wrap;
  const list = el('div', null, 'tr-list');
  items.forEach(x => {
    const row = el('article', null, 'tr-row');
    const main = el('div', null, 'tr-row-main'); main.append(stockName(x, openStock));
    const tag = el('span', x.leading ? '주가보다 먼저' : '주가와 함께', `tr-tag ${x.leading ? 'lead' : ''}`);
    const facts = el('div', null, 'tr-facts');
    [['종가 변화', pct(x.changePct), tone(x.changePct)], ['고점까지', isNum(x.highDistancePct) ? `${fmt(x.highDistancePct)}%` : '—'], ['RS 순위', fmt(x.rs, 0)], ['단계', x.stage ? `Stage ${x.stage}` : '—']]
      .forEach(([k, v, c]) => { const f = el('span'); f.append(el('small', k), el('b', v, c)); facts.append(f); });
    const chart = el('div', null, 'tr-mini');
    const price = x.priceSpark || [], plo = Math.min(...price), phi = Math.max(...price);
    chart.append(svgLine(x.rsSpark || [], { width: 150, height: 40, min: 0, max: 100, extra: [{ series: price.map(v => (v - plo) / ((phi - plo) || 1) * 100), color: '#7f93ad', dash: true }] }));
    chart.title = '초록: RS선 · 회색 점선: 주가 (최근 60거래일)';
    main.append(tag);
    row.append(main, facts, chart);
    list.append(row);
  });
  wrap.append(el('small', '초록 선 = RS선(지수 대비 강도), 회색 점선 = 주가. 최근 60거래일 모양입니다.', 'tr-legend'), list);
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
    row.append(main, facts, reasons);
    list.append(row);
  });
  wrap.append(list);
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

export function renderTrend(content, market, { openStock, rerender }) {
  content.replaceChildren();
  const b = block(market);
  if (!b) {
    content.append(emptyBox(failed ? '추세 자료를 불러오지 못했어요. 다음 자동 갱신 뒤 다시 확인해 주세요.' : '추세 자료를 불러오는 중이에요.'));
    return;
  }
  content.append(weatherDetail(b));
  const seg = el('nav', null, 'tr-subtabs'); seg.setAttribute('aria-label', '추세 신호 보기');
  const counts = { rs: b.rsLeaders?.leadingCount ?? 0, sell: b.sellWarnings?.highCount ?? 0, earnings: b.earnings?.status === 'OK' ? (b.earnings.upcoming || []).filter(x => x.inTrend).length : '—', score: b.scorecard?.eventCount ?? 0 };
  SUBTABS.forEach(([k, label]) => { const t = btn('', subtab === k, () => { subtab = k; rerender(); }); t.append(el('span', label), el('span', `${counts[k]}`, 'tab-count')); seg.append(t); });
  content.append(seg);
  const body = el('section', null, 'tr-body');
  if (subtab === 'rs') body.append(rsSection(b, openStock));
  if (subtab === 'sell') body.append(sellSection(b, openStock, rerender));
  if (subtab === 'earnings') body.append(earningsSection(b, openStock));
  if (subtab === 'score') body.append(scoreSection(b, openStock));
  content.append(body);
  content.append(el('p', `${b.asOf} 종가 기준 · 계산 ${new Date(b.builtAt || data.generatedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST · 지수 ${b.index?.source || '—'} · 대상 ${b.universe}종목 · 투자 권유가 아니라 확인용 신호예요.`, 'tr-foot'));
}
