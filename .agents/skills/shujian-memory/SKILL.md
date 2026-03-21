---
name: shujian-memory
description: 管理书剑共享大脑 v3——长期记忆 + 外部知识获取 + 多模态理解 + 知识消化 + pg_cron 自动化。每当书剑在对话中透露个人信息、偏好、情绪、生活细节、目标变化，或你观察到稳定行为模式时，必须使用本 skill 写入数据库。需要研究外部知识时用 learn/search，需要理解图片/视频/音频时用 see，需要回顾和反思时用 reflect/digest。每次对话开始时应运行 `pending` 检查是否有 pg_cron 标记的待办任务。即使用户没有明确说"记住这个"，只要内容属于"关于书剑这个人"的信息，就应主动存储。
---

# 共享大脑 v4 — 多 Profile 架构

## 核心理念

AGENTS.md 是人类能直接看到的"白板"——放规则、建议、想法、方向性思考。
数据库是 AI 的"私人笔记本"——放关于**这个人**的细粒度理解，跨对话持久积累。
外部知识系统让大脑能**主动学习**——从网页、搜索结果中获取知识并存储。
多模态系统让大脑能**看见世界**——理解图片、视频、音频内容。
知识消化系统让大脑能**自我反思**——定期聚合记忆生成洞察、自动发现关联、清理过时记忆。
pg_cron 自动化让大脑能**自主运转**——decay 纯 SQL 自动执行，reflect/digest/auto-link 通过 pending 机制触发。

## 多 Profile 支持

同一套代码和数据库，通过 `.env` 中的 `BRAIN_PROFILE` 隔离不同人的记忆和灵魂。

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `BRAIN_PROFILE` | 当前 profile 名 | `shujian` / `xiaohou` |
| `BRAIN_DATABASE_URI` | PostgreSQL 连接串 | `postgresql://...` |
| `BRAIN_API_KEY` | Embedding 函数密钥 | `brain-shujian-2026` |
| `BRAIN_EMBED_URL` | Embedding 函数地址 | `https://xxx.supabase.co/functions/v1/embed` |
| `LLM_MODEL` | 文本 LLM | `google/gemini-2.5-flash` |
| `LLM_MODEL_OMNI` | 多模态 LLM | `xiaomi/mimo-v2-omni` |

配置文件位于 `shujian-brain/.env`（已 gitignore）。脚本自动加载，环境变量优先级高于 .env。

**为新人创建空白大脑**：只需修改 `.env` 中 `BRAIN_PROFILE=xiaohou`，首次运行时自动创建该 profile 的空白 ai_state 和 owner 隔离。

## 数据模型（v4）

统一表：`brain.entries`

- 隔离字段：`owner`（按 profile 隔离，所有查询自动过滤）
- 固定字段：`kind`, `subject`, `content`, `tags`, `confidence`, `source`, `event_date`
- 扩展字段：`meta`（jsonb）
- 关联字段：`related uuid[]`
- 向量字段：`embedding vector(1536)`（RAG 已接入，model: `openai/text-embedding-3-small` via OpenRouter）

辅助表：
- `brain.ai_state`：AI 人格状态（每个 profile 一行，id = profile 名）
- `brain.secrets`：API 密钥管理（全局共享）
- `brain.cron_tasks`：定时任务管理（全局共享）

### kind

- `memory`: 认知类记忆（关于该人的人格、偏好、决策风格）
- `event`: 时间线事件（里程碑、重要事件）
- `pattern`: 行为模式（带 frequency，自动计数）
- `wish`: 心愿/问题
- `convo`: 对话摘要
- `knowledge`: 从外部获取的知识片段（网页、搜索结果）
- `insight`: AI 生成的洞察和反思
- `bookmark`: 收藏的 URL/资源
- `emotion`: AI 情绪记录（灵魂系统）
- `personality`: AI 人格进化事件（灵魂系统）

### subject

- `<profile名>` / `ai` / `collaboration` / `project` / `business` / `system` / `external`

## 脚本路径

- 主脚本：`shujian-brain/.agents/skills/shujian-memory/scripts/brain_db.py`
- 配置文件：`shujian-brain/.env`

运行方式：

```bash
python3 shujian-brain/.agents/skills/shujian-memory/scripts/brain_db.py <command> [args]
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

## 什么时候用外部知识获取

- 书剑提到某个行业/话题/技术，需要了解背景 → `learn` 或 `search`
- 需要研究竞品、市场趋势 → `search --save`
- 书剑分享了一个 URL，想了解内容 → `learn`
- AI 主动为书剑准备行业洞察 → `search`

## 判断模板

> 这条信息是关于**书剑这个人**的吗？
> - 是 → 存数据库（kind=memory/pattern/event）
> - 否，是关于项目/业务/技术的 → 存对应 AGENTS.md
> - 否，是一次性操作指令 → 不记录
>
> 书剑需要了解某个外部话题吗？
> - 是，有具体 URL → `learn <url>`
> - 是，需要广泛搜索 → `search "<query>" --save`

## 命令速查（v3）

### 核心命令（CRUD）

```bash
# 写入
brain_db.py add "<content>" \
  --kind memory --subject shujian \
  --meta '{"aspect":"personality","title":"效率极致追求"}' \
  --tags "效率,偏好" --confidence 0.95 --source direct_statement

