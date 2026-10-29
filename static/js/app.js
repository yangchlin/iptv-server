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

// ★ 新增：EPG 管理勾选、M3U 勾选、外部 EPG 失败告警
let _epgChSel = new Set();        // Set<logic_id>
let _m3uSel = new Set();          // Set<index>
let _epgFailures = [];

// ==================== 工具函数 ====================
function $(id){return document.getElementById(id)}
function qs(s){return document.querySelector(s)}
function qsa(s){return document.querySelectorAll(s)}

function escapeHtml(s){
  if(!s&&s!==0) return '';
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function jsArg(x){
  return escapeHtml(JSON.stringify(String(x == null ? '' : x)));
}

function showToast(msg,type){
 const d=document.createElement('div');
 d.style.cssText=`position:fixed;top:70px;left:50%;transform:translateX(-50%) translateY(-20px);padding:12px 24px;border-radius:8px;z-index:10000;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.12);opacity:0;transition:all .3s cubic-bezier(0.16,1,0.3,1);
 background:${type==='error'?'#fff2f0':type==='success'?'#f6ffed':'#fff7e6'};
 color:${type==='error'?'#ff4d4f':type==='success'?'#52c41a':'#d46b08'};
 border:1px solid ${type==='error'?'#ffccc7':type==='success'?'#b7eb8f':'#ffe58f'};
 display:flex;align-items:center;gap:8px;font-weight:500;`;
 
 const icon = type === 'error' ? '❌' : (type === 'success' ? '✅' : 'ℹ️');
 d.innerHTML = `<span style="font-size:16px">${icon}</span><span>${escapeHtml(msg)}</span>`;

 document.body.appendChild(d);
 
 requestAnimationFrame(() => {
   d.style.transform = 'translateX(-50%) translateY(0)';
   d.style.opacity = '1';
 });

 // 允许传入自定义停留时间，默认 3 秒
 const stayTime = arguments[2] || 3000;
 setTimeout(()=>{
   d.style.opacity='0';
   d.style.transform='translateX(-50%) translateY(-20px)';
   setTimeout(()=>d.remove(),300)
 }, stayTime)
}

function customConfirm(msg, title = '确认操作', okText = '确定', cancelText = '取消') {
  return new Promise((resolve) => {
    const oldTitle = $('modalTitle').textContent;
    const oldBody = $('modalBody').innerHTML;
    const oldActions = $('modalActions').innerHTML;
    
    $('modalTitle').textContent = title;
    $('modalBody').innerHTML = `<div style="padding: 10px 0; font-size: 14px; line-height: 1.6; color: var(--text);">${escapeHtml(msg).replace(/\n/g, '<br>')}</div>`;
    $('modalActions').innerHTML = `
      <button class="btn" id="customConfirmCancel">${cancelText}</button>
      <button class="btn btn-primary" id="customConfirmOk">${okText}</button>
    `;
    
    $('modal').classList.add('active');
    
    const cleanup = () => {
      $('modal').classList.remove('active');
      $('modalTitle').textContent = oldTitle;
      $('modalBody').innerHTML = oldBody;
      $('modalActions').innerHTML = oldActions;
    };

    $('customConfirmOk').onclick = () => { cleanup(); resolve(true); };
    $('customConfirmCancel').onclick = () => { cleanup(); resolve(false); };
    
    $('modal').onclick = (e) => {
      if (e.target === $('modal')) { cleanup(); resolve(false); }
    };
  });
}

// ==================== 列宽拖拽 ====================
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

function ensureResizeHandles(root){
 ;(root || document).querySelectorAll('table').forEach(tbl=>{
  const heads = tbl.querySelectorAll('thead tr:last-child th')
  heads.forEach((th, i)=>{
   if(i >= heads.length - 1) return
   if(th.querySelector('.resize-handle')) return
   const span = document.createElement('span')
   span.className = 'resize-handle'
   th.appendChild(span)
  })
  applyTableColumnOrder(tbl)
  applyTableColWidths(tbl)
 })
}

document.addEventListener('mousedown', function(e){
 const handle = e.target && e.target.closest ? e.target.closest('th > .resize-handle') : null
 if(!handle) return
 e.preventDefault()
 const th = handle.parentElement
 const table = th ? th.closest('table') : null
 if(!table) return
 const row = th.parentElement
 const idx = Array.from(row.children).indexOf(th)
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

// ==================== 列顺序 ====================
let _colOrder = {}
let _colOrderLoaded = false
let _colNatural = {}

function colName(th){
 return ((th && th.textContent) || '').trim().substring(0, 20)
}

function headerNames(tbl){
 const headRow = tbl && tbl.querySelector('thead tr:last-child')
 return headRow ? Array.from(headRow.children).map(colName) : null
}

function applyColumnOrder(tbl, order){
 if(!tbl) return false
 const key = tableKey(tbl)
 const want = order || _colOrder[key]
 if(!want || !want.length) return false
 const names = headerNames(tbl)
 if(!names) return false
 if(!_colNatural[key]) _colNatural[key] = names.slice()
 const nat = _colNatural[key]
 if(nat.length !== want.length) return false
 const sig = want.join('\u0001')
 let moved = false
 tbl.querySelectorAll('tr').forEach(tr=>{
  if(tr.children.length !== nat.length) return
  const cur = tr.dataset.colOrder ? tr.dataset.colOrder.split('\u0001') : nat
  const idxs = want.map(n => cur.indexOf(n))
  if(idxs.some(i => i < 0)) return
  tr.dataset.colOrder = sig
  if(idxs.every((v, i) => v === i)) return
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
 if(e.target.closest('.resize-handle')) return
 if(e.target.closest('input,select,button,a,textarea,label')) return
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

// ==================== 表格行拖动高亮 ====================
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
    if(tab.dataset.tab==='telecom'){if(window.loadTelecomSchedule)loadTelecomSchedule();if(window.loadTelecomPlatform)loadTelecomPlatform();if(window.refreshTelecomLog)refreshTelecomLog();refreshTelecomStatus()}
	if(tab.dataset.tab==='telecom_channels'){if(window.loadTelecomChannelsPage)loadTelecomChannelsPage();}
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
  if(!r.ok){
    if(r.status === 401) throw new Error('未登录或登录已过期');
    throw new Error('HTTP ' + r.status);
  }
  const d=await r.json()
  let sysStatus = d.downloading ? '⚡ 任务执行中' : '🟢 运行中'
  if(d.telecom_platform_label) sysStatus += ' · 电信平台：' + d.telecom_platform_label
  $('statusInfo').textContent = sysStatus
  $('clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false})
  if(d.stats){
   $('statChannels').textContent = d.stats.channel_total ?? '-'
   $('statIcons').textContent = d.stats.icon_count ?? '-'
   $('statXmlSize').textContent = d.stats.xml_size ?? '-'
  }
 }catch(e){
  let errMsg = e.message || '未知错误';
  if (/NetworkError|Failed to fetch|Load failed/i.test(errMsg)) {
    errMsg = '网络请求失败或已被拦截';
  }
  $('statusInfo').textContent = '🔴 ' + errMsg;
 }
}

setInterval(refreshStatus,10000)
setInterval(()=>{$('clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false})},1000)

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

async function loadChannels(){
 const r=await fetch('/api/channels');const d=await r.json()
 channels=d.channels||[]
}
let dragIdx=null

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
   loadEpgSources()
  }
 }catch(e){}
}

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
   if(line && line.indexOf('[电信]')===-1){
    $('gpTitle').textContent=line.replace(/\n/g,' ').substring(0,120)
   }else{
    $('gpTitle').textContent='就绪'
   }
  }
 }catch(e){}
}

setInterval(pollGlobalProgress,1000)

async function buildXml(){
 if(downloading){showToast('已有任务在运行','error');return}
 downloading=true
 try{
  const r=await fetch('/api/xml/build',{method:'POST'})
  const d=await r.json()
  if(d.success){
   showToast(`XML生成成功: ${d.channel_count||0}频道, ${d.programme_count||0}节目`,'success')
   loadXmlLink()
  } else showToast('XML生成失败: '+(d.error||'未知错误'),'error')
 }catch(e){
  showToast('XML生成失败: '+(e.message||'网络异常'),'error')
 }finally{
  downloading=false
  refreshStatus()
 }
}

async function manualCleanup(){
 if(downloading){showToast('已有任务在运行','error');return}
 downloading=true
 try{
  const r=await fetch('/api/cleanup',{method:'POST'})
  const d=await r.json()
  if(d.logs&&d.logs.length){
   showToast(`清理完成，共${d.logs.length}条记录`,'success')
  }else{
   showToast('清理完成，无需清理','success')
  }
  refreshGlobalLogs()
  switchLogCategory('清理')
 }catch(e){
  showToast('清理失败: '+(e.message||'网络异常'),'error')
 }finally{
  downloading=false
  refreshStatus()
 }
}

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
 if(!total){tbody.innerHTML='<tr><td colspan="8" class="empty">暂无 EPG 源 —— 点「➕ 添加源」，或直接编辑 <code>data/config/app_config.json</code> 里的 <code>epg_sources</code></td></tr>';$('epgSourcePagination').innerHTML='';return}
 tbody.innerHTML=page.map((s,i)=>{
  const realIdx=start+i
  return `<tr draggable="true"
    ondragstart="epgSrcDragStart(event,${realIdx})"
    ondrop="epgSrcDrop(event,${realIdx})"
    ondragover="event.preventDefault()">
   <td style="cursor:grab;text-align:center">☰</td>
   
   <!-- URL 列 -->
   <td style="word-break:break-all;" title="${escapeHtml(s.url)}">${escapeHtml(s.url)}</td>
   
   <!-- 备注列 -->
   <td><input value="${escapeHtml(s.remark||'')}" style="border:none;background:transparent;width:100%" onchange="updateEpgSourceRemark(${realIdx},this.value)"></td>
   
   <!-- 规则列 -->
   <td style="text-align:center"><span class="badge badge-success">${escapeHtml(s.rule||'auto')}</span></td>
   
   <!-- 缓存文件列 -->
   <td style="color:var(--text-secondary);word-break:break-all;" title="缓存文件：${escapeHtml(s.cache_file||s.cache_target||s.cache_name||'—')}">
     ${escapeHtml(s.cache_file||s.cache_target||s.cache_name||'—')}
   </td>
   
   <!-- 状态列 -->
   <td style="text-align:center">
     ${s.parked ? '<span class="badge badge-danger">已挂起</span>' : 
       (s.downloaded ? '<span class="badge badge-success">已下载</span>' : '<span class="badge badge-danger">未下载</span>')}
   </td>
   
   <!-- 开关列 -->
   <td style="text-align:center"><button class="badge ${s.enabled!==false?'badge-success':'badge-danger'}" style="border:0;cursor:pointer" onclick="toggleEpgSource(${realIdx})" title="点击切换 启用 / 禁用">${s.enabled!==false?'启用':'禁用'}</button></td>
   
   <!-- 操作列 -->
   <td style="text-align:center;">
   <button class="btn btn-sm" onclick="downloadSingleEpgSource(${realIdx})" title="下载该源（下载后自动合并，状态会变为已下载）">⬇️</button>
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

async function clearAllEpgCache() {
  if (!await customConfirm('⚠️ 确定要清空所有缓存文件吗？\n\n这会删除所有已下载的 .xml 缓存，导致源频道列表变空。\n如果你之前加过源，需要重新点击「⬇️ 下载EPG」才能再次搜索和提取。', '清空 EPG 缓存')) {
    return;
  }
  
  try {
    showToast('正在清空缓存...', 'info');
    const r = await fetch('/api/epg/clear-cache', { method: 'POST' });
    const d = await r.json();
    
    if (d.success) {
      showToast(d.message || '缓存已清空', 'success');
      // 1. 刷新源列表（状态会变成“未下载”）
      await loadEpgSources();
      // 2. 刷新提取弹窗里的列表（会变成空白）
      await loadEpgPicker();
      await refreshEpgPickSummary();
    } else {
      showToast(d.message || '清空失败', 'error');
    }
  } catch (e) {
    showToast('清空失败: ' + e.message, 'error');
  }
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
 
 const p=$('panel-epgchannels')
 if(p && p.classList.contains('active')) renderEpgChannels()
}

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
  
  let srcTag = '';
  const st = c.source_type || '';
  if (st === 'telecom') srcTag = '[' + (c.source_remark || '电信') + '] ';
  else if (st === 'manual') srcTag = '[手动] ';
  else if (st === 'extract') srcTag = '[' + (c.source_remark || '外部EPG') + '] ';
  else if (st === 'channel') srcTag = '[自建] ';
  else srcTag = '[' + (c.source_remark || '未知') + '] ';

  const mark=(lid===cur)?'selected':''
  opts.push(`<option value="${escapeHtml(lid)}" ${mark}>${escapeHtml(srcTag)}${escapeHtml(c.name)}${c.tvg_id?' · tvg-id '+escapeHtml(c.tvg_id):''}</option>`)
 }
 return opts.join('')
}

window.filterEpgTargetOptions = function(kw){
  kw = String(kw||'').toLowerCase().trim();
  const sel = $('editExtTarget');
  if(!sel) return;
  const cur = sel.value;
  const opts = ['<option value="">独立频道（不并入，单独建一条）</option>'];
  
  for (const c of (epgChList || [])) {
    const lid = c.logic_id || '';
    if (!lid) continue;

    let srcTag = '';
    const st = c.source_type || '';
    if (st === 'telecom') srcTag = '[' + (c.source_remark || '电信') + '] ';
    else if (st === 'manual') srcTag = '[手动] ';
    else if (st === 'extract') srcTag = '[' + (c.source_remark || '外部EPG') + '] ';
    else if (st === 'channel') srcTag = '[自建] ';
    else srcTag = '[' + (c.source_remark || '未知') + '] ';

    const txt = srcTag + c.name + (c.tvg_id ? ' · tvg-id ' + c.tvg_id : '');
    
    if (kw && txt.toLowerCase().indexOf(kw) < 0 && lid !== cur) continue;
    const mark = (lid === cur) ? 'selected' : '';
    opts.push(`<option value="${escapeHtml(lid)}" ${mark}>${escapeHtml(txt)}</option>`);
  }
  sel.innerHTML = opts.join('');
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

// ==================== 源频道勾选 ====================
let epgPickChannels=[]
let epgPickSel=new Map()
let epgPickPage=1
let epgPickSources=[]
const EPG_PICK_PAGE_SIZE=100

function epgPickKey(c){return (c.name||'')+'\u0001'+(c.source_url||'')}

function epgSourceLabel(u, fallback){
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
 const seen=new Map()
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
    <td style="font-size:12px;word-break:break-all;">${escapeHtml(epgSourceLabel(c.source_url,c.source_name))}</td>
    <td>${escapeHtml(c.name||c.id)}</td>
    <td style="font-size:12px;color:var(--text-secondary);word-break:break-all;">${escapeHtml(c.id||'')}</td>
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
   
   let matches = (epgChList || []).filter(x => 
       x.name === cName || 
       x.tvg_id === cTvgId || 
       x.tvg_name === cName ||
       x.tvg_name === cTvgId
   );
   
   let existing = matches.find(x => x.source_type === 'telecom');
   
   if (!existing && matches.length > 0) {
       existing = matches[0];
   }
   
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
   <td style="word-break:break-all;">${escapeHtml(e.name)}${tgt?`<div style="font-size:11px;color:var(--success)">→ ${escapeHtml(tgt)}</div>`:''}</td>
   <td style="word-break:break-all;">${escapeHtml(e.tvg_id||'')}</td>
   <td style="word-break:break-all;">${escapeHtml(getEpgSourceRemark(e.source_url)||e.source||'')}</td>
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

 // 清掉已经不存在的 logic_id（防止上次残留）
 const validLids = new Set(epgChList.map(c => String(c.logic_id||'')).filter(Boolean))
 _epgChSel.forEach(lid => { if(!validLids.has(lid)) _epgChSel.delete(lid) })

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

 // 表头全选框同步
 const allBox = $('epgChAllBox')
 if(allBox){
   const lids = filtered.map(({ch}) => String(ch.logic_id||'')).filter(Boolean)
   allBox.checked = lids.length > 0 && lids.every(lid => _epgChSel.has(lid))
 }

 if(!filtered.length){
   tbody.innerHTML='<tr><td colspan="8" class="empty">'+(epgChList.length?'没有匹配项':'暂无EPG频道')+'</td></tr>'
   return
 }

 tbody.innerHTML=filtered.map(({ch, i})=>{
   const lid = String(ch.logic_id || '')
   const checked = _epgChSel.has(lid) ? 'checked' : ''
   const mergedExtracts = (epgExtracts || []).filter(e => e.target_logic_id === ch.logic_id)

   let tvgNameHtml = `<input value="${escapeHtml(ch.tvg_name||ch.name||'')}" style="width:100%;border:none;background:transparent"
        onmousedown="this.closest('tr').setAttribute('draggable','false')"
        onmouseup="this.closest('tr').setAttribute('draggable','true')"
        onchange="updateEpgChField(${i},'tvg_name',this.value)">`
   if(mergedExtracts.length > 0) {
     const otherNames = mergedExtracts.map(e => e.name).filter(n => n && n !== (ch.tvg_name||ch.name))
     if(otherNames.length > 0) {
       tvgNameHtml += `<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;line-height:1.2;word-break:break-all">并入: ${otherNames.map(escapeHtml).join(' / ')}</div>`
     }
   }

   let mainSource = '';
   const st = ch.source_type || '';
   if (st === 'extract') {
     const remark = ch.source_remark || '';
     const en = ch.extract_name || '';
     mainSource = (en && en !== ch.name) ? (remark + ' (' + en + ')') : remark;
   } else {
     mainSource = ch.source_remark || '自建json';
   }

   const otherSources = [...new Set(mergedExtracts.map(e => e.source).filter(Boolean))];
   const extraSources = otherSources.filter(s => s !== mainSource && s !== ch.source_remark);

   let allSources = [mainSource, ...extraSources].filter(Boolean);
   allSources = [...new Set(allSources)];

   let sourceHtml = `<span style="word-break:break-all;">${allSources.map(escapeHtml).join(' / ')}</span>`;

   return `<tr draggable="true" ondragstart="epgDragStart(event,${i})" ondrop="epgDrop(event,${i})" ondragover="event.preventDefault()">
   <td style="text-align:center"><input type="checkbox" ${checked} style="width:auto"
        onchange="epgChSelectOne(${jsArg(lid)}, this.checked)"></td>
   <td style="cursor:grab;text-align:center">☰</td>

   <td><input value="${escapeHtml(ch.name)}" style="border:none;background:transparent;width:100%"
        onmousedown="this.closest('tr').setAttribute('draggable','false')"
        onmouseup="this.closest('tr').setAttribute('draggable','true')"
        onchange="updateEpgChField(${i},'name',this.value)"></td>

   <td><input value="${escapeHtml(ch.tvg_id||'')}" style="width:100%;border:none;background:transparent"
        onmousedown="this.closest('tr').setAttribute('draggable','false')"
        onmouseup="this.closest('tr').setAttribute('draggable','true')"
        onchange="updateEpgChField(${i},'tvg_id',this.value)"></td>
   <td>${tvgNameHtml}</td>
   <td style="font-size:11px">${sourceHtml}</td>
   <td style="text-align:center"><span class="badge ${ch.enabled!==false?'badge-success':'badge-danger'}">${ch.enabled!==false?'启用':'禁用'}</span></td>
   <td style="text-align:center;white-space:nowrap"><button class="btn btn-sm" onclick="toggleEpgCh(${i})">${ch.enabled!==false?'⛔':'✅'}</button><button class="btn btn-sm btn-danger" onclick="deleteEpgCh(${i})">✕</button></td>
  </tr>`
 }).join('')
}

