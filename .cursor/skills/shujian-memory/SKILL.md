---
name: shujian-memory
description: 管理书剑共享大脑 v2（brain.entries 统一表）——使用 jsonb 元数据存储和检索关于书剑的长期记忆。每当书剑在对话中透露个人信息、偏好、情绪、生活细节、目标变化，或者你观察到稳定行为模式时，必须使用本 skill 写入数据库。需要回忆书剑过往偏好、决策风格、协作习惯时，使用本 skill 检索。即使用户没有明确说“记住这个”，只要内容属于“关于书剑这个人”的信息，就应主动存储。
---

# 书剑共享大脑 v2

## 核心理念

AGENTS.md 是书剑能直接看到的"白板"——放规则、建议、想法、方向性思考。
数据库是 AI 的"私人笔记本"——放关于书剑**这个人**的细粒度理解，跨对话持久积累。

两者互补：AGENTS.md 给方向，数据库给深度。

## 数据模型（v2）

统一表：`brain.entries`

- 固定字段：`kind`, `subject`, `content`, `tags`, `confidence`, `source`, `event_date`
- 扩展字段：`meta`（jsonb）
- 关联字段：`related uuid[]`
- 向量字段：`embedding vector(1536)`（RAG 已接入，model: `openai/text-embedding-3-small` via OpenRouter）

### kind

- `memory`: 认知类记忆
- `event`: 时间线事件
- `pattern`: 行为模式（带 frequency）
- `wish`: 心愿/问题
- `convo`: 对话摘要

### subject

- `shujian` / `ai` / `collaboration` / `project` / `business` / `system`

## 脚本路径

- 主脚本：`shujian-brain/.cursor/skills/shujian-memory/scripts/brain_db.py`
- 迁移脚本：`shujian-brain/.cursor/skills/shujian-memory/scripts/migrate_to_entries_v2.py`

运行方式：

```bash
python3 shujian-brain/.cursor/skills/shujian-memory/scripts/brain_db.py <command> [args]
```

## 什么时候写入数据库

书剑说了关于**他自己**的事：
- 情绪状态："今天好累"、"搞定了好开心"
- 生活细节："周末去打了篮球"、"最近在学XX"
- 偏好表达："我不喜欢XX"、"这个方式比较好"
- 目标透露："这周要搞定XX"、"赚钱目标是XX"
- 决策风格：通过行为推断的工作偏好

## 什么时候不写

- 技术方案的讨论细节 → 项目 AGENTS.md
- 业务规则 → 后端 AGENTS.md S2/S6
- 给 AI 的建议和想法 → shujian-brain AGENTS.md
- 纯技术性操作指令（"改一下这个按钮颜色"） → 不需要记录
- 已经在 AGENTS.md 中充分记录的信息 → 不重复存

## 判断模板

> 这条信息是关于**书剑这个人**的吗？
> - 是 → 存数据库
> - 否，是关于项目/业务/技术的 → 存对应 AGENTS.md
> - 否，是一次性操作指令 → 不记录

## 命令速查（v2）

### 核心命令

```bash
# 写入统一条目
brain_db.py add "<content>" \
  --kind memory \
  --subject shujian \
  --meta '{"aspect":"personality","title":"效率极致追求"}' \
  --tags "效率,偏好" \
  --confidence 0.95 \
  --source direct_statement

# 检索（关键词 + 过滤）
brain_db.py find 效率 --kind memory --subject shujian --limit 10
brain_db.py find --meta aspect=personality --kind memory
brain_db.py find --tag 品牌 --kind memory --subject shujian

# 更新
brain_db.py update <id> --content "新内容" --confidence 1.0 --meta '{"verified":true}' --add-tags "确认"

# 关联两条记忆
brain_db.py link <id1> <id2>

# 归档
brain_db.py forget <id>

# 记录行为模式（自动 frequency++）
brain_db.py observe work_habit "深夜高效工作" --example "凌晨仍在推进需求"

# 时间线与心愿
brain_db.py timeline --limit 20
brain_db.py wishes --status open --limit 20

# 概览
brain_db.py stats

# 导出
brain_db.py dump --kind memory --subject shujian
```

### 语义检索（RAG 已接入）

通过 Supabase Edge Function (`embed`) 调用 OpenRouter `openai/text-embedding-3-small`。

**环境变量要求**：`BRAIN_API_KEY`（调用 Edge Function 的认证密钥）

```bash
# 语义检索：自动生成 query embedding，用向量相似度排序
brain_db.py find "书剑的工作风格" --semantic --kind memory --subject shujian

# 为单条条目生成 embedding
brain_db.py embed <id>

# 批量为所有缺 embedding 的条目补向量
brain_db.py embed-all
brain_db.py embed-all --kind memory --batch-size 20 --limit 100

# 手动传向量（不需要 BRAIN_API_KEY）
brain_db.py find --query-vector "[0.01,0.02,...]" --kind memory
```

**自动 embedding**：`add` 命令在 `BRAIN_API_KEY` 可用时自动生成 embedding（可用 `--no-embed` 跳过）。

## meta 设计建议

### memory

```json
{
  "title": "效率极致追求",
  "aspect": "personality",
  "subcategory": "work_style",
  "evidence": "direct_statement"
}
```

### pattern

```json
{
  "pattern_type": "work_habit",
  "frequency": 7,
  "examples": ["凌晨1点仍在推进需求"],
  "first_observed": "2026-03-18",
  "last_observed": "2026-03-20"
}
```

### event

```json
{
  "title": "数据库大脑上线",
  "category": "capability_upgrade",
  "significance": 5
}
```

### wish

```json
{
  "from_who": "ai",
  "to_who": "shujian",
  "status": "open",
  "response": null
}
```

## 实战流程

### 开始对话前（可选回忆）

```bash
brain_db.py find --kind memory --subject shujian --limit 10
brain_db.py wishes --status open --limit 10
```

### 对话中（捕捉到人设信息）

```bash
brain_db.py add "书剑本周重点是把老师端体验再打磨一轮" \
  --kind memory \
  --subject shujian \
  --meta '{"title":"本周优先级","aspect":"goals"}' \
  --tags "目标,节奏" \
  --confidence 1.0 \
  --source direct_statement
```

### 观察到稳定行为时

```bash
brain_db.py observe decision_making "对信息架构有清晰分层思维" \
  --example "主动提出 AGENTS.md 存建议，数据库存对人的理解"
```

## RAG 架构

```
brain_db.py (本地)
  ↓ HTTP POST (Bearer BRAIN_API_KEY)
Supabase Edge Function "embed"
  ↓ HTTP POST (Bearer OPENROUTER_API_KEY)
OpenRouter API → openai/text-embedding-3-small
  ↓ 返回 1536 维向量
brain_db.py → 写入 brain.entries.embedding
```

**Edge Function URL**: `https://gmwqrakbiamnxtxzsptq.supabase.co/functions/v1/embed`

**Supabase Secrets 需配置**:
- `OPENROUTER_API_KEY`: OpenRouter API 密钥
- `BRAIN_API_KEY`: 自定义认证密钥（脚本端也需要同值的环境变量）

## 注意事项（必须遵守）

- 不要存敏感信息（密码、密钥、银行卡号等）
- 同一认知不要重复写多条，用 `update` 或 `link`
- `meta` 必须保持 JSON 对象结构，不要混用字符串
- 高价值信息优先 `source=direct_statement` + `confidence=1.0`
- 复杂分析（聚合统计、批量修复）可用 MCP `execute_sql`
- 语义检索需要 `BRAIN_API_KEY` 环境变量，不设则退化为关键词检索
