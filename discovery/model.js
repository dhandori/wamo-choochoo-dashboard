export const CONNECTION_TTL = 3 * 60 * 60 * 1000;
export const COUNTRIES = {KR:'한국',US:'미국',JP:'일본',CN:'중국',HK:'홍콩'};
export const FLAGS = {intraday_63:'장중 3개월',intraday_126:'장중 6개월',intraday_252:'장중 52주',intraday_ath:'장중 역사적',close_63:'종가 3개월',close_126:'종가 6개월',close_252:'종가 52주',close_ath:'종가 역사적'};
const aliases = {KR:'korea south korea 대한민국 코스피 코스닥 kospi kosdaq',US:'usa united states america 미국 나스닥 뉴욕 nasdaq nyse amex',JP:'japan 일본 도쿄',CN:'china 중국 상하이 선전',HK:'hong kong hongkong 홍콩'};
const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const text = x => typeof x === 'string' ? x.trim() : '';
const normalized = x => text(x).normalize('NFKC').toLocaleLowerCase();
const pathOf = x => Array.isArray(x) && x.every(v=>typeof v==='string') ? x : [];
const finite = x => typeof x === 'number' && Number.isFinite(x);
const sourceOverride = (value,name) => {
  if(typeof value==='boolean') return name==='radar' && value?'failed':null;
  const status=value?.[name];
  return status===true?'failed':['failed','invalid'].includes(status)?status:null;
};
export const industryKey = path => JSON.stringify(pathOf(path));
export function normalizeDate(value) {
  const raw=text(value).replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return null;
  const parsed=new Date(raw+'T00:00:00Z');
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0,10)===raw ? raw : null;
}
const identity = (country,symbol) => COUNTRIES[country] && text(symbol) ? `${country}:${text(symbol).toUpperCase()}` : null;
const signalCountry = s => text(s?.market).split('-')[0];
const validSignal = s => record(s) && identity(signalCountry(s),s.symbol) && normalizeDate(s.as_of) && record(s.flags) && Object.entries(s.flags).every(([key,value])=>key in FLAGS && (value===true || value===false || value===null)) && Object.values(s.flags).some(value=>value===true);
export const validCatalog = c => record(c) && c.schemaVersion===1 && Array.isArray(c.stocks) && c.stocks.every(s=>record(s) && identity(s.country,s.symbol));
export const validRadar = r => record(r) && r.schemaVersion===1 && record(r.editions) && Object.keys(r.editions).length>0;
const validIndustry = g => record(g) && COUNTRIES[g.market] && pathOf(g.industry_path).length>0 && g.industry_path.every(value=>text(value)) && Number.isInteger(g.current_signal_issuers) && Number.isInteger(g.eligible_classified_issuers) && g.current_signal_issuers>=0 && g.eligible_classified_issuers>0 && g.current_signal_issuers<=g.eligible_classified_issuers;
const validEdition = e => record(e) && (e.status==='FAILED' || e.status==='PASS' && Array.isArray(e.signals) && e.signals.every(validSignal) && Array.isArray(e.industries) && e.industries.every(validIndustry));
function searchText(row) {
  return normalized([row.country,COUNTRIES[row.country],aliases[row.country],row.market,row.symbol,row.ticker,row.name,...(row.industryPath||[]),row.catalog?.sector].filter(Boolean).join(' '));
}
function matches(row, filters) {
  if(filters.country && row.country!==filters.country) return false;
  if(filters.industry && row.industryKey!==filters.industry) return false;
  return normalized(filters.query).split(/\s+/).filter(Boolean).every(token=>row.searchText.includes(token));
}

