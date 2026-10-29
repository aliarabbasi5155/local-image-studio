'use strict';
// The project viewer: the photo fills the screen, the controls float over it, and every image and clip of the
// project sits in one strip. Tools (Edit, Area, Person, Animate) work in place on the same screen.
let editorSaved = {};
try { editorSaved = JSON.parse(localStorage.getItem('studio-editor') || '{}'); } catch {}
const viewer = {project:null, items:[], selected:null, quality:editorSaved.quality || 'balanced', drafts:editorSaved.drafts || {},
  compare:editorSaved.compare === 'root' ? 'root' : 'parent', comparing:false, split:50, peeking:false, chrome:true,
  zoom:1, x:0, y:0, fit:null, tool:'edit', reuse:null, sources:new Map(), autoSelect:null, loading:0};
let mainEstimateTimer, viewerEstimateTimer, estimateRevision = 0, viewerEstimateRevision = 0;
const duration = seconds => seconds >= 60 ? `${Math.max(1, Math.round(seconds / 60))} min` : `${Math.max(1, Math.round(seconds))} s`;
const estimateText = (prediction, count = 1) => prediction.seconds == null ? '' : `≈ ${duration(prediction.seconds)}${count > 1 ? ' each' : ''}`;
const isVideo = item => item?.kind === 'video';
const selectedVersion = () => viewer.items.find(item => item.id === viewer.selected);
const selectedImage = () => { const item = selectedVersion(); return item?.ready && !isVideo(item) ? item : null; };
const draftKey = () => `${viewer.project.id}:${viewer.selected}`;
const aspectOf = item => item.width > item.height ? 'landscape' : item.width < item.height ? 'portrait' : 'square';
const timeline = project => [...project.versions, ...(project.videos || [])].sort((a, b) => a.created - b.created || (a.id < b.id ? -1 : 1));
function itemLabel(item) {
  if (isVideo(item)) return `Clip · ${item.seconds} s`;
  if (item.id === viewer.project.root_id) return 'Original';
  return item.recipe?.close_up ? 'Close-up' : item.recipe?.identity_swap ? 'Person swap' : item.recipe?.masked_edit ? 'Area edit' : 'Edit';
}
function saveEditor() {
  const keys = Object.keys(viewer.drafts);
  for (const key of keys.slice(0, Math.max(0, keys.length - 200))) delete viewer.drafts[key];
  try { localStorage.setItem('studio-editor', JSON.stringify({quality:viewer.quality, compare:viewer.compare, drafts:viewer.drafts})); } catch {}
}

function scheduleEstimate() {
  clearTimeout(mainEstimateTimer);
  const revision = ++estimateRevision, hint = $('#estimate-hint');
  if (connection.offline) { hint.textContent = 'Needs your Mac'; return; }
  mainEstimateTimer = setTimeout(async () => {
    if (!state.authenticated) return;
    try {
      const settings = createRecipe(), estimate = await send('/api/estimate', {mode:'create', quality:state.quality, aspect:state.aspect, ...settings});
      if (revision === estimateRevision) hint.textContent = estimateText(estimate, settings.variations);
    } catch { if (revision === estimateRevision) hint.textContent = ''; }
  }, 250);
}

function viewerHTML() {
  const tools = [['edit', 'Edit'], ['area', 'Area'], ['closeup', 'Close-up'], ['identity', 'Person'], ['animate', 'Animate']];
  return `<section class="lightbox" aria-label="Photo viewer">
    <div class="lb-stage" tabindex="0" aria-label="Photo. Swipe for the next image, pinch or double-tap to zoom, press and hold to see what it came from."><div class="lb-photo"></div><div class="lb-divider" hidden><span>${icon('compare')}</span></div><span class="lb-badge" id="lb-badge" hidden></span><div class="lb-status" id="lb-status" hidden></div><button type="button" class="lb-step lb-prev" aria-label="Previous image">${icon('back')}</button><button type="button" class="lb-step lb-next" aria-label="Next image">${icon('next')}</button></div>
    <header class="lb-top"><button type="button" class="lb-icon" data-close="detail-dialog" aria-label="Back to library">${icon('back')}</button>
      <div class="lb-title"><strong id="lb-title"></strong><small id="lb-subtitle"></small></div>
      <div class="lb-actions"><button type="button" class="lb-icon" id="lb-compare" aria-label="Compare" aria-pressed="false">${icon('compare')}</button><button type="button" class="lb-icon" id="lb-favorite" aria-label="Favorite" aria-pressed="false">${icon('heart')}</button><button type="button" class="lb-icon" id="lb-download" aria-label="Download">${icon('download')}</button><button type="button" class="lb-icon" id="lb-delete" aria-label="Delete">${icon('trash')}</button>
        <details class="lb-menu" id="project-menu"><summary class="lb-icon" aria-label="More">${icon('menu')}</summary><div class="lb-menu-items"><button type="button" id="lb-reuse">Reuse settings</button><button type="button" id="lb-copy">Copy prompt</button><a id="lb-recipe">Download recipe</a><button type="button" id="lb-archive">Archive project</button><button type="button" id="lb-settings">Settings</button><button type="button" id="lb-delete-project" class="danger">Delete whole project</button></div></details></div>
      <div class="lb-segment lb-compare-choice" id="lb-compare-choice" role="radiogroup" aria-label="Compare with" hidden><button type="button" role="radio" data-compare="parent">Parent</button><button type="button" role="radio" data-compare="root">Original</button></div>
    </header>
    <footer class="lb-bottom"><p class="lb-caption" id="lb-caption"></p><div class="lb-strip" id="lb-strip" role="group" aria-label="Images and clips in this project"></div>
      <div class="lb-tools" role="tablist" aria-label="Tools">${tools.map(([tool, label]) => `<button type="button" role="tab" data-tool="${tool}">${label}</button>`).join('')}</div>
      <div class="lb-panel" id="lb-panel"></div></footer>
    <div class="lb-layer" id="mask-content" hidden></div>
  </section>`;
}
function ensureViewer() {
  if ($('.lightbox')) return;
  $('#detail-dialog').className = 'project-dialog';
  $('#detail-content').innerHTML = viewerHTML();
  bindViewer(); bindStage();
  new ResizeObserver(() => { syncViewerHeight(); fitPhoto(); }).observe($('.lb-stage'));
}

