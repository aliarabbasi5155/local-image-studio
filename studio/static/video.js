'use strict';
const videoState = {source:null, ready:false, timer:null, revision:0, submitting:false};
const videoDialog = document.createElement('dialog');
videoDialog.className = 'video-dialog';
videoDialog.setAttribute('aria-labelledby', 'video-title');
videoDialog.innerHTML = `<button type="button" class="icon-button dialog-close" id="video-close" aria-label="Close videos">×</button>
  <span class="eyebrow">MOTION FROM A SINGLE PHOTO</span><h2 id="video-title">Photo to video</h2>
  <p id="video-model" role="status">Checking your Mac…</p>
  <form id="video-form">
    <div class="video-source"><img id="video-source-preview" alt="Selected source photo" hidden><label>Choose a photo<input id="video-upload" type="file" accept="image/png,image/jpeg,image/webp"></label></div>
    <label>Describe the motion<textarea id="video-prompt" maxlength="2000" required placeholder="The camera slowly moves closer. Leaves sway gently in the breeze."></textarea></label>
    <div class="video-settings"><label>Output size<select id="video-size"><option value="original">Match photo · exact dimensions</option><option value="preview">Preview · smaller file</option></select></label>
    <label>Length<select id="video-frames"><option value="49">About 2 seconds</option><option value="97">About 4 seconds</option><option value="145">About 6 seconds</option></select></label>
    <label>Seed<input id="video-seed" type="number" min="0" max="4294967295" step="1" placeholder="Random"></label></div>
    <p id="video-size-hint" aria-live="polite">Choose a photo to see its output dimensions.</p>
    <p>Your photo’s ratio is preserved without cropping. Match photo restores the original dimensions after generation; enlargement does not add native detail. Videos run one at a time. Longer clips take more time.</p>
    <button class="primary" type="submit" id="video-generate" disabled>Generate video</button>
  </form><p id="video-error" class="error" role="alert"></p>
  <h3>Your clips</h3><p id="video-list-status" role="status"></p><div id="video-list" class="video-list"></div>`;
document.body.append(videoDialog);

function closeVideos() {
  videoDialog.close();
  videoState.source = null;
  $('#video-source-preview').removeAttribute('src');
  $('#video-source-preview').hidden = true;
  $('#video-list').replaceChildren();
  videoSizeHint();
}