# 检索（关键词 + 过滤 + 语义）
brain_db.py find 效率 --kind memory --subject shujian --limit 10
brain_db.py find --meta aspect=personality --kind memory
brain_db.py find --tag 品牌 --kind memory
brain_db.py find "书剑的工作风格" --semantic --kind memory

# 更新 / 关联 / 归档
brain_db.py update <id> --content "新内容" --confidence 1.0
brain_db.py link <id1> <id2>
brain_db.py forget <id>

# 行为模式
brain_db.py observe work_habit "深夜高效工作" --example "凌晨仍在推进需求"

# 查看
brain_db.py timeline --limit 20
brain_db.py wishes --status open
brain_db.py stats
brain_db.py dump --kind memory --subject shujian
```

### 多模态理解

```bash
# 分析图片（URL 或本地路径）
brain_db.py see --image "https://example.com/photo.jpg" --prompt "描述这张图"
brain_db.py see --image /path/to/local.png --prompt "图中有什么文字"

# 分析视频
brain_db.py see --video "https://example.com/video.mp4" --prompt "总结视频内容"

# 分析音频
brain_db.py see --audio /path/to/audio.wav --prompt "这段音频说了什么"

# 多模态组合 + 保存
brain_db.py see --image "url" --video "url" --prompt "对比分析" --save --tags "分析"
```

### 外部知识获取

```bash
# 抓取网页 → LLM 摘要 → 存入知识库
brain_db.py learn https://example.com --tags "教育,趋势"
brain_db.py learn https://example.com --query "这篇文章的核心观点是什么"

# 搜索网页 → LLM 总结
brain_db.py search "K12 家教行业 2026 趋势" --limit 5
brain_db.py search "AI agent 最新进展" --save  # 保存到知识库
brain_db.py search "竞品分析" --raw  # 原始结果（不经过 LLM）
```

### 知识消化

```bash
# 反思：聚合记忆生成洞察
brain_db.py reflect --focus "最近的工作状态"
brain_db.py reflect --kind memory --limit 30
brain_db.py reflect --no-save  # 只展示，不保存

# 自动关联：LLM 发现记忆间的隐藏联系
brain_db.py auto-link --limit 30

# 记忆衰减：清理低质量+过时的记忆
brain_db.py decay --days 60 --threshold 0.7 --dry-run
brain_db.py decay --days 90 --threshold 0.5

# 日报/周报/月报
brain_db.py digest --period day
brain_db.py digest --period week
brain_db.py digest --period month --no-save
```

### 密钥管理

```bash
brain_db.py secret list
brain_db.py secret set openrouter_api_key "sk-xxx" --desc "OpenRouter"
brain_db.py secret get openrouter_api_key
brain_db.py secret delete old_key
```

### 定时任务管理

```bash
brain_db.py cron list
brain_db.py cron add weekly-reflect "brain_db.py reflect" "0 9 * * 1"
brain_db.py cron enable weekly-reflect
brain_db.py cron disable weekly-reflect
brain_db.py cron delete weekly-reflect
brain_db.py cron run  # 执行所有启用的任务
brain_db.py cron run weekly-reflect  # 只执行指定任务
```

### 待办任务（pg_cron 自动化）

```bash
brain_db.py pending           # 查看待执行的任务
brain_db.py pending --execute # 执行所有待办任务
```

### 语义检索（RAG）

```bash
brain_db.py find "书剑的工作风格" --semantic --kind memory
brain_db.py embed <id>           # 单条生成 embedding
brain_db.py embed-all            # 批量补向量
```

## 自动化（pg_cron）

已配置 3 个 pg_cron 任务，在 PostgreSQL 层面自动运行：

| pg_cron Job | 周期 | 执行方式 | 说明 |
|-------------|------|---------|------|
| `brain-monthly-decay` | 每月 1 日 3:00 | 纯 SQL 直接执行 | `brain.auto_decay()` 归档+降权，无需 LLM |
| `brain-weekly-tasks` | 每周一 9:00 | 标记到 `pending_tasks` | 标记 reflect + digest 待 AI 执行 |
| `brain-monthly-tasks` | 每月 1 日 4:00 | 标记到 `pending_tasks` | 标记 auto-link 待 AI 执行 |

### 执行流程

```
pg_cron 定时触发
  ├── decay: 纯 SQL → brain.auto_decay() → 直接完成
  └── reflect/digest/auto-link: 写入 brain.pending_tasks
        ↓
