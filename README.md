# Microsoft Rewards 自动任务

这是一个基于 Playwright 的 Microsoft Rewards 自动任务工具，支持 Windows Edge 导出登录态，以及 NAS/Docker 无头运行。

## 当前入口

- `rewards_earn.py`：唯一主程序，负责每日任务、月度任务、登录态校验和邮件通知。
- `nas_cron.sh`：NAS 定时入口。
- `monthly_watchdog.py`：月度任务状态监控。
- `nas_autorun.sh`、`nas_selfheal.sh`：NAS 启动和自愈脚本。

## 安装

```powershell
python -m pip install -r requirements.txt
python rewards_earn.py export
```

首次运行 `export` 时，在 Edge 中完成 Microsoft 登录或条款确认。生成的 `storage_state.json` 只用于本机导出，不能提交到 GitHub；NAS 使用 `data/storage_state.json`。

## NAS 部署

```bash
python rewards_earn.py
```

部署细节见 [`docs/NAS_DEPLOY.md`](docs/NAS_DEPLOY.md)。生产部署文件通过 NAS 专用推送脚本同步，并在推送后执行哈希、换行符、权限和语法校验。

## 认证失败处理

Microsoft 可能要求重新确认条款、重新认证或完成验证码。程序会识别认证墙，写入 `auth_required.json`，暂停定时任务并在邮件中给出中文含义和恢复步骤。完成 Windows 登录后重新导出并部署新的登录态，认证状态才会自动解除。

程序不会绕过 Microsoft 的条款确认或验证码，也不保证第三方页面规则永久不变。

## 安全

不要提交以下文件：`storage_state.json`、`config.json`、`edge_profile/`、`data/`、`authorized_keys_seed.txt`、运行日志和调试快照。真实配置和登录态只应保存在本机或 NAS 私有目录。

本项目仅用于个人自动化研究，请遵守 Microsoft Rewards 服务条款并自行承担使用风险。
