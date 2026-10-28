// Leading stocks and leading industries. Reads wamo_trend.json → markets[X].leadership.
import { helpButton, addTerms } from './help.js?v=20261029-pt3';
import { LEADER_TERMS, STAGES, WATCH_SHORT, glossary } from './terms.mjs?v=20261026-rn1';
addTerms(LEADER_TERMS);
const n = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 0) => isNum(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const term = (key, label) => helpButton(label || LEADER_TERMS[key][0], key);
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
// US names arrive as full security titles; keep the company part.
const short = name => String(name || '').replace(/\s+(Common Stock|Class [A-Z]\b.*|Ordinary Shares.*|American Depositary Shares.*|Depositary Shares.*|Common Shares.*|Inc\. Common.*)$/i, '').replace(/\s+(Common Stock)$/i, '').trim();
let stockFilter = 'all', showAll = false, showMatrix = false;
const NO = ['①', '②', '③', '④', '⑤', '⑥'];
const SVG = 'http://www.w3.org/2000/svg';
const sv = (tag, attrs = {}, text) => { const e = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
const clip = (t, k) => { t = short(t); return t.length > k ? `${t.slice(0, k - 1)}…` : t; };

const EMERGENCE = {
  CONFIRMED: ['출현 확인', 'good', 'FTD 뒤 10거래일 안에 기준 이상의 선도주가 신고가를 뚫었어요. 상승장이 진짜일 가능성이 커졌다는 뜻이에요. 비중 확대를 따져 볼 수 있는 단계예요.'],
  WATCHING: ['확인 중', 'mid', 'FTD 뒤 10거래일이 아직 지나지 않았어요. 신고가를 뚫는 선도주 수를 매일 세고 있어요.'],
  WEAK: ['선도주 부족', 'bad', 'FTD 뒤 10거래일이 지났는데 신고가를 뚫은 선도주가 기준보다 적어요. FTD를 믿기 어렵다는 뜻이라 총자금 10% 이내 원칙을 지켜요.'],
  NO_FTD: ['FTD 없음', 'none', '지금 살아 있는 FTD가 없어서 "출현" 판정은 하지 않아요. 아래 숫자는 최근 10거래일 신고가 돌파 수(참고용)예요.'],
};

export function leadersSummary(block) {
  const L = block?.leadership;
  if (!L || L.status !== 'OK') return '대기';
  return `선도 ${L.industries?.leadingCount ?? 0} · 대기 ${L.industries?.watchCount ?? 0}`;
}

function chip(text, cls, key) {
  const c = n('span', null, `ld-chip ${cls || ''}`);
  c.append(key ? helpButton(text, key) : text);
  return c;
}

function stockButton(item, openStock, extra) {
  const b = n('button', null, 'ld-stock'); b.type = 'button';
  b.onclick = () => openStock(item.id);
  b.append(n('strong', short(item.name)), n('small', `${item.symbol}${extra ? ` · ${extra}` : ''}`));
  return b;
}

function emergenceCard(L, market, openStock, openFTD, no) {
  const e = L.emergence || {};
  const [label, tone, meaning] = EMERGENCE[e.status] || EMERGENCE.NO_FTD;
  const card = n('section', null, `ld-card ld-emergence tone-${tone}`);
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('emergence', '선도주 출현 신호'));
  head.append(h, n('span', label, `ld-status tone-${tone}`)); card.append(head);
  card.append(n('p', meaning, 'ld-meaning'));
  const meter = n('div', null, 'ld-meter');
  const count = n('div', null, 'ld-meter-count');
  count.append(n('strong', `${e.count ?? 0}`), n('span', e.status === 'NO_FTD' ? '최근 10거래일 신고가 돌파 (RS 80 이상)' : `/ 기준 ${e.need}종목 · FTD 뒤 신고가 돌파`));
  const bar = n('div', null, 'ld-bar'); const fill = n('span'); fill.style.width = `${Math.min(100, ((e.count || 0) / (e.need || 1)) * 100)}%`; bar.append(fill);
  meter.append(count, bar);
  if (e.ftd) {
    const days = n('div', null, 'ld-days');
    for (let i = 1; i <= (e.window || 10); i++) days.append(n('i', null, i <= (e.age || 0) ? 'on' : ''));
    const info = n('small', `${e.ftd.index || '지수'} FTD ${md(e.ftd.date)} · ${e.age}/${e.window}거래일 지남${e.ftd.late ? ' · 늦은 FTD' : ''}`);
    meter.append(days, info);
  }
  card.append(meter);
  if (e.items?.length) {
    const list = n('div', null, 'ld-names');
    e.items.slice(0, 12).forEach(x => list.append(stockButton(x, openStock, `RS ${num(x.rs)}`)));
    card.append(list);
  }
  if (openFTD) { const go = n('button', '시장 전환·FTD 탭에서 FTD 보기 →', 'ld-link'); go.type = 'button'; go.onclick = openFTD; card.append(go); }
  return card;
}

function stageLegend() {
  const wrap = n('div', null, 'ld-legend');
  wrap.append(n('b', '단계 뜻'));
  const ul = n('ul');
  for (const [key, [icon, label, meaning]] of Object.entries(STAGES)) {
    const li = n('li', null, `stage-${key.toLowerCase()}`);
    li.append(n('span', `${icon} ${label}`, 'ld-stage-name'), n('span', meaning, 'ld-stage-meaning'));
    ul.append(li);
  }
  wrap.append(ul);
  return wrap;
}

function stageChip(stage) {
  if (!stage || !STAGES[stage]) return n('span', '—', 'ld-stage none');
  const [icon, label] = STAGES[stage];
  const c = n('span', null, `ld-stage stage-${stage.toLowerCase()}`);
  c.append(helpButton(`${icon} ${label}`, `stage${stage}`));
  return c;
}

function rankDelta(v) {
  if (!isNum(v)) return n('span', '—', 'ld-delta flat');
  if (v === 0) return n('span', '변동 없음', 'ld-delta flat');
  return n('span', `${v > 0 ? '▲' : '▼'}${Math.abs(v)}`, `ld-delta ${v > 0 ? 'up' : 'down'}`);
}

function condDots(c) {
  const wrap = n('div', null, 'ld-conds');
  [['rsTop', 'RS 상위 20%'], ['newHighs', '신고가 3+'], ['above50', '50일선 위 60%+'], ['persist', '10일 중 5일+']].forEach(([k, label]) => {
    wrap.append(n('span', `${c?.[k] ? '✓' : '·'} ${label}`, c?.[k] ? 'ok' : ''));
  });
  return wrap;
}

function industryCard(L, openStock, no, flow = new Map()) {
  const ind = L.industries || {};
  const card = n('section', null, 'ld-card');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('industryRs', '업종 RS'), ' 순위 · ', term('leadingIndustry', '선도업종'));
  head.append(h, n('span', `선도업종 ${ind.leadingCount ?? 0}개 / ${ind.count ?? 0}개 업종`, 'ld-status tone-none'));
  card.append(head);
  card.append(n('p', '업종 종목들을 같은 무게로 묶어 RS 점수를 매기고, 4가지 조건을 모두 채우면 선도업종(✔)으로 표시해요. 이름 옆 단계 표시를 누르면 뜻이 나와요.', 'ld-meaning'));
  card.append(stageLegend());
  if (ind.status !== 'OK' || !ind.items?.length) { card.append(n('div', ind.note || '업종 순위를 계산할 자료가 부족합니다.', 'ld-empty')); return card; }
  const list = n('ol', null, 'ld-industries');
  ind.items.slice(0, 10).forEach(x => {
    const li = n('li', null, `ld-industry${x.leading ? ' leading' : ''}`);
    const top = n('div', null, 'ld-ind-top');
    const name = n('div', null, 'ld-ind-name');
    name.append(n('span', `${x.rank}`, 'ld-rank'), n('strong', x.name));
    if (x.leading) name.append(chip('✔ 선도업종', 'lead', 'leadingIndustry'));
    if (x.rsLineHigh) name.append(chip('★ RS선 신고가', 'star', 'industryRsLine'));
    if (x.leadsIndex) name.append(chip('지수보다 먼저 고점권', 'bo', 'leadsIndex'));
    if (x.heldUp) name.append(chip(`버팀 ${num(x.heldUpRatio, 2)}`, 'held', 'heldUpIndustry'));
    { const mc = moneyChip(x.name, flow); if (mc) name.append(mc); }
    top.append(name, stageChip(x.stage));
    li.append(top);
    const stats = n('div', null, 'ld-ind-stats');
    const stat = (labelNode, valueNode) => { const s = n('div'); const l = n('span'); l.append(labelNode); s.append(l, valueNode); return s; };
    stats.append(
      stat(term('industryRs', 'RS'), n('b', num(x.rs))),
      stat(term('rankChange', '1주'), rankDelta(x.rankChange5)),
      stat(term('rankChange', '1개월'), rankDelta(x.rankChange20)),
      stat(term('newHighs', '신고가'), n('b', `${x.newHighs}종목`)),
      stat(term('above50', '50일선 위'), n('b', isNum(x.above50Pct) ? `${num(x.above50Pct)}%` : '—')),
      stat(term('persist', '상위권'), n('b', `${x.persist}/10일`)),
    );
    li.append(stats, condDots(x.conditions));
    if (x.representatives?.length) {
      const reps = n('div', null, 'ld-reps'); reps.append(n('span', `대표 종목 · 구성 ${x.members}종목`));
      x.representatives.forEach(r => reps.append(stockButton(r, openStock, `RS ${num(r.rs)}${r.leader ? ' · 선도주' : ''}`)));
      li.append(reps);
    }
    list.append(li);
  });
  card.append(list);
  if (ind.dropped?.length) {
    const out = n('p', null, 'ld-dropped'); out.append(stageChip('OUT'), ` ${ind.dropped.map(x => x.name).join(' · ')}`);
    card.append(out);
  }
  return card;
}

