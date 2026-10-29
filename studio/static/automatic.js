'use strict';

// SAM 3 finds existing concepts; placement is deliberately chosen by the user.
function suggestedMaskTarget(prompt) {
  const text=prompt.toLowerCase();
  if(/\b(hands?|fingers?)\b/.test(text)&&suggestedMaskMode(prompt)==='move')return 'arm';
  for(const [pattern,target] of [[/\b(hands?|fingers?)\b/,'hand'],[/\b(shirts?|t-shirts?|tops?)\b/,'shirt'],[/\b(dress(es)?)\b/,'dress'],[/\b(jackets?|coats?)\b/,'jacket'],[/\b(pants|trousers)\b/,'pants'],[/\b(cloth(es|ing)?)\b/,'clothing'],[/\b(hair)\b/,'hair'],[/\b(shoes?)\b/,'shoe']])if(pattern.test(text))return target;
  return '';
}

// Clothing edits should keep the body: protect visible skin and guide the original outline.
const clothingTarget=target=>/\b(shirts?|t-shirts?|tops?|dress(es)?|jackets?|coats?|pants|trousers|jeans|skirts?|cloth(es|ing)?|outfits?|sweaters?|hoodies?|blouses?|suits?|uniforms?|garments?)\b/i.test(target);

function suggestedMaskMode(prompt) {
  const text=prompt.trim().toLowerCase();
  if(/^(add|put|place|insert)\b/.test(text))return 'add';
  if(/\b(move|reposition)\b/.test(text)||/\b(change|adjust)\b.*\b(position|pose)\b/.test(text))return 'move';
  return 'change';
}

async function loadAutomaticMask(row) {
  const bytes=Uint8Array.from(atob(row.png),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));
  try{return await loadMaskImage(url);}finally{URL.revokeObjectURL(url);}
}

function binaryMaskCanvas(image,width,height) {
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,width,height);
  const pixels=ctx.getImageData(0,0,width,height);
  for(let i=0;i<pixels.data.length;i+=4){const on=pixels.data[i]>=128;pixels.data[i]=245;pixels.data[i+1]=130;pixels.data[i+2]=178;pixels.data[i+3]=on?255:0;}
  ctx.putImageData(pixels,0,0);return canvas;
}

function growSelection(canvas,radius) {
  const w=canvas.width,h=canvas.height,ctx=canvas.getContext('2d',{willReadFrequently:true});
  const pixels=ctx.getImageData(0,0,w,h),input=pixels.data,middle=new Uint8Array(w*h),prefix=new Uint32Array(Math.max(w,h)+1);
  radius=Math.max(0,Math.round(radius));
  for(let y=0;y<h;y++){
    prefix[0]=0;for(let x=0;x<w;x++)prefix[x+1]=prefix[x]+(input[(y*w+x)*4+3]>=128?1:0);
    for(let x=0;x<w;x++)middle[y*w+x]=prefix[Math.min(w,x+radius+1)]>prefix[Math.max(0,x-radius)]?1:0;
  }
  for(let x=0;x<w;x++){
    prefix[0]=0;for(let y=0;y<h;y++)prefix[y+1]=prefix[y]+middle[y*w+x];
    // Grown pixels were transparent black; give them the selection color too.
    for(let y=0;y<h;y++){const i=(y*w+x)*4,on=prefix[Math.min(h,y+radius+1)]>prefix[Math.max(0,y-radius)];if(on&&input[i+3]<128){input[i]=245;input[i+1]=130;input[i+2]=178;}input[i+3]=on?255:0;}
  }
  ctx.putImageData(pixels,0,0);return canvas;
}

