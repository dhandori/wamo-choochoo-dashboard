// "지금 갱신": opens the existing manual GitHub Actions run, then follows it.
// No login inside WAMO. Same-origin status file every minute (free); the public
// GitHub API only while a requested run is being followed, with ETags so
// unchanged answers do not count against the 60-per-hour limit.
const PENDING_KEY = 'wamo-run-pending-v1';
const MAX_WAIT = 100 * 60000;
const NAMES = { KR: '한국', US: '미국' };

function repoSlug() {
  const host = location.hostname, seg = location.pathname.split('/').filter(Boolean)[0];
  if (host.endsWith('.github.io') && seg) return `${host.split('.')[0]}/${seg}`;
  return 'dhandori/wamo-choochoo-dashboard';
}
const REPO = repoSlug();
const WORKFLOW_URL = `https://github.com/${REPO}/actions/workflows/main.yml`;
const API = `https://api.github.com/repos/${REPO}/actions/workflows/main.yml/runs?event=workflow_dispatch&per_page=5`;

let status = null, statusAt = 0, statusTimer = null, pollTimer = null, onDone = () => {}, getMarketStatus = () => null;
let gh = { etag: null, runs: null, limited: false };
let pending = readPending();

function readPending() {
  try {
    const p = JSON.parse(localStorage.getItem(PENDING_KEY) || 'null');
    return p && Date.now() - p.at < MAX_WAIT + 15 * 60000 ? p : null;
  } catch { return null; }
}
function savePending(p) {
  pending = p;
  try { p ? localStorage.setItem(PENDING_KEY, JSON.stringify(p)) : localStorage.removeItem(PENDING_KEY); } catch {}
}
function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
const parse = v => { const t = Date.parse(v || ''); return Number.isFinite(t) ? t : null; };

/** Why a market needs a fresh collection, or null when it looks current. */
export function refreshReason(market, s = status?.[market], radarStatus = getMarketStatus(market), now = Date.now()) {
  if (!s) return radarStatus === 'STALE' ? '지난 거래일 데이터예요' : null;
  const attempted = parse(s.attemptedAt), next = parse(s.nextScheduledFor);
  if (s.status === 'RUNNING' && attempted && now - attempted > 40 * 60000) return '수집이 중간에 멈춘 것 같아요';
  if (s.status === 'RUNNING') return null;
  if (s.status === 'FAILED') return '지난 수집이 실패했어요';
  if (next && now > next + 90 * 60000) return '예약 시각이 지났는데 새 결과가 없어요';
  const f = s.freshness || {};
  const missing = Number(s.count) - Number(f.currentCount);
  if (f.expectedSession && s.asOf === f.expectedSession && missing > 0) return `${missing}종목이 빠졌어요`;
  if (radarStatus === 'STALE') return '지난 거래일 데이터예요';
  return null;
}

function runningNow(market) {
  const s = status?.[market];
  const attempted = parse(s?.attemptedAt);
  return s?.status === 'RUNNING' && attempted && Date.now() - attempted <= 40 * 60000;
}

function phaseFor(market) {
  if (pending && pending.market === market) return pending;
  if (runningNow(market)) return { phase: 'running', market };
  return null;
}

const LABEL = { waiting: '실행 확인 대기', queued: '실행 대기', running: '수집 중', applying: '완료 · 반영 중', done: '완료', failed: '실패' };

export function runnerButton(market) {
  const box = el('span', null, 'run-now'); box.dataset.market = market;
  paint(box);
  return box;
}