function leaderCard(L, openStock, rerender, no) {
  const ld = L.leaders || {};
  const card = n('section', null, 'ld-card');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('leader', '선도주'), ' 목록');
  head.append(h, n('span', `${ld.count ?? 0}종목 · 핵심 ${ld.coreCount ?? 0}`, 'ld-status tone-none'));
  card.append(head);
  card.append(n('p', 'RS 80 이상 · 52주 고점 15% 이내 · Stage 2 · 유동성을 모두 채운 종목이에요. 표시(★·돌파·버팀)를 누르면 뜻이 나와요. 종목을 누르면 차트가 열려요.', 'ld-meaning'));
  const filters = n('div', null, 'ld-filters');
  [['all', `전체 ${ld.count ?? 0}`], ['core', `핵심 ${ld.coreCount ?? 0}`], ['rsLead', `RS선 선행 ★ ${ld.rsLeadCount ?? 0}`], ['breakout', `돌파 ${ld.breakoutCount ?? 0}`]].forEach(([k, label]) => {
    const b = n('button', label, stockFilter === k ? 'active' : ''); b.type = 'button';
    b.onclick = () => { stockFilter = k; showAll = false; rerender(); };
    filters.append(b);
  });
  card.append(filters);
  const items = (ld.items || []).filter(x => stockFilter === 'all' || (stockFilter === 'core' ? x.core : stockFilter === 'rsLead' ? x.rsLead : x.breakoutDates?.length));
  if (!items.length) { card.append(n('div', '조건에 맞는 선도주가 없어요. 시장이 약할 때는 선도주가 거의 없는 것이 정상이에요.', 'ld-empty')); return card; }
  const list = n('div', null, 'ld-stocks');
  items.slice(0, showAll ? 60 : 20).forEach(x => {
    const row = n('div', null, 'ld-row');
    row.append(stockButton(x, openStock, x.industry));
    const nums = n('div', null, 'ld-nums');
    const rs = n('span'); rs.append(term('rsOneil', 'RS'), n('b', ` ${num(x.rs)}`));
    const dist = n('span'); dist.append(term('ldDistance', '고점까지'), n('b', ` ${isNum(x.highDistancePct) ? `-${num(x.highDistancePct, 1)}%` : '—'}`));
    nums.append(rs, dist); row.append(nums);
    const flags = n('div', null, 'ld-flags');
    if (x.core) flags.append(chip('핵심', 'core', 'core'));
    if (x.rsLead) flags.append(chip('★ RS선 선행', 'star', 'rsLead'));
    if (x.breakoutDates?.length) flags.append(chip(`돌파 ${md(x.breakoutDates[x.breakoutDates.length - 1])}`, 'bo', 'ldBreakout'));
    if (isNum(x.heldUpRatio)) flags.append(chip(`버팀 ${num(x.heldUpRatio, 2)}${x.heldUp ? ' ✓' : ''}`, x.heldUp ? 'held' : 'soft', 'heldUp'));
    row.append(flags); list.append(row);
  });
  card.append(list);
  if (items.length > 20 && !showAll) { const more = n('button', `더 보기 (${Math.min(60, items.length) - 20}종목)`, 'ld-more'); more.type = 'button'; more.onclick = () => { showAll = true; rerender(); }; card.append(more); }
  return card;
}


