import {aiMembership, aiSearchText} from './ai.mjs?v=20261019-4';
export {aiOverview, aiMembership, AI_SECTORS, AI_REVIEWED} from './ai.mjs?v=20261019-4';
export const numeric = x => typeof x === 'number' && Number.isFinite(x);
const day = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10)===value;
const timestamp = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
const count = value => Number.isInteger(value) && value>=0;
const text = value => typeof value==='string' && value.trim().length>0;
const optionalNumber = value => value==null || numeric(value);
export function hasComparison(m) {
 const c=m.comparison;
 return Boolean(c && day(c.asOf) && c.asOf===c.expectedSession && c.asOf<m.asOf && Array.isArray(c.stocks));
}
const comparisonIndexes=new WeakMap();
export function priorSignal(m,s) {
 if(!hasComparison(m))return null;
 if(!comparisonIndexes.has(m.comparison))comparisonIndexes.set(m.comparison,new Map(m.comparison.stocks.map(row=>[row.id,row])));
 return comparisonIndexes.get(m.comparison).get(s.id)||null;
}
export function signalChanges(s,prior,distance=10) {
 const entered=[],exited=[],comparable=[];
 if(!prior||prior.id!==s.id)return {entered,exited,comparable};
 const criteria=[['near','highDistancePct',v=>numeric(v)&&v>=0,v=>v<=distance],['rs','rs',v=>numeric(v)&&v>=0&&v<=100,v=>v>=80],['stage','stage2',v=>typeof v==='boolean',v=>v]];
 for(const [key,field,valid,passes] of criteria){
  if(!valid(s[field])||!valid(prior[field]))continue;
  comparable.push(key);
  if(passes(s[field])&&!passes(prior[field]))entered.push(key);
  if(!passes(s[field])&&passes(prior[field]))exited.push(key);
 }
 return {entered,exited,comparable};
}
// Korea/US are required. Japan/Taiwan are optional Lite markets: an invalid one is
// dropped so a bad Asia block can never take the Korea/US radar down with it.
export const OPTIONAL_MARKETS=['JP','TW'];
export function validSnapshot(x) {
 if (!x || x.schemaVersion!==1 || !timestamp(x.generatedAt) || !x.markets) return false;
 for(const k of OPTIONAL_MARKETS)if(x.markets[k]!=null&&!validMarket(x.markets[k]))delete x.markets[k];
 return ['KR','US'].every(k=>validMarket(x.markets[k]));
}
function validMarket(m) {
 {
  if(!m || !['CURRENT','PARTIAL','STALE','UNAVAILABLE'].includes(m.status) || !day(m.expectedSession) || !timestamp(m.nextScheduledFor) || (m.verifiedThrough!=null&&!timestamp(m.verifiedThrough)) || !text(m.source) || !Array.isArray(m.stocks) || !Array.isArray(m.industries) || !Array.isArray(m.movers) || !Array.isArray(m.warnings) || !m.warnings.every(w=>typeof w==='string'))return false;
  if(m.status==='UNAVAILABLE' ? !(m.asOf==null && m.stocks.length===0) : !day(m.asOf))return false;
  const c=m.coverage;
  if(!c || !['total','valid','excluded','eligible52w'].every(key=>count(c[key])) || c.valid!==m.stocks.length || c.valid+c.excluded!==c.total || c.eligible52w>c.valid)return false;
  const ids=new Set(m.stocks.map(s=>s?.id));
  if(ids.size!==m.stocks.length)return false;
  const comparison=m.comparison;
  if(comparison!=null && (!comparison || typeof comparison!=='object' || (comparison.expectedSession!=null&&!day(comparison.expectedSession)) || !Array.isArray(comparison.stocks) ||
     (comparison.asOf==null?comparison.stocks.length!==0:!hasComparison(m)) || new Set(comparison.stocks.map(s=>s?.id)).size!==comparison.stocks.length ||
     !comparison.stocks.every(s=>s&&text(s.id)&&['highDistancePct','rs'].every(k=>optionalNumber(s[k]))&&(s.highDistancePct==null||s.highDistancePct>=0)&&(s.rs==null||s.rs>=0&&s.rs<=100)&&(s.stage2==null||typeof s.stage2==='boolean'))))return false;
  const validStock=s=>s && text(s.id) && text(s.name) && text(s.symbol) && text(s.industry) && s.asOf===m.asOf && numeric(s.close)&&s.close>0 && ['changePct','return5d','return20d','return60d','return120d','return200d','high52','highDistancePct','volumeRatio','rs'].every(key=>optionalNumber(s[key])) && (s.highDistancePct==null||s.highDistancePct>=0) && (s.volumeRatio==null||s.volumeRatio>=0) && ['intradayHigh52','closeHigh52','stage2'].every(key=>s[key]==null||typeof s[key]==='boolean') && (s.sparkline==null||Array.isArray(s.sparkline)&&s.sparkline.every(n=>numeric(n)&&n>0)) && ['catalysts','news'].every(key=>s[key]==null||Array.isArray(s[key])&&s[key].every(a=>a&&text(a.title)&&text(a.url)&&(a.date==null||day(a.date)||(key==='news'&&timestamp(a.date))))) && (s.moverReasons==null||Array.isArray(s.moverReasons)&&s.moverReasons.every(text));
  const validCap=s=>s.marketCap==null||numeric(s.marketCap)&&s.marketCap>0;
  const validTrend=s=>['aboveMa50','aboveMa50Ago5'].every(key=>s[key]==null||typeof s[key]==='boolean');
  if(!m.stocks.every(s=>validStock(s)&&validCap(s)&&validTrend(s))||!m.movers.every(s=>validStock(s)&&validCap(s)&&validTrend(s)&&ids.has(s.id)))return false;
  const groupIds=new Set();
  return m.industries.every(g=>{
   if(!g||!text(g.id)||groupIds.has(g.id)||!text(g.name)||typeof g.confirmed!=='boolean')return false;
   groupIds.add(g.id);
   if(!['memberCount','nearHighCount','advancingCount','volumeConfirmedCount'].every(key=>count(g[key])) || (g.newHighCount!=null&&!count(g.newHighCount)) || !['nearHighPct','advancingPct','medianReturn5d','medianChangePct'].every(key=>optionalNumber(g[key])))return false;
   if(!Array.isArray(g.members)||!Array.isArray(g.leaders)||new Set(g.members).size!==g.members.length||g.memberCount!==g.members.length||!g.members.every(id=>ids.has(id))||!g.leaders.every(id=>g.members.includes(id)))return false;
   return ['nearHighCount','advancingCount','volumeConfirmedCount','newHighCount'].every(key=>g[key]==null||g[key]<=g.memberCount);
  });
 }
}
export function acceptSnapshot(current, incoming) {
 current=validSnapshot(current)?current:null;
 if (!validSnapshot(incoming)) return current;
 if (!current) return incoming;
 if (Date.parse(incoming.generatedAt)<Date.parse(current.generatedAt)) return current;
 const markets={...incoming.markets};
 // A Korea/US-only file must not wipe Japan/Taiwan that the page already holds.
 for(const k of OPTIONAL_MARKETS)if(!markets[k]&&current.markets[k])markets[k]=current.markets[k];
 for(const k of Object.keys(markets)) {
  const old=current.markets[k],next=markets[k];
  if(!old||old===next)continue;
  if ((old.asOf||'')>(next.asOf||'') || (old.stocks.length && next.stocks.length<old.stocks.length*.9)) {
   markets[k]={...old,expectedSession:next.expectedSession,nextScheduledFor:next.nextScheduledFor,status:old.asOf===next.expectedSession?'PARTIAL':'STALE',warnings:[...new Set([...old.warnings,'새 가격 파일의 날짜가 더 오래되거나 유효 종목이 10% 넘게 감소해 이전 검증 가격을 유지합니다.'])]};
   if(next.verifiedThrough)markets[k].verifiedThrough=next.verifiedThrough;else delete markets[k].verifiedThrough;
  }
 }
 return {...incoming,markets};
}
// Market-cap floors offered per market (KRW for KR, USD for US). 0 = no floor.
export const CAP_FLOORS={KR:[[0,'전체'],[5e11,'5천억↑'],[1e12,'1조↑'],[5e12,'5조↑'],[1e13,'10조↑']],
 JP:[[0,'전체'],[5e11,'5천억엔↑'],[1e12,'1조엔↑'],[5e12,'5조엔↑'],[1e13,'10조엔↑']],
 TW:[[0,'전체'],[3e10,'300억↑'],[1e11,'1천억↑'],[5e11,'5천억↑'],[2e12,'2조↑']],US:[[0,'전체'],[2e9,'$20억↑'],[1e10,'$100억↑'],[5e10,'$500억↑'],[2e11,'$2천억↑']]};
