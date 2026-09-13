import {COUNTRIES,FLAGS,normalizeDate} from './model.js';

export function el(tag,text,parent,className) {
  const node=document.createElement(tag);
  if(text!==undefined && text!==null) node.textContent=String(text);
  if(className) node.className=className;
  if(parent) parent.append(node);
  return node;
}
export function handleOriginalRequest(search,open) {
  const params=new URLSearchParams(search);
  const ticker=params.get('stock')||params.get('radar');
  if(!ticker) return 'none';
  return typeof open==='function' && open(ticker)?'opened':'missing';
}
export function originalDetailUrl(row) {
  const c=row.catalog;
  if(!c || !['KR','US'].includes(c.country) || typeof c.ticker!=='string' || !c.ticker.trim()) return null;
  return `${c.country==='KR'?'index':'us'}.html?stock=${encodeURIComponent(c.ticker)}`;
}
export const truth = value => value===true?'확인':value===false?'해당 없음':'미확인';
const numeric = value => typeof value==='number' && Number.isFinite(value)?value.toLocaleString('ko-KR',{maximumFractionDigits:4}):'미확인';
export const priceText = row => `${numeric(row.close)} ${row.currency||'통화 미확인'}`;
export const candidateText = row => ({current:'검증된 공개 후보',historical:'과거 관측 · 현재 후보 검증 불가','not-listed':'현재 공개 후보 목록에 없음',unavailable:'공개 후보 여부 확인 불가'})[row.candidateState];
export function technicalObservationLabel(row) {
  if(row.technicalCurrent) return '열람 시 신고가와 동일 기준일 연결';
  if(!row.signal) return '현재 후보용 적용 불가 · 신고가 관측과 연결 없음';
  const unavailable={failed:'카탈로그 연결 실패',invalid:'카탈로그 자료 형식 오류',missing:'카탈로그 자료 없음'}[row.technicalSourceStatus];
  if(unavailable) return `현재 후보용 적용 불가 · ${unavailable}`;
  if(normalizeDate(row.technical?.asOf)!==normalizeDate(row.asOf)) return '현재 후보용 적용 불가 · 기준일 불일치 관측';
  return '현재 후보용 적용 불가 · 과거 신고가 관측';
}

