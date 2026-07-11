# 微软积分 (Microsoft Rewards) 每日自动获取脚本

> **关于本项目**
> - 支持 **Windows 任务计划**、**NAS（Docker + Playwright）** 与 **手机 Termux** 三种方式实现每日自动运行。
> - 本项目**全程代码由 AI 编写**。如遇 BUG，欢迎在仓库留言反馈，或自行用 AI 修复后提交。

通过自动化浏览器完成微软积分的每日任务（PC 搜索、每日卡片、Quiz 等），并配合 Windows 任务计划程序实现「每天自动运行」。

> ⚠️ 风险提示：微软官方不允许使用自动化工具获取积分，使用本脚本存在账户被警告或封禁的风险，请自行评估并控制运行频率。建议仅在个人测试环境使用。

## 工作原理

1. 使用 Playwright 驱动你指定的 Microsoft Edge（固定 `edge_path`）。
2. **自动签到**：打开 Rewards 仪表盘，识别并点击「签到 / 打卡 / Daily」入口。
3. **完成每日任务**：点击仪表盘上的「开始 / Start / 参与 / Play」等任务卡片，
   自动处理弹窗，并对 Quiz 类任务自动点「下一题 / Next」直到完成。
4. **按仪表盘内容搜索**：从仪表盘提取真实的「热门 / 建议搜索词」，优先用这些词
   做 PC 端与移动端搜索（比随机词更贴合任务、更像真人操作）。
5. **登录态持久化**：首次手动登录一次后，登录态保存为 `storage_state.json`，
   之后自动注入，无需再次登录。

> 注意：脚本使用**独立的 `edge_profile` 目录**（非系统默认 Edge 目录），因为
> Edge 不允许 DevTools 远程调试指向默认用户目录。登录态通过 `storage_state.json` 复用。

## 安装

```powershell
# 1. 创建虚拟环境（可选）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt
```

> 本脚本固定使用系统安装的 **Microsoft Edge**，路径在 `config.json` 的 `edge_path` 中指定
> （默认 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`）。
> **无需**执行 `playwright install chromium`，也不会额外下载浏览器。
> 若你的 Edge 安装在其他路径，请修改 `edge_path`。

## 配置

复制 `config.example.json` 为 `config.json` 并填写：

```json
{
  "email": "你的微软账号",
  "password": "你的密码",
  "headless": false,
  "daily_search_count": 30,
  "use_mobile": true,
  "profile_dir": "edge_profile"
}
```

> 脚本使用 Edge 的**持久化用户数据目录**（默认 `edge_profile`）保存登录态。
> **首次运行**时会弹出 Edge 窗口，请手动登录一次微软账号；此后登录态会被保存，
> 后续运行（含计划任务）即可全自动，无需再次登录，也不必在配置里写明文密码。

## 运行

```powershell
python main.py
```

## 设置每天自动运行（Windows 任务计划程序）

脚本 `install_task.ps1` 可一键创建「每天定时运行」的计划任务：

```powershell
# 以管理员身份运行 PowerShell
.\install_task.ps1
```

默认每天 09:30 运行一次。可修改脚本顶部的 `$Time` 变量调整时间。

或手动创建（把下面路径替换为你的项目实际路径）：
- 程序/脚本：`<项目目录>\.venv\Scripts\python.exe`
- 参数：`<项目目录>\main.py`
- 起始于：`<项目目录>\`
- 触发器：每天 09:30

> 计划任务全自动运行时，建议把 `config.json` 的 `headless` 设为 `true`（首次手动登录后再改）。

## 文件说明

**Windows 版（本机 Edge 运行）**

| 文件 | 作用 |
|------|------|
| `main.py` | 主脚本，自动完成每日任务 |
| `mr.py` | 任务逻辑模块（被 main 调用） |
| `config.example.json` | 配置模板（复制为 `config.json`） |
| `install_task.ps1` | 一键创建 Windows 计划任务 |
| `requirements.txt` | Python 依赖 |

**NAS / Docker 版（无人值守）**

| 文件 | 作用 |
|------|------|
| `nas_main.py` | NAS 版主脚本（登录态注入 + 支持 `--keepalive` 保活） |
| `nas_cron.sh` | 宿主机 cron 每日触发脚本（随机延迟 + docker run） |
| `nas_keepalive.sh` | 登录态保活脚本（约每 18 天跑一次延长会话） |
| `Dockerfile` | 构建 `ms-rewards` 镜像 |
| `config_nas.json` | NAS 版配置 |
| `requirements_nas.txt` | 容器依赖（playwright、rich） |
| `微软积分自动获取-部署与图形化操作指南.md` | NAS/Docker 详细部署文档 |
| `TERMUX_手机自动签到指南.md` | 安卓 Termux 运行指南 |

> ⚠️ **不包含**：`storage_state.json`（登录态 cookie）、`edge_profile/`（浏览器配置）、`config.json`（你的真实配置）。这些含隐私，已在 `.gitignore` 中排除，需各自本地生成。
> NAS 版首次使用需先在 Windows 跑一次 `main.py` 登录，再把生成的 `storage_state.json` 同步到 NAS。

## 免责声明

本脚本仅用于学习自动化技术，使用时请遵守 Microsoft Rewards 服务条款。因使用本脚本导致的任何后果由使用者自行承担。
