# Roadmap — Radar Inmobiliario

Estado al 18/11/2026: la base del sistema está lista y probada contra Gallito (descubrimiento por sitemap de
~54.000 avisos, extracción, detección de bajas y cambios de precio, misma propiedad entre inmobiliarias,
reporte diario).

## Fase 0 — Puesta en marcha (esta semana)
- [x] Esquema de base, scraper genérico, mapa de barrios (62 barrios de Montevideo + zonas + interior).
- [x] Gallito por sitemap (con `lastmod` para priorizar avisos modificados y sitemap completo para detectar bajas).
- [ ] Crear Supabase, correr `sql/schema.sql`, bucket `scrapeos` (ver `docs/SETUP.md`).
- [x] Corrida diaria en GitHub Actions + tarea de ChatGPT que lee `reportes_diarios` (las tareas
      programadas de ChatGPT no pueden ejecutar código: se comprobó el 18/11).
- [ ] Primera corrida manual supervisada.

## Fase 1 — Cobertura de portales (semanas 1–2)
- [x] **InfoCasas** en modo "listado completo" (`radar/adapters/infocasas.py`): ~110.000 avisos/día desde las
      páginas de búsqueda, sin abrir cada aviso (precio de todo el stock todos los días).
- [x] **Gallito en modo híbrido** (`radar/adapters/gallito.py`, 23/11): listado de búsquedas partido en cortes
      (operación → tipo → departamento → barrio → dormitorios, porque cada búsqueda corta en 50 páginas) con el
      precio de ~50.000 avisos por día; sitemap para las bajas; avisos sueltos para completar la inmobiliaria.
- [x] **Panel a medida desde el chat**: tabla `panel_secciones` + conector de escritura
      (`supabase/functions/radar-escritura`) con secciones, reportes, preferencias y pedidos.
- [ ] **Directorio de inmobiliarias**: armar `data/seed/inmobiliarias.csv` a partir de
      `gallito.com.uy/sitemaps/agencies.xml` y del sitemap `inmobilirias-constructoras` de InfoCasas
      (nombre, web propia, alias). Con eso los avisos de portales quedan atribuidos a cada inmobiliaria.
- [ ] Ajustar el mapa de barrios con lo que vaya apareciendo en `barrios_sin_mapear`.

## Fase 2 — Webs propias de las inmobiliarias (semanas 2–6)
- [ ] Sumar las webs propias, empezando por las 30 inmobiliarias con más avisos en portales.
- [ ] Detectar plataformas comunes (mismo proveedor web ⇒ mismo adapter) para sumar decenas de sitios de una vez.
- [ ] Reporte "web propia vs. portal": misma propiedad con precio distinto en la web de la inmobiliaria y en
      los portales (ya lo calcula `v_propiedades_multifuente`; falta tener las webs cargadas).
- [ ] **Mercado Libre**: usar la API oficial (crear app en developers.mercadolibre.com.uy, guardar credenciales
      como secretos). Su robots.txt bloquea agentes de IA, así que no se scrapea el sitio.

## Fase 3 — Producto para el usuario (mes 2)
- [x] Vistas `dash_*` y usuario de solo lectura para el dashboard (`sql/dashboard.sql`).
- [ ] Armar el tablero en **Looker Studio** siguiendo `docs/DASHBOARD.md`.
- [x] Alertas personalizadas: reportes guardados + preferencias `pref_*` (desde el chat, conector de escritura).
- [ ] Vigilar el tamaño: con ~160.000 avisos el plan gratis de Supabase (500 MB de base, 1 GB de Storage) puede
      quedar justo en unos meses. Opciones: comprimir/rotar las fotos diarias de Storage o pasar al plan Pro.
- [ ] Resumen semanal (lunes) además del diario.
- [ ] Historial de precios por propiedad (Gallito expone `priceHistory` en sus datos; aprovecharlo).

## Fase 4 — Análisis (mes 3+)
- [ ] Tiempo en mercado por barrio/tipología (de `primera_vez` a la baja).
- [ ] Tasa de rotación: bajas / stock por zona e inmobiliaria.
- [ ] Ranking de inmobiliarias por stock, movimiento y nivel de precios vs. la mediana del barrio.
- [ ] Archivar `scrapeos/` viejos (el plan gratis de Storage es 1 GB).

## Riesgos conocidos
- **Términos de uso**: los portales en general no permiten la extracción automatizada aunque su robots.txt
  deje pasar a crawlers genéricos. El radar se identifica con un User-Agent propio, va despacio y respeta
  robots.txt, pero el riesgo de bloqueo o reclamo existe. Para Mercado Libre se usa la API.
- **IPs de nube**: algunos sitios bloquean IPs de centros de datos. Si una fuente queda `bloqueada`, no se
  intenta esquivar.
- **Tiempo por corrida**: si la tarea de Codex tiene un límite menor al esperado, bajar `--minutos` o `max_urls`.
