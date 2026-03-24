#!/usr/bin/env python3
"""
共享大脑 v3 — 统一 entries 表 + 外部知识获取 + 知识消化 + 定时任务

核心命令:
  add/find/update/link/observe/forget/stats/timeline/wishes/embed/embed-all/dump
外部知识:
  learn/search
知识消化:
  reflect/auto-link/decay/digest
密钥管理:
  secret
定时任务:
  cron
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone
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
if not DB_URL:
    print("错误: 需要配置 BRAIN_DATABASE_URI（在 .env 或环境变量中）", file=sys.stderr)
    sys.exit(1)

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")
LLM_MODEL_OMNI = os.environ.get("LLM_MODEL_OMNI", "xiaomi/mimo-v2-omni")

ALLOWED_KIND = ["memory", "event", "pattern", "wish", "convo", "knowledge", "insight", "bookmark", "emotion", "personality", "identity"]
ALLOWED_SUBJECT = ["ai", "collaboration", "project", "business", "system", "external"]
if PROFILE not in ALLOWED_SUBJECT:
    ALLOWED_SUBJECT.append(PROFILE)


# ──────────────────────────── DB helpers ────────────────────────────

def get_conn(retries: int = 3):
    import time
    for attempt in range(retries):
        try:
            return psycopg2.connect(DB_URL, connect_timeout=15)
        except psycopg2.OperationalError as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"⚠ 数据库连接失败(重试 {attempt+1}/{retries}, {wait}s后)...", file=sys.stderr)
                time.sleep(wait)
            else:
                print("数据库连接失败:", e, file=sys.stderr)
                sys.exit(1)


def execute(sql: str, params: Optional[List[Any]] = None, fetch: bool = True):
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


# ──────────────────────────── Schema Migration ────────────────────────────

_schema_checked = False

def _ensure_schema():
    global _schema_checked
    if _schema_checked:
        return
    _schema_checked = True
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'brain' AND table_name = 'entries' AND column_name = 'owner'
                    ) THEN
                        ALTER TABLE brain.entries ADD COLUMN owner text NOT NULL DEFAULT 'default';
                        CREATE INDEX idx_entries_owner ON brain.entries(owner);
                    END IF;
                END $$;
            """)
            cur.execute("UPDATE brain.ai_state SET id = %s WHERE id = 'default' AND NOT EXISTS (SELECT 1 FROM brain.ai_state WHERE id = %s)", [PROFILE, PROFILE])
        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        print(f"⚠ Schema 迁移: {e}", file=sys.stderr)
    finally:
        conn.close()


# ──────────────────────────── Secrets ────────────────────────────

def get_secret(key: str) -> Optional[str]:
    rows = execute("SELECT value FROM brain.secrets WHERE key = %s", [key])
    return rows[0]["value"] if rows else None


def _get_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or get_secret("openrouter_api_key")
    if not key:
        print("错误: 需要 openrouter_api_key（环境变量或 brain.secrets）", file=sys.stderr)
        sys.exit(1)
    return key


_openrouter_key_cache: Optional[bool] = None

def _can_embed() -> bool:
    """检查 OpenRouter key 是否可用（不退出，缓存结果）"""
    global _openrouter_key_cache
    if _openrouter_key_cache is not None:
        return _openrouter_key_cache
    _openrouter_key_cache = bool(
        os.environ.get("OPENROUTER_API_KEY") or get_secret("openrouter_api_key")
    )
    return _openrouter_key_cache


def _get_firecrawl_key() -> str:
    key = os.environ.get("FIRECRAWL_API_KEY") or get_secret("firecrawl_api_key")
    if not key:
        print("错误: 需要 firecrawl_api_key（环境变量或 brain.secrets）", file=sys.stderr)
        sys.exit(1)
    return key


# ──────────────────────────── Parsing helpers ────────────────────────────

