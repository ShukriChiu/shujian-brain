#!/usr/bin/env python3
"""
书剑共享大脑 v2 - 统一 entries 表命令行工具

核心命令:
  add/find/update/link/observe/forget/stats/timeline/wishes/embed/embed-all
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import date
from typing import Any, Dict, List, Optional


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


DB_URL = os.environ.get(
    "BRAIN_DATABASE_URI",
    "postgresql://postgres.gmwqrakbiamnxtxzsptq:Loveyiran1314@aws-1-ap-south-1.pooler.supabase.com:5432/postgres",
)

EMBED_FUNCTION_URL = os.environ.get(
    "BRAIN_EMBED_URL",
    "https://gmwqrakbiamnxtxzsptq.supabase.co/functions/v1/embed",
)
BRAIN_API_KEY = os.environ.get("BRAIN_API_KEY", "")


ALLOWED_KIND = ["memory", "event", "pattern", "wish", "convo"]
ALLOWED_SUBJECT = ["shujian", "ai", "collaboration", "project", "business", "system"]


def get_conn():
    try:
        return psycopg2.connect(DB_URL, connect_timeout=10)
    except psycopg2.OperationalError as e:
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
    """兼容 psycopg2 对 uuid[] 可能返回 list 或 '{...}' 字符串两种情况。"""
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


def call_embed_api(texts: List[str]) -> List[List[float]]:
    """调用 Supabase Edge Function 获取 embedding 向量。"""
    if not BRAIN_API_KEY:
        print("错误: 需要设置 BRAIN_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"input": texts}).encode("utf-8")
    req = urllib.request.Request(
        EMBED_FUNCTION_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRAIN_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Embed API 错误 ({e.code}): {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Embed API 网络错误: {e.reason}", file=sys.stderr)
        sys.exit(1)

    embeddings_data = result.get("embeddings", [])
    embeddings_data.sort(key=lambda x: x["index"])
    return [item["embedding"] for item in embeddings_data]


def vector_to_pg_literal(vec: List[float]) -> str:
    """将 Python float list 转换为 PostgreSQL vector 字面量。"""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


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
    if BRAIN_API_KEY and not args.no_embed:
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
        (kind, subject, content, meta, tags, confidence, source, related, event_date, embedding)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, {embedding_sql})
        RETURNING id, kind, subject
        """,
        [
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
    print(f"✓ 已写入: [{r['kind']}/{r['subject']}] {r['id']}{embed_hint}")


def cmd_find(args):
    conditions = ["is_active = true"]
    params: List[Any] = []

    if args.kind:
        conditions.append("kind = %s")
        params.append(args.kind)
    if args.subject:
        conditions.append("subject = %s")
        params.append(args.subject)
    if args.tag:
        conditions.append("%s = ANY(tags)")
        params.append(args.tag)

    use_semantic = args.semantic and args.query and BRAIN_API_KEY

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
    elif args.semantic and args.query and not BRAIN_API_KEY:
        print("⚠ --semantic 需要 BRAIN_API_KEY 环境变量；改为普通文本检索。", file=sys.stderr)
    elif args.query_vector:
        order_sql = "embedding <=> %s::vector ASC"
        params.append(args.query_vector)
        conditions.append("embedding IS NOT NULL")

    where_sql = " AND ".join(conditions)
    rows = execute(
        f"""
        SELECT id, kind, subject, content, meta, tags, confidence, source, event_date, updated_at
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

    for r in rows:
        tags = f" #{' #'.join(r['tags'])}" if r["tags"] else ""
        conf = f" [{r['confidence']:.0%}]" if r["confidence"] < 1 else ""
        date = f" @{r['event_date']}" if r["event_date"] else ""
        print(f"\n[{r['kind']}/{r['subject']}]{conf}{date}{tags}")
        print(f"  {r['content'][:220]}{'...' if len(r['content']) > 220 else ''}")
        print(f"  meta: {pretty_meta(r['meta'])}")
        print(f"  id: {r['id']}")

    print(f"\n共 {len(rows)} 条")


def cmd_update(args):
    existing = execute("SELECT id FROM brain.entries WHERE id = %s AND is_active = true", [args.id])
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

    params.append(args.id)
    execute(f"UPDATE brain.entries SET {', '.join(sets)} WHERE id = %s", params, fetch=False)
    print(f"✓ 已更新: {args.id}")


def cmd_link(args):
    exists = execute(
        "SELECT id FROM brain.entries WHERE id = ANY(%s::uuid[]) AND is_active = true",
        [[args.id1, args.id2]],
    )
    if len(exists) != 2:
        print("link 失败：至少一个 id 不存在或已归档", file=sys.stderr)
        return

    execute(
        """
        UPDATE brain.entries
        SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
            updated_at = now()
        WHERE id = %s
        """,
        [args.id2, args.id2, args.id1],
        fetch=False,
    )
    execute(
        """
        UPDATE brain.entries
        SET related = CASE WHEN NOT (%s::uuid = ANY(related)) THEN array_append(related, %s::uuid) ELSE related END,
            updated_at = now()
        WHERE id = %s
        """,
        [args.id1, args.id1, args.id2],
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
          AND is_active = true
          AND (content ILIKE %s OR (meta ->> 'pattern_type' = %s AND content ILIKE %s))
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        [f"%{args.description}%", args.pattern_type, f"%{args.description[:24]}%"],
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
        (kind, subject, content, meta, tags, confidence, source, event_date)
        VALUES ('pattern', 'shujian', %s, %s::jsonb, %s, %s, 'observed_behavior', %s)
        """,
        [args.description, json.dumps(meta, ensure_ascii=False), [args.pattern_type, "pattern"], 0.8, observed_date],
        fetch=False,
    )
    print(f"✓ 新模式已记录: [{args.pattern_type}] {args.description}")


def cmd_forget(args):
    updated = execute(
        "UPDATE brain.entries SET is_active = false, updated_at = now() WHERE id = %s RETURNING id",
        [args.id],
    )
    if not updated:
        print(f"未找到条目: {args.id}")
        return
    print(f"✓ 已归档: {args.id}")


def cmd_wishes(args):
    conditions = ["kind = 'wish'", "is_active = true"]
    params: List[Any] = []
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
        WHERE kind = 'event' AND is_active = true
        ORDER BY COALESCE(event_date, created_at::date) DESC, created_at DESC
        LIMIT %s
        """,
        [args.limit],
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
          count(*) FILTER (WHERE kind = 'wish' AND is_active = true AND meta ->> 'status' = 'open') AS open_wishes,
          count(*) FILTER (WHERE is_active = true AND embedding IS NOT NULL) AS with_embedding
        FROM brain.entries
        """
    )[0]
    print("🧠 共享大脑 v2")
    print(f"  总活跃条目: {summary['total_active']}")
    print(f"  有 embedding: {summary['with_embedding']}/{summary['total_active']}")
    print(f"  memory: {summary['memories']}")
    print(f"  pattern: {summary['patterns']}")
    print(f"  event: {summary['events']}")
    print(f"  wish: {summary['wishes']} (open={summary['open_wishes']})")
    print(f"  convo: {summary['convos']}")

    by_subject = execute(
        """
        SELECT subject, count(*) AS cnt
        FROM brain.entries
        WHERE is_active = true
        GROUP BY subject
        ORDER BY cnt DESC
        """
    )
    print("\n  按 subject 分布:")
    for row in by_subject:
        print(f"    {row['subject']}: {row['cnt']}")

    by_kind = execute(
        """
        SELECT kind, count(*) AS cnt
        FROM brain.entries
        WHERE is_active = true
        GROUP BY kind
        ORDER BY kind
        """
    )
    print("\n  按 kind 分布:")
    for row in by_kind:
        print(f"    {row['kind']}: {row['cnt']}")