const PHASE_BANNER = {
  WAIT: ['FTD 전 · 준비하는 때', 'mid', '시장은 아직 바닥을 확인하지 못했어요. 지금은 사는 때가 아니라, 지수보다 먼저 강해지는 업종을 찾아 워치리스트를 만들어 두는 때예요. FTD가 나오면 이 목록부터 봅니다.'],
  FTD: ['FTD 나옴 · 선도주를 확인하는 때', 'good', 'FTD가 나왔어요. 미리 준비한 업종의 종목들이 실제로 거래량을 실어 신고가를 뚫는지 확인해요. 뚫는 종목이 많아야 진짜 상승장이에요.'],
  UPTREND: ['상승장 · 선도업종이 버티는지 보는 때', 'none', '지수가 크게 빠진 상태가 아니에요. 선도업종이 계속 강한지, 다음으로 올라오는 업종(워치리스트)이 무엇인지 봐요.'],
  UNKNOWN: ['시장 상태 판정 보류', 'none', 'FTD 판정 자료가 없어 시장 상태를 정하지 않았어요. 아래 숫자는 그대로 볼 수 있어요.'],
};

function phaseBanner(L, openFTD) {
  const ph = L.phase || { mode: 'UNKNOWN', indexes: [] };
  const [label, tone, meaning] = PHASE_BANNER[ph.mode] || PHASE_BANNER.UNKNOWN;
  const box = n('section', null, `ld-phase tone-${tone}`);
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(term('phaseMode', '지금 할 일'), ' · ', label);
  head.append(h);
  if (openFTD) { const go = n('button', 'FTD 탭 →', 'ld-link'); go.type = 'button'; go.onclick = openFTD; head.append(go); }
  box.append(head, n('p', meaning, 'ld-meaning'));
  if (ph.indexes?.length) box.append(n('small', ph.indexes.map(x => `${x.label} ${({ OUTSIDE: '하락장 아님', CORRECTION: '하락장', RALLY: '반등 시도', LATE: '반등 시도(늦은 구간)', CONFIRMED: 'FTD 나옴', PRESSURE: 'FTD 흔들림' })[x.phase] || x.phase}`).join(' · '), 'ld-phase-idx'));
  return box;
}

