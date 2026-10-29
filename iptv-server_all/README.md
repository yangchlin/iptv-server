# 📺 IPTV EPG 智能管理系统

基于 FastAPI 的 Docker 化 EPG 电视节目管理系统。
**同一套文件可在群晖 DSM、飞牛 fnOS、威联通、任意 Linux 上直接跑**，不需要改任何路径。

---

## 🚀 快速开始（通用，3 步）

```bash
# 1. 把整个 iptv-server 目录放到目标机器（含 app/ static/ Dockerfile ...）
cd /path/to/iptv-server

# 2. 可选：改端口 / 密码 / 数据目录
cp .env.example .env

# 3. 一键构建 + 启动
bash build.sh
```

启动后访问 `http://<机器IP>:5673`，默认账号 `666 / 666`。

`build.sh` 会自动完成：检查 Docker → 生成 `.env` → 统一 LF 换行 → 构建镜像 → 启动容器 → 等健康检查通过 → 打印访问地址。

不想用脚本也可以：

```bash
docker compose up -d --build
docker compose logs -f
```

---

## 🧭 使用流程

> 首次配置一次，之后主要靠定时任务自动跑。所有配置都存在 `data/config/app_config.json`，页面只是编辑器。

### ① 登录并改密码
访问 `http://<机器IP>:5673` → 默认 `666 / 666` → 「👤 账户密码」改掉。

### ② 选择电信平台，再配置

> **四川各地市的电信平台不一样**（有的地市是华为，有的是中兴），同一套参数不能混用。
> 「📡 电信EPG」页顶部先选 **🟦 华为平台 / 🟧 中兴平台**，选好后再填参数。
> 切换平台时会弹窗警告（因为配置过一次后，切错城市会导致频道地址/节目单对不上），确认后才会切换。

#### ②-A 华为平台

「📡 电信EPG」→「⚙️ 认证与输出配置」填 `UserID / mac / STBID / key / Authenticator` → **保存**
→ 「测试连接」→「执行认证」→「更新频道列表」

> 保存后写入 `app_config.json` 的 `telecom` 段；之后所有认证/取频道/下载 EPG 都只读这个文件，页面参数不再参与。

**`key` 和 `Authenticator` 的关系**（`app/telecom_epg.py` 的 `telecom_auth`）：

| 配置 | 认证时用的 Authenticator | 说明 |
|---|---|---|
| 填了 `key` | 每次现场用 3DES 把 `key$EncryptToken$UserID$STBID$ip$mac$$CTC` 加密出来 | 推荐：跟着会话的 EncryptToken 变，不会过期 |
| `key` 留空 | 直接用配置里那串静态 `Authenticator` | 兼容模式：抓包得到的值，可能过期或绑设备 |
| 勾选「必须用 key 生成 Authenticator」 | —— | `key` 为空时**直接拒绝认证**，不再回退到静态值 |
| 两个都空 | —— | 无密文可反推，认证前就会报「缺少必要配置项」 |

**忘了 key 怎么办：配置弹窗里点「🔍 反推」**（`POST /api/telecom/find-key`）

- 原理：`Authenticator` 的明文是 `{key}${EncryptToken}${UserID}${STBID}${ip}${mac}$$CTC`，**前 8 字节就是 key 本身**，所以拿现有 Authenticator 穷举 `00000000–99999999` 逐个解密、比对前缀即可。
- 前提：配置里至少要有一条 `Authenticator`（抓包值）。
- 过程：后台多进程跑（Docker/Linux 吃多核，最多 4 进程；Windows 退回单进程），进度写进「电信EPG」页的执行日志，弹窗里也实时显示「已试 N / 1 亿」。
- 结果：找到后**自动写回 `key`**，并立刻用新 key 认证一次、把现场生成的 `Authenticator` 也写回配置；找不到会说清楚原因。
- 耗时参考：单进程约 5 秒/10 万次，4 进程大约 2–10 分钟跑完 1 亿；中途可以再点一次按钮停止。

> 注意：服务器只校验最终提交的 `Authenticator`，不校验 `key`（`key` 从不上传）。所以「没填 key 也能认证成功」意味着那串静态 `Authenticator` 在你这个平台上仍然有效，不是绕过认证。想强制走 key，就在配置弹窗勾上那个勾选框。

#### ②-B 中兴平台

「📡 电信EPG」→ 平台选 **🟧 中兴平台** →「⚙️ 认证与输出配置」填：

| 字段 | 说明 |
|---|---|
| IPTVServer 地址 | 抓包得到的中兴 EPG 服务器，例如 `http://182.137.9.86:8080` |
| UserID / STBID / StbIP | 机顶盒账号 / 设备号 / 机顶盒 IP |
| Authenticator | 抓包得到的一长串十六进制；**中兴不能用 key 现场生成**，换账号要重抓 |
| UserGroupNMB / EPGGroupNMB / LastTermno / stbinfo | 抓包值，一般 `55` / 空 / `0` / `undefined` |
| live_proxy | 组播转单播地址（udpxy / msd_lite），例如 `http://192.168.100.1:4022`，生成 `…/udp/239.x.x.x:5140` |
| replay_proxy | 回看地址，填本程序 RTSP 代理，例如 `rtsp://192.168.100.1:554` |
| 回看天数 | 1–10 天（默认 8 天：前 6 天 + 今天 + 明天，与原始脚本一致） |

保存后：「测试连接」→「执行认证」→「更新频道列表」→「更新EPG信息」。
**中兴的回看必须经过 RTSP 代理**：在同一个弹窗下半部分勾选「启用回看代理」、填真实 RTSP 服务器
（抓包得到的 CDN 地址，如 `218.89.44.4:554`）→ 保存即自动启动；也可在页面「🎧 回看代理」一行手动启停。

> 中兴数据全部独立存放：配置 `telecom_zte` 段、频道 `zte_channels.json`、节目单 `data/json/<频道名>_zte_<日期>.json`、
> 专用 XML `data/xml/epg_zte.xml`、播放列表 `zte.m3u`。切回华为不会覆盖，也不会混用。

### ③ 配置外部 EPG 源
「🌐 外部EPG」→「➕ 添加源」，推荐这两个（内容相同、覆盖不同）：

| 源 | 地址 |
|---|---|
| 51zmt（老张 EPG 镜像） | `https://m3u.ibert.me/epg/51zmt.xml` |
| 112114 | `https://m3u.ibert.me/epg/112114_xyz.xml` |

→ 点「⬇️ 下载EPG」，等两个源都显示「已下载」（红标「未下载」消失）。

### ④ 把外部频道加进 EPG 管理
「🌐 外部EPG」→「📥 频道提取」→「📋 打开频道表」
→ 搜索/筛选/只看未添加 → 勾选 → 「➕ 添加选中」
→ 切到「📄 已添加记录」可改 tvg-id、或点 ✏️ 选「并入目标」把外部节目单并到电信频道

> 电信频道拉取后会自动并入 EPG 管理，不需要手动添加。

### ⑤ 生成 XML
「📊 仪表盘」→「📄 生成XML」（或等定时任务）
产物：`data/xml/epg.xml`（+`.gz`，全部频道，按当前平台优先取电信节目）、平台专用 `epg_telecom.xml` / `epg_zte.xml`
播放器 EPG 地址填：`http://<IP>:5673/epg.xml`

### ⑥ 整理播放列表
「📋 频道管理」上传 M3U/TXT →「🔀 合并文件」→ 勾选 **保持第一个文件顺序**（把电信文件放第一个）
→ 生成合并文件 → 打开编辑器给频道「匹配节目单」（下拉来自合并后的 `epg.xml`）

