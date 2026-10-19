#!/bin/bash
# ============================================================================
# iptv-server 启动脚本 - nginx + uvicorn
# ============================================================================
set -e

echo "=== iptv-server 启动 ==="
echo "时间: $(date)"

# 确保数据目录存在
mkdir -p /app/data/json /app/data/xml /app/data/m3u /app/data/logs /app/data/config

# 确保 nginx stream 配置目录存在（端口映射用）
mkdir -p /etc/nginx/stream.d
# 生成初始 stream 配置（含空 server 块避免 nginx 报错）
cat > /etc/nginx/stream.d/port_maps.conf << 'NGXEOF'
# 端口映射规则（由 WEB 界面管理）
server {
    listen 127.0.0.1:1;
    proxy_pass 127.0.0.1:1;
}
NGXEOF

# 启动 uvicorn（监听内部端口 8001）
echo "[启动] 启动 uvicorn (端口 8001)..."
uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level info --proxy-headers --forwarded-allow-ips '*' &
UVICORN_PID=$!

# 等待 uvicorn 就绪
sleep 2

# 启动 nginx（监听端口 80，反向代理到 uvicorn）
echo "[启动] 启动 nginx (端口 80)..."
nginx -g "daemon off;" &
NGINX_PID=$!

echo "[启动] 服务已启动 (nginx:80 → uvicorn:8001)"
echo "[启动] PID: nginx=$NGINX_PID uvicorn=$UVICORN_PID"

# 等待任意进程退出
wait
