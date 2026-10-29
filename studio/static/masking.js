'use strict';
// Keep unsent masks per image in this tab. Submitted masks are saved on the server.
const maskSelections = new Map();
const currentMask = () => viewer.project && maskSelections.get(draftKey());
let maskEditor = null, maskOpening = false;
const loadMaskImage = url => new Promise((resolve,reject) => {
  const image = new Image(); image.onload=()=>resolve(image); image.onerror=()=>reject(new Error('Could not load the photo or selection.')); image.src=url;
});
const canvasBlob = canvas => new Promise((resolve,reject) => canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('Could not save the selection.')),'image/png'));

function updateMaskControls() {
  // The edit panel shows a selected area as a chip that switches between the area and the whole photo.
  if (viewer.tool === 'edit' && $('#lb-edit')) renderEditPanel();
}

async function prepareMask(source,key) {
  const selection=maskSelections.get(key);
  if (!selection?.enabled) return {};
  let saved=selection.uploaded.get(source.id);
  if (!saved) {
    const form=new FormData();form.append('mask',selection.blob,'selection.png');
    saved=await api(`/api/uploads/${source.id}/mask`,{method:'POST',body:form});
    selection.uploaded.set(source.id,saved);selection.bounds=saved.bounds;
  }
  let protection=selection.protectedUploaded?.get(source.id);
  if(selection.protectionBlob&&!protection){
    const form=new FormData();form.append('mask',selection.protectionBlob,'protected-faces.png');
    protection=await api(`/api/uploads/${source.id}/mask`,{method:'POST',body:form});
    selection.protectedUploaded.set(source.id,protection);
  }
  let core=selection.coreUploaded?.get(source.id);
  if(selection.coreBlob&&!core){
    const form=new FormData();form.append('mask',selection.coreBlob,'object.png');
    core=await api(`/api/uploads/${source.id}/mask`,{method:'POST',body:form});
    (selection.coreUploaded ||= new Map()).set(source.id,core);
  }
  const shape=!!selection.preserveShape&&(selection.automatic?.mode || 'change')==='change';
  return {mask_id:saved.id,mask_protection_id:protection?.id,mask_core_id:core?.id,mask_preserve_shape:shape,mask_selection:selection.automatic,mask_feather:selection.feather,mask_detail:selection.detail || 'balanced',mask_context:selection.context || 'balanced',mask_blend_mode:selection.blendMode || 'adaptive',mask_color_match:selection.colorMatch!==false,mask_align:selection.align!==false};
}

async function reuseMask(recipe) {
  const version=selectedVersion();
  if (!version || version.width!==recipe.width || version.height!==recipe.height) {
    maskSelections.delete(draftKey());return toast('The earlier mask needs the original image dimensions. Paint a new selection.');
  }
  const saved=await api(`/api/masks/${recipe.masked_edit.mask_id}`);
  const response=await fetch(saved.url);
  if (!response.ok) throw new Error('The saved selection is unavailable. Paint it again.');
  let protectionBlob=null,protectedSaved=null;
  if(recipe.masked_edit.protection_mask_id){protectedSaved=await api(`/api/masks/${recipe.masked_edit.protection_mask_id}`);const r=await fetch(protectedSaved.url);if(r.ok)protectionBlob=await r.blob();}
  let coreBlob=null,coreSaved=null;
  if(recipe.masked_edit.core_mask_id){coreSaved=await api(`/api/masks/${recipe.masked_edit.core_mask_id}`);const r=await fetch(coreSaved.url);if(r.ok)coreBlob=await r.blob();else coreSaved=null;}
  maskSelections.set(draftKey(),{blob:await response.blob(),protectionBlob,coreBlob,preserveShape:!!recipe.masked_edit.shape_guidance,automatic:recipe.masked_edit.selection,protectedUploaded:new Map(protectedSaved?[[protectedSaved.source_id,protectedSaved]]:[]),coreUploaded:new Map(coreSaved?[[coreSaved.source_id,coreSaved]]:[]),bounds:saved.bounds,width:version.width,height:version.height,
    feather:recipe.masked_edit.feather,detail:recipe.mask_detail || 'balanced',context:recipe.mask_context || 'balanced',
    blendMode:recipe.masked_edit.blend_mode || 'manual',colorMatch:recipe.masked_edit.color_match!==false,align:recipe.masked_edit.align!==false,enabled:true,uploaded:new Map([[saved.source_id,saved]])});
}

