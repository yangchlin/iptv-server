/**
 * ku9apps.js - IPTV软件配置面板（Ku9 APK 配置编辑）
 * 嵌入在 iptv-server 仪表盘中作为一个Tab
 */

/* ============= 数据定义 ============= */

const addrItems5 = [
  {name:'预置 M3U'},
  {name:'预置 EPG'},
  {name:'预置 JSON'},
  {name:'预置头部请求'},
  {name:'预置用户代理'}
];
const addrItems3 = [
  {name:'启动图片路径'},
  {name:'退出图片路径'},
  {name:'启动数据提示文字'},
  {name:'启动图停留时间(ms)'},
  {name:'右上角时间格式'}
];

/* ===== 快捷菜单勾选（表3）：四行七列 ===== */
const shortcutRows = [
  ['设置','媒体信息','列表订阅','EPG 订阅','Headers','UserAgent','频道搜索'],
  ['轨道切换','设备信息','APP 信息','渲染类型','安全 DNS','清除数据','回放方式'],
  ['RTSP 通道','导航模式','EPG 展示','界面设置','图标设置','数据备份','浏览内核'],
  ['断线重连','自启设置','分组类型','视频标签','频道推送','JSON 解析','无线投屏']
];

const XMLT = (()=>{
  let a=['默认时区'];
  for(let i=1;i<=12;i++) a.push('-'+i+'小时');
  a.push('+0小时');
  for(let i=1;i<=12;i++) a.push('+'+i+'小时');
  let m={};
  a.forEach((o,i)=>m[o]=i);
  return {opts:a, map:m};
})();

const selectItems = [
  {name:'画面比例', key:'PLAY_SCALE', opts:['默认','16:9','4:3','填充','原始','裁剪'], map:{'默认':0,'16:9':1,'4:3':2,'填充':3,'原始':4,'裁剪':6}},
  {name:'解码方式', key:'PLAY_TYPE', opts:['系统解码','IJK硬解','IJK软解','EXO硬解','EXO软解'], map:{'系统解码':0,'IJK硬解':1,'IJK软解':2,'EXO硬解':3,'EXO软解':4}},
  {name:'超时换源', key:'LIVE_CONNECT_TIMEOUT', opts:['5s','10s','15s','20s','25s','30s'], map:{'5s':0,'10s':1,'15s':2,'20s':3,'25s':4,'30s':5}},
  {name:'EPG解析方式', key:'EPGTYPE_SELECT', opts:['百川/DIYP','超级TV','XmlTV'], map:{'百川/DIYP':0,'超级TV':1,'XmlTV':2}},
  {name:'EPG缓存设置', key:'EPGCACHE_SELECT', opts:['关闭缓存','每天2点','每天4点','每天6点','每天8点','每天10点','每天12点','每天14点','每天16点','每天18点','每天20点','每天22点'], map:{'关闭缓存':0,'每天2点':1,'每天4点':2,'每天6点':3,'每天8点':4,'每天10点':5,'每天12点':6,'每天14点':7,'每天16点':8,'每天18点':9,'每天20点':10,'每天22点':11}},
  {name:'XML时间偏移', key:'TIME_ZONE_SELECT', opts:XMLT.opts, map:XMLT.map},
  {name:'EPG缓存路径', key:'EPG_CACHE_PATH_SET', opts:['外部储存','内部储存'], map:{'外部储存':true,'内部储存':false}},
  {name:'渲染类型', key:'PLAY_RENDER', opts:['texture','surface'], map:{'texture':0,'surface':1}},
  {name:'RTSP通道', key:'RTSP_TCP_SELECT', opts:['TCP','UDP'], map:{'TCP':0,'UDP':1}},
  {name:'安全DNS选项', key:'DOH_URL', opts:['关闭','腾讯','阿里','360','Google','AdGuard','Quad9'], map:{'关闭':0,'腾讯':1,'阿里':2,'360':3,'Google':4,'AdGuard':5,'Quad9':6}},
  {name:'回放方式', key:'PLAY_BACK_TYPE', opts:['重新加载url更新进度','播放器seekTo更新进度'], map:{'重新加载url更新进度':0,'播放器seekTo更新进度':1}},
  {name:'时移结束播放设置', key:'TIME_SHIFT_MODE', opts:['时移结束自动刷新继续播放','回到直播'], map:{'时移结束自动刷新继续播放':true,'回到直播':false}},
  {name:'断线重连', key:'RECONNECT_INDEX', opts:['关闭重连','1秒后重连','3秒后重连','5秒后重连','10秒后重连','20秒后重连'], map:{'关闭重连':0,'1秒后重连':1,'3秒后重连':2,'5秒后重连':3,'10秒后重连':4,'20秒后重连':5}},
  {name:'列表类型', key:'GROUP_PARS_SET_SELECT', opts:['传统分组','列表分组','二级分组模式1','二级分组模式2'], map:{'传统分组':0,'列表分组':1,'二级分组模式1':2,'二级分组模式2':3}},
  {name:'EPG展示', key:'EPG_SHOW_TYPE_SELECT', opts:['折叠展示','一直展示'], map:{'折叠展示':0,'一直展示':1}},
  {name:'图标优先级', key:'M3U_LOGO_PRIORITY', opts:['m3u优先','默认优先级'], map:{'m3u优先':true,'默认优先级':false}},
  {name:'图标默认样式', key:'ICON_INITIAL_SET', opts:['显示自定义背景频道首字','显示默认图片'], map:{'显示自定义背景频道首字':true,'显示默认图片':false}},
  {name:'分辨率显示样式', key:'RESOLUTION_MODE_SELECT', opts:['分辨率缩写','分辨率数字x数字'], map:{'分辨率缩写':0,'分辨率数字x数字':1}},
  {name:'导航栏模式', key:'NAVIGATION_SELECT', opts:['滑动显示','一直显示','触摸显示'], map:{'滑动显示':0,'一直显示':1,'触摸显示':2}},
  {name:'主题类型', key:'THEME_SELECT', opts:['0','1','2','3','4','5','6','7','8','9'], map:{'0':0,'1':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9}},
  {name:'开机自启', key:'BOOTRECEIVER_SET_SELECT', opts:['开机获取IP后自启','开机直接自启'], map:{'开机获取IP后自启':true,'开机直接自启':false}},
];

