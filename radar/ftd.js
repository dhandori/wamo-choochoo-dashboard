// Separate market-reversal workspace; individual-stock filters do not apply.
import { ftdSummary, freshIndex, PHASES } from './ftd-model.mjs?v=20261019-1';
const n = (tag, text, cls) => { const e=document.createElement(tag); if(text!=null)e.textContent=text; if(cls)e.className=cls; return e; };
const num = (v,d=1) => typeof v==='number' && Number.isFinite(v) ? v.toLocaleString('ko-KR',{maximumFractionDigits:d,minimumFractionDigits:d}) : '—';
const pct = v => typeof v==='number'?`${v>0?'+':''}${num(v)}%`:'—';
const selected={KR:0,US:0};
const EVENT_NAMES={WINDOW_EXPIRED:'판정 기간 종료 (Day 8)',CORRECTION:'하락 국면 진입',NEW_CORRECTION:'새 하락 국면',RALLY:'반등 Day 1',RALLY_FAILED:'반등 무효',FTD:'FTD 조건 충족',FTD_FAILED:'FTD 무효',RECOVERED:'전고점 회복 · 탐색 종료',UPTREND:'FTD 후 전고점 회복 · 상승 전환 완료'};
const NOTE={EXPIRED:'반등 7거래일 안에 FTD 조건을 충족하지 못했습니다. 8일째 이후의 급등은 FTD로 인정하지 않습니다. 새로운 저점·반등 시도를 기다립니다.',OUTSIDE:'지금은 하락장이 아니라서 FTD를 찾지 않습니다. 하락장이 오면 그때부터 반등 날짜를 셉니다.',
  CORRECTION:'지수가 하락장에 들어왔습니다. 지수가 처음으로 오르며 끝나는 날(반등 1일째)을 기다립니다.',
  RALLY:'반등을 시도하는 중입니다. 1일째 저가를 깨지 않고, 4~7일째에 지수가 +1.7% 넘게 오르면서 거래량도 전날보다 많으면 FTD입니다.',
  CONFIRMED:'하락장 뒤에 FTD가 나왔습니다. 시장이 다시 오르기 시작했다는 신호입니다. 반등 1일째 저가를 깨면 신호가 무효가 됩니다.',
  PRESSURE:'FTD 뒤에 지수가 FTD 날 저가 아래로 마감했습니다. 아직 무효는 아니지만 힘이 약해졌다는 경고입니다.',
  UNAVAILABLE:'지수 가격·거래량·거래일 이력이 모두 확인될 때 판정합니다.'};
