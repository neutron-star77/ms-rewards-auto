# Skill 库整理架构方案（待审阅 v0.1）

> 目的：把散落的 `mp-*` / `sp-*` / 项目 SOP 收敛成「**2 入口 + 1 套统一方法论原子集 + 项目 SOP/工具层**」，让任意项目都走「新建（#42）→ 规范开发 → 审计（#40）」闭环；文档模块化（统一 `CONTEXT.md` / `docs/adr/` / `CLAUDE.md`），减少 token 消耗、便于二次开发。
> 当前阶段：**只出文档、不动任何文件**。审阅通过后再分阶段落地。

---

## 0. 背景与核心诉求

- 用户痛点：技能库 ~70 个，散、触发词撞车（`auto-sign` 误抢 `my-audit-existing-project` 的「审计存量项目」），模型每次要加载多个候选、token 浪费。
- 用户真正要的**不是把 #40 和 #42 合并**，而是：
  - 以 **#42 `my-newtask-flow`**（新建项目入口）和 **#40 `my-audit-existing-project`**（审计存量入口）作为**两个固定方法论入口**；
  - 把底下 `mp-*` / `sp-*` 等重复技能**合并成「一套通用方法论原子集」**；
  - 任意项目都走「新建→规范开发→审计」闭环，统一项目开发语言与文档结构，模块化、易维护。

---

## 1. 目标架构

```
                    ┌────────────── 两个入口（保留，不合并）──────────────┐
   新项目 ─────► #42 my-newtask-flow        │    #40 my-audit-existing-project ─────► 存量项目
                 (grill→prototype→spec→     │     (setup→architecture-audit→
                  tickets→implement/TDD→     │      domain-model→可选重构→固化 CLAUDE.md)
                  code-review→建模→发布)      │
                    └────────────────────────┴──────────────────────────────┘
                                     │ 只编排下方「统一原子集」（单一真源）
                                     ▼
        ★ 一套通用方法论原子集（mp/sp 去重合并后的唯一真源，约 14 个）
        grill / wayfinder / to-spec / to-tickets / implement / tdd /
        code-review / diagnosing-bugs / domain-modeling /
        improve-codebase-architecture / codebase-design / research /
        triage / setup（一次性初始化）

                    项目 SOP / 工具层（不属于方法论，各自沉淀到项目 CONTEXT.md）
        auto-sign · jpg-lossless-dev · zhuxian · ops-deck · siyuan-nas-sync
        xinghuisama-blog-deploy · blog-tunnel · beast-url-encoder · firecrawl
        nas 运维族 · ui 引擎族 · agency-agents · 4-layer-stack · codebuddy-cost-saving …
```

**原则**
- 入口管「顺序/衔接」，原子集管「具体做法」，项目 SOP 管「本项目知识」。
- 项目专属 SOP **不强行并入 #40/#42**，而是各自沉淀到项目自己的 `CONTEXT.md`（由原子集 `domain-modeling` 生成/维护）。
- `sp-*`（superpowers 中文切片）与 `mp-*`（mattpocock 中文移植）**去重合并，以 `mp-*` 为唯一真源**；`sp-*` 活动副本删除，上游 `upstream-protect/superpowers/` 保留不动（不破坏上游同步）。

---

## 2. 原子集：合并 mp / sp 后的唯一真源

### 2.1 当前 inventory（实际目录）

`my-skills` 下：
- `mp-engineering-*`（16 个）：grill-with-docs / prototype / implement / tdd / code-review / diagnosing-bugs / domain-modeling / improve-codebase-architecture / codebase-design / to-spec / to-tickets / wayfinder / triage / ask-matt / resolving-merge-conflicts / setup-matt-pocock-skills
- `mp-*` 非 engineering：mp-misc-setup-pre-commit / mp-personal-edit-article / mp-productivity-grilling / mp-productivity-writing-great-skills / mp-productivity-handoff / mp-productivity-teach / mp-productivity-grill-me
- `sp-*`（17 个）：superpowers 的中文切片版（上游在 `upstream-protect/superpowers/`）

### 2.2 合并映射表（sp → mp，唯一真源）

