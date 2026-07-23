#!/usr/bin/env python3
"""
微软积分 (Microsoft Rewards) 每日自动获取脚本

通过 Playwright 驱动浏览器完成每日任务。
使用前请阅读 README.md，并自行承担使用风险。
"""

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
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# Edge 专用的持久化用户数据目录（保存登录态、Cookie 等）
PROFILE_DIR = os.path.join(BASE_DIR, "edge_profile")

# 一组常见搜索词，用于模拟每日搜索
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


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        console.print("[red]未找到 config.json，请复制 config.example.json 并填写。[/red]")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def random_delay(min_s: float = 1.5, max_s: float = 4.0):
    time.sleep(random.uniform(min_s, max_s))


def _dump_one(el, lines, prefix=""):
    """把单个元素的关键属性写入 lines。"""
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        txt = (el.inner_text() or "").strip().replace("\n", " ")
        href = el.get_attribute("href") or ""
        cls = el.get_attribute("class") or ""
        eid = el.get_attribute("id") or ""
        data = el.evaluate(
            "e => Array.from(e.attributes).filter(a => a.name.startsWith('data-'))"
            ".map(a => a.name + '=' + a.value).join(' ')"
        )
        onclick = el.get_attribute("onclick") or ""
        info = prefix + "[" + tag + "] text=" + repr(txt)
        if href:
            info += " href=" + repr(href)
        if eid:
            info += " id=" + repr(eid)
        if data:
            info += " data={" + data + "}"
        if cls:
            info += " class=" + repr(cls)
        if onclick:
            info += " onclick=" + repr(onclick)
        lines.append(info)
    except Exception:
        pass


def dump_dashboard(page: Page, out_path: str):
    """诊断用：把仪表盘 + 积分赚取页面的结构 dump 到文件，便于写对选择器。"""
    try:
        # ---- 仪表盘页面 ----
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        lines = []
        _dump_page_structure(page, lines, "DASHBOARD")

        # ---- 积分赚取页面 ----
        page.goto("https://rewards.bing.com/earn", wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)
        _dump_page_structure(page, lines, "EARN")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        console.print("[cyan]诊断已写入：" + out_path + "[/cyan]")
    except Exception as e:
        console.print("[yellow]诊断失败：" + str(e) + "[/yellow]")


def _dump_page_structure(page: Page, lines: list, tag_prefix: str):
    """把当前页面的关键结构追加到 lines，tag_prefix 用于区分不同页面。"""
    lines.append("")
    lines.append("########## " + tag_prefix + " URL: " + page.url + " ##########")
    lines.append("# TITLE: " + page.title())

    # iframe
    lines.append("")
    lines.append("===== IFRAME 列表 =====")
    for fr in page.frames:
        try:
            lines.append("frame name=" + repr(fr.name) + " url=" + repr(fr.url))
        except Exception:
            pass

    # 可见文本（去重）
    lines.append("")
    lines.append("===== 可见文本（去重） =====")
    texts = page.eval_on_selector_all(
        "*",
        "els => els.map(e => (e.innerText || '').trim()).filter(t => t.length > 0 && t.length < 80)",
    )
    seen = set()
    for t in texts:
        if t not in seen:
            seen.add(t)
            lines.append(t)

    # 可点击元素
    lines.append("")
    lines.append("===== 可点击元素（含 data-/onclick/class） =====")
    for el in page.query_selector_all("button, a, [role='button'], div[onclick], li[onclick], span[onclick]"):
        _dump_one(el, lines)

    # 任务相关卡片结构
    lines.append("")
    lines.append("===== 任务相关卡片结构 =====")
    cards = page.query_selector_all("div, li, a, article, section")
    for el in cards:
        try:
            txt = (el.inner_text() or "").strip()
            if any(k in txt for k in ["每日", "活动", "签到", "Daily set", "Daily", "签到:", "活动:", "赚取", "任务", "Earn", "完成"]) and len(txt) < 200:
                _dump_one(el, lines, "CARD> ")
        except Exception:
            pass


