/* Render operational status separately from the mathematical QA badge. */
(() => {
  'use strict';
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
    line('정규예약(KST) · 한국 12:00 / 16:00 · 미국 00:00 / 06:20 · 시장별 거래일 2회', '#a8cbef');
    const markets = isMovers ? ['KR', 'US'] : [market];
    for (const m of markets) {
      const s = status?.[m] || {};
      const p = isMovers ? {} : meta;
      const title = m === 'KR' ? '한국' : '미국';
      const asOf = s.asOf || p.asOf || '확인 불가';
      const late = s.nextScheduledFor && Date.now() > Date.parse(s.nextScheduledFor) + 45 * 60000;
      let label = s.status === 'FAILED' ? '갱신 실패 · 이전 데이터 유지'
        : late ? '예약 이후 갱신 확인 필요'
        : s.status === 'WARNING' ? '가격 갱신 완료 · 일부 항목 확인 필요'
        : s.status === 'PASS' ? '가격 갱신 완료' : '실행 상태 기록 없음';
      if (isMovers && s.moversStatus === 'FAILED') label = 'TOP 30 갱신 실패 · 이전 결과 유지';
      line(`${title} · ${label} · 가격 기준일 ${asOf}`, s.status === 'FAILED' || late ? '#ffb3a9' : '#f4d58a');
      line(`예정 ${stamp(s.scheduledFor || p.scheduledFor)} · 실제 시작 ${stamp(s.attemptedAt || p.runStartedAt)} · 완료 ${stamp(s.lastSuccessAt || p.updatedAt)} · ${s.trigger || p.runTrigger || '이전 실행'}`);
      if (s.nextScheduledFor) line(`다음 예약 ${stamp(s.nextScheduledFor)} · GitHub 예약 지연 시 누락 확인 후 재시도`);
      const warnings = s.warnings || p.freshness?.warnings || [];
      warnings.forEach(w => line(w, '#f4d58a'));
      if (s.error) line(s.error, '#ffb3a9');
      const kis = s.kis || p.kisMeta;
      if (m === 'KR' && kis) line(kis.message || `한국투자 API: ${kis.status}`);
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
  render(null);
  fetch('wamo_refresh_status.json', {cache:'no-store'})
    .then(r => { if (!r.ok) throw Error('status'); return r.json(); })
    .then(render).catch(() => line('실행 상태를 불러오지 못했습니다. 표시된 가격 기준일을 확인하세요.', '#f4d58a'));
})();
