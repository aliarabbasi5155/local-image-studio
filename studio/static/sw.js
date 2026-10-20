'use strict';
// Keeps the app shell available when the phone is away from home Wi-Fi. Library data, images and
// generation requests always go to the Mac. Incoming shares have their own temporary inbox.
importScripts('/static/share-store.js');
const CACHE = 'studio-shell-v4';
const SHELL = ['/', '/static/style.css', '/static/projects.css', '/static/masking.css', '/static/automatic.css',
  '/static/polish.css', '/static/mask-workspace.css', '/static/identity.css', '/static/app.js', '/static/projects.js', '/static/automatic.js',
  '/static/masking.js', '/static/identity.js', '/static/actions.js', '/static/share-store.js', '/static/sharing.js', '/static/sharing.css',
  '/static/manifest.json', '/static/icon.svg', '/static/icon-192.png'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL.map(path => new Request(path, {cache:'reload'})))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('studio-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin === location.origin && url.pathname === '/share-target' && event.request.method === 'POST') {
    event.respondWith(ShareInbox.receive(event.request)
      .then(() => Response.redirect(new URL('/?shared=1', location.origin), 303))
      .catch(error => Response.redirect(new URL('/?share-error=' + (['empty','limit','size','type'].includes(error.message) ? error.message : 'storage'), location.origin), 303)));
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