def _click_card_links(page: Page, base_url: str, selector: str, label_prefix: str):
    """通用：收集页面上符合 selector 的卡片链接，逐个点击完成。

    - base_url: 点击离开后需要回到的页面（如 dashboard / earn）
    - selector: 卡片 <a> 的选择器（限定在某个 section 内以精确定位）
    - label_prefix: 日志前缀

    点击卡片（<a>）会跳转到目标（通常是 Bing 搜索），完成即计分；
    若 click 失败则退回到直接 goto 该 URL。完成后尝试自动推进 quiz。
    """
    try:
        data = page.eval_on_selector_all(
            selector,
            "els => els.map(e => ({"
            "href: e.getAttribute('href') || '',"
            "text: (e.innerText || '').trim()"
            "}))",
        )
        # 去重，并跳过文本含「已完成」的卡片（避免重复点击已完成任务）
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
        if not urls and skipped > 0:
            # 全部卡片都已完成（如「活动: 3/3」），这是正常成功状态，
            # 并非失败。明确打印，避免日志被误读为「0/N 失败」。
            console.print(
                f"[green]  {label_prefix}：{skipped} 个卡片均已完成，无需操作（今日已达成）。[/green]"
            )
        else:
            console.print(
                f"[cyan]  {label_prefix}：发现 {len(urls)} 个未完成任务卡片"
                f"（跳过 {skipped} 个已完成）[/cyan]"
            )
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
    """判断任务卡片文字是否表示「已完成」。

    除了显式的「已完成」，微软 Rewards 的很多卡片用进度型描述表示完成，
    例如「145/145」「1/4 个任务」「已达成」「已领取」「已点亮」「0/50」等。
    这些卡片实际已经完成，不应再被当作待处理项点击。
    """
    t = (text or "").strip()
    if not t:
        return False
    if "已完成" in t or "已达成" in t or "已领取" in t or "已点亮" in t or "已解锁" in t:
        return True
    # 进度型：X/Y 且 X == Y（已到上限）
    for m in re.finditer(r"([0-9]+)\s*/\s*([0-9]+)", t):
        if m.group(1) == m.group(2):
            return True
    # 「N 个任务」已凑满：如「4/4 个任务」「完成 4 个任务」
    if re.search(r"个任务", t) and re.search(r"[0-9]+\s*/\s*[0-9]+", t):
        # 上面进度型已覆盖 X/Y，这里再兜底「完成 N 个任务」类纯文字（无斜杠但有总数）
        pass
    return False


def _collect_earn_cards(page: Page):
    """收集 earn 页面「日常任务」下所有未完成的任务卡片，返回结构化列表。

    每张卡片为 dict：
      - kind: "puzzle"    拼图链接（spotlight/imagepuzzle）
      - kind: "search"    必应搜索连续打卡按钮（文本含「搜索:」且为 0/1，无 href 的 BUTTON）
      - kind: "link"      其它普通任务链接（href 以 http 开头）

    均用任务卡片专属 class `rounded-cornerCardDefault` 精准定位（导航/页脚
    <a> 不含此 class，避免误点）。已明确标记为「已完成」的卡片会被跳过。
    同时收集无 href 的 BUTTON 卡片，用于识别「搜索: 0/1」连续打卡。
    """
    try:
        # 普通 <a> 任务卡片
        link_data = page.eval_on_selector_all(
            "a.rounded-cornerCardDefault[href^='http']",
            "els => els.map(e => ({"
            "href: e.getAttribute('href') || '',"
            "text: (e.innerText || '').trim()"
            "}))",
        )
        # 无 href 的 BUTTON / DIV 任务卡片（连续打卡类，如「搜索: 0/1」「活动: 0/3」）
        btn_data = page.eval_on_selector_all(
            "button.rounded-cornerCardDefault, div.rounded-cornerCardDefault",
            "els => els.map(e => ({"
            "text: (e.innerText || '').trim()"
            "}))",
        )

        cards = []
        seen_hrefs = set()

        for item in link_data:
            href = item.get("href", "")
            text = item.get("text", "")
            if not href:
                continue
            # 文字为空的卡片通常是导航/推广 banner（如 bing.com、xbox.com 等
            # 首页链接），并非可完成的任务，直接跳过，避免误点。
            if not text:
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
            # 必应搜索连续打卡：文本含「搜索:」且当前为 0/1（未完成）
            m = re.search(r"搜索:\s*([0-9]+)/([0-9]+)", text)
            if m and m.group(1) != m.group(2):
                cards.append({"kind": "search", "text": text})
            else:
                # 每日连续打卡活动：文本含「活动: X/Y」且未完成（X<Y）。
                # 这类卡片需点击展开右侧侧边栏、再点里面 3 个搜索链接才算完成，
                # 不能直接当作「已完成」跳过。
                am = re.search(r"活动:\s*([0-9]+)/([0-9]+)", text)
                if am and am.group(1) != am.group(2):
                    cards.append({"kind": "activity", "text": text})

        return cards
    except Exception as e:
        console.print(f"[yellow]  收集日常任务卡片失败：{e}[/yellow]")
        return []


