import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const api = await import('../discovery/model.js').catch(() => ({}));
const NOW = Date.parse('2026-09-12T16:00:00Z');
const technical = {ready:true,asOf:'2026-09-11',stage2:true,aligned:true,rsPercentile:92,ma50:10,ma150:9,ma200:8,checks:{}};
const stock = (country, symbol, extra={}) => ({id:`${country}:${symbol}`,country,market:country==='KR'?'KR-KOSPI':country,symbol,ticker:country==='KR'?`${symbol}.KS`:symbol,name:country==='KR'?'삼성 전자':'Alpha Labs',sector:'catalog sector',asOf:'2026-09-11',currency:country==='KR'?'KRW':'USD',close:12,detailUrl:`${country==='KR'?'index':'us'}.html?stock=${symbol}`,source:'public fixture',technical:{...technical},...extra});
const signal = (country, symbol, extra={}) => ({market:`${country}-${country==='KR'?'KOSPI':'NASDAQ'}`,country:'localized name',symbol,name:country==='KR'?'삼성 전자':'Alpha Labs',as_of:'20260911',currency:country==='KR'?'KRW':'USD',current_close:12,industry_path:['전자','반도체'],classification_caveats:['분류 한계'],flags:{close_63:true,close_252:false,close_ath:null},...extra});
const industry = (country, path=['전자','반도체']) => ({market:country,industry_path:path,current_signal_issuers:1,eligible_classified_issuers:4,phase:'확산',comparison_scope:'분류 종목군'});
const catalog = rows => ({schemaVersion:1,generatedAt:'2026-09-12T15:00:00Z',markets:{},stocks:rows});
const radar = (signals=[signal('KR','005930'),signal('US','ALPHA')],extra={}) => ({schemaVersion:1,checkedAt:'2026-09-12T15:00:00Z',scope:'공개 분류 종목군',editions:{KR:{status:'PASS',signals:signals.filter(s=>s.market.startsWith('KR-')),industries:[industry('KR')]},US:{status:'PASS',signals:signals.filter(s=>s.market.startsWith('US-')),industries:[industry('US')] }},...extra});
function build(c=catalog([stock('KR','005930'),stock('US','ALPHA')]), r=radar(), now=NOW, failed=false) {
  assert.equal(typeof api.buildModel,'function','model buildModel must be implemented');
  return api.buildModel(c,r,now,failed);
}

