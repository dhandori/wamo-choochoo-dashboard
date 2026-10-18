export const PHASES={OUTSIDE:'하락장 아님',EXPIRED:'반등 실패 · 기간 지남',CORRECTION:'하락장',RALLY:'반등 시도 중',CONFIRMED:'FTD 나옴 · 상승 전환 신호',PRESSURE:'FTD 흔들림 · 경고',UNAVAILABLE:'판정 보류'};
export const freshIndex=(s,expected)=>!!s&&s.status==='OK'&&!!expected&&s.asOf===expected;
export function ftdSummary(block,expected,failed=false){
  const indexes=block?.ftd?.indexes||[];
  if(failed||!indexes.length||indexes.every(s=>!freshIndex(s,expected)))return '대기';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='PRESSURE'))return '경고';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CONFIRMED'))return '유효';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='RALLY'))return '반등';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CORRECTION'))return '하락장';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='EXPIRED'))return '기간 종료';
  return '해당 없음';
}
