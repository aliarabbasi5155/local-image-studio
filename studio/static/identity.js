'use strict';
// Identity swap: replace the head or the whole person. The server finds both people
// with SAM 3; the pink preview shows exactly which pixels may change before anything is generated.
const identityModes={head:['head'],whole:['face','head','body','clothes']};
let identity=null;

function identityHTML() {
  return `<div class="identity-panel"><header class="identity-header"><div><span class="eyebrow">IDENTITY SWAP</span><h2 id="identity-title">Replace a person, keep the moment</h2><p>The original photo guides the pose, head angle, expression and interactions.</p></div><button type="button" class="icon-button" data-close="identity-dialog" aria-label="Close identity swap">${icon('close')}</button></header>
    <div class="identity-body"><div class="identity-photos">
      <figure><figcaption>Original · pose & scene</figcaption><div class="identity-frame"><span class="identity-fit"><img id="identity-target" alt="Original photo whose pose and scene are kept"><canvas id="identity-overlay" aria-hidden="true"></canvas></span></div></figure>
      <figure><figcaption>Replacement · identity</figcaption><div class="identity-frame" id="identity-donor-frame"><span class="identity-fit"><img id="identity-donor" alt="Person to put into the original photo" hidden><span id="identity-crops"></span></span><p id="identity-empty">Choose the replacement person</p></div>
        <div class="identity-donor-actions"><button type="button" id="identity-pick">${icon('grid')} Library</button><button type="button" id="identity-upload-button">${icon('image')} Upload</button><input id="identity-upload" type="file" accept="image/png,image/jpeg,image/webp" hidden></div></figure>
    </div>
    <div id="identity-library" class="identity-library" hidden></div>
    <fieldset class="identity-parts"><legend>What to replace</legend>
      <label><input type="radio" name="identity-mode" value="head" checked><span><strong>Head identity</strong><small>Replace the head, face and hair.<br>Keep the original body and outfit.</small></span></label>
      <label><input type="radio" name="identity-mode" value="whole"><span><strong>Whole identity</strong><small>Replace the person, including body and outfit.<br>Keep the original pose and what they’re doing.</small></span></label></fieldset>
    <p class="identity-help">Use a clear photo of one person. A similar head angle helps likeness; whole identity also needs their body and outfit visible.</p>
    <label class="identity-notes">Object to keep <span>optional · held objects are detected automatically</span><input id="identity-object" maxlength="80" placeholder="For example: wine glass, phone or handbag"></label>
    <label class="identity-notes">Notes for this swap <span>optional</span><textarea id="identity-notes" rows="2" maxlength="2000" placeholder="For example: keep the smile, match the warm evening light"></textarea></label>
    <p id="identity-status" role="status"></p><p id="identity-estimate" class="estimate-hint" aria-live="polite"></p><p id="identity-error" class="error" role="alert" hidden></p></div>
    <footer class="identity-footer"><div class="identity-settings"><label>Quality<select id="identity-quality"><option value="fast">Fast</option><option value="balanced">Balanced</option><option value="high">High</option></select></label><label>Images<select id="identity-variations"><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option></select></label></div><button type="button" id="identity-find" hidden>Refresh preview</button><button type="button" class="primary" id="identity-swap">Preview swap ${icon('arrow')}</button></footer></div>`;
}

const chosenMode=()=>document.querySelector('[name="identity-mode"]:checked')?.value || 'head';
const chosenParts=()=>identityModes[chosenMode()];
const partsLabel=parts=>parts.includes('body')?'whole identity':'head identity';

async function openIdentitySwap(restore=null) {
  const version=selectedVersion();if(!version?.ready)return;
  const dialog=$('#identity-dialog');dialog.innerHTML=identityHTML();
  identity={version,projectId:viewer.project.id,source:null,donor:null,areas:null,finding:false,submitting:false,revision:0};
  const current=identity;
  $('#identity-target').src=version.url;
  const mode=restore?.mode || (restore?.parts?.some(part=>['body','clothes'].includes(part))?'whole':'head');
  for(const input of document.querySelectorAll('[name="identity-mode"]'))input.checked=input.value===mode;
  $('#identity-notes').value=restore?.notes || '';
  $('#identity-object').value=restore?.keep_object || '';
  bindIdentity();dialog.showModal();
  if(restore){
    identity.donor={id:restore.donor_id,url:`/media/upload/${restore.donor_id}`,width:restore.donor.width,height:restore.donor.height};showDonor();
    // Recompute masks when reusing: old face/body recipes and protected objects need the new preview.
    if(identity!==current)return;
  }
  updateIdentity();
}

