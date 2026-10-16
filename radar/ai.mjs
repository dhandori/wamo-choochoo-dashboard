// Maintained thematic watchlist, independent of unreliable vendor industry labels.
// Membership describes business exposure, not AI revenue share or a buy signal.
export const AI_REVIEWED = '2026-10-16';
export const AI_SECTORS = [
  {id:'hardware',name:'AI H/W',description:'연산 반도체 · 메모리 · 서버 · 기판 · 반도체 공급망'},
  {id:'infrastructure',name:'AI 인프라',description:'클라우드 · 데이터센터 · 네트워크 · 냉각'},
  {id:'software',name:'AI S/W',description:'데이터 플랫폼 · 기업용 AI · 개발·업무 소프트웨어'},
  {id:'bigtech',name:'빅테크',description:'AI 플랫폼 · 모델 · 클라우드 투자 주체'},
  {id:'security',name:'보안',description:'엔드포인트 · 네트워크 · ID · 데이터 보안'},
  {id:'optics',name:'광통신',description:'광학 부품 · 트랜시버 · 광섬유 · 연결 반도체'},
  {id:'power',name:'전력',description:'발전 · 전력망 · 전력기기 · 데이터센터 전원'},
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
  return {count:stocks.length,changeCount,advancingCount,advancingPct:changeCount?advancingCount/changeCount*100:null,
    medianChangePct:med('changePct'),medianReturn5d:med('return5d'),medianReturn20d:med('return20d'),medianReturn60d:med('return60d'),
    return5dCount:values('return5d').length,return20dCount:values('return20d').length,return60dCount:values('return60d').length,
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
