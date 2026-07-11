#!/bin/sh
# 微软积分 —— 会话保活脚本（宿主机 cron 调用）
# 作用：定期以「保活模式」跑一次容器（仅验证登录态 + 最小活动，不跑搜索任务），
#       让微软会话保持活跃、顺延登录态有效期，降低强制重新认证（需 2FA）的概率。
# 建议频率：每 15~20 天一次，且与每日任务错开时间。
#   crontab 示例（每 18 天的凌晨 3:30 跑一次，用 day-of-month 近似）：
#   30 3 */18 * * /bin/sh /share/CACHEDEV1_DATA/Web/gadget/microsoft/nas_keepalive.sh >> /share/CACHEDEV1_DATA/Web/gadget/microsoft/keepalive.log 2>&1

SCRIPT_DIR="/share/CACHEDEV1_DATA/Web/gadget/microsoft"   # ← NAS 上实际路径
IMAGE="ms-rewards"
STATE_VOL="-v ${SCRIPT_DIR}:/app"
# cron 环境 PATH 精简，docker 用绝对路径（Container Station 自带）
DOCKER_BIN="/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"

# 保活不需要长随机延迟，小范围抖动即可（0~5 分钟）
DELAY=$((RANDOM % 300))
echo "[$(date)] 保活：随机延迟 ${DELAY}s 后启动..."
sleep $DELAY

"$DOCKER_BIN" run --rm $STATE_VOL $IMAGE python nas_main.py --keepalive >> ${SCRIPT_DIR}/keepalive.log 2>&1
echo "[$(date)] 保活本次运行结束"