function epgChSelectOne(lid, checked){
  lid = String(lid || '')
  if(!lid) return
  if(checked) _epgChSel.add(lid); else _epgChSel.delete(lid)
  // 更新表头全选框
  const box = $('epgChAllBox')
  if(box){
    const kw = ($('epgChSearch') || {}).value ? $('epgChSearch').value.toLowerCase().trim() : ''
    const rows = epgChList.filter(ch => {
      if(!kw) return true
      const hay = `${ch.name||''} ${ch.tvg_id||''} ${ch.tvg_name||''} ${ch.source_remark||''}`.toLowerCase()
      return hay.includes(kw)
    })
    box.checked = rows.length > 0 && rows.every(c => _epgChSel.has(String(c.logic_id||'')))
  }
}

function epgChToggleAll(checked){
  const kw = ($('epgChSearch') || {}).value ? $('epgChSearch').value.toLowerCase().trim() : ''
  epgChList.forEach(ch => {
    const lid = String(ch.logic_id || '')
    if(!lid) return
    if(kw){
      const hay = `${ch.name||''} ${ch.tvg_id||''} ${ch.tvg_name||''} ${ch.source_remark||''}`.toLowerCase()
      if(!hay.includes(kw)) return
    }
    if(checked) _epgChSel.add(lid); else _epgChSel.delete(lid)
  })
  renderEpgChannels()
}

