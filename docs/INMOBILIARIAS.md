# Webs propias de inmobiliarias: qué sumar y cómo

Relevamiento del 23/11/2026. Datos en `data/seed/inmobiliarias_candidatas.csv` (130 inmobiliarias).

## Cómo se armó

- **Tamaño (`avisos_portal`)**: cantidad de avisos que cada inmobiliaria tiene en InfoCasas (`IC:n`), leída de su
  página de inmobiliaria en el portal. Se revisaron las 773 inmobiliarias del directorio de InfoCasas (suman
  ~108.000 avisos, contando venta, alquiler y temporada). El sitemap de agencias de Gallito no sirve como
  directorio: solo lista 6 agencias.
- **Web**: casi ninguna inmobiliaria pone su web en los portales. Se adivinó el dominio a partir del nombre
  (`nombre.com.uy`, `nombrepropiedades.com`, etc.) y se confirmó abriendo la home (título y texto con el nombre).
  Unos pocos matches quedaron como dudosos y están marcados en `notas`.
- **Plataforma**: sacada del HTML de la home (scripts, CSS, créditos al pie, URLs). Por cada sitio se pidieron la
  home, `robots.txt` y el sitemap (como mucho 4 o 5 pedidos por sitio).
- Quedaron afuera las que tienen menos de 100 avisos, salvo las grandes marcas y los nombres que pediste revisar.

## Plataformas, por cantidad de inmobiliarias

| Plataforma | Inmobiliarias | Avisos en InfoCasas | % de avisos | Cómo se ve |
|---|---:|---:|---:|---|
| **TeraCRM** (Sierra, uruguayo) | 42 | 31.500 | 38 % | `/apartamentos/en-venta/`, aviso en `/Apartamento/12345`, créditos a sierra.com.uy / tera.com.uy / ri.com.uy |
| A medida (custom) | 20 | 17.100 | 21 % | Next.js, React, PHP, Shopify, Lovable... uno distinto cada vez |
| **Tokko Broker** (argentino) | 12 | 8.400 | 10 % | `static.tokkobroker.com`, aviso en `/p/2671638-...`, listado en `/Venta` y `/Propiedades` |
| **nai** (Sodio, uruguayo) | 14 | 4.900 | 6 % | `/propiedades/Venta/U$S/Sin_precio_mínimo/...`, aviso en `/propiedad/97` |
| WordPress, otros temas | 6 | 4.000 | 5 % | Estatik, WpResidence, temas propios |
| **Cliksi** | 5 | 4.000 | 5 % | `cliksi-saas-base.s3...`, JSON-LD |
| **WordPress + Houzez** (4 cargadas desde el CRM 2clics) | 7 | 3.600 | 4 % | `wp-content/themes/houzez` |
| Xintel (argentino) | 5 | 3.700 | 4 % | `cdn-images.xintelweb.com`, `/propiedades?ope=V` |
| Sublime Solutions | 3 | 1.400 | 2 % | |
| Casasweb / RE.uy (portal con webs de marca blanca) | 2 (9 en la lista completa) | 700 | 1 % | `detalle.aspx?id=...` |
| InfoCasas Sitios ("Powered by InfoCasas") | 2 | 400 | 0,5 % | el stock es el mismo de InfoCasas, no suman nada |
| Wasi, Kraken/Medusa, Massari, Inmobiliaria YA | 1 c/u | — | — | |
| Plataforma propia de la red | 4 | — | — | RE/MAX, Century 21, Engel & Völkers, Sotheby's (NexusProps) |
| Sin web, bloqueada o sin identificar | 4 | 1.300 | 2 % | |

No apareció ningún sitio con Wasi en cantidad, ni con Inmovilla, Witei, Kiteprop o 2clics como web propia: 2clics
aparece solo como el CRM que carga los avisos en sitios WordPress+Houzez.

## Qué adapters hacer primero

Con los primeros 5 se cubren **80 de las 130 inmobiliarias (62 %) y ~64 % de sus avisos**. Con los primeros 3 ya
se cubren 68 inmobiliarias y ~55 % de los avisos.

1. **TeraCRM** (42 inmobiliarias, 38 % de los avisos, casi todas en Maldonado / Punta del Este). Es la plantilla
   más pareja de todas. El listado viene armado en el HTML (30 avisos por página, con precio y link a
   `/Apartamento/ID`). Casi nunca hay `robots.txt` ni sitemap, así que conviene `modo=listado` recorriendo las
   categorías (`/apartamentos/en-venta/`, `/casas/en-venta/`, `.../en-alquiler/`). Ojo: algunos sitios (Luxury
   Punta) prohíben en robots el parámetro de paginación `?pagina=`.
2. **Tokko Broker** (12 inmobiliarias, 10 %; Montevideo + Terramar + KW). El listado `/Venta` o `/Propiedades`
   trae los links `/p/ID-slug`. El precio está en la ficha (`<div class="operation-val">`), no en el listado.
   Algunos tienen sitemap (Terramar, `propiedad.php?ref=`, la plantilla vieja).
3. **nai** (14 inmobiliarias acá, 22 en la lista completa; casi todas en Montevideo). El listado se carga con JS
   (`buscador.js`), pero varios publican un sitemap con `/propiedad/ID` (Kosak tiene ~960, Casa Victorica ~400).
   Hay que usar el sitemap y abrir cada ficha.
