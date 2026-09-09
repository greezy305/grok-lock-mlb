const CACHE = "lock-picker-v31";
const ASSETS = ["./manifest.json", "./icon.png", "./apple-touch-icon.png"];
// index.html + picks.json always network-first so logo/UI updates show immediately
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const path = url.pathname || "";
  const isIndex = path.endsWith("/") || path.endsWith("/index.html");
  const isPicks = path.endsWith("picks.json");
  if (isIndex || isPicks) {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request))
  );
});
