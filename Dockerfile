FROM python:3.11-slim

# Chromium 在 Debian/Ubuntu 上需要的系统库（Playwright 官方依赖）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libxshmfence1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements_nas.txt .
RUN pip install --no-cache-dir -r requirements_nas.txt
RUN playwright install chromium

COPY . .

# 每天由宿主机 cron 触发，也可在容器内用 cron 触发
CMD ["python", "nas_main.py"]
