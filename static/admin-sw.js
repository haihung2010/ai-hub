/* AI Hub Admin — Service Worker (DISABLED)
   Previous cache-first SW caused stale-file hell. Now a no-op that
   immediately unregisters itself so the browser drops the old cache
   and always fetches fresh files from the network. */

self.addEventListener('install', () => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        Promise.all([
            // Delete ALL old caches
            caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k)))),
            // Unregister this SW so future page loads don't run SW code
            self.registration.unregister(),
        ])
    ).then(() => self.clients.claim());
});

// Pass-through fetch — no caching
self.addEventListener('fetch', (event) => {
    // Let the browser handle it normally (network)
    return;
});