function bindIdentity() {
  for(const input of document.querySelectorAll('[name="identity-mode"]'))input.onchange=changed;
  $('#identity-object').oninput=changed;
  $('#identity-pick').onclick=()=>toggleLibrary().catch(error=>identityError(error));
  $('#identity-upload-button').onclick=()=>$('#identity-upload').click();
  $('#identity-upload').onchange=event=>uploadDonor(event.target.files[0]).catch(error=>identityError(error)).finally(()=>{event.target.value='';});
  $('#identity-find').onclick=()=>findIdentityAreas().catch(error=>identityError(error));
  $('#identity-swap').onclick=()=>submitIdentitySwap().catch(error=>identityError(error));
  // Shared with the edit box, so both always queue with the same settings.
  $('#identity-quality').value=viewer.settings.quality;$('#identity-variations').value=viewer.settings.variations;
  $('#identity-quality').onchange=event=>{viewer.settings.quality=event.target.value;saveEditor();updateEditorSettings();scheduleIdentityEstimate();};
  $('#identity-variations').onchange=event=>{viewer.settings.variations=Number(event.target.value);saveEditor();updateEditorSettings();scheduleIdentityEstimate();};
  $('#identity-target').onload=()=>{const canvas=$('#identity-overlay'),image=$('#identity-target');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;drawAreas().catch(()=>{});};
}

function changed(){if(!identity)return;identity.revision++;identity.areas=null;$('#identity-error').hidden=true;$('#identity-overlay').getContext('2d').clearRect(0,0,$('#identity-overlay').width,$('#identity-overlay').height);$('#identity-crops').replaceChildren();updateIdentity();}
function identityError(error){if(!identity)return;$('#identity-error').textContent=error.message;$('#identity-error').hidden=false;$('#identity-status').textContent='';}

function updateIdentity() {
  if(!identity)return;
  const ready=!!identity.donor,busy=identity.finding||identity.submitting||identity.loading;
  $('#identity-find').hidden=!identity.areas;$('#identity-find').disabled=!ready||busy;$('#identity-swap').disabled=!ready||busy;
  $('#identity-swap').innerHTML=`${identity.areas?'Swap identity':'Preview swap'} ${icon('arrow')}`;
  for(const input of document.querySelectorAll('[name="identity-mode"],#identity-object,#identity-pick,#identity-upload-button,#identity-quality,#identity-variations,#identity-library button'))input.disabled=!!busy;
  if(!busy&&!identity.areas)$('#identity-status').textContent=!identity.donor?'Choose the replacement person.':'Preview the pink edit area before swapping. Whole identity includes a separate head detail pass.';
  scheduleIdentityEstimate();
}

