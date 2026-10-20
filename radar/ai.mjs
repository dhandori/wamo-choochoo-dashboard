// Maintained thematic watchlist, independent of unreliable vendor industry labels.
// Membership describes business exposure, not AI revenue share or a buy signal.
export const AI_REVIEWED = '2026-10-20';
export const AI_SECTORS = [
  {id:'hardware',name:'AI H/W',description:'연산 반도체 · 메모리 · 서버 · 기판 · 반도체 공급망'},
  {id:'infrastructure',name:'AI 인프라',description:'클라우드 · 데이터센터 · 네트워크 · 냉각'},
  {id:'software',name:'AI S/W',description:'데이터 플랫폼 · 기업용 AI · 개발·업무 소프트웨어'},
  {id:'bigtech',name:'빅테크',description:'AI 플랫폼 · 모델 · 클라우드 투자 주체'},
  {id:'security',name:'보안',description:'엔드포인트 · 네트워크 · ID · 데이터 보안'},
  {id:'optics',name:'광통신',description:'광학 부품 · 트랜시버 · 광섬유 · 연결 반도체'},
  {id:'power',name:'전력',description:'발전 · 전력망 · 전력기기 · 데이터센터 전원'},
  {id:'nuclear',name:'원전',description:'원전 발전사 · 빅테크 원전 전력계약 · 원자로·SMR 기자재 · 우라늄 연료'},
  {id:'dcpower',name:'AI 발전',description:'데이터센터 현장 연료전지 · 태양광 · 빅테크 재생에너지 전력계약 · 비상발전'},
  {id:'ess',name:'ESS',description:'데이터센터·전력망용 에너지저장장치(배터리)'},
];
const catalog = new Map();
function add(market, symbols, sectors, role) {
  for(const symbol of symbols.split(' ')) {
    const id=`${market}:${symbol}`, old=catalog.get(id);
    catalog.set(id,{id,market,symbol,sectors:[...new Set([...(old?.sectors||[]),...sectors.split(' ')])],role:old?`${old.role} · ${role}`:role});
  }
}
add('US','NVDA AMD INTC ARM CBRS','hardware','GPU·CPU·AI 연산');
add('US','AVGO MRVL ALAB CRDO','hardware optics','연결·맞춤형 반도체');
add('US','MU SNDK WDC STX NTAP','hardware','메모리·스토리지');
add('US','TSM ASML AMAT LRCX KLAC AMKR ASX TER NVMI','hardware','파운드리·장비·패키징');
add('US','DELL HPE SMCI CLS FLEX JBL SANM','hardware infrastructure','서버·시스템 공급');
add('US','APH TEL TTMI MPWR','hardware','연결·기판·전원 부품');
add('US','SNPS CDNS','hardware software','반도체 설계 소프트웨어');
add('US','ANET CSCO','infrastructure','데이터센터 네트워크');
add('US','CRWV NBIS IREN DOCN','infrastructure','AI·클라우드 컴퓨팅');
add('US','EQIX DLR','infrastructure','데이터센터 운영');
add('US','VRT','infrastructure power','데이터센터 전원·냉각');
add('US','TT CARR MOD FIX EME STRL','infrastructure','냉각·데이터센터 설비·시공');
add('US','PLTR SNOW MDB NOW CRM DDOG DT ORCL IBM SAP ADBE INTU TEAM HUBS PATH AI','software','기업용 AI·데이터·업무 플랫폼');
add('US','MSFT AMZN GOOGL','bigtech infrastructure software','AI·클라우드 플랫폼');
add('US','META AAPL','bigtech','AI 플랫폼·서비스·단말');
add('US','PANW CRWD ZS FTNT OKTA CHKP S TENB RBRK SAIL','security','사이버·ID·데이터 보안');
add('US','NET FFIV','security infrastructure','네트워크·애플리케이션 보안');
add('US','COHR LITE CIEN GLW FN AAOI MTSI SMTC','optics','광학·광네트워크·연결 부품');
add('US','ETN GEV HUBB PWR WCC','power','송배전·전력설비 공급망');
add('US','CEG VST TLN NRG NEE SO DUK AEP ETR D BE','power','발전·전력 공급');
add('KR','005930 000660','hardware','메모리·HBM·반도체');
add('KR','042700 036540 067310 089030 131970 095340 058470 131290','hardware','패키징·검사·테스트');
add('KR','007660 353200 222800 007810 009150 000150','hardware','기판·PCB·전자부품');
add('KR','039030 240810 036930 403870 084370 095610 319660 031980 281820 064760 183300 357780 014680 005290 420770 232140','hardware','반도체 장비·소재 공급망');
add('KR','440110','hardware','데이터센터 SSD 컨트롤러');
add('KR','018260 064400','infrastructure software','클라우드·AI·IT 서비스');
add('KR','017670 030200','infrastructure','AI 데이터센터·통신 인프라');
add('KR','066570','infrastructure','데이터센터 냉각');
add('KR','035420 035720','bigtech software','AI·인터넷 플랫폼');
add('KR','012510 030520 304100 402030','software','기업용 AI·소프트웨어');
add('KR','053800 131370','security','기업·네트워크 보안');
add('KR','010170 230240 388050','optics','광섬유·광통신 부품');
add('KR','010120 267260 298040 103590 062040 001440 000500 229640 006260','power','전력기기·변압기·전선');
add('KR','034020 052690 015760 336260','power','발전·전력 공급망');
// AI data-center power: only names already in the verified price lists.
add('US','BE','dcpower','데이터센터 현장 연료전지 발전');
add('US','FSLR NXT','dcpower','태양광 모듈·트래커 (빅테크 전력계약 공급)');
add('US','NEE AES','dcpower','빅테크 재생에너지 장기 전력계약');
add('US','GNRC','dcpower','데이터센터 비상발전기');
add('KR','373220 006400','ess','데이터센터·전력망 ESS 배터리');
add('KR','096770','ess','ESS용 LFP 배터리 (자회사 SK온)');
add('KR','066970','ess','ESS용 LFP 양극재');
add('KR','247540 003670','ess','ESS 셀용 양극재 일부 공급 (주력 전기차)');
add('KR','051910','ess','양극재·LG에너지솔루션 모회사 (주력 전기차)');
add('US','FLNC','ess','ESS 전문 (데이터센터·전력망 저장장치)');
add('US','TSLA','ess','ESS(메가팩) · 전기차 겸업');
add('KR','336260','dcpower','데이터센터 연료전지 발전');
add('KR','009830','dcpower','태양광·빅테크 재생에너지 전력계약');
add('US','CEG TLN VST','nuclear','원전 보유 발전사·빅테크 원전 전력계약');
add('US','GEV','nuclear','SMR·원전 기자재');
add('US','BWXT CW','nuclear','원자로 부품·원전 기자재');
add('US','CCJ','nuclear','우라늄·원전 연료');
add('KR','034020','nuclear','원자로 주기기·SMR');
add('KR','052690','nuclear','원전 설계');
add('KR','015760','nuclear','원전 운영·수출');
add('KR','000720','nuclear','원전 시공');
add('KR','083650 032820','nuclear','원전 보조기기·제어계측');