// Scatter: industry RS (x) × share of members near the 52-week high (y).
function mapCard(L, no) {
  const ind = L.industries || {};
  const pts = (ind.map || []).filter(p => isNum(p.rs) && isNum(p.nearHighPct));
  const card = n('section', null, 'ld-card');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('industryMap', '업종 지도'), ' · RS × 신고가 근접');
  head.append(h, n('span', `${pts.length}개 업종`, 'ld-status tone-none')); card.append(head);
  card.append(n('p', '상대강도(RS)와 52주 신고가를 함께 봅니다. 오른쪽 위로 갈수록 시장보다 강하고 고점 근처 종목도 많은 업종이에요. 초록은 선도업종, 노랑은 FTD 대기 워치리스트예요.', 'ld-meaning'));
  if (pts.length < 3) { card.append(n('div', '지도를 그릴 업종 자료가 부족합니다.', 'ld-empty')); return card; }
  const W = 360, H = 270, ml = 54, mr = 10, mt = 12, mb = 36, pw = W - ml - mr, ph = H - mt - mb;
  const X = v => ml + (v / 100) * pw, Y = v => mt + (1 - v / 100) * ph;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ld-map', role: 'img', 'aria-label': '업종 RS와 신고가 근접 비율 지도' });
  const pol = L.policy || {};
  const xLine = pol.industryTopPct ?? 80, yLine = pol.watchNearHighPctMin ?? 30;
  svg.append(sv('rect', { x: X(xLine), y: mt, width: X(100) - X(xLine), height: Y(yLine) - mt, class: 'ld-map-zone' }));
  for (let v = 0; v <= 100; v += 20) {
    svg.append(sv('line', { x1: X(v), x2: X(v), y1: mt, y2: mt + ph, class: 'ld-map-grid' }), sv('line', { x1: ml, x2: ml + pw, y1: Y(v), y2: Y(v), class: 'ld-map-grid' }));
    svg.append(sv('text', { x: X(v), y: mt + ph + 13, class: 'ld-map-tick', 'text-anchor': 'middle' }, `${v}`), sv('text', { x: ml - 5, y: Y(v) + 3, class: 'ld-map-tick', 'text-anchor': 'end' }, `${v}%`));
  }
  svg.append(sv('line', { x1: X(xLine), x2: X(xLine), y1: mt, y2: mt + ph, class: 'ld-map-rule' }), sv('line', { x1: ml, x2: ml + pw, y1: Y(yLine), y2: Y(yLine), class: 'ld-map-rule' }));
  svg.append(sv('line', { x1: ml, x2: ml, y1: mt, y2: mt + ph, class: 'ld-map-axis' }), sv('line', { x1: ml, x2: ml + pw, y1: mt + ph, y2: mt + ph, class: 'ld-map-axis' }));
  svg.append(sv('text', { x: ml + pw / 2, y: H - 4, class: 'ld-map-label', 'text-anchor': 'middle' }, '업종 RS (오른쪽 = 시장보다 강함) →'));
  svg.append(sv('text', { x: 10, y: mt + ph / 2, class: 'ld-map-label', 'text-anchor': 'middle', transform: `rotate(-90 10 ${mt + ph / 2})` }, '52주 고점 15% 이내 종목 비율 →'));
  const maxM = Math.max(...pts.map(p => p.members));
  const watchMin = pol.watchScoreMin ?? 4;
  const kind = p => p.leading ? 'lead' : p.watchScore >= watchMin ? 'watch' : 'other';
  const order = { other: 0, watch: 1, lead: 2 };
  pts.slice().sort((a, b) => order[kind(a)] - order[kind(b)]).forEach(p => {
    const c = sv('circle', { cx: X(p.rs), cy: Y(p.nearHighPct), r: (3 + 6 * Math.sqrt(p.members / maxM)).toFixed(1), class: `ld-dot ${kind(p)}` });
    c.append(sv('title', {}, `${short(p.name)} · RS ${num(p.rs)} · 고점 15% 이내 ${num(p.nearHighPct)}% · ${p.members}종목 · 준비도 ${p.watchScore}/6`));
    svg.append(c);
  });
  // Label the coloured dots, strongest first, skipping labels that would overlap.
  const placed = [];
  pts.filter(p => kind(p) !== 'other').sort((a, b) => b.rs - a.rs).slice(0, 10).forEach(p => {
    const x = X(p.rs), y0 = Y(p.nearHighPct), y = y0 - 8 < mt + 8 ? y0 + 15 : y0 - 8, txt = clip(p.name, 12);
    // Korean/Chinese characters are about one font-size wide; Latin letters about half.
    const w = [...txt].reduce((sum, ch) => sum + (/[\u1100-\u9fff\uac00-\ud7af\uff00-\uffef]/.test(ch) ? 11.8 : 6.8), 0);
    const left = Math.max(ml + 2, Math.min(W - 3 - w, x - w / 2));
    if (placed.some(q => Math.abs(q.y - y) < 10 && left < q.r && left + w > q.l)) return;
    placed.push({ l: left, r: left + w, y });
    svg.append(sv('text', { x: left, y, class: `ld-map-name ${kind(p)}` }, txt));
  });
  card.append(svg);
  const legend = n('div', null, 'ld-map-legend');
  legend.append(n('span', '● 선도업종', 'lead'), n('span', `● 워치리스트(준비도 ${watchMin}점 이상)`, 'watch'), n('span', '● 그 밖의 업종', 'other'), n('span', `초록 칸 = RS ${xLine} 이상이면서 비율 ${yLine}% 이상 (강하고 고점에 가까움)`, 'zone'));
  card.append(legend);
  return card;
}

