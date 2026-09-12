/* Public allowlisted radar results. No browser credentials or private requests. */
(() => {
  'use strict';
  if (!window.WAMO_DATA || document.getElementById('wamo-discovery')) return;
  const el = (tag, text, parent) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (parent) parent.append(node);
    return node;
  };
  const panel = el('section'); panel.id = 'wamo-discovery';
  panel.style.cssText = 'margin:14px 0;padding:18px;border:1px solid #37516c;border-radius:14px;background:#101e30;color:#dbe8f5;font:14px/1.7 system-ui;overflow-wrap:anywhere';
  (document.getElementById('wamo-live-status') || document.body.firstElementChild).after(panel);
  el('h2', 'WAMO · 글로벌 종목 발견', panel);
  const status = el('p', '검증된 신고가 결과를 불러오는 중…', panel);
  const input = el('input', null, panel); input.type = 'search';
  input.placeholder = '종목·시장·산업 검색'; input.setAttribute('aria-label', '글로벌 후보 검색');
  input.style.cssText = 'padding:10px;width:100%;max-width:420px;box-sizing:border-box';
  const market = el('select', null, panel); market.setAttribute('aria-label', '글로벌 시장');
  for (const [v, t] of [['', '전체 시장'], ['US-', '미국'], ['KR-', '한국'], ['JP-', '일본'], ['CN-', '중국'], ['HK-', '홍콩']]) {
    const option = el('option', t, market); option.value = v;
  }
  const counter = el('p', '', panel);
  const results = el('div', null, panel); results.style.cssText = 'max-height:420px;overflow:auto';
  const industries = el('details', null, panel); el('summary', '산업 강세 확산', industries);
  const groups = el('div', null, industries);
  const label = {intraday_63:'장중 3개월',intraday_126:'장중 6개월',intraday_252:'장중 52주',intraday_ath:'장중 역사적',close_63:'종가 3개월',close_126:'종가 6개월',close_252:'종가 52주',close_ath:'종가 역사적'};
  const ticker = x => x.market === 'KR-KOSPI' ? x.symbol + '.KS' : x.market === 'KR-KOSDAQ' ? x.symbol + '.KQ' : x.symbol;
  const open = x => {
    const t = ticker(x);
    const page = x.market.startsWith('KR-') ? 'index.html' : x.market.startsWith('US-') ? 'us.html' : null;
    if (page && window.WAMO_OPEN_DETAIL?.(t)) return;
    if (page && !location.pathname.endsWith('/' + page)) {
      location.href = page + '?radar=' + encodeURIComponent(t); return;
    }
    const dialog = el('dialog', null, document.body);
    dialog.style.cssText = 'max-width:600px;width:85%;background:#101e30;color:#dbe8f5;border:1px solid #37516c;border-radius:14px;padding:24px';
    el('h3', `${x.name} · ${x.market}:${x.symbol}`, dialog);
    el('p', `신고가 기준일 ${x.as_of} · ${(x.industry_path || []).join(' › ')}`, dialog);
    el('p', '이 종목은 현재 WAMO 정밀분석 종목군에 없습니다. 신고가 신호만으로 기업의 질·적정가격·매수 적합성을 판단하지 않습니다.', dialog);
    el('p', '기업 평가: 미평가 / 가격 평가: 미평가 / 차트: 신고가 탐지, Stage 2·RS 미연결', dialog);
    const close = el('button', '닫기', dialog); close.onclick = () => dialog.close();
    dialog.onclose = () => dialog.remove(); dialog.showModal();
  };
  fetch('wamo_radar.json', {cache:'no-store'}).then(r => { if (!r.ok) throw Error(); return r.json(); }).then(feed => {
    if (feed.schemaVersion !== 1) throw Error();
    const expired = !Number.isFinite(Date.parse(feed.checkedAt)) || Date.now() - Date.parse(feed.checkedAt) > 3 * 3600000;
    const editions = Object.values(feed.editions || {});
    status.textContent = `${feed.scope} 마지막 연결 확인 ${feed.checkedAt} · ${expired ? '연결 확인 지연: 과거 결과' : editions.every(e=>e.status==='PASS') ? '기준일 검증 통과' : '일부 시장 갱신 실패: 실패 시장 후보는 숨김'}`;
    const valid = expired ? [] : editions.filter(e => e.status === 'PASS');
    const stocks = valid.flatMap(e => e.signals || []);
    const render = () => {
      results.replaceChildren(); groups.replaceChildren();
      const query = input.value.trim().toLowerCase();
      const chosen = stocks.filter(x => x.market.startsWith(market.value) && `${x.name} ${x.symbol} ${(x.industry_path || []).join(' ')}`.toLowerCase().includes(query));
      counter.textContent = `${chosen.length}개 후보 · 신고가는 매수 추천이 아닙니다. 역사적 신고가 미확인은 false가 아닌 미확인입니다.`;
      for (const x of chosen) {
        const row = el('div', null, results); row.style.cssText = 'padding:10px 0;border-bottom:1px solid #37516c';
        const button = el('button', `${x.name} · ${x.market}:${x.symbol}`, row); button.onclick = () => open(x);
        el('div', `${x.as_of} · ${Object.entries(x.flags).filter(([,v])=>v===true).map(([k])=>label[k]).join(' / ')} 신고가`, row);
        el('small', `${(x.industry_path || []).join(' › ')} · ${x.micro_verified ? '세부사업 검증' : '세부사업 미확인'}`, row);
      }
      for (const g of valid.flatMap(e=>e.industries || [])) {
        const text = `${g.market} · ${(g.industry_path || []).join(' › ')} · ${g.current_signal_issuers}/${g.eligible_classified_issuers} · ${g.phase || '미확인'}`;
        if (query && !text.toLowerCase().includes(query)) continue;
        const row = el('p', text, groups);
        el('small', ' · ' + (g.comparison_scope || '확인된 분류 종목군 기준'), row);
      }
    };
    input.oninput = render; market.onchange = render; render();
    setTimeout(() => {
      results.replaceChildren(); groups.replaceChildren(); input.disabled = true; market.disabled = true;
      counter.textContent = '연결 확인 시간이 지났습니다. 페이지를 새로고침해 최신 상태를 확인하세요.';
    }, Math.max(0, 3 * 3600000 - (Date.now() - Date.parse(feed.checkedAt))));
    const requested = new URLSearchParams(location.search).get('radar');
    if (requested) {
      const x = stocks.find(s=>ticker(s)===requested);
      if (x) open(x);
    }
  }).catch(() => { status.textContent = '글로벌 결과를 확인할 수 없습니다. 기존 WAMO 기능은 계속 사용할 수 있습니다.'; });
})();