async function openMaskEditor() {
  const version=selectedImage();if(!version || maskOpening)return;
  maskOpening=true;
  try {
    const key=draftKey(),existing=maskSelections.get(key),image=await loadMaskImage(version.url);
    if(key!==draftKey())return;
    const area=studioSettings.area,feather=existing?.feather ?? 8;
    $('#mask-content').innerHTML=`<section class="mask-editor" aria-label="Select an area">
      <header class="mask-top"><button type="button" class="lb-text" id="mask-cancel">Cancel</button><div class="lb-title"><strong id="mask-title">Select an area</strong><small><span class="mask-legend-pink">Pink changes</span> · <span class="mask-legend-blue">blue stays</span></small></div><button class="lb-text strong" id="mask-apply" type="button">Done</button></header>
      <div class="mask-surface"><div class="mask-frame" tabindex="0" role="group" aria-label="Paint the area to change. Use two fingers to zoom and move."><div class="mask-stage"><img id="mask-photo" alt="Photo being selected" draggable="false"><canvas id="mask-canvas" aria-hidden="true"></canvas></div></div></div>
      <footer class="mask-panel"><div class="mask-row"><div class="lb-segment mask-mode" role="radiogroup" aria-label="Selection method"><button type="button" role="radio" data-mask-mode="brush" aria-checked="true">Brush</button><button type="button" role="radio" data-mask-mode="auto" aria-checked="false">${icon('spark')} Auto</button></div><span class="mask-actions"><button id="mask-undo" type="button" class="lb-text">Undo</button><button id="mask-clear" type="button" class="lb-text">Clear</button></span></div>
        <div class="mask-tools"><div class="lb-segment mask-tool-choice" role="radiogroup" aria-label="Brush tool"><button data-mask-tool="paint" type="button" aria-pressed="true">Paint</button><button data-mask-tool="erase" type="button" aria-pressed="false">Erase</button><button data-mask-tool="pan" type="button" aria-pressed="false">Move</button></div><input id="mask-brush" type="range" min="4" max="100" value="32" aria-label="Brush size"><output id="mask-brush-value" hidden>32</output></div>
        <label id="mask-manual-feather" class="mask-feather" ${area.blend==='manual'?'':'hidden'}>Soft edge <input id="mask-feather" type="range" min="0" max="32" value="${feather}"><output id="mask-feather-value">${feather} px</output></label>
        <p id="mask-error" class="lb-error" role="alert" hidden></p></footer>
    </section>`;
    $('#mask-photo').src=version.url;
    const canvas=$('#mask-canvas'),scale=Math.min(1,2048/Math.max(image.naturalWidth,image.naturalHeight));
    canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const base=document.createElement('canvas');base.width=canvas.width;base.height=canvas.height;
    maskEditor={key,version,previous:existing,canvas,base,ctx:canvas.getContext('2d',{willReadFrequently:true}),strokes:[],current:null,tool:'paint',zoom:1,x:0,y:0,pointers:new Map(),gesture:null,observer:null};
    if(existing) {
      const url=URL.createObjectURL(existing.blob);
      try {
        const mask=await loadMaskImage(url),ctx=base.getContext('2d',{willReadFrequently:true});ctx.drawImage(mask,0,0,base.width,base.height);
        const pixels=ctx.getImageData(0,0,base.width,base.height);
        for(let i=0;i<pixels.data.length;i+=4){const alpha=pixels.data[i];pixels.data[i]=245;pixels.data[i+1]=130;pixels.data[i+2]=178;pixels.data[i+3]=alpha;}
        ctx.putImageData(pixels,0,0);
      }finally{URL.revokeObjectURL(url);}
    }
    if(key!==draftKey()){maskEditor=null;return;}
    await initializeAutoSelection(existing);
    setMaskMode(existing?.automatic?'auto':'brush');
    bindMaskEditor();redrawMask();
    // The same screen changes into selection mode; the photo stays where it was.
    $('#mask-content').hidden=false;$('.lightbox').classList.add('selecting');
    fitMaskPhoto();
    maskEditor.observer=new ResizeObserver(fitMaskPhoto);maskEditor.observer.observe($('.mask-surface'));
  } finally {maskOpening=false;}
}

