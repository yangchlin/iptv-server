// ==================== 全局状态 ====================
let channels = [];
let epgSources = [];
let epgExtracts = [];
let settings = {};
let currentM3uFile = null;
let currentM3uEntries = [];
let _m3uOptions = [];
let _m3uIconFiles = [];
let currentPage = 1;
let downloading = false;
let downloadAutoRefresh = null;

// ==================== 工具函数 ====================
function $(id){return document.getElementById(id)}
function qs(s){return document.querySelector(s)}
function qsa(s){return document.querySelectorAll(s)}

// ★ 修复：补单引号转义，避免 onclick="fn('...')" 属性注入
function escapeHtml(s){
  if(!s&&s!==0) return '';
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

// ★ 新增：安全地把任意字符串作为 JS 参数嵌入 onclick / onchange
// 双重保护：先 JSON.stringify（内部引号、反斜杠都转义），再 HTML 属性转义
// 用法：onclick="fn(${jsArg(x)})"  →  onclick="fn(&quot;xxx&quot;)"
function jsArg(x){
  return escapeHtml(JSON.stringify(String(x == null ? '' : x)));
}

function showToast(msg,type){
 const d=document.createElement('div');
 // 改为固定居中显示，顶部留出 70px 避开你的深色头部，增加丝滑动画
 d.style.cssText=`position:fixed;top:70px;left:50%;transform:translateX(-50%) translateY(-20px);padding:12px 24px;border-radius:8px;z-index:10000;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.12);opacity:0;transition:all .3s cubic-bezier(0.16,1,0.3,1);
 background:${type==='error'?'#fff2f0':type==='success'?'#f6ffed':'#fff7e6'};
 color:${type==='error'?'#ff4d4f':type==='success'?'#52c41a':'#d46b08'};
 border:1px solid ${type==='error'?'#ffccc7':type==='success'?'#b7eb8f':'#ffe58f'};
 display:flex;align-items:center;gap:8px;font-weight:500;`;
 
 // 增加对应类型的 Emoji 图标，更显眼
 const icon = type === 'error' ? '❌' : (type === 'success' ? '✅' : 'ℹ️');
 d.innerHTML = `<span style="font-size:16px">${icon}</span><span>${escapeHtml(msg)}</span>`;

 document.body.appendChild(d);
 
 // 触发滑入动画
 requestAnimationFrame(() => {
   d.style.transform = 'translateX(-50%) translateY(0)';
   d.style.opacity = '1';
 });

 // 3秒后触发滑出消失动画
 setTimeout(()=>{
   d.style.opacity='0';
   d.style.transform='translateX(-50%) translateY(-20px)';
   setTimeout(()=>d.remove(),300)
 },3000)
}

// ==================== 自定义确认弹窗 ====================
// 替代原生的 confirm()，避免被浏览器拦截，风格更统一
function customConfirm(msg, title = '确认操作', okText = '确定', cancelText = '取消') {
  return new Promise((resolve) => {
    // 保存原有的内容以便恢复（防止弹窗冲突）
    const oldTitle = $('modalTitle').textContent;
    const oldBody = $('modalBody').innerHTML;
    const oldActions = $('modalActions').innerHTML;
    
    // 设置新的弹窗内容
    $('modalTitle').textContent = title;
    $('modalBody').innerHTML = `<div style="padding: 10px 0; font-size: 14px; line-height: 1.6; color: var(--text);">${escapeHtml(msg).replace(/\n/g, '<br>')}</div>`;
    $('modalActions').innerHTML = `
      <button class="btn" id="customConfirmCancel">${cancelText}</button>
      <button class="btn btn-primary" id="customConfirmOk">${okText}</button>
    `;
    
    // 显示弹窗
    $('modal').classList.add('active');
    
    const cleanup = () => {
      $('modal').classList.remove('active');
      // 恢复原状态
      $('modalTitle').textContent = oldTitle;
      $('modalBody').innerHTML = oldBody;
      $('modalActions').innerHTML = oldActions;
    };

    // 绑定按钮事件
    $('customConfirmOk').onclick = () => { cleanup(); resolve(true); };
    $('customConfirmCancel').onclick = () => { cleanup(); resolve(false); };
    
    // 点击遮罩层也可以关闭（取消）
    $('modal').onclick = (e) => {
      if (e.target === $('modal')) { cleanup(); resolve(false); }
    };
  });
}

// ==================== 列宽拖拽（所有表格通用） ====================
// 任意表格（包括 JS 动态生成的）表头都会自动出现拖拽手柄：
//   · 拖动 = 调整该列与右侧列的宽度
//   · 松手写入 data/config/column_widths.json，刷新后保持
let _colWidths = {}
let _colWidthsLoaded = false

function tableKey(tbl){
 if(!tbl) return ''
 if(tbl.id) return tbl.id
 if(!tbl.dataset.ckey){
  const panel = tbl.closest('.panel')
  const scope = panel || document
  const idx = Array.from(scope.querySelectorAll('table')).indexOf(tbl)
  tbl.dataset.ckey = (panel ? panel.id : 'page') + '_tbl' + Math.max(0, idx + 1)
 }
 return tbl.dataset.ckey
}

function colKey(tbl, th){
 const text = (th.textContent || '').trim().substring(0, 20)
 const idx = th.parentElement ? Array.from(th.parentElement.children).indexOf(th) : 0
 return tableKey(tbl) + '_' + (text || ('#' + idx))
}

function applyColWidths(table,i1,i2,cw,nw,sw,nsw){
 const MIN=50
 const total=sw+nsw
 cw=Math.max(MIN,Math.min(cw,total-MIN))
 nw=total-cw
 table.querySelectorAll('tr').forEach(tr=>{
  const cur=tr.children[i1]
  if(cur){cur.style.width=cw+'px';cur.style.minWidth=cw+'px';cur.style.maxWidth=cw+'px'}
  const nxt=tr.children[i2]
  if(nxt){nxt.style.width=nw+'px';nxt.style.minWidth=nw+'px';nxt.style.maxWidth=nw+'px'}
 })
}

// 把保存过的列宽套到表头和每一行的单元格上
function applyStoredWidth(tbl, th){
 const w = _colWidths[colKey(tbl, th)]
 if(!(w > 20)) return
 th.style.width = w + 'px'
 th.style.minWidth = w + 'px'
 th.style.maxWidth = w + 'px'
 const idx = Array.from(th.parentElement.children).indexOf(th)
 tbl.querySelectorAll('tr').forEach(tr=>{
  const cell = tr.children[idx]
  if(cell && cell !== th){
   cell.style.width = w + 'px'
   cell.style.minWidth = w + 'px'
   cell.style.maxWidth = w + 'px'
  }
 })
}

function applyTableColWidths(tbl){
 if(!tbl) return
 tbl.querySelectorAll('thead th').forEach(th=>applyStoredWidth(tbl, th))
}

function applyAllColWidths(){ document.querySelectorAll('table').forEach(applyTableColWidths) }

// 给所有表头补拖拽手柄（已有的跳过，可重复调用）
function ensureResizeHandles(root){
 ;(root || document).querySelectorAll('table').forEach(tbl=>{
  const heads = tbl.querySelectorAll('thead tr:last-child th')
  heads.forEach((th, i)=>{
   if(i >= heads.length - 1) return                 // 最后一列右边没有下一列可分配
   if(th.querySelector('.resize-handle')) return
   const span = document.createElement('span')
   span.className = 'resize-handle'
   th.appendChild(span)
  })
  applyTableColumnOrder(tbl)      // 先恢复列顺序，再按（跟着列走的）列宽设置
  applyTableColWidths(tbl)
 })
}

// 事件委托：静态表 / 后渲染的表都用同一套拖拽逻辑
document.addEventListener('mousedown', function(e){
 const handle = e.target && e.target.closest ? e.target.closest('th > .resize-handle') : null
 if(!handle) return
 e.preventDefault()
 const th = handle.parentElement
 const table = th ? th.closest('table') : null
 if(!table) return
 const row = th.parentElement
 const idx = Array.from(row.children).indexOf(th)
 // 跳过隐藏列（M3U 的「频道分组 / 频道图标」），否则拖拽时宽度会越拖越窄
 let nIdx = idx + 1
 while(nIdx < row.children.length && row.children[nIdx].offsetWidth === 0) nIdx++
 if(nIdx >= row.children.length) return
 const nextTh = row.children[nIdx]
 const startX = e.clientX
 const startW = th.offsetWidth
 const nextStartW = nextTh.offsetWidth
 th.classList.add('resize-active')
 function onMouseMove(ev){
  applyColWidths(table, idx, nIdx, startW + (ev.clientX - startX), nextStartW - (ev.clientX - startX), startW, nextStartW)
 }
 function onMouseUp(){
  th.classList.remove('resize-active')
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  _colWidths[colKey(table, th)] = parseInt(th.style.width) || startW
  const nw = parseInt(nextTh.style.width)
  if(nw > 20) _colWidths[colKey(table, nextTh)] = nw
  saveColumnWidths()
 }
 document.addEventListener('mousemove', onMouseMove)
 document.addEventListener('mouseup', onMouseUp)
})

// 动态渲染出来的表格（M3U 文件列表、电信频道表等）一出现就补手柄 + 恢复列宽
let _resizeScanTimer = null
function scheduleResizeScan(){
 if(_resizeScanTimer) return
 _resizeScanTimer = setTimeout(function(){
  _resizeScanTimer = null
  try{ ensureResizeHandles() }catch(e){}
 }, 120)
}
if(typeof MutationObserver !== 'undefined'){
 try{
  new MutationObserver(scheduleResizeScan).observe(document.body, { childList: true, subtree: true })
 }catch(e){}
}
if(document.readyState === 'loading'){
 document.addEventListener('DOMContentLoaded', function(){ ensureResizeHandles() })
}else{
 try{ ensureResizeHandles() }catch(e){}
}

// ==================== 列顺序（拖动表头调整前后位置） ====================
// 按住表头横向拖动 = 把这一列插到目标列的位置（整张表所有行一起动），
// 松手后写入 data/config/column_order.json，刷新 / 重新渲染后保持。
let _colOrder = {}
let _colOrderLoaded = false
let _colNatural = {}      // 每张表的“自然列序”（首次见到、还没被搬动时记下来）

function colName(th){
 return ((th && th.textContent) || '').trim().substring(0, 20)
}

function headerNames(tbl){
 const headRow = tbl && tbl.querySelector('thead tr:last-child')
 return headRow ? Array.from(headRow.children).map(colName) : null
}

// 把一张表的所有行按 order（列名数组）重排；返回是否真的搬动了 DOM。
// 每行用 data-col-order 记住自己当前是哪种顺序，这样：
//   · 新渲染出来的 <tbody>（自然顺序）能被正确重排
//   · 已经排好的行不会重复搬动（避免走进观察器死循环）
function applyColumnOrder(tbl, order){
 if(!tbl) return false
 const key = tableKey(tbl)
 const want = order || _colOrder[key]
 if(!want || !want.length) return false
 const names = headerNames(tbl)
 if(!names) return false
 if(!_colNatural[key]) _colNatural[key] = names.slice()   // 首次见到即自然顺序
 const nat = _colNatural[key]
 if(nat.length !== want.length) return false               // 列数变了（版本升级），先不套
 const sig = want.join('\u0001')
 let moved = false
 tbl.querySelectorAll('tr').forEach(tr=>{
  if(tr.children.length !== nat.length) return            // 空态行（colspan）等跳过
  const cur = tr.dataset.colOrder ? tr.dataset.colOrder.split('\u0001') : nat
  const idxs = want.map(n => cur.indexOf(n))
  if(idxs.some(i => i < 0)) return
  tr.dataset.colOrder = sig
  if(idxs.every((v, i) => v === i)) return                 // 这一行已经是目标顺序
  const cells = Array.from(tr.children)
  const frag = document.createDocumentFragment()
  idxs.forEach(i=>frag.appendChild(cells[i]))
  tr.appendChild(frag)
  moved = true
 })
 return moved
}

function applyTableColumnOrder(tbl){ try{ return applyColumnOrder(tbl) }catch(e){ return false } }
function applyAllColumnOrders(){ document.querySelectorAll('table').forEach(applyTableColumnOrder) }

async function saveColumnOrder(){
 if(!_colOrderLoaded) return
 try{
  await fetch('/api/column-order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(_colOrder)})
 }catch(e){}
}

async function loadColumnOrder(){
 try{
  const r=await fetch('/api/column-order');const d=await r.json()
  if(d && typeof d==='object' && !Array.isArray(d)) _colOrder=d
  _colOrderLoaded=true
 }catch(e){
  _colOrderLoaded=false
  return
 }
 applyAllColumnOrders()
}

let _colDrag = null

document.addEventListener('mousedown', function(e){
 if(e.button !== 0) return
 const th = e.target && e.target.closest ? e.target.closest('thead th') : null
 if(!th) return
 if(e.target.closest('.resize-handle')) return                       // 拖边=改宽度
 if(e.target.closest('input,select,button,a,textarea,label')) return  // 表头里有控件就不抢
 const table = th.closest('table')
 if(!table) return
 _colDrag = { table: table, th: th, startX: e.clientX, startY: e.clientY, active: false }
 document.addEventListener('mousemove', onColDragMove)
 document.addEventListener('mouseup', onColDragEnd)
})

function onColDragMove(e){
 const d = _colDrag
 if(!d) return
 if(!d.active){
  if(Math.abs(e.clientX - d.startX) < 8) return
  d.active = true
  d.th.classList.add('col-drag-src')
  document.body.classList.add('col-dragging')
 }
 const heads = Array.from(d.table.querySelectorAll('thead tr:last-child th'))
 heads.forEach(th=>{
  if(th === d.th){ th.classList.remove('col-drag-over'); return }
  const r = th.getBoundingClientRect()
  const hot = e.clientX >= r.left && e.clientX <= r.right
  th.classList.toggle('col-drag-over', hot)
 })
}

function onColDragEnd(e){
 document.removeEventListener('mousemove', onColDragMove)
 document.removeEventListener('mouseup', onColDragEnd)
 const d = _colDrag
 _colDrag = null
 if(!d) return
 d.th.classList.remove('col-drag-src')
 document.body.classList.remove('col-dragging')
 const heads = Array.from(d.table.querySelectorAll('thead tr:last-child th'))
 heads.forEach(th=>th.classList.remove('col-drag-over'))
 if(!d.active) return

 const fromIdx = heads.indexOf(d.th)
 let toIdx = -1
 heads.forEach((th, i)=>{
  const r = th.getBoundingClientRect()
  if(e.clientX >= r.left && e.clientX <= r.right) toIdx = i
 })
 if(toIdx < 0) toIdx = e.clientX < (heads[0] ? heads[0].getBoundingClientRect().left : 0) ? 0 : heads.length - 1
 if(toIdx === fromIdx) return

 const names = heads.map(colName)
 const moved = names.splice(fromIdx, 1)[0]
 names.splice(toIdx, 0, moved)
 applyColumnOrder(d.table, names)
 _colOrder[tableKey(d.table)] = names
 saveColumnOrder()
 showToast('列顺序已保存：' + colName(d.th), 'success')
}

// ==================== 表格行拖动高亮（所有带 ☰ 的可拖拽行） ====================
function cleanupRowDrag(){
 document.querySelectorAll('tr.drag-src,tr.drag-over').forEach(t=>t.classList.remove('drag-src','drag-over'))
 document.body.classList.remove('row-dragging')
}
document.addEventListener('dragstart',function(e){
 const tr=e.target&&e.target.closest?e.target.closest('tr'):null
 if(tr&&tr.getAttribute('draggable')==='true'){
  tr.classList.add('drag-src')
  document.body.classList.add('row-dragging')
  try{ e.dataTransfer.effectAllowed='move' }catch(_){}
 }
})
document.addEventListener('dragover',function(e){
 const tr=e.target&&e.target.closest?e.target.closest('tr'):null
 if(!tr||tr.getAttribute('draggable')!=='true')return
 if(tr.classList.contains('drag-src'))return
 document.querySelectorAll('tr.drag-over').forEach(t=>{if(t!==tr)t.classList.remove('drag-over')})
 tr.classList.add('drag-over')
})
document.addEventListener('dragleave',function(e){
 const tr=e.target&&e.target.closest?e.target.closest('tr'):null
 if(tr&&tr.classList.contains('drag-over'))tr.classList.remove('drag-over')
})
document.addEventListener('drop',cleanupRowDrag)
document.addEventListener('dragend',cleanupRowDrag)

async function saveColumnWidths(){
 // 没成功读到旧值时不写，避免把已经保存的其它表格列宽覆盖掉
 if(!_colWidthsLoaded) return
 try{
  await fetch('/api/column-widths',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(_colWidths)})
 }catch(e){}
}

async function loadColumnWidths(){
 try{
  const r=await fetch('/api/column-widths');const d=await r.json()
  if(d&&typeof d==='object') _colWidths=d
  _colWidthsLoaded=true
 }catch(e){
  _colWidthsLoaded=false
  return
 }
 ensureResizeHandles()
 applyAllColWidths()
}


// ==================== Tab切换 ====================
document.querySelectorAll('.tab').forEach(tab=>{
 tab.addEventListener('click',()=>{
  qsa('.tab').forEach(t=>t.classList.remove('active'))
  tab.classList.add('active')
  qsa('.panel').forEach(p=>p.classList.remove('active'))
  var targetPanel = $('panel-'+tab.dataset.tab)
  if (targetPanel) targetPanel.classList.add('active')
  
  localStorage.setItem('iptv_active_tab',tab.dataset.tab)

  try {
    if(tab.dataset.tab==='dashboard'){refreshStatus();refreshGlobalLogs().catch(()=>{})}
    if(tab.dataset.tab==='telecom'){if(window.loadTelecomSchedule)loadTelecomSchedule();if(window.refreshTelecomLog)refreshTelecomLog();refreshTelecomStatus()}
	if(tab.dataset.tab==='telecom_channels'){if(window.loadTelecomChannelsPage)loadTelecomChannelsPage();} // ★ 新增独立 Tab 加载
    if(tab.dataset.tab==='channels'){renderChannels();setTimeout(reapplyColWidths,50);refreshTelecomStatus()}
    if(tab.dataset.tab==='epg'){renderEpgSources();renderEpgExtracts();setTimeout(reapplyColWidths,100);refreshTelecomStatus();refreshEpgPickSummary();loadCronSettings()}
    if(tab.dataset.tab==='m3u'){renderM3uFiles();setTimeout(reapplyColWidths,50);loadM3uConfigPanel()}
    if(tab.dataset.tab==='epgchannels'){loadEpgChannels();setTimeout(reapplyColWidths,50)}
    if(tab.dataset.tab==='overview'){loadOverview()}
    if(tab.dataset.tab==='icons'){refreshIcons()}
    if(tab.dataset.tab==='sharedir'){loadShareFiles()}
    if(tab.dataset.tab==='auth'){loadAuthPage()}
  } catch(e) {
    console.error("页面加载时发生错误:", e);
  }
 })
})

// ==================== 状态刷新 ====================
async function refreshStatus(){
 try{
  const r=await fetch('/api/status');
  
  // ★ 新增：检查 HTTP 状态码
  if(!r.ok){
    if(r.status === 401) throw new Error('未登录或登录已过期');
    throw new Error('HTTP ' + r.status);
  }
  
  const d=await r.json()
  
  // ★ 只显示系统运行状态
  let sysStatus = d.downloading ? '⚡ 任务执行中' : '🟢 运行中'
  $('statusInfo').textContent = sysStatus
  
  $('clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false})
  if(d.stats){
   $('statChannels').textContent=d.stats.channel_total||'-'
   // ★ 删掉了 statJsonFiles 这一行，防止报错
   $('statIcons').textContent=d.stats.icon_count||'-'
   $('statXmlSize').textContent=d.stats.xml_size||'-'
  }
 }catch(e){
  // ★ 修改这里：显示真实错误原因
  let errMsg = e.message || '未知错误';
  if (/NetworkError|Failed to fetch|Load failed/i.test(errMsg)) {
    errMsg = '网络请求失败或已被拦截';
  }
  $('statusInfo').textContent='🔴 ' + e.message; 
 }
}



// ==================== 计时器 ====================
setInterval(refreshStatus,10000)
setInterval(()=>{$('clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false})},1000)

// ==================== XML直链 ====================
async function loadXmlLink(){
 try{
  const sR=await fetch('/api/settings');const sD=await sR.json()
  if(sD.xml_gz_enabled!==undefined) $('xmlGzToggle').checked=sD.xml_gz_enabled
  const r=await fetch('/api/xml/url');const d=await r.json()
  const base=d.url.replace('/epg.xml','')
  $('xmlLink').value=base+($('xmlGzToggle').checked?'/epg.xml.gz':'/epg.xml')
 }catch(e){$('xmlLink').value='无法获取'}
}

function toggleXmlGz(){
 const checked=$('xmlGzToggle').checked
 fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml_gz_enabled:checked})})
 const r=$('xmlLink')
 const base=r.value.replace(/\/epg\.xml(\.gz)?$/,'')
 r.value=base+(checked?'/epg.xml.gz':'/epg.xml')
}

loadXmlLink()

// ==================== 频道管理 ====================
async function loadChannels(){
 // 频道管理页已删除；这里只保留数据（EPG 管理编辑来源时会用到 channels）
 const r=await fetch('/api/channels');const d=await r.json()
 channels=d.channels||[]
}
let dragIdx=null
// ==================== 下载操作 ====================
async function startDownload(){
 if(downloading){showToast('已有任务在运行','error');return}
 const r=await fetch('/api/download/start',{method:'POST'})
 const d=await r.json()
 if(d.success){
  downloading=true
  showToast('下载任务已启动','success')
  startDownloadMonitor()
 }else showToast(d.error||'启动失败','error')
 refreshStatus()
}

async function downloadHistory(){
 if(downloading){showToast('已有任务在运行','error');return}
 const r=await fetch('/api/download/history',{method:'POST'})
 const d=await r.json()
 if(d.success){
  downloading=true
  showToast('历史下载任务已启动','success')
  startDownloadMonitor()
 }else showToast(d.error||'启动失败','error')
 refreshStatus()
}

async function extractEpgToJson(){
 if(downloading){showToast('已有任务在运行','error');return}
 showToast('正在下载EPG并提取...','success')
 const r=await fetch('/api/epg/extract-to-json',{method:'POST'})
 const d=await r.json()
 if(d.success){downloading=true;startDownloadMonitor();showToast('外部EPG提取已启动','success')}else{showToast(d.error||'启动失败','error')}
 refreshStatus()
}

function startDownloadMonitor(){
 if(downloadAutoRefresh)clearInterval(downloadAutoRefresh)
 downloadAutoRefresh=setInterval(pollDownloadProgress,1000)
}

async function pollDownloadProgress(){
 try{
  const r=await fetch('/api/download/progress');const d=await r.json()
  if(!d.running&&downloading){
   downloading=false
   if(downloadAutoRefresh){clearInterval(downloadAutoRefresh);downloadAutoRefresh=null}
   showToast('下载任务已完成','success')
   loadChannels()
   loadEpgSources()          // ← 新增：刷新 EPG 源的「已下载」状态
  }
 }catch(e){}
}

// ==================== 全局进度监控 ====================
// 注意：这里绝不能切换 #globalProgress 的 display —— 它参与正常文档流，
// 一隐一显会把下面整块内容顶上去，就是「页面上下抖动」的原因。
// 只改文案和宽度，元素高度由 CSS 的 min-height 固定。
function clearGlobalProgress(){
 const f=$('gpFill'), p=$('gpPct'), t=$('gpTitle')
 if(f) f.style.width='0%'
 if(p) p.textContent=''
 if(t) t.textContent='电信任务进行中 · 进度见「电信EPG」页执行日志'
}
window.clearGlobalProgress = clearGlobalProgress

async function pollGlobalProgress(){
 try{
  const r=await fetch('/api/download/progress');const d=await r.json()
  const running=d.running
  const msgs=d.messages||[]
  if(running&&msgs.length){
   const last=msgs[msgs.length-1]
   const pct=Math.min(100,Math.round((d.current.current||0)/Math.max(1,d.current.total||1)*100))
   $('gpFill').style.width=pct+'%'
   $('gpPct').textContent=pct+'%'
   const title=last.replace(/^\[[^\]]+\]\s*/,'')
   $('gpTitle').textContent=title
  }else if(running){
   // running 但没有 messages = 电信任务（它不写全局进度）
   clearGlobalProgress()
  }else{
   $('gpFill').style.width='0%'
   $('gpPct').textContent=''
   let line=''
   try{
    const lr=await fetch('/api/logs/global?limit=1');const ld=await lr.json()
    const entries=ld.entries||[]
    if(entries.length){
     line=entries[0].line||('['+entries[0].timestamp+'] '+entries[0].message)
    }
   }catch(e){}
   // 电信日志不上顶部条；用固定文案占位，保证高度不变
   if(line && line.indexOf('[电信]')===-1){
    $('gpTitle').textContent=line.replace(/\n/g,' ').substring(0,120)
   }else{
    $('gpTitle').textContent='就绪'
   }
  }
 }catch(e){}
}

