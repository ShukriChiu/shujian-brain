-- brain schema: 自动化函数

-- 记忆自动衰减：降低长期未更新且低 confidence 的记忆权重，归档极低的
CREATE OR REPLACE FUNCTION brain.auto_decay()
RETURNS void AS $$
BEGIN
    -- 归档：超过 90 天未更新且 confidence < 0.3
    UPDATE brain.entries
    SET is_active = false, updated_at = now()
    WHERE is_active = true
      AND kind IN ('memory', 'pattern', 'convo')
      AND confidence < 0.3
      AND updated_at < now() - interval '90 days';

    -- 降权：超过 60 天未更新且 confidence < 0.7
    UPDATE brain.entries
    SET confidence = GREATEST(confidence - 0.1, 0.1), updated_at = now()
    WHERE is_active = true
      AND kind IN ('memory', 'pattern')
      AND confidence < 0.7
      AND updated_at < now() - interval '60 days';
END;
$$ LANGUAGE plpgsql;
