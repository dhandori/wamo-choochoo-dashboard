// Leading stocks and leading industries. Reads wamo_trend.json → markets[X].leadership.
import { helpButton, addTerms } from './help.js';
import { LEADER_TERMS, STAGES, WATCH_SHORT, glossary } from './terms.mjs';
addTerms(LEADER_TERMS);
const n = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 0) => isNum(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const term = (key, label) => helpButton(label || LEADER_TERMS[key][0], key);
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
// US names arrive as full security titles; keep the company part.
const short = name => String(name || '').replace(/\s+(Common Stock|Class [A-Z]\b.*|Ordinary Shares.*|American Depositary Shares.*|Depositary Shares.*|Common Shares.*|Inc\. Common.*)$/i, '').replace(/\s+(Common Stock)$/i, '').trim();
let stockFilter = 'all', boardFilter = 'all', showAll = false, showMatrix = false;
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

// 종목의 소속 시장 배지·당일 등락은 레이더 스냅샷에서 가져온다(view.js 가 stockInfo 로 넘겨 줌).
let stockInfo = () => null;
function boardMark(id) {
  const b = stockInfo(id)?.board;
  return b ? n('span', b, `board-badge board-${b.toLowerCase().replace(/[^a-z]/g, '')}`) : null;
}
function stockButton(item, openStock, extra) {
  const b = n('button', null, 'ld-stock'); b.type = 'button';
  b.onclick = () => openStock(item.id);
  const name = n('strong', short(item.name)); const mk = boardMark(item.id); if (mk) name.append(mk);
  b.append(name, n('small', `${item.symbol}${extra ? ` · ${extra}` : ''}`));
  return b;
}
// 표 안에서 쓰는 작은 종목 링크(대표 종목·후보). 눌러서 상세를 연다.
function stockLink(item, openStock, title) {
  const b = n('button', short(item.name), 'ld-name-link'); b.type = 'button';
  b.title = title || `${short(item.name)} · ${item.symbol}`;
  b.onclick = () => openStock(item.id);
  return b;
}
// 시장 필터와 업종 구성 표시. 미국의 NYSE American·Arca 는 NYSE 칸에 함께 센다.
const BOARDS = { KR: ['KOSPI', 'KOSDAQ'], US: ['NASDAQ', 'NYSE'] };
const boardOf = item => item.board || stockInfo(item.id)?.board || null;
const inBoard = (b, want) => !!b && (b === want || (want === 'NYSE' && b.startsWith('NYSE')));
/** {'KOSDAQ': 3, 'KOSPI': 1} → 'KOSDAQ 3 · KOSPI 1' (많은 쪽부터). total 을 주면 'KOSDAQ 3/4'. */
function boardText(counts, total) {
  const list = Object.entries(counts || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!list.length) return '';
  if (total && list.length === 1) return `${list[0][0]} ${list[0][1]}/${total}`;
  return list.map(([k, v]) => `${k} ${v}`).join(' · ');
}
function countBoards(items) {
  const out = {};
  for (const x of items || []) { const b = boardOf(x); if (b) out[b] = (out[b] || 0) + 1; }
  return out;
}
const slug = s => String(s || '').replace(/[^0-9A-Za-z가-힣]+/g, '-');

function emergenceCard(L, market, openStock, openFTD, no) {
  const e = L.emergence || {};
  const [label, tone, meaning] = EMERGENCE[e.status] || EMERGENCE.NO_FTD;
  const card = n('section', null, `ld-card ld-emergence tone-${tone}`); card.id = 'ld-sec-emergence';
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('emergence', '선도주 출현 신호'));
  head.append(h, n('span', label, `ld-status tone-${tone}`)); card.append(head);
  card.append(n('p', meaning, 'ld-meaning'));
  const meter = n('div', null, 'ld-meter');
  const count = n('div', null, 'ld-meter-count');
  count.append(n('strong', `${e.count ?? 0}`), n('span', e.status === 'NO_FTD' ? '최근 10거래일 신고가 돌파 (RS 80 이상)' : `/ 기준 ${e.need}종목 · FTD 뒤 신고가 돌파`));
  const bar = n('div', null, 'ld-bar'); const fill = n('span'); fill.style.width = `${Math.min(100, ((e.count || 0) / (e.need || 1)) * 100)}%`; bar.append(fill);
  meter.append(count, bar);
  // 시장별 내역. 판정(기준 수)은 시장 전체로 하고, 어느 지수 쪽에서 나왔는지만 나눠 보여 준다.
  const split = boardText(e.byBoard || countBoards(e.items));
  if (split && (e.count || 0) > 0) meter.append(n('small', `시장별 ${split}${e.byBoard ? '' : ' (상위 20종목 기준)'} · 판정은 시장 전체 수로 해요`, 'ld-board-split'));
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
  const wrap = n('details', null, 'ld-legend');
  wrap.append(n('summary', '단계(신규 진입·강화·유지·약화·상위권 이탈) 뜻 보기'));
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
  if (v === 0) return n('span', '–', 'ld-delta flat');
  return n('span', `${v > 0 ? '▲' : '▼'}${Math.abs(v)}`, `ld-delta ${v > 0 ? 'up' : 'down'}`);
}

