'use strict';
const $ = selector => document.querySelector(selector);
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const activeStatuses = ['queued', 'submitted', 'running'];
let saved;
try { saved = JSON.parse(localStorage.getItem('studio-composer') || '{}'); } catch { saved = {}; }
const state = {view:'library', layout:saved.layout === 'images' ? 'images' : 'projects', projects:[], navigation:[], jobs:[], total:0, query:'', options:null,
  quality:saved.quality || 'balanced', aspect:saved.aspect || 'square', draft:saved.draft ?? saved.drafts?.create ?? '', reuse:null,
  composerCollapsed:saved.composerCollapsed === true, authenticated:false};
let toastTimer, searchTimer, refreshing = false, gallerySignature = '', fetching = false;
let deletingImage = null, deletePending = false, libraryRevision = 0;

async function api(path, options={}) {
  if (connection.offline) return offlineRequest(path, options);
  // Library reads are quick on the Mac, so a silent wait on one means the phone is away from home.
  // Status and options can wait on the Mac's helpers, so they keep no limit.
  const quick = (options.method || 'GET') === 'GET' && /^\/api\/(auth|jobs|projects|favorites|photos)\b/.test(path);
  const wait = options.timeout ?? (quick ? 8000 : null);
  let response;
  try { response = await fetch(path, {credentials:'same-origin', ...options, signal:wait && AbortSignal.timeout ? AbortSignal.timeout(wait) : undefined,
    headers:options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json',...options.headers} : options.headers}); }
  catch {
    if (path !== '/api/login' && await openOfflineCopy()) { goOffline(); return offlineRequest(path, options); }
    const error=new Error('Can’t reach your Mac. Join your home Wi-Fi and keep the Mac awake, then try again.');error.unreachable=true;throw error;
  }
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') { setOfflineLocked(true); showLogin(); }
    const error=new Error(body.error || 'Something went wrong. Please try again.');error.status=response.status;throw error;
  }
  return body;
}
const send = (path, body, method='POST') => api(path, {method, body:JSON.stringify(body)});
function persist() {
  state.draft = $('#prompt').value;
  try { localStorage.setItem('studio-composer', JSON.stringify({quality:state.quality, aspect:state.aspect, draft:state.draft, layout:state.layout, composerCollapsed:state.composerCollapsed})); } catch { /* Private browsing may disable storage. */ }
}
function renderComposerVisibility() {
  $('.composer-wrap').classList.toggle('is-collapsed',state.composerCollapsed);
  $('#composer-body').hidden = state.composerCollapsed;
  $('#composer-toggle').setAttribute('aria-expanded',String(!state.composerCollapsed));
  $('#composer-toggle').setAttribute('aria-label',state.composerCollapsed ? 'Expand Create panel' : 'Minimize Create panel');
  $('#composer-toggle-label').textContent = state.composerCollapsed ? 'Expand' : 'Minimize';
}
function setComposerCollapsed(collapsed) {
  state.composerCollapsed = collapsed;
  renderComposerVisibility(); persist();
  $('#composer-toggle').focus({preventScroll:true});
}
function toast(message, action, callback) {
  clearTimeout(toastTimer);
  $('#toast').replaceChildren(document.createTextNode(message));
  if (action) { const button = document.createElement('button'); button.textContent = action;
    button.onclick = () => { $('#toast').hidden = true; callback(); }; $('#toast').append(button); }
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}
function showLogin() {
  state.authenticated = false; $('#shared-dialog').close(); if (typeof updateSharedNotice === 'function') updateSharedNotice().catch(() => {}); $('#app').hidden = true; $('#login').hidden = false;
  $('#delete-dialog').close(); $('#detail-dialog').close(); $('#info-dialog').close(); $('#metrics-dialog').close(); $('#settings-dialog')?.close(); $('#pin').focus();
}
async function enterStudio() {
  state.options = await api('/api/options');
  // Offline changes reach the Mac before the library is shown, so they never seem to vanish.
  if (!connection.offline) reportSent(await sendPending().catch(() => null));
  state.authenticated = true; $('#login').hidden = true; $('#app').hidden = false;
  document.body.classList.toggle('is-offline', connection.offline);
  renderComposer(); renderEngine(); if (typeof refreshMetrics === 'function') refreshMetrics(); await refresh();
  if (typeof resumeShares === 'function') await resumeShares();
  syncCopy().catch(() => {});
}
function renderEngine() {
  $('#engine-dot').classList.toggle('offline', connection.offline || (!state.options.engine_ready && !state.options.auto_start));
  $('#engine-label').textContent = engineLabel(state.options);
  $('#phone-url').textContent = state.options.lan_url || location.origin;
  $('#phone-url').href = state.options.lan_url || location.origin;
  $('#logout').hidden = !state.options.pin_required;
}
function engineLabel(status) {
  return {ready:'Your Mac is ready',sleeping:'Sleeping · wakes automatically',starting:'Starting image engine',away:'Away from your Mac · using this phone'}[status.engine_state] || (status.engine_ready?'Your Mac is ready':'Image engine is offline');
}
function choiceChips(name, choices, selected) {
  return choices.map(([value, label, title]) => `<button type="button" role="radio" data-${name}="${value}" aria-checked="${value === selected}"${title ? ` aria-label="${title}" title="${title}"` : ''}>${label}</button>`).join('');
}
const shapeIcon = (width, height) => `<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="${10 - width / 2}" y="${10 - height / 2}" width="${width}" height="${height}" rx="2.5"/></svg>`;
function renderComposer() {
  if (!state.options.qualities[state.quality]) state.quality = 'balanced';
  if (!state.options.qualities[state.quality].sizes[state.aspect]) state.aspect = 'square';
  $('#composer-quality').innerHTML = choiceChips('composer-quality', Object.entries(state.options.qualities).map(([key, quality]) => [key, quality.label]), state.quality);
  $('#composer-aspect').innerHTML = choiceChips('aspect', [['square', shapeIcon(13, 13), 'Square'], ['portrait', shapeIcon(11, 15), 'Portrait'], ['landscape', shapeIcon(16, 11), 'Landscape']], state.aspect);
  $('#composer-reuse').hidden = !state.reuse;
  if ($('#prompt').value !== state.draft) $('#prompt').value = state.draft;
  const variations = state.reuse ? 1 : studioSettings.create.variations;
  $('#generate').disabled = connection.offline;
  $('#generate-label').textContent = variations > 1 ? `Create ${variations}` : 'Create';
  renderComposerVisibility(); persist(); if (typeof scheduleEstimate === 'function') scheduleEstimate();
}
function dateText(timestamp) {
  const day = new Date(timestamp*1000);
  return day.toLocaleDateString(undefined,{month:'short',day:'numeric'});
}
function elapsedText(job) {
  const seconds = Math.max(0,Math.round(Date.now()/1000 - (job.started || job.created)));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds/60)}m ${seconds%60}s`;
}
function emptyHTML() {
  if (state.query) return `<div class="empty"><div class="orb">${icon('search')}</div><h2>No matching images.</h2><p>Try a different word from your prompt.</p></div>`;
  if (state.view === 'favorites') return `<div class="empty"><div class="orb">${icon('heart')}</div><h2>Keep the ones you love.</h2><p>Tap the heart on an image to give it a home here.</p></div>`;
  if (state.view === 'archive') return `<div class="empty"><div class="orb">${icon('archive')}</div><h2>Out of sight. Still yours.</h2><p>Archived images live here with their settings. Restore one whenever you like.</p></div>`;
  return `<div class="empty"><div class="orb">${icon('spark')}</div><h2>Make something worth keeping.</h2><p>Start with a thought, a place, or a little “what if”.<br>Your imagination. Your Mac. Your studio.</p><div class="suggestions"><button data-suggestion="A cinematic photograph of a tiny Italian cafe at sunrise, warm terracotta walls, soft golden light and realistic details.">An Italian morning</button><button data-suggestion="A beautiful photograph of a red fox in a snowy forest, soft natural light, detailed fur, quiet winter atmosphere.">A quiet winter</button><button data-suggestion="An architectural photograph of a peaceful modern cabin beside a mountain lake, misty dawn, natural wood, warm lights and realistic details.">Somewhere peaceful</button></div></div>`;
}
function jobHTML(job) {
  const failed = job.status === 'failed';
  const cancellable = activeStatuses.includes(job.status);
  return `<article class="image-card job-card ${failed ? 'failed' : ''}"><div class="job-top"><span>${job.recipe.mode === 'edit' ? 'Edit' : 'Creation'} · Variation ${job.variation}</span>${cancellable ? `<button data-action="cancel" data-id="${job.id}" aria-label="Stop variation ${job.variation}">${icon('close')}</button>` : ''}</div>${icon('spark')}<p>${escapeHTML(job.recipe.prompt)}</p>${failed ? `<div class="job-error">${escapeHTML(job.error)}</div><button class="secondary" data-action="retry" data-id="${job.id}">Reuse settings</button>` : `<div class="job-phase"><span>${escapeHTML(job.cancel_requested ? 'Stopping…' : job.phase)}</span><span data-elapsed="${job.id}">${elapsedText(job)}</span></div><div class="progress-track"><span data-progress="${job.id}"></span></div>`}</article>`;
}
function cardActions(buttons) {
  return `<div class="card-actions">${buttons.join('')}</div>`;
}
function imageHTML(project) {
  const cover = project.cover;
  const pending = project.pending.filter(v => activeStatuses.includes(v.status));
  const failed = project.pending.find(v => v.status === 'failed');
  const job = state.jobs.find(j => j.id === pending[0]?.id) || pending[0]?.job;
  const work = job ? progressHTML(job) : project.animating?.length ? `<div class="work-badge" role="status"><span class="work-ring spinning">${icon('spark')}</span><span class="work-copy"><strong>Animating</strong></span></div>` : '';
  return `<article class="image-card project-card ${bulk.gallery.ids.has(project.id)?'is-selected':''}"><div class="card-preview"><button class="image-open" data-action="project" data-id="${project.id}" aria-label="Open project: ${escapeHTML(project.title.slice(0,100))}">${cover ? `<img src="${cover.thumbnail}" alt="${escapeHTML(project.title)}" loading="lazy">` : `<span class="project-placeholder">${icon('spark')}<span>${failed ? 'Needs attention' : 'Creating…'}</span></span>`}</button>${bulkBox('gallery',project.id,project.title)}${cardActions([
    `<button data-action="project-favorite" data-id="${project.id}" class="${project.favorite ? 'favorited' : ''}" aria-label="${project.favorite ? 'Remove all favorites in this project' : 'Favorite every image in this project'}" aria-pressed="${project.favorite}">${icon('heart')}</button>`,
    `<button data-action="project-archive" data-id="${project.id}" aria-label="${project.archived ? 'Restore project' : 'Archive project'}">${icon(project.archived ? 'restore' : 'archive')}</button>`,
    cover ? `<button data-action="download" data-id="${project.id}" aria-label="Download as JPG">${icon('download')}</button>` : '',
    `<button data-action="project-delete" data-id="${project.id}" class="delete-image" aria-label="Delete whole project">${icon('trash')}</button>`])}${work}</div><div class="card-info"><p>${escapeHTML(project.title)}</p><div class="card-meta"><span>${project.edits ? `${project.edits} ${project.edits === 1 ? 'edit' : 'edits'}` : cover?.kind === 'upload' ? 'Uploaded' : 'Original'}</span>${project.favorite_count?`<span class="favorite-count" title="Favorite images">♥ ${project.favorite_count}</span>`:''}<span class="date">${dateText(project.updated)}</span></div>${failed?'<div class="job-error">An image needs attention</div>':''}</div></article>`;
}
function photoHTML(image) {
  return `<article class="image-card favorite-card ${bulk.gallery.ids.has(image.id)?'is-selected':''}"><div class="card-preview"><button class="image-open" data-action="favorite-open" data-id="${image.id}" aria-label="Open image: ${escapeHTML((image.recipe ? image.prompt : image.project_title).slice(0,100))}"><img src="${image.thumbnail}" alt="${escapeHTML(image.prompt)}" loading="lazy"></button>${bulkBox('gallery',image.id,image.prompt)}${cardActions([
    `<button data-action="favorite-toggle" data-id="${image.id}" class="${image.favorite ? 'favorited' : ''}" aria-label="${image.favorite ? 'Remove from favorites' : 'Favorite'}" aria-pressed="${image.favorite}">${icon('heart')}</button>`,
    `<button data-action="download" data-id="${image.id}" aria-label="Download as JPG">${icon('download')}</button>`,
    `<button data-action="favorite-delete" data-id="${image.id}" class="delete-image" aria-label="Delete this image">${icon('trash')}</button>`])}</div><div class="card-info"><p>${escapeHTML(image.recipe ? image.prompt : image.project_title)}</p><div class="card-meta"><span>${image.is_root ? 'Original' : image.recipe?.identity_swap ? 'Person swap' : image.recipe?.masked_edit ? 'Area edit' : 'Edit'}</span><span class="date">${dateText(image.created)}</span></div></div></article>`;
}
const showsImages = () => state.view === 'favorites' || state.layout === 'images';
function renderGallery() {
  const signature = JSON.stringify([state.view,state.layout,state.query,state.projects.map(p=>[p.id,p.title,p.favorite,p.favorite_count,p.updated,p.edits,p.cover?.id,p.pending?.map(v=>[v.id,v.status]),p.animating?.length])]);
  if (signature !== gallerySignature) {
    gallerySignature = signature;
    $('#gallery').innerHTML = state.projects.map(showsImages()?photoHTML:imageHTML).join('') || emptyHTML();
    $('#gallery').classList.toggle('photo-grid', showsImages());
  }
  updateWorkBadges();renderBulk('gallery');
  $('#load-more').hidden = state.projects.length >= state.total;
  $('#page-title').firstChild.textContent = {library:'Your library',favorites:'Your favorites',archive:'Your archive'}[state.view];
  const unit=showsImages()?'image':'project';
  $('#layout-toggle').hidden = state.view === 'favorites';
  for (const button of document.querySelectorAll('[data-layout]')) button.setAttribute('aria-checked', String(button.dataset.layout === state.layout));
  $('#page-count').textContent = `${state.total} ${unit}${state.total===1?'':'s'}`;
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === state.view));
}
async function refresh(loadMore=false) {
  if (!state.authenticated || fetching) return;
  fetching = true;
  try {
    const offset = loadMore ? state.projects.length : 0;
    const view = state.view, query = state.query, revision = libraryRevision;
    const limit = loadMore ? 48 : Math.max(48,state.projects.length);
    const images = showsImages(), endpoint = view === 'favorites' ? '/api/favorites' : images ? '/api/photos' : '/api/projects';
    const [library, jobs] = await Promise.all([api(`${endpoint}?section=${view}&q=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`), api('/api/jobs')]);
    if (view !== state.view || query !== state.query || revision !== libraryRevision || images !== showsImages()) { setTimeout(refresh,0); return; }
    const entries=images?library.images:library.projects;
    if (loadMore) {const seen=new Set(state.projects.map(p=>p.id));state.projects.push(...entries.filter(p=>!seen.has(p.id)));} else state.projects = entries;
    state.navigation=library.navigation;state.jobs = jobs.jobs; state.total = library.total;
    for (const view of ['library','favorites','archive']) $(`#count-${view}`).textContent = library.counts[view];
    renderGallery();
    if (!connection.offline) syncSoon(JSON.stringify([library.counts, state.jobs.map(job => [job.id, job.status])]));
    if (typeof refreshOpenProject === 'function') await refreshOpenProject();
  } catch (error) { if (state.authenticated) toast(error.message); }
  finally { fetching = false; }
}
async function changeView(view) {
  libraryRevision++;state.view = view;state.projects=[];state.total=0;bulk.gallery.active=false;bulk.gallery.ids.clear();gallerySignature = ''; await refresh(); window.scrollTo({top:0,behavior:'smooth'});
}
function setLayout(layout) {
  if (state.layout === layout) return;
  state.layout = layout; persist(); changeView(state.view);
}
function reuse(recipe) {
  if (recipe.mode === 'edit') return reuseInViewer(recipe);
  state.quality = recipe.quality; state.aspect = recipe.aspect; state.draft = recipe.prompt; state.composerCollapsed = false;
  state.reuse = {output_size:recipe.output_size || 'preset', resize_mode:recipe.resize_mode || 'auto', snofs:recipe.snofs, seed:recipe.seed, variations:1};
  renderComposer(); $('#detail-dialog').close(); changeView('library'); $('#prompt').focus();
}
async function action(name,id) {
  try { await projectAction(name,id); } catch (error) { toast(error.message); }
}
document.addEventListener('click', event => {
  const view = event.target.closest('[data-view]'); if (view) changeView(view.dataset.view);
  const layout = event.target.closest('[data-layout]'); if (layout) setLayout(layout.dataset.layout);
  const item = event.target.closest('[data-action]'); if (item) action(item.dataset.action,item.dataset.id);
  const close = event.target.closest('[data-close]'); if (close) $(`#${close.dataset.close}`).close();
  const quality = event.target.closest('[data-composer-quality]'); if (quality) { state.quality = quality.dataset.composerQuality; renderComposer(); }
  const aspect = event.target.closest('[data-aspect]'); if (aspect) { state.aspect = aspect.dataset.aspect; renderComposer(); }
  const suggestion = event.target.closest('[data-suggestion]');
  if (suggestion) { state.draft = suggestion.dataset.suggestion; state.composerCollapsed = false; renderComposer(); $('#prompt').focus(); }
});
$('#login-form').onsubmit = async event => {
  event.preventDefault(); const button = $('#login-form button'); button.disabled = true; $('#login-error').textContent = '';
  try { await send('/api/login',{pin:$('#pin').value}); setOfflineLocked(false); $('#pin').value = ''; await enterStudio(); }
  catch (error) { $('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
};
$('#compose-form').onsubmit = async event => {
  event.preventDefault(); if (refreshing) return;
  const errorElement = $('#compose-error'); errorElement.hidden = true;
  refreshing = true; $('#generate').disabled = true;
  try {
    persist(); const body = {mode:'create',prompt:$('#prompt').value,quality:state.quality,aspect:state.aspect,...createRecipe(),...(state.reuse || {}),seed:state.reuse?.seed ?? null};
    await send('/api/generate',body); state.draft = ''; state.reuse = null; renderComposer(); await changeView('library');
  } catch (error) { errorElement.textContent = error.message; errorElement.hidden = false; }
  finally { refreshing = false; $('#generate').disabled = connection.offline; }
};
$('#upload-root').onclick = () => $('#upload').click();
$('#upload').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 20*1024*1024) { toast('Choose a photo smaller than 20 MB.'); event.target.value = ''; return; }
  if (connection.offline) {
    // Uploads are stored by the Mac, so the photo waits in this phone's inbox like a shared one.
    try { await ShareInbox.keep([file]); await updateSharedNotice(); toast('Saved on this phone for up to 24 hours. Import it when your Mac is reachable.'); }
    catch (error) { toast(shareErrors[error.message] || shareErrors.storage); }
    finally { event.target.value = ''; }
    return;
  }
  const form = new FormData(); form.append('image',file); $('#upload-root').disabled = true;
  try { const root = await api('/api/uploads',{method:'POST',body:form}); await refresh(); await showProject(root.project_id,root.id); }
  catch (error) { toast(error.message); }
  finally { $('#upload-root').disabled = false; event.target.value = ''; }
};
$('#prompt').oninput = persist;
$('#composer-reuse').onclick = () => { state.reuse = null; renderComposer(); };
$('#composer-toggle').onclick = () => setComposerCollapsed(!state.composerCollapsed);
$('#new-image').onclick = () => { $('#detail-dialog').close(); state.draft = ''; state.reuse = null; state.composerCollapsed = false; renderComposer(); changeView('library'); $('#prompt').focus(); };
$('#open-settings').onclick = $('#sidebar-settings').onclick = () => openSettings();
function searchImages(immediate=false) {
  clearTimeout(searchTimer); state.query = $('#search').value; gallerySignature = '';
  if (immediate) refresh(); else searchTimer = setTimeout(refresh,250);
}
$('#search').oninput = () => searchImages();
$('#search').onchange = () => searchImages(true);
$('#search').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); searchImages(true); } };
$('#load-more').onclick = () => refresh(true);
$('#phone-info').onclick = $('#mobile-info').onclick = () => $('#info-dialog').showModal();
$('#logout').onclick = () => lockStudio().catch(error => toast(error.message));
document.addEventListener('keydown',event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && state.authenticated && !document.querySelector('dialog[open]')) {
    event.preventDefault();
    if (state.composerCollapsed) { setComposerCollapsed(false); $('#prompt').focus(); }
    else $('#compose-form').requestSubmit();
  }
});
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click',event => { if (event.target === dialog && !(dialog.id === 'delete-dialog' && deletePending)) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
setInterval(() => { if (state.authenticated && !document.hidden) refresh(); },2500);
const reserveComposerSpace = () => {
  const composerHeight = $('.composer-wrap').getBoundingClientRect().height;
  const navigationHeight = $('.mobile-nav').getBoundingClientRect().height;
  $('main').style.paddingBottom = `${composerHeight + navigationHeight + 45}px`;
  $('#toast').style.bottom = `${composerHeight + navigationHeight + 12}px`;
};
new ResizeObserver(reserveComposerSpace).observe($('.composer-wrap'));
window.addEventListener('resize',reserveComposerSpace);
setInterval(async () => { if (state.authenticated && !document.hidden) { try { state.options = await api('/api/options'); renderEngine(); } catch { /* Next refresh retries the connection. */ } } },15000);
let unreachable = false;
async function start() {
  try {
    const auth = await api('/api/auth', {timeout:await openOfflineCopy() ? 3500 : 8000}); unreachable = false; $('#login-error').textContent = '';
    // The studio was locked while away from home; end that session now the Mac can hear it.
    if (auth.authenticated && !auth.offline && offlineLocked()) { await send('/api/logout',{}); showLogin(); return; }
    if (auth.authenticated) await enterStudio(); else showLogin();
  }
  catch (error) { unreachable = Boolean(error.unreachable); $('#login').hidden = false; $('#login-error').textContent = error.message; }
}
document.addEventListener('visibilitychange', () => { if (document.hidden) return; if (connection.offline) reconnect(); if (state.authenticated) refresh(); else if (unreachable) start(); });
window.addEventListener('online', () => { if (connection.offline) reconnect(); else if (unreachable) start(); });
start();
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => { /* The studio works without the offline app shell. */ });
