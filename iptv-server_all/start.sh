#!/bin/bash
# ============================================================================
# iptv-server 启动脚本 · nginx(80) → uvicorn(127.0.0.1:8001)
# 由 tini 作为 PID 1 启动，docker stop 的 SIGTERM 会转发到这里
# ============================================================================
set -euo pipefail

# 数据根目录（与 docker-compose 挂载一致；默认 /app/data）
DATA_ROOT="${IPTV_DATA_DIR:-/app/data}"

echo "=== iptv-server 启动 ==="
echo "时间: $(date)"
echo "数据目录: ${DATA_ROOT}"

# ---------- 1. 数据目录（含 icons / share，与 compose 挂载点一一对应） ----------
mkdir -p \
    "${DATA_ROOT}/json" \
    "${DATA_ROOT}/xml" \
    "${DATA_ROOT}/m3u" \
    "${DATA_ROOT}/icons" \
    "${DATA_ROOT}/logs" \
    "${DATA_ROOT}/config" \
    "${DATA_ROOT}/config/epg_cache" \
    "${DATA_ROOT}/tools" \
    "${DATA_ROOT}/share"

# 清掉上次异常退出残留的 pid（容器重启时容器内文件系统会保留）
rm -f /run/nginx.pid /var/run/nginx.pid 2>/dev/null || true

# ---------- 2. 先校验 nginx 配置 ----------
# 配置写错就直接失败，避免出现“容器在跑但 80 端口不可用”的假存活
echo "[检查] nginx -t ..."
if ! nginx -t; then
    echo "[错误] nginx 配置有误，启动中止"
    exit 1
fi

# ---------- 3. 启动 uvicorn（只监听本机回环） ----------
echo "[启动] uvicorn 127.0.0.1:8001 ..."
uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8001 \
    --log-level info \
    --proxy-headers \
    --forwarded-allow-ips 127.0.0.1 &
UVICORN_PID=$!

# 等端口真正可用（最长 30 秒），避免 nginx 先起来抢跑
READY=0
for _ in $(seq 1 60); do
    if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
        echo "[错误] uvicorn 进程已退出，启动中止"
        exit 1
    fi
    if (exec 3<>/dev/tcp/127.0.0.1/8001) 2>/dev/null; then
        READY=1
        break
    fi
    sleep 0.5
done
if [ "${READY}" = "1" ]; then
    echo "[就绪] uvicorn 端口已监听"
else
    echo "[警告] uvicorn 30 秒内未就绪，仍继续启动 nginx（就绪前会返回 502）"
fi

# ---------- 4. 启动 nginx ----------
echo "[启动] nginx 0.0.0.0:80 ..."
nginx -g "daemon off;" &
NGINX_PID=$!

sleep 1
if ! kill -0 "${NGINX_PID}" 2>/dev/null; then
    echo "[错误] nginx 启动失败"
    kill -TERM "${UVICORN_PID}" 2>/dev/null || true
    exit 1
fi

echo "[启动] 服务已就绪: nginx:80 → uvicorn:8001"
echo "[启动] PID: nginx=${NGINX_PID} uvicorn=${UVICORN_PID}"

# ---------- 5. 优雅退出（docker stop） ----------
shutdown() {
    echo "[停止] 收到退出信号，正在关闭 ..."
    kill -TERM "${NGINX_PID}" 2>/dev/null || true
    kill -TERM "${UVICORN_PID}" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT QUIT

# ---------- 6. 任一进程退出 → 整体退出，交给 restart 策略拉起 ----------
# 原来的裸 wait 会一直等到所有子进程结束，uvicorn 崩了容器还是 "Up"（全是 502）
set +e
wait -n
CODE=$?
set -e

if [ "${CODE}" -eq 0 ]; then
    CODE=1
fi
echo "[警告] 子进程退出 (code=${CODE})，停止容器 ..."
kill -TERM "${NGINX_PID}" "${UVICORN_PID}" 2>/dev/null || true
wait 2>/dev/null || true
exit "${CODE}"
