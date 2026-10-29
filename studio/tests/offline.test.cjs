const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const staticDir = path.join(__dirname, '../static');

function worker(network = async () => { throw new Error('offline'); }) {
  const buckets = new Map(), listeners = {};
  const href = key => typeof key === 'string' ? new URL(key, 'https://studio.test').href : key.url;
  const caches = {
    async open(name) {
      if (!buckets.has(name)) buckets.set(name, new Map());
      const data = buckets.get(name);
      return {
        async keys() { return [...data.keys()].map(url => new Request(url)); },
        async match(key) { return data.get(href(key))?.clone(); },
        async put(key, value) { data.set(href(key), value.clone()); },
        async delete(key) { return data.delete(href(key)); }
      };
    },
    async keys() { return [...buckets.keys()]; },
    async delete(name) { return buckets.delete(name); }
  };
  const context = vm.createContext({Request, Response, Headers, FormData, File, Blob, URL, structuredClone, caches, crypto:globalThis.crypto,
    navigator:{locks:{request:(name, task) => task()}}, location:{origin:'https://studio.test'}, setTimeout, fetch:network,
    self:{addEventListener(name, fn) { listeners[name] = fn; }, clients:{claim:async () => {}}}});
  context.importScripts = (...files) => files.forEach(file => vm.runInContext(fs.readFileSync(path.join(staticDir, path.basename(file)), 'utf8'), context));
  vm.runInContext(fs.readFileSync(path.join(staticDir, 'sw.js'), 'utf8'), context);
  return {store:vm.runInContext('OfflineCopy', context), caches, listeners};
}
const {store} = worker();
// Values from the worker's realm have their own prototypes, so compare them as data.
const plain = value => JSON.parse(JSON.stringify(value));
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
function version(n, project, extra = {}) {
  return {id:id(n), project_id:id(project), parent_id:null, source_id:null, kind:'image', created:n, sequence:0, deleted:0, favorite:false,
    status:'complete', ready:true, width:64, height:64, prompt:`Prompt ${n}`, recipe:{prompt:`Prompt ${n}`}, url:`/media/image/${id(n)}`,
    thumbnail:`/media/thumb/${id(n)}`, job:null, parent:null, ...extra};
}
const child = (n, project, parent, source, extra) => version(n, project, {parent_id:id(parent), source_id:id(source), sequence:n - project,
  parent:{id:id(parent), sequence:parent - project, deleted:false, created:parent, url:`/media/upload/${id(source)}`}, ...extra});
// Project 1: an original with an edit and an edit of that edit. Project 10: a creation still being made.
function library() {
  const projects = [{id:id(1), created:1, root_id:id(1), title:'Harbor', favorite:false, archived:false,
      versions:[version(1, 1, {kind:'upload', url:`/media/upload/${id(1)}`}), child(2, 1, 1, 1), child(3, 1, 2, 90)]},
    {id:id(10), created:10, root_id:id(10), title:'Fox', favorite:false, archived:false,
      versions:[version(10, 10, {status:'running', ready:false, url:null, thumbnail:null, job:{status:'running'}})]}];
  return {projects:store.view({projects}, store.empty())};
}

test('favorites and archiving are wanted states that clear when set back', () => {
  const copy = library(), pending = store.empty();
  assert.equal(store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}/versions/${id(2)}`, {favorite:true}).status, 200);
  assert.deepEqual(plain(pending.favorites), {[id(2)]:true});
  assert.equal(store.view(copy, pending)[0].favorite_count, 1);
  store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}/versions/${id(2)}`, {favorite:false});
  store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}`, {archived:true});
  assert.deepEqual(plain(store.library(store.view(copy, pending), 'archive').navigation.map(p => p.id)), [id(1)]);
  store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}`, {archived:false});
  assert.equal(store.size(pending), 0);
});

