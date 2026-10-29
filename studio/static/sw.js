'use strict';
// Keeps the app shell available when the phone is away from home Wi-Fi, and serves the images the installed
// app saved for offline use (offline-store.js). Library data otherwise comes from the Mac, and
// generation requests always go to it. Incoming shares have their own temporary inbox.
importScripts('/static/share-store.js', '/static/offline-store.js');
const CACHE = 'studio-shell-v7';
const SHELL = ['/', '/static/style.css', '/static/projects.css', '/static/masking.css', '/static/automatic.css',
  '/static/polish.css', '/static/mask-workspace.css', '/static/identity.css', '/static/app.js', '/static/projects.js', '/static/automatic.js',
  '/static/masking.js', '/static/identity.js', '/static/actions.js', '/static/share-store.js', '/static/sharing.js', '/static/sharing.css',
  '/static/manifest.json', '/static/icon.svg', '/static/icon-192.png', '/static/video.js', '/static/video.css',
  '/static/offline-store.js', '/static/offline.js', '/static/offline.css'];
const MEDIA = /^\/media\/(image|upload|thumb|video)\/([0-9a-f-]{36})$/;

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL.map(path => new Request(path, {cache:'reload'})))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('studio-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
// Saved media never changes once made, so the phone's copy answers first, at home too.
async function savedMedia(request, url) {
  const saved = await (await caches.open(OfflineCopy.media)).match(url.pathname);
  if (!saved) return fetch(request);
  const headers = new Headers(saved.headers), [, kind, id] = MEDIA.exec(url.pathname);
  if (url.searchParams.get('download') === '1')
    headers.set('Content-Disposition', `attachment; filename="${kind === 'video' ? `${id}.mp4` : `studio-${id}${kind === 'thumb' ? '.webp' : '.png'}`}"`);
  // Video players ask for byte ranges, and Safari plays nothing without them.
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get('Range') || '');
  if (!range || (!range[1] && !range[2])) return new Response(saved.body, {headers});
  const blob = await saved.blob(), size = blob.size;
  const start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
  const end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
  if (start > end) return new Response(null, {status:416, headers:{'Content-Range':`bytes */${size}`}});
  headers.set('Content-Range', `bytes ${start}-${end}/${size}`); headers.set('Content-Length', String(end - start + 1)); headers.set('Accept-Ranges', 'bytes');
  return new Response(blob.slice(start, end + 1), {status:206, headers});
}
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin === location.origin && url.pathname === '/share-target' && event.request.method === 'POST') {
    event.respondWith(ShareInbox.receive(event.request)
      .then(() => Response.redirect(new URL('/?shared=1', location.origin), 303))
      .catch(error => Response.redirect(new URL('/?share-error=' + (['empty','limit','size','type'].includes(error.message) ? error.message : 'storage'), location.origin), 303)));
    return;
  }
  if (event.request.method === 'GET' && url.origin === location.origin && MEDIA.test(url.pathname) && !url.searchParams.has('format')) {
    event.respondWith(savedMedia(event.request, url));
    return;
  }
  const navigation = event.request.mode === 'navigate' && url.pathname === '/';
  if (event.request.method !== 'GET' || url.origin !== location.origin || !(navigation || SHELL.includes(url.pathname))) return;
  const key = navigation ? '/' : url.pathname;
  // Network first, so the newest app always loads at home; fall back quickly when the Mac is out of reach.
  const network = fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(key, copy)); }
    return response;
  });
  const timeout = new Promise((resolve, reject) => setTimeout(reject, 4000));
  event.respondWith(Promise.race([network, timeout]).catch(() => caches.match(key).then(cached => cached || network)));
});
