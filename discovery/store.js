import {buildModel,validCatalog,validRadar} from './model.js';

/** Independent public-source cache; failed revalidation never grants a new freshness lease. */
export function createStore({fetcher=globalThis.fetch.bind(globalThis),now=()=>Date.now(),onChange=()=>{}}={}) {
  const data={catalog:null,radar:null};
  const sources={catalog:{status:'missing'},radar:{status:'missing'}};
  let pending=null, loading=false;
  const getState=()=>({loading,sources:{catalog:{...sources.catalog},radar:{...sources.radar}},model:buildModel(data.catalog,data.radar,now(),{catalog:sources.catalog.status,radar:sources.radar.status})});
  const emit=()=>onChange(getState());
  async function load(name) {
    const controller=new AbortController();
    let timer;
    try {
      const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('timeout'));},10000);});
      const request=(async()=>{
        const result=await fetcher(`wamo_${name}.json`,{cache:'no-store',signal:controller.signal});
        if(!result.ok) throw new Error('http');
        return result.json();
      })();
      const next=await Promise.race([request,timeout]);
      if(!(name==='catalog'?validCatalog(next):validRadar(next))) {
        sources[name]={status:'invalid'};
      } else {
        data[name]=next;
        sources[name]={status:'ready',receivedAt:now()};
      }
    } catch {
      sources[name]={status:'failed'};
    } finally {
      clearTimeout(timer);
      emit();
    }
  }
  function refresh() {
    if(pending) return pending;
    loading=true;
    pending=Promise.allSettled([load('catalog'),load('radar')]).then(()=>{
      loading=false; pending=null; emit(); return getState();
    });
    emit();
    return pending;
  }
  return {refresh,getState};
}
