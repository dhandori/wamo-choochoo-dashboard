/* Render operational status separately from the mathematical QA badge. */
(() => {
  'use strict';
  const discovery = document.createElement('script');
  discovery.src = 'wamo_discovery.js';
  discovery.defer = true;
  document.head.append(discovery);
  const data = window.WAMO_DATA;
  const meta = data?.meta || {};
  const isMovers = !data;
  const market = meta.market === 'US' || /\/us\.html$/.test(location.pathname) ? 'US' : 'KR';
  const stamp = value => {
    if (!value) return '기록 없음';
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return '기록 없음';
    return new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul', year:'numeric', month:'2-digit',
      day:'2-digit', hour:'2-digit', minute:'2-digit'}).format(d) + ' KST';
  };
  const box = document.createElement('section');
  box.id = 'wamo-live-status';
  box.style.cssText = 'margin:12px 0;padding:14px 16px;border:1px solid #37516c;border-radius:12px;background:#101e30;color:#dbe8f5;font:13px/1.7 system-ui;overflow-wrap:anywhere';
  const anchor = document.querySelector('.market-nav') || document.querySelector('nav') || document.querySelector('header');
  if (anchor) anchor.after(box); else document.body.prepend(box);
  document.querySelectorAll('a[href="kkangto.html"]').forEach(a => a.remove());
  const line = (text, color) => {
    const el = document.createElement('div');
    el.textContent = text;
    if (color) el.style.color = color;
    box.append(el);
  };
  const render = status => {
    box.replaceChildren();
    line('갱신예약(KST) · 한국 09:30 / 10:30 / 11:30 / 12:00 / 13:00 / 14:00 / 15:00 / 16:00 · 미국 22:40 / 23:40 / 01:00 / 02:00 / 03:00 / 04:00 / 05:00 / 06:20', '#a8cbef');
    const markets = isMovers ? ['KR', 'US'] : [market];
    for (const m of markets) {
      const s = status?.[m] || {};
      const p = isMovers ? {} : meta;
      const title = m === 'KR' ? '한국' : '미국';
      const asOf = s.asOf || p.asOf || '확인 불가';
      const late = s.nextScheduledFor && Date.now() > Date.parse(s.nextScheduledFor) + 5 * 60000;
      let label = s.status === 'FAILED' ? '갱신 실패 · 이전 데이터 유지'
        : late ? '예약시각 경과 · 새 결과 대기'
        : s.status === 'WARNING' ? '가격 갱신 완료 · 일부 항목 확인 필요'
        : s.status === 'PASS' ? '가격 갱신 완료' : '실행 상태 기록 없음';
      if (isMovers && s.moversStatus === 'DEFERRED') label = 'TOP 30 기존 결과 · 전체 갱신 회차에 업데이트';
      if (isMovers && s.moversStatus === 'RUNNING') label = 'TOP 30 갱신 중 · 이전 결과 표시';
      if (isMovers && s.moversStatus === 'FAILED') label = 'TOP 30 갱신 실패 · 이전 결과 유지';
      line(`${title} · ${label} · 가격 기준일 ${asOf}`, s.status === 'FAILED' || late ? '#ffb3a9' : '#f4d58a');
      line(`예정 ${stamp(s.scheduledFor || p.scheduledFor)} · 실제 시작 ${stamp(s.attemptedAt || p.runStartedAt)} · 완료 ${stamp(s.lastSuccessAt || p.updatedAt)} · ${s.trigger || p.runTrigger || '이전 실행'}`);
      if (s.refreshMode === 'PRICE') line('장중 보강 · 가격·거래량·추세 재계산 / 기업정보·공시는 기존 자료 / TOP 30은 전체 갱신 시 갱신');
      if (!isMovers && s.moversStatus === 'RUNNING') line('가격 계산 완료 · TOP 30은 별도로 갱신 중');
      if (isMovers && s.moversCompletedAt) line(`TOP 30 완료 ${stamp(s.moversCompletedAt)}`);
      if (s.nextScheduledFor) line(`다음 예약 ${stamp(s.nextScheduledFor)} · GitHub 예약 지연 시 누락 확인 후 재시도`);
      const warnings = s.warnings || p.freshness?.warnings || [];
      warnings.forEach(w => line(w, '#f4d58a'));
      if (s.error) line(s.error, '#ffb3a9');
      const kis = s.kis || p.kisMeta;
      if (kis) line(kis.message || `한국투자 API: ${kis.status}`);
      if (m === 'KR' && p.marketEnergy?.membershipCounts) {
        const counts = p.marketEnergy.membershipCounts;
        line(`시장 에너지 · KRX 실제 구성 ${counts.KOSPI200}+${counts.KOSDAQ150}개 · ${p.marketEnergy.membershipCheck || '공식 구성 확인'} · 350종목 환산값 별도 표시`);
      }
      if (s.runUrl && /^https:\/\/github\.com\/dhandori\/wamo-choochoo-dashboard\/actions\/runs\/\d+$/.test(s.runUrl)) {
        const a = document.createElement('a');
        a.href = s.runUrl; a.textContent = `${title} 실행 기록 보기`; a.style.color = '#98c7ff';
        a.target = '_blank'; a.rel = 'noopener'; box.append(a);
      }
    }
    const qa = document.getElementById('qualitySummary');
    if (qa && !isMovers) qa.textContent = `표시 정합성 ${meta.qa?.status === 'PASS' ? '통과' : '확인 필요'} · 검사시각 ${stamp(meta.qa?.checkedAt)} · 가격 최신성은 위 실행 상태 참고`;
  };
  // Render authenticated market data from the generated payload, never credentials.
  if (!isMovers) {
    const unit = market === 'US' ? '달러' : '원';
    const priceDigits = market === 'US' ? 2 : 0;
    const stocks = data.stocks || [];
    const targets = stocks.filter(s => s.kisQuote || s.kisEstimate);
    const create = (tag, text, parent) => {
      const node = document.createElement(tag);
      if (text != null) node.textContent = text;
      if (parent) parent.append(node);
      return node;
    };
    const n = (value, digits=2) => typeof value === 'number' && Number.isFinite(value)
      ? value.toLocaleString('ko-KR', {maximumFractionDigits:digits}) : '미확인';
    const state = q => ({LIVE:'신규 확인', CACHED:'이전값 · 이번 조회 실패', FAILED:'조회 실패', SKIPPED:'미조회'})[q?.status] || '미조회';
    const panel = create('details');
    panel.id = 'wamo-kis-panel';
    panel.style.cssText = 'margin:12px 0;padding:14px;border:1px solid #37516c;border-radius:12px;background:#101e30;color:#dbe8f5;font:13px/1.7 system-ui;min-width:0';
    create('summary', `한국투자 시세·밸류 확인 · ${meta.kisMeta?.quoteCount || 0}/${meta.kisMeta?.targetCount || targets.length}종목 신규 조회`, panel).style.cursor = 'pointer';
    create('p', '시장별 기존 추세·점수 상위 20종목을 보조 조회합니다. PER(주가수익비율)·EPS(주당순이익)·PBR(주가순자산비율)은 현재가 API의 값이며, 예상실적 기준이 아닙니다. 비어 있는 값은 0으로 계산하지 않습니다.', panel);
    const search = create('input', null, panel);
    search.type='search'; search.placeholder='종목명 또는 코드 검색'; search.setAttribute('aria-label','한국투자 조회 종목 검색');
    search.style.cssText='box-sizing:border-box;width:100%;max-width:360px;padding:8px;margin-bottom:8px';
    const wrap = create('div', null, panel);
    wrap.style.cssText='overflow:auto;max-height:350px';
    const table = create('table', null, wrap);
    table.style.cssText='width:100%;min-width:620px;border-collapse:collapse';
    const header = create('tr', null, create('thead', null, table));
    ['종목 / 확인시각(KST)', '조회 상태', '한투 제공 업종', `KIS 조회가(${unit})`, 'PER(배)', `EPS(${unit})`, 'PBR(배)', `52주 최고/최저(${unit})`].forEach(t=>create('th',t,header));
    const tbody = create('tbody', null, table);
    const rows = [];
    for (const stock of targets) {
      const q = stock.kisQuote || {};
      const row = create('tr', null, tbody);
      const cell = create('td', null, row);
      create('b', `${stock.name} · ${stock.stock_code || stock.ticker}`, cell);
      create('div', stamp(q.checkedAt), cell).style.fontSize='11px';
      [state(q), q.industry || '미확인', n(q.price,priceDigits), n(q.per), n(q.eps), n(q.pbr), `${n(q.high52,priceDigits)} / ${n(q.low52,priceDigits)}`].forEach(t=>create('td',t,row));
      rows.push([row,`${stock.name} ${stock.stock_code} ${stock.ticker} ${q.industry || ''}`.toLowerCase()]);
    }
    table.querySelectorAll('td,th').forEach(e=>e.style.cssText='text-align:left;padding:8px;border-bottom:1px solid #37516c;white-space:nowrap');
    const empty = create('div', targets.length ? '' : '현재 확인된 보조조회 결과가 없습니다.', panel);
    search.addEventListener('input',()=>{
      let visible=0;
      rows.forEach(([r,label])=>{r.hidden=!label.includes(search.value.trim().toLowerCase());if(!r.hidden)visible++;});
      empty.textContent=visible ? '' : '검색 결과가 없습니다.';
    });
    create('p', '기존 세부업종과 한투 제공 업종은 분류 기준이 달라 함께 표시합니다. 한투 업종명은 상세 사업설명이나 사업별 매출 구성을 뜻하지 않습니다.', panel);
    if (market === 'KR') create('p', `추정실적 응답: ${meta.kisMeta?.estimateCount || 0}종목. 항목·단위와 예상치 기준을 검증 중이며, Forward PER(예상 주당순이익 기준 주가수익비율)과 컨센서스 상향률은 아직 제공하지 않습니다. 조회일을 추정치 작성일로 간주하지 않습니다.`, panel);
    if (market === 'US') create('p', '현재가·PER·EPS·PBR·52주 가격·업종을 연결했습니다. Forward PER(예상 EPS 기준 PER)과 컨센서스는 미연결입니다.', panel);
    box.after(panel);
    // The existing detail drawer updates its ticker label whenever a stock opens.
    const detailSub = document.getElementById('detailSub');
    if (detailSub) {
      const detail = create('section');
      detail.id='wamo-kis-detail';
      detail.style.cssText='margin:12px 0;padding:12px;border:1px solid #37516c;border-radius:10px;font-size:13px;overflow-wrap:anywhere';
      document.getElementById('detailSectorFlow')?.after(detail);
      const update = () => {
        const ticker = detailSub.textContent.split(' · ')[0];
        const stock = stocks.find(s=>s.ticker === ticker);
        detail.replaceChildren();
        create('b','한국투자 시세·밸류 보조확인',detail);
        const q = stock?.kisQuote;
        if (!q) { create('div','이번 상위 20종목 보조조회 대상에 포함되지 않았습니다.',detail); return; }
        create('div',`${state(q)} · 확인시각 ${stamp(q.checkedAt)}`,detail);
        create('div',`KIS 조회가 ${n(q.price,priceDigits)}${unit} · PER ${n(q.per)}배 · EPS ${n(q.eps)}${unit} · PBR ${n(q.pbr)}배`,detail);
        create('div',`한투 제공 업종: ${q.industry || '미확인'} · 기존 세부업종: ${stock.sector || '미확인'}`,detail);
        create('div',`한투 52주 최고 ${n(q.high52,priceDigits)}${unit} / 최저 ${n(q.low52,priceDigits)}${unit}`,detail);
        create('div','현재가 API 기준 · 예상실적 지표가 아닙니다.',detail);
        const estimate = stock.kisEstimate;
        if (estimate?.periods?.length) create('div',`추정실적 응답 결산기간: ${estimate.periods.join(' / ')} · E는 제공사 예상치 표시 · 항목 검증 대기`,detail);
      };
      new MutationObserver(update).observe(detailSub,{childList:true,characterData:true,subtree:true});
    }
  }
  render(null);
  fetch('wamo_refresh_status.json', {cache:'no-store'})
    .then(r => { if (!r.ok) throw Error('status'); return r.json(); })
    .then(render).catch(() => line('실행 상태를 불러오지 못했습니다. 표시된 가격 기준일을 확인하세요.', '#f4d58a'));
})();
