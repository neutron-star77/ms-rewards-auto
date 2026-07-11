#!/bin/sh
# NAS 定时触发脚本（宿主机 cron 调用此文件）
# 用法：把下面一行加入 crontab（crontab -e）
#   0 9,21 * * * /bin/sh /share/你的卷/microsoft/nas_cron.sh >> /share/你的卷/microsoft/nas.log 2>&1
#
# 若用 Docker（推荐）：先 build 镜像
#   docker build -t ms-rewards /share/你的卷/microsoft
# 本脚本每次运行一个一次性容器，跑完自动退出。

SCRIPT_DIR="/share/CACHEDEV1_DATA/Web/gadget/microsoft"   # ← NAS 上实际路径
IMAGE="ms-rewards"
STATE_VOL="-v ${SCRIPT_DIR}:/app"
# cron 环境 PATH 精简，docker 用绝对路径（Container Station 自带）
DOCKER_BIN="/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"

# 随机延迟 0~30 分钟，避免固定时间被检测
DELAY=$((RANDOM % 1800))
echo "[$(date)] 随机延迟 ${DELAY}s 后启动..."
sleep $DELAY

"$DOCKER_BIN" run --rm $STATE_VOL $IMAGE python nas_main.py >> ${SCRIPT_DIR}/nas.log 2>&1
echo "[$(date)] 本次运行结束"
