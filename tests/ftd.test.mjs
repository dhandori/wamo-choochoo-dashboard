import test from 'node:test';
import assert from 'node:assert/strict';
import {ftdSummary,freshIndex} from '../radar/ftd-model.mjs';
const session='2026-10-16';
const index=(phase,status='OK',asOf=session)=>({phase,status,asOf});
const block=(...indexes)=>({ftd:{indexes}});
test('stale or missing data cannot produce a valid FTD badge',()=>{
  assert.equal(ftdSummary(block(index('CONFIRMED','STALE')),session),'대기');
  assert.equal(ftdSummary(block(index('CONFIRMED','OK','2026-10-15')),session),'대기');
  assert.equal(ftdSummary(block(index('CONFIRMED')),session,true),'대기');
  assert.equal(freshIndex(index('CONFIRMED'),null),false);
});
test('warning takes priority while the other index is confirmed',()=>{
  assert.equal(ftdSummary(block(index('CONFIRMED'),index('PRESSURE')),session),'경고');
});
test('rally and ordinary bull-market states stay distinct',()=>{
  assert.equal(ftdSummary(block(index('OUTSIDE'),index('RALLY')),session),'반등');
  assert.equal(ftdSummary(block(index('OUTSIDE'),index('OUTSIDE')),session),'비대상');
  assert.equal(ftdSummary(block(index('CORRECTION')),session),'관찰');
});