// Sort keys: [label shown in the menu, direction]. 'asc' means smaller first.
export const SORT_KEYS={marketCap:['시총 큰 순','desc'],marketCapAsc:['시총 작은 순','asc'],rs:['RS 높은 순','desc'],highDistancePct:['신고가에 가까운 순','asc'],changePct:['당일 상승률 높은 순','desc'],absolute:['당일 변동 큰 순','desc'],return5d:['5일 수익률 높은 순','desc'],return20d:['20일 수익률 높은 순','desc'],return60d:['60일 수익률 높은 순','desc'],return120d:['120일 수익률 높은 순','desc'],return200d:['200일 수익률 높은 순','desc'],volumeRatio:['거래량 배수 큰 순','desc'],name:['종목명 순','asc']};
// Compare one key; missing values always go last, whatever the direction.
function compareBy(key){
 const value=s=>key==='marketCap'||key==='marketCapAsc'?(numeric(s.marketCap)&&s.marketCap>0?s.marketCap:null):key==='absolute'?(numeric(s.changePct)?Math.abs(s.changePct):null):numeric(s[key])?s[key]:null;
 if(key==='name')return (a,b)=>a.name.localeCompare(b.name,'ko');
 const asc=SORT_KEYS[key]?.[1]==='asc';
 return (a,b)=>{const x=value(a),y=value(b);if(x==null||y==null)return x==null&&y==null?0:x==null?1:-1;return asc?x-y:y-x;};
}
export function selectStocks(m,{tab='near',distance=10,high='either',mode='all',query='',rs=false,stage=false,industry=null,sort='default',sort2='none',minCap=0,aiSector='all',aiSignal='all',aiPeriod='5d',newOnly=false}={}) {
 let rows=m.stocks.filter(s=> (!query||[s.name,s.symbol,s.industry,tab==='ai'?aiSearchText(s):''].join(' ').toLowerCase().includes(query.toLowerCase())) && (!rs||s.rs>=80)&&(!stage||s.stage2===true));
 if(tab==='ai') {
  rows=rows.filter(s=>{const member=aiMembership(s);return member&&(aiSector==='all'||member.sectors.includes(aiSector));});
  if(aiSignal==='near')rows=rows.filter(s=>numeric(s.highDistancePct)&&s.highDistancePct<=10);
  if(aiSignal==='high')rows=rows.filter(s=>s.intradayHigh52===true||s.closeHigh52===true);
  if(aiSignal==='up')rows=rows.filter(s=>numeric(s.changePct)&&s.changePct>=3);
  if(aiSignal==='down')rows=rows.filter(s=>numeric(s.changePct)&&s.changePct<=-3);
  if(aiSignal==='volume')rows=rows.filter(s=>numeric(s.volumeRatio)&&s.volumeRatio>=1.5);
 }
 if(tab==='near') rows=rows.filter(s=>numeric(s.highDistancePct)&&s.highDistancePct<=distance);
 if(tab==='high') rows=rows.filter(s=>high==='intraday'?s.intradayHigh52===true:high==='close'?s.closeHigh52===true:s.intradayHigh52===true||s.closeHigh52===true);
 if(tab==='movers') rows=rows.filter(s=>mode==='up'?s.changePct>=3:mode==='down'?s.changePct<=-3:mode==='volume'?s.volumeRatio>=1.5:Math.abs(s.changePct)>=3||s.volumeRatio>=1.5);
 if(industry) rows=rows.filter(s=>industry.members.includes(s.id));
 if(newOnly)rows=rows.filter(s=>signalChanges(s,priorSignal(m,s),tab==='near'?distance:10).entered.length>0);
 // Cap floor: stocks without a known market cap cannot prove they pass, so they drop out.
 if(minCap>0)rows=rows.filter(s=>numeric(s.marketCap)&&s.marketCap>=minCap);
 const periodKey=aiPeriod==='1d'?'changePct':['5d','20d','60d','120d','200d'].includes(aiPeriod)?`return${aiPeriod}`:'return5d';
 const key=sort==='default'?(tab==='near'?'highDistancePct':tab==='movers'?'absolute':tab==='ai'?periodKey:'return5d'):sort;
 const first=compareBy(key),second=sort2&&sort2!=='none'&&sort2!==key?compareBy(sort2):null;
 return rows.sort((a,b)=>first(a,b)||(second?second(a,b):0));
}
export function marketStatus(m,now=Date.now()) {const deadline=Date.parse(m.verifiedThrough||m.nextScheduledFor);return m.status!=='UNAVAILABLE'&&Number.isFinite(deadline)&&now>deadline+90*60000?'STALE':m.status;}
export async function refreshSnapshot(current,{fetcher=fetch,timeout=6500}={}) {
 const controller=new AbortController();let timer;
 try {const result=await Promise.race([fetcher('wamo_insights.json',{cache:'no-store',signal:controller.signal}).then(async r=>{if(!r.ok)throw Error('HTTP');return r.json();}),new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('timeout'));},Math.min(timeout,8000));})]);const next=acceptSnapshot(current,result);return {snapshot:next,failed:!validSnapshot(result)};}catch{return {snapshot:current,failed:true};}finally{clearTimeout(timer);}
}
export function safeUrl(value) {if(typeof value!=='string'||!value.trim())return null;try {const u=new URL(value, 'https://wamo.invalid/');return ['http:','https:'].includes(u.protocol)?value:null;}catch{return null;}}

export function createRefresher(getCurrent, apply, options={}) {
 let inflight=null;
 return () => {
  if(inflight)return inflight;
  inflight=refreshSnapshot(getCurrent(),options).then(result=>{apply(result);return result;}).finally(()=>{inflight=null;});
  return inflight;
 };
}
