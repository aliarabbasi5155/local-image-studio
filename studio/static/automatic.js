'use strict';

// SAM 3 finds existing concepts; placement is deliberately chosen by the user.
function suggestedMaskTarget(prompt) {
  const text=prompt.toLowerCase();
  if(/\b(hands?|fingers?)\b/.test(text)&&suggestedMaskMode(prompt)==='move')return 'arm';
  for(const [pattern,target] of [[/\b(hands?|fingers?)\b/,'hand'],[/\b(shirts?|t-shirts?|tops?)\b/,'shirt'],[/\b(dress(es)?)\b/,'dress'],[/\b(jackets?|coats?)\b/,'jacket'],[/\b(pants|trousers)\b/,'pants'],[/\b(cloth(es|ing)?)\b/,'clothing'],[/\b(hair)\b/,'hair'],[/\b(shoes?)\b/,'shoe']])if(pattern.test(text))return target;
  return '';
}

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
    for(let y=0;y<h;y++)input[(y*w+x)*4+3]=prefix[Math.min(h,y+radius+1)]>prefix[Math.max(0,y-radius)]?255:0;
  }
  ctx.putImageData(pixels,0,0);return canvas;
}

async function initializeAutoSelection(existing) {
  const m=maskEditor;
  m.selectionMetadata=existing?.automatic;
  m.auto={pool:[],faces:[],result:null,index:0,mode:'change',placement:null};
  $('.mask-tools').insertAdjacentHTML('beforebegin',`<details class="mask-auto" open><summary>${icon('settings')} Automatic selection <span id="auto-summary"></span></summary><div class="auto-fields"><label>What are you doing?<select id="auto-mode"><option value="change">Change something existing</option><option value="move">Move a part or object</option><option value="add">Add something new</option></select></label><label id="auto-target-label">Object to select<input id="auto-target" placeholder="shirt, hand, jacket…" maxlength="80" autocomplete="off"></label><button id="auto-find" type="button">Find area</button><label class="auto-check"><input id="auto-protect" type="checkbox" checked>Protect detected faces</label><label class="auto-margin">Extra room <input id="auto-margin" type="range" min="0" max="48" value="12"><output id="auto-margin-value">12 px</output></label><div id="auto-matches" hidden><label>Choose a match<select id="auto-match"></select></label><button id="auto-all" type="button">Select all</button></div><p id="auto-status" role="status">Use a short object name, then check the pink preview. Brush and erase can refine it.</p></div></details>`);
  $('[data-mask-tool="pan"]').insertAdjacentHTML('beforebegin','<button data-mask-tool="place" type="button" aria-pressed="false" hidden>Draw destination</button>');
  $('.mask-stage').insertAdjacentHTML('beforeend','<canvas id="mask-protection" aria-hidden="true"></canvas>');
  const protectedCanvas=$('#mask-protection');protectedCanvas.width=m.canvas.width;protectedCanvas.height=m.canvas.height;
  $('#auto-target').value=existing?.automatic?.target || suggestedMaskTarget($('#viewer-edit-prompt')?.value || '');
  $('#auto-protect').checked=existing?.automatic?.protect_faces!==false;
  $('#auto-mode').onchange=()=>{
    m.auto.mode=$('#auto-mode').value;m.auto.placement=null;
    $('#auto-target-label').hidden=m.auto.mode==='add';$('.auto-margin').hidden=m.auto.mode==='add';
    $('#auto-find').textContent=m.auto.mode==='add'?'Find faces':'Find area';
    $('[data-mask-tool="place"]').hidden=m.auto.mode==='change';
    $('[data-mask-tool="place"]').textContent=m.auto.mode==='add'?'Draw placement':'Draw destination';
    $('#auto-status').textContent=m.auto.mode==='add'?'Draw a box for the new object, including its shadows. Find faces first if you want face protection.':m.auto.mode==='move'?'Find the existing part, then draw its destination. Hand moves need room for the wrist/arm; the exact pose may need retries.':'Find an existing object, then check the pink preview.';
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
  if(existing?.protectionBlob&&$('#auto-protect').checked)await restoreFaceProtection(existing.protectionBlob,m);
  $('#auto-mode').value=existing?.automatic?.mode || suggestedMaskMode($('#viewer-edit-prompt')?.value || '');
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
  const protect=$('#auto-protect').checked;
  if(!target&&!protect)throw new Error('Draw the placement box, or enable face protection first.');
  m.finding=true;updateMaskButtons();
  for(const id of ['auto-find','auto-mode','auto-target','auto-protect','auto-match','auto-all'])$('#'+id).disabled=true;
  $('#auto-status').classList.remove('auto-warning');$('#auto-status').textContent='Finding the area on your Mac…';$('#auto-summary').textContent='Finding…';
  try{
    let source=viewer.sources.get(m.version.id);
    if(!source){source=await send(`/api/projects/${viewer.project.id}/versions/${m.version.id}/edit-source`,{});viewer.sources.set(m.version.id,source);}
    const result=await send(`/api/uploads/${source.id}/auto-mask`,{target,protect_faces:protect});
    if(maskEditor!==m)return;
    m.auto.pool=await Promise.all(result.candidates.map(loadAutomaticMask));
    m.auto.faces=await Promise.all(result.faces.map(loadAutomaticMask));
    if(maskEditor!==m)return;
    m.auto.result={...result,target,protect_requested:protect};m.auto.index=0;
    $('#auto-match').innerHTML=m.auto.pool.map((_,i)=>`<option value="${i}">Match ${i+1} of ${m.auto.pool.length}</option>`).join('')+'<option value="all">All matches</option>';
    $('#auto-matches').hidden=m.auto.mode==='add'||m.auto.pool.length<2;
    refreshAutoSelection();
    const faces=protect?`${m.auto.faces.length} face${m.auto.faces.length===1?'':'s'} protected. `:'';
    $('#auto-status').textContent=(m.auto.mode==='add'?'Draw a placement box for the new object. ':m.auto.pool.length?`${m.auto.pool.length} match${m.auto.pool.length===1?'':'es'} found. Choose the correct pink area. `:'No match found. Try a simpler name or use the brush. ')+faces+(protect&&!m.auto.faces.length?'No faces detected; check the preview carefully. ':'')+(result.cached?'Reused selection.':`Found in ${Math.round(result.seconds)} seconds.`);
    $('#auto-summary').textContent=m.auto.mode==='add'?'Draw placement':`${m.auto.pool.length} found`;
  }finally{if(maskEditor===m){m.finding=false;for(const id of ['auto-find','auto-mode','auto-target','auto-protect','auto-match','auto-all'])$('#'+id).disabled=false;updateMaskButtons();}}
}

function updateAutomaticProtection() {
  const m=maskEditor,a=m.auto,w=m.canvas.width,h=m.canvas.height;
  m.protection=null;if(!$('#auto-protect').checked)return;
  if(!a.result){m.protection=m.restoredProtection || null;return;}
  if(a.faces.length){const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});for(const image of a.faces)ctx.drawImage(binaryMaskCanvas(image,w,h),0,0);
    m.protection=growSelection(canvas,Math.max(2,6*w/m.version.width));
  }
}