async function showProject(projectId, itemId = null) {
  const revision = ++viewer.loading, project = await api(`/api/projects/${projectId}`);
  if (revision !== viewer.loading || !project.versions.length) return;
  const opening = !$('#detail-dialog').open, changed = viewer.project?.id !== project.id;
  viewer.project = project; viewer.items = timeline(project); viewer.autoSelect = null;
  if (itemId === 'first') itemId = viewer.items[0].id;
  if (itemId === 'last') itemId = viewer.items.at(-1).id;
  // Without a choice, open on the newest image, the one the library card shows.
  viewer.selected = viewer.items.some(item => item.id === itemId) ? itemId : ([...project.versions].reverse().find(v => v.ready) || viewer.items.at(-1)).id;
  ensureViewer();
  if (opening) { viewer.chrome = true; viewer.comparing = false; $('#detail-dialog').showModal(); }
  if (changed || opening) viewer.tool = 'edit';
  resetZoom(); renderViewer();
  $(`[data-item="${viewer.selected}"]`)?.scrollIntoView({block:'nearest', inline:'center'});
}
function renderViewer() {
  if (!viewer.project || !$('.lightbox')) return;
  $('.lightbox').classList.toggle('chrome-hidden', !viewer.chrome);
  for (const element of document.querySelectorAll('.lb-top,.lb-bottom')) element.inert = !viewer.chrome;
  renderTop(); renderStrip(); renderPhoto(); renderTools(); updateViewerJobStatus();
}
function renderTop() {
  const item = selectedVersion(), project = viewer.project, index = viewer.items.indexOf(item), image = selectedImage();
  $('#lb-title').textContent = project.title;
  $('#lb-subtitle').textContent = `${itemLabel(item)} · ${index + 1} of ${viewer.items.length}`;
  $('#lb-favorite').hidden = !image;
  $('#lb-favorite').setAttribute('aria-pressed', String(!!item.favorite));
  $('#lb-favorite').setAttribute('aria-label', item.favorite ? 'Remove from favorites' : 'Favorite');
  $('#lb-download').hidden = !item.ready;
  $('#lb-delete').disabled = activeStatuses.includes(item.status);
  $('#lb-compare').hidden = !comparisonAvailable();
  $('#lb-compare').setAttribute('aria-pressed', String(viewer.comparing));
  $('#lb-compare-choice').hidden = !viewer.comparing;
  for (const button of document.querySelectorAll('[data-compare]')) button.setAttribute('aria-checked', String(button.dataset.compare === viewer.compare));
  $('#lb-reuse').hidden = !item.recipe || isVideo(item);
  $('#lb-copy').hidden = !item.recipe;
  $('#lb-recipe').hidden = !item.recipe || !item.ready;
  $('#lb-recipe').href = isVideo(item) ? `/api/videos/${item.id}/recipe` : `/api/images/${item.id}/recipe`;
  $('#lb-archive').textContent = project.archived ? 'Restore project' : 'Archive project';
  const zoomed = item.recipe?.close_up;
  $('#lb-caption').textContent = zoomed ? `Close-up${zoomed.subject ? ` · ${zoomed.subject}` : ''}` : item.recipe ? item.prompt : '';
  $('[data-compare="root"]').hidden = !!zoomed;
  $('#lb-caption').classList.remove('open');
  const projectIndex = state.navigation.findIndex(p => p.id === project.id);
  $('.lb-prev').hidden = index <= 0 && projectIndex <= 0;
  $('.lb-next').hidden = index >= viewer.items.length - 1 && (projectIndex < 0 || projectIndex >= state.navigation.length - 1);
}
function renderStrip() {
  const strip = $('#lb-strip'), scroll = strip.scrollLeft;
  strip.innerHTML = viewer.items.map(item => {
    const poster = item.thumbnail || (!isVideo(item) && item.parent ? viewer.items.find(other => other.id === item.parent.id)?.thumbnail : null);
    return `<button type="button" class="lb-thumb${item.id === viewer.selected ? ' selected' : ''}${isVideo(item) ? ' is-video' : ''}${item.ready ? '' : ' is-pending'}${item.status === 'failed' ? ' is-failed' : ''}" data-item="${item.id}" aria-label="${escapeHTML(itemLabel(item))}${item.favorite ? ', favorite' : ''}" aria-current="${item.id === viewer.selected}">${poster ? `<img src="${poster}" alt="" loading="lazy" draggable="false">` : ''}${isVideo(item) ? `<span class="lb-play" aria-hidden="true"></span>` : ''}${activeStatuses.includes(item.status) ? `<span class="lb-ring" data-ring="${item.id}"></span>` : ''}${item.status === 'failed' ? '<span class="lb-alert" aria-hidden="true">!</span>' : ''}${item.favorite && item.ready ? '<span class="lb-fav" aria-hidden="true"></span>' : ''}</button>`;
  }).join('');
  strip.scrollLeft = scroll;
}

