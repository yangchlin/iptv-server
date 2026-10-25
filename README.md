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

### ② 配置电信（可选，但推荐）
「📡 电信EPG」→「⚙️ 电信认证与EPG配置」填 `UserID / mac / STBID / key / Authenticator` → **保存**
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
产物：`data/xml/epg.xml`（+`.gz`）、`data/xml/epg_telecom.xml`（仅电信）
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
├── json/      # 下载的 JSON 节目数据
├── xml/       # 生成的 XML：epg.xml(.gz) 全部频道 / epg_telecom.xml(.gz) 仅电信
├── m3u/       # M3U 播放列表
├── icons/     # 台标
├── logs/      # 运行日志（保留 7 天）
├── tools/     # 外部工具：仪表盘「电脑播放」下载的 IPTV.exe 放这里（换版本不用重建镜像）
├── config/    # app_config.json（唯一配置文件）+ 频道/认证等其它数据
└── share/     # 共享目录（设置项 share_xml_path，默认 /app/data/share）
```

### 统一配置文件 `data/config/app_config.json`

**所有可配置项都在这一个文件里**，前端页面只是编辑器：

```json
{
  "config_version": 2,
  "telecom": { "enabled": true, "AuthenticationIP": "...", "UserID": "...", "mac": "...", "Authenticator": "...", "RTP2HTTPD": "..." },
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

- **电信认证 / 取频道 / 下载 EPG 一律读这个文件**，不再使用请求里的临时参数；页面的「⚙️ 电信认证与EPG配置」只负责编辑保存
- **`epg_server_url` / `logo_url` 留空＝自动用当前访问地址**：打开面板用的什么域名/IP，就生成什么链接
  - EPG 直链 = `<地址>/<epgname>`（`epgname` 默认 `epg.xml`，即「生成XML」合并后输出的那份，路由 `GET /epg.xml` 免登录）
  - 台标 = `<地址>/api/download/<频道名>.png`（与「频道管理」里选台标得到的链接同一种写法）；若 `logo_url` 填的是**外部台标服务器**（与本站不同源），则沿用 `<logo_url>/logo/<频道名>.png`。两种路径都免登录、都从 `data/icons` 取图（外部服务器除外），没有图片返回 404
  - 取频道 / 生成 m3u 时会把补齐后的地址写回 `app_config.json`，定时任务也用同一套链接；`127.0.0.1` / `localhost` 不会写回
  - **`RTP2HTTPD` 始终手工填**（组播转单播服务地址，无法自动推断）
- **外部 EPG 源只存在于这个文件**：脚本里没有任何内置默认源，全新安装时 `epg_sources` 是空数组
  - 可以在页面「➕ 添加源」里加，也可以直接编辑 JSON 的 `epg_sources`（改完刷新即生效）
  - 上面示例里的两个地址可直接抄用（`epg.51zmt.top:8000` 官方域名很多网络超时，这里用的是同内容镜像）
- **`telecom.enabled` 只是定时任务开关**：只有 `scheduler` 里的「定时刷新电信频道 / 定时下载电信EPG」会检查它；页面上的「执行认证 / 获取频道 / 下载EPG」等手动按钮**从不读这个字段**。所以状态条显示「定时任务未启用」时，手动操作一切照常
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
| `app/telecom_api.py` | 电信 IPTV 接口层（配置 / 认证 / 频道 / EPG / 日志） |
| `app/telecom_epg.py` | 电信抓取核心：认证（`crypt_1` 加密 Authenticator）、频道、EPG、M3U 生成 |
| `app/downloader.py` | 下载引擎：频道 JSON、外部 EPG 源、过期清理 |
| `app/epg_parser.py` | 外部 EPG 解析（112114 / 51zmt 两种规则） |
| `app/xml_builder.py` | 生成 XMLTV：`build_xml`（主 XML，电信优先合并）+ `build_telecom_xml`（仅电信） |
| `app/m3u_handler.py` | M3U 解析 / 重建 / 合并（去重、RTP、FCC、分组、保序） |
| `app/scheduler.py` | 定时任务：每日 EPG、电信频道、电信 EPG |
| `app/config.py` | 配置读写（`app_config.json` 统一配置、原子写、文件名清洗、保留策略） |
| `app/logger.py` | 按天分文件、按分类的日志 |
| `app/progress.py` | 下载进度（普通任务与电信任务共用） |
| `app/crypt_1.py` | 3DES/DES 加解密（电信 `EncryptToken` / Authenticator） |

---

## ✨ 功能

界面共 **9 个页签**，对应后端 **84 个路由 / 88 个接口方法**。

**📊 仪表盘**
- 系统概览：频道总数、JSON 文件数、台标总数、XML 大小
- 外部 EPG 提取、手动生成 XML、清理过期文件
- 「⏰ 外部 EPG 定时任务」：启用开关 + 每日时间（写入 `app_config.json`，保存即重新排程）
- XML 直链（可复制）/ `.xml.gz` 开关
- 系统日志：分类查看、按日期下载
- 「电脑播放」卡片：下载 Windows 播放器 `IPTV.exe`
- 旧的「开始下载 / 下载过往」按钮已隐藏（当前流程以电信任务 + 外部 EPG 提取为主）

**📡 电信 EPG**
- 机顶盒参数配置（UserID / mac / STBID / key / Authenticator / areaId / STBType …）
- 配置只写 `app_config.json`；**认证 / 取频道 / 下载 EPG 全部读该文件**，页面参数不参与
- 测试连接、执行认证（华为 IPTV 认证流程 + 3DES 解 `EncryptToken`，`crypt_1.py`）
- 获取频道列表（组播地址、RTSP、fcc/fec 参数）并缓存
- 拉完频道后**自动并入「EPG 管理」**（设置项 `telecom_auto_add_to_epg`，默认开），电信节目因此能进入主 `epg.xml`
- 单频道 / 全部频道下载 EPG，后台异步 + 进度
- 抓频道 / 下载 EPG 的进度**实时滚在页面底部「执行日志」**里（不占顶部进度条），底层认证与逐频道明细也会显示
- 「⏰ 定时任务」：频道刷新 / EPG 下载 各自开关与时间
- 生成 `telecom.m3u`，以及 `sctv.txt` / `sctv.m3u`（含回看地址）
- 自动生成电信专用 XML：`data/xml/epg_telecom.xml` + `.gz`（与 `epg.xml` 同目录）

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
- 来源切换：**电信频道**（看直播/回看地址）或任意 **M3U / TXT 文件**（含合并结果）
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
2. **端口**：容器内 80，宿主机默认 5673；改 `WEB_PORT` 即可。
3. **首次启动**：所有频道列表都是空的，外部 EPG 源也是**空的**（脚本不内置默认源），需要去「外部EPG → ➕ 添加源」自己加；频道来自「电信EPG」或「外部EPG」。
4. **上传上限**：共享目录 200MB、M3U 50MB、台标 20MB、频道导入 10MB；nginx 层统一 300m（前端 base64 编码会放大约 1.33 倍）。
5. **文件属主**：容器以 root 运行，数据文件属主是 `root`；群晖 File Station 里用管理员账号可正常读写。
6. **定时任务**：外部 EPG 默认每天 11:00（仪表盘 →「⏰ 外部 EPG 定时任务」修改）；电信频道/EPG 定时在「电信EPG → ⏰ 定时任务」。都写入 `app_config.json`，保存即生效；时区由 `TZ` 决定。
7. **电信频道会自动补回**：`telecom_auto_add_to_epg` 开启时，在「EPG 管理」里删掉的电信频道，下次拉频道会再加回来；不想这样就把该设置改成 `false`。

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

- 合并是**按天整份替换**，不是逐条节目拼接：某天有 `_telecom` 文件就整天用电信，没有才用外部。
- 合并后的提取记录不会在「EPG 管理」里再建一条频道（避免重复）。
- 想解除合并：编辑该提取记录，把「并入目标」改回「独立频道」。

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
| `/api/telecom/*` | 电信认证、频道、EPG、M3U | 需登录 |

> 完整接口以 `app/main.py`、`app/auth.py`、`app/telecom_api.py` 中的路由定义为准（共 84 个）。

---

## ⬇️ 统一下载入口 `/api/download/...`

一个前缀覆盖所有可下载类型，按 `m3u → icon → xml → share` 顺序查找同名文件：

| 地址 | 实际文件 | 认证 |
|---|---|---|
| `/api/download/telecom.m3u` | `data/m3u/telecom.m3u`（`.m3u` `.m3u8` `.txt`） | 公开 |
| `/api/download/CCTV1.png` | `data/icons/CCTV1.png`（png/jpg/jpeg/gif/svg/webp/ico） | 公开 |
| `/api/download/epg.xml` · `/api/download/epg.xml.gz` | `data/xml/epg.xml(.gz)` | 公开 |
| `/api/download/epg_telecom.xml` · `.gz` | `data/xml/epg_telecom.xml(.gz)`（仅电信） | 公开 |
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