| sp-*（活动副本，删） | 并入 mp-*（唯一真源） | 备注 |
|---|---|---|
| `sp-systematic-debugging` | → `mp-engineering-diagnosing-bugs` | 吸收其独有诊断流程（含 .ts/.sh 脚本若通用） |
| `sp-test-driven-development` | → `mp-engineering-tdd` | 直接合并 |
| `sp-writing-skills` | → `mp-productivity-writing-great-skills` | 写 skill 指南唯一真源 |
| `sp-requesting-code-review` + `sp-receiving-code-review` | → `mp-engineering-code-review` | 请求/接收双视角并入双轴审查 |
| `sp-verification-before-completion` | → `mp-engineering-code-review` | 并入"完成前验证"段 |
| `sp-executing-plans` | → `mp-engineering-implement` | 执行计划即实现 |
| `sp-finishing-a-development-branch` | → `mp-engineering-implement` | 收尾分支并入 |
| `sp-writing-plans` | → `mp-engineering-to-spec` | 写计划即写 spec |
| `sp-subagent-driven-development` | → `mp-engineering-to-tickets` | 子代理驱动开发=拆票派发 |
| `sp-dispatching-parallel-agents` | → `mp-engineering-to-tickets` | 并行派发注释段 |
| `sp-brainstorming` | → `mp-engineering-wayfinder` | 头脑风暴=大工程地图 |
| `sp-using-git-worktrees` | → `mp-engineering-implement` | worktree 注释段 |
| `sp-using-superpowers` | → 通用说明，删活动副本 | 其"如何用 superpowers"内容压缩进 `mp-engineering-ask-matt` 路由说明 |

### 2.3 合并后原子集（唯一真源）

```
方法论原子（engineering）
  mp-engineering-grill-with-docs        澄清 + ADR/术语骨架
  mp-engineering-prototype              一次性原型验证
  mp-engineering-implement              实现（内驱 TDD；含 worktree/branch 收尾）
  mp-engineering-tdd                    红-绿-重构
  mp-engineering-code-review            双轴审查（Standards+Spec）+ 完成前验证 + 请求/接收视角
  mp-engineering-diagnosing-bugs        诊断 bug（吸收 systematic-debugging）
  mp-engineering-domain-modeling        统一语言(CONTEXT.md) + ADR
  mp-engineering-improve-codebase-architecture  架构体检 + 可视化 HTML 报告
  mp-engineering-codebase-design        深模块词汇（seam/depth/locality）
  mp-engineering-to-spec                综合规范文档
  mp-engineering-to-tickets             拆带阻塞边 ticket（含并行派发）
  mp-engineering-wayfinder              大工程决策地图（含头脑风暴）
  mp-engineering-triage                 issue 分诊
  mp-engineering-ask-matt               路由器（含 superpowers 用法说明）
  mp-engineering-resolving-merge-conflicts  合并冲突
  mp-engineering-setup-matt-pocock-skills    一次性仓库初始化（disable-model-invocation）
  mp-engineering-research               调研/资料核实

生产力原子（保留，单一职责）
  mp-productivity-writing-great-skills  写 skill 指南（吸收 sp-writing-skills）
  mp-productivity-grilling / handoff / teach / grill-me  个人生产力（互不重叠）
  mp-misc-setup-pre-commit              husky 预提交钩子
  mp-personal-edit-article              个人文章编辑
```

`sp-*` 活动副本全部移除（17→0），上游 `upstream-protect/superpowers/` 保留。

---

## 3. 入口 #42 / #40 改造点

- 两个入口**当前已只编排 `mp-engineering-*`、未直接引用 `sp-*`**，所以改造量小：
  - **#42 `my-newtask-flow`**：description 加注「编排统一原子集 `mp-engineering-*`；`sp-*` 已合并进对应 `mp-*`，请勿再引用 `sp-*`」。
  - **#40 `my-audit-existing-project`**：同上标注；并写清与 #42 互补（#42 面向 0→1，#40 面向 1→N 自我迭代）。
  - 两个入口各自 description **加路由说明**：意图是"审计/优化/建模/代码审查"一律走 #40，不要误派到项目 SOP（如 `auto-sign`）。
- 入口本身**不合并、不删**。

---

## 4. 项目 SOP 层：沉淀到 CONTEXT.md

