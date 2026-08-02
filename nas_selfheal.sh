#!/bin/sh
# nas_selfheal.sh - 固件更新 / 重启后自动恢复「微软积分」+「img.ink 图床签到」两个任务
#
# 设计要点：
#   1. 幂等：可反复安全运行，已存在的不重复注册。
#   2. 全部恢复逻辑落在数据卷 /share/CACHEDEV1_DATA 上，固件更新只动系统分区，
#      数据卷不动 → 本脚本 + autorun.sh 在固件更新后仍能存活并自动执行。
#   3. 由 /share/CACHEDEV1_DATA/.config/autorun.sh 在开机时调用（见 nas_autorun.sh）。
#
# 恢复内容：
#   - SSH 公钥（admin/root 的 ~/.ssh/authorized_keys），避免固件更新后退回输密码
#   - 微软积分 cron：*/15 调 nas_cron.sh（脚本内部随机双时刻触发）
#   - img.ink cron：*/5 调 start_web.sh（保活 = 开机自启 + 崩溃自愈）
#   - 立即拉起一次 img.ink Web 面板
#
# 手动触发（固件更新后第一次，或想立即恢复）：
#   /bin/sh /share/CACHEDEV1_DATA/Web/gadget/microsoft/nas_selfheal.sh

set -u

VOL="/share/CACHEDEV1_DATA"
MS_DIR="$VOL/Web/gadget/microsoft"
IMG_DIR="$VOL/Web/gadget/auto_sign"
SEED="$MS_DIR/authorized_keys_seed.txt"     # 随项目同步进来的公钥种子
SSH_DIR="${HOME:-/root}/.ssh"               # admin 即 root，家目录 /root
CRONTAB="/etc/config/crontab"
LOG="$MS_DIR/selfheal.log"

log() { echo "[$(date)] selfheal: $*" | tee -a "$LOG"; }

log "===== 开始自愈 ====="

# ---------- 1. 恢复 SSH 公钥（admin/root）----------
if [ -f "$SEED" ]; then
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    touch "$SSH_DIR/authorized_keys"
    chmod 600 "$SSH_DIR/authorized_keys"
    while IFS= read -r key; do
        [ -z "$key" ] && continue
        # 仅追加不存在的公钥，避免重复堆积
        if grep -qxF "$key" "$SSH_DIR/authorized_keys" 2>/dev/null; then
            :
        else
            echo "$key" >> "$SSH_DIR/authorized_keys"
            log "已写入 SSH 公钥"
        fi
    done < "$SEED"
    log "SSH 公钥恢复完成（来源 $SEED）"
else
    log "未找到公钥种子 $SEED，跳过 SSH 恢复（如需请先把公钥放进该文件）"
fi

# ---------- 2. 修复 crontab setuid 位（防固件更新后 must be suid 报错）----------
# 固件更新常把 /usr/bin/crontab 的 setuid 位清掉，普通用户跑 crontab -l 报
# "must be suid to work properly"。开机以 root 跑一次 chmod 4755 根治，避免复发。
_CRONTAB_BIN="$(command -v crontab 2>/dev/null || echo /usr/bin/crontab)"
if [ -x "$_CRONTAB_BIN" ] && _is_root; then
    chmod 4755 "$_CRONTAB_BIN" 2>/dev/null && log "已修复 crontab setuid 位（$_CRONTAB_BIN）"
elif [ -x "$_CRONTAB_BIN" ] && command -v sudo >/dev/null 2>&1; then
    sudo chmod 4755 "$_CRONTAB_BIN" 2>/dev/null && log "已修复 crontab setuid 位（sudo）"
fi

# ---------- 3. 重建 cron 定时任务 ----------
# 先清除旧的同类条目（按命令关键字），避免固件更新前后残留的旧调度
# （如 0 9,21）与新的 */15 条目并存，导致一天被多次/固定时刻触发。
# 注意：/etc/config/crontab 只有 root 可写。autorun.sh 以 root 开机执行；
# 若手动以 admin 运行，脚本会自动尝试 sudo，否则给出明确提示（不再卡在 mv -i）。
MS_CRON="*/15 * * * * /bin/sh $MS_DIR/nas_cron.sh >> $MS_DIR/nas.log 2>&1"
IMG_CRON="*/5 * * * * /bin/sh $IMG_DIR/start_web.sh >> $IMG_DIR/web_keepalive.log 2>&1"
# 夜间扩容自检：每天 23:00 跑 sign_expand.py --check（只读），若今日未成功扩容则发告警邮件。
# 这条与「旧 0 7 直跑扩容」条目不同：它是 --check 只读自检，必须保留（下面清理时按 --check 区分）。
CHECK_CRON="0 23 * * * PYTHONPATH=/share/homes/Mars/.local/lib/python3.12/site-packages /share/CACHEDEV1_DATA/.qpkg/Python3/opt/python3/bin/python3 $IMG_DIR/sign_expand.py --check >> $IMG_DIR/check.log 2>&1"

