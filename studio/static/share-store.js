'use strict';
// Only incoming shares and offline uploads are staged here; the library copy lives in offline-store.js.
// Shared by the worker and page so a PIN prompt, reload or offline launch cannot lose a photo.
const ShareInbox = (() => {
  const cacheName = 'studio-share-inbox-v1';
  const maxFile = 20 * 1024 * 1024, maxTotal = 100 * 1024 * 1024;
  const lifetime = 24 * 60 * 60 * 1000;
  const exclusive = task => navigator.locks.request('studio-share-inbox', task);
  async function list() {
    const cache = await caches.open(cacheName), files = [];
    for (const key of await cache.keys()) {
      const response = await cache.match(key);
      if (!response) continue;
      if (Date.now() - Number(response.headers.get('X-Shared-At')) >= lifetime) {
        await cache.delete(key); continue;
      }
      files.push({key:key.url, name:decodeURIComponent(response.headers.get('X-File-Name')),
        size:Number(response.headers.get('X-File-Size')), response});
    }
    return files;
  }
  async function receive(request) {
    const form = await request.formData();
    return keep(form.getAll('images').filter(value => value instanceof File));
  }
  async function keep(files) {
    if (!files.length) throw new Error('empty');
    if (files.length > 10) throw new Error('limit');
    for (const file of files) {
      if (!file.size || file.size > maxFile) throw new Error('size');
      if (!['image/png','image/jpeg','image/webp'].includes(file.type) &&
          !((!file.type || file.type === 'application/octet-stream') && /\.(png|jpe?g|webp)$/i.test(file.name))) throw new Error('type');
    }
    return exclusive(async () => {
      const pending = await list();
      if (pending.length + files.length > 10 ||
          [...pending,...files].reduce((sum,file) => sum + file.size, 0) > maxTotal) throw new Error('limit');
      const cache = await caches.open(cacheName), written = [];
      try {
        for (const file of files) {
          const key = new URL('/__studio_shared/' + crypto.randomUUID(), location.origin).href;
          await cache.put(key, new Response(file, {headers:{'Content-Type':file.type || 'application/octet-stream',
            'X-File-Name':encodeURIComponent(file.name || 'Shared photo'), 'X-File-Size':String(file.size), 'X-Shared-At':String(Date.now())}}));
          written.push(key);
        }
      } catch (error) {
        await Promise.all(written.map(key => cache.delete(key)));
        throw error;
      }
    });
  }
  async function remove(key) { return (await caches.open(cacheName)).delete(key); }
  return {cacheName, exclusive, list, receive, keep, remove};
})();
