// Anjo service worker — network-first for API, cache-first for static assets.
const CACHE = 'anjo-v1';
const STATIC = [
  '/chat',
  '/static/manifest.json',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Always network-first for API and SSE streams
  if (url.pathname.startsWith('/api/') || e.request.headers.get('accept') === 'text/event-stream') {
    e.respondWith(fetch(e.request));
    return;
  }

  // Cache-first for static assets (fonts, icons, sw itself)
  if (url.pathname.startsWith('/static/') || url.pathname === '/sw.js') {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }))
    );
    return;
  }

  // Network-first for HTML pages
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