### ⑦ 核对地址
「📇 频道总览」选「电信频道」或某个 M3U 文件，核对**直播 / 回看 / 台标 / TVG-NAME**，可单条复制、导出 CSV。

### ⑧ 设置定时任务
| 任务 | 位置 |
|---|---|
| 外部 EPG 每日提取 | 仪表盘 →「⏰ 外部 EPG 定时任务」 |
| 电信频道刷新 / 电信 EPG | 电信EPG →「⏰ 定时任务」 |

改完点「💾 保存」即写回 `app_config.json` 并重新排程。

---

## 🐧 群晖 DSM 部署

1. **放文件**：File Station 里新建 `/volume1/docker/iptv`，把本项目**整个目录**（包括 `app/`、`static/`、`Dockerfile`、`docker-compose.yml`）传进去。
2. **开 SSH**：控制面板 → 终端机 → 启用 SSH。
3. **登录并提权**：
   ```bash
   ssh 你的账号@NAS_IP
   sudo -i
   cd /volume1/docker/iptv
   ```
4. **（可选）改配置**：
   ```bash
   cp .env.example .env
   vi .env      # 改 WEB_PORT / AUTH_PASSWORD / DATA_ROOT
   ```
   想沿用群晖固定路径就设 `DATA_ROOT=/volume1/docker/iptv/data`；不设则数据落在项目目录下的 `./data`。
5. **构建启动**：
   ```bash
   bash build.sh
   ```
6. **确认状态**：Container Manager → 容器，能看到 `iptv-server`，状态 `healthy`。
7. **放行端口**：控制面板 → 安全性 → 防火墙，允许 TCP `5673`（或你改的端口）。

> 也可以用 Container Manager 的「项目」功能：新增 → 选择本目录的 `docker-compose.yml` → 构建。
> 相对路径是以**项目文件夹**为基准的，所以放在哪个卷都能用。

---

## 🐮 飞牛 fnOS / 威联通 / 普通 Linux / macOS

步骤完全一样，只是路径不同：

```bash
cd /vol1/1000/Docker/IPTV   # 飞牛示例
cp .env.example .env
bash build.sh
```

- 威联通：Container Station → 应用程序 → 创建，指向本目录的 compose 文件；或 SSH 里执行 `bash build.sh`。
- 普通 Linux：装好 docker + compose 插件后直接 `bash build.sh`。

---

## ⚙️ 环境变量（`.env`）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WEB_PORT` | `5673` | 宿主机端口，容器内固定 80 |
| `TZ` | `Asia/Shanghai` | 容器时区，影响日志与定时任务 |
| `AUTH_USERNAME` | `666` | 首次启动的登录账号 |
| `AUTH_PASSWORD` | `666` | 首次启动的登录密码 |
| `DATA_ROOT` | `./data` | 数据根目录（可写绝对路径） |
| `SHARE_DIR` | `./data/share` | epg.xml 额外备份目录 |
| `CONTAINER_NAME` | `iptv-server` | 容器名 |
| `APT_MIRROR` | `mirrors.aliyun.com` | 构建时的 Debian 源 |
| `PIP_INDEX_URL` | 清华源 | 构建时的 PyPI 源 |
| `PIP_TRUSTED_HOST` | `pypi.tuna.tsinghua.edu.cn` | 与上面配套；用官方源时留空 |
| `DNS1` / `DNS2` | `223.5.5.5` / `119.29.29.29` | 容器 DNS |

> ⚠️ 凭据只在**首次启动**时写入 `<DATA_ROOT>/config/auth.json`。之后再改 `.env` 里的密码不会生效——请用页面「账户密码」修改，或删除 `auth.json` 后重启。

---

## 📂 数据目录

默认随 compose 文件走（`./data`），也可以指向任意绝对路径：

```
<DATA_ROOT>/
├── json/      # 节目数据：外部 EPG <频道名>_<日期>.json
│              #          华为电信 <频道名>_telecom_<日期>.json
│              #          中兴电信 <频道名>_zte_<日期>.json
├── xml/       # epg.xml(.gz) 全部频道 / epg_telecom.xml(.gz) 华为 / epg_zte.xml(.gz) 中兴
├── m3u/       # M3U 播放列表：telecom.m3u（华为）/ zte.m3u（中兴）
│              # sctv.txt / sctv.m3u 是「当前平台」别名，切换平台后按新平台重建
├── icons/     # 台标
├── logs/      # 运行日志（保留 7 天）
├── tools/     # 外部工具：仪表盘「电脑播放」下载的 IPTV.exe 放这里（换版本不用重建镜像）
├── config/    # app_config.json（唯一配置文件）
│              # telecom_channels.json（华为频道）/ zte_channels.json（中兴频道）等
└── share/     # 共享目录（设置项 share_xml_path，默认 /app/data/share）
```

### 统一配置文件 `data/config/app_config.json`

**所有可配置项都在这一个文件里**，前端页面只是编辑器：

```json
{
  "config_version": 2,
  "telecom": {
    "platform": "huawei",            // ★ 当前生效的电信平台：huawei / zte
    "enabled": true, "AuthenticationIP": "...", "UserID": "...", "mac": "...",
    "Authenticator": "...", "RTP2HTTPD": "..."
  },
  "telecom_zte": {
    "enabled": false,
    "server": "http://182.137.9.86:8080",
    "UserID": "...", "STBID": "...", "StbIP": "...", "Authenticator": "...",
    "live_proxy": "http://192.168.100.1:4022",
    "replay_proxy": "rtsp://192.168.100.1:554",
    "rtsp_proxy_enabled": false, "rtsp_proxy_port": 554,
    "rtsp_upstream": "", "rtsp_upstream_port": 554
  },
  "cron_time": "11:00",              // 外部 EPG 定时
  "cron_enabled": true,
  "telecom_channels_cron_enabled": false,
  "telecom_channels_cron_time": "02:00",
  "telecom_epg_cron_enabled": false,
  "telecom_epg_cron_time": "03:00",
  "share_xml_path": "/app/data/share",
  "xml_gz_enabled": true,
  "epg_sources": [
    { "url": "https://m3u.ibert.me/epg/51zmt.xml", "rule": "auto", "enabled": true, "remark": "51zmt EPG" },
    { "url": "https://m3u.ibert.me/epg/112114_xyz.xml", "rule": "auto", "enabled": true, "remark": "112114 EPG" }
  ]
}
```

- **`telecom.platform` 决定当前用哪家的数据**：`huawei` 或 `zte`。
  - 页面上切换平台走 `POST /api/telecom/platform`，当前平台已配置过/有频道缓存时会先返回警告，必须 `confirm=true` 才真正切换
  - 两家的配置、频道缓存、EPG JSON、专用 XML、m3u 全部各自独立，切换不会删数据，也不会互相覆盖
  - 切换后会在后台按新平台重建 `sctv.txt / sctv.m3u / epg.xml / epg_<平台>.xml`；新平台没有频道时，两个 sctv 别名文件会被清掉，避免对外继续提供上一个平台的频道
