'use strict';
// Animate: turn the photo in the viewer into a short clip. The clip joins the project's strip like any image.
const animation = {frames:97, prompt:'', readiness:null, checked:0};
async function videoReadiness() {
  if (Date.now() - animation.checked > 60000) {
    animation.checked = Date.now();
    try { animation.readiness = await api('/api/videos/options'); }
    catch (error) { animation.readiness = {ready:false, error:error.message}; }
  }
  return animation.readiness;
}
function renderAnimatePanel() {
  $('#lb-panel').innerHTML = `<form id="animate-form" class="lb-form"><div class="lb-row"><div class="lb-segment" role="radiogroup" aria-label="Clip length">${[[49, '2 s'], [97, '4 s'], [145, '6 s']].map(([frames, label]) => `<button type="button" role="radio" data-frames="${frames}" aria-checked="${animation.frames === frames}">${label}</button>`).join('')}</div><span class="lb-hint" id="animate-status"></span></div>
    <div class="lb-field"><textarea id="animate-prompt" rows="1" maxlength="2000" required placeholder="Describe the motion, like the camera slowly moves closer" aria-label="Describe the motion"></textarea><button class="lb-send" id="animate-send" type="submit" aria-label="Animate photo" disabled>${icon('arrow')}</button></div>
    <p class="lb-error" id="animate-error" role="alert" hidden></p></form>`;
  const prompt = $('#animate-prompt');
  prompt.value = animation.prompt; growPrompt(prompt);
  prompt.oninput = () => { animation.prompt = prompt.value; growPrompt(prompt); };
  $('#animate-form').onclick = event => {
    const length = event.target.closest('[data-frames]');
    if (!length) return;
    animation.frames = Number(length.dataset.frames);
    for (const button of document.querySelectorAll('[data-frames]')) button.setAttribute('aria-checked', String(button === length));
  };
  $('#animate-form').onsubmit = submitAnimation;
  videoReadiness().then(status => {
    if (!$('#animate-send')) return;
    $('#animate-send').disabled = !status?.ready || connection.offline;
    $('#animate-status').textContent = status?.ready ? '' : status?.error || 'Video setup is needed on your Mac.';
  });
}
async function submitAnimation(event) {
  event.preventDefault();
  const item = selectedImage(), button = $('#animate-send');
  if (!item || button.disabled) return;
  button.disabled = true; $('#animate-error').hidden = true;
  try {
    const source = await editSourceFor(item);
    const clip = await send('/api/videos', {source_id:source.id, prompt:animation.prompt, output_size:studioSettings.video.output_size, frames:animation.frames, seed:null});
    animation.prompt = '';
    viewer.autoSelect = {id:clip.id, from:item.id};
    viewer.tool = 'edit'; renderTools(); fitPhoto();
    await refreshOpenProject();
    $(`[data-item="${clip.id}"]`)?.scrollIntoView({block:'nearest', inline:'center', behavior:'smooth'});
  } catch (error) {
    if ($('#animate-error')) { $('#animate-error').textContent = error.message; $('#animate-error').hidden = false; }
  } finally { if ($('#animate-send')) $('#animate-send').disabled = false; }
}