async function batchDeleteEpgCh(){
  if(!_epgChSel.size){ showToast('请先勾选频道','error'); return }
  if(!await customConfirm(`确定删除选中的 ${_epgChSel.size} 个频道？\n\n（并入到这些频道的外部提取记录也会被同步清理）`)) return
  try{
    const r = await fetch('/api/epg-channels/batch-delete', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({logic_ids:[..._epgChSel]})
    })
    const d = await r.json().catch(()=>({}))
    if(!r.ok){ showToast('删除失败: '+(d.detail||r.status),'error'); return }
    showToast(`已删除 ${d.deleted||0} 个频道`, 'success')
    _epgChSel.clear()
    await loadEpgChannels()
    await loadEpgExtracts()
    await syncAllAfterChange()
  }catch(e){ showToast('删除失败: '+e.message,'error') }
}

async function batchToggleEpgCh(enabled){
  if(!_epgChSel.size){ showToast('请先勾选频道','error'); return }
  try{
    const r = await fetch('/api/epg-channels/batch-toggle', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({logic_ids:[..._epgChSel], enabled})
    })
    const d = await r.json().catch(()=>({}))
    if(!r.ok){ showToast('操作失败: '+(d.detail||r.status),'error'); return }
    showToast(`已${enabled?'启用':'禁用'} ${d.affected||0} 个频道`, 'success')
    _epgChSel.clear()
    await loadEpgChannels()
  }catch(e){ showToast('操作失败: '+e.message,'error') }
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
 const lid = String((epgChList[i]||{}).logic_id || '')
 await fetch('/api/epg-channels/'+i,{method:'DELETE'})
 _epgChSel.delete(lid)          // ★ 新增这一行
 await loadEpgChannels()
 await loadEpgExtracts()
 await syncAllAfterChange()
}

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

