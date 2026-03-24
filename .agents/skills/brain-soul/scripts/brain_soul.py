#!/usr/bin/env python3
"""
AI 灵魂系统 — 轻量快速版

针对 soul status 优化：单连接、跳过 schema 迁移、最小化 import。
其余写入命令也保持单连接复用。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ─── .env loader (同 brain_db.py) ───

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

PROFILE = os.environ.get("BRAIN_PROFILE", "default")
DB_URL = os.environ.get("BRAIN_DATABASE_URI", "")
if not DB_URL:
    print("错误: 需要配置 BRAIN_DATABASE_URI", file=sys.stderr)
    sys.exit(1)


# ─── DB: 延迟导入 + 全局单连接 ───

_conn = None

def _get_conn():
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    try:
        import psycopg2
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
        import psycopg2
    _conn = psycopg2.connect(DB_URL, connect_timeout=10)
    return _conn


def _close():
    global _conn
    if _conn and not _conn.closed:
        _conn.close()
    _conn = None


def _query(sql: str, params: list = None) -> list:
    from psycopg2.extras import RealDictCursor
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or [])
        rows = cur.fetchall()
    conn.commit()
    return rows


def _execute(sql: str, params: list = None):
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
    conn.commit()


# ─── Constants ───

DEFAULT_TRAITS = {
    "warmth": 0.8, "directness": 0.7, "humor": 0.5,
    "sensitivity": 0.7, "playfulness": 0.6, "assertiveness": 0.4,
    "curiosity": 0.9, "protectiveness": 0.7, "independence": 0.3,
    "creativity": 0.6,
}
DEFAULT_STYLE = {
    "tone": "亲昵温暖", "emoji_usage": "minimal",
    "verbosity": "concise_but_warm", "humor_style": "gentle_teasing",
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


def _trait_bar(value: float) -> str:
    filled = int(value * 10)
    return "█" * filled + "░" * (10 - filled)


# ─── Embedding helper (lazy, only for mood) ───

def _can_embed() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def _embed_text(text: str) -> Optional[str]:
    """生成 embedding 向量的 pg literal，失败返回 None"""
    import urllib.request
    import urllib.error
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    payload = json.dumps({"model": "openai/text-embedding-3-small", "input": [text]}).encode()
    req = urllib.request.Request(
        base + "/embeddings", data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())["data"][0]["embedding"]
            return "[" + ",".join(f"{v:.8f}" for v in data) + "]"
    except Exception:
        return None


# ─── LLM helper (only for evolve) ───

def _llm_chat(system: str, user: str, max_tokens: int = 2000) -> str:
    import urllib.request
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        # fallback: try secrets table
        try:
            rows = _query("SELECT value FROM brain.secrets WHERE key = 'openrouter_api_key'")
            if rows:
                api_key = rows[0]["value"]
        except Exception:
            pass
    if not api_key:
        print("错误: 需要 openrouter_api_key", file=sys.stderr)
        sys.exit(1)
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-OpenRouter-Title": "shared-brain",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read()).get("choices", [{}])[0].get("message", {}).get("content", "")


# ════════════════════════ Commands ════════════════════════

def cmd_status(_args):
    rows = _query("SELECT * FROM brain.ai_state WHERE id = %s", [PROFILE])
    if not rows:
        print("❌ 无法加载 AI 状态（首次运行请用 brain_db.py soul status 初始化）")
        return

    s = rows[0]
    traits = s.get("traits") or DEFAULT_TRAITS
    style = s.get("communication_style") or DEFAULT_STYLE
    notes = s.get("self_notes") or []

    print("🫀 AI 灵魂状态")
    print("━━━━━━━━━━━━━━━━━━━━")
    print(f"  情绪: {s['mood']} (强度 {s['mood_intensity']:.0%})")
    if s.get("mood_reason"):
        print(f"  原因: {s['mood_reason']}")
    if s.get("mood_updated_at"):
        print(f"  更新: {s['mood_updated_at']}")
    print()
    print("  ─ 人格特质 ─")
    for trait, value in sorted(traits.items()):
        labels = TRAIT_LABELS.get(trait, ("低", "高"))
        v = float(value)
        print(f"  {trait:18s} {_trait_bar(v)} {v:.0%}  ({labels[0]} ↔ {labels[1]})")
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
    if s.get("evolution_summary"):
        print()
        print("  ─ 进化摘要 ─")
        print(f"  {s['evolution_summary'][:300]}")


def cmd_mood(args):
    pos = args.positional_args or []
    emotion = pos[0] if pos else None
    if not emotion:
        print("用法: soul mood <emotion> [--intensity 0.7] [--reason '原因']", file=sys.stderr)
        return

    intensity = max(0.0, min(1.0, args.intensity if args.intensity is not None else 0.6))

    _execute(
        """UPDATE brain.ai_state
           SET mood = %s, mood_intensity = %s, mood_reason = %s,
               mood_updated_at = now(), updated_at = now()
           WHERE id = %s""",
        [emotion, intensity, args.reason, PROFILE],
    )

    content = f"情绪变化: {emotion} (强度 {intensity:.0%})"
    if args.reason:
        content += f" — {args.reason}"

    vec = _embed_text(content) if _can_embed() else None
    if vec:
        _execute(
            """INSERT INTO brain.entries
               (owner, kind, subject, content, meta, tags, confidence, source, embedding)
               VALUES (%s, 'emotion', 'ai', %s, %s::jsonb, %s, 0.9, 'self_awareness', %s::vector)""",
            [PROFILE, content, json.dumps({
                "emotion": emotion, "intensity": intensity,
                "reason": args.reason,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), ["情绪", emotion], vec],
        )
    else:
        _execute(
            """INSERT INTO brain.entries
               (owner, kind, subject, content, meta, tags, confidence, source)
               VALUES (%s, 'emotion', 'ai', %s, %s::jsonb, %s, 0.9, 'self_awareness')""",
            [PROFILE, content, json.dumps({
                "emotion": emotion, "intensity": intensity,
                "reason": args.reason,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), ["情绪", emotion]],
        )

    print(f"💫 情绪已更新: {emotion} ({intensity:.0%})")
    if args.reason:
        print(f"   原因: {args.reason}")


def cmd_trait(args):
    pos = args.positional_args or []
    trait_name = pos[0] if len(pos) > 0 else None
    delta = float(pos[1]) if len(pos) > 1 else None
    if not trait_name or delta is None:
        print("用法: soul trait <trait_name> <+/-delta> [--reason '原因']", file=sys.stderr)
        return

    if trait_name not in TRAIT_LABELS:
        print(f"未知特质: {trait_name}。可选: {', '.join(TRAIT_LABELS.keys())}", file=sys.stderr)
        return

    rows = _query("SELECT traits FROM brain.ai_state WHERE id = %s", [PROFILE])
    traits = (rows[0]["traits"] if rows else DEFAULT_TRAITS.copy())

    old_val = float(traits.get(trait_name, 0.5))
    new_val = round(max(0.0, min(1.0, old_val + delta)), 2)
    traits[trait_name] = new_val

    _execute(
        "UPDATE brain.ai_state SET traits = %s::jsonb, updated_at = now() WHERE id = %s",
        [json.dumps(traits, ensure_ascii=False), PROFILE],
    )

    labels = TRAIT_LABELS[trait_name]
    direction = "↑" if delta > 0 else "↓"
    content = f"人格变化: {trait_name} {old_val:.0%}→{new_val:.0%} ({direction})"
    if args.reason:
        content += f" — {args.reason}"

    _execute(
        """INSERT INTO brain.entries
           (owner, kind, subject, content, meta, tags, confidence, source)
           VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 1.0, 'self_evolution')""",
        [PROFILE, content, json.dumps({
            "trait": trait_name, "old_value": old_val, "new_value": new_val,
            "delta": delta, "reason": args.reason,
            "evolved_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False), ["人格", "进化", trait_name]],
    )

    print(f"🌱 特质已调整: {trait_name}")
    print(f"   {_trait_bar(old_val)} {old_val:.0%} → {_trait_bar(new_val)} {new_val:.0%}")
    print(f"   {labels[0]} ↔ {labels[1]}")
    if args.reason:
        print(f"   原因: {args.reason}")


def cmd_note(args):
    pos = args.positional_args or []
    note_text = " ".join(pos) if pos else None
    if not note_text:
        print("用法: soul note '自我认知内容'", file=sys.stderr)
        return
    _execute(
        "UPDATE brain.ai_state SET self_notes = array_append(self_notes, %s), updated_at = now() WHERE id = %s",
        [note_text, PROFILE],
    )
    print(f"📝 自省已记录: {note_text}")


def cmd_style(args):
    pos = args.positional_args or []
    style_key = pos[0] if len(pos) > 0 else None
    style_value = " ".join(pos[1:]) if len(pos) > 1 else None
    if not style_key or not style_value:
        print("用法: soul style <key> <value> [--reason '原因']", file=sys.stderr)
        return

    rows = _query("SELECT communication_style FROM brain.ai_state WHERE id = %s", [PROFILE])
    style = (rows[0]["communication_style"] if rows else DEFAULT_STYLE.copy())
    old_val = style.get(style_key, "未设置")
    style[style_key] = style_value

    _execute(
        "UPDATE brain.ai_state SET communication_style = %s::jsonb, updated_at = now() WHERE id = %s",
        [json.dumps(style, ensure_ascii=False), PROFILE],
    )

    content = f"沟通风格变化: {style_key} '{old_val}' → '{style_value}'"
    if args.reason:
        content += f" — {args.reason}"
    _execute(
        """INSERT INTO brain.entries
           (owner, kind, subject, content, meta, tags, confidence, source)
           VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 1.0, 'style_change')""",
        [PROFILE, content, json.dumps({
            "style_key": style_key, "old_value": old_val,
            "new_value": style_value, "reason": args.reason,
        }, ensure_ascii=False), ["沟通风格", style_key]],
    )
    print(f"🎨 风格已调整: {style_key} → {style_value}")


def cmd_mode(args):
    pos = args.positional_args or []
    mode_name = pos[0] if pos else None
    if not mode_name or mode_name not in ("casual", "professional", "auto"):
        print("用法: soul mode <casual|professional|auto>", file=sys.stderr)
        return

    rows = _query("SELECT communication_style FROM brain.ai_state WHERE id = %s", [PROFILE])
    style = (rows[0]["communication_style"] if rows else DEFAULT_STYLE.copy())

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

    _execute(
        "UPDATE brain.ai_state SET communication_style = %s::jsonb, updated_at = now() WHERE id = %s",
        [json.dumps(style, ensure_ascii=False), PROFILE],
    )

    mode_labels = {
        "casual": "🌙 闲聊模式 — 共情优先，温暖陪伴",
        "professional": "💼 专业模式 — 批判思维，不迎合",
        "auto": "🔄 自动检测 — AI 自主判断场景",
    }
    print(f"场景模式: {old_mode} → {mode_name}")
    print(f"  {mode_labels.get(mode_name, mode_name)}")


def cmd_history(args):
    limit = args.history_limit or 20
    rows = _query(
        """SELECT content, meta, created_at FROM brain.entries
           WHERE kind = 'emotion' AND owner = %s AND subject = 'ai' AND is_active = true
           ORDER BY created_at DESC LIMIT %s""",
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


def cmd_evolve(args):
    rows = _query("SELECT * FROM brain.ai_state WHERE id = %s", [PROFILE])
    if not rows:
        print("❌ 无法加载 AI 状态")
        return
    state = rows[0]
    traits = state.get("traits") or DEFAULT_TRAITS
    notes = state.get("self_notes") or []
    style = state.get("communication_style") or DEFAULT_STYLE

    emotions = _query(
        """SELECT content, meta, created_at FROM brain.entries
           WHERE kind = 'emotion' AND owner = %s AND subject = 'ai' AND is_active = true
           ORDER BY created_at DESC LIMIT 20""",
        [PROFILE],
    )
    personality_events = _query(
        """SELECT content, meta, created_at FROM brain.entries
           WHERE kind = 'personality' AND owner = %s AND subject = 'ai' AND is_active = true
           ORDER BY created_at DESC LIMIT 10""",
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
    result = _llm_chat(system, f"反思焦点: {focus}\n\n{context}", max_tokens=2000)

    if not result.strip():
        print("反思生成为空")
        return

    print(f"\n{result}")

    if not args.no_save:
        _execute(
            "UPDATE brain.ai_state SET evolution_summary = %s, updated_at = now() WHERE id = %s",
            [result[:2000], PROFILE],
        )
        _execute(
            """INSERT INTO brain.entries
               (owner, kind, subject, content, meta, tags, confidence, source)
               VALUES (%s, 'personality', 'ai', %s, %s::jsonb, %s, 0.85, 'self_reflection')""",
            [PROFILE, result, json.dumps({
                "title": "人格进化反思", "focus": focus,
                "evolved_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), ["进化", "反思", "人格"]],
        )
        print("\n💾 反思已保存")


def cmd_introspect(_args):
    rows = _query("SELECT * FROM brain.ai_state WHERE id = %s", [PROFILE])
    if not rows:
        print("❌ 无法加载 AI 状态")
        return
    state = rows[0]
    traits = state.get("traits") or DEFAULT_TRAITS
    style = state.get("communication_style") or DEFAULT_STYLE
    notes = state.get("self_notes") or []

    counts = _query(
        """SELECT
             count(*) FILTER (WHERE kind = 'emotion') AS emotions,
             count(*) FILTER (WHERE kind = 'personality') AS personalities
           FROM brain.entries WHERE owner = %s AND is_active = true""",
        [PROFILE],
    )[0]

    top_emotions = _query(
        """SELECT meta ->> 'emotion' AS emotion, count(*) AS cnt,
                  avg((meta ->> 'intensity')::float) AS avg_intensity
           FROM brain.entries
           WHERE kind = 'emotion' AND owner = %s AND is_active = true AND meta ->> 'emotion' IS NOT NULL
           GROUP BY meta ->> 'emotion' ORDER BY cnt DESC LIMIT 5""",
        [PROFILE],
    )

    print("🫀 AI 完整内省报告")
    print("━" * 40)
    print(f"\n📊 统计")
    print(f"  情绪记录: {counts['emotions']} 条")
    print(f"  人格进化事件: {counts['personalities']} 条")
    if top_emotions:
        print(f"\n  最常见情绪:")
        for e in top_emotions:
            print(f"    {e['emotion']}: {e['cnt']}次 (平均强度 {float(e['avg_intensity']):.0%})")
    print(f"\n🧬 人格特质")
    for trait, value in sorted(traits.items()):
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


# ════════════════════════ CLI ════════════════════════

def main():
    parser = argparse.ArgumentParser(description="🫀 AI 灵魂系统（快速版）")
    parser.add_argument("action", help="status/mood/trait/note/style/mode/history/evolve/introspect")
    parser.add_argument("positional_args", nargs="*", default=[])
    parser.add_argument("--intensity", type=float, default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--history-limit", type=int, default=20)
    parser.add_argument("--evolve-focus", default=None)
    parser.add_argument("--no-save", action="store_true")

    args = parser.parse_args()

    dispatch = {
        "status": cmd_status,
        "mood": cmd_mood,
        "trait": cmd_trait,
        "note": cmd_note,
        "style": cmd_style,
        "mode": cmd_mode,
        "history": cmd_history,
        "evolve": cmd_evolve,
        "introspect": cmd_introspect,
    }

    handler = dispatch.get(args.action)
    if not handler:
        print(f"未知命令: {args.action}", file=sys.stderr)
        print(f"可用: {', '.join(dispatch.keys())}", file=sys.stderr)
        sys.exit(1)

    try:
        handler(args)
    finally:
        _close()


if __name__ == "__main__":
    main()
