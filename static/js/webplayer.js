/**
 * webplayer.js - 终极版：悬浮画中画 + 回看高亮 + 强制解除静音
 */

let hlsPlayer = null;
let mpegtsPlayer = null;
let cachedEpgXmlDoc = null;    
let allM3uFiles = [];
let currentStreamUrls = [];    
let currentCatchupUrl = '';    
let currentChannelName = '';
let currentTvgId = '';
let currentCatchupIndex = -1;  
let currentEpgDay = '';        
let currentChannelProgrammes = []; 
let catchupEndTimer = null;    

let currentPlayingCatchupStart = null;
let currentPlayingCatchupStop = null;

// ★ 获取本地时区的时间字符串 YYYYMMDDHHmmss
function getLocalNowStr() {
  const now = new Date();
  const Y = now.getFullYear();
  const M = String(now.getMonth() + 1).padStart(2, '0');
  const D = String(now.getDate()).padStart(2, '0');
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  const s = String(now.getSeconds()).padStart(2, '0');
  return `${Y}${M}${D}${h}${m}${s}`;
}

// 清理播放器
function destroyPlayers() {
  const video = document.getElementById('webPlayerVideo');
  if (video) {
    video.pause();
    video.removeAttribute('src');
    video.load();
    video.onerror = null;
    video.onplaying = null;
  }
  if (hlsPlayer) { hlsPlayer.destroy(); hlsPlayer = null; }
  if (mpegtsPlayer) { mpegtsPlayer.destroy(); mpegtsPlayer = null; }
  if (catchupEndTimer) { clearInterval(catchupEndTimer); catchupEndTimer = null; }
}

// ★ 实现窗口拖拽移动
document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('webPlayerModal');
  const header = document.getElementById('webPlayerHeader');
  const content = document.getElementById('webPlayerModalContent');
  
  if(header && content && modal) {
    let isDragging = false;
    let startX, startY, startLeft, startTop;

    header.addEventListener('mousedown', (e) => {
      if (e.target.closest('button') || e.target.closest('select') || e.target.closest('input')) return;
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = content.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      
      const video = document.getElementById('webPlayerVideo');
      if (video) video.style.pointerEvents = 'none';
      
      document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      content.style.left = (startLeft + dx) + 'px';
      content.style.top = (startTop + dy) + 'px';
      content.style.right = 'auto';
    });

    document.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        const video = document.getElementById('webPlayerVideo');
        if (video) video.style.pointerEvents = 'auto';
        document.body.style.userSelect = '';
      }
    });
  }
});

// 打开播放器
window.openWebPlayer = async function() {
  const modal = document.getElementById('webPlayerModal');
  const content = document.getElementById('webPlayerModalContent');
  modal.classList.add('active');
  
  if (content) {
    content.style.left = 'auto';
    content.style.right = '20px';
    content.style.top = '80px';
  }

  const msg = document.getElementById('webPlayerMsg');
  const fileSel = document.getElementById('webPlayerFileSource');
  const chSel = document.getElementById('webPlayerSource');
  
  msg.textContent = '正在读取文件列表...';
  fileSel.innerHTML = '<option value="">加载中...</option>';
  chSel.innerHTML = '<option value="">选择频道...</option>';
  document.getElementById('webPlayerEpg').style.display = 'none';
  destroyPlayers();

  try {
    const r = await fetch('/api/m3u');
    const d = await r.json();
    allM3uFiles = d.files || [];
    
    if (allM3uFiles.length === 0) {
      msg.textContent = '没有找到任何 M3U 文件。';
      return;
    }

    fileSel.innerHTML = '';
    allM3uFiles.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name;
      fileSel.appendChild(opt);
    });

    const defaultFile = allM3uFiles.find(f => f.name === 'sctv.m3u') 
                     || allM3uFiles.find(f => f.name === 'telecom.m3u') 
                     || allM3uFiles[0];
    
    if (defaultFile) {
      fileSel.value = defaultFile.name;
      await loadChannelsFromFile(defaultFile.name);
    }
  } catch (e) {
    msg.textContent = '加载失败: ' + e.message;
  }
};

