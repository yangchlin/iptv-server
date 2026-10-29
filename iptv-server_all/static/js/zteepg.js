/**
 * zteepg.js - 中兴平台 EPG 前端逻辑
 *
 * 与 telecomepg.js（华为）各管各的：
 * - 所有请求走 /api/zte/*
 * - 平台切换与状态显示在 telecomplatform.js
 * - 执行日志复用「电信EPG」页的日志框（按平台切换日志接口）
 */
(function(){
  function escAttr(v){
    return String(v==null?'':v)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
  }

  function ztReq(method, path, body){
    var opt = {method:method, headers:{'Content-Type':'application/json'}, credentials:'include'};
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch('/api/zte/' + path, opt).then(function(r){
      return r.text().then(function(t){
        var j = null; try { j = JSON.parse(t); } catch(e){}
        if (!r.ok) throw new Error((j && (j.detail||j.message)) || ('HTTP ' + r.status));
        return j || t;
      });
    });
  }

  function ztLog(msg){
    if (typeof window.tcLog === 'function') window.tcLog(msg);
  }

  function ztErr(e){
    var m = (e && e.message) ? e.message : String(e || '');
    if (/NetworkError|Failed to fetch|Load failed/i.test(m)) {
      return '服务器无响应（可能正在执行长任务或已重启），请稍等再刷新页面';
    }
    return m;
  }

  function ztToast(msg, type){
    if (typeof showToast === 'function') showToast(msg, type || 'success');
  }

  function baseUrl(){
    try{ return String(location.origin||'').replace(/\/+$/,'') }catch(e){ return '' }
  }

  // ════════════════════════════════════════════════════════════
  // 操作按钮
  // ════════════════════════════════════════════════════════════

  window.zteTestConn = function(){
    ztLog('测试中兴服务器连接...');
    ztReq('POST','test-connection',{}).then(function(j){
      ztLog((j.success ? '✅ ' : '❌ ') + (j.message || JSON.stringify(j)));
      ztToast(j.message || '测试完成', j.success ? 'success' : 'error');
    }).catch(function(e){ ztLog('❌ 失败: ' + ztErr(e)); });
  };

  window.zteAuth = function(){
    ztLog('执行中兴认证...');
    ztReq('POST','auth',{}).then(function(j){
      ztLog((j.success ? '✅ ' : '❌ ') + (j.message || JSON.stringify(j)));
      ztToast(j.success ? '认证成功' : ('认证失败: ' + (j.message||'')), j.success ? 'success' : 'error');
    }).catch(function(e){ ztLog('❌ 失败: ' + ztErr(e)); });
  };

  window.zteGetChannels = function(){
    ztLog('从电信服务器获取中兴频道列表中...');
    var tb = document.querySelector('#tcChannelTable tbody');
    if (tb) tb.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#999">正在请求后端，请稍候...</td></tr>';

    ztReq('POST','get-channels',{}).then(function(j){
      var list = j.channels || [];
      ztLog((j.success ? '✅ ' : '❌ ') + (j.message || '') + '，共 ' + list.length + ' 个');
      if (typeof window.telecomLoadCachedChannels === 'function') window.telecomLoadCachedChannels();
      if (j.success && typeof window.refreshTelecomStatus === 'function') window.refreshTelecomStatus();
    }).catch(function(e){
      ztLog('❌ 获取失败: ' + ztErr(e));
      var tb2 = document.querySelector('#tcChannelTable tbody');
      if (tb2) tb2.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;color:red">请求失败: ' + escAttr(e.message) + '</td></tr>';
    });
  };

  function ztRunEpg(force){
    var label = force ? '一键获取频道+EPG' : '更新EPG信息';
    ztLog('启动中兴 ' + label + '...');
    ztReq('POST','download-all-epg',{ force_refresh_channels: !!force })
      .then(function(j){
        if (j && j.success) {
          ztLog('✅ 已启动，实时日志如下');
          ztToast('任务已启动，进度见「电信EPG」页执行日志');
          if (window.clearGlobalProgress) clearGlobalProgress();
          if (window.startTelecomLogPolling) window.startTelecomLogPolling();
        } else {
          ztLog('❌ 未能启动: ' + ((j && j.message) || ''));
          ztToast('未能启动: ' + ((j && j.message) || ''), 'error');
        }
      })
      .catch(function(e){ ztLog('❌ 启动失败: ' + ztErr(e)); });
  }

  window.zteAllEpg = function(){ ztRunEpg(false); };
  window.zteRunAll = function(){ ztRunEpg(true); };

  window.zteGenM3U = function(){
    ztReq('POST','generate-m3u',{}).then(function(j){
      ztLog(j && j.success ? '✅ zte.m3u / sctv 已生成' : ('❌ ' + ((j && j.message)||'')));
    }).catch(function(e){ ztLog('❌ 生成失败: ' + ztErr(e)); });
  };

  window.zteStatus = function(){
    ztReq('GET','status').then(function(j){
      ztLog('状态: ' + JSON.stringify(j));
    }).catch(function(e){ ztLog('❌ 失败: ' + ztErr(e)); });
  };

  // ════════════════════════════════════════════════════════════
  // 配置弹窗
  // ════════════════════════════════════════════════════════════

  function val(id){
    var el = document.getElementById(id);
    return el ? String(el.value || '').trim() : '';
  }

  window.updateZteUrlPreview = function(){
    var box = document.getElementById('ztUrlPreview');
    if (!box) return;
    var base = val('ztEpgUrl'), name = val('ztEpgName') || 'epg.xml', logo = val('ztLogo');
    var epg = base ? (base.replace(/\/+$/,'') + '/' + name.replace(/^\/+/,'')) : '（留空：生成时自动用当前访问地址）';
    var sameSite = !logo || !base || logo.replace(/\/+$/,'') === base.replace(/\/+$/,'');
    var logoUrl = logo
      ? (logo.replace(/\/+$/,'') + (sameSite ? '/api/download/频道名.png' : '/logo/频道名.png'))
      : '（留空：生成时自动用当前访问地址）';
    box.innerHTML = 'EPG 直链：<b>' + escAttr(epg) + '</b><br>台标地址：<b>' + escAttr(logoUrl) + '</b>';
  };

  window.fillZteBaseUrls = function(){
    var b = baseUrl();
    if (!b) return;
    var e = document.getElementById('ztEpgUrl'); if (e) e.value = b;
    var l = document.getElementById('ztLogo'); if (l) l.value = b;
    window.updateZteUrlPreview();
  };

  window.showZteConfigModal = function(){
    ztReq('GET','config').then(function(j){
      var c = j.config || {};
      var base = baseUrl();
      var epgServer = c.epg_server_url || base;
      var logoBase = c.logo_url || base;
      var px = j.proxy || {};

      document.getElementById('modalTitle').textContent = '⚙️ 中兴平台认证与EPG配置';
      document.getElementById('modalBody').innerHTML = `
        <div style="font-size:12px;color:#d46b08;background:#fff7e6;border:1px solid #ffd591;border-radius:6px;padding:8px 10px;margin-bottom:12px;line-height:1.7">
          中兴平台的参数与华为不同：<b>IPTVServer 地址</b>、<b>Authenticator</b> 都要按中兴抓包结果填写。
          四川各地市平台不一样，配置前请先确认当前地市是中兴平台。
        </div>

        <div style="font-weight:600;color:var(--primary);margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px;font-size:14px">基础参数</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px">
          <div class="form-group" style="flex:0 0 140px;margin-bottom:0">
            <label>启用定时任务</label>
            <select id="ztEnabled"><option value="false">关闭</option><option value="true">开启</option></select>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;line-height:1.4">仅控制定时任务，手动操作不受影响。</div>
          </div>
          <div class="form-group" style="flex:2.5 1 280px;margin-bottom:0">
            <label>IPTVServer 地址</label>
            <input id="ztServer" value="${escAttr(c.server)}" placeholder="http://182.137.9.86:8080">
          </div>
          <div class="form-group" style="flex:2 1 220px;margin-bottom:0">
            <label>账号 (UserID)</label>
            <input id="ztUserID" value="${escAttr(c.UserID)}">
          </div>
        </div>

        <div style="font-weight:600;color:var(--primary);margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px;font-size:14px">设备信息</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px">
          <div class="form-group" style="flex:1 1 130px;margin-bottom:0"><label>StbIP</label><input id="ztStbIp" value="${escAttr(c.StbIP)}"></div>
          <div class="form-group" style="flex:1 1 160px;margin-bottom:0"><label>STBID</label><input id="ztStbid" value="${escAttr(c.STBID)}"></div>
          <div class="form-group" style="flex:1.4 1 200px;margin-bottom:0"><label>UserToken</label><input id="ztUserToken" value="${escAttr(c.UserToken)}"></div>
          <div class="form-group" style="flex:0 0 110px;margin-bottom:0"><label>UserGroupNMB</label><input id="ztUserGroup" value="${escAttr(c.UserGroupNMB)}"></div>
          <div class="form-group" style="flex:0 0 110px;margin-bottom:0"><label>EPGGroupNMB</label><input id="ztEpgGroup" value="${escAttr(c.EPGGroupNMB)}"></div>
          <div class="form-group" style="flex:0 0 100px;margin-bottom:0"><label>LastTermno</label><input id="ztLastTermno" value="${escAttr(c.LastTermno)}"></div>
          <div class="form-group" style="flex:1 1 140px;margin-bottom:0"><label>stbinfo</label><input id="ztStbinfo" value="${escAttr(c.stbinfo)}"></div>
          <div class="form-group" style="flex:2 1 280px;margin-bottom:0"><label>UserAgent</label><input id="ztUA" value="${escAttr(c.UserAgent)}"></div>
        </div>

        <div style="font-weight:600;color:var(--primary);margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px;font-size:14px">安全认证</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px">
          <div class="form-group" style="flex:100%;margin-bottom:0">
            <label>Authenticator（抓包值，直接粘贴）</label>
            <input id="ztAuthn" value="${escAttr(c.Authenticator)}" style="font-family:monospace;font-size:12px">
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;line-height:1.5">
              中兴的 Authenticator 是机顶盒抓包得到的一串十六进制，<b>不能</b>像华为那样用 key 现场生成；换机顶盒/换账号后要重新抓。
            </div>
          </div>
        </div>

        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px">
          <span style="font-weight:600;color:var(--primary);font-size:14px">EPG / 播放输出</span>
          <button type="button" class="btn btn-sm" onclick="fillZteBaseUrls()" title="把 epg_server_url 和 logo_url 都填成当前访问地址">↺ 用当前访问地址</button>
          <span style="font-size:11px;color:var(--text-secondary)">留空也行：生成 m3u/txt 时会自动补</span>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px">
          <div class="form-group" style="flex:2 1 200px;margin-bottom:0"><label>epg_server_url</label><input id="ztEpgUrl" value="${escAttr(epgServer)}" placeholder="http://你的域名或IP" oninput="updateZteUrlPreview()"></div>
          <div class="form-group" style="flex:1 1 120px;margin-bottom:0"><label style="cursor:help;" title="系统固定为 epg.xml，不可修改">epgname (🔒只读)</label><input id="ztEpgName" value="${escAttr(c.epgname || 'epg.xml')}" readonly style="background:#f5f5f5;color:#888;cursor:not-allowed;"></div>
          <div class="form-group" style="flex:2 1 200px;margin-bottom:0"><label>live_proxy（组播转单播）</label><input id="ztLiveProxy" value="${escAttr(c.live_proxy)}" placeholder="http://192.168.100.1:4022"></div>
          <div class="form-group" style="flex:2 1 200px;margin-bottom:0"><label>replay_proxy（回看地址）</label><input id="ztReplayProxy" value="${escAttr(c.replay_proxy)}" placeholder="rtsp://192.168.100.1:554"></div>
          <div class="form-group" style="flex:2 1 200px;margin-bottom:0"><label>logo_url</label><input id="ztLogo" value="${escAttr(logoBase)}" placeholder="留空＝当前访问地址" oninput="updateZteUrlPreview()"></div>
          <div class="form-group" style="flex:0 0 140px;margin-bottom:0">
            <label>回看天数</label>
            <select id="ztEpgDays">
              ${[1,2,3,4,5,6,7,8,9,10].map(function(n){
                return '<option value="'+n+'"'+(Number(c.epg_days||8)===n?' selected':'')+'>'+n+' 天</option>';
              }).join('')}
            </select>
          </div>
          <div class="form-group" style="flex:1 1 200px;margin-bottom:0;display:flex;align-items:flex-end;padding-bottom:8px">
            <label style="display:inline-flex;align-items:center;gap:6px;font-weight:normal;font-size:13px;cursor:pointer;margin:0">
              <input type="checkbox" id="ztEnableGroup" style="width:auto" ${c.enable_group ? 'checked' : ''}> 生成文件时自动分组
            </label>
          </div>
        </div>
        <div id="ztUrlPreview" style="font-size:12px;color:var(--text-secondary);line-height:1.9;background:#fafafa;border:1px dashed #ddd;border-radius:6px;padding:8px 12px;margin-bottom:12px"></div>

        <div style="font-weight:600;color:var(--primary);margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:4px;font-size:14px">内置 RTSP 回看代理</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:10px">
          <div class="form-group" style="flex:1 1 200px;margin-bottom:0;display:flex;align-items:flex-end;padding-bottom:8px">
            <label style="display:inline-flex;align-items:center;gap:6px;font-weight:normal;font-size:13px;cursor:pointer;margin:0">
              <input type="checkbox" id="ztProxyEnabled" style="width:auto" ${c.rtsp_proxy_enabled ? 'checked' : ''}> 启用回看代理（保存后自动启动）
            </label>
          </div>
          <div class="form-group" style="flex:0 0 120px;margin-bottom:0"><label>监听端口</label><input id="ztProxyPort" value="${escAttr(c.rtsp_proxy_port)}"></div>
          <div class="form-group" style="flex:2 1 220px;margin-bottom:0"><label>真实 RTSP 服务器</label><input id="ztUpstream" value="${escAttr(c.rtsp_upstream)}" placeholder="218.89.44.4"></div>
          <div class="form-group" style="flex:0 0 120px;margin-bottom:0"><label>服务器端口</label><input id="ztUpstreamPort" value="${escAttr(c.rtsp_upstream_port)}"></div>
          <div class="form-group" style="flex:2 1 200px;margin-bottom:0"><label>丢弃的重定向节点（可空）</label><input id="ztBadServer" value="${escAttr(c.rtsp_proxy_bad_server)}" placeholder="192.168.102.139"></div>
        </div>
        <div id="ztProxyBox" style="font-size:12px;line-height:1.8;background:#f6ffed;border:1px solid #b7eb8f;border-radius:6px;padding:8px 10px;margin-bottom:12px">
          <div>当前状态：<b>${px.running ? '运行中' : '未运行'}</b>
            ${px.running ? ('· 端口 ' + escAttr(px.port) + ' · 转发到 ' + escAttr(px.upstream) + ':' + escAttr(px.upstream_port)) : ''}
          </div>
          <div style="color:var(--text-secondary)">代理作用：把播放器的回看请求转给真实服务器，并自动跟随 302 重定向。replay_proxy 一般填 <code>rtsp://本机内网IP:${escAttr(c.rtsp_proxy_port || 554)}</code>。</div>
          <div style="margin-top:6px">
            <button type="button" class="btn btn-sm" onclick="zteProxyStart(true)">▶️ 启动</button>
            <button type="button" class="btn btn-sm" onclick="zteProxyStop(true)">⏹ 停止</button>
            <button type="button" class="btn btn-sm" onclick="zteProxyRefresh()">↻ 刷新状态</button>
          </div>
        </div>

        <div style="font-size:12px;color:var(--text-secondary);line-height:1.7;border-top:1px dashed #ddd;padding-top:8px">
          💾 保存后写入 <code>data/config/app_config.json</code> 的 <code>telecom_zte</code> 段，与华为配置互不影响。<br>
          📺 中兴频道缓存：<code>data/config/zte_channels.json</code>；节目单：<code>data/json/频道名_zte_日期.json</code>；XML：<code>data/xml/epg_zte.xml</code>。
        </div>
      `;
      document.getElementById('modalActions').innerHTML = `
        <button class="btn btn-primary" onclick="saveZteConfigModal()">💾 保存配置</button>
        <button class="btn" onclick="forceCloseModal()">取消</button>
      `;
      if (typeof window.forceShowModal === 'function') window.forceShowModal();
      else document.getElementById('modal').classList.add('active');
      var en = document.getElementById('ztEnabled');
      if (en) en.value = c.enabled ? 'true' : 'false';
      window.updateZteUrlPreview();
    }).catch(function(e){
      alert('读取中兴配置失败: ' + e.message);
    });
  };

  window.saveZteConfigModal = function(){
    var body = { config: {
      enabled: val('ztEnabled') === 'true',
      server: val('ztServer'),
      UserID: val('ztUserID'),
      StbIP: val('ztStbIp'),
      STBID: val('ztStbid'),
      UserToken: val('ztUserToken'),
      UserGroupNMB: val('ztUserGroup'),
      EPGGroupNMB: val('ztEpgGroup'),
      LastTermno: val('ztLastTermno') || '0',
      stbinfo: val('ztStbinfo') || 'undefined',
      UserAgent: val('ztUA'),
      Authenticator: val('ztAuthn'),
      epg_server_url: val('ztEpgUrl') || baseUrl(),
      epgname: val('ztEpgName') || 'epg.xml',
      logo_url: val('ztLogo') || baseUrl(),
      live_proxy: val('ztLiveProxy'),
      replay_proxy: val('ztReplayProxy'),
      enable_group: !!(document.getElementById('ztEnableGroup') || {}).checked,
      epg_days: parseInt(val('ztEpgDays'), 10) || 8,
      rtsp_proxy_enabled: !!(document.getElementById('ztProxyEnabled') || {}).checked,
      rtsp_proxy_port: parseInt(val('ztProxyPort'), 10) || 554,
      rtsp_upstream: val('ztUpstream'),
      rtsp_upstream_port: parseInt(val('ztUpstreamPort'), 10) || 554,
      rtsp_proxy_bad_server: val('ztBadServer')
    }};
    ztReq('POST','config', body).then(function(j){
      if (j.success) {
        ztToast('中兴配置已保存' + (j.proxy_message ? ('（' + j.proxy_message + '）') : ''));
        if (typeof window.forceCloseModal === 'function') window.forceCloseModal();
        else document.getElementById('modal').classList.remove('active');
        if (typeof window.loadTelecomPlatform === 'function') window.loadTelecomPlatform(true);
        if (typeof window.refreshTelecomStatus === 'function') window.refreshTelecomStatus();
      } else {
        alert('保存失败: ' + (j.message || ''));
      }
    }).catch(function(e){ alert('保存失败: ' + e.message); });
  };

  // ════════════════════════════════════════════════════════════
  // 回看代理控制
  // ════════════════════════════════════════════════════════════

  function renderProxy(st){
    var el = document.getElementById('tcProxyStatus');
    if (el) {
      if (st && st.running) {
        el.innerHTML = '<span style="color:var(--success)">运行中</span> · 端口 <b>' + escAttr(st.port) + '</b>'
          + ' · 转发 <b>' + escAttr(st.upstream || '') + ':' + escAttr(st.upstream_port || '') + '</b>'
          + ' · 连接 ' + (st.active || 0) + ' / 累计 ' + (st.accepted || 0)
          + (st.last_error ? (' · <span style="color:var(--danger)">' + escAttr(st.last_error) + '</span>') : '');
      } else {
        el.innerHTML = '<span style="color:var(--text-secondary)">未运行</span>'
          + (st && st.last_error ? (' · <span style="color:var(--danger)">' + escAttr(st.last_error) + '</span>') : '');
      }
    }
    var box = document.getElementById('ztProxyBox');
    if (box && st) {
      box.innerHTML = '<div>当前状态：<b>' + (st.running ? '运行中' : '未运行') + '</b>'
        + (st.running ? (' · 端口 ' + escAttr(st.port) + ' · 转发到 ' + escAttr(st.upstream) + ':' + escAttr(st.upstream_port)) : '')
        + '</div>'
        + '<div style="color:var(--text-secondary)">代理作用：把播放器的回看请求转给真实服务器，并自动跟随 302 重定向。</div>'
        + '<div style="margin-top:6px">'
        + '<button type="button" class="btn btn-sm" onclick="zteProxyStart(true)">▶️ 启动</button> '
        + '<button type="button" class="btn btn-sm" onclick="zteProxyStop(true)">⏹ 停止</button> '
        + '<button type="button" class="btn btn-sm" onclick="zteProxyRefresh()">↻ 刷新状态</button>'
        + '</div>';
    }
  }

  window.zteProxyRefresh = function(){
    return ztReq('GET','proxy/status').then(function(st){
      renderProxy(st);
      return st;
    }).catch(function(e){
      var el = document.getElementById('tcProxyStatus');
      if (el) el.textContent = '状态读取失败: ' + ztErr(e);
    });
  };

  window.zteProxyStart = function(fromModal){
    var body = {};
    if (fromModal) {
      body = {
        port: parseInt(val('ztProxyPort'), 10) || 554,
        upstream: val('ztUpstream'),
        upstream_port: parseInt(val('ztUpstreamPort'), 10) || 554,
        bad_server: val('ztBadServer')
      };
    }
    ztReq('POST','proxy/start', body).then(function(j){
      ztToast(j.message || (j.success ? '代理已启动' : '启动失败'), j.success ? 'success' : 'error');
      renderProxy(j.status);
    }).catch(function(e){ ztToast('启动失败: ' + ztErr(e), 'error'); });
  };

  window.zteProxyStop = function(){
    ztReq('POST','proxy/stop',{}).then(function(j){
      ztToast(j.message || '已停止');
      renderProxy(j.status);
    }).catch(function(e){ ztToast('停止失败: ' + ztErr(e), 'error'); });
  };
})();