test('search normalizes case, whitespace, market names and conjunctive tokens',()=>{
  const model=build();
  for (const [query,want] of [[' 미국  alpha ',['US:ALPHA']],['한국 삼성',['KR:005930']],['005930',['KR:005930']],['US labs',['US:ALPHA']],['미국 삼성',[]]]) {
    assert.deepEqual(api.selectStocks(model,{mode:'search',query}).map(x=>x.id),want);
  }
});
test('country identity prevents same-symbol cross-country technical joins and preserves zeroes',()=>{
  const model=build(catalog([stock('US','005930')]),radar([signal('KR','005930')]));
  assert.equal(model.stocks.find(x=>x.id==='KR:005930').technicalCurrent,false);
  assert.equal(model.stocks.find(x=>x.id==='US:005930').signal,null);
  assert.equal(api.selectStocks(model,{mode:'candidates'})[0].symbol,'005930');
});
test('country, exact radar path, signal and query compose',()=>{
  const model=build();
  assert.equal(api.selectStocks(model,{mode:'candidates',query:'미국',country:'KR'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'candidates',country:'KR',industry:JSON.stringify(['전자','반도체']),signal:'close_63'}).length,1);
  assert.equal(api.selectStocks(model,{mode:'candidates',country:'KR',industry:JSON.stringify(['전자']),signal:'close_63'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'candidates',signal:'close_ath'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'search',industry:JSON.stringify(['catalog sector'])}).length,0);
});
test('unknown and mismatched technical dates cannot qualify current Stage 2 filters',()=>{
  const model=build(catalog([stock('KR','005930',{technical:{...technical,ready:false,stage2:null}}),stock('US','ALPHA',{technical:{...technical,asOf:'2026-09-10'}})]));
  assert.equal(api.selectStocks(model,{mode:'candidates',technical:'stage2'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'candidates',technical:'aligned'}).length,0);
  assert.equal(api.selectStocks(build(),{mode:'candidates',technical:'stage2'}).length,2);
});
test('search includes known noncandidates with explicit absent-public-candidate status',()=>{
  const model=build(catalog([stock('KR','005930'),stock('US','OTHER')]),radar([signal('KR','005930')]));
  assert.equal(api.selectStocks(model,{mode:'search',query:'OTHER'})[0].candidateState,'not-listed');
  assert.equal(api.selectStocks(model,{mode:'candidates',query:'OTHER'}).length,0);
});
test('duplicate identities produce one stock while countries remain separate',()=>{
  const model=build(catalog([stock('KR','005930'),stock('KR','005930')]),radar([signal('KR','005930'),signal('KR','005930'),signal('US','005930')]));
  assert.deepEqual(model.stocks.map(x=>x.id).sort(),['KR:005930','US:005930']);
});
test('industry selection respects country and query, ordered by ratio then count',()=>{
  const feed=radar();
  feed.editions.KR.industries.push({...industry('KR',['소프트웨어']),current_signal_issuers:3,eligible_classified_issuers:4});
  const rows=api.selectIndustries(build(undefined,feed),{country:'KR'});
  assert.equal(rows.every(x=>x.country==='KR'),true);
  assert.equal(rows[0].industryKey,JSON.stringify(['소프트웨어']));
  assert.equal(api.selectIndustries(build(),{country:'KR',query:'미국'}).length,0);
});
test('expired, future and invalid checkedAt exclude current records without disabling historical search',()=>{
  for(const stamp of ['2026-09-12T12:59:59Z','2026-09-12T16:00:01Z','bad']) {
    const model=build(undefined,radar(undefined,{checkedAt:stamp}));
    assert.equal(api.selectStocks(model,{mode:'candidates'}).length,0);
    assert.equal(api.selectStocks(model,{mode:'candidates',historical:true}).length,2);
    assert.equal(api.selectStocks(model,{mode:'search',query:'삼성'}).length,1);
    assert.notEqual(model.status.radar,'current');
  }
});
test('FAILED and malformed editions cannot masquerade as verified candidates',()=>{
  const feed=radar(); feed.editions.KR.status='FAILED';
  const model=build(undefined,feed);
  assert.deepEqual(api.selectStocks(model,{mode:'candidates'}).map(x=>x.id),['US:ALPHA']);
  assert.equal(api.selectStocks(model,{mode:'candidates',historical:true}).length,2);
  feed.editions.US.signals={};
  assert.equal(api.selectStocks(build(undefined,feed),{mode:'candidates'}).length,0);
  for(const bad of [{schemaVersion:1,checkedAt:feed.checkedAt,editions:{}},{schemaVersion:1,checkedAt:feed.checkedAt,editions:[]},{schemaVersion:9,editions:{}}]) {
    assert.equal(build(undefined,bad).status.radar,'invalid');
  }
});
test('malformed industry rows invalidate only their owning PASS edition',()=>{
  for(const malformed of [
    {...industry('KR'),current_signal_issuers:5},
    {...industry('KR'),eligible_classified_issuers:'4'},
  ]) {
    const feed=radar(); feed.editions.KR.industries=[malformed];
    const model=build(undefined,feed);
    assert.equal(model.status.editions.KR,'INVALID');
    assert.equal(model.status.editions.US,'PASS');
    assert.deepEqual(api.selectStocks(model,{mode:'candidates'}).map(row=>row.id),['US:ALPHA']);
    assert.deepEqual(api.selectIndustries(model,{}).map(row=>row.country),['US']);
  }
});
test('network failure excludes cached current candidates but preserves catalog searches',()=>{
  const model=build(undefined,undefined,NOW,true);
  assert.equal(model.status.radar,'failed');
  assert.equal(api.selectStocks(model,{mode:'candidates'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'search',query:'ALPHA'}).length,1);
});
test('real public snapshots retain all verified signals without duplicate or cross-country joins',async()=>{
  const c=JSON.parse(await readFile(new URL('../wamo_catalog.json',import.meta.url),'utf8'));
  const r=JSON.parse(await readFile(new URL('../wamo_radar.json',import.meta.url),'utf8'));
  const model=build(c,r,Date.parse(r.checkedAt));
  const rows=api.selectStocks(model,{mode:'candidates'});
  const editions=Object.values(r.editions).filter(edition=>edition.status==='PASS');
  assert.equal(rows.length,editions.flatMap(edition=>edition.signals).length);
  assert.equal(new Set(model.stocks.map(row=>row.id)).size,model.stocks.length);
  for(const row of rows.filter(row=>row.catalog)) {
    assert.equal(row.country,row.catalog.country);
    assert.equal(row.symbol,row.catalog.symbol);
  }
  assert.equal(api.selectIndustries(model,{}).length,editions.flatMap(edition=>edition.industries).length);
});