def do_earn_tasks(page: Page):
    """点击「积分赚取」(/earn) 页面「日常任务」下的所有未完成任务卡片。

    策略：
    1. 收集 earn 页面所有任务卡片（class=rounded-cornerCardDefault，http 链接）。
    2. 跳过文本含「已完成」的卡片。
    3. 逐个点击卡片：若弹出新标签页则关闭新页、回到 earn；若同页跳转则
       goto 回 earn。停留几秒让积分记录后继续下一张。
    """
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
                # 回到 earn，确保每次点击从干净的上下文开始
                page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
                random_delay(2, 4)

                if kind == "search":
                    # 「必应搜索连续打卡」：文本含「搜索: 0/1」的 BUTTON。
                    # 点开卡片后需要当天真正用必应搜索一次，进度才会变成 1/1。
                    _do_search_streak(page, card.get("text", ""))
                elif kind == "puzzle":
                    # 拼图卡片：进入拼图页 → 真实点击「跳过拼图」→ 关闭页面。
                    _do_puzzle_card(page, card.get("href", ""))
                elif kind == "activity":
                    # 每日连续打卡活动：点击卡片展开侧边栏 → 逐个点搜索链接 → 关闭。
                    _do_activity_card(page, card.get("text", ""))
                else:
                    # 其它普通任务链接：点击跳转，关闭新页/回到 earn。
                    _do_link_card(page, card.get("href", ""))

                random_delay(2, 4)
                console.print(f"[green]  [{i + 1}/{len(cards)}] 已完成任务卡片[/green]")
            except Exception as e:
                console.print(f"[yellow]  日常任务 第 {i + 1} 个失败：{e}[/yellow]")
                # 失败也尝试回到 earn 继续
                try:
                    page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

        # ---- 读取 earn 页面进度文本，便于观察 ----
        try:
            page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)
            body = page.inner_text("body")
            for line in body.splitlines():
                s = line.strip()
                if re.search(r"[0-9]+/[0-9]+", s) or "完成" in s or "已完成" in s:
                    if len(s) < 60:
                        console.print(f"[cyan]  earn 进度：{s}[/cyan]")
        except Exception:
            pass

        # ---- 检查是否仍有未完成的拼图卡片（部分进阶奖励网页端无法自动完成）----
        try:
            page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)
            remaining = page.eval_on_selector_all(
                "a.rounded-cornerCardDefault[href*='spotlight/imagepuzzle']",
                "els => els.map(e => (e.innerText||'').trim()).filter(t => !t.includes('已完成'))",
            )
            if remaining:
                console.print(
                    f"[yellow]  提示：仍有 {len(remaining)} 个拼图卡片显示未完成"
                    f"（如「完成此拼图 +5」等进阶奖励，网页端点击「跳过拼图」只能拿"
                    f"基础分，需 Bing 手机 App 或真正拼完才能拿到，请在 App 内补完）。[/yellow]"
                )
        except Exception:
            pass

        console.print(
            f"[cyan]积分赚取处理完成：共处理 {len(cards)} 个未完成任务卡片"
            f"（已完成卡片已跳过）。[/cyan]"
        )
    except Exception as e:
        console.print(f"[yellow]积分赚取处理失败：{e}[/yellow]")