- **`telecom.enabled` / `telecom_zte.enabled` 只是定时任务开关**：只有 `scheduler` 里的「定时刷新频道 / 定时下载 EPG」会检查对应平台的 `enabled`；页面上的「执行认证 / 获取频道 / 下载EPG」等手动按钮**从不读这个字段**
- **电信认证 / 取频道 / 下载 EPG 一律读这个文件**，不再使用请求里的临时参数；页面的「⚙️ 认证与输出配置」只负责编辑保存（华为、中兴各一个弹窗，各写各的段）
- **`epg_server_url` / `logo_url` 留空＝自动用当前访问地址**：打开面板用的什么域名/IP，就生成什么链接
  - EPG 直链 = `<地址>/<epgname>`（`epgname` 默认 `epg.xml`，即「生成XML」合并后输出的那份，路由 `GET /epg.xml` 免登录）
  - 台标 = `<地址>/api/download/<频道名>.png`（与「频道管理」里选台标得到的链接同一种写法）；若 `logo_url` 填的是**外部台标服务器**（与本站不同源），则沿用 `<logo_url>/logo/<频道名>.png`。两种路径都免登录、都从 `data/icons` 取图（外部服务器除外），没有图片返回 404
  - 取频道 / 生成 m3u 时会把补齐后的地址写回 `app_config.json`，定时任务也用同一套链接；`127.0.0.1` / `localhost` 不会写回
  - **`RTP2HTTPD` 始终手工填**（华为平台的组播转单播服务地址，无法自动推断）；中兴平台对应的是 `live_proxy`
- **外部 EPG 源只存在于这个文件**：脚本里没有任何内置默认源，全新安装时 `epg_sources` 是空数组
  - 可以在页面「➕ 添加源」里加，也可以直接编辑 JSON 的 `epg_sources`（改完刷新即生效）
  - 上面示例里的两个地址可直接抄用（`epg.51zmt.top:8000` 官方域名很多网络超时，这里用的是同内容镜像）
- 首次启动会把旧的 `settings.json` + `epg_sources.json` 自动合并进来，旧文件改名为 `.json.bak` 保留
- 只保存部分设置（比如切一下 gzip 开关）不会冲掉其它配置

> `share_xml_path` 没有界面输入框，只由 `app_config.json` 里的值决定；改完立即生效（每次请求都会重读），但容器内必须有对应挂载才会保留文件。

**从旧版（`/volume2/docker/IPTV/...` 硬编码）升级**：把旧的数据目录复用即可，注意旧版的配置目录叫 `data`、新版叫 `config`：

```bash
mv /volume2/docker/IPTV/data /volume2/docker/IPTV/config
# 然后在 .env 里写：DATA_ROOT=/volume2/docker/IPTV
```

旧 `docker-compose.yml` 里挂的 `/volume2/ST`（共享目录）可以用 `SHARE_DIR=/volume2/ST` 保留。

---

## 🏗 运行架构

```
浏览器 ──▶ nginx:80 ──▶ uvicorn 127.0.0.1:8001 (FastAPI)
                └──▶ /epg.xml、/epg.xml.gz 直接读 /app/data/xml（不经 Python）
```

- 对外只暴露一个端口（默认 5673 → 容器 80）。
- uvicorn 只监听回环，外网无法直连。
- 容器由 `tini` 作为 PID 1，`docker stop` 会把 SIGTERM 转给 nginx / uvicorn 优雅退出。
- 内置 `HEALTHCHECK`，走 nginx → uvicorn 全链路。

---

## 🧩 代码结构

| 文件 | 职责 |
|---|---|
| `app/main.py` | 页面 + 频道 / EPG / M3U / 台标 / 共享目录 / 日志 / 设置 / 频道总览 |
| `app/auth.py` | 登录、改账号密码、Token 鉴权 |
| `app/telecom_platform.py` | **平台选择**：当前平台状态、切换（带警告确认）、切换后重建输出 |
| `app/telecom_api.py` | **华为**接口层（配置 / 认证 / 频道 / EPG / 日志）；频道缓存接口按当前平台读写 |
| `app/telecom_epg.py` | **华为**抓取核心：认证（`crypt_1` 加密 Authenticator）、频道、EPG、M3U 生成 |
| `app/zte_api.py` | **中兴**接口层（`/api/zte/*`：配置 / 认证 / 频道 / EPG / 代理控制 / 日志） |
| `app/zte_epg.py` | **中兴**抓取核心：三步认证、frameset 频道解析、`getTvodlist` 节目单、地址翻译、输出生成 |
| `app/rtsp_proxy.py` | 中兴回看 **RTSP 代理**（跟随 302、PLTV↔live / smil↔sdp 翻译、地址改写、可启停） |
| `app/channel_groups.py` | 频道分组推断（华为 / 中兴共用） |
| `app/downloader.py` | 下载引擎：频道 JSON、外部 EPG 源、过期清理 |
| `app/epg_parser.py` | 外部 EPG 解析（112114 / 51zmt 两种规则） |
| `app/xml_builder.py` | 生成 XMLTV：`build_xml`（主 XML，按当前平台优先合并）+ `build_telecom_xml`（平台专用） |
| `app/m3u_handler.py` | M3U 解析 / 重建 / 合并（去重、RTP、FCC、分组、保序） |
| `app/scheduler.py` | 定时任务：每日 EPG、电信频道 / EPG（按当前平台分发到华为或中兴） |
| `app/config.py` | 配置读写（`app_config.json` 统一配置、平台/频道的文件映射、原子写、文件名清洗） |
| `app/logger.py` | 按天分文件、按分类的日志（支持多个实时订阅者：华为 / 中兴面板） |
| `app/progress.py` | 下载进度（普通任务与电信任务共用） |
| `app/crypt_1.py` | 3DES/DES 加解密（华为 `EncryptToken` / Authenticator） |

前端对应文件：

| 文件 | 职责 |
|---|---|
| `static/js/telecomepg.js` | 华为配置弹窗 + 操作按钮；**按当前平台把按钮分发给中兴脚本** |
| `static/js/zteepg.js` | 中兴配置弹窗、认证/频道/EPG 操作、回看代理控制 |
| `static/js/telecomplatform.js` | 平台选择器、切换警告弹窗、平台状态展示 |
| `static/js/app.js` | 其余页面（仪表盘 / EPG / M3U / 总览 / 日志…） |

---

## ✨ 功能

界面共 **10 个页签**，对应后端 **105 个接口路径 / 133 个接口方法**。

**📊 仪表盘**
- 系统概览：频道总数、JSON 文件数、台标总数、XML 大小
- 外部 EPG 提取、手动生成 XML、清理过期文件
- 「⏰ 外部 EPG 定时任务」：启用开关 + 每日时间（写入 `app_config.json`，保存即重新排程）
- XML 直链（可复制）/ `.xml.gz` 开关
- 系统日志：分类查看、按日期下载
- 「电脑播放」卡片：下载 Windows 播放器 `IPTV.exe`
- 旧的「开始下载 / 下载过往」按钮已隐藏（当前流程以电信任务 + 外部 EPG 提取为主）

**📡 电信 EPG**
- **平台选择：🟦 华为平台 / 🟧 中兴平台**（四川各地市平台不同）
  - 顶部常驻平台状态：当前平台、是否已配置、频道缓存数量、输出文件名
  - **切换平台会弹窗警告**：当前平台已配置过 / 有频道缓存时，必须明确确认才切换；警告里会说明切换后哪些数据会变、哪些保留
  - 切换后自动按新平台重建 `sctv.txt / sctv.m3u / epg.xml / epg_<平台>.xml`（后台执行）
  - 两个平台「执行认证 / 获取频道 / 下载EPG / 生成M3U」互斥：当前平台不是它时，直接调用另一个平台的写接口会被拒绝（409）