项目专属 SOP（`auto-sign`、`jpg-lossless-dev`、`zhuxian`、`ops-deck`、`siyuan-nas-sync`、`xinghuisama-blog-deploy`、`blog-tunnel`）**保留为独立 skill**（它们是知识，不是方法论），但约定：
- 其关键知识同时沉淀进项目自己的 `CONTEXT.md`（由 `domain-modeling` 原子生成）。
- 当 `#40` 审计该项目时，读 `CONTEXT.md` 即可拿到项目专属上下文，无需反复加载 SOP skill → 省 token。
- `auto-sign` 的 description 已收窄（上一轮完成），与 #40 不再撞车。

---

## 5. 统一项目文档模板（模块化、单一真相源）

每个项目经 #42 或 #40 后，仓库根应有：

```
项目仓库/
├─ CONTEXT.md          # 统一语言（ubiquitous language） glossary —— 词汇单一真相源
├─ docs/adr/           # 架构决策记录，0001-*.md 递增
│   ├─ 0001-xxx.md
│   └─ ...
├─ CLAUDE.md           # 常态纪律：## Agent skills 区块，固化"大改动前先审计+建模"
├─ docs/agents/        # issue-tracker.md（setup-matt-pocock-skills 生成）
└─ .scratch/<feature>/ # 临时 spec / ticket 草稿
```

- `CONTEXT.md` 是二次开发/修改的**唯一词汇表**，避免"一个词干多职"的术语混乱。
- `docs/adr/` 记录难以回退的决策，**带日期、带理由、可回滚**。
- `CLAUDE.md` 让新会话模型自动照"先审计后改"的飞轮执行。

---

## 6. 执行计划（分阶段、可回滚）