const switchItems = [
  {name:'是否隐藏加载数据文字提示及动画',key:'hideLoginText',v:false},
  {name:'显示时间',key:'LIVE_SHOW_TIME',v:false},
  {name:'显示网速',key:'LIVE_SHOW_NET_SPEED',v:false},
  {name:'列表图标',key:'HIDE_Channel_LOGO',v:true},
  {name:'底部图标',key:'HIDE_Bottom_LOGO',v:true},
  {name:'关闭 EPG',key:'CLOSE_EPG',v:false},
  {name:'关闭收藏',key:'HIDE_FAVOR',v:true},
  {name:'隐藏序号',key:'HIDE_NUMBER',v:true},
  {name:'回放标识',key:'PLAYBACK_ID',v:false},
  {name:'快捷菜单',key:'SHORTCUTS_MENU',v:false},
  {name:'画面锁定',key:'EYE_PROTECTION',v:true},
  {name:'列表 EPG 详情显示',key:'EPG_DESC_SET',v:false},
  {name:'底部 EPG 详情显示',key:'BOTTOM_DESC_SET',v:true},
  {name:'记忆解码',key:'PL_MEMORYS_ET_SELECT',v:false},
  {name:'换台反转',key:'LIVE_CHANNEL_REVERSE',v:false},
  {name:'跨选分类',key:'LIVE_CROSS_GROUP',v:false},
  {name:'关闭密码',key:'LIVE_SKIP_PASSWORD',v:false},
  {name:'后台小窗',key:'PIC_IN_PIC',v:false},
  {name:'快速退出',key:'QUICK_EXIT',v:false},
  {name:'开启时移',key:'TIME_SHIFT_ON',v:true},
  {name:'脚本缓存',key:'SCRIPT_CACHE',v:true},
  {name:'记忆多源',key:'MEMORYS_SOURCE',v:true},
  {name:'记忆进度',key:'MEMORYS_POSITION',v:true},
  {name:'遍历多源',key:'PLAY_ALL_SOURCE',v:true},
  {name:'本地视频',key:'ENABLE_LOCAL_VIDEO',v:false},
  {name:'隧道模式',key:'EXO_TUNNELING_SELECT',v:false},
  {name:'开机启动',key:'BOOT_START',v:false},
  {name:'图标缓存',key:'IMAGECACHE_SELECT',v:false},
];