async function showEpgPivot(){
  const dateInput = prompt("查看日期(YYYYMMDD)，留空 = 今天：", "");
  const q = dateInput && /^\d{8}$/.test(dateInput) ? '?date=' + dateInput : '';
  let d;
  try{
    const r = await fetch('/api/epg/pivot' + q);
    d = await r.json();
    if(!r.ok){ showToast('获取失败: '+(d.detail||r.status),'error'); return; }
  }catch(e){ showToast('获取失败: '+e.message,'error'); return; }

  const rows = (d.rows||[]).map(r => {
    const merged = r.merged_from.length
      ? '<div style="font-size:11px;color:var(--text-secondary)">并入: '+r.merged_from.map(escapeHtml).join(' / ')+'</div>'
      : ''
    return `<tr>
      <td style="word-break:break-all">${escapeHtml(r.name)}${merged}</td>
      <td style="font-size:12px">${escapeHtml(r.tvg_id||'')}</td>
      <td><span class="badge ${r.has_data?'badge-success':'badge-danger'}">${r.has_data?'有数据':'无数据'}</span></td>
      <td style="font-size:12px">${escapeHtml(r.data_source)}</td>
      <td style="text-align:right">${r.programme_count}</td>
      <td style="font-size:11px;color:var(--text-secondary);word-break:break-all">${escapeHtml(r.file||'—')}</td>
    </tr>`
  }).join('')

  $('modalTitle').textContent = '📊 EPG 数据透视 · ' + d.date;
  $('modalBody').innerHTML = `
    <div style="margin-bottom:10px;font-size:13px">
      共 <b>${d.total}</b> 个频道 ·
      <span style="color:var(--success)">有数据 <b>${d.with_data}</b></span> ·
      <span style="color:var(--danger)">无数据 <b>${d.without_data}</b></span>
    </div>
    <div style="max-height:60vh;overflow:auto;border:1px solid var(--border);border-radius:6px">
      <table style="min-width:820px">
        <thead><tr>
          <th>频道</th><th>TVG-ID</th><th>状态</th><th>数据来源</th>
          <th style="text-align:right">节目数</th><th>文件名</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty">没有 EPG 频道</td></tr>'}</tbody>
      </table>
    </div>`
  $('modalActions').innerHTML = '<button class="btn" onclick="closeModal()">关闭</button>'
  $('modal').classList.add('active')
}

async function pollEpgFailures(){
  try{
    const r = await fetch('/api/epg/failures')
    const d = await r.json()
    _epgFailures = d.failures || []
    const dot = $('epgTabRedDot')
    if(dot) dot.style.display = _epgFailures.length ? 'inline' : 'none'
  }catch(e){}
}
setInterval(pollEpgFailures, 30000)

async function showEpgFailures(){
  if(!_epgFailures.length){ showToast('没有失败告警','success'); return }
  const rows = _epgFailures.map(f => `
    <tr>
      <td style="word-break:break-all">${escapeHtml(f.remark || '')}
        <div style="font-size:11px;color:var(--text-secondary);word-break:break-all">${escapeHtml(f.url)}</div>
      </td>
      <td style="color:var(--danger);font-size:12px;word-break:break-all">${escapeHtml(f.error)}</td>
      <td style="font-size:12px;white-space:nowrap">${new Date(f.ts*1000).toLocaleString('zh-CN',{hour12:false})}</td>
    </tr>`).join('')
  $('modalTitle').textContent = '⚠️ 最近下载失败的 EPG 源（共 ' + _epgFailures.length + ' 个）'
  $('modalBody').innerHTML = `
    <div style="max-height:60vh;overflow:auto;border:1px solid var(--border);border-radius:6px">
      <table style="min-width:700px">
        <thead><tr><th>源</th><th>错误</th><th>时间</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`
  $('modalActions').innerHTML = `
    <button class="btn btn-danger" onclick="clearAllEpgFailures()">清除所有告警</button>
    <button class="btn" onclick="closeModal()">关闭</button>`
  $('modal').classList.add('active')
}

async function clearAllEpgFailures(){
  await fetch('/api/epg/failures/clear', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
  })
  _epgFailures = []
  const dot = $('epgTabRedDot'); if(dot) dot.style.display = 'none'
  closeModal()
  showToast('已清除告警','success')
}