def _do_puzzle_card(page: Page, href: str):
    """处理拼图任务卡片：模仿鼠标点击卡片 → 跳转拼图页 → 模仿鼠标点击「跳过拼图」
    → 关闭页面回到 earn。

    流程全部用真实鼠标操作（mouse.move + mouse.click）：
      1. 回到 earn 页，用真实鼠标点击该拼图卡片（<a>），触发跳转（而不是用 goto）。
      2. 拼图页 (cn.bing.com/spotlight/imagepuzzle) 是 React 动态渲染，等待
         「跳过拼图」这个 <a> 出现，再用真实鼠标点击它，触发「跳过」动作并记录积分。
      3. 关闭拼图/spotlight 页面，回到 earn。

    注意：点击「跳过拼图」通常能拿到该拼图的基础分（卡片变「已完成」）；但部分
    「完成此拼图 +N」类进阶奖励与同一 puzzle 共享 ID，网页端跳过只能拿基础分，
    拿不到这 +N 的额外奖励（需在 Bing 手机 App 内真正拼完）。这类卡片在
    do_earn_tasks 末尾会被检测并提示手动完成。
    """
    console.print("[cyan]    处理拼图卡片：模仿鼠标点击卡片 → 跳转 → 点击「跳过拼图」→ 关闭...[/cyan]")
    earn_url = "https://rewards.bing.com/earn"
    before_pages = len(page.context.pages)
    try:
        # 1) 回到 earn 页，用真实鼠标点击拼图卡片本身（触发跳转）
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        card_sel = f"a.rounded-cornerCardDefault[href='{href}']"
        try:
            _real_mouse_click(page, card_sel, timeout=12000)
            console.print("[green]    已模仿鼠标点击拼图卡片（触发跳转）[/green]")
        except Exception as e:
            console.print(f"[yellow]    未找到拼图卡片或点击失败，改为直接跳转：{e}[/yellow]")
            page.goto(href, wait_until="domcontentloaded", timeout=30000)

        random_delay(3, 6)

        # 2) 拼图页：等待「跳过拼图」出现，用真实鼠标点击
        skip_sel = "a:has-text('跳过拼图')"
        try:
            _real_mouse_click(page, skip_sel, timeout=15000)
            console.print("[green]    已模仿鼠标点击「跳过拼图」[/green]")
        except Exception:
            console.print("[yellow]    未找到「跳过拼图」按钮，尝试真人鼠标交互[/yellow]")
            _human_interact_on_puzzle(page)

        random_delay(3, 6)
    except Exception as e:
        console.print(f"[yellow]    拼图页操作失败：{e}[/yellow]")
    finally:
        # 关掉拼图/spotlight 页面（新标签或当前页均回到 earn）
        ctx = page.context
        if len(ctx.pages) > before_pages:
            for extra in ctx.pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)


def _human_interact_on_puzzle(puzzle_page: Page):
    """兜底：在拼图页面上用真实鼠标点击模拟真人交互，触发积分记录。"""
    try:
        puzzle_page.wait_for_load_state("domcontentloaded", timeout=15000)
        random_delay(2, 4)
        size = puzzle_page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        w = max(int(size.get("w", 1280)), 200)
        h = max(int(size.get("h", 720)), 200)
        spots = [(int(w * 0.5), int(h * 0.5)), (int(w * 0.3), int(h * 0.7)), (int(w * 0.7), int(h * 0.4))]
        for (x, y) in spots:
            jx = max(0, min(w - 1, x + random.randint(-30, 30)))
            jy = max(0, min(h - 1, y + random.randint(-30, 30)))
            try:
                puzzle_page.mouse.click(jx, jy)
            except Exception:
                pass
            random_delay(1, 2.5)
        random_delay(3, 6)
        console.print("[green]    拼图页面真人交互完成[/green]")
    except Exception as e:
        console.print(f"[yellow]    拼图交互失败：{e}[/yellow]")