// --- The photo, zoom and comparison ---------------------------------------------------------------------
function comparisonAvailable() {
  const item = selectedImage();
  return !!(item?.parent && referenceURL(item) && referenceURL(item) !== item.url);
}
// A close-up compares with its plain enlargement, the only image framed like it.
const referenceURL = item => viewer.compare === 'root' && !item.recipe?.close_up ? viewer.project.root?.url : item.parent?.url;
function renderPhoto() {
  const item = selectedVersion(), photo = $('.lb-photo');
  if (!comparisonAvailable()) viewer.comparing = false;
  const parent = item.parent && viewer.items.find(other => other.id === item.parent.id);
  photo.innerHTML = isVideo(item)
    ? (item.ready ? `<video class="lb-current lb-video" src="${item.url}" poster="${parent?.url || ''}" controls playsinline loop preload="metadata"></video>` : `<img class="lb-current lb-dim" src="${parent?.url || item.thumbnail}" alt="" draggable="false">`)
    : item.ready ? `<img class="lb-current" src="${item.url}" alt="${escapeHTML(itemLabel(item))}: ${escapeHTML(item.prompt)}" draggable="false">`
    : item.parent?.url ? `<img class="lb-current lb-dim" src="${item.parent.url}" alt="" draggable="false">` : '';
  if (viewer.comparing || viewer.peeking) ensureReference();
  photo.getAnimations().forEach(animation => animation.cancel());
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) photo.animate([{opacity:.35}, {opacity:1}], {duration:180, easing:'ease-out'});
  $('.lightbox').classList.toggle('comparing', viewer.comparing);
  renderStatus(); fitPhoto();
}
function ensureReference() {
  const item = selectedImage(), photo = $('.lb-photo'), url = item && referenceURL(item);
  if (!url) return false;
  let reference = photo.querySelector('.lb-reference');
  if (!reference) { reference = document.createElement('img'); reference.className = 'lb-reference'; reference.alt = ''; reference.draggable = false; photo.prepend(reference); }
  if (reference.getAttribute('src') !== url) { reference.src = url; reference.onerror = () => { setComparing(false); toast('The image this came from isn’t available.'); }; }
  return true;
}
function setComparing(on) {
  viewer.comparing = on && comparisonAvailable() && ensureReference();
  $('.lightbox').classList.toggle('comparing', viewer.comparing);
  if (viewer.comparing) {
    // Start the split where it can be seen, even while zoomed in.
    const rect = $('.lb-photo').getBoundingClientRect(), stage = $('.lb-stage').getBoundingClientRect();
    const visible = (Math.max(rect.left, stage.left) + Math.min(rect.right, stage.right)) / 2;
    viewer.split = Math.max(0, Math.min(100, (visible - rect.left) / rect.width * 100));
  }
  renderTop(); applyTransform();
}
function setPeek(on) {
  if (on && !ensureReference()) return;
  viewer.peeking = on;
  $('.lightbox').classList.toggle('peeking', on);
  $('#lb-badge').textContent = viewer.compare === 'root' ? 'Original' : 'Parent';
  $('#lb-badge').hidden = !on;
  if (on && navigator.vibrate) navigator.vibrate(8);
}
function renderStatus() {
  const item = selectedVersion(), box = $('#lb-status');
  if (item.ready) { box.hidden = true; box.innerHTML = ''; return; }
  const job = state.jobs.find(j => j.id === item.id) || item.job, active = activeStatuses.includes(item.status);
  const value = job && !isVideo(item) ? jobDisplay(job) : null;
  box.hidden = false;
  box.innerHTML = active
    ? `<span class="lb-ring big" data-ring="${item.id}">${value?.percent != null ? `${value.percent}%` : ''}</span><strong>${isVideo(item) ? 'Animating' : value?.stage || 'Waiting'}</strong>${value?.remaining ? `<small>≈ ${value.remaining} left</small>` : ''}<button type="button" class="lb-text-button" data-action="${isVideo(item) ? 'video-cancel' : 'cancel'}" data-id="${item.id}">Stop</button>`
    : `<strong>${item.status === 'cancelled' ? 'Stopped' : 'This didn’t finish'}</strong>${item.error || item.job?.error ? `<p>${escapeHTML(item.error || item.job.error)}</p>` : ''}${isVideo(item) ? '' : `<button type="button" class="lb-text-button" data-action="retry" data-id="${item.id}">Try again</button>`}`;
  updateRings();
}
function updateRings() {
  for (const ring of document.querySelectorAll('[data-ring]')) {
    const job = state.jobs.find(j => j.id === ring.dataset.ring), percent = job ? jobDisplay(job).percent : null;
    ring.classList.toggle('spinning', percent == null);
    ring.style.setProperty('--progress', `${percent || 0}%`);
    if (ring.classList.contains('big') && percent != null) ring.textContent = `${percent}%`;
  }
}
function fitPhoto() {
  const item = selectedVersion(), stage = $('.lb-stage'), photo = $('.lb-photo');
  if (!item || !stage) return;
  const W = stage.clientWidth, H = stage.clientHeight;
  // Viewing fills the screen under the floating controls; the tools keep the whole photo above their panel.
  const above = viewer.tool !== 'edit' && viewer.chrome;
  const top = above ? $('.lb-top').offsetHeight : 0, bottom = above ? $('.lb-bottom').offsetHeight : 0;
  const scale = Math.min(W / (item.width || 1), Math.max(1, H - top - bottom) / (item.height || 1));
  const fw = item.width * scale, fh = item.height * scale;
  viewer.fit = {W, H, fw, fh, left:(W - fw) / 2, top:top + (H - top - bottom - fh) / 2};
  Object.assign(photo.style, {width:`${fw}px`, height:`${fh}px`, left:`${viewer.fit.left}px`, top:`${viewer.fit.top}px`});
  clampPan(); applyTransform();
}
function clampPan() {
  const f = viewer.fit;
  if (!f) return;
  const cx = f.left + f.fw / 2, cy = f.top + f.fh / 2, w = f.fw * viewer.zoom, h = f.fh * viewer.zoom;
  viewer.x = w > f.W ? Math.min(w / 2 - cx, Math.max(f.W - w / 2 - cx, viewer.x)) : 0;
  viewer.y = h > f.H ? Math.min(h / 2 - cy, Math.max(f.H - h / 2 - cy, viewer.y)) : 0;
}
function applyTransform() {
  const photo = $('.lb-photo');
  if (!photo) return;
  photo.style.transform = `translate(${viewer.x}px,${viewer.y}px) scale(${viewer.zoom})`;
  photo.style.setProperty('--split', `${viewer.split}%`);
  $('.lightbox').classList.toggle('zoomed', viewer.zoom > 1.01);
  const divider = $('.lb-divider');
  divider.hidden = !viewer.comparing;
  if (viewer.comparing) {
    const rect = photo.getBoundingClientRect(), stage = $('.lb-stage').getBoundingClientRect();
    const x = rect.left + rect.width * viewer.split / 100 - stage.left;
    divider.style.left = `${Math.max(0, Math.min(stage.width, x))}px`;
    divider.style.top = `${Math.max(0, rect.top - stage.top)}px`;
    divider.style.height = `${Math.min(stage.height, rect.bottom - stage.top) - Math.max(0, rect.top - stage.top)}px`;
  }
}
function zoomAt(zoom, point, start = {x:point.x, y:point.y, zoom:viewer.zoom, x0:viewer.x, y0:viewer.y}) {
  const f = viewer.fit;
  if (!f) return;
  const next = Math.max(1, Math.min(6, zoom)), ratio = next / start.zoom, cx = f.left + f.fw / 2, cy = f.top + f.fh / 2;
  viewer.x = point.x - cx - (start.x - cx - start.x0) * ratio;
  viewer.y = point.y - cy - (start.y - cy - start.y0) * ratio;
  viewer.zoom = next;
  clampPan(); applyTransform();
}
function resetZoom() { viewer.zoom = 1; viewer.x = viewer.y = 0; applyTransform(); }
function setSplit(clientX) {
  const rect = $('.lb-photo').getBoundingClientRect();
  viewer.split = Math.max(0, Math.min(100, (clientX - rect.left) / rect.width * 100));
  applyTransform();
}
function toggleChrome(show = !viewer.chrome) {
  viewer.chrome = show;
  if (!show) $('#project-menu').open = false;
  $('.lightbox').classList.toggle('chrome-hidden', !show);
  for (const element of document.querySelectorAll('.lb-top,.lb-bottom')) element.inert = !show;
  if (!show) $('.lb-stage').focus({preventScroll:true});
  fitPhoto();
}
function bindStage() {
  const stage = $('.lb-stage'), photo = $('.lb-photo'), pointers = new Map();
  let gesture = null, lastTap = null, tapTimer = null, pressTimer = null;
  const point = event => ({x:event.clientX, y:event.clientY});
  const local = p => { const rect = stage.getBoundingClientRect(); return {x:p.x - rect.left, y:p.y - rect.top}; };
  const pair = () => { const [a, b] = [...pointers.values()]; return {x:(a.x + b.x) / 2, y:(a.y + b.y) / 2, d:Math.hypot(a.x - b.x, a.y - b.y)}; };
  const nearDivider = p => viewer.comparing && Math.abs(p.x - $('.lb-divider').getBoundingClientRect().left) < 32;
  stage.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button,a,video,.lb-status,.lb-crop-layer')) return;
    event.preventDefault(); stage.setPointerCapture(event.pointerId); pointers.set(event.pointerId, point(event));
    clearTimeout(pressTimer); photo.classList.add('moving');
    if (pointers.size === 1) {
      gesture = {type:'tap', start:point(event), x0:viewer.x, y0:viewer.y, time:Date.now(), split:nearDivider(point(event))};
      // Press and hold shows what this image came from, until the finger lifts.
      if (comparisonAvailable() && !gesture.split) pressTimer = setTimeout(() => { if (gesture?.type === 'tap') { gesture.type = 'peek'; setPeek(true); } }, 380);
    } else if (pointers.size === 2) {
      if (viewer.peeking) setPeek(false);
      const center = pair();
      gesture = {type:'pinch', ...local(center), d:center.d, zoom:viewer.zoom, x0:viewer.x, y0:viewer.y};
    }
  });
  stage.addEventListener('pointermove', event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, point(event));
    if (gesture?.type === 'pinch' && pointers.size >= 2) { const center = pair(); zoomAt(gesture.zoom * center.d / Math.max(1, gesture.d), local(center), gesture); return; }
    if (!gesture || gesture.type === 'peek') return;
    const dx = event.clientX - gesture.start.x, dy = event.clientY - gesture.start.y;
    if (gesture.type === 'tap') {
      if (Math.hypot(dx, dy) < 8) return;
      clearTimeout(pressTimer);
      gesture.type = viewer.comparing && (gesture.split || viewer.zoom <= 1.01) ? 'split' : viewer.zoom > 1.01 ? 'pan' : Math.abs(dx) > Math.abs(dy) ? 'swipe' : 'none';
    }
    if (gesture.type === 'split') setSplit(event.clientX);
    else if (gesture.type === 'pan') { viewer.x = gesture.x0 + dx; viewer.y = gesture.y0 + dy; clampPan(); applyTransform(); }
    else if (gesture.type === 'swipe') photo.style.translate = `${dx}px 0`;
  });
  const end = event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.delete(event.pointerId); clearTimeout(pressTimer);
    if (pointers.size) { if (gesture?.type === 'pinch') { const [rest] = pointers.values(); gesture = {type:'pan', start:rest, x0:viewer.x, y0:viewer.y}; } return; }
    photo.classList.remove('moving');
    const g = gesture; gesture = null;
    if (!g) return;
    if (g.type === 'peek') { setPeek(false); return; }
    if (g.type === 'swipe') {
      const dx = event.clientX - g.start.x;
      photo.style.translate = '';
      if (event.type === 'pointerup' && Math.abs(dx) > 60) navigateImage(dx < 0 ? 1 : -1).catch(error => toast(error.message));
      return;
    }
    if (g.type !== 'tap' || event.type !== 'pointerup' || Date.now() - g.time > 350) return;
    const p = local(point(event));
    if (lastTap && Date.now() - lastTap.time < 300 && Math.hypot(p.x - lastTap.x, p.y - lastTap.y) < 40) {
      clearTimeout(tapTimer); lastTap = null;
      photo.classList.add('easing'); zoomAt(viewer.zoom > 1.01 ? 1 : 2.5, p);
      setTimeout(() => photo.classList.remove('easing'), 260);
      return;
    }
    lastTap = {...p, time:Date.now()};
    tapTimer = setTimeout(() => { lastTap = null; toggleChrome(); }, 300);
  };
  stage.addEventListener('pointerup', end); stage.addEventListener('pointercancel', end);
  stage.addEventListener('wheel', event => { if (!event.ctrlKey) return; event.preventDefault(); zoomAt(viewer.zoom * Math.exp(-event.deltaY * .01), local(point(event))); }, {passive:false});
  stage.addEventListener('contextmenu', event => event.preventDefault());
}

