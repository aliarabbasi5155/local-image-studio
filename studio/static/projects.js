'use strict';
let editorSaved = {};
try { editorSaved = JSON.parse(localStorage.getItem('studio-editor') || '{}'); } catch {}
const viewer = {project:null, selected:null, compare:'parent', comparison:editorSaved.comparison !== false, controls:true, position:50,
  zoom:1, x:0, y:0, sources:new Map(), drafts:editorSaved.drafts || {},
  settings:{quality:state.quality,output_size:'original',resize_mode:state.resizeMode,variations:1,snofs:state.snofs,seed:null,...editorSaved.settings},
  autoSelect:null, loading:0, observer:null, controlsTimer:null};
let mainEstimateTimer, viewerEstimateTimer, estimateRevision=0, viewerEstimateRevision=0;
const duration = seconds => seconds >= 60 ? `${Math.max(1,Math.round(seconds/60))} min` : `${Math.max(1,Math.round(seconds))} sec`;
const timeRange = (low,high) => low == null || high == null ? null :
  high >= 60 ? `${Math.max(1,Math.floor(low/60))}–${Math.max(1,Math.ceil(high/60))} min` : `${Math.max(1,Math.floor(low))}–${Math.max(1,Math.ceil(high))} sec`;
function estimateText(prediction,count=1) {
  return prediction.seconds==null ? 'Timing · learning this size' : `≈${duration(prediction.seconds)}${count>1?' per image':''} · ${prediction.samples} similar run${prediction.samples===1?'':'s'}`;
}
function scheduleEstimate() {
  clearTimeout(mainEstimateTimer);
  const revision = ++estimateRevision;
  mainEstimateTimer = setTimeout(async () => {
    if (!state.authenticated) return;
    try {
      const estimate = await send('/api/estimate',{mode:'create',quality:state.quality,aspect:state.aspect,output_size:state.outputSize,resize_mode:state.resizeMode,snofs:state.snofs});
      if (revision === estimateRevision) { $('#estimate-hint').textContent = estimateText(estimate,state.variations); $('#estimate-hint').title = `${estimate.reason}. ${estimate.samples} matching recent runs.`; }
    } catch (error) { if (revision === estimateRevision) $('#estimate-hint').textContent = error.message; }
  },250);
}
function saveEditor() {
  const keys = Object.keys(viewer.drafts);
  for (const key of keys.slice(0,Math.max(0,keys.length-200))) delete viewer.drafts[key];
  try { localStorage.setItem('studio-editor',JSON.stringify({settings:viewer.settings,drafts:viewer.drafts,comparison:viewer.comparison})); } catch {}
}
const selectedVersion = () => viewer.project?.versions.find(version => version.id === viewer.selected);
const draftKey = () => `${viewer.project.id}:${viewer.selected}`;
function projectMenuHTML() {
  return `<details id="project-menu" class="viewer-menu"><summary class="icon-button" aria-label="Project and image menu">${icon('menu')}</summary><div class="viewer-menu-items"><button id="viewer-favorite">Favorite image</button><button id="viewer-reuse">Reuse settings</button><a id="viewer-recipe">Download recipe</a><button id="viewer-archive">Archive project</button><button id="viewer-delete-image" class="danger">Delete this image</button><button id="viewer-delete-project" class="danger">Delete whole project</button></div></details>`;
}
function viewerHTML() {
  return `<section class="project-viewer" aria-label="Project image editor">
    <header class="viewer-header"><div class="viewer-heading"><button class="icon-button" data-close="detail-dialog" aria-label="Back to library">${icon('close')}</button><div><span class="eyebrow">ORIGINAL + EVERY EDIT</span><h2 id="viewer-title"></h2></div><div class="project-navigation"><button class="icon-button" id="previous-project" aria-label="Previous project">${icon('back')}</button><span id="project-counter"></span><button class="icon-button" id="next-project" aria-label="Next project">${icon('next')}</button></div>${projectMenuHTML()}</div>
      <div class="viewer-tools"><button type="button" id="viewer-compare-toggle" class="viewer-tool compare-toggle" aria-pressed="${viewer.comparison}">${icon('compare')} Compare</button><label id="viewer-reference-choice">With<select id="viewer-compare"><option value="parent">Immediate parent</option><option value="root">Original</option></select></label><span id="viewer-lineage"></span><div class="viewer-zoom"><button type="button" id="zoom-out" aria-label="Zoom out">−</button><button type="button" id="zoom-reset" aria-label="Reset zoom">100%</button><button type="button" id="zoom-in" aria-label="Zoom in">＋</button></div><button class="viewer-tool" id="viewer-hide">Hide controls</button><button class="icon-button" id="viewer-fullscreen" aria-label="Fullscreen" aria-pressed="false">${icon('expand')}</button></div>
    </header>
    <div class="viewer-canvas"><div class="viewer-frame" tabindex="0" role="group" aria-label="Image viewer. Drag to compare. Pinch to zoom. Tap the center for controls or the edges to navigate."><div id="viewer-photo"></div><div class="viewer-divider" aria-hidden="true"><span class="compare-handle">${icon('compare')}</span></div><div class="image-edge edge-left" aria-hidden="true"></div><div class="image-edge edge-right" aria-hidden="true"></div></div><div id="viewer-work"></div><p class="viewer-controls-hint">Tap the center to show controls · edges to navigate</p></div>
    <div class="viewer-history"><div class="viewer-split"><label class="sr-only" for="viewer-range">Reference photo visible</label><input id="viewer-range" type="range" min="0" max="100" value="50"><button id="viewer-reset-split" type="button">Reset split</button><span id="viewer-compare-hint"></span></div><div id="viewer-bulk"></div><div class="version-strip" role="group" aria-label="Images in chronological order"></div></div>
    <form id="viewer-edit-form" class="viewer-editor"><div class="viewer-caption"><strong id="viewer-image-label"></strong><details id="viewer-prompt-details"><summary id="viewer-prompt-summary"></summary><p id="viewer-made-prompt"></p></details><button type="button" class="icon-button" id="viewer-copy-prompt" aria-label="Copy image prompt" title="Copy prompt">${icon('copy')}</button><div id="viewer-downloads"></div></div><label class="sr-only" for="viewer-edit-prompt">Edit the selected image</label><div class="viewer-edit-line"><textarea id="viewer-edit-prompt" rows="2" maxlength="5000" required placeholder="Describe what to change in this image…"></textarea><button class="primary" id="viewer-generate" type="submit">Edit image ${icon('arrow')}</button></div>
      <div class="edit-mask-row"><button id="viewer-video" type="button">Animate photo</button><button id="viewer-identity" type="button">Identity swap</button><button id="viewer-paint-mask" type="button">Paint an area</button><label><input id="viewer-use-mask" type="checkbox">Selected area only</label><button id="viewer-remove-mask" type="button" hidden>Remove mask</button><span id="viewer-mask-hint"></span></div>
      <details class="viewer-settings"><summary>${icon('settings')} Edit settings ${icon('chevron')}</summary><div class="settings-recipe"><span id="viewer-recipe-summary"></span><button type="button" id="viewer-copy-settings">${icon('copy')} Copy saved settings</button></div><div class="viewer-setting-grid"><label>Quality<select id="edit-quality"><option value="fast">Fast</option><option value="balanced">Balanced</option><option value="high">High</option></select></label><label>Output size<select id="edit-size"><option value="original">Match original</option><option value="preset">Preset</option><option value="2k">2K</option><option value="4k">4K</option></select></label><label>Resize<select id="edit-resize"><option value="auto">Resize / AI upscale</option><option value="native">No resize · native size</option><option value="direct">No resize · full size</option></select></label><label>Variations<select id="edit-variations"><option value="1">1 image</option><option value="2">2 images</option><option value="3">3 images</option><option value="4">4 images</option></select></label><label>Seed<input id="edit-seed" type="number" min="0" max="4294967295" placeholder="Random"></label><label class="edit-snofs"><input id="edit-snofs" type="checkbox">SNOFS</label></div></details>
      <p id="viewer-estimate" class="estimate-hint" aria-live="polite"></p><p id="viewer-job-status" aria-live="polite"></p><p id="viewer-error" class="error" role="alert" hidden></p>
    </form>
  </section>`;
}
async function showProject(projectId,versionId=null) {
  const revision = ++viewer.loading;
  const project = await api(`/api/projects/${projectId}`);
  if (revision !== viewer.loading) return;
  if(!project.versions.length)return;
  clearTimeout(viewer.controlsTimer);bulk.viewer.active=false;bulk.viewer.ids.clear();
  viewer.project = project; viewer.controls = true; viewer.autoSelect = null; viewer.position = 50;
  if(versionId==='last')versionId=project.versions.at(-1)?.id;
  viewer.selected = project.versions.some(v=>v.id===versionId) ? versionId : project.versions.some(v=>v.id===project.root_id) ? project.root_id : project.versions[0]?.id;
  viewer.observer?.disconnect();
  $('#detail-dialog').className = 'project-dialog';
  $('#detail-content').innerHTML = viewerHTML();
  bindViewer();
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
  syncViewerHeight();
  renderSelected(); renderTimeline(); renderProjectNavigation();
  viewer.observer = new ResizeObserver(fitViewerImage);
  viewer.observer.observe($('.viewer-canvas'));
}
function renderProjectNavigation() {
  $('#viewer-title').textContent = viewer.project.title;
  const index = state.navigation.findIndex(p=>p.id===viewer.project.id);
  $('#project-counter').textContent = index >= 0 ? `${index+1} / ${state.navigation.length}` : '';
  $('#previous-project').disabled = index <= 0;
  $('#next-project').disabled = index < 0 || index+1 >= state.navigation.length;
  renderImageNavigation();
}
async function navigateProject(direction,edge=false) {
  const index = state.navigation.findIndex(p=>p.id===viewer.project.id);
  const next = index<0?null:state.navigation[index+direction];
  if (next) await showProject(next.id,edge && direction<0?'last':null);
}
function renderImageNavigation() {
  const index=viewer.project.versions.findIndex(v=>v.id===viewer.selected),projectIndex=state.navigation.findIndex(p=>p.id===viewer.project.id);
  for(const direction of [-1,1]){
    const edge=$(direction<0?'.edge-left':'.edge-right');if(!edge)return;
    const within=!!viewer.project.versions[index+direction],cross=!within && projectIndex>=0 && !!state.navigation[projectIndex+direction];
    edge.classList.toggle('boundary',cross);edge.hidden=!within&&!cross;
    edge.innerHTML=`${icon(direction<0?'back':'next')}${cross?`<span>${icon('grid')}${direction<0?'Previous':'Next'} project</span>`:''}`;
  }
}
async function navigateImage(direction) {
  const index=viewer.project.versions.findIndex(v=>v.id===viewer.selected),next=viewer.project.versions[index+direction];
  if(next)selectVersion(next.id);else await navigateProject(direction,true);
}
const versionLabel = version => version.id===viewer.project.root_id ? 'Original' : version.recipe?.masked_edit ? 'Area edit' : 'Edit';
const versionTime = timestamp => new Date(timestamp*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
function renderTimeline() {
  const strip=$('.version-strip'),scroll=strip.scrollLeft;
  strip.innerHTML = `<div class="version-rail">${viewer.project.versions.map(version => `<div class="version-cell ${bulk.viewer.ids.has(version.id)?'is-selected':''}"><button type="button" class="version-thumb ${version.id === viewer.selected ? 'selected' : ''}" data-version="${version.id}" aria-pressed="${version.id === viewer.selected}" aria-label="${versionLabel(version)}, ${versionTime(version.created)}${version.favorite?', favorite':''}">${version.ready ? `<img src="${version.thumbnail}" alt="" loading="lazy">` : `<span class="version-pending">${icon('spark')}</span>`}<span>${versionLabel(version)}${version.favorite?' ♥':''}</span><small>${versionTime(version.created)}${!version.ready ? ` · ${version.status}` : ''}</small></button>${bulkBox('viewer',version.id,`${versionLabel(version)} ${versionTime(version.created)}`)}</div>`).join('')}</div>`;
  strip.scrollLeft=scroll;renderBulk('viewer');
}
function selectVersion(id,userAction=true) {
  if (viewer.selected === id) return;
  viewer.selected = id; viewer.position = 50;
  if (userAction) viewer.autoSelect = null;
  renderSelected(); renderTimeline();
  $(`[data-version="${id}"]`)?.scrollIntoView({block:'nearest',inline:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
}
function renderSelected(resetZoom=true) {
  const version = selectedVersion();
  if (!version) return;
  if (resetZoom) { viewer.zoom = 1; viewer.x = viewer.y = 0; }
  $('#viewer-image-label').textContent = versionLabel(version);
  $('#viewer-prompt-summary').textContent = version.recipe ? `Prompt: ${version.prompt.slice(0,150)}${version.prompt.length>150 ? '…' : ''}` : 'Uploaded original · no generation prompt';
  $('#viewer-made-prompt').textContent = version.recipe ? version.prompt : 'This image was uploaded and saved without an edit.';
  $('#viewer-prompt-details').open = false;
  $('#viewer-edit-prompt').value = viewer.drafts[draftKey()] || '';
  $('#viewer-downloads').innerHTML = version.ready ? downloadLinks(version) : '';
  $('#viewer-generate').disabled = !version.ready;
  $('#viewer-edit-prompt').disabled = !version.ready;
  $('#viewer-delete-image').hidden = false;
  $('#viewer-delete-image').disabled = activeStatuses.includes(version.status);
  $('#viewer-favorite').hidden = !version.ready;
  $('#viewer-favorite').textContent = version.favorite ? 'Remove image from favorites' : 'Favorite image';
  $('#viewer-reuse').hidden = !version.recipe;
  $('#viewer-recipe').hidden = !version.recipe || !version.ready;
  $('#viewer-recipe').href = `/api/images/${version.id}/recipe`;
  $('#viewer-copy-prompt').disabled=!version.recipe;
  $('#viewer-copy-settings').disabled=!version.recipe;
  $('#viewer-recipe-summary').textContent=version.recipe?`${state.options.qualities[version.recipe.quality]?.label || version.recipe.quality} · ${version.width} × ${version.height} · Seed ${version.recipe.seed}${version.duration_seconds?` · Took ${duration(version.duration_seconds)}`:''}`:'Uploaded original';
  $('#viewer-archive').textContent = viewer.project.archived ? 'Restore project' : 'Archive project';
  $('#viewer-error').hidden = true;
  renderPhoto(); updateEditorSettings(); updateMaskControls(); scheduleViewerEstimate(); updateViewerJobStatus();
}
function renderPhoto() {
  const version = selectedVersion();
  const reference = viewer.compare === 'root' ? viewer.project.root?.url : version.parent?.url;
  const available = version.ready && version.parent && !!reference;
  const comparing = available && viewer.comparison;
  $('.project-viewer').classList.toggle('has-comparison',comparing);
  $('#viewer-compare-toggle').disabled=!available;
  $('#viewer-compare-toggle').setAttribute('aria-pressed',String(comparing));
  $('#viewer-reference-choice').hidden=!comparing;
  $('#viewer-compare').disabled = !comparing;
  $('#viewer-compare').value = viewer.compare;
  $('#viewer-lineage').textContent = version.parent ? `From ${version.parent.id===viewer.project.root_id?'original':versionTime(version.parent.created)}${version.parent.deleted ? ' · saved parent' : ''}` : 'Your original';
  $('#viewer-compare-hint').textContent = comparing ? `Comparing with ${viewer.compare === 'root' ? 'original' : 'parent'}` : 'Pinch to zoom · tap edges to navigate';
  $('#viewer-photo').innerHTML = version.ready ? `${comparing ? `<div class="viewer-layer viewer-reference"><img src="${reference}" alt="${viewer.compare === 'root' ? 'Original root image' : 'Immediate parent image'}" draggable="false"><span class="compare-label">${viewer.compare === 'root' ? 'Original' : 'Parent'}</span></div>` : ''}<div class="viewer-layer viewer-edited"><img src="${version.url}" alt="${versionLabel(version)}: ${escapeHTML(version.prompt)}" draggable="false"><span class="compare-label">${versionLabel(version)}</span></div>` : `<div class="viewer-pending"><span>${icon('spark')}</span><strong>${version.status === 'failed' ? 'This image needs attention' : version.status === 'cancelled' ? 'This image was stopped' : 'Your image is on its way'}</strong><p>${escapeHTML(version.job?.error || '')}</p>${activeStatuses.includes(version.status) ? `<button type="button" class="secondary" data-action="cancel" data-id="${version.id}">Stop this image</button>` : `<button type="button" class="secondary" data-action="retry" data-id="${version.id}">Reuse its settings</button>`}</div>`;
  $('#viewer-photo').getAnimations().forEach(animation=>animation.cancel());
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches)$('#viewer-photo').animate([{opacity:.2},{opacity:1}],{duration:220,easing:'ease-out'});
  const original = $('.viewer-reference img');
  if (original) original.onerror = () => { $('.project-viewer')?.classList.remove('has-comparison'); $('#viewer-compare-hint').textContent = 'Comparison source unavailable. Showing the saved image.'; };
  $('#viewer-range').value = viewer.position; applySplit(); fitViewerImage(); renderZoom();renderImageNavigation();
}
function fitViewerImage() {
  const version = selectedVersion(), canvas = $('.viewer-canvas'), frame = $('.viewer-frame');
  if (!version || !canvas || !frame) return;
  const bounds = canvas.getBoundingClientRect();
  const ratio = version.width/version.height;
  const width = Math.max(1,Math.min(bounds.width-24,(bounds.height-12)*ratio));
  frame.style.width = `${width}px`; frame.style.height = `${width/ratio}px`;
  renderZoom();
}
function applySplit() {
  $('.viewer-frame')?.style.setProperty('--split',`${viewer.position}%`);
  $('#viewer-range')?.setAttribute('aria-valuetext',`${viewer.position}% reference, ${100-viewer.position}% selected image`);
}
function renderZoom() {
  const frame = $('.viewer-frame');
  if (!frame) return;
  const maxX = frame.clientWidth*(viewer.zoom-1)/2, maxY = frame.clientHeight*(viewer.zoom-1)/2;
  viewer.x = Math.max(-maxX,Math.min(maxX,viewer.x)); viewer.y = Math.max(-maxY,Math.min(maxY,viewer.y));
  frame.querySelectorAll('.viewer-layer img').forEach(img => { img.style.transform = `translate(${viewer.x}px,${viewer.y}px) scale(${viewer.zoom})`; });
  $('#zoom-reset').textContent = `${Math.round(viewer.zoom*100)}%`;
  $('#zoom-out').disabled = viewer.zoom <= 1; $('#zoom-in').disabled = viewer.zoom >= 6;
}
function setZoom(scale,x=0,y=0) {
  const next = Math.max(1,Math.min(6,scale)), ratio = next/viewer.zoom;
  viewer.x = x-(x-viewer.x)*ratio; viewer.y = y-(y-viewer.y)*ratio; viewer.zoom = next;
  renderZoom();
}
function toggleViewerControls(show=!viewer.controls) {
  viewer.controls = show;
  clearTimeout(viewer.controlsTimer);
  const surface=$('.project-viewer'),motion=!matchMedia('(prefers-reduced-motion: reduce)').matches;
  surface.querySelectorAll('.viewer-header,.viewer-history,.viewer-editor').forEach(element=>{element.inert=!show;element.setAttribute('aria-hidden',String(!show));});
  if(show){surface.classList.remove('controls-hidden');requestAnimationFrame(()=>surface.classList.remove('chrome-fading'));}
  else {surface.classList.add('chrome-fading');viewer.controlsTimer=setTimeout(()=>{surface.classList.add('controls-hidden');fitViewerImage();},motion?180:0);}
  if (!show) { $('#project-menu').open = false; $('.viewer-frame').focus({preventScroll:true}); }
  requestAnimationFrame(fitViewerImage);
}
function bindViewerGestures() {
  const frame = $('.viewer-frame'), pointers = new Map();
  let start=null,pinch=null,moved=false;
  const relative = event => { const rect=frame.getBoundingClientRect(); return {x:event.clientX-rect.left-rect.width/2,y:event.clientY-rect.top-rect.height/2}; };
  const pair = () => { const [a,b]=[...pointers.values()];return {distance:Math.hypot(a.x-b.x,a.y-b.y),x:(a.x+b.x)/2,y:(a.y+b.y)/2}; };
  frame.addEventListener('pointerdown',event => {
    if (event.target.closest('button,a') || event.button !== 0) return;
    event.preventDefault(); frame.setPointerCapture(event.pointerId); pointers.set(event.pointerId,relative(event));
    if (pointers.size===1) { start={...relative(event),x0:viewer.x,y0:viewer.y,time:Date.now()}; moved=false; }
    if (pointers.size===2) { pinch={...pair(),zoom:viewer.zoom,x0:viewer.x,y0:viewer.y}; moved=true; }
  });
  frame.addEventListener('pointermove',event => {
    if (!pointers.has(event.pointerId)) return;
    const point=relative(event); pointers.set(event.pointerId,point);
    if (pointers.size>=2 && pinch) {
      const current=pair(), next=Math.max(1,Math.min(6,pinch.zoom*current.distance/Math.max(1,pinch.distance))),ratio=next/pinch.zoom;
      viewer.zoom=next;viewer.x=current.x-(pinch.x-pinch.x0)*ratio;viewer.y=current.y-(pinch.y-pinch.y0)*ratio;renderZoom();return;
    }
    if (!start) return;
    const dx=point.x-start.x,dy=point.y-start.y;
    if (Math.hypot(dx,dy)>5) moved=true;
    if (!moved) return;
    if (viewer.zoom>1) { viewer.x=start.x0+dx;viewer.y=start.y0+dy;renderZoom(); }
    else if ($('.project-viewer').classList.contains('has-comparison')) { viewer.position=Math.max(0,Math.min(100,Math.round((point.x/frame.clientWidth+.5)*100)));$('#viewer-range').value=viewer.position;applySplit(); }
  });
  const end = event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.delete(event.pointerId);
    if (frame.hasPointerCapture(event.pointerId)) frame.releasePointerCapture(event.pointerId);
    if (!pointers.size) { if (event.type==='pointerup' && !moved && start && Date.now()-start.time<500) {const fraction=relative(event).x/frame.clientWidth+.5;if(fraction<.22 || fraction>.78)navigateImage(fraction<.22?-1:1).catch(error=>toast(error.message));else toggleViewerControls();}start=pinch=null; }
    else { const point=[...pointers.values()][0];start={...point,x0:viewer.x,y0:viewer.y,time:Date.now()};pinch=null;moved=true; }
  };
  frame.addEventListener('pointerup',end);frame.addEventListener('pointercancel',end);frame.addEventListener('lostpointercapture',end);
  frame.addEventListener('wheel',event => { if (event.ctrlKey) {event.preventDefault();const p=relative(event);setZoom(viewer.zoom*Math.exp(-event.deltaY*.01),p.x,p.y);} },{passive:false});
}
function updateEditorSettings() {
  const settings=viewer.settings;
  if(!state.options.qualities[settings.quality])settings.quality='balanced';
  if(!state.options.output_sizes[settings.output_size])settings.output_size='original';
  if(!state.options.resize_modes[settings.resize_mode])settings.resize_mode='auto';
  $('#edit-quality').value=settings.quality;$('#edit-size').value=settings.output_size;$('#edit-resize').value=settings.resize_mode;
  $('#edit-variations').value=settings.variations;$('#edit-snofs').checked=settings.snofs;$('#edit-seed').value=settings.seed ?? '';
  const masked=!!currentMask()?.enabled;
  $('#edit-size').disabled=settings.resize_mode==='native' || masked;
  $('#edit-resize').disabled=masked;
  $('#edit-resize option[value="masked"]')?.remove();
  if(masked){$('#edit-size').value='original';$('#edit-resize').add(new Option('Selected crop only','masked'));$('#edit-resize').value='masked';}
}
function bindViewer() {
  $('#viewer-video').onclick=()=>openVideoForSelected().catch(error=>toast(error.message));
  $('#previous-project').onclick=()=>navigateProject(-1).catch(error=>toast(error.message));
  $('#next-project').onclick=()=>navigateProject(1).catch(error=>toast(error.message));
  $('.version-strip').onclick=event=>{const button=event.target.closest('[data-version]');if(button)selectVersion(button.dataset.version);};
  $('#viewer-compare').onchange=event=>{viewer.compare=event.target.value;renderPhoto();};
  $('#viewer-compare-toggle').onclick=()=>{viewer.comparison=!viewer.comparison;saveEditor();renderPhoto();};
  $('#viewer-copy-prompt').onclick=()=>copyText(selectedVersion()?.prompt,'Prompt copied.');
  $('#viewer-copy-settings').onclick=()=>copyText(JSON.stringify(selectedVersion()?.recipe,null,2),'Saved settings copied.');
  $('#viewer-range').oninput=event=>{viewer.position=Number(event.target.value);applySplit();};
  $('#viewer-reset-split').onclick=()=>{viewer.position=50;$('#viewer-range').value=50;applySplit();};
  $('#zoom-in').onclick=()=>setZoom(viewer.zoom*1.25);$('#zoom-out').onclick=()=>setZoom(viewer.zoom/1.25);
  $('#zoom-reset').onclick=()=>{viewer.zoom=1;viewer.x=viewer.y=0;renderZoom();};
  $('#viewer-hide').onclick=()=>toggleViewerControls(false);
  $('#viewer-fullscreen').onclick=async()=>{
    const surface=$('.project-viewer');
    try { if(document.fullscreenElement===surface) await document.exitFullscreen();else if(surface.requestFullscreen && document.fullscreenEnabled) await surface.requestFullscreen(); }
    catch { /* The viewer already fills the available phone screen. */ }
  };
  $('.project-viewer').addEventListener('fullscreenchange',()=>{
    const active=document.fullscreenElement===$('.project-viewer');$('#viewer-fullscreen').setAttribute('aria-pressed',String(active));
    $('#viewer-fullscreen').setAttribute('aria-label',active?'Exit fullscreen':'Fullscreen');$('#viewer-fullscreen').innerHTML=icon(active?'collapse':'expand');
  });
  $('#viewer-edit-prompt').oninput=()=>{viewer.drafts[draftKey()]=$('#viewer-edit-prompt').value;saveEditor();};
  for(const [id,key] of [['edit-quality','quality'],['edit-size','output_size'],['edit-resize','resize_mode'],['edit-variations','variations'],['edit-snofs','snofs'],['edit-seed','seed']]) {
    $(`#${id}`).onchange=event=>{viewer.settings[key]=key==='snofs'?event.target.checked:key==='variations'?Number(event.target.value):key==='seed'?(event.target.value===''?null:Number(event.target.value)):event.target.value;saveEditor();updateEditorSettings();scheduleViewerEstimate();};
  }
  $('#viewer-edit-form').onsubmit=submitViewerEdit;
  $('#viewer-paint-mask').onclick=()=>openMaskEditor().catch(error=>toast(error.message));
  $('#viewer-identity').onclick=()=>openIdentitySwap().catch(error=>toast(error.message));
  $('#viewer-use-mask').onchange=event=>{const mask=currentMask();if(mask)mask.enabled=event.target.checked;updateMaskControls();updateEditorSettings();scheduleViewerEstimate();};
  $('#viewer-remove-mask').onclick=()=>{maskSelections.delete(draftKey());updateMaskControls();updateEditorSettings();scheduleViewerEstimate();};
  $('#viewer-favorite').onclick=()=>action('version-favorite',viewer.selected);
  $('#viewer-reuse').onclick=()=>reuseInViewer(selectedVersion().recipe).catch(error=>toast(error.message));
  $('#viewer-archive').onclick=()=>action('project-archive',viewer.project.id);
  $('#viewer-delete-image').onclick=()=>action('version-delete',viewer.selected);
  $('#viewer-delete-project').onclick=()=>action('project-delete',viewer.project.id);
  $('.viewer-canvas').onclick=event=>{if(event.target===$('.viewer-canvas'))toggleViewerControls();};
  bindViewerGestures();
}
function scheduleViewerEstimate() {
  clearTimeout(viewerEstimateTimer);const revision=++viewerEstimateRevision;
  viewerEstimateTimer=setTimeout(async()=>{
    const version=selectedVersion();if(!version?.ready || !$('#detail-dialog').open)return;
    try {
      const mask=currentMask();
      const result=await send('/api/estimate',{mode:'edit',project_id:viewer.project.id,version_id:version.id,aspect:version.width>version.height?'landscape':version.width<version.height?'portrait':'square',...viewer.settings,...(mask?.enabled?{mask_bounds:mask.bounds,mask_feather:mask.feather,mask_detail:mask.detail || 'balanced',mask_context:mask.context || 'balanced',mask_placement_guidance:!!mask.automatic?.placement,mask_shape_guidance:!mask.automatic?.placement&&!!mask.preserveShape&&(mask.automatic?.mode || 'change')==='change'}:{})});
      if(revision===viewerEstimateRevision && $('#viewer-estimate')) {$('#viewer-estimate').textContent=estimateText(result,viewer.settings.variations);$('#viewer-estimate').title=`${result.reason}. ${result.samples} matching recent runs.`;}
    } catch(error) {if(revision===viewerEstimateRevision && $('#viewer-estimate'))$('#viewer-estimate').textContent=error.message;}
  },250);
}
async function submitViewerEdit(event) {
  event.preventDefault();const version=selectedVersion(),projectId=viewer.project.id;
  if(!version?.ready)return;
  const button=$('#viewer-generate'),errorBox=$('#viewer-error'),prompt=$('#viewer-edit-prompt').value;
  button.disabled=true;errorBox.hidden=true;
  try {
    let source=viewer.sources.get(version.id);
    if(!source) {source=await send(`/api/projects/${projectId}/versions/${version.id}/edit-source`,{});viewer.sources.set(version.id,source);}
    const maskPayload=await prepareMask(source,`${projectId}:${version.id}`);
    const result=await send('/api/generate',{mode:'edit',prompt,project_id:projectId,source_id:source.id,parent_image_id:source.parent_image_id,
      aspect:version.width>version.height?'landscape':version.width<version.height?'portrait':'square',...viewer.settings,...maskPayload});
    viewer.autoSelect={id:result.jobs[0],from:version.id};viewer.drafts[`${projectId}:${version.id}`]='';saveEditor();
    if(viewer.project?.id===projectId && viewer.selected===version.id)$('#viewer-edit-prompt').value='';
    await refresh();updateViewerJobStatus();
  } catch(error) {if(viewer.project?.id===projectId){errorBox.textContent=error.message;errorBox.hidden=false;}}
  finally {if(button.isConnected)button.disabled=!selectedVersion()?.ready;}
}
async function refreshOpenProject() {
  if(!viewer.project || !$('#detail-dialog').open)return;
  const id=viewer.project.id,previous=selectedVersion(),oldSignature=JSON.stringify(viewer.project.versions.map(v=>[v.id,v.status,v.favorite,v.parent]));
  let project;
  try{project=await api(`/api/projects/${id}`);}catch(error){if(error.status===404){$('#detail-dialog').close();viewer.project=null;return;}throw error;}
  if(viewer.project?.id!==id || !$('#detail-dialog').open)return;
  viewer.project=project;
  if(!project.versions.length){$('#detail-dialog').close();viewer.project=null;return;}
  if(viewer.autoSelect && viewer.selected===viewer.autoSelect.from && project.versions.some(v=>v.id===viewer.autoSelect.id && v.ready)) {const next=viewer.autoSelect.id;viewer.autoSelect=null;selectVersion(next,false);}
  else if(!selectedVersion()) {viewer.selected=project.versions.find(v=>v.id===project.root_id)?.id || project.versions[0]?.id;renderSelected();}
  else if(previous?.status!==selectedVersion().status)renderSelected();
  if(oldSignature!==JSON.stringify(project.versions.map(v=>[v.id,v.status,v.favorite,v.parent])))renderTimeline();
  if(selectedVersion()?.ready){$('#viewer-favorite').textContent=selectedVersion().favorite?'Remove image from favorites':'Favorite image';}
  renderProjectNavigation();updateViewerJobStatus();
}
function updateViewerJobStatus() {
  if(!viewer.project || !$('#viewer-job-status'))return;
  const job=state.jobs.find(j=>j.id===viewer.selected && activeStatuses.includes(j.status)) || state.jobs.find(j=>j.recipe.project_id===viewer.project.id && activeStatuses.includes(j.status));
  $('#viewer-work').innerHTML=job?progressHTML(job):'';
  $('#viewer-job-status').textContent='';updateWorkBadges();
}
async function reuseInViewer(recipe) {
  if(recipe.mode==='create')return reuse(recipe);
  const projectId=recipe.project_id || viewer.project?.id;
  if(!projectId)return toast('Open the original project to reuse this edit.');
  if(viewer.project?.id!==projectId || !$('#detail-dialog').open)await showProject(projectId,recipe.parent_version_id);
  const parent=recipe.parent_version_id || recipe.parent_image_id;
  if(viewer.project.versions.some(v=>v.id===parent))selectVersion(parent);
  else if(recipe.source_id) {
    // When the earlier parent is gone, copy its settings onto this saved result.
    const binding=viewer.project.versions.find(v=>v.id===recipe.image_id);
    if(binding)selectVersion(binding.id);
  }
  Object.assign(viewer.settings,{quality:recipe.quality,output_size:recipe.output_size || 'preset',resize_mode:recipe.resize_mode || 'auto',snofs:recipe.snofs,seed:recipe.seed,variations:1});
  if(recipe.identity_swap){
    // Identity swaps reopen their own dialog with the same reference, parts and areas.
    maskSelections.delete(draftKey());saveEditor();renderSelected(false);
    return openIdentitySwap({...recipe.identity_swap,mask_id:recipe.masked_edit.mask_id,core_mask_id:recipe.masked_edit.core_mask_id});
  }
  if(recipe.masked_edit)await reuseMask(recipe);
  else maskSelections.delete(draftKey());
  viewer.drafts[draftKey()]=recipe.prompt;saveEditor();renderSelected(false);$('.viewer-settings').open=true;$('#viewer-edit-prompt').focus();
}
async function projectAction(name,id) {
  if(name.startsWith('favorite-')){
    const image=state.projects.find(p=>p.id===id);if(!image)return;
    if(name==='favorite-open')return showProject(image.project_id,id);
    if(name==='favorite-delete')return confirmProjectDeletion({id:image.project_id,title:image.project_title},image);
    if(name==='favorite-toggle'){await send(`/api/projects/${image.project_id}/versions/${id}`,{favorite:!image.favorite},'PATCH');await refresh();return;}
  }
  if(name==='project')return showProject(id);
  if(name==='cancel'){await send(`/api/jobs/${id}/cancel`,{});await refresh();return;}
  if(name==='retry'){const job=state.jobs.find(j=>j.id===id);if(job)return reuse(job.recipe);return;}
  if(name==='version-favorite') {
    const version=selectedVersion();await send(`/api/projects/${viewer.project.id}/versions/${id}`,{favorite:!version.favorite},'PATCH');await refresh();$('#viewer-favorite').textContent=selectedVersion().favorite?'Remove image from favorites':'Favorite image';return;
  }
  const project=state.projects.find(p=>p.id===id) || (viewer.project?.id===id ? viewer.project : null);
  if(name==='project-favorite' || name==='project-archive') {
    const key=name==='project-favorite'?'favorite':'archived';await send(`/api/projects/${id}`,{[key]:!project[key]},'PATCH');await refresh();
    if(viewer.project?.id===id && $('#viewer-archive'))$('#viewer-archive').textContent=viewer.project.archived?'Restore project':'Archive project';return;
  }
  if(name==='project-delete')return confirmProjectDeletion(project);
  if(name==='version-delete')return confirmProjectDeletion(viewer.project,selectedVersion());
}
function confirmProjectDeletion(project,version=null) {
  if(deletePending)return;
  deletingImage={project_id:project.id,version_id:version?.id,url:version?`/api/projects/${project.id}/versions/${version.id}`:`/api/projects/${project.id}`};
  $('#delete-title').textContent=version?'Delete this image?':'Delete this whole project?';
  $('#delete-cancel').textContent=version?'Keep image':'Keep project';
  $('#delete-description').textContent=version?'Only this image will disappear from the timeline. Its children stay, with saved parent comparisons.':'This removes the original, every edit, their recipes, and the project from the library. Other projects are kept.';
  $('#delete-prompt').textContent=version?.prompt || project.title;$('#delete-size').textContent=version?`${version.width} × ${version.height}`:'Original + all edits';
  $('#delete-thumbnail').src=version?.thumbnail || project.cover?.thumbnail || project.root?.thumbnail || '';
  $('#delete-thumbnail').hidden=!$('#delete-thumbnail').getAttribute('src');$('#delete-error').hidden=true;$('#delete-dialog').showModal();$('#delete-cancel').focus();
}
$('#delete-confirm').onclick=async()=>{
  if(!deletingImage || deletePending)return;const deletion=deletingImage;deletePending=true;
  $('#delete-confirm').disabled=$('#delete-cancel').disabled=true;$('#delete-error').hidden=true;
  try {
    await send(deletion.url,deletion.body || {confirm:true},deletion.method || 'DELETE');libraryRevision++;$('#delete-dialog').close();
    if((!deletion.version_id && viewer.project?.id===deletion.project_id) || (deletion.body?.scope==='projects' && deletion.body.ids.includes(viewer.project?.id))){$('#detail-dialog').close();viewer.project=null;}
    if(deletion.context){bulk[deletion.context].ids.clear();}
    await refresh();if(deletion.context)renderSelection(deletion.context);toast(deletion.version_id || deletion.body?.scope==='images'?'Images deleted. Other edits are kept.':'Projects deleted permanently.');
  }catch(error){$('#delete-error').textContent=error.message;$('#delete-error').hidden=false;}
  finally{deletePending=false;$('#delete-confirm').disabled=$('#delete-cancel').disabled=false;}
};
$('#delete-cancel').onclick=()=>{if(!deletePending)$('#delete-dialog').close();};
$('#delete-dialog').addEventListener('close',()=>{deletingImage=null;});
$('#delete-dialog').addEventListener('cancel',event=>{if(deletePending)event.preventDefault();});
$('#detail-dialog').addEventListener('cancel',event=>{if(!viewer.controls){event.preventDefault();toggleViewerControls(true);}});
$('#detail-dialog').addEventListener('close',()=>{
  viewer.loading++;viewer.observer?.disconnect();clearTimeout(viewerEstimateTimer);clearTimeout(viewer.controlsTimer);viewerEstimateRevision++;
  if(document.fullscreenElement?.classList.contains('project-viewer'))document.exitFullscreen().catch(()=>{});
});
document.addEventListener('keydown',event=>{
  if(!$('#detail-dialog').open || $('#delete-dialog').open || $('#mask-dialog').open || !viewer.project || event.target.closest('textarea,input,select,summary,button'))return;
  if(event.key.toLowerCase()==='h'){event.preventDefault();toggleViewerControls();}
  if(event.key==='ArrowLeft' || event.key==='ArrowRight') {
    event.preventDefault();const direction=event.key==='ArrowLeft'?-1:1;
    if(event.shiftKey)navigateProject(direction).catch(error=>toast(error.message));
    else navigateImage(direction).catch(error=>toast(error.message));
  }
});
let metricsBusy=false;
async function refreshMetrics() {
  if(!state.authenticated || metricsBusy)return;metricsBusy=true;
  try {
    const status=await api('/api/metrics'),gb=value=>value==null?'Unavailable':`${(value/1024**3).toFixed(1)} GB`;
    Object.assign(state.options,status);renderEngine();
    $('#server-status').textContent=`${engineLabel(status)} · ${status.active_images?`${status.active_images} image${status.active_images===1?'':'s'} in progress`:'Queue empty'}${status.memory_free!=null?` · ${gb(status.memory_free)} memory available`:''} · Details`;
    $('#metrics-content').innerHTML=`<div class="metrics-grid"><div><span>Image engine</span><strong>${escapeHTML(engineLabel(status))}</strong></div><div><span>Waiting images</span><strong>${status.queued_images}</strong></div><div><span>Mac memory available</span><strong>${gb(status.memory_free)} / ${gb(status.memory_total)}</strong></div><div><span>Last completed image</span><strong>${status.last_image_seconds==null?'No completed run yet':duration(status.last_image_seconds)}</strong></div><div><span>Recent average · last 10</span><strong>${status.recent_average_seconds==null?'Learning':duration(status.recent_average_seconds)}</strong></div><div><span>Image storage available</span><strong>${gb(status.storage_free)}</strong></div><div><span>Engine response</span><strong>${status.engine_response_ms==null?'Unavailable':`${status.engine_response_ms} ms`}</strong></div><div><span>Website running for</span><strong>${duration(status.uptime_seconds)}</strong></div></div><p>${status.auto_start?'The image engine starts when you submit an image and stops after 10 idle minutes to free memory. ':''}Memory is reported by your Mac. Timings include model preparation and saving; different sizes and quality settings take different amounts of time. Estimates learn from your completed images and include the studio queue.</p>`;
  } catch(error){$('#server-status').textContent='Server status unavailable · tap to retry';}
  finally{metricsBusy=false;}
}
$('#server-status').onclick=()=>{refreshMetrics();$('#metrics-dialog').showModal();};
setInterval(()=>{if(state.authenticated && !document.hidden){refreshMetrics();scheduleEstimate();}},10000);
if(state.authenticated){refreshMetrics();scheduleEstimate();}
const syncViewerHeight=()=>{const surface=$('.project-viewer');if(surface){const height=window.visualViewport?.height || innerHeight;surface.style.setProperty('--viewer-height',`${height}px`);surface.classList.toggle('keyboard-open',height<innerHeight*.75);}};
window.visualViewport?.addEventListener('resize',syncViewerHeight);
window.addEventListener('resize',syncViewerHeight);
