# -*- coding: utf-8 -*-
"""
Microsoft Rewards (rewards.bing.com/earn) 自动打卡脚本
- 模拟真人鼠标轨迹（贝塞尔曲线 + 抖动 + 随机停顿）
- 连续打卡：必应搜索 (搜索: 1/1) / 每日活动 (活动: 3/3)
- 日常任务：逐个链接跳转并关闭，遇到拼图页点击「跳过拼图」
依赖：pip install playwright && playwright install msedge (或 chromium)

注意：页面为登录态，元素 class/id 会变化。本脚本以「可见文字」为主选择器并做多重兜底。
运行时若报元素未找到，请把控制台日志/截图发回，便于精修选择器。
"""
import asyncio
import os
import random
import re
import shutil
import subprocess
import sys
import time
import json
import traceback
import tempfile
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Locator


# ====================== 运行日志文件 ======================
class _Tee:
    """把输出同时写到多个流（控制台 + 日志文件）。"""
    def __init__(self, *streams):
        self.streams = list(streams)

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _install_log():
    """把 stdout/stderr 同时写入带时间戳的日志文件（run_YYYYMMDD_HHMMSS.log）。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    if IS_NAS:
        # NAS 下日志落盘到持久化数据卷，便于事后排查
        base = "/app/data"
    else:
        try:
            base = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            base = "."
    os.makedirs(base, exist_ok=True)
    logpath = os.path.join(base, f"run_{ts}.log")
    try:
        f = open(logpath, "a", encoding="utf-8")
    except Exception:
        return
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    print(f"[日志] 本次运行日志写入：{logpath}")


# ====================== 邮件通知（NAS 定时任务结果推送） ======================
# D1：本脚本运行期全部可变状态收敛进单一 RunState 对象（替代 5 个松散模块全局），
# 一眼看清有哪些跨函数状态、不再需要散落的 `global` 声明。
@dataclass
class RunState:
    last_run_failed: bool = False
    points_before: int | None = None        # 积分差额自审：运行前可用积分（读不到为 None）
    points_after: int | None = None         # 运行后可用积分
    daily_set_result: dict | None = None    # 每日活动三张卡片 +「可领取」处理结果（供邮件汇报）
    unfinished: list = field(default_factory=list)  # 收尾复检仍未完成清单（写进结果邮件）
    structural_failures: list = field(default_factory=list)  # 结构级异常（发【结构异常】告警邮件）


run_state = RunState()


# ====================== 本月攻略 punchcard 的稳定识别（跨月免维护） ======================
# 识别键取「最稳定的信号」：机器生成的 quest ID 模式 > 结构化文案特征（见 ADR-0002）。
# 绝不写死月份名 / 子任务名——微软每月全换文案（八月标题「让这个八月收获更多」根本不叫「八月攻略」）。
MONTHLY_QUEST_RE = re.compile(r"BingMonthlyPC_[A-Za-z]{3}_punchcard", re.I)

def is_monthly_strategy(text: str, href: str) -> bool:
    """识别「本月攻略」父卡片：优先 URL 模式，回退到结构化文案特征。

    绝不写死月份名——微软每月更换标题与子任务名。仅依赖跨月稳定的结构信号。
    """
    if href and MONTHLY_QUEST_RE.search(href):
        return True
    # 回退：形如「x/4 个任务」的多子任务打卡卡片（ADR-0002 验证过的结构信号）
    return bool(re.search(r"\d\s*/\s*4\s*个任务", text or ""))


def is_locked_monthly(text: str, href: str = "") -> bool:
    """识别「本月攻略」中尚未到开放时间的锁定周任务（左侧显示小锁图标）。

    锁定周既不是「已完成」也不是「未完成待重试」，而是未来某周（如第二/三/四周）
    才开放——用户实测：八月第一周其余三周左侧显示小锁，分别要第二/三/四周才开放。
    这类任务不应被点击、不应计入未完成、更不应写进「未完成」邮件清单。

    文本信号（可靠，来自页面文案）：含『到期日期 / X周后 / X天后 / 锁定 / 未解锁 /
    即将开放』等。注意：仅对 is_monthly_strategy 命中的任务使用本判定，避免误伤。
    """
    t = text or ""
    return bool(re.search(
        r"到期日期|\d+\s*周(后|内)|\d+\s*天(后|内)|锁定|未解锁|即将开放|未开放|暂不可", t))


def _parse_monthly_progress(text: str):
    """从「N/M 个任务」解析 (done, total)；解析不到返回 (None, None)。

    例：「让这个八月收获更多 +50 1/4 个任务」 -> (1, 4)。
    """
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*个任务", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def current_month_week_index(now: datetime | None = None) -> int:
    """本月「本月攻略」月任务已开放到第几周（1-based）。

    Rewards 月任务按自然日窗口逐周开放：1~7 日=第 1 周，8~14 日=第 2 周，
    15~21 日=第 3 周，22 日~月底=第 4 周（每月 4 个周任务，正好覆盖一个月）。
    已完成的周数 >= 当前周序号，即「已按时完成 / 后续周尚未到解锁时间」，属正常，
    不计入未完成（用户反馈：八月第一周显示 1/4 即正确，不应误报）。
    """
    d = now or datetime.now()
    return (d.day - 1) // 7 + 1


# 结构级异常（如「本月攻略定位器整月零匹配」）单独收集，发【结构异常】告警邮件，
# 与日常「有 N 项未完成」的噪音级邮件区分（见 ADR-0002 §3.5）——已收纳进上方 run_state（D1）。
# 积分差额自审：运行前后「可用积分」余额等全部运行期状态，同上收敛进 run_state。

def _monthly_state_path() -> str:
    base = "/app/data" if IS_NAS else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "monthly_state.json")


def _mail_state_path() -> str:
    """邮件去重状态：同日同类型只发一次。"""
    base = "/app/data" if IS_NAS else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "mail_notify_state.json")


def _auth_status_path() -> str:
    """认证阻塞事件文件；NAS 调度器据此暂停，直到新状态文件到达。"""
    base = "/app/data" if IS_NAS else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "auth_required.json")


def _load_json(path, default):
    """D6：统一『读 JSON 状态文件』——三态(mail/monthly/weekly)共用，遇错返回默认。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else default
    except Exception:
        return default


def _save_json(path, data, err_label="写入状态文件失败"):
    """统一原子写 JSON 状态文件，避免断电留下半个状态文件。"""
    try:
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".rewards-json-", dir=os.path.dirname(target))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception as e:
        print(f"    ⚠ {err_label}: {e}")


def _mark_auth_required(error: "AuthenticationRequired") -> None:
    """记录认证墙，供 NAS 调度器熔断，且不触碰旧登录态。"""
    state_hash = ""
    if IS_NAS:
        try:
            with open(NAS_STORAGE_STATE, "rb") as f:
                state_hash = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            pass
    _save_json(_auth_status_path(), {
        "code": error.code,
        "url": _display_url(getattr(error, "url", "")) or "unknown",
        "message": str(error),
        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state_path": NAS_STORAGE_STATE if IS_NAS else "",
        "state_sha256": state_hash,
    }, "写入 auth_required.json 失败")
    print(f"[认证熔断] 已记录 {error.code}，待新的 storage_state.json 到达后自动解除。")


def _clear_auth_required() -> None:
    """只有认证成功且新状态已安全写回后才清除熔断标记。"""
    path = _auth_status_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            print("[认证熔断] 登录态已验证并写回，解除调度阻塞。")
    except OSError as e:
        print(f"    ⚠ 清理 auth_required.json 失败：{e}")


def _load_mail_state() -> dict:
    return _load_json(_mail_state_path(), {})


def _save_mail_state(state: dict) -> None:
    _save_json(_mail_state_path(), state, "[邮件] 写入去重状态失败")


def _mail_day_key() -> str:
    return time.strftime("%Y-%m-%d")


def _write_monthly_state(weekly_done: int, weekly_total: int) -> None:
    """成功定位并扫描到本月攻略后记录进度，供月度看门狗独立消费（见 ADR-0002 §5.2）。

    仅在确实找到卡片、完成扫描时调用；定位失败（零匹配）不写，留给看门狗发现。
    """
    data = {
        "month": time.strftime("%Y-%m"),
        "weekly_done": weekly_done,
        "weekly_total": weekly_total,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_json(_monthly_state_path(), data, "写入 monthly_state.json 失败")
    print(f"    已写入月度进度 monthly_state.json: {weekly_done}/{weekly_total}")


# ====================== 每周点击门控（earn 周任务「每周一次」） ======================
# 目标：同一自然周内，对某个周子任务——已点过且尚未绿勾翻转 -> 不再重复点（防风控）；
#       绿勾已翻转 -> 本周期内彻底不再碰；跨周自动重置（下周重新开放再点）。
# 仅记录本地状态，不改变页面上的「已完成」判定（页面绿勾仍为唯一真相源）。
def _weekly_state_path() -> str:
    base = "/app/data" if IS_NAS else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "weekly_click_state.json")


def _load_weekly_state() -> dict:
    return _load_json(_weekly_state_path(), {})


def _save_weekly_state(st: dict) -> None:
    _save_json(_weekly_state_path(), st, "写入 weekly_click_state.json 失败")


def _week_key(now: datetime | None = None) -> str:
    """ISO 自然周键，如 '2026-W37'；跨周自动失效，实现「每周一次」。"""
    y, w, _ = (now or datetime.now()).isocalendar()
    return f"{y}-W{w:02d}"


def week_gate_ok(title: str) -> bool:
    """该周子任务本轮是否允许点击：
    已翻绿勾 -> False；本周已点过且未翻绿 -> False；否则 -> True（可点）。"""
    title = (title or "").strip()
    st = _load_weekly_state()
    wk = _week_key()
    if st.get("week") != wk:  # 跨周或首跑，重置周一状态
        st = {"week": wk, "clicked": {}, "done": {}}
    if title in st.get("done", {}):
        return False  # 已翻绿，本周期内不再碰
    if title in st.get("clicked", {}):
        print(f"    ⏭ 周门控：本周已点击过「{title[:30]}」且未翻绿，跳过重复点击")
        return False
    return True


def week_mark_clicked(title: str) -> None:
    title = (title or "").strip()
    st = _load_weekly_state()
    if st.get("week") != _week_key():
        st = {"week": _week_key(), "clicked": {}, "done": {}}
    st.setdefault("clicked", {})[title] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_weekly_state(st)


def week_mark_done(title: str) -> None:
    title = (title or "").strip()
    st = _load_weekly_state()
    if st.get("week") != _week_key():
        st = {"week": _week_key(), "clicked": {}, "done": {}}
    st.setdefault("done", {})[title] = time.strftime("%Y-%m-%d")
    st.get("clicked", {}).pop(title, None)  # 翻绿后不再属"待重复"范畴
    _save_weekly_state(st)


async def humanize_read(popup: Page, min_t: float = 8.0, max_t: float = 18.0):
    """在跳转到的搜索/内容页做拟真阅读：随机向下滚动、停留、偶尔回滚，再静候入账。

    比原来「只 sleep 5~9s」更像真人阅读，降低风控概率；失败不阻塞主流程。
    """
    t0 = time.time()
    try:
        await popup.mouse.wheel(0, random.randint(200, 700)); await human_pause(1.5, 3.5)
        await popup.mouse.wheel(0, random.randint(300, 900)); await human_pause(1.5, 4.0)
        await popup.mouse.wheel(0, -random.randint(200, 600)); await human_pause(1.0, 2.5)
    except Exception:
        pass
    remain = rand(min_t, max_t) - (time.time() - t0)
    if remain > 0:
        await asyncio.sleep(remain)


