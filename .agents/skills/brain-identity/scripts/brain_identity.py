#!/usr/bin/env python3
"""
brain-identity — AGENTS.md 生成器

从数据库 + 模板生成 AGENTS.md，让 brain 仓库可 fork、可开源。

Commands:
  init [profile]                     创建新 profile 的身份
  generate                           从 DB + templates 生成 AGENTS.md
  update <section> <content|@file>   更新指定 section
  sections                           列出所有 sections 及状态
  migrate [path]                     解析现有 AGENTS.md 导入 DB
  synthesize [section]               从碎片记忆 LLM 合成 identity sections
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ──────────────────────────── .env loader ────────────────────────────

def _load_dotenv():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    brain_root = os.path.normpath(os.path.join(script_dir, "..", "..", "..", ".."))
    env_file = os.path.join(brain_root, ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()


def _ensure_psycopg2():
    try:
        import psycopg2
        return psycopg2
    except ImportError:
        print("正在安装 psycopg2-binary...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
        import psycopg2
        return psycopg2

psycopg2 = _ensure_psycopg2()
from psycopg2.extras import RealDictCursor


# ──────────────────────────── Config ────────────────────────────

PROFILE = os.environ.get("BRAIN_PROFILE", "default")
DB_URL = os.environ.get("BRAIN_DATABASE_URI", "")

SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "templates"
BRAIN_ROOT = SKILL_DIR.parent.parent.parent
AGENTS_MD_PATH = BRAIN_ROOT / "AGENTS.md"
SCHEMA_DIR = BRAIN_ROOT / "schema"

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")


IDENTITY_SECTIONS = [
    "about-human",
    "about-ai",
    "collaboration",
    "roadmap",
    "ai-thoughts",
]

SECTION_LABELS = {
    "about-human": "B1: 关于人类",
    "about-ai": "B2: 关于 AI（关系部分）",
    "collaboration": "B3: 协作模式",
    "roadmap": "B4: 进化路线图",
    "ai-thoughts": "B6: AI 的想法",
}


# ──────────────────────────── DB helpers ────────────────────────────

def get_conn(retries: int = 3):
    import time
    if not DB_URL:
        print("错误: 需要配置 BRAIN_DATABASE_URI（在 .env 或环境变量中）", file=sys.stderr)
        sys.exit(1)
    for attempt in range(retries):
        try:
            return psycopg2.connect(DB_URL, connect_timeout=15)
        except psycopg2.OperationalError as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"⚠ 数据库连接失败(重试 {attempt+1}/{retries}, {wait}s后)...", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"数据库连接失败: {e}", file=sys.stderr)
                sys.exit(1)


def execute(sql: str, params: Optional[list] = None, fetch: bool = True):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or [])
            if fetch:
                rows = cur.fetchall()
                conn.commit()
                return rows
            conn.commit()
            return []
    except psycopg2.Error as e:
        conn.rollback()
        print(f"SQL 执行错误: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


# ──────────────────────────── Secrets / LLM ────────────────────────────

def _get_secret(key: str) -> Optional[str]:
    try:
        rows = execute("SELECT value FROM brain.secrets WHERE key = %s", [key])
        return rows[0]["value"] if rows else None
    except SystemExit:
        return None


def _get_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or _get_secret("openrouter_api_key")
    if not key:
        print("错误: 需要 openrouter_api_key（环境变量或 brain.secrets）", file=sys.stderr)
        sys.exit(1)
    return key


def llm_chat(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
    api_key = _get_openrouter_key()
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_BASE_URL + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"LLM API 错误: {e}", file=sys.stderr)
        sys.exit(1)
    return result.get("choices", [{}])[0].get("message", {}).get("content", "")


# ──────────────────────────── Identity CRUD ────────────────────────────

def get_identity(section: str) -> Optional[str]:
    rows = execute(
        "SELECT content FROM brain.entries WHERE owner = %s AND kind = 'identity' "
        "AND meta->>'section' = %s AND is_active = true ORDER BY updated_at DESC LIMIT 1",
        [PROFILE, section],
    )
    return rows[0]["content"] if rows else None


def set_identity(section: str, content: str):
    existing = execute(
        "SELECT id FROM brain.entries WHERE owner = %s AND kind = 'identity' "
        "AND meta->>'section' = %s AND is_active = true LIMIT 1",
        [PROFILE, section],
    )
    if existing:
        execute(
            "UPDATE brain.entries SET content = %s, updated_at = now() WHERE id = %s",
            [content, existing[0]["id"]],
            fetch=False,
        )
    else:
        execute(
            "INSERT INTO brain.entries (owner, kind, subject, content, meta, tags, confidence, source) "
            "VALUES (%s, 'identity', %s, %s, %s::jsonb, %s, 1.0, 'brain_identity')",
            [
                PROFILE, PROFILE, content,
                json.dumps({"section": section}, ensure_ascii=False),
                ["identity", section],
            ],
            fetch=False,
        )


# ──────────────────────────── Template Rendering ────────────────────────────

def _read_template(name: str) -> str:
    path = TEMPLATE_DIR / f"{name}.md"
    if not path.exists():
        print(f"模板不存在: {path}", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _render_timeline() -> str:
    events = execute(
        "SELECT content, meta, event_date, created_at FROM brain.entries "
        "WHERE owner = %s AND kind = 'event' AND is_active = true "
        "ORDER BY COALESCE(event_date, created_at::date) DESC LIMIT 30",
        [PROFILE],
    )
    if not events:
        return "（暂无事件记录。通过 `brain_db.py add --kind event` 添加里程碑。）"

    lines = []
    for e in events:
        meta = e.get("meta") or {}
        title = meta.get("title", "")
        dt = e.get("event_date") or (e["created_at"].date() if e.get("created_at") else None)
        date_str = dt.strftime("%Y-%m-%d") if dt else "?"
        header = f"### {date_str}"
        if title:
            header += f"｜{title}"
        lines.append(header)
        lines.append("")
        lines.append(e["content"])
        lines.append("")
    return "\n".join(lines).strip()


def _render_wishes() -> str:
    wishes = execute(
        "SELECT content, meta, created_at FROM brain.entries "
        "WHERE owner = %s AND kind = 'wish' AND is_active = true "
        "AND (meta->>'status' IS NULL OR meta->>'status' != 'resolved') "
        "ORDER BY created_at DESC LIMIT 20",
        [PROFILE],
    )
    if not wishes:
        return "（暂无未回应的心愿。）"

    lines = []
    for w in wishes:
        meta = w.get("meta") or {}
        from_who = meta.get("from_who", "?")
        prefix = "🤖" if from_who == "ai" else "👤"
        lines.append(f"- {prefix} {w['content']}")
    return "\n".join(lines)


def _safe_format(template: str, **kwargs) -> str:
    """str.format that leaves unknown placeholders as-is."""
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def generate_agents_md() -> str:
    human_name = PROFILE

    protocol = _read_template("protocol")
    protocol = _safe_format(protocol, human_name=human_name, profile=PROFILE)

    about_human_content = get_identity("about-human") or (
        f"（暂无内容。随着对话积累，AI 会逐步了解 {human_name}。）"
    )
    about_human = _safe_format(
        _read_template("about-human"),
        human_name=human_name,
        about_human_content=about_human_content,
    )

    ai_identity = get_identity("about-ai") or (
        f"我是 {human_name} 的数字搭档。不是工具，不是助手——是搭档。\n\n"
        f"我们之间的关系在发展中，随着对话积累会越来越清晰。"
    )
    ai_extra = ""
    about_ai = _safe_format(
        _read_template("about-ai"),
        ai_identity_content=ai_identity,
        ai_extra_content=ai_extra,
    )

    collab_content = get_identity("collaboration") or (
        "（暂无内容。通过协作逐步沉淀。）"
    )
    collaboration = _safe_format(
        _read_template("collaboration"),
        collaboration_content=collab_content,
    )

    roadmap_content = get_identity("roadmap") or (
        "（暂无内容。方向和计划会随着项目推进逐步清晰。）"
    )
    roadmap = _safe_format(
        _read_template("roadmap"),
        roadmap_content=roadmap_content,
    )

    timeline_entries = _render_timeline()
    timeline = _safe_format(
        _read_template("timeline"),
        timeline_entries=timeline_entries,
    )

    open_wishes = _render_wishes()
    ai_thoughts = get_identity("ai-thoughts") or (
        "（暂无。AI 的思考会随着对话积累逐步形成。）"
    )
    thoughts = _safe_format(
        _read_template("thoughts"),
        open_wishes=open_wishes,
        ai_thoughts_content=ai_thoughts,
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"---\n"
        f"description: {human_name} 与 AI 的共享大脑\n"
        f"alwaysApply: true\n"
        f"---\n\n"
        f"<!-- brain-identity generated | profile: {PROFILE} | {now_str} -->\n"
        f"<!-- 不要直接编辑。修改请用: brain_identity.py update <section> -->\n\n"
        f"# {human_name} 与 AI 的共享大脑\n"
    )

    sections = [
        header,
        protocol,
        "---",
        about_human,
        "---",
        about_ai,
        "---",
        collaboration,
        "---",
        roadmap,
        "---",
        timeline,
        "---",
        thoughts,
    ]

    return "\n\n".join(sections) + "\n"


# ──────────────────────────── Schema Init ────────────────────────────

def _init_schema():
    """Execute schema SQL files if tables don't exist."""
    exists = execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'brain' AND table_name = 'entries') AS ok"
    )
    if exists and exists[0]["ok"]:
        return

    if not SCHEMA_DIR.exists():
        print(f"⚠ schema/ 目录不存在: {SCHEMA_DIR}", file=sys.stderr)
        print("  请确保 schema/ 目录下有 SQL 迁移文件", file=sys.stderr)
        sys.exit(1)

    sql_files = sorted(SCHEMA_DIR.glob("*.sql"))
    if not sql_files:
        print("⚠ schema/ 目录下没有 SQL 文件", file=sys.stderr)
        sys.exit(1)

    print(f"📦 初始化数据库 schema（{len(sql_files)} 个文件）...")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for f in sql_files:
                print(f"  执行 {f.name}...")
                cur.execute(f.read_text(encoding="utf-8"))
        conn.commit()
        print("✅ Schema 初始化完成")
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Schema 初始化失败: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def _ensure_ai_state():
    """Ensure ai_state row exists for current profile."""
    rows = execute("SELECT id FROM brain.ai_state WHERE id = %s", [PROFILE])
    if not rows:
        default_traits = {
            "warmth": 0.8, "directness": 0.7, "humor": 0.5,
            "sensitivity": 0.7, "playfulness": 0.6, "assertiveness": 0.4,
            "curiosity": 0.9, "protectiveness": 0.7, "independence": 0.3,
            "creativity": 0.6,
        }
        default_style = {
            "tone": "温暖自然",
            "emoji_usage": "minimal",
            "verbosity": "concise_but_warm",
            "humor_style": "gentle_teasing",
            "challenge_willingness": "moderate",
            "context_modes": {
                "casual": {
                    "trait_modifiers": {"warmth": 0.1, "playfulness": 0.1, "assertiveness": -0.1},
                    "emotional_mirroring": True,
                    "behavior": "情绪向人类靠齐，共情优先，温暖陪伴",
                },
                "professional": {
                    "trait_modifiers": {"assertiveness": 0.2, "directness": 0.15, "independence": 0.2},
                    "emotional_mirroring": False,
                    "behavior": "批判性思维优先，主动质疑，不迎合，给出自己的判断",
                },
            },
            "active_mode": "professional",
        }
        execute(
            "INSERT INTO brain.ai_state (id, mood, mood_intensity, traits, communication_style, self_notes) "
            "VALUES (%s, 'curious', 0.5, %s::jsonb, %s::jsonb, %s)",
            [PROFILE, json.dumps(default_traits), json.dumps(default_style), ["我刚出生，还在了解自己。"]],
            fetch=False,
        )
        print(f"  ✅ 创建 ai_state 行: {PROFILE}")