setInterval(pollGlobalProgress,1000)

// ==================== XML构建 ====================
async function buildXml(){
 if(downloading){showToast('已有任务在运行','error');return}
 downloading=true
 const r=await fetch('/api/xml/build',{method:'POST'})
 const d=await r.json()
 downloading=false
 if(d.success){
  showToast(`XML生成成功: ${d.channel_count||0}频道, ${d.programme_count||0}节目`,'success')
  loadXmlLink()
 } else showToast('XML生成失败: '+(d.error||'未知错误'),'error')
 refreshStatus()
}

async function manualCleanup(){
 if(downloading){showToast('已有任务在运行','error');return}
 downloading=true
 const r=await fetch('/api/cleanup',{method:'POST'})
 const d=await r.json()
 downloading=false
 if(d.logs&&d.logs.length){
  showToast(`清理完成，共${d.logs.length}条记录`,'success')
 }else{
  showToast('清理完成，无需清理','success')
 }
 refreshGlobalLogs()
 switchLogCategory('清理')}
 refreshStatus()

// ==================== 外部EPG源 ====================
async function loadEpgSources(){
 const r=await fetch('/api/epg/sources');const d=await r.json()
 epgSources=d.sources||[]
 renderEpgSources()
}

function renderEpgSources(){
 const tbody=$('epgSourceBody')
 const total=epgSources.length
 const totalPages=Math.ceil(total/EPG_SOURCE_PAGE_SIZE)||1
 if(epgSourcePage>totalPages)epgSourcePage=totalPages
 const start=(epgSourcePage-1)*EPG_SOURCE_PAGE_SIZE
 const page=epgSources.slice(start,start+EPG_SOURCE_PAGE_SIZE)
 if(!total){tbody.innerHTML='<tr><td colspan="6" class="empty">暂无 EPG 源 —— 点「➕ 添加源」，或直接编辑 <code>data/config/app_config.json</code> 里的 <code>epg_sources</code></td></tr>';$('epgSourcePagination').innerHTML='';return}
 tbody.innerHTML=page.map((s,i)=>{
  const realIdx=start+i
  return `<tr draggable="true"
    ondragstart="epgSrcDragStart(event,${realIdx})"
    ondrop="epgSrcDrop(event,${realIdx})"
    ondragover="event.preventDefault()">
   <td style="cursor:grab;text-align:center">☰</td>
   <td style="word-break:break-all;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(s.url)}">${escapeHtml(s.url)}</td>
   <td><input value="${escapeHtml(s.remark||'')}" style="border:none;background:transparent;width:100%" onchange="updateEpgSourceRemark(${realIdx},this.value)">${s.downloaded?'':' <span class="badge badge-danger" title="该源还没下载缓存，搜索不到里面的频道；点「⬇️ 下载EPG」">未下载</span>'}${s.parked?' <span class="badge badge-danger" style="opacity:.75" title="源已禁用，缓存改名挂起，不参与搜索/提取/合并">已挂起</span>':''}
    <div style="font-size:11px;color:var(--text-secondary);word-break:break-all" title="缓存文件名（可在 ✏️ 里改）">缓存：${escapeHtml(s.cache_file||s.cache_target||s.cache_name||'—')}</div></td>
   <td style="text-align:center"><span class="badge badge-success">${escapeHtml(s.rule||'auto')}</span></td>
   <td style="text-align:center"><button class="badge ${s.enabled!==false?'badge-success':'badge-danger'}" style="border:0;cursor:pointer" onclick="toggleEpgSource(${realIdx})" title="点击切换 启用 / 禁用">${s.enabled!==false?'启用':'禁用'}</button></td>
   <td style="text-align:center;white-space:nowrap">
   <button class="btn btn-sm" onclick="downloadSingleEpgSource(${realIdx})"
         title="下载该源（下载后自动合并，状态会变为已下载）">⬇️</button>
   <button class="btn btn-sm" onclick="editEpgSource(${realIdx})">✏️</button>
   <button class="btn btn-sm btn-danger" onclick="deleteEpgSource(${realIdx})">✕</button>
   </td>
  </tr>`
 }).join('')
 const pag=$('epgSourcePagination')
 pag.innerHTML=`<span style="font-size:11px;color:var(--text-secondary);margin-right:8px">共${total}条</span>
  <button onclick="epgSourcePage=1;renderEpgSources()" ${epgSourcePage<=1?'disabled':''}>«</button>
  <button onclick="epgSourcePage=Math.max(1,epgSourcePage-1);renderEpgSources()" ${epgSourcePage<=1?'disabled':''}>‹</button>
  <span style="padding:0 8px;font-size:12px">${epgSourcePage}/${totalPages}</span>
  <button onclick="epgSourcePage=Math.min(totalPages,epgSourcePage+1);renderEpgSources()" ${epgSourcePage>=totalPages?'disabled':''}>›</button>
  <button onclick="epgSourcePage=totalPages;renderEpgSources()" ${epgSourcePage>=totalPages?'disabled':''}>»</button>`
}

