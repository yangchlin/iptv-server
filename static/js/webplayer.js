/**
 * webplayer.js - 仪表盘「电脑播放」卡片初始化
 * 独立文件，不改 app.js 主逻辑。
 * 功能：点击卡片下载 Windows 版播放器 EXE
 */

// ---------- 电脑播放 (下载 Windows EXE) ----------
// 仪表盘「电脑播放」卡片点击 → 下载 Windows 播放器绿色 EXE
function downloadExe() {
  const a = document.createElement('a');
  // 指向镜像内 static/不要动/IPTV.exe（文件已打进 Docker 镜像，普通操作删不到）
  a.href = '/static/不要动/IPTV.exe';
  a.download = 'IPTV.exe';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  if (typeof showToast === 'function') showToast('开始下载 IPTV.exe');
  else alert('开始下载 IPTV.exe');
}
