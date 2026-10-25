/**
 * telecomepg.js - 电信 EPG 前端逻辑
 *
 * 本次修复：
 * 1. 移除 tcAuthHeaders 中从 localStorage 读 token 的逻辑（服务端只认 Cookie）
 * 2. 配置弹窗中所有 ${c.xxx||''} 改用 escAttr 属性转义，防存储型 XSS
 * 3. 不再覆盖 window.closeModal（主 app.js 的 closeModal 优先）
 * 4. forceCloseModal 不再 appendChild 移动 modal 节点，只设置高 z-index
 */
(function(){
  // ★ 属性转义：值放入 value="..." / 双引号属性里
  function escAttr(v){
    return String(v==null?'':v)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
  }

  // ★ 服务端只认 Cookie，不再注入 Authorization 头
  function tcAuthHeaders(){
    return {'Content-Type':'application/json'};
  }

  window.tcLog = function(msg){
    var el = document.getElementById('tcLog');
    if (!el) return;
    if (el.textContent === '等待操作...') el.textContent = '';
    el.textContent += '[' + new Date().toLocaleTimeString() + '] ' + msg + '\n';
    el.scrollTop = el.scrollHeight;
  };

  function tcReq(method, path, body){
    var opt = {method:method, headers:tcAuthHeaders(), credentials:'include'};
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch('/api/telecom/' + path, opt).then(function(r){
      return r.text().then(function(t){
        var j = null; try { j = JSON.parse(t); } catch(e){}
        if (!r.ok) throw new Error((j && (j.detail||j.message)) || ('HTTP ' + r.status));
        return j || t;
      });
    });
  }

  // ★ 只调整 z-index，不移动节点
  function forceShowModal(){
    var modal = document.getElementById('modal');
    if (!modal) return;
    modal.classList.add('active');
    modal.style.zIndex = '2147483647';
  }

  function forceCloseModal(){
    var modal = document.getElementById('modal');
    if (modal) {
      modal.classList.remove('active');
      modal.style.zIndex = '';
    }
    // 关掉弹窗就停掉反推 key 的前端轮询（后台任务本身继续跑，进度仍在执行日志里）
    if (window.stopTelecomKeyPolling) window.stopTelecomKeyPolling();
  }

// ========== 频道列表弹窗（纯查看本地缓存，不访问电信服务器）==========
  // 弹窗内部状态
  var _tcChEdit = { editing: false, list: [], updated: '' };

  window.showTelecomChannelsModal = function() {
    _tcChEdit = { editing: false, list: [], updated: '' };
    document.getElementById('modalTitle').textContent = '📺 电信频道列表';
    document.getElementById('modalBody').innerHTML = `
      <div style="margin-bottom:8px;font-size:13px;color:var(--text-secondary);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div>
          <span>本地缓存: <b id="tcCachedInfo">读取中...</b></span>
          <label style="margin-left:15px;cursor:pointer;font-weight:normal;user-select:none" title="勾选后，生成的 m3u 和 txt 文件中会自动带上 group-title（调用『频道管理-合并工具配置』里的分组规则）">
            <input type="checkbox" id="tcEnableGroup" onchange="telecomSaveChEdit()" style="width:auto;vertical-align:middle;margin-right:4px"> 
            生成文件时自动分组 <span style="font-size:11px;color:#999">(规则见『合并工具配置』)</span>
          </label>
        </div>
        <button class="btn btn-sm" id="tcEditToggleBtn" onclick="telecomToggleChEdit()">✏️ 编辑 / 追加</button>
      </div>
      <div id="tcChHint" style="font-size:11px;color:var(--text-secondary);margin-bottom:6px"></div>
      <div style="overflow:auto;max-height:54vh;border:1px solid var(--border);border-radius:6px">
        <table class="table" id="tcChannelTable" style="width:100%;min-width:900px;margin:0;table-layout:fixed">
          <thead style="position:sticky;top:0;background:#fafafa;z-index:1">
            <tr>
              <th style="width:130px">频道ID</th>
              <th style="width:100px">频道名称</th>
              <th style="width:150px">组播地址</th>
              <th>RTSP回看地址</th>
              <th style="width:80px;text-align:center" id="tcChOpTh">操作</th>
            </tr>
          </thead>
          <tbody><tr><td colspan="5" style="text-align:center;padding:20px;color:#999">读取中...</td></tr></tbody>
        </table>
      </div>
      <div id="tcChEditBar" style="display:none;margin-top:8px;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn btn-sm" onclick="telecomAddChRow()">➕ 添加频道</button>
        <span style="font-size:11px;color:var(--text-secondary)">跨地市频道若拉不到EPG，channel_id可留空，但组播地址必填。</span>
        <span style="flex:1"></span>
        <button class="btn btn-sm btn-primary" onclick="telecomSaveChEdit(event)">💾 保存</button>
        <button class="btn btn-sm" onclick="telecomCancelChEdit()">取消</button>
      </div>
      <div id="tcChReadHint" style="margin-top:8px;font-size:11px;color:var(--text-secondary);line-height:1.6">
        只读查看本地缓存；点「✏️ 编辑 / 追加」可增删改。保存后自动重生成 sctv 和 XML。
      </div>
    `;
    document.getElementById('modalActions').innerHTML =
      `<button class="btn" onclick="forceCloseModal()">关闭</button>`;
    forceShowModal();
    telecomLoadCachedChannels();
  };

  window.telecomLoadCachedChannels = function(){
    fetch('/api/telecom/cached-channels', {credentials:'include'})
      .then(function(r){ return r.json() })
      .then(function(d){
        _tcChEdit.list = (d.channels || []).map(function(c){ return Object.assign({}, c) });
        _tcChEdit.updated = d.updated || '';
        var info = document.getElementById('tcCachedInfo');
        if (info) info.textContent = _tcChEdit.list.length + ' 个'
          + (d.updated ? '（' + d.updated + ' 更新）' : '');
        
        // ★ 新增：回显开关
        var chk = document.getElementById('tcEnableGroup');
        if (chk) chk.checked = !!d.enable_group;

        telecomRenderChTable();
      })
      .catch(function(e){
        var tb = document.querySelector('#tcChannelTable tbody');
        if (tb) tb.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:red">读取失败: ' + escAttr(e.message) + '</td></tr>';
      });
  };

  function telecomRenderChTable(){
    var tb = document.querySelector('#tcChannelTable tbody');
    if (!tb) return;
    var list = _tcChEdit.list || [];
    var editing = _tcChEdit.editing;

    var opTh = document.getElementById('tcChOpTh');
    if (opTh) opTh.style.display = '';

    if (!list.length && !editing) {
      tb.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;color:orange">'
        + '缓存为空 —— 请点「📺 更新频道列表」或直接「✏️ 编辑 / 追加」手工添加</td></tr>';
      return;
    }

    tb.innerHTML = '';
    list.forEach(function(ch, i){
      var tr = document.createElement('tr');
      
      // 1. 频道ID
      var td1 = document.createElement('td');
      if (editing) {
        var inp1 = document.createElement('input');
        inp1.value = ch.channel_id || '';
        inp1.style.cssText = 'width:100%;font-size:12px;padding:2px';
        // ★ 改动时标记为手工
        inp1.oninput = function(){ _tcChEdit.list[i].channel_id = this.value; _tcChEdit.list[i]._manual = true; };
        td1.appendChild(inp1);
      } else {
        td1.textContent = ch.channel_id || '';
      }

      // 2. 频道名称
      var td2 = document.createElement('td');
      if (editing) {
        var inp2 = document.createElement('input');
        inp2.value = ch.name || '';
        inp2.style.cssText = 'width:100%;font-size:12px;padding:2px';
        if (ch._disabled) inp2.style.textDecoration = 'line-through';
        // ★ 改动时标记为手工
        inp2.oninput = function(){ _tcChEdit.list[i].name = this.value; _tcChEdit.list[i]._manual = true; };
        td2.appendChild(inp2);
      } else {
        td2.textContent = ch.name || '';
        if (ch._disabled) {
          td2.style.textDecoration = 'line-through';
          td2.style.color = '#999';
          td2.textContent += '（已禁用）';
        }
        if (ch._manual) {
          var tag = document.createElement('span');
          tag.textContent = ' 手工';
          tag.style.cssText = 'margin-left:6px;font-size:10px;padding:1px 5px;background:#fff7e6;color:#d46b08;border-radius:3px';
          td2.appendChild(tag);
        }
      }

      // 3. 组播地址
      var td3 = document.createElement('td');
      if (editing) {
        var inp3 = document.createElement('input');
        inp3.value = ch.channel_url || '';
        inp3.placeholder = "239.94.x.x:5140";
        inp3.style.cssText = 'width:100%;font-size:12px;padding:2px';
        // ★ 改动时标记为手工
        inp3.oninput = function(){ _tcChEdit.list[i].channel_url = this.value; _tcChEdit.list[i]._manual = true; };
        td3.appendChild(inp3);
      } else {
        td3.textContent = ch.channel_url || '—';
        td3.style.cssText = 'font-size:11px;color:#666';
      }

      // 4. RTSP回看地址
      var td4 = document.createElement('td');
      if (editing) {
        var inp4 = document.createElement('input');
        inp4.value = ch.rtsp_url || '';
        inp4.placeholder = "rtsp://...（可空）";
        inp4.style.cssText = 'width:100%;font-size:12px;padding:2px';
        // ★ 改动时标记为手工
        inp4.oninput = function(){ _tcChEdit.list[i].rtsp_url = this.value; _tcChEdit.list[i]._manual = true; };
        td4.appendChild(inp4);
      } else {
        td4.textContent = ch.rtsp_url || '—';
        td4.style.cssText = 'font-size:11px;color:#999;word-break:break-all;';
      }

      tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4);

      // 5. 操作列
      var td5 = document.createElement('td');
      td5.style.cssText = 'text-align:center;white-space:nowrap';
      if (editing) {
        var b1 = document.createElement('button');
        b1.className = 'btn btn-sm';
        b1.textContent = ch._disabled ? '✅' : '⛔';
        b1.title = ch._disabled ? '恢复启用' : '禁用（不输出到 sctv）';
        // ★ 点击禁用/启用也标记为手工
        b1.onclick = function(){ 
          _tcChEdit.list[i]._disabled = !_tcChEdit.list[i]._disabled; 
          _tcChEdit.list[i]._manual = true; 
          telecomRenderChTable(); 
        };
        
        var b2 = document.createElement('button');
        b2.className = 'btn btn-sm btn-danger';
        b2.textContent = '✕';
        b2.style.marginLeft = '4px';
        b2.title = '删除';
        b2.onclick = function(){ _tcChEdit.list.splice(i, 1); telecomRenderChTable() };
        
        td5.appendChild(b1); td5.appendChild(b2);
      }
      tr.appendChild(td5);

      tb.appendChild(tr);
    });

    // 底部追加空行
    if (editing) {
      var addRow = document.createElement('tr');
      addRow.style.background = '#fafafa';
      addRow.innerHTML =
        '<td><input id="tcNewCid" placeholder="可空" style="width:100%;font-size:12px;padding:2px"></td>'
        + '<td><input id="tcNewName" placeholder="名称*" style="width:100%;font-size:12px;padding:2px"></td>'
        + '<td><input id="tcNewUrl" placeholder="组播地址*" style="width:100%;font-size:12px;padding:2px"></td>'
        + '<td><input id="tcNewRtsp" placeholder="RTSP地址(可空)" style="width:100%;font-size:12px;padding:2px"></td>'
        + '<td style="text-align:center"><button class="btn btn-sm btn-primary" onclick="telecomQuickAddRow()">➕</button></td>';
      tb.appendChild(addRow);
    }

    // 恢复拖拽的列宽和顺序
    var tbl = document.getElementById('tcChannelTable');
    if (tbl) {
      if (typeof applyTableColumnOrder === 'function') applyTableColumnOrder(tbl);
      if (typeof applyTableColWidths === 'function') applyTableColWidths(tbl);
      if (typeof ensureResizeHandles === 'function') ensureResizeHandles(tbl);
    }
  }

  window.telecomToggleChEdit = function(){
    _tcChEdit.editing = true;
    document.getElementById('tcEditToggleBtn').style.display = 'none';
    document.getElementById('tcChEditBar').style.display = 'flex';
    document.getElementById('tcChReadHint').style.display = 'none';
    telecomRenderChTable();
  };

  window.telecomCancelChEdit = function(){
    _tcChEdit.editing = false;
    document.getElementById('tcEditToggleBtn').style.display = '';
    document.getElementById('tcChEditBar').style.display = 'none';
    document.getElementById('tcChReadHint').style.display = '';
    telecomLoadCachedChannels(); // 丢弃未保存改动
  };

  window.telecomQuickAddRow = function(){
    var cid = ((document.getElementById('tcNewCid')||{}).value || '').trim();
    var name = ((document.getElementById('tcNewName')||{}).value || '').trim();
    var url  = ((document.getElementById('tcNewUrl')||{}).value || '').trim();
    var rtsp = ((document.getElementById('tcNewRtsp')||{}).value || '').trim(); // ★ 新增

    if (!name) { alert('请填写频道名称'); return }
    if (!cid && !url) { alert('channel_id 和组播地址至少填一个'); return }
    if (cid && _tcChEdit.list.some(function(c){ return String(c.channel_id) === cid })) {
      alert('channel_id 已存在: ' + cid); return;
    }
    
    _tcChEdit.list.push({
      channel_id: cid, name: name, channel_url: url, rtsp_url: rtsp, _manual: true // ★ 保存 rtsp
    });
    telecomRenderChTable();
  };

  window.telecomAddChRow = function(){ /* 走表格底部的空行 */ };

  window.telecomSaveChEdit = function(ev){
    var list = _tcChEdit.list || [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      var cid = String(c.channel_id || '').trim();
      var url = String(c.channel_url || '').trim();
      var nm = String(c.name || '').trim();
      if (!nm) { alert('第 ' + (i+1) + ' 行缺少频道名称'); return; }
      if (!cid && !url) {
        alert('第 ' + (i+1) + ' 行：channel_id 和组播地址至少填一个');
        return;
      }
    }
    var btn = (ev && ev.target) || null;
    if (btn) { btn.disabled = true; btn.textContent = '保存中...' }
    tcReq('PUT', 'cached-channels', { 
      channels: list,
      enable_group: document.getElementById('tcEnableGroup') ? document.getElementById('tcEnableGroup').checked : false
    })
      .then(function(j){
        if (btn) { btn.disabled = false; btn.textContent = '💾 保存' }
        if (!j.success) { alert(j.message || '保存失败'); return }
        if (typeof showToast === 'function') showToast(j.message, 'success');
        _tcChEdit.editing = false;
        document.getElementById('tcEditToggleBtn').style.display = '';
        document.getElementById('tcChEditBar').style.display = 'none';
        document.getElementById('tcChReadHint').style.display = '';
        telecomLoadCachedChannels();
        if (typeof tcFetchLogs === 'function') tcFetchLogs().catch(function(){});
      }).catch(function(e){
        if (btn) { btn.disabled = false; btn.textContent = '💾 保存' }
        alert('保存失败: ' + e.message);
      });
  };

// 「📺 获取频道列表 / 📺 更新频道列表」按钮仍然走这里（原逻辑不动）
window.telecomGetChannels = function(){
  tcLog('从电信服务器获取频道列表中...');
  var tb = document.querySelector('#tcChannelTable tbody');
  if (tb) tb.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:#999">正在请求后端，请稍候...</td></tr>';

  tcReq('POST','get-channels',{}).then(function(j){
    var list = j.channels || [];
    var info = document.getElementById('tcCachedInfo');
    if (info) info.textContent = list.length + ' 个';
    tcLog('已刷新，共 ' + list.length + ' 个');
    
    // ★ 直接调用新渲染函数，保持表头 4 列一致
    telecomLoadCachedChannels(); 
    
    tcFetchLogs().catch(function(){});
  }).catch(function(e){
    tcLog('获取失败: ' + tcErrMsg(e));
    var tb = document.querySelector('#tcChannelTable tbody');
    if (tb) tb.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:20px;color:red">请求失败: ' + escAttr(e.message) + '</td></tr>';
  });
};

  // ========== 弹窗配置模块 ==========
  // 当前访问地址（如 http://iptv.example.com）：epg_server_url / logo_url 的默认值。
  // 后端也做同样的兜底：留空时按请求头自动补，生成 m3u/txt 时立即生效。
  function tcBaseUrl(){
    try{ return String(location.origin||'').replace(/\/+$/,'') }catch(e){ return '' }
  }

  function tcModalVal(id){
    var el = document.getElementById(id);
    return el ? String(el.value||'').trim() : '';
  }

  // 实时预览：最终写进 m3u/txt 的 EPG 直链和台标地址
  window.updateTelecomUrlPreview = function(){
    var box = document.getElementById('mcUrlPreview');
    if (!box) return;
    var base = tcModalVal('mcEpgUrl'), name = tcModalVal('mcEpgName') || 'epg.xml', logo = tcModalVal('mcLogo');
    var epg = base ? (base.replace(/\/+$/,'') + '/' + name.replace(/^\/+/,'')) : '（留空：生成时自动用当前访问地址）';
    // 台标：和本站 EPG 同源（或留空）走 /api/download/，指向外部台标服务器走 /logo/
    var sameSite = !logo || !base || logo.replace(/\/+$/,'') === base.replace(/\/+$/,'');
    var logoUrl = logo
      ? (logo.replace(/\/+$/,'') + (sameSite ? '/api/download/频道名.png' : '/logo/频道名.png'))
      : '（留空：生成时自动用当前访问地址）';
    box.innerHTML = 'EPG 直链：<b>' + escAttr(epg) + '</b><br>台标地址：<b>' + escAttr(logoUrl) + '</b>';
  };

  // 「↺ 用当前访问地址」：把两个地址都重置为浏览器当前地址
  window.fillTelecomBaseUrls = function(){
    var b = tcBaseUrl();
    if (!b) return;
    var e = document.getElementById('mcEpgUrl'); if (e) e.value = b;
    var l = document.getElementById('mcLogo'); if (l) l.value = b;
    window.updateTelecomUrlPreview();
  };

  // ========== 反推 key（用现有 Authenticator 穷举 8 位数字） ==========
  var _keyPollTimer = null;
  var _keySearching = false;

  window.telecomKeyStatus = function(text, color){
    var el = document.getElementById('mcFindKeyStatus');
    if (!el) return;
    el.style.display = text ? 'block' : 'none';
    el.style.color = color || 'var(--text-secondary)';
    el.textContent = text || '';
  };

  window.stopTelecomKeyPolling = function(){
    if (_keyPollTimer) { clearInterval(_keyPollTimer); _keyPollTimer = null; }
  };

  function setKeyBtn(text, disabled){
    var b = document.getElementById('mcFindKeyBtn');
    if (!b) return;
    b.textContent = text;
    b.disabled = !!disabled;
  }

  window.stopTelecomKeySearch = function(){
    window.telecomKeyStatus('正在停止…');
    tcReq('POST','find-key/stop',{}).catch(function(){});
  };

  window.startTelecomKeyPolling = function(){
    window.stopTelecomKeyPolling();
    _keyPollTimer = setInterval(function(){
      if (!document.getElementById('mcFindKeyBtn')) {   // 弹窗已关闭
        window.stopTelecomKeyPolling();
        _keySearching = false;
        return;
      }
      fetch('/api/telecom/find-key/status', {credentials:'include'})
        .then(function(r){ return r.json() })
        .then(function(s){
          if (s.running){
            var pct = s.total ? Math.floor(s.tested * 100 / s.total) : 0;
            window.telecomKeyStatus('反推中：' + Number(s.tested||0).toLocaleString() + ' / ' +
              Number(s.total||0).toLocaleString() + '（' + pct + '%）');
            return;
          }
          window.stopTelecomKeyPolling();
          _keySearching = false;
          if (s.found){
            var k = document.getElementById('mcKey'); if (k) k.value = s.found;
            setKeyBtn('✅ 已找到', false);
            window.telecomKeyStatus('已找到 key：' + s.found + '（已写入配置，Authenticator 也已更新）', '#52c41a');
          } else {
            setKeyBtn('🔍 反推', false);
            window.telecomKeyStatus(s.message || '反推结束，没有找到 key', '#ff4d4f');
          }
        })
        .catch(function(){});
    }, 2000);
  };

  window.findTelecomKey = function(){
    if (_keySearching){ window.stopTelecomKeySearch(); return; }   // 再点一次＝停止
    if (!tcModalVal('mcAuthn')){
      alert('需要先有 Authenticator（抓包值）才能反推 key：反推就是拿它去试解密');
      return;
    }
    tcReq('POST','find-key',{}).then(function(j){
      if (!j.success){ alert(j.message || '启动失败'); return; }
      _keySearching = true;
      setKeyBtn('⏹ 停止', false);
      window.telecomKeyStatus(j.message || '已开始反推…');
      window.startTelecomKeyPolling();
      if (window.startTelecomLogPolling) window.startTelecomLogPolling();
    }).catch(function(e){ alert('启动失败: ' + e.message); });
  };

  window.showTelecomConfigModal = function() {
    tcReq('GET','config').then(function(j){
      var c = j.config || {};
      var base = tcBaseUrl();
      var epgServer = c.epg_server_url || base;
      var logoBase = c.logo_url || base;
      // ★ 所有属性值统一用 escAttr
      document.getElementById('modalTitle').textContent = '⚙️ 电信认证与EPG配置';
      document.getElementById('modalBody').innerHTML = `
        <div style="font-weight:600;margin-bottom:8px;color:var(--primary);border-bottom:1px solid #eee;padding-bottom:4px">认证配置</div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px">
          <div class="form-group" style="flex:1;min-width:220px"><label>启用定时任务</label><select id="mcEnabled"><option value="false">关闭</option><option value="true">开启</option></select>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">只控制电信的定时任务（频道刷新 / EPG 下载）；「执行认证」「获取频道」「下载EPG」这些手动按钮不受影响。</div>
          </div>
          <div class="form-group" style="flex:2;min-width:200px"><label>认证服务器</label><input id="mcAuthIP" value="${escAttr(c.AuthenticationIP)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>账号 (UserID)</label><input id="mcUserID" value="${escAttr(c.UserID)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>机顶盒 (mac)</label><input id="mcMac" value="${escAttr(c.mac)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>STBID</label><input id="mcStbid" value="${escAttr(c.STBID)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>终端IP (ip)</label><input id="mcIp" value="${escAttr(c.ip)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>STBType</label><input id="mcStbType" value="${escAttr(c.STBType)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>STBVersion</label><input id="mcStbVer" value="${escAttr(c.STBVersion)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>templateName</label><input id="mcTemplate" value="${escAttr(c.templateName)}"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>areaId</label><input id="mcArea" value="${escAttr(c.areaId)}"></div>
          <div class="form-group" style="flex:2;min-width:200px"><label>Authenticator</label><input id="mcAuthn" value="${escAttr(c.Authenticator)}"></div>
          <div class="form-group" style="flex:1;min-width:190px">
            <label style="display:flex;align-items:center;gap:6px">加密 key
              <button type="button" class="btn btn-sm" id="mcFindKeyBtn" onclick="findTelecomKey()" title="用下面的 Authenticator 穷举 8 位数字 key（后台跑，进度看执行日志）">🔍 反推</button>
            </label>
            <input id="mcKey" value="${escAttr(c.key)}">
            <div id="mcFindKeyStatus" style="display:none;font-size:11px;color:var(--text-secondary);margin-top:4px;line-height:1.6"></div>
          </div>
          <div class="form-group" style="flex:1;min-width:190px;padding-top:22px">
            <label style="display:inline-flex;align-items:center;gap:6px;font-weight:normal;font-size:12px;cursor:pointer" title="勾选后：key 为空直接拒绝认证，不再回退到静态 Authenticator">
              <input type="checkbox" id="mcRequireKey" style="width:auto" ${c.auth_require_key ? 'checked' : ''}> 必须用 key 生成 Authenticator
            </label>
          </div>
          <div class="form-group" style="flex:1 1 100%"><label>UserAgent</label><input id="mcUA" value="${escAttr(c.UserAgent)}"></div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px;flex-wrap:wrap">
          <span style="font-weight:600;color:var(--primary)">EPG输出配置</span>
          <button type="button" class="btn btn-sm" onclick="fillTelecomBaseUrls()" title="把 epg_server_url 和 logo_url 都填成当前访问地址">↺ 用当前访问地址</button>
          <span style="font-size:11px;color:var(--text-secondary)">留空也行：生成 m3u/txt 时会自动补</span>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px">
          <div class="form-group" style="flex:2;min-width:200px"><label>epg_server_url</label><input id="mcEpgUrl" value="${escAttr(epgServer)}" placeholder="http://你的域名或IP" oninput="updateTelecomUrlPreview()"></div>
          <div class="form-group" style="flex:1;min-width:120px"><label>epgname</label><input id="mcEpgName" value="${escAttr(c.epgname || 'epg.xml')}" oninput="updateTelecomUrlPreview()"></div>
          <div class="form-group" style="flex:2;min-width:200px"><label>RTP2HTTPD（手工填）</label><input id="mcRtp" value="${escAttr(c.RTP2HTTPD)}" placeholder="http://192.168.1.1:5140"></div>
          <div class="form-group" style="flex:2;min-width:200px"><label>logo_url</label><input id="mcLogo" value="${escAttr(logoBase)}" placeholder="留空＝当前访问地址" oninput="updateTelecomUrlPreview()"></div>
        </div>
        <div id="mcUrlPreview" style="font-size:12px;color:var(--text-secondary);line-height:1.9;background:#fafafa;border:1px dashed #ddd;border-radius:6px;padding:8px 10px;margin-bottom:12px"></div>
        <div style="font-size:12px;color:var(--text-secondary);line-height:1.7;border-top:1px dashed #ddd;padding-top:8px">
          💾 保存后写入 <code>data/config/app_config.json</code>：电信配置 + 定时任务 + 全局设置 + 外部 EPG 源都在<b>这一个文件</b>里。<br>
          💡 epg_server_url / logo_url 留空时自动用<b>当前访问地址</b>（即 <code>${escAttr(base || 'http://…')}</code>），
          EPG 直链 = <code>地址/epgname</code>；台标 = <code>地址/api/download/频道名.png</code>（logo_url 与本站同源时，与「频道管理」一致），
          若 logo_url 填的是外部台标服务器则沿用 <code>地址/logo/频道名.png</code>。用公网域名打开本页面保存，生成的就是公网链接。<br>
          测试连接 / 执行认证 / 获取频道 / 下载 EPG 都只读这个文件，不再使用页面上的临时参数。
        </div>
      `;
      document.getElementById('modalActions').innerHTML = `
        <button class="btn btn-primary" onclick="saveTelecomConfigModal()">💾 保存配置</button>
        <button class="btn" onclick="forceCloseModal()">取消</button>
      `;
      forceShowModal();
      document.getElementById('mcEnabled').value = c.enabled ? 'true' : 'false';
      window.updateTelecomUrlPreview();
    }).catch(function(e){ alert('获取配置失败: ' + e.message); });
  };

  window.saveTelecomConfigModal = function() {
    function getV(id){ var el = document.getElementById(id); return el ? el.value.trim() : ''; }
    // ★ 留空就自动用当前访问地址：保存后定时任务也能用同一套链接
    var base = tcBaseUrl();
    var body = { config: {
        enabled: getV('mcEnabled') === 'true', AuthenticationIP: getV('mcAuthIP'), UserID: getV('mcUserID'), mac: getV('mcMac'),
        STBID: getV('mcStbid'), ip: getV('mcIp'), STBType: getV('mcStbType'), STBVersion: getV('mcStbVer'), templateName: getV('mcTemplate'),
        areaId: getV('mcArea'), Authenticator: getV('mcAuthn'), key: getV('mcKey'), UserAgent: getV('mcUA'),
        auth_require_key: !!(document.getElementById('mcRequireKey') && document.getElementById('mcRequireKey').checked),
        epg_server_url: getV('mcEpgUrl') || base,
        epgname: getV('mcEpgName') || 'epg.xml',
        RTP2HTTPD: getV('mcRtp'),
        logo_url: getV('mcLogo') || base
    }};
    tcReq('POST', 'config', body).then(function(j){
        if (j.success) { alert('保存成功！'); forceCloseModal(); }
        else { alert('保存失败: ' + j.message); }
    }).catch(function(e){ alert('保存失败: ' + e.message); });
  };

  // ★ 不再覆盖 window.closeModal，避免影响主 app.js

  // ========== 实时执行日志（长任务进度在这里滚，不走顶部进度条） ==========
  var _tcLogCursor = 0, _tcLogTimer = null;

  function tcLogPanel(){ return document.getElementById('tcLog'); }

  function tcAppendLogs(list, replace){
    var el = tcLogPanel();
    if (!el || !list || !list.length) return;
    if (replace || el.textContent === '等待操作...') el.textContent = '';
    list.forEach(function(x){ el.textContent += '[' + x.t + '] ' + x.msg + '\n'; });
    el.scrollTop = el.scrollHeight;
  }

  function tcFetchLogs(){
    return fetch('/api/telecom/logs?since=' + _tcLogCursor, {credentials:'include'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d && d.logs && d.logs.length){
          tcAppendLogs(d.logs);
          _tcLogCursor = (typeof d.total === 'number') ? d.total : (_tcLogCursor + d.logs.length);
        }
        return d || {};
      });
  }

  // 一次性拉全量（打开页面/切到电信页时用）
  window.refreshTelecomLog = function(){
    return fetch('/api/telecom/logs?since=0', {credentials:'include'})
      .then(function(r){ return r.json(); })
      .then(function(d){
        _tcLogCursor = (d && d.total) || 0;
        tcAppendLogs((d && d.logs) || [], true);
        if (d && d.running) window.startTelecomLogPolling();
      })
      .catch(function(){});
  };

  // 增量轮询（启动长任务后调用）
  window.startTelecomLogPolling = function(){
    window.stopTelecomLogPolling();
    var tick = function(){
      tcFetchLogs().then(function(d){
        if (d && d.running === false) window.stopTelecomLogPolling();
      }).catch(function(){});
    };
    tick();
    _tcLogTimer = setInterval(tick, 100);
  };

  window.stopTelecomLogPolling = function(){
    if (_tcLogTimer){ clearInterval(_tcLogTimer); _tcLogTimer = null; }
  };

  // 网络错误文案：别只甩一句 NetworkError
  function tcErrMsg(e){
    var m = (e && e.message) ? e.message : String(e || '');
    if (/NetworkError|Failed to fetch|Load failed|network error/i.test(m)) {
      return '服务器无响应（可能正在执行长任务或已重启），请稍等再刷新页面';
    }
    return m;
  }

  // ========== 快捷操作 ==========
  window.telecomTestConn = function(){ tcLog('测试连接中...'); tcReq('POST','test-connection',{}).then(j=>tcLog('结果: ' + JSON.stringify(j))).catch(e=>tcLog('失败: ' + e.message)); };
  window.telecomAuth = function(){ tcLog('认证中...'); tcReq('POST','auth',{}).then(j=>tcLog('结果: ' + JSON.stringify(j))).catch(e=>tcLog('失败: ' + e.message)); };
  window.telecomAllEpg = function(){
    tcLog('启动 EPG 下载...');
    tcReq('POST','download-all-epg',{ force_refresh_channels: false, days: 7 })
      .then(function(j){
        if (j && j.success) {
          tcLog('✅ 已启动，实时日志如下');
          if (typeof showToast === 'function') showToast('任务已启动，进度见「电信EPG」页执行日志','success');
          if (window.clearGlobalProgress) clearGlobalProgress();
          window.startTelecomLogPolling();
        } else {
          tcLog('❌ 未能启动: ' + ((j && j.message) || ''));
          if (typeof showToast === 'function') showToast('未能启动: ' + ((j && j.message) || ''),'error');
        }
      })
      .catch(function(e){ tcLog('❌ 启动失败: ' + tcErrMsg(e)); });
  };
  window.telecomRunAll = function(){
    tcLog('启动 一键获取频道+EPG...');
    tcReq('POST','download-all-epg',{ force_refresh_channels: true, days: 7 })
      .then(function(j){
        if (j && j.success) {
          tcLog('✅ 已启动，实时日志如下');
          if (typeof showToast === 'function') showToast('任务已启动，进度见「电信EPG」页执行日志','success');
          if (window.clearGlobalProgress) clearGlobalProgress();
          window.startTelecomLogPolling();
        } else {
          tcLog('❌ 未能启动: ' + ((j && j.message) || ''));
          if (typeof showToast === 'function') showToast('未能启动: ' + ((j && j.message) || ''),'error');
        }
      })
      .catch(function(e){ tcLog('❌ 启动失败: ' + tcErrMsg(e)); });
  };
  window.telecomGenM3U = function(){ tcReq('POST','generate-m3u',{}).then(j=>tcLog('M3U已生成')).catch(e=>tcLog('失败: ' + e.message)); };
  window.telecomStatus = function(){ tcReq('GET','status').then(j=>tcLog('状态: ' + JSON.stringify(j))).catch(e=>tcLog('失败: ' + e.message)); };
  // ========== 定时任务（频道 / EPG 各自独立） ==========
  function setVal(id, v){ var el = document.getElementById(id); if (el) el.value = v; }
  function getVal(id){ var el = document.getElementById(id); return el ? el.value : ''; }

  function fmtSched(label, it){
    if (!it || !it.enabled) return label + ' 关闭';
    var when = (it.mode === 'interval')
      ? ('每隔 ' + (it.interval_days || 3) + ' 天 ' + (it.time || ''))
      : ('每日 ' + (it.time || ''));
    return label + ' ' + when;
  }

  window.tcToggleIntervalMode = function(prefix){
    var mode = document.getElementById(prefix + 'CronMode');
    var grp = document.getElementById(prefix + 'IntervalGroup');
    if (!grp) return;
    grp.style.display = (mode && mode.value === 'interval') ? '' : 'none';
  };

  window.toggleTelecomSchedule = function(){
    var body = document.getElementById('tcScheduleBody');
    var arrow = document.getElementById('tcScheduleArrow');
    if (!body) return;
    var willHide = body.style.display !== 'none';
    body.style.display = willHide ? 'none' : '';
    if (arrow) arrow.textContent = willHide ? '▼ 展开' : '▲ 收起';
  };

  window.loadTelecomSchedule = function(){
    return tcReq('GET', 'config').then(function(j){
      var sc = (j && j.schedule) || {};
      var ch = sc.channels || {}, ep = sc.epg || {};
      setVal('tcChanCronEnabled', ch.enabled ? 'true' : 'false');
      setVal('tcChanCronMode', ch.mode || 'daily');
      setVal('tcChanCronTime', ch.time || '02:00');
      setVal('tcChanCronInterval', ch.interval_days || 3);
      setVal('tcEpgCronEnabled', ep.enabled ? 'true' : 'false');
      setVal('tcEpgCronMode', ep.mode || 'daily');
      setVal('tcEpgCronTime', ep.time || '03:00');
      setVal('tcEpgCronInterval', ep.interval_days || 3);
      var af = document.getElementById('tcAutoFetchChannels');
      if (af) af.checked = (j.auto_fetch_channels !== false);
      var l1 = document.getElementById('tcChanLastRun');
      if (l1) l1.textContent = ch.last_run || '无';
      var l2 = document.getElementById('tcEpgLastRun');
      if (l2) l2.textContent = ep.last_run || '无';
      window.tcToggleIntervalMode('tcChan');
      window.tcToggleIntervalMode('tcEpg');
      var sum = document.getElementById('tcScheduleSummary');
      if (sum) sum.textContent = fmtSched('频道', ch) + ' · ' + fmtSched('EPG', ep);
    }).catch(function(){ /* 未登录或网络异常时静默 */ });
  };

  window.telecomSaveConfig = function(){
    var body = {
      schedule: {
        channels: {
          enabled: getVal('tcChanCronEnabled') === 'true',
          mode: getVal('tcChanCronMode') || 'daily',
          time: getVal('tcChanCronTime') || '02:00',
          interval_days: parseInt(getVal('tcChanCronInterval'), 10) || 3
        },
        epg: {
          enabled: getVal('tcEpgCronEnabled') === 'true',
          mode: getVal('tcEpgCronMode') || 'daily',
          time: getVal('tcEpgCronTime') || '03:00',
          interval_days: parseInt(getVal('tcEpgCronInterval'), 10) || 3
        }
      },
      auto_fetch_channels: !!(document.getElementById('tcAutoFetchChannels') || {}).checked
    };
    tcReq('POST', 'config', body).then(function(j){
      if (j && j.success) { tcLog('✅ 定时设置已保存'); window.loadTelecomSchedule(); }
      else { tcLog('❌ 保存失败: ' + ((j && j.message) || '')); }
    }).catch(function(e){ tcLog('❌ 保存失败: ' + e.message); });
  };

  // ★ 新增这一行：暴露 forceCloseModal 到全局，供 onclick 调用
  window.forceCloseModal = forceCloseModal;
})();