// 특징주 탭(미국) 맨 위: S&P 500 상승·하락 상위 12종목 막대 카드. wamo_sp500_movers.json만 읽는다.
let data = null, loading = null, failed = false;
const isNum = n => typeof n === 'number' && Number.isFinite(n);
const fmt = (n, d = 2) => isNum(n) ? n.toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—';
const md = d => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;
function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }

export function loadSpMovers(force = false) {
  if (loading) return loading;
  if (data && !force) return Promise.resolve(data);
  loading = fetch('wamo_sp500_movers.json', { cache: 'no-store' })
    .then(r => { if (!r.ok) throw Error('HTTP'); return r.json(); })
    .then(j => { if (j && Array.isArray(j.gainers)) { data = j; failed = false; } return data; })
    .catch(() => { failed = true; return data; })
    .finally(() => { loading = null; });
  return loading;
}

// 같은 섹터가 몇 개씩 몰렸는지: 상위 12개 중 3개 이상인 섹터만 한 줄로 알려 준다.
function crowd(rows) {
  const n = {};
  rows.forEach(r => { n[r.sector] = (n[r.sector] || 0) + 1; });
  return Object.entries(n).filter(([, c]) => c >= 3).sort((a, b) => b[1] - a[1]);
}

function column(title, rows, side, maxAbs, onPick) {
  const col = el('div', null, `spm-col spm-${side}`);
  const head = el('div', null, 'spm-col-head');
  head.append(el('h4', title));
  const c = crowd(rows);
  if (c.length) head.append(el('span', c.map(([s, k]) => `${s} ${k}개`).join(', ') + ' 몰림', `spm-crowd spm-${side}`));
  col.append(head);
  rows.forEach((r, i) => {
    const row = el(onPick ? 'button' : 'div', null, 'spm-row');
    if (onPick) { row.type = 'button'; row.onclick = () => onPick(r.ticker); }
    row.title = `${r.ticker} ${r.name} · ${r.sector}${r.subIndustry ? ' / ' + r.subIndustry : ''} · 종가 $${fmt(r.close)}`;
    const bar = el('span', null, 'spm-bar'); const fill = el('i'); fill.style.width = `${Math.max(4, Math.abs(r.changePct) / maxAbs * 100)}%`; bar.append(fill);
    const name = el('span', null, 'spm-name'); name.append(el('b', r.ticker), el('small', r.name));
    row.append(el('span', String(i + 1), 'spm-rank'), name, el('span', r.sector, 'spm-sector'),
      el('span', `$${fmt(r.close)}`, 'spm-close'), el('strong', `${r.changePct > 0 ? '+' : ''}${fmt(r.changePct)}%`, 'spm-pct'), bar);
    col.append(row);
  });
  return col;
}

export function renderSpMovers(panel, { market, tab, onPick } = {}) {
  if (!panel) return;
  panel.replaceChildren();
  panel.hidden = !(market === 'US' && tab === 'movers');
  if (panel.hidden) return;
  if (!data) {
    if (failed) { panel.hidden = true; return; }
    panel.append(el('p', 'S&P 500 상승·하락 상위 자료를 불러오는 중이에요.', 'spm-empty'));
    loadSpMovers().then(() => renderSpMovers(panel, { market, tab, onPick }));
    return;
  }
  const d = data;
  const head = el('div', null, 'spm-head');
  const cap = el('div');
  cap.append(el('h3', `S&P 500 상승·하락 상위 ${d.gainers.length}`),
    el('p', `${md(d.asOf)} 종가 기준 · 500종목 전체에서 뽑았어요 (아래 목록은 WAMO 스크리닝 종목 기준이라 다를 수 있어요)`));
  const breadth = el('div', null, 'spm-breadth');
  const total = (d.advancers || 0) + (d.decliners || 0) || 1;
  const bar = el('span', null, 'spm-adv'); const up = el('i', null, 'spm-upfill'); up.style.width = `${(d.advancers || 0) / total * 100}%`; bar.append(up);
  breadth.append(el('b', `오른 종목 ${d.advancers}`, 'spm-up'), bar, el('b', `내린 종목 ${d.decliners}`, 'spm-down'));
  head.append(cap, breadth);
  const maxAbs = Math.max(...[...d.gainers, ...d.losers].map(r => Math.abs(r.changePct)), 1);
  const grid = el('div', null, 'spm-grid');
  grid.append(column('많이 오른 종목', d.gainers, 'up', maxAbs, onPick), column('많이 내린 종목', d.losers, 'down', maxAbs, onPick));
  panel.append(head, grid, el('p', `막대 길이 = 등락률 크기(양쪽 같은 눈금) · ${d.source}`, 'spm-note'));
}