function videoButton() { $('#video-generate').disabled = !videoState.ready || !videoState.source || videoState.submitting; }
function videoSource(source) {
  videoState.source = source;
  const preview = $('#video-source-preview'); preview.src = source.url; preview.hidden = false;
  videoSizeHint();
  videoButton();
}
function videoSizeHint() {
  const source = videoState.source;
  if (!source) { $('#video-size-hint').textContent = 'Choose a photo to see its output dimensions.'; return; }
  let width = source.width, height = source.height;
  if ($('#video-size').value === 'preview') {
    let a = width, b = height; while (b) { const next = a % b; a = b; b = next; }
    const unitW = width / a, unitH = height / a;
    const factor = Math.max(1, Math.min(a, Math.floor(Math.min(768 / unitW, 768 / unitH, Math.sqrt(262144 / (unitW * unitH))))));
    width = unitW * factor; height = unitH * factor;
  }
  $('#video-size-hint').textContent = `${width} × ${height} · same ratio as your ${source.width} × ${source.height} photo.` +
    (width % 2 || height % 2 ? ' Exact odd dimensions use H.264 4:4:4; some players may require downloading the clip.' : '') +
    ($('#video-size').value === 'preview' && width === source.width && height === source.height ? ' This photo stays at its original size to preserve its exact ratio.' : '');
}
async function openVideos() {
  if (!videoDialog.open) videoDialog.showModal();
  $('#video-error').textContent = '';
  videoState.ready = false; videoButton();
  const revision = ++videoState.revision;
  const options = await api('/api/videos/options');
  if (revision !== videoState.revision || !videoDialog.open) return;
  videoState.ready = options.ready;
  $('#video-model').textContent = options.ready ? options.model : options.error || 'Video setup is needed on your Mac.';
  videoButton();
  await refreshVideos();
}
async function openVideoForSelected() {
  const version = selectedVersion(), project = viewer.project;
  if (!version?.ready) throw new Error('Wait for this image to finish first.');
  const source = await send(`/api/projects/${project.id}/versions/${version.id}/edit-source`, {});
  videoSource(source);
  await openVideos();
}
async function refreshVideos() {
  clearTimeout(videoState.timer);
  if (!videoDialog.open) return;
  try {
    const {videos} = await api('/api/videos');
    if (!videoDialog.open) return;
    const list = $('#video-list');
    $('#video-list-status').textContent = videos.length ? '' : 'Your generated clips will appear here.';
    // Retain completed video elements while polling so playback is uninterrupted.
    for (const row of videos) {
      let card = list.querySelector(`[data-video="${row.id}"]`);
      if (card && card.dataset.status === row.status) continue;
      const next = document.createElement('article'); next.className = 'video-card';
      next.dataset.video = row.id; next.dataset.status = row.status;
      const title = document.createElement('strong'); title.textContent = `${row.status} · ${(row.recipe.frames / row.recipe.fps).toFixed(1)} sec · ${row.recipe.width} × ${row.recipe.height}`;
      const prompt = document.createElement('p'); prompt.textContent = row.recipe.prompt;
      next.append(title, prompt);
      if (row.error) { const error = document.createElement('p'); error.className = 'error'; error.textContent = row.error; next.append(error); }
      if (row.status === 'complete') {
        const player = document.createElement('video'); player.controls = true; player.playsInline = true; player.preload = 'metadata'; player.src = row.url;
        const links = document.createElement('div'); links.className = 'video-links';
        const download = document.createElement('a'); download.href = `${row.url}?download=1`; download.textContent = 'Download MP4'; download.download = `${row.id}.mp4`;
        const recipe = document.createElement('a'); recipe.href = `/api/videos/${row.id}/recipe`; recipe.textContent = 'Download settings';
        links.append(download, recipe); next.append(player, links);
      } else if (['queued','running'].includes(row.status)) {
        const cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.type = 'button';
        cancel.onclick = async () => { try { await send(`/api/videos/${row.id}/cancel`, {}); await refreshVideos(); } catch (error) { $('#video-error').textContent = error.message; } };
        next.append(cancel);
      }
      if (card) card.replaceWith(next); else list.append(next);
    }
    // The server returns newest first, including newly queued videos.
    videos.forEach((row, index) => { const card = list.querySelector(`[data-video="${row.id}"]`); if (card && list.children[index] !== card) list.insertBefore(card, list.children[index] || null); });
  } catch (error) { $('#video-error').textContent = error.message; }
  if (videoDialog.open) videoState.timer = setTimeout(refreshVideos, 3000);
}
$('#open-videos').onclick = () => openVideos().catch(error => { $('#video-error').textContent = error.message; });
$('#video-close').onclick = () => videoDialog.close();
$('#video-size').onchange = videoSizeHint;
videoDialog.addEventListener('close', () => { ++videoState.revision; clearTimeout(videoState.timer); videoDialog.querySelectorAll('video').forEach(video => video.pause()); });
$('#video-upload').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 20 * 1024 * 1024) throw new Error('Choose a photo smaller than 20 MB.');
    const form = new FormData(); form.append('image', file);
    videoSource(await api('/api/uploads', {method:'POST', body:form}));
  } catch (error) { $('#video-error').textContent = error.message; }
  event.target.value = '';
};
$('#video-form').onsubmit = async event => {
  event.preventDefault(); if (!videoState.source || videoState.submitting) return;
  videoState.submitting = true; videoButton(); $('#video-error').textContent = '';
  try {
    await send('/api/videos', {source_id:videoState.source.id, prompt:$('#video-prompt').value,
      output_size:$('#video-size').value, frames:Number($('#video-frames').value),
      seed:$('#video-seed').value === '' ? null : Number($('#video-seed').value)});
    await refreshVideos();
  } catch (error) { $('#video-error').textContent = error.message; }
  finally { videoState.submitting = false; videoButton(); }
};
