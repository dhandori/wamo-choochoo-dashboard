// Leading stocks and leading industries. Reads wamo_trend.json → markets[X].leadership.
import { helpButton, addTerms } from './help.js?v=20261019-1';
import { LEADER_TERMS, STAGES, glossary } from './terms.mjs?v=20261019-1';
addTerms(LEADER_TERMS);
const n = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 0) => isNum(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const term = (key, label) => helpButton(label || LEADER_TERMS[key][0], key);
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
// US names arrive as full security titles; keep the company part.
const short = name => String(name || '').replace(/\s+(Common Stock|Class [A-Z]\b.*|Ordinary Shares.*|American Depositary Shares.*|Depositary Shares.*|Common Shares.*|Inc\. Common.*)$/i, '').replace(/\s+(Common Stock)$/i, '').trim();
let stockFilter = 'all', showAll = false;

const EMERGENCE = {
  CONFIRMED: ['출현 확인', 'good', 'FTD 뒤 10거래일 안에 기준 이상의 선도주가 신고가를 뚫었어요. 상승장이 진짜일 가능성이 커졌다는 뜻이에요. 비중 확대를 따져 볼 수 있는 단계예요.'],
  WATCHING: ['확인 중', 'mid', 'FTD 뒤 10거래일이 아직 지나지 않았어요. 신고가를 뚫는 선도주 수를 매일 세고 있어요.'],
  WEAK: ['선도주 부족', 'bad', 'FTD 뒤 10거래일이 지났는데 신고가를 뚫은 선도주가 기준보다 적어요. FTD 신뢰도가 낮다는 뜻이라 10% 이내 원칙을 지켜요.'],
  NO_FTD: ['FTD 없음', 'none', '지금 살아 있는 FTD가 없어서 "출현" 판정은 하지 않아요. 아래 숫자는 최근 10거래일 신고가 돌파 수(참고용)예요.'],
};

export function leadersSummary(block) {
  const L = block?.leadership;
  if (!L || L.status !== 'OK') return '대기';
  return `선도업종 ${L.industries?.leadingCount ?? 0}`;
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

function emergenceCard(L, market, openStock, openFTD) {
  const e = L.emergence || {};
  const [label, tone, meaning] = EMERGENCE[e.status] || EMERGENCE.NO_FTD;
  const card = n('section', null, `ld-card ld-emergence tone-${tone}`);
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append(term('emergence', '① 선도주 출현 신호'));
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
  if (!isNum(v) || v === 0) return n('span', v === 0 ? '–' : '—', 'ld-delta flat');
  return n('span', `${v > 0 ? '▲' : '▼'}${Math.abs(v)}`, `ld-delta ${v > 0 ? 'up' : 'down'}`);
}

function condDots(c) {
  const wrap = n('div', null, 'ld-conds');
  [['rsTop', 'RS 상위20%'], ['newHighs', '신고가 3+'], ['above50', '50일선 위 60%+'], ['persist', '10일 중 5일+']].forEach(([k, label]) => {
    wrap.append(n('span', `${c?.[k] ? '✓' : '·'} ${label}`, c?.[k] ? 'ok' : ''));
  });
  return wrap;
}

function industryCard(L, openStock) {
  const ind = L.industries || {};
  const card = n('section', null, 'ld-card');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append('② ', term('industryRs', '업종 RS'), ' 순위 · ', term('leadingIndustry', '선도업종'));
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

function leaderCard(L, openStock, rerender) {
  const ld = L.leaders || {};
  const card = n('section', null, 'ld-card');
  const head = n('div', null, 'ld-card-head');
  const h = n('h3'); h.append('③ ', term('leader', '선도주'), ' 목록');
  head.append(h, n('span', `${ld.count ?? 0}종목 · 핵심 ${ld.coreCount ?? 0}`, 'ld-status tone-none'));
  card.append(head);
  card.append(n('p', 'RS 80 이상 · 신고가 15% 이내 · Stage 2 · 유동성을 모두 채운 종목이에요. 표시(★·돌파·버팀)를 누르면 뜻이 나와요. 종목을 누르면 차트가 열려요.', 'ld-meaning'));
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
    const dist = n('span'); dist.append(term('distance', '고점까지'), n('b', ` ${isNum(x.highDistancePct) ? `-${num(x.highDistancePct, 1)}%` : '—'}`));
    nums.append(rs, dist); row.append(nums);
    const flags = n('div', null, 'ld-flags');
    if (x.core) flags.append(chip('핵심', 'core', 'core'));
    if (x.rsLead) flags.append(chip('★ RS선 선행', 'star', 'rsLead'));
    if (x.breakoutDates?.length) flags.append(chip(`돌파 ${md(x.breakoutDates[x.breakoutDates.length - 1])}`, 'bo', 'breakout'));
    if (isNum(x.heldUpRatio)) flags.append(chip(`버팀 ${num(x.heldUpRatio, 2)}${x.heldUp ? ' ✓' : ''}`, x.heldUp ? 'held' : 'soft', 'heldUp'));
    row.append(flags); list.append(row);
  });
  card.append(list);
  if (items.length > 20 && !showAll) { const more = n('button', `더 보기 (${Math.min(60, items.length) - 20}종목)`, 'ld-more'); more.type = 'button'; more.onclick = () => { showAll = true; rerender(); }; card.append(more); }
  return card;
}

export function renderLeaders(container, market, block, { openStock, openFTD, stale } = {}) {
  container.replaceChildren();
  const rerender = () => renderLeaders(container, market, block, { openStock, openFTD, stale });
  const intro = n('div', null, 'ld-intro');
  const p = n('p'); p.append('상승장은 ', term('leader', '선도주'), '와 ', term('leadingIndustry', '선도업종'), '이 끌고 갑니다. FTD가 나온 뒤 이들이 실제로 신고가를 뚫는지(①), 어떤 업종이 시장보다 강한지(②), 어떤 종목이 앞장서는지(③)를 봅니다. 밑줄 친 말을 누르면 뜻이 나와요.');
  intro.append(p); container.append(intro);
  container.append(glossary(LEADER_TERMS, ['leader', 'core', 'rsOneil', 'distance', 'rsLead', 'breakout', 'heldUp', 'emergence', 'industryRs', 'rankChange', 'industryRsLine', 'newHighs', 'above50', 'persist', 'leadingIndustry', 'stage', 'stageNEW', 'stageUP', 'stageHOLD', 'stageDOWN', 'stageOUT']));
  const L = block?.leadership;
  if (!L || L.status !== 'OK') { container.append(n('div', L?.note || '선도주·선도업종 자료를 준비 중입니다. 다음 자동 갱신 때 채워집니다.', 'ld-empty')); return; }
  if (stale) container.append(n('div', '최근 마감 거래일 자료가 아니라서 과거 기준으로 보여 주고 있어요.', 'ftd-data-warning'));
  container.append(emergenceCard(L, market, openStock, openFTD), industryCard(L, openStock), leaderCard(L, openStock, rerender));
  const rules = n('details', null, 'ftd-rules'); rules.append(n('summary', '판정 기준·데이터 범위'));
  const pol = L.policy || {};
  rules.append(
    n('p', `기준일 ${L.asOf || '—'} · 분석 대상 ${L.universe ?? '—'}종목 (와모 스크리닝 목록 안에서의 비교이며 시장 전체가 아닙니다).`),
    n('p', `선도주: RS(오닐 방식) ${pol.rsMin} 이상, 52주 고점 ${pol.highDistanceMax}% 이내, Stage 2, 유동성(${market === 'US' ? '시총 10억 달러 이상' : '20일 평균 거래대금 50억 원 이상'}). 핵심은 RS ${pol.rsCore} 이상.`),
    n('p', `선도주 출현: FTD 뒤 ${pol.emergenceWindow}거래일 안에 RS ${pol.rsMin} 이상 종목이 52주 최고 종가를 50일 평균 ${pol.breakoutVolume}배 이상 거래량으로 돌파한 수. 기준 ${market === 'US' ? pol.emergenceMin?.US : pol.emergenceMin?.KR}종목 (와모 기본값).`),
    n('p', `업종: 구성 ${pol.industryMinMembers}종목 이상인 업종만. 업종 RS는 구성 종목 RS 원점수의 중간값을 업종끼리 순위화. 분류는 가격 화면의 산업 분류를 그대로 씁니다.`),
    n('p', '모든 숫자는 표시 거래일까지의 종가·거래량만으로 계산하며, 매수 권유가 아닙니다. RS·신고가는 과거 가격의 상대 비교입니다.'),
  );
  if (L.note) rules.append(n('p', L.note));
  container.append(rules);
}
