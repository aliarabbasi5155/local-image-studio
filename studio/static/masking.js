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
  if (!$('#viewer-paint-mask')) return;
  const mask=currentMask(),ready=!!selectedVersion()?.ready;
  $('#viewer-paint-mask').disabled=!ready || connection.offline;$('#viewer-identity').disabled=!ready || connection.offline;
  $('#viewer-paint-mask').textContent=mask?'Adjust selection':'Select an area';
  $('#viewer-use-mask').checked=!!mask?.enabled;
  $('#viewer-use-mask').disabled=!mask || !ready;
  $('#viewer-remove-mask').hidden=!mask;
  $('#viewer-mask-hint').textContent=mask?.enabled?`Selected area only · ${mask.detail==='quick'?'512px':mask.detail==='maximum'?'maximum':'768px'} working limit · original size`:mask?'Mask paused · editing the whole image':'Whole image · select an area for local editing';
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
  const version=selectedVersion();if(!version?.ready || maskOpening)return;
  maskOpening=true;
  try {
    const key=draftKey(),existing=maskSelections.get(key),image=await loadMaskImage(version.url);
    if(key!==draftKey())return;
    if(document.fullscreenElement)await document.exitFullscreen();
    $('#mask-content').innerHTML=`<section class="mask-editor" aria-label="Selection painter">
      <header class="mask-header"><div><span class="eyebrow">EDIT ONLY WHAT YOU CHOOSE</span><h2 id="mask-title">Select the area to change</h2><p>Pink can change · blue faces and skin are protected · everything else stays original.</p></div><button class="icon-button" data-close="mask-dialog" aria-label="Cancel mask changes">${icon('close')}</button></header>
      <div class="mask-tools"><div class="mask-tool-choice" role="group" aria-label="Mask tool"><button data-mask-tool="paint" type="button" aria-pressed="true">Brush</button><button data-mask-tool="erase" type="button" aria-pressed="false">Erase</button><button data-mask-tool="pan" type="button" aria-pressed="false">Pan</button></div><label>Brush <input id="mask-brush" type="range" min="4" max="100" value="32"><output id="mask-brush-value">32</output></label><button id="mask-undo" type="button">Undo</button><button id="mask-clear" type="button">Clear</button><div class="mask-zoom"><button id="mask-zoom-out" type="button" aria-label="Zoom out mask photo">−</button><button id="mask-zoom-reset" type="button">100%</button><button id="mask-zoom-in" type="button" aria-label="Zoom in mask photo">＋</button></div></div>
      <div class="mask-surface"><div class="mask-frame" tabindex="0" role="group" aria-label="Paint selection. Use two fingers to zoom. Pan tool moves the zoomed photo."><div class="mask-stage"><img id="mask-photo" alt="Photo being masked" draggable="false"><canvas id="mask-canvas" aria-hidden="true"></canvas></div></div></div>
      <footer class="mask-footer"><label id="mask-manual-feather">Edge softness <input id="mask-feather" type="range" min="0" max="32" value="${existing?.feather ?? 8}"><output id="mask-feather-value">${existing?.feather ?? 8} px</output></label><details class="mask-options"><summary>Speed &amp; blending ${icon('chevron')}</summary><div><label class="mask-blend-choice">Edge blending<select id="mask-blend-mode"><option value="adaptive">Automatic · recommended</option><option value="manual">Manual · edge softness</option></select></label><label>Working resolution<select id="mask-detail"><option value="quick">Quick · up to 512px</option><option value="balanced">Balanced · up to 768px</option><option value="maximum">Maximum · quality limit</option></select></label><label>Surrounding context<select id="mask-context"><option value="tight">Tight · faster</option><option value="balanced">Balanced</option><option value="wide">More context · complex shapes</option></select></label><label class="mask-option-check"><input id="mask-color-match" type="checkbox" ${existing?.colorMatch===false?'':'checked'}>Match surrounding colors</label><label class="mask-option-check"><input id="mask-align" type="checkbox" ${existing?.align===false?'':'checked'}>Align patch edges</label></div><p>A smaller canvas is faster, with less fine detail. Maximum uses your image quality setting. Automatic blending also matches local colors near the outline. Turn color matching off for a deliberate lighting change.</p></details><p id="mask-help">Paint the whole object and its shadows · smaller selections are faster</p><p id="mask-error" class="error" role="alert" hidden></p><div><button type="button" data-close="mask-dialog">Cancel</button><button class="primary" id="mask-apply" type="button">Use this area ${icon('arrow')}</button></div></footer>
    </section>`;
    $('#mask-photo').src=version.url;
    $('#mask-detail').value=existing?.detail || 'balanced';$('#mask-context').value=existing?.context || 'balanced';
    $('#mask-blend-mode').value=existing?.blendMode || 'adaptive';updateMaskBlendControls();
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
    arrangeMaskWorkspace();bindMaskEditor();redrawMask();$('#mask-dialog').showModal();syncMaskViewport();fitMaskPhoto();
    maskEditor.observer=new ResizeObserver(fitMaskPhoto);maskEditor.observer.observe($('.mask-surface'));
  } finally {maskOpening=false;}
}

