import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../radar/runner.js',import.meta.url),'utf8').replace(/^export /gm,'');
const at=Date.parse('2026-10-18T06:00:00Z');
const run=(id=42,extra={})=>({id,display_title:'WAMO · KR · 게시',created_at:'2026-10-18T06:00:05Z',status:'completed',conclusion:'success',html_url:`https://github.com/dhandori/wamo-choochoo-dashboard/actions/runs/${id}`,...extra});
const status=(extra={})=>({status:'PASS',attemptedAt:'2026-10-18T06:00:10Z',lastSuccessAt:'2026-10-18T06:01:00Z',asOf:'2026-10-16',freshness:{expectedSession:'2026-10-16',currentCount:216},count:216,runUrl:run().html_url,...extra});
async function poll({pending={},s=status(),runs=[run()],applied={status:'CURRENT',asOf:'2026-10-16',generatedAt:'2026-10-18T06:01:30Z'}}={}) {
 const ctx={location:{hostname:'dhandori.github.io',pathname:'/wamo-choochoo-dashboard/radar.html'},localStorage:{getItem:()=>null,setItem(){},removeItem(){}},document:{hidden:false,querySelectorAll:()=>[]},Date:class extends Date{static now(){return at+120000}},setTimeout:()=>1,clearTimeout(){},URL,console};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 ctx.p={market:'KR',at,phase:'waiting',...pending};ctx.s=s;ctx.runs=runs;ctx.applied=applied;
 vm.runInContext('pending=p; fetchStatus=async()=>{status={KR:s}}; fetchRuns=async()=>runs; onDone=async()=>{}; getMarketStatus=()=>applied.status; getAppliedState=()=>applied;',ctx);
 await vm.runInContext('poll()',ctx);return vm.runInContext('pending',ctx);
}
test('completed GitHub job cannot certify failed market collection',async()=>{
 const p=await poll({s:status({status:'FAILED',lastSuccessAt:'2026-10-17T06:00:00Z'})});assert.equal(p.phase,'failed');
});
test('tracked run identity wins over another nearby run',async()=>{
 const p=await poll({pending:{runId:42},s:{},runs:[run(99),run(42,{status:'in_progress'})]});assert.equal(p.runId,42);assert.equal(p.phase,'running');
});
test('another market manual run is not adopted',async()=>{
 const p=await poll({s:{},runs:[run(99,{display_title:'WAMO · US · 게시'})]});assert.equal(p.phase,'waiting');assert.equal(p.runId,undefined);
});
test('successful collection waits for the matching loaded snapshot',async()=>{
 const p=await poll({applied:{status:'STALE',asOf:'2026-10-15',generatedAt:'2026-10-17T00:00:00Z'}});assert.equal(p.phase,'applying');
});
test('same-date old snapshot does not certify a partial rerun was applied',async()=>{
 const p=await poll({applied:{status:'CURRENT',asOf:'2026-10-16',generatedAt:'2026-10-17T00:00:00Z'}});assert.equal(p.phase,'applying');
});
test('verified market collection and newer loaded snapshot complete',async()=>{
 const p=await poll();assert.equal(p.phase,'done');
});
test('completed no-op does not claim a fresh collection',async()=>{
 const p=await poll({pending:{completedSeenAt:at},s:status({attemptedAt:'2026-10-17T06:00:00Z',lastSuccessAt:'2026-10-17T06:00:30Z',runUrl:run(1).html_url})});assert.equal(p.phase,'unchanged');
});
test('failed workflow is not overridden by an earlier successful market checkpoint',async()=>{
 const p=await poll({runs:[run(42,{conclusion:'failure'})]});assert.equal(p.phase,'failed');
});
test('a retained partial market cannot inherit a newer global timestamp as completion',async()=>{
 const p=await poll({applied:{status:'PARTIAL',asOf:'2026-10-16',generatedAt:'2026-10-18T06:01:30Z'}});assert.equal(p.phase,'applying');
});
