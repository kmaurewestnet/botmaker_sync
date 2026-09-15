-- Borra las filas de session_ai_analysis que no dicen nada: ni flag, ni
-- summary, ni score, ni aspectos. Son las del período en que la API devolvía
-- `aiAnalysis: {}` por un bug de Botmaker (corregido en septiembre 2026).
--
-- NO toca las filas con does_not_meet_criteria = true: esas registran que
-- Botmaker evaluó la conversación y decidió no puntuarla, que es distinto de
-- no haberla analizado nunca.
--
-- El colector ya no las escribe (SessionAiAnalysisModel.has_content), así que
-- esto no se repuebla solo.

-- 1) Mirá qué se va a borrar ANTES de borrar:
--
--   SELECT count(*) FROM session_ai_analysis a
--    WHERE a.does_not_meet_criteria IS NULL
--      AND a.summary IS NULL AND a.name IS NULL
--      AND a.justification IS NULL AND a.quality_score IS NULL
--      AND NOT EXISTS (SELECT 1 FROM session_aspect_scores sc
--                       WHERE sc.session_id = a.session_id);
--
-- 2) Si el número te cierra (rondaba las 20.500 filas), corré el DELETE:

BEGIN;

DELETE FROM session_ai_analysis a
 WHERE a.does_not_meet_criteria IS NULL
   AND a.summary IS NULL
   AND a.name IS NULL
   AND a.justification IS NULL
   AND a.quality_score IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM session_aspect_scores sc WHERE sc.session_id = a.session_id
   );

-- Revisá el número de filas borradas que reporta el DELETE y recién ahí:
COMMIT;
-- (o ROLLBACK; si no te cierra)
