// Separate market-reversal workspace; individual-stock filters do not apply.
import { ftdSummary, freshIndex, PHASES, PHASE_MEANING, ftdQuality } from './ftd-model.mjs?v=20261026-rn1';
import { helpButton, addTerms } from './help.js?v=20261028-at1';
import { FTD_TERMS, glossary } from './terms.mjs?v=20261026-rn1';
addTerms(FTD_TERMS);
const n = (tag, text, cls) => { const e=document.createElement(tag); if(text!=null)e.textContent=text; if(cls)e.className=cls; return e; };
const num = (v,d=1) => typeof v==='number' && Number.isFinite(v) ? v.toLocaleString('ko-KR',{maximumFractionDigits:d,minimumFractionDigits:d}) : '—';
const pct = v => typeof v==='number'?`${v>0?'+':''}${num(v)}%`:'—';
const term = (key,label) => helpButton(label||FTD_TERMS[key][0], key);
const ask = (key) => helpButton(FTD_TERMS[key][0], key, true);
const selected={KR:0,US:0,JP:0,TW:0};
const EVENT_NAMES={WINDOW_EXPIRED:'판정 기간 종료 (예전 규칙)',LATE_WINDOW:'반등 Day 8 · 늦은 구간 진입',CORRECTION:'하락장 진입',NEW_CORRECTION:'새 하락장',RALLY:'반등 Day 1',RALLY_FAILED:'반등 무효 (기준 저점 이탈)',FTD:'FTD 나옴',FTD_FAILED:'FTD 무효 (기준 저점 이탈)',RECOVERED:'FTD 없이 전고점 회복 · FTD 찾기 종료',UPTREND:'FTD 뒤 전고점 회복 · 상승 전환 완료'};
const NOTE={EXPIRED:'예전 규칙(Day 8 이후 불인정)으로 계산된 결과입니다. 다음 자동 갱신에서 새 규칙으로 다시 계산합니다.',OUTSIDE:'지금은 하락장이 아니라서 FTD를 찾지 않습니다. 하락장이 오면 그때부터 반등 날짜를 셉니다.',
  CORRECTION:'지수가 하락장에 들어왔습니다. 지수가 처음으로 반등하는 날(Day 1)을 기다립니다.',
  RALLY:'반등을 시도하는 중입니다. 기준 저점을 깨지 않고, Day 4 이후에 지수가 +1.7% 넘게 오르면서 거래량도 전날보다 많으면 FTD입니다.',
  LATE:'반등이 Day 8을 넘겼지만 아직 FTD가 없습니다. 지금부터 나오는 FTD는 "늦은 FTD"로 표시하고, 힘이 약하다고 봅니다.',
  CONFIRMED:'하락장 뒤에 FTD가 나왔습니다. 시장이 다시 오르기 시작했을 수 있다는 신호입니다. 기준 저점을 깨면 무효가 됩니다. 지금은 총자금의 10% 이내에서만 시험 매수합니다.',
  PRESSURE:'FTD 뒤에 힘이 빠지는 경고가 나왔습니다. 아직 무효는 아니지만 비중을 10% 넘게 늘리지 않습니다.',
  UNAVAILABLE:'지수 가격·거래량·거래일 이력이 모두 확인될 때 판정합니다.'};