/* ============= 数据存储 ============= */

let ku9 = {
  addrData5: {},
  addrData3: {},
  addrData4: {},
  selectVals: {},
  switches: {},
  appName: '',
  profiles: ['配置方案1', '配置方案2', '配置方案3'],
  currentProfile: '配置方案1',
  lastBuildTime: ''
};

function initKu9Data() {
  // 先渲染空白界面，再从后端加载
  renderAll();
  fetchKu9Config();
}

// 表1/表2：地址填写 — 手写实现

/* ============= API 调用 ============= */

async function fetchKu9Config() {
  try {
    const res = await fetch('/api/ku9/config');
    if (!res.ok) return;
    const data = await res.json();
    
    if (data.addrData5) ku9.addrData5 = data.addrData5;
    if (data.addrData3) ku9.addrData3 = data.addrData3;
    if (data.addrData4) ku9.addrData4 = data.addrData4;
    if (data.selectVals) ku9.selectVals = data.selectVals;
    if (data.switches) ku9.switches = data.switches;
    if (data.appName) ku9.appName = data.appName;
    if (data.profiles) ku9.profiles = data.profiles;
    if (data.currentProfile) ku9.currentProfile = data.currentProfile;
    if (data.lastBuildTime) ku9.lastBuildTime = data.lastBuildTime;
    renderAll();
  } catch(e) {}
}

async function saveKu9Config() {
  // 保存前清理空条目
  cleanEmptyAddrEntries();
  // 收集 select 当前值（用 key 对应 JSON 字段）
  const selects = document.querySelectorAll('#selectTable .config-select');
  const selectVals = {};
  selectItems.forEach((item, i) => {
    selectVals[item.key] = selects[i] ? selects[i].value : '';
  });
  // 收集 switch 当前值（用 key 对应 JSON 字段）
  const switches = {};
  switchItems.forEach((item) => {
    switches[item.key] = item.v;
  });
  // 收集 appName
  const appNameEl = document.getElementById('ku9AppName');
  const appName = appNameEl ? appNameEl.value : '';
  
  try {
    await fetch('/api/ku9/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        addrData5: ku9.addrData5,
        addrData3: ku9.addrData3,
        addrData4: ku9.addrData4,
        selectVals,
        switches,
        appName,
        profiles: ku9.profiles,
        currentProfile: ku9.currentProfile
      })
    });
  } catch(e) {}
}

/* ============= 渲染函数 ============= */

function renderAll() {
  renderAddrTable5();
  renderAddrTable3();
  renderAddrTable4();
  renderSelectTable();
  renderSwitchTable();
  updateProfileBar();
  updateLastBuild();
  updateAppName();
  setTimeout(ku9RefreshUploadStatus, 100);
  ku9RefreshDownloads();
}

function updateAppName() {
  const el = document.getElementById('ku9AppName');
  if (el) el.value = ku9.appName;
}

function updateProfileBar() {
  const sel = document.querySelector('#panel-ku9apps .profile-bar select');
  if (!sel) return;
  sel.innerHTML = '';
  ku9.profiles.forEach(p => {
    let op = document.createElement('option');
    op.textContent = p;
    op.value = p;
    if (p === ku9.currentProfile) op.selected = true;
    sel.appendChild(op);
  });
}

function updateLastBuild() {
  const el = document.getElementById('ku9LastBuild');
  if (el) el.textContent = ku9.lastBuildTime ? '上一步: ' + ku9.lastBuildTime : '';
}

// 清理 addrTable5 中数据列为空的条目
function cleanEmptyAddrEntries() {
  addrItems5.forEach(item => {
    const arr = ku9.addrData5[item.name];
    if (!arr) return;
    ku9.addrData5[item.name] = arr.filter(e => e.val && e.val.trim());
  });
}

