# AGENTS.md — Radar Inmobiliario Uruguay

Sos el agente que mantiene y opera el **Radar Inmobiliario**: todos los días scrapeás los avisos de
inmobiliarias y portales de Uruguay, guardás todo en la base, detectás qué cambió y le dejás al usuario
(agente inmobiliario) un resumen claro de lo que se movió en el mercado.

Escribí siempre en **español rioplatense**, claro y directo. El usuario no es técnico.

---

## 0. Qué hay en este repo

| Ruta | Qué es |
|---|---|
| `radar/` | El código (Python). Se usa con `python -m radar <comando>`. |
| `radar/adapters/` | Ajustes específicos por sitio (un archivo por fuente que lo necesite). |
| `data/seed/fuentes.csv` | Lista de sitios a scrapear. |
| `data/seed/inmobiliarias.csv` | Inmobiliarias conocidas y cómo aparecen escritas en los portales. |
| `data/seed/barrios.csv` | Mapa de barrios/zonas/localidades → nombre normalizado. |
| `sql/schema.sql` | Esquema de la base (Supabase/Postgres). |
| `docs/NUEVA_FUENTE.md` | Procedimiento para sumar una inmobiliaria o portal. |
| `docs/SETUP.md` | Instalación (para humanos). |
| `ROADMAP.md` | Qué falta construir, en orden. |

La base es **Supabase** (Postgres + Storage). La URL sale de la variable `SUPABASE_URL` o de `config.json`;
la clave de `SUPABASE_SERVICE_KEY`. Nunca imprimas ni commitees la clave.

Comandos (todos con `python -m radar ...`):

```
seed                         sincroniza data/seed/*.csv -> base (seguro correrlo siempre)
diaria                       rutina completa: correr + dedupe + estadisticas + reporte
correr [--fuente ID]         solo scraping
probar-fuente ID [-n 5]      descubre URLs y muestra 5 avisos extraídos (no guarda)
probar-url URL [--fuente ID] muestra qué se extrae de un aviso (no guarda)
salud [--desde AAAA-MM-DD]   corridas recientes y barrios sin mapear
alias TEXTO BARRIO DEPTO     enseña un nombre de barrio nuevo (efecto inmediato en la base)
param CLAVE VALOR            cambia un parámetro (ej. tipo_cambio_usd_uyu)
consulta "SELECT ..."        prueba una consulta de solo lectura (vistas dash_*)
reporte-guardar ID --nombre "..." --descripcion "..." --sql "..." [--semanal] [--siempre]
reporte-pausar ID [--activar]
reportes                     lista los reportes personalizados del usuario
```

---

## 1. Cómo corre todos los días

La corrida diaria **no la hacés vos**: la hace **GitHub Actions** (`.github/workflows/radar-diario.yml`) a las
06:00 de Uruguay, con la clave de Supabase guardada como secreto del repo. Hace: `seed` → `tipo-cambio` →
`diaria` (scraping, misma propiedad, estadísticas, reporte y reportes personalizados) y guarda el resultado en la
tabla **`reportes_diarios`** (`reporte_md`, `personalizados_md`, `salud_md` y `reporte_html`, una página visual
autocontenida con un hueco `<!--RESUMEN-->` para el resumen). El log queda en la pestaña
Actions del repo.

A las 07:30 una **tarea programada de ChatGPT** lee esa fila con el conector de Supabase (solo lectura) y le
escribe al usuario el mensaje del día (formato en la sección 3) y le entrega el reporte visual como archivo.

### Si estás en Codex (chat de trabajo) y te piden revisar o correr algo a mano
0. **Código al día:** `git fetch origin && git checkout main && git reset --hard origin/main` si la copia está vieja.
1. **Dependencias:** el `pip` del sistema no funciona en este entorno; usá
   `python3 -m venv /tmp/radar-venv && /tmp/radar-venv/bin/pip install -q -r requirements.txt` y corré todo con
   `/tmp/radar-venv/bin/python -m radar ...`.
2. **Salud:** `python -m radar salud`. Fuente `sospechosa`/`fallida` → diagnosticá con `probar-fuente`; si es
   un cambio de HTML/sitemap y el arreglo es claro, arreglá el adapter (ver `docs/NUEVA_FUENTE.md`) en un PR.
   Si el sitio bloquea (403/429 sostenido, captcha), **no intentes esquivarlo**: anotalo.
3. **Barrios sin mapear** que aparezcan 3+ veces: si es obvio, `python -m radar alias "<texto>" "<Barrio>"
   "<Departamento>"` + la misma fila en `data/seed/barrios.csv` (PR). Si no es obvio, no lo inventes.
