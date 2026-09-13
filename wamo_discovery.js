/* Classic-script entry retained for the existing WAMO status loader. */
(() => {
  'use strict';
  if (!window.WAMO_DATA) return;
  const source = document.currentScript?.src || new URL('wamo_discovery.js', location.href).href;
  import(new URL('./discovery/view.js', source).href)
    .then(module => module.mountDiscovery())
    .catch(() => {
      const message = document.createElement('p');
      message.id = 'discovery-load-error';
      message.setAttribute('role', 'alert');
      message.textContent = '종목 발견 화면을 불러올 수 없습니다. 페이지를 새로고침해 다시 확인하세요. 기존 WAMO 분석은 계속 사용할 수 있습니다.';
      const anchor = document.getElementById('wamo-live-status');
      if (anchor) anchor.after(message); else document.body.prepend(message);
    });
})();