def _load_mail_config():
    """从脚本同目录 config.json 的 email_notify 段读取邮件配置；环境变量可覆盖。

    环境变量（优先级高于 config.json）：
      REWARDS_MAIL_ENABLED / REWARDS_MAIL_SERVER / REWARDS_MAIL_PORT /
      REWARDS_MAIL_SENDER / REWARDS_MAIL_AUTH / REWARDS_MAIL_RECIPIENT
    """
    cfg = {}
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(here, "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = (json.load(f).get("email_notify") or {})
    except Exception:
        cfg = {}

    def _env(key, default):
        v = os.environ.get(key)
        return v if v not in (None, "") else default

    return {
        "enabled": str(_env("REWARDS_MAIL_ENABLED", cfg.get("enabled", "false"))).lower()
                   in ("1", "true", "yes"),
        "smtp_server": _env("REWARDS_MAIL_SERVER", cfg.get("smtp_server", "smtp.qq.com")),
        "smtp_port": int(_env("REWARDS_MAIL_PORT", cfg.get("smtp_port", 465))),
        "sender": _env("REWARDS_MAIL_SENDER", cfg.get("sender", "")),
        "auth_code": _env("REWARDS_MAIL_AUTH", cfg.get("auth_code", "")),
        "recipient": _env("REWARDS_MAIL_RECIPIENT", cfg.get("recipient", "")),
    }


def send_mail(subject, body):
    """发送结果邮件；未配置 / 发送失败均静默跳过，不影响主流程。"""
    m = _load_mail_config()
    if not m["enabled"]:
        print("[邮件] 未启用（email_notify.enabled 非 true），跳过发送。")
        return False
    if not (m["sender"] and m["recipient"] and m["auth_code"]):
        print("[邮件] 配置不完整（需 sender / auth_code / recipient），跳过发送。")
        return False
    try:
        import smtplib, ssl
        from email.mime.text import MIMEText
        from email.header import Header
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = m["sender"]
        msg["To"] = m["recipient"]
        if int(m["smtp_port"]) == 465:
            with smtplib.SMTP_SSL(m["smtp_server"], int(m["smtp_port"]), timeout=20) as s:
                s.login(m["sender"], m["auth_code"])
                s.sendmail(m["sender"], [m["recipient"]], msg.as_string())
        else:
            with smtplib.SMTP(m["smtp_server"], int(m["smtp_port"]), timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(m["sender"], m["auth_code"])
                s.sendmail(m["sender"], [m["recipient"]], msg.as_string())
        print(f"[邮件] 已发送至 {m['recipient']}")
        return True
    except Exception as e:
        print(f"[邮件] 发送失败：{e}")
        return False


def _error_code(error_text: str) -> str:
    """把异常统一映射为稳定码，避免邮件依赖 Playwright 原始文案。"""
    explicit = error_text.split(":", 1)[0] if ":" in error_text else ""
    known = {
        "TOU_REQUIRED", "AUTH_EXPIRED", "CHALLENGE_REQUIRED", "AUTH_IMPORT_INVALID",
        "UNKNOWN_REDIRECT", "NAVIGATION_FAILED", "SELECTOR_FAILED", "EXPORT_EDGE_CLOSED",
        "TARGET_CLOSED", "ERR_ABORTED", "VERIFY_EMPTY_PAGE", "VERIFY_HTTP_ERROR",
    }
    if explicit in known or explicit.startswith("VERIFY_"):
        return explicit
    lowered = error_text.lower()
    if any(x in lowered for x in ("timeout", "locator", "strict mode", "selector", "element is not")):
        return "SELECTOR_FAILED"
    if "targetclosederror" in lowered or "browsercontext.new_page" in lowered:
        return "TARGET_CLOSED"
    if "err_aborted" in lowered:
        return "ERR_ABORTED"
    return explicit or "UNKNOWN_ERROR"


def _build_mail_body(failed, error=""):
    """汇总结果：标题 + 本次运行日志末尾。"""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        base = "/app/data" if IS_NAS else os.path.dirname(os.path.abspath(__file__))
        logs = sorted(
            os.path.join(base, f)
            for f in os.listdir(base)
            if f.startswith("run_") and f.endswith(".log")
        )
        tail = ""
        if logs:
            with open(logs[-1], "r", encoding="utf-8", errors="ignore") as f:
                tail = "\n".join(f.read().splitlines()[-50:])
    except Exception:
        tail = "(无法读取运行日志)"
    error_text = str(error or "")
    error_code = _error_code(error_text)
    recovery = {
        "TOU_REQUIRED": ("含义：Microsoft 要求重新确认账户条款或重新认证，NAS 无法代替人工确认。\n"
                         "自动动作：已写入 auth_required.json，并暂停后续自动调度，避免反复失败。\n"
                         "处理：Windows 完成条款确认后执行 `python rewards_earn.py export`，再用真实 NAS 路径覆盖 storage_state.json。"),
        "AUTH_EXPIRED": ("含义：Microsoft 登录态已过期。\n"
                         "自动动作：已熔断自动任务并保留原登录态文件。\n"
                         "处理：Windows 重新登录后执行 `python rewards_earn.py export`，再部署新的 storage_state.json。"),
        "CHALLENGE_REQUIRED": ("含义：页面要求人工完成验证码、人机验证或二次认证。\n"
                               "自动动作：已停止当前任务，未覆盖有效登录态。\n"
                               "处理：Windows 浏览器完成验证，再重新 export storage_state。"),
        "AUTH_IMPORT_INVALID": ("含义：storage_state.json 缺少 cookies/origins 或 JSON 已损坏。\n"
                                "自动动作：拒绝启动任务，避免用坏状态覆盖生产文件。\n"
                                "处理：重新 export，并核对远端 SHA-256、字节数和 CR=0。"),
        "UNKNOWN_REDIRECT": ("含义：页面跳转到了未知地址，无法确认是否仍在 Rewards。\n"
                            "自动动作：停止扫描，不把空页面当成成功。\n"
                            "处理：检查网络、登录态和 debug_auth_error.png；不要盲目重试。"),
        "VERIFY_": ("含义：页面或 HTTP 返回无法提供可信的完成状态。\n"
                    "自动动作：执行收尾复检并把未完成任务写入邮件，避免假成功。\n"
                    "处理：先运行 `python rewards_earn.py verify` 只读核对，再根据日志定位页面结构变化。"),
        "NAVIGATION_FAILED": ("含义：Rewards 页面导航失败，常见原因是网络、DNS 或浏览器进程异常。\n"
                              "自动动作：导航会按策略重试；仍失败则保留现有状态并发信。\n"
                              "处理：检查 NAS 网络和 Chromium，再重试。"),
        "SELECTOR_FAILED": ("含义：页面元素定位或点击失败，通常是微软页面结构变化。\n"
                            "自动动作：已执行一次收尾复检，未确认完成的任务不会被标为成功。\n"
                            "处理：查看最新日志/快照，更新对应选择器；不要重新导出登录态。"),
        "EXPORT_EDGE_CLOSED": ("含义：Edge/Playwright 导出页面被关闭。\n"
                              "自动动作：脚本已自动重启一次导出上下文。\n"
                              "处理：关闭所有 Edge 窗口后重试，并确认 edge_profile 可写。"),
        "TARGET_CLOSED": ("含义：浏览器上下文或页面已被关闭，导出/任务无法继续。\n"
                         "自动动作：已重建一次 Playwright 页面；原登录态文件不会被覆盖。\n"
                         "处理：关闭所有 Edge/Chromium 窗口后重试。"),
        "ERR_ABORTED": ("含义：浏览器导航被中断，通常由旧页面关闭或 Edge 进程抢占造成。\n"
                       "自动动作：已等待并重试导航；仍失败则保留原文件。\n"
                       "处理：关闭所有 Edge 窗口后重新执行 export。"),
        "VERIFY_EMPTY_PAGE": ("含义：页面为空，无法确认登录和任务完成状态。\n"
                              "自动动作：拒绝把空页面判定为成功，也不会覆盖登录态。\n"
                              "处理：检查 NAS 网络、Chromium 和 Rewards 页面可达性。"),
        "VERIFY_HTTP_ERROR": ("含义：Rewards 返回 HTTP 错误，任务结果不可信。\n"
                             "自动动作：停止复检并保留当前状态。\n"
                             "处理：查看错误日志，确认网络或微软服务恢复后再运行。"),
    }
    if failed:
        head = f"任务执行失败 [{error_code or 'UNKNOWN_ERROR'}]。"
        for prefix, action in recovery.items():
            if error_code == prefix or (prefix.endswith("_") and error_code.startswith(prefix)):
                head += f"\n{action}"
                break
        else:
            head += ("\n含义：脚本遇到未分类异常，无法安全自动修复。"
                     "\n自动动作：已停止任务并保留现有登录态，避免覆盖有效文件。"
                     "\n处理：查看日志末尾和完整异常类型，再决定是否重试。")
    elif run_state.unfinished:
        head = f"任务已执行，但收尾复检发现 {len(run_state.unfinished)} 项仍未完成（已自动重试一次）："
        head += "\n" + "\n".join(f"  · {t}" for t in run_state.unfinished)
    else:
        head = "任务执行成功（收尾复检：页面上所有任务均已标记完成）。"
    if error_text:
        head += f"\n错误信息：{error_text}"
    # 积分差额自审：确认「积分真的涨了」，而非仅看页面「已完成」标记
    try:
        if run_state.points_before is not None or run_state.points_after is not None:
            pb = "?" if run_state.points_before is None else str(run_state.points_before)
            pa = "?" if run_state.points_after is None else str(run_state.points_after)
            if run_state.points_before is not None and run_state.points_after is not None:
                delta = run_state.points_after - run_state.points_before
                if delta > 0:
                    verdict = "已确认积分增长（自审通过）"
                elif not run_state.unfinished:
                    verdict = "余额未变化：任务标记完成但积分未增（可能延迟入账或已于昨日计入）"
                else:
                    verdict = "既有未完成任务、余额也未增长，疑似选择器失效或风控，需人工排查"
                head += f"\n[积分自审] 运行前 {pb} -> 运行后 {pa}（差额 {delta:+d}）{verdict}"
            else:
                head += f"\n[积分自审] 运行前 {pb} -> 运行后 {pa}（仅一端读数成功，无法计算差额）"
    except Exception:  # noqa: BLE001
        pass
    if run_state.daily_set_result is not None:
        ds = run_state.daily_set_result
        head += f"\n[每日活动] 完成 {ds.get('done')}/{ds.get('total')} 张，本次领取奖励积分 {ds.get('claimed')}"
    return f"{head}\n\n----- 本次运行日志末尾 -----\n{tail}\n"


def _notify_mail(failed, error=""):
    """结果邮件入口：手动触发可设 REWARDS_MAIL_OFF=1 关闭。"""
    if os.environ.get("IMMEDIATE") == "1":
        print("[邮件] IMMEDIATE=1，跳过邮件通知。")
        return
    if os.environ.get("REWARDS_MAIL_OFF") == "1":
        print("[邮件] REWARDS_MAIL_OFF=1，跳过邮件通知。")
        return
    today = _mail_day_key()
    status = "failure" if failed else "success"
    state = _load_mail_state()
    if state.get(status) == today:
        print(f"[邮件] 今日 {status} 邮件已发送过，跳过重复通知。")
        return
    error_text = str(error or "")
    error_code = _error_code(error_text)
    tag = (error_code or "失败") if failed else (f"部分未完成({len(run_state.unfinished)})" if run_state.unfinished else "成功")
    subject = f"微软积分自动任务 {time.strftime('%Y-%m-%d %H:%M')} - {tag}"
    body = _build_mail_body(failed, error)
    if run_state.structural_failures:
        body += "\n[结构异常]\n"
        body += "\n".join(f"  · {s}" for s in run_state.structural_failures)
        body += "\n\n这通常意味着页面结构 / 识别键已变化，脚本可能整月零完成而未被发现。"
        body += "\n请检查 is_monthly_strategy 识别键是否仍匹配当前页面（见 ADR-0002）。"
    if send_mail(subject, body):
        state[status] = today
        _save_mail_state(state)


# 真实登录态所在的系统 Edge 默认 User Data 目录（不能直接给 Playwright 用，
# 因为 Edge 禁止在「默认」目录上开启 DevTools 远程调试）。
SRC_PROFILE = r"C:\Users\ADMIN\AppData\Local\Microsoft\Edge\User Data"
# Playwright 实际使用的目录：必须是「非默认」路径，否则报
# "DevTools remote debugging requires a non-default data directory"。
# 首次运行会从 SRC_PROFILE 复制一份登录态过来（同 Windows 用户下 DPAPI 仍可解密）。
PROFILE_DIR = os.environ.get(
    "REWARDS_WINDOWS_PROFILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_profile"),
)
EARN_URL = "https://rewards.bing.com/earn"
DASHBOARD_URL = "https://rewards.bing.com/dashboard"
HEADLESS = False  # 必须非无头，才能复用桌面登录态并观察

# D3：DOM 选择器「单一真相源」——href 特征 / CSS 类名集中于此，Python 侧一律引用本常量，
# 杜绝 EARN/周扫描等 6 处各自写死。内嵌 JS 的 page.evaluate() 周任务扫描器保持原样
# （r-string 含大量 `{}`，转 f-string 会破坏对象字面量），见周扫描处注释说明如何对齐。
SELECTORS = {
    "SEARCH_HREF": "bing.com/search",         # 搜索任务链接 href 特征（classify_offer / 跳转搜索）
    "STATUS_OK": "statusSuccessRewardsBg",    # 完成绿勾徽章类（周任务 / 活动判定）
    "DS_CARD": "a[href*='DailySet']",         # DailySet 兜底卡片
    "A_TARGET": "a[data-offer-target='1']",   # click_offer 真人点击前打标的 <a>
}


# ====================== NAS / 无头模式 ======================
# 在 NAS（Docker）环境中设置环境变量 REWARDS_NAS=1 即切换到无头模式：
#   - 使用 Playwright 自带的 Chromium（不再依赖 Windows Edge 可执行文件）；
#   - 无头运行（headless=True），复用 storage_state.json 中的登录态；
#   - 跳过 Windows 专属的 preflight / 复制 Edge 登录态等操作。
# 登录态来源：在 Windows 上执行 `python rewards_earn.py export` 导出 storage_state.json，
# 再把它放到 NAS 主机的 REWARDS_STORAGE 路径（默认 /app/data/storage_state.json）。
IS_NAS = os.environ.get("REWARDS_NAS", "0").lower() in ("1", "true", "yes")
NAS_PROFILE_DIR = os.environ.get("REWARDS_PROFILE", "/app/data/profile")
NAS_STORAGE_STATE = os.environ.get("REWARDS_STORAGE", "/app/data/storage_state.json")
# Windows 上 `python rewards_earn.py export` 的默认导出路径（可用 REWARDS_EXPORT 覆盖）
EXPORT_STATE_PATH = os.environ.get(
    "REWARDS_EXPORT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_state.json"),
)


class AuthenticationRequired(RuntimeError):
    """登录态失效、被重新认证或被 Microsoft 登录墙拦截。"""

    def __init__(self, message: str, code: str = "AUTH_REQUIRED"):
        self.code = code
        super().__init__(f"{code}: {message}")


class StorageStateError(RuntimeError):
    """storage_state.json 不是可供 Playwright 使用的状态文件。"""


class VerificationError(RuntimeError):
    """复检缺少可信页面数据，不能断言任务完成。"""


AUTH_WALL_HOSTS = {
    "account.live.com",
    "login.live.com",
    "login.microsoftonline.com",
    "login.microsoft.com",
}


def _is_auth_wall_url(url: str) -> bool:
    """判断当前 URL 是否已离开 Rewards，进入登录/重新认证流程。"""
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host in AUTH_WALL_HOSTS:
        return True
    return host == "rewards.bing.com" and path.startswith(("/login", "/signin"))


def _display_url(url: str) -> str:
    """日志中只保留 URL 的 origin/path，避免把认证查询参数写入日志或邮件。"""
    try:
        parsed = urlsplit(url or "")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return (url or "").split("?", 1)[0].split("#", 1)[0]


