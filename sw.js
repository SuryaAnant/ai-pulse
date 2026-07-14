// AI Pulse service worker — app shell cached, briefing data network-first.
const SHELL_CACHE = "aipulse-shell-v1";
const DATA_CACHE = "aipulse-data-v1";
const SHELL = ["./", "./index.html", "./manifest.json", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => ![SHELL_CACHE, DATA_CACHE].includes(k)).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return; // external links/images: straight to network

  if (url.pathname.includes("/data/")) {
    // Briefing data: fresh when online, cached briefing when offline.
    e.respondWith(
      fetch(e.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(DATA_CACHE).then((c) => c.put(stripQuery(e.request), copy));
          return resp;
        })
        .catch(() => caches.match(stripQuery(e.request)))
    );
    return;
  }

  // Shell: cache-first.
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request)));
});

function stripQuery(request) {
  const u = new URL(request.url);
  u.search = "";
  return u.toString();
}