// Japan (TOPIX 500) and Taiwan: only codes checked against the 2026-10-20 universe lists.
add('JP','8035 6857 6920 7735 6146 6525 6323','hardware','반도체 장비');
add('JP','4063 3436 4186 5016','hardware','웨이퍼·반도체 소재');
add('JP','285A','hardware','메모리(낸드)');
add('JP','6723 6526','hardware','반도체 설계·시스템 반도체');
add('JP','4062 6981 6762 6976 7741','hardware','기판·전자부품·마스크 블랭크');
add('JP','9432 9433 9434','infrastructure','통신·데이터센터');
add('JP','6702 6701 4307','infrastructure software','IT 서비스·기업용 AI');
add('JP','9984','bigtech','AI 투자 지주(ARM·OpenAI 등)');
add('JP','4704','security','기업 보안');
add('JP','5803 5802 5801','optics power','광섬유·광부품·전선');
add('JP','6501 6503 6504','power','전력망·전력기기');
add('JP','1942 1944','power infrastructure','전기·데이터센터 설비 공사');
add('JP','7011 7013 5631','nuclear','원자로·원전 기자재');
add('JP','9501 9503','nuclear power','원전 보유 발전사');
add('JP','6752 6674','ess','ESS·데이터센터용 배터리');
add('TW','2330 2303 3711 2449 6488','hardware','파운드리·패키징·테스트·웨이퍼');
add('TW','2454 3443 3661 5274','hardware','AI 칩 설계·맞춤형 반도체(ASIC)·서버 관리 칩');
add('TW','2408 2344','hardware','메모리');
add('TW','2317 2382 3231 6669 2356 2376','hardware infrastructure','AI 서버 조립');
add('TW','8210 2059','hardware infrastructure','서버 섀시·레일');
add('TW','3037 8046 3189 2368 2383 6274 3044 2313','hardware','기판·PCB·동박적층판');
add('TW','3533 3665','hardware','커넥터·고속 케이블');
add('TW','3017 3324 3653','infrastructure','서버 냉각(수랭·방열)');
add('TW','2308','infrastructure power','데이터센터 전원·냉각');
add('TW','2301','power','서버 전원공급장치');
add('TW','1519 1503 1513','power','변압기·전력기기');
add('TW','6781','ess','서버용 배터리 백업(BBU)');
add('TW','3081 3363 4979','optics','광통신 부품');
add('TW','2345','optics infrastructure','데이터센터 네트워크 스위치');