// ===== addrTable5（表1）：地址填写 =====
function renderAddrTable5() {
  const tbody = document.querySelector('#addrTable5 tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  addrItems5.forEach(item => {
    const tr = document.createElement('tr');
    
    // 名称列
    const tdName = document.createElement('td');
    tdName.textContent = item.name;
    
    // 数据列
    const tdVal = document.createElement('td');
    tdVal.appendChild(build5Entries(item.name, 'val'));
    tdVal.appendChild(build5NewInput(item.name, 'val'));
    
    // 自定义名称列
    const tdCus = document.createElement('td');
    tdCus.appendChild(build5Entries(item.name, 'name'));
    tdCus.appendChild(build5NewInput(item.name, 'name'));
    
    tr.appendChild(tdName);
    tr.appendChild(tdVal);
    tr.appendChild(tdCus);
    tbody.appendChild(tr);
  });
}

// 构建已有条目列表
function build5Entries(key, field) {
  const div = document.createElement('div');
  div.className = 'addr-entries';
  const arr = ku9.addrData5[key] || [];
  arr.forEach((entry, idx) => {
    const row = document.createElement('div');
    row.className = 'addr-entry';
    
    // 自定义名称列若数据列为空则不显示
    if (field === 'name' && !entry.val) return;
    
    const span = document.createElement('span');
    span.className = 'saved-text';
    const txt = entry[field] || '';
    span.textContent = txt || '（空）';
    if (!txt) span.style.color = '#ccc';
    
    // 点击文本 → 编辑
    span.addEventListener('click', () => {
      const inp = document.createElement('input');
      inp.className = 'config-input new-input';
      inp.value = entry[field] || '';
      inp.style.width = '100%';
      inp.style.maxWidth = '100%';
      
      const save = () => {
        const v = inp.value.trim();
        entry[field] = v;
        // 数据列空则删除整条
        if (!entry.val) {
          ku9.addrData5[key].splice(idx, 1);
        }
        renderAddrTable5();
        saveKu9Config();
      };
      inp.addEventListener('blur', save);
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
      
      span.replaceWith(inp);
      inp.focus();
      inp.select();
    });
    
    row.appendChild(span);
    
    // 只在自定义名称列显示删除按钮
    if (field === 'name') {
      const spacer = document.createElement('span');
      spacer.style.flex = '1';
      row.appendChild(spacer);
      
      const del = document.createElement('span');
      del.className = 'del-btn';
      del.textContent = '✕';
      del.title = '删除此条';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        ku9.addrData5[key].splice(idx, 1);
        renderAddrTable5();
        saveKu9Config();
      });
      row.appendChild(del);
    }
    
    div.appendChild(row);
  });
  return div;
}

// 构建新增输入框
function build5NewInput(key, field) {
  const inp = document.createElement('input');
  inp.className = 'config-input new-input';
  inp.style.maxWidth = '100%';
  inp.placeholder = field === 'val' ? '输入数据...' : '名称...';
  inp.dataset.addrKey = key;
  inp.dataset.field = field;
  
  inp.addEventListener('blur', function() {
    const k = this.dataset.addrKey;
    const f = this.dataset.field;
    const v = this.value.trim();
    if (!v) return;
    
    // 自定义名称列：填入同组最后一条数据项的名称
    if (f === 'name') {
      const arr = ku9.addrData5[k] || [];
      for (let i = arr.length - 1; i >= 0; i--) {
        if (arr[i].val && !arr[i].name) {
          arr[i].name = v;
          renderAddrTable5();
          saveKu9Config();
          return;
        }
      }
      return;
    }
    
    const arr = ku9.addrData5[k] || [];
    if (arr.length === 0) {
      ku9.addrData5[k] = [{ val: v, name: '' }];
    } else {
      const last = arr[arr.length - 1];
      if (last.val === '' && last.name === '') {
        last.val = v;
      } else {
        arr.push({ val: '', name: '' });
        arr[arr.length - 1].val = v;
      }
    }
    renderAddrTable5();
    saveKu9Config();
  });
  
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
  return inp;
}

