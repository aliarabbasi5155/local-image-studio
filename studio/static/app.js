'use strict';
const $ = selector => document.querySelector(selector);
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const activeStatuses = ['queued', 'submitted', 'running'];
let saved;
try { saved = JSON.parse(localStorage.getItem('studio-composer') || '{}'); } catch { saved = {}; }
const state = {view:'library', images:[], jobs:[], total:0, query:'', options:null, detail:null,
  mode:saved.mode || 'create', quality:saved.quality || 'balanced', aspect:saved.aspect || 'square',
  variations:saved.variations || 1, snofs:saved.snofs !== false, source:saved.source || null,
  drafts:saved.drafts || {create:'',edit:''}, seed:saved.seed ?? null, authenticated:false};
let toastTimer, searchTimer, refreshing = false, gallerySignature = '', fetching = false;

async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin', ...options,
    headers:options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json',...options.headers} : options.headers});
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(body.error || 'Something went wrong. Please try again.');
  }
  return body;
}
const send = (path, body, method='POST') => api(path, {method, body:JSON.stringify(body)});
function persist() {
  state.drafts[state.mode] = $('#prompt').value;
  try { localStorage.setItem('studio-composer', JSON.stringify({mode:state.mode, quality:state.quality,
    aspect:state.aspect,variations:state.variations,snofs:state.snofs,source:state.source,drafts:state.drafts,seed:state.seed})); } catch { /* Private browsing may disable storage. */ }
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
  $('#detail-dialog').close(); $('#info-dialog').close(); $('#pin').focus();
}
async function enterStudio() {
  state.options = await api('/api/options');
  state.authenticated = true; $('#login').hidden = true; $('#app').hidden = false;
  renderComposer(); renderEngine(); await refresh();
}
function renderEngine() {
  $('#engine-dot').classList.toggle('offline', !state.options.engine_ready);
  $('#engine-label').textContent = state.options.engine_ready ? 'Your Mac is ready' : 'Image engine is offline';
  $('#phone-url').textContent = state.options.lan_url || location.origin;
  $('#phone-url').href = state.options.lan_url || location.origin;
  $('#logout').hidden = !state.options.pin_required;
}
function qualityHint() {
  const quality = state.options.qualities[state.quality];
  const [width,height] = quality.sizes[state.aspect];
  $('#quality-hint').textContent = `${quality.description} · ${width} × ${height}`;
}
function renderComposer() {
  if (!state.options.qualities[state.quality]) state.quality = 'balanced';
  if (!state.options.qualities[state.quality].sizes[state.aspect]) state.aspect = 'square';
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected',button.dataset.mode === state.mode));
  $('#prompt').value = state.drafts[state.mode] || '';
  $('#prompt').placeholder = state.mode === 'edit' ? 'What should change? Tell us what to keep, too…' : 'Describe an image you’d love to see…';
  $('#source-area').hidden = state.mode !== 'edit';
  $('#upload-button').hidden = !!state.source;
  $('#source-preview').hidden = !state.source;
  if (state.source) $('#source-preview').innerHTML = `<div class="source-chip"><img src="${escapeHTML(state.source.thumbnail)}" alt="Photo to edit"><span>${escapeHTML(state.source.name)}<small>${state.source.width} × ${state.source.height} · Original stays saved</small></span><button type="button" id="remove-source" aria-label="Remove reference photo">${icon('close')}</button></div>`;
  $('#quality').value = state.quality; $('#aspect').value = state.aspect; $('#variations').value = state.variations;
  $('#snofs').checked = state.snofs;
  $('#generate-label').textContent = state.mode === 'edit' ? (state.variations > 1 ? `Make ${state.variations} edits` : 'Edit image') : (state.variations > 1 ? `Create ${state.variations} images` : 'Create image');
  $('#seed').hidden = state.seed === null; $('#seed').value = state.seed ?? '';
  $('#seed-toggle').textContent = state.seed === null ? 'Random seed' : 'Fixed seed · switch to random';
  qualityHint(); persist();
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
function imageHTML(image) {
  const recipe = image.recipe;
  return `<article class="image-card"><button class="image-open" data-action="detail" data-id="${image.id}" aria-label="Open image: ${escapeHTML(recipe.prompt.slice(0,100))}"><img src="/media/thumb/${image.id}" alt="${escapeHTML(recipe.prompt)}" loading="lazy"></button><div class="card-actions"><button data-action="favorite" data-id="${image.id}" class="${image.favorite ? 'favorited' : ''}" aria-label="${image.favorite ? 'Remove from favorites' : 'Add to favorites'}" aria-pressed="${image.favorite}">${icon('heart')}</button><button data-action="archive" data-id="${image.id}" aria-label="${image.archived ? 'Restore image' : 'Archive image'}">${icon(image.archived ? 'restore' : 'archive')}</button></div><div class="card-info"><p>${escapeHTML(recipe.prompt)}</p><div class="card-meta"><span class="tag ${recipe.mode === 'edit' ? 'edited' : ''}">${recipe.mode === 'edit' ? 'Edited' : recipe.quality}</span><span>${recipe.width} × ${recipe.height}</span><span class="date">${dateText(image.created)}</span></div></div></article>`;
}
function renderGallery() {
  const visibleJobs = state.view === 'library' && !state.query ? state.jobs.filter(job => activeStatuses.includes(job.status) || job.status === 'failed') : [];
  const signature = JSON.stringify([state.view,state.query,state.images,visibleJobs.map(j => [j.id,j.status,j.phase,j.progress,j.cancel_requested,j.error])]);
  if (signature !== gallerySignature) {
    gallerySignature = signature;
    $('#gallery').innerHTML = [...visibleJobs.map(jobHTML), ...state.images.map(imageHTML)].join('') || emptyHTML();
  }
  for (const job of visibleJobs) {
    const element = document.querySelector(`[data-elapsed="${job.id}"]`);
    if (element) element.textContent = elapsedText(job);
    const progress = document.querySelector(`[data-progress="${job.id}"]`);
    if (progress) progress.style.width = `${Math.round(job.progress*100)}%`;
  }
  $('#load-more').hidden = state.images.length >= state.total;
  $('#page-title').firstChild.textContent = {library:'Your library',favorites:'Your favorites',archive:'Your archive'}[state.view];
  $('#page-count').textContent = `${state.total} ${state.total === 1 ? 'image' : 'images'}`;
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === state.view));
}
async function refresh(loadMore=false) {
  if (!state.authenticated || fetching) return;
  fetching = true;
  try {
    const offset = loadMore ? state.images.length : 0;
    const view = state.view, query = state.query;
    const limit = loadMore ? 48 : Math.max(48,state.images.length);
    const [library, jobs] = await Promise.all([api(`/api/images?section=${view}&q=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`), api('/api/jobs')]);
    if (view !== state.view || query !== state.query) { setTimeout(refresh,0); return; }
    if (loadMore) state.images.push(...library.images); else state.images = library.images;
    state.jobs = jobs.jobs; state.total = library.total;
    for (const view of ['library','favorites','archive']) $(`#count-${view}`).textContent = library.counts[view];
    renderGallery();
  } catch (error) { if (state.authenticated) toast(error.message); }
  finally { fetching = false; }
}
async function changeView(view) {
  state.view = view; gallerySignature = ''; await refresh(); window.scrollTo({top:0,behavior:'smooth'});
}
function setMode(mode) {
  persist(); state.mode = mode; $('#compose-error').hidden = true; renderComposer(); $('#prompt').focus();
}
function reuse(recipe) {
  state.mode = recipe.mode; state.quality = recipe.quality; state.aspect = recipe.aspect;
  state.snofs = recipe.snofs; state.seed = recipe.seed; state.variations = 1;
  state.drafts[recipe.mode] = recipe.prompt;
  if (recipe.source_id) state.source = {id:recipe.source_id,name:recipe.source.name,width:recipe.source.width,height:recipe.source.height,
    thumbnail:`/media/thumb/${recipe.source_id}`,url:`/media/upload/${recipe.source_id}`,parent_image_id:recipe.parent_image_id};
  renderComposer(); $('#detail-dialog').close(); changeView('library'); $('#prompt').focus(); toast('Settings restored. Change anything, then create.');
}
function detailHTML(image) {
  const r = image.recipe;
  return `<div class="detail-layout"><button class="icon-button dialog-close" data-close="detail-dialog" aria-label="Close image">${icon('close')}</button><div class="detail-image"><img src="/media/image/${image.id}" alt="${escapeHTML(r.prompt)}"></div><div class="detail-info"><span class="eyebrow">${r.mode === 'edit' ? 'AN IMAGE, REIMAGINED' : 'FROM YOUR IMAGINATION'}</span><h2>${r.mode === 'edit' ? 'Your edited image' : 'Your creation'}</h2><div class="detail-prompt">${escapeHTML(r.prompt)}</div><dl class="recipe-grid"><div><dt>Quality</dt><dd>${escapeHTML(r.quality)}</dd></div><div><dt>Size</dt><dd>${r.width} × ${r.height}</dd></div><div><dt>Seed</dt><dd>${r.seed}</dd></div><div><dt>Variation</dt><dd>${r.variation}</dd></div><div><dt>Created</dt><dd>${dateText(image.created)}</dd></div><div><dt>SNOFS</dt><dd>${r.snofs ? 'On' : 'Off'}</dd></div></dl>${r.source_id ? `<a class="source-link" href="/media/upload/${r.source_id}?download=1"><img src="/media/thumb/${r.source_id}" alt="Source photo"><span>Original photo · download source</span></a>` : ''}<div class="detail-actions"><button class="primary" data-action="edit" data-id="${image.id}">${icon('edit')}Edit this image</button><button class="secondary" data-action="reuse" data-id="${image.id}">${icon('repeat')}Reuse settings</button><a class="secondary" href="/media/image/${image.id}?download=1">${icon('download')}Download PNG</a><button class="secondary ${image.favorite ? 'favorited' : ''}" data-action="favorite" data-id="${image.id}">${icon('heart')}${image.favorite ? 'Favorited' : 'Favorite'}</button><button class="secondary" data-action="archive" data-id="${image.id}">${icon(image.archived ? 'restore' : 'archive')}${image.archived ? 'Restore' : 'Archive'}</button><a class="secondary" href="/api/images/${image.id}/recipe">${icon('download')}Save recipe</a></div><details><summary>Full generation settings</summary><pre>${escapeHTML(JSON.stringify(r,null,2))}</pre></details></div></div>`;
}
async function showDetail(id) {
  state.detail = await api(`/api/images/${id}`);
  $('#detail-content').innerHTML = detailHTML(state.detail);
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
}
async function action(actionName,id) {
  try {
    if (actionName === 'detail') return await showDetail(id);
    if (actionName === 'cancel') { await send(`/api/jobs/${id}/cancel`,{}); await refresh(); return toast('Stopping this variation. Other images will keep going.'); }
    if (actionName === 'retry') return reuse(state.jobs.find(job => job.id === id).recipe);
    const image = state.images.find(image => image.id === id) || (state.detail?.id === id ? state.detail : await api(`/api/images/${id}`));
    if (actionName === 'reuse') return reuse(image.recipe);
    if (actionName === 'edit') {
      const source = await send(`/api/images/${id}/edit-source`,{});
      state.source = source; state.drafts.edit = ''; state.mode = 'edit'; state.seed = null;
      state.quality = image.recipe.quality; state.aspect = image.recipe.aspect;
      renderComposer(); $('#detail-dialog').close(); await changeView('library'); $('#prompt').focus();
      return toast('Photo ready. Describe what you want to change.');
    }
    if (actionName === 'favorite') {
      await send(`/api/images/${id}`,{favorite:!image.favorite},'PATCH');
      if ($('#detail-dialog').open && state.detail?.id === id) await showDetail(id);
      await refresh();
    }
    if (actionName === 'archive') {
      const archived = !image.archived;
      await send(`/api/images/${id}`,{archived},'PATCH'); $('#detail-dialog').close(); await refresh();
      toast(archived ? 'Moved to Archive. Image and settings are kept.' : 'Image restored to your library.', 'Undo', async () => {
        try { await send(`/api/images/${id}`,{archived:!archived},'PATCH'); await refresh(); } catch (error) { toast(error.message); }
      });
    }
  } catch (error) { toast(error.message); }
}

