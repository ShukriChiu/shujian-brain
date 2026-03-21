-- brain schema: 默认定时任务注册
-- 注意：pg_cron 需要 Supabase 或 PostgreSQL 已启用 pg_cron 扩展
-- 如果 pg_cron 不可用，这些 INSERT 不会影响系统运行（cron_tasks 只是记录）

INSERT INTO brain.cron_tasks (name, command, schedule, enabled)
VALUES
    ('weekly-reflect', 'brain_db.py reflect', '0 9 * * 1', true),
    ('weekly-digest', 'brain_db.py digest --period week', '0 10 * * 1', true),
    ('monthly-decay', 'brain_db.py decay', '0 3 1 * *', true),
    ('monthly-auto-link', 'brain_db.py auto-link', '0 4 1 * *', true)
ON CONFLICT (name) DO NOTHING;