- 华为平台：机顶盒参数配置（UserID / mac / STBID / key / Authenticator / areaId / STBType …）
- 中兴平台：IPTVServer / UserID / STBID / StbIP / Authenticator / UserGroupNMB / EPGGroupNMB 等参数
- 配置只写 `app_config.json`（华为写 `telecom`、中兴写 `telecom_zte`）；**认证 / 取频道 / 下载 EPG 全部读该文件**，页面参数不参与
- 测试连接、执行认证（华为：IPTV 认证 + 3DES 解 `EncryptToken`；中兴：`auth.jsp → index.jsp → funcportalauth.jsp` 三步）
- 获取频道列表（华为：组播地址、RTSP、fcc/fec；中兴：`frameset_builder.jsp` 解析 channelcode/组播/RTSP）并缓存
- 拉完频道后**自动并入「EPG 管理」**（设置项 `telecom_auto_add_to_epg`，默认开），电信节目因此能进入主 `epg.xml`（备注里标「电信源 / 中兴源」）
- 单频道 / 全部频道下载 EPG，后台异步 + 进度（中兴默认 8 天：前 6 天 + 今天 + 明天）
- 抓频道 / 下载 EPG 的进度**实时滚在页面底部「执行日志」**里（不占顶部进度条）；两个平台各有一份日志缓冲，切平台后日志跟着切换
- 「⏰ 定时任务」：频道刷新 / EPG 下载 各自开关与时间；任务运行时按**当前平台**分发，不会把两个平台混着跑
- 生成 `telecom.m3u`（华为）/ `zte.m3u`（中兴），以及 `sctv.txt` / `sctv.m3u`（当前平台，含回看地址）
- 自动生成平台专用 XML：`data/xml/epg_telecom.xml`（华为）/ `data/xml/epg_zte.xml`（中兴）+ `.gz`
- **中兴回看 RTSP 代理**：页面可直接启停，自动跟随服务器 302 重定向、把 `PLTV/smil + playseek` 翻译成服务器要的 `live/mpg + programbegin/programend`，并把响应里的地址改回本代理，播放器不用直连内网 CDN

**🌐 外部 EPG**
- 源管理：**源列表完全由配置文件决定**（`app_config.json` 的 `epg_sources`），脚本不内置默认源；全新安装时为空，在页面「➕ 添加源」或直接编辑 JSON 即可
  - 没下载过缓存的源会标 **「未下载」**
  - **状态列可点**：直接切换启用 / 禁用；禁用会把该源缓存改名成 `<名字>.xml.disabled` **挂起**，搜索频道、提取节目、合并 EPG 都不会再用它，重新启用自动恢复（不删数据）
  - 推荐地址（可直接抄进配置）：`https://m3u.ibert.me/epg/51zmt.xml`、`https://m3u.ibert.me/epg/112114_xyz.xml`
  - 官方 `epg.51zmt.top:8000` 在很多网络下 30s 超时连不上，上面是它的同内容镜像
- **两个下载按钮在同一行**：「⬇️ 下载EPG」（外部源）和「📡 下载电信EPG（优先）」都在源列表工具栏
- **缓存文件不会同名覆盖**：默认 `<域名>_<文件名>_<短哈希>.xml`（两个源都叫 `txml.gz` 也会分开），也可以在添加/编辑弹窗里自己填「保存文件名」；表格「备注」下面直接显示实际缓存名
- 下载源（自动解 gzip；连接超时 10s、读取 60s，失败会明确提示"超时/HTTP 状态"）
- 提取规则：112114 / 51zmt / 自动识别（区别见下方「两种规则的区别」）
- **频道提取**：已下载源里的频道自动列成表格，勾选后批量加到 EPG 管理
  - 搜索、来源筛选、「只看未添加」都在同一个弹窗里；已添加的显示「已添加 / 已并入 → 目标频道」
  - 全选本页、单条 ➕、批量添加，自动去重（重复提交会跳过）
- 同一个弹窗的「已添加记录」标签：搜索、「只看未并入」、导出/导入、表头吸顶、双向滚动
- 提取到 JSON（今明后），或「统一提取并合并」直接重建 XML

**📋 EPG 管理**
- EPG 频道表增删改、排序（拖动 ☰ 或「名称 / TVG-ID 排序」）
- ➕ **手动添加频道**（名称 / TVG-ID / TVG-name）
- **🔄 刷新是「只增不删」**：只把外部 EPG 提取记录里缺的补进来，不动任何已有频道（改过的名字/ID 不会被覆盖）
- **🧹 清理孤儿**：只删「来源已不存在」的频道；电信、手动、提取记录还在的一律保留
- 外部频道进这里的两条路：
  - 「外部EPG → 频道提取 → 勾选 ➕ 添加」→ 自动建一条（`source_type=extract`，带 logic_id、tvg-id=频道名）
  - 想并入已有频道：在「已添加记录」里点 ✏️ 选「并入目标」，外部节目单就补到那个频道缺失的日期
- 按 `logic_id` 一键把改名同步到提取记录

**📋 频道管理**（即原来的 M3U 管理）
- 上传 / 列表 / 在线编辑保存 / 下载 / 删除（支持 `.m3u` `.m3u8` `.txt`）
- 多文件合并：按 URL 或按名称去重、组播地址聚合、`rtp://` 转 RTP 代理、补 `fcc` 参数、按规则自动分组
- **保持第一个文件顺序**（默认勾选）：第一个文件（如电信）不重排，其它文件的新频道追加到末尾
- 编辑器支持「🔤 名称排序 / 🔗 URL 排序」，拖动 ☰ 排序时整行高亮
- FCC 预设（电信 / 联通 / 移动）、分组规则配置
- 手动给频道选节目单：下拉选项来自**合并后的 `epg.xml`**（电信 + 外部 EPG 的实际输出），电信里没有的频道也能匹配

**📇 频道总览**
- 来源切换：**当前电信平台频道**（看直播/回看地址，华为 / 中兴按平台生成）或任意 **M3U / TXT 文件**（含合并结果）
- 表格显示：频道、**TVG-NAME**、**台标 URL**、直播地址、回看地址（`catchup-source` / RTSP+playseek）、时移、FCC/FEC、tvg-id、分组
- 支持搜索、「只看有回看」筛选、单条复制直播/回看/台标地址、导出 CSV

**🖼️ 频道台标**
- 上传（20MB）/ 删除 / 列表，对外可直接访问，用作 M3U 的 `tvg-logo`

**📁 共享目录**
- 浏览、上传（200MB）、删除、下载
- **需登录才能下载**（不再提供匿名直链）

**👤 账户密码**
- 修改用户名、修改密码（改密后其他会话立即失效）

> 早期版本 README 里的「端口映射 / 302 代理」代码已在本次清理中全部删除。

---

## ⬆️ 升级到本版本

```bash
cd /path/to/iptv-server
cp -a data/config data/config.bak.$(date +%Y%m%d)   # 备份配置（建议）
bash build.sh                                       # 重新构建 + 启动
```

升级会自动完成：

| 事项 | 说明 |
|---|---|
| 配置合并 | 旧 `settings.json` + `epg_sources.json` → `app_config.json`（旧文件改名 `.json.bak`） |
| 前端缓存 | 静态资源已带版本号，刷新页面即生效 |
| 外部 EPG 源 | 不再内置默认值；沿用你原来的源，没有则去「外部EPG → ➕ 添加源」补 |
| 依赖 | 已移除 `beautifulsoup4 / lxml / soupsieve`，首次升级建议 `docker compose build --no-cache` |

---

## 🔧 2026-10 双平台合并（华为 + 中兴）

把原始备份里的中兴脚本（`iptv.py` / `rtspproxy.py`）合并进本项目，与原有华为实现并列：