// --- Moving between images and projects ------------------------------------------------------------------
function selectItem(id) {
  if (viewer.selected === id) return;
  if (viewer.peeking) setPeek(false);
  viewer.selected = id; viewer.autoSelect = null;
  if (viewer.tool !== 'edit' && !selectedImage()) viewer.tool = 'edit';
  resetZoom(); renderTop(); renderStrip(); renderPhoto(); renderTools();
  $(`[data-item="${id}"]`)?.scrollIntoView({block:'nearest', inline:'center', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
}
async function navigateImage(direction) {
  const index = viewer.items.findIndex(item => item.id === viewer.selected), next = viewer.items[index + direction];
  if (next) selectItem(next.id); else await navigateProject(direction);
}
async function navigateProject(direction) {
  const index = state.navigation.findIndex(p => p.id === viewer.project.id), next = index < 0 ? null : state.navigation[index + direction];
  if (next) await showProject(next.id, direction < 0 ? 'last' : 'first');
}

// --- Tools ----------------------------------------------------------------------------------------------
function setTool(tool) {
  if (connection.offline && tool !== 'edit') return toast('This tool needs your Mac. Join your home Wi-Fi to use it.');
  if (tool === 'area') { openMaskEditor().catch(error => toast(error.message)); return; }
  if (tool === 'identity') { openIdentitySwap().catch(error => toast(error.message)); return; }
  if (tool === 'closeup') { openCloseUp(); return; }
  viewer.tool = tool; renderTools(); fitPhoto();
}
function renderTools() {
  const item = selectedVersion(), image = selectedImage();
  $('.lb-tools').hidden = !image;
  for (const button of document.querySelectorAll('[data-tool]')) {
    button.setAttribute('aria-selected', String(button.dataset.tool === viewer.tool));
    button.disabled = connection.offline && button.dataset.tool !== 'edit';
  }
  // A person swap belongs to one photo: leaving the tool or the photo ends it.
  if (typeof closeIdentity === 'function' && identity && (viewer.tool !== 'identity' || identity.version.id !== image?.id)) { closeIdentity(); if (viewer.tool === 'identity') viewer.tool = 'edit'; }
  if (typeof closeCloseUp === 'function' && closeup.item && (viewer.tool !== 'closeup' || closeup.item.id !== image?.id)) { closeCloseUp(); if (viewer.tool === 'closeup' && image) { openCloseUp(); return; } }
  const panel = $('#lb-panel');
  if (!image) { panel.innerHTML = ''; panel.hidden = true; return; }
  panel.hidden = false;
  if (viewer.tool === 'identity' && typeof identity !== 'undefined' && identity) renderIdentityPanel();
  else if (viewer.tool === 'animate') renderAnimatePanel();
  else if (viewer.tool === 'closeup' && typeof closeup !== 'undefined' && closeup.item) renderCloseUpPanel();
  else { viewer.tool = 'edit'; renderEditPanel(); }
  for (const button of document.querySelectorAll('[data-tool]')) button.setAttribute('aria-selected', String(button.dataset.tool === viewer.tool));
}
function qualityChips(selected, attribute = 'data-quality') {
  return `<div class="lb-segment" role="radiogroup" aria-label="Quality">${['fast', 'balanced', 'high'].map(quality => `<button type="button" role="radio" ${attribute}="${quality}" aria-checked="${quality === selected}">${state.options.qualities[quality].label}</button>`).join('')}</div>`;
}
function renderEditPanel() {
  const mask = currentMask();
  $('#lb-panel').innerHTML = `<form id="lb-edit" class="lb-form"><div class="lb-row">${qualityChips(viewer.quality)}${mask ? `<button type="button" class="lb-chip${mask.enabled ? ' on' : ''}" id="lb-area-chip" aria-pressed="${mask.enabled}">${mask.enabled ? 'Selected area' : 'Whole photo'}</button>` : ''}${viewer.reuse ? `<button type="button" class="lb-chip on" id="lb-reuse-chip" aria-label="Stop reusing the earlier settings">Earlier settings ${icon('close')}</button>` : ''}<span class="lb-estimate" id="lb-estimate"></span></div>
    <div class="lb-field"><textarea id="lb-prompt" rows="1" maxlength="5000" required placeholder="${mask?.enabled ? 'Describe the change in the selected area…' : 'Describe a change…'}" aria-label="Describe a change"></textarea><button class="lb-send" id="lb-send" type="submit" aria-label="Edit image">${icon('arrow')}</button></div>
    <p class="lb-error" id="lb-error" role="alert" hidden></p></form>`;
  const prompt = $('#lb-prompt');
  prompt.value = viewer.drafts[draftKey()] || ''; growPrompt(prompt);
  prompt.oninput = () => { viewer.drafts[draftKey()] = prompt.value; saveEditor(); growPrompt(prompt); };
  prompt.onkeydown = event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); $('#lb-edit').requestSubmit(); } };
  $('#lb-send').disabled = connection.offline;
  $('#lb-edit').onsubmit = submitViewerEdit;
  $('#lb-area-chip')?.addEventListener('click', () => { const selection = currentMask(); selection.enabled = !selection.enabled; renderEditPanel(); });
  $('#lb-reuse-chip')?.addEventListener('click', () => { viewer.reuse = null; renderEditPanel(); });
  scheduleViewerEstimate();
}
const growPrompt = field => { field.style.height = 'auto'; field.style.height = `${Math.min(field.scrollHeight, 120)}px`; };
function editPayload() {
  return {quality:viewer.quality, ...editRecipe(), ...(viewer.reuse || {}), seed:viewer.reuse?.seed ?? null};
}
function scheduleViewerEstimate() {
  clearTimeout(viewerEstimateTimer);
  const revision = ++viewerEstimateRevision, hint = $('#lb-estimate');
  if (!hint) return;
  if (connection.offline) { hint.textContent = 'Editing needs your Mac'; return; }
  viewerEstimateTimer = setTimeout(async () => {
    const item = selectedImage();
    if (!item || !$('#detail-dialog').open) return;
    try {
      const mask = currentMask(), settings = editPayload();
      const result = await send('/api/estimate', {mode:'edit', project_id:viewer.project.id, version_id:item.id, aspect:aspectOf(item), ...settings,
        ...(mask?.enabled ? {mask_bounds:mask.bounds, mask_feather:mask.feather, mask_detail:mask.detail || 'balanced', mask_context:mask.context || 'balanced',
          mask_placement_guidance:!!mask.automatic?.placement, mask_shape_guidance:!mask.automatic?.placement && !!mask.preserveShape && (mask.automatic?.mode || 'change') === 'change'} : {})});
      if (revision === viewerEstimateRevision && $('#lb-estimate')) $('#lb-estimate').textContent = estimateText(result, settings.variations);
    } catch { if (revision === viewerEstimateRevision && $('#lb-estimate')) $('#lb-estimate').textContent = ''; }
  }, 250);
}
async function editSourceFor(item) {
  let source = viewer.sources.get(item.id);
  if (!source) { source = await send(`/api/projects/${viewer.project.id}/versions/${item.id}/edit-source`, {}); viewer.sources.set(item.id, source); }
  return source;
}
async function submitViewerEdit(event) {
  event.preventDefault();
  const item = selectedImage(), projectId = viewer.project.id;
  if (!item) return;
  const button = $('#lb-send'), error = $('#lb-error'), prompt = $('#lb-prompt').value, key = draftKey();
  button.disabled = true; error.hidden = true;
  try {
    const source = await editSourceFor(item), mask = await prepareMask(source, key);
    const result = await send('/api/generate', {mode:'edit', prompt, project_id:projectId, source_id:source.id, parent_image_id:source.parent_image_id, aspect:aspectOf(item), ...editPayload(), ...mask});
    viewer.autoSelect = {id:result.jobs[0], from:item.id}; viewer.drafts[key] = ''; viewer.reuse = null; saveEditor();
    if (viewer.project?.id === projectId && viewer.selected === item.id) renderEditPanel();
    await refresh();
    $(`[data-item="${result.jobs[0]}"]`)?.scrollIntoView({block:'nearest', inline:'center', behavior:'smooth'});
  } catch (failure) { if ($('#lb-error') && viewer.project?.id === projectId) { $('#lb-error').textContent = failure.message; $('#lb-error').hidden = false; } }
  finally { if ($('#lb-send')) $('#lb-send').disabled = connection.offline; }
}

