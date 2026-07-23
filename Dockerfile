FROM python:3.11-slim

# Chromium 在 Debian/Ubuntu 上需要的系统库（Playwright 官方依赖）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libxshmfence1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装依赖（注意：与 Windows 端的 playwright 版本一致）
COPY requirements_nas.txt .
RUN pip install --no-cache-dir -r requirements_nas.txt

# 安装 Chromium 浏览器内核（NAS 用自带 Chromium，不再依赖 Windows Edge）
RUN playwright install chromium

# 只复制运行所需的脚本（登录态通过挂载的数据卷提供，勿打进镜像）
# rewards_earn.py 为新版入口；nas_main.py 保留作兼容（旧部署 CMD 用它）
COPY rewards_earn.py .
COPY nas_main.py .

# NAS / 无头模式开关（也可在 docker run 时通过 -e 覆盖）
ENV REWARDS_NAS=1
ENV REWARDS_PROFILE=/app/data/profile
ENV REWARDS_STORAGE=/app/data/storage_state.json

# 数据卷：宿主机 crontab 把脚本目录挂载到 /app，
# 则 /app/data 对应宿主机 <脚本目录>/data，用于持久化 profile、storage_state、运行日志。
# 每天由宿主机 cron 通过 nas_cron.sh 触发，跑完自动退出。
CMD ["python", "rewards_earn.py"]