4. **WordPress + Houzez** (7). Es el más barato de hacer: la API REST de WordPress devuelve los avisos con precio
   (`/wp-json/wp/v2/properties?per_page=100`, campo `property_meta.fave_property_price`; en workplace.com.uy da
   `X-WP-Total: 456`). No hace falta parsear HTML.
5. **Cliksi** (5, pero 4.000 avisos: Vizcaya, Toribio Achával, Coldwell Banker...). Trae JSON-LD. Su sitemap
   **no sirve**: mezcla URLs de otro cliente (megaespacios.com.ar). Hay que recorrer el listado `/propiedades`.

Después vendría **Xintel** (5 sitios, 3.700 avisos, entre ellos Beba Páez Vilaró con 2.347).

Los sitios a medida que tienen un sitemap bueno (Caetano con ~5.200 avisos, ALDA ~1.900, Antonio Mieres ~1.100,
Sotheby's ~1.200, RE/MAX ~4.350) entran sin adapter, con el `modo=sitemap` genérico que ya existe.

## Las primeras 15 para sumar

| # | Inmobiliaria | Avisos IC | Plataforma | Por qué |
|---:|---|---:|---|---|
| 1 | Nicolás De Módena | 5.674 | TeraCRM | la más grande del portal; sirve para estrenar el adapter Tera |
| 2 | Vizcaya | 2.950 | Cliksi | la más grande de Montevideo |
| 3 | Arquibrokers | 2.390 | Tokko | Montevideo; para estrenar el adapter Tokko |
| 4 | Caetano | 2.077 | a medida (Next.js) | sitemap con ~5.200 avisos y precios en el HTML: modo sitemap |
| 5 | SíPUNTA | 2.002 | TeraCRM | |
| 6 | Colmena | 1.779 | TeraCRM | |
| 7 | Alpha Realtors | 1.563 | WP Houzez (2clics) | API REST |
| 8 | Nana Lavagna | 1.554 | TeraCRM | además tiene sitemap con ~1.190 avisos |
| 9 | Parolin & Asociados | 1.488 | TeraCRM | |
| 10 | Terramar | 1.271 | Tokko | sitemap con ~780 avisos |
| 11 | Kosak | 1.133 | nai | sitemap con ~960 avisos; para estrenar el adapter nai |
| 12 | RE/MAX Uruguay | (sin dato) | propia (Angular) | sitemap con ~4.350 avisos; la red más grande |
| 13 | ALDA Group | 1.030 | a medida | `sitemap-fichas.xml` con ~1.900 fichas |
| 14 | Antonio Mieres | 805 | a medida | sitemap con ~1.100 avisos |
| 15 | Uruguay Sotheby's | (sin dato) | NexusProps | sitemap con ~1.180 avisos; segmento de lujo |

Quedaron afuera a propósito Capital Real Estate (3.130 avisos), Imperium (1.665) y Golf (1.605). Capital no tiene
web confirmada; Imperium es una SPA de React que necesita JS; Golf es PHP a medida sin sitemap.

## Robots y bloqueos

- **Ninguna** bloquea todo (`Disallow: /`) para crawlers genéricos (`*`).
- **Century 21 Uruguay**: robots prohíbe `/busqueda` y `/fichaTecnica`, que es justo el listado y la ficha.
  Respetando robots, no se puede scrapear.
- **Engel & Völkers**: prohíbe `*/search/*`. **Luxury Punta**: prohíbe `?pagina=`.
- **Con challenge o cerradas**: Austral Propiedades y piso360.com tienen el challenge de Cloudflare, y Nieto y
  Páez da HTTP 401.
- Varias bloquean solo a los bots de IA (GPTBot, ClaudeBot...): Inmobiliaria MVD, Sánchez, Ventura, Ingar, Luxury
  Punta. El User-Agent del radar (`RadarInmobiliarioUY`) no está alcanzado por esas reglas.

## Nombres que pediste verificar

- **Bavastro**: aparece en InfoCasas como "Bavastro Rematadores", pero `bavastro.uy` es una página por defecto de
  Antel y `bavastro.com` no existe.
- **"Hughes & Hughes" / "Renzi"**: lo que existe es **Hughes & Rienzi** (en InfoCasas). Su web
  `hughesyrienzi.com` es solo un login. Hughes & Hughes es un estudio de abogados.
- **Pérez del Castillo**: es un estudio de abogados y escribanos, no una inmobiliaria. La parecida es Pérez
  Robaudo (Tokko).
- **Casas & Más** → casasymas.com.uy: es un portal/red de Maldonado (~42.000 URLs), no una sola inmobiliaria.
- **Mieres Propiedades**: mierespropiedades.com es de Lomas de Zamora (Argentina). En Uruguay está **Antonio
  Mieres**, en Punta del Este.
- **Ottonello** → Cristina Ottonello (Houzez, pocos avisos). **Moebius** → moebius.uy es una consultora de
  datos, no publica avisos.
- **Zas** → zas.com.uy es una billetera electrónica. **Jaureguiberry** es un balneario, no una inmobiliaria.
- **Ach, Kassa, Vinci, Tizado, Renzi**: no aparecen en el directorio de InfoCasas ni con dominio propio.
- RE/MAX, Century 21, Coldwell Banker, Engel & Völkers, Sotheby's y Keller Williams existen y están en la lista.
