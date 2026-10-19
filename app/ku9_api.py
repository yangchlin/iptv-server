"""
ku9_api.py - Ku9 APK 配置构建 API（独立模块）
默认配置存储于 _default_profile 目录，支持从基座 APK 提取 Configuration.json
"""
import json
import datetime
import shutil
import zipfile
import subprocess
import tempfile
import os
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Body, UploadFile, File
from fastapi.responses import FileResponse

from .logger import logger

router = APIRouter(prefix="/api/ku9")
DATA_DIR = Path("/app/.ku9_data")
KU9_CONFIG_FILE = DATA_DIR / "ku9_config.json"
KU9_DEFAULT_PROFILE_DIR = DATA_DIR / "_default_profile"
KU9_OUTPUT_DIR = DATA_DIR / "ku9_output"
KU9_APK_COPY = DATA_DIR / "ku9.apk"
KU9_APK_SOURCE = None
# 自动查找 static/不要动/ 下的任意 .apk 文件
_find = list((Path(__file__).parent.parent / "static" / "不要动").glob("*.apk"))
if _find:
    KU9_APK_SOURCE = _find[0]

KU9_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =========================== 初始化 ===========================

def ensure_apk_copy():
    """确保容器内有 APK 副本"""
    if KU9_APK_COPY.exists():
        return
    if KU9_APK_SOURCE and KU9_APK_SOURCE.exists():
        shutil.copy2(str(KU9_APK_SOURCE), str(KU9_APK_COPY))
        logger.info(f"已复制 APK 到 {KU9_APK_COPY}", category="其他")


def extract_config_json(apk_path: Path) -> dict | None:
    """从 APK (zip) 的 assets/ 中提取 Configuration.json"""
    try:
        with zipfile.ZipFile(str(apk_path), 'r') as zf:
            candidates = [f for f in zf.namelist() if f.endswith("Configuration.json")]
            if candidates:
                return json.loads(zf.read(candidates[0]))
    except Exception as e:
        logger.error(f"提取 Configuration.json 失败: {e}")
    return None


def parse_addr_items(val):
    """解析地址字符串为 [{val, name}] 格式
    格式：地址1$名称1 || 地址2 || 地址3$名称3
    """
    if not val:
        return []
    items = []
    for part in val.split(" || "):
        part = part.strip()
        if not part:
            continue
        if "$" in part:
            v, n = part.rsplit("$", 1)
            items.append({"val": v.strip(), "name": n.strip()})
        else:
            items.append({"val": part, "name": ""})
    return items


# 快捷菜单默认勾选（四行七列）
DEFAULT_SHORTCUT_NAMES = [
    '设置','媒体信息','列表订阅','EPG 订阅','Headers','UserAgent','频道搜索',
    '轨道切换','设备信息','APP 信息','渲染类型','安全 DNS','清除数据','回放方式',
    'RTSP 通道','导航模式','EPG 展示','界面设置','图标设置','数据备份','浏览内核',
    '断线重连','自启设置','分组类型','视频标签','频道推送','JSON 解析','无线投屏',
]
DEFAULT_ADDR4 = {k: False for k in DEFAULT_SHORTCUT_NAMES}