_is_root() { [ "$(id -u)" = "0" ]; }

# 把内容写入 /etc/config/crontab（root 直写，否则 sudo）；返回 0 表示成功
_write_crontab() {
    _src="$1"
    if _is_root; then
        cat "$_src" > "$CRONTAB"
    elif command -v sudo >/dev/null 2>&1; then
        sudo sh -c "cat '$1' > '$CRONTAB'"
    else
        return 1
    fi
}

# 重启 crond（root 直跑，否则 sudo）
_restart_crond() {
    if _is_root; then
        if [ -x /etc/init.d/crond.sh ]; then
            /etc/init.d/crond.sh restart >> "$LOG" 2>&1
        else
            killall -HUP crond 2>/dev/null || true
        fi
    elif command -v sudo >/dev/null 2>&1; then
        if [ -x /etc/init.d/crond.sh ]; then
            sudo /etc/init.d/crond.sh restart >> "$LOG" 2>&1
        else
            sudo sh -c 'killall -HUP crond' 2>/dev/null || true
        fi
    fi
}

# 生成干净的新 crontab：保留其它旧条目，仅去掉以下关键字行后追加新条目
# （临时文件落在 /tmp，避免 /etc/config 不可写时失败；不再使用 mv，规避 mv -i 交互询问）
#   - nas_cron.sh  : 微软积分（重建为 */15，清掉旧 0 9,21）
#   - start_web.sh : img.ink Web 面板保活（重建为 */5）
#   - sign_expand.py: 旧「0 7 直跑扩容」独立条目，已冗余——扩容现由 web_app.py 内部按
#                     config.ini 随机时段触发（web_app.py:17 SCRIPT_PATH 指向它），
#                     旧 cron 每天 7 点直跑还会崩（缺依赖），必须清掉避免重复/报错。
#                     注意：清的是「非 --check」的 sign_expand.py 条目；23:00 的
#                     「sign_expand.py --check」只读自检条目要保留（下面用 grep 区分）。
TMP_CRON="/tmp/selfheal.cron.$$"
: > "$TMP_CRON"
[ -f "$CRONTAB" ] && grep -vF "nas_cron.sh" "$CRONTAB" 2>/dev/null \
    | grep -vF "start_web.sh" \
    | grep -vF "sign_expand.py" >> "$TMP_CRON"
echo "$MS_CRON" >> "$TMP_CRON"
echo "$IMG_CRON" >> "$TMP_CRON"
echo "$CHECK_CRON" >> "$TMP_CRON"

if _write_crontab "$TMP_CRON"; then
    log "cron 已重建（*/15 微软 + */5 img.ink + 23:00 扩容自检）"
    _restart_crond
    log "crond 已重启"
else
    log "!!! 无法写入 $CRONTAB（需要 root 权限）。请二选一："
    log "    1) QNAP GUI：控制面板 → 系统 → 任务计划 → 新建用户定义脚本，命令填"
    log "       /bin/sh $MS_DIR/nas_selfheal.sh  然后点「运行」（以 root 执行）"
    log "    2) 或 sudo 运行： sudo /bin/sh $MS_DIR/nas_selfheal.sh"
fi
rm -f "$TMP_CRON"

# ---------- 3. 立即拉起 img.ink Web 面板（开机自启 / 自愈）----------
if [ -f "$IMG_DIR/start_web.sh" ]; then
    /bin/sh "$IMG_DIR/start_web.sh" >> "$LOG" 2>&1
    log "img.ink Web 面板已拉起"
else
    log "未找到 $IMG_DIR/start_web.sh，跳过 Web 拉起"
fi

log "===== 自愈完成 ====="