| 类别 | 变更 |
|---|---|
| 平台 | `app_config.json` 新增 `telecom.platform`（`huawei` / `zte`）与独立 `telecom_zte` 段 |
| 中兴抓取 | 新增 `app/zte_epg.py`：三步认证、`frameset_builder.jsp` 频道解析、`getTvodlist.jsp` 节目单、地址翻译、JSON/M3U 输出、整任务编排 |
| 中兴接口 | 新增 `app/zte_api.py`（`/api/zte/*`），配置白名单、日志流、后台任务、代理控制 |
| 回看代理 | 新增 `app/rtsp_proxy.py`：原始 `rtspproxy.py` 整理为可启停服务，修了请求头截断、端口 0、RTP 交织数据误读等问题 |
| 平台选择 | 新增 `app/telecom_platform.py` + `static/js/telecomplatform.js`：切换前警告确认、切换后重建输出 |
| 数据隔离 | 频道缓存 `zte_channels.json`、EPG 后缀 `_zte`、XML `epg_zte.xml`、M3U `zte.m3u`；与华为互不覆盖 |
| 定时任务 | `scheduler.py` 按当前平台分发：中兴任务复用 `run_zte_job`，平台切走后自动跳过 XML 重建 |
| 前端 | 新增 `static/js/zteepg.js`；`telecomepg.js` 按平台分发按钮；频道列表 / 状态条 / 总览都显示当前平台 |
| 部署 | Dockerfile `EXPOSE 80 554`；compose 提供（默认注释的）`RTSP_PROXY_PORT:554` 映射和 `.env` 说明 |

---

## 🔧 本次更新概览（2026-10）

| 类别 | 变更 |
|---|---|
| 配置 | 电信 / 定时 / 全局 / 外部源统一到 `app_config.json`；抓取只读文件，页面只负责编辑 |
| 外部 EPG | 频道自动列表 + 勾选批量加入 EPG 管理；搜索内置；「已添加」状态；提取日期统一 `range(-6,3)` |
| EPG 管理 | ➕ 手动添加频道；🔄 刷新改为**只增不删**；🧹 清理孤儿；名称 / TVG-ID 排序 |
| 频道总览 | 新增页签：电信 / M3U 文件的直播、回看、台标 URL、TVG-NAME，可复制与导出 CSV |
| 合并 | 新增「保持第一个文件顺序」（电信为主，其它追加） |
| 交互 | 拖动 ☰ 整行高亮；表头吸顶；频道提取移到提取记录之前 |
| 修复 | 电信任务不再阻塞整站（`to_thread`）；外部 EPG 定时任务可保存；51zmt 源换镜像 |
| 清理 | 删除 11 个后端死函数、34 个前端死函数、20 个无用 import、3 个无用依赖；`/docs` 无重复 operationId |

---

## 🔧 本次修改清单

