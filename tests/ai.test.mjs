import test from 'node:test';
import assert from 'node:assert/strict';
import * as model from '../radar/model.mjs';

const stock=(symbol,patch={})=>({id:`US:${symbol}`,market:'US',symbol,name:symbol,industry:'Unreliable source industry',changePct:1,return5d:2,return20d:3,highDistancePct:4,marketCap:100,...patch});
const m={stocks:[stock('NVDA',{changePct:-5,highDistancePct:40}),stock('MSFT'),stock('AVGO'),stock('KO',{industry:'AI software'})]};

test('trend breadth uses a paired cohort for its five-session change',()=>{
 const stocks=[stock('NVDA',{aboveMa50:true,aboveMa50Ago5:false}),stock('AVGO',{aboveMa50:false,aboveMa50Ago5:false}),stock('MSFT',{aboveMa50:true,aboveMa50Ago5:null}),stock('AMD',{aboveMa50:null,aboveMa50Ago5:true})];
 const a=model.aiOverview({stocks:[...stocks,stocks[0]]},'US');
 assert.equal(a.total.trendCount,3);
 assert.equal(a.total.trendAbove,2);
 assert.equal(a.total.trendPct,200/3);
 assert.equal(a.total.trendPairCount,2);
 assert.equal(a.total.trendDelta,50);
 assert.equal(a.groups.find(g=>g.id==='optics').trendPct,0);
 assert.equal(a.groups.find(g=>g.id==='bigtech').trendDelta,null);
 assert.equal(a.groups.find(g=>g.id==='security').trendPct,null);
});

test('transitions require actual prior values, respect the near boundary and ignore missing names',()=>{
 const current=stock('NVDA',{highDistancePct:5,rs:80,stage2:true});
 const prior={id:current.id,highDistancePct:10.1,rs:79.9,stage2:false};
 assert.deepEqual(model.signalChanges(current,prior,10).entered,['near','rs','stage']);
 assert.deepEqual(model.signalChanges(current,{...prior,highDistancePct:8},5).entered,['near','rs','stage']);
 assert.deepEqual(model.signalChanges(current,{...prior,highDistancePct:10},10).entered,['rs','stage']);
 assert.deepEqual(model.signalChanges(current,null).entered,[]);
 assert.deepEqual(model.signalChanges(current,{...prior,highDistancePct:null,rs:null,stage2:null}).entered,[]);
 assert.deepEqual(model.signalChanges(prior,current).exited,['near','rs','stage']);
});

test('new-only filter intersects current filters and rejects a stale comparison',()=>{
 const stocks=[stock('NVDA',{rs:81}),stock('MSFT',{rs:90}),stock('AVGO',{rs:99})];
 const comparison={asOf:'2026-10-15',expectedSession:'2026-10-15',stocks:[{id:'US:NVDA',rs:79},{id:'US:MSFT',rs:90}]};
 const market={asOf:'2026-10-16',stocks,comparison};
 assert.deepEqual(model.selectStocks(market,{tab:'ai',newOnly:true}).map(s=>s.symbol),['NVDA']);
 assert.deepEqual(model.selectStocks(market,{tab:'ai',newOnly:true,query:'MSFT'}),[]);
 assert.deepEqual(model.selectStocks({...market,comparison:{...comparison,asOf:'2026-10-14'}},{tab:'ai',newOnly:true}),[]);
});

test('AI selection uses explicit identifiers and includes declining names away from highs',()=>{
 assert.deepEqual(model.selectStocks(m,{tab:'ai'}).map(s=>s.symbol).sort(),['AVGO','MSFT','NVDA']);
 assert.deepEqual(model.selectStocks(m,{tab:'ai',aiSignal:'down'}).map(s=>s.symbol),['NVDA']);
 assert.deepEqual(model.selectStocks(m,{tab:'ai',aiSignal:'near'}).map(s=>s.symbol).sort(),['AVGO','MSFT']);
 assert.deepEqual(model.selectStocks(m,{tab:'ai',aiSector:'optics'}).map(s=>s.symbol),['AVGO']);
 assert.deepEqual(model.selectStocks(m,{tab:'ai',query:'GPU'}).map(s=>s.symbol),['NVDA']);
 assert.deepEqual(model.selectStocks(m,{tab:'ai',aiSector:'missing'}),[]);
});