function checkRow(watch, checks) {
  const wrap = n('ul', null, 'ld-checks');
  (checks || []).forEach((c, i) => {
    const li = n('li', null, watch?.[c.key] ? 'ok' : '');
    li.append(n('i', watch?.[c.key] ? '✓' : '·'), n('span', `${NO[i]} ${c.label}`));
    li.title = c.meaning;
    wrap.append(li);
  });
  return wrap;
}

function watchCard(L, openStock, rerender, no, flow = new Map()) {
  const ind = L.industries || {};
  const checks = L.watchChecks || [];
  const card = n('section', null, 'ld-card ld-watch');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('watchlist', 'FTD 대기 워치리스트'));
  head.append(h, n('span', `${ind.watchCount ?? 0}개 업종`, 'ld-status tone-mid')); card.append(head);
  card.append(n('p', '워치리스트는 매수 목록이 아니라 기회를 준비하는 목록이에요. 지수보다 먼저 강해지는 업종을 아래 6가지 질문으로 점검해, 4개 이상 "예"인 업종을 올려 둡니다. 시장이 FTD를 만들면 이 업종의 후보 종목부터 봐요.', 'ld-meaning'));
  const q = n('ol', null, 'ld-questions');
  checks.forEach((c, i) => { const li = n('li'); li.append(n('b', `${NO[i]} ${c.label}`), n('span', c.meaning)); q.append(li); });
  const qd = n('details', null, 'ld-qbox'); qd.append(n('summary', '6가지 질문과 숫자 기준 보기'), q); card.append(qd);

  // Strength while the index is weak (who moves before the index).
  const strong = n('div', null, 'ld-early');
  const line = (labelKey, label, names) => { const p = n('p'); p.append(term(labelKey, label), ' · ', names.length ? names.join(', ') : '없음'); return p; };
  strong.append(
    line('leadsIndex', '지수보다 먼저 고점권', (ind.leadsIndex || []).map(x => clip(x, 22))),
    line('heldUpIndustry', '조정 때 덜 빠진 업종', (ind.heldUp || []).map(x => `${clip(x.name, 18)} ${num(x.ratio, 2)}`)),
  );
  card.append(strong);

  const items = ind.watchlist || [];
  if (!items.length) card.append(n('div', '준비도 4점 이상인 업종이 아직 없어요. 시장이 약할 때는 정상이에요. 아래 체크리스트 표에서 3점짜리 업종을 확인해 보세요.', 'ld-empty'));
  const list = n('ol', null, 'ld-industries');
  items.forEach(x => {
    const li = n('li', null, 'ld-industry watch');
    const top = n('div', null, 'ld-ind-top');
    const name = n('div', null, 'ld-ind-name');
    name.append(n('strong', short(x.name)));
    if (x.leadsIndex) name.append(chip('지수보다 먼저 고점권', 'bo', 'leadsIndex'));
    if (x.heldUp) name.append(chip(`버팀 ${num(x.heldUpRatio, 2)}`, 'held', 'heldUpIndustry'));
    { const mc = moneyChip(x.name, flow); if (mc) name.append(mc); }
    if (x.rsLineHigh) name.append(chip('★ RS선 신고가', 'star', 'industryRsLine'));
    const score = n('div', null, 'ld-score');
    score.append(helpButton(`준비도 ${x.watchScore}/6`, 'watchScore'));
    const bar = n('div', null, 'ld-score-bar'); for (let i = 0; i < 6; i++) bar.append(n('i', null, i < x.watchScore ? 'on' : '')); score.append(bar);
    top.append(name, score); li.append(top);
    const stats = n('div', null, 'ld-ind-stats');
    const stat = (labelNode, valueNode) => { const s = n('div'); const l = n('span'); l.append(labelNode); s.append(l, valueNode); return s; };
    stats.append(
      stat(term('industryRs', 'RS'), n('b', num(x.rs))),
      stat(term('rankChange', '1개월'), rankDelta(x.rankChange20)),
      stat(term('newHighs', '신고가'), n('b', `${x.newHighs}종목`)),
      stat(term('above50', '50일선 위'), n('b', isNum(x.above50Pct) ? `${num(x.above50Pct)}%` : '—')),
      stat(term('growthBack', '실적 뒷받침'), n('b', isNum(x.growthPct) ? `${num(x.growthPct)}%` : '—')),
      stat(term('candidate', '후보'), n('b', `${x.candidateCount}종목`)),
    );
    li.append(stats, checkRow(x.watch, checks));
    if (x.candidates?.length) {
      const reps = n('div', null, 'ld-reps'); reps.append(n('span', `${LEADER_TERMS.candidate[0]} · 구성 ${x.members}종목`));
      x.candidates.forEach(r => reps.append(stockButton(r, openStock, `RS ${num(r.rs)} · 고점 -${num(r.dist, 1)}%${r.growth ? ` · ${r.growth}` : ''}${r.leader ? ' · 선도주' : ''}`)));
      li.append(reps);
    }
    list.append(li);
  });
  if (items.length) card.append(list);

  // Full checklist matrix (top 15 by readiness).
  const rows = ind.checklist || [];
  if (rows.length) {
    const tog = n('button', showMatrix ? '체크리스트 표 접기' : `체크리스트 표 펼치기 (준비도 상위 ${rows.length}개 업종)`, 'ld-more'); tog.type = 'button';
    tog.onclick = () => { showMatrix = !showMatrix; rerender(); };
    card.append(tog);
    if (showMatrix) {
      const wrap = n('div', null, 'ld-matrix-wrap');
      const t = n('table', null, 'ld-matrix');
      const hr = n('tr'); hr.append(n('th', '업종'), n('th', '점수'));
      checks.forEach((c, i) => { const th = n('th', `${NO[i]}`); th.title = c.label; th.append(n('small', WATCH_SHORT[c.key] || '')); hr.append(th); });
      t.append(hr);
      rows.forEach(r => {
        const tr = n('tr', null, r.leading ? 'lead' : r.watchScore >= (L.policy?.watchScoreMin ?? 4) ? 'watch' : '');
        const nm = n('td', null, 'nm'); nm.append(clip(r.name, 20)); if (r.leading) nm.append(n('em', ' 선도'));
        tr.append(nm, n('td', `${r.watchScore}`, 'sc'));
        checks.forEach(c => tr.append(n('td', r.watch?.[c.key] ? '✓' : '·', r.watch?.[c.key] ? 'ok' : '')));
        t.append(tr);
      });
      wrap.append(t); card.append(wrap);
    }
  }
  return card;
}