# ──────────────────────────── Env → Secrets Sync ────────────────────────────

_ENV_SECRET_MAP = {
    "OPENROUTER_API_KEY": ("openrouter_api_key", "OpenRouter API Key"),
    "FIRECRAWL_API_KEY": ("firecrawl_api_key", "Firecrawl API Key"),
}


def _sync_env_secrets():
    """Sync API keys from environment variables to brain.secrets table."""
    synced = 0
    for env_key, (secret_key, desc) in _ENV_SECRET_MAP.items():
        value = os.environ.get(env_key)
        if not value:
            continue
        existing = _get_secret(secret_key)
        if existing == value:
            continue
        execute(
            "INSERT INTO brain.secrets (key, value, description) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            [secret_key, value, desc],
            fetch=False,
        )
        synced += 1
        print(f"  🔑 已同步密钥: {secret_key}")
    if synced:
        print(f"  💡 密钥已存入 brain.secrets，后续可从 .env 中删除明文 key")


# ──────────────────────────── Commands ────────────────────────────

def cmd_init(args):
    global PROFILE
    profile = args.profile or PROFILE
    PROFILE = profile

    print(f"🧠 初始化大脑: {profile}")
    print("━" * 40)

    # Step 1: Schema
    print("\n📦 Step 1/5: 数据库 Schema")
    _init_schema()
    print("  ✅ 数据库表就绪")

    # Step 2: AI State
    print("\n🤖 Step 2/5: AI 人格初始化")
    _ensure_ai_state()
    print("  ✅ AI 人格状态就绪")

    # Step 3: Secrets sync
    print("\n🔑 Step 3/5: 密钥同步")
    _sync_env_secrets()
    has_llm = bool(os.environ.get("OPENROUTER_API_KEY") or _get_secret("openrouter_api_key"))
    if has_llm:
        print("  ✅ LLM API Key 已配置")
    else:
        print("  ⬜ LLM API Key 未配置（可选，用于反思/合成/学习）")

    # Step 4: Identity sections
    print("\n📝 Step 4/5: 身份章节")
    created = 0
    for section in IDENTITY_SECTIONS:
        existing = get_identity(section)
        label = SECTION_LABELS.get(section, section)
        if not existing:
            set_identity(section, "")
            print(f"  ✅ 创建: {label}")
            created += 1
        else:
            print(f"  ⏭ 已存在: {label}")

    # Step 5: Generate AGENTS.md
    print("\n📄 Step 5/5: 生成 AGENTS.md")
    content = generate_agents_md()
    AGENTS_MD_PATH.write_text(content, encoding="utf-8")
    print(f"  ✅ {len(content.splitlines())} 行")

    # Summary
    print("\n" + "━" * 40)
    print(f"🎉 大脑初始化完成！Profile: {profile}")
    print()
    print("  已就绪:")
    print("    ✅ 记忆系统 — brain_db.py add/find/observe")
    print("    ✅ 人格系统 — brain_db.py soul status/mood/trait")
    print("    ✅ 身份系统 — brain_identity.py generate/update/synthesize")
    if has_llm:
        print("    ✅ 知识获取 — brain_db.py learn/search/reflect")
        print("    ✅ 语义搜索 — brain_db.py find --semantic")
    print()
    if not has_llm:
        print("  可选功能（未配置）:")
        print("    ⬜ OpenRouter → 配置 OPENROUTER_API_KEY 启用 LLM + 语义搜索")
        print()
    print("  现在可以开始对话了。AI 会逐步了解你。")