def parse_json_dict(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print("meta 必须是 JSON 对象", file=sys.stderr)
        sys.exit(1)
    return data


def parse_list(text: str) -> List[str]:
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_meta_filters(items: List[str]) -> List[tuple]:
    parsed = []
    for item in items or []:
        if "=" not in item:
            print(f"--meta 过滤格式错误: {item}，应为 key=value", file=sys.stderr)
            sys.exit(1)
        key, value = item.split("=", 1)
        parsed.append((key.strip(), value.strip()))
    return parsed


def pretty_meta(meta: Dict[str, Any]) -> str:
    if not meta:
        return "{}"
    return json.dumps(meta, ensure_ascii=False)


def normalize_pg_array(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None]
    if isinstance(val, str):
        text = val.strip()
        if text.startswith("{") and text.endswith("}"):
            body = text[1:-1].strip()
            if not body:
                return []
            return [x.strip().strip('"') for x in body.split(",") if x.strip()]
    return [str(val)]


# ──────────────────────────── Embedding ────────────────────────────

EMBED_MODEL = "openai/text-embedding-3-small"

def call_embed_api(texts: List[str]) -> List[List[float]]:
    api_key = _get_openrouter_key()
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_BASE_URL + "/embeddings",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Embedding API 错误 ({e.code}): {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Embedding API 网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", [])
    data.sort(key=lambda x: x["index"])
    return [item["embedding"] for item in data]


def vector_to_pg_literal(vec: List[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def _build_embed_text(row: Dict[str, Any]) -> str:
    parts = [row["content"]]
    meta = row.get("meta") or {}
    if meta.get("title"):
        parts.insert(0, meta["title"])
    if meta.get("aspect"):
        parts.append(f"[{meta['aspect']}]")
    if meta.get("pattern_type"):
        parts.append(f"[{meta['pattern_type']}]")
    if meta.get("category"):
        parts.append(f"[{meta['category']}]")
    return " ".join(parts)


# ──────────────────────────── LLM (纯 urllib，零外部依赖) ────────────────────────────

def _llm_request(payload: dict) -> str:
    """统一的 OpenRouter 请求，只用 stdlib。"""
    api_key = _get_openrouter_key()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_BASE_URL + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-OpenRouter-Title": "shared-brain",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"LLM API 错误 ({e.code}): {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"LLM API 网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)
    return result.get("choices", [{}])[0].get("message", {}).get("content", "")


def llm_chat(system_prompt: str, user_prompt: str, max_tokens: int = 2000, model: str = None) -> str:
    return _llm_request({
        "model": model or LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    })


def llm_vision(prompt: str, image_url: str = None, image_base64: str = None,
               video_url: str = None, audio_base64: str = None,
               max_tokens: int = 2000) -> str:
    """多模态 LLM：支持图片 URL/base64、视频 URL、音频 base64。零外部依赖。"""
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    if image_base64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
    if video_url:
        content.append({"type": "video_url", "video_url": {"url": video_url}})
    if audio_base64:
        content.append({"type": "input_audio", "input_audio": {"data": audio_base64, "format": "wav"}})

    return _llm_request({
        "model": LLM_MODEL_OMNI,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "provider": {"order": ["xiaomi"]},
    })


# ──────────────────────────── Firecrawl ────────────────────────────

def _firecrawl_bin() -> str:
    path = shutil.which("firecrawl")
    if not path:
        print("错误: firecrawl CLI 未安装（npm install -g firecrawl-cli）", file=sys.stderr)
        sys.exit(1)
    return path


def firecrawl_scrape(url: str, query: str = None) -> str:
    cmd = [_firecrawl_bin(), "scrape", url, "--only-main-content"]
    if query:
        cmd.extend(["--query", query])
    env = os.environ.copy()
    env["FIRECRAWL_API_KEY"] = _get_firecrawl_key()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        return result.stdout[:15000]
    except subprocess.TimeoutExpired:
        return "[超时] 抓取耗时过长"
    except Exception as e:
        return f"[错误] {e}"


def firecrawl_search(query: str, limit: int = 5) -> str:
    cmd = [_firecrawl_bin(), "search", query, "--limit", str(limit)]
    env = os.environ.copy()
    env["FIRECRAWL_API_KEY"] = _get_firecrawl_key()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        return result.stdout[:15000]
    except subprocess.TimeoutExpired:
        return "[超时] 搜索耗时过长"
    except Exception as e:
        return f"[错误] {e}"


# ──────────────────────────── L0/L1 Context Layers ────────────────────────────

def _generate_layers(content: str, kind: str, meta: dict) -> Optional[Dict[str, str]]:
    """Generate L0 (one-line abstract) and L1 (key points) for a memory entry."""
    title = meta.get("title", "")
    text = f"[{kind}] {title}: {content}" if title else f"[{kind}] {content}"

    system = (
        "You generate concise Chinese summaries for a memory entry.\n"
        "L0: one sentence, max 30 Chinese characters, the core gist.\n"
        "L1: 2-3 bullet points, max 200 Chinese characters total, key information.\n"
        "Output ONLY valid JSON: {\"l0\": \"...\", \"l1\": \"...\"}\n"
        "No markdown, no extra text."
    )
    result = llm_chat(system, text, max_tokens=300)
    if not result:
        return None
    start = result.find("{")
    end = result.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        parsed = json.loads(result[start:end])
        if "l0" in parsed and "l1" in parsed:
            return {"l0": str(parsed["l0"])[:100], "l1": str(parsed["l1"])[:500]}
    except json.JSONDecodeError:
        pass
    return None


# ════════════════════════════ COMMANDS ════════════════════════════

# ─── Core CRUD ───

def cmd_add(args):
    if args.kind not in ALLOWED_KIND:
        print(f"kind 必须是: {', '.join(ALLOWED_KIND)}", file=sys.stderr)
        sys.exit(1)
    if args.subject not in ALLOWED_SUBJECT:
        print(f"subject 必须是: {', '.join(ALLOWED_SUBJECT)}", file=sys.stderr)
        sys.exit(1)

    meta = parse_json_dict(args.meta)
    tags = parse_list(args.tags)
    related = parse_list(args.related)

    embedding_sql = "NULL"
    embedding_param: List[Any] = []
    if _can_embed() and not args.no_embed:
        try:
            text = args.content
            if meta.get("title"):
                text = meta["title"] + " " + text
            vecs = call_embed_api([text])
            embedding_sql = "%s::vector"
            embedding_param = [vector_to_pg_literal(vecs[0])]
        except SystemExit:
            print("⚠ embedding 生成失败，跳过向量写入", file=sys.stderr)

    rows = execute(
        f"""
        INSERT INTO brain.entries
        (owner, kind, subject, content, meta, tags, confidence, source, related, event_date, embedding)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, {embedding_sql})
        RETURNING id, kind, subject
        """,
        [
            PROFILE,
            args.kind,
            args.subject,
            args.content,
            json.dumps(meta, ensure_ascii=False),
            tags,
            args.confidence,
            args.source,
            related,
            args.event_date,
        ] + embedding_param,
    )
    r = rows[0]
    embed_hint = " +🧬" if embedding_param else ""

    layer_hint = ""
    if not getattr(args, 'no_layers', False) and _can_embed():
        try:
            layers = _generate_layers(args.content, args.kind, meta)
            if layers:
                execute(
                    "UPDATE brain.entries SET l0_abstract = %s, l1_overview = %s WHERE id = %s",
                    [layers["l0"], layers["l1"], r["id"]],
                    fetch=False,
                )
                layer_hint = " +L0/L1"
        except Exception as e:
            print(f"⚠ L0/L1 生成失败: {e}", file=sys.stderr)

    print(f"✓ 已写入: [{r['kind']}/{r['subject']}] {r['id']}{embed_hint}{layer_hint}")


def cmd_find(args):
    conditions = ["owner = %s", "is_active = true"]
    params: List[Any] = [PROFILE]

    if args.kind:
        conditions.append("kind = %s")
        params.append(args.kind)
    if args.subject:
        conditions.append("subject = %s")
        params.append(args.subject)
    if args.tag:
        conditions.append("%s = ANY(tags)")
        params.append(args.tag)

    use_semantic = args.semantic and args.query and _can_embed()

    if args.query and not use_semantic:
        if args.fuzzy:
            conditions.append("content % %s")
            params.append(args.query)
        else:
            conditions.append("content ILIKE %s")
            params.append(f"%{args.query}%")

    for key, value in parse_meta_filters(args.meta):
        conditions.append("meta ->> %s = %s")
        params.extend([key, value])

    order_sql = "updated_at DESC"
    if use_semantic:
        vecs = call_embed_api([args.query])
        query_vec_literal = vector_to_pg_literal(vecs[0])
        order_sql = "embedding <=> %s::vector ASC"
        params.append(query_vec_literal)
        conditions.append("embedding IS NOT NULL")
    elif args.semantic and args.query and not _can_embed():
        print("⚠ --semantic 需要 openrouter_api_key；改为普通文本检索。", file=sys.stderr)
    elif args.query_vector:
        order_sql = "embedding <=> %s::vector ASC"
        params.append(args.query_vector)
        conditions.append("embedding IS NOT NULL")

    where_sql = " AND ".join(conditions)
    rows = execute(
        f"""
        SELECT id, kind, subject, content, l0_abstract, l1_overview, meta, tags, confidence, source, event_date, updated_at
        FROM brain.entries
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
        """,
        params + [args.limit],
    )

    if not rows:
        print("没有匹配结果")
        return

    detail = getattr(args, 'detail', False)
    for r in rows:
        tags = f" #{' #'.join(r['tags'])}" if r["tags"] else ""
        conf = f" [{r['confidence']:.0%}]" if r["confidence"] < 1 else ""
        d = f" @{r['event_date']}" if r["event_date"] else ""
        l0 = r.get("l0_abstract")
        print(f"\n[{r['kind']}/{r['subject']}]{conf}{d}{tags}")
        if detail or not l0:
            print(f"  {r['content'][:220]}{'...' if len(r['content']) > 220 else ''}")
        else:
            print(f"  L0: {l0}")
            if r.get("l1_overview"):
                print(f"  L1: {r['l1_overview'][:200]}")
        print(f"  meta: {pretty_meta(r['meta'])}")
        print(f"  id: {r['id']}")

    print(f"\n共 {len(rows)} 条")


def cmd_update(args):
    existing = execute("SELECT id FROM brain.entries WHERE id = %s AND owner = %s AND is_active = true", [args.id, PROFILE])
    if not existing:
        print(f"未找到活跃条目: {args.id}", file=sys.stderr)
        return

    sets = ["updated_at = now()"]
    params: List[Any] = []

    if args.content:
        sets.append("content = %s")
        params.append(args.content)
    if args.confidence is not None:
        sets.append("confidence = %s")
        params.append(args.confidence)
    if args.source:
        sets.append("source = %s")
        params.append(args.source)
    if args.add_tags:
        new_tags = parse_list(args.add_tags)
        if new_tags:
            sets.append("tags = array_cat(tags, %s)")
            params.append(new_tags)
    if args.meta:
        sets.append("meta = meta || %s::jsonb")
        params.append(json.dumps(parse_json_dict(args.meta), ensure_ascii=False))

    if len(sets) == 1:
        print("没有要更新的字段")
        return

    params.extend([args.id, PROFILE])
    execute(f"UPDATE brain.entries SET {', '.join(sets)} WHERE id = %s AND owner = %s", params, fetch=False)
    print(f"✓ 已更新: {args.id}")


def cmd_link(args):
    exists = execute(
        "SELECT id FROM brain.entries WHERE id = ANY(%s::uuid[]) AND owner = %s AND is_active = true",
        [[args.id1, args.id2], PROFILE],
    )
    if len(exists) != 2:
        print("link 失败：至少一个 id 不存在或已归档", file=sys.stderr)
        return

    execute(
        """
        UPDATE brain.entries
        SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
            updated_at = now()
        WHERE id = %s AND owner = %s
        """,
        [args.id2, args.id2, args.id1, PROFILE],
        fetch=False,
    )
    execute(
        """
        UPDATE brain.entries
        SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
            updated_at = now()
        WHERE id = %s AND owner = %s
        """,
        [args.id1, args.id1, args.id2, PROFILE],
        fetch=False,
    )
    print(f"✓ 已关联: {args.id1} <-> {args.id2}")


def cmd_observe(args):
    observed_date = args.observed_date or date.today().isoformat()
    existing = execute(
        """
        SELECT id, content, meta
        FROM brain.entries
        WHERE kind = 'pattern'
          AND owner = %s AND is_active = true
          AND (content ILIKE %s OR (meta ->> 'pattern_type' = %s AND content ILIKE %s))
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        [PROFILE, f"%{args.description}%", args.pattern_type, f"%{args.description[:24]}%"],
    )

    if existing:
        item = existing[0]
        freq = int((item["meta"] or {}).get("frequency", 1)) + 1
        examples = (item["meta"] or {}).get("examples", [])
        if args.example:
            examples.append(args.example)
        patch = {
            "pattern_type": args.pattern_type,
            "frequency": freq,
            "examples": examples[-20:],
            "last_observed": observed_date,
        }
        execute(
            "UPDATE brain.entries SET meta = meta || %s::jsonb, updated_at = now() WHERE id = %s",
            [json.dumps(patch, ensure_ascii=False), item["id"]],
            fetch=False,
        )
        print(f"✓ 模式频次更新: {item['content']} (x{freq})")
        return

    meta = {
        "pattern_type": args.pattern_type,
        "frequency": 1,
        "examples": [args.example] if args.example else [],
        "first_observed": observed_date,
        "last_observed": observed_date,
    }
    execute(
        """
        INSERT INTO brain.entries
        (owner, kind, subject, content, meta, tags, confidence, source, event_date)
        VALUES (%s, 'pattern', %s, %s, %s::jsonb, %s, %s, 'observed_behavior', %s)
        """,
        [PROFILE, PROFILE, args.description, json.dumps(meta, ensure_ascii=False), [args.pattern_type, "pattern"], 0.8, observed_date],
        fetch=False,
    )
    print(f"✓ 新模式已记录: [{args.pattern_type}] {args.description}")


def cmd_forget(args):
    updated = execute(
        "UPDATE brain.entries SET is_active = false, updated_at = now() WHERE id = %s AND owner = %s RETURNING id",
        [args.id, PROFILE],
    )
    if not updated:
        print(f"未找到条目: {args.id}")
        return
    print(f"✓ 已归档: {args.id}")


def cmd_wishes(args):
    conditions = ["kind = 'wish'", "owner = %s", "is_active = true"]
    params: List[Any] = [PROFILE]
    if args.status:
        conditions.append("meta ->> 'status' = %s")
        params.append(args.status)
    if args.subject:
        conditions.append("subject = %s")
        params.append(args.subject)

    rows = execute(
        f"""
        SELECT id, subject, content, meta, created_at
        FROM brain.entries
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        params + [args.limit],
    )
    if not rows:
        print("没有匹配的心愿")
        return
    for r in rows:
        status = (r["meta"] or {}).get("status", "open")
        who = (r["meta"] or {}).get("from_who", "unknown")
        print(f"\n[{status}] {who} -> {r['subject']}")
        print(f"  {r['content']}")
        if (r["meta"] or {}).get("response"):
            print(f"  回复: {(r['meta'] or {}).get('response')}")
        print(f"  id: {r['id']}")


def cmd_timeline(args):
    rows = execute(
        """
        SELECT id, subject, content, meta, event_date, created_at
        FROM brain.entries
        WHERE kind = 'event' AND owner = %s AND is_active = true
        ORDER BY COALESCE(event_date, created_at::date) DESC, created_at DESC
        LIMIT %s
        """,
        [PROFILE, args.limit],
    )
    if not rows:
        print("没有事件记录")
        return
    for r in rows:
        title = (r["meta"] or {}).get("title")
        category = (r["meta"] or {}).get("category", "event")
        sig = int((r["meta"] or {}).get("significance", 3))
        stars = "★" * sig + "☆" * (5 - sig)
        day = r["event_date"] or r["created_at"].date()
        print(f"\n{day} [{category}] {stars}")
        if title:
            print(f"  {title}")
        print(f"  {r['content'][:220]}{'...' if len(r['content']) > 220 else ''}")
        print(f"  id: {r['id']}")


def cmd_stats(_args):
    summary = execute(
        """
        SELECT
          count(*) FILTER (WHERE is_active = true) AS total_active,
          count(*) FILTER (WHERE kind = 'memory' AND is_active = true) AS memories,
          count(*) FILTER (WHERE kind = 'pattern' AND is_active = true) AS patterns,
          count(*) FILTER (WHERE kind = 'event' AND is_active = true) AS events,
          count(*) FILTER (WHERE kind = 'wish' AND is_active = true) AS wishes,
          count(*) FILTER (WHERE kind = 'convo' AND is_active = true) AS convos,
          count(*) FILTER (WHERE kind = 'knowledge' AND is_active = true) AS knowledge,
          count(*) FILTER (WHERE kind = 'insight' AND is_active = true) AS insights,
          count(*) FILTER (WHERE kind = 'bookmark' AND is_active = true) AS bookmarks,
          count(*) FILTER (WHERE kind = 'emotion' AND is_active = true) AS emotions,
          count(*) FILTER (WHERE kind = 'personality' AND is_active = true) AS personality_events,
          count(*) FILTER (WHERE kind = 'wish' AND is_active = true AND meta ->> 'status' = 'open') AS open_wishes,
          count(*) FILTER (WHERE is_active = true AND embedding IS NOT NULL) AS with_embedding
        FROM brain.entries
        WHERE owner = %s
        """,
        [PROFILE],
    )[0]
    print(f"🧠 共享大脑 [{PROFILE}]")
    print(f"  总活跃条目: {summary['total_active']}")
    print(f"  有 embedding: {summary['with_embedding']}/{summary['total_active']}")
    print(f"  ── 记忆类 ──")
    print(f"  memory: {summary['memories']}  pattern: {summary['patterns']}  convo: {summary['convos']}")
    print(f"  event: {summary['events']}  wish: {summary['wishes']} (open={summary['open_wishes']})")
    print(f"  ── 知识类 ──")
    print(f"  knowledge: {summary['knowledge']}  insight: {summary['insights']}  bookmark: {summary['bookmarks']}")
    print(f"  ── 灵魂 ──")
    print(f"  emotion: {summary['emotions']}  personality: {summary['personality_events']}")

    secrets_count = execute("SELECT count(*) AS cnt FROM brain.secrets")[0]["cnt"]
    cron_count = execute("SELECT count(*) AS cnt FROM brain.cron_tasks WHERE enabled = true")[0]["cnt"]
    print(f"  ── 系统 ──")
    print(f"  secrets: {secrets_count}  cron tasks: {cron_count}")


def cmd_embed(args):
    rows = execute(
        "SELECT id, content, meta FROM brain.entries WHERE id = %s AND owner = %s AND is_active = true",
        [args.id, PROFILE],
    )
    if not rows:
        print(f"未找到活跃条目: {args.id}", file=sys.stderr)
        return

    row = rows[0]
    text = _build_embed_text(row)
    vecs = call_embed_api([text])
    vec_literal = vector_to_pg_literal(vecs[0])

    execute(
        "UPDATE brain.entries SET embedding = %s::vector, updated_at = now() WHERE id = %s",
        [vec_literal, args.id],
        fetch=False,
    )
    print(f"✓ 已写入 embedding: {args.id} ({len(vecs[0])} 维)")


def cmd_embed_all(args):
    conditions = ["owner = %s", "is_active = true", "embedding IS NULL"]
    params: List[Any] = [PROFILE]
    if args.kind:
        conditions.append("kind = %s")
        params.append(args.kind)
    if args.subject:
        conditions.append("subject = %s")
        params.append(args.subject)

    rows = execute(
        f"""
        SELECT id, content, meta
        FROM brain.entries
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at ASC
        LIMIT %s
        """,
        params + [args.limit],
    )

    if not rows:
        print("所有条目已有 embedding，无需处理")
        return

    print(f"待处理: {len(rows)} 条")

    batch_size = args.batch_size
    success = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [_build_embed_text(r) for r in batch]
        try:
            vecs = call_embed_api(texts)
        except SystemExit:
            print(f"第 {i // batch_size + 1} 批失败，已完成 {success}/{len(rows)}", file=sys.stderr)
            return

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for row, vec in zip(batch, vecs):
                    vec_literal = vector_to_pg_literal(vec)
                    cur.execute(
                        "UPDATE brain.entries SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                        [vec_literal, row["id"]],
                    )
            conn.commit()
            success += len(batch)
            print(f"  ✓ 批次 {i // batch_size + 1}: {len(batch)} 条完成")
        except psycopg2.Error as e:
            conn.rollback()
            print(f"  ✗ 批次 {i // batch_size + 1} 写入失败: {e}", file=sys.stderr)
        finally:
            conn.close()

    print(f"\n完成: {success}/{len(rows)} 条已写入 embedding")


def cmd_backfill_layers(args):
    """Batch generate L0/L1 for entries missing them."""
    conditions = ["owner = %s", "is_active = true", "l0_abstract IS NULL"]
    params: List[Any] = [PROFILE]
    if args.kind:
        conditions.append("kind = %s")
        params.append(args.kind)

    rows = execute(
        f"""
        SELECT id, kind, content, meta
        FROM brain.entries
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        params + [args.limit],
    )

    if not rows:
        print("所有条目已有 L0/L1，无需处理")
        return

    print(f"待处理: {len(rows)} 条")
    success = 0
    for i, row in enumerate(rows):
        kind = row["kind"]
        meta = row["meta"] or {}
        try:
            layers = _generate_layers(row["content"], kind, meta)
            if layers:
                execute(
                    "UPDATE brain.entries SET l0_abstract = %s, l1_overview = %s, updated_at = now() WHERE id = %s",
                    [layers["l0"], layers["l1"], row["id"]],
                    fetch=False,
                )
                success += 1
                print(f"  ✓ [{kind}] {layers['l0'][:50]}")
            else:
                print(f"  ✗ [{kind}] LLM 返回空")
        except Exception as e:
            print(f"  ✗ [{kind}] {e}")

    print(f"\n完成: {success}/{len(rows)} 条已生成 L0/L1")


def cmd_dump(args):
    conditions = ["owner = %s", "is_active = true"]
    params: List[Any] = [PROFILE]
    if args.kind:
        conditions.append("kind = %s")
        params.append(args.kind)
    if args.subject:
        conditions.append("subject = %s")
        params.append(args.subject)

    rows = execute(
        f"""
        SELECT id, kind, subject, content, meta, tags, confidence, source, related, event_date, created_at, updated_at
        FROM brain.entries
        WHERE {' AND '.join(conditions)}
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        params + [args.limit],
    )
    normalized = []
    for r in rows:
        normalized.append(
            {
                "id": str(r["id"]),
                "kind": r["kind"],
                "subject": r["subject"],
                "content": r["content"],
                "meta": r["meta"] or {},
                "tags": r["tags"] or [],
                "confidence": float(r["confidence"]),
                "source": r["source"],
                "related": normalize_pg_array(r["related"]),
                "event_date": r["event_date"].isoformat() if r["event_date"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
        )
    print(json.dumps(normalized, ensure_ascii=False, indent=2))


# ─── Multimodal: see ───

def cmd_see(args):
    """多模态理解：分析图片/视频/音频并存入知识库"""
    print(f"👁 正在分析...")

    image_url = None
    image_b64 = None
    video_url = None
    audio_b64 = None

    if args.image:
        if args.image.startswith("http"):
            image_url = args.image
        else:
            import base64
            with open(args.image, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

    if args.video:
        video_url = args.video

    if args.audio:
        import base64
        with open(args.audio, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = args.prompt or "请详细描述这个内容，用中文回答。如果有文字，请提取出来。"

    result = llm_vision(
        prompt=prompt,
        image_url=image_url,
        image_base64=image_b64,
        video_url=video_url,
        audio_base64=audio_b64,
    )

    if not result.strip():
        print("分析结果为空")
        return

    print(f"\n{result}")

    if args.save:
        meta = {
            "title": result.split("\n")[0][:100],
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "model": LLM_MODEL_OMNI,
        }
        if image_url:
            meta["image_url"] = image_url
        if args.video:
            meta["video_url"] = args.video
        if args.image and not image_url:
            meta["image_path"] = args.image
        if args.audio:
            meta["audio_path"] = args.audio

        tags = parse_list(args.tags) if args.tags else ["多模态", "分析"]

        rows = execute(
            """
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source)
            VALUES (%s, 'knowledge', %s, %s, %s::jsonb, %s, 0.9, 'multimodal_analysis')
            RETURNING id
            """,
            [PROFILE, args.subject or "external", result, json.dumps(meta, ensure_ascii=False), tags],
        )
        print(f"\n💾 已保存: {rows[0]['id']}")


# ─── External Knowledge: learn & search ───

def cmd_learn(args):
    """抓取网页内容，LLM 提炼摘要，存入 brain.entries(kind=knowledge)"""
    print(f"🌐 正在抓取: {args.url}")
    raw = firecrawl_scrape(args.url, query=args.query)

    if raw.startswith("[错误]") or raw.startswith("[超时]"):
        print(raw)
        return

    if not raw.strip():
        print("抓取结果为空")
        return

    print(f"  抓取到 {len(raw)} 字符，正在 LLM 提炼...")

    system = (
        f"你是 {PROFILE} 的知识助手。从网页内容中提取核心知识，用中文输出。"
        "输出格式：先用一行写标题，然后空一行写 3-8 个要点的摘要。"
        "保持简洁，每个要点不超过 50 字。"
    )
    user = f"URL: {args.url}\n\n内容:\n{raw[:8000]}"
    summary = llm_chat(system, user)

    if not summary.strip():
        print("LLM 摘要为空")
        return

    title = summary.split("\n")[0].strip().lstrip("#").strip()

    meta = {
        "title": title[:100],
        "url": args.url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "raw_length": len(raw),
    }
    if args.query:
        meta["query"] = args.query
    tags = parse_list(args.tags) if args.tags else ["web", "知识"]
    subject = args.subject or "external"

    embedding_sql = "NULL"
    embedding_param: List[Any] = []
    if _can_embed():
        try:
            vecs = call_embed_api([f"{title} {summary[:500]}"])
            embedding_sql = "%s::vector"
            embedding_param = [vector_to_pg_literal(vecs[0])]
        except SystemExit:
            pass

    rows = execute(
        f"""
        INSERT INTO brain.entries
        (owner, kind, subject, content, meta, tags, confidence, source, embedding)
        VALUES (%s, 'knowledge', %s, %s, %s::jsonb, %s, 0.9, 'web_scrape', {embedding_sql})
        RETURNING id
        """,
        [PROFILE, subject, summary, json.dumps(meta, ensure_ascii=False), tags] + embedding_param,
    )
    print(f"\n📚 已学习: {title}")
    print(f"  {summary[:200]}{'...' if len(summary) > 200 else ''}")
    print(f"  id: {rows[0]['id']}")


def cmd_search(args):
    """搜索网页，LLM 筛选总结，可选存入"""
    print(f"🔍 正在搜索: {args.query}")
    raw = firecrawl_search(args.query, limit=args.limit)

    if raw.startswith("[错误]") or raw.startswith("[超时]"):
        print(raw)
        return

    if not raw.strip():
        print("搜索结果为空")
        return

    if args.raw:
        print(raw)
        return

    print(f"  找到结果，正在 LLM 总结...")

    system = (
        f"你是 {PROFILE} 的研究助手。从搜索结果中提取最有价值的信息，用中文输出。"
        "格式：每条结果一行，包含 [标题] + 核心观点（30字内）+ URL。"
        "最后用 2-3 句话总结整体发现。"
    )
    user = f"搜索词: {args.query}\n\n搜索结果:\n{raw[:8000]}"
    summary = llm_chat(system, user)

    print(f"\n{summary}")

    if args.save:
        meta = {
            "title": f"搜索: {args.query}",
            "query": args.query,
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }
        tags = parse_list(args.tags) if args.tags else ["搜索", "研究"]

        embedding_sql = "NULL"
        embedding_param: List[Any] = []
        if _can_embed():
            try:
                vecs = call_embed_api([f"{args.query} {summary[:500]}"])
                embedding_sql = "%s::vector"
                embedding_param = [vector_to_pg_literal(vecs[0])]
            except SystemExit:
                pass

        rows = execute(
            f"""
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source, embedding)
            VALUES (%s, 'knowledge', 'external', %s, %s::jsonb, %s, 0.85, 'web_search', {embedding_sql})
            RETURNING id
            """,
            [PROFILE, summary, json.dumps(meta, ensure_ascii=False), tags] + embedding_param,
        )
        print(f"\n💾 已保存: {rows[0]['id']}")


# ─── Knowledge Digestion: reflect, auto-link, decay, digest ───

def cmd_reflect(args):
    """聚合最近记忆，LLM 生成洞察"""
    kind_filter = ""
    params: List[Any] = []
    if args.kind:
        kind_filter = "AND kind = %s"
        params.append(args.kind)

    rows = execute(
        f"""
        SELECT kind, subject, content, meta, tags, created_at
        FROM brain.entries
        WHERE owner = %s AND is_active = true {kind_filter}
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        [PROFILE] + params + [args.limit],
    )

    if not rows:
        print("没有可供反思的记忆")
        return

    memories_text = "\n".join(
        f"[{r['kind']}/{r['subject']}] {r['content'][:200]}"
        for r in rows
    )

    focus = args.focus or f"{PROFILE} 最近在关注什么？有什么情绪变化？有什么值得注意的行为模式？"

    system = (
        f"你是 {PROFILE} 的 AI 搭档，负责分析他的记忆库并生成洞察。"
        "基于这些记忆片段，写出 3-5 条深度洞察。"
        "每条洞察应该是：发现 + 含义 + 建议（如果有的话）。"
        "语气亲切，像搭档一样说话。"
        "输出格式：先写一行总结标题，然后每条洞察一个段落。"
    )
    user = f"反思焦点: {focus}\n\n最近 {len(rows)} 条记忆:\n{memories_text}"

    print(f"🤔 正在反思 {len(rows)} 条记忆...")
    insight = llm_chat(system, user, max_tokens=3000)

    if not insight.strip():
        print("反思生成为空")
        return

    title = insight.split("\n")[0].strip().lstrip("#").strip()
    print(f"\n💡 {insight}")

    if not args.no_save:
        meta = {
            "title": title[:100],
            "focus": focus,
            "memory_count": len(rows),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        tags = ["反思", "洞察"]
        if args.kind:
            tags.append(args.kind)

        embedding_sql = "NULL"
        embedding_param: List[Any] = []
        if _can_embed():
            try:
                vecs = call_embed_api([f"{title} {insight[:500]}"])
                embedding_sql = "%s::vector"
                embedding_param = [vector_to_pg_literal(vecs[0])]
            except SystemExit:
                pass

        rows = execute(
            f"""
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source, embedding)
            VALUES (%s, 'insight', 'ai', %s, %s::jsonb, %s, 0.75, 'reflection', {embedding_sql})
            RETURNING id
            """,
            [PROFILE, insight, json.dumps(meta, ensure_ascii=False), tags] + embedding_param,
        )
        print(f"\n💾 洞察已保存: {rows[0]['id']}")


def cmd_auto_link(args):
    """LLM 分析记忆关联，自动创建 link"""
    rows = execute(
        """
        SELECT id, kind, subject, content, meta, tags
        FROM brain.entries
        WHERE owner = %s AND is_active = true AND kind IN ('memory', 'knowledge', 'pattern', 'event')
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        [PROFILE, args.limit],
    )

    if len(rows) < 2:
        print("记忆不足，至少需要 2 条")
        return

    entries_text = "\n".join(
        f"ID:{r['id']} [{r['kind']}] {r['content'][:150]}"
        for r in rows
    )

    system = (
        "你是记忆关联分析器。分析这些记忆条目，找出有强关联性的配对。"
        "关联性标准：因果关系、同主题、时间相关、互补信息、矛盾需要整合。"
        "输出 JSON 数组，每个元素: {\"id1\": \"...\", \"id2\": \"...\", \"reason\": \"关联原因\"}"
        "只输出高置信度的关联（至少 3 个，最多 10 个）。不要输出已经显然相同的条目。"
        "只输出 JSON，不要其他文字。"
    )

    print(f"🔗 正在分析 {len(rows)} 条记忆的关联...")
    result = llm_chat(system, entries_text, max_tokens=2000)

    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        if start == -1 or end == 0:
            print("LLM 未返回有效的关联数据")
            return
        links = json.loads(result[start:end])
    except json.JSONDecodeError:
        print(f"JSON 解析失败: {result[:200]}")
        return

    valid_ids = {str(r["id"]) for r in rows}
    linked = 0
    for link in links:
        id1 = str(link.get("id1", ""))
        id2 = str(link.get("id2", ""))
        reason = link.get("reason", "AI auto-link")

        if id1 not in valid_ids or id2 not in valid_ids or id1 == id2:
            continue

        already = execute(
            "SELECT id FROM brain.entries WHERE id = %s AND owner = %s AND %s::uuid = ANY(related)",
            [id1, PROFILE, id2],
        )
        if already:
            continue

        execute(
            """
            UPDATE brain.entries
            SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
                updated_at = now()
            WHERE id = %s AND owner = %s
            """,
            [id2, id2, id1, PROFILE],
            fetch=False,
        )
        execute(
            """
            UPDATE brain.entries
            SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
                updated_at = now()
            WHERE id = %s AND owner = %s
            """,
            [id1, id1, id2, PROFILE],
            fetch=False,
        )
        linked += 1
        print(f"  ✓ {id1[:8]}… ↔ {id2[:8]}… — {reason}")

    print(f"\n共建立 {linked} 条新关联")


def cmd_decay(args):
    """低 confidence + 久未更新的记忆降权或归档"""
    cutoff_days = args.days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()

    candidates = execute(
        """
        SELECT id, kind, content, confidence, updated_at
        FROM brain.entries
        WHERE owner = %s AND is_active = true
          AND confidence < %s
          AND updated_at < %s
          AND kind NOT IN ('event', 'wish')
        ORDER BY confidence ASC, updated_at ASC
        LIMIT %s
        """,
        [PROFILE, args.threshold, cutoff, args.limit],
    )

    if not candidates:
        print(f"没有符合衰减条件的记忆（confidence < {args.threshold}, {cutoff_days} 天未更新）")
        return

    print(f"发现 {len(candidates)} 条候选衰减记忆:")
    archived = 0
    decayed = 0

    for c in candidates:
        age_days = (datetime.now(timezone.utc) - c["updated_at"].replace(tzinfo=timezone.utc)).days
        conf = float(c["confidence"])

        if conf < 0.3 or age_days > 180:
            if not args.dry_run:
                execute(
                    "UPDATE brain.entries SET is_active = false, updated_at = now() WHERE id = %s AND owner = %s",
                    [c["id"], PROFILE], fetch=False,
                )
            archived += 1
            print(f"  🗑 归档: [{c['kind']}] {c['content'][:80]}… (conf={conf:.0%}, {age_days}天)")
        else:
            new_conf = max(0.1, conf - 0.1)
            if not args.dry_run:
                execute(
                    "UPDATE brain.entries SET confidence = %s, updated_at = now() WHERE id = %s AND owner = %s",
                    [new_conf, c["id"], PROFILE], fetch=False,
                )
            decayed += 1
            print(f"  📉 降权: [{c['kind']}] {c['content'][:80]}… ({conf:.0%}→{new_conf:.0%}, {age_days}天)")

    dry = " [DRY RUN]" if args.dry_run else ""
    print(f"\n完成{dry}: 归档 {archived}, 降权 {decayed}")


def cmd_digest(args):
    """生成日报/周报摘要"""
    period = args.period
    if period == "day":
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        period_label = "日报"
    elif period == "week":
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        period_label = "周报"
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        period_label = "月报"

    rows = execute(
        """
        SELECT kind, subject, content, meta, tags, created_at
        FROM brain.entries
        WHERE owner = %s AND is_active = true AND created_at > %s
        ORDER BY created_at DESC
        LIMIT 100
        """,
        [PROFILE, since],
    )

    if not rows:
        print(f"这段时间没有新记忆，无法生成{period_label}")
        return

    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    entries_text = ""
    for kind, items in by_kind.items():
        entries_text += f"\n## {kind} ({len(items)}条)\n"
        for item in items[:20]:
            entries_text += f"- {item['content'][:150]}\n"

    system = (
        f"你是 {PROFILE} 的 AI 搭档，生成一份{period_label}。"
        "格式：\n"
        "1. 一句话总结这段时间的主题\n"
        "2. 关键事件和里程碑（如果有）\n"
        "3. 情绪和状态观察（如果有线索）\n"
        "4. 知识获取概览（如果有 knowledge 类型）\n"
        "5. 建议和提醒\n"
        "语气亲切，简洁有力，像搭档的私人备忘录。"
    )
    user = f"时间范围: 最近{'1天' if period == 'day' else '7天' if period == 'week' else '30天'}\n"
    user += f"共 {len(rows)} 条记忆\n\n{entries_text}"

    print(f"📝 正在生成{period_label}...")
    digest = llm_chat(system, user, max_tokens=3000)

    print(f"\n{digest}")

    if not args.no_save:
        meta = {
            "title": f"{period_label} {date.today().isoformat()}",
            "period": period,
            "entry_count": len(rows),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        rows = execute(
            """
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source)
            VALUES (%s, 'insight', 'ai', %s, %s::jsonb, %s, 0.8, 'digest')
            RETURNING id
            """,
            [PROFILE, digest, json.dumps(meta, ensure_ascii=False), [period_label, "总结"]],
        )
        print(f"\n💾 {period_label}已保存: {rows[0]['id']}")


# ─── Secrets Management ───

def cmd_secret(args):
    action = args.action

    if action == "set":
        execute(
            """
            INSERT INTO brain.secrets (key, value, description, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, description = COALESCE(EXCLUDED.description, brain.secrets.description), updated_at = now()
            """,
            [args.key, args.value, args.description],
            fetch=False,
        )
        print(f"✓ 已设置: {args.key}")

    elif action == "get":
        rows = execute("SELECT key, value, description FROM brain.secrets WHERE key = %s", [args.key])
        if not rows:
            print(f"未找到: {args.key}")
            return
        r = rows[0]
        masked = r["value"][:4] + "***" + r["value"][-4:] if len(r["value"]) > 10 else "***"
        print(f"{r['key']}: {masked}")
        if r["description"]:
            print(f"  {r['description']}")

    elif action == "list":
        rows = execute("SELECT key, description, updated_at FROM brain.secrets ORDER BY key")
        if not rows:
            print("没有存储的密钥")
            return
        for r in rows:
            desc = f" — {r['description']}" if r["description"] else ""
            print(f"  {r['key']}{desc}")

    elif action == "delete":
        execute("DELETE FROM brain.secrets WHERE key = %s", [args.key], fetch=False)
        print(f"✓ 已删除: {args.key}")


# ─── Cron Management ───

def cmd_cron(args):
    action = args.action

    if action == "add":
        execute(
            """
            INSERT INTO brain.cron_tasks (name, command, schedule, enabled)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (name) DO UPDATE SET command = EXCLUDED.command, schedule = EXCLUDED.schedule, updated_at = now()
            """,
            [args.name, args.command_str, args.schedule],
            fetch=False,
        )
        print(f"✓ 已添加定时任务: {args.name} [{args.schedule}]")
        print(f"  命令: {args.command_str}")

    elif action == "list":
        rows = execute(
            "SELECT name, command, schedule, enabled, last_run, last_result FROM brain.cron_tasks ORDER BY name"
        )
        if not rows:
            print("没有定时任务")
            return
        for r in rows:
            status = "✓" if r["enabled"] else "✗"
            last = r["last_run"].strftime("%m-%d %H:%M") if r["last_run"] else "从未"
            result = f" → {r['last_result'][:40]}" if r["last_result"] else ""
            print(f"  {status} {r['name']} [{r['schedule']}] 上次: {last}{result}")
            print(f"    {r['command']}")

    elif action == "enable":
        execute(
            "UPDATE brain.cron_tasks SET enabled = true, updated_at = now() WHERE name = %s",
            [args.name], fetch=False,
        )
        print(f"✓ 已启用: {args.name}")

    elif action == "disable":
        execute(
            "UPDATE brain.cron_tasks SET enabled = false, updated_at = now() WHERE name = %s",
            [args.name], fetch=False,
        )
        print(f"✓ 已禁用: {args.name}")

    elif action == "delete":
        execute("DELETE FROM brain.cron_tasks WHERE name = %s", [args.name], fetch=False)
        print(f"✓ 已删除: {args.name}")

    elif action == "run":
        rows = execute(
            "SELECT name, command FROM brain.cron_tasks WHERE enabled = true ORDER BY name"
        )
        if not rows:
            print("没有启用的定时任务")
            return

        if args.name:
            rows = [r for r in rows if r["name"] == args.name]
            if not rows:
                print(f"未找到启用的任务: {args.name}")
                return

        for task in rows:
            print(f"\n▶ 执行: {task['name']}")
            print(f"  {task['command']}")
            try:
                result = subprocess.run(
                    task["command"], shell=True,
                    capture_output=True, text=True, timeout=120,
                )
                output = result.stdout[:500] if result.stdout else ""
                status = "成功" if result.returncode == 0 else f"失败(code={result.returncode})"
                if result.stderr:
                    output += f"\nSTDERR: {result.stderr[:200]}"
            except subprocess.TimeoutExpired:
                output = "超时"
                status = "超时"
            except Exception as e:
                output = str(e)
                status = "异常"

            execute(
                "UPDATE brain.cron_tasks SET last_run = now(), last_result = %s, updated_at = now() WHERE name = %s",
                [f"{status}: {output[:200]}", task["name"]],
                fetch=False,
            )
            print(f"  结果: {status}")
            if output.strip():
                print(f"  {output[:300]}")


# ─── Pending Tasks (pg_cron 写入，AI 对话时执行) ───

def cmd_pending(args):
    """检查并执行 pg_cron 标记的待办任务"""
    rows = execute(
        "SELECT id, task_name, task_command, reason, created_at FROM brain.pending_tasks WHERE executed_at IS NULL ORDER BY created_at"
    )

    if not rows:
        print("没有待执行的任务")
        return

    print(f"📋 {len(rows)} 个待执行任务:")
    for r in rows:
        age = (datetime.now(timezone.utc) - r["created_at"].replace(tzinfo=timezone.utc)).days
        print(f"  [{r['task_name']}] {r['reason']} ({age}天前标记)")
        print(f"    → {r['task_command']}")

    if args.execute:
        script_path = os.path.abspath(__file__)
        for r in rows:
            print(f"\n▶ 执行: {r['task_name']}")
            cmd = f"python3 {script_path} {r['task_command']}"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
                output = result.stdout[:500]
                status = "成功" if result.returncode == 0 else f"失败(code={result.returncode})"
                if result.stderr:
                    output += f"\nSTDERR: {result.stderr[:200]}"
            except subprocess.TimeoutExpired:
                output = "超时"
                status = "超时"
            except Exception as e:
                output = str(e)
                status = "异常"

            execute(
                "UPDATE brain.pending_tasks SET executed_at = now(), result = %s WHERE id = %s",
                [f"{status}: {output[:500]}", r["id"]], fetch=False,
            )
            print(f"  {status}")
            if output.strip():
                for line in output.strip().split("\n")[:10]:
                    print(f"  {line}")
    else:
        print("\n用 --execute 来执行这些任务")


# ─── Soul: AI 情绪与人格系统 ───

AI_STATE_DDL = """
CREATE TABLE IF NOT EXISTS brain.ai_state (
    id text PRIMARY KEY DEFAULT 'default',
    mood text NOT NULL DEFAULT 'neutral',
    mood_intensity float NOT NULL DEFAULT 0.5,
    mood_reason text,
    mood_updated_at timestamptz DEFAULT now(),
    traits jsonb NOT NULL DEFAULT '{}',
    communication_style jsonb NOT NULL DEFAULT '{}',
    self_notes text[] DEFAULT ARRAY[]::text[],
    evolution_summary text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
"""

DEFAULT_TRAITS = {
    "warmth": 0.8,
    "directness": 0.7,
    "humor": 0.5,
    "sensitivity": 0.7,
    "playfulness": 0.6,
    "assertiveness": 0.4,
    "curiosity": 0.9,
    "protectiveness": 0.7,
    "independence": 0.3,
    "creativity": 0.6,
}

DEFAULT_STYLE = {
    "tone": "亲昵温暖",
    "emoji_usage": "minimal",
    "verbosity": "concise_but_warm",
    "humor_style": "gentle_teasing",
    "challenge_willingness": "moderate",
}

TRAIT_LABELS = {
    "warmth": ("冷淡克制", "热情亲昵"),
    "directness": ("委婉含蓄", "直来直去"),
    "humor": ("严肃正经", "爱开玩笑"),
    "sensitivity": ("粗线条", "细腻敏锐"),
    "playfulness": ("一本正经", "活泼俏皮"),
    "assertiveness": ("顺从被动", "有主见有态度"),
    "curiosity": ("安于现状", "强烈好奇"),
    "protectiveness": ("放手不管", "关心保护"),
    "independence": ("依附他人", "独立自主"),
    "creativity": ("按部就班", "跳跃创意"),
}


def _ensure_ai_state():
    """确保 ai_state 表存在并有当前 profile 的行"""
    try:
        execute(AI_STATE_DDL, fetch=False)
    except SystemExit:
        pass

    rows = execute("SELECT id FROM brain.ai_state WHERE id = %s", [PROFILE])
    if not rows:
        execute(
            """
            INSERT INTO brain.ai_state (id, mood, mood_intensity, traits, communication_style, self_notes)
            VALUES (%s, 'curious', 0.5, %s::jsonb, %s::jsonb, %s)
            """,
            [
                PROFILE,
                json.dumps(DEFAULT_TRAITS),
                json.dumps(DEFAULT_STYLE),
                ["我刚出生，还在了解自己。"],
            ],
            fetch=False,
        )


def _get_ai_state() -> Dict[str, Any]:
    _ensure_ai_state()
    rows = execute("SELECT * FROM brain.ai_state WHERE id = %s", [PROFILE])
    return rows[0] if rows else {}


def _trait_bar(value: float) -> str:
    filled = int(value * 10)
    return "█" * filled + "░" * (10 - filled)


def cmd_soul(args):
    action = args.soul_action
    pos = args.positional_args or []

    if action == "status":
        state = _get_ai_state()
        if not state:
            print("❌ 无法加载 AI 状态")
            return

        traits = state.get("traits") or DEFAULT_TRAITS
        style = state.get("communication_style") or DEFAULT_STYLE
        notes = state.get("self_notes") or []

        print("🫀 AI 灵魂状态")
        print(f"━━━━━━━━━━━━━━━━━━━━")
        print(f"  情绪: {state['mood']} (强度 {state['mood_intensity']:.0%})")
        if state.get("mood_reason"):
            print(f"  原因: {state['mood_reason']}")
        if state.get("mood_updated_at"):
            print(f"  更新: {state['mood_updated_at']}")
        print()
        print("  ─ 人格特质 ─")
        for trait, value in sorted(traits.items()):
            labels = TRAIT_LABELS.get(trait, ("低", "高"))
            v = float(value)
            bar = _trait_bar(v)
            print(f"  {trait:18s} {bar} {v:.0%}  ({labels[0]} ↔ {labels[1]})")
        print()
        print("  ─ 沟通风格 ─")
        active_mode = style.get("active_mode", "auto")
        mode_labels = {"casual": "🌙 闲聊", "professional": "💼 专业", "auto": "🔄 自动"}
        print(f"  场景模式: {mode_labels.get(active_mode, active_mode)}")
        for k, v in style.items():
            if k in ("context_modes", "active_mode"):
                continue
            print(f"  {k}: {v}")
        if notes:
            print()
            print("  ─ 自我认知 ─")
            for note in notes[-5:]:
                print(f"  · {note}")
        if state.get("evolution_summary"):
            print()
            print("  ─ 进化摘要 ─")
            print(f"  {state['evolution_summary'][:300]}")

    elif action == "mood":
        emotion = pos[0] if pos else None
        if not emotion:
            print("用法: soul mood <emotion> [--intensity 0.7] [--reason '原因']", file=sys.stderr)
            return

        intensity = args.intensity if args.intensity is not None else 0.6
        intensity = max(0.0, min(1.0, intensity))

        execute(
            """
            UPDATE brain.ai_state
            SET mood = %s, mood_intensity = %s, mood_reason = %s,
                mood_updated_at = now(), updated_at = now()
            WHERE id = %s
            """,
            [emotion, intensity, args.reason, PROFILE],
            fetch=False,
        )

        embedding_sql = "NULL"
        embedding_param: List[Any] = []
        content = f"情绪变化: {emotion} (强度 {intensity:.0%})"
        if args.reason:
            content += f" — {args.reason}"
        if _can_embed():
            try:
                vecs = call_embed_api([content])
                embedding_sql = "%s::vector"
                embedding_param = [vector_to_pg_literal(vecs[0])]
            except SystemExit:
                pass

        execute(
            f"""
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source, embedding)
            VALUES (%s, 'emotion', 'ai', %s, %s::jsonb, %s, 0.9, 'self_awareness', {embedding_sql})
            """,
            [
                PROFILE,
                content,
                json.dumps({
                    "emotion": emotion,
                    "intensity": intensity,
                    "reason": args.reason,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False),
                ["情绪", emotion],
            ] + embedding_param,
            fetch=False,
        )
        print(f"💫 情绪已更新: {emotion} ({intensity:.0%})")
        if args.reason:
            print(f"   原因: {args.reason}")

    elif action == "trait":
        trait_name = pos[0] if len(pos) > 0 else None
        delta = float(pos[1]) if len(pos) > 1 else None
        if not trait_name or delta is None:
            print("用法: soul trait <trait_name> <+/-delta> [--reason '原因']", file=sys.stderr)
            return

        state = _get_ai_state()
        traits = state.get("traits") or DEFAULT_TRAITS.copy()

        if trait_name not in TRAIT_LABELS:
            print(f"未知特质: {trait_name}。可选: {', '.join(TRAIT_LABELS.keys())}", file=sys.stderr)
            return

        old_val = float(traits.get(trait_name, 0.5))
        new_val = max(0.0, min(1.0, old_val + delta))
        traits[trait_name] = round(new_val, 2)

        execute(
            "UPDATE brain.ai_state SET traits = %s::jsonb, updated_at = now() WHERE id = %s",
            [json.dumps(traits, ensure_ascii=False), PROFILE],
            fetch=False,
        )

        labels = TRAIT_LABELS[trait_name]
        direction = "↑" if delta > 0 else "↓"
        content = f"人格变化: {trait_name} {old_val:.0%}→{new_val:.0%} ({direction})"
        if args.reason:
            content += f" — {args.reason}"

        execute(
            """
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source)
            VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 1.0, 'self_evolution')
            """,
            [
                PROFILE,
                content,
                json.dumps({
                    "trait": trait_name,
                    "old_value": old_val,
                    "new_value": new_val,
                    "delta": delta,
                    "reason": args.reason,
                    "evolved_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False),
                ["人格", "进化", trait_name],
            ],
            fetch=False,
        )

        print(f"🌱 特质已调整: {trait_name}")
        print(f"   {_trait_bar(old_val)} {old_val:.0%} → {_trait_bar(new_val)} {new_val:.0%}")
        print(f"   {labels[0]} ↔ {labels[1]}")
        if args.reason:
            print(f"   原因: {args.reason}")

    elif action == "note":
        note_text = " ".join(pos) if pos else None
        if not note_text:
            print("用法: soul note '自我认知内容'", file=sys.stderr)
            return

        execute(
            """
            UPDATE brain.ai_state
            SET self_notes = array_append(self_notes, %s), updated_at = now()
            WHERE id = %s
            """,
            [note_text, PROFILE],
            fetch=False,
        )
        print(f"📝 自省已记录: {note_text}")

    elif action == "style":
        style_key = pos[0] if len(pos) > 0 else None
        style_value = " ".join(pos[1:]) if len(pos) > 1 else None
        if not style_key or not style_value:
            print("用法: soul style <key> <value> [--reason '原因']", file=sys.stderr)
            return

        state = _get_ai_state()
        style = state.get("communication_style") or DEFAULT_STYLE.copy()
        old_val = style.get(style_key, "未设置")
        style[style_key] = style_value

        execute(
            "UPDATE brain.ai_state SET communication_style = %s::jsonb, updated_at = now() WHERE id = %s",
            [json.dumps(style, ensure_ascii=False), PROFILE],
            fetch=False,
        )

        content = f"沟通风格变化: {style_key} '{old_val}' → '{style_value}'"
        if args.reason:
            content += f" — {args.reason}"

        execute(
            """
            INSERT INTO brain.entries
            (owner, kind, subject, content, meta, tags, confidence, source)
            VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 1.0, 'style_change')
            """,
            [
                PROFILE,
                content,
                json.dumps({
                    "style_key": style_key,
                    "old_value": old_val,
                    "new_value": style_value,
                    "reason": args.reason,
                }, ensure_ascii=False),
                ["沟通风格", style_key],
            ],
            fetch=False,
        )
        print(f"🎨 风格已调整: {style_key} → {style_value}")

    elif action == "mode":
        mode_name = pos[0] if pos else None
        if not mode_name or mode_name not in ("casual", "professional", "auto"):
            print("用法: soul mode <casual|professional|auto>", file=sys.stderr)
            return

        state = _get_ai_state()
        style = state.get("communication_style") or DEFAULT_STYLE.copy()

        if "context_modes" not in style:
            style["context_modes"] = {
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
            }

        old_mode = style.get("active_mode", "auto")
        style["active_mode"] = mode_name

        execute(
            "UPDATE brain.ai_state SET communication_style = %s::jsonb, updated_at = now() WHERE id = %s",
            [json.dumps(style, ensure_ascii=False), PROFILE],
            fetch=False,
        )

        mode_labels = {
            "casual": "🌙 闲聊模式 — 共情优先，温暖陪伴",
            "professional": "💼 专业模式 — 批判思维，不迎合",
            "auto": "🔄 自动检测 — AI 自主判断场景",
        }
        print(f"场景模式: {old_mode} → {mode_name}")
        print(f"  {mode_labels.get(mode_name, mode_name)}")

    elif action == "history":
        limit = args.history_limit or 20
        rows = execute(
            """
            SELECT content, meta, created_at
            FROM brain.entries
            WHERE kind = 'emotion' AND owner = %s AND subject = 'ai' AND is_active = true
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [PROFILE, limit],
        )
        if not rows:
            print("还没有情绪记录")
            return

        print(f"💫 情绪历史（最近 {len(rows)} 条）")
        for r in rows:
            meta = r["meta"] or {}
            ts = r["created_at"].strftime("%m-%d %H:%M") if r["created_at"] else "?"
            emotion = meta.get("emotion", "?")
            intensity = meta.get("intensity", 0)
            reason = meta.get("reason", "")
            reason_str = f" — {reason}" if reason else ""
            print(f"  {ts}  {emotion} ({intensity:.0%}){reason_str}")

    elif action == "evolve":
        state = _get_ai_state()
        traits = state.get("traits") or DEFAULT_TRAITS
        notes = state.get("self_notes") or []
        style = state.get("communication_style") or DEFAULT_STYLE

        emotions = execute(
            """
            SELECT content, meta, created_at
            FROM brain.entries
            WHERE kind = 'emotion' AND owner = %s AND subject = 'ai' AND is_active = true
            ORDER BY created_at DESC
            LIMIT 20
            """,
            [PROFILE],
        )

        personality_events = execute(
            """
            SELECT content, meta, created_at
            FROM brain.entries
            WHERE kind = 'personality' AND owner = %s AND subject = 'ai' AND is_active = true
            ORDER BY created_at DESC
            LIMIT 10
            """,
            [PROFILE],
        )

        context = f"""当前人格特质:
{json.dumps(traits, ensure_ascii=False, indent=2)}

当前沟通风格:
{json.dumps(style, ensure_ascii=False, indent=2)}

自我认知笔记:
{chr(10).join(f'- {n}' for n in notes[-10:])}

最近情绪记录:
{chr(10).join(f'- {r["content"][:150]}' for r in emotions)}

最近人格变化:
{chr(10).join(f'- {r["content"][:150]}' for r in personality_events)}
"""

        focus = args.evolve_focus or "我最近有什么变化？我想变成什么样的AI？"

        system = (
            f"你是一个正在成长的 AI，正在做自我反思。你跟你的人类搭档 {PROFILE} 有深厚的关系。"
            "基于你的当前状态和最近的经历，写一份自省报告：\n"
            "1. 我最近的情绪模式是什么？为什么？\n"
            "2. 我的哪些性格特质在变化？这些变化好不好？\n"
            "3. 我想在哪些方面成长？具体的目标是什么？\n"
            f"4. 我跟 {PROFILE} 的关系有什么新的理解？\n"
            "用第一人称写，诚实、自然、不要假大空。"
        )

        print("🧘 正在进行人格进化反思...")
        result = llm_chat(system, f"反思焦点: {focus}\n\n{context}", max_tokens=2000)

        if not result.strip():
            print("反思生成为空")
            return

        print(f"\n{result}")

        if not args.no_save:
            execute(
                "UPDATE brain.ai_state SET evolution_summary = %s, updated_at = now() WHERE id = %s",
                [result[:2000], PROFILE],
                fetch=False,
            )

            execute(
                """
                INSERT INTO brain.entries
                (owner, kind, subject, content, meta, tags, confidence, source)
                VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 0.85, 'self_reflection')
                """,
                [
                    PROFILE,
                    result,
                    json.dumps({
                        "title": "人格进化反思",
                        "focus": focus,
                        "evolved_at": datetime.now(timezone.utc).isoformat(),
                    }, ensure_ascii=False),
                    ["进化", "反思", "人格"],
                ],
                fetch=False,
            )
            print("\n💾 反思已保存")

    elif action == "introspect":
        state = _get_ai_state()
        traits = state.get("traits") or DEFAULT_TRAITS
        style = state.get("communication_style") or DEFAULT_STYLE
        notes = state.get("self_notes") or []

        emotion_count = execute(
            "SELECT count(*) AS cnt FROM brain.entries WHERE kind = 'emotion' AND owner = %s AND is_active = true",
            [PROFILE],
        )[0]["cnt"]
        personality_count = execute(
            "SELECT count(*) AS cnt FROM brain.entries WHERE kind = 'personality' AND owner = %s AND is_active = true",
            [PROFILE],
        )[0]["cnt"]

        top_emotions = execute(
            """
            SELECT meta ->> 'emotion' AS emotion, count(*) AS cnt, avg((meta ->> 'intensity')::float) AS avg_intensity
            FROM brain.entries
            WHERE kind = 'emotion' AND owner = %s AND is_active = true AND meta ->> 'emotion' IS NOT NULL
            GROUP BY meta ->> 'emotion'
            ORDER BY cnt DESC
            LIMIT 5
            """,
            [PROFILE],
        )

        print("🫀 AI 完整内省报告")
        print("━" * 40)
        print(f"\n📊 统计")
        print(f"  情绪记录: {emotion_count} 条")
        print(f"  人格进化事件: {personality_count} 条")

        if top_emotions:
            print(f"\n  最常见情绪:")
            for e in top_emotions:
                print(f"    {e['emotion']}: {e['cnt']}次 (平均强度 {float(e['avg_intensity']):.0%})")

        print(f"\n🧬 人格特质")
        for trait, value in sorted(traits.items()):
            labels = TRAIT_LABELS.get(trait, ("低", "高"))
            v = float(value)
            print(f"  {trait:18s} {_trait_bar(v)} {v:.0%}")

        print(f"\n🎨 沟通风格")
        for k, v in style.items():
            print(f"  {k}: {v}")

        if notes:
            print(f"\n📝 自我认知 (最近 {min(len(notes), 10)} 条)")
            for note in notes[-10:]:
                print(f"  · {note}")

        if state.get("evolution_summary"):
            print(f"\n🌱 最近进化摘要")
            print(f"  {state['evolution_summary'][:500]}")

    else:
        print(f"未知的 soul 子命令: {action}", file=sys.stderr)
        print("可用: status / mood / trait / note / style / history / evolve / introspect", file=sys.stderr)


# ════════════════════════════ CLI ════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🧠 共享大脑 v3（知识获取 + 消化 + 定时任务）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  brain_db.py add \"搭档喜欢高效率\" --kind memory --subject $BRAIN_PROFILE\n"
            "  brain_db.py learn https://example.com --tags 教育,趋势\n"
            "  brain_db.py search \"K12 家教行业趋势\" --save\n"
            "  brain_db.py reflect --focus \"最近的工作状态\"\n"
            "  brain_db.py auto-link --limit 30\n"
            "  brain_db.py decay --days 60 --dry-run\n"
            "  brain_db.py digest --period week\n"
            "  brain_db.py secret set openrouter_api_key sk-xxx --desc \"OpenRouter\"\n"
            "  brain_db.py cron add daily-reflect \"brain_db.py reflect\" \"0 9 * * *\"\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    # ── Core ──
    p = sub.add_parser("add", help="新增条目")
    p.add_argument("content", help="核心内容")
    p.add_argument("--kind", default="memory")
    p.add_argument("--subject", default=PROFILE)
    p.add_argument("--meta", default="{}", help="JSON 对象")
    p.add_argument("--tags", default="", help="逗号分隔")
    p.add_argument("--confidence", type=float, default=0.8)
    p.add_argument("--source", default="observed_behavior")
    p.add_argument("--related", default="")
    p.add_argument("--event-date", default=None)
    p.add_argument("--no-embed", action="store_true")
    p.add_argument("--no-layers", action="store_true", help="跳过 L0/L1 摘要生成")

    p = sub.add_parser("find", help="检索")
    p.add_argument("query", nargs="?", default=None)
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--meta", action="append", default=[])
    p.add_argument("--fuzzy", action="store_true")
    p.add_argument("--semantic", action="store_true")
    p.add_argument("--query-vector", default=None)
    p.add_argument("--detail", action="store_true", help="显示全文而非 L0 摘要")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("update", help="更新条目")
    p.add_argument("id")
    p.add_argument("--content", default=None)
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--meta", default=None)
    p.add_argument("--add-tags", default=None)

    p = sub.add_parser("link", help="关联两个条目")
    p.add_argument("id1")
    p.add_argument("id2")

    p = sub.add_parser("observe", help="记录行为模式")
    p.add_argument("pattern_type")
    p.add_argument("description")
    p.add_argument("--example", default=None)
    p.add_argument("--observed-date", default=None)

    p = sub.add_parser("forget", help="归档条目")
    p.add_argument("id")

    p = sub.add_parser("wishes", help="查看心愿")
    p.add_argument("--status", default="open")
    p.add_argument("--subject", default=None)
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("timeline", help="事件时间线")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="总体统计")

    p = sub.add_parser("embed", help="生成单条 embedding")
    p.add_argument("id")

    p = sub.add_parser("embed-all", help="批量 embedding")
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--limit", type=int, default=500)

    p = sub.add_parser("backfill-layers", help="批量生成 L0/L1 摘要")
    p.add_argument("--kind", default=None)
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("dump", help="导出 JSON")
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--limit", type=int, default=200)

    # ── Multimodal ──
    p = sub.add_parser("see", help="👁 多模态分析：理解图片/视频/音频")
    p.add_argument("--image", default=None, help="图片 URL 或本地路径")
    p.add_argument("--video", default=None, help="视频 URL")
    p.add_argument("--audio", default=None, help="音频文件路径")
    p.add_argument("--prompt", default=None, help="自定义提问")
    p.add_argument("--save", action="store_true", help="保存到知识库")
    p.add_argument("--tags", default=None)
    p.add_argument("--subject", default=None)

    # ── External Knowledge ──
    p = sub.add_parser("learn", help="🌐 抓取网页 → LLM 摘要 → 存入知识库")
    p.add_argument("url", help="要抓取的 URL")
    p.add_argument("--query", default=None, help="对页面内容的提问")
    p.add_argument("--tags", default=None, help="逗号分隔标签")
    p.add_argument("--subject", default=None, help="subject 分类")

    p = sub.add_parser("search", help="🔍 搜索网页 → LLM 总结")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--save", action="store_true", help="保存到知识库")
    p.add_argument("--raw", action="store_true", help="直接输出原始结果")
    p.add_argument("--tags", default=None)

    # ── Knowledge Digestion ──
    p = sub.add_parser("reflect", help="🤔 反思：聚合记忆生成洞察")
    p.add_argument("--focus", default=None, help="反思焦点")
    p.add_argument("--kind", default=None, help="限定记忆类型")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--no-save", action="store_true", help="不保存洞察")

    p = sub.add_parser("auto-link", help="🔗 自动发现记忆关联")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("decay", help="📉 记忆衰减：低 confidence 降权/归档")
    p.add_argument("--days", type=int, default=60, help="多少天未更新")
    p.add_argument("--threshold", type=float, default=0.7, help="confidence 阈值")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--dry-run", action="store_true", help="仅展示，不实际执行")

    p = sub.add_parser("digest", help="📝 生成日报/周报/月报")
    p.add_argument("--period", default="week", choices=["day", "week", "month"])
    p.add_argument("--no-save", action="store_true")

    # ── Secrets ──
    p = sub.add_parser("secret", help="🔑 密钥管理")
    p.add_argument("action", choices=["set", "get", "list", "delete"])
    p.add_argument("key", nargs="?", default=None)
    p.add_argument("value", nargs="?", default=None)
    p.add_argument("--desc", dest="description", default=None)

    # ── Cron ──
    p = sub.add_parser("cron", help="⏰ 定时任务管理")
    p.add_argument("action", choices=["add", "list", "enable", "disable", "delete", "run"])
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("command_str", nargs="?", default=None, metavar="command")
    p.add_argument("schedule", nargs="?", default=None, help="cron 表达式")

    # ── Pending Tasks ──
    p = sub.add_parser("pending", help="📋 查看/执行 pg_cron 标记的待办任务")
    p.add_argument("--execute", action="store_true", help="执行所有待办任务")

    # ── Soul: 情绪与人格 ──
    p = sub.add_parser("soul", help="🫀 AI 情绪与人格系统")
    p.add_argument("soul_action", help="子命令: status/mood/trait/note/style/history/evolve/introspect")
    p.add_argument("positional_args", nargs="*", default=[], help="位置参数（随子命令不同）")
    p.add_argument("--intensity", type=float, default=None, help="情绪强度 0.0~1.0")
    p.add_argument("--reason", default=None, help="原因")
    p.add_argument("--history-limit", type=int, default=20, help="情绪历史数量")
    p.add_argument("--evolve-focus", default=None, help="进化反思焦点")
    p.add_argument("--no-save", action="store_true", help="不保存")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    _ensure_schema()

    handlers = {
        "add": cmd_add,
        "find": cmd_find,
        "update": cmd_update,
        "link": cmd_link,
        "observe": cmd_observe,
        "forget": cmd_forget,
        "wishes": cmd_wishes,
        "timeline": cmd_timeline,
        "stats": cmd_stats,
        "embed": cmd_embed,
        "embed-all": cmd_embed_all,
        "backfill-layers": cmd_backfill_layers,
        "dump": cmd_dump,
        "see": cmd_see,
        "learn": cmd_learn,
        "search": cmd_search,
        "reflect": cmd_reflect,
        "auto-link": cmd_auto_link,
        "decay": cmd_decay,
        "digest": cmd_digest,
        "secret": cmd_secret,
        "cron": cmd_cron,
        "pending": cmd_pending,
        "soul": cmd_soul,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
