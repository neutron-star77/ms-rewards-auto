#!/bin/sh
# NAS 定时触发脚本：每天两次【随机时刻】触发微软积分脚本
#
# 用法：加入 crontab（crontab -e），让本脚本每 15 分钟被调用一次：
#   */15 * * * * /bin/sh /share/你的卷/microsoft/nas_cron.sh >> /share/你的卷/microsoft/nas.log 2>&1
#
# 机制：本脚本在每天首次运行时随机生成两个触发时刻（HH:MM，去重；小时范围 09:00–21:00，白天活动时段，规避深夜/凌晨），
#       仅当当前时间到达某个「尚未执行」的时刻，才真正拉起容器；其余调用直接退出。
#       这样实现「一天两次随机触发」，避免固定时刻被风控识别。
# 说明：实际触发时刻 = 随机 HH:MM 之后最近的一次 cron 调用（≤15 分钟误差）。
#
# 若用 Docker（推荐）：先 build 镜像
#   docker build -t ms-rewards /share/你的卷/microsoft
# 本脚本每次运行一个一次性容器，跑完自动退出。

SCRIPT_DIR="/share/CACHEDEV1_DATA/Web/gadget/microsoft"   # ← NAS 上实际路径
IMAGE="ms-rewards"
# 脚本目录整体挂载到容器 /app；则 /app/data 对应宿主机的 ${SCRIPT_DIR}/data，
# 用于持久化 profile、storage_state.json 与运行日志（run_*.log）。
STATE_VOL="-v ${SCRIPT_DIR}:/app"
DATA_DIR="${SCRIPT_DIR}/data"
mkdir -p "${DATA_DIR}"
SCHED_FILE="${DATA_DIR}/schedule.txt"
# cron 环境 PATH 精简，docker 用绝对路径（Container Station 自带）
DOCKER_BIN="/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"

# ---- 立即模式：面板「立即执行」时跳过随机时刻，直接跑（随机延迟 ≤1 分钟）----
# 触发方式（任一即可）：
#   1) 传参：  nas_cron.sh --now
#   2) 环境变量： IMMEDIATE=1 nas_cron.sh
IMMEDIATE="${IMMEDIATE:-0}"
if [ "$1" = "--now" ] || [ "$1" = "-n" ]; then
    IMMEDIATE=1
fi

# 真正拉起容器的函数（含随机延迟 + 并发保护）
run_container() {
    if [ "$IMMEDIATE" = "1" ]; then
        # 立即模式（面板「立即执行」）：随机延迟控制在 1 分钟内（0~60s），
        # 既保留一点随机性规避风控，又不会让用户久等。
        DELAY=$((RANDOM % 60))
        echo "[$(date)] 立即模式：随机延迟 ${DELAY}s（≤1 分钟）后启动..."
    else
        # 到点触发：随机延迟 0~30 分钟，避免固定时间被检测
        DELAY=$((RANDOM % 1800))
        echo "[$(date)] 到点触发：随机延迟 ${DELAY}s 后启动..."
    fi
    sleep $DELAY

    # 防止并发：若已有同名容器在跑（手动触发与定时触发重叠时），直接跳过，
    # 避免两个 Chromium 共用同一登录态/ storage_state 造成冲突或积分异常。
    if "$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null | grep -q "^ms-rewards-run$"; then
        echo "[$(date)] 已有容器 ms-rewards-run 在运行，本次跳过。"
        return 0
    fi

    # 把「立即执行」标记传入容器，供脚本判断是否为手动触发（手动触发不发邮件）
    DOCKER_ENV=""
    if [ "$IMMEDIATE" = "1" ]; then
        DOCKER_ENV="-e IMMEDIATE=1"
    fi

    # NAS 无头模式开关 + 数据卷路径（storage_state.json 需提前放到 ${DATA_DIR}/storage_state.json）
    "$DOCKER_BIN" run --rm --name ms-rewards-run \
        -e REWARDS_NAS=1 \
        -e REWARDS_PROFILE=/app/data/profile \
        -e REWARDS_STORAGE=/app/data/storage_state.json \
        $DOCKER_ENV $STATE_VOL $IMAGE python rewards_earn.py >> ${SCRIPT_DIR}/nas.log 2>&1
    echo "[$(date)] 本次运行结束"
}