function link(text,url){const a=n('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function metric(label,value,sub){const box=n('div',null,'ftd-metric');box.append(n('span',label),n('strong',value),n('small',sub));return box;}
function badge(text,phase){return n('span',text,`ftd-badge ftd-${phase.toLowerCase()}`);}
function check(label,ok,detail){const item=n('div',null,`ftd-check ${ok?'pass':''}`);item.append(n('span',ok?'✓':'—','ftd-check-icon'));const body=n('div');body.append(n('b',label),n('small',detail));item.append(body);return item;}

function chart(s){
  const all=s.series||[],wrap=n('section',null,'ftd-chart');
  wrap.append(n('h4','지수 흐름과 반등 기준선'));
  if(all.length<2){wrap.append(n('p','표시할 검증 지수 이력이 없습니다.'));return wrap;}
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
  for(const [value,color,label] of [[s.rally?.low,'#f2c474','반등 무효선'],[s.ftd?.low,'#5bd4b1','FTD 저가']])if(typeof value==='number'){add('line',{x1:L,y1:y(value),x2:W-R,y2:y(value),stroke:color,'stroke-width':1.4,'stroke-dasharray':'6 5'});add('text',{x:L+4,y:y(value)-5,fill:color,'font-size':FS-1},label);}
  // Volume bars with their own baseline.
  const maxVol=Math.max(...rows.map(r=>r.volume)),bw=Math.max(1.5,(W-L-R)/rows.length*.65);
  rows.forEach((r,i)=>{const h=r.volume/maxVol*VH;add('rect',{x:x(i)-bw/2,y:V+VH-h,width:bw,height:h,fill:i&&r.close<rows[i-1].close?'#a85d70':'#387f76'});});
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

export function renderFTD(container,market,block,{expected,failed=false}={}){
  container.replaceChildren();
  const indexes=block?.ftd?.indexes || [];
  const intro=n('div',null,'ftd-intro');intro.append(n('p','FTD(Follow-Through Day)는 하락장이 끝나고 다시 오르기 시작했다는 신호입니다. 지수가 크게 떨어진 뒤 반등을 시작하고, 4~7일째에 +1.7% 넘게 오르면서 거래량도 늘면 FTD로 봅니다. 지수마다 따로 계산합니다.'));
  intro.append(badge('종가 기준 · 자체 규칙','OUTSIDE'));container.append(intro);
  if(!indexes.length){container.append(n('div','FTD 지수 이력을 준비 중입니다. 다음 자동 갱신에서 검증된 가격과 거래량을 받은 뒤 표시합니다.','ftd-empty'));return;}
  const deck=n('div',null,'ftd-indexes');
  indexes.forEach((s,i)=>{const b=n('button',null,`ftd-index ${selected[market]===i?'selected':''}`);b.type='button';b.setAttribute('aria-pressed',String(selected[market]===i));
    const isFresh=freshIndex(s,expected)&&!failed;
    b.append(n('span',s.label),badge(isFresh?PHASES[s.phase]||'판정 보류':'판정 보류',isFresh?s.phase:'UNAVAILABLE'),n('small',`${s.asOf||'—'} 종가 · ${s.rally?`반등 ${s.rally.day}거래일`:'반등 대기'}`));
    b.onclick=()=>{selected[market]=i;renderFTD(container,market,block,{expected,failed});};deck.append(b);
  });container.append(deck);
  const s=indexes[selected[market]]||indexes[0],fresh=freshIndex(s,expected)&&!failed;
  if(!fresh){const messages=[failed?'최신 파일을 다시 확인하지 못했습니다.':null,s.note||'지수 데이터의 최신성 또는 완전성을 확인할 수 없습니다.',expected&&s.asOf!==expected?`최근 마감 거래일 ${expected}`:null];container.append(n('div',messages.filter(Boolean).join(' '),'ftd-data-warning'));}
  const panel=n('section',null,`ftd-panel ftd-${fresh?s.phase.toLowerCase():'unavailable'}`);
  const head=n('div',null,'ftd-panel-head'),title=n('div');title.append(n('div',s.label,'eyebrow'),n('h3',fresh?PHASES[s.phase]:'현재 판정 보류'),n('p',fresh?NOTE[s.phase]:(s.status==='STALE'?`${s.asOf} 기준 과거 상태: ${PHASES[s.phase]}`:NOTE.UNAVAILABLE)));head.append(title);panel.append(head);
  const phaseSteps=['CORRECTION','RALLY','CONFIRMED'],step=s.phase==='PRESSURE'?2:phaseSteps.indexOf(s.phase);
  const progress=n('ol',null,'ftd-progress');['하락 국면','반등 시도','FTD 조건 충족','유지·실패 점검'].forEach((label,i)=>{const li=n('li',null,fresh&&step>=Math.min(i,2)?'reached':'');li.append(n('span',String(i+1)),n('b',label));progress.append(li);});panel.append(progress);
  const latest=s.latest||{},grid=n('div',null,'ftd-grid');
  grid.append(metric('63거래일 고점 대비',typeof latest.drawdownPct==='number'?`-${num(latest.drawdownPct)}%`:'—',latest.belowMa50?'50일선 아래':'50일선 위 또는 미확인'),
    metric('반등 경과',s.rally?`Day ${s.rally.day}`:'—',s.rally?`${s.rally.date} 시작`:'하락 국면 이후부터 계산'),
    metric('당일 지수 등락',pct(latest.changePct),'FTD 조건 +1.7% 이상'),
    metric('전일 대비 거래량',typeof latest.volumeRatio==='number'?`${num(latest.volumeRatio,2)}배`:'—',typeof latest.volumeRatio==='number'?`전일 대비 ${pct((latest.volumeRatio-1)*100)}`:'전일 초과 필요'));
  panel.append(grid);
  if(s.status!=='UNAVAILABLE'){
    const checks=n('div',null,'ftd-checks');
    checks.append(check('하락 전제',!!s.correction,s.correction?`${s.correction.date} 확인 · 당시 고점 대비 -${num(s.correction.drawdownPct)}%`:'현재 FTD 탐색 대상 아님'),
      check('반등 일수',!!s.rally&&s.rally.day>=4&&s.rally.day<=7,s.rally?`Day ${s.rally.day} · 허용 Day 4~7`:'첫 상승 마감 대기'),
      check('상승률',latest.changePct>=1.7,`${pct(latest.changePct)} / +1.7%`),
      check('거래량',latest.volumeRatio>1,`${num(latest.volumeRatio,2)}배 / 1.00배 초과`));
    const caption=n('p','위 네 조건은 표시 거래일의 관찰값입니다. 이미 발생한 FTD의 유지 여부와는 구분합니다.','ftd-caption');panel.append(checks,caption);
  }
  if(s.ftd){const signal=n('div',null,'ftd-signal');signal.append(n('strong',`${fresh?'최근':'과거'} FTD ${s.ftd.date}`),n('span',`당시 ${pct(s.ftd.changePct)} · 거래량 ${num(s.ftd.volumeRatio,2)}배 · 반등 Day ${s.ftd.rallyDay} · 이후 ${s.ftdAge??0}거래일`));panel.append(signal);}
  if(!s.ftd&&s.lastFtd){const lf=s.lastFtd,box=n('div',null,'ftd-signal ftd-past');box.append(n('strong',`지난 FTD ${lf.date}`),n('span',`당시 ${pct(lf.changePct)} · 거래량 ${num(lf.volumeRatio,2)}배 · 반등 ${lf.rallyDay}일째`),n('span',`${lf.recoveredDate} 하락 전 고점(${num(lf.peak,0)}) 회복 → 상승 전환 완료 · 이후 ${lf.age??0}거래일`));panel.append(box);}
  if(s.rally){const lows=n('div',null,'ftd-levels');lows.append(metric('반등 무효 기준',num(s.rally.low,2),'장중 이 가격 미만이면 반등·FTD 무효'));if(s.ftd)lows.append(metric('FTD 경고 기준',num(s.ftd.low,2),'종가가 이 가격 미만이면 경고'));panel.append(lows);}
  panel.append(chart(s));container.append(panel);
  const history=n('details',null,'ftd-history');history.append(n('summary',`최근 판정 이력 · ${(s.events||[]).length}건`));
  const list=n('ol');[...(s.events||[])].reverse().slice(0,20).forEach(e=>{const li=n('li');li.append(n('time',e.date),n('strong',EVENT_NAMES[e.type]||e.type),n('span',e.type==='FTD'?`${pct(e.changePct)} · ${num(e.volumeRatio,2)}배 · Day ${e.rallyDay}`:`지수 ${num(e.close,2)}`));list.append(li);});history.append(list);container.append(history);
  const rules=n('details',null,'ftd-rules');rules.append(n('summary','판정 기준·데이터 출처'));const p=s.policy;
  if(p){rules.append(n('p',`하락 전제 (와모 자체 필터): ${p.correction}`),n('p',`FTD: ${p.confirmation}`),n('p',`유지·실패: ${p.failure}`),...(p.completion?[n('p',`상승 전환 완료: ${p.completion}`)]:[]),n('p',p.note));(p.sources||[]).forEach(src=>rules.append(link(src.title,src.url)));}
  rules.append(n('p',`${s.source||'지수 출처 미확인'} · ${s.volumeNote||''} · ${s.asOf||'—'} 기준. ${market==='KR'?'한국 적용의 유효성은 별도 검증되지 않았습니다.':''}`));
  if(s.repairedDates?.length){rules.append(n('p',`네이버 실제 관측으로 보완: ${s.repairedDates.join(', ')} · 겹치는 기간 종가·거래량 단위를 교차검증했습니다.`));rules.append(link('한국 지수 보완 원천 ↗',s.repairSourceUrl));}
  if(s.sourceUrl)rules.append(link('지수 원천 이력 ↗',s.sourceUrl));container.append(rules);
}
export { ftdSummary };
