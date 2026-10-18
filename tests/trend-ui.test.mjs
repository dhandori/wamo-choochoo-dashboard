import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const source=readFileSync(new URL('../radar/trend.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace(/^export /gm,'');
const ctx={location:{search:''},URLSearchParams,Intl,Date,MA:[]};
vm.createContext(ctx); vm.runInContext(source,ctx);
test('calendar D-day uses current exchange date, not stored price-date distance',()=>{
 ctx.e={upcoming:[{date:'2026-10-19',daysUntil:3},{date:'2026-10-17',daysUntil:1}]};
 const result=vm.runInContext("currentEarnings(e, new Date('2026-10-18T06:00:00Z'))",ctx);
 assert.equal(result.length,1);assert.equal(result[0].daysUntil,1);
});
test('New York date boundary and daylight-saving transition preserve whole days',()=>{
 assert.equal(vm.runInContext("earningsDayDistance('2026-10-19', new Date('2026-10-19T01:00:00Z'))",ctx),1);
 assert.equal(vm.runInContext("earningsDayDistance('2026-03-09', new Date('2026-03-08T08:00:00Z'))",ctx),1);
 assert.equal(vm.runInContext("earningsDayDistance('2026-10-19', new Date('2026-10-19T15:00:00Z'))",ctx),0);
});
test('pagination exposes every signal including rows after the former eighty-row limit',()=>{
 ctx.rows=Array.from({length:150},(_,id)=>({id}));const shown=[];
 for(let page=1;page<=5;page++) { ctx.p=page; const rows=vm.runInContext("listPages.set('US:sell',p); pageSlice(rows,'US:sell')",ctx); shown.push(...rows.map(x=>x.id)); }
 assert.deepEqual(shown,Array.from({length:150},(_,i)=>i));
});
