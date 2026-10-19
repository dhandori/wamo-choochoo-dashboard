// "갱신 일정·진행 상황": both markets at a glance, from the same status file the
// "지금 갱신" button reads (published at the start and end of every run).
import { refreshReason } from './runner.js?v=20261019-2';

const NAMES = { KR: '한국', US: '미국' };
const RULE = {
  KR: '평일 오후 4시 (장 마감 15:30 뒤)',
  US: '평일 새벽 5시 20분 · 미국 서머타임 끝나면 6시 20분',
};
const RETRY = {
  KR: '실패하면 밤 10시 41분까지 30분마다 다시 시도',
  US: '실패하면 낮 12시 41분까지 30분마다 다시 시도',
};
const n = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const ms = v => { const t = Date.parse(v || ''); return Number.isFinite(t) ? t : null; };
const kst = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const when = t => t == null ? '—' : kst.format(new Date(t)).replace(/\.\s?/g, '/').replace(/\/\s?\(/, ' (').replace(/\/$/, '');
function span(diff) {
  const m = Math.round(Math.abs(diff) / 60000);
  if (m < 1) return '방금';
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60), r = m % 60;
  if (h < 48) return r ? `${h}시간 ${r}분` : `${h}시간`;
  return `${Math.floor(h / 24)}일`;
}
const ago = (t, now) => t == null ? '' : `${span(now - t)} 전`;
const until = (t, now) => t == null ? '' : t <= now ? `${span(now - t)} 지남` : `${span(t - now)} 뒤`;

// Which step of a run is happening, from the fields wamo_run.py writes.
function runStep(s) {
  if (s.status === 'RUNNING') return [1, '가격·거래량 수집 중'];
  if (s.moversStatus === 'RUNNING') return [2, '특징주 정리 중'];
  if (s.radarStatus === 'RUNNING') return [3, '레이더·추세·FTD 반영 중'];
  return null;
}

function card(market, s, trend, now) {
  const box = n('div', null, 'sched-card');
  const head = n('div', null, 'sched-head');
  head.append(n('strong', NAMES[market]));
  const step = s ? runStep(s) : null;
  const attempted = ms(s?.attemptedAt), success = ms(s?.lastSuccessAt), next = ms(s?.nextScheduledFor);
  const stuck = step && attempted && now - attempted > 40 * 60000;
  const reason = refreshReason(market, s || undefined);
  let label, tone;
  if (!s) [label, tone] = ['상태 확인 중', 'idle'];
  else if (stuck) [label, tone] = ['수집이 멈춘 것 같음', 'bad'];
  else if (step) [label, tone] = ['갱신 진행 중', 'run'];
  else if (s.status === 'FAILED') [label, tone] = ['지난 갱신 실패', 'bad'];
  else if (reason) [label, tone] = ['갱신 지연', 'warn'];
  else if (s.status === 'WARNING') [label, tone] = ['완료 · 참고 경고 있음', 'ok'];
  else [label, tone] = ['최신 상태', 'ok'];
  head.append(n('span', label, `sched-chip tone-${tone}`));
  box.append(head);

  if (step && !stuck) {
    const prog = n('div', null, 'sched-progress');
    const steps = n('ol');
    ['가격 수집', '특징주', '레이더·추세 반영'].forEach((text, i) => steps.append(n('li', text, i + 1 < step[0] ? 'done' : i + 1 === step[0] ? 'now' : '')));
    prog.append(steps, n('p', `${step[1]} · 시작 ${when(attempted)} · ${span(now - attempted)} 경과 · 보통 10~25분 걸려요`));
    box.append(prog);
  }

  const rows = n('dl', null, 'sched-rows');
  const row = (k, v, sub) => { rows.append(n('dt', k)); const dd = n('dd'); dd.append(n('b', v)); if (sub) dd.append(n('small', ` ${sub}`)); rows.append(dd); };
  row('마지막 갱신', when(success), success ? `${ago(success, now)} · ${s?.asOf || '—'} 종가 기준` : '');
  row('다음 예약', when(next), next ? until(next, now) : '');
  if (trend?.builtAt) row('추세·FTD·선도주', when(ms(trend.builtAt)), `${ago(ms(trend.builtAt), now)} 계산`);
  box.append(rows);
  if (reason && !step) box.append(n('p', `왜 지연으로 보나요? ${reason}`, 'sched-reason'));
  if (s?.warnings?.length) box.append(n('p', `참고: ${s.warnings.join(' · ')}`, 'sched-warn'));
  const foot = n('p', `${RULE[market]} · ${RETRY[market]}`, 'sched-rule');
  if (s?.runUrl) { const a = n('a', '실행 기록 ↗'); a.href = s.runUrl; a.target = '_blank'; a.rel = 'noopener noreferrer'; foot.append(' · ', a); }
  box.append(foot);
  return box;
}

export function renderSchedule(container, status, trendBlock, current) {
  if (!container) return;
  const now = Date.now();
  container.hidden = false;
  container.replaceChildren();
  container.append(n('p', '한국시간 기준 · 1분마다 자동으로 다시 확인해요', 'sched-hint'));
  const grid = n('div', null, 'sched-grid');
  // The market on screen comes first.
  for (const m of current === 'US' ? ['US', 'KR'] : ['KR', 'US']) grid.append(card(m, status?.[m] || null, trendBlock?.(m), now));
  container.append(grid);
  container.append(n('p', 'GitHub 예약 실행은 사용량이 몰리면 10~30분쯤 늦게 시작될 수 있어요. 휴장일에는 갱신하지 않아요.', 'sched-note'));
}