/** Join only public identities; signal evidence and WAMO technical evidence stay separate. */
export function buildModel(catalog,radar,now=Date.now(),connectionFailed=false) {
  const catalogValid=validCatalog(catalog), radarValid=validRadar(radar);
  const checked=radarValid?Date.parse(radar.checkedAt):NaN;
  const fresh=Number.isFinite(checked) && Number.isFinite(Number(now)) && Number(now)>=checked && Number(now)-checked<=CONNECTION_TTL;
  const catalogStatus=sourceOverride(connectionFailed,'catalog')||(catalogValid?'ready':catalog?'invalid':'missing');
  const radarStatus=sourceOverride(connectionFailed,'radar')||(!radarValid?(radar?'invalid':'missing'):fresh?'current':'stale');
  const status={catalog:catalogStatus,radar:radarStatus,checkedAt:text(radar?.checkedAt),scope:text(radar?.scope),editions:{}};
  const stocks=new Map(), industries=new Map();
  if(catalogValid) for(const c of catalog.stocks) {
    const id=identity(c.country,c.symbol);
    if(stocks.has(id)) continue;
    stocks.set(id,{id,country:c.country,symbol:text(c.symbol).toUpperCase(),ticker:text(c.ticker),name:text(c.name),market:text(c.market),asOf:normalizeDate(c.asOf),currency:text(c.currency),close:finite(c.close)?c.close:null,catalog:c,technical:record(c.technical)?c.technical:null,technicalSourceStatus:status.catalog,signal:null,industryPath:[],industryKey:'',current:false,technicalCurrent:false});
  }
  if(radarValid) for(const [editionName,edition] of Object.entries(radar.editions)) {
    const valid=validEdition(edition);
    status.editions[editionName]=edition?.status==='FAILED'?'FAILED':valid?edition.status:'INVALID';
    const current=status.radar==='current' && valid && edition.status==='PASS';
    const signals=valid && Array.isArray(edition?.signals)?edition.signals.filter(validSignal):[];
    for(const s of signals) {
      const country=signalCountry(s), id=identity(country,s.symbol), existing=stocks.get(id);
      if(existing?.signal && (existing.current || !current)) continue;
      const path=pathOf(s.industry_path);
      const row={...existing,id,country,symbol:text(s.symbol).toUpperCase(),ticker:existing?.ticker||text(s.symbol),name:text(s.name)||existing?.name||text(s.symbol),market:text(s.market),asOf:normalizeDate(s.as_of),currency:text(s.currency),close:finite(s.current_close)?s.current_close:null,catalog:existing?.catalog||null,technical:existing?.technical||null,technicalSourceStatus:status.catalog,signal:s,industryPath:path,industryKey:industryKey(path),current,technicalCurrent:false,edition:editionName};
      const t=row.technical;
      row.technicalCurrent=Boolean(current && status.catalog==='ready' && t?.ready===true && normalizeDate(t.asOf)===row.asOf && typeof t.stage2==='boolean' && typeof t.aligned==='boolean' && finite(t.rsPercentile));
      stocks.set(id,row);
    }
    for(const g of valid && Array.isArray(edition?.industries)?edition.industries:[]) {
      if(!validIndustry(g)) continue;
      const path=pathOf(g.industry_path), key=industryKey(path), id=`${g.market}:${key}`;
      if(industries.has(id) && (industries.get(id).current || !current)) continue;
      const row={...g,id,country:g.market,industryPath:path,industryKey:key,current,ratio:g.current_signal_issuers/g.eligible_classified_issuers,edition:editionName};
      row.searchText=searchText(row); industries.set(id,row);
    }
  }
  if(status.radar==='current' && Object.values(status.editions).every(s=>s==='INVALID')) status.radar='invalid';
  for(const row of stocks.values()) {
    const editionStatus=status.editions[row.country] ?? status.editions[row.country==='US'?'US':'ASIA'];
    row.candidateState=row.current?'current':row.signal?'historical':status.radar==='current' && editionStatus==='PASS'?'not-listed':'unavailable';
    row.searchText=searchText(row);
  }
  return {stocks:[...stocks.values()],industries:[...industries.values()],status};
}

export function selectStocks(model,filters={}) {
  const rows=model.stocks.filter(row=>{
    if(filters.mode!=='search' && !row.current && !(filters.historical && row.signal)) return false;
    if(!matches(row,filters)) return false;
    if(filters.technical) {
      const t=row.technical;
      if(!row.technicalCurrent) return false;
      if(filters.technical==='stage2' ? t.stage2!==true : filters.technical==='aligned' ? t.aligned!==true : filters.technical==='rs80' ? t.rsPercentile<80 : true) return false;
    }
    if(filters.signal) return Boolean((row.current || filters.historical) && row.signal?.flags[filters.signal]===true);
    return true;
  });
  return rows.sort((a,b)=> filters.sort==='name'?a.name.localeCompare(b.name,'ko')||a.id.localeCompare(b.id):filters.sort==='symbol'?a.symbol.localeCompare(b.symbol)||a.id.localeCompare(b.id):Number(b.current)-Number(a.current)||(b.asOf||'').localeCompare(a.asOf||'')||a.id.localeCompare(b.id));
}
export function selectIndustries(model,filters={}) {
  return model.industries.filter(row=>(row.current||filters.historical) && matches(row,filters)).sort((a,b)=>b.ratio-a.ratio||b.current_signal_issuers-a.current_signal_issuers||a.id.localeCompare(b.id));
}
