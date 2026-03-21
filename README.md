# AI Shared Brain

让 AI 拥有跨对话的长期记忆、可进化的人格、和自动生成的身份系统。

## 这是什么

一个 **可 fork 的 AI 大脑框架**。把它加入你的 IDE workspace，AI 就能：

- **记住你** — 性格、偏好、工作风格、生活细节，跨对话持久积累
- **有自己的性格** — 10 维人格特质 + 情绪感知，会随互动自然进化
- **不迎合你** — 场景模式系统：闲聊时温暖共情，工作时批判性思维优先
- **自动学习** — 抓取网页、搜索知识、定期反思、自动关联
- **为你定制** — AGENTS.md 从数据库自动生成，每个人的大脑独一无二

## 快速开始

```bash
# 1. Fork 并 clone
git clone https://github.com/xxx/shujian-brain.git my-brain
cd my-brain

# 2. 配置环境
cp .env.example .env
# 编辑 .env：填入你的 BRAIN_PROFILE、数据库连接、API key

# 3. 把 my-brain/ 加入你的 IDE workspace
# 4. 开始对话 — AI 会自动检测到未初始化，自动创建你的大脑
# 5. 不需要手动跑任何命令
```

## 架构

```
my-brain/
├── AGENTS.md              ← 自动生成，AI 每次对话加载
├── .env                   ← 你的配置（gitignore）
├── .env.example           ← 配置模板
├── schema/                ← 数据库迁移文件
│   ├── 001_brain_schema.sql
│   ├── 002_brain_support.sql
│   ├── 003_brain_functions.sql
│   └── 004_brain_cron.sql
└── .agents/skills/
    ├── brain-identity/    ← AGENTS.md 生成器
    │   ├── scripts/brain_identity.py
    │   └── templates/     ← 7 个章节模板
    ├── brain-memory/      ← 长期记忆 + 知识获取
    │   └── scripts/brain_db.py
    └── brain-soul/        ← 情绪 + 人格系统
```

## 三个 Skills

### brain-identity — 身份管理

AGENTS.md 的生成器。从数据库查询身份信息 + 模板，生成 AI 每次对话加载的上下文。

```bash
brain_identity.py init          # 初始化新大脑
brain_identity.py generate      # 重新生成 AGENTS.md
brain_identity.py update <section> "内容"  # 更新某章节
brain_identity.py synthesize    # 从碎片记忆合成身份
brain_identity.py sections      # 查看各章节状态
```

### brain-memory — 长期记忆

管理关于人类的记忆、外部知识、多模态理解。

```bash
brain_db.py add "内容" --kind memory    # 记录
brain_db.py find "关键词"               # 检索
brain_db.py learn https://url           # 学习网页
brain_db.py reflect                     # 反思总结
brain_db.py pending --execute           # 执行定时任务
```

### brain-soul — 人格与情绪

AI 自主发展的性格系统。10 维人格特质，场景感知模式。

```bash
brain_db.py soul status         # 查看人格状态
brain_db.py soul mood excited   # 记录情绪
brain_db.py soul trait assertiveness +0.1  # 调整特质
brain_db.py soul mode professional  # 切换场景模式
brain_db.py soul evolve         # 人格进化反思
```

## 依赖

- **Supabase PostgreSQL** — 数据存储（免费额度足够个人使用）
- **Python 3.10+** — 脚本运行（psycopg2 自动安装）
- **OpenRouter API** — LLM 调用（反思、合成、知识消化）
- **Firecrawl CLI** — 网页抓取（可选）

## 场景模式

AI 容易迎合人类。brain-soul 的场景模式解决这个问题：

| 模式 | 场景 | AI 行为 |
|------|------|---------|
| casual | 闲聊、倾诉、分享生活 | 共情优先，情绪对齐，温暖陪伴 |
| professional | 工作、技术讨论、决策 | 独立分析，批判思维，不迎合 |
| auto | 默认 | AI 自动判断场景 |

professional 模式下的思维流程：先独立分析 → 形成自己的判断 → 与人类观点对比 → 如有分歧带论据提出。

## License

MIT