def _real_mouse_click(page: Page, selector: str, timeout: int = 12000):
    """模仿真人：把真实鼠标移动到元素中心，再点击。

    与 Playwright 的 element.click()（走 Actionability 程序化点击、不移动真实鼠标）
    不同，这里用 page.mouse.move + page.mouse.click，先悬停再点击，更接近人类操作，
    能更好地触发页面点击/积分记录逻辑。
    """
    el = page.wait_for_selector(selector, timeout=timeout)
    box = el.bounding_box()
    if not box:
        raise RuntimeError("无法获取元素位置")
    # 加入随机抖动，模拟真人不会精准点正中
    jx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    jy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    # 先移动鼠标（带步进，模拟移动轨迹），再按下点击
    page.mouse.move(jx, jy, steps=random.randint(8, 20))
    random_delay(0.2, 0.6)
    page.mouse.click(jx, jy)
    return el


def _do_link_card(page: Page, href: str):
    """处理普通任务链接卡片：点击跳转，关闭新标签或回到 earn。"""
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
    """处理「必应搜索连续打卡」(文本含「搜索: 0/1」) 的 BUTTON 卡片。

    点击卡片进入/激活任务后，需要在当天真正用必应搜索一次，进度才会从
    0/1 变成 1/1。这里：点开卡片 → 做一次必应搜索 → 回到 earn。
    """
    console.print(f"[cyan]    处理必应搜索连续打卡：{card_text[:30]}...[/cyan]")
    earn_url = "https://rewards.bing.com/earn"
    try:
        # 点击 earn 上文本含「搜索: 0/1」的按钮卡片
        clicked = page.evaluate(
            """() => {
                const btns = Array.from(document.querySelectorAll('button.rounded-cornerCardDefault'));
                const b = btns.find(x => (x.innerText||'').includes('搜索:') && (x.innerText||'').includes('0/1'));
                if (b) { b.click(); return true; } return false;
            }"""
        )
        if clicked:
            console.print("[green]    已点击必应搜索连续打卡卡片[/green]")
        else:
            console.print("[yellow]    未找到必应搜索连续打卡按钮[/yellow]")
        random_delay(3, 5)
        # 当天真正做一次必应搜索，使连续打卡被记录
        kw = random.choice(SEARCH_KEYWORDS)
        page.goto(
            f"https://www.bing.com/search?q={kw.replace(' ', '+')}&setlang=zh-CN",
            wait_until="domcontentloaded", timeout=30000,
        )
        random_delay(3, 6)
        console.print(f"[green]    已完成必应搜索：{kw}[/green]")
    except Exception as e:
        console.print(f"[yellow]    必应搜索连续打卡处理失败：{e}[/yellow]")
    finally:
        page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)


