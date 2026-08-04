-- Backfill de una sola vez para chats.synced_at.
--
-- Contexto: hasta el commit que introdujo SYNCED_AT_ON (sync/chats.py),
-- synced_at nunca se actualizaba en el ON CONFLICT -- quedaba con el valor del
-- DEFAULT now() del INSERT inicial, o sea "primera vez que vimos este chat".
-- Desde ese commit avanza cuando cambia un timestamp de la API, pero las filas
-- ya existentes arrastran el valor viejo hasta que el chat vuelva a tener
-- actividad. Este script las reescribe con la mejor aproximación disponible.
--
-- NO forma parte de schema.sql a propósito: schema.sql se re-aplica en cada
-- `init-db`, y esto tiraría synced_at hacia atrás hasta ~15 min (el valor real
-- es la corrida del cron que detectó el cambio, no el timestamp en sí).
-- Corrélo una vez y listo.
--
-- Uso:
--   psql "$DATABASE_URL" -f migrations/2026-08-04_backfill_chats_synced_at.sql
--
-- Antes de correrlo, para ver cuánto se mueve (no modifica nada):
--
--   SELECT count(*) AS filas,
--          min(base) AS mas_viejo,
--          max(base) AS mas_nuevo,
--          count(*) FILTER (WHERE base < synced_at) AS retroceden,
--          count(*) FILTER (WHERE base > synced_at) AS avanzan
--   FROM (
--       SELECT synced_at,
--              least(greatest(creation_time, last_session_creation_time,
--                             last_user_message_at), now()) AS base
--       FROM chats
--   ) t
--   WHERE base IS NOT NULL;

BEGIN;

-- whatsapp_window_close_at queda fuera a propósito: es el cierre de la ventana
-- de 24h de WhatsApp, un timestamp a futuro en todo chat activo, y además se
-- deriva de last_user_message_at, así que no aporta señal propia. El least(...,
-- now()) es un cinturón extra contra timestamps futuros de la API.
--
-- greatest() en Postgres ignora los NULL (devuelve NULL solo si todos lo son),
-- por eso no hace falta coalesce por columna -- pero sí el WHERE, porque
-- synced_at es NOT NULL y un chat sin ningún timestamp reventaría el UPDATE.
UPDATE chats
SET synced_at = least(
        greatest(creation_time, last_session_creation_time, last_user_message_at),
        now()
    )
WHERE greatest(creation_time, last_session_creation_time, last_user_message_at) IS NOT NULL;

COMMIT;

-- Los chats sin ningún timestamp de la API (poco frecuentes: registros
-- incompletos del lado de Botmaker) conservan su synced_at de primera vez.
-- Para encontrarlos:
--
--   SELECT chat_id, channel_id, synced_at FROM chats
--   WHERE greatest(creation_time, last_session_creation_time,
--                  last_user_message_at) IS NULL;
