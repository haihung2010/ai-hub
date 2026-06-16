/* AI Hub Admin — Service Worker
   Cache-first for shell, network-first for /v1 API. */

const CACHE_VERSION = 'aihub-admin-v8';
const SHELL_ASSETS = [
    '/admin.html',
    '/admin.css',
    '/admin.js',
    '/manifest.json',
    '/icon-192.png',
    '/icon-512.png',
    'https://cdn.jsdelivr.net/npm/chart.js',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_VERSION).then((cache) =>
            cache.addAll(SHELL_ASSETS).catch(() => null)
        ).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);

    // API + admin endpoints: network-first, no cache fallback (live data only)
    if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/health')) {
        event.respondWith(fetch(req).catch(() => new Response(JSON.stringify({ error: 'offline' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
        })));
        return;
    }

    // Static shell + fonts: cache-first, fallback to network, then cache the response
    event.respondWith(
        caches.match(req).then((cached) => {
            if (cached) return cached;
            return fetch(req).then((res) => {
                if (!res || res.status !== 200 || res.type === 'opaqueredirect') return res;
                const cloned = res.clone();
                if (req.url.startsWith(self.location.origin) || req.url.includes('jsdelivr.net') || req.url.includes('fonts.googleapis') || req.url.includes('fonts.gstatic')) {
                    caches.open(CACHE_VERSION).then((cache) => cache.put(req, cloned));
                }
                return res;
            }).catch(() => caches.match('/admin.html'));
        })
    );
});