// 표 한 칸: 값 + (조건을 채웠으면 초록). 휴대폰에서는 칸 앞에 이름을 붙인다(data-label).
function cell(label, node, ok = null, cls = '') {
  const c = n('div', null, `ld-td ${cls}${ok === true ? ' ok' : ok === false ? ' no' : ''}`.trim());
  c.dataset.label = label;
  if (typeof node === 'string') c.append(n('b', node)); else c.append(node);
  return c;
}
function headRow(cols) {
  const r = n('div', null, 'ld-tr ld-thead');
  cols.forEach(([key, label, cls]) => { const c = n('div', null, `ld-td ${cls || ''}`.trim()); c.append(key ? term(key, label) : label); r.append(c); });
  return r;
}

function industryCard(L, openStock, no, flow = new Map()) {
  const ind = L.industries || {};
  const card = n('section', null, 'ld-card'); card.id = 'ld-sec-industries';
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('industryRs', '업종 RS'), ' 순위 · ', term('leadingIndustry', '선도업종'));
  head.append(h, n('span', `선도업종 ${ind.leadingCount ?? 0}개 / ${ind.count ?? 0}개 업종`, 'ld-status tone-none'));
  card.append(head);
  card.append(n('p', '업종 종목들을 같은 무게로 묶어 RS 점수를 매겨요. 초록 숫자는 선도업종 조건(RS 상위 20% · 신고가 3종목+ · 50일선 위 60%+ · 10일 중 5일+ 상위권)을 채운 칸이고, 4칸이 모두 초록이면 선도업종(✔)이에요.', 'ld-meaning'));
  card.append(stageLegend());
  if (ind.status !== 'OK' || !ind.items?.length) { card.append(n('div', ind.note || '업종 순위를 계산할 자료가 부족합니다.', 'ld-empty')); return card; }
  const table = n('div', null, 'ld-table ld-ind-table');
  table.append(headRow([[null, '#', 'c-rank'], [null, '업종 · 대표 종목', 'c-name'], ['ldStage', '단계', 'c-stage'], ['industryRs', 'RS'], ['rankChange', '1주'], ['rankChange', '1개월'], ['newHighs', '신고가'], ['above50', '50일선 위'], ['persist', '상위권']]));
  ind.items.slice(0, 10).forEach(x => {
    const c = x.conditions || {};
    const row = n('div', null, `ld-tr${x.leading ? ' leading' : ''}`); row.id = `ld-ind-${slug(x.name)}`;
    const name = n('div', null, 'ld-td c-name');
    const title = n('div', null, 'ld-ind-name'); title.append(n('strong', x.name));
    if (x.leading) title.append(chip('✔ 선도업종', 'lead', 'leadingIndustry'));
    if (x.rsLineHigh) title.append(chip('★ RS선 신고가', 'star', 'industryRsLine'));
    if (x.leadsIndex) title.append(chip('지수보다 먼저 고점권', 'bo', 'leadsIndex'));
    if (x.heldUp) title.append(chip(`버팀 ${num(x.heldUpRatio, 2)}`, 'held', 'heldUpIndustry'));
    { const mc = moneyChip(x.name, flow); if (mc) title.append(mc); }
    name.append(title);
    if (x.representatives?.length) {
      const reps = n('div', null, 'ld-reps-inline');
      const mix = boardText(x.boards, x.members);
      reps.append(n('span', `${x.members}종목${mix ? ` (${mix})` : ''} ·`));
      x.representatives.forEach(r => reps.append(stockLink(r, openStock, `${short(r.name)} · ${r.symbol} · RS ${num(r.rs)}${r.leader ? ' · 선도주' : ''}`), n('small', `${num(r.rs)}${r.leader ? '★' : ''}`)));
      name.append(reps);
    }
    const rank = n('div', null, 'ld-td c-rank'); rank.append(n('span', `${x.rank}`, 'ld-rank'));
    const stage = n('div', null, 'ld-td c-stage'); stage.dataset.label = '단계'; stage.append(stageChip(x.stage));
    row.append(rank, name, stage,
      cell('RS', num(x.rs), !!c.rsTop),
      cell('1주', rankDelta(x.rankChange5)),
      cell('1개월', rankDelta(x.rankChange20)),
      cell('신고가', `${x.newHighs}`, !!c.newHighs),
      cell('50일선 위', isNum(x.above50Pct) ? `${num(x.above50Pct)}%` : '—', !!c.above50),
      cell('상위권', `${x.persist}/10일`, !!c.persist));
    table.append(row);
  });
  card.append(table);
  if (ind.dropped?.length) {
    const out = n('p', null, 'ld-dropped'); out.append(stageChip('OUT'), ` ${ind.dropped.map(x => x.name).join(' · ')}`);
    card.append(out);
  }
  return card;
}

