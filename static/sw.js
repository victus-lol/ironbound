/* IRONBOUND — service worker.
   Caches the static app shell (CSS/JS/icons/manifest) plus the three public
   pages (/, /login, /signup) so the app loads with no connection. Private,
   per-user pages are never intercepted — data and auth always come from the
   server. Navigations are network-first so fresh content wins when online. */

const CACHE = 'ironbound-v7';
const VERSION = 15;
const CORE_STATIC = [
  '/static/css/style.css?v=' + VERSION,
  '/static/js/main.js?v=' + VERSION,
  '/static/js/theme.js?v=' + VERSION,
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/vendor/chart.umd.min.js'
];
const CORE_PAGES = ['/', '/login', '/signup', '/offline'];
const OFFLINE_PAGE = '/offline';

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      var jobs = CORE_STATIC.map(function (url) {
        return cache.add(url).catch(function () {});
      });
      CORE_PAGES.forEach(function (url) {
        jobs.push(fetch(url).then(function (res) {
          if (res && res.ok) return cache.put(url, res.clone());
        }).catch(function () {}));
      });
      return Promise.all(jobs);
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

  // Static assets: cache-first, then fetch + runtime-cache.
  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        if (hit) return hit;
        return fetch(request).then(function (res) {
          if (res && res.ok) {
            var copy = res.clone();
            caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
          }
          return res;
        });
      })
    );
    return;
  }

  // Navigations: network-first, fall back to the cached page or /offline.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then(function (res) {
        if (res && res.ok && (url.pathname === '/' || url.pathname === '/login' ||
                              url.pathname === '/signup' || url.pathname === '/offline')) {
          var copy = res.clone();
          caches.open(CACHE).then(function (cache) { cache.put(url.pathname, copy); });
        }
        return res;
      }).catch(function () {
        return caches.match(url.pathname).then(function (hit) {
          if (hit) return hit;
          return caches.match(OFFLINE_PAGE);
        });
      })
    );
  }
});