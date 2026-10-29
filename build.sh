#!/bin/bash
# ============================================================================
# iptv-server 一键构建 / 启动
# 适用：群晖 DSM、飞牛 fnOS、威联通、任意 Linux / macOS
#
#   bash build.sh            # 构建 + 启动 + 等健康检查
#   bash build.sh build      # 只构建镜像
#   bash build.sh restart    # 重启容器
#   bash build.sh logs       # 跟踪日志
#   bash build.sh down       # 停止并删除容器（data 目录保留）
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

ACTION="${1:-up}"

say() { printf '%s\n' "$*"; }

# ---------- 0. 环境检查 ----------
if ! command -v docker >/dev/null 2>&1; then
    say "❌ 未找到 docker，请先安装 Docker（群晖：套件中心 → Container Manager）"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    say "❌ Docker 守护进程未运行，或当前用户没有权限"
    say "   群晖/飞牛请先   sudo -i   再执行本脚本"
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    say "❌ 未找到 docker compose"
    say "   群晖 DSM 7.0 请在套件中心安装 Docker + docker-compose"
    exit 1
fi
say "✅ Docker 就绪：${DC[*]}"

# ---------- 1. .env（没有就用示例生成一份） ----------
if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    say "ℹ️  已根据 .env.example 生成 .env（可改端口 / 密码 / 数据目录）"
fi

# ---------- 2. 换行符统一为 LF ----------
# Windows 下解压/编辑过的 .env 会带 CR，导致 DATA_ROOT 变成 "./data\r"
sed -i 's/\r$//' Dockerfile docker-compose.yml nginx.conf start.sh build.sh \
    requirements.txt .env.example 2>/dev/null || true
if [ -f .env ]; then
    sed -i 's/\r$//' .env 2>/dev/null || true
fi
if [ -d app ]; then
    find app static -type f \( -name '*.py' -o -name '*.js' -o -name '*.html' -o -name '*.css' -o -name '*.sh' \) \
        -exec sed -i 's/\r$//' {} \; 2>/dev/null || true
fi

# ---------- 3. 读取 .env ----------
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
fi

WEB_PORT="${WEB_PORT:-5673}"
DATA_ROOT="${DATA_ROOT:-./data}"
SHARE_DIR="${SHARE_DIR:-./data/share}"
TOOLS_DIR="${TOOLS_DIR:-${DATA_ROOT}/tools}"

# ---------- 4. 数据目录 ----------
mkdir -p \
    "${DATA_ROOT}/json" \
    "${DATA_ROOT}/xml" \
    "${DATA_ROOT}/m3u" \
    "${DATA_ROOT}/icons" \
    "${DATA_ROOT}/logs" \
    "${DATA_ROOT}/config" \
    "${SHARE_DIR}" \
    "${TOOLS_DIR}" \
    "${DATA_ROOT}/config/epg_cache"
say "📁 数据目录：$(cd "${DATA_ROOT}" && pwd)"

# ---------- 5. 等健康检查 ----------
wait_healthy() {
    local cid status
    cid="$("${DC[@]}" ps -q iptv-server 2>/dev/null | head -n1 || true)"
    if [ -z "${cid}" ]; then
        return 0
    fi
    for _ in $(seq 1 60); do
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || echo unknown)"
        case "${status}" in
            healthy) say "✅ 健康检查通过"; return 0 ;;
            unhealthy|exited|dead)
                say "❌ 容器状态：${status}"
                "${DC[@]}" logs --tail=80 iptv-server || true
                return 1 ;;
        esac
        sleep 2
    done
    say "⚠️  120 秒内未变为 healthy，请查看日志：${DC[*]} logs -f"
    return 0
}

# ---------- 6. 执行 ----------
case "${ACTION}" in
    build)
        "${DC[@]}" build
        ;;
    up|start)
        "${DC[@]}" up -d --build
        wait_healthy
        IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
        if [ -z "${IP}" ]; then
            IP="<NAS_IP>"
        fi
        say ""
        say "============================================"
        say "  🎉 已启动"
        say "============================================"
        say "  访问：http://${IP}:${WEB_PORT}"
        say "  账号：${AUTH_USERNAME:-666} / ${AUTH_PASSWORD:-666}"
        say "  数据：$(cd "${DATA_ROOT}" && pwd)"
        say ""
        say "  查看日志：${DC[*]} logs -f"
        ;;
    restart)
        "${DC[@]}" restart
        wait_healthy
        ;;
    logs)
        "${DC[@]}" logs -f --tail=100
        ;;
    down|stop)
        "${DC[@]}" down
        ;;
    *)
        say "用法: bash build.sh [up|build|restart|logs|down]"
        exit 1
        ;;
esac
