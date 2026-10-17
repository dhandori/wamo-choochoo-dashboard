import test from 'node:test';import assert from 'node:assert/strict';import {selectStocks,acceptSnapshot,validSnapshot,refreshSnapshot,marketStatus,safeUrl,createRefresher} from '../radar/model.mjs';
const stock=(id,patch={})=>({id,name:id,symbol:id,industry:'Test',asOf:'2026-10-13',close:100,changePct:1,return5d:2,highDistancePct:5,volumeRatio:1,rs:80,...patch});const m={asOf:'2026-10-13',status:'CURRENT',nextScheduledFor:'2026-10-14T00:00:00Z',stocks:[stock('near'),stock('high',{intradayHigh52:true,highDistancePct:0}),stock('fall',{changePct:-8,highDistancePct:40,volumeRatio:2})],industries:[],warnings:[],expectedSession:'2026-10-13',source:'verified',movers:[],coverage:{total:3,valid:3,excluded:0,eligible52w:3}};const snap={schemaVersion:1,generatedAt:'2026-10-14T00:00:00Z',markets:{KR:m,US:m}};
test('comparison validation keeps legacy snapshots valid and rejects invalid baseline dates or values',()=>{
 const valid={asOf:'2026-10-12',expectedSession:'2026-10-12',stocks:[{id:'near',rs:79,stage2:false,highDistancePct:15}]};
 const withComparison=comparison=>({...snap,markets:{...snap.markets,US:{...m,comparison}}});
 assert.equal(validSnapshot(withComparison(valid)),true);
 assert.equal(validSnapshot(withComparison({asOf:null,expectedSession:'2026-10-12',stocks:[]})),true);
 for(const comparison of [{...valid,asOf:'2026-10-13'},{...valid,asOf:'2026-10-09'},{...valid,stocks:[valid.stocks[0],valid.stocks[0]]},{...valid,stocks:[{id:'near',rs:'79'}]},{...valid,stocks:[{id:'near',stage2:0}]}])assert.equal(validSnapshot(withComparison(comparison)),false);
 for(const key of ['aboveMa50','aboveMa50Ago5']){
  const candidate=value=>({...snap,markets:{...snap.markets,US:{...m,stocks:m.stocks.map(s=>({...s,[key]:value}))}}});
  for(const value of [null,undefined,true,false])assert.equal(validSnapshot(candidate(value)),true);
  for(const value of ['true',1,NaN])assert.equal(validSnapshot(candidate(value)),false);
 }
});
test('long returns are optional for old snapshots and reject malformed values',()=>{
 for(const key of ['return60d','return120d','return200d']){
  for(const value of [undefined,null,0,-25,125])assert.equal(validSnapshot({...snap,markets:{...snap.markets,KR:{...m,stocks:m.stocks.map(s=>({...s,[key]:value}))}}}),true);
  for(const value of [NaN,Infinity,true,'20'])assert.equal(validSnapshot({...snap,markets:{...snap.markets,KR:{...m,stocks:m.stocks.map(s=>({...s,[key]:value}))}}}),false);
 }
});
test('market cap sorts actual values in both directions with missing values last',()=>{
 const stocks=[stock('missing',{marketCap:null}),stock('large',{marketCap:1e12}),stock('old-schema'),stock('small',{marketCap:9e9}),stock('medium',{marketCap:1e10})];
 assert.deepEqual(selectStocks({...m,stocks},{sort:'marketCap'}).map(s=>s.id),['large','medium','small','missing','old-schema']);
 assert.deepEqual(selectStocks({...m,stocks},{sort:'marketCapAsc'}).map(s=>s.id),['small','medium','large','missing','old-schema']);
 assert.deepEqual(selectStocks({...m,stocks},{sort:'marketCap',query:'small'}).map(s=>s.id),['small']);
 assert.equal(stocks[0].id,'missing');
});
test('market cap remains optional for older snapshots but must be positive if supplied',()=>{
 for(const cap of [null,undefined,1e12])assert.equal(validSnapshot({...snap,markets:{...snap.markets,KR:{...m,stocks:m.stocks.map(s=>({...s,marketCap:cap}))}}}),true);
 for(const cap of [0,-1,NaN,Infinity,true,'1000'])assert.equal(validSnapshot({...snap,markets:{...snap.markets,KR:{...m,stocks:m.stocks.map(s=>({...s,marketCap:cap}))}}}),false);
});
test('near boundary is distinct from actual new highs',()=>{assert.equal(selectStocks(m).length,2);assert.deepEqual(selectStocks(m,{tab:'high'}).map(s=>s.id),['high']);assert.equal(selectStocks({...m,stocks:[stock('boundary',{highDistancePct:10}),stock('outside',{highDistancePct:10.01})]}).length,1);});
test('downside and volume movers outside near highs remain visible',()=>{assert.equal(selectStocks(m,{tab:'movers',mode:'down'})[0].id,'fall');assert.equal(selectStocks(m,{tab:'movers',mode:'volume'})[0].id,'fall');});
test('malformed data and older responses retain verified content',()=>{assert.equal(acceptSnapshot(snap,{}),snap);assert.equal(acceptSnapshot(snap,{...snap,generatedAt:'2026-10-12T00:00:00Z'}),snap);assert.equal(validSnapshot({...snap,markets:{KR:{...m,stocks:[stock('bad',{close:NaN})]},US:m}}),false);const old={...snap,generatedAt:'2026-10-15T00:00:00Z',markets:{KR:{...m,asOf:'2026-10-12',stocks:[stock('old',{asOf:'2026-10-12'})],coverage:{total:1,valid:1,excluded:0,eligible52w:1}},US:m}};assert.equal(acceptSnapshot(snap,old).markets.KR.asOf,m.asOf);});
test('failed and malformed refresh never clear current snapshot',async()=>{assert.deepEqual(await refreshSnapshot(snap,{fetcher:async()=>{throw Error('offline');}}),{snapshot:snap,failed:true});assert.equal((await refreshSnapshot(snap,{fetcher:async()=>({ok:true,json:async()=>({})})})).snapshot,snap);});
test('bounded timeout preserves fallback',async()=>{const r=await refreshSnapshot(snap,{fetcher:()=>new Promise(()=>{}),timeout:10});assert.equal(r.snapshot,snap);assert.equal(r.failed,true);});
test('deadline allows scheduler delay and rejects unsafe URLs',()=>{assert.equal(marketStatus(m,Date.parse('2026-10-14T01:00:00Z')),'CURRENT');assert.equal(marketStatus(m,Date.parse('2026-10-14T02:00:00Z')),'STALE');assert.equal(safeUrl('javascript:alert(1)'),null);assert.equal(safeUrl('us.html?stock=A&legacy=1'),'us.html?stock=A&legacy=1');});

