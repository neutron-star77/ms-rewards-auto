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
LAST_FILE="${SCRIPT_DIR}/keepalive.last"
INTERVAL_DAYS=18
if [ -f "$LAST_FILE" ]; then
  last_ts=$(cat "$LAST_FILE" 2>/dev/null)
  now_ts=$(date +%s)
  diff=$(( (now_ts - ${last_ts:-0}) / 86400 ))
  if [ "$diff" -lt "$INTERVAL_DAYS" ]; then
    echo "[$(date)] keepalive: skip, only ${diff}d since last (need >=18d)." >> ${SCRIPT_DIR}/keepalive.log
    exit 0
  fi
fi

DELAY=$((RANDOM % 300))
echo "[$(date)] 保活：随机延迟 ${DELAY}s 后启动..."
sleep $DELAY

"$DOCKER_BIN" run --rm $STATE_VOL $IMAGE python nas_main.py --keepalive >> ${SCRIPT_DIR}/keepalive.log 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  date +%s > "${SCRIPT_DIR}/keepalive.last"
  echo "[$(date)] keepalive: recorded; next run in ~18 days." >> ${SCRIPT_DIR}/keepalive.log
fi

echo "[$(date)] 保活本次运行结束"
# ---- 安装「23:00 img.ink 扩容自检」cron（仅 root 执行一次，幂等）----
# 由 nas_keepalive.sh 在 root 下每次运行时顺带检查；未安装则追加，并置 sentinel 避免重复。
_CHECK_SENT="/share/CACHEDEV1_DATA/Web/gadget/auto_sign/.cron_check_applied"
_CHECK_CRON="0 23 * * * PYTHONPATH=/share/homes/Mars/.local/lib/python3.12/site-packages /share/CACHEDEV1_DATA/.qpkg/Python3/opt/python3/bin/python3 /share/CACHEDEV1_DATA/Web/gadget/auto_sign/sign_expand.py --check >> /share/CACHEDEV1_DATA/Web/gadget/auto_sign/check.log 2>&1"
if [ "$(id -u)" = "0" ] && [ ! -f "$_CHECK_SENT" ]; then
  _CT="/etc/config/crontab"
  if ! grep -qF "$_CHECK_CRON" "$_CT" 2>/dev/null; then
    echo "$_CHECK_CRON" >> "$_CT"
    if [ -x /etc/init.d/crond.sh ]; then
      /etc/init.d/crond.sh restart >> "$SCRIPT_DIR/keepalive.log" 2>&1
    else
      killall -HUP crond 2>/dev/null || true
    fi
    echo "[$(date)] keepalive: installed 23:00 sign_expand.py --check cron" >> "$SCRIPT_DIR/keepalive.log"
  fi
  touch "$_CHECK_SENT"
fi