function paint(box) {
  const market = box.dataset.market, p = phaseFor(market), reason = refreshReason(market);
  box.replaceChildren();
  const b = el('button', null, 'run-now-button'); b.type = 'button';
  if (p && ['waiting', 'queued', 'running', 'applying'].includes(p.phase)) {
    b.disabled = true; b.classList.add('busy');
    b.append(el('span', null, 'run-spin'), document.createTextNode(LABEL[p.phase]));
    b.title = '이미 요청했어요. 끝날 때까지 다시 누를 필요가 없어요.';
    box.append(b);
    if (p.phase === 'waiting') box.append(linkTo(WORKFLOW_URL, 'GitHub 창 다시 열기'));
    if (p.runUrl) box.append(linkTo(p.runUrl, '실행 기록'));
    if (p.market === market && pending) box.append(cancelLink());
    return;
  }
  if (p && p.phase === 'done') {
    b.disabled = true; b.textContent = '✓ 갱신 완료'; b.classList.add('ok'); box.append(b);
    if (p.note) box.append(el('span', p.note, 'run-reason'));
    return;
  }
  if (p && p.phase === 'failed') {
    b.textContent = '실패 · 다시 갱신'; b.classList.add('bad'); b.onclick = () => openDialog(market, '지난 요청이 실패했어요');
    box.append(b); if (p.runUrl) box.append(linkTo(p.runUrl, '실패 기록 보기')); return;
  }
  b.textContent = '지금 갱신';
  if (!reason) { b.disabled = true; b.title = '지금 데이터가 최신이라 갱신할 필요가 없어요.'; }
  else { b.classList.add('needed'); b.title = reason; b.onclick = () => openDialog(market, reason); }
  box.append(b);
  if (reason) box.append(el('span', reason, 'run-reason'));
}

function linkTo(url, text) { const a = el('a', text, 'run-link'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }
function cancelLink() {
  const c = el('button', '추적 그만두기', 'run-cancel'); c.type = 'button';
  c.onclick = () => { savePending(null); repaint(); };
  return c;
}

function repaint() { document.querySelectorAll('.run-now').forEach(paint); }

function openDialog(market, reason) {
  let d = document.getElementById('run-dialog');
  if (!d) { d = el('dialog'); d.id = 'run-dialog'; d.className = 'run-dialog'; document.body.append(d); d.addEventListener('click', e => { if (e.target === d) d.close(); }); }
  d.replaceChildren();
  d.append(el('div', 'DATA REFRESH', 'eyebrow'), el('h2', `${NAMES[market]} 데이터 지금 갱신`), el('p', `이유: ${reason}`, 'run-why'));
  const steps = el('ol', null, 'run-steps');
  [
    ['아래 초록 버튼을 누르면', 'GitHub 실행 화면이 새 창으로 열려요. (저장소 주인 계정으로 로그인되어 있어야 해요)'],
    ["오른쪽 'Run workflow' 버튼을 누르세요.", "작은 창이 열려요. 갱신할 시장은 그대로 두거나 " + `'${market}'` + "를 고르고, '검증 결과를 실제 게시'가 체크된 걸 확인하세요."],
    ["초록색 'Run workflow'를 한 번 더 누르면 끝!", '이 화면으로 돌아오면 실행 대기 → 수집 중 → 완료 순서로 보여드려요. 보통 10~25분 걸려요.'],
  ].forEach(([title, text]) => { const li = el('li'); li.append(el('b', title), el('span', text)); steps.append(li); });
  d.append(steps);
  d.append(el('p', '이미 실행 중인 작업이 있으면 GitHub이 순서대로 처리해서 겹치지 않아요. 빠진 종목만 있으면 그 시장만 다시 모으고, 결과가 더 나빠지면 이전 데이터를 그대로 둬요.', 'run-note'));
  const actions = el('div', null, 'run-actions');
  const go = el('button', 'GitHub 실행창 열기', 'run-go'); go.type = 'button';
  const cancel = el('button', '닫기'); cancel.type = 'button'; cancel.onclick = () => d.close();
  go.onclick = async () => {
    const existing = await activeRun();
    if (existing) {
      savePending({ market, at: Date.now(), phase: existing.status === 'in_progress' ? 'running' : 'queued', runId: existing.id, runUrl: existing.html_url });
      d.close(); repaint(); schedulePoll(5000); return;
    }
    window.open(WORKFLOW_URL, '_blank', 'noopener');
    savePending({ market, at: Date.now(), phase: 'waiting' });
    d.close(); repaint(); schedulePoll(20000);
  };
  actions.append(go, cancel);
  d.append(actions);
  d.showModal();
}

async function fetchRuns() {
  try {
    const headers = { Accept: 'application/vnd.github+json' };
    if (gh.etag) headers['If-None-Match'] = gh.etag;
    const r = await fetch(API, { headers, cache: 'no-store' });
    if (r.status === 304) return gh.runs;
    if (r.status === 403 || r.status === 429) { gh.limited = true; return null; }
    if (!r.ok) return null;
    gh.etag = r.headers.get('ETag'); gh.limited = false;
    gh.runs = (await r.json()).workflow_runs || [];
    return gh.runs;
  } catch { return null; }
}

async function activeRun() {
  const runs = await fetchRuns();
  return (runs || []).find(r => ['queued', 'in_progress', 'waiting', 'pending', 'requested'].includes(r.status)) || null;
}

async function fetchStatus() {
  try {
    const r = await fetch('wamo_refresh_status.json', { cache: 'no-store' });
    if (r.ok) { status = await r.json(); statusAt = Date.now(); }
  } catch {}
}

function schedulePoll(delay) { clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay); }