def _do_activity_card(page: Page, card_text: str):
    """处理「每日连续打卡活动」(文本含「活动: 0/3」) 的卡片。

    流程（模拟真人）：
      1. 真实鼠标点击该卡片 → 右侧出现侧边栏，内含 3 个搜索链接。
      2. 逐个点击侧边栏链接（通常新开标签）→ 模仿真人滚动/停留查看 → 关闭标签。
      3. 3 个都完成后，卡片进度变为「活动: 3/3」。
    """
    earn_url = "https://rewards.bing.com/earn"
    console.print(f"[cyan]    处理每日连续打卡活动：{card_text[:30]}...[/cyan]")
    try:
        # 1) 找到卡片元素（含「活动:」且未完成），用真实鼠标点击
        handle = page.evaluate_handle(
            "() => { const els = Array.from(document.querySelectorAll("
            "'button.rounded-cornerCardDefault, div.rounded-cornerCardDefault, a.rounded-cornerCardDefault')); "
            "const el = els.find(x => { const t = (x.innerText||''); "
            "return t.includes('活动:') && !/\\s3\\/3/.test(t) && !t.includes('已完成'); }); "
            "return el || null; }"
        ).as_element()
        if not handle:
            console.print("[yellow]    未找到每日连续打卡活动卡片[/yellow]")
            return
        try:
            handle.scroll_into_view_if_needed()
            box = handle.bounding_box()
            if not box:
                handle.click()
            else:
                jx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
                jy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
                page.mouse.move(jx, jy, steps=random.randint(8, 20))
                random_delay(0.2, 0.6)
                page.mouse.click(jx, jy)
            console.print("[green]    已点击活动卡片，等待侧边栏加载...[/green]")
        except Exception as e:
            console.print(f"[yellow]    点击活动卡片失败：{e}[/yellow]")
            return
        random_delay(3, 6)

        # 2) 等待侧边栏中的搜索链接出现
        try:
            page.wait_for_selector("a[href*='bing.com/search']", timeout=15000)
        except Exception:
            console.print("[yellow]    侧边栏未出现搜索链接（可能已是 3/3 或页面异常）[/yellow]")
            return

        # 3) 收集侧边栏链接（去重）
        links = page.eval_on_selector_all(
            "a[href*='bing.com/search']",
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
        )
        seen = set()
        urls = []
        for h in links:
            if h in seen:
                continue
            seen.add(h)
            urls.append(h)
        console.print(f"[cyan]    侧边栏发现 {len(urls)} 个活动搜索链接[/cyan]")

        ctx = page.context
        for i, url in enumerate(urls):
            before = len(ctx.pages)
            try:
                # 模拟真人点击侧边栏链接（通常会新开标签）
                _real_mouse_click(page, f"a[href='{url}']", timeout=8000)
            except Exception:
                pass
            # 判断目标页：新开的标签，或退化为当前页直接跳转
            target = None
            if len(ctx.pages) > before:
                target = ctx.pages[-1]
            else:
                target = ctx.new_page()
                try:
                    target.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
            if target and not target.is_closed():
                try:
                    target.wait_for_load_state("domcontentloaded", timeout=15000)
                    # 模仿真人浏览：滚动查看搜索结果
                    target.mouse.wheel(0, random.randint(300, 700))
                    random_delay(3, 6)
                    target.mouse.wheel(0, random.randint(-600, -200))
                    random_delay(2, 4)
                    console.print(f"[green]    [{i + 1}/{len(urls)}] 已完成活动子任务（搜索）[/green]")
                except Exception as e:
                    console.print(f"[yellow]    子任务 {i + 1} 浏览失败：{e}[/yellow]")
                finally:
                    try:
                        target.close()
                    except Exception:
                        pass
            random_delay(1.5, 3)

        # 4) 回到 earn 页，确认进度
        try:
            page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)
            body = page.inner_text("body")
            m = re.search(r"活动:\s*([0-9]+/[0-9]+)", body)
            if m:
                console.print(f"[cyan]    活动进度：{m.group(1)}[/cyan]")
            else:
                console.print("[yellow]    未能读取活动进度[/yellow]")
        except Exception as e:
            console.print(f"[yellow]    读取活动进度失败：{e}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]    每日连续打卡活动处理失败：{e}[/yellow]")
    finally:
        try:
            page.goto(earn_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass


def do_dashboard_tasks(page: Page):
    """打开 Rewards 仪表盘，点击「每日活动」(Daily Set) 未完成卡片完成任务。

    新版仪表盘的「每日活动」(section#dailyset) 内含任务卡片，每张是
    <a href="https://www.bing.com/search?q=...&PUBL=RewardsDO&rnoreward=1">。
    点击卡片会跳转到 Bing，即计为该子任务完成（每个 +10）。已完成的卡片
    文本含「已完成」，会被跳过，不再重复点击。
    """
    console.print("[cyan]打开 Rewards 仪表盘，处理每日活动...[/cyan]")
    try:
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(3, 6)

        # ---- 1. 逐个点击「每日活动」中未完成的卡片 ----
        _click_card_links(
            page,
            "https://rewards.bing.com/dashboard",
            "section#dailyset a[href*='bing.com/search']",
            "每日活动",
        )

        # ---- 2. 点掉「查看新仪表板」引导任务（一次性，可拿积分）----
        try:
            onboarding = page.query_selector("a[href*='modal=quest'][href*='onboarding_offer_punchcard']")
            if onboarding:
                onboarding.click(timeout=5000)
                random_delay(3, 5)
                console.print("[green]  已完成「查看新仪表板」引导任务[/green]")
                # 关闭可能弹出的 modal
                _handle_popups(page)
        except Exception:
            pass

        # ---- 3. 读取任务完成进度 ----
        try:
            page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)
            body = page.inner_text("body")
            for label in ["搜索:", "活动:"]:
                m = re.search(re.escape(label) + r"\s*([0-9]+/[0-9]+)", body)
                if m:
                    console.print(f"[cyan]  进度 {label} {m.group(1)}[/cyan]")
        except Exception:
            pass

        console.print("[cyan]仪表盘每日活动处理完成。[/cyan]")
    except Exception as e:
        console.print(f"[yellow]仪表盘处理失败：{e}[/yellow]")