test('malformed industry, coverage, duplicate IDs and invalid current are rejected',()=>{for(const patch of [{industries:[{id:'g',name:'bad',memberCount:1,members:['missing']}]},{coverage:{total:3,valid:2,excluded:1,eligible52w:2}},{stocks:[m.stocks[0],m.stocks[0],m.stocks[2]]},{asOf:'2026-02-30'},{movers:[{id:'missing'}]}]){assert.equal(acceptSnapshot(snap,{...snap,markets:{KR:{...m,...patch},US:m}}),snap);}assert.equal(acceptSnapshot({},snap),snap);});
test('major coverage loss retains old quotes and recalculates freshness',()=>{const update={...snap,generatedAt:'2026-10-15T00:00:00Z',markets:{KR:{...m,expectedSession:'2026-10-14',stocks:m.stocks.slice(0,2),coverage:{total:3,valid:2,excluded:1,eligible52w:2}},US:m}};const retained=acceptSnapshot(snap,update).markets.KR;assert.equal(retained.stocks.length,3);assert.equal(retained.status,'STALE');assert.equal(retained.expectedSession,'2026-10-14');assert.match(retained.warnings.at(-1),/10%/);});

test('news allows timestamps while official catalysts use trading dates',()=>{const stocks=m.stocks.map(s=>({...s,news:[{title:'Headline',url:'https://example.com/news',date:'2026-10-14T03:12:00+00:00'}]}));const withNews={...snap,markets:{KR:{...m,stocks},US:m}};assert.equal(validSnapshot(withNews),true);stocks[0].catalysts=[{title:'Official',url:'https://sec.gov/a',date:'2026-10-14T03:12:00+00:00'}];assert.equal(validSnapshot(withNews),false);});
test('refresh deduplicates inflight, remains bounded and allows offline retry',async()=>{let calls=0,current=snap;const refresh=createRefresher(()=>current,r=>{current=r.snapshot;},{timeout:10,fetcher:()=>{calls++;return new Promise(()=>{});}});const first=refresh();assert.equal(refresh(),first);await first;assert.equal(calls,1);assert.equal(current,snap);await refresh();assert.equal(calls,2);assert.equal(current,snap);});