function leaderCard(L, openStock, rerender, no, market) {
  const ld = L.leaders || {};
  const card = n('section', null, 'ld-card'); card.id = 'ld-sec-leaders';
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
  const boards = BOARDS[market];
  if (boards) {
    // 시장 필터: 같은 선도주 목록을 상장 시장별로 나눠 본다(종류 필터와 함께 걸린다).
    const all = ld.items || [];
    if (boardFilter !== 'all' && !boards.includes(boardFilter)) boardFilter = 'all';
    const row = n('div', null, 'ld-filters ld-board-filters');
    row.append(n('span', '시장', 'ld-filter-label'));
    // 수는 서버가 센 전체(byBoard, 목록은 60종목까지만 싣는다)를 먼저 쓴다.
    const boardCount = b => ld.byBoard ? Object.entries(ld.byBoard).filter(([k]) => inBoard(k, b)).reduce((t, [, v]) => t + v, 0)
      : all.filter(x => inBoard(boardOf(x), b)).length;
    [['all', `전체 ${ld.count ?? all.length}`], ...boards.map(b => [b, `${b} ${boardCount(b)}`])].forEach(([k, label]) => {
      const b = n('button', label, boardFilter === k ? 'active' : ''); b.type = 'button';
      b.setAttribute('aria-pressed', String(boardFilter === k));
      b.onclick = () => { boardFilter = k; showAll = false; rerender(); };
      row.append(b);
    });
    card.append(row);
  }
  const items = (ld.items || []).filter(x => (stockFilter === 'all' || (stockFilter === 'core' ? x.core : stockFilter === 'rsLead' ? x.rsLead : x.breakoutDates?.length))
    && (boardFilter === 'all' || inBoard(boardOf(x), boardFilter)));
  if (!items.length) { card.append(n('div', '조건에 맞는 선도주가 없어요. 시장이 약할 때는 선도주가 거의 없는 것이 정상이에요.', 'ld-empty')); return card; }
  const table = n('div', null, 'ld-table ld-stock-table');
  table.append(headRow([[null, '종목 · 업종', 'c-name'], ['rsOneil', 'RS'], ['ldDistance', '고점까지'], [null, '당일'], [null, '표시', 'c-flags']]));
  items.slice(0, showAll ? 60 : 20).forEach(x => {
    const row = n('div', null, `ld-tr${x.core ? ' core' : ''}`);
    const name = n('div', null, 'ld-td c-name'); name.append(stockButton(x, openStock, x.industry));
    const chg = isNum(x.changePct) ? n('b', `${x.changePct > 0 ? '+' : ''}${num(x.changePct, 1)}%`, x.changePct > 0 ? 'up' : x.changePct < 0 ? 'down' : '') : n('b', '—');
    const flags = n('div', null, 'ld-td c-flags');
    if (x.core) flags.append(chip('핵심', 'core', 'core'));
    if (x.rsLead) flags.append(chip('★ RS선 선행', 'star', 'rsLead'));
    if (x.breakoutDates?.length) flags.append(chip(`돌파 ${md(x.breakoutDates[x.breakoutDates.length - 1])}`, 'bo', 'ldBreakout'));
    if (isNum(x.heldUpRatio)) flags.append(chip(`버팀 ${num(x.heldUpRatio, 2)}${x.heldUp ? ' ✓' : ''}`, x.heldUp ? 'held' : 'soft', 'heldUp'));
    row.append(name, cell('RS', num(x.rs), x.rs >= (L.policy?.rsCore ?? 90)), cell('고점까지', isNum(x.highDistancePct) ? `-${num(x.highDistancePct, 1)}%` : '—', isNum(x.highDistancePct) ? x.highDistancePct <= 5 : null), cell('당일', chg), flags);
    table.append(row);
  });
  card.append(table);
  if (items.length > 20 && !showAll) { const more = n('button', `더 보기 (${Math.min(60, items.length) - 20}종목)`, 'ld-more'); more.type = 'button'; more.onclick = () => { showAll = true; rerender(); }; card.append(more); }
  return card;
}