async function initializeAutoSelection(existing) {
  const m=maskEditor;
  m.selectionMetadata=existing?.automatic;
  m.auto={pool:[],faces:[],skin:[],core:null,result:null,index:0,mode:'change',placement:null,bodyTouched:!!existing?.automatic};
  $('.mask-tools').insertAdjacentHTML('beforebegin',`<div class="mask-auto" hidden><div class="mask-auto-row"><select id="auto-mode" aria-label="Edit type"><option value="change">Change</option><option value="move">Move</option><option value="add">Add</option></select><label id="auto-target-label" class="mask-auto-target"><span class="sr-only">Object to select</span><input id="auto-target" placeholder="What to select, like shirt or hand" maxlength="80" autocomplete="off"></label><button id="auto-find" type="button" class="lb-text strong">Find area</button></div><div class="auto-checks"><label class="lb-check"><input id="auto-protect" type="checkbox" checked>Protect faces</label><label class="lb-check auto-body" title="Arms, legs and neck stay exactly as they are. Turn off when new clothing should cover more skin."><input id="auto-skin" type="checkbox">Keep skin</label><label class="lb-check auto-body" title="Keeps the original body outline and proportions. Slightly slower."><input id="auto-shape" type="checkbox">Keep shape</label><label class="auto-margin">Room <input id="auto-margin" type="range" min="0" max="48" value="12"><output id="auto-margin-value">12 px</output></label></div><div id="auto-matches" hidden><select id="auto-match" aria-label="Choose a match"></select><button id="auto-all" type="button" class="lb-text">Select all</button></div><p id="auto-status" role="status"></p><span id="auto-summary" hidden></span></div>`);
  $('[data-mask-tool="pan"]').insertAdjacentHTML('beforebegin','<button data-mask-tool="place" type="button" aria-pressed="false" hidden>Draw destination</button>');
  $('.mask-stage').insertAdjacentHTML('beforeend','<canvas id="mask-protection" aria-hidden="true"></canvas>');
  const protectedCanvas=$('#mask-protection');protectedCanvas.width=m.canvas.width;protectedCanvas.height=m.canvas.height;
  $('#auto-target').value=existing?.automatic?.target || suggestedMaskTarget(viewer.drafts[draftKey()] || '');
  $('#auto-protect').checked=existing?.automatic?.protect_faces!==false;
  $('#auto-skin').checked=existing?.automatic?existing.automatic.protect_skin===true:clothingTarget($('#auto-target').value);
  $('#auto-shape').checked=existing?existing.preserveShape===true:clothingTarget($('#auto-target').value);
  $('#auto-mode').onchange=()=>{
    m.auto.mode=$('#auto-mode').value;m.auto.placement=null;
    $('#auto-target-label').hidden=m.auto.mode==='add';$('.auto-margin').hidden=m.auto.mode==='add';
    $('#auto-find').textContent=m.auto.mode==='add'?'Find faces':'Find area';
    $('[data-mask-tool="place"]').hidden=m.auto.mode==='change';
    for(const label of document.querySelectorAll('.auto-body'))label.hidden=m.auto.mode!=='change';
    $('[data-mask-tool="place"]').textContent=m.auto.mode==='add'?'Draw placement':'Draw destination';
    $('#auto-status').textContent=m.auto.mode==='add'?'Draw a box where the new object goes.':m.auto.mode==='move'?'Find the part, then draw where it goes.':'';
    if(m.auto.result)refreshAutoSelection();
    if(m.auto.mode==='add'){
      m.tool='place';for(const button of document.querySelectorAll('[data-mask-tool]'))button.setAttribute('aria-pressed',String(button.dataset.maskTool==='place'));
    }else if(m.tool==='place'&&m.auto.mode==='change'){
      m.tool='paint';for(const button of document.querySelectorAll('[data-mask-tool]'))button.setAttribute('aria-pressed',String(button.dataset.maskTool==='paint'));
    }
  };
  $('#auto-find').onclick=()=>findAutomaticArea().catch(error=>{if(maskEditor===m){$('#auto-summary').textContent='Try again';$('#auto-status').textContent=error.message;$('#auto-status').classList.add('auto-warning');}});
  $('#auto-match').onchange=()=>{m.auto.index=Number($('#auto-match').value);refreshAutoSelection();};
  $('#auto-all').onclick=()=>{m.auto.index=-1;$('#auto-match').value='all';refreshAutoSelection();};
  $('#auto-margin').oninput=()=>{$('#auto-margin-value').textContent=`${$('#auto-margin').value} px`;if(m.auto.result)refreshAutoSelection(true);};
  $('#auto-protect').onchange=()=>{updateAutomaticProtection();renderFaceProtection();redrawMask();};
  $('#auto-skin').onchange=()=>{m.auto.bodyTouched=true;updateAutomaticProtection();renderFaceProtection();redrawMask();};
  $('#auto-shape').onchange=()=>{m.auto.bodyTouched=true;};
  $('#auto-target').oninput=()=>{if(!m.auto.bodyTouched)$('#auto-skin').checked=$('#auto-shape').checked=clothingTarget($('#auto-target').value);updateMaskButtons();};
  if(existing?.protectionBlob&&($('#auto-protect').checked||$('#auto-skin').checked))await restoreFaceProtection(existing.protectionBlob,m);
  $('#auto-mode').value=existing?.automatic?.mode || suggestedMaskMode(viewer.drafts[draftKey()] || '');
  $('#auto-mode').dispatchEvent(new Event('change'));
  if(existing?.automatic?.placement){const b=existing.automatic.placement;m.auto.placement={tool:'place',mode:m.auto.mode,points:[{x:b[0]*m.canvas.width/m.version.width,y:b[1]*m.canvas.height/m.version.height},{x:b[2]*m.canvas.width/m.version.width,y:b[3]*m.canvas.height/m.version.height}]};}
}