def _read_storage_state(path) -> dict:
    """读取并校验 Playwright storage state，保留 cookies 与 origins。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as e:
        raise StorageStateError(
            f"AUTH_IMPORT_INVALID: 无法读取 storage_state：{path}。"
            "请执行 `python rewards_earn.py export` 并更新 NAS 的 data/storage_state.json。"
        ) from e
    if not isinstance(state, dict):
        raise StorageStateError(f"storage_state 顶层必须是对象：{path}")
    if not isinstance(state.get("cookies"), list):
        raise StorageStateError(f"storage_state.cookies 必须是数组：{path}")
    if "origins" in state and not isinstance(state["origins"], list):
        raise StorageStateError(f"storage_state.origins 必须是数组：{path}")
    state.setdefault("origins", [])
    return state


def _auth_failure_message(url: str) -> str:
    target = _display_url(url)
    if "/tou/" in target.lower():
        detail = "Microsoft 要求重新确认账户条款或重新认证"
    else:
        detail = "Microsoft 登录态已失效或需要重新登录"
    return (
        f"{detail}，当前页面为 {target}。"
        f"请在 Windows 执行 `python rewards_earn.py export`，"
        f"并将新的 storage_state.json 覆盖到 {NAS_STORAGE_STATE}。"
    )


def _auth_error(url: str) -> AuthenticationRequired:
    target = _display_url(url).lower()
    code = "TOU_REQUIRED" if "/tou/" in target else "AUTH_EXPIRED"
    error = AuthenticationRequired(_auth_failure_message(url), code=code)
    error.url = url
    return error


async def _require_rewards_page(page: Page, response=None) -> None:
    """页面位于 Rewards 且没有认证/挑战/HTTP 错误，才允许继续采集。"""
    if _is_auth_wall_url(page.url):
        raise _auth_error(page.url)
    parsed = urlsplit(page.url)
    if (parsed.hostname != "rewards.bing.com" or
            parsed.path.rstrip("/") not in ("/earn", "/dashboard", "/rewards/dashboard")):
        raise AuthenticationRequired(
            f"未进入 Rewards 任务页：{_display_url(page.url)}，请检查重定向或重新导出登录态。",
            code="UNKNOWN_REDIRECT",
        )
    if response is not None and response.status >= 400:
        raise VerificationError(f"VERIFY_HTTP_ERROR: Rewards 返回 HTTP {response.status}")
    body = (await page.locator("body").inner_text(timeout=20000)).lower()
    if any(marker in body for marker in (
        "captcha", "验证码", "verify you are human", "人机验证", "unusual traffic",
    )):
        raise AuthenticationRequired("页面要求人工验证，请在 Windows 浏览器中处理。", code="CHALLENGE_REQUIRED")
    if not body.strip():
        raise VerificationError("VERIFY_EMPTY_PAGE: Rewards 页面没有可读内容，不能确认登录或完成状态。")


async def _write_storage_state(context: BrowserContext, path) -> dict:
    """先完整序列化、再原子替换；任何失败保留原文件。"""
    state = await context.storage_state()
    data = json.dumps(state, ensure_ascii=False).encode("utf-8")
    target = os.path.abspath(path)
    fd, temp_path = tempfile.mkstemp(prefix=".rewards-state-", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return state


@dataclass
class BrowserSession:
    """同时持有 context 与非持久 browser，确保 NAS 状态能安全回写并完整关闭。"""

    context: BrowserContext
    browser: Browser | None = None
    state_path: str | None = None
    authenticated: bool = False


async def _persist_storage_state(session: BrowserSession) -> None:
    """认证成功后原子保存最新状态，避免中途崩溃截断正式文件。"""
    if not session.state_path or not session.authenticated:
        return
    try:
        await _write_storage_state(session.context, session.state_path)
        print(f"    已更新 storage_state：{session.state_path}")
        _clear_auth_required()
    except Exception as e:
        raise StorageStateError("AUTH_SAVE_FAILED: 无法保存续期后的登录态，原文件已保留。") from e


async def _close_session(session: BrowserSession, persist_state: bool = False) -> None:
    try:
        if persist_state:
            await _persist_storage_state(session)
    finally:
        try:
            await session.context.close()
        finally:
            if session.browser is not None:
                await session.browser.close()


def preflight():
    """启动前自检：若有 Edge 残留进程，强制关闭以保证 profile 文件不被锁"""
    print("[自检] 检查 Edge 进程...")
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
            capture_output=True, text=True,
        ).stdout
        running = "msedge.exe" in out
    except Exception:
        running = False
    if running:
        print("⚠ 检测到 Edge 仍在运行，将强制关闭以保证登录态完整复制。")
        subprocess.run(
            ["taskkill", "/F", "/IM", "msedge.exe"],
            capture_output=True, text=True,
        )
        time.sleep(2)
        try:
            out2 = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
                capture_output=True, text=True,
            ).stdout
            still = "msedge.exe" in out2
        except Exception:
            still = False
        if still:
            print("⚠ 仍有 Edge 进程无法关闭，复制可能不完整（登录态或丢失）。")
    print("[自检] 继续。")
    return True


def setup_profile(force: bool = False):
    """把系统 Edge 登录态复制到非默认目录 PROFILE_DIR（仅复制一次）"""
    if os.path.exists(PROFILE_DIR) and not force:
        print(f"[配置] 复用已有 profile：{PROFILE_DIR}")
        return
    if force and os.path.exists(PROFILE_DIR):
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        print("[配置] 已清空旧副本，准备重新复制。")
    print(f"[配置] 复制登录态 profile：\n  {SRC_PROFILE}\n→ {PROFILE_DIR}")
    os.makedirs(PROFILE_DIR, exist_ok=True)

    # 1) 复制根目录关键文件：Local State（含 DPAPI 加密种子）
    for f in ["Local State"]:
        src = os.path.join(SRC_PROFILE, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(PROFILE_DIR, f))

    # 2) 复制所有 profile 目录（Default / Profile 1 / ...）。
    #    仅保留登录态必需文件：Network/Cookies、Local Storage、Preferences 等；
    #    排除：缓存目录、历史/图标/自动填充等巨型库、以及整个 Extensions
    #    （uBlock/adguard 等规则集上百 MB，对打卡无用且拖慢启动、可能干扰页面）。
    exclude_dirs = [
        "Cache", "GPUCache", "Crashpad", "BrowserMetrics", "Service Worker",
        "Code Cache", "GrShaderCache", "ShaderCache", "optimization_guide",
        "Subresource Filter", "Site Characteristics Database",
        "File System", "blob_storage", "IndexedDB", "association",
        "Extensions", "Extension Rules", "Extension Scripts", "Extension State",
        "EntityExtraction", "EdgeCoupons", "DawnCache", "DawnGraphiteCache",
        "DawnWebGPUCache", "AutofillAiModelCache", "discounts_db",
        "discount_infos_db", "commerce_subscription_db", "EdgeEDrop",
    ]
    # 大体积但非登录必需的文件（按文件名排除）
    exclude_files = [
        "History", "Favicons", "Top Sites", "Web Data", "Visited Links",
        "Shortcuts", "QuotaManager", "QuotaManager-journal",
        "Cookies-journal", "Network Cookies-journal", "data_reduction_proxy_leveldb",
        "load_statistics.db", "WebAssistDatabase", "InterestGroups",
        "Network Action Predictor", "Affiliation Database",
    ]
    copied = 0
    for name in sorted(os.listdir(SRC_PROFILE)):
        full = os.path.join(SRC_PROFILE, name)
        if os.path.isdir(full) and (name == "Default" or name.startswith("Profile")):
            dst = os.path.join(PROFILE_DIR, name)
            print(f"  · 复制 {name} ...")
            cmd = [
                "robocopy", full, dst,
                "/E", "/R:0", "/W:0", "/NFL", "/NDL", "/NJH", "/NJS",
                "/XD", *exclude_dirs,
                "/XF", *exclude_files,
            ]
            try:
                subprocess.run(cmd, check=False, timeout=600)
            except subprocess.TimeoutExpired:
                print(f"    ⚠ {name} 复制超时(600s)，部分文件可能未复制")
            copied += 1
    if copied == 0:
        print("  ⚠ 未找到任何 profile 目录（Default/Profile*），请检查 SRC_PROFILE。")
    print("[配置] 复制完成。")


def cleanup_locks():
    """清除上一次异常退出留下的锁文件，避免新启动卡死"""
    if IS_NAS:
        return  # NAS 使用隔离 context，不再操作旧持久化 profile 的锁。
    base = PROFILE_DIR
    candidates = [
        os.path.join(base, "lockfile"),
        os.path.join(base, "SingletonLock"),
        os.path.join(base, "SingletonCookie"),
        os.path.join(base, "SingletonSocket"),
        os.path.join(base, "Default", "lockfile"),
        os.path.join(base, "Default", "SingletonLock"),
    ]
    for f in candidates:
        try:
            if os.path.exists(f):
                os.remove(f)
                print(f"[清理] 已删除锁文件：{f}")
        except Exception as e:
            print(f"[清理] 删除锁文件失败 {f}: {e}")

# 用于搜索任务的随机关键词（模拟真人搜索）
SEARCH_QUERIES = [
    "今天的天气", "最新科技新闻", "如何学习 Python", "世界遗产有哪些",
    "健康饮食建议", "太阳系行星", "北京旅游攻略", "人工智能发展史",
    "家常菜做法", "提高工作效率的方法",
]


# ====================== 真人化交互 ======================
def rand(a: float = 0.4, b: float = 1.2) -> float:
    return random.uniform(a, b)


async def human_pause(a: float = 0.4, b: float = 1.2) -> None:
    """D5:统一『人性化等待』——等价 asyncio.sleep(rand(a,b))，风控/阅读节奏集中在这，
    便于整体调抖动区间，避免 54 处各自拍脑袋。调用方只需表达"等 lo~hi 秒"。"""
    await asyncio.sleep(rand(a, b))

# 注：human_pause 是 54 处 asyncio.sleep(rand(...)) 的统一出口，
# 其函数体必须保持 asyncio.sleep(rand(a,b)) 原样（勿被 replace_all 改写成自调用）。


# Playwright 的 Mouse 对象未暴露「当前坐标」，需自行在 page 上维护一份状态。
def _get_mouse_pos(page: Page) -> dict:
    pos = getattr(page, "_mm_pos", None)
    if pos is None:
        pos = {"x": 10.0, "y": 10.0}  # 任意初始点
        try:
            page._mm_pos = pos
        except Exception:
            pass
    return pos


def _set_mouse_pos(page: Page, x: float, y: float):
    try:
        pos = getattr(page, "_mm_pos", None)
        if pos is None:
            page._mm_pos = {"x": x, "y": y}
        else:
            pos["x"], pos["y"] = x, y
    except Exception:
        pass


async def move_mouse_human(page: Page, tx: float, ty: float):
    """贝塞尔曲线 + 抖动，把鼠标从当前位置移动到 (tx, ty)"""
    pos = _get_mouse_pos(page)
    sx, sy = pos["x"], pos["y"]
    # 控制点随机偏移，形成自然弧线
    mx = (sx + tx) / 2 + random.uniform(-60, 60)
    my = (sy + ty) / 2 + random.uniform(-60, 60)
    steps = random.randint(22, 45)
    for i in range(1, steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * mx + t ** 2 * tx
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * my + t ** 2 * ty
        x += random.uniform(-2.5, 2.5)
        y += random.uniform(-2.5, 2.5)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.004, 0.018))
    _set_mouse_pos(page, tx, ty)


async def click_human(page: Page, locator: Locator):
    """滚动到元素 -> 曲线移动 -> 随机短暂停顿 -> 点击"""
    await locator.scroll_into_view_if_needed()
    await human_pause(0.3, 0.7)
    box = await locator.bounding_box()
    if not box:
        raise RuntimeError("元素无包围盒，无法点击: " + await locator.to_string() if hasattr(locator, "to_string") else str(locator))
    # 点击元素内部随机一点（非死板正中心）
    cx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    cy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    await move_mouse_human(page, cx, cy)
    await human_pause(0.1, 0.35)
    await page.mouse.click(cx, cy)
    _set_mouse_pos(page, cx, cy)
    await human_pause(0.3, 0.9)


async def perform_search(context: BrowserContext):
    """新开页 -> 必应搜索一次 -> 关闭（模拟真人输入）"""
    page = await context.new_page()
    try:
        await page.goto("https://www.bing.com", wait_until="domcontentloaded")
        await human_pause(1.0, 2.0)
        box = await page.locator("#sb_form_q").bounding_box()
        if box:
            await click_human(page, page.locator("#sb_form_q"))
            q = random.choice(SEARCH_QUERIES)
            await page.keyboard.type(q, delay=random.uniform(60, 160))
            await human_pause(0.3, 0.8)
            await page.keyboard.press("Enter")
            await human_pause(2.5, 4.5)
    finally:
        await human_pause(0.5, 1.5)
        await page.close()


async def dismiss_edge_sync_prompt(context: BrowserContext):
    """关闭 Edge 首次启动的「登录以同步数据」弹窗（点「不，谢谢」）。

    仅作 best-effort：找不到/超时就直接跳过，绝不阻塞主流程。
    """
    for kw in ["不，谢谢", "不,谢谢", "No thanks", "暂不", "Skip", "Not now"]:
        for pg in context.pages:
            try:
                btn = pg.locator(f"text={kw}").first
                if await btn.count() > 0 and await btn.is_visible(timeout=2000):
                    print(f"[同步] 检测到 Edge 同步提示，点击「{kw}」关闭")
                    await btn.click(timeout=3000)
                    await human_pause(0.8, 1.5)
                    return
            except Exception:
                continue


async def perform_search_on_page(page: Page):
    box = await page.locator("#sb_form_q").bounding_box()
    if not box:
        return
    await click_human(page, page.locator("#sb_form_q"))
    await page.keyboard.type(random.choice(SEARCH_QUERIES), delay=random.uniform(60, 160))
    await human_pause(0.3, 0.8)
    await page.keyboard.press("Enter")
    await human_pause(2.5, 4.5)


async def skip_puzzle(page: Page):
    """拼图页点击「跳过拼图」"""
    skip = page.locator("text=跳过拼图, text=跳过, text=SKIP").first
    if await skip.count() > 0:
        await click_human(page, skip)
        await human_pause(1.0, 2.0)


async def handle_quiz(np: Page):
    """Bing Homepage quiz / 问答页：真人逐题点选答案。

    该类页面常嵌在 iframe 中，选项 class 多变，故对主页面与所有 iframe 都尝试。
    每轮点击一个候选答案，重复若干轮直到没有可点选项或达到上限。
    """
    # 先等页面/测验加载，并尝试点击「开始/开始测验」入口
    await human_pause(2.0, 3.5)
    for kw in ["开始测验", "开始答题", "立即开始", "开始", "START"]:
        btn = np.locator(f"text={kw}").first
        try:
            if await btn.count() > 0 and await btn.is_visible():
                print(f"      点击「{kw}」进入测验")
                await click_human(np, btn)
                await human_pause(1.5, 2.5)
                break
        except Exception:
            continue

    # 只用 Bing quiz 专属的答案选项 class，避免命中搜索结果页里的普通按钮
    # （div[role='button'] / a[role='button'] 太宽，会误点搜索框、菜单等）
    option_selectors = [
        "[class*='rqAnswerOption' i]", "[class*='answerOption' i]",
        "[class*='b_ans' i] a", "[class*='quizOption' i]",
        "[class*='option' i]", "[class*='answer' i]",
    ]

    def frames():
        # 主页面 + 所有 iframe，都尝试
        return [np] + list(np.frames)

    # 前置门槛：先确认页面真有可点答案选项（搜索结果页无此类元素，直接跳过不误点）
    _has_option = False
    for fr in frames():
        for sel in option_selectors:
            try:
                if await fr.locator(sel).count() > 0:
                    _has_option = True
                    break
            except Exception:
                continue
        if _has_option:
            break
    if not _has_option:
        print("      未检测到问答答案选项，跳过逐题点选（疑似非 quiz 页）")
        return

    # 终态标记：出现即代表整份测验已答完，应立即停止点选（避免无脑点满多轮）
    _terminal_markers = ["已完成", "查看成绩", "你已完成", "完成!", "全部完成",
                         "view your score", "quiz complete", "you completed",
                         "see your score", "成绩"]

    async def _is_terminal():
        try:
            txt = await np.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return False
        low = txt.lower()
        return any(m.lower() in low for m in _terminal_markers)

    for round_i in range(10):  # 上限 10 轮（覆盖多题），但命中终态即提前 break
        # 每轮开点前先确认还没答完，避免重复点已完成的题
        if await _is_terminal():
            print(f"      检测到测验已完成标记，提前结束点选（第{round_i + 1}轮前）")
            break
        clicked = False
        for fr in frames():
            for sel in option_selectors:
                try:
                    opts = fr.locator(sel)
                    cnt = await opts.count()
                except Exception:
                    continue
                if cnt == 0:
                    continue
                # 只点第一个可见选项（Bing quiz 每点一题即提交并翻到下一题）
                for i in range(min(cnt, 6)):
                    opt = opts.nth(i)
                    try:
                        if await opt.is_visible():
                            print(f"      第{round_i + 1}轮：点击一个答案选项")
                            try:
                                await click_human(np, opt)
                            except Exception:
                                await opt.click(timeout=3000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break
            if clicked:
                break
        if not clicked:
            # 没可点选项：再给一次终态机会，否则判定结束
            if await _is_terminal():
                print("      检测到测验已完成标记，结束点选")
            else:
                print("      未发现更多可点选项，测验流程结束")
            break
        # 等本题提交/翻页（原 2-3.5s，缩短以减少冗余等待）
        await human_pause(1.5, 2.5)
    await human_pause(1.5, 2.5)  # 收尾等待（原 2-4s）


async def _has_real_quiz_elements(np: Page) -> bool:
    """页面是否真含 Bing 问答(quiz)容器，而非普通搜索结果页。

    必应搜索结果页到处是『问题/question』字样，故只用问答专属 class 判定，
    避免把搜索页误当成 quiz 去逐题点选（用户实测：『探索阿马尔菲』『即将举行的体育赛事』
    等 search 型每日活动都被误判为测验页去点答案）。
    """
    try:
        html = await np.content()
    except Exception:
        return False
    low = html.lower()
    # 注意：「b_ans」是必应通用答案框（knowledge/answer box）的 class，普通搜索结果页
    # 几乎都带它，并非问答(quiz)。把它当 quiz 信号会把搜索页误判成内嵌问答，进而被送进
    # handle_puzzle_or_quiz 的拼图/点选流程（见 ADR-0003）。仅保留 quiz 专属 class。
    markers = ["rqansweroption", "answeroption", "officequiz", "welcomequiz",
               "quizoption", "quizcontainer", "btcorpus", "fyestrivia"]
    return any(m in low for m in markers)


async def handle_puzzle_or_quiz(np: Page) -> bool:
    """拼图/问题页的有效交互（用户实测流程）：
    模仿真人鼠标点击拼图（或问题内容区）-> 点击「跳过拼图/跳过」-> 由调用方关闭页面。
    只浏览不点击是不计分的。返回 True 表示识别并处理了该类页面。
    """
    try:
        content = await np.content()
    except Exception:
        return False
    low_url = np.url.lower()
    low_content = content.lower()
    # 拼图判定：仅当 URL 明确为拼图页(imagepuzzle/puzzle)，或页面确实存在拼图专属 DOM
    # （#skipPuzzle 或「跳过拼图」按钮）。禁止用裸「拼图」正文匹配——必应搜索结果页侧栏/
    # 每日活动推广常含「拼图」字样(指向每日拼图活动)，会把普通搜索页误判为拼图页去点
    # 不存在的 skipPuzzle（见 ADR-0003）。真实拼图页靠 URL 已能被稳定识别。
    is_puzzle = ("imagepuzzle" in low_url) or ("puzzle" in low_url)
    if not is_puzzle:
        try:
            if (await np.locator("#skipPuzzle").count() > 0
                    or await np.locator("text=跳过拼图").count() > 0):
                is_puzzle = True
        except Exception:
            pass
    # 注意：不能用裸「问题/question」判定——中文/必应搜索结果页到处是这些词，会被误判成 quiz 去点选答案。
    # 仅当页面含明确的测验信号：URL 含 quiz / 文案含「你是否知道」「测验」/ 或真含问答容器 class。
    is_quiz = ("quiz" in low_url) or ("你是否知道" in content) or ("测验" in content) \
        or (await _has_real_quiz_elements(np))
    if not (is_puzzle or is_quiz):
        return False

    # 问题/测验页（如 Bing Homepage quiz）：没有「跳过」，需真人逐题点选答案
    if is_quiz and not is_puzzle:
        print("      识别为问题/测验页，执行 逐题点选答案 流程")
        await handle_quiz(np)
        return True

    print("      识别为拼图页，执行 点击「跳过拼图」(id=skipPuzzle) 流程")

    # 0) 优先真人鼠标点击 #skipPuzzle（其内 <a> 即「跳过拼图」），点击即完成并计分
    skipped = False
    try:
        sl = np.locator("#skipPuzzle").first
        if await sl.count() > 0 and await sl.is_visible():
            print("      真人鼠标点击 #skipPuzzle（跳过拼图）")
            await click_human(np, sl)
            await human_pause(2.5, 4.5)
            skipped = True
    except Exception:
        pass
    if not skipped:
        # 兜底 1：按文字「跳过拼图 / 跳过 / Skip」点击
        for kw in ["跳过拼图", "跳过", "Skip", "SKIP"]:
            btn = np.locator(f"text={kw}").first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    print(f"      点击「{kw}」")
                    await click_human(np, btn)
                    await human_pause(2.0, 4.0)
                    skipped = True
                    break
            except Exception:
                continue
    if not skipped:
        # 兜底 2：先点「开始」进入、点内容、再点跳过（应对有「说明」遮罩的情况）
        print("      ⚠ #skipPuzzle 不可点，尝试 开始->点击内容->跳过 兜底")
        for kw in ["开始拼图", "立即开始", "开始"]:
            btn = np.locator(f"text={kw}").first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    print(f"      点击「{kw}」进入")
                    await click_human(np, btn)
                    await human_pause(1.5, 2.5)
                    break
            except Exception:
                continue
        target = None
        for sel in ["canvas", "img[class*='puzzle' i]", "[class*='puzzle' i]",
                    "[class*='tile' i]", "#currentQuestionContainer",
                    "[class*='question' i]", "[class*='option' i]"]:
            loc = np.locator(sel).first
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    target = loc
                    break
            except Exception:
                continue
        try:
            if target is not None:
                print("      真人点击拼图/问题内容区")
                await click_human(np, target)
            else:
                vs = np.viewport_size or {"width": 1280, "height": 800}
                cx = vs["width"] * random.uniform(0.4, 0.6)
                cy = vs["height"] * random.uniform(0.4, 0.6)
                await move_mouse_human(np, cx, cy)
                await np.mouse.click(cx, cy)
                _set_mouse_pos(np, cx, cy)
        except Exception:
            pass
        await human_pause(1.0, 2.0)
        for kw in ["跳过拼图", "跳过", "Skip"]:
            btn = np.locator(f"text={kw}").first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    print(f"      点击「{kw}」")
                    await click_human(np, btn)
                    await human_pause(1.5, 3.0)
                    skipped = True
                    break
            except Exception:
                continue
    if not skipped:
        print("      ⚠ 未找到「跳过拼图」入口")
    # 留出积分入账时间（关闭由调用方执行）
    await human_pause(2.0, 4.0)
    return True


# ====================== 卡片/弹层识别 ======================
# 非任务的导航/杂项链接特征（小写匹配）
NAV_MARKERS = [
    "/dashboard", "/redeem", "/about", "/refer", "/faq",
    "/orderhistory", "/sitemap", "/account/", "/earn?modal=",
    "rwgbopen=1", "support.microsoft.com",
]


def is_nav_link(h: str) -> bool:
    low = (h or "").lower()
    if any(m in low for m in NAV_MARKERS):
        return True
    # 纯首页 / 自身链接
    if low.rstrip("/") in ("https://rewards.bing.com", "https://www.bing.com", "https://bing.com"):
        return True
    if low.rstrip("/") == EARN_URL.rstrip("/").lower():
        return True
    return False


async def safe_click(page: Page, loc: Locator):
    """真人化点击（贝塞尔轨迹），失败回退到 Playwright 原生 click。"""
    try:
        await click_human(page, loc)
    except Exception:
        await loc.click(timeout=6000)


def classify_offer(href: str) -> str:
    """按 href 判断任务类型：puzzle / quiz / punchcard / search / other"""
    low = (href or "").lower()
    if "imagepuzzle" in low:
        return "puzzle"
    if "quiz" in low:
        return "quiz"
    if "/quest/" in low or "punchcard" in low:
        return "punchcard"
    if SELECTORS["SEARCH_HREF"] in low:
        return "search"
    return "other"


async def collect_offers(page: Page) -> list:
    """直接从 earn 页面收集所有「赚分任务」<a>（不依赖卡片容器 class）。

    诊断显示卡片容器选择器匹配数为 0，但 <a href> 任务链接一定存在。
    返回 [{href, text, done, tb}]，tb 为 target 属性（是否有 target=_blank）。
    """
    return await page.evaluate(r"""() => {
        const out = [];
        const isOffer = h => /bing\.com\/search|imagepuzzle|\/quest\/|quiz/i.test(h);
        document.querySelectorAll("a[href]").forEach(a => {
            const h = a.href || '';
            if (!isOffer(h)) return;
            const t = (a.innerText||'').replace(/\s+/g,' ').trim();
            out.push({href:h, text:t, done:/已完成/.test(t),
                      tb:(a.getAttribute('target')||'').toLowerCase()});
        });
        return out;
    }""")


async def click_offer(page: Page, href: str, source_url: str = EARN_URL):
    """真人点击指定 href 的 <a>，返回 (目标页, 是否同页跳转, 备注)。

    用 JS 按「解析后的绝对 href」定位，避免链接属性为相对地址或含 &amp; 实体时，
    CSS 选择器 a[href="..."] 精确匹配失败（曾导致「未能定位/打开该活动」）。
    依据 target 属性判断：有 _blank 必弹新标签；否则同页跳转。
    """
    info = await page.evaluate("""(h) => {
        const els = Array.from(document.querySelectorAll('a[href]'));
        const m = els.find(a => a.href === h);
        if (!m) return null;
        m.setAttribute('data-offer-target', '1');
        return { tb: (m.getAttribute('target') || '').toLowerCase() };
    }""", href)
    if info is None:
        return None, False, "no-link"
    link = page.locator(SELECTORS["A_TARGET"]).first
    tb = info["tb"]
    if "blank" in tb:
        # D2：复用统一弹出原语（expect_popup + 真人点击），不重复内联 expect_popup
        popup = await click_link_get_popup(page, link)
        if popup is not None:
            await popup.wait_for_load_state("domcontentloaded")
            return popup, False, None
    # 同页跳转
    try:
        await safe_click(page, link)
    except Exception:
        pass
    await page.wait_for_timeout(2500)
    src = source_url.rstrip("/")
    # 点击未使 source 离开 -> 尝试 JS 触发
    if src in page.url:
        try:
            await link.evaluate("el => el.click()")
            await page.wait_for_timeout(2500)
        except Exception:
            pass
    # 仍停留在 source -> 最后回退 goto（可能脱离点击上下文而不计分）
    if src in page.url:
        print("    ⚠ 真人点击未触发跳转，回退 goto（可能不计分）")
        np = await page.context.new_page()
        await np.goto(href, wait_until="domcontentloaded", timeout=30000)
        await np.wait_for_timeout(2000)
        return np, False, "goto-fallback"
    return page, True, None


async def handle_punchcard(page: Page):
    """打卡/任务集页面：点击「继续/开始/领取」等按钮（best-effort），留存积分入账时间。"""
    await human_pause(2.0, 3.0)
    for kw in ["继续", "开始", "领取", "领取积分", "立即开始", "参与", "去完成"]:
        for _ in range(3):
            btn = page.locator(f"text={kw}").first
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    print(f"      点击「{kw}」")
                    await safe_click(page, btn)
                    await human_pause(1.5, 2.5)
            except Exception:
                continue
    await human_pause(2.0, 4.0)


async def _snapshot(target: Page, name: str):
    """把落到的任务页 HTML 存盘，便于离线精修选择器（可能含账户信息，仅本地）。

    默认关闭，避免每轮运行都在源目录堆积 dbg_*.html 且含账户信息；
    需排查时设环境变量 REWARDS_DEBUG=1 再跑即可启用。
    """
    if os.environ.get("REWARDS_DEBUG") != "1":
        return
    try:
        html = await target.content()
        with open(f"dbg_{name}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"    已保存 {name} 页面快照 dbg_{name}.html")
    except Exception:
        pass


async def run_offer(target: Page, href: str, typ: str):
    """在真正的任务页上按类型完成交互。target 是弹出页或已跳转的当前页。"""
    await human_pause(1.5, 2.5)
    await _snapshot(target, typ)  # 落页即存快照，便于后续精修拼图/问答选择器
    if typ == "search":
        # 带奖励参数的搜索页载入即计分，静候入账
        await human_pause(5.0, 9.0)
        # 极少数「每日活动」的 bing.com/search 链接内嵌真实问答(quiz)，需逐题点选；
        # 但必应搜索结果页本身不是 quiz——只有页面真含问答容器时才处理，
        # 避免把普通搜索页误当 quiz 去点选答案（用户实测：『探索阿马尔菲』『即将举行的体育赛事』等）。
        try:
            if await _has_real_quiz_elements(target):
                handled = await handle_puzzle_or_quiz(target)
                if handled:
                    print("    search 页检测到内嵌问答/拼图内容，已执行交互流程")
            else:
                print("    search 页无问答容器，按搜索即计分处理（不点选）")
        except Exception as e:
            print(f"    search 页问答补充处理异常: {e}")
    elif typ == "puzzle":
        await handle_puzzle_or_quiz(target)
    elif typ == "quiz":
        await handle_quiz(target)
    elif typ == "punchcard":
        await handle_punchcard(target)
    else:
        # 未知类型同样做一次内容嗅探：不能只按 href 猜类型，
        # 否则内嵌问答/拼图的任务会被当成「等一会儿就算完成」而漏做。
        try:
            if await handle_puzzle_or_quiz(target):
                print("    other 页识别为问答/拼图，已执行交互流程")
            else:
                await human_pause(3.0, 5.0)
        except Exception as e:
            print(f"    other 页问答补充处理异常: {e}")
            await human_pause(3.0, 5.0)
    await human_pause(2.0, 4.0)


async def task_dashboard_activities(page: Page, context: BrowserContext):
    """1b. 每日连续打卡活动：直接在 dashboard 页面点击「每日活动 / 赚取更多」子任务。

    用户反馈：dashboard（rewards.bing.com/dashboard）的「每日任务」即 earn 页的
    「每日连续打卡（活动: x/3）」，且子活动（如现场音乐/康沃尔/海洋巨人）直接在页面列出、
    带「已完成」标记。无需右侧栏，真人点击未完成的子活动即可计分；显示「已完成」即跳过。
    """
    print("[1b] 处理「每日连续打卡」（dashboard 直接点击每日活动）...")
    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3500)
    try:
        await _snapshot(page, "dashboard")
    except Exception:
        pass
    raw = await collect_offers(page)
    seen, tasks = set(), []
    for o in raw:
        if is_nav_link(o["href"]) or not o["text"]:
            continue
        if "DailySet" in o["href"]:
            # 每日活动三张卡片改由 task_dashboard_daily_set 专门处理（逐卡校验「已完成」+「可领取」领取），
            # 避免此处 fire-and-forget 式点击导致卡片静默未完成却无人发现。
            continue
        # 按 (href, 文本) 去重：同名/同链的子活动只点一次
        key = (o["href"], o["text"][:20])
        if key in seen:
            continue
        seen.add(key)
        if o["done"]:
            print(f"    跳过已完成：{o['text'][:24]}")
            continue
        tasks.append(o)
    print(f"    发现 {len(tasks)} 个未完成的每日活动")
    for o in tasks:
        typ = classify_offer(o["href"])
        print(f"  ▶ 每日活动：{o['text'][:30]}  [{typ}]")
        # 确保回到干净的 dashboard 页面（上一任务若同页跳转会在此重载回来）
        if DASHBOARD_URL.rstrip("/") not in page.url.rstrip("/"):
            await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        target, same_tab, note = await click_offer(page, o["href"], DASHBOARD_URL)
        if target is None:
            print("    ⚠ 未能定位/打开该活动，跳过")
            continue
        try:
            await run_offer(target, o["href"], typ)
        except Exception as e:
            print(f"    活动处理异常: {e}")
        finally:
            if same_tab:
                # 同页跳转：重载 dashboard 以返回并刷新状态
                try:
                    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                except Exception:
                    pass
            else:
                try:
                    await target.close()
                except Exception:
                    pass
        await human_pause(1.5, 3.0)
    print("    其它 dashboard 任务处理完成（每日活动由 task_dashboard_daily_set 专项处理）")


async def task_dashboard_daily_set(page: Page, context: BrowserContext = None, log=None):
    """2b. 每日活动三张卡片 + 「可领取」奖励积分：专属于 dashboard 的 SPA 流程。

    修复审计盲区（为什么「每日活动没做完却没被审计到」）：
      - 原审计只依赖 collect_offers 的链接匹配 + 「已完成」文本，且点击后从不校验卡片
        是否真的翻转为「已完成」（fire-and-forget，只 sleep 就走），卡片静默失败也无人发现；
      - 原流程完全没有「可领取」奖励的领取步骤，完成每日活动后的奖励积分无法入账，
        净增量审计因此可能「通过」而实际奖励缺失。
    本函数：逐卡点击跳转搜索 → 轮询「已完成」→ 领取「可领取」→ 校验可用积分增加；
    被 main 与 verify_and_retry 调用，使审计真正覆盖这一闭环。
    返回 {"total":int, "done":int, "claimed":int}。
    """
    ctx = context or page.context
    log = log or (lambda *a, **k: None)
    try:
        await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log(f"[daily_set] dashboard 打开失败: {e}")
    await page.wait_for_timeout(3000)

    # —— 定位「每日活动」折叠区面板，并确保展开 ——
    panel = None
    try:
        h2 = page.locator("h2", has_text="每日活动").first
        discl = h2.locator("xpath=ancestor::div[contains(@class,'react-aria-Disclosure')]").first
        panel = discl.locator("div.react-aria-DisclosurePanel").first
        # 确保折叠区已展开（aria-expanded=false 时需先点开，否则卡片不在 DOM 中）
        try:
            trigger = discl.locator("button[aria-expanded]").first
            if await trigger.get_attribute("aria-expanded") == "false":
                await trigger.click(timeout=5000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
    except Exception:
        panel = None
    cards = []
    if panel is not None:
        try:
            cards = await panel.locator(f"a[href*='{SELECTORS['SEARCH_HREF']}']").all()
        except Exception:
            cards = []
    if not cards:
        # 兜底：整页直接找 DailySet 卡片
        try:
            cards = await page.locator(SELECTORS["DS_CARD"]).all()
        except Exception:
            cards = []

    total = len(cards)
    done = 0
    for idx, card in enumerate(cards):
        try:
            txt = await card.inner_text()
        except Exception:
            txt = ""
        if "已完成" in txt:
            done += 1
            continue
        log(f"[daily_set] 处理第 {idx+1}/{total} 张每日活动卡片 ...")
        try:
            async with ctx.expect_page(timeout=8000) as pop:
                try:
                    await card.click(timeout=5000)
                except Exception:
                    await card.click(force=True, timeout=5000)
            popup = await pop.value
            await popup.wait_for_timeout(3000)
            # 弹窗内可能是 puzzle/quiz 互动题，需答完才计完成（函数名须用真实存在的
            # handle_puzzle_or_quiz；此前误写 _check_puzzle_or_quiz 会抛 NameError
            # 并被外层 except 吞掉，静默走到「非弹窗」分支）
            try:
                await handle_puzzle_or_quiz(popup)
            except Exception as _e:
                log(f"[daily_set] puzzle/quiz 处理异常: {_e}")
            try:
                await popup.close()
            except Exception:
                pass
        except Exception:
            # 非弹窗：原地导航（等待返回 dashboard 并刷新状态）
            await page.wait_for_timeout(4000)
            try:
                if DASHBOARD_URL.rstrip("/") not in page.url:
                    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(2000)
            except Exception:
                pass
        # 轮询该卡片是否翻转为「已完成」（最多约 18s）
        ok = False
        for _ in range(12):
            try:
                if "已完成" in await card.inner_text():
                    ok = True
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1500)
        if ok:
            done += 1
            log(f"[daily_set] 第 {idx+1} 张卡片已完成 ✓")
        else:
            log(f"[daily_set] 第 {idx+1} 张卡片点击后未检测到「已完成」标记")

    # —— 领取「可领取」奖励积分 ——
    claimed = 0
    try:
        claim_btn = page.locator("button:has-text('可领取')").first
        # 注意：async API 的 count() 返回 coroutine，未 await 时恒为真值 → 必须 await
        if await claim_btn.count() and await claim_btn.is_visible():
            num_txt = ""
            try:
                num_txt = await claim_btn.locator("p.text-pageHeader").inner_text()
            except Exception:
                num_txt = await claim_btn.inner_text()
            m = re.search(r"\d+", num_txt.replace(",", ""))
            amt = int(m.group()) if m else 0
            if amt > 0:
                log(f"[daily_set] 检测到可领取 {amt} 积分，点击领取 ...")
                before = await read_available_points(page)
                try:
                    await claim_btn.click(timeout=5000)
                except Exception:
                    await claim_btn.click(force=True, timeout=5000)
                # 点击「可领取」后，右侧栏(flyout)弹出，其下方会出现「领取积分」按钮，
                # 点击它才会真正把奖励积分入账（可领取归零、总积分增加）。
                # 必须先等 flyout 真正弹出，再在 flyout 内定位，否则页面级 text= 易因时机/匹配超时。
                try:
                    await page.wait_for_timeout(2500)  # 等右侧栏渲染
                    # 先确认右侧栏已弹出
                    fly = await find_flyout(page)
                    claim2 = None
                    if fly is not None:
                        # flyout 内的「领取积分」：优先 button/可点击元素，再退化为任意含该文字的元素
                        for sel in ["button:has-text('领取积分')",
                                    "a:has-text('领取积分')",
                                    "div:has-text('领取积分')",
                                    "text=领取积分"]:
                            cand = fly.locator(sel).first
                            if await cand.count() and await cand.is_visible():
                                claim2 = cand
                                break
                    if claim2 is None:
                        # flyout 内没找到，退化为页面级（并放宽到含「领取」的可点击元素）
                        for sel in ["button:has-text('领取')",
                                    "a:has-text('领取')",
                                    "text=领取积分"]:
                            cand = page.locator(sel).first
                            if await cand.count() and await cand.is_visible():
                                claim2 = cand
                                break
                    if claim2 is None:
                        raise RuntimeError("点击『可领取』后未出现『领取积分』")
                    await claim2.click(timeout=5000)
                except Exception as e:
                    log(f"[daily_set] 未找到/点击『领取积分』: {e}")
                    # 诊断：落盘当前页面 + flyout HTML，便于离线核对「领取积分」真实结构
                    try:
                        await _snapshot(page, "dbg_claim")
                        f2 = await find_flyout(page)
                        if f2 is not None:
                            html = await f2.inner_html()
                            import os as _os
                            _os.makedirs("dbg", exist_ok=True)
                            with open("dbg/dbg_claim_flyout.html", "w", encoding="utf-8") as _fh:
                                _fh.write(html)
                    except Exception:
                        pass
                await page.wait_for_timeout(3000)
                after = await read_available_points(page)
                claimed = max(0, (after or 0) - (before or 0))
                log(f"[daily_set] 领取后可用积分 {before} -> {after} (Δ={claimed})")
            else:
                log("[daily_set] 可领取金额为 0，无需领取")
        else:
            log("[daily_set] 未发现可领取卡片")
    except Exception as e:
        log(f"[daily_set] 可领取流程异常: {e}")

    log(f"[daily_set] 每日活动完成 {done}/{total}，本次领取 {claimed} 积分")
    return {"total": total, "done": done, "claimed": claimed}


async def task_monthly_strategy(page: Page, context: BrowserContext):
    """1c. 本月攻略周任务：4 周，每周一个，带绿勾的跳过、无绿勾的点击其搜索链接。

    识别键见 is_monthly_strategy——绝不写死月份名 / 子任务名（见 ADR-0002）。
    用户说明：该任务每月更换文案（如「让这个八月收获更多」），含 4 个周子任务，
    每个是一个跳转搜索链接；已完成的子任务会显示一个绿色勾。规则：
      - 有绿勾 -> 不点击，直接跳过（关闭页面）
      - 无绿勾 -> 点击一下，跳转搜索页计分，关闭页面，看到绿勾即可
    故处理：点进 punchcard 详情页 -> 收集 4 个周搜索链接 -> 逐个判断是否已勾选
    -> 未勾选才真人点击（弹窗）-> 关闭弹窗。
    """
    print("[1c] 处理「本月攻略」周任务（4 周，有绿勾跳过、无绿勾点击）...")
    # 回到干净的 earn 页
    if EARN_URL.rstrip("/") not in page.url.rstrip("/"):
        await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)
    # 在 earn 页定位本月攻略卡片（识别键见 is_monthly_strategy，绝不写死月份名）
    card = None
    july_href = ""
    try:
        offers = await collect_offers(page)
    except Exception:
        offers = []
    for o in offers:
        if is_monthly_strategy(o.get("text", ""), o.get("href", "")):
            july_href = o.get("href", "")
            card = page.locator('a[href*="BingMonthlyPC"]').first
            break
    if card is None:
        print("    未找到本月攻略卡片（定位器零匹配）——结构级异常，已计入告警")
        run_state.structural_failures.append(
            "本月攻略定位器整月零匹配：is_monthly_strategy 在 earn 页未找到任何本月攻略卡片"
        )
        return
    popup = await click_link_get_popup(page, card)
    if popup is not None:
        await popup.wait_for_load_state("domcontentloaded")
        await human_pause(2.5, 4.5)
        target = popup
        print("    已进入本月攻略 punchcard 详情（新标签）")
    else:
        # 同页跳转：当前页即为详情页；若点击未触发导航则回退 goto
        target = page
        await human_pause(2.5, 4.5)
        if "/quest/" not in page.url and july_href:
            print("    同页未跳转，回退 goto 进入本月攻略详情")
            try:
                await page.goto(july_href, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3500)
            except Exception:
                pass
        print("    已进入本月攻略 punchcard 详情")
    try:
        try:
            await _snapshot(target, "monthly")
        except Exception:
            pass

        # 基于「周任务行」结构化判定（DOM 忠实版，2026-09 审计后重写）：
        #   - 不再依赖 <h3> 作为唯一锚点（避免微软改版后"整段静默漏扫"）；
        #   - 废弃页面中实际不存在的空圈 class ctrlChoiceBaseStroke（dbg 快照计数=0），
        #     只信正向信号：绿勾徽章 bg-statusSuccessRewardsBg / 行内跳转搜索链接 / 锁图标。
        # 每行返回 {done, locked, href, title, links, no_href}。
        async def scan_punchcard_rows(pg):
            return await pg.evaluate(r"""() => {
                const GREEN = /statusSuccessRewardsBg|successrewards|completed|checkmark|已打卡|已完成|✓|✔|<svg[^>]*aria-label="?completed/i;
                const LOCK_TXT = /4周后|\d+\s*周\s*(后|内)|\d+\s*天\s*(后|内)|到期日期|锁定|未解锁|即将开放|未开放|暂不可/i;
                function hasLockIcon(el){
                    if(!el) return false;
                    const q = '[class*="lock" i]:not([class*="unlock" i]), [aria-label*="lock" i], [aria-label*="锁定" i], [title*="lock" i], [title*="锁定" i]';
                    try { return !!el.querySelector(q); } catch(e){ return false; }
                }
                // 从三类种子元素向上收敛到「周任务行」容器，再按容器去重：
                //  绿勾徽章、锁图标、行内跳转搜索链接（h3 不再是唯一入口）。
                const seeds = Array.from(document.querySelectorAll(
                    '[class*="statusSuccessRewardsBg"], [class*="lock" i]:not([class*="unlock" i]), a[href*="bing.com/search"]'
                ));
                const rows = [];
                const seen = new Set();
                for (const el of seeds) {
                    let row = el;
                    for (let i = 0; i < 8 && row; i++) {
                        const rhtml = row.innerHTML || '';
                        const rcls = row.className || '';
                        // 收敛为「同时含有状态图标 + 文本 +（可选）搜索链接」的最小行容器
                        const hasBadge = /statusSuccessRewardsBg|ctrlChoiceBaseStroke/.test(rhtml)
                            || row.querySelector('a[href*="bing.com/search"]')
                            || /rewardsCard|rewardsModule|disclosure|item-|module|quest|punchcard|rewardRow|d1c/.test(rcls);
                        const hasText = (row.innerText || '').trim().length > 0;
                        if (hasBadge && hasText) break;
                        row = row.parentElement;
                    }
                    if (!row || seen.has(row)) continue;
                    seen.add(row);
                    const rhtml = row.innerHTML || '';
                    const rtext = (row.innerText || '').replace(/\s+/g, ' ');
                    const green = GREEN.test(rhtml) || GREEN.test(rtext);
                    // 周任务跳转链接：优先「_blank + bing.com/search」直链，逐层放宽到
                    // rewards.bing.com 内 search/quest 形式、乃至行内任意非导航 <a href>，
                    // 避免「周链接 host 不是 bing.com/search 字面量」时取到空 href 而不被点击。
                    let cand = row.querySelector('a[target="_blank"][href*="bing.com/search"]')
                            || row.querySelector('a[href*="bing.com/search"]')
                            || row.querySelector('a[target="_blank"][href*="search"]')
                            || row.querySelector('a[href*="search"]')
                            || row.querySelector('a[href*="quest"]')
                            || row.querySelector('a:not([href^="#"]):not([href=""])');
                    const href = cand ? cand.href : '';
                    // 微软会在“已开放”的行文案中写“完成第 2 天后等待 24 小时”。
                    // 这不是锁定状态；只要行内存在启用的前往链接，就以按钮状态为准。
                    const enabledAction = !!cand
                        && !cand.hasAttribute('disabled')
                        && cand.getAttribute('aria-disabled') !== 'true'
                        && !/disabled|is-disabled/i.test(cand.className || '')
                        && getComputedStyle(cand).pointerEvents !== 'none';
                    const disabledAction = !!cand
                        && !enabledAction
                        && (cand.hasAttribute('disabled')
                            || cand.getAttribute('aria-disabled') === 'true'
                            || /disabled|is-disabled/i.test(cand.className || ''));
                    const lockHit = hasLockIcon(row)
                        || (LOCK_TXT.test(rtext) && !enabledAction && !disabledAction);
                    const allLinks = Array.from(row.querySelectorAll('a[href]'))
                        .map(a => (a.href || '')).filter(Boolean);
                    const title = (row.querySelector('h3, p[class*="Strong"], [class*="globalBody2Strong"], span[class*="body"]')?.innerText
                        || row.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40);
                    rows.push({
                        done: green && !lockHit,
                        locked: lockHit && !green,
                        href,
                        title,
                        links: allLinks.slice(0, 8),
                        no_href: !href || !enabledAction,
                        actionable: enabledAction,
                    });
                }
                // 稳定去抖：同一 title 重复出现的容器只留一个
                const byTitle = new Map();
                for (const r of rows) byTitle.set(r.title || r.href, r);
                return Array.from(byTitle.values());
            }""")

        rows = await scan_punchcard_rows(target)
        weekly_done = sum(1 for r in rows if r["done"])
        weekly_locked = sum(1 for r in rows if r["locked"])
        weekly_open = len(rows) - weekly_locked
        _write_monthly_state(weekly_done, weekly_open)
        print(f"    本月攻略共 {len(rows)} 个周任务行（含锁定未开放 {weekly_locked} 个）")
        for i, r in enumerate(rows):
            if r["done"]:
                mark = "✓绿勾"
            elif r["locked"]:
                mark = "🔒锁定"
            else:
                mark = "○未勾"
            warn = "" if r["href"] else "  ⚠href为空(链接提取失败，将不会被点击)"
            print(f"      [{i}] {mark}  {r['title'][:30]}  {r['href'][:50]}{warn}")
            for lk in r.get("links", [])[:4]:
                print(f"           link: {lk[:90]}")
        # 仅处理「未勾选且未锁定」的当前开放周；锁定周直接忽略（不点击、不计入未完成）
        # 无法提取到前往链接的开放周（no_href）不静默跳过，而是写入复检清单，避免漏做。
        uniq = [r for r in rows if not r["done"] and not r["locked"]]
        blockers = [r for r in uniq if r["no_href"]]
        doable = [r for r in uniq if r["href"] and r.get("actionable", True)]
        if blockers:
            for b in blockers:
                print(f"    ⚠ 开放周「{b['title'][:30]}」href 提取失败，本轮无法点击，已记入复检清单")
                run_state.unfinished.append(f"{b['title'][:40]}  [周前往链接提取失败/为空]")
        print(f"    其中未勾选且非锁定的开放周：{len(uniq)} 个（可合法点击 {len(doable)}，待排查 {len(blockers)}）")
        # 页面按周只会开放一个蓝色“前往”按钮；本轮最多点击一个，
        # 防止延迟入账或微软页面重复渲染导致同一月度任务被连续触发。
        for r in doable[:1]:
            if not week_gate_ok(r["title"]):
                print(f"    ⏭ 周门控跳过：{r['title'][:30]}（本周已处理过）")
                continue
            print(f"  ▶ 真人点击未勾选周任务：{r['title'][:30]}  ->  {r['href'][:60]}")
            week_mark_clicked(r["title"])  # 记录"已在本周点击过"，供周门控防重复
            # 用 Locator 按绝对 href 定位（比 evaluate_handle 更稳），真人点击弹出搜索页
            sp = None
            link = target.locator(f'a[href="{r["href"]}"]').first
            if await link.count() > 0:
                sp = await click_link_get_popup(target, link)  # 内部 expect_popup(8000) + 真人点击
            if sp is None:
                # 回退 1：宽松选择器 + 更长超时（复用统一弹出原语，放宽到 15s）
                link2 = target.locator(f'a[href*="{SELECTORS["SEARCH_HREF"]}"]').first
                if await link2.count() > 0:
                    sp = await click_link_get_popup(target, link2, timeout=15000)
            if sp is not None:
                # 真实弹出搜索页（保留从 Rewards 点击的上下文，微软才正确计分）
                try:
                    await sp.wait_for_load_state("domcontentloaded")
                    await humanize_read(sp)  # 拟真化阅读 + 静候入账（替代裸 sleep）
                finally:
                    await sp.close()  # 关闭跳转页面（用户要求：点击后关闭）
            else:
                # 回退 2：仍无法弹出 -> 新标签 goto（URL 含 OCID/PUBL 奖励参数，可能仍计分）
                print("      ⚠ 仍未能弹出搜索页，回退新标签 goto 兜底（可能不计分）")
                np = await context.new_page()
                try:
                    await np.goto(r["href"], wait_until="domcontentloaded", timeout=30000)
                    await humanize_read(np)
                finally:
                    await np.close()
            # 单周完成闭环校验：reload punchcard 页再扫一次，确认该周绿勾翻转
            # 区分「点击了但微软不计分」与「压根没点到」，对应 ADR-0002 的涨分自审思路
            await human_pause(1.5, 3.0)
            try:
                await target.reload(wait_until="domcontentloaded")
                await target.wait_for_timeout(4000)
                rows_chk = await scan_punchcard_rows(target)
                matched = next((x for x in rows_chk
                                if x["title"][:20] == r["title"][:20]), None)
                if matched and matched["done"]:
                    print(f"      ✓ 单周校验通过：{r['title'][:30]} 已翻转为绿勾")
                    week_mark_done(r["title"])  # 翻绿后本周期内不再碰
                else:
                    print(f"      ⚠ 单周校验失败：{r['title'][:30]} 点击后仍未标记完成（可能计入延迟，本周不再重复点）")
            except Exception as e:
                print(f"      （单周校验异常：{e}）")
        if len(doable) > 1:
            print(f"    本轮仅处理第一个启用周任务，其余 {len(doable) - 1} 个留待下次复检")
        # 处理完刷新 punchcard 页（DOM 需重载才反映新绿勾），再读一次状态确认
        try:
            await target.reload(wait_until="domcontentloaded")
            await target.wait_for_timeout(4000)
        except Exception:
            try:
                await target.goto(target.url, wait_until="domcontentloaded", timeout=60000)
                await target.wait_for_timeout(4000)
            except Exception:
                pass
        try:
            rows2 = await scan_punchcard_rows(target)
            done_cnt = sum(1 for x in rows2 if x["done"])
            print(f"    本月攻略周任务总计 {len(rows2)}，已勾选(绿勾) {done_cnt}，未勾选 {len(rows2) - done_cnt}")
        except Exception:
            pass
    finally:
        if popup is not None:
            try:
                await popup.close()
            except Exception:
                pass
        else:
            try:
                await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass
    print("    本月攻略处理完成")


async def process_offers(page: Page, context: BrowserContext):
    """统一处理所有赚分任务：像之前能成功那样，按卡片「可见文字」真人点击、捕获弹出新标签页。

    关键：用文字在 earn 页面重新定位卡片 <a>，真人点击并 expect_popup 捕获新标签
    （保留「从 Rewards 点击」的上下文，微软才正确计分）。拼图页内点 #skipPuzzle，
    搜索页静候入账；处理完关闭弹窗。不再用 a.href===h 的精确匹配（曾导致拼图
    「未能定位」被跳过）。
    """
    print("[*] 处理所有赚分任务（真人点击卡片 / 链接）...")
    # 先回到干净的 earn 页（上一步在 dashboard 操作，若直接 collect 会收集到 dashboard 链接而漏掉拼图等）
    if EARN_URL.rstrip("/") not in page.url.rstrip("/"):
        await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)
    raw = await collect_offers(page)
    seen, tasks = set(), []
    for o in raw:
        if is_nav_link(o["href"]) or not o["text"]:
            continue
        # 本月攻略由 task_monthly_strategy 专门处理（需进 punchcard 逐个点周链接）
        if is_monthly_strategy(o["text"], o["href"]):
            continue
        # 必应搜索连续打卡（搜索: x/1）由 task_search_card 专门处理：需真实搜索一次才计分，
        # 不能走 process_offers 的「载入即计分」路径（那只等待、不真正搜索 -> 永远 0/1）
        if "必应搜索" in o["text"]:
            continue
        key = o["text"][:24]
        if key in seen:
            continue
        seen.add(key)
        if o["done"]:
            print(f"    跳过已完成：{o['text'][:24]}")
            continue
        tasks.append(o)
    print(f"    发现 {len(tasks)} 个未完成任务")
    for o in tasks:
        typ = classify_offer(o["href"])
        print(f"  ▶ 任务：{o['text'][:30]}  [{typ}]")
        # 确保处于干净的 earn 页面（上一次若是同页跳转，会在此重载回来）
        if "rewards.bing.com/earn" not in page.url:
            await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
        # 用可见文字重新定位卡片 <a>（与之前能成功的方式一致，避免 href 精确匹配失败）
        kw = o["text"].split(" ")[0][:14]
        link = page.locator("a").filter(has_text=kw).first
        try:
            if await link.count() == 0:
                print(f"    ⚠ 未定位到卡片「{kw}」，跳过")
                continue
            popup = await click_link_get_popup(page, link)
        except Exception as e:
            print(f"    ⚠ 点击卡片异常: {e}")
            popup = None
        if popup is not None:
            try:
                await popup.wait_for_load_state("domcontentloaded")
                await human_pause(2.0, 3.5)
                await run_offer(popup, o["href"], typ)
            except Exception as e:
                print(f"    任务处理异常: {e}")
            finally:
                try:
                    await popup.close()
                except Exception:
                    pass
        else:
            # 未弹出新标签（同页跳转）：在当前页处理后回退 earn
            print("    未弹出新标签，尝试同页处理并回退")
            try:
                await human_pause(2.0, 3.5)
                await run_offer(page, o["href"], typ)
            except Exception as e:
                print(f"    同页处理异常: {e}")
            try:
                await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass
        await human_pause(1.5, 3.0)
    print("    全部任务处理完成")


async def collect_hrefs(target) -> list:
    """从 Page 或 Locator 收集所有 a[href]（自动区分两种 evaluate 签名）"""
    try:
        if isinstance(target, Page):
            return await target.evaluate("""() => {
                const out = [];
                document.querySelectorAll("a[href]").forEach(a => { const h = a.href || ''; if (h) out.push(h); });
                return out;
            }""")
        return await target.evaluate("""el => {
            const out = [];
            el.querySelectorAll("a[href]").forEach(a => { const h = a.href || ''; if (h) out.push(h); });
            return out;
        }""")
    except Exception as e:
        print(f"    收集链接失败: {e}")
        return []


async def get_offer_cards(page: Page) -> Locator:
    """Bing Rewards 的赚分卡片（多重 class 兜底）"""
    return page.locator(
        "div[class*='offerCard'], div[class*='OfferCard'], "
        "div[class*='card'],[role='article'], section[class*='card']"
    )


async def find_card_by_keyword(page: Page, keyword: str) -> Locator | None:
    """返回第一个包含关键字的卡片定位器（class 兜底 + 可见文字兜底）。

    Bing Rewards 的卡片 class 经常变化，纯靠 class 容易匹配不到（诊断曾显示
    候选卡片数: 0）。这里在 class 失败时，再用卡片标题等可见文字直接定位。
    """
    cards = await get_offer_cards(page)
    n = await cards.count()
    for i in range(n):
        c = cards.nth(i)
        try:
            txt = await c.inner_text()
        except Exception:
            continue
        if keyword in txt:
            return c
    # 兜底：直接用可见文字定位（keyword 应使用足够独特的短语，如卡片标题）
    try:
        loc = page.locator(f"text={keyword}").first
        if await loc.count() > 0:
            return loc
    except Exception:
        pass
    return None


async def click_card_open_flyout(page: Page, card: Locator):
    """点击卡片打开右侧栏；若未弹出，则尝试点击卡片内的「开始/继续」等按钮。"""
    await click_human(page, card)
    await human_pause(1.0, 2.0)
    if await find_flyout(page) is None:
        for label in ["开始", "继续", "立即开始", "了解更多", "去完成", "参与", "立即参与"]:
            btn = card.locator("a,button").filter(has_text=label).first
            if await btn.count() > 0:
                try:
                    await click_human(page, btn)
                    await human_pause(1.0, 2.0)
                    break
                except Exception:
                    continue


async def find_flyout(page: Page) -> Locator | None:
    """侧边弹出的详情面板（多种兜底）"""
    for sel in ["#rewardsFlyout", ".flyout", "[role='dialog']",
                "div[class*='flyout' i]", "div[class*='drawer' i]",
                "div[class*='panel' i]", "div[class*='modal' i]",
                "div[class*='sidebar' i]"]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def click_link_get_popup(page: Page, link: Locator, timeout: int = 8000) -> Page | None:
    """真人鼠标点击一个链接/卡片元素，返回它弹出的新标签页（无则 None）。

    这是「模仿真人从 Rewards 页点击卡片」的**唯一弹出原语**（D2）：
    EARN 点击 / 周任务跳转 / 兜底重试都复用本函数，内部 expect_popup + 真人点击，
    不再各写各的 expect_popup。timeout 供特殊场景（宽松兜底）放宽到 15s。
    保留点击上下文，微软才会正确计分（直接 goto href 会脱离上下文导致不计分）。
    """
    popup = None
    try:
        async with page.expect_popup(timeout=timeout) as pi:
            await click_human(page, link)
        popup = await pi.value
    except Exception:
        popup = None
    return popup


async def process_task_page(target: Page, href: str, is_search: bool):
    """在任务页（弹出的新标签页或当前页）上完成交互并等待积分入账。"""
    if is_search:
        # 带奖励参数的搜索页「载入即计分」，切勿再输入词/回车，静候入账
        await human_pause(5.0, 9.0)
    else:
        # 拼图/问题页：真人点击 -> 跳过 -> （由调用方关闭）
        await handle_puzzle_or_quiz(target)
    await human_pause(2.0, 4.0)


# ====================== 任务实现 ======================
async def task_search_card(page: Page, context: BrowserContext):
    """1a. 必应搜索连续打卡（搜索: 1/1）：需真正搜索一次才计分。

    真人点击卡片 -> 右侧栏 -> 真人点击搜索跳转链接 -> 在弹出的搜索页真实搜一次 -> 关闭。
    该卡片可能在 earn 或 dashboard 页，逐页尝试；关键词兼容「必应搜索连续打卡 / 使用必应搜索」。
    """
    print("[1a] 处理「必应搜索」连续打卡...")
    card = None
    for url in [EARN_URL, DASHBOARD_URL]:
        # 必应奖励卡片的「搜索: x/1」进度徽标是异步加载的，必须等其渲染就绪，
        # 否则会读到「0/1」甚至还没出数字，误判为未完成而重复搜索。
        # 注意：不能只在「跨页面跳转」时才等——主流程/复检调用本函数时页面往往
        # 已停在 EARN 页，会跳过 goto 与等待，直接读到未就绪的徽标。故无论是否跳转都等待。
        if url.rstrip("/") not in page.url.rstrip("/"):
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4500)
        for kw in ["必应搜索连续打卡", "必应搜索", "使用必应搜索"]:
            c = await find_card_by_keyword(page, kw)
            if c is not None:
                card = c
                break
        if card is not None:
            break
    if card is None:
        print("    未找到必应搜索卡片，跳过")
        return
    # 读取完成状态：进度徽标可能仍在异步校验，先等两轮确认，避免误把已 1/1 当成未完成
    txt = await card.inner_text()
    if "1/1" in txt or "1 / 1" in txt:
        print("    已显示 搜索: 1/1，跳过")
        return
    if "0/1" not in txt and "0 / 1" not in txt and "/" not in txt:
        # 进度数字尚未渲染（既无 1/1 也无 0/1），再等一会儿重新读取后判定
        await page.wait_for_timeout(3000)
        txt = await card.inner_text()
        if "1/1" in txt or "1 / 1" in txt:
            print("    二次读取已显示 搜索: 1/1，跳过")
            return
    # 至此确认尚未完成（含明确的 0/1），需要真实搜索一次
    # 点卡片 -> 右侧栏 -> 真人点击搜索跳转链接（保留上下文，正确计分）
    before = set(await collect_hrefs(page))
    await click_card_open_flyout(page, card)
    await human_pause(1.5, 2.5)
    flyout = await find_flyout(page)
    hrefs = await collect_hrefs(flyout) if flyout is not None else []
    if not hrefs:
        after = await collect_hrefs(page)
        hrefs = [h for h in after if h not in before]
    link = next((h for h in hrefs if not is_nav_link(h) and ("bing.com" in h or "search" in h.lower())), None)
    if not link:
        print("    未找到搜索跳转链接，跳过")
        return
    a = page.locator(f'a[href="{link}"]').first
    popup = await click_link_get_popup(page, a) if await a.count() > 0 else None
    if popup is not None:
        try:
            await popup.wait_for_load_state("domcontentloaded")
            await human_pause(2.0, 4.0)
            if await popup.locator("#sb_form_q").count() > 0:
                await perform_search_on_page(popup)
            await human_pause(3.0, 5.0)
        finally:
            await popup.close()
    else:
        np = await context.new_page()
        try:
            await np.goto(link, wait_until="domcontentloaded", timeout=30000)
            await human_pause(2.0, 4.0)
            if await np.locator("#sb_form_q").count() > 0:
                await perform_search_on_page(np)
            await human_pause(3.0, 5.0)
        finally:
            await np.close()
    print("    完成一次搜索，期望显示 搜索: 1/1")


# ====================== 页面诊断 ======================
async def debug_dump(page: Page):
    """打印页面候选卡片与相关链接，用于精修选择器（不依赖猜测）"""
    print("\n========== 页面诊断 ==========")
    print("URL:", page.url)
    print("标题:", await page.title())
    data = await page.evaluate(r"""() => {
        const out = {cards: [], links: []};
        const cards = document.querySelectorAll(
            "div[class*='card'], section[class*='card'], [role='article'], div[class*='offer']");
        cards.forEach(c => {
            const t = (c.innerText||'').replace(/\s+/g,' ').trim();
            if (t.length < 3) return;
            out.cards.push({cls: (c.className||'').toString().slice(0,90), text: t.slice(0,180)});
        });
        document.querySelectorAll("a[href]").forEach(a => {
            const h = a.href || '';
            if (/bing\.com|rewards\.microsoft|rewards\.bing|microsoft\.com\/rewards/.test(h)) {
                const t = (a.innerText||'').replace(/\s+/g,' ').trim().slice(0,60);
                out.links.push({href: h.slice(0,140), text: t});
            }
        });
        return out;
    }""")
    print(f"候选卡片数: {len(data['cards'])}")
    for i, c in enumerate(data['cards'][:40]):
        print(f"  [{i}] cls={c['cls']}")
        print(f"      text={c['text']}")
    print(f"相关链接数: {len(data['links'])}")
    for i, l in enumerate(data['links'][:50]):
        print(f"  ({i}) {l['text']}  ->  {l['href']}")
    # 同时打印「可处理任务」清单（按真实 <a href> 提取，不依赖卡片容器）
    try:
        offers = await page.evaluate(r"""() => {
            const out = [];
            const isOffer = h => /bing\.com\/search|imagepuzzle|\/quest\/|quiz/i.test(h);
            document.querySelectorAll("a[href]").forEach(a => {
                const h = a.href || '';
                if (!isOffer(h)) return;
                const t = (a.innerText||'').replace(/\s+/g,' ').trim();
                out.push({href:h, text:t, done:/已完成/.test(t),
                          tb:(a.getAttribute('target')||'').toLowerCase()});
            });
            return out;
        }""")
        print(f"可处理任务数: {len(offers)}")
        for i, o in enumerate(offers):
            tag = "✓已完成" if o["done"] else f"→{o['tb'] or '同页'}"
            print(f"  ({i}) [{tag}] {o['text'][:40]}  ->  {o['href'][:90]}")
    except Exception as e:
        print("  （任务清单提取失败:", e, "）")
    print("==============================\n")


# ====================== 主流程 ======================
def _looks_done(text: str) -> bool:
    """从卡片可见文字判断是否已完成：含「已完成/已领取」，或形如 1/1、3/3、4/4 的进度打满。"""
    t = text or ""
    flat = t.replace(" ", "")
    if "已完成" in flat or "已领取" in flat:
        return True
    # 注意：不能先去空格再匹配，否则「50 4/4」会被粘成「504/4」而误判未完成
    pairs = re.findall(r"(?<![\d/])(\d+)\s*/\s*(\d+)(?![\d/])", t)
    return bool(pairs) and all(a == b for a, b in pairs)


async def _scan_unfinished(page: Page) -> list:
    """重新采集 dashboard + earn，返回页面上仍未标记完成的任务（以页面状态为准）。"""
    out, seen = [], set()
    task_count = 0
    for url in (DASHBOARD_URL, EARN_URL):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            await _require_rewards_page(page, response)
            raw = await collect_offers(page)
            raw = [o for o in raw if o["text"] and not is_nav_link(o["href"])]
            task_count += len(raw)
        except (AuthenticationRequired, VerificationError):
            raise
        except Exception as e:
            raise VerificationError(f"VERIFY_FAILED: 无法完成 {url} 的复检（{type(e).__name__}）。") from e
        for o in raw:
            if is_nav_link(o["href"]) or not o["text"]:
                continue
            # 本月攻略的「锁定周」（左侧小锁，未到开放时间）既不是已完成也不是未完成，
            # 不应重试、也不应写入「未完成」邮件清单（否则每月会误报未来周任务）。
            # 时间判定优先：月任务共 4 周、每周一个，已完成周数 >= 当前所处周序号即属正常
            # （如八月第一周显示 1/4）——即使文案未出现「到期日期/X周后」也不误报。
            if is_monthly_strategy(o["text"], o["href"]):
                done_n, _ = _parse_monthly_progress(o["text"])
                if done_n is not None and done_n >= current_month_week_index():
                    continue
                if is_locked_monthly(o["text"], o["href"]):
                    continue
            if o["done"] or _looks_done(o["text"]):
                continue
            key = o["text"][:24]
            if key in seen:
                continue
            seen.add(key)
            o["src"] = url
            out.append(o)
    if task_count == 0:
        raise VerificationError(
            "VERIFY_NO_TASKS: dashboard 与 earn 均未识别到任务，不能判定为全部完成。"
        )
    return out


async def verify_and_retry(page: Page, context: BrowserContext):
    """收尾复检 + 自动重试：以页面「已完成」标记为准兜底。

    动机：任务是否做成不能靠 classify_offer 按 href 猜（曾把内嵌问答的
    bing.com/search 链接当成「载入即计分」而只 sleep，导致该活动一直没完成）。
    这里跑完所有流程后重新扫描一次，凡是页面仍未标记完成的就再跑一轮；
    仍未完成的写入 run_state.unfinished，由结果邮件直接告知，不必人工比对网页。
    """
    print("[✓] 收尾复检：重新扫描未完成任务 ...")
    # 先把「每日活动」三张卡片 +「可领取」奖励积分做完（含完成校验与领取），
    # 否则通用复检只认链接、看不到卡片静默未完成，也永远不会领取奖励积分。
    try:
        await task_dashboard_daily_set(page, context, log=print)
    except Exception as e:
        print(f"  ✗ 复检-每日活动专处理异常: {e}")
    remain = await _scan_unfinished(page)
    run_state.unfinished.clear()
    if not remain:
        print("    复检通过：本轮识别到的任务均已标记完成")
        return
    print(f"    复检发现 {len(remain)} 项仍未完成，开始重试：")
    retried_special = set()
    for o in remain:
        text, href = o["text"], o["href"]
        src = o.get("src", EARN_URL)
        print(f"  ↻ 重试：{text[:30]}")
        try:
            # 专项任务走各自的处理器（它们内部有自己的完成判断与交互方式）
            if "必应搜索" in text:
                if "search" not in retried_special:
                    retried_special.add("search")
                    await task_search_card(page, context)
                continue
            if is_monthly_strategy(text, href):
                if "monthly" not in retried_special:
                    retried_special.add("monthly")
                    await task_monthly_strategy(page, context)
                continue
            if src.rstrip("/") not in page.url.rstrip("/"):
                await page.goto(src, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
            target, same_tab, _note = await click_offer(page, href, src)
            if target is None:
                print("    ⚠ 重试时未能定位/打开该任务")
                continue
            try:
                await run_offer(target, href, classify_offer(href))
            finally:
                if same_tab:
                    await page.goto(src, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(2500)
                else:
                    try:
                        await target.close()
                    except Exception:
                        pass
        except Exception as e:
            print(f"    重试异常：{e}")
        await human_pause(1.5, 3.0)
    # 二次复检：仍未完成的记入邮件清单（只报不再重试，避免风控与死循环）
    still = await _scan_unfinished(page)
    run_state.unfinished.extend(f"{o['text'][:40]}  [{o['href'][:70]}]" for o in still)
    if run_state.unfinished:
        print(f"    ⚠ 重试后仍有 {len(run_state.unfinished)} 项未完成，将写入结果邮件：")
        for t in run_state.unfinished:
            print(f"      · {t}")
    else:
        print("    重试后全部完成")
    # 末次兜底：确保「可领取」奖励积分在本轮结束前被领取（即使上方重试未触碰它）
    try:
        await task_dashboard_daily_set(page, context, log=print)
    except Exception as e:
        print(f"  ✗ 末次-每日活动专处理异常: {e}")


async def _launch_context(p) -> BrowserSession:
    """按运行环境创建浏览器会话；NAS 原生加载完整 storage_state。"""
    if IS_NAS:
        print("启动 Chromium（headless，复用完整 storage_state 登录态）...")
        state = _read_storage_state(NAS_STORAGE_STATE)
        if not state["cookies"]:
            raise StorageStateError("AUTH_IMPORT_INVALID: 登录态中没有 cookies，请重新导出。")
        print(
            f"    载入登录态（cookies + origins）：{NAS_STORAGE_STATE} "
            f"（{len(state['cookies'])} cookies，{len(state['origins'])} origins）"
        )

        browser = await p.chromium.launch(
            headless=True,
            timeout=60000,
            slow_mo=300,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                # NAS 镜像默认以 root 运行，Chromium sandbox 无法在该用户下初始化。
                "--no-sandbox",
            ],
        )
        try:
            # 原生 storage_state 同时恢复 cookies 与 origins/localStorage。
            context = await browser.new_context(storage_state=state)
        except BaseException:
            await browser.close()
            raise
        return BrowserSession(
            context=context,
            browser=browser,
            state_path=NAS_STORAGE_STATE,
        )
    print("启动 Edge（复用登录态）...")
    context = await p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="msedge",
        headless=HEADLESS,
        timeout=60000,  # 大 profile 启动可能较慢，给足超时
        slow_mo=300,  # 放慢操作便于观察与避开风控
        args=["--start-maximized", "--no-first-run", "--no-default-browser-check"],
    )
    return BrowserSession(context=context)


async def verify_only():
    """只读复检：不点任何任务，扫描 dashboard/earn 打印仍未完成的项目。

    用法：python rewards_earn.py verify —— 随时确认「今天到底还差什么」，
    不必人工逐个比对网页，也不会因为再跑一轮而增加风控风险。
    """
    _install_log()
    if IS_NAS:
        global HEADLESS
        HEADLESS = True
    cleanup_locks()
    async with async_playwright() as p:
        session = await _launch_context(p)
        context = session.context
        try:
            page = await context.new_page()
            remain = await _scan_unfinished(page)
            if not remain:
                print("[复检] 本轮识别到的任务均已标记完成")
            else:
                print(f"[复检] 仍未完成 {len(remain)} 项：")
                for o in remain:
                    print(f"  · {o['text'][:40]}  [{o['href'][:70]}]")
        finally:
            await _close_session(session)  # verify 不写登录态，不发邮件。


async def read_available_points(page):
    """best-effort 读取「可用积分」余额；读不到返回 None，绝不阻塞主流程。

    用于「积分差额自审」：运行前/后各读一次，差额 > 0 即确认本次签到确实涨分，
    而非仅依赖页面「已完成」标记——后者可能因延迟入账/误判而失真。
    """
    try:
        val = await page.evaluate(
            r"""() => {
                const E = document.querySelectorAll('*');
                for (const e of E) {
                    const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
                    const m = t.match(/可用积分[\s:：]*([\d,]+)/);
                    if (m) {
                        const n = parseInt(m[1].replace(/,/g, ''), 10);
                        if (!isNaN(n)) return n;
                    }
                }
                const m2 = (document.body && document.body.innerText || '').match(/(?:积分|奖励)[^\d]*([\d,]{3,})/);
                if (m2) { const n = parseInt(m2[1].replace(/,/g, ''), 10); if (!isNaN(n)) return n; }
                return null;
            }"""
        )
        return val
    except Exception as e:  # noqa: BLE001
        print(f"  [积分自审] 读取可用积分失败（已忽略）：{e}")
        return None


async def main():
    _install_log()
    global HEADLESS
    if IS_NAS:
        # NAS / 无头模式：跳过 Windows 专属步骤，使用 Chromium + storage_state
        HEADLESS = True
        print("[模式] NAS / 无头模式（Chromium + storage_state）")
        cleanup_locks()
    else:
        if not preflight():
            return
        setup_profile(force="refresh" in sys.argv)
        cleanup_locks()
    async with async_playwright() as p:
        session = await _launch_context(p)
        context = session.context
        page = None
        try:
            page = await context.new_page()
            # 关闭可能弹出的「登录以同步数据」首次启动提示（best-effort）
            await dismiss_edge_sync_prompt(context)
            print("正在打开:", EARN_URL)
            try:
                response = await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=120000)
            except Exception as e:
                if _is_auth_wall_url(page.url):
                    raise _auth_error(page.url) from None
                raise VerificationError(f"NAVIGATION_FAILED: 打开 Rewards 失败（{type(e).__name__}）。") from e
            # networkidle 在重 SPA 上易卡死，改用显式等待关键元素
            await page.wait_for_timeout(5000)
            # 导航后再次尝试关闭同步提示（部分情况下在 new-tab 页才弹出）
            await dismiss_edge_sync_prompt(context)
            print("当前 URL:", _display_url(page.url))
            print("页面标题:", await page.title())
            # 未登录兜底：复制的 profile 可能未携带登录态，允许手动登录一次。
            # NAS 不能交互，且 TOU/重新认证页面不能被当作普通 Rewards 页面继续执行。
            if _is_auth_wall_url(page.url):
                if IS_NAS:
                    print("⚠ NAS 模式检测到登录/重新认证页面，无法交互处理。")
                    raise _auth_error(page.url)
                print("⚠ 页面跳转到登录/重新认证页面：复制的 profile 未携带可用登录态。")
                print("   请在打开的浏览器中手动登录 Microsoft 账户，")
                print("   如出现账户条款页面，请先完成人工确认；进入 Rewards 页面后回到此处按 Enter。")
                input("   登录完成后按 Enter 继续...")
                response = await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=120000)
                await page.wait_for_timeout(6000)
                print("重新打开后 URL:", _display_url(page.url))
                if _is_auth_wall_url(page.url):
                    raise _auth_error(page.url)
            await _require_rewards_page(page, response)
            await page.wait_for_selector("body", timeout=20000)
            # 整页截图（本地参考，可能含账户信息，勿外传）
            await page.screenshot(path="earn_page.png", full_page=True)
            # 打印页面结构诊断，用于精修选择器
            await debug_dump(page)

            # 积分自审：记录运行前「可用积分」余额（best-effort，失败不影响流程）
            try:
                run_state.points_before = await read_available_points(page)
                print(f"[积分自审] 运行前可用积分：{run_state.points_before}")
            except Exception as e:  # noqa: BLE001
                print(f"  [积分自审] 运行前读数失败（已忽略）：{e}")

            # 每日连续打卡活动（活动: x/3 右侧栏）需先于普通任务处理
            try:
                await task_dashboard_activities(page, context)
            except Exception as e:
                print(f"  ✗ 活动打卡执行异常: {e}")
            await human_pause(1.0, 2.0)
            # 每日活动三张卡片：专门处理（逐卡校验「已完成」+ 领取「可领取」奖励积分）
            try:
                run_state.daily_set_result = await task_dashboard_daily_set(page, context)
                print(f"[每日活动] 完成 {run_state.daily_set_result['done']}/{run_state.daily_set_result['total']} 张，"
                      f"本次领取奖励积分 {run_state.daily_set_result['claimed']}")
            except Exception as e:
                print(f"  ✗ 每日活动专处理异常: {e}")
            await human_pause(1.0, 2.0)
            # 统一处理：直接在 earn 页面按真实任务链接真人点击（不再依赖卡片容器）
            try:
                await process_offers(page, context)
            except Exception as e:
                print(f"  ✗ 任务执行异常: {e}")
            await human_pause(1.0, 2.0)
            # 必应搜索连续打卡（搜索: 0/1）：需真实搜索一次才计分，专门处理
            try:
                await task_search_card(page, context)
            except Exception as e:
                print(f"  ✗ 必应搜索执行异常: {e}")
            await human_pause(1.0, 2.0)
            # 本月攻略：4 周子任务，绿勾跳过、无勾点击
            try:
                await task_monthly_strategy(page, context)
            except Exception as e:
                print(f"  ✗ 本月攻略执行异常: {e}")
            await human_pause(1.0, 2.0)
            # 收尾复检：以页面「已完成」标记为准，漏做的自动重试一轮并写入邮件
            await verify_and_retry(page, context)
            await _require_rewards_page(page)

            # 积分自审：记录运行后「可用积分」余额（确认本次是否真的涨分）
            try:
                run_state.points_after = await read_available_points(page)
                print(f"[积分自审] 运行后可用积分：{run_state.points_after}")
            except Exception as e:  # noqa: BLE001
                print(f"  [积分自审] 运行后读数失败（已忽略）：{e}")

            session.authenticated = True  # 最终复检成功后才允许写回登录态。
            print("全部任务执行完毕。")
        except AuthenticationRequired as e:
            run_state.last_run_failed = True
            session.authenticated = False
            e.url = page.url if page is not None else ""
            _mark_auth_required(e)
            print("主流程认证失败:", e)
            try:
                await page.screenshot(path="debug_auth_error.png", full_page=True)
                print("已保存 debug_auth_error.png 以便排查")
            except Exception:
                pass
            raise
        except Exception as e:
            run_state.last_run_failed = True
            session.authenticated = False
            print("主流程异常:", e)
            try:
                await page.screenshot(path="debug_error.png", full_page=True)
                print("已保存 debug_error.png 以便排查")
            except Exception:
                pass
            raise
        finally:
            if IS_NAS:
                # 无头模式无需等待人工确认，直接关闭
                await _close_session(session, persist_state=session.authenticated)
            else:
                input("按 Enter 关闭浏览器...")
                await _close_session(session, persist_state=session.authenticated)


async def export_storage_state():
    """在 Windows 上导出登录态到 storage_state.json，供 NAS 无头模式复用。

    用法：python rewards_earn.py export
    可选环境变量 REWARDS_EXPORT 指定导出路径（默认脚本同目录 storage_state.json）。
    """
    _install_log()
    print("[导出] 启动 Edge（复制的登录态）以导出 storage_state ...")
    if IS_NAS:
        raise RuntimeError("请在 Windows 上导出登录态，NAS 无头模式不能进行交互登录。")
    # 默认从系统 Edge 重建副本，确保刚完成的登录/条款确认不会被旧副本遮蔽。
    # 仅在需要复用自动化窗口副本时使用 export reuse。
    reuse_profile = "reuse" in sys.argv
    if not reuse_profile:
        preflight()
    setup_profile(force=not reuse_profile)
    cleanup_locks()
    async with async_playwright() as p:
        async def launch_export_context() -> BrowserContext:
            return await p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                channel="msedge",
                headless=HEADLESS,
                timeout=60000,
                slow_mo=300,
                args=["--start-maximized", "--no-first-run", "--no-default-browser-check"],
            )

        context: BrowserContext = await launch_export_context()
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                response = await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=120000)
            except Exception as first_error:
                # Edge 首次启动时可能因初始空白页/扩展重定向竞争返回 ERR_ABORTED；
                # 先等待当前导航落地，再用 commit 导航重试，避免把可恢复中断当成登录失败。
                if "ERR_ABORTED" not in str(first_error):
                    raise
                print(f"[导出] 首次导航被 Edge 中断（ERR_ABORTED），等待后重试：{page.url}")
                # ERR_ABORTED 可能同时关闭整个 Context；必须重启 Context。
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    context = await launch_export_context()
                    page = await context.new_page()
                except Exception as retry_error:
                    raise VerificationError(
                        "EXPORT_EDGE_CLOSED: Edge 在首次导航后关闭了导出页面。"
                        "脚本已自动重启一次仍失败；请确认所有 Edge 窗口已关闭后重试。"
                    ) from retry_error
                response = await page.goto(EARN_URL, wait_until="commit", timeout=120000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    print(f"[导出] 重试后页面仍在加载，当前 URL：{_display_url(page.url)}，继续诊断。")
            await page.wait_for_timeout(5000)
            if _is_auth_wall_url(page.url):
                print("⚠ 跳转到登录/重新认证页面，请手动完成登录或账户条款确认后回到此处按 Enter。")
                input("登录完成后按 Enter 继续...")
                response = await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=120000)
                await page.wait_for_timeout(5000)
            await _require_rewards_page(page, response)
            await _scan_unfinished(page)  # 任务页可读之后才覆盖正式导出文件。
            state = await _write_storage_state(context, EXPORT_STATE_PATH)
            print(f"[导出] storage_state 已写入：{EXPORT_STATE_PATH}")
            print(f"[导出] 将该文件拷贝到 NAS 的 REWARDS_STORAGE 路径（默认 /app/data/storage_state.json）即可。")
            print(f"[导出] 捕获 {len(state.get('cookies', []))} 条 cookie，{len(state.get('origins', []))} 个 origin。")
        finally:
            await context.close()


if __name__ == "__main__":
    if "export" in sys.argv:
        try:
            asyncio.run(export_storage_state())
        except BaseException as e:
            code = _error_code(str(e))
            meanings = {
                "EXPORT_EDGE_CLOSED": "Edge/Playwright 页面在首次导航后被关闭，尚未进入登录页面。",
                "TOU_REQUIRED": "Microsoft 要求确认账户条款或重新认证。",
                "AUTH_EXPIRED": "Microsoft 登录态已失效。",
                "AUTH_IMPORT_INVALID": "登录态文件格式损坏或不完整。",
                "NAVIGATION_FAILED": "Rewards 页面导航失败，可能是网络或浏览器问题。",
            }
            body = _build_mail_body(True, str(e)).splitlines()
            action = next((line for line in body if line.startswith("处理：")), "请查看日志和上方异常堆栈。")
            print(f"[错误码] {code}")
            print(f"[错误含义] {meanings.get(code, '导出流程未完成，请查看上方异常堆栈。')}")
            print(f"[建议动作] {action}")
            raise
    elif "verify" in sys.argv:
        # 只读复检：不做任务，只报告「还差什么」，不发邮件
        asyncio.run(verify_only())
    else:
        try:
            asyncio.run(main())
        except BaseException as e:
            run_state.last_run_failed = True
            traceback.print_exc()
            _notify_mail(failed=True, error=e)
            raise
        else:
            _notify_mail(failed=run_state.last_run_failed, error="")