function setMaskMode(mode) {
  for(const button of document.querySelectorAll('[data-mask-mode]'))button.setAttribute('aria-checked',String(button.dataset.maskMode===mode));
  $('.mask-auto').hidden=mode!=='auto';
}

function closeMaskEditor() {
  const layer=$('#mask-content');
  if(!layer || layer.hidden)return;
  maskEditor?.observer?.disconnect();maskEditor=null;
  layer.hidden=true;layer.innerHTML='';$('.lightbox')?.classList.remove('selecting');
}

function fitMaskPhoto() {
  if(!maskEditor)return;
  const bounds=$('.mask-surface').getBoundingClientRect(),ratio=maskEditor.version.width/maskEditor.version.height;
  const width=Math.max(1,Math.min(bounds.width-12,(bounds.height-12)*ratio));
  const frame=$('.mask-frame'),stage=$('.mask-stage');
  frame.style.width=`${bounds.width}px`;frame.style.height=`${bounds.height}px`;
  stage.style.width=`${width}px`;stage.style.height=`${width/ratio}px`;
  stage.style.left=`${(bounds.width-width)/2}px`;stage.style.top=`${(bounds.height-width/ratio)/2}px`;
  renderMaskZoom();
}
function renderMaskZoom() {
  const m=maskEditor;if(!m)return;const frame=$('.mask-frame'),stage=$('.mask-stage');
  const limitX=Math.max(0,(stage.clientWidth*m.zoom-frame.clientWidth)/2),limitY=Math.max(0,(stage.clientHeight*m.zoom-frame.clientHeight)/2);
  m.x=Math.max(-limitX,Math.min(limitX,m.x));m.y=Math.max(-limitY,Math.min(limitY,m.y));
  stage.style.transform=`translate(${m.x}px,${m.y}px) scale(${m.zoom})`;
}
function drawMaskStroke(stroke) {
  const m=maskEditor,ctx=m.ctx;
  if(stroke.tool==='automatic'){ctx.clearRect(0,0,m.canvas.width,m.canvas.height);ctx.drawImage(stroke.image,0,0);return;}
  if(stroke.tool==='place'){automaticPlacement(stroke);return;}
  if(stroke.tool==='clear'){ctx.clearRect(0,0,m.canvas.width,m.canvas.height);return;}
  if(!stroke.points.length)return;
  ctx.save();ctx.globalCompositeOperation=stroke.tool==='erase'?'destination-out':'source-over';ctx.strokeStyle=ctx.fillStyle='#f582b2';ctx.lineCap=ctx.lineJoin='round';ctx.lineWidth=stroke.width;
  if(stroke.points.length===1){ctx.beginPath();ctx.arc(stroke.points[0].x,stroke.points[0].y,stroke.width/2,0,Math.PI*2);ctx.fill();}
  else{ctx.beginPath();ctx.moveTo(stroke.points[0].x,stroke.points[0].y);for(const p of stroke.points.slice(1))ctx.lineTo(p.x,p.y);ctx.stroke();}
  ctx.restore();
}
function redrawMask(updateButtons=true) {
  const m=maskEditor;if(!m)return;m.ctx.clearRect(0,0,m.canvas.width,m.canvas.height);m.ctx.drawImage(m.base,0,0);
  for(const stroke of m.strokes)drawMaskStroke(stroke);
  if(m.protection){m.ctx.save();m.ctx.globalCompositeOperation='destination-out';m.ctx.drawImage(m.protection,0,0);m.ctx.restore();}
  if(updateButtons)updateMaskButtons();
}
function maskBounds() {
  const m=maskEditor,pixels=m.ctx.getImageData(0,0,m.canvas.width,m.canvas.height).data;
  let left=m.canvas.width,top=m.canvas.height,right=0,bottom=0;
  for(let y=0;y<m.canvas.height;y++)for(let x=0;x<m.canvas.width;x++)if(pixels[(y*m.canvas.width+x)*4+3]>=128){left=Math.min(left,x);top=Math.min(top,y);right=Math.max(right,x+1);bottom=Math.max(bottom,y+1);}
  if(right===0)return null;
  return [Math.floor(left*m.version.width/m.canvas.width),Math.floor(top*m.version.height/m.canvas.height),Math.min(m.version.width,Math.ceil(right*m.version.width/m.canvas.width)),Math.min(m.version.height,Math.ceil(bottom*m.version.height/m.canvas.height))];
}
function updateMaskButtons() {
  if(!maskEditor)return;$('#mask-undo').disabled=!maskEditor.strokes.length;
  const m=maskEditor,needsFaces=$('#auto-protect')?.checked&&((m.strokes.some(s=>s.tool==='place')&&!m.auto?.result&&!m.protection)||(m.auto?.result&&!m.auto.result.protect_requested));
  const needsSkin=m.auto?.mode==='change'&&$('#auto-skin')?.checked&&m.auto?.result&&!m.auto.result.skin_requested;
  $('#mask-apply').disabled=!maskBounds()||needsFaces||needsSkin||m.finding;
  if(needsFaces)$('#auto-status').textContent='Click Find faces / Find area to protect faces before applying, or turn off face protection.';
  else if(needsSkin)$('#auto-status').textContent='Click Find area again to keep visible skin, or turn that option off.';
}
function maskPoint(event) {
  const bounds=$('.mask-stage').getBoundingClientRect(),m=maskEditor;
  return {x:Math.max(0,Math.min(m.canvas.width,(event.clientX-bounds.left)*m.canvas.width/bounds.width)),y:Math.max(0,Math.min(m.canvas.height,(event.clientY-bounds.top)*m.canvas.height/bounds.height))};
}
function bindMaskEditor() {
  const m=maskEditor,frame=$('.mask-frame');
  for(const button of document.querySelectorAll('[data-mask-tool]'))button.onclick=()=>{m.tool=button.dataset.maskTool;for(const other of document.querySelectorAll('[data-mask-tool]'))other.setAttribute('aria-pressed',String(other===button));frame.classList.toggle('moving',m.tool==='pan');};
  $('#mask-brush').oninput=event=>$('#mask-brush-value').value=event.target.value;
  $('#mask-feather').oninput=event=>$('#mask-feather-value').value=`${event.target.value} px`;
  $('#mask-undo').onclick=()=>{if(m.strokes.pop()?.tool==='place')m.auto.placement=m.strokes.filter(s=>s.tool==='place').at(-1)||null;redrawMask();};$('#mask-clear').onclick=()=>{m.auto.placement=null;m.strokes.push({tool:'clear'});redrawMask();};
  $('#mask-cancel').onclick=closeMaskEditor;
  $('.mask-mode').onclick=event=>{const mode=event.target.closest('[data-mask-mode]');if(mode)setMaskMode(mode.dataset.maskMode);};
  frame.onpointerdown=event=>{
    if(event.button!==0)return;event.preventDefault();frame.setPointerCapture(event.pointerId);
    m.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(m.pointers.size===2){
      if(m.current){m.strokes.pop();m.current=null;redrawMask();}
      const [a,b]=[...m.pointers.values()],bounds=frame.getBoundingClientRect();
      m.gesture={distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),zoom:m.zoom,x:m.x,y:m.y,cx:(a.x+b.x)/2-bounds.left-bounds.width/2,cy:(a.y+b.y)/2-bounds.top-bounds.height/2};
    }else if(m.pointers.size===1){
      m.gesture={x:m.x,y:m.y,startX:event.clientX,startY:event.clientY};
      const photo=$('.mask-stage').getBoundingClientRect(),inside=event.clientX>=photo.left&&event.clientX<=photo.right&&event.clientY>=photo.top&&event.clientY<=photo.bottom;
      if(m.tool!=='pan'&&inside){m.current={tool:m.tool,mode:m.auto.mode,width:Number($('#mask-brush').value)*m.canvas.width/photo.width,points:[maskPoint(event)]};m.strokes.push(m.current);redrawMask(false);}
    }
  };
  frame.onpointermove=event=>{
    if(!m.pointers.has(event.pointerId))return;event.preventDefault();m.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(m.pointers.size>=2 && m.gesture?.distance){
      const [a,b]=[...m.pointers.values()],g=m.gesture,bounds=frame.getBoundingClientRect();
      m.zoom=Math.max(1,Math.min(6,g.zoom*Math.hypot(a.x-b.x,a.y-b.y)/g.distance));
      m.x=(a.x+b.x)/2-bounds.left-bounds.width/2-(g.cx-g.x)*m.zoom/g.zoom;
      m.y=(a.y+b.y)/2-bounds.top-bounds.height/2-(g.cy-g.y)*m.zoom/g.zoom;renderMaskZoom();
    }else if(m.pointers.size===1 && m.current){const point=maskPoint(event);if(m.current.tool==='place')m.current.points=[m.current.points[0],point];else m.current.points.push(point);if(!m.framePending){m.framePending=requestAnimationFrame(()=>{m.framePending=null;if(maskEditor===m)redrawMask(false);});}}
    else if(m.pointers.size===1 && m.gesture && !m.gesture.distance){m.x=m.gesture.x+event.clientX-m.gesture.startX;m.y=m.gesture.y+event.clientY-m.gesture.startY;renderMaskZoom();}
  };
  const finish=event=>{m.pointers.delete(event.pointerId);if(m.current?.tool==='place')m.auto.placement={...m.current,points:[...m.current.points]};m.current=null;m.gesture=null;if(frame.hasPointerCapture(event.pointerId))frame.releasePointerCapture(event.pointerId);redrawMask();};
  frame.onpointerup=frame.onpointercancel=finish;
  frame.onwheel=event=>{if(!event.ctrlKey)return;event.preventDefault();m.zoom=Math.max(1,Math.min(6,m.zoom*(event.deltaY<0?1.15:1/1.15)));renderMaskZoom();};
  $('#mask-apply').onclick=async()=>{
    const bounds=maskBounds();if(!bounds)return;$('#mask-apply').disabled=true;$('#mask-error').hidden=true;
    try {
      const output=document.createElement('canvas');output.width=m.canvas.width;output.height=m.canvas.height;
      const ctx=output.getContext('2d');ctx.drawImage(m.canvas,0,0);ctx.globalCompositeOperation='source-in';ctx.fillStyle='white';ctx.fillRect(0,0,output.width,output.height);ctx.globalCompositeOperation='destination-over';ctx.fillStyle='black';ctx.fillRect(0,0,output.width,output.height);
      let automatic=m.strokes.filter(s=>s.tool==='automatic').at(-1)?.metadata || m.selectionMetadata;
      if(automatic)automatic={...automatic,protect_faces:$('#auto-protect').checked,protect_skin:(automatic.mode || m.auto.mode)==='change'&&$('#auto-skin').checked};
      if(m.auto.placement){const [a,b]=m.auto.placement.points;automatic={...(automatic || {mode:m.auto.mode,target:$('#auto-target').value.trim(),extra_room:Number($('#auto-margin').value),protect_faces:$('#auto-protect').checked}),placement:[Math.floor(Math.min(a.x,b.x)*m.version.width/m.canvas.width),Math.floor(Math.min(a.y,b.y)*m.version.height/m.canvas.height),Math.ceil(Math.max(a.x,b.x)*m.version.width/m.canvas.width),Math.ceil(Math.max(a.y,b.y)*m.version.height/m.canvas.height)]};}
      maskSelections.set(m.key,{blob:await canvasBlob(output),protectionBlob:await protectedSelectionBlob(m),protectedUploaded:new Map(),coreBlob:await objectCoreBlob(m,m.previous),coreUploaded:new Map(),preserveShape:m.auto.mode==='change'&&$('#auto-shape').checked,automatic,bounds,width:m.version.width,height:m.version.height,feather:Number($('#mask-feather').value),detail:studioSettings.area.detail,context:studioSettings.area.context,blendMode:studioSettings.area.blend,colorMatch:studioSettings.area.colorMatch,align:studioSettings.area.align,enabled:true,uploaded:new Map()});
      closeMaskEditor();viewer.tool='edit';renderTools();$('#lb-prompt')?.focus();
    }catch(error){$('#mask-error').textContent=error.message;$('#mask-error').hidden=false;$('#mask-apply').disabled=false;}
  };
}