const storeApi=await import('../discovery/store.js').catch(()=>({}));
function makeStore(options) {
  assert.equal(typeof storeApi.createStore,'function','store createStore must be implemented');
  return storeApi.createStore(options);
}
const response=data=>({ok:true,json:async()=>data});
test('store retains searchable catalog when radar fails and recovers on retry',async()=>{
  let fail=true;
  const store=makeStore({now:()=>NOW,fetcher:async url=>{
    if(url.includes('catalog')) return response(catalog([stock('KR','005930')]));
    if(fail) throw new Error('network');
    return response(radar());
  }});
  await store.refresh();
  assert.equal(store.getState().sources.catalog.status,'ready');
  assert.equal(store.getState().sources.radar.status,'failed');
  assert.equal(api.selectStocks(store.getState().model,{mode:'search'}).length,1);
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,0);
  fail=false; await store.refresh();
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,2);
});
test('store marks cached radar historical on failed revalidation and reevaluates time on reads',async()=>{
  let clock=NOW, fail=false;
  const store=makeStore({now:()=>clock,fetcher:async url=>{
    if(fail) throw new Error('offline');
    return response(url.includes('catalog')?catalog([stock('KR','005930')]):radar());
  }});
  await store.refresh();
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,2);
  clock=Date.parse('2026-09-12T18:00:01Z');
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,0);
  clock=NOW; fail=true; await store.refresh();
  assert.equal(store.getState().model.status.radar,'failed');
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates',historical:true}).length,2);
  assert.equal(api.selectStocks(store.getState().model,{mode:'search',query:'삼성'}).length,1);
});
test('malformed responses preserve cache with invalid source status',async()=>{
  let malformed=false;
  const store=makeStore({now:()=>NOW,fetcher:async url=>response(malformed?{schemaVersion:1}:url.includes('catalog')?catalog([stock('KR','005930')]):radar())});
  await store.refresh(); malformed=true; await store.refresh();
  assert.equal(store.getState().sources.radar.status,'invalid');
  assert.equal(store.getState().sources.catalog.status,'invalid');
  assert.equal(store.getState().model.status.radar,'invalid');
  assert.equal(store.getState().model.status.catalog,'invalid');
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,0);
  assert.equal(api.selectStocks(store.getState().model,{mode:'search',query:'삼성'}).length,1);
});
test('radar remains useful when catalog fails without inventing technical coverage',async()=>{
  const store=makeStore({now:()=>NOW,fetcher:async url=>{
    if(url.includes('catalog')) return {ok:false,status:503};
    return response(radar());
  }});
  await store.refresh();
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,2);
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates',technical:'stage2'}).length,0);
});
test('overlapping refresh requests share one source round and finish without stale overwrites',async()=>{
  const pending=[]; let notifications=0;
  const store=makeStore({now:()=>NOW,onChange:()=>notifications++,fetcher:url=>new Promise(resolve=>pending.push({url,resolve}))});
  const first=store.refresh(), second=store.refresh();
  await Promise.resolve();
  assert.equal(pending.length,2);
  for(const request of pending) request.resolve(response(request.url.includes('catalog')?catalog([]):radar()));
  await Promise.all([first,second]);
  assert.equal(store.getState().loading,false);
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,2);
  assert.ok(notifications>0);
});
test('hung requests time out and a later refresh can recover',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  let hung=true;
  const store=makeStore({now:()=>NOW,fetcher:async url=>hung?new Promise(()=>{}):response(url.includes('catalog')?catalog([]):radar())});
  const refresh=store.refresh();
  t.mock.timers.tick(10001);
  await refresh;
  assert.equal(store.getState().loading,false);
  assert.equal(store.getState().sources.radar.status,'failed');
  hung=false; await store.refresh();
  assert.equal(api.selectStocks(store.getState().model,{mode:'candidates'}).length,2);
});

