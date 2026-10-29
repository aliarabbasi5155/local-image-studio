'use strict';
const bulk = {gallery:{active:false,ids:new Set()},viewer:{active:false,ids:new Set()}};
function selectionEntries(context) { return context==='viewer' ? viewer.project?.versions || [] : state.projects; }
function selectionScope(context) { return context==='viewer' || state.view==='favorites' ? 'images' : 'projects'; }
function bulkBox(context,id,label) {
  if(!bulk[context].active)return '';
  const checked=bulk[context].ids.has(id);
  return `<button type="button" class="selection-check ${checked?'checked':''}" data-select="${context}" data-id="${id}" aria-label="Select ${escapeHTML(label)}" aria-pressed="${checked}">${icon(checked?'check':'select')}</button>`;
}
function renderBulk(context) {
  const host=$(`#${context}-bulk`);if(!host)return;
  const selection=bulk[context],scope=selectionScope(context),entries=selectionEntries(context);
  const available=new Set(entries.map(entry=>entry.id));
  for(const id of selection.ids)if(!available.has(id))selection.ids.delete(id);
  const count=selection.ids.size,unit=scope==='projects'?'projects':'images';
  const signature=JSON.stringify([selection.active,scope,[...selection.ids],entries.length,connection.offline]);
  if(host.dataset.signature===signature)return;
  host.dataset.signature=signature;
  host.classList.toggle('selecting',selection.active);
  host.innerHTML=selection.active ? `<div class="bulk-toolbar"><strong>${count} selected</strong><button type="button" data-bulk="all" data-context="${context}">Select shown</button><button type="button" data-bulk="clear" data-context="${context}" ${count?'':'disabled'}>Clear</button><div class="bulk-tools"><button type="button" data-bulk="favorite" data-context="${context}" ${count?'':'disabled'} title="Favorite selected ${unit}">${icon('heart')}<span>Favorite</span></button><button type="button" data-bulk="unfavorite" data-context="${context}" ${count?'':'disabled'} title="Remove selected favorites">${icon('heart')}<span>Unfavorite</span></button><details class="bulk-download"><summary aria-label="Download selection" ${count?'':'aria-disabled="true"'}>${icon('download')}<span>Download</span></summary><div class="download-menu"><button type="button" data-bulk="png" data-context="${context}" ${count && !connection.offline?'':'disabled'}>PNG ZIP · with recipes</button><button type="button" data-bulk="jpg" data-context="${context}" ${count && !connection.offline?'':'disabled'}>JPG ZIP · easy to share</button>${connection.offline?'<small>ZIP files are made by your Mac.</small>':''}</div></details><button type="button" class="danger" data-bulk="delete" data-context="${context}" ${count?'':'disabled'}>${icon('trash')}<span>Delete</span></button></div><button type="button" data-bulk="done" data-context="${context}">Done</button></div>${scope==='projects'?'<small>Actions apply to every image in the selected projects.</small>':''}` : `<button type="button" class="selection-start" data-bulk="start" data-context="${context}" ${entries.length?'':'disabled'}>${icon('select')} Select ${unit==='projects'?'projects':'images'}</button>`;
}
function renderSelection(context) {
  if(context==='viewer' && $('.version-strip'))renderTimeline();else if(context==='gallery'){gallerySignature='';renderGallery();}
  renderBulk(context);
}
function resetSelection(context) {bulk[context].active=false;bulk[context].ids.clear();renderSelection(context);}
async function bulkAction(action,context) {
  const selection=bulk[context];
  if(action==='start' || action==='done'){selection.active=action==='start';selection.ids.clear();renderSelection(context);return;}
  if(action==='all' || action==='clear'){selection.ids=new Set(action==='all'?selectionEntries(context).slice(0,500).map(entry=>entry.id):[]);renderSelection(context);return;}
  if(!selection.ids.size)return;
  const body={scope:selectionScope(context),ids:[...selection.ids]};
  if(action==='delete'){confirmBulkDeletion(context,body);return;}
  if(action==='png' || action==='jpg'){
    toast('Preparing your ZIP…');
    const result=await send('/api/bulk-download',{...body,format:action});
    const link=document.createElement('a');link.href=result.url;link.download=result.filename;document.body.append(link);link.click();link.remove();
    toast(`${result.images} image${result.images===1?'':'s'} ready in a ${action.toUpperCase()} ZIP.`);return;
  }
  await send('/api/bulk',{...body,action:'favorite',favorite:action==='favorite'});
  await refresh();renderSelection(context);toast(action==='favorite'?'Selection favorited.':'Selection removed from favorites.');
}
function confirmBulkDeletion(context,body) {
  if(deletePending)return;
  const projects=body.scope==='projects',count=body.ids.length;
  deletingImage={url:'/api/bulk',method:'POST',body:{...body,action:'delete',confirm:true},context};
  $('#delete-title').textContent=`Delete ${count} ${projects?'project':'image'}${count===1?'':'s'}?`;
  $('#delete-cancel').textContent='Keep selection';$('#delete-thumbnail').hidden=true;
  $('#delete-prompt').textContent=`${count} selected ${projects?'projects':'images'}`;
  $('#delete-size').textContent=projects?'Includes every original and edit':'Only selected images';
  $('#delete-description').textContent=projects?'This permanently removes all images, recipes and editing sources in these projects.':'Selected images will disappear. Unselected edits stay, including copies of their parents for comparison.';
  $('#delete-error').hidden=true;$('#delete-dialog').showModal();$('#delete-cancel').focus();
}
async function copyText(text,label='Copied') {
  if(!text)return toast('There is nothing to copy yet.');
  try {
    if(navigator.clipboard && window.isSecureContext)await navigator.clipboard.writeText(text);
    else {
      const field=document.createElement('textarea');field.value=text;field.className='clipboard-field';
      const parent=document.querySelector('dialog[open]') || document.body;parent.append(field);field.focus();field.select();
      const copied=document.execCommand('copy');field.remove();if(!copied)throw new Error('copy');
    }
    toast(label);
  }catch{toast('Copy is unavailable in this browser. Select the prompt text to copy it.');}
}
function jobDisplay(job) {
  const detail=job.progress_detail;
  const measured=detail && detail.total>0 && detail.value>=0;
  const percent=measured?Math.min(100,Math.round(detail.value/detail.total*100)):null;
  const stage=job.cancel_requested?'Stopping':job.status==='queued'?'Queued':detail?.stage==='head detail'||/refining head/i.test(job.phase)?'Head detail':detail?.stage==='sampling'?'Generating':detail?.stage==='enhancing'||/enhancing/i.test(job.phase)?'Upscaling':/saving/i.test(job.phase)?'Saving':/blend|patch/i.test(job.phase)?'Blending':/fitting/i.test(job.phase)?'Finishing':'Preparing';
  const time=job.started?elapsedText(job):null;
  const remaining=job.estimate?.remaining_high!=null?duration((job.estimate.remaining_low+job.estimate.remaining_high)/2):null;
  return {percent,stage,time,remaining,steps:measured?`${detail.value} / ${detail.total} ${detail.stage==='enhancing'?'tiles':'steps'}`:null};
}
function progressHTML(job) {
  const value=jobDisplay(job);
  return `<div class="work-badge" data-work="${job.id}" role="status" title="${escapeHTML(`${job.phase}${value.steps?` · ${value.steps}`:''}${value.remaining?` · approximately ${value.remaining} remaining from similar runs`:''}`)}"><span class="work-ring ${value.percent==null?'spinning':''}">${value.percent==null?icon('spark'):`${value.percent}<small>%</small>`}</span><span class="work-copy"><strong>${value.stage}</strong><small>${value.steps || value.time || 'Waiting for your Mac'}${value.time && value.steps?` · ${value.time}`:''}${value.remaining?` · ≈${value.remaining} left`:''}</small></span></div>`;
}
function updateWorkBadges() {
  for(const job of state.jobs)document.querySelectorAll(`[data-work="${job.id}"]`).forEach(element=>{
    const fresh=document.createElement('template');fresh.innerHTML=progressHTML(job);const replacement=fresh.content.firstElementChild;
    element.title=replacement.title;element.innerHTML=replacement.innerHTML;
    element.querySelector('.work-ring').style.setProperty('--progress',`${jobDisplay(job).percent || 0}%`);
  });
}
document.addEventListener('click',event=>{
  const check=event.target.closest('[data-select]');
  if(check){const selection=bulk[check.dataset.select];if(selection.ids.has(check.dataset.id))selection.ids.delete(check.dataset.id);else if(selection.ids.size<500)selection.ids.add(check.dataset.id);else toast('Select up to 500 entries at a time.');renderSelection(check.dataset.select);return;}
  const button=event.target.closest('[data-bulk]');
  if(button && !button.disabled){button.disabled=true;bulkAction(button.dataset.bulk,button.dataset.context).catch(error=>toast(error.message)).finally(()=>{if(button.isConnected)button.disabled=false;});}
});
renderBulk('gallery');