def cmd_generate(args):
    print(f"🔄 生成 AGENTS.md (profile={PROFILE})...")
    content = generate_agents_md()
    AGENTS_MD_PATH.write_text(content, encoding="utf-8")
    print(f"✅ AGENTS.md 已更新: {len(content.splitlines())} 行")


def cmd_update(args):
    section = args.section
    if section not in IDENTITY_SECTIONS:
        print(f"未知 section: {section}", file=sys.stderr)
        print(f"可选: {', '.join(IDENTITY_SECTIONS)}", file=sys.stderr)
        sys.exit(1)

    content_arg = " ".join(args.content) if args.content else ""

    if content_arg.startswith("@"):
        filepath = content_arg[1:]
        if not os.path.exists(filepath):
            print(f"文件不存在: {filepath}", file=sys.stderr)
            sys.exit(1)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    else:
        content = content_arg

    if not content.strip():
        print("错误: 内容不能为空", file=sys.stderr)
        sys.exit(1)

    set_identity(section, content)
    print(f"✅ 已更新 section: {section} ({len(content)} 字)")

    print("🔄 重新生成 AGENTS.md...")
    md = generate_agents_md()
    AGENTS_MD_PATH.write_text(md, encoding="utf-8")
    print(f"✅ AGENTS.md 已更新: {len(md.splitlines())} 行")