async function restoreFaceProtection(blob,m) {
  const url=URL.createObjectURL(blob);
  try{const image=await loadMaskImage(url);if(maskEditor===m){m.protection=binaryMaskCanvas(image,m.canvas.width,m.canvas.height);m.restoredProtection=m.protection;renderFaceProtection();}}
  finally{URL.revokeObjectURL(url);}
}

function renderFaceProtection() {
  const m=maskEditor,canvas=$('#mask-protection');if(!m||!canvas)return;
  const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
  if(m.protection){ctx.drawImage(m.protection,0,0);ctx.globalCompositeOperation='source-in';ctx.fillStyle='#59d8e7';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.globalCompositeOperation='source-over';}
}

async function findAutomaticArea() {
  const m=maskEditor,target=m.auto.mode==='add'?'':$('#auto-target').value.trim();
  if(m.auto.mode!=='add'&&!target)throw new Error('Enter the existing object’s name, for example shirt or hand.');
  const protect=$('#auto-protect').checked,skin=m.auto.mode==='change'&&$('#auto-skin').checked;
  if(!target&&!protect)throw new Error('Draw the placement box, or enable face protection first.');
  m.finding=true;updateMaskButtons();
  for(const id of ['auto-find','auto-mode','auto-target','auto-protect','auto-skin','auto-shape','auto-match','auto-all'])$('#'+id).disabled=true;
  $('#auto-status').classList.remove('auto-warning');$('#auto-status').textContent='Finding…';$('#auto-summary').textContent='Finding…';
  try{
    let source=viewer.sources.get(m.version.id);
    if(!source){source=await send(`/api/projects/${viewer.project.id}/versions/${m.version.id}/edit-source`,{});viewer.sources.set(m.version.id,source);}
    const result=await send(`/api/uploads/${source.id}/auto-mask`,{target,protect_faces:protect,protect_skin:skin});
    if(maskEditor!==m)return;
    m.auto.pool=await Promise.all(result.candidates.map(loadAutomaticMask));
    m.auto.faces=await Promise.all(result.faces.map(loadAutomaticMask));
    m.auto.skin=await Promise.all((result.skin || []).map(loadAutomaticMask));
    if(maskEditor!==m)return;
    m.auto.result={...result,target,protect_requested:protect,skin_requested:skin};m.auto.index=0;
    $('#auto-match').innerHTML=m.auto.pool.map((_,i)=>`<option value="${i}">Match ${i+1} of ${m.auto.pool.length}</option>`).join('')+'<option value="all">All matches</option>';
    $('#auto-matches').hidden=m.auto.mode==='add'||m.auto.pool.length<2;
    refreshAutoSelection();
    const faces=(protect?(m.auto.faces.length?` · ${m.auto.faces.length} face${m.auto.faces.length===1?'':'s'} protected`:' · no faces found'):'')+(skin&&m.auto.skin.length?' · skin kept':'');
    $('#auto-status').textContent=(m.auto.mode==='add'?'Now draw a box where it goes':m.auto.pool.length?`${m.auto.pool.length} found${m.auto.pool.length>1?' · choose one':''}`:'Nothing found · try a simpler word or paint it')+faces;
    $('#auto-summary').textContent=m.auto.mode==='add'?'Draw placement':`${m.auto.pool.length} found`;
  }finally{if(maskEditor===m){m.finding=false;for(const id of ['auto-find','auto-mode','auto-target','auto-protect','auto-skin','auto-shape','auto-match','auto-all'])$('#'+id).disabled=false;updateMaskButtons();}}
}

function maskLayer(images,w,h) {
  const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
  const ctx=canvas.getContext('2d',{willReadFrequently:true});for(const image of images)ctx.drawImage(image instanceof HTMLCanvasElement?image:binaryMaskCanvas(image,w,h),0,0);
  return canvas;
}
function combineMasks(target,source,operation) {
  const ctx=target.getContext('2d');ctx.globalCompositeOperation=operation;ctx.drawImage(source,0,0);ctx.globalCompositeOperation='source-over';return target;
}
// Two independent detections never meet exactly. The few pixels between the object and a protected
// face or skin belong to the object, so none of its old color survives beside them.
function automaticRegions() {
  // SAM sees at most a 1024px preview, so its outlines are only that precise on larger photos.
  const m=maskEditor,a=m.auto,w=m.canvas.width,h=m.canvas.height,detected=Math.max(1,Math.max(m.version.width,m.version.height)/1024);
  const margin=Math.max(2,Math.max(6,3*detected)*w/m.version.width);
  const faces=$('#auto-protect').checked,skin=a.mode==='change'&&$('#auto-skin').checked;
  const masks=[...(faces?a.faces:[]),...(skin?a.skin:[])];
  if(!masks.length)return {protection:null,core:a.core};
  const protection=growSelection(maskLayer(masks,w,h),margin);
  if(!a.core)return {protection,core:null};
  const core=maskLayer([a.core],w,h);
  combineMasks(core,combineMasks(growSelection(maskLayer([a.core],w,h),margin),protection,'destination-in'),'source-over');
  combineMasks(protection,core,'destination-out');
  // Faces keep their own pixels, apart from a tiny allowance where an object outline touches them.
  if(faces&&a.faces.length)combineMasks(protection,combineMasks(maskLayer(a.faces,w,h),growSelection(maskLayer([a.core],w,h),Math.max(1,Math.max(2,detected)*w/m.version.width)),'destination-out'),'source-over');
  return {protection,core};
}

