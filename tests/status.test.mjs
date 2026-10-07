import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../wamo_status.js', import.meta.url), 'utf8');
async function render(status, now='2026-10-06T08:00:00Z', reject=false) {
  const nodes = {};
  const timers = [];
  const listeners = {};
  const calls = [];
  nodes.timers=timers; nodes.listeners=listeners; nodes.calls=calls;
  const element = () => ({style:{},children:[],textContent:'',className:'',
    append(...items){this.children.push(...items)},after(){},prepend(){},
    replaceChildren(){this.children=[]},setAttribute(){},addEventListener(){},
    querySelectorAll(){return []}});
  for (const id of ['healthTitle','healthBanner','qualitySummary']) nodes[id]=element();
  const document = {createElement:element,head:element(),body:element(),
    hidden:false,addEventListener:(name,fn)=>{listeners[name]=fn},
    querySelector:()=>null,querySelectorAll:()=>[],getElementById:id=>nodes[id]||null};
  const DateAt = class extends Date {static now(){return Date.parse(now)}};
  vm.runInNewContext(source, {document,window:{WAMO_DATA:{meta:{market:'US',asOf:'2026-10-02',qa:{status:'PASS'}},stocks:[]}},
    AbortController, setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearTimeout:()=>{},
    location:{reload:()=>{},pathname:'/us.html'},Date:DateAt,Intl,MutationObserver:class{},
    fetch:async(...args)=> {calls.push(args);if(reject)throw Error('network');return {ok:true,json:async()=>status}}});
  await new Promise(resolve=>setImmediate(resolve));
  return nodes;
}
test('failed refresh cannot retain the healthy price badge',async()=>{
  const nodes=await render({US:{status:'FAILED',asOf:'2026-10-02'}});
  assert.match(nodes.healthTitle.textContent,/갱신 실패/);
  assert.doesNotMatch(nodes.healthBanner.className,/\bgood\b/);
});
test('overdue successful data is labeled as awaiting refresh',async()=>{
  const nodes=await render({US:{status:'PASS',asOf:'2026-10-02',nextScheduledFor:'2026-10-05T20:20:00Z'}});
  assert.match(nodes.healthTitle.textContent,/갱신 지연/);
});
test('unavailable status is never reported healthy',async()=>{
  const nodes=await render(null,undefined,true);
  assert.match(nodes.healthTitle.textContent,/확인 불가/);
});
test('successful status for a newer payload cannot certify an old page',async()=>{
  const nodes=await render({US:{status:'PASS',asOf:'2026-10-05',nextScheduledFor:'2026-10-06T20:20:00Z'}});
  assert.match(nodes.healthTitle.textContent,/불일치/);
});

test('running collection is visible and never certified healthy',async()=>{
  const nodes=await render({US:{status:'RUNNING',asOf:'2026-10-02',attemptedAt:'2026-10-06T07:50:00Z'}});
  assert.match(nodes.healthTitle.textContent,/갱신 중/);
  assert.doesNotMatch(nodes.healthBanner.className,/\bgood\b/);
});
test('interrupted old running checkpoint is labeled for recovery',async()=>{
  const nodes=await render({US:{status:'RUNNING',asOf:'2026-10-02',attemptedAt:'2026-10-06T06:00:00Z'}});
  assert.match(nodes.healthTitle.textContent,/중단.*복구/);
});
test('status checks have a network deadline and poll without reloading data',async()=>{
  const nodes=await render({US:{status:'PASS',asOf:'2026-10-02'}});
  assert.ok(nodes.calls[0][1].signal);
  assert.ok(nodes.timers.some(t=>t.ms===10000));
  const poll=nodes.timers.find(t=>t.ms===60000);
  assert.ok(poll);
  poll.fn();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes.calls.length,2);
  assert.equal(typeof nodes.listeners.visibilitychange,'function');
});
