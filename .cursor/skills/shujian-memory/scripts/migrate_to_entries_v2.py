#!/usr/bin/env python3
"""
迁移 brain v1(5张表) -> brain.entries(v2统一表)

用法:
  python3 migrate_to_entries_v2.py
  python3 migrate_to_entries_v2.py --reset
"""

import argparse
import json
import os
import subprocess
import sys
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


def map_memory_subject(category: str) -> str:
    mapping = {
        "about_shujian": "shujian",
        "about_me": "ai",
        "collaboration": "collaboration",
        "projects": "project",
        "business": "business",
        "life": "shujian",
    }
    return mapping.get(category, "system")


def map_event_subject(category: str) -> str:
    if category in ("project_milestone",):
        return "project"
    if category in ("business_insight",):
        return "business"
    if category in ("collaboration_evolution",):
        return "collaboration"
    return "system"


def map_wish_subject(from_who: str, to_who: str) -> str:
    if from_who == "ai" and to_who == "shujian":
        return "shujian"
    if from_who == "shujian":
        return "ai"
    return "collaboration"


def rows(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(conn, sql: str, params: tuple[Any, ...] = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def insert_entry(
    conn,
    *,
    kind: str,
    subject: str,
    content: str,
    meta: Dict[str, Any],
    tags: Optional[List[str]] = None,
    confidence: float = 0.8,
    source: str = "observed_behavior",
    related: Optional[List[str]] = None,
    is_active: bool = True,
    event_date: Any = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> None:
    execute(
        conn,
        """
        INSERT INTO brain.entries
        (kind, subject, content, meta, tags, confidence, source, related, is_active, event_date, created_at, updated_at)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
        """,
        (
            kind,
            subject,
            content,
            json.dumps(meta, ensure_ascii=False),
            tags or [],
            confidence,
            source,
            related or [],
            is_active,
            event_date,
            created_at,
            updated_at,
        ),
    )


def migrate(reset: bool = False) -> None:
    conn = psycopg2.connect(DB_URL, connect_timeout=10)
    stats = {"memory": 0, "pattern": 0, "event": 0, "wish": 0, "convo": 0}
    try:
        conn.autocommit = False
        if reset:
            execute(conn, "DELETE FROM brain.entries")

        # memories -> kind=memory
        mems = rows(
            conn,
            """
            SELECT id, category, subcategory, title, content, confidence, source, tags,
                   created_at, updated_at, superseded_by, is_active
            FROM brain.memories
            """,
        )
        for m in mems:
            meta = {
                "title": m["title"],
                "category": m["category"],
                "subcategory": m["subcategory"],
                "superseded_by": str(m["superseded_by"]) if m["superseded_by"] else None,
                "legacy_id": str(m["id"]),
                "legacy_table": "memories",
            }
            related = [str(m["superseded_by"])] if m["superseded_by"] else []
            insert_entry(
                conn,
                kind="memory",
                subject=map_memory_subject(m["category"]),
                content=m["content"],
                meta=meta,
                tags=m["tags"] or [],
                confidence=float(m["confidence"] or 0.8),
                source=m["source"] or "observed_behavior",
                related=related,
                is_active=bool(m["is_active"]),
                created_at=m["created_at"],
                updated_at=m["updated_at"],
            )
            stats["memory"] += 1

        # patterns -> kind=pattern
        patterns = rows(
            conn,
            """
            SELECT id, pattern_type, description, examples, frequency,
                   first_observed, last_observed, is_active, created_at, updated_at
            FROM brain.patterns
            """,
        )
        for p in patterns:
            freq = int(p["frequency"] or 1)
            conf = min(1.0, 0.6 + 0.05 * freq)
            meta = {
                "pattern_type": p["pattern_type"],
                "frequency": freq,
                "examples": p["examples"] or [],
                "first_observed": p["first_observed"].isoformat() if p["first_observed"] else None,
                "last_observed": p["last_observed"].isoformat() if p["last_observed"] else None,
                "legacy_id": str(p["id"]),
                "legacy_table": "patterns",
            }
            tags = [p["pattern_type"], "pattern"]
            insert_entry(
                conn,
                kind="pattern",
                subject="shujian",
                content=p["description"],
                meta=meta,
                tags=tags,
                confidence=conf,
                source="observed_behavior",
                is_active=bool(p["is_active"]),
                event_date=p["last_observed"],
                created_at=p["created_at"],
                updated_at=p["updated_at"],
            )
            stats["pattern"] += 1

        # growth_timeline -> kind=event
        events = rows(
            conn,
            """
            SELECT id, event_date, title, description, category, significance, related_memories, created_at
            FROM brain.growth_timeline
            """,
        )
        for e in events:
            meta = {
                "title": e["title"],
                "category": e["category"],
                "significance": int(e["significance"] or 3),
                "related_memories": [str(x) for x in (e["related_memories"] or [])],
                "legacy_id": str(e["id"]),
                "legacy_table": "growth_timeline",
            }
            insert_entry(
                conn,
                kind="event",
                subject=map_event_subject(e["category"]),
                content=e["description"],
                meta=meta,
                tags=[e["category"], "timeline"],
                confidence=0.9,
                source="observed_behavior",
                related=[str(x) for x in (e["related_memories"] or [])],
                event_date=e["event_date"],
                created_at=e["created_at"],
                updated_at=e["created_at"],
            )
            stats["event"] += 1

        # wishes -> kind=wish
        wishes = rows(
            conn,
            """
            SELECT id, from_who, to_who, content, context, status, response, created_at, responded_at
            FROM brain.wishes
            """,
        )
        for w in wishes:
            meta = {
                "from_who": w["from_who"],
                "to_who": w["to_who"],
                "status": w["status"],
                "response": w["response"],
                "context": w["context"],
                "responded_at": w["responded_at"].isoformat() if w["responded_at"] else None,
                "legacy_id": str(w["id"]),
                "legacy_table": "wishes",
            }
            insert_entry(
                conn,
                kind="wish",
                subject=map_wish_subject(w["from_who"], w["to_who"]),
                content=w["content"],
                meta=meta,
                tags=[w["status"] or "open", w["from_who"]],
                confidence=1.0,
                source="direct_statement",
                is_active=True,
                event_date=w["created_at"].date() if w["created_at"] else None,
                created_at=w["created_at"],
                updated_at=w["responded_at"] or w["created_at"],
            )
            stats["wish"] += 1

        # conversations -> kind=convo
        convos = rows(
            conn,
            """
            SELECT id, session_date, summary, key_decisions, mood_observed, topics, new_memories_created, created_at
            FROM brain.conversations
            """,
        )
        for c in convos:
            meta = {
                "key_decisions": c["key_decisions"] or [],
                "mood_observed": c["mood_observed"],
                "topics": c["topics"] or [],
                "new_memories_created": [str(x) for x in (c["new_memories_created"] or [])],
                "legacy_id": str(c["id"]),
                "legacy_table": "conversations",
            }
            insert_entry(
                conn,
                kind="convo",
                subject="collaboration",
                content=c["summary"],
                meta=meta,
                tags=c["topics"] or ["conversation"],
                confidence=1.0,
                source="observed_behavior",
                related=[str(x) for x in (c["new_memories_created"] or [])],
                event_date=c["session_date"],
                created_at=c["created_at"],
                updated_at=c["created_at"],
            )
            stats["convo"] += 1

        conn.commit()
        total = sum(stats.values())
        print("迁移完成:")
        print(f"  memory:  {stats['memory']}")
        print(f"  pattern: {stats['pattern']}")
        print(f"  event:   {stats['event']}")
        print(f"  wish:    {stats['wish']}")
        print(f"  convo:   {stats['convo']}")
        print(f"  total:   {total}")
    except Exception as e:
        conn.rollback()
        print(f"迁移失败: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 brain v1 -> brain.entries v2")
    parser.add_argument("--reset", action="store_true", help="清空 brain.entries 后重新迁移")
    args = parser.parse_args()
    migrate(reset=args.reset)


if __name__ == "__main__":
    main()
