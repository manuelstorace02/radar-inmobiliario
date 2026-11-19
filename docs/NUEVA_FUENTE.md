# Cómo sumar una fuente (inmobiliaria o portal)

Objetivo: que el radar descubra todos los avisos del sitio y extraiga bien precio, operación, tipo, barrio,
m² y dormitorios. Tiempo típico: 10–30 minutos por sitio.

## 1. Mirar el sitio

1. `robots.txt` (`https://sitio/robots.txt`):
   - Si bloquea todo (`Disallow: /` para `*`), **no se scrapea**. Agregala igual con `activa=no`,
     `estado=bloqueada` y una nota.
   - Anotá si hay líneas `Sitemap:`.
2. Abrí 2 o 3 avisos y mirá el formato de sus URLs. Ej.: `/propiedad/12345-apto-pocitos`, `/inmueble/abc-123`.
   El **patrón** tiene que matchear SOLO avisos individuales, no listados ni páginas institucionales.
3. Fijate si hay sitemap con los avisos (`/sitemap.xml`, `/sitemap_index.xml`, `/wp-sitemap.xml`).
   - Si el sitemap tiene **todos** los avisos activos → `modo=sitemap`, `sitemap_completo=si`.
   - Si no hay sitemap útil → `modo=listado` con las páginas de listado en `listado_urls`, por ejemplo
     `https://sitio/venta?page={page} | https://sitio/alquiler?page={page}`.

**Atajo:** muchas inmobiliarias usan la misma plataforma web (mismo HTML, mismas URLs). Si ves que el sitio
se parece a otro que ya funciona (mismo patrón de URL, mismo "powered by"), reutilizá su adapter.

## 2. Agregar la fila

En `data/seed/fuentes.csv` (y, si es una inmobiliaria, también en `data/seed/inmobiliarias.csv`):

```
id,nombre,tipo,inmobiliaria_id,url_base,modo,patron_aviso,listado_urls,sitemap_completo,max_urls,delay_seg,activa,estado,notas
web-ejemplo,Inmobiliaria Ejemplo (web),inmobiliaria,ejemplo,https://www.ejemplo.com.uy,sitemap,/propiedad/\d+,,si,1500,1.5,si,nueva,
```

- `id`: corto, en minúsculas, con guiones. Para webs propias usá `web-<inmobiliaria>`.
- `inmobiliaria_id`: el `id` de la fila en `inmobiliarias.csv`. En `alias` de esa fila poné cómo aparece su
  nombre en los portales (separado por `;`), así sus avisos de Gallito/InfoCasas quedan atribuidos a ella.

Después: `python -m radar seed`.

## 3. Probar

```
python -m radar probar-fuente web-ejemplo -n 5
```

Chequeá:
- Que la cantidad de URLs descubiertas tenga sentido (comparala con lo que dice el sitio).
- Que en los 5 avisos estén bien `operacion`, `tipo`, `barrio`, `precio`, `moneda`, `m2_edificados`,
  `dormitorios`. Si un barrio sale vacío, mirá `ubicacion_raw` y, si hace falta, enseñalo con `alias`.

## 4. Si la extracción falla: adapter

Copiá `radar/adapters/_ejemplo.py` a `radar/adapters/<id con _ en vez de ->.py` y completá solo lo que falta:

- `es_aviso(url)` si el patrón de URL no alcanza.
- `extraer(html, url, generico)` para corregir campos (precio, ubicación, anunciante, id_externo...).
- `async descubrir(cli, fuente)` si el descubrimiento es especial (API, listados con JSON, etc.). Puede devolver
  un `set` de URLs o un `dict {url: lastmod}`.

Probá con `probar-url <url> --fuente <id>` hasta que salga bien.

## 5. Dejarla andando

- `estado=ok` en el CSV cuando funcione.
- Todo (CSV + adapter) en un PR. Mientras el PR no se apruebe, la fuente igual queda en la base por el `seed`,
  pero sin el adapter.
- La primera semana la fuente está en **carga inicial** (no genera "altas" hasta conocer el 90% de su stock).