# 立即模式直接跑，不走随机时刻逻辑
if [ "$IMMEDIATE" = "1" ]; then
    run_container
    exit 0
fi

# ---- 每日随机时刻生成（每天首次运行时生成两个去重的 HH:MM）----
TODAY=$(date +%Y-%m-%d)
NEED_NEW=0
if [ -f "$SCHED_FILE" ]; then
    CUR_DATE=$(grep '^DATE=' "$SCHED_FILE" | cut -d= -f2)
    if [ "$CUR_DATE" != "$TODAY" ]; then
        NEED_NEW=1
    fi
else
    NEED_NEW=1
fi

if [ "$NEED_NEW" = "1" ]; then
    # 触发时刻限制在 09:00–21:00（白天活动时段），小时取 9..21（共 13 小时窗）
    H1=$(( (RANDOM % 13) + 9 )); M1=$((RANDOM % 60))
    H2=$(( (RANDOM % 13) + 9 )); M2=$((RANDOM % 60))
    # 去重：两个时刻至少差 1 分钟
    while [ $((H1 * 60 + M1)) -eq $((H2 * 60 + M2)) ]; do
        H2=$(( (RANDOM % 13) + 9 )); M2=$((RANDOM % 60))
    done
    S1=$(printf '%02d:%02d' $H1 $M1)
    S2=$(printf '%02d:%02d' $H2 $M2)
    {
        echo "DATE=$TODAY"
        echo "SLOT1=$S1"
        echo "SLOT2=$S2"
        echo "DONE1=0"
        echo "DONE2=0"
    } > "$SCHED_FILE"
    echo "[$(date)] 今日随机触发时刻：$S1, $S2"
fi

# ---- 判断是否到达某个未执行的随机时刻 ----
NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
S1=$(grep '^SLOT1=' "$SCHED_FILE" | cut -d= -f2)
S2=$(grep '^SLOT2=' "$SCHED_FILE" | cut -d= -f2)
D1=$(grep '^DONE1=' "$SCHED_FILE" | cut -d= -f2)
D2=$(grep '^DONE2=' "$SCHED_FILE" | cut -d= -f2)
T1=$(( 10#$(echo "$S1" | cut -d: -f1) * 60 + 10#$(echo "$S1" | cut -d: -f2) ))
T2=$(( 10#$(echo "$S2" | cut -d: -f1) * 60 + 10#$(echo "$S2" | cut -d: -f2) ))

FIRE=0
if [ "$D1" = "0" ] && [ "$NOW_MIN" -ge "$T1" ]; then
    sed -i 's/^DONE1=0/DONE1=1/' "$SCHED_FILE"
    FIRE=1
fi
if [ "$D2" = "0" ] && [ "$NOW_MIN" -ge "$T2" ]; then
    sed -i 's/^DONE2=0/DONE2=1/' "$SCHED_FILE"
    FIRE=1
fi

if [ "$FIRE" = "1" ]; then
    run_container
else
    echo "[$(date)] 未到随机触发时刻（$S1/$S2），跳过。"
fi

# ---- 月度看门狗：每月 5/15/25 检查本月攻略进度（独立于主流程，消灭静默失败，见 ADR-0002）----
CASE_DAY=$(date +%d)
if [ "$CASE_DAY" = "05" ] || [ "$CASE_DAY" = "15" ] || [ "$CASE_DAY" = "25" ]; then
    echo "[$(date)] 触发月度看门狗检查..."
    /opt/python3/bin/python3 "${SCRIPT_DIR}/monthly_watchdog.py" >> "${DATA_DIR}/watchdog.log" 2>&1
fi
