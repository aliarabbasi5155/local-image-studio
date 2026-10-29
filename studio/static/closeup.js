'use strict';
// Close-up: frame part of the photo, by dragging or by naming it, and get a new, larger image of just that area.
// Coarse structure comes from the photo; model detail is reduced where it disagrees (studio/closeup.py).
const closeup = {item:null, frame:null, detail:'faithful', size:'standard', subject:'', matches:[], match:0, found:'', busy:false, revision:0, seed:null};
const defaultFrame = () => ({x:.3, y:.25, w:.4, h:.4});

function openCloseUp(recipe = null) {
  const item = selectedImage();
  if (!item) return;
  if (closeup.item?.id !== item.id) {
    closeCloseUp();
    Object.assign(closeup, {item, frame:defaultFrame(), matches:[], match:0, found:'', subject:'', seed:null});
  }
  if (recipe) {
    const [x, y, w, h] = recipe.close_up.from;
    Object.assign(closeup, {frame:{x:x/item.width, y:y/item.height, w:w/item.width, h:h/item.height},
      detail:recipe.close_up.detail, size:recipe.close_up.size, subject:recipe.close_up.subject || '', seed:recipe.seed});
  }
  setComparing(false); setPeek(false);
  viewer.tool = 'closeup'; resetZoom(); renderTools(); fitPhoto();
}
function closeCloseUp() { closeup.revision++; closeup.busy = false; closeup.item = null; $('#lb-crop-layer')?.remove(); }
function framePixels() {
  const {item, frame} = closeup;
  const x = Math.round(frame.x * item.width), y = Math.round(frame.y * item.height);
  return [x, y, Math.min(item.width - x, Math.round(frame.w * item.width)), Math.min(item.height - y, Math.round(frame.h * item.height))];
}
function renderCloseUpPanel() {
  const choice = (name, value, label) => `<button type="button" role="radio" data-closeup-${name}="${value}" aria-checked="${closeup[name] === value}">${label}</button>`;
  $('#lb-panel').innerHTML = `<form id="closeup-form" class="lb-form"><div class="lb-row"><div class="lb-segment" role="radiogroup" aria-label="Fidelity">${choice('detail', 'faithful', 'Faithful')}${choice('detail', 'detailed', 'More detail')}</div><div class="lb-segment" role="radiogroup" aria-label="Size">${choice('size', 'standard', 'Standard')}${choice('size', '2k', '2K')}${choice('size', '4k', '4K')}</div></div>
    <div class="lb-row">${qualityChips(viewer.quality)}<span class="lb-hint" id="closeup-size"></span></div>
    <div class="lb-field"><input id="closeup-subject" maxlength="80" placeholder="What to zoom into, like her face" value="${escapeHTML(closeup.subject)}" aria-label="What to zoom into"><button type="button" class="lb-text strong" id="closeup-find">Find</button><button class="lb-send" id="closeup-send" type="submit" aria-label="Make the close-up">${icon('arrow')}</button></div>
    <p class="lb-hint" id="closeup-status">Drag on the photo to frame the area, or name it and choose Find.</p><p class="lb-error" id="closeup-error" role="alert" hidden></p></form>`;
  $('#closeup-form').onclick = event => {
    const detail = event.target.closest('[data-closeup-detail]'), size = event.target.closest('[data-closeup-size]');
    if (detail) closeup.detail = detail.dataset.closeupDetail;
    if (size) closeup.size = size.dataset.closeupSize;
    if (detail || size) { renderCloseUpPanel(); return; }
  };
  $('#closeup-subject').oninput = event => { closeup.subject = event.target.value; };
  $('#closeup-subject').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); findCloseUp(); } };
  $('#closeup-find').onclick = () => findCloseUp();
  $('#closeup-form').onsubmit = submitCloseUp;
  $('#closeup-send').disabled = closeup.busy || connection.offline;
  $('#closeup-find').disabled = closeup.busy || connection.offline;
  ensureCropLayer(); renderFrame();
}

