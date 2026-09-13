import {createStore} from './store.js';
import {COUNTRIES,FLAGS,CONNECTION_TTL,selectStocks,selectIndustries} from './model.js';
import {el,openDetail,candidateText,priceText} from './detail.js';

const PAGE_SIZE=24;
const defaults=()=>({mode:'candidates',query:'',country:'',industry:'',signal:'',technical:'',historical:false,sort:'recent'});
const statusNames={ready:'연결됨',missing:'자료 없음',failed:'연결 실패',invalid:'자료 형식 오류',current:'연결 확인 유효',stale:'연결 확인 지연 · 과거 자료'};

export function emptyStateMessage(model,filters={}) {
  if(filters.mode==='search') return '현재 제공 범위에서 찾을 수 없음. 검색어·국가·산업·신호 조건을 확인하세요.';
  if(model.status.radar!=='current') return '현재 후보를 검증할 수 없습니다. 연결 확인·자료 상태를 확인하거나 다시 확인해 주세요. 과거 자료는 위에서 선택하여 볼 수 있습니다.';
  const editions=model.status.editions||{};
  const selected=filters.country ? editions[filters.country] ?? (filters.country==='US'?null:editions.ASIA) : null;
  if(selected==='FAILED') return '선택한 국가의 시장 발표가 갱신 실패하여 현재 후보를 검증할 수 없습니다. 다시 확인하거나 과거 자료 보기를 선택해 주세요.';
  if(selected==='INVALID') return '선택한 국가의 시장 발표에 자료 형식 오류가 있어 현재 후보를 검증할 수 없습니다.';
  const editionStates=Object.values(editions);
  if(editionStates.length && !editionStates.includes('PASS')) {
    if(editionStates.every(value=>value==='FAILED')) return '모든 시장 발표가 갱신 실패하여 현재 후보를 검증할 수 없습니다. 다시 확인하거나 과거 자료 보기를 선택해 주세요.';
    return '사용 가능한 시장 발표가 없습니다. 갱신 실패 또는 자료 형식 오류 상태를 확인해 주세요.';
  }
  return '조건에 맞는 현재 공개 후보가 없습니다. 필터를 조정하거나 알고 있는 종목 검색에서 제공 범위를 확인하세요.';
}