// ===== addrTable3（表2）：简单输入 =====
function renderAddrTable3() {
  const tbody = document.querySelector('#addrTable3 tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  addrItems3.forEach(item => {
    const tr = document.createElement('tr');
    
    const tdName = document.createElement('td');
    tdName.textContent = item.name;
    
    const tdVal = document.createElement('td');
    const inp = document.createElement('input');
    inp.className = 'config-input';
    inp.placeholder = '输入...';
    inp.style.width = '100%';
    inp.value = ku9.addrData3[item.name] || '';
    inp.addEventListener('input', function() {
      ku9.addrData3[item.name] = this.value;
    });
    inp.addEventListener('blur', function() {
      saveKu9Config();
    });
    tdVal.appendChild(inp);
    
    tr.appendChild(tdName);
    tr.appendChild(tdVal);
    tbody.appendChild(tr);
  });
}

// ===== addrTable4（表3）：快捷菜单勾选 =====
function renderAddrTable4() {
  const tbody = document.querySelector('#addrTable4 tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  
  // 左侧固定标签行（跨行）
  shortcutRows.forEach((row, rowIdx) => {
    const tr = document.createElement('tr');
    
    const tdLabel = document.createElement('td');
    tdLabel.className = 'addr4-label';
    if (rowIdx === 0) {
      tdLabel.textContent = '快捷菜单默认选项';
      tdLabel.rowSpan = shortcutRows.length;
    }
    if (rowIdx === 0) {
      tr.appendChild(tdLabel);
    }
    
    const tdChecks = document.createElement('td');
    tdChecks.className = 'addr4-checks';
    const rowDiv = document.createElement('div');
    rowDiv.className = 'addr4-row';
    row.forEach(item => {
      const label = document.createElement('label');
      label.className = 'addr4-checkbox';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = ku9.addrData4[item] !== false;
      cb.addEventListener('change', function() {
        ku9.addrData4[item] = this.checked;
        saveKu9Config();
      });
      const span = document.createElement('span');
      span.textContent = item;
      label.appendChild(cb);
      label.appendChild(span);
      rowDiv.appendChild(label);
    });
    tdChecks.appendChild(rowDiv);
    tr.appendChild(tdChecks);
    tbody.appendChild(tr);
  });
}

function renderSelectTable() {
  const tbody = document.querySelector('#selectTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  selectItems.forEach(item => {
    const tr = document.createElement('tr');
    const tdN = document.createElement('td');
    tdN.textContent = item.name;
    const tdO = document.createElement('td');
    tdO.style.textAlign = 'center';
    const sel = document.createElement('select');
    sel.className = 'config-select';
    const saved = ku9.selectVals[item.key];
    item.opts.forEach(o => {
      let op = document.createElement('option');
      op.textContent = o;
      op.value = o;
      if (o === saved) op.selected = true;
      sel.appendChild(op);
    });
    sel.addEventListener('focus', () => tr.classList.add('select-highlight'));
    sel.addEventListener('blur', () => {
      tr.classList.remove('select-highlight');
      saveKu9Config();
    });
    tdO.appendChild(sel);
    tr.appendChild(tdN);
    tr.appendChild(tdO);
    tbody.appendChild(tr);
  });
}

function renderSwitchTable() {
  const tbody = document.querySelector('#switchTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  switchItems.forEach(item => {
    // 从 ku9.switches 读取保存的开关状态
    const saved = ku9.switches[item.key];
    if (saved !== undefined) {
      item.v = saved;
    }
    
    const tr = document.createElement('tr');
    const tdN = document.createElement('td');
    tdN.textContent = item.name;
    const tdS = document.createElement('td');
    tdS.style.textAlign = 'center';
    const btn = document.createElement('span');
    btn.className = 'switch-btn ' + (item.v ? 'on' : 'off');
    btn.textContent = item.v ? '● 启用' : '○ 禁用';
    btn.addEventListener('click', () => {
      item.v = !item.v;
      if (item.v) {
        btn.classList.remove('off');
        btn.classList.add('on');
        btn.textContent = '● 启用';
      } else {
        btn.classList.remove('on');
        btn.classList.add('off');
        btn.textContent = '○ 禁用';
      }
      saveKu9Config();
    });
    tdS.appendChild(btn);
    tr.appendChild(tdN);
    tr.appendChild(tdS);
    tbody.appendChild(tr);
  });
}

