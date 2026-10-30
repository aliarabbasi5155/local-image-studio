'use strict';
// Away from home the installed app answers from its copy of the library (offline-store.js). Browsing and
// library changes keep working; changes wait on this phone until the computer is reachable. Anything the computer
// has to compute is refused with a clear message. Loaded before app.js, so nothing here runs on load.
const connection = {offline:false, copy:null, pending:null, view:null, probe:null, reconnecting:false,
  syncing:false, again:false, synced:0, signature:'', progress:null, full:false};
const lockedKey = 'studio-offline-locked', checkedKey = 'studio-offline-checked';
const installedApp = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const offlineCapable = () => window.isSecureContext && 'caches' in window && !!navigator.locks;
const keepsCopy = () => offlineCapable() && installedApp();
const pendingCount = () => OfflineCopy.size(connection.pending);
function offlineLocked() { try { return localStorage.getItem(lockedKey) === '1'; } catch { return false; } }
function setOfflineLocked(locked) { try { if (locked) localStorage.setItem(lockedKey, '1'); else localStorage.removeItem(lockedKey); } catch { /* The copy simply stays usable. */ } }
const macMessages = [
  [/^\/api\/(generate|estimate)$/, 'Creating and editing need your computer. Your prompt stays saved for later.'],
  [/^\/api\/(uploads|masks|images\/[^/]+\/edit-source|projects\/[^/]+\/versions\/[^/]+\/edit-source)/, 'Editing needs your computer. Join your home Wi-Fi to continue.'],
  [/^\/api\/videos/, 'Making videos needs your computer. Your saved clips still play here.'],
  [/^\/api\/bulk-download$/, 'ZIP downloads are made by your computer.'],
  [/^\/api\/jobs\/[^/]+\/cancel$/, 'Stopping an image needs your computer.']
];
function macNeeded(path) {
  const error = new Error((macMessages.find(([pattern]) => pattern.test(path)) || [, 'This needs your computer. Join your home Wi-Fi, then try again.'])[1]);
  error.status = 503; error.offline = true;
  return error;
}

async function openOfflineCopy() {
  if (connection.copy) return connection.copy;
  if (!offlineCapable() || offlineLocked()) return null;
  try {
    const [copy, pending] = await Promise.all([OfflineCopy.read('copy'), OfflineCopy.read('pending')]);
    if (!copy) return null;
    Object.assign(connection, {copy, pending:pending || OfflineCopy.empty()});
    connection.view = OfflineCopy.view(copy, connection.pending);
    return copy;
  } catch { return null; }
}
async function forgetOfflineCopy() {
  clearTimeout(connection.probe);
  Object.assign(connection, {offline:false, copy:null, pending:null, view:null, progress:null, signature:''});
  document.body.classList.remove('is-offline');
  try { await OfflineCopy.erase(); localStorage.removeItem(checkedKey); } catch { /* Nothing was saved on this phone. */ }
}
function goOffline() {
  if (connection.offline) return;
  connection.offline = true;
  renderConnection(); watchForMac();
  if (state.authenticated) toast('Your computer is out of reach. Showing the copy saved on this phone.');
}