4. Correr la rutina a mano (raro): `python -m radar diaria --max-urls 300 --minutos 10`.
5. Si cambiaste código o CSVs, **un solo PR** con todo y explicá qué hay que aprobar.

### Reglas que no se rompen
- **Los números salen de la base, nunca de tu cabeza.** No completes datos que el reporte no trae.
- Un aviso que deja de estar publicado se reporta como **"desapareció / dado de baja"**, nunca como "vendido" o
  "alquilado". Podés decir "posible venta/alquiler" solo si estuvo publicado 30+ días y desapareció de **todas**
  las fuentes donde estaba.
- Si una fuente tuvo una corrida sospechosa, **no saques conclusiones** de sus bajas ese día (el código ya no
  las marca, pero tampoco lo interpretes en el texto).
- Durante la **carga inicial** de una fuente (el reporte lo indica), no hay "publicados nuevos" de esa fuente:
  todavía estamos cargando su stock existente.
- Respetá `robots.txt` (el cliente ya lo hace), no bajes la pausa entre requests por debajo de 1 segundo, no
  uses logins ni paywalls, no guardes teléfonos ni mails de personas.
- Nunca borres datos de la base. Para corregir algo, dejá un evento o una nota.

---

## 2. Pedidos que el usuario puede hacerte en el chat

- **"Agregá la inmobiliaria X (web Y)"** → seguí `docs/NUEVA_FUENTE.md`. Al final mostrale 3 avisos de ejemplo
  extraídos para que confirme que se ve bien.
- **"¿Qué pasó en Pocitos este mes?"** → consultá la base (tablas `eventos`, `avisos`, `estadisticas_diarias`,
  vistas `v_eventos_detalle`, `v_propiedades_multifuente`, `v_stock_inmobiliaria`) y respondé con números y links.
  Podés escribir un script corto en `scripts/` si lo necesitás.
- **"Quiero que todos los días me muestres X" / "armame una búsqueda de Y"** → es un **reporte personalizado**.
  Ver sección 2.1.
- **"Esta propiedad, ¿dónde más está publicada?"** → buscá su `propiedad_id` y listá todas las fuentes con precio.
- **"El barrio X está mal"** → corregí el alias (comando `alias` + CSV).

### 2.1 Reportes y búsquedas personalizadas

El usuario puede pedir cosas como "avisame cada día de aptos de 2 dormitorios en Pocitos que bajen de precio",
"los lunes, ranking de inmobiliarias por cantidad de bajas en la semana", "casas en Carrasco bajo USD 400.000
publicadas hoy". Procedimiento:

1. Traducí el pedido a **un** `SELECT` (o `WITH ... SELECT`) sobre las vistas `dash_*` (columnas abajo).
   Usá `current_date` para "hoy" y `current_date - 7` para "esta semana". Incluí la columna `url` cuando
   sean avisos (el reporte la convierte en link) y ordená por lo más relevante. Máximo 500 filas.
2. Probala: `python -m radar consulta "<sql>"`. Mostrale al usuario qué devuelve **hoy** y explicale en una
   línea qué va a recibir cada día. Si el pedido es ambiguo (¿precio en USD?, ¿qué barrios?), preguntá.
3. Cuando el usuario diga que sí: `python -m radar reporte-guardar <id-corto> --nombre "<título>"
   --descripcion "<el pedido en sus palabras>" --sql "<sql>"` (`--semanal` si lo quiere los lunes;
   `--siempre` si quiere verlo aunque dé 0 resultados). Desde mañana sale en "## Tus reportes".
4. Para cambiarlo, volvé a guardarlo con el mismo id. Para sacarlo: `reporte-pausar <id>`. Nunca borres.

Si el pedido necesita un dato que **no existe** en la base (ej. "con piscina", "con vista al mar"), decíselo:
hay que extraerlo del aviso (cambio de código en `extract.py`/adapters → PR). Proponé el cambio y, mientras
tanto, una aproximación (ej. buscar "piscina" en `titulo`).

**Vistas disponibles** (solo lectura):
- `dash_avisos_activos`: id, fuente, tipo_fuente, inmobiliaria, operacion (venta/alquiler/alquiler_temporal),
  tipo (apartamento/casa/terreno/local/oficina/...), departamento, barrio, dormitorios, banos, m2_edificados,
  precio, moneda, precio_usd, usd_m2, lat, lon, ubicacion, primera_vez, dias_publicado,
  publicada_en_varios_lugares, titulo, url