async function renderM3uFiles(){
 const r=await fetch('/api/m3u');const d=await r.json()
 const files=d.files||[]
 const container=$('m3uFileList')
 if(!files.length){container.innerHTML='<div class="empty">暂无M3U文件，请上传</div>';return}
 container.innerHTML=`
 <table id="m3uFileTable"><thead><tr><th>文件名</th><th>频道数</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead>
 <tbody>${files.map(f=>`
  <tr>
   <td style="white-space:nowrap">
   <button class="btn btn-sm" onclick="editM3uFile(${jsArg(f.name)})">✏️ 匹配</button>
   <button class="btn btn-sm" onclick="copyM3uLink(${jsArg(f.name)})">📋 复制链接</button>
   <button class="btn btn-sm" onclick="exportCleanM3u(${jsArg(f.name)})" title="只导出匹配到节目单的频道">📥 纯净版</button>
   <button class="btn btn-sm btn-danger" onclick="deleteM3uFile(${jsArg(f.name)})">✕</button>
   </td>
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
  // ★ 修改：改为请求我们的后端接口，让后端去下载
  const r=await fetch('/api/m3u/download-url',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url})
  })
  const d=await r.json()
  if(d.success){
      showToast('添加成功','success')
      closeModal()
      renderM3uFiles()
  }else{
      showToast(d.detail || d.error || '下载失败','error')
  }
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
 m3uFlushPendingSave()
 $('m3uEditor').style.display='none'
 currentM3uFile=null
 currentM3uEntries=[]
 localStorage.removeItem('m3u_editing')
}
async function editM3uFile(name){
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

 // ★ 修改：统计自动匹配的结果并自动保存
 let epgMatchedCount = 0;
 let iconMatchedCount = 0;
 
 currentM3uEntries.forEach(e => {
   m3uMatchEntry(e, _m3uOptions, _m3uIconFiles);
   if (e._epg_matched) epgMatchedCount++;
   if (e._icon_matched) iconMatchedCount++;
 });
 
 const total = currentM3uEntries.length;
 const unmatchedEpg = total - epgMatchedCount;
 
 if (epgMatchedCount > 0 || iconMatchedCount > 0) {
   showToast(`自动匹配完成：节目单成功 ${epgMatchedCount} 个，台标成功 ${iconMatchedCount} 个`, 'success', 5000);
   // 匹配完自动保存
   autoSaveM3u();
 } else if (total > 0) {
   showToast(`未能自动匹配到节目单或台标，请手动选择`, 'error', 5000);
 }
 
 localStorage.setItem('m3u_editing',name)
 // 在标题旁显示匹配结果
 const resultSpan = `<span style="font-size:12px;font-weight:normal;color:var(--text-secondary);margin-left:10px">
   (自动匹配: 节目单 <b style="color:var(--success)">${epgMatchedCount}</b>, 台标 <b style="color:var(--success)">${iconMatchedCount}</b>)
 </span>`;
 $('m3uEditorTitle').innerHTML = `📝 匹配编辑: ${name} ${resultSpan}`;
 $('m3uEditor').style.display='block'

 renderM3uTable(currentM3uEntries, _m3uOptions)
 
 // ★ 新增：询问是否自动匹配分组
 setTimeout(async () => {
   // 只要系统里配了规则，并且包含自动应用，就弹窗
   if (typeof _groupRules !== 'undefined' && _groupRules.auto_apply && _groupRules.rules && _groupRules.rules.length > 0) {
     const doGroup = await customConfirm('检测到您配置了「自动分组规则」，是否立即为当前列表应用分组匹配？\n\n(注：已有分组的频道不会被覆盖)', '自动匹配分组', '立即匹配', '暂不');
     if (doGroup) {
       let groupedCount = 0;
       currentM3uEntries.forEach(e => {
         // 仅对当前没有分组的频道进行匹配
         if (!e.group_title || e.group_title.trim() === '') {
           const cname = e.display_name || e.tvg_name || '';
           const g = _applyGroupRuleForFrontend(cname, _groupRules);
           if (g) {
             e.group_title = g;
             groupedCount++;
           }
         }
       });
       
       if (groupedCount > 0) {
         autoSaveM3u();
         renderM3uTable(currentM3uEntries, _m3uOptions);
         showToast(`已自动匹配 ${groupedCount} 个频道分组`, 'success');
       } else {
         showToast('未能匹配到新的分组', 'info');
       }
     }
   }
 }, 500); // 延迟一点，等界面渲染稳定
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
let m3uSaveChain=Promise.resolve()

function m3uSaveEntries(file,entries){
 if(!file)return m3uSaveChain
 const body=JSON.stringify({entries:entries||[]})
 const run=()=>fetch('/api/m3u/'+encodeURIComponent(file)+'/save',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body
 }).catch(()=>{})
 m3uSaveChain=m3uSaveChain.then(run,run)
 return m3uSaveChain
}

function m3uFlushPendingSave(){
 if(!m3uAutoSaveTimer)return
 clearTimeout(m3uAutoSaveTimer)
 m3uAutoSaveTimer=null
 const file=m3uPendingFile,entries=m3uPendingEntries
 m3uPendingFile=null
 m3uPendingEntries=null
 if(file) m3uSaveEntries(file,entries)
}

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

async function m3uAutoGroup(){
 if(!currentM3uEntries||!currentM3uEntries.length){showToast('没有可处理的条目','error');return}
 if (typeof _groupRules === 'undefined' || !_groupRules.rules || !_groupRules.rules.length) {
    showToast('请先在「合并工具配置」中配置分组规则', 'error'); return;
 }
 const doGroup = await customConfirm('是否立即为当前列表应用自动分组规则？\n(已有分组的频道不会被覆盖)', '自动匹配分组');
 if (!doGroup) return;
 
 let groupedCount = 0;
 currentM3uEntries.forEach(e => {
   if (!e.group_title || e.group_title.trim() === '') {
     const cname = e.display_name || e.tvg_name || '';
     const g = _applyGroupRuleForFrontend(cname, _groupRules);
     if (g) {
       e.group_title = g;
       groupedCount++;
     }
   }
 });
 if (groupedCount > 0) {
   autoSaveM3u();
   renderM3uTable(currentM3uEntries, _m3uOptions);
   showToast(`已自动匹配 ${groupedCount} 个频道分组`, 'success');
 } else {
   showToast('未能匹配到新的分组', 'info');
 }
}