# select 反向映射（数值 → 中文文本）
SELECT_TEXT_MAP = {
    "PLAY_TYPE": {0:"系统解码",1:"IJK硬解",2:"IJK软解",3:"EXO硬解",4:"EXO软解"},
    "PLAY_SCALE": {0:"默认",1:"16:9",2:"4:3",3:"填充",4:"原始",6:"裁剪"},
    "LIVE_CONNECT_TIMEOUT": {0:"5s",1:"10s",2:"15s",3:"20s",4:"25s",5:"30s"},
    "EPGTYPE_SELECT": {0:"百川/DIYP",1:"超级TV",2:"XmlTV"},
    "PLAY_RENDER": {0:"texture",1:"surface"},
    "THEME_SELECT": {0:"0",1:"1",2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9"},
    "PLAY_BACK_TYPE": {0:"重新加载url更新进度",1:"播放器seekTo更新进度"},
    "RECONNECT_INDEX": {0:"关闭重连",1:"1秒后重连",2:"3秒后重连",3:"5秒后重连",4:"10秒后重连",5:"20秒后重连"},
    "RTSP_TCP_SELECT": {0:"TCP",1:"UDP"},
    "NAVIGATION_SELECT": {0:"滑动显示",1:"一直显示",2:"触摸显示"},
    "EPG_SHOW_TYPE_SELECT": {0:"折叠展示",1:"一直展示"},
    "EPGCACHE_SELECT": {0:"关闭缓存",1:"每天2点",2:"每天4点",3:"每天6点",4:"每天8点",5:"每天10点",6:"每天12点",7:"每天14点",8:"每天16点",9:"每天18点",10:"每天20点",11:"每天22点"},
    "GROUP_PARS_SET_SELECT": {0:"传统分组",1:"列表分组",2:"二级分组模式1",3:"二级分组模式2"},
    "TIME_ZONE_SELECT": {},
    "RESOLUTION_MODE_SELECT": {0:"分辨率缩写",1:"分辨率数字x数字"},
    "TIME_SHIFT_MODE": {True:"时移结束自动刷新继续播放",False:"回到直播"},
    "ICON_INITIAL_SET": {True:"显示自定义背景频道首字",False:"显示默认图片"},
    "DOH_URL": {0:"关闭",1:"腾讯",2:"阿里",3:"360",4:"Google",5:"AdGuard",6:"Quad9"},
    "M3U_LOGO_PRIORITY": {True:"m3u优先",False:"默认优先级"},
    "BOOTRECEIVER_SET_SELECT": {True:"开机获取IP后自启",False:"开机直接自启"},
    "EPG_CACHE_PATH_SET": {True:"外部储存",False:"内部储存"},
}
# 补全 TIME_ZONE_SELECT
_tz_opts = ["默认时区"]
for i in range(1, 13): _tz_opts.append(f"-{i}小时")
_tz_opts.append("+0小时")
for i in range(1, 13): _tz_opts.append(f"+{i}小时")
SELECT_TEXT_MAP["TIME_ZONE_SELECT"] = {k: v for k, v in enumerate(_tz_opts)}

# switches 映射
SWITCH_KEYS = [
    "hideLoginText", "LIVE_SHOW_TIME", "LIVE_SHOW_NET_SPEED",
    "HIDE_Channel_LOGO", "HIDE_Bottom_LOGO", "CLOSE_EPG",
    "HIDE_FAVOR", "HIDE_NUMBER", "PL_MEMORYS_ET_SELECT",
    "LIVE_CHANNEL_REVERSE", "LIVE_CROSS_GROUP", "LIVE_SKIP_PASSWORD",
    "PIC_IN_PIC", "BOOT_START", "QUICK_EXIT", "EYE_PROTECTION",
    "PLAYBACK_ID", "TIME_SHIFT_ON", "IMAGECACHE_SELECT", "SCRIPT_CACHE",
    "MEMORYS_SOURCE", "MEMORYS_POSITION", "SHORTCUTS_MENU",
    "PLAY_ALL_SOURCE", "ENABLE_LOCAL_VIDEO", "EPG_DESC_SET",
    "BOTTOM_DESC_SET", "EXO_TUNNELING_SELECT",
]


def make_empty_user_config() -> dict:
    """返回空配置"""
    return {
        "addrData5": {},
        "addrData3": {},
        "addrData4": dict(DEFAULT_ADDR4),
        "selectVals": {},
        "switches": {},
        "appName": ""
    }


def config_to_user_config(conf: dict) -> dict:
    """将 Configuration.json 的 conf 字典映射为 user_config 格式"""
    uc = make_empty_user_config()

    # addrData5
    addr5_keys = {
        "预置 M3U": "LIVE_URLS",
        "预置 EPG": "EPG_URLS",
        "预置 JSON": "LIVE_JSONS",
        "预置头部请求": "HEADERS_URLS",
        "预置用户代理": "USER_AGENTS",
    }
    for cn_name, json_key in addr5_keys.items():
        raw = conf.get(json_key)
        if raw:
            parsed = parse_addr_items(raw)
            if parsed:
                uc["addrData5"][cn_name] = parsed

    # addrData3
    uc["addrData3"]["启动图片路径"] = conf.get("loginPng", "")
    uc["addrData3"]["退出图片路径"] = conf.get("exitPng", "")
    uc["addrData3"]["启动数据提示文字"] = conf.get("loginText", "正在加载数据，请稍后...")
    uc["addrData3"]["启动图停留时间(ms)"] = conf.get("loginPng_Dwell_time", 200)
    uc["addrData3"]["右上角时间格式"] = conf.get("time_format", "HH:mm EE")

    # selectVals
    for json_key, val_map in SELECT_TEXT_MAP.items():
        val = conf.get(json_key)
        if val is not None and val in val_map:
            uc["selectVals"][json_key] = val_map[val]

    # switches
    for sk in SWITCH_KEYS:
        if sk in conf:
            uc["switches"][sk] = conf[sk]

    # SHORTCUTS_MENU_SELECT → addrData4
    shortcut_raw = conf.get("SHORTCUTS_MENU_SELECT", "")
    if shortcut_raw:
        selected = [s.strip() for s in shortcut_raw.split(",") if s.strip()]
        for name in uc["addrData4"]:
            uc["addrData4"][name] = name in selected

    return uc


def try_read_app_name() -> str:
    """反编译 APK 读取应用名称（耗时长）"""
    ensure_apk_copy()
    if not KU9_APK_COPY.exists():
        return ""
    apktool = shutil.which("apktool")
    if not apktool:
        return ""
    td = tempfile.mkdtemp()
    try:
        result = subprocess.run(
            [apktool, "d", "-f", "-o", td, str(KU9_APK_COPY)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            sp = Path(td) / "res" / "values" / "strings.xml"
            if sp.exists():
                import re
                content = sp.read_text(encoding="utf-8")
                m = re.search(r'<string name="app_name">(.*?)</string>', content)
                if m:
                    logger.info(f"从 APK 读取应用名称: {m.group(1)}", category="其他")
                    return m.group(1)
    except Exception as e:
        logger.warning(f"读取应用名称失败: {e}", category="失败")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return ""


def init_default_profile():
    """容器首次启动时，从 APK 提取配置+图标到 _default_profile"""
    dp = KU9_DEFAULT_PROFILE_DIR
    dp.mkdir(parents=True, exist_ok=True)
    if (dp / "user_config.json").exists():
        return  # 已初始化

    ensure_apk_copy()
    if not KU9_APK_COPY.exists():
        # 没有 APK 源，写个空配置
        (dp / "user_config.json").write_text(
            json.dumps(make_empty_user_config(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return

    # 提取 Configuration.json
    cfg = extract_config_json(KU9_APK_COPY)
    if cfg:
        (dp / "Configuration.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        conf = cfg.get("Configuration", {})
        uc = config_to_user_config(conf)
    else:
        uc = make_empty_user_config()

    # 读取应用名称（反编译）
    uc["appName"] = try_read_app_name()
    (dp / "user_config.json").write_text(
        json.dumps(uc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 提取默认图标/背景（从 APK zip 直接提取资源文件）
    # 新文件名提取，与上传保存的命名一致
    try:
        with zipfile.ZipFile(str(KU9_APK_COPY), 'r') as zf:
            icon_targets = ["app_banner.png", "exit_image.png", "app_icon.png"]
            bg_targets = ["app_crash_image.png", "app_start_image.png"]
            
            for name in zf.namelist():
                basename = Path(name).name
                if basename in icon_targets:
                    data = zf.read(name)
                    (dp / basename).write_bytes(data)
                if basename in bg_targets:
                    data = zf.read(name)
                    (dp / basename).write_bytes(data)
    except Exception as e:
        logger.warning(f"提取默认图标/背景失败: {e}", category="失败")

    logger.info("默认配置方案已从 APK 初始化", category="其他")


def read_user_config() -> dict:
    """读取默认 user_config.json"""
    init_default_profile()
    uf = KU9_DEFAULT_PROFILE_DIR / "user_config.json"
    if uf.exists():
        try:
            return json.loads(uf.read_text(encoding="utf-8"))
        except Exception:
            pass
    return make_empty_user_config()


def write_user_config(data: dict):
    """写入默认 user_config.json"""
    KU9_DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (KU9_DEFAULT_PROFILE_DIR / "user_config.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_index() -> dict:
    """加载构建信息索引"""
    if KU9_CONFIG_FILE.exists():
        try:
            return json.loads(KU9_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"lastBuildTime": ""}


def save_index(data: dict):
    """保存构建信息"""
    KU9_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    KU9_CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================== API 路由 ===========================

@router.get("/config")
async def get_config():
    """获取默认配置"""
    uc = read_user_config()
    return {
        "addrData5": uc.get("addrData5", {}),
        "addrData3": uc.get("addrData3", {}),
        "addrData4": uc.get("addrData4", {}),
        "selectVals": uc.get("selectVals", {}),
        "switches": uc.get("switches", {}),
        "appName": uc.get("appName", ""),
        "lastBuildTime": load_index().get("lastBuildTime", ""),
    }


@router.get("/preview/{file_type}")
async def preview_file(file_type: str):
    """获取默认方案图标/背景预览图"""
    if file_type not in ("icon", "bg"):
        raise HTTPException(status_code=400, detail="type must be icon or bg")
    pdir = KU9_DEFAULT_PROFILE_DIR
    filename = "app_banner.png" if file_type == "icon" else "app_start_image.png"
    file_path = pdir / filename
    if not file_path.exists():
        # 兼容旧文件名：实际上传保存的多个文件名
        alt_names = {"icon": ["exit_image.png", "app_icon.png"], "bg": ["app_crash_image.png"]}
        for alt in alt_names.get(file_type, []):
            alt_path = pdir / alt
            if alt_path.exists():
                file_path = alt_path
                break
        else:
            raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="image/png"
    )


@router.post("/config")
async def save_config_api(data: dict = Body(...)):
    """保存配置（索引 + 当前方案 user_config）"""
    uc = read_user_config()
    for key in ["addrData5", "addrData3", "addrData4", "selectVals", "switches", "appName"]:
        if key in data:
            uc[key] = data[key]
    write_user_config(uc)

    return {"ok": True}


@router.post("/upload/icon")
async def upload_icon(file: UploadFile = File(...)):
    """上传应用图标"""
    pdir = KU9_DEFAULT_PROFILE_DIR
    content = await file.read()
    
    # 保存为各目标文件名
    icon_targets = ["app_banner.png", "exit_image.png", "app_icon.png"]
    for name in icon_targets:
        (pdir / name).write_bytes(content)
    
    logger.info(f"Ku9 图标已上传 ({len(content)} bytes)", category="其他")
    return {"ok": True}


@router.post("/upload/bg")
async def upload_bg(file: UploadFile = File(...)):
    """上传应用背景"""
    pdir = KU9_DEFAULT_PROFILE_DIR
    content = await file.read()
    
    # 保存为各目标文件名
    bg_targets = ["app_crash_image.png", "app_start_image.png"]
    for name in bg_targets:
        (pdir / name).write_bytes(content)
    
    logger.info(f"Ku9 背景已上传 ({len(content)} bytes)", category="其他")
    return {"ok": True}


@router.get("/upload/status")
async def upload_status():
    """获取上传文件状态"""
    pdir = KU9_DEFAULT_PROFILE_DIR
    return {
        "hasIcon": (pdir / "app_banner.png").exists(),
        "hasBg": (pdir / "app_start_image.png").exists(),
        "iconName": "app_banner.png",
        "bgName": "app_start_image.png",
    }


def ensure_apk_bidirectional():
    """双向确保 APK 副本存在"""
    src = KU9_APK_SOURCE
    dst = KU9_APK_COPY
    if src and src.exists() and not dst.exists():
        shutil.copy2(str(src), str(dst))
        logger.info("APK 源 → 副本", category="其他")
    if src and dst.exists() and not src.exists():
        shutil.copy2(str(dst), str(src))
        logger.info("APK 副本 → 源", category="其他")


@router.post("/build")
async def build_apk(data: dict = Body(...)):
    """构建 APK：反编译 → 替换配置/图标/名称 → 重打包 → 签名"""
    pdir = KU9_DEFAULT_PROFILE_DIR
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 用前端提交的配置覆盖到磁盘，保证 read_user_config() 读到最新（Bug 1 修复：build 不读前端提交的配置）
    uc_cur = read_user_config()
    for key in ["addrData5", "addrData3", "addrData4", "selectVals", "switches", "appName"]:
        if key in data and data[key]:
            uc_cur[key] = data[key]
    write_user_config(uc_cur)

    # 读取配置中的 appName
    uc = read_user_config()
    app_name = uc.get("appName", "IPTV")
    
    # 生成编号：输出目录中 .apk 文件数量 + 1
    build_num = 1
    if KU9_OUTPUT_DIR.exists():
        existing = [f for f in KU9_OUTPUT_DIR.iterdir() if f.suffix == ".apk"]
        build_num = len(existing) + 1
    else:
        KU9_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    download_label = "小布的IPTV"
    filename = f"{download_label}_{timestamp}_{build_num}.apk"
    output_path = KU9_OUTPUT_DIR / filename

    # 1. 双向确保 APK 存在
    ensure_apk_bidirectional()
    if not KU9_APK_COPY.exists():
        return {"ok": False, "error": "APK 基座文件不存在"}

    work_apk = KU9_OUTPUT_DIR / f"work_{filename}"
    shutil.copy2(str(KU9_APK_COPY), str(work_apk))

    work_dir = KU9_OUTPUT_DIR / f"work_{timestamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 2. 反编译
        apktool = shutil.which("apktool")
        if not apktool:
            raise RuntimeError("apktool 未安装")

        result = subprocess.run(
            [apktool, "d", "-f", "-o", str(work_dir / "decoded"), str(work_apk)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"apktool 反编译失败: {result.stderr[:500]}")

        decoded = work_dir / "decoded"

        # 3. 替换 assets/Configuration.json
        assets_dir = decoded / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        # 从 user_config 数据生成 Configuration.json
        uc = read_user_config()
        conf_data = {}
        # 如果有 Configuration.json 基础，先加载
        cfg_file = pdir / "Configuration.json"
        if cfg_file.exists():
            try:
                conf_data = json.loads(cfg_file.read_text(encoding="utf-8"))
            except:
                pass
        if "Configuration" not in conf_data:
            conf_data["Configuration"] = {}
        conf = conf_data["Configuration"]

        # 地址填写 → Configuration.json 字符串格式
        addr5_map = {
            "预置 M3U": "LIVE_URLS",
            "预置 EPG": "EPG_URLS",
            "预置 JSON": "LIVE_JSONS",
            "预置头部请求": "HEADERS_URLS",
            "预置用户代理": "USER_AGENTS",
        }
        for cn_name, json_key in addr5_map.items():
            arr = uc.get("addrData5", {}).get(cn_name, [])
            if arr:
                parts = []
                for e in arr:
                    v = e.get("val", "")
                    n = e.get("name", "")
                    if v:
                        if n:
                            parts.append(f"{v}${n}")
                        else:
                            parts.append(v)
                conf[json_key] = " || ".join(parts)
            else:
                conf[json_key] = None

        # addrData3 字段
        addr3_map = {
            "启动图片路径": "loginPng",
            "退出图片路径": "exitPng",
            "启动数据提示文字": "loginText",
            "启动图停留时间(ms)": "loginPng_Dwell_time",
            "右上角时间格式": "time_format",
        }
        for cn_name, json_key in addr3_map.items():
            v = uc.get("addrData3", {}).get(cn_name, "")
            if v:
                conf[json_key] = v

        # selectVals (选项文本 → 数字值)
        select_reverse_map = {
            "PLAY_SCALE": {"默认":0,"16:9":1,"4:3":2,"填充":3,"原始":4,"裁剪":6},
            "PLAY_TYPE": {"系统解码":0,"IJK硬解":1,"IJK软解":2,"EXO硬解":3,"EXO软解":4},
            "LIVE_CONNECT_TIMEOUT": {"5s":0,"10s":1,"15s":2,"20s":3,"25s":4,"30s":5},
            "EPGTYPE_SELECT": {"百川/DIYP":0,"超级TV":1,"XmlTV":2},
            "EPGCACHE_SELECT": {"关闭缓存":0,"每天2点":1,"每天4点":2,"每天6点":3,"每天8点":4,"每天10点":5,"每天12点":6,"每天14点":7,"每天16点":8,"每天18点":9,"每天20点":10,"每天22点":11},
            "TIME_ZONE_SELECT": {},
            "EPG_CACHE_PATH_SET": {"外部储存":True,"内部储存":False},
            "PLAY_RENDER": {"texture":0,"surface":1},
            "RTSP_TCP_SELECT": {"TCP":0,"UDP":1},
            "DOH_URL": {"关闭":0,"腾讯":1,"阿里":2,"360":3,"Google":4,"AdGuard":5,"Quad9":6},
            "PLAY_BACK_TYPE": {"重新加载url更新进度":0,"播放器seekTo更新进度":1},
            "TIME_SHIFT_MODE": {"时移结束自动刷新继续播放":True,"回到直播":False},
            "RECONNECT_INDEX": {"关闭重连":0,"1秒后重连":1,"3秒后重连":2,"5秒后重连":3,"10秒后重连":4,"20秒后重连":5},
            "GROUP_PARS_SET_SELECT": {"传统分组":0,"列表分组":1,"二级分组模式1":2,"二级分组模式2":3},
            "EPG_SHOW_TYPE_SELECT": {"折叠展示":0,"一直展示":1},
            "M3U_LOGO_PRIORITY": {"m3u优先":True,"默认优先级":False},
            "ICON_INITIAL_SET": {"显示自定义背景频道首字":True,"显示默认图片":False},
            "RESOLUTION_MODE_SELECT": {"分辨率缩写":0,"分辨率数字x数字":1},
            "NAVIGATION_SELECT": {"滑动显示":0,"一直显示":1,"触摸显示":2},
            "THEME_SELECT": {"0":0,"1":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9},
            "BOOTRECEIVER_SET_SELECT": {"开机获取IP后自启":True,"开机直接自启":False},
        }
        # 补全 TIME_ZONE_SELECT 映射
        tz_opts = ["默认时区"]
        for i in range(1, 13): tz_opts.append(f"-{i}小时")
        tz_opts.append("+0小时")
        for i in range(1, 13): tz_opts.append(f"+{i}小时")
        select_reverse_map["TIME_ZONE_SELECT"] = {v: k for k, v in enumerate(tz_opts)}

        sv = uc.get("selectVals", {})
        for json_key, rmap in select_reverse_map.items():
            txt_val = sv.get(json_key)
            if txt_val and txt_val in rmap:
                conf[json_key] = rmap[txt_val]

        # switches
        sw = uc.get("switches", {})
        for k, v in sw.items():
            conf[k] = v

        # addrData4 → SHORTCUTS_MENU_SELECT（勾选逗号拼接）
        addr4 = uc.get("addrData4", {})
        selected = [name for name, checked in addr4.items() if checked]
        conf["SHORTCUTS_MENU_SELECT"] = ",".join(selected)

        # 应用名称
        app_name = uc.get("appName", "IPTV")

        # 写回 Configuration.json
        (assets_dir / "Configuration.json").write_text(
            json.dumps(conf_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 4. 替换图标/背景
        icon_src = pdir / "app_banner.png"
        bg_src = pdir / "app_start_image.png"
        res_dir = decoded / "res"

        # 图标：mipmap 下只有 app_icon.png，drawable-hdpi 下放其他
        mipmap_dir = res_dir / "mipmap"
        hdpi_dir = res_dir / "drawable-hdpi"

        if icon_src.exists():
            for t in ["app_banner.png", "exit_image.png"]:
                tp = hdpi_dir / t
                if tp.exists():
                    shutil.copy2(str(icon_src), str(tp))
                    logger.info(f"图标已替换: {tp.relative_to(decoded)}", category="其他")
            tp = mipmap_dir / "app_icon.png"
            if tp.exists():
                shutil.copy2(str(icon_src), str(tp))
                logger.info(f"图标已替换: {tp.relative_to(decoded)}", category="其他")

        if bg_src.exists():
            for t in ["app_crash_image.png", "app_start_image.png"]:
                tp = hdpi_dir / t
                if tp.exists():
                    shutil.copy2(str(bg_src), str(tp))
                    logger.info(f"背景已替换: {tp.relative_to(decoded)}", category="其他")

        # 5. 修改应用名称（改 arsc 或 strings.xml）
        strings_paths = [
            decoded / "res" / "values" / "strings.xml",
            decoded / "res" / "values-zh" / "strings.xml",
        ]
        name_changed = False
        for sp in strings_paths:
            if sp.exists():
                content = sp.read_text(encoding="utf-8")
                if '<string name="app_name">' in content:
                    import re
                    content = re.sub(
                        r'<string name="app_name">.*?</string>',
                        f'<string name="app_name">{app_name}</string>',
                        content
                    )
                    sp.write_text(content, encoding="utf-8")
                    name_changed = True
                    logger.info(f"应用名称已改为: {app_name}", category="其他")

        if not name_changed:
            logger.warning("未找到 app_name 字符串，尝试 apktool 改 arsc", category="其他")

        # 6. 重打包
        rebuild_apk = work_dir / "rebuild.apk"
        result = subprocess.run(
            [apktool, "b", "-o", str(rebuild_apk), str(decoded)],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            raise RuntimeError(f"apktool 重打包失败: {result.stderr[:500]}")

        # 7. 签名
        keystore = work_dir / "debug.keystore"
        if not keystore.exists():
            keytool = shutil.which("keytool")
            if keytool:
                subprocess.run([
                    keytool, "-genkey", "-v", "-keystore", str(keystore),
                    "-alias", "ku9", "-keyalg", "RSA", "-keysize", "2048",
                    "-validity", "3650",
                    "-storepass", "123456", "-keypass", "123456",
                    "-dname", "CN=Ku9, OU=Ku9, O=Ku9, L=Unknown, ST=Unknown, C=CN"
                ], capture_output=True, timeout=30)

        if keystore.exists():
            jarsigner = shutil.which("jarsigner")
            if jarsigner:
                subprocess.run([
                    jarsigner, "-sigalg", "SHA1withRSA", "-digestalg", "SHA1",
                    "-keystore", str(keystore),
                    "-storepass", "123456", "-keypass", "123456",
                    str(rebuild_apk), "ku9"
                ], capture_output=True, timeout=60)

        # 8. 复制到输出目录
        shutil.copy2(str(rebuild_apk), str(output_path))
        logger.info(f"APK 构建完成: {filename}", category="其他")

        # 9. 保存构建配置
        config_path = KU9_OUTPUT_DIR / f"{filename}.json"
        config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 更新构建时间
        idx = load_index()
        idx["lastBuildTime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_index(idx)

        return {"ok": True, "file": filename}

    except Exception as e:
        logger.error(f"APK 构建失败: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        if work_apk.exists():
            work_apk.unlink()
        if work_dir.exists():
            shutil.rmtree(str(work_dir), ignore_errors=True)


@router.get("/downloads")
async def list_downloads():
    """已构建的文件列表"""
    files = []
    if KU9_OUTPUT_DIR.exists():
        for f in sorted(KU9_OUTPUT_DIR.iterdir(),
                         key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix != ".apk":
                continue
            size = f.stat().st_size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / 1024 / 1024:.1f} MB"
            files.append({
                "id": f.name,
                "filename": f.name,
                "time": datetime.datetime.fromtimestamp(
                    f.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "size": size_str,
            })
    return files[:50]


@router.get("/download/")
async def download_file(file: str = ""):
    """下载文件"""
    file_path = KU9_OUTPUT_DIR / file
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path=str(file_path),
        filename=file,
        media_type="application/vnd.android.package-archive"
    )


@router.delete("/download/")
async def delete_file(file: str = ""):
    """删除文件"""
    file_path = KU9_OUTPUT_DIR / file
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="文件不存在")
