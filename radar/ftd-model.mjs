export const PHASES={OUTSIDE:'탐색 대상 아님',EXPIRED:'판정 기간 종료',CORRECTION:'하락 국면',RALLY:'반등 시도',CONFIRMED:'FTD 유효',PRESSURE:'FTD 경고',UNAVAILABLE:'판정 보류'};
export const freshIndex=(s,expected)=>!!s&&s.status==='OK'&&!!expected&&s.asOf===expected;
export function ftdSummary(block,expected,failed=false){
  const indexes=block?.ftd?.indexes||[];
  if(failed||!indexes.length||indexes.every(s=>!freshIndex(s,expected)))return '대기';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='PRESSURE'))return '경고';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CONFIRMED'))return '유효';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='RALLY'))return '반등';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='CORRECTION'))return '관찰';
  if(indexes.some(s=>freshIndex(s,expected)&&s.phase==='EXPIRED'))return '기간 종료';
  return '비대상';
}
