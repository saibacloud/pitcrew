// PitCrew service worker — network-first with offline shell fallback
const CACHE_NAME = 'pitcrew-v3';
const SHELL_ASSETS = [
    '/',
    '/static/style.css',
    '/static/js/app.js',
    '/static/js/dialogs.js',
    '/static/js/garage.js',
    '/static/js/car.js',
    '/static/js/journal.js',
    '/static/js/pins.js',
    '/static/js/cart.js',
    '/static/js/manuals.js',
    '/static/manifest.json',
];

// Pre-cache shell on install
self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(SHELL_ASSETS))
            .then(() => self.skipWaiting())
    );
});

// Clean old caches on activate
self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

// Network-first for API calls, cache-first for static assets
self.addEventListener('fetch', e => {
    const url = new URL(e.request.url);

    // API calls — always network, never cache
    if (url.pathname.startsWith('/api/')) {
        return;
    }

    // Static assets — network first, fall back to cache
    e.respondWith(
        fetch(e.request)
            .then(res => {
                // Cache successful responses for offline use
                if (res.ok) {
                    const clone = res.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
                }
                return res;
            })
            .catch(() => caches.match(e.request))
    );
});