test('offline deletion keeps comparisons, refuses running work and remembers what the phone knew', () => {
  const copy = library(), pending = store.empty();
  store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}`, {favorite:true});
  store.change(copy, pending, 'DELETE', `/api/projects/${id(1)}/versions/${id(2)}`, {confirm:true});
  let harbor = store.detail(copy, store.view(copy, pending), id(1));
  assert.deepEqual(plain(harbor.versions.map(v => v.id)), [id(1), id(3)]);
  // The deleted parent is still saved on this phone, so the comparison uses it rather than the Mac's source copy.
  assert.deepEqual(plain([harbor.versions[1].parent.deleted, harbor.versions[1].parent.url]), [true, `/media/image/${id(2)}`]);
  assert.equal(pending.favorites[id(2)], undefined);
  store.change(copy, pending, 'DELETE', `/api/projects/${id(1)}/versions/${id(1)}`, {confirm:true});
  harbor = store.view(copy, pending)[0];
  assert.deepEqual(plain(harbor.root), {id:id(1), url:`/media/upload/${id(1)}`, ready:true, recovered:true});
  assert.equal(store.library([harbor]).projects[0].cover.id, id(3));
  assert.equal(store.change(copy, pending, 'DELETE', `/api/projects/${id(10)}`, {confirm:true}).status, 409);
  assert.equal(store.change(copy, pending, 'POST', '/api/bulk', {scope:'projects', ids:[id(10)], action:'delete', confirm:true}).status, 409);
  assert.equal(store.change(copy, pending, 'DELETE', `/api/projects/${id(1)}`, {}).status, 400);
  store.change(copy, pending, 'DELETE', `/api/projects/${id(1)}`, {confirm:true});
  assert.deepEqual(plain(pending), {archived:{}, favorites:{}, deletedVersions:{}, deletedProjects:{[id(1)]:[id(1), id(2), id(3)]}});
  assert.equal(store.change(copy, pending, 'PATCH', `/api/projects/${id(1)}`, {favorite:true}).status, 404);
  assert.equal(store.change(copy, pending, 'POST', '/api/generate', {}), null);
});

test('media list saves thumbnails first and only the comparison copies the phone lacks', () => {
  const copy = library();
  assert.deepEqual(plain(store.mediaPaths(copy.projects, [{url:'/media/video/v'}, {url:null}])), [
    `/media/thumb/${id(1)}`, `/media/thumb/${id(2)}`, `/media/thumb/${id(3)}`,
    `/media/upload/${id(1)}`, `/media/image/${id(2)}`, `/media/image/${id(3)}`, '/media/video/v']);
  copy.projects[0].versions.splice(1, 1);
  assert.ok(store.mediaPaths(copy.projects).includes(`/media/upload/${id(90)}`));
});

test('the copy and its changes persist in cache storage until the studio is locked', async () => {
  const w = worker();
  await w.store.write('copy', {projects:[]}); await w.store.write('pending', w.store.empty());
  await (await w.caches.open(w.store.media)).put('/media/thumb/x', new Response('thumb'));
  assert.deepEqual(plain(await w.store.read('copy')), {projects:[]});
  let finished;
  w.listeners.activate({waitUntil(promise) { finished = promise; }});
  await finished;
  assert.deepEqual(plain(await w.store.read('copy')), {projects:[]});
  await w.store.erase();
  assert.deepEqual(plain(await w.caches.keys()), []);
  assert.equal(await w.store.read('copy'), null);
});

test('worker serves saved media with download names and byte ranges, leaving JPG copies to the Mac', async () => {
  const requested = [];
  const w = worker(async request => { requested.push(request.url); return new Response('from the Mac'); });
  const media = await w.caches.open(w.store.media);
  await media.put(`/media/image/${id(2)}`, new Response('0123456789', {headers:{'Content-Type':'image/png'}}));
  const fetch = async (url, headers) => { let response; w.listeners.fetch({request:new Request(url, {headers}), respondWith(value) { response = value; }}); return response; };
  const download = await fetch(`https://studio.test/media/image/${id(2)}?download=1`);
  assert.equal(download.headers.get('Content-Disposition'), `attachment; filename="studio-${id(2)}.png"`);
  assert.equal(await download.text(), '0123456789');
  for (const [range, expected, status] of [['bytes=2-4', '234', 206], ['bytes=7-', '789', 206], ['bytes=-2', '89', 206], ['bytes=20-', '', 416]]) {
    const partial = await fetch(`https://studio.test/media/image/${id(2)}`, {Range:range});
    assert.equal(partial.status, status, range);
    assert.equal(await partial.text(), expected, range);
  }
  assert.equal((await fetch(`https://studio.test/media/image/${id(2)}`, {Range:'bytes=7-'})).headers.get('Content-Range'), 'bytes 7-9/10');
  assert.equal(await (await fetch(`https://studio.test/media/thumb/${id(3)}`)).text(), 'from the Mac');
  assert.equal(await fetch(`https://studio.test/media/image/${id(2)}?download=1&format=jpg`), undefined);
  assert.deepEqual(requested, [`https://studio.test/media/thumb/${id(3)}`]);
});
