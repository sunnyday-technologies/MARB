// Runtime rigid transforms only. Geometry and engineering evidence stay frozen.
export async function install({metadata,state,THREE,requestRender,applyMotion}){
  if(metadata.schema!=='halden.matching_revision_motion.v1'||metadata.source_sha256!==state.manifest.source_step_sha256||metadata.source_commit!=='edbd541b1a9d2e9eb7eb5706309239aa3d94e2e6'||metadata.joints.length!==15)throw new Error('Matching-source motion contract mismatch');
  const byId=new Map(metadata.joints.map(j=>[j.joint_id,j]));
  const chains=new Map(metadata.parts.map(p=>[p.group_id,p.illustrative_joint_chain]));
  for(const g of state.manifest.groups)if(!chains.has(g.id))throw new Error('Motion occurrence mapping mismatch');
  const primary=metadata.controls,optional=metadata.optional_controls||[],specs=[...primary,...optional];
  if(primary.length!==5||primary.some(s=>s.min!==0||s.max!==100||s.default!==0))throw new Error('Unexpected source control range');
  const panel=document.getElementById('motion-controls');
  function addControl(spec){
    state.motion[spec.id]=spec.default;const row=document.createElement('div');row.className='motion-row';
    const labels=document.createElement('div');labels.className='slider-label';const label=document.createElement('label');label.htmlFor='motion-'+spec.id;label.textContent=spec.label;
    const output=document.createElement('output');output.textContent='Rest · 0%';output.htmlFor=label.htmlFor;labels.append(label,output);
    const input=document.createElement('input');input.type='range';input.id=label.htmlFor;input.dataset.motion=spec.id;input.min=spec.min;input.max=spec.max;input.step=1;input.value=spec.default;
    input.addEventListener('input',()=>{state.motion[spec.id]=+input.value/100;output.textContent=+input.value===0?'Rest · 0%':`${input.value}%`;applyMotion();});row.append(labels,input);panel.append(row);
  }
  primary.forEach(addControl);optional.forEach(addControl);
  if(optional.length){const note=document.createElement('p');note.className='muted';note.textContent='This CAD revision has a palm-plane thumb hinge. The matching browser’s additional thumb lift/pronation is not represented here.';panel.append(note);}
  const fixed=document.createElement('p');fixed.className='muted';fixed.textContent='Five capstans follow the source’s visual spin coupling. Servo bodies and tendon/elastic geometry stay fixed. Finger spread has no verified source CAD axis.';panel.append(fixed);
  document.getElementById('motion-status').textContent='Five curl controls use the CAD revision that produced this STEP (edbd541). Zero is authored Rest;100% is its source controller endpoint. These display ranges are not hardware travel limits.';
  document.getElementById('rest-pose').disabled=false;
  function rotation(axisRecord,angle){
    const axis=new THREE.Vector3().fromArray(axisRecord.axis_direction_world_unit).normalize(),point=new THREE.Vector3().fromArray(axisRecord.axis_point_world_mm);
    const matrix=new THREE.Matrix4().makeRotationAxis(axis,angle),rotated=point.clone().applyMatrix4(matrix);matrix.setPosition(point.clone().sub(rotated));return matrix;
  }
  state.motionAdapter=()=>{
    const matrices=new Map();for(const spec of specs)for(const joint of spec.joint_deltas){if(matrices.has(joint.joint_id))throw new Error('Overlapping motion control assignment');const source=byId.get(joint.joint_id);if(!source?.axis)throw new Error('Missing verified source axis');matrices.set(joint.joint_id,rotation(source.axis,joint.stored_axis_degrees_at_100*Math.PI/180*state.motion[spec.id]));}
    const capstans=new Map((metadata.visual_capstan_spins||[]).map(spin=>[spin.group_id,rotation(spin.axis,spin.stored_axis_degrees_at_100*Math.PI/180*state.motion[spin.control_id])]));
    for(const meshes of state.meshes.values())for(const mesh of meshes){mesh.matrixAutoUpdate=false;mesh.matrix.identity();for(const joint of chains.get(mesh.userData.groupId)||[]){const matrix=matrices.get(joint);if(matrix)mesh.matrix.multiply(matrix);}if(capstans.has(mesh.userData.groupId))mesh.matrix.copy(capstans.get(mesh.userData.groupId));mesh.matrixWorldNeedsUpdate=true;}
    requestRender();
  };
  applyMotion();
}
