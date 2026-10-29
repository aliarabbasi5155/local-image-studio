'use strict';
// Identity swap: replace the head or the whole person, in place on the viewer. The server finds both people
// with SAM 3; the pink preview on the photo shows exactly which pixels may change before anything is generated.
const identityModes={head:['head'],whole:['face','head','body','clothes']};
let identity=null;

const chosenParts=()=>identityModes[identity?.mode || 'head'];
const partsLabel=parts=>parts.includes('body')?'whole person':'head';

async function openIdentitySwap(restore=null) {
  const version=selectedImage();if(!version)return;
  const mode=restore?.mode || (restore?.parts?.some(part=>['body','clothes'].includes(part))?'whole':'head');
  identity={version,projectId:viewer.project.id,mode,source:null,donor:null,areas:null,finding:false,submitting:false,loading:false,revision:0,
    notes:restore?.notes || '',keep:restore?.keep_object || ''};
  if(restore)identity.donor={id:restore.donor_id,url:`/media/upload/${restore.donor_id}`,width:restore.donor.width,height:restore.donor.height};
  viewer.tool='identity';renderTools();fitPhoto();
}
function closeIdentity() { identity=null;$('#identity-overlay')?.remove(); }

function renderIdentityPanel() {
  const current=identity;
  $('#lb-panel').innerHTML=`<div class="lb-form identity-panel"><div class="lb-row"><div class="lb-segment" role="radiogroup" aria-label="What to replace"><button type="button" role="radio" data-identity-mode="head" aria-checked="${current.mode==='head'}">Head</button><button type="button" role="radio" data-identity-mode="whole" aria-checked="${current.mode==='whole'}">Whole person</button></div>${qualityChips(viewer.quality)}<span class="lb-estimate" id="identity-estimate"></span></div>
    <div class="identity-donor"><button type="button" class="identity-person" id="identity-pick" aria-label="Choose the replacement person from your library">${current.donor?`<img src="${current.donor.url}" alt="">`:`<span>${icon('image')}</span>`}<strong>${current.donor?'Replacement person':'Choose a person'}</strong></button><button type="button" class="lb-text" id="identity-upload-button">Upload</button><input id="identity-upload" type="file" accept="image/png,image/jpeg,image/webp" hidden></div>
    <div id="identity-library" class="identity-library" hidden></div>
    <div class="lb-field"><input id="identity-notes" maxlength="2000" placeholder="Notes, like keep the smile · optional" value="${escapeHTML(current.notes)}"><button type="button" class="lb-send" id="identity-swap" aria-label="Preview swap">${icon('arrow')}</button></div>
    <details class="identity-more"${current.keep?' open':''}><summary>Keep a held object</summary><input id="identity-object" maxlength="80" placeholder="Like wine glass or phone · found automatically" value="${escapeHTML(current.keep)}"></details>
    <p id="identity-status" class="lb-hint" role="status"></p><p id="identity-error" class="lb-error" role="alert" hidden></p></div>`;
  let overlay=$('#identity-overlay');
  if(!overlay){overlay=document.createElement('canvas');overlay.id='identity-overlay';overlay.setAttribute('aria-hidden','true');$('.lb-photo').append(overlay);}
  const scale=Math.min(1,2048/Math.max(current.version.width,current.version.height));
  overlay.width=Math.round(current.version.width*scale);overlay.height=Math.round(current.version.height*scale);
  bindIdentity();updateIdentity();drawAreas().catch(()=>{});
}

function bindIdentity() {
  $('#lb-panel .identity-panel').addEventListener('click',event=>{
    const mode=event.target.closest('[data-identity-mode]');
    if(mode&&!mode.disabled){identity.mode=mode.dataset.identityMode;for(const button of document.querySelectorAll('[data-identity-mode]'))button.setAttribute('aria-checked',String(button===mode));changed();}
    if(event.target.closest('[data-quality]'))scheduleIdentityEstimate();
  });
  $('#identity-notes').oninput=event=>{identity.notes=event.target.value;};
  $('#identity-object').oninput=event=>{identity.keep=event.target.value;changed();};
  $('#identity-pick').onclick=()=>toggleLibrary().catch(error=>identityError(error));
  $('#identity-upload-button').onclick=()=>$('#identity-upload').click();
  $('#identity-upload').onchange=event=>uploadDonor(event.target.files[0]).catch(error=>identityError(error)).finally(()=>{event.target.value='';});
  $('#identity-swap').onclick=()=>submitIdentitySwap().catch(error=>identityError(error));
}