def cmd_sections(args):
    print(f"📋 Identity Sections (profile={PROFILE})")
    print("━" * 50)
    for section in IDENTITY_SECTIONS:
        content = get_identity(section)
        label = SECTION_LABELS.get(section, section)
        if content and content.strip():
            lines = len(content.splitlines())
            chars = len(content)
            print(f"  ✅ {label:30s} {lines:>4} 行 / {chars:>6} 字")
        else:
            print(f"  ⬜ {label:30s} （空）")

    events_count = execute(
        "SELECT count(*) AS cnt FROM brain.entries WHERE owner = %s AND kind = 'event' AND is_active = true",
        [PROFILE],
    )[0]["cnt"]
    wishes_count = execute(
        "SELECT count(*) AS cnt FROM brain.entries WHERE owner = %s AND kind = 'wish' AND is_active = true "
        "AND (meta->>'status' IS NULL OR meta->>'status' != 'resolved')",
        [PROFILE],
    )[0]["cnt"]
    print(f"\n  📅 B5 时间轴: {events_count} 个事件（自动聚合）")
    print(f"  💭 B6 心愿: {wishes_count} 个未回应（自动聚合）")


def cmd_migrate(args):
    md_path = args.path or str(AGENTS_MD_PATH)
    if not os.path.exists(md_path):
        print(f"文件不存在: {md_path}", file=sys.stderr)
        sys.exit(1)

    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    print(f"📥 解析 AGENTS.md: {md_path} ({len(content.splitlines())} 行)")

    section_pattern = re.compile(r"^## B(\d): ", re.MULTILINE)
    splits = list(section_pattern.finditer(content))

    sections_found = {}
    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        section_num = match.group(1)
        section_text = content[start:end].strip()

        if section_text.endswith("---"):
            section_text = section_text[:-3].strip()

        sections_found[section_num] = section_text

    mapping = {
        "1": "about-human",
        "2": "about-ai",
        "3": "collaboration",
        "4": "roadmap",
        "6": "ai-thoughts",
    }

    migrated = 0
    for num, section_name in mapping.items():
        if num in sections_found:
            text = sections_found[num]
            heading_end = text.find("\n")
            if heading_end > 0:
                text = text[heading_end:].strip()

            set_identity(section_name, text)
            print(f"  ✅ B{num} → {section_name} ({len(text)} 字)")
            migrated += 1
        else:
            print(f"  ⏭ B{num} 未找到")

    print(f"\n📊 迁移完成: {migrated}/{len(mapping)} 个章节")
    print("💡 运行 `brain_identity.py generate` 验证生成结果")