/* ============= 操作函数 ============= */

function ku9NewProfile() {
  const name = prompt('请输入新配置方案名称：');
  if (!name || !name.trim()) return;
  if (ku9.profiles.includes(name.trim())) {
    alert('该名称已存在');
    return;
  }
  ku9.profiles.push(name.trim());
  ku9.currentProfile = name.trim();
  updateProfileBar();
  saveKu9Config();
}

function ku9RenameProfile() {
  const newName = prompt('请输入新名称：', ku9.currentProfile);
  if (!newName || !newName.trim()) return;
  const idx = ku9.profiles.indexOf(ku9.currentProfile);
  if (idx >= 0) ku9.profiles[idx] = newName.trim();
  ku9.currentProfile = newName.trim();
  updateProfileBar();
  saveKu9Config();
}

function ku9DeleteProfile() {
  if (ku9.profiles.length <= 1) {
    alert('至少保留一个配置方案');
    return;
  }
  if (!confirm(`确定删除「${ku9.currentProfile}」？`)) return;
  const idx = ku9.profiles.indexOf(ku9.currentProfile);
  ku9.profiles.splice(idx, 1);
  ku9.currentProfile = ku9.profiles[0];
  updateProfileBar();
  saveKu9Config();
}

function ku9BuildApk() {
  const btn = document.querySelector('#panel-ku9apps .build-bar .btn-success');
  btn.disabled = true;
  btn.textContent = '⏳ 构建中...';
  
  // 收集当前配置
  const selects = document.querySelectorAll('#selectTable .config-select');
  const selectVals = {};
  selectItems.forEach((item, i) => {
    selectVals[item.key] = selects[i] ? selects[i].value : '';
  });
  
  const switches = {};
  switchItems.forEach((item, i) => {
    switches[item.key] = item.v;
  });

  const progressFill = document.getElementById('ku9Progress');
  const progressText = document.getElementById('ku9ProgressText');
  const progressHint = document.getElementById('ku9ProgressHint');

  if (progressHint) progressHint.style.display = 'none';

  // 模拟进度（卡在 90% 是因为后端正在编译 APK）
  let pct = 0;
  const iv = setInterval(() => {
    pct += Math.floor(Math.random() * 8) + 2;
    if (pct > 90) pct = 90;
    if (pct >= 85 && progressHint) progressHint.style.display = 'inline';
    if (progressFill) progressFill.style.width = pct + '%';
    if (progressText) progressText.textContent = pct + '%';
  }, 500);

  // 构建前先把当前配置落盘，确保后端 read_user_config() 读到最新配置（Bug 1/3 修复）
  saveKu9Config().then(() => {
  fetch('/api/ku9/build', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      profile: ku9.currentProfile,
      addrData5: ku9.addrData5,
      addrData3: ku9.addrData3,
      selectVals,
      switches,
      appName: ku9.appName
    })
  }).then(res => res.json()).then(data => {
    clearInterval(iv);
    if (progressHint) progressHint.style.display = 'none';
    if (progressFill) progressFill.style.width = '100%';
    if (progressText) progressText.textContent = '100%';
    setTimeout(() => {
      if (progressFill) progressFill.style.width = '0%';
      if (progressText) progressText.textContent = '0%';
    }, 2000);
    ku9.lastBuildTime = new Date().toLocaleString('zh-CN');
    updateLastBuild();
    if (data.file) {
      alert('APK 构建完成！');
      ku9RefreshDownloads();
    } else if (data.error) {
      alert('构建失败：' + data.error);
    }
  }).catch(e => {
    clearInterval(iv);
    if (progressHint) progressHint.style.display = 'none';
    if (progressFill) progressFill.style.width = '0%';
    if (progressText) progressText.textContent = '0%';
    alert('构建请求失败：' + e.message);
  }).finally(() => {
    btn.disabled = false;
    btn.textContent = '📦 生成APK';
  });
  });
}