| 问题 | 处理 |
|---|---|
| 上传超过 1MB 必 413 | nginx 增加 `client_max_body_size 300m` |
| `killall` 在镜像里不存在（未装 psmisc） | 移除该逻辑，改为启动时先 `nginx -t` |
| `docker stop` 不转发信号，10 秒后被 SIGKILL | 镜像内置 `tini` + start.sh `trap` 优雅退出 |
| uvicorn 崩溃后容器仍显示 Up（全是 502） | `wait -n` + 整体退出，交给 `restart: unless-stopped` |
| 固定 `/volume2/...` 路径，换机器就废 | compose 改为 `./data` 相对路径 + `.env` 可覆盖 |
| `/app/data/icons`、`/app/data/share` 未创建 | Dockerfile / start.sh 补全 7 个目录 |
| 无健康检查 | 增加 `HEALTHCHECK`（nginx → uvicorn） |
| EPG 直链丢 `Cache-Control` | 按 nginx `add_header` 继承规则补写 |
| `Connection: upgrade` 永久生效 | 改为 `map $http_upgrade`，仅 WebSocket 才升级 |
| `--mount=type=cache` 依赖 BuildKit | 去掉，老版本 Docker / 群晖也能构建 |
| docker 日志无限增长 | json-file `max-size=10m` / `max-file=3` |
| 依赖含未使用包 | 移除 orjson / aiofiles / python-multipart / websocket-client / cryptography / cffi / pycparser |
| 镜像源写死 | `APT_MIRROR` / `PIP_INDEX_URL` 改为 build-arg |
| README 与代码不符（70 个默认频道 / 端口映射 / 302 代理 / 网页播放器） | 按实际代码重写「功能」「API」两节 |
| 下载地址分散在 `/api/m3u`、`/api/icons`、`/api/xml` | 新增统一入口 `/api/download/...`（m3u/txt/图片/xml/share） |
| 共享目录文件匿名可下载 | 改为**需登录**（只保留 `/api/download/share/...`） |
| 旧下载地址 `/api/m3u/*/download`、`/api/icons/file/*`、`/api/share/file/*` | 全部删除，只保留统一入口 `/api/download/` |
| `app/scripts/*.sh`、`progress.py`、`telecomepg.js` 是 CRLF | 统一转 LF；`build.sh` 的换行修正范围加上 `*.sh` |
| `/epg.xml` 短路径实际返回 gzip 内容 | 修正为返回原始 XML |
| `epg_parser.py` 被覆盖成 `downloader.py` 副本 | 由使用者补回解析器；另修 4 处：实体双转义、CDATA、时间缺时区、缓存文件名误匹配 |
| 电信 EPG 只有 JSON、没有自己的 XML | 新增 `build_telecom_xml()`，输出 `data/xml/epg_telecom.xml(.gz)` |
| 电信节目进不了主 `epg.xml`（要手工把频道加到 EPG 管理） | 拉完频道自动并入 EPG 管理（`telecom_auto_add_to_epg`，可关） |
| 频道管理表格在窄窗口/全屏被裁切，看不到「状态/操作」 | 内容区补 `min-width:0`；「下载链接」「来源备注」列改弹性宽度 |
| 电信页「保存定时设置」点了没反应 | `telecomSaveConfig` / `tcToggleIntervalMode` / `toggleTelecomSchedule` 三个函数缺失，已补；并补上打开页面时的回填 |
| EPG 管理「刷新」会把电信频道删掉 | `sync-all` 改为保留 `source_type=telecom` 的频道 |
| 外部 EPG 无法并入电信频道 | 提取记录新增「并入目标」，提取时按目标频道名落盘 |
| 电信任务进度挤在顶部进度条里，看不到细节 | 改为独立日志流 `/api/telecom/logs`，页面按 0.2s 轮询显示在执行日志 |
| 电信面板只有自己写的几行，底层认证/频道日志看不到 | `logger.set_extra_sink()`：电信操作期间把 app 日志镜像进面板，含认证过程、逐频道结果、获得多少频道 |
| 进入页面时整页上下抖动 | 全局进度条不再来回切换 `display`（它占文档流），改为只更新文案，并用 CSS `min-height` 固定高度 |
| 点电信操作后整站卡死、报 `NetworkError` | `requests` 是阻塞调用，认证/拉频道/拉 EPG/同步全部改到 `asyncio.to_thread` 执行，事件循环不再被卡住 |
| EPG 管理滚动后表头看不见 | `#epgChTable thead th` 加 `position:sticky`（与 M3U 编辑器一致） |
| 列宽拖拽越拖越窄、tvg-id 输入框不跟随 | 拖拽时跳过隐藏列（分组/图标）；M3U 编辑器里 URL/tvg-id/匹配框统一 `width:100%` |
| 没有排序功能 | EPG 管理加「名称排序 / TVG-ID 排序」；M3U 编辑器加「名称排序 / URL 排序」 |
| 看不到频道对应的直播/回看地址 | 新增「📇 频道总览」：电信频道或 M3U/TXT 文件，显示直播/回看/catchup，可复制、可导出 CSV |
| EPG 管理无法手动加频道，且外部频道条目容易被「刷新」清掉 | 新增「➕ 添加频道」；「🔄 刷新」改为只增不删，另有「🧹 清理孤儿」；外部独立频道自动带 logic_id、tvg-id=频道名 |
| 搜索外部 EPG 频道没反应 / 找不到 | 源列表标「未下载」；搜索返回每个源的下载状态与频道数，搜不到时直接提示哪个源没下载；下载失败信息明确（超时/HTTP） |
| 提取记录列表太长把页面撑开 | 「已配置提取记录」改为弹窗：表头吸顶、内部双向滚动，可搜索 / 只看未并入 / 导出导入 |
| 只能一个频道一个频道搜索添加，太慢 | 外部 EPG 频道自动列成表格（源频道标签页），可搜索/筛选/全选，勾选后批量添加到 EPG 管理 |
| 频道表「来源」空白、「全选本页→添加选中」全被跳过 | 缓存文件对不上配置源时用文件名兜底；批量添加允许来源为空（兜底 `外部EPG`） |
| 配置分散在 settings.json / epg_sources.json，且操作时还能被请求体覆盖 | 统一为 `app_config.json`（电信+定时+全局+外部源）；所有操作只读配置文件，前端仅编辑保存 |
| 默认 51zmt 源 30s 超时、搜索永远为空 | 官方域名确实连不上；源列表改为**只从配置文件读取**（脚本不内置默认），推荐地址写在 README 里 |
| 「外部 EPG 定时任务」改了不生效 | 表单 `onsubmit` 返回 Promise 被当成 true，浏览器原生提交刷新页面；改为 `saveCronSettings();return false`，保存后同步刷新状态 |
| 频道提取块位置靠后 | 「📥 频道提取」整行移到「📋 已配置提取记录」之前 |
| 拖动 ☰ 时看不出在拖哪一行 | 所有可拖拽表格：拖动行高亮（浅蓝）、目标行高亮（橙色上下边），M3U 编辑器同样生效 |
| 频道总览看不到台标/TVG-NAME | 新增「TVG-NAME」「台标 URL」两列（电信按 `logo_url/logo/频道名.png` 生成，M3U 读 `tvg-logo`/`tvg-name`），CSV 同步导出 |
| 提取只覆盖今天+2天，XML 读 9 天 | 三处 `extract_dates` 统一为 `range(-6, 3)`（前 6 天 + 今天 + 后 2 天） |
| 合并会重排频道顺序 | 新增「保持第一个文件顺序」选项（默认勾选）：第一个文件为主不重排，其它文件的新频道追加到末尾 |
| 代码里残留大量未使用的 import / 死函数 | 全量清理：`pyflakes` 0 警告；后端删掉 11 个未引用函数（端口映射、旧 M3U 频道表、base EPG、bs4 相关等） |
| 前端残留已删除页面的函数（旧「频道管理」、旧搜索面板） | 删除 30+ 个不会再被调用的 JS 函数与残留状态变量，事件处理器 78/78 全部有定义 |
| requirements 里带着用不到的 bs4 / lxml / soupsieve | 已移除（`beautifulsoup4` / `lxml` / `soupsieve`），镜像更小 |
| 拖动排序目标行不清晰 | 见上：拖动行/目标行高亮 |
| `/docs` 出现 `Duplicate Operation ID` 警告 | `api_route(methods=["GET","HEAD"])` 拆成 `@app.get` + `@app.head(include_in_schema=False)`；`/api/channels/export` 的 POST 同理隐藏，文档里 operationId 不再重复 |
| 「下载电信EPG（优先）」任务没跑完就重建 XML | 改为启动后跳到电信页看实时日志，XML 由任务结束时统一重建 |
| M3U「匹配节目单」只读 EPG 管理列表 | 改为读**合并后的 `epg.xml`**（电信 + 外部），再补未进 XML 的频道 |
| 界面里用不到的入口 | 删除旧「频道管理」页（M3U 页改名为「频道管理」）、隐藏仪表盘 2 个下载按钮；删除「从电信导入频道」「外部EPG 页的电信配置」 |
| 折叠面板默认展开占屏 | 「合并工具配置」「电信定时任务」默认收起，点标题才展开 |
| M3U 匹配编辑表太长、要滚整页 | 编辑器容器改为 `max-height:66vh` 内部滚动 + 表头吸顶 |
| EPG 管理 TVG-ID 显示被截断（CCTV10 → CCTV1） | 输入框宽度 60px → 100%，列宽 95 → 120px |
| 只有部分表格能拖列宽（文件列表、共享目录、电信频道表没有手柄） | 表头手柄改为统一注入 + 事件委托，**所有表格**（含动态渲染）都能拖；动态表格重渲染后自动恢复保存的列宽 |
| 只能调列宽，不能调整列的前后位置 | 表头可直接**拖动换位**（如把「显示名称」拖到「URL」前面），整表所有行一起动，保存到 `data/config/column_order.json`，刷新/重渲染后保持 |
| 生成 m3u/txt 里的 EPG 直链和台标要手填地址，容易填错（`http//:`） | `epg_server_url` / `logo_url` 留空即自动用当前访问地址；台标统一走 `/api/download/<频道名>.png`（与频道管理一致），`RTP2HTTPD` 仍手工填 |
| 没填 key 也能通过电信认证，看不出是否真的用了 key | 认证前统一校验：无 key 时日志明确警告「改用静态 Authenticator」；新增「必须用 key 生成 Authenticator」开关，勾选后无 key 直接拒绝认证 |
| 中文频道名台标 404（以为 URL 编码坏了） | 编码没问题；404 是 `data/icons` 里没有对应文件。台标查找改为**忽略大小写 + 自动换后缀**（`CCTV2.PNG`/`CCTV3.jpg` 都能命中），总览加「⚠️ 缺台标」标记、缺台标数量统计和「只看缺台标」筛选 |
| 忘了 key 就没法认证，原版会自动反推 | 新增「🔍 反推 key」：从现存 Authenticator 穷举 8 位数字 key（多进程、可停止、有进度），找到后写回 key 并用它认证一次把 Authenticator 也写回；认证成功时也会同步最新 Authenticator |
| 两个源 URL 都叫 `txml.gz` 时缓存互相覆盖 | 缓存名改为 `<域名>_<文件名>_<短哈希>.xml`，并支持在添加/编辑里自定义「保存文件名」；表格显示实际缓存名 |
| 外部 EPG 的「状态」只能看不能改 | 状态列改成按钮，点一下切换启用/禁用；禁用时把该源缓存改名 `.disabled` 挂起，搜索/提取/合并都不再使用它 |
| 两个下载按钮分散在两处 | 「⬇️ 下载EPG」和「📡 下载电信EPG（优先）」合并到源列表工具栏同一行 |
| 顶部「电信EPG 未启用」看着像开关，其实认证/下载都照常能用 | `enabled` 只是**定时任务开关**（`scheduler` 里的频道刷新 / EPG 下载会检查），手动按钮从不读它。状态条改为显示「已配置 / 缺什么字段 / 定时任务是否启用 / 已缓存多少频道」，弹窗里字段改名「启用定时任务」并注明手动按钮不受影响 |
| IPTV.exe 打进镜像，100MB、换版本要重建 | 改为 `GET /tools/IPTV.exe`，优先读挂载的 `./data/tools`（compose 已加卷），换文件即时生效；镜像内 `static/tools` 仅作兜底 |

