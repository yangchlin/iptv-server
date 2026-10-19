#!/bin/bash
# ============================================================================
# iptv-server - 一键构建和运行脚本
# 注意：此文件使用 Linux 换行符（LF），在 Windows 下编辑请确保换行格式为 LF
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  IPTV EPG 智能管理系统 - 构建脚本"
echo "============================================"
echo ""

# 检查 Docker
if ! command -v docker &>/dev/null; then
    echo "❌ 错误: 未找到 Docker，请先安装 Docker"
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    echo "❌ 错误: Docker 守护进程未运行或当前用户无权限"
    echo ""
    echo "解决方法："
    echo "  1. 确保 Docker 已启动"
    echo "  2. 将当前用户加入 docker 组：sudo usermod -aG docker $USER"
    echo "  3. 重新登录后重试"
    exit 1
fi

echo "✅ Docker 已就绪"
echo ""

# 确保数据目录权限
echo "📁 创建数据目录..."
mkdir -p /vol1/1000/Docker/IPTV/{json,xml,m3u,logs,data}
echo "✅ 数据目录已创建: /vol1/1000/Docker/IPTV/"

# 确保换行符为 LF
echo "📝 检查换行符..."
find . -type f \( -name "*.py" -o -name "*.sh" -o -name "*.html" -o -name "*.yml" -o -name "Dockerfile" -o -name "*.txt" \) -exec sed -i 's/\r$//' {} \;
echo "✅ 所有文件已转换为 LF 格式"

# 构建镜像
echo ""
echo "🔨 构建 Docker 镜像..."
docker build -t iptv-server:latest .
echo "✅ 镜像构建完成: iptv-server:latest"

# 提示启动
echo ""
echo "============================================"
echo "  🎉 构建成功！"
echo "============================================"
echo ""
echo "启动方式："
echo ""
echo "  方式一：使用 docker-compose"
echo "    docker compose up -d"
echo ""
echo "  方式二：直接使用 docker run"
echo '    docker run -d \'
echo '      --name iptv-server \'
echo '      -p 5673:80 \'
echo '      -v /vol1/1000/Docker/IPTV/json:/app/data/json \'
echo '      -v /vol1/1000/Docker/IPTV/xml:/app/data/xml \'
echo '      -v /vol1/1000/Docker/IPTV/m3u:/app/data/m3u \'
echo '      -v /vol1/1000/Docker/IPTV/logs:/app/data/logs \'
echo '      -v /vol1/1000/Docker/IPTV/data:/app/data/config \'
echo '      -e TZ=Asia/Shanghai \'
echo '      --restart unless-stopped \'
echo '      iptv-server:latest'
echo ""
echo "启动后访问：http://飞牛IP:5673"
echo ""