test('malformed or entirely non-high signal flags do not create verified candidates',()=>{
  for(const flags of [{close_63:'true'},{close_63:false,close_ath:null},{}]) {
    const model=build(undefined,radar([signal('KR','005930',{flags})]));
    assert.equal(api.selectStocks(model,{mode:'candidates'}).length,0);
    assert.equal(model.status.editions.KR,'INVALID');
  }
});
const detailApi=await import('../discovery/detail.js').catch(()=>({}));
const viewApi=await import('../discovery/view.js').catch(()=>({}));
test('empty state distinguishes edition failures from ordinary empty filters',()=>{
  assert.equal(typeof viewApi.emptyStateMessage,'function');
  const allFailed=radar();
  allFailed.editions.US={status:'FAILED'};
  allFailed.editions.KR={status:'FAILED'};
  assert.match(viewApi.emptyStateMessage(build(undefined,allFailed),{mode:'candidates'}),/갱신 실패/);

  const partial=radar(); partial.editions.ASIA={status:'FAILED'}; delete partial.editions.KR;
  assert.match(viewApi.emptyStateMessage(build(undefined,partial),{mode:'candidates',country:'KR'}),/선택한 국가.*갱신 실패/);
  assert.doesNotMatch(viewApi.emptyStateMessage(build(undefined,partial),{mode:'candidates',country:'US',query:'없는종목'}),/갱신 실패|형식 오류/);

  const transportFailed=build(undefined,undefined,NOW,true);
  assert.match(viewApi.emptyStateMessage(transportFailed,{mode:'candidates'}),/연결 확인/);
  assert.doesNotMatch(viewApi.emptyStateMessage(transportFailed,{mode:'candidates'}),/시장 발표/);
});
test('technical detail distinguishes unconnected high observations from real date mismatches',()=>{
  assert.equal(typeof detailApi.technicalObservationLabel,'function');
  const unconnected={technical:{...technical},signal:null,asOf:'2026-09-11',technicalCurrent:false};
  assert.match(detailApi.technicalObservationLabel(unconnected),/신고가 관측과 연결 없음/);
  assert.doesNotMatch(detailApi.technicalObservationLabel(unconnected),/불일치|과거/);
  assert.match(detailApi.technicalObservationLabel({...unconnected,signal:{},asOf:'2026-09-10'}),/기준일 불일치/);
});
test('catalog revalidation failure keeps source reason distinct from stale radar evidence',async()=>{
  let catalogFails=false;
  const store=makeStore({now:()=>NOW,fetcher:async url=>{
    if(url.includes('catalog') && catalogFails) throw new Error('offline');
    return response(url.includes('catalog')?catalog([stock('KR','005930')]):radar());
  }});
  await store.refresh(); catalogFails=true; await store.refresh();
  const state=store.getState(), row=state.model.stocks.find(item=>item.id==='KR:005930');
  assert.equal(state.model.status.catalog,'failed');
  assert.equal(state.model.status.radar,'current');
  assert.equal(row.current,true);
  assert.equal(row.technicalCurrent,false);
  assert.match(detailApi.technicalObservationLabel(row),/카탈로그 연결 실패/);
  assert.doesNotMatch(detailApi.technicalObservationLabel(row),/과거/);
});
test('high-period and independent technical filters compose without unknown or mismatched evidence',()=>{
  const feed=radar([signal('KR','005930',{flags:{close_63:true,close_252:true}}),signal('US','ALPHA',{flags:{close_63:true,close_252:false}})]);
  const model=build(undefined,feed);
  assert.deepEqual(api.selectStocks(model,{mode:'candidates',signal:'close_252',technical:'stage2'}).map(row=>row.id),['KR:005930']);
  const unready=build(catalog([stock('KR','005930',{technical:{...technical,ready:false}}),stock('US','ALPHA')]),feed);
  assert.equal(api.selectStocks(unready,{mode:'candidates',signal:'close_252',technical:'stage2'}).length,0);
  const mismatched=build(catalog([stock('KR','005930',{technical:{...technical,asOf:'2026-09-10'}})]),feed);
  assert.equal(api.selectStocks(mismatched,{mode:'candidates',signal:'close_252',technical:'aligned'}).length,0);
  const lowRS=build(catalog([stock('KR','005930',{technical:{...technical,rsPercentile:79.9}})]),feed);
  assert.equal(api.selectStocks(lowRS,{mode:'candidates',signal:'close_252',technical:'rs80'}).length,0);
  assert.equal(api.selectStocks(model,{mode:'candidates',signal:'close_252',technical:'rs80'}).length,1);
});
test('legacy technical values are not accepted by the high-signal filter',()=>{
  for(const signalFilter of ['stage2','aligned','rs70']) {
    assert.equal(api.selectStocks(build(),{mode:'candidates',signal:signalFilter}).length,0);
  }
});
test('ASIA verification covers Korean known noncandidates and empty FAILED editions stay failed',()=>{
  const feed=radar();feed.editions.ASIA=feed.editions.KR;delete feed.editions.KR;
  const model=build(catalog([stock('KR','001450')]),feed);
  assert.equal(api.selectStocks(model,{mode:'search',query:'001450'})[0].candidateState,'not-listed');
  feed.editions.ASIA={status:'FAILED',error:'source unavailable'};
  const failed=build(catalog([stock('KR','001450')]),feed);
  assert.equal(failed.status.editions.ASIA,'FAILED');
  assert.equal(api.selectStocks(failed,{mode:'search',query:'001450'})[0].candidateState,'unavailable');
});
test('original-stock routing works without any radar data and reports missing symbols',()=>{
  assert.equal(typeof detailApi.handleOriginalRequest,'function','original request routing must be implemented');
  let opened;
  const open=ticker=>{opened=ticker;return ticker==='005930.KS';};
  assert.equal(detailApi.handleOriginalRequest('?stock=005930.KS',open),'opened');
  assert.equal(opened,'005930.KS');
  assert.equal(detailApi.handleOriginalRequest('?stock=UNKNOWN',open),'missing');
  assert.equal(detailApi.handleOriginalRequest('',open),'none');
});
test('original analysis links are limited to catalog-backed local KR/US targets',()=>{
  assert.equal(typeof detailApi.originalDetailUrl,'function','original link validation must be implemented');
  assert.equal(detailApi.originalDetailUrl({catalog:stock('KR','005930')}),'index.html?stock=005930.KS');
  assert.equal(detailApi.originalDetailUrl({catalog:stock('US','ALPHA')}),'us.html?stock=ALPHA');
  assert.equal(detailApi.originalDetailUrl({catalog:null}),null);
  assert.equal(detailApi.originalDetailUrl({catalog:{country:'JP',ticker:'ALPHA',detailUrl:'javascript:alert(1)'}}),null);
  assert.equal(detailApi.originalDetailUrl({catalog:stock('US','ALPHA',{detailUrl:'javascript:alert(1)'})}),'us.html?stock=ALPHA');
});
