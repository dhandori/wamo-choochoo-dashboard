export const PHASES={OUTSIDE:'하락장 아님',EXPIRED:'반등 실패 · 기간 지남',CORRECTION:'하락장',RALLY:'반등 시도 중',LATE:'반등 시도 중 · 늦은 구간',CONFIRMED:'FTD 나옴 · 상승 전환 신호',PRESSURE:'FTD 흔들림 · 경고',UNAVAILABLE:'판정 보류'};
// One-line meaning shown under every phase label, so the word never stands alone.
export const PHASE_MEANING={
  OUTSIDE:'지수가 크게 떨어진 상태가 아니라서 FTD를 찾지 않아요.',
  CORRECTION:'지수가 크게 떨어진 상태예요. 처음 반등하는 날(Day 1)을 기다려요.',
  RALLY:'반등을 시작했어요. 기준 저점을 지키면서 Day 4 이후 큰 상승을 기다려요.',
  LATE:'반등이 7일 넘게 이어졌지만 아직 FTD가 없어요. 지금 나오면 "늦은 FTD"예요.',
  CONFIRMED:'FTD가 나왔어요. 시험 매수(총자금 10% 이내)를 해도 되는 단계예요.',
  PRESSURE:'FTD 뒤 힘이 빠지는 경고가 나왔어요. 비중을 늘리지 말고 지켜봐요.',
  EXPIRED:'예전 규칙의 결과예요. 다음 갱신 때 새 규칙으로 다시 계산돼요.',
  UNAVAILABLE:'지수 가격·거래량 자료가 모두 확인돼야 판정해요.'};
export const freshIndex=(s,expected)=>!!s&&s.status==='OK'&&!!expected&&s.asOf===expected;
export function ftdSummary(block,expected,failed=false){
  const indexes=block?.ftd?.indexes||[];
  if(failed||!indexes.length||indexes.every(s=>!freshIndex(s,expected)))return '대기';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='PRESSURE'))return '경고';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CONFIRMED'))return '유효';
  if(indexes.some(s=>freshIndex(s,expected)&&['RALLY','LATE'].includes(s.phase)))return '반등';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CORRECTION'))return '하락장';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='EXPIRED'))return '기간 종료';
  return '해당 없음';
}
// FTD quality: three engine facts plus leader emergence from the leaders module.
export function ftdQuality(ftd,emergence){
  if(!ftd)return null;
  const q=ftd.quality||{};
  const leaders=emergence&&emergence.ftd&&emergence.ftd.date===ftd.date?(emergence.status==='CONFIRMED'?true:emergence.status==='WEAK'?false:null):null;
  const items=[['Day 4~7에 나옴',q.early!==false&&!ftd.late],['상승률 +2% 이상',!!q.strongGain],[`거래량 50일 평균 초과${typeof ftd.volumeVsAvg==='number'?` (${ftd.volumeVsAvg.toFixed(2)}배)`:''}`,q.aboveAvgVolume??null],['10거래일 안에 선도주 출현',leaders]];
  const score=items.filter(([,v])=>v===true).length;
  return {items,score,label:score>=3?'강함':score===2?'보통':'약함'};
}