function arrangeMaskWorkspace() {
  const editor=$('.mask-editor'),header=$('.mask-header'),surface=$('.mask-surface'),tools=$('.mask-tools'),footer=$('.mask-footer');
  $('#mask-title').textContent='Select an area';
  header.querySelector('p').innerHTML='<span class="mask-legend-pink">Pink can change</span><span class="mask-legend-blue">Blue stays protected</span>';
  header.querySelector('.eyebrow').textContent='LOCAL EDIT';
  const toggle=document.createElement('button');toggle.id='mask-controls-toggle';toggle.type='button';toggle.className='mask-controls-toggle';
  toggle.innerHTML=`${icon('settings')} <span>Settings</span>`;toggle.setAttribute('aria-controls','mask-controls');
  header.insertBefore(toggle,header.lastElementChild);
  const workspace=document.createElement('div');workspace.className='mask-workspace';
  const controls=document.createElement('aside');controls.id='mask-controls';controls.className='mask-controls';controls.setAttribute('aria-label','Selection controls');
  controls.innerHTML='<div class="mask-controls-heading"><strong>Selection tools</strong><button id="mask-controls-close" type="button">Back to photo ↓</button></div><div class="mask-controls-scroll"></div>';
  const scroll=controls.querySelector('.mask-controls-scroll');
  scroll.append($('.mask-auto'));
  const brush=document.createElement('div');brush.className='mask-brush-settings';
  const brushLabel=tools.querySelector(':scope > label');brushLabel.firstChild.textContent='Brush size ';brush.append(brushLabel,$('#mask-clear'));scroll.append(brush);
  scroll.append($('#mask-manual-feather'),$('.mask-options'),$('#mask-help'));
  // Zoom stays on the photo even while the controls panel is open.
  surface.append($('.mask-zoom'));
  editor.insertBefore(workspace,tools);workspace.append(surface,controls);
  const automatic=footer.querySelector('[data-close]');automatic.removeAttribute('data-close');automatic.id='mask-auto-open';
  automatic.innerHTML=`${icon('spark')} Auto select`;
  const open=()=>{setMaskControls(true);$('.mask-auto').open=true;};
  automatic.onclick=open;toggle.onclick=()=>setMaskControls(!editor.classList.contains('controls-open'));
  $('#mask-controls-close').onclick=()=>setMaskControls(false,true);
  setMaskControls(!matchMedia('(max-width: 900px), (max-height: 500px)').matches);
}

function setMaskControls(open,returnFocus=false) {
  $('.mask-editor')?.classList.toggle('controls-open',open);
  const controls=$('#mask-controls');if(!controls)return;
  controls.inert=!open;controls.setAttribute('aria-hidden',String(!open));
  $('#mask-controls-toggle').setAttribute('aria-expanded',String(open));
  $('#mask-auto-open').setAttribute('aria-expanded',String(open));
  if(returnFocus)$('#mask-auto-open').focus({preventScroll:true});
}

