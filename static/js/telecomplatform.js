/**
 * telecomplatform.js - 电信平台选择（华为 / 中兴）
 *
 * 四川各地市的电信平台不一样，配置一次后再次切换必须给用户明确警告：
 * 先 POST /api/telecom/platform 拿服务端的警告文案，弹窗确认后才真正切换。
 * 切换完成后：
 * - 更新 window.TC_ACTIVE_PLATFORM，telecomepg.js 的按钮会自动分发给中兴脚本
 * - 重置执行日志（两个平台各有一份日志缓冲）
 * - 刷新频道列表 / 状态条 / 代理状态
 */
(function(){
  function esc(v){
    return String(v==null?'':v)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function toast(msg, type){
    if (typeof showToast === 'function') showToast(msg, type || 'success');
  }

  function platformLabel(){
    return window.TC_ACTIVE_PLATFORM_LABEL || '华为平台';
  }

  window.TC_ACTIVE_PLATFORM = window.TC_ACTIVE_PLATFORM || 'huawei';
  window.TC_ACTIVE_PLATFORM_LABEL = window.TC_ACTIVE_PLATFORM_LABEL || '华为平台';

  function renderSwitch(active){
    var box = document.getElementById('tcPlatformSwitch');
    if (!box) return;
    var btns = box.querySelectorAll('button[data-platform]');
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i];
      var on = b.getAttribute('data-platform') === active;
      b.className = 'btn btn-sm' + (on ? ' btn-primary' : '');
      b.style.opacity = on ? '1' : '0.75';
    }
  }

  function renderInfo(platforms){
    var box = document.getElementById('tcPlatformInfo');
    if (!box) return;
    if (!platforms || !platforms.length) { box.style.display = 'none'; return; }
    box.style.display = '';
    box.innerHTML = platforms.map(function(p){
      var ok = p.configured
        ? '<span style="color:var(--success)">已配置</span>'
        : '<span style="color:var(--danger)">未配置</span>';
      return '<div>· <b>' + esc(p.label) + '</b>：' + ok
        + ' · 频道缓存 <b>' + (p.channels || 0) + '</b> 个'
        + (p.updated ? '（' + esc(p.updated) + '）' : '')
        + ' · 输出 <code>' + esc(p.m3u || '') + '</code> / <code>' + esc(p.xml || '') + '</code>'
        + '</div>';
    }).join('');
  }

  function renderHint(activeLabel, platforms, active){
    var el = document.getElementById('tcPlatformHint');
    if (!el) return;
    var cur = null;
    (platforms || []).forEach(function(p){ if (p.id === active) cur = p; });
    var extra = '';
    if (cur && cur.config_error) {
      extra = ' · <span style="color:var(--danger)">' + esc(cur.config_error) + '</span>';
    } else if (cur) {
      extra = ' · <span style="color:var(--success)">' + (cur.configured ? '已配置' : '未配置')
        + '</span> · 频道缓存 <b>' + (cur.channels || 0) + '</b> 个';
    }
    el.innerHTML = '当前生效：<b>' + esc(activeLabel) + '</b>' + extra
      + '<br><span style="font-size:11px">四川各地市平台不同，切换前会弹窗确认；两个平台的配置和数据各存各的文件。</span>';
  }

  function renderProxyRow(active){
    var row = document.getElementById('tcProxyRow');
    if (!row) return;
    var isZte = active === 'zte';
    row.style.display = isZte ? '' : 'none';
    if (isZte && typeof window.zteProxyRefresh === 'function') {
      window.zteProxyRefresh();
    }
  }

  // ════════════════════════════════════════════════════════════
  // 读取 / 切换
  // ════════════════════════════════════════════════════════════

  window.loadTelecomPlatform = function(force){
    return fetch('/api/telecom/platform', {credentials:'include'})
      .then(function(r){ return r.json() })
      .then(function(d){
        var changed = (window.TC_ACTIVE_PLATFORM !== d.active);
        window.TC_ACTIVE_PLATFORM = d.active;
        window.TC_ACTIVE_PLATFORM_LABEL = d.active_label;
        renderSwitch(d.active);
        renderHint(d.active_label, d.platforms, d.active);
        renderInfo(d.platforms);
        renderProxyRow(d.active);
        if (changed || force) {
          if (typeof window.resetTelecomLog === 'function') window.resetTelecomLog();
          if (typeof window.refreshTelecomLog === 'function') window.refreshTelecomLog();
        }
        return d;
      })
      .catch(function(e){
        var el = document.getElementById('tcPlatformHint');
        if (el) el.innerHTML = '<span style="color:var(--danger)">平台状态读取失败: ' + esc(e.message) + '</span>';
      });
  };

  function postSwitch(platform, confirm){
    return fetch('/api/telecom/platform', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify({platform: platform, confirm: !!confirm})
    }).then(function(r){
      return r.text().then(function(t){
        var j = null; try { j = JSON.parse(t); } catch(e){}
        if (!r.ok) throw new Error((j && (j.detail || j.message)) || ('HTTP ' + r.status));
        return j || {};
      });
    });
  }

  function afterSwitched(target, label){
    toast('已切换到「' + label + '」，正在后台重建 sctv / XML');
    return window.loadTelecomPlatform(true).then(function(){
      if (typeof window.refreshTelecomStatus === 'function') window.refreshTelecomStatus();
      if (typeof window.loadTelecomSchedule === 'function') window.loadTelecomSchedule();
      // 重置频道表的编辑状态，避免把上一平台的编辑内容带过来
      if (typeof window.loadTelecomChannelsPage === 'function') window.loadTelecomChannelsPage();
      else if (typeof window.telecomLoadCachedChannels === 'function') window.telecomLoadCachedChannels();
      if (typeof loadOverviewSources === 'function') loadOverviewSources();
    });
  }

  window.switchTelecomPlatform = function(platform){
    if (!platform) return;
    if (platform === window.TC_ACTIVE_PLATFORM) {
      toast('当前已经是「' + platformLabel() + '」', 'success');
      return;
    }
    // 第一次不带 confirm：让服务端判断是否需要警告
    postSwitch(platform, false).then(function(d){
      if (d && d.success) {
        return afterSwitched(d.active, d.active_label || platform);
      }
      var msg = (d && d.message) || '切换平台后需要重新认证并获取频道/EPG，确定继续？';
      var proceed = function(ok){
        if (!ok) { toast('已取消切换', 'error'); return; }
        return postSwitch(platform, true).then(function(r){
          if (r && r.success) return afterSwitched(r.active, r.active_label || platform);
          toast('切换失败: ' + ((r && r.message) || ''), 'error');
        });
      };
      if (typeof customConfirm === 'function') {
        return customConfirm(msg, '⚠️ 切换电信平台', '我已确认，切换', '取消').then(proceed);
      }
      var ok = window.confirm(msg);
      return proceed(ok);
    }).catch(function(e){
      toast('切换失败: ' + e.message, 'error');
    });
  };
})();
