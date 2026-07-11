#!/usr/bin/env python3
"""
微软积分 (Microsoft Rewards) NAS 版 —— 基于 Playwright + Chromium + 真实登录态

与 Windows 版 main.py 的差异：
  1. 不依赖 Windows Edge，改用 Playwright 自带的 Chromium（需 `playwright install chromium`）。
  2. 强制 headless=True（NAS 无显示器）。
  3. 登录态直接复用 storage_state.json（由 Windows 版首次手动登录后导出/同步过来）。
  4. 所有路径基于本文件所在目录，方便在 Docker / QTS cron 中运行。

使用前提：
  - 先在 Windows 上跑一次 main.py 完成手动登录，确保 storage_state.json 已生成并含有效 cookie。
  - 把 storage_state.json（以及本文件、requirements_nas.txt）一起放到 NAS 上同目录。
  - NAS 端首次跑前，建议手动验证 cookie 未过期；过期则需重新在 Windows 登录并同步该文件。

依赖：
  pip install -r requirements_nas.txt
  playwright install chromium   # 拉取浏览器内核
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, Page
from rich.console import Console

console = Console()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config_nas.json")
STATE_PATH = os.path.join(BASE_DIR, "storage_state.json")

PROFILE_DIR = os.path.join(BASE_DIR, "edge_profile")  # 兜底：若没有 storage_state 则用持久化目录

SEARCH_KEYWORDS = [
    "today news", "weather forecast", "best movies 2024", "python tutorial",
    "how to cook pasta", "latest technology", "space exploration", "healthy recipes",
    "world cup standings", "stock market today", "ai developments", "travel tips",
    "funny jokes", "history facts", "science experiments", "music charts",
    "sports scores", "recipe ideas", "book recommendations", "coding challenges",
    "nature documentary", "car reviews", "game releases", "fashion trends",
    "home workout", "language learning", "photography tips", "gardening ideas",
    "puzzle games", "motivational quotes", "cpu benchmark", "gpu comparison",
]

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0")
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36 EdgA/124.0.0.0")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        console.print(f"[red]未找到 {CONFIG_PATH}，请复制 config.example.json 为 config_nas.json 并填写。[/red]")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def random_delay(min_s: float = 1.5, max_s: float = 4.0):
    time.sleep(random.uniform(min_s, max_s))


def _real_mouse_click(page: Page, selector: str, timeout: int = 12000):
    el = page.wait_for_selector(selector, timeout=timeout)
    box = el.bounding_box()
    if not box:
        raise RuntimeError("无法获取元素位置")
    jx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    jy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    page.mouse.move(jx, jy, steps=random.randint(8, 20))
    random_delay(0.2, 0.6)
    page.mouse.click(jx, jy)
    return el


def _handle_popups(page: Page):
    try:
        for sel in ["button[aria-label='Close']", "button[aria-label='关闭']",
                    ".close-btn", "div[role='button'][aria-label='Close']"]:
            el = page.query_selector(sel)
            if el:
                el.click(timeout=2000)
                random_delay(1, 2)
    except Exception:
        pass
    try:
        ctx = page.context
        if len(ctx.pages) > 1:
            for extra in ctx.pages[1:]:
                extra.close()
    except Exception:
        pass


def _auto_quiz(page: Page):
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            clicked = False
            for sel in ["button:has-text('下一题')", "button:has-text('Next')",
                        "button:has-text('提交')", "button:has-text('Submit')",
                        "div[role='button']:has-text('下一题')",
                        "div[role='button']:has-text('Next')"]:
                el = page.query_selector(sel)
                if el:
                    el.click(timeout=3000)
                    clicked = True
                    random_delay(1.5, 3)
                    break
            if not clicked:
                break
        except Exception:
            break


def _click_card_links(page: Page, base_url: str, selector: str, label_prefix: str):
    try:
        data = page.eval_on_selector_all(
            selector,
            "els => els.map(e => ({href: e.getAttribute('href') || '', text: (e.innerText || '').trim()}))",
        )
        seen = set()
        urls = []
        skipped = 0
        for item in data:
            h = item.get("href", "")
            text = item.get("text", "")
            if not h:
                continue
            if "已完成" in text:
                skipped += 1
                continue
            if h in seen:
                continue
            seen.add(h)
            urls.append(h)
        console.print(f"[cyan]  {label_prefix}：发现 {len(urls)} 个未完成任务卡片（跳过 {skipped} 个已完成）[/cyan]")
        for i, url in enumerate(urls):
            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                random_delay(2, 4)
                clicked = page.evaluate(
                    "([sel, target]) => { const els = Array.from(document.querySelectorAll(sel)); "
                    "const a = els.find(e => e.getAttribute('href') === target); "
                    "if (a) { a.click(); return true; } return false; }",
                    [selector, url],
                )
                if not clicked:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                random_delay(4, 7)
                console.print(f"[green]  [{i + 1}/{len(urls)}] 完成任务卡片[/green]")
                _auto_quiz(page)
            except Exception as e:
                console.print(f"[yellow]  {label_prefix} 第 {i + 1} 个失败：{e}[/yellow]")
        return urls
    except Exception as e:
        console.print(f"[yellow]  {label_prefix} 处理失败：{e}[/yellow]")
        return []


def _is_done_card(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if "已完成" in t or "已达成" in t or "已领取" in t or "已点亮" in t or "已解锁" in t:
        return True
    for m in re.finditer(r"([0-9]+)\s*/\s*([0-9]+)", t):
        if m.group(1) == m.group(2):
            return True
    return False


def _collect_earn_cards(page: Page):
    try:
        link_data = page.eval_on_selector_all(
            "a.rounded-cornerCardDefault[href^='http']",
            "els => els.map(e => ({href: e.getAttribute('href') || '', text: (e.innerText || '').trim()}))",
        )
        btn_data = page.eval_on_selector_all(
            "button.rounded-cornerCardDefault",
            "els => els.map(e => ({text: (e.innerText || '').trim()}))",
        )
        cards = []
        seen_hrefs = set()
        for item in link_data:
            href = item.get("href", "")
            text = item.get("text", "")
            if not href or not text:
                continue
            if _is_done_card(text):
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            if "spotlight/imagepuzzle" in href:
                cards.append({"kind": "puzzle", "href": href, "text": text})
            else:
                cards.append({"kind": "link", "href": href, "text": text})
        for item in btn_data:
            text = item.get("text", "")
            if not text or _is_done_card(text):
                continue
            m = re.search(r"搜索:\s*([0-9]+)/([0-9]+)", text)
            if m and m.group(1) != m.group(2):
                cards.append({"kind": "search", "text": text})
        return cards
    except Exception as e:
        console.print(f"[yellow]  收集日常任务卡片失败：{e}[/yellow]")
        return []


def do_earn_tasks(page: Page):
    console.print("[cyan]打开积分赚取页面，处理日常任务...[/cyan]")
    try:
        page.goto("https://rewards.bing.com/earn", wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        cards = _collect_earn_cards(page)
        console.print(f"[cyan]  日常任务：发现 {len(cards)} 个未完成任务卡片[/cyan]")
        earn_url = "https://rewards.bing.com/earn"
        for i, card in enumerate(cards):
            kind = card.get("kind")
            try:
                page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
                random_delay(2, 4)
                if kind == "search":
                    _do_search_streak(page, card.get("text", ""))
                elif kind == "puzzle":
                    _do_puzzle_card(page, card.get("href", ""))
                else:
                    _do_link_card(page, card.get("href", ""))
                random_delay(2, 4)
                console.print(f"[green]  [{i + 1}/{len(cards)}] 已完成任务卡片[/green]")
            except Exception as e:
                console.print(f"[yellow]  日常任务 第 {i + 1} 个失败：{e}[/yellow]")
                try:
                    page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
        console.print(f"[cyan]积分赚取处理完成：共处理 {len(cards)} 个未完成卡片[/cyan]")
    except Exception as e:
        console.print(f"[yellow]积分赚取处理失败：{e}[/yellow]")


def _do_puzzle_card(page: Page, href: str):
    console.print("[cyan]    处理拼图卡片...[/cyan]")
    earn_url = "https://rewards.bing.com/earn"
    before_pages = len(page.context.pages)
    try:
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        card_sel = f"a.rounded-cornerCardDefault[href='{href}']"
        try:
            _real_mouse_click(page, card_sel, timeout=12000)
        except Exception as e:
            console.print(f"[yellow]    拼图卡片点击失败，改为跳转：{e}[/yellow]")
            page.goto(href, wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        try:
            _real_mouse_click(page, "a:has-text('跳过拼图')", timeout=15000)
        except Exception:
            pass
        random_delay(3, 6)
    except Exception as e:
        console.print(f"[yellow]    拼图页操作失败：{e}[/yellow]")
    finally:
        ctx = page.context
        if len(ctx.pages) > before_pages:
            for extra in ctx.pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)


def _do_link_card(page: Page, href: str):
    console.print("[cyan]    处理任务链接卡片...[/cyan]")
    earn_url = "https://rewards.bing.com/earn"
    before_pages = len(page.context.pages)
    try:
        clicked = page.evaluate(
            "([target]) => { const els = Array.from(document.querySelectorAll('a.rounded-cornerCardDefault[href^=\"http\"]')); "
            "const a = els.find(e => e.getAttribute('href') === target); "
            "if (a) { a.click(); return true; } return false; }",
            [href],
        )
        if not clicked:
            page.goto(href, wait_until="domcontentloaded", timeout=30000)
        random_delay(4, 8)
    except Exception as e:
        console.print(f"[yellow]    链接卡片点击失败：{e}[/yellow]")
    finally:
        ctx = page.context
        if len(ctx.pages) > before_pages:
            for extra in ctx.pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)


def _do_search_streak(page: Page, card_text: str):
    console.print(f"[cyan]    处理必应搜索连续打卡：{card_text[:30]}...[/cyan]")
    earn_url = "https://rewards.bing.com/earn"
    try:
        clicked = page.evaluate(
            """() => {
                const btns = Array.from(document.querySelectorAll('button.rounded-cornerCardDefault'));
                const b = btns.find(x => (x.innerText||'').includes('搜索:') && (x.innerText||'').includes('0/1'));
                if (b) { b.click(); return true; } return false;
            }"""
        )
        if clicked:
            console.print("[green]    已点击必应搜索连续打卡卡片[/green]")
        random_delay(3, 5)
        kw = random.choice(SEARCH_KEYWORDS)
        page.goto(f"https://www.bing.com/search?q={kw.replace(' ', '+')}&setlang=zh-CN",
                  wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        console.print(f"[green]    已完成必应搜索：{kw}[/green]")
    except Exception as e:
        console.print(f"[yellow]    必应搜索连续打卡处理失败：{e}[/yellow]")
    finally:
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)


def do_dashboard_tasks(page: Page):
    console.print("[cyan]打开 Rewards 仪表盘，处理每日活动...[/cyan]")
    try:
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        _click_card_links(page, "https://rewards.bing.com/dashboard",
                          "section#dailyset a[href*='bing.com/search']", "每日活动")
        try:
            onboarding = page.query_selector("a[href*='modal=quest'][href*='onboarding_offer_punchcard']")
            if onboarding:
                onboarding.click(timeout=5000)
                random_delay(3, 5)
                console.print("[green]  已完成「查看新仪表板」引导任务[/green]")
                _handle_popups(page)
        except Exception:
            pass
        console.print("[cyan]仪表盘每日活动处理完成。[/cyan]")
    except Exception as e:
        console.print(f"[yellow]仪表盘处理失败：{e}[/yellow]")


def do_pc_searches(page: Page, count: int):
    console.print(f"[cyan]开始 PC 端搜索，目标 {count} 次...[/cyan]")
    for i in range(count):
        keyword = random.choice(SEARCH_KEYWORDS)
        url = f"https://www.bing.com/search?q={keyword.replace(' ', '+')}&setlang=zh-CN"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 5)
            console.print(f"[green]  [{i + 1}/{count}][/green] 搜索：{keyword}")
        except Exception as e:
            console.print(f"[yellow]  搜索失败：{e}[/yellow]")
    console.print("[cyan]PC 端搜索完成。[/cyan]")


def do_mobile_searches(page: Page, count: int):
    console.print(f"[cyan]开始移动端搜索，目标 {count} 次...[/cyan]")
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Emulation.setUserAgentOverride", {"userAgent": MOBILE_UA})
        cdp.send("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True})
    except Exception as e:
        console.print(f"[yellow]移动端 UA 覆盖失败（将以 PC UA 搜索）：{e}[/yellow]")
    try:
        page.goto("https://www.bing.com/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    for i in range(count):
        keyword = random.choice(SEARCH_KEYWORDS)
        url = f"https://www.bing.com/search?q={keyword.replace(' ', '+')}&setlang=zh-CN"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 5)
            console.print(f"[green]  [{i + 1}/{count}][/green] 移动搜索：{keyword}")
        except Exception as e:
            console.print(f"[yellow]  移动搜索失败：{e}[/yellow]")


def print_progress(page: Page):
    try:
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Emulation.clearDeviceMetricsOverride")
        except Exception:
            pass
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        body = page.inner_text("body")
        console.print("[bold cyan]----- 今日任务进度 -----[/bold cyan]")
        for label in ["搜索:", "活动:", "签到:"]:
            m = re.search(re.escape(label) + r"\s*([0-9]+/[0-9]+)", body)
            if m:
                console.print(f"[cyan]  {label} {m.group(1)}[/cyan]")
        mp = re.search(r"可用积分\s*([0-9,]+)", body)
        if mp:
            console.print(f"[green]  当前可用积分：{mp.group(1)}[/green]")
    except Exception as e:
        console.print(f"[yellow]读取进度失败：{e}[/yellow]")


def _ensure_logged_in(page: Page):
    try:
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        if "login" in page.url.lower() or "signin" in page.url.lower():
            console.print("[red]检测到未登录：storage_state.json 已失效，请在 Windows 重新登录并同步该文件。[/red]")
            return False
        console.print("[green]登录态有效。[/green]")
        return True
    except Exception as e:
        console.print(f"[yellow]登录检测失败：{e}[/yellow]")
        return False


def _build_context(p, config):
    """构建 Chromium 持久化上下文并注入登录态，返回 context。"""
    use_state = config.get("use_storage_state", True)
    launch_kwargs = dict(
        headless=True,
        args=[
            "--no-sandbox",            # NAS/容器环境必须
            "--disable-dev-shm-usage", # 防止 /dev/shm 太小导致崩溃
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    # 优先用 storage_state 注入登录态；否则回退到持久化目录
    if use_state and os.path.exists(STATE_PATH):
        console.print(f"[cyan]使用登录态文件：{STATE_PATH}[/cyan]")
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR, user_agent=PC_UA, **launch_kwargs)
        context.add_cookies(json.load(open(STATE_PATH, encoding="utf-8"))["cookies"])
    else:
        console.print("[yellow]未找到 storage_state.json，使用持久化目录（首次需手动登录）。[/yellow]")
        os.makedirs(PROFILE_DIR, exist_ok=True)
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR, user_agent=PC_UA, **launch_kwargs)
    context.set_default_timeout(30000)
    return context


def _save_storage_state(context):
    """把当前上下文的登录态回写到 storage_state.json（顺延会话/更新 cookie）。"""
    try:
        state = context.storage_state()
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        console.print(f"[green]已回写登录态：{STATE_PATH}[/green]")
    except Exception as e:
        console.print(f"[yellow]回写登录态失败：{e}[/yellow]")


def keepalive():
    """轻量保活：仅验证登录态并做最小活动（访问 dashboard），不跑搜索任务。

    目的：在 cookie 快到期前定期「刷个存在感」，让微软会话保持活跃、顺延有效期，
    从而降低强制重新认证（需 2FA）的概率。运行结束会回写 storage_state.json。
    """
    config = load_config()
    console.print(f"[bold green]===== 微软积分 保活模式 {datetime.now():%Y-%m-%d %H:%M} =====[/bold green]")

    with sync_playwright() as p:
        context = _build_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()

        if not _ensure_logged_in(page):
            context.close()
            console.print("[bold red]===== 保活失败：登录态已失效，需在 Windows 重新登录并同步 storage_state.json =====[/bold red]")
            return

        # 最小活动：访问几个已登录页面，制造真实浏览行为
        # 注：account.microsoft.com/rewards 偶发加载超时，改用 rewards.bing.com 首页更稳
        for url in ("https://rewards.bing.com/dashboard",
                    "https://rewards.bing.com/",
                    "https://www.bing.com/"):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                random_delay(2, 5)
                console.print(f"[cyan]  已访问：{url}[/cyan]")
            except Exception as e:
                console.print(f"[yellow]  访问失败 {url}：{e}[/yellow]")

        print_progress(page)
        _save_storage_state(context)   # 顺延后的 cookie 回写，延长本地登录态
        context.close()

    console.print("[bold green]===== 保活完成 =====[/bold green]")


def run():
    config = load_config()
    pc_count = int(config.get("daily_search_count", 30))
    mobile_count = pc_count if config.get("use_mobile", True) else 0

    console.print(f"[bold green]===== 微软积分 NAS 版 {datetime.now():%Y-%m-%d %H:%M} =====[/bold green]")

    with sync_playwright() as p:
        context = _build_context(p, config)
        page = context.pages[0] if context.pages else context.new_page()

        if not _ensure_logged_in(page):
            context.close()
            console.print("[bold red]===== 因登录态失效终止 =====[/bold red]")
            return

        do_dashboard_tasks(page)
        do_earn_tasks(page)
        do_pc_searches(page, pc_count)
        if mobile_count > 0:
            m_page = context.new_page()
            do_mobile_searches(m_page, mobile_count)
            m_page.close()
        console.print("[yellow]提示：「移动应用 签到」为 Bing 手机 App 专属任务，请在手机 Bing App 内补签。[/yellow]")
        print_progress(page)
        _save_storage_state(context)   # 每次任务后回写，顺延登录态
        context.close()

    console.print("[bold green]===== 本次任务完成 =====[/bold green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="微软积分 NAS 版")
    parser.add_argument("--keepalive", action="store_true",
                        help="仅保活（验证登录态 + 最小活动，不跑搜索任务）")
    args = parser.parse_args()
    if args.keepalive:
        keepalive()
    else:
        run()