const PHASE_BANNER = {
  WAIT: ['FTD 전 · 준비하는 때', 'mid', '시장은 아직 바닥을 확인하지 못했어요. 지금은 사는 때가 아니라, 지수보다 먼저 강해지는 업종을 찾아 워치리스트를 만들어 두는 때예요. FTD가 나오면 이 목록부터 봅니다.'],
  FTD: ['FTD 나옴 · 선도주를 확인하는 때', 'good', 'FTD가 나왔어요. 미리 준비한 업종의 종목들이 실제로 거래량을 실어 신고가를 뚫는지 확인해요. 뚫는 종목이 많아야 진짜 상승장이에요.'],
  UPTREND: ['상승장 · 선도업종이 버티는지 보는 때', 'none', '지수가 크게 빠진 상태가 아니에요. 선도업종이 계속 강한지, 다음으로 올라오는 업종(워치리스트)이 무엇인지 봐요.'],
  UNKNOWN: ['시장 상태 판정 보류', 'none', 'FTD 판정 자료가 없어 시장 상태를 정하지 않았어요. 아래 숫자는 그대로 볼 수 있어요.'],
};
const PHASE_WORD = { OUTSIDE: '하락장 아님', CORRECTION: '하락장', RALLY: '반등 시도', LATE: '반등 시도(늦은 구간)', CONFIRMED: 'FTD 나옴', PRESSURE: 'FTD 흔들림' };

// 맨 위 요약: 지금 할 일 + 핵심 숫자 4칸(누르면 그 카드로). 카드 순서가 단계마다 바뀌어도 숫자는 늘 같은 자리.
function phaseBanner(L, openFTD, jump) {
  const ph = L.phase || { mode: 'UNKNOWN', indexes: [] };
  const [label, tone, meaning] = PHASE_BANNER[ph.mode] || PHASE_BANNER.UNKNOWN;
  const box = n('section', null, `ld-phase tone-${tone}`);
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(term('phaseMode', '지금 할 일'), ' · ', label);
  head.append(h);
  if (openFTD) { const go = n('button', 'FTD 탭 →', 'ld-link'); go.type = 'button'; go.onclick = openFTD; head.append(go); }
  box.append(head, n('p', meaning, 'ld-meaning'));
  if (ph.indexes?.length) box.append(n('small', ph.indexes.map(x => `${x.label} ${PHASE_WORD[x.phase] || x.phase}`).join(' · '), 'ld-phase-idx'));
  const ind = L.industries || {}, ld = L.leaders || {}, e = L.emergence || {};
  const [eLabel, eTone] = EMERGENCE[e.status] || EMERGENCE.NO_FTD;
  const stats = n('div', null, 'ld-kpis');
  [['leaders', '선도주', `${ld.count ?? 0}`, `핵심 ${ld.coreCount ?? 0} · ★ ${ld.rsLeadCount ?? 0}`, ''],
    ['industries', '선도업종', `${ind.leadingCount ?? 0}`, `/ ${ind.count ?? 0}개 업종`, ''],
    ['watch', '워치리스트', `${ind.watchCount ?? 0}`, '준비도 4점+ 업종', ''],
    ['emergence', '출현 신호', e.status === 'NO_FTD' ? `${e.count ?? 0}` : `${e.count ?? 0}/${e.need ?? '—'}`, eLabel, `tone-${eTone}`]].forEach(([key, title, value, sub, cls]) => {
    const b = n('button', null, `ld-kpi ${cls}`.trim()); b.type = 'button'; b.onclick = () => jump(key);
    b.append(n('span', title), n('strong', value), n('small', sub));
    stats.append(b);
  });
  box.append(stats);
  return box;
}