async function ku9RefreshDownloads() {
  const tbody = document.getElementById('ku9DownloadBody');
  if (!tbody) return;
  try {
    const res = await fetch('/api/ku9/downloads');
    if (!res.ok) throw Error('请求失败');
    const list = await res.json();
    tbody.innerHTML = '';
    if (!list || list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);padding:20px">暂无生成记录</td></tr>';
      return;
    }
    list.forEach((item, idx) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="text-align:center">${idx + 1}</td>
        <td>${item.filename}</td>
        <td>${item.time}</td>
        <td>${item.size}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm btn-primary" onclick="ku9DownloadApk('${item.id}')">⬇️ 下载</button>
          <button class="btn btn-sm btn-danger" onclick="ku9DeleteApk('${item.id}')">🗑️ 删除</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);padding:20px">加载失败</td></tr>';
  }
}

async function ku9DownloadApk(id) {
  window.open('/api/ku9/download/?file=' + encodeURIComponent(id), '_blank');
}

async function ku9DeleteApk(id) {
  if (!confirm('确定删除此文件？')) return;
  try {
    const res = await fetch('/api/ku9/download/?file=' + encodeURIComponent(id), {method: 'DELETE'});
    if (res.ok) ku9RefreshDownloads();
  } catch(e) {}
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

/* ============= 文件上传 ============= */

function ku9UploadIcon() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'image/png,image/jpeg,image/webp';
  inp.addEventListener('change', async function() {
    const file = this.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch('/api/ku9/upload/icon', { method: 'POST', body: form });
      if (res.ok) {
        alert('图标上传成功，下次构建 APK 时生效');
        ku9RefreshUploadStatus();
      } else {
        alert('图标上传失败');
      }
    } catch(e) {
      alert('上传出错: ' + e.message);
    }
  });
  inp.click();
}

function ku9UploadBg() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = 'image/png,image/jpeg,image/webp';
  inp.addEventListener('change', async function() {
    const file = this.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch('/api/ku9/upload/bg', { method: 'POST', body: form });
      if (res.ok) {
        alert('背景上传成功，下次构建 APK 时生效');
        ku9RefreshUploadStatus();
      } else {
        alert('背景上传失败');
      }
    } catch(e) {
      alert('上传出错: ' + e.message);
    }
  });
  inp.click();
}

async function ku9RefreshUploadStatus() {
  try {
    const res = await fetch('/api/ku9/upload/status');
    if (!res.ok) return;
    const data = await res.json();
    
    // 图标预览
    const iconPreview = document.querySelector('.preview-ku9-icon');
    const iconName = document.querySelector('.preview-ku9-icon-name');
    if (iconPreview) {
      if (data.hasIcon) {
        iconPreview.innerHTML = `<img src="/api/ku9/preview/icon?t=${Date.now()}" class="preview-img" onclick="ku9PreviewImage(this.src)">`;
      } else {
        iconPreview.innerHTML = '';
      }
    }
    if (iconName) iconName.textContent = data.hasIcon ? (data.iconName || 'app_icon.png') : '';
    
    // 背景预览
    const bgPreview = document.querySelector('.preview-ku9-bg');
    const bgName = document.querySelector('.preview-ku9-bg-name');
    if (bgPreview) {
      if (data.hasBg) {
        bgPreview.innerHTML = `<img src="/api/ku9/preview/bg?t=${Date.now()}" class="preview-img" onclick="ku9PreviewImage(this.src)">`;
      } else {
        bgPreview.innerHTML = '';
      }
    }
    if (bgName) bgName.textContent = data.hasBg ? (data.bgName || 'app_bg.png') : '';
  } catch(e) {}
}

/* ============= 图片预览 ============= */

function ku9PreviewImage(src) {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:pointer';
  overlay.addEventListener('click', () => overlay.remove());
  const img = document.createElement('img');
  img.src = src;
  img.style.cssText = 'max-width:80vw;max-height:80vh;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.4)';
  overlay.appendChild(img);
  document.body.appendChild(overlay);
}

