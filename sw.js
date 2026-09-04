const CACHE = "lock-picker-v3";
const ASSETS = ["./index.html", "./picks.json", "./manifest.json", "./icon.png", "./apple-touch-icon.png"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  // Only cache same-origin app assets — never intercept MLB / time APIs
  if (url.origin !== self.location.origin) {
    return; // default browser fetch
  }
  e.respondWith(
    fetch(e.request).then(r => {
      if (e.request.method === "GET" && r.ok) {
        const copy = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});
