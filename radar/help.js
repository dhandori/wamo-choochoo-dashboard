// Definitions follow wamo_update_business_dart.py and wamo_insights.py.
const TERMS = {
  rs: ['RS · 상대 강도', '같은 시장의 분석 대상 안에서 주가 흐름이 얼마나 강한지 나타내는 0~100 상대 순위입니다. 5일 수익률 20% + 20일 40% + 60일 40%를 합쳐 비교합니다. RS 80 이상은 대략 상위 20%에 해당하며, RSI(과매수·과매도 지표)와는 다릅니다.'],
  stage: ['Stage 2 · 상승 추세 조건', '종가 > 50일선 > 150일선 > 200일선이고, 200일선이 20거래일 전보다 높아야 합니다. 여기에 종가가 52주 저점보다 30% 이상 높고 고점에서 25% 이내이면 충족입니다. 이동평균선은 해당 기간 종가의 평균이며, RS 조건은 별도로 적용합니다.'],
  distance: ['고점까지 · 52주 고점 거리', '최근 252거래일 장중 최고가에서 표시된 종가가 얼마나 아래에 있는지 보여 줍니다. 5%면 고점보다 5% 낮다는 뜻이며, 작을수록 고점에 가깝습니다. 고점 재도달에 필요한 상승률과는 다릅니다.'],
  high: ['52주 고점', '최근 252거래일 중 가장 높은 장중 가격입니다. 252개 거래일 이력이 있어야 비교하며, 이력이 부족하면 —로 표시합니다.'],
  near: ['52주 신고가권', '종가가 최근 252거래일 장중 고점에서 10% 이내인 종목입니다. 상단 집계는 10% 기준이며, 신고가권 탭에서는 5·10·15%로 좁히거나 넓힐 수 있습니다.'],
  breakout: ['당일 신고가', '표시된 거래일의 장중 최고가나 종가가 직전 251거래일의 최고값을 넘은 경우입니다. 같은 가격에 그친 경우는 빼고, 장중·종가 조건을 둘 다 채워도 한 종목으로 셉니다.'],
  high60: ['60일 고점 · 60일 신고가', '최근 60거래일(약 3개월)의 장중 최고가입니다. 60일 신고가는 그날 고가(또는 종가)가 직전 59거래일의 최고가(또는 최고 종가)를 넘긴 날이에요. 52주 신고가보다 짧은 돌파라 조정 뒤 다시 올라오는 종목을 일찍 잡지만, 그만큼 가짜 돌파도 많아요.'],
  atr: ['ATR(14일) · 평균 변동폭', '최근 14거래일 동안 하루에 움직인 폭(고가−저가, 전날 종가와의 갭 포함)의 평균입니다. 옆의 %는 종가 대비 비율로, 이 종목이 보통 하루에 얼마나 움직이는지를 뜻합니다. 손절 폭이나 목표가를 잡을 때 기준으로 씁니다. 고가·저가 기록이 없는 날은 종가로 대신해 실제보다 작게 나올 수 있습니다.'],
  volume: ['거래량 배수 · 상대 거래량','표시 거래일의 거래량 ÷ 이전 50거래일 평균 거래량입니다. 평균에서 당일은 제외합니다. 1.5×는 평소보다 50% 많은 거래량이며, 필요한 이력이 없으면 —로 표시합니다.'],
  median: ['중앙값 · 분야 수익률', '계산된 종목 수익률을 낮은 순서로 놓았을 때 가운데 값입니다. 짝수 개이면 가운데 두 값의 평균을 씁니다. 시총 가중 수익률이 아니므로 일부 대형주의 영향이 과하게 커지지 않습니다. 이력이 부족한 종목은 해당 기간 집계에서 제외합니다.'],
  returns: ['기간 수익률', '표시 거래일 종가가 해당 거래일 전 종가보다 얼마나 올랐거나 내렸는지 나타냅니다. 200일은 달력상 200일이 아닌 200거래일이며, 종가 201개가 필요합니다. 이동평균선이나 배당 재투자 수익률을 뜻하지 않습니다.'],
  today: ['당일 등락률', '표시 거래일 종가의 직전 거래일 종가 대비 변화율입니다. 실시간 가격이 아니며, 화면 상단의 가격 기준일을 따릅니다.'],
  cap: ['시총 · 시가총액', '기업의 주식 가치를 합한 규모입니다. 한국은 원, 미국은 달러로 표시하며, 서로 다른 통화의 숫자를 그대로 비교하면 안 됩니다. 자료가 없으면 ‘정보 없음’으로 표시합니다.'],
  breadth: ['당일 상승 비율', '당일 등락률이 0보다 큰 AI 종목 수 ÷ 당일 등락률이 계산된 AI 종목 수입니다. AI 전체에서는 중복 종목을 한 번만 셉니다.'],
  trendBreadth: ['추세 확산도 · 50일선 위 비율', '종가가 최근 50거래일 종가 평균보다 높은 종목의 비율입니다. 예를 들어 8/10은 비교 가능한 10종목 중 8종목, 즉 80%입니다. 5일 전 대비 변화는 양 시점에 모두 자료가 있는 동일 종목만 비교합니다. 60%에서 75%가 되면 +15%p이며, 수익률 +15%와는 다릅니다. 이력이 부족하거나 거래일이 누락되면 해당 비교에서 제외합니다.'],
  newOnly: ['새로 들어온 종목만 · 직전 거래일 대비', '신고가권 진입, RS 80 미만에서 80 이상으로 상승, Stage 2 미충족에서 충족으로 전환 중 하나 이상이 확인된 종목만 표시합니다. 현재 선택한 다른 필터도 함께 적용합니다. 신고가권 탭은 선택한 5·10·15% 기준, 다른 탭은 10% 기준입니다. 직전 거래일 자료가 없는 종목은 새로 들어온 것으로 세지 않습니다.'],
  changes: ['전일 변화 · 진입과 이탈', '각 시장의 실제 직전 거래일과 현재 종가 기준 조건을 비교합니다. 신규는 신고가권·RS 80·Stage 2 중 하나 이상에 새로 진입한 종목, 이탈은 하나 이상에서 벗어난 종목입니다. 한 종목이 서로 다른 조건에서 신규와 이탈에 동시에 잡힐 수 있습니다. 데이터가 없거나 새로 목록에 추가된 종목은 변화로 판정하지 않습니다. RS는 시장 내 상대 순위이므로 다른 종목의 움직임·분석 대상 변화에도 달라질 수 있습니다.'],
  confirmed: ['산업 동반 강세', '3종목 이상인 산업에서 당일 상승 2종목 이상, 5일 상승 2종목 이상이면서 절반 이상, 5일 중앙값이 양수여야 합니다. 추가로 고점 10% 이내 2종목 이상 또는 거래량 1.5배 이상 2종목 이상이면 동반 강세로 표시합니다.'],
  trend: ['60일 추이', '최근 최대 60거래일 종가의 흐름을 줄여 그린 차트입니다. 종목마다 세로축이 달라 선의 기울기만으로 수익률 크기를 비교할 수 없습니다.'],
  ai: ['AI 관찰 종목', 'AI 사업·공급망과 연결된 별도 관리 목록 중 가격이 확인된 종목입니다. 시장의 모든 AI 기업을 포함하지 않으며, AI 매출 비중이나 투자 매력도 순위는 아닙니다.'],
};
const LABELS = {
  'RS':'rs', 'Stage 2':'stage', '고점까지':'distance', '고점 거리':'distance', '52주 고점 거리':'distance', '52주 고점까지':'distance',
  '52주 장중 고점':'high', '52주 신고가권':'near', 'AI 신고가권':'near',
  '당일 신고가':'breakout', 'AI 당일 신고가':'breakout', '거래량 배수':'volume', '상대 거래량':'volume',
  '중앙값':'median', '당일':'today', '시가총액':'cap', '시총':'cap', '당일 상승 비율':'breadth',
  '산업 동반 강세':'confirmed', '확인된 그룹만':'confirmed', '동반 강세 산업만':'confirmed', '60일 추이':'trend', 'AI 관찰 종목':'ai',
  '추세 확산도':'trendBreadth','전일 변화':'changes', 'ATR(14일)':'atr', '60일 고점까지':'high60', '60일 장중 고점':'high60', '60일 신고가':'high60',
};
// Other screens register their own glossary (FTD, leaders) without touching the list above.
export function addTerms(extra) {
  // Never overwrite a term another screen already defined (a shared key once made
  // the Stage 2 help show the leaders tab's industry-stage text).
  for (const [key, value] of Object.entries(extra)) if (!(key in TERMS)) TERMS[key] = value; else if (TERMS[key] !== value) console.warn('help term key in use:', key);
}
let active = null, pinned = false, closeTimer;
const tip = document.createElement('div');
tip.id = 'term-tooltip'; tip.className = 'term-tooltip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true;