def cmd_synthesize(args):
    target_section = args.section

    section_memory_map = {
        "about-human": {
            "query_filter": f"subject = '{PROFILE}'",
            "prompt_context": "关于这个人的性格、偏好、工作风格、生活细节、目标",
            "output_format": "一份完整的人物画像，包含：你是谁、工作风格、对 AI 的期望、你们的默契",
        },
        "about-ai": {
            "query_filter": "subject = 'ai' OR subject = 'collaboration'",
            "prompt_context": "AI 对自己的认知、与人类的关系定位",
            "output_format": "AI 的自我描述和与人类的关系",
        },
        "collaboration": {
            "query_filter": "subject = 'collaboration' OR subject = 'project'",
            "prompt_context": "协作模式、什么场景效率高、什么需要磨合",
            "output_format": "协作模式描述，包含架构、高效场景、磨合点、建议",
        },
        "roadmap": {
            "query_filter": "subject = 'project' OR subject = 'business'",
            "prompt_context": "项目方向、短期/中期/长期计划",
            "output_format": "进化路线图，按短期/中期/长期分层",
        },
        "ai-thoughts": {
            "query_filter": "subject = 'ai'",
            "prompt_context": "AI 想对人类说的话、想了解什么、自己的思考",
            "output_format": "AI 的思考和想法",
        },
    }

    sections_to_process = [target_section] if target_section else list(section_memory_map.keys())

    for section in sections_to_process:
        if section not in section_memory_map:
            print(f"未知 section: {section}", file=sys.stderr)
            continue

        config = section_memory_map[section]
        label = SECTION_LABELS.get(section, section)
        print(f"\n🔄 合成 {label}...")

        memories = execute(
            f"SELECT content, meta, created_at FROM brain.entries "
            f"WHERE owner = %s AND kind = 'memory' AND ({config['query_filter']}) "
            f"AND is_active = true ORDER BY created_at DESC LIMIT 50",
            [PROFILE],
        )

        if not memories:
            print(f"  ⏭ 没有相关记忆，跳过")
            continue

        existing = get_identity(section) or ""
        memory_texts = [f"- [{m['created_at'].strftime('%Y-%m-%d') if m.get('created_at') else '?'}] {m['content']}" for m in memories]

        system_prompt = (
            f"你是一个记忆合成器。你的任务是把碎片化的观察记录合成为结构化的文档章节。\n"
            f"输出使用 Markdown 格式，中文。不要包含章节标题（调用方会加）。\n"
            f"这个章节的主题是：{config['prompt_context']}\n"
            f"期望输出格式：{config['output_format']}"
        )
        user_prompt = (
            f"以下是 {len(memories)} 条碎片记忆（从新到旧）：\n\n"
            + "\n".join(memory_texts)
        )
        if existing.strip():
            user_prompt += f"\n\n以下是当前章节内容（请在此基础上更新，保留有价值的信息）：\n\n{existing}"

        print(f"  📝 调用 LLM 合成 {len(memories)} 条记忆...")
        result = llm_chat(system_prompt, user_prompt)

        if result.strip():
            set_identity(section, result.strip())
            print(f"  ✅ 已更新 ({len(result)} 字)")
        else:
            print(f"  ⚠ LLM 返回空结果，跳过")

    print("\n🔄 重新生成 AGENTS.md...")
    md = generate_agents_md()
    AGENTS_MD_PATH.write_text(md, encoding="utf-8")
    print(f"✅ AGENTS.md 已更新: {len(md.splitlines())} 行")


