"""端口映射 API - 四层端口转发（nginx stream）"""
import os as _os
import subprocess as _subprocess

from fastapi import APIRouter, HTTPException
from fastapi import Body

from . import config

router = APIRouter()

NGINX_CONF = "/etc/nginx/nginx.conf"
STREAM_CONF_DIR = "/etc/nginx/stream.d"
STREAM_CONF_FILE = f"{STREAM_CONF_DIR}/port_maps.conf"
NGINX_RELOAD_CMD = ["nginx", "-s", "reload"]

# 容器首次启动时默认添加的端口映射
_DEFAULT_PORT_MAPS = []


def _ensure_stream_dir():
    """确保 stream 配置目录存在"""
    _os.makedirs(STREAM_CONF_DIR, exist_ok=True)


def _generate_stream_config(maps: list) -> str:
    """根据端口映射规则生成 nginx stream 配置（只包含启用的规则）"""
    lines = ["# 端口映射规则（由 WEB 界面管理，请勿手动修改）"]
    
    added = 0
    for m in maps:
        if not m.get("enabled", True):
            continue
        local_port = m.get("local_port")
        target = m.get("target", "")
        if not local_port or not target:
            continue
        if ":" not in target:
            continue
        
        host, port = target.rsplit(":", 1)
        if not port.isdigit():
            continue
        
        lines.append(f"""
server {{
    listen 0.0.0.0:{local_port};
    proxy_pass {host}:{port};
}}""")
        added += 1
    
    if added == 0:
        lines.append("# (无端口映射规则)")
        lines.append("server {")
        lines.append("    listen 127.0.0.1:1;")
        lines.append("    proxy_pass 127.0.0.1:1;")
        lines.append("}")
    
    return "\n".join(lines) + "\n"


def _write_and_reload(maps: list) -> bool:
    """写入 stream 配置并 reload nginx"""
    _ensure_stream_dir()
    config_text = _generate_stream_config(maps)
    
    with open(STREAM_CONF_FILE, "w") as f:
        f.write(config_text)
    
    try:
        _subprocess.run(NGINX_RELOAD_CMD, check=True, capture_output=True, timeout=5)
        return True
    except _subprocess.SubprocessError:
        return False


def get_running_ports() -> set:
    """获取当前配置中定义的端口"""
    ports = set()
    maps = config.get_port_maps()
    for m in maps:
        lp = m.get("local_port")
        if lp:
            ports.add(lp)
    return ports


# ==================== API 路由 ====================


@router.get("/api/port-maps")
async def get_port_maps():
    """获取所有端口映射规则及运行状态"""
    maps = config.get_port_maps()
    if not maps:
        maps = list(_DEFAULT_PORT_MAPS)
        config.save_port_maps(maps)
    running_ports = get_running_ports()
    for m in maps:
        enabled = m.get("enabled", True)
        m["running"] = enabled and (m.get("local_port") in running_ports)
    return {"maps": maps}


@router.post("/api/port-maps")
async def add_port_map(data: dict = Body(...)):
    """添加端口映射规则"""
    maps = config.get_port_maps()

    raw_port = data.get("local_port", "").strip()
    target = data.get("target", "").strip()

    if not raw_port or not target:
        raise HTTPException(400, "请填写完整")

    try:
        local_port = int(raw_port)
    except ValueError:
        raise HTTPException(400, "端口必须是数字")

    if local_port < 1 or local_port > 65535:
        raise HTTPException(400, "端口范围 1-65535")

    # 检查端口冲突
    for m in maps:
        if m.get("local_port") == local_port:
            raise HTTPException(400, f"端口 {local_port} 已被占用")

    remark = (data.get("remark") or "").strip()
    new_map = {"local_port": local_port, "target": target, "remark": remark}
    maps.append(new_map)
    config.save_port_maps(maps)
    _write_and_reload(maps)
    return {"success": True, "applied": True}


@router.put("/api/port-maps/{index}")
async def update_port_map(index: int, data: dict = Body(...)):
    """更新端口映射规则"""
    maps = config.get_port_maps()
    if index < 0 or index >= len(maps):
        raise HTTPException(404, "映射不存在")

    m = maps[index]
    raw_port = data.get("local_port")
    target = data.get("target")
    remark = data.get("remark")

    if raw_port is not None:
        try:
            local_port = int(raw_port)
        except ValueError:
            raise HTTPException(400, "端口必须是数字")
        m["local_port"] = local_port

    if target is not None:
        m["target"] = target.strip()

    if remark is not None:
        m["remark"] = remark.strip()

    config.save_port_maps(maps)
    _write_and_reload(maps)
    return {"success": True}


@router.delete("/api/port-maps/{index}")
async def delete_port_map(index: int):
    """删除端口映射规则"""
    maps = config.get_port_maps()
    if index < 0 or index >= len(maps):
        raise HTTPException(404, "映射不存在")

    maps.pop(index)
    config.save_port_maps(maps)
    _write_and_reload(maps)
    return {"success": True}


@router.post("/api/port-maps/{index}/start")
async def start_port_map(index: int):
    """启用一条端口映射"""
    maps = config.get_port_maps()
    if index < 0 or index >= len(maps):
        raise HTTPException(404, "映射不存在")
    maps[index]["enabled"] = True
    config.save_port_maps(maps)
    ok = _write_and_reload(maps)
    if not ok:
        raise HTTPException(500, "启动失败（nginx reload 失败）")
    return {"success": True, "running": True}


@router.post("/api/port-maps/{index}/stop")
async def stop_port_map(index: int):
    """停用一条端口映射"""
    maps = config.get_port_maps()
    if index < 0 or index >= len(maps):
        raise HTTPException(404, "映射不存在")
    maps[index]["enabled"] = False
    config.save_port_maps(maps)
    ok = _write_and_reload(maps)
    if not ok:
        raise HTTPException(500, "停止失败（nginx reload 失败）")
    return {"success": True, "running": False}