function syncMaskViewport() {
  if(!maskEditor)return;
  const viewport=window.visualViewport;
  $('#mask-dialog').style.height=`${viewport?.height || innerHeight}px`;
  $('#mask-dialog').style.top=`${viewport?.offsetTop || 0}px`;
}
window.visualViewport?.addEventListener('resize',syncMaskViewport);
window.visualViewport?.addEventListener('scroll',syncMaskViewport);
window.addEventListener('resize',syncMaskViewport);

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
  $('#mask-zoom-reset').textContent=m.zoom===1?'Fit':`${Math.round(m.zoom*100)}%`;
  $('#mask-zoom-reset').setAttribute('aria-label','Fit the whole photo');
  $('#mask-zoom-out').disabled=m.zoom<=1;$('#mask-zoom-in').disabled=m.zoom>=6;
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
function updateMaskBlendControls() {
  const manual=$('#mask-blend-mode').value==='manual';
  $('#mask-manual-feather').hidden=!manual;
  $('#mask-help').textContent=manual?'Paint the whole object and its shadows · smaller selections are faster':'Automatic blending stays inside your outline · paint the whole object with a little background around it';
}

function bindMaskEditor() {
  const m=maskEditor,frame=$('.mask-frame');
  for(const button of document.querySelectorAll('[data-mask-tool]'))button.onclick=()=>{m.tool=button.dataset.maskTool;for(const other of document.querySelectorAll('[data-mask-tool]'))other.setAttribute('aria-pressed',String(other===button));frame.classList.toggle('moving',m.tool==='pan');};
  $('#mask-brush').oninput=event=>$('#mask-brush-value').value=event.target.value;
  $('#mask-feather').oninput=event=>$('#mask-feather-value').value=`${event.target.value} px`;
  $('#mask-blend-mode').onchange=updateMaskBlendControls;
  $('#mask-undo').onclick=()=>{if(m.strokes.pop()?.tool==='place')m.auto.placement=m.strokes.filter(s=>s.tool==='place').at(-1)||null;redrawMask();};$('#mask-clear').onclick=()=>{m.auto.placement=null;m.strokes.push({tool:'clear'});redrawMask();};
  $('#mask-zoom-in').onclick=()=>{m.zoom=Math.min(6,m.zoom*1.25);renderMaskZoom();};
  $('#mask-zoom-out').onclick=()=>{m.zoom=Math.max(1,m.zoom/1.25);renderMaskZoom();};
  $('#mask-zoom-reset').onclick=()=>{m.zoom=1;m.x=m.y=0;renderMaskZoom();};
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
      maskSelections.set(m.key,{blob:await canvasBlob(output),protectionBlob:await protectedSelectionBlob(m),protectedUploaded:new Map(),coreBlob:await objectCoreBlob(m,m.previous),coreUploaded:new Map(),preserveShape:m.auto.mode==='change'&&$('#auto-shape').checked,automatic,bounds,width:m.version.width,height:m.version.height,feather:Number($('#mask-feather').value),detail:$('#mask-detail').value,context:$('#mask-context').value,blendMode:$('#mask-blend-mode').value,colorMatch:$('#mask-color-match').checked,align:$('#mask-align').checked,enabled:true,uploaded:new Map()});
      $('#mask-dialog').close();updateMaskControls();updateEditorSettings();scheduleViewerEstimate();
    }catch(error){$('#mask-error').textContent=error.message;$('#mask-error').hidden=false;$('#mask-apply').disabled=false;}
  };
}
$('#mask-dialog').addEventListener('close',()=>{maskEditor?.observer?.disconnect();maskEditor=null;$('#mask-content').innerHTML='';});
$('#mask-dialog').addEventListener('cancel',event=>{
  if($('.mask-editor')?.classList.contains('controls-open')){event.preventDefault();setMaskControls(false,true);}
});