function updateAutomaticProtection() {
  const m=maskEditor,a=m.auto;
  const faces=$('#auto-protect').checked,skin=a.mode==='change'&&$('#auto-skin').checked;
  m.protection=null;a.opaque=a.core;if(!faces&&!skin)return;
  if(!a.result){m.protection=m.restoredProtection || null;return;}
  const regions=automaticRegions();m.protection=regions.protection;a.opaque=regions.core;
}

function refreshAutoSelection(coalesce=false) {
  const m=maskEditor,a=m.auto,w=m.canvas.width,h=m.canvas.height;
  const selected=document.createElement('canvas');selected.width=w;selected.height=h;
  const ctx=selected.getContext('2d',{willReadFrequently:true});
  if(a.mode!=='add')for(const [i,image] of a.pool.entries())if(a.index===-1||a.index===i)ctx.drawImage(binaryMaskCanvas(image,w,h),0,0);
  // The detected object is replaced completely; only the extra room around it blends.
  a.core=null;
  if(a.mode!=='add'&&a.pool.length){a.core=document.createElement('canvas');a.core.width=w;a.core.height=h;a.core.getContext('2d').drawImage(selected,0,0);}
  growSelection(selected,Number($('#auto-margin').value)*w/m.version.width);
  if(a.placement)automaticPlacement(a.placement,ctx,selected);
  updateAutomaticProtection();
  if(coalesce&&m.strokes.at(-1)?.tool==='automatic')m.strokes.pop();
  m.strokes.push({tool:'automatic',image:selected,core:a.core,metadata:{model:a.result?.model,model_revision:a.result?.model_revision,target:a.mode==='add'?'':a.result?.target || '',mode:a.mode,extra_room:Number($('#auto-margin').value),protect_faces:$('#auto-protect').checked,protect_skin:a.mode==='change'&&$('#auto-skin').checked}});
  renderFaceProtection();redrawMask();
}

function automaticPlacement(stroke,ctx=maskEditor.ctx,selection=null) {
  const m=maskEditor,first=stroke.points[0],last=stroke.points.at(-1);
  const x=Math.min(first.x,last.x),y=Math.min(first.y,last.y),w=Math.abs(first.x-last.x),h=Math.abs(first.y-last.y);
  if(w<2||h<2)return;
  ctx.save();ctx.fillStyle=ctx.strokeStyle='#f582b2';ctx.fillRect(x,y,w,h);
  if(stroke.mode==='move'){
    const base=selection||m.strokes.filter(s=>s.tool==='automatic').at(-1)?.image;
    if(base){const data=base.getContext('2d').getImageData(0,0,base.width,base.height).data;let l=base.width,t=base.height,r=0,b=0;
      for(let py=0;py<base.height;py++)for(let px=0;px<base.width;px++)if(data[(py*base.width+px)*4+3]>=128){l=Math.min(l,px);t=Math.min(t,py);r=Math.max(r,px);b=Math.max(b,py);}
      if(r>l){ctx.lineWidth=Math.max(8,Math.min(r-l,b-t,w,h));ctx.lineCap='round';ctx.beginPath();ctx.moveTo((l+r)/2,(t+b)/2);ctx.lineTo(x+w/2,y+h/2);ctx.stroke();}
    }
  }
  ctx.restore();
}

function binaryMaskBlob(source,width,height) {
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext('2d');ctx.drawImage(source,0,0);ctx.globalCompositeOperation='source-in';ctx.fillStyle='white';ctx.fillRect(0,0,width,height);ctx.globalCompositeOperation='destination-over';ctx.fillStyle='black';ctx.fillRect(0,0,width,height);
  return canvasBlob(canvas);
}

async function protectedSelectionBlob(m) {
  return m.protection?binaryMaskBlob(m.protection,m.canvas.width,m.canvas.height):null;
}

// The newest automatic selection supplies the object core unless the mask was cleared after it.
async function objectCoreBlob(m,previous) {
  const last=m.strokes.filter(s=>s.tool==='automatic'||s.tool==='clear').at(-1);
  if(!last)return previous?.coreBlob || null;
  if(!last.core)return null;
  return binaryMaskBlob(last.core===m.auto.core&&m.auto.opaque?m.auto.opaque:last.core,m.canvas.width,m.canvas.height);
}