---

## ⚠️ 注意事项

1. **换行符**：Dockerfile / *.sh 必须是 LF，Windows 下用记事本改过会启动失败（`build.sh` 会自动修一遍）。
2. **端口**：容器内 80，宿主机默认 5673；改 `WEB_PORT` 即可。中兴回看代理默认容器内 554，compose 里对应的映射**默认注释**（避免宿主机 554 被占用拖垮容器启动），用中兴回看时取消注释即可；宿主机端口由 `RTSP_PROXY_PORT` 决定。
3. **首次启动**：所有频道列表都是空的，外部 EPG 源也是**空的**（脚本不内置默认源），需要去「外部EPG → ➕ 添加源」自己加；频道来自「电信EPG」或「外部EPG」。
4. **上传上限**：共享目录 200MB、M3U 50MB、台标 20MB、频道导入 10MB；nginx 层统一 300m（前端 base64 编码会放大约 1.33 倍）。
5. **文件属主**：容器以 root 运行，数据文件属主是 `root`；群晖 File Station 里用管理员账号可正常读写。
6. **定时任务**：外部 EPG 默认每天 11:00（仪表盘 →「⏰ 外部 EPG 定时任务」修改）；电信频道/EPG 定时在「电信EPG → ⏰ 定时任务」。都写入 `app_config.json`，保存即生效；时区由 `TZ` 决定。定时任务按**当前平台**执行，只检查对应平台配置里的 `enabled`。
7. **电信频道会自动补回**：`telecom_auto_add_to_epg` 开启时，在「EPG 管理」里删掉的电信频道，下次拉频道会再加回来；不想这样就把该设置改成 `false`。
8. **平台别选错**：四川各地市平台不同。如果认证一直失败、频道列表为空或节目单全对不上，先确认顶部选的平台是否和当地一致；切换平台会有警告弹窗，确认后再重新认证、取频道。
9. **中兴回看代理**：默认关闭。启用后需要宿主机放行/映射 554（或页面里改的端口），并且 `replay_proxy` 要填**运行本程序的机器**的内网 IP（不是 127.0.0.1，否则手机/电视上的播放器连不上）。

---

## 🔗 外部 EPG × 电信 EPG 合并

目标：**电信有数据用电信，电信缺的那天用外部 EPG 补上，同一个频道在 `epg.xml` 里只出现一次。**

合并键是**频道名**，不是 tvg-id：

| 东西 | 作用 |
|---|---|
| 频道名 | 决定 JSON 文件名 `<频道名>_<日期>.json`，也是合并的依据 |
| `tvg-id` | 只决定输出 XML 里的 `<channel id="...">`，**不参与合并** |
| `tvg-name` | 输出 XML 的 `<display-name>` |

操作步骤：

1. 「外部 EPG」→ 添加源 → ⬇️ 下载EPG（拿到缓存，源列表显示「已下载」）
2. 「📥 频道提取」→「📋 打开频道表」→ 搜索/筛选 → 勾选要的频道 →「➕ 添加选中」
3. 切到「📄 已添加记录」，点 ✏️ 编辑：**「并入目标」选择对应的电信频道**，保存
4. 仪表盘「🌐 外部EPG提取」（或等每日任务）→ 节目写成 `data/json/<电信频道名>_<日期>.json`
5. 「📄 生成XML」→ 该频道每天优先读 `_telecom` 文件，没有的日期才用外部文件

频道名匹配：建议直接用「已添加记录」里的下拉手选；`tvg-id` 可与频道名不同（播放器靠它对齐节目单）。

注意：

- 合并是**按天整份替换**，不是逐条节目拼接：某天有 `_telecom`（或 `_zte`）文件就整天用电信，没有才用外部。
- 合并后的提取记录不会在「EPG 管理」里再建一条频道（避免重复）。
- 想解除合并：编辑该提取记录，把「并入目标」改回「独立频道」。

---

## 🏙 四川各地市：华为 / 中兴两个电信平台

四川各地市的电信 IPTV 平台并不统一，同一套参数换到别的城市大概率认证失败或频道对不上。
所以本版本把两个平台做成**并列、互不干扰**的两套实现：

| 项目 | 华为平台 | 中兴平台 |
|---|---|---|
| 前端脚本 | `static/js/telecomepg.js` | `static/js/zteepg.js` |
| 后端接口 | `/api/telecom/*`（`telecom_api.py`） | `/api/zte/*`（`zte_api.py`） |
| 抓取核心 | `app/telecom_epg.py` | `app/zte_epg.py` |
| 配置段（`app_config.json`） | `telecom` | `telecom_zte` |
| 频道缓存 | `data/config/telecom_channels.json` | `data/config/zte_channels.json` |
| 节目单 JSON | `<频道名>_telecom_<日期>.json` | `<频道名>_zte_<日期>.json` |
| 平台专用 XML | `data/xml/epg_telecom.xml` | `data/xml/epg_zte.xml` |
| M3U | `data/m3u/telecom.m3u` | `data/m3u/zte.m3u` |
| 定时任务 | 共用「电信频道 / 电信EPG」两个时间点，运行时按当前平台分发 | 同左 |

### 切换规则（重点）

1. 打开「📡 电信EPG」，顶部按钮显示当前平台（高亮）和两家各自的状态。
2. 点另一个平台时：
   - 如果当前平台**没配置过、也没有频道缓存** → 直接切换；
   - 否则先弹窗列出警告：当前平台是什么、已有多少频道、切换后哪些数据会改变、哪些会保留；
   - 只有点「我已确认，切换」才会真正执行（`POST /api/telecom/platform` 带 `confirm=true`）。
3. 切换后：`sctv.txt / sctv.m3u` 会用新平台的频道缓存重建；`epg.xml` 只读取新平台的 `<平台后缀>` JSON；
   另一个平台的配置文件、频道缓存、JSON、专用 XML、m3u **原样保留**，切回去继续用。
4. 已并入「EPG 管理」的频道不会因切换而删除；如果某频道只存在于旧平台，主 `epg.xml` 里它会暂时没有节目数据，
   需要的话在「EPG 管理」里手动清理或重新获取新平台的频道。

> 想换城市/换账号前，建议先切换平台再改参数，避免把新城市的账号填进旧平台的配置里。

### 中兴回看为什么要代理

中兴的回看地址形如 `rtsp://<CDN>/PLTV/xxx.smil?playseek=...`，服务器会先回 302 把播放器指到边缘节点，
并且只认 `live/.../mpg + programbegin/programend` 这种形式。普通播放器直接播会失败，所以：

- `app/rtsp_proxy.py` 监听一个本地端口（默认 554），接收播放器请求 → 按规则翻译 → 转发真实服务器 → 跟随 302 → 把响应里的地址改回代理
- 页面「中兴平台」弹窗里勾「启用回看代理」并填真实 RTSP 服务器后，保存即自动启动；服务重启也会自动拉起
- `replay_proxy` 填 `rtsp://<运行本程序的机器内网IP>:554`，生成的回看地址就会走代理
- 端口占用或权限不足时，弹窗/状态行会显示具体错误；Docker 需要把 554 映射出来（compose 里那行默认是注释的，用的时候取消注释）
- ⚠️ 代理本身**不做鉴权**（播放器无法方便地带账号），只应在内网使用，不要把 554 直接映射到公网

