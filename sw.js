const CACHE = "lock-picker-v8";
const ASSETS = ["./index.html", "./manifest.json", "./icon.png", "./apple-touch-icon.png"];
// Do NOT precache picks.json — always network for live board
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) {
    return; // MLB / external APIs — browser default
  }
  // picks.json always network-first, never serve stale board
  if (url.pathname.endsWith("/picks.json") || url.pathname.endsWith("picks.json")) {
    e.respondWith(
      fetch(e.request, { cache: "no-store" }).catch(() => caches.match(e.request))
    );
    return;
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
