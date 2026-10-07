'use strict';
const $ = selector => document.querySelector(selector);
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const activeStatuses = ['queued', 'submitted', 'running'];
let saved;
try { saved = JSON.parse(localStorage.getItem('studio-composer') || '{}'); } catch { saved = {}; }
const state = {view:'library', projects:[], navigation:[], jobs:[], total:0, query:'', options:null, detail:null,
  mode:'create', quality:saved.quality || 'balanced', aspect:saved.aspect || 'square',
  outputSize:saved.outputSize || (saved.mode === 'edit' ? 'original' : 'preset'),
  resizeMode:saved.resizeMode || 'auto',
  variations:saved.variations || 1, snofs:saved.snofs !== false, source:null,
  drafts:saved.drafts || {create:'',edit:''}, seed:saved.seed ?? null,
  composerCollapsed:saved.composerCollapsed === true, authenticated:false};
let toastTimer, searchTimer, refreshing = false, gallerySignature = '', fetching = false;
let deletingImage = null, deletePending = false, libraryRevision = 0;

async function api(path, options={}) {
  let response;
  try { response = await fetch(path, {credentials:'same-origin', ...options,
    headers:options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json',...options.headers} : options.headers}); }
  catch { const error=new Error('Can’t reach your Mac. Join your home Wi-Fi and keep the Mac awake, then try again.');error.unreachable=true;throw error; }
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    const error=new Error(body.error || 'Something went wrong. Please try again.');error.status=response.status;throw error;
  }
  return body;
}
const send = (path, body, method='POST') => api(path, {method, body:JSON.stringify(body)});
function persist() {
  state.drafts[state.mode] = $('#prompt').value;
  try { localStorage.setItem('studio-composer', JSON.stringify({mode:state.mode, quality:state.quality,
    aspect:state.aspect,outputSize:state.outputSize,resizeMode:state.resizeMode,variations:state.variations,snofs:state.snofs,source:state.source,drafts:state.drafts,seed:state.seed,composerCollapsed:state.composerCollapsed})); } catch { /* Private browsing may disable storage. */ }
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
  state.authenticated = false; $('#app').hidden = true; $('#login').hidden = false;
  $('#delete-dialog').close(); $('#detail-dialog').close(); $('#info-dialog').close(); $('#metrics-dialog').close(); $('#mask-dialog').close(); $('#pin').focus();
}
async function enterStudio() {
  state.options = await api('/api/options');
  state.authenticated = true; $('#login').hidden = true; $('#app').hidden = false;
  renderComposer(); renderEngine(); if (typeof refreshMetrics === 'function') refreshMetrics(); await refresh();
}
function renderEngine() {
  $('#engine-dot').classList.toggle('offline', !state.options.engine_ready && !state.options.auto_start);
  $('#engine-label').textContent = engineLabel(state.options);
  $('#phone-url').textContent = state.options.lan_url || location.origin;
  $('#phone-url').href = state.options.lan_url || location.origin;
  $('#logout').hidden = !state.options.pin_required;
}
function engineLabel(status) {
  return {ready:'Your Mac is ready',sleeping:'Sleeping · wakes automatically',starting:'Starting image engine'}[status.engine_state] || (status.engine_ready?'Your Mac is ready':'Image engine is offline');
}
function qualityHint() {
  const quality = state.options.qualities[state.quality];
  let [width,height] = quality.sizes[state.aspect];
  $('#quality-hint').textContent = quality.description;
  if (state.mode === 'edit' && (state.outputSize !== 'preset' || state.resizeMode === 'native')) {
    if (!state.source) { $('#output-hint').textContent = 'Choose a photo to see its output size.'; return; }
    width = state.source.width; height = state.source.height;
  }
  if (state.resizeMode !== 'native' && (state.outputSize === '2k' || state.outputSize === '4k')) {
    const scale = (state.outputSize === '2k' ? 2048 : 4096) / Math.max(width,height);
    width = Math.max(1,Math.round(width*scale)); height = Math.max(1,Math.round(height*scale));
  }
  if (state.resizeMode === 'native' && state.mode === 'edit') {
    const [pw,ph] = quality.sizes[state.aspect];
    const scale = Math.min(1,Math.max(pw,ph)/Math.max(width,height),Math.sqrt(1048576/(width*height)),2048/Math.max(width,height));
    width = Math.max(64,16*Math.floor(width*scale/16+0.5)); height = Math.max(64,16*Math.floor(height*scale/16+0.5));
  } else if (state.resizeMode === 'direct') {
    width = Math.max(64,16*Math.floor(width/16+0.5)); height = Math.max(64,16*Math.floor(height/16+0.5));
  }
  let method = state.outputSize === 'preset' ? 'Preset size' : state.outputSize === 'original' ? 'Same size and shape as your photo' : 'AI upscale · keeps the image shape';
  if (state.resizeMode === 'native') method = 'Native output · no resizing or AI upscaling';
  if (state.resizeMode === 'direct') method = 'Generated at full size · no resizing or AI upscaling · dimensions use 16-pixel steps';
  if (state.resizeMode === 'direct' && width*height > 1048576) method += ' · Large images take longer and use more memory';
  $('#output-hint').textContent = `${width} × ${height} · ${method}`;
}
function renderComposer() {
  if (!state.options.qualities[state.quality]) state.quality = 'balanced';
  if (!state.options.qualities[state.quality].sizes[state.aspect]) state.aspect = 'square';
  if (!state.options.output_sizes?.[state.outputSize] || (state.mode === 'create' && state.outputSize === 'original')) state.outputSize = 'preset';
  if (!state.options.resize_modes?.[state.resizeMode]) state.resizeMode = 'auto';
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected',button.dataset.mode === state.mode));
  $('#prompt').value = state.drafts[state.mode] || '';
  $('#prompt').placeholder = state.mode === 'edit' ? 'What should change? Tell us what to keep, too…' : 'Describe an image you’d love to see…';
  $('#source-area').hidden = state.mode !== 'edit';
  $('#upload-button').hidden = !!state.source;
  $('#source-preview').hidden = !state.source;
  if (state.source) $('#source-preview').innerHTML = `<div class="source-chip"><img src="${escapeHTML(state.source.thumbnail)}" alt="Photo to edit"><span>${escapeHTML(state.source.name)}<small>${state.source.width} × ${state.source.height} · Original stays saved</small></span><button type="button" id="remove-source" aria-label="Remove reference photo">${icon('close')}</button></div>`;
  $('#quality').value = state.quality; $('#aspect').value = state.aspect; $('#variations').value = state.variations;
  $('#output-size').value = state.outputSize;
  $('#output-size').disabled = state.resizeMode === 'native';
  $('#resize-mode').value = state.resizeMode;
  $('#output-size option[value="original"]').disabled = state.mode !== 'edit';
  const originalShape = state.mode === 'edit' && (state.outputSize !== 'preset' || state.resizeMode === 'native');
  let originalShapeOption = $('#aspect option[value="original"]');
  if (!originalShapeOption) { originalShapeOption = document.createElement('option'); originalShapeOption.value = 'original'; originalShapeOption.textContent = 'Original photo'; $('#aspect').append(originalShapeOption); }
  originalShapeOption.hidden = !originalShape; originalShapeOption.disabled = true;
  $('#aspect').disabled = originalShape;
  if (originalShape) $('#aspect').value = 'original';
  $('#snofs').checked = state.snofs;
  $('#generate-label').textContent = state.mode === 'edit' ? (state.variations > 1 ? `Make ${state.variations} edits` : 'Edit image') : (state.variations > 1 ? `Create ${state.variations} images` : 'Create image');
  $('#seed').hidden = state.seed === null; $('#seed').value = state.seed ?? '';
  $('#seed-toggle').textContent = state.seed === null ? 'Random seed' : 'Fixed seed · switch to random';
  qualityHint(); renderComposerVisibility(); persist(); if (typeof scheduleEstimate === 'function') scheduleEstimate();
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
function downloadLinks(image, compact=false) {
  return ['png','jpg'].map(format => {
    const name = format.toUpperCase();
    const hint = format === 'png' ? (image.kind === 'upload' ? 'Original image' : 'Original + settings') : 'Easy to share';
    const query = format === 'jpg' ? '&format=jpg' : '';
    return `<a class="${compact ? 'download-option' : 'secondary'}" href="${image.url || `/media/image/${image.id}`}?download=1${query}" download="studio-${image.id}.${format}" aria-label="Download ${name}" title="${hint}">${icon('download')}${compact ? `<span>${name}<small>${hint}</small></span>` : `Download ${name}`}</a>`;
  }).join('');
}
function imageHTML(project) {
  const cover = project.cover;
  const pending = project.pending.filter(v => activeStatuses.includes(v.status));
  const failed = project.pending.find(v => v.status === 'failed');
  const job = state.jobs.find(j=>j.id===pending[0]?.id) || pending[0]?.job;
  return `<article class="image-card project-card ${bulk.gallery.ids.has(project.id)?'is-selected':''}"><div class="card-preview"><button class="image-open" data-action="project" data-id="${project.id}" aria-label="Open project: ${escapeHTML(project.title.slice(0,100))}">${cover ? `<img src="${cover.thumbnail}" alt="${escapeHTML(project.title)}" loading="lazy">` : `<span class="project-placeholder">${icon('spark')}<span>${failed ? 'Needs attention' : 'Creating the original…'}</span></span>`}</button>${bulkBox('gallery',project.id,project.title)}<div class="card-actions"><button data-action="project-favorite" data-id="${project.id}" class="${project.favorite ? 'favorited' : ''}" aria-label="${project.favorite ? `Contains ${project.favorite_count} favorite images. Remove all image favorites` : 'Favorite all images in project'}" title="${project.favorite_count ? `${project.favorite_count} favorite images · click to unfavorite all` : 'Favorite all images'}" aria-pressed="${project.favorite}">${icon('heart')}</button><button data-action="project-archive" data-id="${project.id}" aria-label="${project.archived ? 'Restore project' : 'Archive project'}">${icon(project.archived ? 'restore' : 'archive')}</button><button data-action="project-delete" data-id="${project.id}" class="delete-image" aria-label="Delete whole project">${icon('trash')}</button></div>${cover ? `<details class="card-download"><summary aria-label="Download original image" title="Choose PNG or JPG">${icon('download')}</summary><div class="download-menu">${downloadLinks(cover,true)}</div></details>` : ''}${job?progressHTML(job):''}</div><div class="card-info"><p>${escapeHTML(project.title)}</p><div class="card-meta"><span class="tag">${cover?.kind === 'upload' ? 'Uploaded' : 'Original'}</span><span>${project.edits} ${project.edits === 1 ? 'edit' : 'edits'}</span>${project.favorite_count?`<span class="favorite-count" title="Favorite images">♥ ${project.favorite_count}</span>`:''}<span class="date">${dateText(project.updated)}</span></div>${failed?'<div class="job-error">An image needs attention. Open to retry.</div>':''}</div></article>`;
}
function favoriteHTML(image) {
  return `<article class="image-card favorite-card ${bulk.gallery.ids.has(image.id)?'is-selected':''}"><div class="card-preview"><button class="image-open" data-action="favorite-open" data-id="${image.id}" aria-label="Open favorite image: ${escapeHTML(image.prompt.slice(0,100))}"><img src="${image.thumbnail}" alt="${escapeHTML(image.prompt)}" loading="lazy"></button>${bulkBox('gallery',image.id,image.prompt)}<div class="card-actions"><button data-action="favorite-toggle" data-id="${image.id}" class="favorited" aria-label="Remove image from favorites" aria-pressed="true">${icon('heart')}</button><button data-action="favorite-delete" data-id="${image.id}" class="delete-image" aria-label="Delete this image">${icon('trash')}</button></div><details class="card-download"><summary aria-label="Download favorite image">${icon('download')}</summary><div class="download-menu">${downloadLinks(image,true)}</div></details></div><div class="card-info"><p>${escapeHTML(image.recipe?image.prompt:image.project_title)}</p><div class="card-meta"><span class="tag">${image.is_root?'Original':image.recipe?.masked_edit?'Area edit':'Edit'}</span><span>${image.width} × ${image.height}</span><span class="date">${dateText(image.created)}</span></div></div></article>`;
}
function renderGallery() {
  const signature = JSON.stringify([state.view,state.query,state.projects.map(p=>[p.id,p.title,p.favorite,p.favorite_count,p.updated,p.edits,p.cover?.id,p.pending?.map(v=>[v.id,v.status])])]);
  if (signature !== gallerySignature) {
    gallerySignature = signature;
    $('#gallery').innerHTML = state.projects.map(state.view==='favorites'?favoriteHTML:imageHTML).join('') || emptyHTML();
  }
  updateWorkBadges();renderBulk('gallery');
  $('#load-more').hidden = state.projects.length >= state.total;
  $('#page-title').firstChild.textContent = {library:'Your library',favorites:'Your favorites',archive:'Your archive'}[state.view];
  const unit=state.view==='favorites'?'image':'project';
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
    const [library, jobs] = await Promise.all([api(`${view==='favorites'?'/api/favorites':'/api/projects'}?section=${view}&q=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`), api('/api/jobs')]);
    if (view !== state.view || query !== state.query || revision !== libraryRevision) { setTimeout(refresh,0); return; }
    const entries=view==='favorites'?library.images:library.projects;
    if (loadMore) {const seen=new Set(state.projects.map(p=>p.id));state.projects.push(...entries.filter(p=>!seen.has(p.id)));} else state.projects = entries;
    state.navigation=library.navigation;state.jobs = jobs.jobs; state.total = library.total;
    for (const view of ['library','favorites','archive']) $(`#count-${view}`).textContent = library.counts[view];
    renderGallery();
    if (typeof refreshOpenProject === 'function') await refreshOpenProject();
  } catch (error) { if (state.authenticated) toast(error.message); }
  finally { fetching = false; }
}
async function changeView(view) {
  libraryRevision++;state.view = view;state.projects=[];state.total=0;bulk.gallery.active=false;bulk.gallery.ids.clear();gallerySignature = ''; await refresh(); window.scrollTo({top:0,behavior:'smooth'});
}
function setMode(mode) {
  persist(); if (mode === 'edit' && state.mode !== 'edit' && state.outputSize === 'preset') state.outputSize = 'original';
  state.mode = mode; state.composerCollapsed = false;
  $('#compose-error').hidden = true; renderComposer(); $('#prompt').focus();
}
function reuse(recipe) {
  if (recipe.mode === 'edit') return reuseInViewer(recipe);
  state.mode = 'create'; state.quality = recipe.quality; state.aspect = recipe.aspect;
  state.outputSize = recipe.output_size || 'preset'; state.resizeMode = recipe.resize_mode || 'auto';
  state.snofs = recipe.snofs; state.seed = recipe.seed; state.variations = 1;
  state.drafts.create = recipe.prompt; state.composerCollapsed = false;
  renderComposer(); $('#detail-dialog').close(); changeView('library'); $('#prompt').focus();
  toast('Settings restored. Change anything, then create.');
}
async function action(name,id) {
  try { await projectAction(name,id); } catch (error) { toast(error.message); }
}
document.addEventListener('click', event => {
  const download = event.target.closest('.card-download');
  document.querySelectorAll('.card-download[open]').forEach(menu => {
    if (menu !== download || event.target.closest('.download-option')) menu.open = false;
  });
  const view = event.target.closest('[data-view]'); if (view) changeView(view.dataset.view);
  const mode = event.target.closest('[data-mode]'); if (mode) setMode(mode.dataset.mode);
  const item = event.target.closest('[data-action]'); if (item) action(item.dataset.action,item.dataset.id);
  const close = event.target.closest('[data-close]'); if (close) $(`#${close.dataset.close}`).close();
  const suggestion = event.target.closest('[data-suggestion]');
  if (suggestion) { state.drafts.create = suggestion.dataset.suggestion; state.mode = 'create'; state.composerCollapsed = false; renderComposer(); $('#prompt').focus(); }
  if (event.target.closest('#remove-source')) { state.source = null; renderComposer(); }
});
$('#login-form').onsubmit = async event => {
  event.preventDefault(); const button = $('#login-form button'); button.disabled = true; $('#login-error').textContent = '';
  try { await send('/api/login',{pin:$('#pin').value}); $('#pin').value = ''; await enterStudio(); }
  catch (error) { $('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
};
$('#compose-form').onsubmit = async event => {
  event.preventDefault(); if (refreshing) return;
  const errorElement = $('#compose-error'); errorElement.hidden = true;
  if (state.mode === 'edit' && !state.source) { errorElement.textContent = 'Choose a photo to edit first.'; errorElement.hidden = false; return; }
  refreshing = true; $('#generate').disabled = true;
  try {
    persist(); const body = {mode:state.mode,prompt:$('#prompt').value,quality:state.quality,aspect:state.aspect,variations:state.variations,
      snofs:state.snofs,seed:state.seed,output_size:state.outputSize,resize_mode:state.resizeMode,source_id:state.source?.id,parent_image_id:state.source?.parent_image_id};
    await send('/api/generate',body); await changeView('library');
    toast(state.variations > 1 ? `${state.variations} variations queued. They’ll appear as each finishes.` : 'Your image is on its way. It will save automatically.');
  } catch (error) { errorElement.textContent = error.message; errorElement.hidden = false; }
  finally { refreshing = false; $('#generate').disabled = false; }
};
$('#upload-root').onclick = $('#upload-button').onclick = () => $('#upload').click();
$('#upload').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 20*1024*1024) { toast('Choose a photo smaller than 20 MB.'); event.target.value = ''; return; }
  const form = new FormData(); form.append('image',file); $('#upload-root').disabled = $('#upload-button').disabled = true;
  try { const root = await api('/api/uploads',{method:'POST',body:form}); await refresh(); await showProject(root.project_id,root.id); toast('Original saved. You can edit it now or come back later.'); }
  catch (error) { toast(error.message); }
  finally { $('#upload-root').disabled = $('#upload-button').disabled = false; event.target.value = ''; }
};
for (const name of ['quality','aspect','variations','snofs']) $(`#${name}`).onchange = event => {
  persist(); state[name] = name === 'snofs' ? event.target.checked : name === 'variations' ? Number(event.target.value) : event.target.value;
  renderComposer();
};
$('#output-size').onchange = event => { persist(); state.outputSize = event.target.value; renderComposer(); };
$('#resize-mode').onchange = event => { persist(); state.resizeMode = event.target.value; renderComposer(); };
$('#prompt').oninput = persist;
$('#seed-toggle').onclick = () => { state.seed = state.seed === null ? 123 : null; renderComposer(); if (state.seed !== null) $('#seed').focus(); };
$('#seed').oninput = event => { state.seed = event.target.value === '' ? null : Number(event.target.value); persist(); };
$('#composer-toggle').onclick = () => setComposerCollapsed(!state.composerCollapsed);
$('#new-image').onclick = () => { $('#detail-dialog').close(); state.drafts.create = ''; state.mode = 'create'; state.seed = null; state.composerCollapsed = false; renderComposer(); changeView('library'); $('#prompt').focus(); };
function searchImages(immediate=false) {
  clearTimeout(searchTimer); state.query = $('#search').value; gallerySignature = '';
  if (immediate) refresh(); else searchTimer = setTimeout(refresh,250);
}
$('#search').oninput = () => searchImages();
$('#search').onchange = () => searchImages(true);
$('#search').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); searchImages(true); } };
$('#load-more').onclick = () => refresh(true);
$('#phone-info').onclick = $('#mobile-info').onclick = () => $('#info-dialog').showModal();
$('#logout').onclick = async () => { try { await send('/api/logout',{}); showLogin(); } catch (error) { toast(error.message); } };
document.addEventListener('keydown',event => {
  if (event.key === 'Escape') {
    const menu = $('.card-download[open]');
    if (menu) { menu.open = false; menu.querySelector('summary').focus(); event.preventDefault(); }
  }
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
  try { const auth = await api('/api/auth'); unreachable = false; $('#login-error').textContent = ''; if (auth.authenticated) await enterStudio(); else showLogin(); }
  catch (error) { unreachable = Boolean(error.unreachable); $('#login').hidden = false; $('#login-error').textContent = error.message; }
}
document.addEventListener('visibilitychange', () => { if (document.hidden) return; if (state.authenticated) refresh(); else if (unreachable) start(); });
window.addEventListener('online', () => { if (unreachable) start(); });
start();
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => { /* The studio works without the offline app shell. */ });