document.addEventListener('click', event => {
  const view = event.target.closest('[data-view]'); if (view) changeView(view.dataset.view);
  const mode = event.target.closest('[data-mode]'); if (mode) setMode(mode.dataset.mode);
  const item = event.target.closest('[data-action]'); if (item) action(item.dataset.action,item.dataset.id);
  const close = event.target.closest('[data-close]'); if (close) $(`#${close.dataset.close}`).close();
  const suggestion = event.target.closest('[data-suggestion]');
  if (suggestion) { state.drafts.create = suggestion.dataset.suggestion; state.mode = 'create'; renderComposer(); $('#prompt').focus(); }
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
      snofs:state.snofs,seed:state.seed,source_id:state.source?.id,parent_image_id:state.source?.parent_image_id};
    await send('/api/generate',body); await changeView('library');
    toast(state.variations > 1 ? `${state.variations} variations queued. They’ll appear as each finishes.` : 'Your image is on its way. It will save automatically.');
  } catch (error) { errorElement.textContent = error.message; errorElement.hidden = false; }
  finally { refreshing = false; $('#generate').disabled = false; }
};
$('#upload-button').onclick = () => $('#upload').click();
$('#upload').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 20*1024*1024) { toast('Choose a photo smaller than 20 MB.'); event.target.value = ''; return; }
  const form = new FormData(); form.append('image',file); $('#upload-button').disabled = true;
  try { state.source = await api('/api/uploads',{method:'POST',body:form}); renderComposer(); toast('Photo uploaded. Your original stays unchanged.'); }
  catch (error) { toast(error.message); }
  finally { $('#upload-button').disabled = false; event.target.value = ''; }
};
for (const name of ['quality','aspect','variations','snofs']) $(`#${name}`).onchange = event => {
  persist(); state[name] = name === 'snofs' ? event.target.checked : name === 'variations' ? Number(event.target.value) : event.target.value;
  renderComposer();
};
$('#prompt').oninput = persist;
$('#seed-toggle').onclick = () => { state.seed = state.seed === null ? 123 : null; renderComposer(); if (state.seed !== null) $('#seed').focus(); };
$('#seed').oninput = event => { state.seed = event.target.value === '' ? null : Number(event.target.value); persist(); };
$('#new-image').onclick = () => { state.drafts.create = ''; state.mode = 'create'; state.seed = null; renderComposer(); changeView('library'); $('#prompt').focus(); };
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
document.addEventListener('keydown',event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && state.authenticated && !$('#detail-dialog').open && !$('#info-dialog').open) { event.preventDefault(); $('#compose-form').requestSubmit(); } });
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click',event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
setInterval(() => { if (state.authenticated && !document.hidden) refresh(); },2500);
const reserveComposerSpace = () => {
  const composerHeight = $('.composer-wrap').getBoundingClientRect().height;
  const navigationHeight = $('.mobile-nav').getBoundingClientRect().height;
  $('main').style.paddingBottom = `${composerHeight + navigationHeight + 45}px`;
};
new ResizeObserver(reserveComposerSpace).observe($('.composer-wrap'));
window.addEventListener('resize',reserveComposerSpace);
setInterval(async () => { if (state.authenticated && !document.hidden) { try { state.options = await api('/api/options'); renderEngine(); } catch { /* Next refresh retries the connection. */ } } },15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.authenticated) refresh(); });
(async () => { try { const auth = await api('/api/auth'); if (auth.authenticated) await enterStudio(); else showLogin(); } catch (error) { $('#login').hidden = false; $('#login-error').textContent = error.message; } })();