// The frame sits on the photo itself, so it follows zoom and always matches the pixels that will be used.
function ensureCropLayer() {
  if ($('#lb-crop-layer')) return;
  const layer = document.createElement('div');
  layer.id = 'lb-crop-layer'; layer.className = 'lb-crop-layer';
  layer.innerHTML = `<div class="lb-crop" id="lb-crop">${['nw', 'ne', 'sw', 'se'].map(corner => `<span class="lb-crop-handle ${corner}" data-corner="${corner}"></span>`).join('')}</div>`;
  $('.lb-photo').append(layer);
  let gesture = null;
  const point = event => { const rect = layer.getBoundingClientRect(); return {x:Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y:Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))}; };
  layer.addEventListener('pointerdown', event => {
    if (event.button !== 0 || closeup.busy || gesture) return;
    event.preventDefault(); event.stopPropagation(); layer.setPointerCapture(event.pointerId);
    const p = point(event), f = closeup.frame, corner = event.target.closest('[data-corner]')?.dataset.corner;
    const inside = p.x > f.x && p.x < f.x + f.w && p.y > f.y && p.y < f.y + f.h;
    gesture = corner ? {type:'resize', anchor:{x:corner.includes('w') ? f.x + f.w : f.x, y:corner.includes('n') ? f.y + f.h : f.y}}
      : inside ? {type:'move', start:p, frame:{...f}} : {type:'draw', anchor:p};
  });
  layer.addEventListener('pointermove', event => {
    if (!gesture) return;
    const p = point(event), f = closeup.frame, minimum = .04;
    if (gesture.type === 'move') {
      f.x = Math.max(0, Math.min(1 - f.w, gesture.frame.x + p.x - gesture.start.x));
      f.y = Math.max(0, Math.min(1 - f.h, gesture.frame.y + p.y - gesture.start.y));
    } else {
      const a = gesture.anchor;
      Object.assign(f, {x:Math.min(a.x, p.x), y:Math.min(a.y, p.y), w:Math.max(minimum, Math.abs(p.x - a.x)), h:Math.max(minimum, Math.abs(p.y - a.y))});
      f.x = Math.min(f.x, 1 - f.w); f.y = Math.min(f.y, 1 - f.h);
    }
    renderFrame();
  });
  const end = () => { gesture = null; };
  layer.addEventListener('pointerup', end); layer.addEventListener('pointercancel', end);
  layer.addEventListener('lostpointercapture', end);
}
// Mirrors studio/closeup.py: Standard is about a megapixel within the quality's preset; 2K and 4K set the long edge.
function enlargement(width, height) {
  const long = Math.max(width, height), shape = width > height ? 'landscape' : height > width ? 'portrait' : 'square';
  if (closeup.size !== 'standard') return (closeup.size === '4k' ? 4096 : 2048) / long;
  const factor = Math.min(Math.max(...state.options.qualities[viewer.quality].sizes[shape]) / long, Math.sqrt(1048576 / (width * height)), 2048 / long);
  return Math.max(64, 16 * Math.floor(long * factor / 16)) / long;
}
function renderFrame() {
  const box = $('#lb-crop'), f = closeup.frame;
  if (!box || !f) return;
  Object.assign(box.style, {left:`${f.x * 100}%`, top:`${f.y * 100}%`, width:`${f.w * 100}%`, height:`${f.h * 100}%`});
  const [, , width, height] = framePixels(), ratio = Math.max(width / height, height / width), factor = enlargement(width, height);
  const hint = $('#closeup-size');
  if (!hint) return;
  hint.textContent = width < 32 || height < 32 ? 'Frame at least 32 pixels' : ratio > 4 ? 'Frame no longer than 4 × its width'
    : `${width} × ${height} px · ${factor >= 1.05 ? `${factor.toFixed(1)}× larger` : factor < .95 ? `smaller output · ${Math.round(factor * 100)}% of crop size` : 'same size'}`;
  if ($('#closeup-send')) $('#closeup-send').disabled = closeup.busy || connection.offline || width < 32 || height < 32 || ratio > 4;
  if ($('#closeup-find')) $('#closeup-find').disabled = closeup.busy || connection.offline;
  if ($('#closeup-subject')) $('#closeup-subject').disabled = closeup.busy;
}