def print_progress(page: Page):
    """回到仪表盘，仅打印各项任务完成进度（不重复执行任务）。"""
    try:
        # 恢复 PC UA 视图
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Emulation.clearDeviceMetricsOverride")
        except Exception:
            pass
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        body = page.inner_text("body")
        console.print("[bold cyan]----- 今日任务进度 -----[/bold cyan]")
        for label in ["搜索:", "活动:"]:
            m = re.search(re.escape(label) + r"\s*([0-9]+/[0-9]+)", body)
            if m:
                console.print(f"[cyan]  {label} {m.group(1)}[/cyan]")
        mp = re.search(r"可用积分\s*([0-9,]+)", body)
        if mp:
            console.print(f"[green]  当前可用积分：{mp.group(1)}[/green]")
    except Exception as e:
        console.print(f"[yellow]读取进度失败：{e}[/yellow]")


def _handle_popups(page: Page):
    """处理签到/任务可能弹出的新窗口或弹窗。"""
    try:
        # 关闭可能的弹窗（如 "X" 关闭按钮）
        for sel in ["button[aria-label='Close']", "button[aria-label='关闭']",
                    ".close-btn", "div[role='button'][aria-label='Close']"]:
            el = page.query_selector(sel)
            if el:
                el.click(timeout=2000)
                random_delay(1, 2)
    except Exception:
        pass
    # 若有新开的页面（target=_blank），关掉它并回到原页
    try:
        ctx = page.context
        if len(ctx.pages) > 1:
            for extra in ctx.pages[1:]:
                extra.close()
    except Exception:
        pass


def _auto_quiz(page: Page):
    """对 quiz 类页面，反复点击"下一题/Next"直到完成或超时。"""
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


def do_pc_searches(page: Page, count: int, user_agent: str):
    """在 PC 端 Bing 上完成 count 次搜索（独立的搜索配额任务）。

    使用通用随机关键词，与任务卡片无关，避免把已完成任务的搜索词重复搜索。
    """
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


def do_mobile_searches(page: Page, count: int, user_agent: str):
    """使用移动端 UA 完成搜索（移动端每日积分配额更高）。

    在持久化上下文中，通过 CDP 的 setUserAgentOverride 把当前页面伪装成移动端。
    使用通用随机关键词，与任务卡片无关。
    """
    console.print(f"[cyan]开始移动端搜索，目标 {count} 次...[/cyan]")
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Emulation.setUserAgentOverride", {"userAgent": user_agent})
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True,
        })
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


