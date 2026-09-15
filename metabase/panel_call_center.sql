-- Consultas para reproducir en Metabase el panel de agentes de Botmaker.
--
-- Fuente principal: agent_metrics (una fila por conversación+agente, con cola,
-- tiempos y estado). Para el estado vivo de la cola se usa chats, que refleja
-- la asignación actual, y agents para la disponibilidad del operador.
--
-- Variable {{queue}}: opcional en todas. Dejala vacía para ver todas las colas.
-- El valor es el identificador de cola tal como lo devuelve la API (a veces un
-- nombre como 'Comercial', a veces un id opaco como 'JFT92SE9KN4HTXQH9YPE').
--
-- OJO con los mapeos de tiempos (FRT/ASA/ART/AHT): Botmaker no documenta con
-- qué campo calcula cada sigla del panel. Los de abajo son la lectura más
-- razonable de los nombres, pero hay que calibrarlos comparando contra el panel
-- antes de publicarlos como oficiales.


-- ============================================================
-- 1. Tarjetas de arriba (métricas de hoy, una fila)
-- ============================================================
-- Para una card tipo "Number" en Metabase, duplicá esta consulta dejando una
-- sola columna en el SELECT.
SELECT
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'open')            AS conversaciones_en_curso,
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'closed')          AS resueltas_hoy,
    count(DISTINCT m.session_id)                                                     AS total_hoy,
    count(DISTINCT m.session_id) FILTER (WHERE m.closed_with_no_messages > 0)        AS abandonadas_aprox,
    count(DISTINCT m.agent_id) FILTER (WHERE m.agent_id <> '')                       AS agentes_con_actividad
FROM agent_metrics m
WHERE m.session_creation_time >= current_date
  [[AND m.queue = {{queue}}]];


-- ============================================================
-- 2. Estado vivo de la cola: en espera y pospuestas
-- ============================================================
-- No sale de agent_metrics sino de chats, que guarda la asignación actual:
-- un chat con cola y sin agente está esperando; uno con on_hold_agent_id está
-- pospuesto.
SELECT
    -- COALESCE porque la API puede mandar cadena vacía en vez de omitir el
    -- campo, y el sync la guarda tal cual
    count(*) FILTER (WHERE COALESCE(c.agent_id, '') = '')            AS chats_en_espera,
    count(*) FILTER (WHERE COALESCE(c.on_hold_agent_id, '') <> '')   AS pospuestas
FROM chats c
WHERE c.queue_id IS NOT NULL
  [[AND c.queue_id = {{queue}}]];


-- ============================================================
-- 3. Agentes libres / capacidad
-- ============================================================
-- "Capacidad 6/5" del panel = conversaciones en curso / slots del agente.
-- Un agente está libre si está online y todavía no llenó sus slots.
SELECT
    count(*)                                                    AS agentes_online,
    count(*) FILTER (WHERE a.en_curso < COALESCE(a.slots, 0))   AS agentes_libres
FROM (
    SELECT
        ag.id,
        ag.slots,
        count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'open') AS en_curso
    FROM agents ag
    LEFT JOIN agent_metrics m
           ON m.agent_id = ag.id
          AND m.session_creation_time >= current_date
    WHERE ag.is_online
    GROUP BY ag.id, ag.slots
) a;


-- ============================================================
-- 4. Tiempos agregados (FRT / ASA / ART / AHT) de hoy
-- ============================================================
-- Solo sobre conversaciones cerradas: las abiertas traen "-" en todos los
-- tiempos y entran como NULL, así que avg() las ignora sola.
SELECT
    round(avg(m.from_op_assigned_to_op_first_response))          AS frt_seg,
    round(avg(m.from_queue_asign_to_op_assigned))                AS asa_seg,
    round(avg(m.op_response_time))                               AS art_seg,
    round(avg(m.avg_attending_time))                             AS aht_seg,
    -- las mismas, formateadas como duración para leer de un vistazo
    (round(avg(m.from_op_assigned_to_op_first_response)) * interval '1 second') AS frt,
    (round(avg(m.from_queue_asign_to_op_assigned))       * interval '1 second') AS asa,
    (round(avg(m.op_response_time))                      * interval '1 second') AS art,
    (round(avg(m.avg_attending_time))                    * interval '1 second') AS aht
FROM agent_metrics m
WHERE m.session_status = 'closed'
  AND m.session_creation_time >= current_date
  [[AND m.queue = {{queue}}]];


-- ============================================================
-- 5. Tabla por agente (el bloque principal del panel)
-- ============================================================
SELECT
    COALESCE(ag.name, m.agent_name)     AS agente,
    CASE
        WHEN ag.is_online THEN COALESCE(ag.status, 'Online')
        WHEN ag.id IS NULL THEN NULL
        ELSE 'Desconectado'
    END                                 AS estado,
    (round(avg(m.from_op_assigned_to_op_first_response)) * interval '1 second') AS frt,
    (round(avg(m.op_response_time))                      * interval '1 second') AS art,
    (round(avg(m.avg_attending_time))                    * interval '1 second') AS aht,
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'open')   AS en_curso,
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'closed') AS resueltas_hoy,
    ag.slots                            AS capacidad,
    round(avg(aa.quality_score), 2)     AS quality_score
FROM agent_metrics m
LEFT JOIN agents ag ON ag.id = m.agent_id
LEFT JOIN session_ai_analysis aa ON aa.session_id = m.session_id
WHERE m.session_creation_time >= current_date
  AND m.agent_id <> ''
  [[AND m.queue = {{queue}}]]
GROUP BY 1, 2, ag.slots
ORDER BY en_curso DESC, resueltas_hoy DESC;


-- ============================================================
-- 6. Resumen por cola (no está en el panel, pero es lo que pediste primero)
-- ============================================================
SELECT
    m.queue                                                                  AS cola,
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'open')    AS abiertas,
    count(DISTINCT m.session_id) FILTER (WHERE m.session_status = 'closed')  AS cerradas,
    count(DISTINCT m.session_id)                                             AS total,
    count(DISTINCT m.agent_id) FILTER (WHERE m.agent_id <> '')               AS agentes,
    (round(avg(m.avg_attending_time)) * interval '1 second')                 AS aht
FROM agent_metrics m
WHERE m.session_creation_time >= current_date
GROUP BY 1
ORDER BY total DESC;
