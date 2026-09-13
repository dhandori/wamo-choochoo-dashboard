/* Shared navigation/original-detail entry; discovery data loads only on its page. */
(() => {
  'use strict';
  const source = document.currentScript?.src || new URL('wamo_discovery.js', location.href).href;
  const nav = document.querySelector('.market-nav, .market-switch') || document.querySelector('nav');
  // Keep regenerated legacy navigation within its container at tablet widths.
  if (nav?.matches('.market-switch')) nav.style.flexWrap = 'wrap';
  if (nav && !nav.querySelector('a[href="discovery.html"]')) {
    const link = document.createElement('a');
    link.href = 'discovery.html';
    link.textContent = '🌐 글로벌 종목 발견';
    const lastLink = [...nav.querySelectorAll('a')].at(-1);
    if (lastLink) lastLink.after(link); else nav.append(link);
  }
  function notice(id, text) {
    const message = document.createElement('p');
    message.id = id;
    message.setAttribute('role', 'alert');
    message.textContent = text;
    const host = document.getElementById('discovery-root');
    const anchor = document.getElementById('wamo-live-status');
    if (host) host.replaceChildren(message);
    else if (anchor) anchor.after(message);
    else document.body.prepend(message);
  }
  if (!document.getElementById('discovery-root')) {
    const params = new URLSearchParams(location.search);
    if (window.WAMO_DATA && (params.get('stock') || params.get('radar'))) {
      import(new URL('./discovery/detail.js', source).href)
        .then(module => {
          if (module.handleOriginalRequest(location.search, window.WAMO_OPEN_DETAIL) === 'missing') {
            notice('discovery-original-notice', '요청한 종목을 이 페이지의 기존 WAMO 상세에서 찾을 수 없습니다. 국가 페이지와 종목코드를 확인하거나 글로벌 종목 발견 탭에서 검색하세요.');
          }
        })
        .catch(() => notice('discovery-original-notice', '종목 상세 바로가기를 불러오지 못했습니다. 기존 대시보드에서 종목을 직접 선택하거나 새로고침해 주세요.'));
    }
    return;
  }
  import(new URL('./discovery/view.js', source).href)
    .then(module => module.mountDiscovery())
    .catch(() => notice('discovery-load-error', '종목 발견 화면을 불러올 수 없습니다. 페이지를 새로고침해 다시 확인하세요. 상단 메뉴의 기존 WAMO 분석은 계속 사용할 수 있습니다.'));
})();
