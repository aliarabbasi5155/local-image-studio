'use strict';
// Choices people rarely change live in one Settings sheet instead of beside every prompt. Kept on this device.
const settingDefaults = {snofs:true,
  edit:{output_size:'original', resize_mode:'native', variations:1},
  create:{output_size:'preset', resize_mode:'auto', variations:1},
  area:{detail:'balanced', context:'balanced', blend:'adaptive', colorMatch:true, align:true},
  video:{output_size:'original'}};
const studioSettings = (() => {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('studio-settings') || '{}'); } catch { /* Defaults. */ }
  return Object.fromEntries(Object.entries(settingDefaults).map(([key, value]) =>
    [key, typeof value === 'object' ? {...value, ...saved[key]} : saved[key] ?? value]));
})();
function saveSettings() { try { localStorage.setItem('studio-settings', JSON.stringify(studioSettings)); } catch { /* Kept for this visit. */ } }
// What an edit or a new image is made with, apart from its prompt, quality and shape.
const editRecipe = () => ({...studioSettings.edit, snofs:studioSettings.snofs});
const createRecipe = () => ({...studioSettings.create, snofs:studioSettings.snofs});

// The optional style adapter's row is hidden when it isn't installed.
const settingRows = [
  ['Edits', [['edit.output_size', 'Size', [['original', 'Match the photo'], ['preset', 'Preset'], ['2k', '2K'], ['4k', '4K']]],
    ['edit.resize_mode', 'Resizing', [['native', 'None · native size'], ['auto', 'Resize or AI upscale'], ['direct', 'None · full size']]],
    ['edit.variations', 'Images per edit', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]]]],
  ['New images', [['create.output_size', 'Size', [['preset', 'Preset'], ['2k', '2K'], ['4k', '4K']]],
    ['create.resize_mode', 'Resizing', [['auto', 'Resize or AI upscale'], ['native', 'None · native size'], ['direct', 'None · full size']]],
    ['create.variations', 'Images per prompt', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]]]],
  ['Style', [['snofs', 'SNOFS adapter', 'toggle']]],
  ['Area edits', [['area.detail', 'Working size', [['quick', 'Quick · 512 px'], ['balanced', 'Balanced · 768 px'], ['maximum', 'Maximum']]],
    ['area.context', 'Surroundings', [['tight', 'Tight · faster'], ['balanced', 'Balanced'], ['wide', 'Wide · complex shapes']]],
    ['area.blend', 'Edge blending', [['adaptive', 'Automatic'], ['manual', 'Soft edge']]],
    ['area.colorMatch', 'Match surrounding colors', 'toggle'], ['area.align', 'Align patch edges', 'toggle']]],
  ['Videos', [['video.output_size', 'Size', [['original', 'Match the photo'], ['preview', 'Preview · smaller']]]]]];
const settingPath = path => path.split('.').reduce((value, key) => value[key], studioSettings);
function setSetting(path, value) {
  const keys = path.split('.'), last = keys.pop();
  keys.reduce((value, key) => value[key], studioSettings)[last] = value;
  saveSettings();
}

const settingsDialog = document.createElement('dialog');
settingsDialog.id = 'settings-dialog'; settingsDialog.className = 'sheet';
settingsDialog.setAttribute('aria-labelledby', 'settings-title');
document.body.append(settingsDialog);
function renderSettings() {
  settingsDialog.innerHTML = `<header class="sheet-header"><h2 id="settings-title">Settings</h2><button type="button" class="sheet-done" data-close="settings-dialog">Done</button></header>
    ${settingRows.filter(([group]) => group !== 'Style' || typeof state === 'undefined' || state.options?.adapter_available !== false).map(([group, rows]) => `<section class="setting-group"><h3>${group}</h3>${rows.map(([path, label, options]) => options === 'toggle'
      ? `<label class="setting-row"><span>${label}</span><input type="checkbox" role="switch" data-setting="${path}" ${settingPath(path) ? 'checked' : ''}></label>`
      : `<label class="setting-row"><span>${label}</span><select data-setting="${path}">${options.map(([value, text]) => `<option value="${value}" ${String(settingPath(path)) === String(value) ? 'selected' : ''}>${text}</option>`).join('')}</select></label>`).join('')}</section>`).join('')}
    <p class="setting-note">Without resizing, the size choice is not used. New images keep their shape choice in the create panel.</p>`;
  for (const prefix of ['edit', 'create']) {
    const size = settingsDialog.querySelector(`[data-setting="${prefix}.output_size"]`);
    if (size) size.disabled = studioSettings[prefix].resize_mode === 'native';
  }
}
settingsDialog.addEventListener('change', event => {
  const input = event.target.closest('[data-setting]');
  if (!input) return;
  const current = settingPath(input.dataset.setting);
  setSetting(input.dataset.setting, input.type === 'checkbox' ? input.checked : typeof current === 'number' ? Number(input.value) : input.value);
  renderSettings();
  if (typeof scheduleEstimate === 'function') scheduleEstimate();
  if (typeof scheduleViewerEstimate === 'function') scheduleViewerEstimate();
});
function openSettings() { renderSettings(); settingsDialog.showModal(); }
