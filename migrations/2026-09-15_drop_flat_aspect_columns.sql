-- aspectScores stopped being a fixed object of ints in September 2026; it is now
-- an open map of {result, weight} per aspect, stored in session_aspect_scores.
--
-- These five columns were never populated on this install (the API returned an
-- empty aiAnalysis block for the whole time they existed), so dropping them
-- loses nothing. Verify before running:
--
--   SELECT count(aspect_conciseness) + count(aspect_clarity)
--        + count(aspect_empathy_tone) + count(aspect_understanding)
--        + count(aspect_resolution) AS valores_no_nulos
--   FROM session_ai_analysis;
--
-- Expected: 0. If it is not 0, copy those values into session_aspect_scores
-- first. Also drop them from schema.sql, or init-db will recreate them.
ALTER TABLE session_ai_analysis
    DROP COLUMN IF EXISTS aspect_conciseness,
    DROP COLUMN IF EXISTS aspect_clarity,
    DROP COLUMN IF EXISTS aspect_empathy_tone,
    DROP COLUMN IF EXISTS aspect_understanding,
    DROP COLUMN IF EXISTS aspect_resolution;
