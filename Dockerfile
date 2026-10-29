# ============================================================================
# iptv-server Dockerfile
# 注意：此文件使用 Linux 换行符（LF），切勿在 Windows 下保存为 CRLF！
#
# 通用构建，不绑定任何 NAS 的固定路径：
#   docker build -t iptv-server:latest .
#   # 海外网络（不走国内镜像）：
#   docker build --build-arg APT_MIRROR=deb.debian.org \
#                --build-arg PIP_INDEX_URL=https://pypi.org/simple \
#                --build-arg PIP_TRUSTED_HOST= \
#                -t iptv-server:latest .
# ============================================================================

FROM python:3.11-slim-bookworm

# ---- 可覆盖的镜像源（默认国内加速）----
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ==========================================
# 1. 修复 apt 源
#    删除 Debian 12 的 deb822 源（debian.sources），改写为单行源。
#    路径结构对 aliyun / 清华 / deb.debian.org 都通用，只保留 main。
# ==========================================
RUN rm -f /etc/apt/sources.list.d/* \
    && . /etc/os-release \
    && echo "deb https://${APT_MIRROR}/debian/ ${VERSION_CODENAME} main" > /etc/apt/sources.list \
    && echo "deb https://${APT_MIRROR}/debian-security/ ${VERSION_CODENAME}-security main" >> /etc/apt/sources.list

# ==========================================
# 2. 系统依赖
#    tini  : 作为 PID 1 转发 SIGTERM，docker stop 时能优雅退出
#    nginx : 反向代理（80 → 127.0.0.1:8001）
#    nano  : 可选，容器内应急改配置（约 1MB，不需要可删掉）
# ==========================================
RUN apt-get update \
        -o Acquire::http::Timeout=15 \
        -o Acquire::https::Timeout=15 \
        -o Acquire::Retries=3 \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        nginx \
        tini \
        nano \
    && rm -rf /var/lib/apt/lists/*

# ==========================================
# 3. pip 源（PIP_TRUSTED_HOST 为空时跳过，兼容官方源）
# ==========================================
RUN pip config set global.index-url "${PIP_INDEX_URL}" \
    && if [ -n "${PIP_TRUSTED_HOST}" ]; then \
         pip config set global.trusted-host "${PIP_TRUSTED_HOST}"; \
       fi

# ==========================================
# 4. Python 依赖（只用 wheel，构建阶段不需要编译器）
#    不使用 BuildKit 的 --mount 缓存，保证老版本 Docker / 群晖也能构建。
#    镜像层缓存已由 requirements.txt 的内容决定，改代码不会重新下载依赖。
# ==========================================
COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

# ==========================================
# 5. 应用代码
# ==========================================
COPY app/ ./app/
COPY static/ ./static/
COPY start.sh ./
RUN chmod +x start.sh

# ==========================================
# 6. nginx 配置 + 数据目录
#    目录与 docker-compose 的挂载点一一对应（含 icons / share）
# ==========================================
COPY nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /app/data/json /app/data/xml /app/data/m3u /app/data/icons \
             /app/data/logs /app/data/config /app/data/share \
    && nginx -t

# 80  = nginx（Web 界面 / EPG / M3U）
# 554 = 中兴平台内置 RTSP 回看代理（默认关闭，在「电信EPG → 中兴平台」里启用）
EXPOSE 80 554

# 健康检查走 nginx → uvicorn 全链路（favicon 是免认证的静态文件）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1/static/favicon.svg', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./start.sh"]
