# ============================================================================
# iptv-server Dockerfile
# 注意：此文件使用了 Linux 换行符（LF），切勿在 Windows 下保存为 CRLF！
# ============================================================================

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    nginx \
    libnginx-mod-stream \
    default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# 安装 apktool
RUN wget -q https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar -O /usr/local/bin/apktool.jar \
    && chmod +x /usr/local/bin/apktool.jar \
    && echo '#!/bin/bash\nexec java -jar /usr/local/bin/apktool.jar "$@"' > /usr/local/bin/apktool \
    && chmod +x /usr/local/bin/apktool

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY static/ ./static/
COPY start.sh .

# 创建数据目录（运行时通过 volume 挂载）
RUN mkdir -p /app/data/json /app/data/xml /app/data/m3u /app/data/logs /app/data/config

# 设置执行权限
RUN chmod +x start.sh

# 添加 nginx 配置
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
EXPOSE 8888

CMD ["./start.sh"]