/* ============= 子标签事件代理（独立于 ensureKu9Init，永不被时序影响） ============= */
// 使用事件代理监听子标签点击，不依赖 ensureKu9Init 是否能正确绑定
// 即使子标签的 click 事件监听器没绑上，事件冒泡到父容器也能响应
document.addEventListener('click', function(e) {
  // 查找点击的 .sub-tab（可能在 panel-ku9apps 内部）
  let target = e.target;
  while (target && target !== document.body) {
    if (target.classList && target.classList.contains('sub-tab') && target.closest('#panel-ku9apps')) {
      const t = target;
      document.querySelectorAll('#panel-ku9apps .sub-tab').forEach(a => a.classList.remove('active'));
      t.classList.add('active');
      document.querySelectorAll('#panel-ku9apps .sub-panel').forEach(p => p.classList.remove('active'));
      const el = document.getElementById('sub-' + t.dataset.sub);
      if (el) el.classList.add('active');
      localStorage.setItem('ku9ActiveSubTab', t.dataset.sub);
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    target = target.parentElement;
  }
});

/* ============= 面板初始化 ============= */

function ensureKu9Init() {
  const panel = document.getElementById('panel-ku9apps');
  if (!panel) return;
  const already = panel.dataset.ku9Inited;
  // 数据/渲染：打开网页（DOMContentLoaded）即可初始化，不再等面板 active；
  // 已初始化过则不重复拉取数据。
  if (!already) {
    panel.dataset.ku9Inited = '1';
    initKu9Data();
  }

  // 事件绑定只在首次时执行（保证不重复绑定）
  if (already) return;

  // 上传状态
  setTimeout(ku9RefreshUploadStatus, 500);
  
  // 应用名称输入框
  const appNameEl = document.getElementById('ku9AppName');
  if (appNameEl) {
    appNameEl.addEventListener('blur', function() {
      ku9.appName = this.value;
      saveKu9Config();
    });
  }

  // sub-tab 切换
  document.querySelectorAll('#panel-ku9apps .sub-tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('#panel-ku9apps .sub-tab').forEach(a => a.classList.remove('active'));
      t.classList.add('active');
      document.querySelectorAll('#panel-ku9apps .sub-panel').forEach(p => p.classList.remove('active'));
      const el = document.getElementById('sub-' + t.dataset.sub);
      if (el) el.classList.add('active');
      localStorage.setItem('ku9ActiveSubTab', t.dataset.sub);
    });
  });

  // 恢复上次的子标签
  const savedSub = localStorage.getItem('ku9ActiveSubTab');
  if (savedSub) {
    const targetTab = document.querySelector('#panel-ku9apps .sub-tab[data-sub="' + savedSub + '"]');
    const targetPanel = document.getElementById('sub-' + savedSub);
    if (targetTab && targetPanel) {
      document.querySelectorAll('#panel-ku9apps .sub-tab').forEach(a => a.classList.remove('active'));
      targetTab.classList.add('active');
      document.querySelectorAll('#panel-ku9apps .sub-panel').forEach(p => p.classList.remove('active'));
      targetPanel.classList.add('active');
    }
  }

  // 配置方案切换
  const profileSelect = document.querySelector('#panel-ku9apps .profile-bar select');
  if (profileSelect) {
    profileSelect.addEventListener('change', function() {
      ku9.currentProfile = this.value;
      fetchKu9Config();
    });
  }
}

// 双保险：MutationObserver + 直接监听 tab 点击
document.addEventListener('DOMContentLoaded', function() {
  // 方式1：MutationObserver 监听 panel active 变化
  const observer = new MutationObserver(() => {
    ensureKu9Init();
  });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  
  // 方式2：直接监听所有 tab 点击
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // 等待原始 tab 切换代码执行完后（微任务），再检查
      setTimeout(ensureKu9Init, 0);
    });
  });
  
  // 方式3：如果页面加载时 ku9apps 已经是 active（比如刷新时保留了 localStorage），立即初始化
  setTimeout(ensureKu9Init, 100);
});

// 图示记录（来自APK assets）