export function mountDiscovery() {
  const host=document.getElementById('discovery-root');
  if(!host || document.getElementById('wamo-discovery')) return;
  const stylesheet=el('link',null,document.head); stylesheet.rel='stylesheet'; stylesheet.href=new URL('./discovery.css',import.meta.url).href;
  const panel=el('section'); panel.id='wamo-discovery'; panel.setAttribute('aria-labelledby','discovery-title');
  host.replaceChildren(panel);
  el('p','WAMO DISCOVERY',panel,'discovery-eyebrow');
  el('h1','글로벌 종목 발견',panel).id='discovery-title';
  el('p','신고가 근거로 후보를 찾고, 산업의 확산을 살펴보고, 알고 있는 종목을 확인하세요.',panel,'discovery-intro');
  const scope=el('p','공개 자료 범위를 확인하는 중…',panel,'discovery-muted'); scope.id='discovery-scope';
  const metrics=el('div',null,panel,'discovery-metrics'); metrics.id='discovery-metrics';
  const currentMetric=el('p',null,metrics), readyMetric=el('p',null,metrics), knownMetric=el('p',null,metrics);
  const statusRow=el('div',null,panel,'discovery-status-row');
  const status=el('p','자료를 불러오는 중…',statusRow); status.id='discovery-status'; status.setAttribute('role','status');
  const retry=el('button','다시 확인',statusRow); retry.id='discovery-retry'; retry.type='button';
  const editionStatus=el('p',null,panel,'discovery-muted'); editionStatus.id='discovery-editions';
  const controls=el('div',null,panel,'discovery-controls');
  const filters=defaults(); let page=1, expiryTimer, renderedResults='';
  function selectControl(name,label,options) {
    const wrapper=el('label',label,controls,'discovery-field'); wrapper.htmlFor=`discovery-${name}`;
    const input=el('select',null,wrapper); input.id=`discovery-${name}`;
    input.setAttribute('aria-label',label);
    for(const [value,title] of options) el('option',title,input).value=value;
    input.addEventListener('change',()=>{
      filters[name]=input.value; page=1;
      if(name==='mode' && input.value==='industries') {filters.signal='';filters.technical='';signal.value='';technical.value='';}
      render();
    });
    return input;
  }
  const mode=selectControl('mode','발견 방식',[['candidates','검증된 신고가 후보'],['industries','산업 탐색'],['search','알고 있는 종목 검색']]);
  const queryLabel=el('label','종목·시장·산업 검색',controls,'discovery-field discovery-query-field'); queryLabel.htmlFor='discovery-query';
  const query=el('input',null,queryLabel); query.type='search'; query.id='discovery-query'; query.placeholder='예: 미국 반도체 · 삼성 · AAPL'; query.autocomplete='off';
  query.addEventListener('input',()=>{filters.query=query.value;page=1;render();});
  const country=selectControl('country','국가',[['','전체 국가'],...Object.entries(COUNTRIES)]);
  const industry=selectControl('industry','레이더 산업 경로',[['','전체 산업']]);
  const signal=selectControl('signal','신고가 유형',[['','모든 신고가 유형'],...Object.entries(FLAGS)]);
  const technical=selectControl('technical','WAMO 기술 조건',[['','기술 조건 없음'],['stage2','Stage 2 핵심 7조건'],['aligned','이동평균 정배열'],['rs80','WAMO RS 백분위 ≥80']]);
  const sort=selectControl('sort','종목 정렬',[['recent','검증 상태·기준일순'],['name','이름순'],['symbol','종목코드순']]);
  const actions=el('div',null,panel,'discovery-actions');
  const historicalLabel=el('label',null,actions,'discovery-checkbox');
  const historical=el('input',null,historicalLabel); historical.type='checkbox'; historical.id='discovery-historical';
  historicalLabel.append(document.createTextNode('과거 신호·실패한 발표 자료도 보기'));
  historical.addEventListener('change',()=>{filters.historical=historical.checked;page=1;render();});
  const reset=el('button','필터 초기화',actions); reset.id='discovery-reset'; reset.type='button';
  reset.onclick=()=>{
    Object.assign(filters,defaults());
    for(const [key,input] of Object.entries({mode,query,country,industry,signal,technical,sort})) input.value=filters[key];
    historical.checked=false; page=1; render(); query.focus();
  };
  const modeNote=el('p',null,panel,'discovery-note'); modeNote.id='discovery-mode-note';
  const count=el('p',null,panel,'discovery-count'); count.id='discovery-count'; count.setAttribute('role','status'); count.setAttribute('aria-live','polite');
  const results=el('div',null,panel,'discovery-results'); results.id='discovery-results';
  const pagination=el('nav',null,panel,'discovery-pagination'); pagination.setAttribute('aria-label','발견 결과 페이지');
  const prev=el('button','이전',pagination); prev.id='discovery-prev'; prev.type='button';
  const pageLabel=el('span',null,pagination); pageLabel.id='discovery-page';
  const next=el('button','다음',pagination); next.id='discovery-next'; next.type='button';
  const movePage=delta=>{page+=delta;render();count.tabIndex=-1;count.focus();};
  prev.onclick=()=>movePage(-1); next.onclick=()=>movePage(1);
  el('p','신고가는 매수 추천이 아닙니다. 확인·해당 없음·미확인을 구분하며, 기업·가격 평가는 별도로 확인해야 합니다.',panel,'discovery-muted');
  const store=createStore({onChange:()=>render()});
  retry.onclick=()=>store.refresh();

  function updateIndustries(model) {
    const paths=new Map();
    const rows=[...model.stocks.filter(x=>x.signal),...model.industries];
    for(const row of rows) if((!filters.country||row.country===filters.country) && row.industryPath.length && (row.current||filters.historical||filters.mode==='search')) paths.set(row.industryKey,row.industryPath.join(' › '));
    if(filters.industry && !paths.has(filters.industry)) {
      let label='선택한 산업 (자료 없음)';
      try {label=JSON.parse(filters.industry).join(' › ')+' (자료 없음)';} catch {}
      paths.set(filters.industry,label);
    }
    const entries=[...paths].sort((a,b)=>a[1].localeCompare(b[1],'ko'));
    const signature=JSON.stringify(entries);
    if(industry.dataset.options!==signature) {
      industry.replaceChildren();el('option','전체 산업',industry).value='';
      for(const [key,title] of entries) el('option',title,industry).value=key;
      industry.dataset.options=signature;
    }
    industry.value=filters.industry;
  }
  function renderStock(row,model) {
    const card=el('article',null,results,'discovery-stock'); card.dataset.id=row.id;card.dataset.country=row.country;card.dataset.industry=row.industryKey;
    el('p',`${COUNTRIES[row.country]} · ${row.symbol}`,card,'discovery-card-market');
    const button=el('button',`${row.name} · ${row.market}:${row.symbol}`,card,'discovery-open-detail'); button.type='button';
    button.onclick=()=>openDetail(row,{trigger:button,checkedAt:model.status.checkedAt,restoreFocus:()=>restoreResultFocus(row.id)});
    el('p',candidateText(row),card,row.current?'discovery-current':'discovery-muted');
    el('p',`${row.asOf||'기준일 미확인'} · ${priceText(row)}`,card,'discovery-price');
    if(row.signal) {
      const labels=Object.entries(FLAGS).filter(([key])=>row.signal.flags[key]===true).map(([,label])=>label);
      el('p',`${row.current?'확인 신호':'과거 확인 신호'}: ${labels.join(' / ')||'없음'}`,card);
      el('p',row.industryPath.join(' › ')||'레이더 분류 미확인',card,'discovery-muted');
      const caveats=Array.isArray(row.signal.classification_caveats)?row.signal.classification_caveats.filter(x=>typeof x==='string'):[];
      el('small',caveats.join(' · ')||'세부사업·분류 범위는 상세 근거에서 확인하세요.',card,'discovery-caveat');
    } else el('p',`WAMO 업종: ${row.catalog?.sector||'미확인'} · 전체 거래소 신호 부재를 뜻하지 않습니다.`,card,'discovery-muted');
    if(row.technicalCurrent) el('p',`기술 연결 · Stage 2 핵심 7조건 ${row.technical.stage2?'확인':'해당 없음'} · RS ${row.technical.rsPercentile}`,card,'discovery-technical');
    else el('p','현재 후보용 Stage 2·정배열·RS 연결 없음',card,'discovery-muted');
  }
  function restoreResultFocus(id) {
    const card=[...results.children].find(node=>node.dataset.id===id);
    const button=card?.querySelector('button');
    if(button) button.focus();
    else {count.tabIndex=-1;count.focus();}
  }
  function renderIndustry(row) {
    const card=el('article',null,results,'discovery-industry');card.dataset.id=row.id;card.dataset.country=row.country;card.dataset.industry=row.industryKey;
    el('p',`${COUNTRIES[row.country]} · ${row.current?'검증된 발표':'과거 발표'}`,card,'discovery-card-market');
    const button=el('button',row.industryPath.join(' › '),card,'discovery-open-industry');button.type='button';
    button.onclick=()=>{
      filters.mode='candidates'; filters.country=row.country; filters.industry=row.industryKey; filters.signal='';filters.technical='';
      mode.value=filters.mode;country.value=filters.country;signal.value='';technical.value='';page=1;render();count.tabIndex=-1;count.focus();
    };
    el('p',`${row.current_signal_issuers} / ${row.eligible_classified_issuers} 기업 · ${(row.ratio*100).toFixed(1)}%`,card,'discovery-industry-ratio');
    el('p','신고가 기업 / 분류 확인 기업',card,'discovery-muted');
    el('p',row.phase||'확산 단계 미확인',card);
    el('small',row.comparison_scope||'현재 확인된 분류 종목군 기준',card,'discovery-caveat');
    el('p','해당 국가·산업의 후보 종목 보기 →',card,'discovery-current');
  }
  function render() {
    const state=store.getState(), model=state.model;
    const candidates=selectStocks(model,{mode:'candidates'});
    const ready=candidates.filter(row=>row.technicalCurrent).length;
    const catalogCount=model.stocks.filter(row=>row.catalog).length;
    const radarCount=model.stocks.filter(row=>row.signal).length;
    scope.textContent=`제공 범위: WAMO KR/US 공개 스냅샷 ${catalogCount.toLocaleString('ko-KR')}종목 + 공개 레이더 후보 관측 ${radarCount}종목 (중복 제외 ${model.stocks.length.toLocaleString('ko-KR')}종목). 전체 거래소 종목 검색이 아닙니다.`;
    currentMetric.textContent=`${candidates.length} 현재 공개 후보`;
    readyMetric.textContent=`${ready} 동일 기준일 기술 연결`;
    knownMetric.textContent=`${model.stocks.length.toLocaleString('ko-KR')} 검색 가능 종목`;
    status.textContent=`레이더: ${statusNames[model.status.radar]} · 카탈로그: ${statusNames[model.status.catalog]}${state.loading?' · 갱신 중…':''}`;
    status.className=['current','missing'].includes(model.status.radar)?'':'discovery-warning';
    editionStatus.textContent=`마지막 연결 확인 ${model.status.checkedAt||'기록 없음'} · ${Object.entries(model.status.editions).map(([name,value])=>`${COUNTRIES[name]||name} ${value==='PASS'?'검증 통과':value==='FAILED'?'갱신 실패':'형식 오류'}`).join(' / ')||'시장별 검증 자료 없음'}`;
    retry.disabled=state.loading; retry.textContent=state.loading?'확인 중…':'다시 확인';
    updateIndustries(model);
    const isIndustry=filters.mode==='industries';
    signal.parentElement.hidden=isIndustry;technical.parentElement.hidden=isIndustry;sort.parentElement.hidden=isIndustry;
    modeNote.textContent=isIndustry?'모든 신고가 유형을 합친 기업 수 비율입니다. 확인된 분류 종목군의 확산이며 전체 시장 비율이 아닙니다. 비율·기업 수순으로 표시하고, 선택 시 국가와 정확한 산업 경로의 후보로 이동합니다. 기업 수와 종목 수는 다를 수 있습니다.':filters.mode==='search'?'알고 있는 종목을 제공 범위에서 찾습니다. “공개 후보 목록에 없음”은 전체 거래소의 신고가 부재를 뜻하지 않습니다. 기술 조건을 선택하면 동일 기준일로 연결된 현재 후보만 남습니다.':'검증된 발표와 최근 3시간 이내 연결 확인을 통과한 공개 후보입니다. 신고가 유형과 기술 조건을 함께 적용할 수 있습니다. 기술 조건은 FULL·이력 준비·동일 기준일인 WAMO 관측에만 적용합니다.';
    const rows=isIndustry?selectIndustries(model,filters):selectStocks(model,filters);
    const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE)); page=Math.max(1,Math.min(page,pages));
    const start=(page-1)*PAGE_SIZE, visible=rows.slice(start,start+PAGE_SIZE);
    const historicalCount=rows.filter(row=>!row.current && (isIndustry||row.signal)).length;
    const readyCount=isIndustry?0:rows.filter(row=>row.technicalCurrent).length;
    count.textContent=`${rows.length.toLocaleString('ko-KR')}개 ${isIndustry?'산업':filters.mode==='search'?'검색 결과':'후보'}${!isIndustry?` · 현재 ${rows.filter(row=>row.current).length} · 기술 연결 ${readyCount}`:''}${historicalCount?` · 과거 관측 ${historicalCount}`:''} · ${rows.length?`${start+1}–${start+visible.length}`:'0'} 표시`;
    const message=emptyStateMessage(model,filters);
    const resultSignature=JSON.stringify([isIndustry,model.status.checkedAt,model.status.radar,visible,message]);
    if(resultSignature!==renderedResults) {
      results.replaceChildren();
      if(!rows.length) el('p',message,results,'discovery-empty');
      else for(const row of visible) isIndustry?renderIndustry(row):renderStock(row,model);
      renderedResults=resultSignature;
    }
    prev.disabled=page<=1; next.disabled=page>=pages; pageLabel.textContent=`${page} / ${pages} 페이지 · ${PAGE_SIZE}개씩`;
    clearTimeout(expiryTimer);
    const expires=Date.parse(model.status.checkedAt)+CONNECTION_TTL+1;
    if(model.status.radar==='current' && expires>Date.now()) expiryTimer=setTimeout(render,expires-Date.now());
  }
  render();store.refresh();
  setInterval(()=>store.refresh(),5*60*1000);
  setInterval(()=>render(),30000);
  const revalidate=()=>{render();store.refresh();};
  window.addEventListener('focus',revalidate);
  window.addEventListener('online',revalidate);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') revalidate();});
}
