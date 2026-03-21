---
name: brain-identity
description: 管理 AI 大脑的身份系统——AGENTS.md 的生成、更新、初始化、Onboarding 引导。每次对话开始时必须检查 AGENTS.md 是否已初始化，未初始化则引导用户完成 Supabase 数据库连接、.env 配置、初始化建表，最终生成 AGENTS.md。即使用户没有明确说"初始化"，只要 AGENTS.md 头部没有 brain-identity generated 标记，就应自动触发 Onboarding 流程。也用于更新身份章节、从碎片记忆合成结构化身份、重新生成 AGENTS.md。
alwaysApply: true
---

# Brain Identity — AGENTS.md 生成器 + Onboarding 引导

## 核心理念

AGENTS.md 是 AI 每次对话自动加载的上下文。本 skill 让它从"手动维护的静态文件"变成"从数据库 + 模板自动生成的动态文件"，并负责引导新用户完成全部初始设置。

## 每次对话开始：检测 + Onboarding

**这是最重要的协议。** 每次新对话开始时：

### 情况 1: 已初始化（AGENTS.md 含 `brain-identity generated` 标记）

正常进入对话。

### 情况 2: 未初始化（新用户 / 首次使用）

触发 **Onboarding 引导流程**。AI 需要引导用户逐步完成设置，而不是直接报错。

**引导流程**（详细步骤见 `references/onboarding.md`）：

```
Step 1: Supabase 数据库
  └─ 引导用户创建 Supabase 项目，获取数据库连接串
  └─ 验证: brain_identity.py setup --check-db

Step 2: .env 配置
  └─ 引导用户 cp .env.example .env 并填入 BRAIN_PROFILE + 连接串
  └─ 验证: .env 文件存在且必填项非空

Step 3: 初始化
  └─ 运行 brain_identity.py init（自动建表 + 生成 AGENTS.md）
  └─ 验证: brain_identity.py setup --check-tables

Step 4: (可选) Embedding Edge Function
  └─ 引导用户部署 supabase/functions/embed 启用语义搜索
  └─ 验证: brain_identity.py setup --check-embed

Step 5: (可选) LLM API Key
  └─ 引导用户注册 OpenRouter 获取 key，启用反思/合成/学习
  └─ init 时 .env 中的 key 自动同步到 brain.secrets 表
  └─ 验证: brain_identity.py setup --check-llm
```

**每完成一步都运行对应的验证命令确认成功，再进入下一步。**

遇到问题时，读取 `references/onboarding.md` 获取详细的排错指南和引导话术。

**全部完成后**运行完整检查：

```bash
brain_identity.py setup
```

## AGENTS.md 章节结构

| 章节 | 模板文件 | 数据源 |
|------|---------|--------|
| B0 系统协议 | `protocol.md` | 模板（通用） |
| B1 关于人类 | `about-human.md` | DB `section=about-human` |
| B2 关于 AI | `about-ai.md` | 模板 + DB `section=about-ai` |
| B3 协作模式 | `collaboration.md` | DB `section=collaboration` |
| B4 进化路线图 | `roadmap.md` | DB `section=roadmap` |
| B5 成长时间轴 | `timeline.md` | DB `kind=event` 自动聚合 |
| B6 AI 的想法 | `thoughts.md` | DB `kind=wish` + `section=ai-thoughts` |

## 脚本路径

```bash
python3 shujian-brain/.agents/skills/brain-identity/scripts/brain_identity.py <command>
```

## 命令速查

```bash
# Onboarding: 环境检查（引导用户时逐项运行）
brain_identity.py setup
brain_identity.py setup --check-db      # 只检查数据库
brain_identity.py setup --check-tables   # 只检查表
brain_identity.py setup --check-embed    # 只检查 Embedding
brain_identity.py setup --check-llm      # 只检查 LLM

# 初始化（建表 + 生成 AGENTS.md + 同步 API key 到 secrets）
brain_identity.py init [profile_name]

# 重新生成 AGENTS.md
brain_identity.py generate

# 更新指定章节
brain_identity.py update about-human "新的内容..."
brain_identity.py update about-human @/path/to/file.md

# 列出所有章节及状态
brain_identity.py sections

# 迁移：解析现有 AGENTS.md 导入 DB
brain_identity.py migrate [path/to/AGENTS.md]

# 从碎片记忆合成身份章节（LLM 辅助）
brain_identity.py synthesize [section]
```

## 对话中的身份更新

1. **碎片记忆** → `brain_db.py add --kind memory`（即时，每次对话都做）
2. **身份合成** → `brain_identity.py synthesize`（定期，积累了 10+ 条新记忆后）
3. **章节直接更新** → `brain_identity.py update <section>`（大段内容更新时）

## 与其他 skills 的关系

```
brain-memory (碎片记忆) → brain-identity (合成+生成) → AGENTS.md (输出)
brain-soul (人格/情绪) → 影响 AI 行为风格
```

## 新用户体验

```
1. Fork 仓库
2. 打开 Cursor，加入 workspace
3. 开始对话 → AI 检测到未初始化 → 触发 Onboarding 引导
4. AI 手把手引导：创建 Supabase → 配 .env → 初始化 → (可选) 部署 Embedding → (可选) 配 LLM
5. 全程不需要用户查 README 或手动跑命令
6. 设置完成，正常聊天，AI 逐步认识你
```