// --- Live updates ---------------------------------------------------------------------------------------
async function refreshOpenProject() {
  if (!viewer.project || !$('#detail-dialog').open) return;
  const id = viewer.project.id, before = selectedVersion(), signature = items => JSON.stringify(items.map(i => [i.id, i.status, i.favorite, i.parent?.id]));
  let project;
  try { project = await api(`/api/projects/${id}`); }
  catch (error) { if (error.status === 404) { $('#detail-dialog').close(); viewer.project = null; return; } throw error; }
  if (viewer.project?.id !== id || !$('#detail-dialog').open) return;
  const old = signature(viewer.items);
  viewer.project = project; viewer.items = timeline(project);
  if (!viewer.items.length || !project.versions.length) { $('#detail-dialog').close(); viewer.project = null; return; }
  const finished = viewer.autoSelect && viewer.items.find(item => item.id === viewer.autoSelect.id && item.ready);
  if (finished && viewer.selected === viewer.autoSelect.from) { viewer.autoSelect = null; selectItem(finished.id); return; }
  if (!selectedVersion()) { viewer.selected = viewer.items.at(-1).id; resetZoom(); renderViewer(); return; }
  if (old !== signature(viewer.items)) renderStrip();
  if (before?.status !== selectedVersion().status) { renderPhoto(); renderTools(); }
  renderTop(); updateViewerJobStatus();
}
function updateViewerJobStatus() {
  if (!viewer.project || !$('#lb-status')) return;
  const item = selectedVersion();
  if (item && !item.ready && activeStatuses.includes(item.status)) {
    const job = state.jobs.find(j => j.id === item.id), value = job && jobDisplay(job), label = $('#lb-status strong'), left = $('#lb-status small');
    if (value && label) label.textContent = value.stage;
    if (left) left.textContent = value?.remaining ? `≈ ${value.remaining} left` : '';
  }
  updateRings();
}

