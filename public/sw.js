// ParaFlight Jet Tracker 1 service worker.
// The app needs a signed-in user, so pages are never served from cache: navigations, /api/*, sign-in/sign-up/admin
// always go to the network (the server checks the session). Only static assets fetched while signed in are cached,
// for speed. Signing out sends Clear-Site-Data, which also empties this cache.
const C = 'jt1-v15';
const NOCACHE = /^\/(api\/|login|signup|logout|admin|healthz)/;
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(k => Promise.all(k.filter(x => x !== C).map(x => caches.delete(x))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const req = e.request, u = new URL(req.url);
  if (req.method !== 'GET' || u.origin !== location.origin || req.mode === 'navigate' || NOCACHE.test(u.pathname) || u.pathname === '/' || u.pathname.endsWith('.html')) return;
  if (!/\.(js|css|png|webmanifest|woff2?)$/.test(u.pathname)) return;
  e.respondWith(fetch(req).then(r => {
    if (r.ok && !r.redirected && r.type === 'basic') { const cp = r.clone(); caches.open(C).then(c => c.put(req, cp)); }
    return r;
  }).catch(() => caches.match(req)));
});