async function offlineRequest(path, options = {}) {
  const copy = await openOfflineCopy();
  if (!copy) { const error = new Error('Can’t reach your computer. Join your home Wi-Fi and keep the computer awake, then try again.'); error.unreachable = true; throw error; }
  const method = (options.method || 'GET').toUpperCase(), url = new URL(path, location.origin), route = url.pathname, query = url.searchParams;
  const number = (name, fallback) => Number.parseInt(query.get(name) ?? fallback, 10) || 0;
  const offset = Math.max(0, number('offset', 0)), limit = Math.min(1000, Math.max(1, number('limit', 48))), search = (query.get('q') || '').slice(0, 200);
  if (method === 'GET') {
    if (route === '/api/auth') return {authenticated:true, pin_required:!!copy.options?.pin_required, offline:true};
    if (route === '/api/options') return {...copy.options, engine_ready:false, engine_state:'away', auto_start:false};
    if (route === '/api/jobs') return {jobs:[]};
    if (route === '/api/projects') return OfflineCopy.library(connection.view, query.get('section') || 'library', search, offset, limit);
    if (route === '/api/favorites') return OfflineCopy.favorites(connection.view, search, offset, limit);
    if (route === '/api/photos') return OfflineCopy.images(connection.view, query.get('section') || 'library', search, offset, limit);
    if (route === '/api/videos') return {videos:(copy.videos || []).filter(v => !connection.pending.deletedVideos?.[v.id])};
    if (route === '/api/videos/options') return {ready:false, model:'', error:'Making videos needs your computer. Your saved clips still play here.'};
    const project = route.match(/^\/api\/projects\/([^/]+)$/);
    if (project) {
      const result = OfflineCopy.detail(copy, connection.view, project[1]);
      if (result) return result;
      const error = new Error('This project isn’t in the copy on this phone.'); error.status = 404; throw error;
    }
    throw macNeeded(route);
  }
  if (route === '/api/logout' && method === 'POST') { setOfflineLocked(true); return {ok:true}; }
  const body = typeof options.body === 'string' ? JSON.parse(options.body) : null;
  return navigator.locks.request('studio-offline-pending', async () => {
    const pending = await OfflineCopy.read('pending') || OfflineCopy.empty();
    const result = OfflineCopy.change(copy, pending, method, route, body);
    if (!result) throw macNeeded(route);
    if (result.status >= 400) { const error = new Error(result.body.error); error.status = result.status; throw error; }
    await OfflineCopy.write('pending', pending);
    Object.assign(connection, {pending, view:OfflineCopy.view(copy, pending)});
    renderOfflineStatus();
    return result.body;
  });
}

// Changes are wanted states, so resending one the computer already has is harmless.
async function sendPending() {
  if (!offlineCapable()) return null;
  return navigator.locks.request('studio-offline-pending', async () => {
    const pending = await OfflineCopy.read('pending');
    if (!OfflineCopy.size(pending)) return null;
    const copy = await OfflineCopy.read('copy'), owners = new Map((copy?.projects || []).flatMap(p => p.versions.map(v => [v.id, p.id])));
    const before = OfflineCopy.size(pending), problems = [];
    const call = async (path, method = 'GET', body) => {
      const response = await fetch(path, {method, credentials:'same-origin', cache:'no-store', headers:body ? {'Content-Type':'application/json'} : {}, body:body && JSON.stringify(body)});
      if (response.status === 401 || response.status >= 500) throw new Error(response.status === 401 ? 'Enter your studio PIN to send your offline changes.' : 'Your computer couldn’t take the offline changes yet.');
      return response;
    };
    const report = async response => { if (!response.ok && response.status !== 404) problems.push((await response.json()).error); };
    const save = () => OfflineCopy.write('pending', pending);
    try {
      for (const [id, known] of Object.entries(pending.deletedProjects)) {
        const current = await call(`/api/projects/${id}`);
        if (current.ok) {
          const project = await current.json();
          if (project.versions.some(v => !known.includes(v.id))) problems.push(`“${project.title}” changed on your computer, so it was kept.`);
          else await report(await call(`/api/projects/${id}`, 'DELETE', {confirm:true}));
        }
        delete pending.deletedProjects[id]; await save();
      }
      for (const [id, project] of Object.entries(pending.deletedVersions)) {
        await report(await call(`/api/projects/${project}/versions/${id}`, 'DELETE', {confirm:true}));
        delete pending.deletedVersions[id]; await save();
      }
      for (const id of Object.keys(pending.deletedVideos || {})) {
        await report(await call(`/api/videos/${id}`, 'DELETE', {confirm:true}));
        delete pending.deletedVideos[id]; await save();
      }
      for (const [id, archived] of Object.entries(pending.archived)) {
        await report(await call(`/api/projects/${id}`, 'PATCH', {archived}));
        delete pending.archived[id]; await save();
      }
      for (const value of [true, false]) {
        const ids = Object.keys(pending.favorites).filter(id => pending.favorites[id] === value);
        for (let start = 0; start < ids.length; start += 500) {
          const chunk = ids.slice(start, start + 500);
          // One missing image rejects a bulk change, so fall back to one image at a time.
          if (!(await call('/api/bulk', 'POST', {scope:'images', ids:chunk, action:'favorite', favorite:value})).ok)
            for (const id of chunk) if (owners.has(id)) await call(`/api/projects/${owners.get(id)}/versions/${id}`, 'PATCH', {favorite:value});
          for (const id of chunk) delete pending.favorites[id];
          await save();
        }
      }
    } finally {
      connection.pending = pending;
      if (connection.copy) connection.view = OfflineCopy.view(connection.copy, pending);
    }
    return {sent:before - OfflineCopy.size(pending), problems};
  });
}
function reportSent(result) {
  if (!result) return;
  if (result.problems.length) toast(`${result.problems.length} offline change${result.problems.length === 1 ? '' : 's'} couldn’t be applied: ${result.problems[0]}`);
  else if (result.sent) toast('Your offline changes reached your computer.');
}

