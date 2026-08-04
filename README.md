# botmaker_sync

Extrae chats, sesiones (conversaciones), agentes, canales y contactos de la
[API de Botmaker](https://api.botmaker.com/v2.0) (solo GET) hacia Postgres.

## Configuración

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completá BOTMAKER_ACCESS_TOKEN y DATABASE_URL
python -m botmaker_sync init-db
```

`requirements.txt` trae solo lo necesario para correr el sync (pensado para
producción, donde no hace falta correr tests). Para desarrollo, instalá
también `requirements-dev.txt` (agrega `pytest`/`respx`):

```bash
pip install -r requirements-dev.txt
```

El `BOTMAKER_ACCESS_TOKEN` se genera en la
[página de integraciones de Botmaker](https://go.botmaker.com/#/integrations/api).

### Postgres (local, IP o dominio)

`DATABASE_URL` es la única configuración del destino, y el host puede ser
`localhost`, una IP o un dominio indistintamente. Para cualquier host que no sea
`localhost`, agregá `?sslmode=require` (o `verify-full` si tenés el
certificado de la CA) al final de la URL: sin eso, la conexión usa
`sslmode=prefer` por defecto, que cae a una conexión **sin cifrar** en
silencio si el servidor no ofrece TLS. Ver ejemplos en `.env.example`.

La conexión reintenta 3 veces ante fallas transitorias (timeout de 5s por
intento) antes de fallar -- pensado para un host de verdad en la red, no solo
un socket local.

## Uso

```bash
# Sync incremental: continúa desde el último watermark (la primera corrida no
# tiene límite inferior y usa la ventana por defecto de la API).
python -m botmaker_sync run

# Rango manual (NO avanza el watermark):
python -m botmaker_sync run --since 2026-01-01T00:00:00 --until 2026-01-02T00:00:00

# Solo algunas entidades:
python -m botmaker_sync run --entities channels,agents

# Incluir análisis de IA de la conversación (las sesiones abiertas ya vienen
# por defecto; usá --no-open-sessions para excluirlas):
python -m botmaker_sync run --include-ai-analysis
```

Corré el comando de nuevo cada vez que quieras datos nuevos -- con un
cron/Task Scheduler si querés que sea automático:

```cron
0 * * * * cd /path/to/botmaker && venv/bin/python -m botmaker_sync run >> sync.log 2>&1
```

### `--entities` y `--since`/`--until`: cómo funcionan

- **`--entities`**: lista separada por comas, subconjunto de
  `channels,agents,chats,sessions`. Filtra qué bloques de `cmd_run` corren.
  `contacts` no es un entity propio -- se sincroniza automáticamente como
  parte de `chats` (`__main__.py` llama `sync_contacts` inmediatamente
  después de `sync_chats`, con el set de chats tocados en esa misma
  corrida), así que para traer contactos hace falta incluir `chats`.
- **`--since`/`--until`**: si pasás cualquiera de los dos, `resolve_window()`
  devuelve ese valor tal cual (sin tocar el watermark guardado) en vez de
  calcular `watermark - 5min`. Tampoco se llama `set_watermark()` después
  (`manual_range=True` en `cmd_run`), así que el cursor incremental normal
  queda intacto -- podés reprocesar un rango pasado sin desincronizar las
  próximas corridas automáticas.
- **Omitir `--since` y pasar solo `--until`** replica exactamente el
  comportamiento de la primera corrida (sin límite inferior, deja que la API
  aplique su ventana reciente por defecto) pero fijando el límite superior.
  Ejemplo real: después de una corrida cuyo `sync_contacts` falló por
  timeout, el watermark de `chats` ya había quedado en
  `2026-06-24T16:51:24Z`. Para reconstruir el set de chats tocados y
  reintentar `contacts` sobre esos mismos 639 chats sin perder ni duplicar
  nada, se repitió la misma ventana:

  ```bash
  python -m botmaker_sync run --entities chats --until 2026-06-24T16:51:24Z
  ```

  `chats` se vuelve a upsertear (es idempotente, `ON CONFLICT DO UPDATE`),
  se reconstruye `touched` en memoria y `contacts` se reintenta para esos
  chats -- sin avanzar el watermark, porque `--until` activó el modo manual.
  Importante: la API de Botmaker no acepta un rango `from`/`to` mayor a 1 mes
  sin `long-term-search=true` (ver
  [Limitaciones conocidas](#limitaciones-conocidas)). Un `--since` muy lejano
  (ej. `2000-01-01`) ahora falla de entrada con un `ValueError` explícito,
  antes de gastar la llamada.

## Qué se sincroniza, y cómo

| Entidad | Endpoint | Alcance |
|---|---|---|
| channels | `GET /channels` | refresh completo en cada corrida (sin filtro de tiempo) |
| agents | `GET /agents` | refresh completo en cada corrida (sin filtro de tiempo) |
| chats | `GET /chats` | incremental, `from`/`to` por última actividad |
| sessions | `GET /sessions` | incremental, `from`/`to` por inicio de sesión, incluye mensajes/variables/eventos. El `from` se extiende hasta la sesión abierta más vieja, con tope de 2 días |
| contacts | `GET /contacts?channel-id=...` | **acotado**: solo contactos referenciados por los chats de esta corrida |

Las entidades incrementales (`chats`, `sessions`) guardan un watermark por
entidad en `sync_state`. El `to` de cada corrida se vuelve el `from` de la
siguiente, menos un solapamiento de 5 minutos para que el upsert absorba
duplicados de borde. Pasar `--since` y/o `--until` explícitamente cambia a un
rango manual puntual y no toca el watermark.

### Semántica de `synced_at`

No significa lo mismo en todas las tablas:

- **`chats`**: "última vez que cambió un timestamp de la API" — se actualiza solo
  si cambió alguno de `creation_time`, `last_session_creation_time`,
  `whatsapp_window_close_at` o `last_user_message_at` (`SYNCED_AT_ON` en
  `sync/chats.py`). El resto de las columnas (`queue_id`, `agent_id`, tags,
  variables) se refrescan siempre; solo `synced_at` está condicionado. El
  solapamiento de 5 minutos re-trae chats sin cambios en cada corrida, así que
  un `now()` incondicional sería solo la hora del cron.
- **`channels` / `agents` / `contacts` / `sessions`**: sigue siendo *first seen*,
  la hora del INSERT inicial. Las tres primeras son barridos completos — `now()`
  ahí estamparía todas las filas con la hora del cron, sin información. En
  `sessions` el único timestamp de la API a nivel de fila es `creation_time`,
  que nunca cambia.

### Sesiones abiertas y `closed_reason`

`sessions` tiene tres estados, no dos:

| `is_open` | `closed_reason` | Significa |
|---|---|---|
| `true` | `NULL` | En curso, hasta donde sabemos |
| `false` | `'event'` | **Observado**: la API mandó `conversation-close` |
| `false` | `'window_expired'` | **Asumido**: la sesión salió de la ventana de lookback |

`'window_expired'` **no es un dato que reporte Botmaker**. Cualquier métrica de
duración o abandono tiene que filtrar por `closed_reason`, o va a tratar como
cierres reales lo que son suposiciones nuestras.

Existe porque una parte grande de las conversaciones **nunca recibe
`conversation-close`**: se abandonan y Botmaker las deja abiertas para siempre
(medido el 2026-08-04: ~17 mil sesiones en ese estado). Dejarlas abiertas de
nuestro lado no es gratis, porque el sync extiende el `from` hasta la sesión
abierta más vieja: una sola sesión colgada ensancha la ventana de *todas* las
corridas, y con una población permanente de abandonadas eso no es un pico sino
el estado estable.

`OPEN_SESSION_LOOKBACK` (`sync/sessions.py`, 2 días) topea ese alcance. El valor
sale de medir, no de estimar: sobre 23801 cierres observados, el 98.8% ocurrió
dentro de 1 día del inicio de la sesión y el **100% dentro de 2 días** — nunca
se vio uno más tarde. Ampliar la ventana no recupera ni un cierre más y sí
multiplica el costo de BI (con 25 días eran ~30 mil sesiones con mensajes,
eventos y variables cada 15 minutos). Lo que queda afuera del tope se cierra con
`'window_expired'` al final de cada corrida.

Si el patrón de uso cambia, esa medición se rehace con:

```sql
SELECT count(*) FILTER (WHERE e.creation_time - s.creation_time < interval '1 day') AS en_1d,
       count(*) FILTER (WHERE e.creation_time - s.creation_time < interval '2 days') AS en_2d,
       count(*) AS total
FROM sessions s
JOIN session_events e ON e.session_id = s.id AND e.name = 'conversation-close'
WHERE NOT s.is_open;
```

Los chats que ya estaban en la tabla antes de este cambio arrastran su
`synced_at` de primera vez hasta que vuelvan a tener actividad. Para dejar una
línea base coherente hay un backfill de una sola vez (no está en `schema.sql`
porque `init-db` se re-aplica; ver los comentarios del archivo, incluida la
query de preview):

```bash
psql "$DATABASE_URL" -f migrations/2026-08-04_backfill_chats_synced_at.sql
```

## Flujo de ejecución

Cada archivo le corresponde un endpoint y una responsabilidad puntual.
`__main__.py` orquesta el orden; `client.py` es el único que habla HTTP;
`db.py` es el único que habla SQL; `models.py` es el único que conoce la
forma de las respuestas de Botmaker.

```mermaid
flowchart TD
    Start["python -m botmaker_sync run"] --> Conn["__main__.cmd_run\nconnect() + BotmakerClient()"]

    Conn --> Channels["sync/channels.py: sync_channels\nGET /channels"]
    Channels --> Agents["sync/agents.py: sync_agents\nGET /agents"]

    Agents --> RW1["db.resolve_window('chats')\nwatermark - 5min  ->  now()"]
    RW1 --> Chats["sync/chats.py: sync_chats\nGET /chats?from&to (paginado)"]
    Chats --> SetWM1["db.set_watermark('chats')"]
    SetWM1 --> Contacts["sync/contacts.py: sync_contacts\nGET /contacts?channel-id=...\n(uno por canal tocado)"]

    Contacts --> RW2["db.resolve_window('sessions')\nwatermark - 5min  ->  now()"]
    RW2 --> Sessions["sync/sessions.py: sync_sessions\nGET /sessions?from&to (paginado)"]
    Sessions --> SetWM2["db.set_watermark('sessions')"]
    SetWM2 --> End["Postgres: channels, agents, chats,\nsessions, contacts + tablas hijas"]
```

Por archivo:

- **`client.py`** (`BotmakerClient`) -- único punto que hace requests HTTP.
  `get_pages()` resuelve la paginación (sigue `nextPage`, que según el
  endpoint llega como URL absoluta o como token opaco) y `_get()` reintenta
  con backoff exponencial ante 429/5xx/timeouts. Cada `sync_*` itera
  `client.get_pages(...)`, página por página.
- **`models.py`** -- un `pydantic.BaseModel` por shape de respuesta
  (`ChatModel`, `SessionModel`, `ContactModel`, ...), mapeando los alias de
  la API (`camelCase`) a campos `snake_case`. `extra="ignore"` para que un
  campo nuevo de Botmaker no rompa el parseo.
- **`db.py`** -- único punto que habla SQL: `connect()` (con retry),
  `resolve_window()`/`set_watermark()` (watermark incremental),
  `upsert_rows()` (`INSERT ... ON CONFLICT DO UPDATE`) y
  `replace_children()` (`DELETE` + `INSERT` para listas hijas: tags,
  variables, teléfonos, mensajes, etc.).
- **`sync/channels.py` / `sync/agents.py`** -- los más simples: una página
  tras otra de `GET /channels` o `GET /agents`, upsert directo, sin filtro de
  tiempo ni estado.
- **`sync/chats.py`** -- pagina `GET /chats?from=...&to=...`, hace upsert de
  cada chat y de sus tablas hijas (`chat_tags`, `chat_variables`), y devuelve
  el set `{(channel_id, contact_id), ...}` de todo lo tocado en la corrida
  (`contact_id` acá es el id de plataforma, ej. el número de teléfono).
- **`sync/contacts.py`** -- no existe `GET /contacts/{id}`, así que recibe
  ese set de `sync_chats` y, agrupado por canal, pagina
  `GET /contacts?channel-id=...` buscando esos ids dentro de
  `chats[].platformContactId` de cada contacto (no en `item.id`, que es el id
  interno de Botmaker). Para de paginar un canal en cuanto encuentra todos
  los que buscaba en ese canal.
- **`sync/sessions.py`** -- igual a `chats.py` pero contra
  `GET /sessions?from=...&to=...`, con sub-listas de mensajes/eventos/análisis
  de IA (`replace_children` para cada una).

## Limitaciones conocidas

- **Sin `long-term-search`**: ese flag suma costo facturado por BI del lado
  de Botmaker, así que nunca se envía. Sin él la API rechaza con 400 cualquier
  rango `from`/`to` mayor a 1 mes, y si se omite `from` cae a su ventana por
  defecto (~1 día). Hay que correr el sync con la frecuencia suficiente para
  que ningún hueco supere el mes, o aceptar huecos si se salta un período.
- **Sesiones abandonadas**: una sesión sin evento `conversation-close` se
  cierra por expiración a los 2 días (ver
  [Sesiones abiertas y `closed_reason`](#sesiones-abiertas-y-closed_reason)).
  Con un umbral de 2 días el margen es chico: **si el cron queda parado más de
  48 horas**, las sesiones de ese hueco se marcan `'window_expired'` sin haber
  sido observadas nunca, aunque hubieran cerrado de verdad. Ante una caída
  larga, subí `OPEN_SESSION_LOOKBACK` antes de reanudar y bajalo después.
- **Alcance de contacts**: no existe un endpoint `/contacts/{id}`, así que
  "solo contactos nuevos" se implementa así: se junta cada par
  `(channel_id, contact_id)` visto en los chats de esta corrida, y luego se
  pagina `/contacts?channel-id=...` por canal, quedándose solo con los ids
  que coinciden. Si un contacto buscado nunca aparece en el listado de su
  canal, ese canal se escanea por completo una vez.
- El esquema se aplica con un `schema.sql` plano (`CREATE TABLE IF NOT
  EXISTS`), no con una herramienta de migraciones -- alcanza para el tamaño
  de este proyecto; volvé a correr `init-db` cada vez que cambie el esquema.

## Tests

```bash
pytest tests/ -v
```

Cubre la paginación (`nextPage` con URL y con token opaco), el retry ante
429, las funciones de mapeo de filas, y la lógica de la ventana de
watermark -- todo sin necesitar un Postgres real. Los caminos de
lectura/escritura a la base en sí se ejercitan corriendo `init-db` + `run`
contra una base de datos real.