// 카드 바로가기: 화면 위(탭 줄 아래)에 붙어 따라온다.
const SEC_NAME = { emergence: '출현 신호', leaders: '선도주', map: '업종 지도', industries: '업종 순위', watch: '워치리스트' };
function sectionNav(order, jump) {
  const nav = n('nav', null, 'ld-nav'); nav.setAttribute('aria-label', '선도주 화면 바로가기');
  order.forEach((key, i) => { const b = n('button', `${NO[i]} ${SEC_NAME[key]}`); b.type = 'button'; b.dataset.sec = key; b.onclick = () => jump(key); nav.append(b); });
  return nav;
}

// Scatter: industry RS (x) × share of members near the 52-week high (y).
// 비율이 0%인 업종(약한 장에서는 대부분)은 축 아래 '0% 띠'에 따로 깔아 서로 겹치거나 숫자를 가리지 않게 한다.
// 점을 누르면 아래 정보 줄에 그 업종이 나오고, 순위 표·워치리스트에 있으면 그 줄로 이동한다.
function mapCard(L, no, focusIndustry) {
  const ind = L.industries || {};
  const pts = (ind.map || []).filter(p => isNum(p.rs) && isNum(p.nearHighPct));
  const card = n('section', null, 'ld-card'); card.id = 'ld-sec-map';
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('industryMap', '업종 지도'), ' · RS × 신고가 근접');
  head.append(h, n('span', `${pts.length}개 업종`, 'ld-status tone-none')); card.append(head);
  card.append(n('p', '상대강도(RS)와 52주 신고가를 함께 봅니다. 오른쪽 위로 갈수록 시장보다 강하고 고점 근처 종목도 많은 업종이에요. 초록은 선도업종, 노랑은 FTD 대기 워치리스트예요. 점을 누르면 업종 정보가 나와요.', 'ld-meaning'));
  if (pts.length < 3) { card.append(n('div', '지도를 그릴 업종 자료가 부족합니다.', 'ld-empty')); return card; }
  // 휴대폰은 좁은 판으로 다시 그려 글자가 작아지지 않게 한다.
  const narrow = typeof window !== 'undefined' && window.innerWidth < 700;
  const W = narrow ? 380 : 640, H = narrow ? 300 : 330, ml = narrow ? 50 : 64, mr = 12, mt = 14, band = 34, mb = 40, pw = W - ml - mr, ph = H - mt - band - mb;
  const X = v => ml + (v / 100) * pw, Y = v => mt + (1 - v / 100) * ph, bandY = mt + ph + band / 2;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ld-map', role: 'img', 'aria-label': '업종 RS와 신고가 근접 비율 지도' });
  const pol = L.policy || {};
  const xLine = pol.industryTopPct ?? 80, yLine = pol.watchNearHighPctMin ?? 30;
  svg.append(sv('rect', { x: X(xLine), y: mt, width: X(100) - X(xLine), height: Y(yLine) - mt, class: 'ld-map-zone' }));
  for (let v = 0; v <= 100; v += 20) {
    svg.append(sv('line', { x1: X(v), x2: X(v), y1: mt, y2: mt + ph + band, class: 'ld-map-grid' }));
    if (v > 0) svg.append(sv('line', { x1: ml, x2: ml + pw, y1: Y(v), y2: Y(v), class: 'ld-map-grid' }), sv('text', { x: ml - 8, y: Y(v) + 4, class: 'ld-map-tick', 'text-anchor': 'end' }, `${v}%`));
    svg.append(sv('text', { x: X(v), y: mt + ph + band + 16, class: 'ld-map-tick', 'text-anchor': 'middle' }, `${v}`));
  }
  svg.append(sv('rect', { x: ml, y: mt + ph + 2, width: pw, height: band - 4, class: 'ld-map-band' }), sv('text', { x: ml - 8, y: bandY + 4, class: 'ld-map-tick', 'text-anchor': 'end' }, '0%'));
  svg.append(sv('line', { x1: X(xLine), x2: X(xLine), y1: mt, y2: mt + ph + band, class: 'ld-map-rule' }), sv('line', { x1: ml, x2: ml + pw, y1: Y(yLine), y2: Y(yLine), class: 'ld-map-rule' }));
  svg.append(sv('line', { x1: ml, x2: ml, y1: mt, y2: mt + ph + band, class: 'ld-map-axis' }), sv('line', { x1: ml, x2: ml + pw, y1: mt + ph + band, y2: mt + ph + band, class: 'ld-map-axis' }));
  svg.append(sv('text', { x: ml + pw / 2, y: H - 6, class: 'ld-map-label', 'text-anchor': 'middle' }, '업종 RS (오른쪽 = 시장보다 강함) →'));
  svg.append(sv('text', { x: 14, y: mt + ph / 2, class: 'ld-map-label', 'text-anchor': 'middle', transform: `rotate(-90 14 ${mt + ph / 2})` }, '고점 15% 이내 종목 비율 →'));
  const maxM = Math.max(...pts.map(p => p.members));
  const watchMin = pol.watchScoreMin ?? 4;
  const kind = p => p.leading ? 'lead' : p.watchScore >= watchMin ? 'watch' : 'other';
  const order = { other: 0, watch: 1, lead: 2 };
  const info = n('div', null, 'ld-map-info'); info.append(n('span', '점을 누르면 업종 정보가 여기 나와요.', 'ld-map-hint'));
  const pos = new Map();
  // 0% 띠: 왼쪽부터 놓되 바로 앞 점과 겹치면 위·아래로 번갈아 비킨다.
  let lastX = -99, flip = 0;
  pts.filter(p => p.nearHighPct === 0).sort((a, b) => a.rs - b.rs).forEach(p => {
    const x = X(p.rs); if (x - lastX < 9) flip = (flip + 1) % 3; else flip = 0; lastX = x;
    pos.set(p, [x, bandY + [0, -8, 8][flip]]);
  });
  pts.filter(p => p.nearHighPct > 0).forEach(p => pos.set(p, [X(p.rs), Y(p.nearHighPct)]));
  const show = p => {
    info.replaceChildren();
    const k = kind(p);
    info.append(n('strong', short(p.name), `ld-map-pick ${k}`), n('span', `RS ${num(p.rs)} · 고점 15% 이내 ${num(p.nearHighPct)}% · ${p.members}종목 · 준비도 ${p.watchScore}/6${p.leading ? ' · 선도업종' : ''}`));
    const target = document.getElementById(`ld-ind-${slug(p.name)}`) || document.getElementById(`ld-watch-${slug(p.name)}`);
    if (target) { const go = n('button', '표에서 보기 ↓', 'ld-link'); go.type = 'button'; go.onclick = () => focusIndustry(target); info.append(go); }
    svg.querySelectorAll('.ld-dot.picked').forEach(d => d.classList.remove('picked'));
    dots.get(p)?.classList.add('picked');
  };
  const dots = new Map();
  pts.slice().sort((a, b) => order[kind(a)] - order[kind(b)]).forEach(p => {
    const [cx, cy] = pos.get(p);
    const c = sv('circle', { cx, cy, r: (3.5 + 6 * Math.sqrt(p.members / maxM)).toFixed(1), class: `ld-dot ${kind(p)}`, tabindex: 0, role: 'button', 'aria-label': `${short(p.name)} 업종 정보` });
    c.append(sv('title', {}, `${short(p.name)} · RS ${num(p.rs)} · 고점 15% 이내 ${num(p.nearHighPct)}% · ${p.members}종목 · 준비도 ${p.watchScore}/6`));
    c.addEventListener('click', () => show(p)); c.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); show(p); } });
    dots.set(p, c); svg.append(c);
  });
  // 이름표: 색 점(선도·워치리스트)을 먼저, 모자라면 RS 높은 업종으로 채워 최대 8개. 겹치는 이름표는 건너뛴다.
  const labelled = [...pts.filter(p => kind(p) !== 'other').sort((a, b) => b.rs - a.rs), ...pts.filter(p => kind(p) === 'other').sort((a, b) => b.rs - a.rs)].slice(0, 8);
  const placed = [];
  labelled.forEach(p => {
    const [x, y0] = pos.get(p), inBand = p.nearHighPct === 0;
    const y = inBand ? mt + ph - 6 : (y0 - 10 < mt + 8 ? y0 + 16 : y0 - 10), txt = clip(p.name, 12);
    // Korean/Chinese characters are about one font-size wide; Latin letters about half.
    const w = [...txt].reduce((sum, ch) => sum + (/[ᄀ-鿿가-힯＀-￯]/.test(ch) ? 11.8 : 6.8), 0);
    const left = Math.max(ml + 2, Math.min(W - mr - w, x - w / 2));
    if (placed.some(q => Math.abs(q.y - y) < 12 && left < q.r + 4 && left + w > q.l - 4)) return;
    placed.push({ l: left, r: left + w, y });
    svg.append(sv('text', { x: left, y, class: `ld-map-name ${kind(p)}` }, txt));
  });
  card.append(svg, info);
  const legend = n('div', null, 'ld-map-legend');
  legend.append(n('span', '● 선도업종', 'lead'), n('span', `● 워치리스트(준비도 ${watchMin}점 이상)`, 'watch'), n('span', '● 그 밖의 업종', 'other'), n('span', `초록 칸 = RS ${xLine} 이상이면서 비율 ${yLine}% 이상 (강하고 고점에 가까움) · 아래 띠 = 고점 15% 이내 종목이 하나도 없는 업종`, 'zone'));
  card.append(legend);
  return card;
}