function watchForMac() {
  clearTimeout(connection.probe);
  if (connection.offline) connection.probe = setTimeout(() => reconnect(), document.hidden ? 30000 : 10000);
}
async function reconnect() {
  if (!connection.offline || connection.reconnecting) return;
  connection.reconnecting = true;
  try {
    const response = await fetch('/api/auth', {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout?.(4000)});
    const auth = await response.json();
    if (!response.ok) return;
    connection.offline = false; clearTimeout(connection.probe); renderConnection();
    if (!auth.authenticated) { setOfflineLocked(true); showLogin(); return; }
    await enterStudio();
  } catch { /* Still away from home. */ }
  finally { connection.reconnecting = false; watchForMac(); }
}

// Keeps the copy current while the computer is reachable: library details first, then any images not saved yet.
function syncSoon(signature) {
  if (!keepsCopy() || connection.offline || document.hidden) return;
  if (signature === connection.signature && Date.now() - connection.synced < 120000) return;
  connection.signature = signature;
  syncCopy().catch(() => {});
}
async function syncCopy() {
  if (!keepsCopy() || connection.offline || !state.authenticated) return;
  if (connection.syncing) { connection.again = true; return; }
  connection.syncing = true; connection.synced = Date.now();
  try {
    await navigator.locks.request('studio-offline-sync', async () => {
      if (!connection.copy && !offlineLocked()) await openOfflineCopy();
      navigator.storage?.persist?.().catch(() => {});
      const previous = connection.copy;
      const response = await fetch('/api/offline', {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout?.(60000),
        headers:previous?.revision ? {'If-None-Match':previous.revision} : {}});
      if (response.status === 401) { showLogin(); return; }
      if (response.status !== 200 && response.status !== 304) throw new Error('The library copy couldn’t be updated.');
      const {qualities, output_sizes, resize_modes, pin_required, max_variations, lan_url} = state.options;
      const options = {qualities, output_sizes, resize_modes, pin_required, max_variations, lan_url};
      let copy = previous;
      if (response.status === 200) copy = {...await response.json(), revision:response.headers.get('ETag')};
      if (copy !== previous || JSON.stringify(options) !== JSON.stringify(previous?.options)) {
        copy = {...copy, options, saved:Date.now()};
        await OfflineCopy.write('copy', copy);
      }
      const pending = await OfflineCopy.read('pending') || OfflineCopy.empty();
      Object.assign(connection, {copy, pending, view:OfflineCopy.view(copy, pending)});
      setOfflineLocked(false);
      try { localStorage.setItem(checkedKey, String(Date.now())); } catch { /* Shown as unknown. */ }
      await saveMedia(copy);
    });
  } finally {
    connection.syncing = false; renderOfflineStatus();
    if (connection.again) { connection.again = false; syncCopy().catch(() => {}); }
  }
}
async function saveMedia(copy) {
  const cache = await caches.open(OfflineCopy.media), wanted = OfflineCopy.mediaPaths(copy.projects, copy.videos), wantedSet = new Set(wanted);
  const saved = new Set((await cache.keys()).map(request => new URL(request.url).pathname));
  for (const path of saved) if (!wantedSet.has(path)) { await cache.delete(path); saved.delete(path); }
  const missing = wanted.filter(path => !saved.has(path));
  connection.progress = {done:wanted.length - missing.length, total:wanted.length}; connection.full = false;
  renderOfflineStatus();
  let next = 0;
  const worker = async () => {
    while (next < missing.length && !connection.full && !connection.offline && state.authenticated) {
      const path = missing[next++], response = await fetch(path, {credentials:'same-origin', cache:'no-store'});
      if (!response.ok) continue;
      try { await cache.put(path, response); }
      catch (error) { if (error.name === 'QuotaExceededError') { connection.full = true; return; } throw error; }
      connection.progress.done++; renderOfflineStatus();
    }
  };
  await Promise.all([worker(), worker(), worker()]);
}

