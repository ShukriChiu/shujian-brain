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

# 2. 把 my-brain/ 加入你的 Cursor workspace
# 3. 开始对话 — AI 会自动触发 Onboarding，手把手引导你完成设置
#    包括：创建 Supabase 数据库 → 配置 .env → 初始化建表 → 生成 AGENTS.md
# 4. 全程不需要你查文档或手动跑命令
```

如果你更喜欢手动设置：

```bash
cp .env.example .env              # 复制配置模板
# 编辑 .env：填入 BRAIN_PROFILE、BRAIN_DATABASE_URI
python3 .agents/skills/brain-identity/scripts/brain_identity.py init
python3 .agents/skills/brain-identity/scripts/brain_identity.py setup  # 验证
```

## 架构

```
my-brain/
├── AGENTS.md                  ← 自动生成，AI 每次对话加载
├── .env                       ← 你的配置（gitignore）
├── .env.example               ← 配置模板
├── schema/                    ← 数据库迁移文件（init 自动执行）
├── supabase/functions/embed/  ← Embedding Edge Function（可选，语义搜索）
└── .agents/skills/
    ├── brain-identity/        ← AGENTS.md 生成器 + Onboarding 引导
    │   ├── scripts/brain_identity.py
    │   ├── templates/         ← 7 个章节模板
    │   └── references/        ← Onboarding 详细步骤
    ├── brain-memory/          ← 长期记忆 + 知识获取
    │   └── scripts/brain_db.py
    └── brain-soul/            ← 情绪 + 人格系统
```

## 三个 Skills

### brain-identity — 身份管理 + Onboarding

AGENTS.md 的生成器。首次使用时引导用户完成全部设置。

```bash
brain_identity.py setup                    # 环境检查（逐项验证）
brain_identity.py init                     # 初始化（建表 + 生成 AGENTS.md）
brain_identity.py generate                 # 重新生成 AGENTS.md
brain_identity.py update <section> "内容"   # 更新某章节
brain_identity.py synthesize               # 从碎片记忆合成身份
brain_identity.py sections                 # 查看各章节状态
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

**必需：**
- **Supabase PostgreSQL** — 数据存储（免费额度足够个人使用）
- **Python 3.10+** — 脚本运行（psycopg2 自动安装）

**可选（高级功能）：**
- **OpenRouter API** — LLM 调用：反思、合成、知识消化、人格进化
- **Embedding Edge Function** — 语义搜索（代码已提供：`supabase/functions/embed/`）
- **Firecrawl CLI** — 网页抓取学习

基础的记忆读写和 AGENTS.md 生成只需要数据库，不需要任何 API key。

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