def cmd_embed(args):
    """为指定条目生成并写入 embedding。"""
    rows = execute(
        "SELECT id, content, meta FROM brain.entries WHERE id = %s AND is_active = true",
        [args.id],
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
    """批量为没有 embedding 的活跃条目生成向量。"""
    conditions = ["is_active = true", "embedding IS NULL"]
    params: List[Any] = []
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


def _build_embed_text(row: Dict[str, Any]) -> str:
    """构建用于 embedding 的文本，融合 content + meta 关键字段。"""
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


def cmd_dump(args):
    conditions = ["is_active = true"]
    params: List[Any] = []
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


def main():
    parser = argparse.ArgumentParser(
        description="🧠 书剑共享大脑 v2（entries统一表）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  brain_db.py add \"书剑喜欢高效率\" --kind memory --subject shujian --meta '{\"aspect\":\"personality\"}' --tags 效率,偏好\n"
            "  brain_db.py find 效率 --kind memory --subject shujian\n"
            "  brain_db.py find --meta aspect=personality --kind memory\n"
            "  brain_db.py observe work_habit \"深夜高效工作\" --example \"凌晨仍在推进需求\"\n"
            "  brain_db.py link <id1> <id2>\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add", help="新增条目到 brain.entries")
    p.add_argument("content", help="核心内容")
    p.add_argument("--kind", default="memory", help="memory/event/pattern/wish/convo")
    p.add_argument("--subject", default="shujian", help="shujian/ai/collaboration/project/business/system")
    p.add_argument("--meta", default="{}", help="JSON 对象字符串")
    p.add_argument("--tags", default="", help="逗号分隔标签")
    p.add_argument("--confidence", type=float, default=0.8)
    p.add_argument("--source", default="observed_behavior")
    p.add_argument("--related", default="", help="逗号分隔关联 ID")
    p.add_argument("--event-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--no-embed", action="store_true", help="跳过自动生成 embedding")

    p = sub.add_parser("find", help="检索 entries")
    p.add_argument("query", nargs="?", default=None, help="关键词（可选）")
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--meta", action="append", default=[], help="key=value，可重复")
    p.add_argument("--fuzzy", action="store_true", help="启用 pg_trgm 模糊匹配(%)")
    p.add_argument("--semantic", action="store_true", help="语义检索模式（需 query-vector）")
    p.add_argument("--query-vector", default=None, help="手动传入向量字面量，例如 '[0.1,0.2,...]'")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("update", help="更新条目")
    p.add_argument("id")
    p.add_argument("--content", default=None)
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--meta", default=None, help="JSON 对象（merge 到 meta）")
    p.add_argument("--add-tags", default=None)

    p = sub.add_parser("link", help="关联两个条目")
    p.add_argument("id1")
    p.add_argument("id2")

    p = sub.add_parser("observe", help="记录行为模式（pattern）")
    p.add_argument("pattern_type")
    p.add_argument("description")
    p.add_argument("--example", default=None)
    p.add_argument("--observed-date", default=None, help="YYYY-MM-DD，默认今天")

    p = sub.add_parser("forget", help="归档条目（软删除）")
    p.add_argument("id")

    p = sub.add_parser("wishes", help="查看心愿列表")
    p.add_argument("--status", default="open")
    p.add_argument("--subject", default=None)
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("timeline", help="查看事件时间线")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="查看总体统计")

    p = sub.add_parser("embed", help="为单个条目生成 embedding")
    p.add_argument("id", help="条目 UUID")

    p = sub.add_parser("embed-all", help="批量为缺少 embedding 的条目生成向量")
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--batch-size", type=int, default=20, help="每批发送给 API 的条数")
    p.add_argument("--limit", type=int, default=500, help="最多处理多少条")

    p = sub.add_parser("dump", help="导出为 JSON")
    p.add_argument("--kind", default=None)
    p.add_argument("--subject", default=None)
    p.add_argument("--limit", type=int, default=200)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if getattr(args, "observed_date", None) is None:
        # DB 内用 CURRENT_DATE; 这里传 None 即可
        pass

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
        "dump": cmd_dump,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
