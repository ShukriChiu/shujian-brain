-- brain schema: 辅助表

-- 密钥管理
CREATE TABLE IF NOT EXISTS brain.secrets (
    key text PRIMARY KEY,
    value text NOT NULL,
    description text,
    created_at timestamptz DEFAULT now()
);

-- 定时任务管理
CREATE TABLE IF NOT EXISTS brain.cron_tasks (
    name text PRIMARY KEY,
    command text NOT NULL,
    schedule text NOT NULL,
    enabled boolean DEFAULT true,
    last_run timestamptz,
    created_at timestamptz DEFAULT now()
);

-- pg_cron 待办任务队列
CREATE TABLE IF NOT EXISTS brain.pending_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type text NOT NULL,
    task_params jsonb DEFAULT '{}',
    created_at timestamptz DEFAULT now(),
    executed_at timestamptz,
    result text
);