// Industries whose turnover share rose/fell over the last 5 days (from the liquidity block).
function moneyFlow(block) {
  const m = new Map();
  const ind = (block?.liquidity || {}).industries || {};
  [...(ind.inflow || []), ...(ind.outflow || [])].forEach(x => m.set(x.name, x));
  return m;
}
function moneyChip(name, flow) {
  const f = flow.get(name);
  if (f && f.shareChangePp >= 0.3) return chip(`💰 돈 몰림 +${num(f.shareChangePp, 1)}%p`, 'money', 'moneyFlow');
  if (f && f.shareChangePp <= -0.3) return chip(`돈 빠짐 ${num(f.shareChangePp, 1)}%p`, 'money-out', 'moneyFlow');
  return null;
}
export function renderLeaders(container, market, block, { openStock, openFTD, stale } = {}) {
  container.replaceChildren();
  const flow = moneyFlow(block);
  const rerender = () => renderLeaders(container, market, block, { openStock, openFTD, stale });
  // 판정('지금 할 일')을 맨 위에, 소개와 용어는 그 아래 접기로(내용은 그대로).
  const intro = n('details', null, 'intro-fold');
  const sum = n('summary'); sum.append('선도주·선도업종을 왜 보는지 · 보는 방법'); intro.append(sum);
  const p = n('p'); p.append('상승장은 ', term('leader', '선도주'), '와 ', term('leadingIndustry', '선도업종'), '이 끌고 갑니다. 선도업종은 지수보다 먼저 움직이는 경우가 많아서, FTD 전에 미리 찾아 두는 것이 중요해요. 상대강도(RS)와 52주 신고가를 함께 보고, 준비된 업종을 워치리스트로 관리합니다. 밑줄 친 말을 누르면 뜻이 나와요.');
  intro.append(p);
  const gloss = glossary(LEADER_TERMS, ['leader', 'core', 'rsOneil', 'ldDistance', 'rsLead', 'ldBreakout', 'heldUp', 'emergence', 'industryRs', 'rankChange', 'industryRsLine', 'newHighs', 'above50', 'persist', 'leadingIndustry', 'ldStage', 'stageNEW', 'stageUP', 'stageHOLD', 'stageDOWN', 'stageOUT', 'phaseMode', 'industryMap', 'watchlist', 'watchScore', 'candidate', 'growthBack', 'heldUpIndustry', 'leadsIndex']);
  const L = block?.leadership;
  if (!L || L.status !== 'OK') { container.append(intro, gloss, n('div', L?.note || '선도주·선도업종 자료를 준비 중입니다. 다음 자동 갱신 때 채워집니다.', 'ld-empty')); return; }
  if (stale) container.append(n('div', '최근 마감 거래일 자료가 아니라서 과거 기준으로 보여 주고 있어요.', 'ftd-data-warning'));
  container.append(phaseBanner(L, openFTD));
  container.append(intro, gloss);
  // Before an FTD the job is preparing the watchlist; after one, checking that leaders break out.
  const mode = L.phase?.mode;
  const builders = mode === 'WAIT'
    ? [no => watchCard(L, openStock, rerender, no, flow), no => mapCard(L, no), no => industryCard(L, openStock, no, flow), no => emergenceCard(L, market, openStock, null, no), no => leaderCard(L, openStock, rerender, no)]
    : mode === 'FTD'
      ? [no => emergenceCard(L, market, openStock, null, no), no => leaderCard(L, openStock, rerender, no), no => mapCard(L, no), no => industryCard(L, openStock, no, flow), no => watchCard(L, openStock, rerender, no, flow)]
      : [no => mapCard(L, no), no => industryCard(L, openStock, no, flow), no => watchCard(L, openStock, rerender, no, flow), no => leaderCard(L, openStock, rerender, no), no => emergenceCard(L, market, openStock, null, no)];
  builders.forEach((b, i) => container.append(b(NO[i])));
  const rules = n('details', null, 'ftd-rules'); rules.append(n('summary', '판정 기준·데이터 범위'));
  const pol = L.policy || {};
  rules.append(
    n('p', `기준일 ${L.asOf || '—'} · 분석 대상 ${L.universe ?? '—'}종목 (와모 스크리닝 목록 안에서의 비교이며 시장 전체가 아닙니다).`),
    n('p', `선도주: RS(오닐 방식) ${pol.rsMin} 이상, 52주 고점 ${pol.highDistanceMax}% 이내, Stage 2, 유동성(${({US:'시총 10억 달러 이상',KR:'20일 평균 거래대금 50억 원 이상',JP:'20일 평균 거래대금 5억 엔 이상',TW:'20일 평균 거래대금 1억 대만달러 이상'})[market]}). 핵심은 RS ${pol.rsCore} 이상.`),
    n('p', `선도주 출현: FTD 뒤 ${pol.emergenceWindow}거래일 안에 RS ${pol.rsMin} 이상 종목이 52주 최고 종가를 50일 평균 ${pol.breakoutVolume}배 이상 거래량으로 돌파한 수. 기준 ${pol.emergenceMin?.[market] ?? pol.emergenceMin?.KR}종목 (와모 기본값).`),
    n('p', `업종: 구성 ${pol.industryMinMembers}종목 이상인 업종만. 업종 RS는 구성 종목 RS 원점수의 중간값으로 업종끼리 순위를 매깁니다. 분류는 가격 화면의 산업 분류를 그대로 씁니다.`),
    n('p', `FTD 대기 워치리스트: 선도업종이 아닌 업종 중 6문항(${(L.watchChecks || []).map(c => c.label).join(' · ')}) 가운데 ${pol.watchScoreMin}개 이상을 채운 업종. 문항별 숫자 기준은 워치리스트 카드의 "6가지 질문" 상자에 있습니다(와모 기본값).`),
    n('p', '모든 숫자는 표시된 거래일까지의 종가·거래량만으로 계산하며, 매수 권유가 아닙니다. RS·신고가는 과거 가격의 상대 비교입니다.'),
  );
  if (L.note) rules.append(n('p', L.note));
  container.append(rules);
}