// --- Actions ----------------------------------------------------------------------------------------------
async function reuseInViewer(recipe) {
  if (recipe.mode === 'create') return reuse(recipe);
  const projectId = recipe.project_id || viewer.project?.id;
  if (!projectId) return toast('Open the original project to reuse this edit.');
  if (viewer.project?.id !== projectId || !$('#detail-dialog').open) await showProject(projectId, recipe.parent_version_id);
  const parent = recipe.parent_version_id || recipe.parent_image_id;
  if (viewer.items.some(item => item.id === parent)) selectItem(parent);
  viewer.quality = recipe.quality;
  viewer.reuse = {output_size:recipe.output_size || 'preset', resize_mode:recipe.resize_mode || 'auto', snofs:recipe.snofs, seed:recipe.seed};
  if (recipe.identity_swap) {
    maskSelections.delete(draftKey()); saveEditor();
    return openIdentitySwap({...recipe.identity_swap, mask_id:recipe.masked_edit.mask_id, core_mask_id:recipe.masked_edit.core_mask_id});
  }
  if (recipe.masked_edit) await reuseMask(recipe); else maskSelections.delete(draftKey());
  viewer.drafts[draftKey()] = recipe.prompt; saveEditor();
  viewer.tool = 'edit'; renderTools(); $('#lb-prompt')?.focus();
}
function downloadItem(item) {
  if (isVideo(item)) return saveLink(`${item.url}?download=1`, `${item.id}.mp4`);
  if (!connection.offline) return saveLink(`${item.url}?download=1&format=jpg`, `studio-${item.id}.jpg`);
  // Away from home the phone's copy is shared; older PNGs become a JPG on the phone.
  return (async () => {
    const response = await fetch(item.url);
    if (!response.ok) throw new Error('This image isn’t saved on this phone yet.');
    let blob = await response.blob();
    if (blob.type !== 'image/jpeg') {
      const bitmap = await createImageBitmap(blob), canvas = Object.assign(document.createElement('canvas'), {width:bitmap.width, height:bitmap.height});
      const context = canvas.getContext('2d'); context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(bitmap, 0, 0);
      blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .95));
    }
    const url = URL.createObjectURL(blob);
    saveLink(url, `studio-${item.id}.jpg`);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  })();
}
function saveLink(href, name) {
  const link = Object.assign(document.createElement('a'), {href, download:name});
  document.body.append(link); link.click(); link.remove();
}
function bindViewer() {
  $('#lb-strip').onclick = event => { const button = event.target.closest('[data-item]'); if (button) selectItem(button.dataset.item); };
  $('#lb-caption').onclick = () => $('#lb-caption').classList.toggle('open');
  $('#lb-compare').onclick = () => setComparing(!viewer.comparing);
  $('#lb-compare-choice').onclick = event => {
    const choice = event.target.closest('[data-compare]');
    if (!choice) return;
    viewer.compare = choice.dataset.compare; saveEditor();
    if (!comparisonAvailable()) { viewer.compare = choice.dataset.compare === 'root' ? 'parent' : 'root'; return toast('There’s nothing to compare with here.'); }
    ensureReference(); renderTop(); applyTransform();
  };
  $('#lb-favorite').onclick = () => action('version-favorite', viewer.selected);
  $('#lb-download').onclick = () => Promise.resolve(downloadItem(selectedVersion())).catch(error => toast(error.message));
  $('#lb-delete').onclick = () => isVideo(selectedVersion()) ? confirmVideoDeletion(selectedVersion()) : action('version-delete', viewer.selected);
  $('#lb-reuse').onclick = () => reuseInViewer(selectedVersion().recipe).catch(error => toast(error.message));
  $('#lb-copy').onclick = () => copyText(selectedVersion()?.prompt, 'Prompt copied.');
  $('#lb-archive').onclick = () => action('project-archive', viewer.project.id);
  $('#lb-settings').onclick = () => { $('#project-menu').open = false; openSettings(); };
  $('#lb-delete-project').onclick = () => action('project-delete', viewer.project.id);
  $('.lb-prev').onclick = () => navigateImage(-1).catch(error => toast(error.message));
  $('.lb-next').onclick = () => navigateImage(1).catch(error => toast(error.message));
  $('.lb-tools').onclick = event => { const tool = event.target.closest('[data-tool]'); if (tool && !tool.disabled) setTool(tool.dataset.tool); };
  $('#lb-panel').addEventListener('click', event => {
    const quality = event.target.closest('[data-quality]');
    if (!quality) return;
    viewer.quality = quality.dataset.quality; saveEditor();
    for (const button of document.querySelectorAll('[data-quality]')) button.setAttribute('aria-checked', String(button === quality));
    scheduleViewerEstimate();
  });
  $('#project-menu').addEventListener('click', event => { if (event.target.closest('.lb-menu-items button,.lb-menu-items a')) $('#project-menu').open = false; });
}

