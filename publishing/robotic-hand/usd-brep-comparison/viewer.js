import * as THREE from './assets/vendor/three.module.min.js';
import {OrbitControls} from './assets/vendor/OrbitControls.js';

const $=id=>document.getElementById(id);
const SOURCE='356ed1fd967696bd9bf9393260950db2556649fd867ea5f52cf81e381721e64d';
const state={route:'ocp',selected:'g00047',opacity:1,reveal:false,wireframe:false,models:new Map(),meshes:new Map(),motion:{},loading:null,manifest:null,performance:{}};
let renderer,camera,scene,controls,frame,renderPending=false,raycaster,pointer;
const afterFrameCallbacks=[];
const host=$('canvas-root');
const hex=buffer=>Array.from(new Uint8Array(buffer),v=>v.toString(16).padStart(2,'0')).join('');
function assetURL(asset){const url=new URL(asset.url,document.baseURI);url.searchParams.set('sha256',asset.sha256);return url;}
async function checkedAsset(asset){
  const r=await fetch(assetURL(asset));if(!r.ok)throw new Error(`Asset request failed (${r.status})`);const b=await r.arrayBuffer();
  if(!crypto.subtle)throw new Error('A secure context is required for asset verification');
  const digest=hex(await crypto.subtle.digest('SHA-256',b));if(digest!==asset.sha256)throw new Error('Display asset checksum mismatch; stopped');return b;
}
function requestRender(){if(!renderPending){renderPending=true;requestAnimationFrame(()=>{renderPending=false;if(renderer)renderer.render(scene,camera);for(const done of afterFrameCallbacks.splice(0))done();});}}
function afterNextFrame(){return new Promise(resolve=>{afterFrameCallbacks.push(resolve);requestRender();});}
function markReady(mark,key){if(state.performance[key]!==undefined)return;state.performance[key]=performance.mark(mark).startTime;}
function updateBrowserReady(){const p=state.performance;$('browser-ready').textContent=state.route==='nvidia'&&p.nvidiaFirstActivationMs!==undefined?`NVIDIA saved mesh opened in ${(p.nvidiaFirstActivationMs/1000).toFixed(2)} s in this browser.`:p.ocpReadyAtMs!==undefined?`Saved hand mesh ready in ${(p.ocpReadyAtMs/1000).toFixed(2)} s in this browser.`:'Verifying saved display assets…';$('browser-ready').title='Saved mesh loading, hash verification, first frame and motion installation. This is not an offline CAD kernel measurement or FPS result.';}
function palette(name){if(/tendon|elastic/i.test(name))return 0xd7ad68;if(/pin|capstan/i.test(name))return 0x9aabb5;if(/servo/i.test(name))return 0x52778b;if(/pad/i.test(name))return 0x6e7779;if(/palm|cover|neck/i.test(name))return 0xd7d3c8;return 0xc7a991;}
function polygonTriangles(ids,positions){
  if(ids.length===3)return [[0,1,2]];
  const pts=ids.map(i=>new THREE.Vector3().fromArray(positions,i*3));const normal=new THREE.Vector3();
  for(let i=0;i<pts.length;i++){const a=pts[i],b=pts[(i+1)%pts.length];normal.x+=(a.y-b.y)*(a.z+b.z);normal.y+=(a.z-b.z)*(a.x+b.x);normal.z+=(a.x-b.x)*(a.y+b.y);}
  const axis=Math.abs(normal.x)>Math.abs(normal.y)?(Math.abs(normal.x)>Math.abs(normal.z)?0:2):(Math.abs(normal.y)>Math.abs(normal.z)?1:2);
  const contours=pts.map(v=>axis===0?new THREE.Vector2(v.y,v.z):axis===1?new THREE.Vector2(v.z,v.x):new THREE.Vector2(v.x,v.y));
  const tris=THREE.ShapeUtils.triangulateShape(contours,[]);
  for(const tri of tris){const c=new THREE.Vector3().subVectors(pts[tri[1]],pts[tri[0]]).cross(new THREE.Vector3().subVectors(pts[tri[2]],pts[tri[0]]));if(c.dot(normal)<0)[tri[1],tri[2]]=[tri[2],tri[1]];}
  return tris;
}
function makeGeometry(buffer,info,route){
  const points=new Float32Array(buffer,info.points_offset,info.points_count*3),faces=new Uint32Array(buffer,info.faces_offset,info.faces_length),normals=info.normals_count?new Float32Array(buffer,info.normals_offset,info.normals_count*3):null;
  const geometry=new THREE.BufferGeometry();let cursor=0,corner=0;
  if(route==='ocp'){
    const indices=[];while(cursor<faces.length){const n=faces[cursor++],ids=Array.from(faces.subarray(cursor,cursor+n));cursor+=n;for(const tri of polygonTriangles(ids,points))for(const k of tri)indices.push(ids[k]);}
    geometry.setAttribute('position',new THREE.BufferAttribute(points,3));geometry.setIndex(indices);geometry.computeVertexNormals();
  }else{
    const p=[],ns=[];while(cursor<faces.length){const n=faces[cursor++],ids=Array.from(faces.subarray(cursor,cursor+n));cursor+=n;for(const tri of polygonTriangles(ids,points)){for(const k of tri){const i=ids[k]*3;p.push(points[i],points[i+1],points[i+2]);const j=(corner+k)*3;ns.push(normals[j],normals[j+1],normals[j+2]);}}corner+=n;}
    if(corner!==info.normals_count)throw new Error('NVIDIA face-varying normal contract mismatch');
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(p,3));geometry.setAttribute('normal',new THREE.Float32BufferAttribute(ns,3));
  }
  if(cursor!==faces.length)throw new Error('Invalid packed polygon array');geometry.computeBoundingSphere();return geometry;
}
async function loadRoute(route){
  if(state.models.has(route))return state.models.get(route);const buffer=await checkedAsset(state.manifest.assets[route]);const model=new THREE.Group(),meshes=[];
  try{for(const item of state.manifest.groups){const material=new THREE.MeshStandardMaterial({color:palette(item.name),roughness:.72,metalness:.08,side:THREE.DoubleSide});const mesh=new THREE.Mesh(makeGeometry(buffer,item[route],route),material);mesh.name=item.name;mesh.userData.groupId=item.id;model.add(mesh);meshes.push(mesh);}}
  catch(error){for(const mesh of meshes){mesh.geometry.dispose();mesh.material.dispose();}throw error;}
  model.visible=false;frame.add(model);state.models.set(route,model);state.meshes.set(route,meshes);return model;
}
function applyAppearance(){
  for(const [route,meshes]of state.meshes){for(const mesh of meshes){const selected=mesh.userData.groupId===state.selected;const m=mesh.material;m.opacity=selected?state.opacity:(state.reveal?0.11:1);m.transparent=m.opacity<1;m.depthWrite=m.opacity>=1;m.wireframe=state.wireframe;m.emissive.setHex(selected?0x3d2810:0);mesh.renderOrder=m.transparent?1:0;}}
  $('opacity-value').textContent=`${Math.round(state.opacity*100)}%`;requestRender();
}
function select(id){state.selected=id;$('part').value=id;const g=state.manifest.groups.find(x=>x.id===id);if(!g)return;
  const q=g.qualification;$('part-status').classList.toggle('pass',q.pass);
  $('part-status').textContent=`Recorded at Rest · ${g.solid_count} source solid${g.solid_count===1?'':'s'} · NVIDIA gate ${q.pass?'PASS':'FAIL'} · volume Δ ${(q.volume_relative_error*100).toFixed(4)}% · area Δ ${(q.area_relative_error*100).toFixed(4)}%${q.mesh_manifold?'':' · NONMANIFOLD DISPLAY MESH'}`;applyAppearance();
}
async function switchRoute(route){
  if(state.loading||!state.manifest)return;const activationStart=performance.now();state.loading=route;$('loading').hidden=false;$('load-message').textContent=route==='nvidia'?'Loading and verifying real NVIDIA-produced geometry…':'Loading OCP baseline…';
  try{const model=await loadRoute(route);for(const [id,m]of state.models)m.visible=id===route;state.route=route;for(const name of ['ocp','nvidia']){$(`route-${name}`).classList.toggle('active',name===route);$(`route-${name}`).setAttribute('aria-pressed',String(name===route));}
    $('mode-badge').textContent=route==='nvidia'?'NVIDIA / SMLIB · DIAGNOSTIC':'OCP · AUTHORED STEP';$('route-note').textContent=route==='nvidia'?'Actual NVIDIA USD BRep → SMLib tessellation, saved offline. Qualification failed; this is diagnostic inspection.':'OCP tessellation of the original authored STEP. Three.js renders this display mesh.';
    $('asset-status').textContent=`${route==='nvidia'?'NVIDIA':'OCP'} display asset SHA-256 verified. Both routes use the same source STEP hash.`;applyMotion();applyAppearance();$('loading').hidden=true;
    await afterNextFrame();if(route==='nvidia'&&state.performance.nvidiaReadyAtMs===undefined){markReady('nvidia-ready','nvidiaReadyAtMs');state.performance.nvidiaFirstActivationMs=performance.now()-activationStart;}updateBrowserReady();
  }catch(e){$('load-message').textContent=`Could not open ${route}: ${e.message}. The previous route remains available.`;if(state.models.size)setTimeout(()=>{$('loading').hidden=true;},4500);}
  finally{state.loading=null;}
}
function setupScene(){
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'low-power'});renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.6));renderer.setClearColor(0x13151a);renderer.outputColorSpace=THREE.SRGBColorSpace;host.appendChild(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(35,1,.1,3000);camera.up.set(0,0,1);camera.position.set(210,-340,200);
  controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,0,0);controls.enableDamping=false;controls.minDistance=90;controls.maxDistance=900;controls.enablePan=true;controls.addEventListener('change',requestRender);controls.update();controls.saveState();
  scene.add(new THREE.HemisphereLight(0xe8efff,0x756451,2.1));const key=new THREE.DirectionalLight(0xffefdc,3.2);key.position.set(100,-180,250);scene.add(key);const fill=new THREE.DirectionalLight(0xb6d2ff,1.2);fill.position.set(-160,110,50);scene.add(fill);
  frame=new THREE.Group();frame.rotation.x=Math.PI/2;const bounds=new THREE.Box3();for(const g of state.manifest.groups){bounds.expandByPoint(new THREE.Vector3().fromArray(g.bounds[0]));bounds.expandByPoint(new THREE.Vector3().fromArray(g.bounds[1]));}const center=bounds.getCenter(new THREE.Vector3()).applyAxisAngle(new THREE.Vector3(1,0,0),Math.PI/2);frame.position.copy(center.negate());scene.add(frame);
  const resize=()=>{const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();requestRender();};new ResizeObserver(resize).observe(host);resize();
  raycaster=new THREE.Raycaster();pointer=new THREE.Vector2();const active=new Map();let candidate=null;
  renderer.domElement.addEventListener('pointerdown',e=>{active.set(e.pointerId,{x:e.clientX,y:e.clientY});if(active.size===1)candidate={id:e.pointerId,x:e.clientX,y:e.clientY};else candidate=null;});
  renderer.domElement.addEventListener('pointercancel',e=>{active.delete(e.pointerId);candidate=null;});
  renderer.domElement.addEventListener('pointerup',e=>{active.delete(e.pointerId);if(!candidate||candidate.id!==e.pointerId)return;const start=candidate;candidate=null;if(Math.hypot(e.clientX-start.x,e.clientY-start.y)>7)return;const r=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);const hit=raycaster.intersectObjects(state.meshes.get(state.route)||[],false)[0];if(hit)select(hit.object.userData.groupId);});
  renderer.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();$('loading').hidden=false;$('load-message').textContent='The graphics context was lost. Reload this page to reopen the verified assets.';});
}
function bindControls(){
  $('route-ocp').addEventListener('click',()=>switchRoute('ocp'));$('route-nvidia').addEventListener('click',()=>switchRoute('nvidia'));$('part').addEventListener('change',e=>select(e.target.value));
  $('opacity').addEventListener('input',e=>{state.opacity=+e.target.value/100;applyAppearance();});for(const [id,value]of [['opaque',100],['transparent',20]])$(id).addEventListener('click',()=>{$('opacity').value=value;state.opacity=value/100;applyAppearance();});
  $('reveal').addEventListener('change',e=>{state.reveal=e.target.checked;applyAppearance();});$('wireframe').addEventListener('change',e=>{state.wireframe=e.target.checked;applyAppearance();});$('reset-view').addEventListener('click',()=>{controls.reset();requestRender();});
  for(const [id,factor]of [['zoom-in',.83],['zoom-out',1.2]])$(id).addEventListener('click',()=>{const d=camera.position.clone().sub(controls.target).multiplyScalar(factor);d.clampLength(controls.minDistance,controls.maxDistance);camera.position.copy(controls.target).add(d);controls.update();requestRender();});
  $('rest-pose').addEventListener('click',()=>{for(const input of document.querySelectorAll('[data-motion]')){input.value=0;state.motion[input.dataset.motion]=0;input.dispatchEvent(new Event('input'));}applyMotion();});
}
function applyMotion(){
  // Optional verified source-axis metadata is integrated separately. Rest is identity.
  if(typeof state.motionAdapter==='function')state.motionAdapter(state);requestRender();
}
async function initMotion(){
  if(!state.manifest.motion)return;
  const mod=await import('./motion-adapter.js?v='+state.manifest.motion_adapter_sha256);const bytes=await checkedAsset(state.manifest.motion);const metadata=JSON.parse(new TextDecoder().decode(bytes));
  await mod.install({metadata,state,THREE,requestRender,applyMotion});
}
async function initCoreMetrics(){
  if(!state.manifest.core_metrics)return;
  const bytes=await checkedAsset(state.manifest.core_metrics),data=JSON.parse(new TextDecoder().decode(bytes));
  if(data.schema!=='cadclaw.core_runtime_comparison.v1'||data.source_step_sha256!==SOURCE||!Array.isArray(data.rows))throw new Error('Core measurement receipt mismatch');
  const contended=data.quality==='contention_affected';$('core-metrics').classList.toggle('contended',contended);
  $('core-metrics-scope').textContent=(contended?'Timing quality: contention affected. Unrelated CPU jobs overlapped this profile; no speedup is established. ':'')+data.scope;
  $('core-metrics-rows').replaceChildren(...data.rows.map(row=>{const tr=document.createElement('tr'),label=document.createElement('td');label.textContent=row.label;tr.append(label);for(const side of ['before','after']){const td=document.createElement('td'),median=row[side+'_median_seconds'],samples=row[side+'_samples_seconds']||[],range=row[side+'_range_seconds'];td.textContent=typeof median==='number'?`${median.toFixed(3)} s`:'Not measured';if(samples.length){const detail=document.createElement('small');detail.textContent=`${samples.length} runs · ${range[0].toFixed(3)}–${range[1].toFixed(3)} s`;td.append(detail);}tr.append(td);}return tr;}));
  $('core-metrics-parity').textContent=data.parity_summary;$('core-metrics-receipt').href=assetURL(state.manifest.core_metrics);$('core-metrics').hidden=false;
}
function metricElement(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function metricTable(headers,rows){const wrap=metricElement('div',undefined,'metric-table'),table=metricElement('table'),head=metricElement('thead'),body=metricElement('tbody'),tr=metricElement('tr');for(const label of headers)tr.append(metricElement('th',label));head.append(tr);for(const row of rows){const r=metricElement('tr');for(const value of row)r.append(metricElement('td',value));body.append(r);}table.append(head,body);wrap.append(table);return wrap;}
function sampleText(label,record,milliseconds=false){const scale=milliseconds?1000:1,unit=milliseconds?'ms':'s',digits=milliseconds?3:5;return `${label}: ${record.samples_seconds.map(v=>(v*scale).toFixed(digits)).join(', ')} ${unit}. Range ${(record.range_seconds[0]*scale).toFixed(digits)}–${(record.range_seconds[1]*scale).toFixed(digits)} ${unit}.`;}
async function initGX10Metrics(){
  if(!state.manifest.gx10_metrics)return;
  const bytes=await checkedAsset(state.manifest.gx10_metrics),data=JSON.parse(new TextDecoder().decode(bytes));
  if(data.schema!=='cadclaw.gx10_public_observations.v1'||data.source_step_sha256!==SOURCE||data.cache_comparison.length!==3||data.backend_comparison.length!==3)throw new Error('GX10 observation receipt mismatch');
  $('gx10-cache').replaceChildren(metricTable(['Operation','Before (s)','Cached (s)'],data.cache_comparison.map(row=>[row.label,row.before.median_seconds.toFixed(5),row.after.median_seconds.toFixed(5)])));
  const samples=metricElement('details');samples.append(metricElement('summary','Cache samples, ranges and parity'));
  for(const row of data.cache_comparison){samples.append(metricElement('p',`${row.label}: ${row.status}.`));samples.append(metricElement('p',sampleText('Before',row.before)),metricElement('p',sampleText('Cached',row.after)));}$('gx10-cache-samples').replaceChildren(samples);
  $('gx10-image-gate').textContent='Exact render gate failed in 1/3 pairs: the top view differs at one pixel, two channels, by at most 1/255. Checked geometry hashes match. The failed image gate remains failed.';
  $('gx10-backend-status').replaceChildren(metricTable(['Case','OCP basic smoke','NVIDIA geometry gates'],data.backend_comparison.map(row=>{const ocp=row.ocp,nv=row.nvidia,ocpPass=ocp.groups_failed.every(v=>v===0),nvPass=nv.groups_failed.every(v=>v===0);return [row.id==='authored_plate'?'Plate':row.id==='nist_fixture'?'NIST':'Halden',`${ocpPass?'Pass':'Fail'}: ${ocp.groups_passed[0]}/${ocp.groups_measured[0]} groups`,`${nvPass?'Pass':'Fail'}: ${nv.groups_passed[0]} pass, ${nv.groups_failed[0]} fail`];})));
  $('gx10-palm').textContent=`NIST mass parity fails. Halden retains 47 mass failures and a nonmanifold palm mesh. GX10 ARM palm: ${data.palm_mesh.gx10_ARM_spine_edges} spine edges; historical Windows palm: ${data.palm_mesh.historical_Windows_spine_edges}. These are separate platform-bound outputs. The viewer still shows the preserved Windows meshes.`;
  const labels=[['python_module_import','Python module startup'],['saved_geometry_read','STEP / saved USD read'],['total_mass_properties','Mass properties'],['total_tight_bounds','Tight bounds'],['total_tessellation_kernel','Tessellation*'],['child_total','Whole smoke child']];
  const cards=[];
  for(const row of data.backend_comparison){const card=metricElement('details');card.append(metricElement('summary',`${row.label}: stage timings`));card.append(metricElement('p','Medians in milliseconds, three fresh-process observations. Module startup is separate; stages must not be added into an end-to-end claim.'));
    card.append(metricTable(['Stage','OCP STEP (ms)','NVIDIA USD (ms)'],labels.map(([key,label])=>[label,(row.ocp.stages[key].median_seconds*1000).toFixed(3),(row.nvidia.stages[key].median_seconds*1000).toFixed(3)])));
    card.append(metricElement('p','*OCP uses relative deflection and includes array extraction. NVIDIA uses an absolute chord-height kernel timer. Mesh accuracy and timed work are not matched.'));
    const detail=metricElement('details');detail.append(metricElement('summary','All stage samples and ranges'));
    for(const [key,label] of labels)for(const engine of ['ocp','nvidia'])detail.append(metricElement('p',sampleText(`${engine==='ocp'?'OCP':'NVIDIA'} · ${label}`,row[engine].stages[key],true)));
    card.append(detail);cards.push(card);
  }$('gx10-backend-stages').replaceChildren(...cards);
  const runtime=data.runtime;$('gx10-runtime').textContent=`ARM64 / GB10 host, ${runtime.architecture}; Python ${runtime.python}, CadQuery ${runtime.cadquery}, OCP ${runtime['cadquery-ocp']}, VTK ${runtime.vtk}, USD BRep ${runtime['usd-brep']}; ${runtime.render_backend}. Saved geometry reads exclude Python module startup. GX10 STEP-to-USD conversion was not timed.`;
  $('gx10-accounting').textContent=data.completion_accounting;$('gx10-limits').replaceChildren(...data.limits.map(line=>metricElement('p',line)));
  $('gx10-metrics-receipt').href=assetURL(state.manifest.gx10_metrics);$('gx10-metrics').hidden=false;
}
async function main(){
  try{const r=await fetch('./assets/manifest.json',{cache:'no-store'});if(!r.ok)throw new Error('Comparison manifest unavailable');state.manifest=await r.json();if(state.manifest.source_step_sha256!==SOURCE||state.manifest.groups.length!==59||state.manifest.source_solid_count!==120)throw new Error('Authored source manifest mismatch');
    const receipt=await checkedAsset(state.manifest.receipt);const parsed=JSON.parse(new TextDecoder().decode(receipt));if(parsed.source_step_sha256!==SOURCE||parsed.gate.groups_passed!==12||parsed.gate.overall_pass!==false)throw new Error('Qualification receipt mismatch');
    document.querySelector('a[href="./assets/comparison-receipt.json"]').href=assetURL(state.manifest.receipt);document.querySelector('a[href="./assets/halden-exact.usda"]').href=assetURL(state.manifest.exact_usd);
    $('part').replaceChildren(...state.manifest.groups.map(g=>{const o=document.createElement('option');o.value=g.id;o.textContent=g.name;return o;}));if(!state.manifest.groups.some(g=>g.id===state.selected))state.selected=state.manifest.groups[0].id;
    setupScene();bindControls();await switchRoute('ocp');if(!state.models.has('ocp'))return;for(const id of ['part','opacity','opaque','transparent','reveal','wireframe'])$(id).disabled=false;select(state.selected);await initMotion();await afterNextFrame();markReady('halden-ocp-ready','ocpReadyAtMs');updateBrowserReady();$('route-ocp').disabled=false;$('route-nvidia').disabled=false;
    globalThis.haldenComparison={getState:()=>({route:state.route,selected:state.selected,opacity:state.opacity,reveal:state.reveal,wireframe:state.wireframe,loadedRoutes:[...state.models.keys()],sourceStepSHA256:SOURCE,motionValues:{...state.motion},performance:{...state.performance},selectedTransform:state.meshes.get(state.route)?.find(m=>m.userData.groupId===state.selected)?.matrix.toArray(),renderedMeshes:state.meshes.get(state.route)?.length,rendererCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles}),switchRoute,select};
    initCoreMetrics().catch(error=>{$('core-metrics').hidden=false;$('core-metrics-scope').textContent='Core measurement panel unavailable: '+error.message;});
    initGX10Metrics().catch(error=>{$('gx10-metrics').hidden=false;$('gx10-image-gate').textContent='GX10 observations unavailable: '+error.message;});
  }catch(error){$('loading').hidden=false;$('load-message').textContent=`Comparison unavailable: ${error.message}`;}
}
main();
