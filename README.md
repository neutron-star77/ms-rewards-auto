# Microsoft Rewards 自动任务

这是一个基于 Playwright 的 Microsoft Rewards 自动任务工具，支持 Windows Edge 导出登录态，以及 NAS/Docker 无头运行。程序只使用已有登录态，不保存或上传 Microsoft 密码。

## 当前入口

- `rewards_earn.py`：唯一主程序，负责每日任务、月度任务、登录态校验和邮件通知。
- `nas_cron.sh`：NAS 定时入口。
- `monthly_watchdog.py`：月度任务状态监控。
- `nas_autorun.sh`、`nas_selfheal.sh`：NAS 启动和自愈脚本。

## 1. Windows 首次登录与导出

```powershell
cd <项目目录>
python -m pip install -r requirements.txt
python rewards_earn.py export
```

1. 关闭所有 Microsoft Edge 窗口，避免浏览器配置目录被锁定。
2. 执行 `python rewards_earn.py export`。
3. 在弹出的 Edge 窗口中完成 Microsoft 登录、条款确认或验证码。
4. 回到终端按 Enter，确认看到 `storage_state 已写入`。
5. 生成的 `storage_state.json` 只用于部署到自己的 NAS，不能提交到 GitHub 或 Gitee。

如果导出提示 `EXPORT_EDGE_CLOSED`，关闭残留 Edge 进程后重新执行。若页面停留在 `account.live.com/tou`，先在窗口中完成条款确认再按 Enter。

## 2. Windows 手动运行

```powershell
python rewards_earn.py
```

首次运行建议使用可见浏览器确认登录态；确认成功后，程序会执行每日任务、搜索任务和当前可用的月度任务。已完成的任务会根据页面绿色勾选状态跳过，不会重复点击。

## 3. NAS / Docker 部署

将以下内容部署到 NAS 应用目录：

```text
rewards_earn.py
nas_cron.sh
monthly_watchdog.py
nas_autorun.sh
nas_selfheal.sh
Dockerfile
requirements_nas.txt
config.json             # 私有配置，不提交仓库
data/storage_state.json # Windows 导出的登录态，不提交仓库
```

部署后由 `nas_cron.sh` 调度容器运行。手动验证时可以进入 NAS 任务目录执行：

```bash
./nas_cron.sh
```

部署脚本应使用项目维护者提供的 NAS 推送工具，并指定远端项目目录。推送完成后必须检查文件哈希、换行符、权限和 Python 语法，不要使用 `scp` 或直接管道覆盖文件。

NAS 运行日志保存在 `data/` 下。每次运行结束后先看日志末尾，再确认 Rewards 页面积分或任务状态发生变化。

## 4. 配置邮件通知

复制配置模板为私有配置文件，并填写 SMTP 参数：

```json
{
  "email_notify": {
    "enabled": true,
    "smtp_server": "smtp.example.com",
    "smtp_port": 465,
    "sender": "sender@example.com",
    "auth_code": "SMTP授权码",
    "recipient": "recipient@example.com"
  }
}
```

`auth_code` 是邮箱 SMTP 授权码，不是邮箱登录密码。也可以使用 NAS 环境变量 `REWARDS_MAIL_SERVER`、`REWARDS_MAIL_PORT`、`REWARDS_MAIL_SENDER`、`REWARDS_MAIL_AUTH` 和 `REWARDS_MAIL_TO`，避免把授权码写入文件。

邮件会同时显示错误代码、中文含义、自动处理结果、下一步操作和日志位置。

## 5. 认证失败和恢复

Microsoft 可能要求重新确认条款、重新认证或完成验证码。程序会识别认证墙，写入 `auth_required.json`，暂停定时任务并在邮件中给出中文含义和恢复步骤。完成 Windows 登录后重新导出并部署新的登录态，认证状态才会自动解除。

程序不会绕过 Microsoft 的条款确认或验证码，也不保证第三方页面规则永久不变。

恢复流程：

1. 在 Windows 执行 `python rewards_earn.py export`。
2. 在 Edge 中完成登录、条款确认或重新认证。
3. 将新的 `storage_state.json` 部署到 NAS 的 `data/storage_state.json`。
4. 重新运行一次任务，确认日志出现认证成功和积分校验结果。

认证失败期间，NAS 会自动暂停重复调度；只有检测到新的登录态指纹后才恢复任务。

## 6. 常用文件

| 文件 | 用途 |
| --- | --- |
| `rewards_earn.py` | Windows 和容器的主程序 |
| `nas_cron.sh` | NAS 定时调度、认证熔断和日志入口 |
| `monthly_watchdog.py` | 月度任务状态监控 |
| `nas_autorun.sh` | NAS 启动初始化 |
| `nas_selfheal.sh` | NAS 基础运行环境自愈 |
| `config.example.json` | 配置模板 |
| `tests/` | 认证与错误处理测试 |

## 安全

不要提交以下文件：`storage_state.json`、`config.json`、`edge_profile/`、`data/`、`authorized_keys_seed.txt`、运行日志和调试快照。真实配置和登录态只应保存在本机或 NAS 私有目录。

本项目仅用于个人自动化研究，请遵守 Microsoft Rewards 服务条款并自行承担使用风险。
