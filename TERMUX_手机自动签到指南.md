# 手机必应 App 每日自动签到 + 搜索 部署指南

> 目标：让微软 Rewards 的「移动应用」任务（包括电脑端脚本一直搞不定的 `签到: 0/1`）
> 每天自动完成，无需手动打开手机 Bing App。

---

## 一、先搞清楚现状（重要）

你电脑上的 `main.py` **已经具备移动端能力**：

- `do_mobile_checkin(page, mobile_ua)` — 用 CDP 把桌面 Edge 伪装成手机 UA + 移动视口去签到
- `do_mobile_searches(page, mobile_count, mobile_ua)` — 同上伪装做移动搜索

也就是说，脚本已经在"尝试"拿移动奖励。**但微软经常不认「桌面 IP + 移动 UA」的组合**，
所以 `签到: 0/1` 在电脑端大概率始终完成不了——这跟 UA 无关，是账号/设备环境判定。

**真正稳定拿到移动奖励的办法：在「真·移动环境」里跑。**
两个选择：手机 Termux 脚本、或电脑 + 安卓模拟器。本指南主推 **Termux（最稳、最省电）**。

---

## 二、方案 A：手机 Termux 跑移动端接口脚本（推荐）

原理：不操控 Bing App 界面，而是用脚本**携带移动端登录 token 直接调微软接口**完成签到/搜索。
这种方式对服务器而言就是"一部安卓设备在用 Bing"，能拿到移动专属奖励。

### 步骤 1：手机准备
1. 安装 **Termux**（F-Droid 版，不要用 Play Store 的旧版）：
   https://f-droid.org/packages/com.termux/
2. 安装 **Termux:API**（可选，用于保活/通知）和 **Termux:Boot**（用于开机自启）。
3. 给 Termux 通知权限、电池无限制（设置→应用→Termux→电池→不受限制），否则定时任务会被杀。

### 步骤 2：在 Termux 里装 Python 与依赖
```bash
pkg update && pkg upgrade -y
pkg install python clang git -y
pip install requests
```

### 步骤 3：获取移动端登录 Token（关键一步，已封装好）
微软移动端接口需要 `Authorization: Bearer <token>`。本项目已提供**轻量专用脚本**
`mobile_rewards.py`，用微软公开 `client_id`（0000000040170455）走 OAuth2 授权码流程
自动拿 token，并缓存 `refresh_token` 实现后续免登录续期。**无需 root、无需抓包。**

首次运行只需交互登录一次：
1. 脚本打印一个授权链接，你在手机/电脑浏览器打开并登录微软账号；
2. 登录后浏览器跳到 `https://login.live.com/oauth20_desktop.srf?code=XXX...`；
3. 把地址栏**完整 URL** 粘回 Termux 终端，脚本自动用 `code` 换 token 并缓存到
   `mobile_token.json`。之后每天用 `refresh_token` 自动续，不再需要登录。

### 步骤 4：部署本项目的 mobile_rewards.py
把电脑上的脚本拷到手机（或 `git`/网盘传），在 Termux 里：
```bash
cd ~/microsoft          # 放脚本的目录
pip install requests
python3 mobile_rewards.py        # 首次会提示登录；之后全自动
python3 mobile_rewards.py --no-search   # 只想签到、不做搜索
python3 mobile_rewards.py --login      # token 失效时强制重新登录
```
脚本会：
- 调 `prod.rewardsplatform.microsoft.com/dapi/me/activities`（offerId:
  `Gamification_Sapphire_DailyCheckIn`）完成移动签到 → 解决电脑端拿不到的 `0/1`；
- 用移动 UA + Bearer token 做 15 次必应搜索攒移动积分；
- 前后各查一次 dashboard 打印积分/签到线索。

> 若你更想用成熟大项目，也可 `git clone https://github.com/Limiter06/microsoft-rewards`
> 并按其 README 配置 mobile 模式。本脚本胜在轻量、参数透明、易改。

### 步骤 5：设每天定时自动跑
Termux 内置 cron：
```bash
pkg install cronie -y
crond                      # 启动 cron 守护
crontab -e
```
加入（每天 9:07 跑，避开整点高峰）：
```
7 9 * * * cd ~/microsoft-rewards && python3 main.py >> ~/rewards.log 2>&1
```
配合 **Termux:Boot** 可实现重启后自动拉起 crond。

---

## 三、方案 B：电脑 + 安卓模拟器（不想折腾手机）

1. 装 **BlueStacks / 雷电 / MuMu** 任一模拟器。
2. 模拟器内装 **Microsoft Bing App**，登录同一微软账号。
3. 每天在模拟器里打开 Bing App 签到 + 搜索（可手动，也可用 Auto.js/Appium 自动化）。
4. 若模拟器被微软识别为可疑环境导致 `0/1` 不计，则只能换真机。

> 模拟器能成功的概率取决于微软的设备完整性检测，不如 Termux 接口脚本稳。

---

## 四、方案 C：改造你电脑的 main.py（先快速验证，零部署）

在 `run()` 里移动逻辑已经存在。你可以先确认 `config.json` 里：
```json
{
  "use_mobile": true,
  "mobile_user_agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 ... Mobile Safari/537.36 Edge/xxx"
}
```
跑一次脚本，看 `签到: 0/1` 是否变 `1/1`。
- **变了**：恭喜，电脑伪装就够用，不用折腾手机。
- **没变**：说明账号被环境判定挡住，老老实实走方案 A/B。

> 注意：长期用固定桌面 IP 频繁发移动请求，有极低概率触发风控。如果只想拿移动奖励，
> 方案 A 是长期最优解。

---

## 五、三种方案对比

| 方案 | 稳定性 | 难度 | 能否拿移动 0/1 | 备注 |
|---|---|---|---|---|
| A. Termux 接口脚本 | ★★★★★ | 中（抓 token） | ✅ 稳 | 最推荐，省电后台可保活 |
| B. 电脑+模拟器 | ★★★ | 低-中 | ⚠️ 看环境检测 | 不用手机，但可能拿不到 |
| C. 电脑 main.py 伪装 | ★★ | 极低 | ⚠️ 不一定 | 先验证，失败再上 A |

---

## 六、最小行动建议

1. 先在电脑跑一次现有 `main.py`，确认 `签到: 0/1` 是否能被桌面伪装刷掉（方案 C 验证）。
2. 若不行 → 花 20 分钟按「方案 A 步骤 1-5」在手机 Termux 部署 Limiter06 项目，从此全自动。
3. 每天 9 点自动跑，移动签到+搜索+电脑端任务一起清空，无需手动。

> 本指南不涉及修改 `main.py`；若需要我帮你给 `main.py` 加「方案 C 验证开关」或
> 写一个 Termux 专用的轻量移动端脚本，告诉我即可。