test('AI breadth excludes missing observations and overlap does not duplicate totals',()=>{
 const market={stocks:[stock('NVDA',{changePct:4,return5d:8,highDistancePct:3,intradayHigh52:true}),stock('AVGO',{changePct:-2,return5d:null,highDistancePct:null,closeHigh52:true}),stock('MSFT',{changePct:null,return5d:2,highDistancePct:20})]};
 const a=model.aiOverview(market,'US');
 assert.equal(a.total.count,3);
 assert.equal(a.total.changeCount,2);
 assert.equal(a.total.advancingPct,50);
 assert.equal(a.total.medianChangePct,1);
 assert.equal(a.total.medianReturn5d,5);
 assert.equal(a.total.nearHighCount,1);
 assert.equal(a.total.eligible52w,2);
 assert.equal(a.total.newHighCount,2);
 assert.equal(a.groups.find(g=>g.id==='hardware').count,2);
 assert.equal(a.groups.find(g=>g.id==='optics').count,1);
 assert.equal(a.groups.find(g=>g.id==='security').count,0);
 assert.equal(a.groups.find(g=>g.id==='security').medianChangePct,null);
 assert.equal(a.total.catalogCount,a.total.count+a.missing.length);
});

test('AI classifications do not inherit erroneous Korean industries or duplicate share classes',()=>{
 const korean={stocks:[stock('005930',{market:'KR',id:'KR:005930'}),stock('003230',{market:'KR',id:'KR:003230',industry:'메모리 반도체'}),stock('010120',{market:'KR',id:'KR:010120',industry:'반도체 장비'})]};
 assert.deepEqual(model.selectStocks(korean,{tab:'ai'}).map(s=>s.symbol).sort(),['005930','010120']);
 assert.deepEqual(model.selectStocks(korean,{tab:'ai',aiSector:'power'}).map(s=>s.symbol),['010120']);
 assert.deepEqual(model.selectStocks({stocks:[stock('GOOG'),stock('GOOGL')]},{tab:'ai'}).map(s=>s.symbol),['GOOGL']);
});

test('60-day sector medians exclude unavailable values and count overlapping names once',()=>{
 const stocks=[stock('NVDA',{return60d:20}),stock('AVGO',{return60d:-10}),stock('MSFT',{return60d:null}),stock('AMD'),stock('MU',{return60d:NaN})];
 const a=model.aiOverview({stocks},'US');
 assert.equal(a.total.medianReturn60d,5);
 assert.equal(a.total.return60dCount,2);
 assert.equal(a.groups.find(g=>g.id==='hardware').medianReturn60d,5);
 assert.equal(a.groups.find(g=>g.id==='optics').medianReturn60d,-10);
 assert.equal(a.groups.find(g=>g.id==='bigtech').medianReturn60d,null);
 assert.equal(a.groups.find(g=>g.id==='bigtech').return60dCount,0);
 assert.deepEqual(model.selectStocks({stocks:stocks.slice(0,4)},{tab:'ai',sort:'return60d'}).map(s=>s.symbol),['NVDA','AVGO','MSFT','AMD']);
});

test('long-period comparison uses valid medians and selected-period default ranking',()=>{
 for(const days of [120,200]){
  const stocks=[stock('NVDA',{[`return${days}d`]:-10}),stock('AVGO',{[`return${days}d`]:30}),stock('MSFT',{[`return${days}d`]:null})];
  const a=model.aiOverview({stocks},'US');
  assert.equal(a.total[`medianReturn${days}d`],10);
  assert.equal(a.total[`return${days}dCount`],2);
  assert.equal(a.groups.find(g=>g.id==='bigtech')[`medianReturn${days}d`],null);
  assert.deepEqual(model.selectStocks({stocks},{tab:'ai',aiPeriod:`${days}d`}).map(s=>s.symbol),['AVGO','NVDA','MSFT']);
  assert.deepEqual(model.selectStocks({stocks},{tab:'ai',aiPeriod:`${days}d`,sort:'name'}).map(s=>s.symbol),['AVGO','MSFT','NVDA']);
 }
});