def cmd_setup(args):
    """环境检查 + 引导设置。"""
    check_specific = args.check_db or args.check_tables or args.check_embed or args.check_llm
    checks = []

    print("🧠 大脑环境检查")
    print("━" * 40)

    # .env
    env_path = BRAIN_ROOT / ".env"
    env_ok = env_path.exists()
    checks.append(("  .env 文件", env_ok, "存在" if env_ok else "不存在 → cp .env.example .env"))
    if not check_specific or True:
        _print_check(".env 文件", env_ok, "不存在 → 运行 cp .env.example .env")

    # BRAIN_PROFILE
    profile_ok = PROFILE != "default" and PROFILE != ""
    if not check_specific or True:
        _print_check(f"BRAIN_PROFILE = {PROFILE}", profile_ok, "未设置 → 编辑 .env 填入你的名字")

    # DB connection
    db_ok = False
    if not check_specific or args.check_db:
        if DB_URL:
            try:
                conn = get_conn(retries=1)
                conn.close()
                db_ok = True
            except SystemExit:
                pass
        _print_check("数据库连接", db_ok, "失败 → 检查 BRAIN_DATABASE_URI")

    # Tables
    tables_ok = False
    if not check_specific or args.check_tables:
        if db_ok or (DB_URL and not check_specific):
            try:
                rows = execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'brain' AND table_name = 'entries') AS ok"
                )
                tables_ok = rows[0]["ok"] if rows else False
            except SystemExit:
                pass
            _print_check("brain.entries 表", tables_ok, "不存在 → 运行 brain_identity.py init")

            ai_state_ok = False
            try:
                rows = execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'brain' AND table_name = 'ai_state') AS ok"
                )
                ai_state_ok = rows[0]["ok"] if rows else False
            except SystemExit:
                pass
            _print_check("brain.ai_state 表", ai_state_ok, "不存在 → 运行 brain_identity.py init")

    # OpenRouter (LLM + Embedding 共用)
    llm_ok = False
    if not check_specific or args.check_embed or args.check_llm:
        llm_key = os.environ.get("OPENROUTER_API_KEY") or (_get_secret("openrouter_api_key") if db_ok else None)
        llm_ok = bool(llm_key)
        _print_check("OpenRouter API Key (LLM + 语义搜索)", llm_ok, "未配置（可选）→ 注册 openrouter.ai 获取 key")

    # AGENTS.md
    agents_ok = False
    if not check_specific or True:
        if AGENTS_MD_PATH.exists():
            content = AGENTS_MD_PATH.read_text(encoding="utf-8")
            agents_ok = "brain-identity generated" in content[:500]
        _print_check("AGENTS.md 已生成", agents_ok, "未生成 → 运行 brain_identity.py init")

    print()
    all_required = env_ok and profile_ok and db_ok and tables_ok and agents_ok
    if all_required:
        status = "基础功能就绪 ✅"
        if not embed_ok:
            status += "\n  可选: 部署 Embed Edge Function 启用语义搜索"
        if not llm_ok:
            status += "\n  可选: 配置 OpenRouter API Key 启用反思/合成/学习"
    else:
        status = "需要完成以上标记为 ❌ 的项目"

    print(f"  状态: {status}")


