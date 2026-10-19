// 端口映射
let portMaps = [];

async function loadPortMaps(){
 try{
  const r=await fetch('/api/port-maps');const d=await r.json()
  portMaps=d.maps||[]
  const tbody=$('portMapBody')
  if(!tbody) return
  tbody.innerHTML=portMaps.map((m,i)=>{
   const running=!!m.running
   const btnClass=running?'btn-warning':'btn-success'
   const btnText=running?'⏹ 停止':'▶ 启动'
   return `<tr>
    <td style="text-align:center">${escapeHtml(m.local_port||'')}</td>
    <td>${escapeHtml(m.target||'')}</td>
    <td><input class="remark-input" value="${escapeHtml(m.remark||'')}" placeholder="备注" onchange="updateRemark(${i},this.value)" style="border:none;background:transparent;width:100%;outline:none" onfocus="this.style.borderBottom='1px solid var(--border)'" onblur="this.style.borderBottom='none'"></td>
    <td style="text-align:center"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${running?'#52c41a':'#d9d9d9'};margin-right:4px"></span>${running?'运行中':'已停止'}</td>
    <td style="text-align:center">
     <button class="btn btn-sm ${btnClass}" onclick="togglePortMap(${i})">${btnText}</button>
     <button class="btn btn-sm" onclick="editPortMap(${i})">✏️ 编辑</button>
     <button class="btn btn-sm btn-danger" onclick="delPortMap(${i})">✕ 删除</button>
    </td>
   </tr>`}).join('')||'<tr><td colspan="5" class="empty">暂无映射规则</td></tr>'
 }catch(e){}
}

async function updateRemark(i,val){
 if(i<portMaps.length){
  portMaps[i].remark=val
  await fetch('/api/port-maps/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({remark:val})})
 }
}

async function togglePortMap(i){
 const m=portMaps[i]
 if(!m) return
 const action = m.running ? 'stop' : 'start'
 const r=await fetch(`/api/port-maps/${i}/${action}`,{method:'POST'})
 const d=await r.json()
 if(d.success){showToast(action==='start'?'已启动':'已停止','success');loadPortMaps()}
 else showToast(d.detail||'操作失败','error')
}

async function delPortMap(i){
 if(!confirm('确定删除？'))return
 const r=await fetch('/api/port-maps/'+i,{method:'DELETE'})
 const d=await r.json()
 if(d.success){showToast('已删除','success');loadPortMaps()}
 else showToast(d.detail||'删除失败','error')
}

function showAddPortMap(){
 $('modalTitle').textContent='➕ 添加端口映射'
 $('modalBody').innerHTML=`
  <div style="display:flex;gap:8px;align-items:flex-end">
   <div class="form-group" style="flex:1"><label>监听端口</label><input id="addLocalPort" type="number" placeholder="1111"></div>
   <div class="form-group" style="flex:1"><label>转发地址</label><input id="addTargetHost" placeholder="192.168.1.1"></div>
   <div class="form-group" style="flex:1"><label>转发端口</label><input id="addTargetPort" type="number" placeholder="1233"></div>
  </div>
  <div class="form-group" style="margin-top:8px"><label>备注</label><input id="addRemark" placeholder="选填"></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmAddPortMap()">✅ 添加</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmAddPortMap(){
 const local_port=$('addLocalPort').value.trim()
 const host=$('addTargetHost').value.trim()
 const port=$('addTargetPort').value.trim()
 if(!local_port||!host||!port){showToast('请填写完整','error');return}
 const target=host+':'+port
 const remark=$('addRemark').value.trim()
 const r=await fetch('/api/port-maps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({local_port,target,remark})})
 const d=await r.json()
 if(d.success){showToast('添加成功','success');closeModal();loadPortMaps()}
 else showToast(d.detail||'添加失败','error')
}

function editPortMap(i){
 const m=portMaps[i]
 if(!m) return
 $('modalTitle').textContent='✏️ 编辑端口映射'
 $('modalBody').innerHTML=`
  <div style="display:flex;gap:8px;align-items:flex-end">
   <div class="form-group" style="flex:1"><label>监听端口</label><input id="editLocalPort" type="number" value="${escapeHtml(m.local_port||'')}"></div>
   <div class="form-group" style="flex:1"><label>转发地址</label><input id="editTargetHost" value="${escapeHtml((m.target||'').split(':')[0]||'')}"></div>
   <div class="form-group" style="flex:1"><label>转发端口</label><input id="editTargetPort" type="number" value="${escapeHtml((m.target||'').split(':')[1]||'')}"></div>
  </div>
  <div class="form-group" style="margin-top:8px"><label>备注</label><input id="editRemark" value="${escapeHtml(m.remark||'')}"></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmEditPortMap(${i})">💾 保存</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmEditPortMap(i){
 const local_port=$('editLocalPort').value.trim()
 const host=$('editTargetHost').value.trim()
 const port=$('editTargetPort').value.trim()
 if(!local_port||!host||!port){showToast('请填写完整','error');return}
 const target=host+':'+port
 const remark=$('editRemark').value.trim()
 const r=await fetch('/api/port-maps/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({local_port,target,remark})})
 const d=await r.json()
 if(d.success){showToast('保存成功','success');closeModal();loadPortMaps()}
 else showToast(d.detail||'保存失败','error')
}