async function lockStudio(confirmed = false) {
  if (!connection.offline) reportSent(await sendPending().catch(() => null));
  const waiting = pendingCount();
  if (waiting && !confirmed) return toast(`${waiting} offline change${waiting === 1 ? '' : 's'} haven’t reached your computer. Locking removes them from this phone.`, 'Lock anyway', () => lockStudio(true).catch(error => toast(error.message)));
  await send('/api/logout', {});
  await forgetOfflineCopy(); showLogin();
}
function downloadRecipe(recipe, name) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2)], {type:'application/json'}));
  link.download = name; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
// Recipe links point at the computer; offline they are written from the saved copy instead.
document.addEventListener('click', event => {
  const link = connection.offline && event.target.closest('a[href^="/api/"]');
  const match = link && link.getAttribute('href').match(/^\/api\/(images|videos)\/([^/]+)\/recipe$/);
  if (!match) return;
  event.preventDefault();
  if (match[1] === 'images') {
    const version = connection.view.flatMap(p => p.versions).find(v => v.id === match[2]);
    if (version?.recipe) downloadRecipe(version.recipe, `studio-${version.id}-settings.json`);
  } else {
    const video = (connection.copy.videos || []).find(v => v.id === match[2]);
    if (video) downloadRecipe(video.recipe, 'video-recipe.json');
  }
});

function renderConnection() {
  document.body.classList.toggle('is-offline', connection.offline);
  if (!state.options || !state.authenticated) return;
  renderEngine(); renderComposer(); gallerySignature = ''; renderGallery();
  if ($('#detail-dialog').open && viewer.project) renderViewer();
  renderOfflineStatus(); updateSharedNotice().catch(() => {});
}
function savedAgo(timestamp) {
  if (!timestamp) return 'not yet';
  const minutes = Math.round((Date.now() - timestamp) / 60000);
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : new Date(timestamp).toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}
function renderOfflineStatus() {
  const box = $('#offline-content'), line = $('#offline-progress');
  if (!box || !state.authenticated) return;
  const waiting = pendingCount(), progress = connection.progress, copy = connection.copy;
  const saving = progress && progress.done < progress.total && !connection.offline && !connection.full;
  line.hidden = !saving && !connection.full;
  line.textContent = connection.full ? 'This phone is out of storage, so some images aren’t saved for offline use.' : saving ? `Saving your library for offline use · ${progress.done} / ${progress.total}` : '';
  $('#metrics-title').textContent = connection.offline ? 'Away from your computer' : 'Studio performance';
  if (connection.offline) {
    $('#server-status').textContent = `Offline · showing the copy on this phone${waiting ? ` · ${waiting} change${waiting === 1 ? '' : 's'} waiting for your computer` : ''} · Details`;
    $('#metrics-content').replaceChildren();
  }
  if (!copy) { box.replaceChildren(); return; }
  let checked = 0;
  try { checked = Number(localStorage.getItem(checkedKey)) || 0; } catch { /* Shown as unknown. */ }
  const projects = connection.view || [], images = projects.reduce((sum, p) => sum + p.saved_count, 0);
  box.innerHTML = `<h3>On this phone</h3><div class="metrics-grid"><div><span>Saved library</span><strong>${projects.length} project${projects.length === 1 ? '' : 's'} · ${images} image${images === 1 ? '' : 's'}</strong></div><div><span>Last updated from your computer</span><strong>${savedAgo(checked)}</strong></div>${progress ? `<div><span>Files saved for offline viewing</span><strong>${progress.done} of ${progress.total}</strong></div>` : ''}<div><span>Changes waiting for your computer</span><strong>${waiting || 'None'}</strong></div></div>
    <p>${connection.offline ? 'Browse, search, compare, favorite, archive, delete and download PNGs from this copy. Changes are sent when your computer is reachable again.' : 'The installed app keeps this copy so your library still opens away from home.'} Creating, editing, selections, identity swaps, videos and JPG or ZIP downloads need your computer. Lock studio removes the copy from this phone.</p>
    ${connection.offline ? '<button type="button" class="secondary" id="offline-retry">Check for my computer</button>' : ''}`;
  const retry = $('#offline-retry');
  if (retry) retry.onclick = () => { retry.disabled = true; retry.textContent = 'Checking…'; reconnect().finally(() => renderOfflineStatus()); };
}
