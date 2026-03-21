---
name: brain-identity
description: 管理 AI 大脑的身份系统——AGENTS.md 的生成、更新、初始化。每次对话开始时必须检查 AGENTS.md 是否已初始化，未初始化则自动运行 init。覆盖场景包括：新 fork 后的首次对话自动初始化、更新身份章节、从碎片记忆合成结构化身份、重新生成 AGENTS.md。即使用户没有明确说"初始化"，只要 AGENTS.md 头部没有 brain-identity generated 标记，就应自动执行初始化。
alwaysApply: true
---

# Brain Identity — AGENTS.md 生成器

## 核心理念

AGENTS.md 是 AI 每次对话自动加载的上下文（`alwaysApply: true`）。本 skill 让它从"手动维护的静态文件"变成"从数据库 + 模板自动生成的动态文件"。

好处：
1. **零手动配置** — 新用户 fork 后只需配 `.env`，首次对话 AI 自动初始化
2. **单一数据源** — 身份信息存 DB（`kind=identity`），AGENTS.md 是生成产物
3. **自动成长** — 碎片记忆通过 `synthesize` 合成为结构化章节，AGENTS.md 随对话自然丰富

## 每次对话开始：自动检测协议

**这是最重要的协议。** 每次新对话开始时，AI 必须检查 AGENTS.md 是否已由 brain-identity 生成：

1. 读取 AGENTS.md 的前 10 行
2. 查找 `<!-- brain-identity generated` 标记
3. **如果找到** → 正常进入对话，AGENTS.md 已就绪
4. **如果没找到** → 说明是新用户或未初始化，执行以下流程：

```bash
# 自动初始化
python3 shujian-brain/.agents/skills/brain-identity/scripts/brain_identity.py init
```

初始化完成后，告诉用户："我已经为你创建了 AI 大脑。随着我们的对话，我会逐步了解你。"

**特殊情况**：如果 `.env` 不存在或 `BRAIN_DATABASE_URI` 未配置，提示用户：
- "需要先配置 `.env` 文件。请复制 `.env.example` 为 `.env`，填入你的数据库连接和 profile 名。"

## AGENTS.md 章节结构

| 章节 | 模板文件 | 数据源 | 说明 |
|------|---------|--------|------|
| B0 系统协议 | `protocol.md` | 模板（通用） | 四层架构、更新规则、操作方式 |
| B1 关于人类 | `about-human.md` | DB `section=about-human` | 人类的信息、工作风格、期望 |
| B2 关于 AI | `about-ai.md` | 模板 + DB `section=about-ai` | 通用能力表 + per-profile 关系 |
| B3 协作模式 | `collaboration.md` | DB `section=collaboration` | 怎么一起工作 |
| B4 进化路线图 | `roadmap.md` | DB `section=roadmap` | 方向和计划 |
| B5 成长时间轴 | `timeline.md` | DB `kind=event` 自动聚合 | 里程碑事件 |
| B6 AI 的想法 | `thoughts.md` | DB `kind=wish` + `section=ai-thoughts` | 心愿 + 反思 |

## 数据模型

复用 `brain.entries` 表，`kind=identity`：

```
kind=identity, owner=<profile>, subject=<profile>,
meta={"section": "about-human"}, content="章节完整内容..."
```

每个 profile 约 5 条 identity entries。B5/B6 部分内容从 `kind=event` / `kind=wish` 自动聚合。

## 脚本路径

```bash
python3 shujian-brain/.agents/skills/brain-identity/scripts/brain_identity.py <command>
```

## 命令速查

```bash
# 初始化（通常由 AI 自动执行，也可手动）
brain_identity.py init [profile_name]

# 从 DB + templates 重新生成 AGENTS.md
brain_identity.py generate

# 更新指定章节（更新 DB 后自动 regenerate）
brain_identity.py update about-human "新的内容..."
brain_identity.py update about-human @/path/to/file.md

# 列出所有章节及状态
brain_identity.py sections

# 一次性迁移：解析现有 AGENTS.md 导入 DB
brain_identity.py migrate [path/to/AGENTS.md]

# 从碎片记忆合成身份章节（LLM 辅助）
brain_identity.py synthesize              # 全部章节
brain_identity.py synthesize about-human  # 指定章节
```

## 对话中的身份更新

当 AI 在对话中积累了对人类的新认知，应该：

1. **碎片记忆** → `brain_db.py add --kind memory`（即时，每次对话都做）
2. **身份合成** → `brain_identity.py synthesize`（定期，比如积累了 10+ 条新记忆后）
3. **章节直接更新** → `brain_identity.py update <section>`（当有明确的大段内容要更新时）

不需要每次对话都 synthesize。碎片记忆积累到一定量后再合成效果更好。

## 与其他 skills 的关系

```
brain-memory (碎片记忆) → brain-identity (合成+生成) → AGENTS.md (输出)
brain-soul (人格/情绪) → 影响 AI 行为风格
```

- **brain-memory** — 记录碎片记忆（`kind=memory`），是 identity 的数据来源
- **brain-soul** — 管理 AI 情绪/人格（`brain.ai_state`），与 identity 独立但互补
- **brain-identity** — 把碎片记忆合成为结构化身份，生成 AGENTS.md

## 新用户体验（fork 后）

```
1. Fork 并 clone 仓库
2. cp .env.example .env && 编辑配置
3. 打开 Cursor，把仓库加入 workspace
4. 开始对话 → AI 自动检测到未初始化 → 自动运行 init → 生成骨架 AGENTS.md
5. 正常聊天，AI 逐步积累认知
6. 定期 synthesize 让 AGENTS.md 自动成长
```

用户不需要手动跑任何命令。