| 阶段 | 动作 | 风险 | 回滚 |
|---|---|---|---|
| **P0 审阅** | 本文档确认 | 无 | — |
| **P1 合并原子集** | 把 `sp-*` 独有内容并入对应 `mp-*`；删除 `my-skills` 下 14 个 `sp-*` 活动副本 | 中：需逐个核对 `sp-*` 独有内容 | `upstream-protect/superpowers/` 仍在，可从上游重建 |
| **P2 改入口** | #42 / #40 description 加"只编排 mp-*"与路由说明 | 低 | 备份还原 |
| **P3 生成模板** | 给 auto-sign 等项目经 #40 生成 `CONTEXT.md`/`docs/adr/`/`CLAUDE.md` | 低 | 文件级删除 |
| **P4 收尾** | 写一份 `SKILL-LIBRARY-MAP.md`（最终架构地图）落地到 `F:\AI\skills\` | 低 | 删除 |

> 注：每个项目的 `CONTEXT.md` 生成建议**单独走一次 #40**，不要一次性批量生成（避免上下文过载、质量下降）。

---

## 7. 风险与回滚

- **P1 是主要风险点**：`sp-*` 可能含 `mp-*` 没有的独有脚本/流程。对策：合并前**逐个 `read_file` 比对 `sp-*` 与对应 `mp-*`**，只把独有部分并入，绝不静默丢内容；上游源保留可作二次核对。
- **触发词漂移**：合并后模型可能因 `sp-*` 消失而找不到旧触发词。对策：P2 在入口与 `ask-matt` 路由里声明 `sp-*` 已合并，并保留旧触发词的语义映射。
- **回滚**：所有删除操作前，用 `robocopy` 把 `my-skills` 下待删 `sp-*` 备份到 `F:\AI\skills\_backup_sp_<date>\`（非仓库内），确认无误后再删。

---

## 8. 决策分析 + 选择题（目的 / 原因 / 优缺点）

> 每个决策都给出「为什么这样做 / 目的」「优点」「缺点」，并附选择题。**请在 Typora 阅读后，在对话框选择题中作答；全部选完**后我才执行（建各项目 CLAUDE.md + P1 合并原子集）。

### Q1. 原子集唯一真源：以 `mp-*` 为唯一真源、删 `sp-*` 活动副本（保留上游）？
- **为什么 / 目的**：`sp-*` 是 superpowers 的中文切片、`mp-*` 是 mattpocock 的中文移植，二者大量重叠（如 `sp-systematic-debugging`≈`mp-engineering-diagnosing-bugs`、`sp-writing-plans`≈`mp-engineering-to-spec`）。双份真源让模型每次要在 sp/mp 间二选一，token 浪费且触发词易漂移。统一成 `mp-*` 单一真源，消除二义性。
- **优点**：技能数 14→0（`sp-*` 全清），原子集清晰；触发词无歧义；省 token；`upstream-protect/superpowers` 上游保留，不破坏同步。
- **缺点**：合并需逐个比对 `sp-*` 独有内容并入 `mp-*`，有少量手工核对成本；若 `sp-*` 有 `mp-*` 没有的独有脚本需谨慎迁移；模型短期可能找不到旧 `sp-*` 触发词（已在入口/路由声明合并缓解）。
- **选项**：① 同意（mp 真源 + 删 sp 活动副本，保留上游）｜② 保留 sp 作并行备选（不删）｜③ 只合并纯文档类 sp，含脚本的 sp 暂留

### Q2. 入口 #42 / #40 保持独立不合并，仅加路由说明？
- **为什么 / 目的**：#42 `my-newtask-flow` 管"新建 0→1"、#40 `my-audit-existing-project` 管"审计/迭代 1→N"，职责正交。合并会丢掉"两个固定方法论入口"的清晰度——你正要的就是"开新任务走 #42、审计走 #40"的双入口闭环。仅加路由说明（审计意图→#40，别误派 auto-sign）即可消除撞车。
- **优点**：零破坏性；两个入口语义清晰，一句话就能选对入口；模型按意图路由。
- **缺点**：若路由说明不显眼仍可能误派（已用 MEMORY 约束 + auto-sign 收窄兜底双重保险）。
- **选项**：① 同意保持独立，仅加路由说明｜② 仍想合并两入口为一个

### Q3. 项目 SOP（auto-sign 等）保留为独立 skill + 知识沉淀进项目 `CONTEXT.md`？
- **为什么 / 目的**：SOP（auto-sign、jpg-lossless-dev 等）是项目专属"实现知识"（路径/命令/坑），`CONTEXT.md` 是方法论产出的"统一语言"。二者正交：SOP 供"怎么改/怎么部署"用，`CONTEXT.md` 供"审计/二次开发"用。全删 SOP 只留 `CONTEXT.md` 会丢实现细节；全留则技能库仍有冗余"项目 SOP"类 skill。
- **优点（保留）**：知识双轨，实现与审计都顺；SOP 可独立触发，`CONTEXT.md` 让审计免加载 SOP 省 token。
- **缺点（保留）**：技能库"项目 SOP"类 skill 数量未减（但属知识非方法论，留着合理）。
- **选项**：① 保留独立 skill + 沉淀 CONTEXT.md（推荐）｜② 删 SOP 只留 CONTEXT.md｜③ 部分删（auto-sign 已收窄，可删其 SOP 下沉）

### Q4. 执行顺序 P1→P2→P3→P4，且 P3 每项目单独走一次 #40？
- **为什么 / 目的**：P1（合并原子集，删 sp）是主要风险点（删文件），需先做且可回滚（上游在）；P3（生成 CONTEXT.md）需逐项目上下文，批量生成会上下文过载、质量下降。分阶段可逐步验证。
- **优点**：风险可控、质量高、每步可回滚。
- **缺点**：耗时较长（多阶段、多次确认）。
- **选项**：① 同意分阶段（P1→P2→P3→P4）｜② 想更快（P3 批量生成 CONTEXT.md）｜③ 调整顺序

### Q5. 落地架构地图 `SKILL-LIBRARY-MAP.md` 到 `F:\AI\skills\`？
- **为什么 / 目的**：合并后技能库结构变了（sp 消失、原子集清单固定），需一份"最终地图"给人/模型快速定位，避免遗忘新结构、便于日后维护。
- **优点**：单一索引，维护方便。
- **缺点**：多一个需随结构变更更新的文档（维护成本小）。
- **选项**：① 落地｜② 不落地（仅靠 CLAUDE.md / 全局记忆）

### Q6. 各项目 CLAUDE.md / 处置（任务 A，已全部选定并执行中）

用户逐项选定（2026-08-05），实际探查 `f:\AI\projects` 共 15 个目录 + `blog/themes/` 下主题。最终处置矩阵：

| 项目 | 处置 | 状态 |
|---|---|---|
| auto-sign | 维持（microsoft/ 已有完善 CLAUDE.md + 3 ADR） | 完成 |
| blog | 建入口级 CLAUDE.md（索引 maoboli/my-butterfly/butterfly2） | 完成 |
| hermes-nas | 优化（含评估包 `hermes-nas-deploy` SOP skill；并入 9999-cf-notify 纪律） | 建 CLAUDE.md 完成 / 优化待做 |
| jpg-lossless | 建 CLAUDE.md（双目录发布 + UTF-8 BOM 纪律） | 完成 |
| listen1 | 优化（二次开发） | 待做 |
| note-sync-hub | 优化 | 待做 |
| OpenFic | 删除（第三方成品）→ **执行时用户拒绝，保留不删** | 保留 |
| photo | 建轻量 CLAUDE.md（+ 后续教写提示词/下载模型） | 完成 |
| shiji-kb | 补充完善（shiji-kb-main/ 已有 CLAUDE.md 14.5KB） | 待做 |
| start-pages | 优化 | 待做 |
| vfe | 建 CLAUDE.md（三件套不可改、靠 spec 适配） | 完成 |
| workflow-dashboard | 建 CLAUDE.md（含 271 人名册 roster.py + dispatcher 5 步调度用法） | 完成 |
| zhongyi | 建 CLAUDE.md（补充 CC0 + build_kb.py 纪律） | 完成 |
| 9999-cf-notify | 并入 hermes-nas 纪律（不单独建） | 已并入 |
| 9999-dashboard | 优化（清理 `_residual/`、固化 RESET.md 纪律） | 建 CLAUDE.md 完成 / 优化待做 |

> **调度机制已查清**：名册唯一接缝 `F:\AI\agency-agents-skills`（271 角色目录）；`roster.py`(build/find/dispatch) + `superpowers-dispatcher` 技能(workflow-dashboard/hermes/skills) 5 步派活。详见 workflow-dashboard/CLAUDE.md。

> **执行顺序**：Q6 的 CLAUDE.md 已落地 8 个；剩余优化项 + 技能库 P1→P2→P3→P4 进行中。

## 9. 执行进度（截至 2026-08-05）

| 阶段 | 内容 | 状态 |
|---|---|---|
| Q1–Q6 决策 | 全部选定并固化 | ✅ 完成 |
| Q6 任务 A | 8 个项目 CLAUDE.md 落地 + OpenFic 保留 + 优化项待做 | ✅ CLAUDE.md 完成 / 优化待做 |
| **P1 合并原子集** | 14 个 `sp-*` 独有内容并入对应 `mp-*`、随附支撑文档、删除 sp-*（备份 `_backup_sp_20260805/`） | ✅ 完成 |
| **P2 入口路由** | `#42 my-newtask-flow` / `#40 my-audit-existing-project` description 加路由说明 + 双入口互补 + `sp-*` 已合并声明 | ✅ 完成 |
| **P4 落地地图** | `F:\AI\skills\SKILL-LIBRARY-MAP.md` 已生成（分层 + 路由 + 合并说明） | ✅ 完成 |
| **P3 生成 CONTEXT.md** | 每项目单独走一次 `#40` 生成 `CONTEXT.md`/`docs/adr/`（架构体检 + 领域建模） | ✅ 完成（4 项目：blog / hermes-nas / 9999-dashboard / vfe，各建根 `CONTEXT.md` + `adr/` + 固化 `CLAUDE.md`；均跳过重型架构体检 HTML——部署/脚手架类非典型可重构代码库，价值低） |

### P3 说明（需逐项目、由用户指定顺序）
- 每个项目经 `#40` 审计后，仓库根应有：`CONTEXT.md`（项目专属上下文，由 `domain-modeling` 生成）+ `docs/adr/`（架构决策）+ `CLAUDE.md`（已建 8 个）。
- 文档明确要求「每项目单独走一次 #40，不要一次性批量生成」以避免上下文过载、质量下降。
- 建议从高价值且尚无 CONTEXT.md 的项目起步（如 `blog`、`hermes-nas`、`9999-dashboard`、`vfe`），逐个与用户确认后再跑 `#40`。
