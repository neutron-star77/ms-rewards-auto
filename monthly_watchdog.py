#!/usr/bin/env python3
"""微软积分「本月攻略」月度看门狗：独立于 rewards_earn.py 检查进度，消灭静默失败。

机制（对齐 img.ink 的 watchdog.py，见 ADR-0002 §5.2）：
  rewards_earn.py 每次成功定位并扫描到本月攻略后，会把
  {"month","weekly_done","weekly_total","updated"} 写入 data/monthly_state.json。
  本看门狗在每月 5 / 15 / 25 日运行：
    - 若文件缺失 / 月份不对 -> 说明「本月攻略从未被成功处理」-> 结构级告警；
    - 若 weekly_done 明显落后于「已过周数 - 1」-> 说明整月零完成 -> 结构级告警。
  不依赖主流程成功，主流程挂了它照样能发现异常。

邮件配置复用同目录 config.json 的 email_notify 段（与 rewards_earn.py 一致）。
"""
import os
import sys
import json
import time
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "monthly_state.json")
CONFIG = os.path.join(BASE, "config.json")
ALERTED = os.path.join(BASE, "data", "watchdog_alerted.json")


def _load_mail_config():
    cfg = {}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = (json.load(f).get("email_notify") or {})
    except Exception:
        cfg = {}
    return {
        "enabled": os.environ.get("REWARDS_MAIL_ENABLED", cfg.get("enabled", False)),
        "smtp_server": os.environ.get("REWARDS_MAIL_SERVER", cfg.get("smtp_server", "")),
        "smtp_port": os.environ.get("REWARDS_MAIL_PORT", cfg.get("smtp_port", 465)),
        "sender": os.environ.get("REWARDS_MAIL_SENDER", cfg.get("sender", "")),
        "auth_code": os.environ.get("REWARDS_MAIL_AUTH", cfg.get("auth_code", "")),
        "recipient": os.environ.get("REWARDS_MAIL_RECIPIENT", cfg.get("recipient", "")),
    }


def _send(subject, body):
    m = _load_mail_config()
    if not m["enabled"] or not (m["sender"] and m["recipient"] and m["auth_code"]):
        print("[watchdog] 邮件未配置，仅打印：%s" % subject)
        print(body)
        return
    try:
        import smtplib
        import ssl
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
        print("[watchdog] 已发送告警邮件至 %s" % m["recipient"])
    except Exception as e:
        print("[watchdog] 邮件发送失败: %s" % e)


def _already_alerted_today():
    try:
        if os.path.exists(ALERTED):
            with open(ALERTED, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date") == time.strftime("%Y-%m-%d"):
                return True
    except Exception:
        pass
    return False


def _mark_alerted():
    try:
        os.makedirs(os.path.dirname(ALERTED), exist_ok=True)
        with open(ALERTED, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d")}, f)
    except Exception:
        pass


def main():
    today = datetime.date.today()
    day = today.day
    if day not in (5, 15, 25):
        print("[watchdog] 今日(%02d)非检查日(5/15/25)，跳过。" % day)
        return

    if _already_alerted_today():
        print("[watchdog] 今日已告警过，跳过重复邮件。")
        return

    # 已过周数（每月攻略共 4 周）：(day-1)//7 + 1；允许「本周还没到」漏 1 周
    elapsed_weeks = (day - 1) // 7 + 1
    expected_min = max(0, elapsed_weeks - 1)

    problems = []
    detail = ""
    try:
        if not os.path.exists(STATE):
            problems.append("monthly_state.json 不存在：本月攻略从未被成功扫描到（主流程可能整月零匹配）")
            detail = "文件缺失"
        else:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            month = d.get("month")
            done = d.get("weekly_done", 0)
            total = d.get("weekly_total", 4)
            detail = "state: month=%s weekly_done=%s/%s updated=%s" % (
                month, done, total, d.get("updated"))
            if month != today.strftime("%Y-%m"):
                problems.append("monthly_state.json 月份(%s)与当前月份(%s)不符：本月攻略未更新"
                               % (month, today.strftime("%Y-%m")))
            elif done < expected_min:
                problems.append(
                    "本月攻略进度落后：weekly_done=%s 低于期望下限 %s（已过 %s 周，%s/%s 完成）"
                    % (done, expected_min, elapsed_weeks, done, total))
    except Exception as e:
        problems.append("读取 monthly_state.json 异常: %s" % e)
        detail = "异常: %s" % e

    if problems:
        body = "【结构异常】微软积分「本月攻略」疑似未正常迭代：\n\n"
        body += "\n".join("  · %s" % p for p in problems)
        body += "\n\n%s\n" % detail
        body += "对照 ADR-0002：识别键取最稳定信号 + 独立看门狗。\n"
        body += "请检查 is_monthly_strategy 是否仍匹配当前页面、storage_state 是否过期。\n"
        _send("【结构异常】微软积分本月攻略看门狗 %s" % today.strftime("%Y-%m-%d"), body)
        _mark_alerted()
    else:
        print("[watchdog] OK: 本月攻略进度正常 (expected_min=%s, %s)" % (expected_min, detail))


if __name__ == "__main__":
    main()