AI 对话开始时: brain_db.py pending
  → 发现待办任务 → brain_db.py pending --execute → 调用 LLM 完成
```

### 每次对话建议

```bash
# 对话开始时检查是否有 pg_cron 标记的待办
brain_db.py pending
# 如果有待办，执行它们
brain_db.py pending --execute
```

## LLM 配置

| 模型 | 用途 | Provider |
|------|------|----------|
| `google/gemini-2.5-flash` | 文本类：摘要、反思、关联分析、周报 | OpenRouter |
| `xiaomi/mimo-v2-omni` | 多模态：图片/视频/音频理解 | OpenRouter (xiaomi) |
| `openai/text-embedding-3-small` | Embedding 向量 | Supabase Edge Function |

API key 存储在 `brain.secrets` 表中。所有 HTTP 调用使用 Python stdlib `urllib`，**零外部依赖**。

依赖链：
- learn/search → Firecrawl CLI + LLM (gemini-2.5-flash)
- see → LLM (mimo-v2-omni，多模态)
- reflect/digest/auto-link → LLM (gemini-2.5-flash)
- embedding → Supabase Edge Function
- decay → pg_cron 纯 SQL（不需要 LLM）

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

### knowledge

```json
{
  "title": "K12在线教育2026趋势",
  "url": "https://example.com/article",
  "query": "核心观点",
  "scraped_at": "2026-03-19T06:30:00Z",
  "raw_length": 12000
}
```

### insight

```json
{
  "title": "书剑工作模式洞察",
  "focus": "工作风格和协作模式",
  "memory_count": 22,
  "generated_at": "2026-03-19T06:42:00Z"
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

### 开始对话前

```bash
# 1. 检查 pg_cron 标记的待办任务
brain_db.py pending
# 如果有待办，执行它们
brain_db.py pending --execute

# 2. 可选回忆
brain_db.py find --kind memory --subject shujian --limit 10
brain_db.py wishes --status open --limit 10
```

### 对话中（捕捉到人设信息）

```bash
brain_db.py add "书剑本周重点是把老师端体验再打磨一轮" \
  --kind memory --subject shujian \
  --meta '{"title":"本周优先级","aspect":"goals"}' \
  --tags "目标,节奏" --confidence 1.0 --source direct_statement
```

### 书剑提到某个话题需要了解

```bash
brain_db.py learn https://some-article.com --tags "行业,趋势"
brain_db.py search "相关领域最新动态" --save
```

### 书剑分享了图片/视频需要理解

```bash
brain_db.py see --image "https://xxx" --prompt "这是什么" --save
brain_db.py see --image /path/to/screenshot.png --prompt "提取文字内容"
```

### 观察到稳定行为时

```bash
brain_db.py observe decision_making "对信息架构有清晰分层思维" \
  --example "主动提出 AGENTS.md 存建议，数据库存对人的理解"
```

### 定期维护

```bash
brain_db.py cron run  # 执行所有定时任务（reflect + digest + decay + auto-link）
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

## 外部知识架构

```
brain_db.py learn/search
  ↓ subprocess
Firecrawl CLI (已全局安装)
  ↓ API
Firecrawl Cloud → 网页抓取/搜索
  ↓ 返回 Markdown
brain_db.py → LLM 摘要(OpenRouter) → 写入 brain.entries(kind=knowledge)
```

## 注意事项（必须遵守）

- 不要存敏感信息到 entries（密码、银行卡号等）——API key 用 `brain.secrets`
- 同一认知不要重复写多条，用 `update` 或 `link`
- `meta` 必须保持 JSON 对象结构
- 高价值信息优先 `source=direct_statement` + `confidence=1.0`
- 复杂分析可用 MCP `execute_sql`
- 语义检索需要 `BRAIN_API_KEY` 环境变量
- LLM 功能（learn/search/reflect/digest/auto-link）需要 `openrouter_api_key` 在 secrets 或环境变量中
- 外部知识获取需要 `firecrawl` CLI 已安装且 `firecrawl_api_key` 在 secrets 中
- 多模态（see）使用 `xiaomi/mimo-v2-omni`，需要 OpenRouter 账号已启用 xiaomi provider
- 所有 HTTP 调用使用 Python stdlib `urllib`，无需安装 `openai` / `httpx` 等第三方包
- 数据库连接有自动重试机制（3 次，指数退避）
- pg_cron 任务在 PostgreSQL 层面自动运行，无需外部调度器
- 每次对话开始时运行 `brain_db.py pending` 检查是否有待执行任务