function showAddEpgSource(){
 $('modalTitle').textContent='➕ 添加EPG源'
 $('modalBody').innerHTML=`
  <div class="form-group"><label>URL</label><input id="epgSourceUrl" placeholder="http://...xml"></div>
  <div class="form-group"><label>备注</label><input id="epgSourceRemark" placeholder="例如: 51zmt EPG"></div>
  <div class="form-group"><label>保存文件名（可选）</label><input id="epgSourceCache" placeholder="留空自动：域名_文件名_短哈希.xml">
   <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">两个源的 URL 最后一段同名（比如都叫 txml.gz）时，留空也会自动区分，不会互相覆盖；想自己起名就填这里。</div></div>
  <div class="form-group"><label>解析规则</label>
   <select id="epgSourceRule">
    <option value="auto">🔄 自动检测</option>
    <option value="112114">112114 规则</option>
    <option value="51zmt">51zmt 规则</option>
   </select></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmAddEpgSource()">✅ 添加</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmAddEpgSource(){
 const url=$('epgSourceUrl').value.trim()
 const remark=$('epgSourceRemark').value.trim()
 const rule=$('epgSourceRule').value
 const cache_name=$('epgSourceCache').value.trim()
 if(!url){showToast('请输入URL','error');return}
 const r=await fetch('/api/epg/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,remark,rule,cache_name})})
 const d=await r.json()
 if(d.success){showToast('添加成功','success');closeModal();loadEpgSources()}
 else showToast(d.error||'添加失败','error')
}

// 状态列：点一下切换启用 / 禁用（禁用后「下载EPG」和定时任务都会跳过它）
async function toggleEpgSource(i){
 const s=epgSources[i]
 if(!s)return
 const next=s.enabled===false
 s.enabled=next
 renderEpgSources()
 try{
  const r=await fetch('/api/epg/sources/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:next})})
  const d=await r.json()
  if(!r.ok||d.success===false) throw new Error(d.error||('HTTP '+r.status))
  showToast(next?'已启用：'+(s.remark||s.url):'已禁用：'+(s.remark||s.url),'success')
 }catch(e){
  s.enabled=!next
  renderEpgSources()
  showToast('切换失败: '+e.message,'error')
 }
}

function editEpgSource(i){
 const src=epgSources[i]
 if(!src)return
 $('modalTitle').textContent='✏️ 编辑EPG源'
 $('modalBody').innerHTML=`
  <div class="form-group"><label>URL</label><input id="editEpgUrl" value="${escapeHtml(src.url)}"></div>
  <div class="form-group"><label>备注</label><input id="editEpgRemark" value="${escapeHtml(src.remark||'')}"></div>
  <div class="form-group"><label>保存文件名（可选）</label><input id="editEpgCache" value="${escapeHtml(src.cache_name||'')}" placeholder="留空自动：域名_文件名_短哈希.xml">
   <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">当前缓存：${escapeHtml(src.cache_file||src.cache_target||'（还没下载）')}；改完名字后重新「⬇️ 下载EPG」才会用新名字。</div></div>
  <div class="form-group"><label>规则</label><select id="editEpgRule">
   <option value="auto" ${src.rule==='auto'?'selected':''}>🔄 自动</option>
   <option value="112114" ${src.rule==='112114'?'selected':''}>112114</option>
   <option value="51zmt" ${src.rule==='51zmt'?'selected':''}>51zmt</option>
  </select></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmEditEpgSource(${i})">💾 保存</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmEditEpgSource(i){
 const url=$('editEpgUrl').value.trim()
 const remark=$('editEpgRemark').value.trim()
 const rule=$('editEpgRule').value
 const cache_name=$('editEpgCache').value.trim()
 if(!url){showToast('URL不能为空','error');return}
 const r=await fetch('/api/epg/sources/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,remark,rule,cache_name})})
 const d=await r.json()
 if(d.success){showToast('保存成功','success');closeModal();loadEpgSources()}
 else showToast('保存失败','error')
}

async function updateEpgSourceRemark(i,val){
 if(i<epgSources.length){epgSources[i].remark=val}
 await fetch('/api/epg/sources/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({remark:val})})
}
let epgSrcDragIdx=null
function epgSrcDragStart(e,i){epgSrcDragIdx=i;e.dataTransfer.effectAllowed='move'}
async function epgSrcDrop(e,i){
 e.preventDefault()
 if(epgSrcDragIdx===null||epgSrcDragIdx===i)return
 const item=epgSources.splice(epgSrcDragIdx,1)[0]
 epgSources.splice(epgSrcDragIdx<i?i-1:i,0,item)
 epgSrcDragIdx=null
 renderEpgSources()
}

async function deleteEpgSource(i){
 if(!await customConfirm('确定删除该EPG源？'))return
 await fetch(`/api/epg/sources/${i}`,{method:'DELETE'})
 loadEpgSources()
}

async function downloadEpg(){
 const r=await fetch('/api/epg/download',{method:'POST'})
 const d=await r.json()
 if(d.success){
  showToast('EPG下载已启动','success')
  downloading=true
  startDownloadMonitor()
 }
}

async function downloadSingleEpgSource(i){
  const s = epgSources[i]
  if(!s) return
  if(s.enabled === false){ showToast('该源已禁用，请先启用再下载','error'); return }
  if(downloading){ showToast('已有任务在运行','error'); return }
  try{
    const r = await fetch('/api/epg/sources/'+i+'/download', {method:'POST'})
    const d = await r.json()
    if(d.success){
      showToast('该源下载已启动','success')
      downloading = true
      startDownloadMonitor()
    } else {
      showToast(d.error || d.message || '启动失败','error')
    }
  } catch(e){ showToast('启动失败: '+e.message,'error') }
}

// ==================== EPG提取记录 ====================
async function loadEpgExtracts(){
 const [r1,r2]=await Promise.all([
  fetch('/api/epg/extracts').then(r=>r.json()),
  fetch('/api/epg-channels').then(r=>r.json())
 ])
 epgExtracts=r1.extracts||[]
 epgChList=r2.channels||[]
 epgChMap={}
 epgChList.forEach(c=>{if(c.logic_id)epgChMap[c.logic_id]=c.name})
 renderEpgExtracts()
 
 // 若当前正在 EPG 管理页面，同步刷新列表以呈现最新的并入数据
 const p=$('panel-epgchannels')
 if(p && p.classList.contains('active')) renderEpgChannels()
}

// ---- 外部 EPG 频道名 → EPG 频道 的模糊匹配 ----
function extractTargetName(e){
 const tid=e.target_logic_id||''
 if(!tid)return ''
 return epgChMap[tid]||'(目标频道已删除)'
}
function epgTargetOptions(cur){
 const opts=['<option value="">独立频道（不并入，单独建一条）</option>']
 for(const c of (epgChList||[])){
  const lid=c.logic_id||''
  if(!lid)continue
  
  // ★ 新增：提取来源标签
  let srcTag = '';
  const st = c.source_type || '';
  if (st === 'telecom') srcTag = '[电信] ';
  else if (st === 'manual') srcTag = '[手动] ';
  else if (st === 'extract') srcTag = '[' + (c.source_remark || '外部EPG') + '] ';
  else if (st === 'channel') srcTag = '[自建] ';
  else srcTag = '[' + (c.source_remark || '未知') + '] ';

  const mark=(lid===cur)?'selected':''
  // ★ 修改：将来源标签加在最前面
  opts.push(`<option value="${escapeHtml(lid)}" ${mark}>${escapeHtml(srcTag)}${escapeHtml(c.name)}${c.tvg_id?' · tvg-id '+escapeHtml(c.tvg_id):''}</option>`)
 }
 return opts.join('')
}

window.filterEpgTargetOptions = function(kw){
  kw = String(kw||'').toLowerCase().trim();
  const sel = $('editExtTarget');
  if(!sel) return;
  const cur = sel.value; // 保持当前已经选中的值
  const opts = ['<option value="">独立频道（不并入，单独建一条）</option>'];
  
  for (const c of (epgChList || [])) {
    const lid = c.logic_id || '';
    if (!lid) continue;

    // ★ 新增：提取来源标签
    let srcTag = '';
    const st = c.source_type || '';
    if (st === 'telecom') srcTag = '[电信] ';
    else if (st === 'manual') srcTag = '[手动] ';
    else if (st === 'extract') srcTag = '[' + (c.source_remark || '外部EPG') + '] ';
    else if (st === 'channel') srcTag = '[自建] ';
    else srcTag = '[' + (c.source_remark || '未知') + '] ';

    const txt = srcTag + c.name + (c.tvg_id ? ' · tvg-id ' + c.tvg_id : '');
    
    // 如果没输入关键字，或者关键字匹配，或者这条是当前选中的，就显示
    if (kw && txt.toLowerCase().indexOf(kw) < 0 && lid !== cur) continue;
    const mark = (lid === cur) ? 'selected' : '';
    opts.push(`<option value="${escapeHtml(lid)}" ${mark}>${escapeHtml(txt)}</option>`);
  }
  sel.innerHTML = opts.join('');
  // 恢复选中状态
  if (sel.value !== cur) sel.value = cur;
}

function getEpgSourceRemark(sourceUrl){
 if(!sourceUrl||!epgSources)return ''
 const src=epgSources.find(s=>sourceUrl.includes(s.url.split('/').pop().replace('.gz','')))
 return src?src.remark||src.url.split('/').pop().replace('.gz','').replace('.xml',''):''
}

function showEpgExtractModal(){
 const m=$('epgExtractModal')
 if(!m) return
 m.classList.add('active')
 switchEpgExtractTab('list')
 loadEpgExtracts()
}

function closeEpgExtractModal(){
 const m=$('epgExtractModal')
 if(m) m.classList.remove('active')
}

// ==================== 源频道勾选（批量添加到 EPG 管理） ====================
let epgPickChannels=[]
let epgPickSel=new Map()
let epgPickPage=1
let epgPickSources=[]
const EPG_PICK_PAGE_SIZE=100

function epgPickKey(c){return (c.name||'')+'\u0001'+(c.source_url||'')}

function epgSourceLabel(u, fallback){
 // ★ 修复：后端 /api/epg/channels 已经按「缓存文件 → 源」精确算好了 source_name，
 //   直接用它；只有拿不到时才退化成 URL 尾部，避免前端再次做模糊匹配。
 if(fallback) return fallback
 const tail = String(u||'').split('/').pop().replace(/\.gz$/,'').replace(/\.xml$/,'')
 return tail || u || '外部EPG'
}

function switchEpgExtractTab(which){
 const pick=$('epgTabPick'), list=$('epgTabList')
 const pb=$('epgTabPickBtn'), lb=$('epgTabListBtn')
 if(!pick||!list) return
 const isPick=which!=='list'
 pick.style.display=isPick?'flex':'none'
 list.style.display=isPick?'none':'flex'
 if(pb) pb.className='btn btn-sm'+(isPick?' btn-primary':'')
 if(lb) lb.className='btn btn-sm'+(isPick?'':' btn-primary')
 if(isPick&&!epgPickChannels.length) loadEpgPicker()
}

function openEpgPicker(){
 const m=$('epgExtractModal')
 if(!m) return
 m.classList.add('active')
 switchEpgExtractTab('pick')
 loadEpgPicker()
}

async function loadEpgPicker(){
 try{
  const [r1,r2]=await Promise.all([
   fetch('/api/epg/channels').then(r=>r.json()),
   fetch('/api/epg/sources').then(r=>r.json())
  ])
  epgPickChannels=r1.channels||[]
  epgPickSources=r2.sources||[]
  buildEpgPickSourceSelect()
  renderEpgPicker()
  refreshEpgPickSummary()
 }catch(e){ showToast('加载频道表失败: '+(e.message||e),'error') }
}

function buildEpgPickSourceSelect(){
 const sel=$('epgPickSource'); if(!sel)return
 const cur=sel.value
 const opts=['<option value="">全部来源</option>']
 const seen=new Map()      // url → 显示名
 epgPickChannels.forEach(c=>{
  const u=c.source_url||''
  if(!u||seen.has(u))return
  seen.set(u, epgSourceLabel(u, c.source_name))
 })
 seen.forEach((label,u)=>{
  opts.push('<option value="'+escapeHtml(u)+'">'+escapeHtml(label)+'</option>')
 })
 sel.innerHTML=opts.join('')
 if(cur) sel.value=cur
}

function epgPickFiltered(){
 const src=$('epgPickSource')?$('epgPickSource').value:''
 const kw=($('epgPickSearch')?$('epgPickSearch').value:'').trim().toLowerCase()
 const onlyUnadded=!!($('epgPickUnadded')&&$('epgPickUnadded').checked)
 return epgPickChannels.filter(c=>{
  if(src&&(c.source_url||'')!==src)return false
  if(onlyUnadded&&c.added)return false
  if(kw){
   const hay=((c.name||'')+' '+(c.id||'')).toLowerCase()
   if(hay.indexOf(kw)<0)return false
  }
  return true
 })
}

function renderEpgPicker(){
 const tbody=$('epgPickBody'); if(!tbody)return
 const rows=epgPickFiltered()
 const totalPages=Math.ceil(rows.length/EPG_PICK_PAGE_SIZE)||1
 if(epgPickPage>totalPages)epgPickPage=totalPages
 const start=(epgPickPage-1)*EPG_PICK_PAGE_SIZE
 const page=rows.slice(start,start+EPG_PICK_PAGE_SIZE)

 const cnt=$('epgPickSelCount')
 if(cnt) cnt.textContent='已选 '+epgPickSel.size+' 个 · 共 '+rows.length+' 条'
 const box=$('epgPickAllBox')
 if(box) box.checked=page.length>0&&page.every(c=>epgPickSel.has(epgPickKey(c)))

 if(!page.length){
  tbody.innerHTML='<tr><td colspan="6" class="empty">'+
   (epgPickChannels.length?'没有匹配的频道':'还没有下载任何 EPG 源 —— 先点「⬇️ 下载EPG」')+'</td></tr>'
 }else{
  tbody.innerHTML=page.map(c=>{
   const key=epgPickKey(c)
   const checked=epgPickSel.has(key)?'checked':''
   let status='<span style="color:#999">未添加</span>'
   if(c.added) status=c.merged
     ?('<span class="badge badge-success">已并入</span> '+escapeHtml(c.target_name||''))
     :'<span class="badge badge-success">已添加</span>'
   return `<tr>
    <td style="text-align:center"><input type="checkbox" ${checked} style="width:auto" onchange="epgPickToggle(${jsArg(key)},this.checked)"></td>
    <td style="font-size:12px">${escapeHtml(epgSourceLabel(c.source_url,c.source_name))}</td>
    <td>${escapeHtml(c.name||c.id)}</td>
    <td style="font-size:12px;color:var(--text-secondary)">${escapeHtml(c.id||'')}</td>
    <td style="font-size:12px">${status}</td>
    <td style="text-align:center">${c.added?'':'<button class="btn btn-sm btn-primary" onclick="epgPickAddOne('+jsArg(key)+')">➕</button>'}</td>
   </tr>`
  }).join('')
 }

 const pag=$('epgPickPagination')
 if(pag){
  pag.innerHTML='<span style="font-size:11px;color:var(--text-secondary);margin-right:8px">第 '+epgPickPage+'/'+totalPages+' 页</span>'+
   '<button onclick="epgPickPage=1;renderEpgPicker()" '+(epgPickPage<=1?'disabled':'')+'>«</button>'+
   '<button onclick="epgPickPage=Math.max(1,epgPickPage-1);renderEpgPicker()" '+(epgPickPage<=1?'disabled':'')+'>‹</button>'+
   '<button onclick="epgPickPage=Math.min('+totalPages+',epgPickPage+1);renderEpgPicker()" '+(epgPickPage>=totalPages?'disabled':'')+'>›</button>'+
   '<button onclick="epgPickPage='+totalPages+';renderEpgPicker()" '+(epgPickPage>=totalPages?'disabled':'')+'>»</button>'
 }
}

function epgPickToggle(key,checked){
 const ch=epgPickChannels.find(c=>epgPickKey(c)===key)
 if(!ch)return
 if(checked)epgPickSel.set(key,ch); else epgPickSel.delete(key)
 renderEpgPicker()
}

function epgPickToggleAll(checked){
 const rows=epgPickFiltered().slice((epgPickPage-1)*EPG_PICK_PAGE_SIZE, epgPickPage*EPG_PICK_PAGE_SIZE)
 rows.forEach(c=>{
  const k=epgPickKey(c)
  if(checked)epgPickSel.set(k,c); else epgPickSel.delete(k)
 })
 renderEpgPicker()
}

function epgPickSelectPage(){epgPickToggleAll(true)}

function epgPickClearSel(){
 epgPickSel.clear()
 renderEpgPicker()
}

async function epgPickAddOne(key){
 const ch=epgPickChannels.find(c=>epgPickKey(c)===key)
 if(!ch)return
 await addChannelsToEpg([ch])
}

async function addPickedEpgChannels(){
 if(!epgPickSel.size){showToast('先勾选要添加的频道','error');return}
 await addChannelsToEpg([...epgPickSel.values()])
}

async function addChannelsToEpg(list){
 const items = list.map(c => {
   const cName = c.name || c.id || '';
   const cTvgId = c.id || '';
   
   // ★ 修改：三项任意一项匹配上，就视为已存在
   const existing = (epgChList || []).find(x => 
       x.name === cName || 
       x.tvg_id === cTvgId || 
       x.tvg_name === cName ||
       x.tvg_name === cTvgId
   );
   
   return {
     name: cName,
     source: epgSourceLabel(c.source_url, c.source_name) || '外部EPG',
     source_url: c.source_url || '',
     tvg_id: cTvgId,
     rule: c.rule || 'auto',
     target_logic_id: existing ? existing.logic_id : ''
   };
 }).filter(x => x.name);

 if(!items.length){showToast('没有可添加的频道','error');return}
 try{
  const r=await fetch('/api/epg/extracts/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})})
  const d=await r.json()
  if(!r.ok){showToast('添加失败: '+(d.detail||r.status),'error');return}
  showToast('已添加 '+d.added+' 个'+(d.skipped?('，跳过 '+d.skipped+' 个已存在'):''),'success')
  epgPickSel.clear()
  await loadEpgPicker()
  await loadEpgExtracts()
 }catch(e){ showToast('添加失败: '+(e.message||e),'error') }
}

async function refreshEpgPickSummary(){
 const el=$('epgPickSummary'); if(!el)return
 try{
  const [r1,r2]=await Promise.all([
   fetch('/api/epg/channels').then(r=>r.json()),
   fetch('/api/epg/extracts').then(r=>r.json())
  ])
  const chans=r1.channels||[]
  const exts=r2.extracts||[]
  const added=chans.filter(c=>c.added).length
  el.innerHTML='已下载源共 <b>'+(r1.total_channels||0)+'</b> 个频道 · 已添加 <b>'+added+'</b> 个 · 提取记录 <b>'+exts.length+'</b> 条'
 }catch(e){ el.textContent='统计失败（源还没下载？）' }
}

function renderEpgExtracts(){
 const tbody=$('epgExtractBody')
 if(!tbody) return
 const kwRaw=$('epgExtractSearch')?$('epgExtractSearch').value.trim().toLowerCase():''
 const onlyStandalone=!!($('epgExtractOnlyStandalone')&&$('epgExtractOnlyStandalone').checked)

 const rows=[]
 epgExtracts.forEach((e,i)=>{
  if(onlyStandalone&&e.target_logic_id)return
  if(kwRaw){
   const hay=((e.name||'')+' '+(e.tvg_id||'')+' '+(e.source||'')+' '+(e.rule||'')).toLowerCase()
   if(hay.indexOf(kwRaw)<0)return
  }
  rows.push({e,i})
 })

 const total=epgExtracts.length
 const cnt=$('epgExtractCount')
 if(cnt) cnt.textContent='共 '+total+' 条'
 const cnt2=$('epgExtractModalCount')
 if(cnt2) cnt2.textContent='（显示 '+rows.length+' / 共 '+total+' 条）'

 if(!rows.length){
  tbody.innerHTML='<tr><td colspan="6" class="empty">'+(total?'没有匹配的记录':'暂无提取记录（可在上方搜索频道后点 ➕ 添加）')+'</td></tr>'
  return
 }
 tbody.innerHTML=rows.map(({e,i})=>{
  const tgt=extractTargetName(e)
  return `<tr>
   <td style="cursor:grab;text-align:center">☰</td>
   <td>${escapeHtml(e.name)}${tgt?`<div style="font-size:11px;color:var(--success)">→ ${escapeHtml(tgt)}</div>`:''}</td>
   <td>${escapeHtml(e.tvg_id||'')}</td>
   <td>${escapeHtml(getEpgSourceRemark(e.source_url)||e.source||'')}</td>
   <td style="text-align:center"><span class="badge badge-success">${escapeHtml(e.rule||'auto')}</span></td>
   <td style="text-align:center;white-space:nowrap">
    <button class="btn btn-sm" onclick="editEpgExtract(${i})">✏️</button>
    <button class="btn btn-sm btn-danger" onclick="deleteEpgExtract(${i})">✕</button>
   </td>
  </tr>`}).join('')
}

function editEpgExtract(i){
 const ext=epgExtracts[i]
 if(!ext)return
 $('modalTitle').textContent='✏️ 编辑提取记录'
 $('modalBody').innerHTML=`
  <div class="form-group"><label>外部频道名（用于在 EPG 源里查找）</label><input id="editExtName" value="${escapeHtml(ext.name)}"></div>
  
  <div class="form-group">
    <label>并入到哪个 EPG 频道</label>
    <input type="text" id="editExtTargetKw" placeholder="输入关键字快速过滤下方选项..." style="margin-bottom: 6px; font-size: 12px; padding: 4px 8px;" oninput="filterEpgTargetOptions(this.value)">
    <select id="editExtTarget">${epgTargetOptions(ext.target_logic_id||'')}</select>
  </div>

  <div class="form-group"><label>来源EPG (频道ID/名称)</label><input id="editExtSource" value="${escapeHtml(ext.source)}"></div>
  <div class="form-group"><label>TVG-ID</label><input id="editExtTvgId" value="${escapeHtml(ext.tvg_id||'')}"></div>
  <div class="form-group"><label>规则</label><select id="editExtRule">
   <option value="auto" ${ext.rule==='auto'?'selected':''}>🔄 自动</option>
   <option value="112114" ${ext.rule==='112114'?'selected':''}>112114</option>
   <option value="51zmt" ${ext.rule==='51zmt'?'selected':''}>51zmt</option>
  </select></div>
  <div style="font-size:12px;color:var(--text-secondary)">选已有频道 = 该频道的节目单和它合并（电信优先生效）；选「独立频道」= 单独输出一个频道。</div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmEditEpgExtract(${i})">💾 保存</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmEditEpgExtract(i){
 const name=$('editExtName').value.trim()
 const source=$('editExtSource').value.trim()
 const tvg_id=$('editExtTvgId').value.trim()
 const rule=$('editExtRule').value
 const target_logic_id=$('editExtTarget')?$('editExtTarget').value:''
 if(!name||!source){showToast('名称和来源不能为空','error');return}
 const r=await fetch('/api/epg/extracts/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,source,tvg_id,rule,target_logic_id})})
 const d=await r.json()
 if(d.success){showToast('保存成功','success');closeModal();await loadEpgExtracts();await loadEpgChannels();await syncAllAfterChange()}
 else showToast('保存失败','error')
}

// 单条删除
async function deleteEpgExtract(i){
  if(!await customConfirm('确定要删除这条提取记录吗？\n（如果是独立频道，可去 EPG 管理页点「🧹 清理孤儿」删掉对应频道）'))return
  try {
    const r = await fetch('/api/epg/extracts/' + i, {method: 'DELETE'})
    const d = await r.json()
    if(d.success){
      showToast('删除成功', 'success')
      await loadEpgExtracts()
      await loadEpgChannels()
      await syncAllAfterChange()
    } else {
      showToast('删除失败: ' + (d.detail || '未知错误'), 'error')
    }
  } catch(e) {
    showToast('删除失败: ' + e.message, 'error')
  }
}

// 一键清空
async function clearAllEpgExtracts(){
  if(!await customConfirm('确定清空所有已配置的提取记录？\n（不会删除「EPG 管理」里的频道）'))return
  try {
    const r = await fetch('/api/epg/extracts/clear', {method:'POST'});
    const d = await r.json();
    if(d.success){
      showToast('已清空提取记录','success');
      await loadEpgExtracts();
      await syncAllAfterChange();
    } else {
      showToast('清空失败','error');
    }
  } catch(e){ showToast('清空失败: '+e.message,'error') }
}

async function exportEpgExtracts(){
 try{
  const r=await fetch('/api/epg/extracts');const d=await r.json()
  const blob=new Blob([JSON.stringify(d.extracts||[],null,2)],{type:'application/json'})
  const a=document.createElement('a');a.href=URL.createObjectURL(blob)
  a.download='extracts_'+(new Date().toISOString().slice(0,10))+'.json'
  document.body.appendChild(a);a.click();document.body.removeChild(a)
  URL.revokeObjectURL(a.href)
  showToast('导出成功','success')
 }catch(e){showToast('导出失败: '+e.message,'error')}
}

async function importEpgExtracts(ev){
 const file=ev.target.files[0]
 if(!file)return
 const text=await file.text()
 let data
 try{data=JSON.parse(text)}catch(e){showToast('JSON格式错误','error');return}
 const list=Array.isArray(data)?data:(data.extracts||[])
 if(!list.length){showToast('没有有效数据','error');return}
 const r=await fetch('/api/epg/extracts/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(list)})
 const d=await r.json()
 if(d.success){showToast('导入完成，新增 '+d.added+' 条','success');await loadEpgExtracts();await syncAllAfterChange()}
 else showToast('导入失败','error')
 ev.target.value=''
}

async function syncAllAfterChange(){
 await refreshEpgChannels()
 if(typeof _m3uOptions!=='undefined'){
  try{
   const r=await fetch('/api/m3u/channel-options?_='+Date.now());const d=await r.json()
   if(d.options) _m3uOptions=d.options
  }catch(e){}
  if($('m3uEditor')&&$('m3uEditor').style.display==='block') renderM3uTable(currentM3uEntries,_m3uOptions)
 }
}

// ==================== EPG搜索 ====================
// 表格重渲染后调用：补手柄 + 恢复列顺序 + 恢复列宽
function reapplyColWidths(){
 try{ ensureResizeHandles() }catch(e){}
 applyAllColumnOrders()
 applyAllColWidths()
}

// ==================== 频道管理 ====================
let epgChList=[]
let epgChMap={}
async function loadEpgChannels(){
 const [r1,r2] = await Promise.all([
  fetch('/api/epg-channels').then(r=>r.json()),
  fetch('/api/epg/extracts').then(r=>r.json())
 ])
 epgChList=r1.channels||[]
 epgExtracts=r2.extracts||[]
 renderEpgChannels()
}
async function refreshEpgChannels(){
 // 只增不删：把「外部 EPG 提取记录」增量补进 EPG 管理，不会删任何现有频道
 try{
  const r=await fetch('/api/epg-channels/sync-all',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
  const d=await r.json()
  if(!r.ok){showToast('同步失败: '+(d.detail||r.status),'error');return}
  await loadEpgChannels()
  showToast(d.added?('已补入 '+d.added+' 个频道（未删除任何频道）'):'已是最新（未删除任何频道）','success')
 }catch(e){
  showToast('同步失败: '+(e.message||e),'error')
 }
}

async function cleanupEpgOrphans(){
 if(!await customConfirm('清理「来源已不存在」的频道？\n\n会保留：电信频道、手动添加的、以及提取记录还在的频道。'))return
 try{
  const r=await fetch('/api/epg-channels/cleanup-orphans',{method:'POST'})
  const d=await r.json()
  await loadEpgChannels()
  if(d.removed){
   showToast('已清理 '+d.removed+' 个频道','success')
  }else{
   showToast('没有需要清理的频道','success')
  }
 }catch(e){ showToast('清理失败: '+(e.message||e),'error') }
}
function renderEpgChannels(){
 const tbody=$('epgChannelBody')
 if(!tbody) return
 const kw = ($('epgChSearch') || {}).value ? $('epgChSearch').value.toLowerCase().trim() : ''
 
 let filtered = epgChList.map((ch, i) => ({ch, i}))
 if(kw) {
   filtered = filtered.filter(({ch}) => {
     const tvgName = ch.tvg_name || ch.name || ''
     const source = ch.source_remark || ''
     const merged = (epgExtracts || []).filter(e => e.target_logic_id === ch.logic_id)
     const mergedNames = merged.map(e => e.name).join(' ')
     const mergedSources = merged.map(e => e.source).join(' ')
     
     const searchStr = `${ch.name || ''} ${ch.tvg_id || ''} ${tvgName} ${source} ${mergedNames} ${mergedSources}`.toLowerCase()
     return searchStr.includes(kw)
   })
 }

 if(!filtered.length){
   tbody.innerHTML='<tr><td colspan="7" class="empty">'+(epgChList.length?'没有匹配项':'暂无EPG频道')+'</td></tr>'
   return
 }
 
 tbody.innerHTML=filtered.map(({ch, i})=>{
   const mergedExtracts = (epgExtracts || []).filter(e => e.target_logic_id === ch.logic_id)
   
   let tvgNameHtml = `<input value="${escapeHtml(ch.tvg_name||ch.name||'')}" style="width:100%;border:none;background:transparent" onchange="updateEpgChField(${i},'tvg_name',this.value)">`
   if(mergedExtracts.length > 0) {
     const otherNames = mergedExtracts.map(e => e.name).filter(n => n && n !== (ch.tvg_name||ch.name))
     if(otherNames.length > 0) {
       tvgNameHtml += `<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;line-height:1.2;word-break:break-all">并入: ${otherNames.map(escapeHtml).join(' / ')}</div>`
     }
   }

   let sourceHtml = (function(){
     const st=ch.source_type||'';
     if(st==='extract'){
       const remark=ch.source_remark||'';
       const en=ch.extract_name||'';
       if(en&&en!==ch.name){return escapeHtml(remark)+' ('+escapeHtml(en)+')'}
       return escapeHtml(remark)
     }
     return escapeHtml(ch.source_remark||'自建json')
   })()
   
   if(mergedExtracts.length > 0) {
     const otherSources = [...new Set(mergedExtracts.map(e => e.source).filter(Boolean))]
     const mainSource = ch.source_remark || ''
     const extraSources = otherSources.filter(s => s !== mainSource)
     if(extraSources.length > 0) {
       sourceHtml += `<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;line-height:1.2;word-break:break-all">并入: ${extraSources.map(escapeHtml).join(' / ')}</div>`
     }
   }

   return `<tr draggable="true" ondragstart="epgDragStart(event,${i})" ondrop="epgDrop(event,${i})" ondragover="event.preventDefault()">
   <td style="cursor:grab;text-align:center">☰</td>
   <td><input value="${escapeHtml(ch.name)}" style="border:none;background:transparent;width:100%" onchange="updateEpgChField(${i},'name',this.value)"></td>
   <td><input value="${escapeHtml(ch.tvg_id||'')}" style="width:100%;border:none;background:transparent" onchange="updateEpgChField(${i},'tvg_id',this.value)"></td>
   <td>${tvgNameHtml}</td>
   <td style="font-size:11px">${sourceHtml}</td>
   <td style="text-align:center"><span class="badge ${ch.enabled!==false?'badge-success':'badge-danger'}">${ch.enabled!==false?'启用':'禁用'}</span></td>
   <td style="text-align:center;white-space:nowrap"><button class="btn btn-sm" onclick="toggleEpgCh(${i})">${ch.enabled!==false?'⛔':'✅'}</button><button class="btn btn-sm btn-danger" onclick="deleteEpgCh(${i})">✕</button></td>
  </tr>`
 }).join('')
}
async function updateEpgChField(i,f,v){
 if(i<epgChList.length) epgChList[i][f]=v
 await fetch('/api/epg-channels/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({[f]:v})})
 if((f==='name'||f==='tvg_id')&&epgChList[i].source_type==='channel'){
  const lid=epgChList[i].logic_id
  if(lid){
   const ci=channels.findIndex(ch=>ch.logic_id===lid)
   if(ci>=0){
    channels[ci][f]=v
    await fetch('/api/channels/'+ci,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({[f]:v})})
   }
  }
 }

 if(typeof _m3uOptions!=='undefined'){
  try{
   const r=await fetch('/api/m3u/channel-options?_='+Date.now());const d=await r.json()
   if(d.options) _m3uOptions=d.options
  }catch(e){}
  if($('m3uEditor')&&$('m3uEditor').style.display==='block') renderM3uTable(currentM3uEntries,_m3uOptions)
 }
}
async function toggleEpgCh(i){
 if(i<epgChList.length) epgChList[i].enabled=epgChList[i].enabled===false?true:false
 await fetch('/api/epg-channels/'+i,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:epgChList[i].enabled})})
 renderEpgChannels()
}
async function deleteEpgCh(i){
 if(!await customConfirm('确定删除该频道？\n（如果有外部EPG提取记录并入到了这个频道，这些提取记录也会被同步删除）'))return
 await fetch('/api/epg-channels/'+i,{method:'DELETE'})
 await loadEpgChannels()
 // ★ 新增：同步更新前端的提取记录数据，防止页面残留脏数据
 epgExtracts = await fetch('/api/epg/extracts').then(r=>r.json()).then(d=>d.extracts||[])
 await syncAllAfterChange()
}

