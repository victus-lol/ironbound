/* IRONBOUND — service worker.
   Registered from /static, so it only controls requests under /static:
   it caches CSS/JS/icons/manifest for fast repeat loads and offline static
   availability. Dynamic pages are never intercepted, so auth and fresh data
   are always served straight from the server. */

const CACHE = 'ironbound-v4';
const CORE = [
  '/static/css/style.css?v=10',
  '/static/js/main.js?v=10',
  '/static/js/theme.js?v=10',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(CORE);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;
  var url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.indexOf('/static/') !== 0) return;
  event.respondWith(
    caches.match(request).then(function (hit) {
      if (hit) return hit;
      return fetch(request).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        return res;
      });
    })
  );
});