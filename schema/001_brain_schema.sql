-- brain schema: 核心表
-- 由 brain-identity init 自动执行

CREATE SCHEMA IF NOT EXISTS brain;

-- 需要 pgvector 扩展（Supabase 已预装）
CREATE EXTENSION IF NOT EXISTS vector;

-- 统一数据表
CREATE TABLE IF NOT EXISTS brain.entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner text NOT NULL DEFAULT 'default',
    kind text NOT NULL,
    subject text,
    content text NOT NULL,
    meta jsonb DEFAULT '{}',
    tags text[] DEFAULT ARRAY[]::text[],
    confidence float DEFAULT 0.8,
    source text,
    event_date date,
    related uuid[] DEFAULT ARRAY[]::uuid[],
    embedding vector(1536),
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

ALTER TABLE brain.entries ADD CONSTRAINT entries_kind_check
    CHECK (kind = ANY (ARRAY['memory','event','pattern','wish','convo','knowledge','insight','bookmark','emotion','personality','identity']));

CREATE INDEX IF NOT EXISTS idx_entries_owner ON brain.entries(owner);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON brain.entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_owner_kind ON brain.entries(owner, kind);
CREATE INDEX IF NOT EXISTS idx_entries_active ON brain.entries(is_active) WHERE is_active = true;

-- AI 人格状态表
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
