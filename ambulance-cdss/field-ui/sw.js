/* Ambulance CDSS — Field Console Service Worker (EPIC 3.5 + Item 1)
 *
 * Minimal offline-first service worker. Caches static assets
 * (HTML, CSS, JS, config) so the field console loads from cache
 * when the device is offline. API calls for reads use stale-while-revalidate
 * for protocol state; writes go through the app-level write queue.
 */

// Bump this version string on every deployment to bust caches.
const CACHE_VERSION = "v3";
const CACHE_NAME = `field-cdss-${CACHE_VERSION}`;
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/config.js",
  "/manifest.json",
];

// API responses to cache with stale-while-revalidate
const API_CACHE = `field-cdss-api-${CACHE_VERSION}`;
const API_CACHE_PATHS = [
  "/field-protocols",
  "/field-protocol/state",
];

// Install: pre-cache static assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)),
  );
  self.skipWaiting();
});

// Activate: clean up old caches on version bump
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME && key !== API_CACHE)
          .map((key) => caches.delete(key)),
      ),
    ),
  );
  self.clients.claim();
})

// Fetch: cache-first for static assets, network-first for API
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // API calls: network-first with stale-while-revalidate for read-only endpoints
  if (url.pathname.startsWith("/incidents") || url.pathname.startsWith("/health") || url.pathname.startsWith("/field-protocols")) {
    // GET requests to cacheable API paths: stale-while-revalidate
    if (request.method === "GET" && API_CACHE_PATHS.some(p => url.pathname.includes(p))) {
      event.respondWith(
        caches.open(API_CACHE).then(async (cache) => {
          const cached = await cache.match(request);
          const networkPromise = fetch(request).then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          }).catch(() => cached);
          return cached || networkPromise;
        })
      );
      return;
    }
    // All other API calls: network only (POST writes go through write queue)
    event.respondWith(
      fetch(request).catch(() =>
        new Response(JSON.stringify({ error: "offline" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok && request.method === "GET") {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      });
    }),
  );
});

// Listen for messages from the app (sync triggers)
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