---

## 🐛 故障排查

| 现象 | 排查 |
|---|---|
| 容器 `unhealthy` | `docker compose logs --tail=100`；多半是 nginx 配置或端口问题 |
| 访问 502 | uvicorn 没起来：日志里搜 `Traceback` |
| 上传 413 | 文件 > 200MB，或改了 nginx 没重建镜像 |
| 打开 `/epg.xml` 404 | 还没生成，先去仪表盘点「生成 XML」 |
| 打开 `/epg.xml` 403 | 挂载目录权限过严（`chmod 755` 目录、`644` 文件） |
| 端口被占用 | `ss -tlnp` 查看谁占了 5673，或直接改 `WEB_PORT` |
| 群晖外网访问不了 | 控制面板防火墙放行端口 + 路由器端口转发 |
| 时间不对 | 检查 `.env` 的 `TZ`，改完 `docker compose up -d` 重建 |
| 改了 `.env` 密码没生效 | 首次启动已写入 `config/auth.json`，删掉它或页面改密码 |
| 构建卡在 apt/pip | 换 `APT_MIRROR` / `PIP_INDEX_URL` 为离你更近的源 |
| 外部 EPG 搜索/频道表是空的 | 源还没下载成功：源列表有红标「未下载」→ 点「⬇️ 下载EPG」；官方 51zmt 域名常超时，用 `https://m3u.ibert.me/epg/51zmt.xml` |
| 改了「外部 EPG 定时任务」不生效 | 已在 v20261022x 修复（表单提交会刷新页面）；若仍不生效，强制刷新一次页面（`Ctrl+F5`） |
| 页面上下抖动 | 旧版本遗留；已修复（进度条不再切换 display）。强制刷新拿新 JS |
| 电信任务一点整个页面卡住 | 旧版本会把事件循环占满；现已改为线程池执行。若仍卡，检查是否还在跑旧镜像 |
| 合并后频道顺序被打乱 | 「合并文件」里勾选**保持第一个文件顺序**，并把电信文件排第一个 |
| 配置改了没生效 | 看 `data/config/app_config.json` 是否被写入（页面保存后会立即生效，不需要重启） |
| 华为/中兴认证一直失败 | 先确认顶部平台选对（四川各地市不同）；中兴要填抓包得到的 `Authenticator` 和 `IPTVServer`，不能用华为的 key 方式生成 |
| 中兴「获取频道列表」为空 | 多半是认证失效或页面结构变了：先「执行认证」看结果；可设置环境变量 `ZTE_DEBUG_DUMP=1` 把原始 `frameset_builder.jsp` 存到 `data/logs/` 排查 |
| 中兴回看播不出来 | 检查「🎧 回看代理」是否运行、`replay_proxy` 是否填了本机内网 IP、`rtsp_upstream` 是否填了抓包到的真实 RTSP 服务器；代理日志会写「已改连边缘节点 / 连接失败」 |
| 回看代理启动失败「端口被占用或权限不足」 | 宿主机 554 被占用时改 `.env` 的 `RTSP_PROXY_PORT`，或在页面里把监听端口改成别的（同时改 compose 映射 / `replay_proxy`） |
| 切换平台后 sctv 还是旧平台地址 | 切换后重建是后台任务，稍等几秒看执行日志；新平台没有频道缓存时会清空 sctv 别名，需要先「更新频道列表」 |

---

## 🔌 主要 API

| 接口 | 说明 | 认证 |
|---|---|---|
| `GET /` | 管理界面 | 公开 |
| `GET /api/xml` · `GET /api/xml/epg.xml` | 获取 EPG XML | 公开 |
| `GET /epg.xml` · `GET /epg.xml.gz` | nginx 直出 XML / gzip | 公开 |
| `GET /api/xml/url` | 返回可复制的直链 | 需登录 |
| `GET /api/status` | 系统状态 | 需登录 |
| `GET/POST /api/channels` | 频道列表 / 新增 | 需登录 |
| `POST /api/download/start` · `GET /api/download/progress` | 下载任务 / 进度 | 需登录 |
| `GET/POST /api/epg/sources` · `POST /api/epg/download` | 外部 EPG 源与下载 | 需登录 |
| `GET /api/m3u/{文件}` | M3U 内容（JSON，供编辑用） | 需登录 |
| `GET /api/overview?source=…` | 频道总览（`telecom` 或 `m3u:文件名`） | 需登录 |
| `GET /api/download/{文件}` | **统一下载入口**（见下节，唯一的文件下载地址） | m3u/图标/xml 公开，share 需登录 |
| `/api/telecom/platform` | **平台选择**：GET 读当前平台 / POST 切换（需 `confirm`） | 需登录 |
| `/api/telecom/status` | 当前平台状态（已配置 / 缺什么 / 频道缓存数） | 需登录 |
| `/api/telecom/*` | **华为**认证、频道、EPG、M3U、日志、反推 key | 需登录 |
| `/api/zte/*` | **中兴**配置、认证、频道、EPG、M3U、代理启停、日志 | 需登录 |

> 完整接口以 `app/main.py`、`app/auth.py`、`app/telecom_api.py`、`app/telecom_platform.py`、`app/zte_api.py` 中的路由定义为准（105 个路径 / 133 个方法）。

---

## ⬇️ 统一下载入口 `/api/download/...`

一个前缀覆盖所有可下载类型，按 `m3u → icon → xml → share` 顺序查找同名文件：

| 地址 | 实际文件 | 认证 |
|---|---|---|
| `/api/download/telecom.m3u` | `data/m3u/telecom.m3u`（华为，`.m3u` `.m3u8` `.txt`） | 公开 |
| `/api/download/zte.m3u` | `data/m3u/zte.m3u`（中兴） | 公开 |
| `/api/download/sctv.m3u` · `sctv.txt` | `data/m3u/sctv.*`（**当前平台**生成的别名，切换平台后会被重建） | 公开 |
| `/api/download/CCTV1.png` | `data/icons/CCTV1.png`（png/jpg/jpeg/gif/svg/webp/ico） | 公开 |
| `/api/download/epg.xml` · `/api/download/epg.xml.gz` | `data/xml/epg.xml(.gz)` | 公开 |
| `/api/download/epg_telecom.xml` · `.gz` | `data/xml/epg_telecom.xml(.gz)`（华为平台专用） | 公开 |
| `/api/download/epg_zte.xml` · `.gz` | `data/xml/epg_zte.xml(.gz)`（中兴平台专用） | 公开 |
| `/api/download/share/子目录/文件` | 共享目录里的任意文件 | **需登录** |

补充用法：

- 显式类型，避免同名歧义：`/api/download/m3u/xxx.m3u`、`/api/download/icon/xxx.png`、`/api/download/xml/epg.xml`、`/api/download/share/a.txt`
- 或加参数：`?type=m3u|icon|xml|share`
- 强制作为附件下载：`?download=1`（默认 `inline`，方便播放器直接读）
- **旧的 `/api/m3u/{名}/download`、`/api/icons/file/{名}`、`/api/share/file/{路径}` 已删除**，一律改用上面的地址

```bash
# 示例
curl -O http://NAS:5673/api/download/telecom.m3u
curl -O http://NAS:5673/api/download/epg.xml.gz
# 共享目录需要先登录拿 token
curl -c jar -X POST http://NAS:5673/api/auth/login \
     -H 'Content-Type: application/json' -d '{"username":"666","password":"666"}'
curl -b jar -O http://NAS:5673/api/download/share/report.pdf
```
