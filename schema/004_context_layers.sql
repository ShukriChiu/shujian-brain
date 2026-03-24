-- L0/L1 context layers for brain.entries
-- L0: one-line abstract (~30 chars), L1: key points overview (~200 chars)
-- L2 = existing content field (full text)

ALTER TABLE brain.entries ADD COLUMN IF NOT EXISTS l0_abstract text;
ALTER TABLE brain.entries ADD COLUMN IF NOT EXISTS l1_overview text;

COMMENT ON COLUMN brain.entries.l0_abstract IS 'L0: one-line summary for quick scan';
COMMENT ON COLUMN brain.entries.l1_overview IS 'L1: key points overview for planning';