// Naming the area uses the same finder as Area: its outline becomes a frame with some room around it.
async function findCloseUp() {
  if (!closeup.item || closeup.busy || connection.offline) return;
  const subject = closeup.subject.trim(), current = closeup.item;
  const revision = ++closeup.revision;
  if (!subject) { $('#closeup-status').textContent = 'Type what to zoom into first.'; return; }
  if (closeup.found === subject && closeup.matches.length > 1) { closeup.match = (closeup.match + 1) % closeup.matches.length; frameMatch(); return; }
  closeup.busy = true; renderFrame(); $('#closeup-find').disabled = true; $('#closeup-status').textContent = 'Finding…';
  try {
    const source = await editSourceFor(current);
    if (closeup.revision !== revision) return;
    const result = await send(`/api/uploads/${source.id}/auto-mask`, {target:subject, protect_faces:false, protect_skin:false});
    const matches = await Promise.all(result.candidates.map(loadAutomaticMask));
    if (closeup.revision !== revision) return;
    closeup.matches = matches;
    closeup.match = 0; closeup.found = subject;
    if (!closeup.matches.length) { $('#closeup-status').textContent = 'Nothing found · try a simpler word or drag a frame'; return; }
    frameMatch();
  } catch (error) { if (closeup.revision === revision && $('#closeup-status')) $('#closeup-status').textContent = error.message; }
  finally { if (closeup.revision === revision) { closeup.busy = false; renderFrame(); } }
}
function frameMatch() {
  const image = closeup.matches[closeup.match], canvas = Object.assign(document.createElement('canvas'), {width:image.naturalWidth, height:image.naturalHeight});
  const context = canvas.getContext('2d', {willReadFrequently:true}); context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  let left = canvas.width, top = canvas.height, right = 0, bottom = 0;
  for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) if (pixels[(y * canvas.width + x) * 4] >= 128) { left = Math.min(left, x); top = Math.min(top, y); right = Math.max(right, x + 1); bottom = Math.max(bottom, y + 1); }
  if (right <= left) return;
  // A little room around the subject keeps its edges and context in the close-up.
  const padX = (right - left) * .2, padY = (bottom - top) * .2;
  const x = Math.max(0, (left - padX) / canvas.width), y = Math.max(0, (top - padY) / canvas.height);
  closeup.frame = {x, y, w:Math.min(1, (right + padX) / canvas.width) - x, h:Math.min(1, (bottom + padY) / canvas.height) - y};
  renderFrame();
  $('#closeup-status').textContent = closeup.matches.length > 1 ? `${closeup.match + 1} of ${closeup.matches.length} · Find again for the next` : 'Framed · adjust it by dragging';
}

async function submitCloseUp(event) {
  event.preventDefault();
  const item = closeup.item, projectId = viewer.project.id;
  if (!item || closeup.busy || connection.offline) return;
  const revision = ++closeup.revision;
  const prepared = {box:framePixels(), size:closeup.size, quality:viewer.quality};
  const settings = {detail:closeup.detail, subject:closeup.subject.trim()}, seed = closeup.seed;
  const variations = seed == null ? studioSettings.edit.variations : 1;
  closeup.busy = true; renderFrame(); $('#closeup-error').hidden = true;
  try {
    const source = await send(`/api/projects/${projectId}/versions/${item.id}/close-up`, prepared);
    if (closeup.revision !== revision) return;
    const result = await send('/api/generate', {mode:'edit', prompt:'Close-up', project_id:projectId, source_id:source.id, quality:prepared.quality,
      variations, seed, close_up:settings});
    if (closeup.revision === revision) {
      viewer.autoSelect = {id:result.jobs[0], from:item.id};
      closeCloseUp(); viewer.tool = 'edit'; renderTools(); fitPhoto();
    }
    await refresh();
    $(`[data-item="${result.jobs[0]}"]`)?.scrollIntoView({block:'nearest', inline:'center', behavior:'smooth'});
  } catch (error) { if (closeup.revision === revision && $('#closeup-error')) { $('#closeup-error').textContent = error.message; $('#closeup-error').hidden = false; } }
  finally { if (closeup.revision === revision) { closeup.busy = false; renderFrame(); } }
}