export function closeHelp() {
  clearTimeout(closeTimer);
  active?.removeAttribute('aria-describedby');
  active = null; pinned = false; tip.hidden = true;
}
let openedAt = 0;
function showHelp(trigger) {
  clearTimeout(closeTimer); openedAt = performance.now();
  if (active !== trigger) closeHelp();
  active = trigger;
  // 세 번째 값은 선택이다: [[소제목, 본문], …]. 패턴 설명처럼 항목이 여러 개인 풀이에 쓴다.
  const [title, description, sections] = TERMS[trigger.dataset.help];
  const heading = document.createElement('strong'), body = document.createElement('p');
  heading.textContent = title; body.textContent = description; tip.replaceChildren(heading, body);
  for (const [label, text] of Array.isArray(sections) ? sections : []) {
    if (!text) continue;
    const part = document.createElement('p'), name = document.createElement('b');
    part.className = 'term-section'; name.textContent = label; part.append(name, document.createTextNode(text)); tip.append(part);
  }
  (trigger.closest('dialog') || document.body).append(tip);
  tip.hidden = false; trigger.setAttribute('aria-describedby', tip.id);
  const rect = trigger.getBoundingClientRect(), gap = 8, margin = 12;
  const width = document.documentElement.clientWidth, height = window.innerHeight;
  tip.style.maxHeight = `${height - margin * 2}px`;
  tip.style.left = `${Math.max(margin, Math.min(rect.left + rect.width / 2 - tip.offsetWidth / 2, width - tip.offsetWidth - margin))}px`;
  const below = rect.bottom + gap;
  tip.style.top = `${Math.max(margin, Math.min(below + tip.offsetHeight <= height - margin ? below : rect.top - gap - tip.offsetHeight, height - tip.offsetHeight - margin))}px`;
}
function leaveHelp() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (!pinned && document.activeElement !== active && !tip.matches(':hover')) closeHelp();
  }, 180);
}
export function helpButton(label, key, icon = false) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = `term-help${icon ? ' term-help-icon' : ''}`;
  b.textContent = icon ? '?' : label; b.dataset.help = key;
  b.setAttribute('aria-label', `${label} 설명`);
  let pointerFocus = false;
  b.addEventListener('pointerdown', () => { pointerFocus = true; });
  b.addEventListener('pointerenter', e => { if (e.pointerType === 'mouse') showHelp(b); });
  b.addEventListener('pointerleave', leaveHelp);
  b.addEventListener('focus', () => { if (!pointerFocus) showHelp(b); });
  b.addEventListener('blur', () => { pointerFocus = false; if (active === b) { pinned = false; leaveHelp(); } });
  b.addEventListener('click', e => {
    e.stopPropagation(); pointerFocus = false;
    if (active === b && pinned) closeHelp();
    else { showHelp(b); pinned = true; }
  });
  return b;
}
export function helpLabel(label) {
  const key = LABELS[label] || (/^(5|20|60|120|200)일( 수익률)?$/.test(label) ? 'returns' : label.startsWith('시총 (') ? 'cap' : null);
  if (key) return helpButton(label, key);
  const text = document.createElement('span'); text.textContent = label; return text;
}
tip.addEventListener('pointerenter', () => clearTimeout(closeTimer));
tip.addEventListener('pointerleave', leaveHelp);
document.addEventListener('pointerdown', e => {
  if (active && !active.contains(e.target) && !tip.contains(e.target)) closeHelp();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && active) { e.preventDefault(); e.stopImmediatePropagation(); closeHelp(); }
}, true);
// 도움말을 여는 동작이 스스로 일으킨 스크롤(휴대폰 아래 시트가 버튼을 가릴 때 브라우저가 버튼을 끌어올림)로는 닫지 않는다.
document.addEventListener('scroll', e => { if (!tip.contains(e.target) && performance.now() - openedAt > 400) closeHelp(); }, true);
window.addEventListener('resize', closeHelp);