// After a correction the index either completed an FTD-led uptrend or simply got back above its old high.
const lastEvent=s=>(s.events||[]).slice(-1)[0]?.type;
const uptrendDone=s=>s.phase==='OUTSIDE'&&lastEvent(s)==='UPTREND';
const phaseName=s=>uptrendDone(s)?'상승 전환 완료':PHASES[s.phase]||s.phase;
const phaseNote=s=>uptrendDone(s)?'FTD 뒤 지수가 하락 전 고점을 되찾았습니다. 하락장이 끝나 새 FTD는 찾지 않고, 선도주가 계속 버티는지 봅니다.':s.phase==='OUTSIDE'&&lastEvent(s)==='RECOVERED'?'FTD 없이 지수가 하락 전 고점을 되찾았습니다. 지금은 하락장이 아니라서 FTD를 찾지 않습니다.':NOTE[s.phase];
const DAY_ONE_KIND={UP_CLOSE:'상승 마감한 날',REVERSAL:'장중 새 저점을 찍고 위쪽 절반에서 마감한 날'};
function link(text,url){const a=n('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function metric(labelNode,value,sub){const box=n('div',null,'ftd-metric');const head=n('span');head.append(labelNode);box.append(head,n('strong',value),n('small',sub));return box;}
function badge(text,phase){return n('span',text,`ftd-badge ftd-${phase.toLowerCase()}`);}
function check(labelNode,ok,detail){const item=n('div',null,`ftd-check ${ok===true?'pass':ok===false?'':'unknown'}`);item.append(n('span',ok===true?'✓':ok===false?'—':'?','ftd-check-icon'));const body=n('div');const b=n('b');b.append(labelNode);body.append(b,n('small',detail));item.append(body);return item;}

// The whole journey in one strip: every step has its meaning written under it.
const FLOW=[['OUTSIDE','하락장 아님','FTD를 찾지 않는 평상시'],['CORRECTION','하락장','지수가 크게 떨어진 상태'],['RALLY','반등 시도','Day 1부터 날짜를 셈'],['CONFIRMED','FTD 확인','Day 4 이후 +1.7%·거래량 증가'],['UPTREND','상승 전환 완료','FTD 뒤 하락 전 고점 회복']];
function flow(s,fresh){
  const wrap=n('section',null,'ftd-flow');
  const head=n('div',null,'ftd-flow-head');head.append(n('h4','시장 상태 흐름'),n('span','지금 단계가 밝게 표시돼요','ftd-flow-hint'));wrap.append(head);
  const now=!fresh?null:s.phase==='LATE'?'RALLY':s.phase==='PRESSURE'?'CONFIRMED':uptrendDone(s)?'UPTREND':s.phase;
  const ol=n('ol');
  FLOW.forEach(([key,label,meaning],i)=>{const li=n('li',null,key===now?'now':'');li.append(n('span',String(i+1),'ftd-flow-no'));const body=n('div');body.append(n('b',label),n('small',meaning));if(key===now){const extra=s.phase==='PRESSURE'?'경고 중':s.phase==='LATE'?'늦은 구간':'지금';body.append(n('em',extra));}li.append(body);ol.append(li);});
  wrap.append(ol);
  wrap.append(n('p','중간에 기준 저점을 깨면 "반등 무효"가 되어 하락장 단계로 돌아가고, 다음 반등부터 다시 셉니다.','ftd-flow-foot'));
  return wrap;
}

function chart(s){
  const all=s.series||[],wrap=n('section',null,'ftd-chart');
  wrap.append(n('h4','지수 흐름과 반등 기준선'));
  if(all.length<2){wrap.append(n('p','표시할 지수 기록이 없습니다.'));return wrap;}
  // 20-day average from the stored closes; the first 19 sessions have none.
  const ma20=all.map((r,i)=>i<19?null:all.slice(i-19,i+1).reduce((t,x)=>t+x.close,0)/20);
  const phone=window.matchMedia('(max-width:760px)').matches;
  const keep=phone?90:all.length, off=all.length-Math.min(keep,all.length);
  const rows=all.slice(off), m20=ma20.slice(off);
  const NS='http://www.w3.org/2000/svg';
  // Phone uses a narrow drawing so text stays readable after scaling.
  const W=phone?400:900,H=phone?330:380,L=8,R=phone?52:70,T=14,P=phone?220:255,V=P+30,VH=phone?44:56,FS=phone?12:12;
  const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',`${s.label} 종가·20일선·50일선·거래량과 FTD 기준선`);
  function add(tag,attrs,text){const el=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text)el.textContent=text;svg.append(el);return el;}
  const levels=[s.rally?.low,s.ftd?.low].filter(v=>typeof v==='number');
  const values=rows.flatMap((r,i)=>[r.close,r.ma50,m20[i]]).concat(levels).filter(v=>typeof v==='number');
  const min=Math.min(...values)*.99,max=Math.max(...values)*1.01,range=max-min||1;
  const x=i=>L+i/(rows.length-1)*(W-L-R), y=v=>T+(max-v)/range*(P-T);
  // Price axis (right) with grid.
  for(let i=0;i<5;i++){const value=min+(max-min)*i/4,yy=y(value);add('line',{x1:L,y1:yy,x2:W-R,y2:yy,stroke:'#27364a','stroke-width':1});add('text',{x:W-R+6,y:yy+4,fill:'#9fb3c9','font-size':FS},num(value,0));}
  add('line',{x1:W-R,y1:T,x2:W-R,y2:P,stroke:'#3a4c63','stroke-width':1});
  add('line',{x1:L,y1:P,x2:W-R,y2:P,stroke:'#3a4c63','stroke-width':1});
  const path=vals=>vals.map((v,i)=>typeof v==='number'?`${x(i)},${y(v)}`:'').filter(Boolean).join(' ');
  add('polyline',{points:path(rows.map(r=>r.ma50)),fill:'none',stroke:'#659de3','stroke-width':1.6});
  add('polyline',{points:path(m20),fill:'none',stroke:'#e39a5b','stroke-width':1.4});
  add('polyline',{points:path(rows.map(r=>r.close)),fill:'none',stroke:'#ecf3fc','stroke-width':2});
  for(const [value,color,label] of [[s.rally?.low,'#f2c474','기준 저점(반등 무효선)'],[s.ftd?.low,'#5bd4b1','FTD 저가']])if(typeof value==='number'){add('line',{x1:L,y1:y(value),x2:W-R,y2:y(value),stroke:color,'stroke-width':1.4,'stroke-dasharray':'6 5'});add('text',{x:L+4,y:y(value)-5,fill:color,'font-size':FS-1},label);}
  // Volume bars with their own baseline.
  const maxVol=Math.max(...rows.map(r=>r.volume)),bw=Math.max(1.5,(W-L-R)/rows.length*.65);
  rows.forEach((r,i)=>{const h=r.volume/maxVol*VH;add('rect',{x:x(i)-bw/2,y:V+VH-h,width:bw,height:h,fill:i&&r.close<rows[i-1].close?'#2f5f9e':'#a3404f'});});
  add('line',{x1:L,y1:V+VH,x2:W-R,y2:V+VH,stroke:'#3a4c63','stroke-width':1});
  add('text',{x:W-R+6,y:V+12,fill:'#9fb3c9','font-size':FS-1},'거래량');
  const events=(s.events||[]).filter(e=>['FTD','RALLY'].includes(e.type));
  events.forEach(e=>{const i=rows.findIndex(r=>r.date===e.date);if(i<0)return;const isFTD=e.type==='FTD',cy=y(rows[i].close);const dot=add('circle',{cx:x(i),cy,r:isFTD?5:3.5,fill:isFTD?'#5bd4b1':'#f2c474',stroke:'#0a1421','stroke-width':2});const title=document.createElementNS(NS,'title');title.textContent=`${e.date} · ${EVENT_NAMES[e.type]}`;dot.append(title);if(isFTD)add('text',{x:x(i),y:cy-11,fill:'#5bd4b1','font-size':FS,'font-weight':700,'text-anchor':'middle'},'FTD');});
  // Date axis.
  const ticks=phone?[0,Math.floor(rows.length/2),rows.length-1]:[0,Math.floor(rows.length/4),Math.floor(rows.length/2),Math.floor(rows.length*3/4),rows.length-1];
  ticks.forEach(i=>{add('line',{x1:x(i),y1:V+VH,x2:x(i),y2:V+VH+4,stroke:'#3a4c63'});add('text',{x:x(i),y:H-4,fill:'#9fb3c9','font-size':FS,'text-anchor':i===0?'start':i===rows.length-1?'end':'middle'},rows[i].date.slice(2).replace(/-/g,'.'));});
  wrap.append(svg);
  const legend=n('div',null,'ftd-legend');
  for(const [label,color] of [['종가','#ecf3fc'],['20일선','#e39a5b'],['50일선','#659de3'],['반등 1일째','#f2c474'],['FTD','#5bd4b1']]){const item=n('span');const sw=n('i');sw.style.background=color;item.append(sw,n('span',label));legend.append(item);}
  legend.append(n('small',`점: 발생일 · 점선: 지금 지켜볼 저가 · 최근 ${rows.length}거래일`));wrap.append(legend);return wrap;
}

export function renderFTD(container,market,block,{expected,failed=false,openLeaders}={}){
  container.replaceChildren();
  const indexes=block?.ftd?.indexes || [];
  const emergence=block?.leadership?.emergence||null;
  // 판정 먼저: 지수별 현재 단계 카드를 맨 위에 둔다. 설명은 그 아래 접기로(내용은 그대로).
  const deck=n('div',null,'ftd-indexes');
  indexes.forEach((s,i)=>{const b=n('button',null,`ftd-index ${selected[market]===i?'selected':''}`);b.type='button';b.setAttribute('aria-pressed',String(selected[market]===i));
    const isFresh=freshIndex(s,expected)&&!failed;
    b.append(n('span',s.label),badge(isFresh?phaseName(s):'판정 보류',isFresh?s.phase:'UNAVAILABLE'),n('small',isFresh?(uptrendDone(s)?'FTD 뒤 하락 전 고점을 되찾았어요.':PHASE_MEANING[s.phase]||''):'자료 확인 전에는 판정하지 않아요.'),n('small',`${s.asOf||'—'} 종가${s.rally?` · 반등 Day ${s.rally.day}`:['CORRECTION'].includes(s.phase)?' · 반등 대기':''}`,'ftd-index-sub'));
    b.onclick=()=>{selected[market]=i;renderFTD(container,market,block,{expected,failed,openLeaders});};deck.append(b);
  });
  if(indexes.length)container.append(deck);
  // The user's own position-size rule, shown on every FTD screen.
  const active=indexes.some(x=>freshIndex(x,expected)&&!failed&&['CONFIRMED','PRESSURE'].includes(x.phase));
  const rule=n('div',null,`ftd-rule${active?' active':''}`);
  rule.append(n('strong','내 원칙 · FTD 뒤에는 총자금의 10% 이내로 발담그기만'),
    n('span','FTD는 "이제 사도 된다"가 아니라 "조금 시험해 봐도 된다"는 신호입니다. 처음에는 총자금의 10% 안에서만 삽니다. 비중을 더 늘리는 것은 선도주가 실제로 신고가를 뚫고(선도주 출현), 시장이 계속 버텨 줄 때 따로 판단합니다.'));
  if(active)rule.append(n('em','지금 FTD 신호가 켜져 있어요 → 10% 이내'));
  container.append(rule);
  const intro=n('details',null,'intro-fold');const sum=n('summary');sum.append('FTD가 무엇인지 · 판정 방식 보기',badge('종가 기준 · 자체 규칙','OUTSIDE'));intro.append(sum);
  const p=n('p');p.append(term('ftd','FTD(Follow-Through Day)'),'는 하락장이 끝나고 다시 오르기 시작했는지 확인하는 신호입니다. 지수가 크게 떨어진 뒤 반등을 시작하고, Day 4 이후에 +1.7% 넘게 오르면서 거래량도 늘면 FTD로 봅니다. 밑줄 친 말을 누르면 뜻이 나와요.');intro.append(p);
  container.append(intro);
  container.append(glossary(FTD_TERMS,['ftd','correction','dayOne','rallyDay','bottom','ftdLow','distribution','lateFtd','quality','failed','uptrendDone','drawdown','dayChange','volRatio']));
  if(!indexes.length){container.append(n('div','FTD 지수 기록을 준비 중입니다. 다음 자동 갱신 때 가격과 거래량을 확인한 뒤 보여 드려요.','ftd-empty'));return;}
  const s=indexes[selected[market]]||indexes[0],fresh=freshIndex(s,expected)&&!failed;
  if(!fresh){const messages=[failed?'최신 파일을 다시 불러오지 못했습니다.':null,s.note||'지수 데이터가 최신인지, 빠진 날이 없는지 확인하지 못했습니다.',expected&&s.asOf!==expected?`최근 마감 거래일 ${expected}`:null];container.append(n('div',messages.filter(Boolean).join(' '),'ftd-data-warning'));}
  container.append(flow(s,fresh));
  const panel=n('section',null,`ftd-panel ftd-${fresh?s.phase.toLowerCase():'unavailable'}`);
  const head=n('div',null,'ftd-panel-head'),title=n('div');title.append(n('div',s.label,'eyebrow'),n('h3',fresh?phaseName(s):'현재 판정 보류'),n('p',fresh?phaseNote(s):(s.status==='STALE'?`${s.asOf} 기준 과거 상태: ${PHASES[s.phase]}`:NOTE.UNAVAILABLE)));head.append(title);panel.append(head);
  if(fresh&&s.warnings?.length){const warn=n('div',null,'ftd-warnings');warn.append(n('strong','⚠ 지금 켜진 경고'));
    s.warnings.forEach(w=>{const line=n('p');if(w.code==='BELOW_FTD_LOW'){line.append('종가가 ',term('ftdLow','FTD 저가'),` (${num(w.level,2)}) 아래로 내려갔어요. 힘이 빠졌다는 뜻이에요.`);}
      else if(w.code==='DISTRIBUTION_CLUSTER'){line.append('FTD 뒤 10거래일 안에 ',term('distribution','분산일'),`이 ${w.count}번 나왔어요 (${(w.dates||[]).map(d=>d.slice(5).replace('-','/')).join(', ')}). 큰손이 팔고 있다는 경고예요.`);}
      else line.append(w.code);warn.append(line);});panel.append(warn);}
  const latest=s.latest||{},grid=n('div',null,'ftd-grid');
  grid.append(metric(term('drawdown','63거래일 고점 대비'),typeof latest.drawdownPct==='number'?`-${num(latest.drawdownPct)}%`:'—',latest.belowMa50?'50일선 아래':'50일선 위 또는 확인 안 됨'),
    metric(term('rallyDay','반등 경과'),s.rally?`Day ${s.rally.day}`:'—',s.rally?`${s.rally.date} 시작 · ${DAY_ONE_KIND[s.rally.kind]||'상승 마감'}`:'하락장 안에서 첫 반등부터 계산'),
    metric(term('dayChange','당일 지수 등락'),pct(latest.changePct),'FTD 조건: +1.7% 이상'),
    metric(term('volRatio','전일 대비 거래량'),typeof latest.volumeRatio==='number'?`${num(latest.volumeRatio,2)}배`:'—',typeof latest.volumeRatio==='number'?`전일 대비 ${pct((latest.volumeRatio-1)*100)} · 1.00배보다 커야 함`:'전일보다 많아야 함'));
  panel.append(grid);
  if(s.status!=='UNAVAILABLE'){
    const checks=n('div',null,'ftd-checks'),day=s.rally?.day;
    checks.append(check(term('correction','하락장 전제'),!!s.correction,s.correction?`${s.correction.date} 확인 · 당시 고점 대비 -${num(s.correction.drawdownPct)}%`:'지금은 FTD를 찾는 구간이 아님'),
      check(term('dayOne','Day 1'),!!s.rally,s.rally?`${s.rally.date} · ${DAY_ONE_KIND[s.rally.kind]||'상승 마감한 날'}`:'첫 반등을 기다리는 중'),
      check(term('rallyDay','반등 일수'),!!day&&day>=4,day?`Day ${day} · Day 4부터 가능${day>7?' (Day 8 이후는 늦은 FTD)':day>1&&day<4?' (Day 2~3 급등은 불인정)':''}`:'Day 1 대기'),
      check(term('dayChange','상승률'),latest.changePct>=1.7,`${pct(latest.changePct)} / 기준 +1.7%`),
      check(term('volRatio','거래량'),latest.volumeRatio>1,`${num(latest.volumeRatio,2)}배 / 1.00배 초과`));
    panel.append(checks,n('p','위 다섯 칸은 오늘 하루의 관찰값입니다. 이미 나온 FTD가 계속 유효하려고 매일 이 조건을 다시 채울 필요는 없습니다.','ftd-caption'));
  }
  if(s.ftd){const signal=n('div',null,'ftd-signal');
    const top=n('div',null,'ftd-signal-top');top.append(n('strong',`${fresh?'최근':'과거'} FTD ${s.ftd.date}`),n('span',`당시 ${pct(s.ftd.changePct)} · 거래량 전일의 ${num(s.ftd.volumeRatio,2)}배 · 반등 Day ${s.ftd.rallyDay} · 이후 ${s.ftdAge??0}거래일`));
    if(s.ftd.late){const late=n('span',null,'ftd-late');late.append(term('lateFtd','늦은 FTD'),' · 신뢰도 낮음');top.append(late);}
    top.append(n('span','총자금 10% 이내 발담그기','ftd-cap'));signal.append(top);
    const q=ftdQuality(s.ftd,emergence);
    if(q){const box=n('div',null,'ftd-quality');const h=n('div',null,'ftd-quality-head');h.append(term('quality','FTD 품질 점수'),n('b',`${q.score} / 4 · ${q.label}`,`q-${q.score>=3?'strong':q.score===2?'mid':'weak'}`));box.append(h);
      const ul=n('ul');q.items.forEach(([label,v])=>{const li=n('li',null,v===true?'pass':v===false?'fail':'wait');li.append(n('span',v===true?'✓':v===false?'✗':'…'),n('span',label+(v===null?' · 아직 확인 중':'')));ul.append(li);});box.append(ul);signal.append(box);}
    if(openLeaders){const go=n('button','선도주가 실제로 나오는지 확인하기 →','ftd-go');go.type='button';go.onclick=openLeaders;signal.append(go);}
    panel.append(signal);}
  if(!s.ftd&&s.lastFtd){const lf=s.lastFtd,box=n('div',null,'ftd-signal ftd-past');const top=n('div',null,'ftd-signal-top');top.append(n('strong',`지난 FTD ${lf.date}`),n('span',`당시 ${pct(lf.changePct)} · 거래량 ${num(lf.volumeRatio,2)}배 · 반등 Day ${lf.rallyDay}`));const done=n('span');done.append(`${lf.recoveredDate} 하락 전 고점(${num(lf.peak,0)}) 회복 → `,term('uptrendDone','상승 전환 완료'),` · 이후 ${lf.age??0}거래일`);top.append(done);box.append(top);panel.append(box);}
  if(s.rally){const lows=n('div',null,'ftd-levels');lows.append(metric(term('bottom','기준 저점 (반등 무효선)'),num(s.rally.low,2),`장중 이 가격 아래로 내려가면 반등·FTD 무효${typeof s.rally.dayOneLow==='number'&&s.rally.dayOneLow!==s.rally.low?` · Day 1 장중 저가는 ${num(s.rally.dayOneLow,2)}`:''}`));if(s.ftd)lows.append(metric(term('ftdLow','FTD 저가 (경고선)'),num(s.ftd.low,2),'종가가 이 가격 아래면 경고'));panel.append(lows);}
  if(s.ftd&&s.ftd.postDistribution){const d=s.ftd.postDistribution;const line=n('p',null,'ftd-caption');const kindName={HEAVY:'꽉 찬 음봉',NORMAL:'보통',LIGHT:'긴 아랫꼬리',HIDDEN:'위장',STALL:'정체'},kinds=s.ftd.postDistributionKind||{};line.append('FTD 뒤 10거래일 안의 ',term('distribution','분산일'),`: ${d.length}번${d.length?` (${d.map(x=>`${x.slice(5).replace('-','/')}${kinds[x]?` ${kindName[kinds[x]]}`:''}`).join(', ')})`:''} · 3번 이상이면 경고`);panel.append(line);}
  panel.append(chart(s));container.append(panel);
  const history=n('details',null,'ftd-history');history.append(n('summary',`최근 판정 이력 · ${(s.events||[]).length}건`));
  const list=n('ol');[...(s.events||[])].reverse().slice(0,20).forEach(e=>{const li=n('li');li.append(n('time',e.date),n('strong',EVENT_NAMES[e.type]||e.type),n('span',e.type==='FTD'?`${pct(e.changePct)} · ${num(e.volumeRatio,2)}배 · Day ${e.rallyDay}${e.late?' · 늦은 FTD':''}`:e.type==='RALLY'?`${DAY_ONE_KIND[e.dayOneKind]||'상승 마감'} · 기준 저점 ${num(e.low,2)}`:`지수 ${num(e.close,2)}`));list.append(li);});history.append(list);container.append(history);
  const rules=n('details',null,'ftd-rules');rules.append(n('summary','판정 기준·데이터 출처'));const pol=s.policy;
  if(pol){rules.append(n('p',`하락장 전제 (와모 자체 필터): ${pol.correction}`),...(pol.dayOne?[n('p',`Day 1: ${pol.dayOne}`)]:[]),...(pol.bottom?[n('p',`기준 저점: ${pol.bottom}`)]:[]),n('p',`FTD: ${pol.confirmation}`),n('p',`유지·실패: ${pol.failure}`),...(pol.completion?[n('p',`상승 전환 완료: ${pol.completion}`)]:[]),n('p',pol.note));(pol.sources||[]).forEach(src=>rules.append(link(src.title,src.url)));}
  rules.append(n('p',`${s.source||'지수 출처 확인 안 됨'} · ${s.volumeNote||''} · ${s.asOf||'—'} 기준.`));
  if(s.repairedDates?.length){rules.append(n('p',`네이버 자료로 채운 날짜: ${s.repairedDates.join(', ')} · 겹치는 기간의 종가·거래량을 서로 맞춰 보며 확인했습니다.`));rules.append(link('한국 지수 보완 자료 ↗',s.repairSourceUrl));}
  if(s.sourceUrl)rules.append(link('지수 원자료 ↗',s.sourceUrl));container.append(rules);
}
export { ftdSummary };