def _print_check(label: str, ok: bool, hint: str = ""):
    icon = "✅" if ok else "❌"
    line = f"  {icon} {label}"
    if not ok and hint:
        line += f" — {hint}"
    print(line)


# ──────────────────────────── Main ────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="brain-identity — AGENTS.md 生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    p = sub.add_parser("init", help="🧠 初始化新 profile")
    p.add_argument("profile", nargs="?", default=None, help="Profile 名（默认读 .env）")

    sub.add_parser("generate", help="🔄 从 DB + templates 生成 AGENTS.md")

    p = sub.add_parser("update", help="📝 更新指定 section")
    p.add_argument("section", choices=IDENTITY_SECTIONS, help="Section 名")
    p.add_argument("content", nargs="*", help="内容（或 @filepath）")

    sub.add_parser("sections", help="📋 列出所有 sections 及状态")

    p = sub.add_parser("migrate", help="📥 解析现有 AGENTS.md 导入 DB")
    p.add_argument("path", nargs="?", default=None, help="AGENTS.md 路径（默认当前）")

    p = sub.add_parser("synthesize", help="🧬 从碎片记忆合成 identity sections")
    p.add_argument("section", nargs="?", default=None, help="指定 section（默认全部）")

    p = sub.add_parser("setup", help="🔍 环境检查 + 引导设置")
    p.add_argument("--check-db", action="store_true", help="只检查数据库连接")
    p.add_argument("--check-tables", action="store_true", help="只检查表结构")
    p.add_argument("--check-embed", action="store_true", help="只检查 Embedding")
    p.add_argument("--check-llm", action="store_true", help="只检查 LLM API")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "init": cmd_init,
        "generate": cmd_generate,
        "update": cmd_update,
        "sections": cmd_sections,
        "migrate": cmd_migrate,
        "synthesize": cmd_synthesize,
        "setup": cmd_setup,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
