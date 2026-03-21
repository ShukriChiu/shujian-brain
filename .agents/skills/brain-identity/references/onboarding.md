# Onboarding 引导手册

AI 在引导用户完成初始设置时，按照以下步骤逐一引导。每完成一步都要验证成功后再进入下一步。

## 步骤总览

```
Step 1: 创建 Supabase 项目 → 拿到数据库连接串
Step 2: 配置 .env 文件 → 填入连接信息
Step 3: 初始化数据库 → 建表建函数
Step 4: (可选) 部署 Embed Edge Function → 启用语义搜索
Step 5: (可选) 配置 LLM API Key → 启用反思/合成/学习
Step 6: 验证一切就绪
```

---

## Step 1: 创建 Supabase 项目

引导用户到 https://supabase.com 创建免费项目。

需要获取的信息：
- **Database Connection String**（Transaction 模式，端口 5432）
  - 位置：Project Settings → Database → Connection string → URI
  - 格式：`postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres`

AI 的引导话术示例：
> "首先需要一个 Supabase 数据库来存储你的大脑数据。如果你还没有 Supabase 账号：
> 1. 去 https://supabase.com 注册（免费额度足够个人使用）
> 2. 创建一个新项目，记住你设的数据库密码
> 3. 等项目创建完成后（约 1 分钟），去 Project Settings → Database
> 4. 找到 Connection string 区域，选 URI 格式，复制连接串
> 5. 把连接串告诉我（或者直接填到 .env 里）"

**验证方法**：`brain_identity.py setup --check-db`

---

## Step 2: 配置 .env

引导用户复制 `.env.example` 为 `.env` 并填入信息：

```bash
cp .env.example .env
```

必填项：
- `BRAIN_PROFILE` — 用户的名字/代号
- `BRAIN_DATABASE_URI` — Step 1 拿到的连接串

AI 的引导话术示例：
> "现在配置你的大脑身份。编辑 .env 文件：
> - `BRAIN_PROFILE` 填你想让 AI 怎么认识你，比如你的名字
> - `BRAIN_DATABASE_URI` 填刚才复制的数据库连接串"

---

## Step 3: 初始化数据库

运行 init 命令建表：

```bash
python3 .agents/skills/brain-identity/scripts/brain_identity.py init
```

这会：
1. 执行 `schema/` 下所有 SQL 文件（建表、建函数、注册定时任务）
2. 创建 `brain.ai_state` 行（AI 人格初始状态）
3. 创建空白 identity sections
4. 生成骨架 AGENTS.md
5. 如果 .env 里有 API key，自动同步到 `brain.secrets` 表

**验证方法**：`brain_identity.py setup --check-tables`

---

## Step 4: (可选) 部署 Embed Edge Function

启用语义搜索（`find --semantic`）需要一个 Embedding Edge Function。

仓库中已提供代码：`supabase/functions/embed/index.ts`

部署步骤：
1. 安装 Supabase CLI：`npm install -g supabase`
2. 登录：`supabase login`
3. 关联项目：`supabase link --project-ref <你的项目ref>`
4. 部署函数：`supabase functions deploy embed --no-verify-jwt`
5. 拿到函数 URL：`https://<project-ref>.supabase.co/functions/v1/embed`
6. 设置一个调用密钥（可以自定义任意字符串）
7. 填入 .env：
   ```
   BRAIN_EMBED_URL=https://<project-ref>.supabase.co/functions/v1/embed
   BRAIN_API_KEY=你自定义的密钥
   ```

AI 的引导话术示例：
> "语义搜索是可选的高级功能。如果你现在不需要，可以跳过这一步——基础的记忆读写完全不受影响。
> 想启用的话，我来引导你部署一个 Supabase Edge Function..."

**验证方法**：`brain_identity.py setup --check-embed`

---

## Step 5: (可选) 配置 LLM API Key

启用反思、合成、知识获取等高级功能需要 LLM API。

1. 注册 OpenRouter：https://openrouter.ai
2. 创建 API Key
3. 填入 .env：`OPENROUTER_API_KEY=sk-or-xxx`
4. 运行 init 或手动同步：key 会自动存入 `brain.secrets` 表

AI 的引导话术示例：
> "LLM API 用于大脑的高级功能——从碎片记忆合成身份、反思、学习网页知识。
> 基础的记忆存储不需要它。想启用的话：
> 1. 去 https://openrouter.ai 注册并创建 API Key
> 2. 填到 .env 的 OPENROUTER_API_KEY"

**验证方法**：`brain_identity.py setup --check-llm`

---

## Step 6: 完整验证

```bash
python3 .agents/skills/brain-identity/scripts/brain_identity.py setup
```

会逐项检查并输出状态报告：
```
🧠 大脑环境检查
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ .env 文件存在
  ✅ BRAIN_PROFILE = shujian
  ✅ 数据库连接正常
  ✅ brain.entries 表存在
  ✅ brain.ai_state 表存在
  ⬜ Embedding 未配置（语义搜索不可用，可选）
  ✅ LLM API Key 已配置
  ✅ AGENTS.md 已生成

  状态: 基础功能就绪 ✅
  可选: 部署 Embed Edge Function 启用语义搜索
```

---

## 常见问题

**Q: 数据库连接失败？**
- 检查连接串格式（必须是 Transaction 模式，端口 5432）
- 确认密码正确（Supabase 项目密码，不是账号密码）
- 确认项目未暂停（免费项目 7 天不活跃会暂停）

**Q: init 报错 "schema brain does not exist"？**
- 这是正常的——init 会自动创建 schema。如果仍然报错，检查数据库连接权限。

**Q: 不想用 Supabase？**
- 任何 PostgreSQL 数据库都可以，只要支持 `pgvector` 扩展。把连接串填到 `BRAIN_DATABASE_URI` 即可。