export function aiMembership(stock) { return catalog.get(stock.id) || null; }
export function aiSearchText(stock) {
  const member=aiMembership(stock);
  return member ? [member.role,...AI_SECTORS.filter(g=>member.sectors.includes(g.id)).map(g=>g.name)].join(' ') : '';
}
const numeric=x=>typeof x==='number'&&Number.isFinite(x);
function summarize(stocks) {
  const values=key=>stocks.map(s=>s[key]).filter(numeric);
  const med=key=>{const a=values(key).sort((a,b)=>a-b),n=a.length;return n?(a[Math.floor((n-1)/2)]+a[Math.floor(n/2)])/2:null;};
  const changeCount=values('changePct').length, advancingCount=stocks.filter(s=>numeric(s.changePct)&&s.changePct>0).length;
  const trend=stocks.filter(s=>typeof s.aboveMa50==='boolean'),trendAbove=trend.filter(s=>s.aboveMa50).length;
  const pairs=trend.filter(s=>typeof s.aboveMa50Ago5==='boolean');
  return {count:stocks.length,changeCount,advancingCount,advancingPct:changeCount?advancingCount/changeCount*100:null,
    trendCount:trend.length,trendAbove,trendPct:trend.length?trendAbove*100/trend.length:null,
    trendPairCount:pairs.length,trendDelta:pairs.length?pairs.reduce((n,s)=>n+Number(s.aboveMa50)-Number(s.aboveMa50Ago5),0)*100/pairs.length:null,
    medianChangePct:med('changePct'),medianReturn5d:med('return5d'),medianReturn20d:med('return20d'),medianReturn60d:med('return60d'),medianReturn120d:med('return120d'),medianReturn200d:med('return200d'),
    return5dCount:values('return5d').length,return20dCount:values('return20d').length,return60dCount:values('return60d').length,return120dCount:values('return120d').length,return200dCount:values('return200d').length,
    nearHighCount:stocks.filter(s=>numeric(s.highDistancePct)&&s.highDistancePct<=10).length,
    eligible52w:values('highDistancePct').length,
    newHighCount:stocks.filter(s=>s.intradayHigh52===true||s.closeHigh52===true).length,
    volumeCount:stocks.filter(s=>numeric(s.volumeRatio)&&s.volumeRatio>=1.5).length};
}
export function aiOverview(market, marketCode) {
  const listed=[...catalog.values()].filter(s=>s.market===marketCode), valid=new Set(market.stocks.map(s=>s.id));
  const stocks=[...new Map(market.stocks.filter(s=>aiMembership(s)&&s.market===marketCode).map(s=>[s.id,s])).values()];
  return {total:{...summarize(stocks),catalogCount:listed.length},
    missing:listed.filter(s=>!valid.has(s.id)),
    groups:AI_SECTORS.map(g=>({...g,...summarize(stocks.filter(s=>aiMembership(s).sectors.includes(g.id))),
      catalogCount:listed.filter(s=>s.sectors.includes(g.id)).length}))};
}