async function projectAction(name, id) {
  if (name.startsWith('favorite-') || name === 'download') {
    const image = state.projects.find(p => p.id === id);
    if (name === 'download') return downloadItem(image?.cover || image);
    if (!image) return;
    if (name === 'favorite-open') return showProject(image.project_id, id);
    if (name === 'favorite-delete') return confirmProjectDeletion({id:image.project_id, title:image.project_title}, image);
    if (name === 'favorite-toggle') { await send(`/api/projects/${image.project_id}/versions/${id}`, {favorite:!image.favorite}, 'PATCH'); await refresh(); return; }
  }
  if (name === 'project') return showProject(id, state.projects.find(p => p.id === id)?.cover?.id);
  if (name === 'cancel') { await send(`/api/jobs/${id}/cancel`, {}); await refresh(); return; }
  if (name === 'video-cancel') { await send(`/api/videos/${id}/cancel`, {}); await refreshOpenProject(); return; }
  if (name === 'retry') { const job = state.jobs.find(j => j.id === id) || selectedVersion()?.job; if (job) return reuse(job.recipe); return; }
  if (name === 'version-favorite') {
    const item = selectedVersion();
    await send(`/api/projects/${viewer.project.id}/versions/${id}`, {favorite:!item.favorite}, 'PATCH'); await refresh(); return;
  }
  const project = state.projects.find(p => p.id === id) || (viewer.project?.id === id ? viewer.project : null);
  if (name === 'project-favorite' || name === 'project-archive') {
    const key = name === 'project-favorite' ? 'favorite' : 'archived';
    await send(`/api/projects/${id}`, {[key]:!project[key]}, 'PATCH'); await refresh();
    if (viewer.project?.id === id && key === 'archived') toast(project.archived ? 'Project restored.' : 'Project archived. Find it under Archive.');
    return;
  }
  if (name === 'project-delete') return confirmProjectDeletion(project);
  if (name === 'version-delete') return confirmProjectDeletion(viewer.project, selectedVersion());
}
function showDeletion({title, cancel, description, prompt, size, thumbnail}) {
  $('#delete-title').textContent = title; $('#delete-cancel').textContent = cancel; $('#delete-description').textContent = description;
  $('#delete-prompt').textContent = prompt; $('#delete-size').textContent = size;
  $('#delete-thumbnail').src = thumbnail || ''; $('#delete-thumbnail').hidden = !thumbnail;
  $('#delete-error').hidden = true; $('#delete-dialog').showModal(); $('#delete-cancel').focus();
}
function confirmProjectDeletion(project, version = null) {
  if (deletePending) return;
  deletingImage = {project_id:project.id, version_id:version?.id, url:version ? `/api/projects/${project.id}/versions/${version.id}` : `/api/projects/${project.id}`};
  showDeletion({title:version ? 'Delete this image?' : 'Delete this whole project?', cancel:version ? 'Keep image' : 'Keep project',
    description:version ? 'Only this image is removed. Images made from it stay.' : 'This removes the original, every edit and clip, and their recipes.',
    prompt:version?.prompt || project.title, size:version ? `${version.width} × ${version.height}` : 'Original, edits and clips',
    thumbnail:version?.thumbnail || project.cover?.thumbnail || project.root?.thumbnail});
}
function confirmVideoDeletion(video) {
  if (deletePending) return;
  deletingImage = {project_id:video.project_id, video_id:video.id, url:`/api/videos/${video.id}`};
  showDeletion({title:'Delete this clip?', cancel:'Keep clip', description:'Only this clip is removed. Its photo stays.', prompt:video.prompt, size:`${video.width} × ${video.height} · ${video.seconds} s`, thumbnail:video.thumbnail});
}
$('#delete-confirm').onclick = async () => {
  if (!deletingImage || deletePending) return;
  const deletion = deletingImage; deletePending = true;
  $('#delete-confirm').disabled = $('#delete-cancel').disabled = true; $('#delete-error').hidden = true;
  try {
    await send(deletion.url, deletion.body || {confirm:true}, deletion.method || 'DELETE'); libraryRevision++; $('#delete-dialog').close();
    if ((!deletion.version_id && !deletion.video_id && viewer.project?.id === deletion.project_id) || (deletion.body?.scope === 'projects' && deletion.body.ids.includes(viewer.project?.id))) { $('#detail-dialog').close(); viewer.project = null; }
    if (deletion.context) bulk[deletion.context].ids.clear();
    await refresh(); if (deletion.context) renderSelection(deletion.context);
    toast(deletion.video_id ? 'Clip deleted.' : deletion.version_id || deletion.body?.scope === 'images' ? 'Deleted. Images made from it stay.' : 'Deleted permanently.');
  } catch (error) { $('#delete-error').textContent = error.message; $('#delete-error').hidden = false; }
  finally { deletePending = false; $('#delete-confirm').disabled = $('#delete-cancel').disabled = false; }
};
$('#delete-cancel').onclick = () => { if (!deletePending) $('#delete-dialog').close(); };
$('#delete-dialog').addEventListener('close', () => { deletingImage = null; });
$('#delete-dialog').addEventListener('cancel', event => { if (deletePending) event.preventDefault(); });
$('#detail-dialog').addEventListener('cancel', event => {
  if ($('#mask-content') && !$('#mask-content').hidden) { event.preventDefault(); closeMaskEditor(); }
  else if (!viewer.chrome) { event.preventDefault(); toggleChrome(true); }
  else if (viewer.zoom > 1.01) { event.preventDefault(); resetZoom(); }
});
$('#detail-dialog').addEventListener('close', () => {
  viewer.loading++; clearTimeout(viewerEstimateTimer); viewerEstimateRevision++;
  document.querySelectorAll('.lightbox video').forEach(video => video.pause());
  if (typeof closeMaskEditor === 'function') closeMaskEditor();
});
document.addEventListener('keydown', event => {
  if (!$('#detail-dialog').open || $('#delete-dialog').open || !viewer.project || !$('#mask-content')?.hidden || event.target.closest('textarea,input,select,summary,button,video')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    const direction = event.key === 'ArrowLeft' ? -1 : 1;
    (event.shiftKey ? navigateProject(direction) : navigateImage(direction)).catch(error => toast(error.message));
  }
  if (event.key.toLowerCase() === 'h') toggleChrome();
});