function refreshAutoSelection(coalesce=false) {
  const m=maskEditor,a=m.auto,w=m.canvas.width,h=m.canvas.height;
  const selected=document.createElement('canvas');selected.width=w;selected.height=h;
  const ctx=selected.getContext('2d',{willReadFrequently:true});
  if(a.mode!=='add')for(const [i,image] of a.pool.entries())if(a.index===-1||a.index===i)ctx.drawImage(binaryMaskCanvas(image,w,h),0,0);
  growSelection(selected,Number($('#auto-margin').value)*w/m.version.width);
  if(a.placement)automaticPlacement(a.placement,ctx,selected);
  updateAutomaticProtection();
  if(coalesce&&m.strokes.at(-1)?.tool==='automatic')m.strokes.pop();
  m.strokes.push({tool:'automatic',image:selected,metadata:{model:a.result?.model,model_revision:a.result?.model_revision,target:a.mode==='add'?'':a.result?.target || '',mode:a.mode,extra_room:Number($('#auto-margin').value),protect_faces:$('#auto-protect').checked}});
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

async function protectedSelectionBlob(m) {
  if(!m.protection)return null;
  const canvas=document.createElement('canvas');canvas.width=m.canvas.width;canvas.height=m.canvas.height;
  const ctx=canvas.getContext('2d');ctx.drawImage(m.protection,0,0);ctx.globalCompositeOperation='source-in';ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.globalCompositeOperation='destination-over';ctx.fillStyle='black';ctx.fillRect(0,0,canvas.width,canvas.height);
  return canvasBlob(canvas);
}