async function toggleLibrary() {
  const current=identity;
  const panel=$('#identity-library');
  if(!panel.hidden){panel.hidden=true;return;}
  panel.hidden=false;panel.innerHTML='<p>Loading your library…</p>';
  const library=await api('/api/projects?section=library&limit=60');
  if(identity!==current)return;
  panel.innerHTML=`<p>Choose a project, then the exact image.</p><div class="identity-grid">${library.projects.filter(p=>p.cover).map(p=>`<button type="button" data-project="${p.id}"><img src="${p.cover.thumbnail}" alt="${escapeHTML(p.title)}" loading="lazy"></button>`).join('')}</div>`;
  panel.onclick=async event=>{
    const project=event.target.closest('[data-project]'),picked=event.target.closest('[data-version]');
    try{
      if(project){
        const detail=await api(`/api/projects/${project.dataset.project}`);
        if(identity!==current)return;
        panel.innerHTML=`<p>${escapeHTML(detail.title)} · choose an image <button type="button" data-back>Back</button></p><div class="identity-grid">${detail.versions.filter(v=>v.ready).map(v=>`<button type="button" data-version="${v.id}" data-owner="${detail.id}"><img src="${v.thumbnail}" alt="${escapeHTML(v.prompt || 'Image')}" loading="lazy"></button>`).join('')}</div>`;
      } else if(picked){
        current.loading=true;updateIdentity();
        const source=await send(`/api/projects/${picked.dataset.owner}/versions/${picked.dataset.version}/edit-source`,{});
        if(identity!==current)return;
        identity.donor={id:source.id,url:source.url,width:source.width,height:source.height};panel.hidden=true;showDonor();changed();
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
  const form=new FormData();form.append('image',file);$('#identity-status').textContent='Uploading the reference photo…';
  const source=await api('/api/uploads',{method:'POST',body:form});
  if(identity!==current)return;
  identity.donor={id:source.id,url:source.url,width:source.width,height:source.height};showDonor();changed();
  }finally{current.loading=false;if(identity===current)updateIdentity();}
}

function showDonor() {
  $('#identity-donor').src=identity.donor.url;$('#identity-donor').hidden=false;$('#identity-empty').hidden=true;
}

async function editSource() {
  const current=identity;
  if(current.source)return current.source;
  let source=viewer.sources.get(current.version.id);
  if(!source){source=await send(`/api/projects/${current.projectId}/versions/${current.version.id}/edit-source`,{});viewer.sources.set(current.version.id,source);}
  return current.source=source;
}

async function findIdentityAreas() {
  const current=identity,parts=chosenParts(),mode=chosenMode(),revision=current.revision,keep_object=$('#identity-object').value.trim(),donor_id=current.donor.id;
  if(current.finding||current.submitting||current.loading)return null;
  current.finding=true;updateIdentity();$('#identity-error').hidden=true;
  $('#identity-status').textContent='Finding both people on your Mac…';
  try{
    const source=await editSource();
    const areas=await send(`/api/uploads/${source.id}/identity-areas`,{donor_id,parts,mode,keep_object});
    if(identity!==current||current.revision!==revision)return null;
    current.areas=areas;await drawAreas();
    if(identity!==current||current.revision!==revision)return null;
    $('#identity-status').textContent=`Pink shows where the photo can change. Everything outside stays unchanged. ${areas.summary.objects_protected?'Detected objects are protected.':'No held object was detected. If needed, name it above and preview again.'} Check that any object to keep is outside the pink area. ${mode==='whole'?'The head gets a separate detail pass.':''}`;
    return areas;
  }finally{if(identity===current){current.finding=false;updateIdentity();}}
}

async function drawAreas() {
  const areas=identity?.areas,canvas=$('#identity-overlay');if(!areas||!canvas?.width)return;
  const image=await loadMaskImage(areas.mask.url);if(!identity||identity.areas!==areas)return;
  const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(binaryMaskCanvas(image,canvas.width,canvas.height),0,0);
  const {width,height}=identity.donor;
  $('#identity-crops').replaceChildren(...areas.donor_crops.map(([x,y,w,h],index)=>{const box=document.createElement('span');box.className='identity-crop'+(index?' identity-close-up':'');
    Object.assign(box.style,{left:`${100*x/width}%`,top:`${100*y/height}%`,width:`${100*w/width}%`,height:`${100*h/height}%`});return box;}));
}

let identityEstimateTimer;
function scheduleIdentityEstimate() {
  clearTimeout(identityEstimateTimer);
  identityEstimateTimer=setTimeout(async()=>{
    const current=identity;if(!current?.areas)return $('#identity-estimate')&&($('#identity-estimate').textContent='');
    const areas=current.areas;
    const v=current.version;
    try{const result=await send('/api/estimate',{mode:'edit',project_id:current.projectId,version_id:v.id,aspect:v.width>v.height?'landscape':v.width<v.height?'portrait':'square',...viewer.settings,mask_bounds:areas.mask.bounds,identity_swap:true,identity_mode:areas.mode});
      if(identity===current&&current.areas===areas)$('#identity-estimate').textContent=estimateText(result,viewer.settings.variations);}
    catch(error){if(identity===current)$('#identity-estimate').textContent=error.message;}
  },250);
}

async function submitIdentitySwap() {
  const current=identity,parts=chosenParts(),mode=chosenMode();
  if(current.finding||current.submitting||current.loading)return;
  $('#identity-error').hidden=true;
  if(!current.areas){await findIdentityAreas();return;}
  const areas=current.areas;
  if(!areas||identity!==current)return;
  current.submitting=true;updateIdentity();
  const notes=$('#identity-notes').value.trim(),settings={...viewer.settings};
  try{
    const source=await editSource(),v=current.version;
    if(identity!==current)return;
    const prompt=`Identity swap · ${partsLabel(parts)}`+(notes?` · ${notes}`:'');
    await send('/api/generate',{mode:'edit',prompt,project_id:current.projectId,source_id:source.id,parent_image_id:source.parent_image_id,
      aspect:v.width>v.height?'landscape':v.width<v.height?'portrait':'square',...settings,snofs:false,
      mask_id:areas.mask.id,mask_core_id:areas.core.id,mask_feather:8,mask_blend_mode:'adaptive',
      identity_swap:{mode,parts,donor_id:current.donor.id,donor_crops:areas.donor_crops,keep_object:areas.keep_object,
        appearance_mask:areas.appearance_mask&&{mask_id:areas.appearance_mask.id},
        head_pass:areas.head_pass&&{mask_id:areas.head_pass.mask.id,core_mask_id:areas.head_pass.core.id},notes}});
    if(identity===current)$('#identity-dialog').close();toast(`Swapping ${partsLabel(parts)}. It will appear in this project.`);await refresh();updateViewerJobStatus();
  }finally{current.submitting=false;if(identity===current)updateIdentity();}
}

$('#identity-dialog').addEventListener('close',()=>{identity=null;});