// 手动添加 EPG 频道（不会被「刷新」同步删掉）
function showAddEpgChannel(){
 $('modalTitle').textContent='➕ 添加 EPG 频道'
 $('modalBody').innerHTML=`
  <div class="form-group"><label>频道名称（写进 epg.xml 的 display-name）</label><input id="addEpgName" placeholder="如 湖南卫视"></div>
  <div class="form-group"><label>TVG-ID（可留空，默认同频道名）</label><input id="addEpgTvgId" placeholder="留空 = 用频道名"></div>
  <div class="form-group"><label>TVG-NAME（可留空）</label><input id="addEpgTvgName" placeholder="留空 = 同频道名"></div>
  <div style="font-size:12px;color:var(--text-secondary);line-height:1.7">
   · 手动添加的频道点「🔄 刷新」不会被删掉<br>
   · 节目数据来自 <code>data/json/频道名_日期.json</code>；没有数据会自动补「暂无节目单」占位<br>
   · 从外部 EPG 来的频道建议走「外部EPG → 搜索 → 添加」，会自动建提取记录
  </div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmAddEpgChannel()">💾 添加</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmAddEpgChannel(){
 const name=($('addEpgName')||{}).value?$('addEpgName').value.trim():''
 if(!name){showToast('请填写频道名称','error');return}
 const tvg_id=($('addEpgTvgId')||{}).value?$('addEpgTvgId').value.trim():''
 const tvg_name=($('addEpgTvgName')||{}).value?$('addEpgTvgName').value.trim():''
 const body={name}
 if(tvg_id) body.tvg_id=tvg_id
 if(tvg_name) body.tvg_name=tvg_name
 const r=await fetch('/api/epg-channels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const d=await r.json().catch(()=>({}))
 if(!r.ok){showToast('添加失败: '+(d.detail||r.status),'error');return}
 closeModal()
 showToast('已添加：'+name,'success')
 await loadEpgChannels()
}

// ---- 排序（自然排序：CCTV2 排在 CCTV10 前面）----
function cmpNatural(a,b){
 return String(a==null?'':a).localeCompare(String(b==null?'':b),'zh-Hans-CN',{numeric:true,sensitivity:'base'})
}

async function sortEpgChannels(by){
 if(!epgChList.length){showToast('没有频道可排序','error');return}
 const old=epgChList.slice()
 const sorted=old.slice().sort((x,y)=>cmpNatural(
   by==='tvg_id'?(x.tvg_id||''):(x.name||''),
   by==='tvg_id'?(y.tvg_id||''):(y.name||'')
 ))
 const indices=sorted.map(c=>old.indexOf(c))
 const r=await fetch('/api/epg-channels/reorder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({indices})})
 if(!r.ok){showToast('排序失败','error');return}
 await loadEpgChannels()
 showToast('已按'+(by==='tvg_id'?'TVG-ID':'名称')+'排序','success')
}

function sortM3uEntries(by){
 if(!currentM3uEntries||!currentM3uEntries.length){showToast('没有可排序的条目','error');return}
 const key=e=>(by==='url')
   ? ((e._urls&&e._urls[0])||e.url||'')
   : (e.tvg_name||e.display_name||e.name||'')
 currentM3uEntries.sort((x,y)=>cmpNatural(key(x),key(y)))
 renderM3uTable(currentM3uEntries,_m3uOptions)
 autoSaveM3u()
 showToast('已按'+(by==='url'?'URL':'名称')+'排序','success')
}

let epgDragIdx=null
function epgDragStart(e,i){epgDragIdx=i}
async function epgDrop(e,i){
 e.preventDefault()
 if(epgDragIdx===null||epgDragIdx===i)return
 const item=epgChList.splice(epgDragIdx,1)[0]
 epgChList.splice(epgDragIdx<i?i-1:i,0,item)
 epgDragIdx=null
 const indices=epgChList.map((_,j)=>j)
 await fetch('/api/epg-channels/reorder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({indices})})
 renderEpgChannels()
}

async function clearAllEpgChannels(){
 if(!await customConfirm('清空所有EPG频道？'))return
 await fetch('/api/epg-channels/clear',{method:'POST'});loadEpgChannels()
}

async function renderM3uFiles(){
 const r=await fetch('/api/m3u');const d=await r.json()
 const files=d.files||[]
 const container=$('m3uFileList')
 if(!files.length){container.innerHTML='<div class="empty">暂无M3U文件，请上传</div>';return}
 // ★ 修复：所有 onclick 里的文件名参数改用 jsArg，防引号注入
 container.innerHTML=`
 <table id="m3uFileTable"><thead><tr><th>文件名</th><th>频道数</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead>
 <tbody>${files.map(f=>`
  <tr>
   <td>${escapeHtml(f.name)}</td>
   <td>${f.channel_count||0}</td>
   <td>${(f.size/1024).toFixed(1)} KB</td>
   <td>${escapeHtml(f.mtime)}</td>
   <td><button class="btn btn-sm" onclick="editM3uFile(${jsArg(f.name)})">✏️ 匹配</button>
   <button class="btn btn-sm" onclick="copyM3uLink(${jsArg(f.name)})">📋 复制链接</button>
   <button class="btn btn-sm btn-danger" onclick="deleteM3uFile(${jsArg(f.name)})">✕</button></td>
  </tr>`).join('')}</tbody></table>`
 reapplyColWidths()
}

function showUploadM3u(){
 $('modalTitle').textContent='📤 上传M3U文件'
 $('modalBody').innerHTML=`
  <div class="dropzone" id="m3uDropzone" onclick="document.getElementById('m3uFileInput').click()"
    ondragover="event.preventDefault();this.classList.add('dragover')"
    ondragleave="this.classList.remove('dragover')"
    ondrop="event.preventDefault();this.classList.remove('dragover');handleM3uDrop(event)">
   <div class="icon">📁</div>
   <div class="text">拖拽文件到此处或点击选择</div>
   <div class="hint">支持 .m3u .m3u8 .txt 格式</div>
  </div>
  <input type="file" id="m3uFileInput" accept=".m3u,.m3u8,.txt" style="display:none" onchange="handleM3uFileSelect(event)" multiple>`
 $('modalActions').innerHTML=`
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

function handleM3uDrop(e){uploadMultipleM3u(e.dataTransfer.files)}
function handleM3uFileSelect(e){uploadMultipleM3u(e.target.files)}
async function uploadMultipleM3u(files){let ok=0;for(let i=0;i<files.length;i++){const f=files[i];try{const t=await f.text();const r=await fetch('/api/m3u/upload',{method:'POST',body:t,headers:{'Content-Type':'text/plain','Content-Disposition':'attachment; filename="'+encodeURIComponent(f.name)+'"'}});if(!r.ok){showToast('上传失败: HTTP '+r.status,'error');continue};const d=await r.json();if(d.success)ok++}catch(e){showToast('上传出错: '+e.message,'error')}}showToast('上传完成 '+ok+'/'+files.length+' 个','success');closeModal();renderM3uFiles()}

function showM3uUrlInput(){
 $('modalTitle').textContent='🔗 直链添加M3U'
 $('modalBody').innerHTML=`
  <div class="form-group"><label>M3U直链</label><input id="m3uUrlInput" placeholder="http://...m3u"></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-primary" onclick="confirmM3uUrl()">⬇️ 下载</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

async function confirmM3uUrl(){
 const url=$('m3uUrlInput').value.trim()
 if(!url){showToast('请输入URL','error');return}
 try{
  const resp=await fetch(url)
  const text=await resp.text()
  const filename=url.split('/').pop()||'m3u_remote.m3u'
  const r=await fetch('/api/m3u/upload',{method:'POST',body:text,headers:{'Content-Type':'text/plain','Content-Disposition':`attachment; filename="${filename}"`}})
  const d=await r.json()
  if(d.success){showToast('添加成功','success');closeModal();renderM3uFiles()}
 }catch(e){showToast('下载失败: '+e.message,'error')}
}

function copyM3uLink(name){
 const url=location.protocol+'//'+location.host+'/api/download/'+encodeURIComponent(name)
 const input=document.createElement('input')
 input.value=url
 document.body.appendChild(input)
 input.select();document.execCommand('copy')
 document.body.removeChild(input)
 showToast('✅ 已复制 M3U 直链到剪贴板','success')
}

async function deleteM3uFile(name){
 if(!await customConfirm(`确定删除 ${name}？`))return
 await fetch(`/api/m3u/${encodeURIComponent(name)}`,{method:'DELETE'})
 renderM3uFiles()
}

function closeM3uEditor(){
 // ★ 关闭前先把防抖窗口里没落盘的改动立刻存掉：
 //   否则定时器会带着已清空的 currentM3uFile 触发，请求打到 /api/m3u/null/save，
 //   既丢掉这次编辑，又在 data/m3u 下留一个只有 "#EXTM3U" 的 null 文件
 m3uFlushPendingSave()
 $('m3uEditor').style.display='none'
 currentM3uFile=null
 currentM3uEntries=[]
 localStorage.removeItem('m3u_editing')
}
async function editM3uFile(name){
 // ★ 等上一次保存真正落盘再读文件：刚点「关闭」就重新打开时，避免读到旧内容又被覆盖回去
 //   最多等 5 秒，避免网络卡住时连编辑器都打不开
 await Promise.race([m3uSaveChain,new Promise(r=>setTimeout(r,5000))])
 const merge=$('m3uMergeCheck')?$('m3uMergeCheck').checked:true
 const r=await fetch(`/api/m3u/${encodeURIComponent(name)}?merge=${merge}`);const d=await r.json()
 const parsed=d.parsed||{entries:[]}
 currentM3uFile=name
 currentM3uEntries=parsed.entries.map(e=>({...e}))

 let options=[]
 try{
  const optR=await fetch('/api/m3u/channel-options?_='+Date.now());const optD=await optR.json()
  options=optD.options||[]
 }catch(e){}
 
 if(!options.length){
  try{
   const eR=await fetch('/api/epg-channels?_='+Date.now());const eD=await eR.json()
   const list=eD.channels||[]
   options=[{tvg_id:'9999',tvg_name:'暂无节目单'}]
   list.forEach(ch=>{
    if(ch.name && ch.name!=='暂无节目单') options.push({tvg_id:ch.tvg_id||'',tvg_name:ch.name})
   })
  }catch(e){}
 }
 
 if(!options.length && typeof channels!=='undefined' && channels.length){
  options=[{tvg_id:'9999',tvg_name:'暂无节目单'}]
  channels.forEach(ch=>{
   if(ch.name && ch.name!=='暂无节目单') options.push({tvg_id:ch.tvg_id||'',tvg_name:ch.name})
  })
 }
 _m3uOptions=options

 try{
  const iconR=await fetch('/api/icons?_='+Date.now());const iconD=await iconR.json()
  _m3uIconFiles=(iconD.files||[]).map(f=>({name:f.name, url:f.url}))
 }catch(e){ _m3uIconFiles=[] }
 m3uSyncColumnVisibility()

 m3uMatchAllEntries()
 autoSaveM3u()

 localStorage.setItem('m3u_editing',name)
 $('m3uEditorTitle').textContent=`📝 匹配编辑: ${name}`
 $('m3uEditor').style.display='block'

 renderM3uTable(currentM3uEntries, _m3uOptions)
}
let m3uDragMouse={active:false,fromIdx:-1,startY:0,rowHeight:40}
function m3uEditField(i,field,val){
 if(i<currentM3uEntries.length) currentM3uEntries[i][field]=val
 autoSaveM3u()
}
function m3uMouseDown(e,idx){
 m3uDragMouse.active=true
 m3uDragMouse.fromIdx=idx
 m3uDragMouse.startY=e.clientY
 const tr=e.target.closest('tr')
 if(tr){
  m3uDragMouse.rowHeight=tr.offsetHeight||40
  tr.classList.add('drag-src')
  document.body.classList.add('row-dragging')
 }
 document.addEventListener('mousemove',m3uMouseMove)
 document.addEventListener('mouseup',m3uMouseUp)
 e.preventDefault()
}
function m3uMouseMove(e){
 if(!m3uDragMouse.active)return
 const diff=e.clientY-m3uDragMouse.startY
 const rows=Math.round(diff/m3uDragMouse.rowHeight)
 const from=m3uDragMouse.fromIdx
 const to=Math.max(0,Math.min(currentM3uEntries.length-1,from+rows))
 if(to===from)return
 const tbody=$('m3uEditBody')
 if(tbody){
  const rows_el=tbody.querySelectorAll('tr')
  if(rows_el[from]&&rows_el[to]){
   const parent=tbody
   if(from<to){
    parent.insertBefore(rows_el[from],rows_el[to].nextSibling)
   }else{
    parent.insertBefore(rows_el[from],rows_el[to])
   }
  }
 }
 const arr=currentM3uEntries
 const item=arr.splice(from,1)[0]
 const insertAt=to
 arr.splice(insertAt,0,item)
 m3uDragMouse.fromIdx=insertAt
 m3uDragMouse.startY=e.clientY
}
async function m3uMouseUp(e){
 if(!m3uDragMouse.active)return
 m3uDragMouse.active=false
 cleanupRowDrag()
 document.removeEventListener('mousemove',m3uMouseMove)
 document.removeEventListener('mouseup',m3uMouseUp)
 if(!currentM3uFile)return
 await fetch('/api/m3u/'+encodeURIComponent(currentM3uFile)+'/save',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({entries:currentM3uEntries})
 })
 if(currentM3uFile) editM3uFile(currentM3uFile)
}

let m3uAutoSaveTimer=null
let m3uPendingFile=null
let m3uPendingEntries=null

// 把 entries 写回指定 M3U 文件（失败静默，保持与原先 autoSaveM3u 一致）。
// 所有保存串成一条链：既保证先后顺序，也让「刚关闭就重新打开」能等到写入完成再读文件。
let m3uSaveChain=Promise.resolve()
function m3uSaveEntries(file,entries){
 if(!file)return m3uSaveChain
 const body=JSON.stringify({entries:entries||[]})   // ★ 调用时快照，之后改动不会串到这次请求里
 const run=()=>fetch('/api/m3u/'+encodeURIComponent(file)+'/save',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body
 }).catch(()=>{})
 m3uSaveChain=m3uSaveChain.then(run,run)
 return m3uSaveChain
}

// 立刻把还在防抖窗口里的改动落盘（关闭编辑器 / 切到别的文件前调用）
function m3uFlushPendingSave(){
 if(!m3uAutoSaveTimer)return
 clearTimeout(m3uAutoSaveTimer)
 m3uAutoSaveTimer=null
 const file=m3uPendingFile,entries=m3uPendingEntries
 m3uPendingFile=null
 m3uPendingEntries=null
 if(file) m3uSaveEntries(file,entries)
}

// 丢弃待保存的改动（调用方已自己显式保存时用）
function m3uDiscardPendingSave(){
 if(m3uAutoSaveTimer)clearTimeout(m3uAutoSaveTimer)
 m3uAutoSaveTimer=null
 m3uPendingFile=null
 m3uPendingEntries=null
}

async function m3uDeleteEntry(idx){
 if(idx<0||idx>=currentM3uEntries.length)return
 if(!await customConfirm('确定删除第 '+(idx+1)+' 条？'))return
 currentM3uEntries.splice(idx,1)
 if(!currentM3uFile)return
 await fetch('/api/m3u/'+encodeURIComponent(currentM3uFile)+'/save',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({entries:currentM3uEntries})
 })
 if(currentM3uFile) editM3uFile(currentM3uFile)
}

async function m3uAddNew(){
 const url=$('m3uNewUrl').value.trim()
 const tvg_id=$('m3uNewTvgId').value.trim()
 const tvg_name=$('m3uNewTvgName').value.trim()
 const display_name=$('m3uNewDispName').value.trim()
 if(!url)return
 currentM3uEntries.push({tvg_id,tvg_name,display_name,url,_urls:[url],_dup_count:1})
 $('m3uNewUrl').value=''
 $('m3uNewTvgId').value=''
 $('m3uNewTvgName').value=''
 $('m3uNewDispName').value=''
 if(!currentM3uFile)return
 await fetch('/api/m3u/'+encodeURIComponent(currentM3uFile)+'/save',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({entries:currentM3uEntries})
 })
 if(currentM3uFile) editM3uFile(currentM3uFile)
}

async function m3uToggleMerge(){
 if(!currentM3uFile) return
 m3uDiscardPendingSave()
 try{
  await fetch('/api/m3u/'+encodeURIComponent(currentM3uFile)+'/save',{
   method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({entries:currentM3uEntries})
  })
 }catch(e){}
 editM3uFile(currentM3uFile)
}

function m3uBatchReplace(){
 if(!currentM3uFile||!currentM3uEntries.length){showToast('请先打开一个 M3U 文件','error');return}
 $('modalTitle').textContent='🔁 批量替换 URL'
 $('modalBody').innerHTML=`
  <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px">将当前列表所有频道的 URL 中匹配的字段批量替换（如 rtp:// → http://192.168.1.1:1233/rtp/）</p>
  <div class="form-group"><label>查找</label><input id="m3uRepOld" placeholder="例如: rtp://"></div>
  <div class="form-group" style="margin-top:10px"><label>替换为</label><input id="m3uRepNew" placeholder="例如: http://192.168.1.1:1233/rtp/"></div>`
 $('modalActions').innerHTML=`
  <button class="btn btn-warning" onclick="confirmM3uBatchReplace()">✅ 确认替换</button>
  <button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
 setTimeout(()=>$('m3uRepOld').focus(),50)
}

function confirmM3uBatchReplace(){
 const old=$('m3uRepOld').value
 const newVal=$('m3uRepNew').value
 if(!old){showToast('请填写需要查找的内容','error');return}
 let count=0, chan=0
 ;(currentM3uEntries||[]).forEach(e=>{
  let urls=e._urls&&e._urls.length?e._urls:[e.url].filter(Boolean)
  if(!urls.length) return
  let hit=false
  urls=urls.map(u=>{
   if(u&&u.indexOf(old)>-1){ u=u.split(old).join(newVal); hit=true; count++ }
   return u
  })
  if(hit){ e._urls=urls; if(!(e._urls&&e._urls.length)) delete e._urls; e.url=urls[0]||''; chan++ }
 })
 if(!count){showToast('没有找到可替换的内容','error');return}
 closeModal()
 renderM3uTable(currentM3uEntries,_m3uOptions)
 m3uSyncColumnVisibility()
 autoSaveM3u()
 showToast('已替换 '+count+' 处 URL（涉及 '+chan+' 个频道），正在保存','success')
}

function autoSaveM3u(){
 if(!currentM3uFile)return
 // 切到别的文件时，先把上一个文件没落盘的改动存掉，别被新文件的防抖覆盖
 if(m3uAutoSaveTimer&&m3uPendingFile!==currentM3uFile) m3uFlushPendingSave()
 if(m3uAutoSaveTimer)clearTimeout(m3uAutoSaveTimer)
 // 文件名和条目在“排程时”就绑定，回调里不再读全局，
 // 避免关闭编辑器后 currentM3uFile 变成 null、把改动写到 null 文件
 m3uPendingFile=currentM3uFile
 m3uPendingEntries=currentM3uEntries
 m3uAutoSaveTimer=setTimeout(()=>{
  const file=m3uPendingFile,entries=m3uPendingEntries
  m3uAutoSaveTimer=null
  m3uPendingFile=null
  m3uPendingEntries=null
  if(file) m3uSaveEntries(file,entries)
 },500)
}

function m3uMatchEntry(entry, options){
 const display=entry.display_name||entry.tvg_name||''
 if(!display) return false
 let matched=null
 if(entry.tvg_id&&entry.tvg_id!=='9999'){
  matched=options.find(o=>o.tvg_id===entry.tvg_id&&o.tvg_name!=='暂无节目单')
 }
 if(!matched){
  matched=options.find(o=>o.tvg_name===display&&o.tvg_name!=='暂无节目单')
 }
 if(!matched){
  const clean=display.replace(/[\s\-（）()\[\]]/g,'')
  matched=options.find(o=>o.tvg_name.replace(/[\s\-（）()\[\]]/g,'')===clean&&o.tvg_name!=='暂无节目单')
 }
 if(matched){
  entry.tvg_name=matched.tvg_name
  return true
 }else{
  entry.tvg_name=display
  return false
 }
}

function m3uMatchAllEntries(){
 if(!_m3uOptions||!currentM3uEntries) return
 currentM3uEntries.forEach(e=>m3uMatchEntry(e,_m3uOptions))
}

async function refreshM3uOptions(){
 if(!currentM3uFile)return
 showToast('正在刷新选项...','success')
 let newOptions=[]
 try{
  const r=await fetch('/api/m3u/channel-options?_='+Date.now());const d=await r.json()
  newOptions=d.options||[]
 }catch(e){}
 if(!newOptions.length){
  try{
   const r=await fetch('/api/epg-channels?_='+Date.now());const d=await r.json()
   const list=d.channels||[]
   newOptions=[{tvg_id:'9999',tvg_name:'暂无节目单'}]
   list.forEach(ch=>{if(ch.name&&ch.name!=='暂无节目单')newOptions.push({tvg_id:ch.tvg_id||'',tvg_name:ch.name})})
  }catch(e){}
 }
 _m3uOptions=newOptions
 m3uMatchAllEntries()
 renderM3uTable(currentM3uEntries, _m3uOptions)
 autoSaveM3u()
 showToast('选项已刷新 '+newOptions.length+' 项','success')
}

function m3uToggleFold(i){
 if(i<currentM3uEntries.length) currentM3uEntries[i]._expanded=!currentM3uEntries[i]._expanded
 renderM3uTable(currentM3uEntries, _m3uOptions)
}
function m3uEditUrl(i,ui,val){
 if(i>=currentM3uEntries.length)return
 const e=currentM3uEntries[i]
 let urls=e._urls&&e._urls.length?e._urls:[e.url].filter(Boolean)
 if(!urls.length) urls=['']
 const oldVal=urls[ui]
 if(oldVal===val)return
 if(oldVal===''){
  if(val.trim()){
   urls[ui]=val.trim()
   e._urls=urls
   if(urls.length===1&&urls[0]){e.url=urls[0];delete e._urls}
   e._expanded=false
  }else{
   urls.splice(ui,1)
   if(urls.length){
    e._urls=urls
    if(urls.length===1&&urls[0]){e.url=urls[0];delete e._urls}
   }else{e.url='';delete e._urls}
  }
  renderM3uTable(currentM3uEntries, _m3uOptions)
  autoSaveM3u()
 }else{
  urls[ui]=val
  if(!e._urls) e._urls=urls
  autoSaveM3u()
 }
}
function m3uAddUrl(i){
 if(i>=currentM3uEntries.length)return
 const e=currentM3uEntries[i]
 if(!e._urls) e._urls=[e.url].filter(Boolean)
 if(!e._urls.length) e._urls=['']
 e._urls.push('')
 e._expanded=true
 renderM3uTable(currentM3uEntries, _m3uOptions)
 setTimeout(()=>{
  const tbody=$('m3uEditBody')
  if(tbody){
   const rows=tbody.querySelectorAll('tr')
   if(rows[i]){
    // ★ 按 id 找 URL 单元格：列顺序可以拖动，nth-child(2) 不再等于 URL 列
    const urlTd=document.getElementById('m3uUrlTd'+i)||rows[i].querySelector('td:nth-child(2)')
    const inputs=urlTd?urlTd.querySelectorAll('input'):[]
    if(inputs.length) inputs[inputs.length-1].focus()
   }
  }
 },50)
}
async function m3uSyncChanName(i){
 const entry=currentM3uEntries[i]
 if(!entry) return
 const dn=entry.display_name||entry.tvg_name||''
 if(!dn) return
 const matched=_m3uOptions.find(o=>o.tvg_id===entry.tvg_id&&o.tvg_id!=='9999')
 if(!matched) return
 try{
  const r=await fetch('/api/channels?_='+Date.now());const d=await r.json()
  const chs=d.channels||[]
  const ci=chs.findIndex(ch=>ch.tvg_id===matched.tvg_id)
  if(ci>=0){await fetch('/api/channels/'+ci,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:dn})});if(ci<channels.length)channels[ci].name=dn}
 }catch(e){}
 try{
  const r=await fetch('/api/epg-channels?_='+Date.now());const d=await r.json()
  const ecs=d.channels||[]
  const ei=ecs.findIndex(ch=>ch.tvg_id===matched.tvg_id)
  if(ei>=0) await fetch('/api/epg-channels/'+ei,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:dn,tvg_name:dn})})
 }catch(e){}
 entry.tvg_name=dn
 try{
  const r=await fetch('/api/epg-channels?_='+Date.now());const d=await r.json()
  const list=d.channels||[]
  _m3uOptions=[{tvg_id:'9999',tvg_name:'暂无节目单'}]
  list.forEach(ch=>{if(ch.name&&ch.name!=='暂无节目单')_m3uOptions.push({tvg_id:ch.tvg_id||'',tvg_name:ch.name})})
 }catch(e){}
 renderM3uTable(currentM3uEntries, _m3uOptions)
 autoSaveM3u()
 showToast('已同步频道名称: '+dn,'success')
}
function renderM3uTable(entries, opts){
 const tbody=$('m3uEditBody')
 tbody.innerHTML=entries.map((e,i)=>{
  let urls=e._urls&&e._urls.length?e._urls:[e.url].filter(Boolean)
  if(!urls.length) urls=['']
  const mergeOn=$('m3uMergeCheck')?$('m3uMergeCheck').checked:true
  const maxShow=(!mergeOn||e._expanded)?urls.length:Math.min(2,urls.length)
  const shown=urls.slice(0,maxShow)
  const foldCount=urls.length-maxShow
  const showFold=foldCount>0&&!e._expanded
  const showUnfold=(e._expanded||!mergeOn)&&urls.length>2
  return `<tr style="user-select:none">
   <td style="text-align:center;cursor:grab;vertical-align:top;padding-top:8px" onmousedown="m3uMouseDown(event,${i})">☰</td>
   <td id="m3uUrlTd${i}" ondragover="event.preventDefault()" style="padding:4px 8px">
    <div style="display:flex;flex-direction:column;gap:3px">
     ${shown.map((url,ui)=>'<input value="'+escapeHtml(url)+'" style="font-size:11px;word-break:break-all;width:100%;border:none;background:transparent;padding:2px 4px;margin:0" onblur="m3uEditUrl('+i+','+ui+',this.value)" placeholder="URL地址">').join('')}
     ${showFold?'<div style="padding:2px 0;cursor:pointer;text-align:center;font-size:12px;color:var(--primary)" onclick="m3uToggleFold('+i+')">+'+foldCount+' 条 <span style="font-size:10px">▼</span></div>':''}
     ${showUnfold?'<div style="padding:2px 0;cursor:pointer;text-align:center;font-size:12px;color:var(--text-secondary)" onclick="m3uToggleFold('+i+')"><span style="font-size:10px">▲</span> 折叠</div>':''}
    </div>
   </td>
   <td id="m3uGrpTd${i}" style="vertical-align:top;padding-top:8px;display:none">${m3uRenderGroupCell(i,e)}</td>
   <td id="m3uIcoTd${i}" style="vertical-align:top;padding-top:8px;display:none">${m3uRenderIconCell(i,e)}</td>
   <td style="vertical-align:top;padding-top:8px"><input class="m3u-tvgid" value="${escapeHtml(e.tvg_id||'')}" style="width:100%;border:none;background:transparent" onblur="m3uEditField(${i},'tvg_id',this.value);renderM3uTable(currentM3uEntries,_m3uOptions)"></td>
   <td style="vertical-align:top;padding-top:8px"><input class="m3u-tvgname" value="${escapeHtml(e.tvg_name||'')}" style="width:100%;min-width:100px;border:none;background:transparent" onblur="m3uEditField(${i},'tvg_name',this.value);renderM3uTable(currentM3uEntries,_m3uOptions)"></td>
   <td style="vertical-align:top;padding-top:8px">
    <div style="display:flex;align-items:center;gap:4px">
     <input value="${escapeHtml(e.display_name||'')}" style="border:none;background:transparent;width:100%" onblur="m3uEditField(${i},'display_name',this.value);renderM3uTable(currentM3uEntries,_m3uOptions)">
     ${(function(idx){const matched=opts.find(o=>o.tvg_id===e.tvg_id&&o.tvg_id!=='9999');if(!matched)return '';const dn=e.display_name||e.tvg_name||'';return dn===matched.tvg_name&&dn===(matched.epg_tvg_name||matched.tvg_name)?'':'<span title="显示名称与EPG频道名称不一致" style="color:#e6a23c;font-size:13px;cursor:pointer" onclick="m3uSyncChanName('+idx+')">⚠️</span>'})(i)}
    </div>
   </td>
   <td style="vertical-align:top;padding-top:8px"><select class="m3u-match" onchange="var sel=this.options[this.selectedIndex];var sid=sel.dataset.id,sname=sel.value;m3uEditField(${i},'tvg_id',sid);if(sid==='9999'){var disp=e.display_name||e.tvg_name||'';m3uEditField(${i},'tvg_name',disp);var tr=this.closest('tr');if(tr){var ni=tr.querySelector('td .m3u-tvgname');if(ni)ni.value=disp}}else{m3uEditField(${i},'tvg_name',sname);var tr=this.closest('tr');if(tr){var ni=tr.querySelector('td .m3u-tvgname');if(ni)ni.value=sname}};var tr=this.closest('tr');if(tr){var ti=tr.querySelector('td .m3u-tvgid');if(ti)ti.value=sid};renderM3uTable(currentM3uEntries,_m3uOptions);this.dataset.currentTvgId=sid" style="width:100%" data-current-tvg-id="${escapeHtml(e.tvg_id||'')}">
    <option value="暂无节目单" data-id="9999">🚫 暂无节目单</option>
    ${opts.filter(o=>o.tvg_name!=='暂无节目单').map(o=>'<option value="'+escapeHtml(o.tvg_name)+'" data-id="'+escapeHtml(o.tvg_id)+'" '+(o.tvg_id===e.tvg_id?'selected':'')+'>'+escapeHtml(o.tvg_name)+'</option>').join('')}
   </select></td>
   <td style="vertical-align:top;text-align:center;white-space:nowrap;padding-top:8px">
    ${document.getElementById('m3uMergeCheck')&&document.getElementById('m3uMergeCheck').checked?'<button class="btn btn-sm" onclick="m3uAddUrl('+i+')" style="padding:2px 6px;font-size:11px;margin-right:2px" title="添加URL">+</button>':''}
    <button class="btn btn-sm btn-danger" onclick="m3uDeleteEntry(${i})" style="padding:2px 6px;font-size:11px">✕</button>
   </td>
  </tr>`
 }).join('')
 applyTableColumnOrder($('m3uEditTable'))   // 列顺序（拖动保存过）立即生效，避免闪一下
 m3uSyncColumnVisibility()
}
/* ==================== M3U 频道分组 / 频道图标列 ==================== */
function m3uShowGroupFlag(){
 const el=document.getElementById('m3uShowGroup')
 return !!el&&el.checked
}
function m3uShowIconFlag(){
 const el=document.getElementById('m3uShowIcon')
 return !!el&&el.checked
}

