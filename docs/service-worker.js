
const CACHE = 'stm-v1';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { self.clients.claim(); });
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache live/favorites data
  event.respondWith(
    caches.open(CACHE).then(function (cache) {
      return cache.match(event.request).then(function (cached) {
        const fetchPromise = fetch(event.request).then(function (res) {
          if (res.ok) cache.put(event.request, res.clone());
          return res;
        }).catch(function () { return cached; });
        return cached || fetchPromise;
      });
    })
  );
});