// 切换文件
window.changeWebPlayerFile = function() {
  const fileName = document.getElementById('webPlayerFileSource').value;
  destroyPlayers();
  document.getElementById('webPlayerMsg').textContent = '';
  document.getElementById('webPlayerEpg').style.display = 'none';
  if (fileName) loadChannelsFromFile(fileName);
};

// 解析频道列表
async function loadChannelsFromFile(fileName) {
  const msg = document.getElementById('webPlayerMsg');
  const chSel = document.getElementById('webPlayerSource');
  chSel.innerHTML = '<option value="">选择频道...</option>';
  msg.textContent = '正在加载频道...';

  try {
    const cr = await fetch(`/api/m3u/${encodeURIComponent(fileName)}?merge=false`);
    const cd = await cr.json();
    const entries = cd.parsed?.entries || [];
    
    const channelMap = new Map();

    entries.forEach((e) => {
      let rawUrls = [];
      if (e._urls && e._urls.length > 0) {
        rawUrls = e._urls;
      } else if (e.url) {
        rawUrls = [e.url];
      }

      const validUrls = rawUrls.filter(url => typeof url === 'string' && url.trim().length > 0);
      if (validUrls.length === 0) return;

      const name = e.display_name || e.tvg_name || e.name || '未知频道';
      const tvgId = e.tvg_id || name;
      const key = tvgId + '||' + name; 
      
      if (channelMap.has(key)) {
        const existing = channelMap.get(key);
        validUrls.forEach(u => {
          if (!existing.urls.includes(u)) existing.urls.push(u);
        });
        if (!existing.catchup && e.catchup_source) existing.catchup = e.catchup_source;
      } else {
        channelMap.set(key, {
          name: name, tvgId: tvgId, catchup: e.catchup_source || '', urls: [...validUrls]
        });
      }
    });

    let count = 0;
    for (const data of channelMap.values()) {
      const opt = document.createElement('option');
      opt.value = count;
      opt.textContent = data.name;
      opt.dataset.urls = JSON.stringify(data.urls); 
      opt.dataset.catchup = data.catchup;
      opt.dataset.channelName = data.name;
      opt.dataset.tvgId = data.tvgId; 
      chSel.appendChild(opt);
      count++;
    }

    msg.textContent = count === 0 ? '该文件中没有可播放的地址。' : '请选择频道进行播放。';
  } catch (e) {
    msg.textContent = '读取文件失败: ' + e.message;
  }
}

// 切换频道
window.changeWebPlayerSource = function() {
  currentPlayingCatchupStart = null;
  currentPlayingCatchupStop = null;

  const sel = document.getElementById('webPlayerSource');
  const opt = sel.options[sel.selectedIndex];
  const lineSel = document.getElementById('webPlayerLineSource');

  try { currentStreamUrls = JSON.parse(opt.dataset.urls || '[]'); } catch(e) { currentStreamUrls = []; }
  currentCatchupUrl = opt.dataset.catchup;
  currentChannelName = opt.dataset.channelName;
  currentTvgId = opt.dataset.tvgId;

  if (currentStreamUrls.length > 1) {
    lineSel.style.display = 'inline-block';
    lineSel.innerHTML = currentStreamUrls.map((u, i) => `<option value="${i}">线路 ${i+1}</option>`).join('');
  } else {
    lineSel.style.display = 'none';
    lineSel.innerHTML = '';
  }

  // ★ 强制解除静音，音量 100%
  const video = document.getElementById('webPlayerVideo');
  video.muted = false;
  video.volume = 1.0;
  const volSlider = document.getElementById('volumeSlider');
  if (volSlider) volSlider.value = 1.0;
  const display = document.getElementById('timeDisplay');
  if (display) display.textContent = '音量 100%';

  if (currentStreamUrls.length > 0) {
    playStream(currentStreamUrls[0], false);
  } else {
    document.getElementById('webPlayerMsg').textContent = '该频道没有可用地址。';
  }

  if (currentTvgId) loadChannelEPG(currentTvgId, currentChannelName);
};

// 切换线路事件
window.changeWebPlayerLine = function() {
  const lineSel = document.getElementById('webPlayerLineSource');
  if (currentStreamUrls[lineSel.value]) {
    playStream(currentStreamUrls[lineSel.value], false);
  }
};