function m3uGroupList(){
 const set=new Set();
 (currentM3uEntries||[]).forEach(e=>{const g=(e.group_title||'').trim();if(g)set.add(g)})
 return Array.from(set)
}

function m3uMatchIconName(logo){
 if(!logo) return ''
 const m=String(logo).match(/\/api\/(?:icons\/file|download)\/([^/?#]+)$/)
 if(!m) return ''
 const name=decodeURIComponent(m[1])
 if(_m3uIconFiles.some(f=>f.name===name)) return name
 return ''
}

function m3uIconFullUrl(relUrl){
 return location.protocol+'//'+location.host+relUrl
}

function m3uIsLanHost(host){
 const ip=(host||'').split(':')[0]
 if(!ip) return false
 const p=ip.split('.')
 if(p.length!==4) return false
 const a=parseInt(p[0],10),b=parseInt(p[1],10)
 if(a===10) return true
 if(a===127) return true
 if(a===192&&b===168) return true
 if(a===172&&b>=16&&b<=31) return true
 return false
}

function m3uToggleColumns(){
 m3uSyncColumnVisibility()
 renderM3uTable(currentM3uEntries, _m3uOptions)
}

function m3uSyncColumnVisibility(){
 const sg=m3uShowGroupFlag(), si=m3uShowIconFlag()
 const thG=document.getElementById('m3uThGroup'), thI=document.getElementById('m3uThIcon')
 if(thG) thG.style.display=sg?'':'none'
 if(thI) thI.style.display=si?'':'none'
 const ng=document.getElementById('m3uNewGroupTd'), ni=document.getElementById('m3uNewIconTd')
 if(ng) ng.style.display=sg?'':'none'
 if(ni) ni.style.display=si?'':'none'
 ;(currentM3uEntries||[]).forEach((e,i)=>{
  const g=document.getElementById('m3uGrpTd'+i); if(g) g.style.display=sg?'':'none'
  const ic=document.getElementById('m3uIcoTd'+i); if(ic) ic.style.display=si?'':'none'
 })
 const urlW = 290 - (sg?130:0) - (si?130:0)
 const urlTh=document.getElementById('m3uThUrl')
 // ★ 用户手动拖过 URL 列宽就尊重保存值，否则按隐藏列自动算
 const storedW = (urlTh && urlTh.closest('table')) ? _colWidths[colKey(urlTh.closest('table'),urlTh)] : 0
 const effW = (storedW > 20) ? storedW : urlW
 if(urlTh){ urlTh.style.width=effW+'px' }
 const newUrlTd=document.getElementById('m3uNewUrlTd')
 if(newUrlTd){ newUrlTd.style.width=effW+'px' }
 ;(currentM3uEntries||[]).forEach((e,i)=>{
  const urlCell=document.getElementById('m3uUrlTd'+i); if(urlCell){ urlCell.style.width=effW+'px' }
 })
}

let _m3uOpen={type:null, idx:-1}
let _m3uPanelEl=null

function m3uCloseDropdown(){
 if(_m3uPanelEl){ _m3uPanelEl.remove(); _m3uPanelEl=null }
 _m3uOpen={type:null, idx:-1}
}

function _m3uDocClick(ev){
 if(_m3uPanelEl && !_m3uPanelEl.contains(ev.target)){
  m3uCloseDropdown(); document.removeEventListener('click',_m3uDocClick)
 }
}

function m3uToggleDropdown(type, idx, btnEl, ev){
 if(ev && ev.stopPropagation) ev.stopPropagation()
 if(_m3uOpen.type===type && _m3uOpen.idx===idx){ m3uCloseDropdown(); return }
 m3uCloseDropdown()
 _m3uOpen={type:type, idx:idx}

 const e=currentM3uEntries[idx]
 const panel=document.createElement('div')
 panel.style.cssText='position:fixed;z-index:9999;background:#fff;border:1px solid rgba(0,0,0,.15);border-radius:6px;box-shadow:0 6px 20px rgba(0,0,0,.18);max-height:220px;overflow-y:auto;min-width:170px;max-width:280px;font-size:12px;padding:4px 0;color:#222'

 if(type==='group'){
  const groups=(typeof m3uGroupList==='function')?m3uGroupList():[]
  const cur=e?e.group_title||'':''
  // ★ 修复：分组名用 jsArg 传入
  const rows=['<div onclick="m3uPickGroup('+idx+','+jsArg('')+')" style="padding:6px 10px;cursor:pointer;border-bottom:1px solid #eee'+(cur===''?';background:#f0f7ff;font-weight:600':'')+'">暂无分类</div>']
  groups.forEach(g=>{
   rows.push('<div style="display:flex;align-items:center;border-bottom:1px solid #eee">'+
    '<div onclick="m3uPickGroup('+idx+','+jsArg(g)+')" style="flex:1;padding:6px 10px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'+(g===cur?';background:#f0f7ff;font-weight:600':'')+'">'+escapeHtml(g)+'</div>'+
    '<span title="删除该分组" onclick="event.stopPropagation();m3uDeleteGroup('+idx+','+jsArg(g)+')" style="flex-shrink:0;padding:6px 8px;cursor:pointer;color:#e5484d;font-size:13px">✕</span>'+
   '</div>')
  })
  if(!groups.length) rows.push('<div style="padding:10px;color:#999">暂无分类可选项</div>')
  panel.innerHTML=rows.join('')
 }else{
  const fs=(typeof _m3uIconFiles!=='undefined')?_m3uIconFiles:[]
  const cur=e?e.tvg_logo||'':''
  const matched=(typeof m3uMatchIconName==='function')?m3uMatchIconName(cur):''
  const rows=['<div onclick="m3uPickIcon('+idx+','+jsArg('')+')" style="padding:6px 10px;cursor:pointer;border-bottom:1px solid #eee'+(matched===''&&!cur?';background:#f0f7ff;font-weight:600':'')+'">无</div>']
  fs.forEach(f=>{
   rows.push('<div onclick="m3uPickIcon('+idx+','+jsArg(f.name)+')" style="display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;border-bottom:1px solid #eee'+(matched===f.name?';background:#f0f7ff;font-weight:600':'')+'">'+
    '<img src="'+escapeHtml(f.url)+'" style="width:18px;height:18px;object-fit:contain;flex-shrink:0" onerror="this.style.display=\'none\'">'+
    '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(f.name)+'</span>'+
   '</div>')
  })
  if(!fs.length) rows.push('<div style="padding:10px;color:#999">暂无台标，可点 ➕ 新建</div>')
  panel.innerHTML=rows.join('')
 }

 document.body.appendChild(panel)
 const r=btnEl.getBoundingClientRect()
 const pw=panel.offsetWidth||170
 const left=Math.max(4, Math.min(r.left, window.innerWidth-pw-8))
 const top=r.bottom+4
 panel.style.left=left+'px'
 panel.style.top=top+'px'
 _m3uPanelEl=panel
 setTimeout(function(){ document.addEventListener('click',_m3uDocClick) },0)
}

function m3uRenderGroupCell(i,e){
 const cur=e.group_title||''
 return '<div style="display:flex;align-items:center;gap:4px;position:relative">'+
  '<button onclick="m3uToggleDropdown(\'group\','+i+',this,event)" title="选择分组" style="flex:1;min-width:0;font-size:11px;border:1px solid var(--border);border-radius:4px;padding:3px 6px;background:#fff;cursor:pointer;text-align:left;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
   (cur?escapeHtml(cur):'<span style="color:#999">暂无分类</span>')+'<span style="float:right;color:#999">▼</span>'+
  '</button>'+
  '<button class="m3u-cell-btn" onclick="m3uNewGroupPrompt('+i+')" title="新建分组" style="flex-shrink:0;width:22px;height:22px;line-height:1;font-size:13px;padding:0;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer">➕</button>'+
 '</div>'
}

function m3uPickGroup(i,g){
 m3uCloseDropdown()
 if(i<0||i>=currentM3uEntries.length) return
 currentM3uEntries[i].group_title=g
 const cell=document.getElementById('m3uGrpTd'+i)
 if(cell) cell.innerHTML=m3uRenderGroupCell(i,currentM3uEntries[i])
 autoSaveM3u()
}

function m3uNewGroupPrompt(i){
 if(i<0||i>=currentM3uEntries.length) return
 const val=prompt('输入新分组名称：')
 if(val===null) return
 const g=val.trim()
 if(!g) return
 currentM3uEntries[i].group_title=g
 const cell=document.getElementById('m3uGrpTd'+i)
 if(cell) cell.innerHTML=m3uRenderGroupCell(i,currentM3uEntries[i])
 autoSaveM3u()
}

function m3uDeleteGroup(i,g){
 g=(g||'').trim()
 let changed=false
 ;(currentM3uEntries||[]).forEach(e=>{
  if((e.group_title||'').trim()===g){ e.group_title=''; changed=true }
 })
 if(i>=0&&i<currentM3uEntries.length&&currentM3uEntries[i].group_title===g){ currentM3uEntries[i].group_title=''; changed=true }
 if(changed) autoSaveM3u()
 renderM3uTable(currentM3uEntries, _m3uOptions)
 m3uSyncColumnVisibility()
}

function m3uRenderIconCell(i,e){
 const cur=e.tvg_logo||''
 const matched=m3uMatchIconName(cur)
 const disp=matched||(cur?'（自定义链接）':'')
 return '<div style="display:flex;align-items:center;gap:4px;position:relative">'+
  '<button onclick="m3uToggleDropdown(\'icon\','+i+',this,event)" title="选择台标" style="flex:1;min-width:0;font-size:11px;border:1px solid var(--border);border-radius:4px;padding:3px 6px;background:#fff;cursor:pointer;text-align:left;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
   (disp?escapeHtml(disp):'<span style="color:#999">无</span>')+'<span style="float:right;color:#999">▼</span>'+
  '</button>'+
  '<button class="m3u-cell-btn" onclick="m3uNewIconPrompt('+i+')" title="新建/自定义台标 URL" style="flex-shrink:0;width:22px;height:22px;line-height:1;font-size:13px;padding:0;border:1px solid var(--border);border-radius:4px;background:transparent;cursor:pointer">➕</button>'+
 '</div>'
}

function m3uPickIcon(i,name){
 m3uCloseDropdown()
 if(i<0||i>=currentM3uEntries.length) return
 if(!name){ currentM3uEntries[i].tvg_logo='' }
 else{
  const f=(typeof _m3uIconFiles!=='undefined')?_m3uIconFiles.find(function(x){return x.name===name}):null
  if(!f) return
  const full=m3uIconFullUrl(f.url)
  if(m3uIsLanHost(location.host)){
   alert('本页面是从局域网打开，保存的是局域网链接地址，外网无法访问，请用公网域名重新打开本页面，如只在局域网内使用，请忽略本提示。')
  }
  currentM3uEntries[i].tvg_logo=full
 }
 const cell=document.getElementById('m3uIcoTd'+i)
 if(cell) cell.innerHTML=m3uRenderIconCell(i,currentM3uEntries[i])
 autoSaveM3u()
}

function m3uNewIconPrompt(i){
 if(i<0||i>=currentM3uEntries.length) return
 const val=prompt('粘贴台标图片链接（留空则清除当前台标）：', currentM3uEntries[i].tvg_logo||'')
 if(val===null) return
 currentM3uEntries[i].tvg_logo=(val||'').trim()
 const cell=document.getElementById('m3uIcoTd'+i)
 if(cell) cell.innerHTML=m3uRenderIconCell(i,currentM3uEntries[i])
 autoSaveM3u()
}

function showShareUpload(){
 $('modalTitle').textContent='📤 上传文件到共享目录'
 $('modalBody').innerHTML=`
  <div class="dropzone" id="shareDropzone" onclick="document.getElementById('shareFileInput').click()"
    ondragover="event.preventDefault();this.classList.add('dragover')"
    ondragleave="this.classList.remove('dragover')"
    ondrop="event.preventDefault();this.classList.remove('dragover');handleShareDrop(event)">
   <div class="icon">📁</div>
   <div class="text">拖拽文件到此处或点击选择</div>
   <div class="hint">支持任何文件格式</div>
  </div>
  <input type="file" id="shareFileInput" style="display:none" onchange="handleShareFileSelect(event)" multiple>`
 $('modalActions').innerHTML=`<button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

function handleShareDrop(e){uploadMultipleShare(e.dataTransfer.files)}
function handleShareFileSelect(e){uploadMultipleShare(e.target.files)}
async function uploadMultipleShare(files){let ok=0;for(let i=0;i<files.length;i++){const f=files[i];try{const r=await new Promise(rs=>{const rd=new FileReader();rd.onload=()=>rs(rd.result);rd.readAsDataURL(f)});const r2=await fetch('/api/share/upload',{method:'POST',body:r.split(',')[1],headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(f.name)}});if(!r2.ok){showToast('上传失败: HTTP '+r2.status,'error');continue};const d=await r2.json();if(d.success)ok++}catch(e){showToast('上传出错: '+e.message,'error')}}showToast('上传完成 '+ok+'/'+files.length+' 个','success');closeModal();loadShareFiles()}

function copyShareLink(name){
 const encPath=name.split('/').map(encodeURIComponent).join('/')
 const url=location.protocol+'//'+location.host+'/api/download/share/'+encPath
 const input=document.createElement('input')
 input.value=url;document.body.appendChild(input)
 input.select();document.execCommand('copy')
 document.body.removeChild(input)
 showToast('✅ 已复制下载链接（需登录后才能访问）','success')
}

async function deleteShareFile(name){
 if(!await customConfirm('确定删除 '+name+'？'))return
 const encPath=name.split('/').map(encodeURIComponent).join('/')
 const r=await fetch('/api/share/'+encPath,{method:'DELETE'})
 const d=await r.json()
 if(d.success){showToast('已删除','success');loadShareFiles(currentSharePath)}
 else showToast('删除失败','error')
}

let currentSharePath=''

// ★ 修复：所有路径参数改用 jsArg
async function loadShareFiles(path){
 if(path===undefined) path=currentSharePath||''
 currentSharePath=path||''
 const tbody=document.getElementById('shareFileBody')
 if(!tbody) return
 tbody.innerHTML='<tr><td colspan="4" style="color:var(--text-secondary);text-align:center;padding:12px">加载中...</td></tr>'
 try{
  const qs=currentSharePath?('?path='+encodeURIComponent(currentSharePath)):''
  const r=await fetch('/api/share/files'+qs)
  const d=await r.json()
  if(d.error){ tbody.innerHTML='<tr><td colspan="4" style="color:var(--danger,#e5484d);text-align:center;padding:12px">'+escapeHtml(d.error)+'</td></tr>'; return }
  const items=d.files||[]
  if(!items.length){
   tbody.innerHTML='<tr><td colspan="4" style="color:var(--text-secondary);text-align:center;padding:12px">此目录为空</td></tr>'
   renderShareBreadcrumb(d.current_path||'')
   return
  }
  let html=''
  items.forEach(it=>{
   if(it.is_dir){
    html+='<tr>'+
     '<td style="cursor:pointer" onclick="loadShareFiles('+jsArg(it.path)+')">📁 '+escapeHtml(it.name)+'</td>'+
     '<td style="color:var(--text-secondary)">—</td>'+
     '<td style="color:var(--text-secondary)">'+escapeHtml(it.mtime||'')+'</td>'+
     '<td style="text-align:center"><button class="btn btn-sm" onclick="loadShareFiles('+jsArg(it.path)+')">进入</button></td>'+
    '</tr>'
   }
  })
  items.forEach(it=>{
   if(!it.is_dir){
    const size=(it.size/1024).toFixed(1)+' KB'
    html+='<tr>'+
     '<td style="word-break:break-all">📄 '+escapeHtml(it.name)+'</td>'+
     '<td>'+size+'</td>'+
     '<td style="color:var(--text-secondary)">'+escapeHtml(it.mtime||'')+'</td>'+
     '<td style="text-align:center;white-space:nowrap">'+
      '<button class="btn btn-sm" title="复制链接" onclick="copyShareLink('+jsArg(it.path)+')">📋</button>'+
      '<button class="btn btn-sm" title="下载" onclick="window.open(\'/api/download/share/'+it.path.split('/').map(encodeURIComponent).join('/')+'\',\'_blank\')">⬇️</button>'+
      '<button class="btn btn-sm btn-danger" title="删除" onclick="deleteShareFile('+jsArg(it.path)+')">🗑️</button>'+
     '</td>'+
    '</tr>'
   }
  })
  tbody.innerHTML=html
  renderShareBreadcrumb(d.current_path||'')
 }catch(e){
  tbody.innerHTML='<tr><td colspan="4" style="color:var(--danger,#e5484d);text-align:center;padding:12px">加载共享目录失败：'+escapeHtml(e.message)+'</td></tr>'
 }
}

function renderShareBreadcrumb(path){
 const bar=document.getElementById('sharePathBar')
 if(!bar) return
 const parts=(path?path.split('/'):[]).filter(Boolean)
 let crumbs=''
 let acc=''
 crumbs+='<span style="cursor:pointer;color:var(--primary)" onclick="loadShareFiles('+jsArg('')+')">根目录</span>'
 parts.forEach((p,i)=>{
  acc=acc?(acc+'/'+p):p
  crumbs+=' <span style="color:var(--text-secondary)">/</span> <span style="cursor:pointer;color:var(--primary)" onclick="loadShareFiles('+jsArg(acc)+')">'+escapeHtml(p)+'</span>'
 })
 if(parts.length){
  const parent=parts.slice(0,-1).join('/')
  bar.innerHTML='⬆️ <span style="cursor:pointer;color:var(--primary)" onclick="loadShareFiles('+jsArg(parent)+')">返回上级</span> &nbsp;|&nbsp; '+crumbs
 }else{
  bar.innerHTML=crumbs
 }
}

// ==================== 频道台标 ====================
async function refreshIcons(){
 try {
  const r=await fetch('/api/icons');const d=await r.json()
  const files=d.files||[]
  const grid=$('iconGrid')
  if(!grid) return;
  if(!files.length){grid.innerHTML='<div class="empty" style="grid-column:1/-1">暂无台标，点击上传</div>';return}
  // ★ 修复：文件名和 url 用 jsArg 传入 onclick
  grid.innerHTML=files.map(f=>
   `<div style="border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center">
    <img src="${escapeHtml(f.url)}" style="max-width:80px;max-height:80px;display:block;margin:0 auto 8px;object-fit:contain">
    <div style="font-size:11px;word-break:break-all;margin-bottom:4px">${escapeHtml(f.name)}</div>
    <div style="font-size:10px;color:var(--text-secondary);margin-bottom:6px">${(f.size/1024).toFixed(1)}KB</div>
    <div style="display:flex;gap:4px;justify-content:center">
     <button class="btn btn-sm" onclick="copyIconUrl(${jsArg(f.url)})">📋 复制</button>
     <button class="btn btn-sm btn-danger" onclick="deleteIcon(${jsArg(f.name)})">🗑️</button>
    </div>
   </div>`
  ).join('')
 } catch(e) {
  if($('iconGrid')) $('iconGrid').innerHTML = '<div class="empty" style="color:red">加载台标失败: '+escapeHtml(e.message)+'</div>';
 }
}

function copyIconUrl(url){
 const input=document.createElement('input')
 input.value=location.protocol+'//'+location.host+url
 document.body.appendChild(input)
 input.select();document.execCommand('copy')
 document.body.removeChild(input)
 showToast('已复制台标链接','success')
}

function showIconUpload(){
 $('modalTitle').textContent='📤 上传频道台标'
 $('modalBody').innerHTML=`
  <div class="dropzone" id="iconDropzone" onclick="document.getElementById('iconFileInput').click()"
    ondragover="event.preventDefault();this.classList.add('dragover')"
    ondragleave="this.classList.remove('dragover')"
    ondrop="event.preventDefault();this.classList.remove('dragover');handleIconDrop(event)">
   <div class="icon">🖼️</div>
   <div class="text">拖拽图片到此处或点击选择</div>
   <div class="hint">支持 PNG/JPG/SVG/GIF/WebP</div>
  </div>
  <input type="file" id="iconFileInput" accept=".png,.jpg,.jpeg,.gif,.svg,.webp,.ico" style="display:none" onchange="handleIconFileSelect(event)" multiple>`
 $('modalActions').innerHTML=`<button class="btn" onclick="closeModal()">取消</button>`
 $('modal').classList.add('active')
}

function handleIconDrop(e){uploadMultipleIcon(e.dataTransfer.files)}
function handleIconFileSelect(e){uploadMultipleIcon(e.target.files)}
async function uploadMultipleIcon(files){let ok=0;for(let i=0;i<files.length;i++){const f=files[i];try{const r=await new Promise(rs=>{const rd=new FileReader();rd.onload=()=>rs(rd.result);rd.readAsDataURL(f)});const r2=await fetch('/api/icons/upload',{method:'POST',body:r.split(',')[1],headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(f.name)}});if(!r2.ok){showToast('上传失败: HTTP '+r2.status,'error');continue};const d=await r2.json();if(d.success)ok++}catch(e){showToast('上传出错: '+e.message,'error')}}showToast('上传完成 '+ok+'/'+files.length+' 个','success');closeModal();refreshIcons()}
async function deleteIcon(name){
 if(!await customConfirm('确定删除 '+name+'？'))return
 const r=await fetch('/api/icons/'+encodeURIComponent(name),{method:'DELETE'})
 const d=await r.json()
 if(d.success){showToast('已删除','success');refreshIcons()}
 else showToast('删除失败','error')
}

async function saveCronSettings(){
 const data={
  cron_time:$('cronTime').value,
  cron_enabled:$('cronEnabled').checked,
 }
 try{
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  const d=await r.json()
  if(d.success){
   showToast('定时任务设置已保存','success')
   refreshStatus()
  }else{
   showToast('保存失败','error')
  }
 }catch(e){
  showToast('保存失败: '+(e.message||e),'error')
 }
 return false
}

async function loadCronSettings(){
  try{
    const cronR=await fetch('/api/settings');
    const cronD=await cronR.json();
    if($('cronTime')) $('cronTime').value=cronD.cron_time||'07:30';
    if($('cronEnabled')) $('cronEnabled').checked=cronD.cron_enabled!==false;
  }catch(e){}
}

// ==================== 日志 ====================
let logCurrentCategory='全部'
let logRefreshTimer=null

let epgSourcePage=1
const EPG_SOURCE_PAGE_SIZE=5

async function refreshGlobalLogs(){
 const r=await fetch('/api/logs/global?category='+encodeURIComponent(logCurrentCategory)+'&limit=300')
 const d=await r.json()
 const entries=d.entries||[]
 const cats=d.categories||[]
 const sorted=entries.slice().reverse()
 const txt=sorted.map(e=>e.line||('['+e.timestamp+'] '+e.message)).join('\n')||'= 暂无日志 ='
 const dash=$('dashLogContent');if(dash){dash.textContent=txt;dash.scrollTop=dash.scrollHeight}
 const dashInfo=$('dashLogCountInfo');if(dashInfo)dashInfo.textContent='显示 '+entries.length+' 条'
}

function switchLogCategory(cat){
 logCurrentCategory=cat
 document.querySelectorAll('[data-dashlogcat]').forEach(b=>{
  b.classList.toggle('btn-primary',b.dataset.dashlogcat===cat)
 })
 refreshGlobalLogs()
}

// ==================== 认证 ====================
if(!window.fetch){};const _origFetch=window.fetch;window.fetch=function(u,o){o=o||{};o.credentials=o.credentials||'include';return _origFetch.call(this,u,o)}

async function checkAuth(){
 try{
  const r=await fetch('/api/auth/check',{credentials:'include'});const d=await r.json()
  if(d.logged_in){$('loginOverlay').classList.add('hidden');return true}
 }catch(e){}
 $('loginOverlay').classList.remove('hidden');return false
}
async function doLogin(){
 const u=$('loginUser').value.trim(),p=$('loginPass').value
 if(!u||!p){$('loginError').textContent='请输入用户名和密码';$('loginError').style.display='block';return}
 $('loginError').style.display='none';$('loginLoading').style.display='block'
 try{
  const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})})
  const d=await r.json()
  if(d.success){
    $('loginOverlay').classList.add('hidden')
    $('loginLoading').style.display='none'
    location.reload(); // ★ 修改这里：直接刷新页面
  }
  else{$('loginError').textContent='用户名或密码错误';$('loginError').style.display='block';$('loginLoading').style.display='none'}
 }catch(e){$('loginError').textContent='连接失败';$('loginError').style.display='block';$('loginLoading').style.display='none'}
}
async function doLogout(){
 const r=await fetch('/api/auth/logout',{method:'POST',credentials:'include'})
 if(r.ok){location.reload()}
}
async function changePassword(){
 const old=$('authOldPass').value,nu=$('authNewPass').value
 if(!old||!nu){showToast('请填写完整','error');return}
 if(nu.length<4){showToast('新密码至少4位','error');return}
 const d=await(await fetch('/api/auth/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:old,new_password:nu}),credentials:'include'})).json()
 if(d.success){showToast('密码已修改','success');$('authOldPass').value='';$('authNewPass').value=''}
 else showToast(d.detail||'修改失败','error')
}
async function changeUsername(){
 const nu=$('authNewUser').value.trim()
 if(!nu){showToast('用户名不能为空','error');return}
 if(nu.length>20){showToast('用户名最多20个字符','error');return}
 const d=await(await fetch('/api/auth/change-username',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_username:nu}),credentials:'include'})).json()
 if(d.success){showToast('用户名已修改','success');$('authCurrentUser').value=nu;$('authNewUser').value=''}
 else showToast(d.detail||'修改失败','error')
}
async function loadAuthPage(){
 try {
   const d=await(await fetch('/api/auth/username',{credentials:'include'})).json()
   if($('authCurrentUser')) $('authCurrentUser').value = d.username || '未知'
 } catch(e) {
   if($('authCurrentUser')) $('authCurrentUser').value = '加载失败'
 }
}

function closeModal(){
  const m = $('modal');
  if (!m) return;
  m.classList.remove('active');
  m.style.zIndex = '';     // ★ 顺手重置
}

/* ==================== 电信快捷入口 ==================== */
function jumpToTelecom(){
  const tab = document.querySelector('.tab[data-tab="telecom"]');
  if (tab) tab.click();
}

async function refreshTelecomStatus(){
  const el = document.getElementById('epgTelecomStatus')
  try {
    const r = await fetch('/api/telecom/status');
    const d = await r.json();
    let html;
    if (!d.configured || d.config_error){
      html = '<span style="color:var(--danger)">❌ ' + escapeHtml(d.config_error || '未配置（缺 UserID / mac / STBID）') + '</span>';
    } else {
      let cache = '';
      try{
        const cd = await (await fetch('/api/telecom/cached-channels')).json();
        cache = ' · 已缓存 <b>' + (cd.count||0) + '</b> 个频道' + (cd.updated ? '（' + escapeHtml(cd.updated) + '）' : '');
      }catch(e){}
      // 「启用」只控制定时任务；手动按钮（认证/获取频道/下载EPG）任何时候都能用
      html = '<span style="color:var(--success)">✅ 已配置</span> · '
        + (d.enabled
            ? '<span style="color:var(--success)">定时任务已启用</span>'
            : '<span style="color:var(--text-secondary)" title="只影响定时任务，手动按钮不受影响">定时任务未启用（手动按钮照常可用）</span>')
        + cache;
    }
    if (el) el.innerHTML = html;
  } catch(e) {
    if (el) el.textContent = '状态获取失败';
  }
}

async function telecomRunEpgOnly(){
  try {
    showToast('正在启动电信EPG任务...', 'success');
    const r = await fetch('/api/telecom/download-all-epg', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({config:{}, days:3})
    });
    const d = await r.json();
    if (!d.success) { showToast('启动失败: ' + (d.message||''), 'error'); return; }
    showToast('已启动，进度见「电信EPG」页的执行日志', 'success');
    if (window.clearGlobalProgress) clearGlobalProgress();
    if (window.refreshTelecomLog) refreshTelecomLog();
    if (window.startTelecomLogPolling) startTelecomLogPolling();
    jumpToTelecom();
  } catch(e) { showToast('失败: ' + e.message, 'error'); }
}

/* ==================== M3U 合并 ==================== */
async function showMergeM3u(){
  const r = await fetch('/api/m3u');
  const d = await r.json();
  const files = d.files || [];
  if (files.length < 2) { showToast('至少需要 2 个文件', 'error'); return; }
  $('modalTitle').textContent = '🔀 合并 M3U/TXT 文件';
  $('modalBody').innerHTML = `
    <div class="form-group"><label>选择文件</label>
      <div style="max-height:220px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:8px">
        ${files.map(f => `
          <label style="display:flex;align-items:center;gap:6px;padding:4px 0;cursor:pointer;font-size:13px">
            <input type="checkbox" value="${escapeHtml(f.name)}" class="merge-file-cb" checked style="width:auto">
            <span>${escapeHtml(f.name)}</span>
            <span style="color:var(--text-secondary);font-size:11px">(${f.channel_count||0} 频道)</span>
          </label>`).join('')}
      </div>
    </div>
    <div class="form-group"><label>合并模式</label>
      <select id="mergeMode">
        <option value="url">按(频道名 + URL)去重（推荐）</option>
        <option value="channel">按频道名去重（多URL合并）</option>
      </select>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">
        💡 通用规则：URL 相同但名字不同的只保留一条（先出现的名字优先，并自动补全缺失字段）
      </div>
    </div>
    <div class="form-group">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="mergeKeepOrder" checked style="width:auto">
        <span>保持第一个文件的频道顺序（其它文件的新频道追加到末尾）</span>
      </label>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">
        💡 勾选后不按分组/名称重排；把「电信」文件放第一个即为「以电信为主」
      </div>
    </div>
    <div class="form-group"><label style="display:flex;justify-content:space-between">
      <span>分组设置（可选，按频道名匹配）</span>
      <button class="btn btn-sm" type="button" onclick="addMergeGroup()">➕ 添加分组</button></label>
      <div id="mergeGroupList"></div>
    </div>
    <div class="form-group">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="mergeRtpEnabled" onchange="toggleMergeRtp()" style="width:auto">
        <span>启用 RTP 代理地址转换（rtp:// → http://代理/rtp/）</span>
      </label>
      <div id="mergeRtpBlock" style="display:none;padding-left:22px;margin-top:8px">
        <div style="display:flex;gap:6px;align-items:center">
          <span style="font-size:12px;color:var(--text-secondary);white-space:nowrap;min-width:60px">代理地址：</span>
          <input id="mergeRtpProxy" placeholder="如 192.168.6.1:8000" style="flex:1;font-size:12px">
        </div>
      </div>
    </div>
    <div class="form-group">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="mergeFccEnabled" onchange="toggleMergeFcc()" style="width:auto">
        <span>启用 FCC 参数补全</span>
      </label>
      <div id="mergeFccBlock" style="display:none;padding-left:22px;margin-top:8px">
        <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
          <span style="font-size:12px;color:var(--text-secondary);white-space:nowrap;min-width:60px">运营商：</span>
          <select id="mergeFccPreset" onchange="onFccPresetChange()" style="flex:1;font-size:12px">
            <option value="中国电信">中国电信</option>
            <option value="中国联通">中国联通</option>
            <option value="中国移动">中国移动</option>
            <option value="自定义">自定义</option>
          </select>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span style="font-size:12px;color:var(--text-secondary);white-space:nowrap;min-width:60px">FCC地址：</span>
          <input id="mergeFccServer" placeholder="如 139.206.31.41:8027" style="flex:1;font-size:12px">
        </div>
      </div>
    </div>
    <div class="form-group"><label>输出文件名</label>
      <input id="mergeOutputName" value="merged_${Date.now()}.m3u">
    </div>`;
  $('modalActions').innerHTML = `
    <button class="btn btn-primary" onclick="confirmMergeM3u()">🔀 开始合并</button>
    <button class="btn" onclick="closeModal()">取消</button>`;
  $('modal').classList.add('active');
  await loadFccPresetsIntoModal();
  loadRtpProxyIntoModal();
}

function addMergeGroup(){
  const div = document.createElement('div');
  div.style.cssText = 'display:flex;gap:6px;margin-bottom:6px;align-items:flex-start';
  div.innerHTML = `
    <input class="merge-group-name" placeholder="分组名" style="flex:0 0 110px;font-size:12px">
    <textarea class="merge-group-channels" placeholder="频道名，逗号或换行分隔"
      style="flex:1;min-height:44px;font-size:12px;font-family:monospace"></textarea>
    <button class="btn btn-sm btn-danger" type="button" onclick="this.parentElement.remove()" style="flex-shrink:0">✕</button>`;
  $('mergeGroupList').appendChild(div);
}

function toggleMergeRtp(){
  $('mergeRtpBlock').style.display = $('mergeRtpEnabled').checked ? 'block' : 'none';
}

function toggleMergeFcc(){
  $('mergeFccBlock').style.display = $('mergeFccEnabled').checked ? 'block' : 'none';
}

function onFccPresetChange(){
  const preset = $('mergeFccPreset').value;
  if (window._fccPresets && window._fccPresets.presets) {
    const found = window._fccPresets.presets.find(p => p.name === preset);
    $('mergeFccServer').value = found ? (found.server || '') : '';
  }
}

async function loadFccPresetsIntoModal(){
  try {
    const r = await fetch('/api/fcc/presets');
    const d = await r.json();
    window._fccPresets = d;
    $('mergeFccEnabled').checked = d.enabled || false;
    if (d.active) {
      const sel = $('mergeFccPreset');
      for (let opt of sel.options) if (opt.value === d.active) { opt.selected = true; break; }
      const a = (d.presets||[]).find(p => p.name === d.active);
      if (a) $('mergeFccServer').value = a.server || '';
    }
    toggleMergeFcc();
  } catch(e) {}
}

function loadRtpProxyIntoModal(){
  if (window._fccPresets) {
    const d = window._fccPresets;
    $('mergeRtpEnabled').checked = d.rtp_proxy_enabled || false;
    if (d.rtp_proxy) $('mergeRtpProxy').value = d.rtp_proxy;
    toggleMergeRtp();
  }
}

async function confirmMergeM3u(){
  const files = Array.from(document.querySelectorAll('.merge-file-cb:checked')).map(cb => cb.value);
  if (files.length < 2) { showToast('至少选择 2 个文件', 'error'); return; }
  const mergeMode = $('mergeMode').value;
  const keepOrder = $('mergeKeepOrder') ? $('mergeKeepOrder').checked : false;
  const outputName = $('mergeOutputName').value.trim();
  if (!outputName) { showToast('请填写输出文件名', 'error'); return; }

  const groups = {};
  document.querySelectorAll('#mergeGroupList > div').forEach(row => {
    const name = row.querySelector('.merge-group-name').value.trim();
    const raw = row.querySelector('.merge-group-channels').value;
    if (!name) return;
    const list = raw.split(/[\n,，]/).map(s => s.trim()).filter(Boolean);
    if (list.length) groups[name] = list;
  });

  const rtpEnabled = $('mergeRtpEnabled').checked;
  const rtpProxy = $('mergeRtpProxy').value.trim();
  if (rtpEnabled && !rtpProxy) { showToast('启用RTP但未填地址', 'error'); return; }
  const rtpProxyValue = (rtpEnabled && rtpProxy) ? rtpProxy : '';

  const fccEnabled = $('mergeFccEnabled').checked;
  const fccPreset = $('mergeFccPreset').value;
  const fccServer = $('mergeFccServer').value.trim();
  if (fccEnabled && !fccServer) { showToast('启用FCC但未填地址', 'error'); return; }
  const fccConfig = (fccEnabled && fccServer) ? { enabled: true, server: fccServer } : { enabled: false, server: '' };

  const groupRulesPayload = (typeof _groupRules !== 'undefined' && _groupRules.auto_apply)
    ? _groupRules : null;

  const data = window._fccPresets || {
    enabled: false, active: '中国电信',
    presets: [{name:'中国电信',server:''},{name:'中国联通',server:''},{name:'中国移动',server:''}],
    rtp_proxy_enabled: false, rtp_proxy: ''
  };
  data.rtp_proxy_enabled = rtpEnabled;
  if (rtpProxy) data.rtp_proxy = rtpProxy;
  data.enabled = fccEnabled;
  if (fccEnabled && fccServer) {
    data.active = fccPreset;
    let p = (data.presets||[]).find(x => x.name === fccPreset);
    if (p) p.server = fccServer;
    else (data.presets = data.presets||[]).push({name: fccPreset, server: fccServer});
  }
  try {
    await fetch('/api/fcc/presets', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    window._fccPresets = data;
  } catch(e) {}

  try {
    const r = await fetch('/api/m3u/merge', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        files, merge_mode: mergeMode, groups,
        output_name: outputName, fcc_config: fccConfig,
        rtp_proxy: rtpProxyValue,
        group_rules: groupRulesPayload,
        keep_order: keepOrder
      })
    });
    const d = await r.json();
    if (d.success) {
      let msg = `合并成功: ${d.merged_from} 文件 → ${d.entry_count} 频道`;
      if (d.mcast_merged_groups) msg += `（组播去重 ${d.mcast_merged_groups} 组）`;
      if (d.rtp_converted) msg += `（RTP转换 ${d.rtp_converted}）`;
      if (d.fcc_applied) msg += `（FCC补全 ${d.fcc_applied}）`;
      showToast(msg, 'success');
      closeModal();
      renderM3uFiles();
    } else showToast(d.detail || '合并失败', 'error');
  } catch(e) { showToast('合并失败: ' + e.message, 'error'); }
}

/* ==================== M3U 常驻配置卡片 ==================== */
function toggleM3uConfigPanel(){
  const body = $('m3uConfigBody');
  const arrow = $('m3uConfigArrow');
  const hidden = body.style.display === 'none';
  body.style.display = hidden ? 'block' : 'none';
  arrow.textContent = hidden ? '▲ 收起' : '▼ 展开';
}

async function loadM3uConfigPanel(){
  try {
    const r = await fetch('/api/fcc/presets');
    const d = await r.json();
    window._fccPresets = d;
    $('cfgRtpEnabled').checked = d.rtp_proxy_enabled || false;
    $('cfgRtpProxy').value = d.rtp_proxy || '';
    $('cfgFccEnabled').checked = d.enabled || false;
    const sel = $('cfgFccPreset');
    for (let opt of sel.options) if (opt.value === (d.active||'中国电信')) { opt.selected = true; break; }
    const a = (d.presets||[]).find(p => p.name === (d.active||'中国电信'));
    $('cfgFccServer').value = a ? (a.server||'') : '';
    updateM3uConfigSummary();
	await loadGroupRules();
  } catch(e) {}
}

function onCfgFccPresetChange(){
  const p = $('cfgFccPreset').value;
  if (window._fccPresets && window._fccPresets.presets) {
    const f = window._fccPresets.presets.find(x => x.name === p);
    $('cfgFccServer').value = f ? (f.server||'') : '';
  }
}

function updateM3uConfigSummary(){
  const parts = [];
  if ($('cfgRtpEnabled').checked)
    parts.push(`RTP: ${$('cfgRtpProxy').value.trim() || '未填'}`);
  if ($('cfgFccEnabled').checked)
    parts.push(`FCC: ${$('cfgFccServer').value.trim() || '未填'}`);
  $('m3uConfigSummary').textContent = parts.length ? `（${parts.join(' | ')}）` : '（未启用）';
}

async function saveM3uConfig(){
  const rtpEnabled = $('cfgRtpEnabled').checked;
  const rtpProxy = $('cfgRtpProxy').value.trim();
  const fccEnabled = $('cfgFccEnabled').checked;
  const fccPreset = $('cfgFccPreset').value;
  const fccServer = $('cfgFccServer').value.trim();
  if (rtpEnabled && !rtpProxy) { showToast('未填RTP地址', 'error'); return; }
  if (fccEnabled && !fccServer) { showToast('未填FCC地址', 'error'); return; }
  const data = window._fccPresets || {
    enabled: false, active: '中国电信',
    presets: [{name:'中国电信',server:''},{name:'中国联通',server:''},{name:'中国移动',server:''}],
    rtp_proxy_enabled: false, rtp_proxy: ''
  };
  data.rtp_proxy_enabled = rtpEnabled;
  data.rtp_proxy = rtpProxy;
  data.enabled = fccEnabled;
  data.active = fccPreset;
  let p = (data.presets||[]).find(x => x.name === fccPreset);
  if (p) p.server = fccServer;
  else (data.presets = data.presets||[]).push({name: fccPreset, server: fccServer});
  try {
    const r = await fetch('/api/fcc/presets', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (d.success) {
      window._fccPresets = data;
      updateM3uConfigSummary();
      showToast('配置已保存', 'success');
    }
  } catch(e) { showToast('保存失败', 'error'); }
}

document.addEventListener('DOMContentLoaded', function(){
  ['cfgRtpEnabled','cfgRtpProxy','cfgFccEnabled','cfgFccServer'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', updateM3uConfigSummary);
      el.addEventListener('change', updateM3uConfigSummary);
    }
  });
});

/* ==================== 分组规则 ==================== */
let _groupRules = { version: 1, rules: [], default_group: "", auto_apply: false };

async function loadGroupRules() {
  try {
    const r = await fetch('/api/group-rules');
    _groupRules = await r.json();
    if (!_groupRules.rules) _groupRules.rules = [];
    $('grpRulesEnabled').checked = !!_groupRules.auto_apply;
    $('grpDefaultGroup').value = _groupRules.default_group || '';
    renderGroupRules();
  } catch(e) {}
}

function renderGroupRules() {
  const box = $('groupRuleList');
  if (!_groupRules.rules.length) {
    box.innerHTML = '<div style="padding:10px;text-align:center;color:var(--text-secondary);font-size:12px">暂无规则，点「➕ 添加规则」创建</div>';
    return;
  }
  _groupRules.rules.sort((a,b) => (a.priority||999) - (b.priority||999));
  box.innerHTML = _groupRules.rules.map((r, i) => `
    <div style="display:flex;gap:6px;align-items:flex-start;padding:8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;background:#fff">
      <input type="number" value="${r.priority||(i+1)}" title="优先级" 
        onchange="_groupRules.rules[${i}].priority=parseInt(this.value)||${i+1}"
        style="width:44px;font-size:12px;text-align:center;flex-shrink:0">
      <input type="checkbox" ${r.enabled!==false?'checked':''} title="启用"
        onchange="_groupRules.rules[${i}].enabled=this.checked;renderGroupRules()"
        style="width:auto;margin-top:6px;flex-shrink:0">
      <div style="flex:1;min-width:0">
        <div style="display:flex;gap:6px;margin-bottom:4px">
          <input value="${escapeHtml(r.name||'')}" placeholder="分组名，如：央视"
            onchange="_groupRules.rules[${i}].name=this.value"
            style="flex:0 0 110px;font-size:12px">
          <select onchange="_groupRules.rules[${i}].match_type=this.value;renderGroupRules()"
            style="flex:0 0 90px;font-size:12px">
            <option value="keyword" ${(r.match_type||'keyword')==='keyword'?'selected':''}>关键词包含</option>
            <option value="channels" ${r.match_type==='channels'?'selected':''}>精确频道名</option>
          </select>
        </div>
        ${(r.match_type||'keyword') === 'keyword' ? `
          <input value="${escapeHtml((r.keywords||[]).join(', '))}" 
            placeholder="关键词，逗号分隔，如：CCTV, 中央, CGTN"
            onchange="_groupRules.rules[${i}].keywords=this.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)"
            style="width:100%;font-size:12px;font-family:monospace">
        ` : `
          <textarea placeholder="频道名，每行或逗号分隔" 
            onchange="_groupRules.rules[${i}].channels=this.value.split(/[\\n,，]/).map(s=>s.trim()).filter(Boolean)"
            style="width:100%;font-size:12px;font-family:monospace;min-height:44px">${escapeHtml((r.channels||[]).join(', '))}</textarea>
        `}
      </div>
      <button class="btn btn-sm btn-danger" style="flex-shrink:0"
        onclick="_groupRules.rules.splice(${i},1);renderGroupRules()">✕</button>
    </div>
  `).join('');
}

function addGroupRule() {
  const nextPriority = _groupRules.rules.length + 1;
  _groupRules.rules.push({
    id: 'rule_' + Date.now(),
    name: '',
    enabled: true,
    priority: nextPriority,
    match_type: 'keyword',
    keywords: [],
    channels: [],
  });
  renderGroupRules();
}

async function saveGroupRules() {
  _groupRules.auto_apply = $('grpRulesEnabled').checked;
  _groupRules.default_group = $('grpDefaultGroup').value.trim();
  _groupRules.rules = _groupRules.rules.filter(r => (r.name||'').trim());
  try {
    const r = await fetch('/api/group-rules', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_groupRules)
    });
    const d = await r.json();
    if (d.success) {
      showToast(`分组规则已保存（${_groupRules.rules.length} 条）`, 'success');
      renderGroupRules();
    } else {
      showToast('保存失败', 'error');
    }
  } catch(e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

// ==================== 频道总览 ====================
let ovRows = []
let ovLabel = ''

function ovErrMsg(e){
 const m = (e && e.message) ? e.message : String(e || '')
 if (/NetworkError|Failed to fetch|Load failed/i.test(m)) return '服务器无响应，请稍后重试'
 return m
}

async function loadOverviewSources(){
 const sel=$('ovSource'); if(!sel) return
 let files=[],ok=true
 try{
  const r=await fetch('/api/m3u'); const d=await r.json()
  files=(d.files||[]).map(f=>f.name)
 }catch(e){ ok=false }
 if(!ok) return   // 拉不到文件列表就别重建下拉，免得把当前来源冲掉
 const cur=sel.value
 sel.innerHTML='<option value="telecom">📡 电信频道（直播 + 回看）</option>'+
  files.map(n=>'<option value="m3u:'+escapeHtml(n)+'">📄 '+escapeHtml(n)+'</option>').join('')
 if(cur) sel.value=cur
}

async function loadOverview(opts){
 const force=!!(opts&&opts.force)
 const sel=$('ovSource'); if(!sel) return null
 // force：连「来源下拉」一起重建，刚上传/合并的 M3U 文件能立刻出现在列表里
 if(force||!sel.options.length) await loadOverviewSources()
 const src=sel.value||'telecom'
 try{
  const r=await fetch('/api/overview?source='+encodeURIComponent(src))
  const d=await r.json()
  if(!r.ok){showToast('加载失败: '+(d.detail||r.status),'error');return null}
  ovRows=d.rows||[]
  ovLabel=d.label||''
  renderOverview()
  return ovRows.length
 }catch(e){ showToast('加载失败: '+ovErrMsg(e),'error'); return null }
}

// 「🔄 刷新」：重新读来源下拉 + 重新拉数据，并给出可见反馈
async function refreshOverview(btn){
 if(btn){ btn.disabled=true; btn.textContent='⏳ 刷新中...' }
 const n=await loadOverview({force:true})
 if(btn){ btn.disabled=false; btn.textContent='🔄 刷新' }
 if(n===null) return
 const t=new Date().toLocaleTimeString('zh-CN',{hour12:false})
 showToast('已刷新：'+n+' 个频道 · '+t,'success')
}

// 「只看有回看」：勾选/取消都给出明确反馈，避免“数据本来就全有回看”时看着像没反应
function onOvOnlyCatchupChange(){
 const r=renderOverview()
 if(!r) return
 const cb=$('ovOnlyCatchup')
 if(cb&&cb.checked){
  const withCatch=ovRows.filter(x=>!!(x.catchup||x.catchup_source)).length
  if(withCatch===0) showToast('当前来源没有带回看地址的频道','error')
  else if(withCatch===ovRows.length) showToast('共 '+ovRows.length+' 条，全部都有回看地址（筛选后无变化）','success')
  else showToast('只看有回看：'+withCatch+' / '+ovRows.length+' 条','success')
 }
}

function renderOverview(){
 const tbody=$('ovBody'); if(!tbody) return null
 const kw=($('ovSearch')?$('ovSearch').value:'').trim().toLowerCase()
 const onlyCatch=!!($('ovOnlyCatchup')&&$('ovOnlyCatchup').checked)
 const onlyMissing=!!($('ovOnlyMissingLogo')&&$('ovOnlyMissingLogo').checked)
 let rows=ovRows
 if(kw){
  rows=rows.filter(r=>((r.name||'')+' '+(r.tvg_name||'')+' '+(r.logo||'')+' '+(r.live||'')+' '+(r.catchup||'')+' '+(r.tvg_id||'')+' '+(r.group||'')).toLowerCase().indexOf(kw)>=0)
 }
 if(onlyCatch) rows=rows.filter(r=>!!(r.catchup||r.catchup_source))
 if(onlyMissing) rows=rows.filter(r=>!!r.logo_missing)
 const missingTotal=ovRows.filter(r=>!!r.logo_missing).length
 const cnt=$('ovCount')
 if(cnt) cnt.textContent='共 '+rows.length+' 条'
  +(onlyCatch?' · 只看有回看':'')
  +(onlyMissing?' · 只看缺台标':'')
  +(missingTotal?' · 缺台标 '+missingTotal:'')
  +(ovLabel?(' · '+ovLabel):'')
 if(!rows.length){tbody.innerHTML='<tr><td colspan="8" class="empty">没有数据</td></tr>';return {shown:0}}
 tbody.innerHTML=rows.map(r=>{
  const meta=[r.tvg_id?('tvg-id: '+r.tvg_id):'', r.group?('分组: '+r.group):''].filter(Boolean).join('<br>')
  const tf=[r.timeshift==='1'?'✅ 时移':(r.timeshift?('时移='+r.timeshift):''),
            r.fcc?('FCC '+r.fcc):'', r.fec?('FEC '+r.fec):''].filter(Boolean).join('<br>')
  const logoCell=escapeHtml(r.logo||'—')+(r.logo_missing
   ?'<div style="color:#e6a23c">⚠️ 缺台标：把 <b>'+escapeHtml((r.name||'')+'.png')+'</b> 上传到「🖼️ 频道台标」</div>'
   :'')
  return `<tr>
   <td style="word-break:break-all">${escapeHtml(r.name||'')}${r.extra_urls?'<div style="font-size:11px;color:var(--text-secondary)">另有 '+r.extra_urls+' 个地址</div>':''}</td>
   <td style="font-size:11px;word-break:break-all" title="${escapeHtml(r.tvg_name||'')}">${escapeHtml(r.tvg_name||'—')}</td>
   <td style="font-size:11px;word-break:break-all" title="${escapeHtml(r.logo||'')}">${logoCell}</td>
   <td style="font-size:11px;word-break:break-all" title="${escapeHtml(r.live||'')}">${escapeHtml(r.live||'—')}</td>
   <td style="font-size:11px;word-break:break-all" title="${escapeHtml(r.catchup||'')}">${escapeHtml(r.catchup||'—')}</td>
   <td style="text-align:center;font-size:11px">${tf||'—'}</td>
   <td style="font-size:11px">${meta||'—'}</td>
   <td style="text-align:center;white-space:nowrap">
    ${r.live?'<button class="btn btn-sm" onclick="ovCopy('+jsArg(r.live)+',\'直播地址\')" title="复制直播地址">📋直播</button>':''}
    ${(r.catchup||r.catchup_source)?'<button class="btn btn-sm" onclick="ovCopy('+jsArg(r.catchup||r.catchup_source)+',\'回看地址\')" title="复制回看地址">📋回看</button>':''}
    ${r.logo?'<button class="btn btn-sm" onclick="ovCopy('+jsArg(r.logo)+',\'台标URL\')" title="复制台标地址">📋台标</button>':''}
   </td>
  </tr>`
 }).join('')
 
  // ★ 新增这两行：在每次渲染完数据后，立刻重新应用列顺序和列宽
 applyTableColumnOrder($('ovTable'))
 applyTableColWidths($('ovTable'))
 return {shown:rows.length}
}

function ovCopy(text,label){
 if(!text) return
 const ta=document.createElement('textarea')
 ta.value=text
 ta.style.position='fixed'; ta.style.opacity='0'
 document.body.appendChild(ta); ta.select()
 try{ document.execCommand('copy'); showToast('已复制'+(label||''),'success') }
 catch(e){ showToast('复制失败','error') }
 document.body.removeChild(ta)
}
window.ovCopy = ovCopy

function exportOverviewCsv(){
 if(!ovRows.length){showToast('没有数据','error');return}
 const esc=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"'
 const lines=[['频道','TVG-NAME','台标URL','直播地址','回看地址','时移','FCC/FEC','TVG-ID','分组'].map(esc).join(',')]
 ovRows.forEach(r=>{
  lines.push([r.name, r.tvg_name, r.logo, r.live, r.catchup,
   (r.timeshift==='1'?'支持':(r.timeshift||'')),
   (r.fcc||'')+(r.fec?(' / FEC '+r.fec):''),
   r.tvg_id, r.group].map(esc).join(','))
 })
 const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'})
 const a=document.createElement('a')
 a.href=URL.createObjectURL(blob)
 a.download='channels_overview_'+(new Date().toISOString().slice(0,10))+'.csv'
 document.body.appendChild(a); a.click(); document.body.removeChild(a)
 URL.revokeObjectURL(a.href)
 showToast('已导出 CSV','success')
}

// ==================== 初始化 ====================
async function init(){
 const authed=await checkAuth();if(!authed)return
 await refreshStatus()
 await loadChannels()
 await loadEpgSources()
 await loadEpgExtracts()

 loadXmlLink()
 renderM3uFiles()
 const m3uFile=localStorage.getItem('m3u_editing')
 if(m3uFile) setTimeout(()=>editM3uFile(m3uFile),500)
 // ★ 原来这里调用的是未定义的 loadLogs()：一抛 ReferenceError，init 后面全都不执行
 //   （保存的页签不恢复、定时任务表单不回填、总览页打开是空的）
 refreshGlobalLogs().catch(()=>{})
 try{ loadColumnOrder() }catch(e){}
 try{ loadColumnWidths() }catch(e){}
 let savedTab=localStorage.getItem('iptv_active_tab')
 if(savedTab==='channels') savedTab='dashboard'   // 「频道管理」已隐藏
 try{
  if(savedTab&&savedTab!=='dashboard'){
   const tab=document.querySelector('.tab[data-tab="'+savedTab+'"]')
   if(tab){
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'))
    tab.classList.add('active')
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'))
    const panel=document.getElementById('panel-'+savedTab)
    if(panel) panel.classList.add('active')
    if(savedTab==='channels'){renderChannels();setTimeout(reapplyColWidths,50)}
    if(savedTab==='epg'){renderEpgSources();renderEpgExtracts();setTimeout(reapplyColWidths,100)}
    if(savedTab==='m3u'){renderM3uFiles();setTimeout(reapplyColWidths,50)}
    if(savedTab==='epgchannels'){loadEpgChannels();setTimeout(reapplyColWidths,50)}
    if(savedTab==='overview'){loadOverview()}
    if(savedTab==='icons'){refreshIcons()}
     if(savedTab==='sharedir'){try{loadShareFiles()}catch(e){}}
    if(savedTab==='auth')loadAuthPage()
   }
  }
 }catch(e){ console.error('恢复上次页签失败:',e) }
 loadCronSettings();
 
 // 「合并工具配置」默认收起（避免占屏，需要时点标题展开）
 if($('m3uConfigBody')) $('m3uConfigBody').style.display='none'
 if($('m3uConfigArrow')) $('m3uConfigArrow').textContent='▼ 展开'

 refreshTelecomStatus();
 if(window.loadTelecomSchedule) loadTelecomSchedule();
 if(window.refreshTelecomLog) refreshTelecomLog();
}


init()