let metricsBusy = false;
async function refreshMetrics() {
  if (connection.offline) { renderOfflineStatus(); return; }
  if (!state.authenticated || metricsBusy) return;
  metricsBusy = true;
  try {
    const status = await api('/api/metrics'), gb = value => value == null ? 'Unavailable' : `${(value / 1024 ** 3).toFixed(1)} GB`;
    Object.assign(state.options, status); renderEngine();
    $('#server-status').textContent = `${engineLabel(status)}${status.active_images ? ` · ${status.active_images} in progress` : ''}`;
    $('#metrics-content').innerHTML = `<div class="metrics-grid"><div><span>Image engine</span><strong>${escapeHTML(engineLabel(status))}</strong></div><div><span>Waiting</span><strong>${status.queued_images}</strong></div><div><span>Mac memory available</span><strong>${gb(status.memory_free)} of ${gb(status.memory_total)}</strong></div><div><span>Last image took</span><strong>${status.last_image_seconds == null ? '—' : duration(status.last_image_seconds)}</strong></div><div><span>Typical recently</span><strong>${status.recent_average_seconds == null ? '—' : duration(status.recent_average_seconds)}</strong></div><div><span>Storage available</span><strong>${gb(status.storage_free)}</strong></div></div><p>${status.auto_start ? 'The image engine starts when you make something and stops after 10 idle minutes to free memory. ' : ''}More free memory on your Mac makes images faster.</p>`;
    renderOfflineStatus();
  } catch { $('#server-status').textContent = 'Status unavailable · tap to retry'; }
  finally { metricsBusy = false; }
}
$('#server-status').onclick = () => { refreshMetrics(); $('#metrics-dialog').showModal(); };
setInterval(() => { if (state.authenticated && !document.hidden) { refreshMetrics(); scheduleEstimate(); } }, 10000);
if (state.authenticated) { refreshMetrics(); scheduleEstimate(); }
const syncViewerHeight = () => {
  const surface = $('.lightbox');
  if (!surface) return;
  const height = window.visualViewport?.height || innerHeight;
  surface.style.setProperty('--viewer-height', `${height}px`);
  surface.classList.toggle('keyboard-open', height < innerHeight * .75);
};
window.visualViewport?.addEventListener('resize', syncViewerHeight);
window.addEventListener('resize', syncViewerHeight);