export function openDetail(row,{trigger,checkedAt,restoreFocus}={}) {
  document.getElementById('discovery-detail')?.close();
  const dialog=el('dialog',null,document.body,'discovery-dialog'); dialog.id='discovery-detail';
  dialog.setAttribute('aria-labelledby','discovery-detail-title');
  const heading=el('div',null,dialog,'discovery-detail-heading');
  el('h2',`${row.name} · ${row.symbol}`,heading).id='discovery-detail-title';
  const close=el('button','닫기',heading); close.type='button'; close.id='discovery-detail-close'; close.onclick=closeDialog;
  el('p',`${COUNTRIES[row.country]||row.country} · ${row.market} · ${row.asOf||'기준일 미확인'} · ${priceText(row)}`,dialog,'discovery-muted');
  el('p',`관측 스냅샷 · 연결 확인 ${checkedAt||'기록 없음'}. 이 창은 열었을 때의 자료입니다. 최신 후보 상태는 닫은 뒤 목록에서 확인하세요.`,dialog,'discovery-notice');
  if(!row.signal) el('p',row.candidateState==='not-listed'?'조회 시 공개 후보 목록에 없었습니다. 전체 거래소의 신고가 부재를 뜻하지 않습니다.':'조회 시 공개 후보 여부를 확인할 수 없었습니다.',dialog);
  else if(!row.current) el('p','과거 신호 자료입니다. 현재 후보 필터에 포함되지 않습니다.',dialog,'discovery-warning');

  const panels=el('div',null,dialog,'discovery-detail-panels');
  const company=el('section',null,panels,'discovery-evidence');
  el('h3','기업 평가 · 미평가',company);
  el('p','사업의 질·재무 건전성·경쟁우위를 발견 화면에서 평가하지 않았습니다.',company);
  el('p',`레이더 분류: ${row.industryPath.length?row.industryPath.join(' › '):'확인된 레이더 분류 없음'}`,company);
  if(row.catalog?.sector) el('p',`WAMO 업종: ${row.catalog.sector} (레이더 산업 소속과 별도)`,company);
  const caveats=Array.isArray(row.signal?.classification_caveats)?row.signal.classification_caveats.filter(x=>typeof x==='string'):[];
  if(caveats.length) {const list=el('ul',null,company); for(const item of caveats) el('li',item,list);}
  el('p',row.signal?.micro_verified===true?'세부사업 분류 검증 기록 있음':'세부사업·주된 매출사업 확인 없음',company,'discovery-muted');

  const price=el('section',null,panels,'discovery-evidence');
  el('h3','가격 평가 · 미평가',price);
  el('p',`관측 종가 ${priceText(row)} · ${row.asOf||'기준일 미확인'}`,price);
  el('p','적정가치·안전마진을 계산하지 않았습니다. 신고가 신호는 저평가나 매수 적합성을 뜻하지 않습니다.',price);
  if(row.catalog) el('p',`WAMO 관측: ${numeric(row.catalog.close)} ${row.catalog.currency||''} · ${normalizeDate(row.catalog.asOf)||'기준일 미확인'} · ${row.catalog.source||'출처 미확인'}`,price,'discovery-muted');

  const chart=el('section',null,panels,'discovery-evidence discovery-chart');
  el('h3','차트 · 신고가와 기술 근거',chart);
  el('p',`신고가 출처: 공개 레이더 · ${row.signal?row.asOf:'연결된 신호 없음'}`,chart);
  const table=el('table',null,chart,'discovery-flags');
  const head=el('tr',null,el('thead',null,table)); el('th','신고가 조건',head).scope='col'; el('th','관측 결과',head).scope='col';
  const body=el('tbody',null,table);
  for(const [key,label] of Object.entries(FLAGS)) {
    const tr=el('tr',null,body); el('th',label,tr).scope='row'; el('td',truth(row.signal?.flags?.[key]),tr);
  }
  const technical=row.technical;
  el('h4','WAMO 기술 관측',chart);
  if(technical?.ready===true) {
    el('p',`출처: ${row.catalog?.source||'WAMO 공개 스냅샷'} · 기술 기준일 ${normalizeDate(technical.asOf)||'미확인'} · ${technicalObservationLabel(row)}`,chart,'discovery-notice');
    el('p',`Stage 2 핵심 7조건: ${truth(technical.stage2)} · 이동평균 정배열: ${truth(technical.aligned)} · WAMO RS 백분위: ${numeric(technical.rsPercentile)}`,chart);
    el('p',`MA50 ${numeric(technical.ma50)} · MA150 ${numeric(technical.ma150)} · MA200 ${numeric(technical.ma200)}`,chart,'discovery-muted');
    const checks=Object.entries(technical.checks||{}).filter(([,value])=>typeof value==='boolean');
    if(checks.length) {const list=el('ul',null,chart); for(const [label,value] of checks) el('li',`${label}: ${truth(value)}`,list);}
  } else el('p','Stage 2 핵심 7조건 · 이동평균 정배열 · RS: 미확인. FULL 기술 범위와 충분한 이력이 확인된 WAMO 관측만 연결합니다.',chart,'discovery-muted');
  const definitions=el('details',null,chart); el('summary','지표의 뜻과 적용 범위',definitions);
  el('p','3개월·6개월·52주 신고가는 직전 63·126·252거래일의 장중 고가 또는 종가 최대치를 각각 초과한 관측입니다. 동일 가격은 신고가가 아닙니다. 역사적 신고가는 상장 이후 조정 이력이 검증된 경우만 확인하며, 이력이 불완전하면 미확인입니다.',definitions);
  el('p','Stage 2 핵심 7조건은 WAMO stage2Core입니다. 전체 추세 템플릿의 RS≥70 조건을 포함하지 않습니다. 정배열은 주가>MA20>MA60>MA120>MA200입니다.',definitions);
  el('p','WAMO RS는 0.2×5일 수익률 + 0.4×20일 수익률 + 0.4×60일 수익률의 해당 WAMO 시장 종목군 내 백분위입니다. O’Neil RS나 글로벌 순위가 아닙니다. 기술 필터는 FULL·이력 준비·신고가와 같은 기준일을 모두 요구합니다.',definitions);
  const url=originalDetailUrl(row);
  if(url) {const link=el('a','기존 WAMO 상세 분석 열기',dialog,'discovery-original-link');link.href=url;}
  else el('p','기존 WAMO KR/US 상세와 연결된 카탈로그 항목이 없습니다. 이 화면의 공개 신호 근거를 확인할 수 있습니다.',dialog,'discovery-muted');
  let finished=false;
  function closeDialog() {
    if(finished) return;
    finished=true;
    if(dialog.open) dialog.close();
    dialog.remove();
    if(trigger?.isConnected) trigger.focus(); else restoreFocus?.();
  }
  dialog.addEventListener('close',closeDialog,{once:true});
  dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog();},{once:true});
  dialog.showModal(); close.focus();
}