def run():
    config = load_config()
    pc_count = int(config.get("daily_search_count", 30))
    mobile_count = pc_count if config.get("use_mobile", True) else 0
    pc_ua = config.get("pc_user_agent")
    mobile_ua = config.get("mobile_user_agent")
    headless = bool(config.get("headless", False))
    # 使用独立的 Edge 用户数据目录（必须是非默认目录，否则 Edge 的 DevTools
    # 远程调试会拒绝启动）。登录态通过 storage_state.json 持久化，首次手动登录一次即可。
    profile_dir = config.get("profile_dir") or PROFILE_DIR
    if not os.path.isabs(profile_dir):
        profile_dir = os.path.join(BASE_DIR, profile_dir)
    os.makedirs(profile_dir, exist_ok=True)

    # Edge 可执行文件路径：优先使用配置中的 edge_path，否则回退到指定安装目录
    edge_path = config.get("edge_path") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge_path):
        console.print(f"[red]未找到 Edge 可执行文件：{edge_path}[/red]")
        sys.exit(1)

    console.print(f"[bold green]===== 微软积分自动脚本 {datetime.now():%Y-%m-%d %H:%M} =====[/bold green]")
    console.print(f"[cyan]使用 Edge：{edge_path}[/cyan]")
    console.print(f"[cyan]用户数据目录：{profile_dir}[/cyan]")

    with sync_playwright() as p:
        # 持久化上下文：登录态直接保存在 user_data_dir（edge_profile）中，
        # 无需也不能传 storage_state 参数（该参数不被 launch_persistent_context 支持）。
        launch_kwargs = dict(
            user_data_dir=profile_dir,
            executable_path=edge_path,  # 固定使用指定的 Edge 程序
            headless=headless,
            user_agent=pc_ua,
            viewport={"width": 1366, "height": 768},
            args=["--no-first-run", "--no-default-browser-check"],
        )

        context = p.chromium.launch_persistent_context(**launch_kwargs)
        context.set_default_timeout(30000)
        page = context.pages[0] if context.pages else context.new_page()

        # 首次运行请在弹出的 Edge 中手动登录微软账号；登录态会保存在 edge_profile 目录
        _ensure_logged_in(page)

        # 诊断模式：仅 dump 仪表盘结构到文件后退出（用于调试选择器）
        if config.get("debug_dump"):
            dump_dashboard(page, os.path.join(BASE_DIR, "dashboard_dump.txt"))
            context.close()
            console.print("[bold green]===== 诊断完成（debug_dump 模式，已退出） =====[/bold green]")
            return

        # 先处理仪表盘：点击「每日活动」卡片完成（点击即计分）
        do_dashboard_tasks(page)

        # 处理「积分赚取」页面的每日/常规任务：只点击未完成卡片（含拼图），
        # 已完成卡片跳过。任务卡片点击本身即计分。
        do_earn_tasks(page)

        # PC 端「搜索」配额是独立任务（搜索 X/Y），与上面的任务卡片无关，
        # 因此使用通用随机关键词，绝不复用任务卡片的搜索词（否则会把
        # 已完成的任务词又搜一遍，表现为「已完成的还在搜索」）。
        do_pc_searches(page, pc_count, pc_ua)

        if mobile_count > 0:
            # 移动端搜索同理，使用通用随机关键词。
            m_page = context.new_page()
            do_mobile_searches(m_page, mobile_count, mobile_ua)
            m_page.close()

        # 收尾：回到仪表盘打印最终进度（只读，不重复做任务）
        print_progress(page)

        # 持久化上下文：登录态已自动保存在 edge_profile 目录，无需额外导出。
        context.close()

    console.print("[bold green]===== 本次任务完成 =====[/bold green]")


def _ensure_logged_in(page: Page):
    """检测是否已登录，未登录则提示手动登录并等待。"""
    try:
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)
        if "login" in page.url.lower() or "signin" in page.url.lower():
            console.print("[yellow]检测到未登录，请在弹出的 Edge 窗口中手动登录微软账号...[/yellow]")
            console.print("[yellow]登录完成后，脚本会自动继续（最多等待 120 秒）。[/yellow]")
            for _ in range(60):
                time.sleep(2)
                if "login" not in page.url.lower() and "signin" not in page.url.lower():
                    console.print("[green]登录成功，继续执行。[/green]")
                    return
            console.print("[yellow]等待超时，继续尝试执行任务。[/yellow]")
    except Exception as e:
        console.print(f"[yellow]登录检测失败：{e}[/yellow]")


if __name__ == "__main__":
    run()
