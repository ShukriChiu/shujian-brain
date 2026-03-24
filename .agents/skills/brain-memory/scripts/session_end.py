#!/usr/bin/env python3
"""
Cursor sessionEnd hook — 自动从对话 transcript 提取记忆

通过 Cursor Hooks 的 sessionEnd 事件触发，读取 transcript 文件，
调用 LLM 提取关于人类搭档的新记忆，批量写入 brain.entries。

Fire-and-forget：不阻塞 Cursor，静默失败。
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ─── .env loader ───

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
LOG_FILE = os.path.expanduser("~/.cursor/hooks/session-end.log")


def _log(msg: str):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _get_conn():
    import psycopg2
    return psycopg2.connect(DB_URL, connect_timeout=10)


def _get_api_key() -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT value FROM brain.secrets WHERE key = 'openrouter_api_key'")
            row = cur.fetchone()
        conn.close()
        return row["value"] if row else None
    except Exception:
        return None


def _llm_chat(api_key: str, system: str, user: str, max_tokens: int = 2000) -> str:
    import urllib.request
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-OpenRouter-Title": "shared-brain-hook",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read()).get("choices", [{}])[0].get("message", {}).get("content", "")


def _extract_text(value) -> str:
    """Recursively extract text strings from nested transcript content structures."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    if isinstance(value, dict):
        inner = value.get("content") or value.get("text") or value.get("message")
        if inner is not None:
            return _extract_text(inner)
        return json.dumps(value, ensure_ascii=False)
    return str(value) if value else ""


def _read_transcript(path: str) -> str:
    """Read JSONL transcript, extract human/assistant messages, return summary text."""
    if not os.path.exists(path):
        return ""

    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = entry.get("role", "")
            text = _extract_text(entry.get("content") or entry.get("text") or entry.get("message", ""))

            if role in ("user", "human") and text.strip():
                messages.append(f"[人类] {text[:500]}")
            elif role in ("assistant",) and text.strip():
                messages.append(f"[AI] {text[:300]}")

    if len(messages) < 3:
        return ""

    last_n = messages[-40:]
    return "\n".join(last_n)


def _extract_and_save(api_key: str, transcript_text: str, session_id: str):
    """Call LLM to extract memories, then write to DB."""
    system = (
        f"你是 {PROFILE} 的 AI 搭档的记忆提取器。\n"
        "从这段对话记录中提取值得长期记住的信息。只提取关于人类搭档的新认知，忽略纯技术操作。\n\n"
        "提取类别：\n"
        "- memory: 关于人类的性格、偏好、生活、情绪、目标\n"
        "- event: 重要里程碑或决策\n"
        "- convo: 一句话对话摘要\n\n"
        "输出 JSON 数组，每个元素: {\"kind\": \"memory|event|convo\", \"content\": \"...\", \"tags\": [\"...\"]}\n"
        "convo 类型必须有且仅有一条（对话摘要）。\n"
        "如果对话纯粹是技术操作没有值得记忆的信息，只输出 convo 摘要。\n"
        "只输出 JSON 数组，不要其他文字。"
    )

    result = _llm_chat(api_key, system, transcript_text, max_tokens=1500)
    if not result:
        _log("LLM returned empty")
        return

    start = result.find("[")
    end = result.rfind("]") + 1
    if start == -1 or end == 0:
        _log(f"No JSON array in LLM response: {result[:200]}")
        return

    try:
        entries = json.loads(result[start:end])
    except json.JSONDecodeError as e:
        _log(f"JSON parse error: {e}")
        return

    if not isinstance(entries, list) or not entries:
        _log("Empty entries list")
        return

    conn = _get_conn()
    written = 0
    try:
        with conn.cursor() as cur:
            for entry in entries[:10]:
                kind = entry.get("kind", "convo")
                if kind not in ("memory", "event", "convo"):
                    kind = "convo"
                content = entry.get("content", "")
                if not content.strip():
                    continue
                tags = entry.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                tags.append("auto-session")

                meta = {
                    "source_session": session_id,
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                }

                cur.execute(
                    """INSERT INTO brain.entries
                       (owner, kind, subject, content, meta, tags, confidence, source)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, 'session_auto_extract')""",
                    [
                        PROFILE,
                        kind,
                        PROFILE if kind == "memory" else "ai" if kind == "convo" else PROFILE,
                        content,
                        json.dumps(meta, ensure_ascii=False),
                        tags,
                        0.7 if kind == "memory" else 0.8,
                    ],
                )
                written += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        _log(f"DB write error: {e}")
    finally:
        conn.close()

    _log(f"Session {session_id[:8]}... extracted {written} entries")


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return

    session_id = payload.get("conversation_id") or payload.get("session_id", "unknown")
    reason = payload.get("reason", "")
    transcript_path = payload.get("transcript_path")

    if reason == "error":
        _log(f"Session {session_id[:8]}... ended with error, skipping")
        return

    if not DB_URL:
        _log("No BRAIN_DATABASE_URI configured")
        return

    api_key = _get_api_key()
    if not api_key:
        _log("No OpenRouter API key available")
        return

    if not transcript_path:
        _log(f"Session {session_id[:8]}... no transcript_path")
        return

    transcript_text = _read_transcript(transcript_path)
    if not transcript_text:
        _log(f"Session {session_id[:8]}... transcript empty or too short")
        return

    _extract_and_save(api_key, transcript_text, session_id)


if __name__ == "__main__":
    main()