function checkDots(watch, checks) {
  const wrap = n('div', null, 'ld-qdots');
  (checks || []).forEach((c, i) => {
    const ok = !!watch?.[c.key];
    const d = n('span', NO[i], ok ? 'ok' : ''); d.title = `${NO[i]} ${c.label} · ${ok ? '예' : '아니오'} — ${c.meaning}`;
    d.setAttribute('aria-label', `${c.label} ${ok ? '예' : '아니오'}`);
    wrap.append(d);
  });
  return wrap;
}

function watchCard(L, openStock, rerender, no, flow = new Map()) {
  const ind = L.industries || {};
  const checks = L.watchChecks || [];
  const card = n('section', null, 'ld-card ld-watch'); card.id = 'ld-sec-watch';
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(`${no} `, term('watchlist', 'FTD 대기 워치리스트'));
  head.append(h, n('span', `${ind.watchCount ?? 0}개 업종`, 'ld-status tone-mid')); card.append(head);
  card.append(n('p', '매수 목록이 아니라 기회를 준비하는 목록이에요. 지수보다 먼저 강해지는 업종을 6가지 질문(①~⑥, 초록 = 예)으로 점검해 4개 이상 "예"인 업종을 올려 둡니다. 시장이 FTD를 만들면 이 업종의 후보 종목부터 봐요.', 'ld-meaning'));
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
  else {
    const table = n('div', null, 'ld-table ld-watch-table');
    table.append(headRow([[null, '업종 · 매수 후보', 'c-name'], ['watchScore', '준비도', 'c-score'], ['industryRs', 'RS'], ['rankChange', '1개월'], ['newHighs', '신고가'], ['above50', '50일선 위'], ['growthBack', '실적'], [null, '①~⑥', 'c-q']]));
    items.forEach(x => {
      const row = n('div', null, 'ld-tr watch'); row.id = `ld-watch-${slug(x.name)}`;
      const name = n('div', null, 'ld-td c-name');
      const title = n('div', null, 'ld-ind-name'); title.append(n('strong', short(x.name)));
      if (x.leadsIndex) title.append(chip('지수보다 먼저 고점권', 'bo', 'leadsIndex'));
      if (x.heldUp) title.append(chip(`버팀 ${num(x.heldUpRatio, 2)}`, 'held', 'heldUpIndustry'));
      { const mc = moneyChip(x.name, flow); if (mc) title.append(mc); }
      if (x.rsLineHigh) title.append(chip('★ RS선 신고가', 'star', 'industryRsLine'));
      name.append(title);
      if (x.candidates?.length) {
        const reps = n('div', null, 'ld-reps-inline');
        const mix = boardText(x.boards, x.members);
        reps.append(n('span', `${LEADER_TERMS.candidate[0]} ${x.candidateCount}/${x.members}${mix ? ` (${mix})` : ''} ·`));
        x.candidates.forEach(r => reps.append(stockLink(r, openStock, `${short(r.name)} · ${r.symbol} · RS ${num(r.rs)} · 고점 -${num(r.dist, 1)}%${r.growth ? ` · ${r.growth}` : ''}${r.leader ? ' · 선도주' : ''}`), n('small', `${num(r.rs)} · -${num(r.dist, 1)}%${r.leader ? '★' : ''}`)));
        name.append(reps);
      }
      const score = n('div', null, 'ld-td c-score'); score.dataset.label = '준비도';
      const bar = n('div', null, 'ld-score-bar'); for (let i = 0; i < 6; i++) bar.append(n('i', null, i < x.watchScore ? 'on' : ''));
      score.append(n('b', `${x.watchScore}/6`), bar);
      const qc = n('div', null, 'ld-td c-q'); qc.dataset.label = '①~⑥'; qc.append(checkDots(x.watch, checks));
      row.append(name, score, cell('RS', num(x.rs)), cell('1개월', rankDelta(x.rankChange20)), cell('신고가', `${x.newHighs}`), cell('50일선 위', isNum(x.above50Pct) ? `${num(x.above50Pct)}%` : '—'), cell('실적', isNum(x.growthPct) ? `${num(x.growthPct)}%` : '—'), qc);
      table.append(row);
    });
    card.append(table);
  }

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
export function renderLeaders(container, market, block, { openStock, openFTD, stale, info } = {}) {
  container.replaceChildren();
  if (info) stockInfo = info;
  const flow = moneyFlow(block);
  const rerender = () => { const y = window.scrollY; renderLeaders(container, market, block, { openStock, openFTD, stale, info }); window.scrollTo(0, y); };
  // 판정('지금 할 일')을 맨 위에, 소개와 용어는 그 아래 접기로(내용은 그대로).
  const intro = n('details', null, 'intro-fold');
  const sum = n('summary'); sum.append('선도주·선도업종을 왜 보는지 · 보는 방법'); intro.append(sum);
  const p = n('p'); p.append('상승장은 ', term('leader', '선도주'), '와 ', term('leadingIndustry', '선도업종'), '이 끌고 갑니다. 선도업종은 지수보다 먼저 움직이는 경우가 많아서, FTD 전에 미리 찾아 두는 것이 중요해요. 상대강도(RS)와 52주 신고가를 함께 보고, 준비된 업종을 워치리스트로 관리합니다. 밑줄 친 말을 누르면 뜻이 나와요.');
  intro.append(p);
  const gloss = glossary(LEADER_TERMS, ['leader', 'core', 'rsOneil', 'ldDistance', 'rsLead', 'ldBreakout', 'heldUp', 'emergence', 'industryRs', 'rankChange', 'industryRsLine', 'newHighs', 'above50', 'persist', 'leadingIndustry', 'ldStage', 'stageNEW', 'stageUP', 'stageHOLD', 'stageDOWN', 'stageOUT', 'phaseMode', 'industryMap', 'watchlist', 'watchScore', 'candidate', 'growthBack', 'heldUpIndustry', 'leadsIndex']);
  const L = block?.leadership;
  if (!L || L.status !== 'OK') { container.append(intro, gloss, n('div', L?.note || '선도주·선도업종 자료를 준비 중입니다. 다음 자동 갱신 때 채워집니다.', 'ld-empty')); return; }
  if (stale) container.append(n('div', '최근 마감 거래일 자료가 아니라서 과거 기준으로 보여 주고 있어요.', 'ftd-data-warning'));
  // 카드로 이동: 고정된 탭 줄·바로가기 줄 높이만큼 띄워서 제목이 가려지지 않게.
  const offset = () => (document.querySelector('.tabs')?.getBoundingClientRect().height || 0) + (container.querySelector('.ld-nav')?.getBoundingClientRect().height || 0) + 10;
  const jump = key => { const el = container.querySelector(`#ld-sec-${key}`); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset(), behavior: 'smooth' }); };
  const focusIndustry = el => { window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset() - 40, behavior: 'smooth' }); el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); };
  // Before an FTD the job is preparing the watchlist; after one, checking that leaders break out.
  const mode = L.phase?.mode;
  const order = mode === 'WAIT' ? ['watch', 'map', 'industries', 'emergence', 'leaders']
    : mode === 'FTD' ? ['emergence', 'leaders', 'map', 'industries', 'watch']
      : ['map', 'industries', 'watch', 'leaders', 'emergence'];
  const make = { watch: no => watchCard(L, openStock, rerender, no, flow), map: no => mapCard(L, no, focusIndustry), industries: no => industryCard(L, openStock, no, flow), emergence: no => emergenceCard(L, market, openStock, null, no), leaders: no => leaderCard(L, openStock, rerender, no, market) };
  container.append(phaseBanner(L, openFTD, jump), sectionNav(order, jump), intro, gloss);
  order.forEach((key, i) => container.append(make[key](NO[i])));
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
