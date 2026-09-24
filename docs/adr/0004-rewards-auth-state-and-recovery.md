# ADR-0004：Rewards 认证墙与完整 storage state 生命周期

- 状态：已接受（2026-09-23）
- 关联：`CONTEXT.md` 认证墙 / `TOU_REQUIRED`、`rewards_earn.py`、`nas_cron.sh`

## 背景

NAS 运行不能交互 Microsoft 登录流程。旧实现使用持久化 Chromium profile 后再调用
`add_cookies`，只恢复 cookies，丢弃导出的 `origins/localStorage`；运行结束也不回写新的
`storage_state`。同时，代码用 `"live.com" in URL` 判断登录页，无法区分普通登录失效和
`account.live.com/tou/accrue` 账户条款确认页，复检还可能把两次认证重定向误报为“全部完成”。

## 决策

1. **认证墙统一分类**：按 hostname/path 判断登录、重新认证和 TOU 页面；`TOU_REQUIRED`
   与 `AUTH_EXPIRED` 使用稳定错误码，错误 URL 日志只保留 origin/path，不写认证查询参数。
2. **NAS 使用原生 Playwright 状态导入**：通过 `chromium.launch()` + `new_context(storage_state=...)`
   同时恢复 cookies 和 origins/localStorage，不再使用“持久化 profile + add_cookies”的半套导入。
3. **只在已确认进入 Rewards 后回写**：任务结束时把最新状态写入同目录临时文件，再用
   `os.replace` 原子替换正式文件；认证墙、损坏文件或启动失败均不得覆盖旧状态。
4. **导出默认刷新 Windows profile**：`export` 先执行 Edge 自检并重建复制 profile，导出前
   必须再次确认页面已经回到 Rewards；仍停留在登录/TOU 页面则拒绝写出状态文件。需要复用
   自动化窗口副本时显式使用 `export reuse`。
5. **复检遇认证墙即失败**：`_scan_unfinished` 不再静默跳过认证重定向，避免空结果制造假成功。
6. **认证墙触发调度熔断**：失败时写入 `data/auth_required.json`，记录错误码与旧状态指纹；`nas_cron.sh` 在新 `storage_state.json` 到达前暂停调度，成功验证并原子回写后清除标记。

## 后果

- 正面：NAS 能使用完整状态快照，状态可在成功运行后续期；失败邮件能直接指出是条款确认还是
  登录态过期；认证跳转不会再被报告为“全部完成”。
- 正面：TOU/重新认证不会被定时任务反复撞击；状态文件真正更新后才自动恢复。
- 代价：Microsoft 要求重新确认条款时仍需人工在 Windows 端处理，这是无凭据 NAS 环境无法
  自动解决的外部前置条件；完成后必须重新执行 `export` 并覆盖 NAS 状态文件。
- 边界：Playwright/Chromium 版本仍需与 `requirements_nas.txt` 保持兼容；生产部署必须通过
  `nas_push.ps1`，并完成字节、CR、权限、语法门禁。

## 修订历史

- 2026-09-23 首版：修复认证墙误分类、状态半导入、状态不回写和复检假成功。
- 2026-09-24：增加认证事件熔断、状态 SHA-256 恢复判定和运行态 JSON 原子写入。
