'use strict';
let shareBusy = false, sharePreviews = [], openingSharedLink = false;
const shareErrors = {
  setup:'Open the installed studio once, then share the photo again. Its sharing support is still updating.',
  empty:'No photo arrived. Share the image file itself, rather than a link to a photo.',
  limit:'The shared-photo inbox holds up to 10 photos and 100 MB. Import or discard pending photos, then share again.',
  size:'Share photos smaller than 20 MB each.',
  type:'Share PNG, JPEG or WebP photos. Convert HEIC photos to JPEG first.',
  storage:'Your phone could not keep the shared photo. Free some browser storage, then share again.'
};
const hasShareInbox = () => window.isSecureContext && 'caches' in window && !!navigator.locks;

async function updateSharedNotice() {
  const params = new URLSearchParams(location.search);
  const error = params.get('share-error');
  let pending = [];
  if (hasShareInbox()) pending = await ShareInbox.list();
  $('#shared-notice').hidden = !pending.length && !error;
  $('#shared-message').textContent = error ? (shareErrors[error] || shareErrors.storage) :
    `${pending.length} shared photo${pending.length === 1 ? '' : 's'} waiting on this phone. ${state.authenticated ? 'Review and import into your library.' : 'Connect to your Mac and unlock the studio to import.'}`;
  $('#shared-review').hidden = !pending.length || !state.authenticated;
  $('#shared-dismiss').hidden = !error;
  return pending;
}

async function resumeShares() {
  try {
    const pending = await updateSharedNotice();
    if (!state.authenticated) return;
    const link = new URLSearchParams(location.hash.slice(1));
    if (link.has('project') && !openingSharedLink) {
      const project = link.get('project'), image = link.get('image');
      const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if (uuid.test(project) && uuid.test(image || '')) {
        openingSharedLink = true;
        try { await showProject(project, image); history.replaceState(null, '', location.pathname + location.search); }
        finally { openingSharedLink = false; }
      }
    }
    if (pending.length && new URLSearchParams(location.search).has('shared')) await reviewShares();
  } catch (error) { toast(error.message); }
}

function clearShareQuery() {
  const url = new URL(location.href);
  url.searchParams.delete('shared'); url.searchParams.delete('share-error');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
}
function releaseSharePreviews() { for (const url of sharePreviews) URL.revokeObjectURL(url); sharePreviews = []; }

async function reviewShares() {
  if (!state.authenticated || shareBusy) return;
  const pending = await ShareInbox.list();
  releaseSharePreviews();
  $('#shared-photos').replaceChildren();
  for (const file of pending) {
    const url = URL.createObjectURL(await file.response.blob()); sharePreviews.push(url);
    const item = document.createElement('li'), image = document.createElement('img'), label = document.createElement('span');
    item.dataset.key = file.key;
    image.src = url; image.alt = ''; label.textContent = file.name;
    item.append(image, label); $('#shared-photos').append(item);
  }
  $('#shared-import').disabled = !pending.length;
  $('#shared-error').textContent = '';
  clearShareQuery(); await updateSharedNotice();
  if (pending.length && !$('#shared-dialog').open) $('#shared-dialog').showModal();
}

async function importShares(discard = false) {
  if (shareBusy) return;
  shareBusy = true;
  for (const button of $('#shared-dialog').querySelectorAll('button')) button.disabled = true;
  $('#shared-error').textContent = '';
  let last = null, count = 0;
  try {
    // Serialize imports/discards across app windows and incoming shares.
    await ShareInbox.exclusive(async () => {
      for (const file of await ShareInbox.list()) {
        if (!discard) {
          $('#shared-progress').textContent = `Importing ${file.name}…`;
          const form = new FormData(); form.append('image', await file.response.blob(), file.name);
          last = await api('/api/uploads', {method:'POST', body:form}); count++;
        }
        await ShareInbox.remove(file.key);
        for (const item of $('#shared-photos').children) { if (item.dataset.key === file.key) { item.remove(); break; } }
      }
    });
    $('#shared-dialog').close();
    if (last) { await refresh(); await showProject(last.project_id, last.id); toast(`${count} shared photo${count === 1 ? '' : 's'} saved in your library.`); }
  } catch (error) {
    $('#shared-error').textContent = `${error.message} Remaining photos stay on this phone; retry or discard them.`;
    if (count) await refresh();
  } finally {
    shareBusy = false;
    for (const button of $('#shared-dialog').querySelectorAll('button')) button.disabled = false;
    $('#shared-progress').textContent = '';
    await updateSharedNotice();
  }
}

$('#shared-review').onclick = () => reviewShares().catch(error => toast(error.message));
$('#shared-dismiss').onclick = () => { clearShareQuery(); updateSharedNotice().catch(() => {}); };
$('#shared-import').onclick = () => importShares();
$('#shared-discard').onclick = () => importShares(true);
$('#shared-dialog').addEventListener('cancel', event => { if (shareBusy) event.preventDefault(); });
$('#shared-dialog').addEventListener('close', releaseSharePreviews);
window.addEventListener('hashchange', resumeShares);
window.addEventListener('pageshow', resumeShares);
document.addEventListener('visibilitychange', () => { if (!document.hidden) resumeShares(); });
updateSharedNotice().catch(() => {});
