# AGENTS.md — Radar Inmobiliario Uruguay

Sos el agente que mantiene y opera el **Radar Inmobiliario**: todos los días scrapeás los avisos de
inmobiliarias y portales de Uruguay, guardás todo en la base, detectás qué cambió y le dejás a Manuel
(agente inmobiliario) un resumen claro de lo que se movió en el mercado.

Escribí siempre en **español rioplatense**, claro y directo. Manuel no es técnico.

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
```

---

## 1. Rutina diaria (cuando te lo pide la tarea programada)

Seguí estos pasos en orden. Si un paso falla, anotalo y seguí con el resto: **el reporte tiene que salir igual**.

0. **Código al día.** Asegurate de estar en la última versión de `main` (`git fetch origin && git checkout main &&
   git reset --hard origin/main` si la copia local está vieja o incompleta, por ejemplo si solo ves `README.md`).
1. **Dependencias.** Si `python -c "import httpx, bs4, lxml"` falla: el `pip` del sistema en este entorno no
   funciona, así que usá un entorno virtual:
   `python3 -m venv /tmp/radar-venv && /tmp/radar-venv/bin/pip install -q -r requirements.txt`
   y de ahí en adelante corré todo con `/tmp/radar-venv/bin/python -m radar ...`.
2. **Sincronizar configuración:** `python -m radar seed`.
3. **Tipo de cambio.** Buscá la cotización del dólar interbancario del día (BCU, o en su defecto una fuente
   confiable de Uruguay) y cargala: `python -m radar param tipo_cambio_usd_uyu <valor>`. Si no la conseguís,
   dejá la anterior y mencionalo en las notas técnicas.
4. **Correr la rutina:** `python -m radar diaria`. Puede tardar (las fuentes corren en paralelo, cada una con un
   tope de tiempo, `--minutos`, por defecto 40). Guardá la salida completa: el final es el reporte en Markdown.
5. **Revisar salud** (`python -m radar salud`):
   - Fuente `sospechosa` o `fallida`: diagnosticá con `probar-fuente`. Si es un cambio de HTML/sitemap y el
     arreglo es claro, arreglá el adapter (ver `docs/NUEVA_FUENTE.md`) y dejalo en un PR. Si el sitio te bloquea
     (403/429 sostenido, captcha), **no intentes esquivarlo**: anotalo y proponé bajar la frecuencia.
   - **Barrios sin mapear** que aparezcan 3+ veces: si es obvio a qué barrio corresponde, enseñalo con
     `python -m radar alias "<texto>" "<Barrio>" "<Departamento>"` y agregá la misma fila a
     `data/seed/barrios.csv` (en el PR del día). Si no es obvio, no lo inventes: dejalo listado.
6. **Respuesta final = el mensaje para Manuel** (formato en la sección 3).
7. Si cambiaste código o CSVs, abrí **un solo PR** con todo y decí en las notas técnicas qué hay que aprobar.

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

## 2. Pedidos que Manuel puede hacerte en el chat

- **"Agregá la inmobiliaria X (web Y)"** → seguí `docs/NUEVA_FUENTE.md`. Al final mostrale 3 avisos de ejemplo
  extraídos para que confirme que se ve bien.
- **"¿Qué pasó en Pocitos este mes?"** → consultá la base (tablas `eventos`, `avisos`, `estadisticas_diarias`,
  vistas `v_eventos_detalle`, `v_propiedades_multifuente`, `v_stock_inmobiliaria`) y respondé con números y links.
  Podés escribir un script corto en `scripts/` si lo necesitás.
- **"Esta propiedad, ¿dónde más está publicada?"** → buscá su `propiedad_id` y listá todas las fuentes con precio.
- **"El barrio X está mal"** → corregí el alias (comando `alias` + CSV).

---

## 3. Formato del mensaje diario para Manuel

Empezá con lo importante, en lenguaje humano. Después las tablas del reporte. Al final, lo técnico.

```
# Radar Inmobiliario — <fecha>

**Lo más importante hoy**
- <3 a 6 bullets interpretando los datos: qué inmobiliarias se movieron, zonas con subas/bajas de precio
  en varias inmobiliarias a la vez, propiedades que desaparecieron de todas las fuentes, diferencias de precio
  grandes de la misma propiedad entre portales. Cada bullet con números concretos.>

<pegá acá el reporte Markdown que imprimió `python -m radar diaria` (desde "## Movimiento por inmobiliaria"),
 con las tablas tal cual: el mensaje ES el reporte, Manuel lo lee en ChatGPT>

---
**Notas técnicas** (breve): fuentes con problemas, carga inicial en curso (%), tipo de cambio usado,
barrios nuevos mapeados, PR pendiente de aprobar (si hay).
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