function m3uBatchReplace(){
 if(!currentM3uFile||!currentM3uEntries.length){showToast('请先打开一个 M3U 文件','error');return}
 $('modalTitle').textContent='🔁 批量替换 URL'
 $('modalBody').innerHTML=`
  <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px">将当前列表所有频道的 URL 中匹配的字段批量替换</p>
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
 if(m3uAutoSaveTimer&&m3uPendingFile!==currentM3uFile) m3uFlushPendingSave()
 if(m3uAutoSaveTimer)clearTimeout(m3uAutoSaveTimer)
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

function m3uMatchEntry(entry, options, iconFiles) {
  const display = entry.display_name || entry.tvg_name || '';
  if (!display) return false;
  
  let matchedEpg = false;
  let matchedIcon = false;
  
  // 提取干净的频道名用于模糊匹配（去掉空格、括号，以及高清/4K等后缀）
  const cleanDisplay = display.replace(/[\s\-（）()\[\]]/g,'').replace(/(高清|超清|HD|FHD|UHD|4K|频道|综合)/gi, '').toLowerCase();

  // ================= 1. 匹配节目单 =================
  let epgMatch = null;
  // 优先按 tvg-id 精确匹配
  if (entry.tvg_id && entry.tvg_id !== '9999') {
    epgMatch = options.find(o => o.tvg_id === entry.tvg_id && o.tvg_name !== '暂无节目单');
  }
  // 其次按名称精确匹配
  if (!epgMatch) {
    epgMatch = options.find(o => o.tvg_name === display && o.tvg_name !== '暂无节目单');
  }
  // 最后使用清洗后的名字模糊匹配
  if (!epgMatch) {
    epgMatch = options.find(o => {
      const cleanOption = o.tvg_name.replace(/[\s\-（）()\[\]]/g,'').replace(/(高清|超清|HD|FHD|UHD|4K|频道|综合)/gi, '').toLowerCase();
      return cleanOption === cleanDisplay && o.tvg_name !== '暂无节目单';
    });
  }

  if (epgMatch) {
    entry.tvg_id = epgMatch.tvg_id;
    entry.tvg_name = epgMatch.tvg_name;
    entry._epg_matched = true; // 打上自动匹配标记
    matchedEpg = true;
  } else {
    entry.tvg_name = display;
    entry._epg_matched = false;
  }

  // ================= 2. 匹配台标 =================
  if (iconFiles && iconFiles.length > 0) {
    let iconMatch = null;
    // 优先用匹配到的EPG名称找台标，找不到再用原名称
    const searchName = entry.tvg_name || display;
    const cleanSearchName = searchName.replace(/[\s\-（）()\[\]]/g,'').replace(/(高清|超清|HD|FHD|UHD|4K)/gi, '').toLowerCase();

    for (const f of iconFiles) {
      // 去掉后缀 .png/.jpg 等
      const fileNameWithoutExt = f.name.substring(0, f.name.lastIndexOf('.'));
      const cleanFileName = fileNameWithoutExt.replace(/[\s\-（）()\[\]]/g,'').replace(/(高清|超清|HD|FHD|UHD|4K)/gi, '').toLowerCase();
      
      if (cleanFileName === cleanSearchName || fileNameWithoutExt.toLowerCase() === searchName.toLowerCase()) {
        iconMatch = f;
        break;
      }
    }

    if (iconMatch) {
      // 如果原本没有台标，或者原本的台标不是本站的，才进行替换
      const oldLogo = entry.tvg_logo || '';
      const isOldLocal = oldLogo.includes('/api/download/') || oldLogo.includes('/logo/');
      if (!oldLogo || !isOldLocal) {
        entry.tvg_logo = m3uIconFullUrl(iconMatch.url);
        entry._icon_matched = true;
        matchedIcon = true;
      }
    }
  }
  
  return matchedEpg || matchedIcon;
}

// 根据已配置的规则计算分组
function _applyGroupRuleForFrontend(name, groupRules) {
  if (!groupRules || !groupRules.rules || !groupRules.rules.length) return '';
  const rules = groupRules.rules.filter(r => r.enabled !== false).sort((a,b) => (a.priority||999) - (b.priority||999));
  const defaultG = groupRules.default_group || '';

  for (const rule of rules) {
    const gname = (rule.name || '').trim();
    if (!gname) continue;
    const mt = rule.match_type || 'keyword';
    if (mt === 'keyword') {
      const kws = (rule.keywords || []).map(k=>k.trim()).filter(Boolean);
      if (kws.some(kw => name.includes(kw))) return gname;
    } else if (mt === 'channels') {
      const chs = (rule.channels || []).map(c=>c.trim()).filter(Boolean);
      if (chs.includes(name)) return gname;
    }
  }
  return defaultG;
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
    const urlTd=document.getElementById('m3uUrlTd'+i)||rows[i].querySelector('td:nth-child(3)')
    const inputs=urlTd?urlTd.querySelectorAll('input'):[]
    if(inputs.length) inputs[inputs.length-1].focus()
   }
  }
 },50)
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
  const checked = _m3uSel.has(i) ? 'checked' : ''
  return `<tr style="user-select:none">
   <!-- 0. 勾选 -->
   <td style="text-align:center;vertical-align:top;padding-top:8px">
    <input type="checkbox" ${checked} style="width:auto" onchange="m3uSelectOne(${i},this.checked)">
   </td>
   <!-- 1. 拖拽 -->
   <td style="text-align:center;cursor:grab;vertical-align:top;padding-top:8px" onmousedown="m3uMouseDown(event,${i})">☰</td>

   <!-- 2. 频道名称 -->
   <td style="vertical-align:top;padding-top:8px">
    <div style="display:flex;align-items:center;gap:4px">
     <input value="${escapeHtml(e.display_name||'')}" style="border:none;background:transparent;width:100%" onblur="m3uEditField(${i},'display_name',this.value);renderM3uTable(currentM3uEntries,_m3uOptions)">
     ${(function(idx){const matched=opts.find(o=>o.tvg_id===e.tvg_id&&o.tvg_id!=='9999');if(!matched)return '';const dn=e.display_name||e.tvg_name||'';return dn===matched.tvg_name&&dn===(matched.epg_tvg_name||matched.tvg_name)?'':'<span title="显示名称与EPG频道名称不一致" style="color:#e6a23c;font-size:13px;cursor:pointer" onclick="m3uSyncChanName('+idx+')">⚠️</span>'})(i)}
    </div>
   </td>

   <!-- 3. URL -->
   <td id="m3uUrlTd${i}" ondragover="event.preventDefault()" style="padding:4px 8px;vertical-align:top;padding-top:8px">
    <div style="display:flex;flex-direction:column;gap:3px">
     ${shown.map((url,ui)=>'<input value="'+escapeHtml(url)+'" style="font-size:11px;word-break:break-all;width:100%;border:none;background:transparent;padding:2px 4px;margin:0" onblur="m3uEditUrl('+i+','+ui+',this.value)" placeholder="URL地址">').join('')}
     ${showFold?'<div style="padding:2px 0;cursor:pointer;text-align:center;font-size:12px;color:var(--primary)" onclick="m3uToggleFold('+i+')">+'+foldCount+' 条 <span style="font-size:10px">▼</span></div>':''}
     ${showUnfold?'<div style="padding:2px 0;cursor:pointer;text-align:center;font-size:12px;color:var(--text-secondary)" onclick="m3uToggleFold('+i+')"><span style="font-size:10px">▲</span> 折叠</div>':''}
    </div>
   </td>

   <!-- 4. 频道分组 -->
   <td id="m3uGrpTd${i}" style="vertical-align:top;padding-top:8px">${m3uRenderGroupCell(i,e)}</td>

   <!-- 5. tvg-id -->
   <td style="vertical-align:top;padding-top:8px"><input class="m3u-tvgid" value="${escapeHtml(e.tvg_id||'')}" style="width:100%;border:none;background:transparent" onblur="m3uEditField(${i},'tvg_id',this.value);renderM3uTable(currentM3uEntries,_m3uOptions)"></td>

   <!-- 6. tvg-name -->
   <td style="vertical-align:top;padding-top:8px;position:relative">
     <input class="m3u-tvgname" value="${escapeHtml(e.tvg_name||'')}" style="width:100%;min-width:100px;border:none;background:${e._epg_matched ? '#f6ffed' : 'transparent'};padding-right:18px" onblur="m3uEditField(${i},'tvg_name',this.value);e._epg_matched=false;renderM3uTable(currentM3uEntries,_m3uOptions)">
     ${e._epg_matched ? '<span style="position:absolute;right:4px;top:12px;font-size:10px;color:#52c41a" title="自动匹配成功">✅</span>' : ''}
   </td>

   <!-- 7. 匹配节目单 -->
   <td style="vertical-align:top;padding-top:8px"><select class="m3u-match" onchange="var sel=this.options[this.selectedIndex];var sid=sel.dataset.id,sname=sel.value;m3uEditField(${i},'tvg_id',sid);if(sid==='9999'){var disp=e.display_name||e.tvg_name||'';m3uEditField(${i},'tvg_name',disp);var tr=this.closest('tr');if(tr){var ni=tr.querySelector('td .m3u-tvgname');if(ni)ni.value=disp}}else{m3uEditField(${i},'tvg_name',sname);var tr=this.closest('tr');if(tr){var ni=tr.querySelector('td .m3u-tvgname');if(ni)ni.value=sname}};var tr=this.closest('tr');if(tr){var ti=tr.querySelector('td .m3u-tvgid');if(ti)ti.value=sid};renderM3uTable(currentM3uEntries,_m3uOptions);this.dataset.currentTvgId=sid" style="width:100%" data-current-tvg-id="${escapeHtml(e.tvg_id||'')}">
    <option value="暂无节目单" data-id="9999">🚫 暂无节目单</option>
    ${opts.filter(o=>o.tvg_name!=='暂无节目单').map(o=>'<option value="'+escapeHtml(o.tvg_name)+'" data-id="'+escapeHtml(o.tvg_id)+'" '+(o.tvg_id===e.tvg_id?'selected':'')+'>'+escapeHtml(o.tvg_name)+'</option>').join('')}
   </select></td>

   <!-- 8. 频道图标 -->
   <td id="m3uIcoTd${i}" style="vertical-align:top;padding-top:8px">${m3uRenderIconCell(i,e)}</td>

   <!-- 9. 操作 -->
   <td style="vertical-align:top;text-align:center;white-space:nowrap;padding-top:8px">
    ${document.getElementById('m3uMergeCheck')&&document.getElementById('m3uMergeCheck').checked?'<button class="btn btn-sm" onclick="m3uAddUrl('+i+')" style="padding:2px 6px;font-size:11px;margin-right:2px" title="添加URL">+</button>':''}
    <button class="btn btn-sm" onclick="m3uCopyEntry(${i})" style="padding:2px 6px;font-size:11px" title="复制该条 M3U">📋</button>
    <button class="btn btn-sm btn-danger" onclick="m3uDeleteEntry(${i})" style="padding:2px 6px;font-size:11px">✕</button>
   </td>
  </tr>`
 }).join('')

 applyTableColumnOrder($('m3uEditTable'))
 m3uSyncColumnVisibility()

 // 表头全选框同步
 const allBox = $('m3uAllBox')
 if(allBox) allBox.checked = entries.length > 0 && entries.every((_,i)=>_m3uSel.has(i))

 // 重新应用列宽
 applyTableColWidths($('m3uEditTable'))
}

function m3uSelectOne(i, checked){
  if(checked) _m3uSel.add(i); else _m3uSel.delete(i)
  const box = $('m3uAllBox')
  if(box && currentM3uEntries.length){
    box.checked = currentM3uEntries.every((_,idx)=>_m3uSel.has(idx))
  }
}

function m3uToggleAll(checked){
  _m3uSel.clear()
  if(checked){
    currentM3uEntries.forEach((_,i)=>_m3uSel.add(i))
  }
  renderM3uTable(currentM3uEntries, _m3uOptions)
}

async function m3uBatchDelete(){
  if(!_m3uSel.size){ showToast('请先勾选要删除的频道','error'); return }
  if(!await customConfirm(`确定删除选中的 ${_m3uSel.size} 条频道？`)) return
  const sel = new Set(_m3uSel)
  currentM3uEntries = currentM3uEntries.filter((_,i)=>!sel.has(i))
  _m3uSel.clear()
  renderM3uTable(currentM3uEntries, _m3uOptions)
  m3uSyncColumnVisibility()
  autoSaveM3u()
  showToast('已批量删除','success')
}

function m3uCopyEntry(i){
  const e = currentM3uEntries[i]
  if(!e) return
  const attrs = []
  if(e.tvg_id)      attrs.push(`tvg-id="${e.tvg_id}"`)
  if(e.tvg_name)    attrs.push(`tvg-name="${e.tvg_name}"`)
  if(e.tvg_logo)    attrs.push(`tvg-logo="${e.tvg_logo}"`)
  if(e.group_title) attrs.push(`group-title="${e.group_title}"`)
  const display = e.display_name || e.tvg_name || '未知'
  const line = '#EXTINF:-1' + (attrs.length ? ' ' + attrs.join(' ') : '') + ',' + display
  const urls = (e._urls && e._urls.length) ? e._urls : [e.url].filter(Boolean)
  const text = line + '\n' + urls.join('\n')

  const fallback = () => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;opacity:0'
    document.body.appendChild(ta); ta.select()
    try{ document.execCommand('copy'); showToast('已复制该条 M3U','success') }
    catch(err){ showToast('复制失败','error') }
    document.body.removeChild(ta)
  }
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(
      ()=>showToast('已复制该条 M3U','success'),
      fallback
    )
  } else {
    fallback()
  }
}

function exportCleanM3u(name){
  const url = '/api/m3u/' + encodeURIComponent(name) + '/export-clean'
  window.open(url, '_blank')
  showToast('开始下载纯净版 M3U（仅含有节目单的频道）','success')
}

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
 const urlW = "auto"
 const urlTh=document.getElementById('m3uThUrl')
 if(urlTh){ urlTh.style.width=urlW }
 const newUrlTd=document.getElementById('m3uNewUrlTd')
 if(newUrlTd){ newUrlTd.style.width=urlW }
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
 // 👇 改成这样，兼容模糊提取 👇
 let matched = ''
 if (cur.includes('/api/download/') || cur.includes('/logo/')) {
   const m = cur.match(/\/(?:api\/download|logo)\/([^/?#]+)$/)
   if (m) matched = decodeURIComponent(m[1])
 }
 const disp=matched||(cur?'（外部链接）':'')
 // 👆 替换结束 👆
 const autoTag = e._icon_matched ? '<span style="font-size:10px;color:#52c41a;margin-left:4px">✅自动</span>' : '';
 
 return '<div style="display:flex;align-items:center;gap:4px;position:relative">'+
  '<button onclick="m3uToggleDropdown(\'icon\','+i+',this,event)" title="选择台标" style="flex:1;min-width:0;font-size:11px;border:1px solid var(--border);border-radius:4px;padding:3px 6px;background:'+(e._icon_matched?'#f6ffed':'#fff')+';cursor:pointer;text-align:left;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
   (disp?escapeHtml(disp):'<span style="color:#999">无</span>')+ autoTag +'<span style="float:right;color:#999">▼</span>'+
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
async function uploadMultipleShare(files){let ok=0;for(let i=0;i<files.length;i++){const f=files[i];try{const r=await new Promise(rs=>{const rd=new FileReader();rd.onload=()=>rs(rd.result);rd.readAsDataURL(f)});const r2=await fetch('/api/share/upload',{method:'POST',body:r.split(',')[1],headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(f.name),'X-Base64':'1'}});if(!r2.ok){showToast('上传失败: HTTP '+r2.status,'error');continue};const d=await r2.json();if(d.success)ok++}catch(e){showToast('上传出错: '+e.message,'error')}}showToast('上传完成 '+ok+'/'+files.length+' 个','success');closeModal();loadShareFiles()}

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
async function uploadMultipleIcon(files){let ok=0;for(let i=0;i<files.length;i++){const f=files[i];try{const r=await new Promise(rs=>{const rd=new FileReader();rd.onload=()=>rs(rd.result);rd.readAsDataURL(f)});const r2=await fetch('/api/icons/upload',{method:'POST',body:r.split(',')[1],headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(f.name),'X-Base64':'1'}});if(!r2.ok){showToast('上传失败: HTTP '+r2.status,'error');continue};const d=await r2.json();if(d.success)ok++}catch(e){showToast('上传出错: '+e.message,'error')}}showToast('上传完成 '+ok+'/'+files.length+' 个','success');closeModal();refreshIcons()}
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
  const r = await fetch('/api/logs/global?category='+encodeURIComponent(logCurrentCategory)+'&limit=300')
  const d = await r.json()
  const entries = d.entries||[]
  const sorted = entries.slice().reverse()
  const txt = sorted.map(e=>e.line||('['+e.timestamp+'] '+e.message)).join('\n')||'= 暂无日志 ='
  const dash = $('dashLogContent'); if(dash){dash.textContent=txt;dash.scrollTop=dash.scrollHeight}
  const dashInfo = $('dashLogCountInfo'); if(dashInfo) dashInfo.textContent='显示 '+entries.length+' 条'

  try{
    const lr = await fetch('/api/logs')
    const ld = await lr.json()
    const files = ld.logs || []
    const box = $('dashLogFileList')
    if(box){
      box.innerHTML = files.length
        ? files.map(f=>`<a href="/api/logs/${encodeURIComponent(f.date)}/download" 
            style="font-size:12px;margin-right:8px;color:var(--primary)" 
            title="${(f.size/1024).toFixed(1)} KB">${escapeHtml(f.date)}</a>`).join('')
        : '<span style="font-size:12px;color:var(--text-secondary)">暂无日志文件</span>'
    }
  }catch(e){}
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
    location.reload();
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
  m.style.zIndex = '';
}

function jumpToTelecom(){
  const tab = document.querySelector('.tab[data-tab="telecom"]');
  if (tab) tab.click();
}

async function refreshTelecomStatus(){
  const el = document.getElementById('epgTelecomStatus')
  try {
    const r = await fetch('/api/telecom/status');
    const d = await r.json();
    const pLabel = d.active_label || d.platform_label || '电信平台';
    let html = '<span style="font-weight:600">[' + escapeHtml(pLabel) + ']</span> · ';
    if (!d.configured || d.config_error){
      html += '<span style="color:var(--danger)">❌ ' + escapeHtml(d.config_error || '未配置（缺少账号/设备信息）') + '</span>';
    } else {
      html += '<span style="color:var(--success)">✅ 已配置</span> · '
        + (d.enabled
            ? '<span style="color:var(--success)">定时任务已启用</span>'
            : '<span style="color:var(--text-secondary)" title="只影响定时任务，手动按钮不受影响">定时任务未启用（手动按钮照常可用）</span>')
        + ' · 已缓存 <b>' + (d.channels || 0) + '</b> 个频道'
        + (d.updated ? '（' + escapeHtml(d.updated) + '）' : '');
    }
    if (el) el.innerHTML = html;
  } catch(e) {
    if (el) el.textContent = '状态获取失败';
  }
}

async function telecomRunEpgOnly(){
  try {
    const isZte = (window.TC_ACTIVE_PLATFORM === 'zte');
    showToast('正在启动' + (isZte ? '中兴' : '华为') + 'EPG任务...', 'success');
    const r = await fetch(isZte ? '/api/zte/download-all-epg' : '/api/telecom/download-all-epg', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({config:{}, days:isZte ? 8 : 3})
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
    </div>
    <div class="form-group">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="mergeKeepOrder" checked style="width:auto">
        <span>保持第一个文件的频道顺序（其它文件的新频道追加到末尾）</span>
      </label>
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
 if(!ok) return
 const cur=sel.value
 sel.innerHTML='<option value="telecom">📡 当前电信平台频道（直播 + 回看）</option>'+
  files.map(n=>'<option value="m3u:'+escapeHtml(n)+'">📄 '+escapeHtml(n)+'</option>').join('')
 if(cur) sel.value=cur
}

async function loadOverview(opts){
 const force=!!(opts&&opts.force)
 const sel=$('ovSource'); if(!sel) return null
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

async function refreshOverview(btn){
 if(btn){ btn.disabled=true; btn.textContent='⏳ 刷新中...' }
 const n=await loadOverview({force:true})
 if(btn){ btn.disabled=false; btn.textContent='🔄 刷新' }
 if(n===null) return
 const t=new Date().toLocaleTimeString('zh-CN',{hour12:false})
 showToast('已刷新：'+n+' 个频道 · '+t,'success')
}

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

async function showEpgPivot() {
  showToast('正在统计今日节目单数据...', 'info');
  try {
    const r = await fetch('/api/epg/pivot');
    const d = await r.json();
    const rows = d.data || [];
    
    let html = `<div style="max-height:60vh;overflow:auto"><table class="table" style="width:100%">
      <thead><tr><th>频道名</th><th>TVG-ID</th><th>数据来源</th><th>节目数量</th></tr></thead>
      <tbody>`;
      
    rows.forEach(x => {
      let badge = 'badge-danger';
      if (x.source === '电信源') badge = 'badge-success';
      else if (x.source === '外部源') badge = 'badge-warning';
      else if (x.source === '空数据(占位)') badge = 'badge-secondary';
      
      html += `<tr>
        <td>${escapeHtml(x.name)}</td>
        <td>${escapeHtml(x.tvg_id)}</td>
        <td><span class="badge ${badge}">${x.source}</span></td>
        <td style="text-align:center;font-weight:bold;color:${x.count>0?'#52c41a':'#ff4d4f'}">${x.count}</td>
      </tr>`;
    });
    
    if (!rows.length) html += '<tr><td colspan="4" style="text-align:center">暂无数据</td></tr>';
    html += '</tbody></table></div>';
    
    $('modalTitle').textContent = `📊 EPG 数据透视 (${d.date})`;
    $('modalBody').innerHTML = html;
    $('modalActions').innerHTML = `<button class="btn" onclick="closeModal()">关闭</button>`;
    $('modal').classList.add('active');
  } catch(e) {
    showToast('获取透视数据失败: ' + e.message, 'error');
  }
}

// 勾选全选
function toggleAllEpgCh(checked) {
  const boxes = document.querySelectorAll('.epg-ch-cb');
  boxes.forEach(b => b.checked = checked);
}

// 收集选中的 Logic ID
function getSelectedEpgChIds() {
  const boxes = document.querySelectorAll('.epg-ch-cb:checked');
  return Array.from(boxes).map(b => b.getAttribute('data-lid'));
}

async function batchDeleteEpgCh() {
  const targets = getSelectedEpgChIds();
  if (!targets.length) return showToast('请先勾选要删除的频道', 'error');
  if (!await customConfirm(`确定要批量删除选中的 ${targets.length} 个频道吗？\n（同步删除关联的提取记录）`)) return;
  
  try {
    const r = await fetch('/api/epg-channels/batch-delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ targets })
    });
    const d = await r.json();
    if (d.success) {
      showToast(`已删除 ${d.count} 个频道`, 'success');
      await loadEpgChannels();
      if (document.getElementById('epgChSelectAll')) document.getElementById('epgChSelectAll').checked = false;
    }
  } catch(e) { showToast('删除失败: ' + e.message, 'error'); }
}

async function batchToggleEpgCh(enabled) {
  const targets = getSelectedEpgChIds();
  if (!targets.length) return showToast('请先勾选要操作的频道', 'error');
  
  try {
    const r = await fetch('/api/epg-channels/batch-toggle', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ targets, enabled })
    });
    const d = await r.json();
    if (d.success) {
      showToast(`已${enabled?'启用':'禁用'} ${d.count} 个频道`, 'success');
      await loadEpgChannels();
    }
  } catch(e) { showToast('操作失败: ' + e.message, 'error'); }
}

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
 refreshGlobalLogs().catch(()=>{})
 try{ loadColumnOrder() }catch(e){}
 try{ loadColumnWidths() }catch(e){}
 let savedTab=localStorage.getItem('iptv_active_tab')
 if(savedTab==='channels') savedTab='dashboard'
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
 
 if($('m3uConfigBody')) $('m3uConfigBody').style.display='none'
 if($('m3uConfigArrow')) $('m3uConfigArrow').textContent='▼ 展开'

 if(window.loadTelecomPlatform) loadTelecomPlatform();
 refreshTelecomStatus();
 if(window.loadTelecomSchedule) loadTelecomSchedule();
 if(window.refreshTelecomLog) refreshTelecomLog();
 
 pollEpgFailures()
}

init()