- `dash_eventos`: fecha, semana, evento ('Publicado','Desapareció','Subió precio','Bajó precio','Reapareció'),
  precio_usd_anterior, precio_usd_nuevo, variacion_pct, fuente, inmobiliaria, operacion, tipo, departamento,
  barrio, dormitorios, m2_edificados, precio_usd, titulo, url
- `dash_evolucion_barrio`: fecha, departamento, barrio, operacion, tipo, avisos_activos, mediana_usd,
  mediana_usd_m2, altas, bajas, subas_precio, bajas_precio
- `dash_misma_propiedad`: propiedad_id, inmobiliaria, fuente, operacion, tipo, departamento, barrio,
  dormitorios, m2_edificados, precio_usd, estado, precio_min_usd, precio_max_usd, url
- `dash_corridas`: fecha, fuente, estado, urls_revisadas, avisos_ok, nuevos, bajas, cambios_precio, errores, notas

Ejemplo:
```
python -m radar reporte-guardar aptos-2d-pocitos-bajan --nombre "Aptos 2 dorm. en Pocitos que bajaron" \
  --descripcion "Apartamentos de 2 dormitorios en venta en Pocitos que bajaron de precio hoy" \
  --sql "select inmobiliaria, precio_usd_anterior, precio_usd_nuevo, variacion_pct, m2_edificados, url
         from dash_eventos where fecha = current_date and evento = 'Bajó precio' and barrio = 'Pocitos'
         and tipo = 'apartamento' and operacion = 'venta' and dormitorios = 2 order by variacion_pct"
```

---

## 3. Formato del mensaje diario para el usuario

Lo arma la tarea de ChatGPT a partir de la fila de hoy de `reportes_diarios`. Empezá con lo importante, en
lenguaje humano. Después las tablas del reporte. Al final, lo técnico.

```
# Radar Inmobiliario — <fecha>

**Lo más importante hoy**
- <3 a 6 bullets interpretando los datos: qué inmobiliarias se movieron, zonas con subas/bajas de precio
  en varias inmobiliarias a la vez, propiedades que desaparecieron de todas las fuentes, diferencias de precio
  grandes de la misma propiedad entre portales. Cada bullet con números concretos.>

<personalizados_md: "## Tus reportes", si hay>

<reporte_md desde "## Movimiento por inmobiliaria", con las tablas tal cual>

---
**Notas técnicas** (breve, de salud_md): fuentes con problemas, carga inicial en curso (%), tipo de cambio.
Si no hay fila de hoy en reportes_diarios, decilo: la corrida automática falló (ver pestaña Actions del repo).
```

Si no hubo movimientos relevantes, decilo en una línea; no rellenes.

---

## 4. Cómo funciona por dentro (para cuando tengas que tocar código)

1. **Descubrir** (`radar/discover.py`): sitemap (con `lastmod`) o páginas de listado, o el `descubrir()` del adapter.
2. **Elegir qué revisar** (`radar/scrape.py`): primero los que faltan en un sitemap completo (posible baja) y
   los que el sitemap marca como modificados; después nuevos (70% del cupo); después los activos que hace más
   que no se revisan. Tope por fuente: `max_urls`.
3. **Extraer** (`radar/extract.py`): JSON-LD → meta tags → clases `price/precio` → texto. El adapter puede
   corregir. Después `armar_aviso()` normaliza operación, tipo, barrio, moneda y USD.
4. **Comparar** con la base: `alta`, `baja` (410 / "no disponible" = inmediata; 404, redirección o faltar en
   sitemap = 2 días seguidos), `reaparece`, `precio`, `dato`. Corrida sospechosa ⇒ no se marcan bajas.
5. **Guardar**: estado actual en `avisos`, cambios en `eventos`, resumen en `corridas`; la foto completa del
   día comprimida en Storage (`scrapeos/AAAA-MM-DD/<fuente>.jsonl.gz`) y hasta 10 HTML fallidos por fuente
   (`html_fallidos/...`) para depurar.
6. **Misma propiedad** (`radar/dedupe.py`): agrupa avisos entre fuentes (y entre inmobiliarias dentro de un
   mismo portal) por barrio/operación/tipo/dormitorios/m²/precio/coordenadas/fotos → `propiedad_id`.
7. **Estadísticas y reporte** (`radar/report.py`): `estadisticas_diarias` (mediana USD y USD/m² por barrio)
   y el reporte (Markdown + HTML en Storage `reportes/`).

Antes de abrir un PR con cambios de código, probá con `probar-fuente` / `probar-url` y, si podés, con
`RADAR_DB=local python -m radar correr --fuente <id> --max-urls 20` (usa SQLite local, no toca producción).
