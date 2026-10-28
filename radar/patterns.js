// 패턴 셋업 화면. wamo_insights.json 의 stocks[].patterns 와 patternMeta 를 읽는다.
// 셋업 판정은 서버(wamo_patterns.py)가 하고, 여기서는 고르고 보여 주기만 한다.
import { helpButton, addTerms } from './help.js?v=20261029-pt3';
import { PATTERN_TERMS, glossary } from './terms.mjs?v=20261029-pt1';
// 이 파일의 PHASES 는 셋업 진행 단계다. FTD 지수 단계는 이름을 구분해 가져온다.
import { PHASES as FTD_PHASES, freshIndex } from './ftd-model.mjs?v=20261028-h60';
import { CAP_FLOORS } from './model.mjs?v=20261028-h60';
addTerms(PATTERN_TERMS);

const n = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 1) => isNum(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const pct = v => isNum(v) ? `${v > 0 ? '+' : ''}${num(v)}%` : '—';
const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
const term = (key, label) => helpButton(label || PATTERN_TERMS[key][0], key);
// 미국 종목명은 증권 전체 이름으로 들어와 꼬리가 길다. 회사 이름만 남긴다.
const short = name => String(name || '').replace(/\s+(Common Stock|Class [A-Z]\b.*|Ordinary Shares.*|American Depositary Shares.*|Depositary Shares.*|Common Shares.*)$/i, '').trim();
const SVG = 'http://www.w3.org/2000/svg';
const sv = (tag, attrs = {}) => { const e = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

// 셋업 판정을 하는 시장. 일본·대만은 판정하지 않는다(한국·미국만 만들기로 함).
const SUPPORTED = new Set(['KR', 'US']);
const DIGITS = { KR: 0, US: 2, JP: 0, TW: 2 };
const price = (v, market) => isNum(v) ? v.toLocaleString('ko-KR', { maximumFractionDigits: DIGITS[market] ?? 2, minimumFractionDigits: DIGITS[market] ?? 2 }) : '—';

const PHASES = {
  BREAKOUT: ['돌파', 'breakout', 'phaseBreakout'],
  READY: ['임박', 'ready', 'phaseReady'],
  WATCH: ['형성중', 'watch', 'phaseWatch'],
};
const PHASE_ORDER = ['BREAKOUT', 'READY', 'WATCH'];

// 근거 항목의 이름과 단위. 서버가 보내는 키만 적고, 모르는 키는 건너뛴다.
const EV = {
  priorAdvancePct: ['앞선 상승', 'pct'], priorTrendPct: ['급등 직전 추세', 'pct'],
  cupDepthPct: ['컵 깊이', 'pct'], cupDays: ['컵 기간', 'days'], bottomDays: ['바닥 체류', 'days'],
  recoveryPct: ['오른쪽 회복', 'raw'], handleDepthPct: ['핸들 깊이', 'pct'],
  handleDays: ['핸들 기간', 'days'], handleVolumeRatio: ['핸들 거래량', 'x'],
  widthPct: ['베이스 폭', 'pct'], downVolumeRatio: ['하락일 거래량', 'x'],
  bottomGapPct: ['두 바닥 차이', 'pct'], prominencePct: ['봉우리 돌출', 'pct'],
  undercut: ['언더컷', 'bool'], firstBottom: ['첫 바닥', 'date'], secondBottom: ['두 번째 바닥', 'date'],
  poleGainPct: ['깃대 상승', 'pct'], poleDays: ['깃대 기간', 'days'],
  flagDropPct: ['깃발 하락', 'pct'], flagDays: ['깃발 기간', 'days'], flagVolumeRatio: ['깃발 거래량', 'x'],
  volumeVsMaxDownDay: ['하락일 최대 대비', 'x'], distanceToMaPct: ['이동평균 거리', 'pct'],
  reboundPct: ['최근 반등', 'pct'],
  boxWidthPct: ['박스 폭', 'pct'], boxDays: ['박스 기간', 'days'],
  boxTopDate: ['윗변', 'date'], boxBottomDate: ['아랫변', 'date'], boxVolumeRatio: ['박스 거래량', 'x'],
  high52Ratio: ['52주 고가 대비', 'raw'],
  touchesTop: ['윗변 접점', 'count'], touchesBottom: ['아랫변 접점', 'count'],
  cupLevelPct: ['컵 안 높이', 'raw'], pauseDays: ['멈춘 기간', 'days'],
  pauseWidthPct: ['멈춘 구간 폭', 'pct'], pauseVolumeRatio: ['멈춘 구간 거래량', 'x'],
  contractions: ['수축 횟수', 'count'], volumeDryRatio: ['거래량 건조', 'x'], range10Pct: ['10일 폭', 'raw'],
  volumeRatio: ['거래량', 'x'], breakoutVolumeRatio: ['당일 거래량', 'x'],
};
const EV_ORDER = Object.keys(EV);

function evidenceText(key, value) {
  const [, kind] = EV[key];
  if (kind === 'bool') return value ? '있음' : null;
  if (kind === 'date') return md(value);
  if (!isNum(value)) return null;
  if (kind === 'pct') return pct(value);
  if (kind === 'raw') return `${num(value)}%`;
  if (kind === 'days') return `${value}일`;
  if (kind === 'count') return `${value}개`;
  if (kind === 'x') return `${num(value, 2)}×`;
  if (kind === 'ratio') return num(value, 2);
  return num(value);
}

let phase = 'all', pick = 'all', onlyStage2 = true, onlyTightStop = false, onlyLeadingIndustry = false, limit = 30;
// 필터를 누르면 화면을 다시 그리므로, 설명 상자를 펼쳐 둔 상태는 따로 기억한다.
let guideOpen = false;

// 정렬. 카드에 보이는 대표 셋업(조건을 통과한 첫 셋업)과 종목 값으로 줄 세운다.
// 값이 없는 종목(예: 피벗이 없는 포켓 피벗)은 맨 뒤로 보내고, 같은 값은 기본 순서를 따른다.
const SORTS = {
  default: ['기본 · 단계순', '돌파 → 임박 → 형성중, 같은 단계에서는 피벗에 가까운 순'],
  pivot: ['피벗 가까운 순', '현재가와 피벗의 거리가 작은 순', r => absOrNull(r.setups[0].distanceToPivotPct), 'asc'],
  stop: ['손절 폭 작은 순', '피벗에서 손절선까지 거리가 작은 순 · 틀렸을 때 손실이 작은 자리부터', r => r.setups[0].stopDistancePct, 'asc'],
  importance: ['셋업 중요도 순', '셋업북 별점(★)이 높은 셋업부터', (r, meta) => meta[r.setups[0].key]?.importance, 'desc'],
  rs: ['RS 높은 순', '같은 시장 안에서 상대 강도가 높은 순', r => r.stock.rs, 'desc'],
  volume: ['거래량 많은 순', '당일 거래량 ÷ 50일 평균이 큰 순', r => r.stock.volumeRatio, 'desc'],
  change: ['당일 상승률 순', '당일 등락률이 높은 순', r => r.stock.changePct, 'desc'],
  cap: ['시총 큰 순', '시가총액이 큰 순(시장 통화 그대로 비교)', r => r.stock.marketCap, 'desc'],
};
const absOrNull = v => isNum(v) ? Math.abs(v) : null;
const SORT_STORE = 'wamo-pattern-sort';
let sortKey = 'default';
const SCOPE_STORE = 'wamo-pattern-scope';
let scope = {};   // { KR: { cap: 1e12, sector: '반도체' }, US: { … } }
try { const saved = JSON.parse(localStorage.getItem(SCOPE_STORE) || '{}'); if (saved && typeof saved === 'object') scope = saved; } catch {}
const scopeOf = market => scope[market] || (scope[market] = { cap: 0, sector: 'all' });
function saveScope() { try { localStorage.setItem(SCOPE_STORE, JSON.stringify(scope)); } catch {} }
try { const saved = localStorage.getItem(SORT_STORE); if (SORTS[saved]) sortKey = saved; } catch {}

// 셋업북 ①-6: 같은 업종 대표 종목이 함께 강하면 성공 확률이 올라간다. 산업 동반 강세는
// 이미 레이더가 판정해 두므로(industries[].confirmed) 그 이름만 모아 쓴다.
function leadingIndustries(block) {
  return new Set((block.industries || []).filter(g => g.confirmed).map(g => g.name));
}
const inLeadingIndustry = (stock, leading) =>
  [stock.industry, ...(stock.alsoIndustries || [])].some(name => leading.has(name));

export function patternsSummary(block) {
  const s = block?.patternSummary;
  if (!s || !s.withSetup) return '0';
  const by = s.byStage || {};
  return `${s.withSetup} · 돌파 ${by.BREAKOUT || 0}`;
}

/** 지금 화면 조건을 통과한 종목. 종목마다 통과한 셋업 목록을 함께 돌려준다. */
function selectRows(block, query, leading, meta = {}, market) {
  const { cap, sector } = scopeOf(market);
  const text = String(query || '').trim().toLowerCase();
  const rows = [];
  for (const stock of block.stocks || []) {
    const found = stock.patterns;
    if (!found || !found.setups?.length) continue;
    if (onlyStage2 && stock.stage2 !== true) continue;
    if (onlyLeadingIndustry && !inLeadingIndustry(stock, leading)) continue;
    // 시총을 모르는 종목은 하한을 걸면 뺀다(넘는지 알 수 없다).
    if (cap > 0 && !(isNum(stock.marketCap) && stock.marketCap >= cap)) continue;
    if (sector !== 'all' && stock.sector !== sector) continue;
    if (text && ![stock.name, stock.symbol, stock.ticker, stock.industry]
      .some(v => String(v || '').toLowerCase().includes(text))) continue;
    const setups = found.setups.filter(s =>
      (phase === 'all' || s.status === phase)
      && (pick === 'all' || s.key === pick)
      && (!onlyTightStop || !s.stopTooWide));
    if (setups.length) rows.push({ stock, found, setups });
  }
  const rank = r => PHASE_ORDER.length - PHASE_ORDER.indexOf(r.setups[0].status);
  const base = (a, b) => rank(b) - rank(a)
    || Math.abs(a.setups[0].distanceToPivotPct ?? 99) - Math.abs(b.setups[0].distanceToPivotPct ?? 99);
  const [, , value, dir] = SORTS[sortKey] || SORTS.default;
  if (!value) return rows.sort(base);
  const read = r => { const v = value(r, meta); return isNum(v) ? v : null; };
  return rows.sort((a, b) => {
    const x = read(a), y = read(b);
    if (x === null || y === null) return (x === null) - (y === null) || base(a, b);
    return (dir === 'asc' ? x - y : y - x) || base(a, b);
  });
}

function chip(label, active, onClick, title) {
  const b = n('button', null, `pt-chip${active ? ' active' : ''}`); b.type = 'button';
  b.append(label); if (title) b.title = title;
  b.setAttribute('aria-pressed', String(active));
  b.onclick = onClick;
  return b;
}

function filters(block, meta, redraw, market) {
  const box = n('div', null, 'pt-filters');
  const summary = block.patternSummary || {};
  const stages = n('div', null, 'pt-chiprow');
  stages.append(n('span', '단계', 'pt-chiplabel'));
  stages.append(chip('전체', phase === 'all', () => { phase = 'all'; limit = 30; redraw(); }));
  for (const key of PHASE_ORDER) {
    const [label, , termKey] = PHASES[key];
    const count = (summary.byStage || {})[key] || 0;
    const b = chip(`${label} ${count}`, phase === key, () => { phase = key; limit = 30; redraw(); },
      PATTERN_TERMS[termKey][1]);
    b.classList.add(`phase-${PHASES[key][1]}`);
    stages.append(b);
  }
  box.append(stages);

  const picks = n('div', null, 'pt-chiprow');
  picks.append(n('span', '셋업', 'pt-chiplabel'));
  picks.append(chip('전체', pick === 'all', () => { pick = 'all'; limit = 30; redraw(); }));
  const bySetup = summary.bySetup || {};
  // 셋업북 중요도가 높은 것부터. 오늘 한 건도 없는 셋업은 칩을 만들지 않는다.
  const keys = Object.keys(bySetup).sort((a, b) =>
    (meta[b]?.importance || 0) - (meta[a]?.importance || 0) || bySetup[b] - bySetup[a]);
  for (const key of keys) {
    picks.append(chip(`${meta[key]?.name || key} ${bySetup[key]}`, pick === key,
      () => { pick = key; limit = 30; redraw(); }, meta[key]?.criterion));
  }
  box.append(picks);

  const toggles = n('div', null, 'pt-chiprow');
  toggles.append(n('span', '조건', 'pt-chiplabel'));
  toggles.append(chip('2단계 종목만', onlyStage2, () => { onlyStage2 = !onlyStage2; limit = 30; redraw(); },
    '셋업북 ①-2: 주가가 150·200일선 위이고 200일선이 위로 기울어진 종목만 봅니다. 하락 추세 종목은 좋은 패턴도 대부분 실패합니다.'));
  toggles.append(chip('손절 8% 이내만', onlyTightStop, () => { onlyTightStop = !onlyTightStop; limit = 30; redraw(); },
    '셋업북 ②-4: 피벗에서 손절선까지 7~8%를 넘는 자리는 사지 않습니다.'));
  const leading = leadingIndustries(block);
  const leadCount = (block.stocks || []).filter(s => s.patterns?.setups?.length && inLeadingIndustry(s, leading)).length;
  toggles.append(chip(`동반 강세 업종만 ${leadCount}`, onlyLeadingIndustry,
    () => { onlyLeadingIndustry = !onlyLeadingIndustry; limit = 30; redraw(); },
    '셋업북 ①-6: 같은 업종 대표 종목 여러 개가 함께 신고가 근처에 있으면 성공 확률이 올라갑니다. 레이더가 산업 동반 강세로 판정한 업종의 종목만 봅니다.'));
  box.append(toggles);

  const current = scopeOf(market);
  const range = n('div', null, 'pt-chiprow pt-scope');
  range.append(n('span', '범위', 'pt-chiplabel'));
  const capSelect = n('select', null, 'pt-select');
  capSelect.setAttribute('aria-label', '시총 하한');
  const floors = CAP_FLOORS[market] || [[0, '전체']];
  if (!floors.some(([v]) => v === current.cap)) current.cap = 0;
  for (const [value, label] of floors) {
    const option = n('option', value ? `시총 ${label}` : '시총 전체'); option.value = String(value);
    option.selected = value === current.cap; capSelect.append(option);
  }
  capSelect.onchange = () => { current.cap = Number(capSelect.value) || 0; limit = 30; saveScope(); redraw(); };
  // 섹터는 셋업이 있는 종목에서만 모은다. 종목 수가 많은 섹터부터.
  const counts = new Map();
  for (const stock of block.stocks || []) {
    if (stock.patterns?.setups?.length && stock.sector) counts.set(stock.sector, (counts.get(stock.sector) || 0) + 1);
  }
  if (current.sector !== 'all' && !counts.has(current.sector)) current.sector = 'all';
  const sectorSelect = n('select', null, 'pt-select');
  sectorSelect.setAttribute('aria-label', '섹터');
  const all = n('option', '섹터 전체'); all.value = 'all'; sectorSelect.append(all);
  for (const [name, count] of [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ko'))) {
    const option = n('option', `${name} ${count}`); option.value = name;
    option.selected = name === current.sector; sectorSelect.append(option);
  }
  sectorSelect.onchange = () => { current.sector = sectorSelect.value; limit = 30; saveScope(); redraw(); };
  range.append(capSelect, sectorSelect);
  box.append(range);

  const order = n('label', null, 'pt-chiprow pt-sort');
  order.append(n('span', '정렬', 'pt-chiplabel'));
  const select = n('select', null, 'pt-select');
  select.setAttribute('aria-label', '정렬');
  for (const [key, [label, hint]] of Object.entries(SORTS)) {
    const option = n('option', label); option.value = key; option.title = hint;
    option.selected = key === sortKey; select.append(option);
  }
  select.onchange = () => {
    sortKey = SORTS[select.value] ? select.value : 'default'; limit = 30;
    try { localStorage.setItem(SORT_STORE, sortKey); } catch {}
    redraw();
  };
  order.append(select, n('small', SORTS[sortKey][1], 'pt-sort-hint'));
  box.append(order);
  return box;
}

/** 시장 판단 띠. 셋업북 ①-1은 시장 방향을 셋업보다 먼저 보라고 적었다. */
const GATE = {
  GREEN: ['공격 가능', 'green',
    '돌파를 따라갈 수 있는 구간입니다. 그래도 셋업북 ②-3대로 피벗 위 5% 안에서만 삽니다.'],
  YELLOW: ['조심', 'yellow',
    '돌파가 나와도 실패가 늘어나는 구간입니다. 비중을 줄이고 손절선을 더 엄격하게 지킵니다.'],
  RED: ['현금 비중 확대', 'red',
    '셋업북 ①-1: 지수가 하락 추세이면 좋은 패턴도 대부분 실패합니다. 지금은 새로 사는 자리가 아니라 관찰 목록을 만드는 구간입니다.'],
  UNKNOWN: ['판단 보류', 'unknown',
    '시장 판단 자료가 아직 들어오지 않았습니다. 셋업만 보고 판단하지 않습니다.'],
};

function marketGate(block, trend, expected, failed, openTrend) {
  const level = (!failed && trend?.weather?.level) || 'UNKNOWN';
  const [label, cls, advice] = GATE[level] || GATE.UNKNOWN;
  const box = n('section', null, `pt-gate level-${cls}`);
  const head = n('div', null, 'pt-gate-head');
  const title = n('span', null, 'pt-gate-title');
  title.append(term('marketGate', '시장 판단'));
  head.append(title, n('strong', label, 'pt-gate-level'));
  if (openTrend) {
    const go = n('button', '시장 판단 자세히', 'pt-gate-go'); go.type = 'button';
    go.onclick = openTrend; head.append(go);
  }
  box.append(head, n('p', advice, 'pt-gate-advice'));

  const facts = n('div', null, 'pt-gate-facts');
  // 셋업북 ①-1은 FTD 뒤에 비중을 늘리라고 적었다. 지수별 단계를 그대로 보여 준다.
  for (const index of (trend?.ftd?.indexes || [])) {
    if (!freshIndex(index, expected)) continue;
    const item = n('span', null, 'pt-gate-fact');
    item.append(n('b', index.label || '지수'), n('i', FTD_PHASES[index.phase] || index.phase));
    facts.append(item);
  }
  const graded = (block.stocks || []).filter(s => typeof s.aboveMa50 === 'boolean');
  if (graded.length) {
    const above = graded.filter(s => s.aboveMa50).length;
    const item = n('span', null, 'pt-gate-fact');
    item.append(n('b', '50일선 위'), n('i', `${Math.round(above / graded.length * 100)}% (${above}/${graded.length})`));
    facts.append(item);
  }
  if (facts.childElementCount) box.append(facts);
  return box;
}

/** 60거래일 종가에 피벗선과 손절선을 겹쳐 그린 작은 그림. */
function levelChart(stock, setup) {
  const data = (stock.sparkline || []).filter(isNum);
  if (data.length < 5) return null;
  const W = 260, H = 70, pad = 4;
  const values = [...data, setup.pivot, setup.stop].filter(isNum);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (!(hi > lo)) { hi = lo + 1; }
  const span = hi - lo;
  lo -= span * 0.06; hi += span * 0.06;
  const x = i => pad + (W - pad * 2) * (i / (data.length - 1));
  const y = v => pad + (H - pad * 2) * (1 - (v - lo) / (hi - lo));
  const svg = sv('svg', { class: 'pt-chart', viewBox: `0 0 ${W} ${H}`, role: 'img', preserveAspectRatio: 'none' });
  svg.setAttribute('aria-label', `최근 ${data.length}거래일 종가와 피벗·손절선`);
  for (const [value, cls] of [[setup.pivot, 'pt-pivot'], [setup.stop, 'pt-stop']]) {
    if (!isNum(value) || value < lo || value > hi) continue;
    svg.append(sv('line', { x1: pad, x2: W - pad, y1: y(value).toFixed(1), y2: y(value).toFixed(1), class: cls }));
  }
  svg.append(sv('polyline', { class: 'pt-line', points: data.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ') }));
  const last = data.length - 1;
  svg.append(sv('circle', { class: 'pt-dot', cx: x(last).toFixed(1), cy: y(data[last]).toFixed(1), r: 2.6 }));
  return svg;
}

function numbers(stock, setup, market) {
  const list = n('dl', null, 'pt-numbers');
  const add = (label, value, cls) => { const dt = n('dt'); dt.append(label); list.append(dt, n('dd', value, cls)); };
  add('현재가', price(stock.close, market));
  if (isNum(setup.pivot)) {
    add(term('pivot', '피벗'), price(setup.pivot, market));
    const d = setup.distanceToPivotPct;
    add('피벗까지', isNum(d) ? (d <= 0 ? `돌파 ${num(-d)}%` : `${num(d)}%`) : '—', isNum(d) && d <= 0 ? 'up' : null);
  }
  if (isNum(setup.stop)) {
    add(term('stopLine', '손절선'), price(setup.stop, market));
    add(term('stopWidth', '손절 폭'), isNum(setup.stopDistancePct) ? `${num(setup.stopDistancePct)}%` : '—',
      setup.stopTooWide ? 'bad' : null);
  }
  return list;
}

function evidence(setup) {
  const box = n('div', null, 'pt-evidence');
  for (const key of EV_ORDER) {
    if (!(key in (setup.evidence || {}))) continue;
    const text = evidenceText(key, setup.evidence[key]);
    if (text == null) continue;
    const item = n('span', null, 'pt-ev');
    item.append(n('b', EV[key][0]), n('i', text));
    box.append(item);
  }
  const checks = setup.evidence?.checks;
  if (checks && Object.values(checks).some(v => !v)) {
    const miss = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
    box.append(n('span', `미충족: ${miss.join(' · ')}`, 'pt-ev miss'));
  }
  return box;
}

// 패턴 이름을 누르면 뜨는 풀이. 용어 팝업(help.js)에 셋업마다 한 번만 등록한다.
// addTerms 는 같은 키를 다시 넣으면 경고를 남기므로, 화면을 다시 그릴 때마다 넣지 않는다.
const registered = new Set();
const setupTerm = key => `setup_${key}`;
function registerSetups(meta) {
  const fresh = {};
  for (const [key, info] of Object.entries(meta)) {
    if (registered.has(key)) continue;
    const stars = info.importance ? ` · ${'★'.repeat(info.importance)}` : '';
    fresh[setupTerm(key)] = [`${info.name || key} · ${info.master || ''}${stars}`, info.logic || '',
      [['어떤 모양인가', info.description], ['판정 기준', info.criterion], ['한계', info.caveat]]];
    registered.add(key);
  }
  if (Object.keys(fresh).length) addTerms(fresh);
}
/** 패턴 이름. 설명이 있으면 눌러서 풀이를 연다. */
function setupName(key, meta) {
  const name = meta[key]?.name || key;
  return meta[key]?.description ? helpButton(name, setupTerm(key)) : document.createTextNode(name);
}

const LINKS = [
  ['naverUrl', '네이버', /^https:\/\/m\.stock\.naver\.com\/(domestic|worldstock)\/stock\/[A-Za-z0-9.]+\/total$/],
  ['tradingviewUrl', '트레이딩뷰', /^https:\/\/www\.tradingview\.com\/chart\/\?symbol=[A-Z]+%3A[A-Z0-9.]+$/],
];
function links(stock) {
  const box = n('div', null, 'pt-links');
  for (const [key, label, shape] of LINKS) {
    const url = stock[key];
    if (typeof url !== 'string' || !shape.test(url)) continue;
    const a = n('a', `${label} ↗`, 'pt-link');
    a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.setAttribute('aria-label', `${short(stock.name)} ${label} 새 탭`);
    box.append(a);
  }
  return box.childElementCount ? box : null;
}

/** 셋업 10개 설명. 셋업북의 한 줄 논리와 모양 설명을 중요도 순으로 펼쳐 본다. */
function guide(meta) {
  const keys = Object.keys(meta).sort((a, b) => (meta[b].importance || 0) - (meta[a].importance || 0));
  if (!keys.length) return null;
  const box = n('details', null, 'pt-guide');
  box.open = guideOpen;
  box.addEventListener('toggle', () => { guideOpen = box.open; });
  box.append(n('summary', `패턴 ${keys.length}개 설명 보기`));
  const list = n('div', null, 'pt-guide-list');
  for (const key of keys) {
    const info = meta[key];
    const item = n('section', null, 'pt-guide-item');
    const head = n('div', null, 'pt-guide-head');
    const heading = n('h4'); heading.append(setupName(key, meta));
    head.append(heading, n('small', `${info.master || ''}${info.importance ? ` · ${'★'.repeat(info.importance)}` : ''}`));
    item.append(head);
    if (info.logic) item.append(n('p', info.logic, 'pt-logic'));
    if (info.description) item.append(n('p', info.description, 'pt-guide-text'));
    list.append(item);
  }
  box.append(list);
  return box;
}

function card(row, market, meta, openStock, leading) {
  const { stock, found, setups } = row;
  const best = setups[0];
  const info = meta[best.key] || {};
  const [phaseLabel, phaseCls] = PHASES[best.status] || ['—', 'watch'];
  const box = n('article', null, `pt-card phase-${phaseCls}`);
  box.dataset.id = stock.id;

  const head = n('div', null, 'pt-head');
  const name = n('button', null, 'pt-name'); name.type = 'button';
  name.onclick = () => openStock(stock.id);
  const where = [stock.sector, stock.industry].filter((v, i, a) => v && a.indexOf(v) === i);
  name.append(n('strong', short(stock.name)), n('small', [stock.symbol, ...(where.length ? where : ['산업 미분류'])].join(' · ')));
  head.append(name, n('span', phaseLabel, `pt-phase phase-${phaseCls}`));
  box.append(head);
  const outbound = links(stock);
  if (outbound) box.append(outbound);

  const title = n('div', null, 'pt-setup');
  const heading = n('h4'); heading.append(setupName(best.key, meta));
  title.append(heading, n('small', `${info.master || ''}${info.importance ? ` · ${'★'.repeat(info.importance)}` : ''}`));
  if (best.label && best.label !== info.name) title.append(n('span', best.label, 'pt-label'));
  if (inLeadingIndustry(stock, leading)) {
    const badge = n('span', null, 'pt-lead');
    badge.append(term('industryStrength', '업종 동반 강세'));
    title.append(badge);
  }
  box.append(title);
  if (info.logic) box.append(n('p', info.logic, 'pt-logic'));

  const body = n('div', null, 'pt-body');
  const chart = levelChart(stock, best);
  if (chart) {
    const wrap = n('div', null, 'pt-chartbox');
    wrap.append(chart);
    const key = n('div', null, 'pt-key');
    if (isNum(best.pivot)) key.append(n('span', '피벗', 'k-pivot'));
    if (isNum(best.stop)) key.append(n('span', '손절선', 'k-stop'));
    key.append(n('span', `최근 ${(stock.sparkline || []).length}거래일`, 'k-note'));
    wrap.append(key);
    body.append(wrap);
  }
  body.append(numbers(stock, best, market));
  box.append(body);
  box.append(evidence(best));

  if (setups.length > 1) {
    const more = n('div', null, 'pt-more');
    more.append(n('span', '같이 걸린 셋업', 'pt-chiplabel'));
    for (const setup of setups.slice(1)) {
      const [label, cls] = PHASES[setup.status] || ['—', 'watch'];
      const tag = n('span', null, `pt-tag phase-${cls}`);
      const tagName = n('b'); tagName.append(setupName(setup.key, meta));
      tag.append(tagName, n('i', label));
      more.append(tag);
    }
    box.append(more);
  }

  const why = n('details', null, 'pt-why');
  why.append(n('summary', '패턴 설명 · 판정 기준'));
  const dl = n('dl');
  if (info.description) dl.append(n('dt', '패턴 설명'), n('dd', info.description));
  dl.append(n('dt', '판정 기준'), n('dd', info.criterion || '—'));
  dl.append(n('dt', '한계'), n('dd', info.caveat || '—'));
  if (found.stage && !found.stage.pass) {
    dl.append(n('dt', '2단계 미충족'), n('dd', `${found.stage.passed}/${found.stage.total} 통과 · 빠진 조건: ${(found.stage.failed || []).join(' · ')}`));
  }
  why.append(dl);
  box.append(why);
  return box;
}

export function renderPatterns(root, market, block, meta, options = {}) {
  const { openStock, query, trend, expectedSession, trendFailed, openTrend } = options;
  root.replaceChildren();
  meta = meta || {};
  // 지원하지 않는 시장이라는 사실이 자료 대기보다 먼저다(일본 자료가 비어 있는 날에도 같은 안내).
  if (!SUPPORTED.has(market) || (block?.stocks?.length && !block.patternSummary)) {
    root.append(n('div', `${market} 시장은 아직 셋업 판정을 제공하지 않습니다. 한국과 미국만 지원합니다.`, 'empty'));
    return;
  }
  if (!block || !block.stocks?.length) {
    root.append(n('div', '가격 자료가 아직 없어 셋업을 판정할 수 없습니다.', 'empty'));
    return;
  }
  const summary = block.patternSummary;
  registerSetups(meta);
  const redraw = () => renderPatterns(root, market, block, meta, options);
  const leading = leadingIndustries(block);

  // 셋업북 ①-1: 시장 방향이 먼저다. 셋업 목록보다 위에 둔다.
  root.append(marketGate(block, trend, expectedSession, trendFailed, openTrend));

  const top = n('section', null, 'pt-top');
  const counts = summary.byStage || {};
  for (const key of PHASE_ORDER) {
    const [label, cls, termKey] = PHASES[key];
    const cell = n('div', null, `pt-stat phase-${cls}`);
    const head = n('span', null, 'pt-stat-label'); head.append(term(termKey, label)); cell.append(head);
    cell.append(n('strong', String(counts[key] || 0)), n('small', '종목'));
    top.append(cell);
  }
  const share = n('div', null, 'pt-stat');
  share.append(n('span', '셋업 보유', 'pt-stat-label'), n('strong', String(summary.withSetup || 0)),
    n('small', `스크리닝 ${block.stocks.length.toLocaleString()}종목 중`));
  top.append(share);
  root.append(top);

  // 셋업북 ①-1: 시장이 약하면 좋은 패턴도 대부분 실패한다. 수가 적은 것 자체가 답이다.
  if ((summary.withSetup || 0) * 20 < block.stocks.length) {
    root.append(n('p', '지금 이 시장에는 촘촘한 베이스가 거의 없습니다. 셋업북은 시장이 하락 추세일 때 좋은 패턴도 대부분 실패한다고 적었습니다. 건수가 적은 것 자체가 판정 결과입니다.', 'pt-weak'));
  }

  root.append(filters(block, meta, redraw, market));
  const help = guide(meta);
  if (help) root.append(help);

  const rows = selectRows(block, query, leading, meta, market);
  const info = n('div', null, 'pt-result');
  info.append(n('span', `${rows.length}종목 · 현재 조건 기준`));
  root.append(info);

  if (!rows.length) {
    root.append(n('div', '현재 조건에 맞는 종목이 없습니다. 위의 단계·셋업·조건 칩을 넓혀 보세요.', 'empty'));
  } else {
    const list = n('div', null, 'pt-list');
    for (const row of rows.slice(0, limit)) list.append(card(row, market, meta, openStock, leading));
    root.append(list);
    if (rows.length > limit) {
      const more = n('button', `${rows.length - limit}종목 더 보기`, 'pt-loadmore');
      more.type = 'button';
      more.onclick = () => { limit += 30; redraw(); };
      root.append(more);
    }
  }
  root.append(glossary(PATTERN_TERMS, Object.keys(PATTERN_TERMS)));
}
