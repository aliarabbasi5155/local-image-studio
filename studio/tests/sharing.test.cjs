const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const staticDir = path.join(__dirname, '../static');

function worker() {
  const buckets = new Map(), listeners = {};
  let now = Date.now(), failAt = Infinity, writes = 0;
  class Clock extends Date { static now() { return now; } }
  const href = key => typeof key === 'string' ? new URL(key, 'https://studio.test').href : key.url;
  const caches = {
    async open(name) {
      if (!buckets.has(name)) buckets.set(name, new Map());
      const data = buckets.get(name);
      return {
        async keys() { return [...data.keys()].map(url => new Request(url)); },
        async match(key) { return data.get(href(key))?.clone(); },
        async put(key, value) { if (++writes === failAt) throw new Error('QuotaExceeded'); data.set(href(key), value.clone()); },
        async delete(key) { return data.delete(href(key)); }
      };
    },
    async keys() { return [...buckets.keys()]; },
    async delete(name) { return buckets.delete(name); }
  };
  let tail = Promise.resolve();
  const context = vm.createContext({Request, Response, FormData, File, Blob, URL, Date:Clock, crypto:globalThis.crypto, caches,
    navigator:{locks:{request(name, task) { const next = tail.then(task); tail = next.catch(() => {}); return next; }}},
    location:{origin:'https://studio.test'},
    self:{addEventListener(name, fn) { listeners[name] = fn; }, clients:{claim:async () => {}}},
    setTimeout, fetch:async () => { throw new Error('offline'); }
  });
  context.importScripts = (...files) => files.forEach(file => vm.runInContext(fs.readFileSync(path.join(staticDir, path.basename(file)), 'utf8'), context));
  vm.runInContext(fs.readFileSync(path.join(staticDir, 'sw.js'), 'utf8'), context);
  const inbox = vm.runInContext('ShareInbox', context);
  return {inbox, caches, listeners, advance() { now += 86400001; }, failSecondWrite() { failAt = writes + 2; }};
}
function share(files) {
  const form = new FormData();
  for (const file of files) form.append('images', file);
  return new Request('https://studio.test/share-target', {method:'POST', body:form});
}
const png = name => new File(['image bytes'], name, {type:'image/png'});
async function dispatch(worker, request) {
  let response;
  worker.listeners.fetch({request, respondWith(promise) { response = promise; }});
  return response;
}

test('worker stages single and multiple shares offline, preserving bytes and Unicode filenames', async () => {
  const w = worker();
  const response = await dispatch(w, share([png('عکس.png'), png('second.png')]));
  assert.equal(response.status, 303);
  assert.equal(response.headers.get('Location'), 'https://studio.test/?shared=1');
  const files = await w.inbox.list();
  assert.equal(files.length, 2);
  assert.equal(files[0].name, 'عکس.png');
  assert.equal(await files[0].response.text(), 'image bytes');
  await w.inbox.remove(files[0].key);
  assert.equal((await w.inbox.list()).length, 1);
});

test('text-only, unsupported, empty and oversized shares leave no partial inbox', async () => {
  const w = worker();
  for (const [files, error] of [
    [[], 'empty'], [[new File(['HEIC'], 'image.heic', {type:'image/heic'})], 'type'],
    [[png('good.png'), new File([], 'empty.png', {type:'image/png'})], 'size'],
    [[new File([new Uint8Array(20 * 1024 * 1024 + 1)], 'large.png', {type:'image/png'})], 'size']
  ]) {
    const response = await dispatch(w, share(files));
    assert.equal(response.headers.get('Location'), 'https://studio.test/?share-error=' + error);
    assert.equal((await w.inbox.list()).length, 0);
  }
});

test('unknown MIME can use a supported extension but unrelated MIME cannot', async () => {
  const w = worker();
  await w.inbox.receive(share([new File(['jpeg bytes'], 'PHOTO.JPG')]));
  assert.equal((await w.inbox.list()).length, 1);
  await assert.rejects(w.inbox.receive(share([new File(['text'], 'photo.jpg', {type:'text/plain'})])), /type/);
});

test('concurrent shares respect the queue limit and expiration frees space', async () => {
  const w = worker();
  const results = await Promise.allSettled([
    w.inbox.receive(share(Array.from({length:6}, (_,i) => png(i + '.png')))),
    w.inbox.receive(share(Array.from({length:6}, (_,i) => png(i + '.png'))))
  ]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal((await w.inbox.list()).length, 6);
  w.advance();
  assert.equal((await w.inbox.list()).length, 0);
  await w.inbox.receive(share([png('fresh.png')]));
  assert.equal((await w.inbox.list()).length, 1);
});

test('storage failure rolls back the new batch, retaining previous shares', async () => {
  const w = worker();
  await w.inbox.receive(share([png('previous.png')]));
  w.failSecondWrite();
  const response = await dispatch(w, share([png('one.png'), png('two.png')]));
  assert.equal(response.headers.get('Location'), 'https://studio.test/?share-error=storage');
  const files = await w.inbox.list();
  assert.equal(files.length, 1);
  assert.equal(files[0].name, 'previous.png');
});

test('worker upgrades keep pending shares and unrelated caches', async () => {
  const w = worker();
  await w.inbox.receive(share([png('pending.png')]));
  await w.caches.open('studio-shell-v3'); await w.caches.open('other-app');
  let finished;
  w.listeners.activate({waitUntil(promise) { finished = promise; }});
  await finished;
  assert.equal((await w.inbox.list()).length, 1);
  assert.equal((await w.caches.keys()).includes('studio-shell-v3'), false);
  assert.equal((await w.caches.keys()).includes('other-app'), true);
});

test('worker does not intercept cross-origin or normal API uploads', async () => {
  const w = worker();
  assert.equal(await dispatch(w, new Request('https://other.test/share-target', {method:'POST'})), undefined);
  assert.equal(await dispatch(w, new Request('https://studio.test/api/uploads', {method:'POST'})), undefined);
});
