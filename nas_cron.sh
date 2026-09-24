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

SCRIPT_DIR="${REWARDS_SCRIPT_DIR:-/share/CACHEDEV1_DATA/Web/gadget/microsoft}"   # ← NAS 上实际路径
IMAGE="ms-rewards"
# 脚本目录整体挂载到容器 /app；则 /app/data 对应宿主机的 ${SCRIPT_DIR}/data，
# 用于持久化 profile、storage_state.json 与运行日志（run_*.log）。
STATE_VOL="-v ${SCRIPT_DIR}:/app"
DATA_DIR="${SCRIPT_DIR}/data"
mkdir -p "${DATA_DIR}"
SCHED_FILE="${DATA_DIR}/schedule.txt"
DISABLED_FLAG="${SCRIPT_DIR}/disabled.flag"
# cron 环境 PATH 精简，docker 用绝对路径（Container Station 自带）
DOCKER_BIN="${REWARDS_DOCKER_BIN:-/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker}"
LOCK_DIR="${DATA_DIR}/.run.lock"
SCHED_LOCK_DIR="${DATA_DIR}/.schedule.lock"
MANUAL_STATE="${DATA_DIR}/manual_run.state"
AUTH_BLOCK="${DATA_DIR}/auth_required.json"
STATE_FILE="${DATA_DIR}/storage_state.json"
# 防止面板/外部调用异常地反复注入 --now。手动任务完成后 20 小时内拒绝重复立即执行；
# 需要排障时显式传 --force，正常 cron 不受此限制。
MANUAL_COOLDOWN_SECONDS="${REWARDS_MANUAL_COOLDOWN_SECONDS:-72000}"
IMMEDIATE_DELAY_MAX_SECONDS="${REWARDS_IMMEDIATE_DELAY_MAX_SECONDS:-60}"
SCHEDULED_DELAY_MAX_SECONDS="${REWARDS_SCHEDULED_DELAY_MAX_SECONDS:-1800}"

# ---- 立即模式：面板「立即执行」时跳过随机时刻，直接跑（随机延迟 ≤1 分钟）----
# 触发方式（任一即可）：
#   1) 传参：  nas_cron.sh --now
#   2) 环境变量： IMMEDIATE=1 nas_cron.sh
IMMEDIATE="${IMMEDIATE:-0}"
DUE1=0
DUE2=0
FORCE_IMMEDIATE=0
if [ "$1" = "--now" ] || [ "$1" = "-n" ]; then
    IMMEDIATE=1
elif [ "$1" = "--force" ]; then
    IMMEDIATE=1
    FORCE_IMMEDIATE=1
fi

# 面板暂停只影响定时触发；--now/--force 仍允许管理员手动运行。
if [ "$IMMEDIATE" != "1" ] && [ -f "$DISABLED_FLAG" ]; then
    echo "[$(date)] 微软积分任务已暂停（disabled.flag），本次跳过。"
    exit 0
fi

# 认证墙是外部人工前置条件。认证事件文件比 storage_state 新时，暂停所有自动重试；
# Windows 端完成条款确认并覆盖状态文件后，mtime 自动解除阻塞，避免撞击账号。
auth_blocked() {
    [ ! -f "$AUTH_BLOCK" ] && return 1
    [ ! -f "$STATE_FILE" ] && return 0
    _blocked_hash="$(sed -n 's/.*"state_sha256"[[:space:]]*:[[:space:]]*"\([0-9a-fA-F]*\)".*/\1/p' "$AUTH_BLOCK" | head -n 1)"
    _state_hash="$(sha256sum "$STATE_FILE" 2>/dev/null | awk '{print $1}')"
    # 新文件可能保留旧 mtime，因此优先比较内容指纹；兼容旧 marker 时回退 mtime。
    if [ -n "$_blocked_hash" ] && [ -n "$_state_hash" ]; then
        [ "$_blocked_hash" = "$_state_hash" ]
    else
        [ "$AUTH_BLOCK" -nt "$STATE_FILE" ]
    fi
}
if auth_blocked; then
    echo "[$(date)] 检测到 ${AUTH_BLOCK}，任务暂停：请在 Windows 完成账户条款/重新认证并更新 storage_state.json。"
    exit 2
fi

epoch_now() { date +%s; }

acquire_lock() {
    _lock="$1"
    _kind="$2"
    if mkdir "$_lock" 2>/dev/null; then
        printf '%s\n' "$$" > "$_lock/pid"
        return 0
    fi

    # 进程被杀、NAS 重启或断电后，mkdir 锁会残留并让所有后续 cron 永久跳过。
    # 锁内 pid 已不存在时只清理由本脚本创建的陈旧锁，再继续本次调度。
    _pid="$(cat "$_lock/pid" 2>/dev/null || true)"
    if [ -n "$_pid" ] && ! kill -0 "$_pid" 2>/dev/null; then
        echo "[$(date)] 检测到陈旧${_kind}锁（pid=${_pid}），自动回收。"
        rm -rf "$_lock"
        if mkdir "$_lock" 2>/dev/null; then
            printf '%s\n' "$$" > "$_lock/pid"
            return 0
        fi
    fi
    return 1
}

