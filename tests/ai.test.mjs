import test from 'node:test';
import assert from 'node:assert/strict';
import * as model from '../radar/model.mjs';

const stock=(symbol,patch={})=>({id:`US:${symbol}`,market:'US',symbol,name:symbol,industry:'Unreliable source industry',changePct:1,return5d:2,return20d:3,highDistancePct:4,marketCap:100,...patch});
const m={stocks:[stock('NVDA',{changePct:-5,highDistancePct:40}),stock('MSFT'),stock('AVGO'),stock('KO',{industry:'AI software'})]};

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
