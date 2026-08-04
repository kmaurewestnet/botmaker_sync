-- Arreglo de una sola vez para las sesiones que quedaron colgadas en is_open=true.
--
-- Contexto: `d78ab6b` extendía el `from` de GET /sessions hasta el MIN(creation_time)
-- de las sesiones abiertas, sin ningún tope. Una sesión del 2026-06-24 que nunca se
-- cerró llevó el rango pedido a 41 días; la API rechaza rangos > 1 mes sin
-- long-term-search, así que devolvía 400. El retry que había enmascaraba el error
-- borrando el `from`, la API caía a su ventana por defecto (~1 día) y desde entonces
-- ninguna sesión se cerró retroactivamente. Se ve en los datos: hasta ~24-jul había
-- 1-4 sesiones abiertas por día, y de ahí en adelante saltan a cientos.
--
-- El código ya topea el lookback (OPEN_SESSION_LOOKBACK en sync/sessions.py) y hace
-- el barrido automático, pero solo hacia adelante. Este script limpia lo ya envenenado.
--
-- REQUIERE `python -m botmaker_sync init-db` ANTES: usa la columna closed_reason.
--
-- Uso:
--   psql "$DATABASE_URL" -f migrations/2026-08-04_close_stale_open_sessions.sql
--
-- Preview antes de correrlo (no modifica nada):
--
--   SELECT count(*) FILTER (WHERE EXISTS (
--              SELECT 1 FROM session_events e
--              WHERE e.session_id = s.id AND e.name = 'conversation-close')) AS cierre_perdido,
--          count(*) FILTER (WHERE creation_time < now() - interval '25 days') AS expiradas,
--          count(*) AS total_abiertas
--   FROM sessions s WHERE s.is_open;

BEGIN;

-- 1. Cierre real que ya teníamos y no habíamos aplicado: el evento está en la
--    tabla pero is_open quedó en true (la sesión se trajo antes de cerrarse y el
--    upsert posterior nunca llegó). Va primero para que estas queden 'event' y no
--    se las lleve por delante el paso 2.
UPDATE sessions s
SET is_open = false, closed_reason = 'event'
WHERE s.is_open
  AND EXISTS (
      SELECT 1 FROM session_events e
      WHERE e.session_id = s.id AND e.name = 'conversation-close'
  );

-- 2. Fuera de la ventana de lookback: la API ya no las devuelve, así que su cierre
--    no se puede observar nunca. Se asumen cerradas, marcadas como tales.
--    El intervalo debe coincidir con OPEN_SESSION_LOOKBACK en sync/sessions.py.
UPDATE sessions
SET is_open = false, closed_reason = 'window_expired'
WHERE is_open
  AND creation_time < now() - interval '25 days';

-- 3. Backfill de motivo para las que ya estaban cerradas antes de existir la
--    columna. Supuesto: hasta este cambio el ÚNICO camino a is_open=false era el
--    evento conversation-close, así que todas son cierres observados. Si a futuro
--    aparecen otros motivos, este paso deja de ser correcto -- por eso el script
--    no vive en schema.sql, que se re-aplica en cada init-db.
UPDATE sessions
SET closed_reason = 'event'
WHERE NOT is_open AND closed_reason IS NULL;

COMMIT;

-- Después de correrlo, la próxima corrida del sync debería pedir una ventana corta
-- (watermark - 5 min, o la sesión abierta más vieja dentro de los 25 días) y no
-- volver a dar 400. Verificación:
--
--   SELECT closed_reason, count(*) FROM sessions GROUP BY 1;
--   SELECT count(*) FROM sessions WHERE is_open;