// 核心播放器挂载函数
function playStream(url, isCatchup = false) {
  const video = document.getElementById('webPlayerVideo');
  const msg = document.getElementById('webPlayerMsg');
  
  destroyPlayers();
  if (!url) { msg.textContent = '缺少视频流地址'; return; }
  msg.textContent = isCatchup ? '正在连接回看视频流...' : '正在连接直播视频流...';

  // ★ 移除强制静音代码，保留当前状态
  // video.volume = 1.0; 
  // video.muted = true;
  
  let finalUrl = url;
  if (url.startsWith('rtp://')) {
      finalUrl = '/api/stream/rtp/' + url.replace('rtp://', '');
  } else if (url.startsWith('rtsp://')) {
      finalUrl = '/api/stream/rtsp/' + url.replace('rtsp://', '');
  } else if (url.startsWith('igmp://')) {
      finalUrl = '/api/stream/rtp/' + url.replace('igmp://', '');
  } else if (url.startsWith('udp://')) {
      finalUrl = '/api/stream/udp/' + url.replace('udp://', '');
  } else if (url.startsWith('http') && (url.includes('/rtp/') || url.includes('/rtsp/') || url.includes('/udp/'))) {
      let idx = url.includes('/rtp/') ? url.indexOf('/rtp/') : 
               (url.includes('/rtsp/') ? url.indexOf('/rtsp/') : url.indexOf('/udp/'));
      finalUrl = '/api/stream' + url.substring(idx);
  }

  video.onerror = function() {
    if (mpegtsPlayer) {
      mpegtsPlayer.pause();
      mpegtsPlayer.unload();
      mpegtsPlayer.destroy();
      mpegtsPlayer = null;
    }
    msg.innerHTML = '⚠️ 播放失败：您的设备浏览器不支持该流的音视频编码<br><span style="font-size:12px;color:#ff7875;">（通常是 AC3 音频或 H.265/4K 导致，请尝试播放其他频道）</span>';
  };

  if (finalUrl.includes('.m3u8')) {
    if (typeof Hls !== 'undefined' && Hls.isSupported()) {
      hlsPlayer = new Hls();
      hlsPlayer.loadSource(finalUrl);
      hlsPlayer.attachMedia(video);
      hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => { 
        msg.textContent = ''; 
        video.play().catch(() => { msg.textContent = '请点击中间播放按钮。'; });
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = finalUrl; msg.textContent = ''; 
      video.play().catch(() => { msg.textContent = '请点击中间播放按钮。'; });
    }
  } else {
    if (typeof mpegts !== 'undefined' && mpegts.getFeatureList().mseLivePlayback) {
      try {
        mpegtsPlayer = mpegts.createPlayer({ 
          type: 'mse', 
          isLive: !isCatchup, 
          url: finalUrl 
        });
        mpegtsPlayer.attachMediaElement(video);
        mpegtsPlayer.load();
        
        mpegtsPlayer.on(mpegts.Events.MEDIA_INFO, () => {
          msg.textContent = '';
          video.play().catch(() => { msg.textContent = '请点击中间播放按钮。'; });
        });
        
        video.onplaying = () => { 
          msg.textContent = ''; 
          const centerBtn = document.getElementById('centerPlayBtn');
          if (centerBtn) centerBtn.style.opacity = '0';
        };
        
        mpegtsPlayer.on(mpegts.Events.ERROR, (type, details) => {
          if (type === 'MediaError') return; 
        });
      } catch (err) {
        msg.textContent = '当前浏览器不支持该流媒体格式。';
      }
    } else {
      msg.textContent = '当前浏览器不支持 TS 流播放。';
    }
  }

  if (isCatchup) checkCatchupEnd(currentCatchupIndex);
}

function checkCatchupEnd(index) {
  if (catchupEndTimer) clearInterval(catchupEndTimer);
  if (index < 0) return; 
  catchupEndTimer = setInterval(() => {
    const video = document.getElementById('webPlayerVideo');
    if (!video || !mpegtsPlayer) { clearInterval(catchupEndTimer); return; }
    if (video.ended || (video.duration && video.currentTime >= video.duration - 1)) {
      clearInterval(catchupEndTimer);
      playNextProgram();
    }
  }, 2000);
}

function playNextProgram() {
  const nextProg = currentChannelProgrammes[currentCatchupIndex + 1];
  if (nextProg) {
    window.playCatchupFromEPG(nextProg.start, nextProg.stop, currentCatchupIndex + 1);
  } else {
    window.playLiveFromEPG();
  }
}

async function loadChannelEPG(tvgId, channelName) {
  const epgBox = document.getElementById('webPlayerEpg');
  const epgList = document.getElementById('webPlayerEpgList');
  epgBox.style.display = 'block';
  epgList.innerHTML = '<span style="color:#888">正在加载节目单...</span>';

  try {
    if (!cachedEpgXmlDoc) {
      const r = await fetch('/api/xml/epg.xml');
      const xmlText = await r.text();
      cachedEpgXmlDoc = new DOMParser().parseFromString(xmlText, "text/xml");
    }

    const doc = cachedEpgXmlDoc;
    let channelNode = doc.querySelector(`channel[id="${tvgId}"]`);
    if (!channelNode) {
      const channels = Array.from(doc.querySelectorAll('channel'));
      channelNode = channels.find(c => {
        const dn = c.querySelector('display-name');
        return dn && (dn.textContent === channelName || dn.textContent === tvgId);
      });
    }

    if (!channelNode) { epgList.innerHTML = '<span style="color:#888">未找到该频道的节目。</span>'; return; }

    const realTvgId = channelNode.getAttribute('id');
    const programmes = Array.from(doc.querySelectorAll(`programme[channel="${realTvgId}"]`));
    if (programmes.length === 0) { epgList.innerHTML = '<span style="color:#888">暂无预告。</span>'; return; }

    currentChannelProgrammes = programmes.map(p => {
      return {
        start: p.getAttribute('start').substring(0, 14),
        stop: p.getAttribute('stop').substring(0, 14),
        title: p.querySelector('title')?.textContent || '未知'
      };
    });

    const days = [...new Set(currentChannelProgrammes.map(p => p.start.substring(0, 8)))].sort();
    const todayStr = getLocalNowStr().substring(0, 8);
    
    let navHtml = `<div style="display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px;border-bottom:1px solid #444;scrollbar-width:none;">`;
    days.forEach(d => {
      let label = d.substring(4,6) + '月' + d.substring(6,8) + '日';
      if (d === todayStr) label = '今天';
      const isActive = (d === currentEpgDay);
      const btnStyle = isActive 
        ? 'background:#1677ff;color:#fff;border-color:#1677ff;' 
        : 'background:#333;color:#aaa;border-color:#444;';
      navHtml += `<button class="btn btn-sm" style="flex-shrink:0;padding:2px 10px;font-size:12px;${btnStyle}" onclick="switchEpgDay('${d}')">${label}</button>`;
    });
    navHtml += `</div>`;

    if (!currentEpgDay || !days.includes(currentEpgDay)) {
      currentEpgDay = days.includes(todayStr) ? todayStr : days[days.length - 1];
    }

    epgList.innerHTML = navHtml + `<div id="epgDayContent" style="display:flex;flex-direction:column;gap:2px;"></div>`;
    renderEpgDayContent();

  } catch (e) {
    epgList.innerHTML = '<span style="color:var(--danger)">加载失败</span>';
  }
}

window.switchEpgDay = function(day) {
  currentEpgDay = day;
  loadChannelEPG(currentTvgId, currentChannelName);
};

function renderEpgDayContent() {
  const container = document.getElementById('epgDayContent');
  if (!container) return;

  const nowStr = getLocalNowStr();
  const todayStr = nowStr.substring(0, 8);
  const dayProgrammes = currentChannelProgrammes.filter(p => p.start.substring(0, 8) === currentEpgDay);

  if (dayProgrammes.length === 0) {
    container.innerHTML = '<span style="color:#888">这一天没有节目数据。</span>';
    return;
  }

  let html = '';
  dayProgrammes.forEach((p) => {
    const startStr = p.start;
    const stopStr = p.stop;
    const title = p.title;

    const isToday = (currentEpgDay === todayStr);
    const isPlaying = isToday && (startStr <= nowStr && stopStr > nowStr);
    const isPast = !isPlaying && (stopStr < nowStr);
    
    const isCatchupPlaying = (currentPlayingCatchupStart === startStr && currentPlayingCatchupStop === stopStr);
    
    const startTimeDisplay = startStr.substring(8, 10) + ':' + startStr.substring(10, 12);

    let style = 'padding:6px 8px; border-bottom:1px solid #333; cursor:pointer; border-radius:4px; transition:background 0.2s;';
    let icon = '🕒';
    let rightTag = '';

    if (isCatchupPlaying) {
      style += 'color:#1677ff; font-weight:bold; background:rgba(22,119,255,0.15); border-left: 3px solid #1677ff;';
      icon = '▶️';
      rightTag = '<span style="font-size:11px;background:#1677ff;color:#fff;padding:1px 4px;border-radius:2px;margin-left:6px;">回看中</span>';
    } else if (isPlaying) {
      style += 'color:#52c41a; font-weight:bold; background:rgba(82,196,26,0.1);';
      icon = '▶️';
      rightTag = '<span style="font-size:11px;background:#52c41a;color:#fff;padding:1px 4px;border-radius:2px;margin-left:6px;">直播中</span>';
    } else if (isPast) {
      style += 'color:#999;';
      icon = '⏪';
      if (currentCatchupUrl) rightTag = '<span style="font-size:11px;background:#333;color:#ccc;padding:1px 4px;border-radius:2px;margin-left:6px;">点击回看</span>';
    } else {
      style += 'color:#aaa;';
    }

    let clickEvent = '';
    let hoverAttr = '';
    
    if (isPlaying && !isCatchupPlaying) {
      clickEvent = `onclick="playLiveFromEPG()"`;
      hoverAttr = `onmouseover="this.style.background='#2a2a2a'" onmouseout="this.style.background='rgba(82,196,26,0.1)'"`;
    } else if (isPast && currentCatchupUrl) {
      const globalIndex = currentChannelProgrammes.findIndex(cp => cp.start === startStr && cp.stop === stopStr);
      clickEvent = `onclick="playCatchupFromEPG('${startStr}', '${stopStr}', ${globalIndex})"`;
      
      if (isCatchupPlaying) {
        hoverAttr = `onmouseover="this.style.background='rgba(22,119,255,0.2)'" onmouseout="this.style.background='rgba(22,119,255,0.15)'"`;
      } else {
        hoverAttr = `onmouseover="this.style.background='#2a2a2a'" onmouseout="this.style.background='transparent'"`;
      }
    }

    html += `<div id="epg_item_${currentChannelProgrammes.indexOf(p)}" style="${style}" ${clickEvent} ${hoverAttr} title="${title}">
      ${icon} ${startTimeDisplay} - ${title} ${rightTag}
    </div>`;
  });

  container.innerHTML = html;
  
  if (currentEpgDay === todayStr) {
    setTimeout(() => {
      let activeEl = container.querySelector('[style*="1677ff"]') || container.querySelector('[style*="52c41a"]');
      if (activeEl) activeEl.scrollIntoView({ block: 'center' });
    }, 100);
  }
}

window.playCatchupFromEPG = function(startStr, stopStr, index) {
  if (!currentCatchupUrl) { alert('该频道不支持回看。'); return; }
  currentCatchupIndex = index !== undefined ? index : -1;

  currentPlayingCatchupStart = startStr;
  currentPlayingCatchupStop = stopStr;

  let catchupUrl = currentCatchupUrl
    .replace('${(b)yyyyMMddHHmmss}', startStr)
    .replace('${(e)yyyyMMddHHmmss}', stopStr)
    .replace('{utc}', startStr)
    .replace('{utcend}', stopStr);

  playStream(catchupUrl, true);
  renderEpgDayContent();
  
  const el = document.getElementById(`epg_item_${index}`);
  if (el) el.scrollIntoView({ block: 'center' });
};

window.playLiveFromEPG = function() {
  currentPlayingCatchupStart = null;
  currentPlayingCatchupStop = null;

  const lineIdx = document.getElementById('webPlayerLineSource').value || 0;
  if (currentStreamUrls[lineIdx]) {
    playStream(currentStreamUrls[lineIdx], false);
    renderEpgDayContent();
  }
};

window.togglePlayPause = function() {
  const video = document.getElementById('webPlayerVideo');
  const msg = document.getElementById('webPlayerMsg');
  
  if (!video) return; 
  
  if (video.paused) {
    msg.textContent = '正在唤醒视频...';
    
    // ★ 只要点击播放，强行解除静音并设为 100%
    video.muted = false;
    video.volume = 1.0;
    const volSlider = document.getElementById('volumeSlider');
    if (volSlider) volSlider.value = 1.0;
    const display = document.getElementById('timeDisplay');
    if (display) display.textContent = '音量 100%';
    
    video.play().then(() => {
      msg.textContent = '';
      const centerBtn = document.getElementById('centerPlayBtn');
      if (centerBtn) centerBtn.style.opacity = '0';
      if (typeof mpegtsPlayer !== 'undefined' && mpegtsPlayer !== null) {
        mpegtsPlayer.play().catch(()=>{});
      }
    }).catch(e => {
      if (e.name === 'NotSupportedError') {
        msg.innerHTML = '❌ 当前浏览器不支持该视频格式<br><span style="font-size:12px;color:#ff7875;">可能是 H.265/4K 编码，或您的设备浏览器不支持 MSE 流。</span>';
      } else {
        msg.textContent = '唤醒失败: ' + (e.name || '未知错误');
      }
    });
  } else {
    if (typeof mpegtsPlayer !== 'undefined' && mpegtsPlayer !== null) {
      mpegtsPlayer.pause();
    } else {
      video.pause();
    }
    const centerBtn = document.getElementById('centerPlayBtn');
    if (centerBtn) centerBtn.style.opacity = '1';
  }
};

window.changeVolume = function(val) {
  const video = document.getElementById('webPlayerVideo');
  video.muted = false; 
  video.volume = parseFloat(val);
  const display = document.getElementById('timeDisplay');
  if (display) {
    display.textContent = '音量 ' + Math.round(val * 100) + '%';
  }
};

document.addEventListener('DOMContentLoaded', () => {
  const video = document.getElementById('webPlayerVideo');
  const playIcon = document.getElementById('centerPlayIcon');
  const pauseIcon = document.getElementById('centerPauseIcon');
  const centerBtn = document.getElementById('centerPlayBtn');
  const controls = document.getElementById('customControls');
  const container = document.getElementById('webPlayerContainer');
  
  if (!video || !playIcon || !pauseIcon || !container) return;

  container.addEventListener('mousemove', () => controls.style.opacity = '1');
  container.addEventListener('mouseleave', () => controls.style.opacity = '0');

  video.addEventListener('play', () => {
    playIcon.style.display = 'none';
    pauseIcon.style.display = 'block';
    centerBtn.style.opacity = '0'; 
  });

  video.addEventListener('pause', () => {
    playIcon.style.display = 'block';
    pauseIcon.style.display = 'none';
    centerBtn.style.opacity = '1'; 
  });

  video.addEventListener('waiting', () => {
    if (video.paused) centerBtn.style.opacity = '1';
  });
});

window.closeWebPlayer = function() {
  document.getElementById('webPlayerModal').classList.remove('active');
  destroyPlayers();
};

window.toggleWebPlayerFullscreen = function() {
  const container = document.getElementById('webPlayerContainer');
  if (!document.fullscreenElement && !document.webkitFullscreenElement) {
    if (container.requestFullscreen) container.requestFullscreen();
    else if (container.webkitRequestFullscreen) container.webkitRequestFullscreen();
  } else {
    if (document.exitFullscreen) document.exitFullscreen();
    else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
  }
};

window.downloadExe = function() {
  const a = document.createElement('a');
  a.href = '/tools/IPTV.exe';
  a.download = 'IPTV.exe';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  if (typeof showToast === 'function') showToast('开始下载 IPTV.exe');
  else alert('开始下载 IPTV.exe');
};