import {aiMembership, aiSearchText} from './ai.mjs?v=20261016-1';
export {aiOverview, aiMembership, AI_SECTORS, AI_REVIEWED} from './ai.mjs?v=20261016-1';
export const numeric = x => typeof x === 'number' && Number.isFinite(x);
const day = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10)===value;
const timestamp = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
const count = value => Number.isInteger(value) && value>=0;
const text = value => typeof value==='string' && value.trim().length>0;
const optionalNumber = value => value==null || numeric(value);
export function validSnapshot(x) {
 if (!x || x.schemaVersion!==1 || !timestamp(x.generatedAt) || !x.markets) return false;
 return ['KR','US'].every(k=> {
  const m=x.markets[k];
  if(!m || !['CURRENT','PARTIAL','STALE','UNAVAILABLE'].includes(m.status) || !day(m.expectedSession) || !timestamp(m.nextScheduledFor) || (m.verifiedThrough!=null&&!timestamp(m.verifiedThrough)) || !text(m.source) || !Array.isArray(m.stocks) || !Array.isArray(m.industries) || !Array.isArray(m.movers) || !Array.isArray(m.warnings) || !m.warnings.every(w=>typeof w==='string'))return false;
  if(m.status==='UNAVAILABLE' ? !(m.asOf==null && m.stocks.length===0) : !day(m.asOf))return false;
  const c=m.coverage;
  if(!c || !['total','valid','excluded','eligible52w'].every(key=>count(c[key])) || c.valid!==m.stocks.length || c.valid+c.excluded!==c.total || c.eligible52w>c.valid)return false;
  const ids=new Set(m.stocks.map(s=>s?.id));
  if(ids.size!==m.stocks.length)return false;
  const validStock=s=>s && text(s.id) && text(s.name) && text(s.symbol) && text(s.industry) && s.asOf===m.asOf && numeric(s.close)&&s.close>0 && ['changePct','return5d','return20d','high52','highDistancePct','volumeRatio','rs'].every(key=>optionalNumber(s[key])) && (s.highDistancePct==null||s.highDistancePct>=0) && (s.volumeRatio==null||s.volumeRatio>=0) && ['intradayHigh52','closeHigh52','stage2'].every(key=>s[key]==null||typeof s[key]==='boolean') && (s.sparkline==null||Array.isArray(s.sparkline)&&s.sparkline.every(n=>numeric(n)&&n>0)) && ['catalysts','news'].every(key=>s[key]==null||Array.isArray(s[key])&&s[key].every(a=>a&&text(a.title)&&text(a.url)&&(a.date==null||day(a.date)||(key==='news'&&timestamp(a.date))))) && (s.moverReasons==null||Array.isArray(s.moverReasons)&&s.moverReasons.every(text));
  const validCap=s=>s.marketCap==null||numeric(s.marketCap)&&s.marketCap>0;
  if(!m.stocks.every(s=>validStock(s)&&validCap(s))||!m.movers.every(s=>validStock(s)&&validCap(s)&&ids.has(s.id)))return false;
  const groupIds=new Set();
  return m.industries.every(g=>{
   if(!g||!text(g.id)||groupIds.has(g.id)||!text(g.name)||typeof g.confirmed!=='boolean')return false;
   groupIds.add(g.id);
   if(!['memberCount','nearHighCount','advancingCount','volumeConfirmedCount'].every(key=>count(g[key])) || (g.newHighCount!=null&&!count(g.newHighCount)) || !['nearHighPct','advancingPct','medianReturn5d','medianChangePct'].every(key=>optionalNumber(g[key])))return false;
   if(!Array.isArray(g.members)||!Array.isArray(g.leaders)||new Set(g.members).size!==g.members.length||g.memberCount!==g.members.length||!g.members.every(id=>ids.has(id))||!g.leaders.every(id=>g.members.includes(id)))return false;
   return ['nearHighCount','advancingCount','volumeConfirmedCount','newHighCount'].every(key=>g[key]==null||g[key]<=g.memberCount);
  });
 });
}
export function acceptSnapshot(current, incoming) {
 current=validSnapshot(current)?current:null;
 if (!validSnapshot(incoming)) return current;
 if (!current) return incoming;
 if (Date.parse(incoming.generatedAt)<Date.parse(current.generatedAt)) return current;
 const markets={...incoming.markets};
 for(const k of ['KR','US']) {
  const old=current.markets[k],next=markets[k];
  if ((old.asOf||'')>(next.asOf||'') || (old.stocks.length && next.stocks.length<old.stocks.length*.9)) {
   markets[k]={...old,expectedSession:next.expectedSession,nextScheduledFor:next.nextScheduledFor,status:old.asOf===next.expectedSession?'PARTIAL':'STALE',warnings:[...new Set([...old.warnings,'새 가격 파일의 날짜가 더 오래되거나 유효 종목이 10% 넘게 감소해 이전 검증 가격을 유지합니다.'])]};
   if(next.verifiedThrough)markets[k].verifiedThrough=next.verifiedThrough;else delete markets[k].verifiedThrough;
  }
 }
 return {...incoming,markets};
}
export function selectStocks(m,{tab='near',distance=10,high='either',mode='all',query='',rs=false,stage=false,industry=null,sort='default',aiSector='all',aiSignal='all'}={}) {
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
 const key=sort==='default'?(tab==='near'?'highDistancePct':tab==='movers'?'absolute':'return5d'):sort;
 if(key==='marketCap'||key==='marketCapAsc')return rows.sort((a,b)=>{
  const aValid=numeric(a.marketCap)&&a.marketCap>0,bValid=numeric(b.marketCap)&&b.marketCap>0;
  if(aValid!==bValid)return aValid?-1:1;
  return aValid?(key==='marketCapAsc'?a.marketCap-b.marketCap:b.marketCap-a.marketCap):0;
 });
 rows.sort((a,b)=>key==='name'?a.name.localeCompare(b.name): key==='absolute'?Math.abs(b.changePct||0)-Math.abs(a.changePct||0):key==='highDistancePct'?(a[key]??Infinity)-(b[key]??Infinity):(b[key]??-Infinity)-(a[key]??-Infinity));return rows;
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