release_lock() { rm -rf "$1"; }

manual_allowed() {
    [ "$FORCE_IMMEDIATE" = "1" ] && return 0
    [ ! -f "$MANUAL_STATE" ] && return 0
    _last="$(cat "$MANUAL_STATE" 2>/dev/null || true)"
    case "$_last" in
        ''|*[!0-9]*) return 0 ;;
    esac
    _now="$(epoch_now)"
    _elapsed=$((_now - _last))
    if [ "$_elapsed" -ge "$MANUAL_COOLDOWN_SECONDS" ]; then
        return 0
    fi
    _remain=$(( (MANUAL_COOLDOWN_SECONDS - _elapsed + 3599) / 3600 ))
    echo "[$(date)] 立即模式冷却中（约 ${_remain} 小时）。拒绝重复 --now；如需排障可显式使用 --force。"
    return 1
}

# 真正拉起容器的函数（含随机延迟 + 并发保护）
run_container() {
    if ! acquire_lock "$LOCK_DIR" "运行"; then
        echo "[$(date)] 已有调度实例运行，本次跳过。"
        return 0
    fi
    if [ "$IMMEDIATE" = "1" ]; then
        # 立即模式（面板「立即执行」）：随机延迟控制在 1 分钟内（0~60s），
        # 既保留一点随机性规避风控，又不会让用户久等。
        DELAY=$((RANDOM % IMMEDIATE_DELAY_MAX_SECONDS))
        echo "[$(date)] 立即模式：随机延迟 ${DELAY}s（≤1 分钟）后启动..."
    else
        # 到点触发：随机延迟 0~30 分钟，避免固定时间被检测
        DELAY=$((RANDOM % SCHEDULED_DELAY_MAX_SECONDS))
        echo "[$(date)] 到点触发：随机延迟 ${DELAY}s 后启动..."
    fi
    sleep $DELAY

    # 防止并发：若已有同名容器在跑（手动触发与定时触发重叠时），直接跳过，
    # 避免两个 Chromium 共用同一登录态/ storage_state 造成冲突或积分异常。
    if "$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null | grep -q "^ms-rewards-run$"; then
        echo "[$(date)] 已有容器 ms-rewards-run 在运行，本次跳过。"
        release_lock "$LOCK_DIR"
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
    rc=$?
    release_lock "$LOCK_DIR"
    if [ "$rc" -eq 0 ]; then
        [ "$DUE1" = "1" ] && sed -i 's/^DONE1=0/DONE1=1/' "$SCHED_FILE"
        [ "$DUE2" = "1" ] && sed -i 's/^DONE2=0/DONE2=1/' "$SCHED_FILE"
        [ "$IMMEDIATE" = "1" ] && epoch_now > "$MANUAL_STATE"
        echo "[$(date)] 本次运行成功（已提交触发槽位）"
    else
        echo "[$(date)] 本次运行失败 rc=$rc（保留触发槽位，下一次 cron 重试）"
    fi
    return "$rc"
}

# 立即模式直接跑，不走随机时刻逻辑
if [ "$IMMEDIATE" = "1" ]; then
    manual_allowed && run_container
    exit 0
fi

# 锁住「每日计划读/写 + 到点判定」，避免重叠 cron 在切日时互相覆盖 schedule.txt。
if ! acquire_lock "$SCHED_LOCK_DIR" "计划"; then
    echo "[$(date)] 计划状态正被另一实例更新，本次跳过。"
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
NOW_H=$(date +%H); NOW_H=${NOW_H#0}; [ -n "$NOW_H" ] || NOW_H=0
NOW_M=$(date +%M); NOW_M=${NOW_M#0}; [ -n "$NOW_M" ] || NOW_M=0
NOW_MIN=$(( NOW_H * 60 + NOW_M ))
S1=$(grep '^SLOT1=' "$SCHED_FILE" | cut -d= -f2)
S2=$(grep '^SLOT2=' "$SCHED_FILE" | cut -d= -f2)
D1=$(grep '^DONE1=' "$SCHED_FILE" | cut -d= -f2)
D2=$(grep '^DONE2=' "$SCHED_FILE" | cut -d= -f2)
S1H=$(echo "$S1" | cut -d: -f1); S1H=${S1H#0}; [ -n "$S1H" ] || S1H=0
S1M=$(echo "$S1" | cut -d: -f2); S1M=${S1M#0}; [ -n "$S1M" ] || S1M=0
S2H=$(echo "$S2" | cut -d: -f1); S2H=${S2H#0}; [ -n "$S2H" ] || S2H=0
S2M=$(echo "$S2" | cut -d: -f2); S2M=${S2M#0}; [ -n "$S2M" ] || S2M=0
T1=$(( S1H * 60 + S1M ))
T2=$(( S2H * 60 + S2M ))

FIRE=0
if [ "$D1" = "0" ] && [ "$NOW_MIN" -ge "$T1" ]; then
    DUE1=1
    FIRE=1
fi
if [ "$D2" = "0" ] && [ "$NOW_MIN" -ge "$T2" ]; then
    DUE2=1
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

release_lock "$SCHED_LOCK_DIR"
