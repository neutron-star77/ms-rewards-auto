# CLAUDE.md — auto-sign/microsoft

> 本文件是本项目对 AI 助手的**显性纪律（强制）**。每次对话开头加载，优先级高于全局记忆的"软约束"。以下任一条若与默认行为冲突，以本文件为准。
> 说明：CodeBuddy 无"写文件自动触发命令"的系统级 hook，故用本文件作最可靠的前置纪律；配套全局记忆 ID 21808785 等。

## 0. 最常被违反的铁律（优先执行，曾因遗漏造成返工）

- **交付性 `.md` 文档完成后，必须用 Typora 打开给用户看**：任何报告 / 架构稿 / 说明 / 审阅稿等 `.md` 交付物写完后，立即 `execute_command`：
  `& 'E:\Program Files (x86)\typora\Typora\Typora.exe' '<md绝对路径>'`（`requires_approval: false`，本机安全操作）。
  **绝不允许只用 `open_result_view` 代替**——`open_result_view` 只是 IDE 预览，用户明确要看 Typora 渲染效果。可辅以 `open_result_view`，但 Typora 打开是必须项。（全局记忆 ID 21808785；2026-08-05 再次遗漏，已强化并补执行）

- **本地源改动后自动部署到 NAS**：改完 `rewards_earn.py` 等本地源，**立即** 执行
  `robocopy "f:\AI\projects\auto-sign\microsoft" "Z:\gadget\microsoft" <改动文件> /R:2 /W:2`，
  核对 `LastWriteTime` 与内容含新改动，勿等用户提醒。SMB 优先，勿走 ssh 写。
  （曾因未部署导致 `len(coroutine)` 崩溃 + 月任务误报。）

## 1. 技能触发区分（审计 vs 签到，曾误派）

- 「审计存量项目」「优化已有项目」「代码审查」「给代码做体检」「优化一下」「检查代码」→ **必须触发 `my-audit-existing-project`**，不要误派 `auto-sign`。
- `auto-sign` 仅用于本项目**具体实现 / 运行 / 部署 / NAS 操作**（不含审计/优化/建模/代码审查意图）。
- 两入口互补：`#42 my-newtask-flow`（新建 0→1）→ `#40 my-audit-existing-project`（审计/迭代 1→N）。

## 2. 项目知识

- **本月攻略（BingMonthlyPC）月任务**：4 周、每周一个。按 `current_month_week_index()` = `(day-1)//7+1` 判定"已完成周数 >= 当前周序号"为正常，避免把 1/4（第一周）误报为未完成。`is_locked_monthly` 仅作文案兜底。
- **NAS 运行副本**：`Z:\gadget\microsoft`（= `\\192.168.5.4\Web\gadget\microsoft`）。Docker 由 `nas_cron.sh` 以 `-v ${SCRIPT_DIR}:/app` 挂载，覆盖镜像内 `COPY` 的副本，故生产实际跑宿主该文件——这正是"改完必须部署"的原因。
- **本地源是真相**：`f:\AI\projects\auto-sign\microsoft`（主脚本 `rewards_earn.py`）。

## 3. 统一项目文档约定（模块化，便于二次开发）

- 经 `#42` / `#40` 后，仓库应有：`CONTEXT.md`（统一语言 glossary，词汇单一真相源）/ `docs/adr/`（架构决策记录，0001-*.md 递增）/ `CLAUDE.md`（本文件，常态纪律）/ `docs/agents/issue-tracker.md`（setup 生成）。
- `.scratch/<feature>/` 放临时 spec / ticket 草稿。
- `CONTEXT.md` 是二次开发/修改的唯一词汇表，避免"一个词干多职"。

## 4. Agent skills 区块（供 #42 / #40 setup 识别）

- 本项目已初始化 mp-engineering 工作流（见上方约定）。新任务走 `#42 my-newtask-flow`，存量审计/优化走 `#40 my-audit-existing-project`。
- 状态：已初始化（本 CLAUDE.md 即 `## Agent skills` 载体）。

## 5. 跨项目通用记忆（不在此重复）

- 其余跨项目铁律（NAS 操作 SMB 优先、QNAP 端口与 root 后门通道、Typecho 固定域名、Hermes 等）见全局知识库，涉及时按全局记忆执行，不在此冗余复制。
- 若需为其它项目（maoboli / jpg-lossless 等）同样固化纪律，应在各自项目仓库根建独立 `CLAUDE.md`，不在本项目文件里堆砌。