function changed(){
  if(!identity)return;identity.revision++;identity.areas=null;$('#identity-error').hidden=true;
  const overlay=$('#identity-overlay');overlay?.getContext('2d').clearRect(0,0,overlay.width,overlay.height);
  updateIdentity();
}
function identityError(error){if(!identity||!$('#identity-error'))return;$('#identity-error').textContent=error.message;$('#identity-error').hidden=false;$('#identity-status').textContent='';}

function updateIdentity() {
  if(!identity||!$('#identity-swap'))return;
  const ready=!!identity.donor,busy=identity.finding||identity.submitting||identity.loading;
  $('#identity-swap').disabled=!ready||busy||connection.offline;
  $('#identity-swap').setAttribute('aria-label',identity.areas?`Swap ${partsLabel(chosenParts())}`:'Preview swap');
  $('#identity-swap').classList.toggle('ready',!!identity.areas);
  for(const input of document.querySelectorAll('[data-identity-mode],#identity-object,#identity-pick,#identity-upload-button,#identity-library button'))input.disabled=!!busy;
  if(!busy)$('#identity-status').textContent=!identity.donor?'Choose who should appear in this photo.':identity.areas?'Pink shows what changes. Send to swap.':'Send to preview what changes.';
  scheduleIdentityEstimate();
}

async function toggleLibrary() {
  const current=identity;
  const panel=$('#identity-library');
  if(!panel.hidden){panel.hidden=true;return;}
  panel.hidden=false;panel.innerHTML='<p class="lb-hint">Loading your library…</p>';
  const library=await api('/api/projects?section=library&limit=60');
  if(identity!==current)return;
  panel.innerHTML=`<div class="identity-grid">${library.projects.filter(p=>p.cover).map(p=>`<button type="button" data-project="${p.id}"><img src="${p.cover.thumbnail}" alt="${escapeHTML(p.title)}" loading="lazy"></button>`).join('')}</div>`;
  panel.onclick=async event=>{
    const project=event.target.closest('[data-project]'),picked=event.target.closest('[data-version]');
    try{
      if(project){
        const detail=await api(`/api/projects/${project.dataset.project}`);
        if(identity!==current)return;
        panel.innerHTML=`<p class="lb-hint"><button type="button" class="lb-text" data-back>${icon('back')} Projects</button> Choose the exact image</p><div class="identity-grid">${detail.versions.filter(v=>v.ready).map(v=>`<button type="button" data-version="${v.id}" data-owner="${detail.id}"><img src="${v.thumbnail}" alt="${escapeHTML(v.prompt || 'Image')}" loading="lazy"></button>`).join('')}</div>`;
      } else if(picked){
        current.loading=true;updateIdentity();
        const source=await send(`/api/projects/${picked.dataset.owner}/versions/${picked.dataset.version}/edit-source`,{});
        if(identity!==current)return;
        identity.donor={id:source.id,url:source.url,width:source.width,height:source.height};current.loading=false;renderIdentityPanel();changed();
      } else if(event.target.closest('[data-back]')){panel.hidden=true;await toggleLibrary();}
    }catch(error){if(identity===current)identityError(error);}
    finally{current.loading=false;if(identity===current)updateIdentity();}
  };
}

async function uploadDonor(file) {
  if(!file)return;
  const current=identity;
  if(file.size>20*1024*1024)throw new Error('Choose a photo smaller than 20 MB.');
  current.loading=true;updateIdentity();
  try{
    const form=new FormData();form.append('image',file);$('#identity-status').textContent='Uploading…';
    const source=await api('/api/uploads',{method:'POST',body:form});
    if(identity!==current)return;
    identity.donor={id:source.id,url:source.url,width:source.width,height:source.height};current.loading=false;renderIdentityPanel();changed();
  }finally{current.loading=false;if(identity===current)updateIdentity();}
}