async function poll() {
  if (!pending) return;
  if (document.hidden) return schedulePoll(60000);
  const age = Date.now() - pending.at;
  if (pending.phase === 'done') {
    if (Date.now() - (pending.doneAt || pending.at) > 10 * 60000) savePending(null);
    repaint(); if (pending) schedulePoll(60000); return;
  }
  if (pending.phase === 'failed') {
    if (Date.now() - (pending.failedAt || pending.at) > 60 * 60000) savePending(null);
    repaint(); return;
  }
  if (age > MAX_WAIT) { savePending({ ...pending, phase: 'failed', failedAt: Date.now() }); repaint(); return; }
  await fetchStatus();
  const s = status?.[pending.market] || {};
  // GitHub every ~2 minutes after the first 10 minutes (304s are free anyway).
  const checkGithub = age < 10 * 60000 || !pending.lastGh || Date.now() - pending.lastGh > 110000;
  let run = null;
  if (checkGithub) {
    const runs = await fetchRuns();
    pending.lastGh = Date.now();
    run = (runs || []).filter(r => Date.parse(r.created_at) >= pending.at - 120000).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))[0]
      || (pending.runId && (runs || []).find(r => r.id === pending.runId)) || null;
  }
  let next = { ...pending };
  if (run) {
    next.runId = run.id; next.runUrl = run.html_url;
    if (run.status === 'completed') { next.phase = run.conclusion === 'success' ? 'applying' : 'failed'; if (next.phase === 'failed') next.failedAt = Date.now(); }
    else next.phase = run.status === 'in_progress' ? 'running' : 'queued';
  }
  const attempted = parse(s.attemptedAt), success = parse(s.lastSuccessAt);
  if (s.status === 'RUNNING' && attempted && attempted >= pending.at - 120000) next.phase = 'running';
  if (success && success >= pending.at - 60000 && s.status !== 'RUNNING') next.phase = 'applying';
  if (next.phase === 'applying') {
    await onDone();
    const finished = run && run.status === 'completed';
    if (!refreshReason(pending.market) || (success && success >= pending.at - 60000) || finished) {
      next.phase = 'done'; next.doneAt = Date.now();
      next.note = s.partialRerun || (refreshReason(pending.market) ? `실행은 끝났지만 아직 ${refreshReason(pending.market)}` : '');
    }
  }
  savePending(next);
  repaint();
  schedulePoll(next.phase === 'waiting' ? 20000 : 30000);
}

export function initRunner({ done, marketStatus }) {
  onDone = done || onDone; getMarketStatus = marketStatus || getMarketStatus;
  const tick = async () => { if (!document.hidden) { await fetchStatus(); repaint(); } clearTimeout(statusTimer); statusTimer = setTimeout(tick, 60000); };
  tick();
  if (pending) schedulePoll(3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { tick(); if (pending) schedulePoll(1000); } });
}