async function editSource() {
  const current=identity;
  if(current.source)return current.source;
  return current.source=await editSourceFor(current.version);
}

async function findIdentityAreas() {
  const current=identity,parts=chosenParts(),mode=current.mode,revision=current.revision,keep_object=current.keep.trim(),donor_id=current.donor.id;
  if(current.finding||current.submitting||current.loading)return null;
  current.finding=true;updateIdentity();$('#identity-error').hidden=true;
  $('#identity-status').textContent='Finding both people…';
  try{
    const source=await editSource();
    const areas=await send(`/api/uploads/${source.id}/identity-areas`,{donor_id,parts,mode,keep_object});
    if(identity!==current||current.revision!==revision)return null;
    current.areas=areas;await drawAreas();
    return areas;
  }finally{if(identity===current){current.finding=false;updateIdentity();if(current.areas&&!current.areas.summary.objects_protected)$('#identity-status').textContent='Pink shows what changes. No held object found; name one below if needed.';}}
}

async function drawAreas() {
  const areas=identity?.areas,canvas=$('#identity-overlay');if(!areas||!canvas?.width)return;
  const image=await loadMaskImage(areas.mask.url);if(!identity||identity.areas!==areas)return;
  const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(binaryMaskCanvas(image,canvas.width,canvas.height),0,0);
}

let identityEstimateTimer;
function scheduleIdentityEstimate() {
  clearTimeout(identityEstimateTimer);
  identityEstimateTimer=setTimeout(async()=>{
    const current=identity,hint=$('#identity-estimate');if(!hint)return;
    if(!current?.areas||connection.offline){hint.textContent='';return;}
    const areas=current.areas,v=current.version,settings=editRecipe();
    try{const result=await send('/api/estimate',{mode:'edit',project_id:current.projectId,version_id:v.id,aspect:aspectOf(v),quality:viewer.quality,...settings,mask_bounds:areas.mask.bounds,identity_swap:true,identity_mode:areas.mode});
      if(identity===current&&current.areas===areas&&$('#identity-estimate'))$('#identity-estimate').textContent=estimateText(result,settings.variations);}
    catch{if(identity===current&&$('#identity-estimate'))$('#identity-estimate').textContent='';}
  },250);
}

async function submitIdentitySwap() {
  const current=identity,parts=chosenParts(),mode=current.mode;
  if(current.finding||current.submitting||current.loading)return;
  $('#identity-error').hidden=true;
  if(!current.areas){await findIdentityAreas();return;}
  const areas=current.areas;
  if(!areas||identity!==current)return;
  current.submitting=true;updateIdentity();
  const notes=current.notes.trim();
  try{
    const source=await editSource(),v=current.version;
    if(identity!==current)return;
    const prompt=`Identity swap · ${partsLabel(parts)}`+(notes?` · ${notes}`:'');
    const result=await send('/api/generate',{mode:'edit',prompt,project_id:current.projectId,source_id:source.id,parent_image_id:source.parent_image_id,
      aspect:aspectOf(v),quality:viewer.quality,...editRecipe(),snofs:false,
      mask_id:areas.mask.id,mask_core_id:areas.core.id,mask_feather:8,mask_blend_mode:'adaptive',
      identity_swap:{mode,parts,donor_id:current.donor.id,donor_crops:areas.donor_crops,keep_object:areas.keep_object,
        appearance_mask:areas.appearance_mask&&{mask_id:areas.appearance_mask.id},
        head_pass:areas.head_pass&&{mask_id:areas.head_pass.mask.id,core_mask_id:areas.head_pass.core.id},notes}});
    viewer.autoSelect={id:result.jobs[0],from:v.id};
    if(identity===current){closeIdentity();viewer.tool='edit';renderTools();fitPhoto();}
    await refresh();
  }finally{current.submitting=false;if(identity===current)updateIdentity();}
}
