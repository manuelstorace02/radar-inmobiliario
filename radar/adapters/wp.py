"""WordPress (tema Houzez y otros temas inmobiliarios) — adapter de PLATAFORMA para fuentes "wp-<slug>".

Dos modos, se elige solo:

1. **API REST de Houzez** (`/wp-json/wp/v2/properties`): la usan las webs hechas con el tema Houzez (muchas cargadas
   desde el CRM 2clics: Alpha Realtors, Workplace, Platinum, UrbanKey...). Cada aviso trae `property_meta`
   (fave_property_price, fave_currency, fave_property_bedrooms, fave_property_size, lat/lon...) y las
   taxonomías property_status/type/city/area/state como ids, que se resuelven con /wp-json/wp/v2/<taxonomía>.
   100 avisos por pedido: un sitio de 2.000 avisos son ~25 pedidos. Se pagina hasta que una página viene con menos
   de 100 o WordPress responde "rest_post_invalid_page_number" → completo=True confiable.
   No se guardan los datos de agentes (agent_name/phone/email vienen en property_meta: se ignoran).

2. **Sitemap + fichas** (Houzez con la API cerrada, WpResidence, Estatik, temas propios): se toman las URLs de
   los avisos del sitemap del custom post type (property, properties, estate_property, propiedad(es)) y se abre
   cada ficha (~1,2 s c/u). El parser de ficha es genérico: pares "etiqueta: valor" del HTML (Precio, Zona, Barrio,
   Ciudad, Departamento, Dormitorios, Baños, Área...), JSON-LD y, si no, el primer precio de la cabecera.
   completo=True solo si se leyeron todos los sitemaps y todas las fichas.
"""
from __future__ import annotations

import html as H
import json
import re
import time
from urllib.parse import urlsplit

from radar import normalize as N

PLATAFORMA = True  # lo usan todas las fuentes "wp-..."

POR_PAGINA = 100
TAXONOMIAS = ["property_status", "property_type", "property_city", "property_area", "property_state",
              "property_label"]
CAMPOS = ("id,link,title,excerpt,property_meta," + ",".join(TAXONOMIAS) + ",_links,_embedded")
CPT = r"(?:property|properties|estate_property|propiedad|propiedades|inmueble|inmuebles)"
SITEMAP_CPT = re.compile(rf"(?:posts-|/){CPT}(?:-sitemap\d*\.xml|-\d+\.xml)$", re.I)
AVISO_PATH = re.compile(rf"^/{CPT}/[^/]+", re.I)
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
DEPTOS = {N.simple(d): d for d in N.DEPARTAMENTOS}

# ficha HTML
_MON = r"(U\$S|US\$|U\$D|USD|U\.S\.\$|\$U|UYU|UY\$|\$)"
_NUMP = r"(\d{1,3}(?:[.,]\d{3})+(?:,\d{1,2})?|\d{3,})"
PRECIO_TXT = re.compile(_MON + r"\s*" + _NUMP + r"(?:\s*(USD|UYU|U\$S))?", re.I)
PRECIO_ETQ = re.compile(r"Precio(?:\s+de)?(?:\s+(?:venta|alquiler))?\s*:\s*" + _MON + r"?\s*" + _NUMP
                        + r"(?:\s*(USD|UYU|U\$S))?", re.I)
PAR = re.compile(r">\s*([A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ .²/¿?]{1,35}?)\s*:?\s*"
                 r"</(?:strong|label|span|dt|th|b|div|h\d|p|li)>\s*(?:<[^>]+>\s*){0,4}([^<>]{1,100}?)\s*<", re.S)
FIN_FICHA = re.compile(r"(?i)propiedades similares|también te puede interesar|propiedades relacionadas|"
                       r"similar listings|otras propiedades|te puede interesar")
NUM = r"(?<![\d.,])(\d{1,2})(?![\d.,])"
DORM = [re.compile(rf"(?:Dormitorios?|Dorms?)\.?\s*:\s*{NUM}", re.I),
        re.compile(rf"{NUM}\s*(?:Dormitorio|Dorms?\b|Habitaci)", re.I)]
BANO = [re.compile(rf"Baños?\s*:\s*{NUM}", re.I), re.compile(rf"{NUM}\s*Baños?", re.I)]
M2 = re.compile(r"(?<![\d.,])(\d[\d.,]*)\s*m\s*(?:2|²)(?![\d])", re.I)


def es_aviso(url: str) -> bool:
    p = urlsplit(url).path
    return bool(AVISO_PATH.match(p)) and "prueba" not in p.lower()


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _texto(fragmento: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragmento, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


def _json(texto: str):
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        return None


def _int(v) -> int | None:
    n = N.numero(str(v)) if v not in (None, "") else None
    return int(n) if n is not None and 0 <= n < 100 else None


def _m2(v) -> float | None:
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):  # valor crudo de la base ("400.00", "109.110" = 109,11)
        return float(s) if float(s) >= 5 else None
    if re.search(r"(?i)\bha\b|hect", s):
        return None
    m = re.search(r"\d[\d.,]*", s)
    n = N.numero(m.group(0)) if m else None
    return n if n and n >= 5 else None


def _depto(*textos) -> str | None:
    for t in textos:
        t = N.simple(str(t or ""))
        for k, v in DEPTOS.items():
            if re.search(rf"\b{k}\b", t):
                return v
    return None


def _moneda_txt(*textos) -> str | None:
    t = " ".join(str(x) for x in textos if x).lower()
    if not t.strip():
        return None
    if re.search(r"usd|u\$s|us\$|u\$d|d[oó]lar", t):
        return "USD"
    if re.search(r"peso|uyu|\$", t):
        return "UYU"
    return None


# ------------------------------------------------------------------------------------------- modo API (Houzez)
def _meta(m: dict, k: str):
    v = m.get(k)
    if isinstance(v, list):
        v = v[0] if v else None
    return v if v not in ("", None) else None


def aviso_api(p: dict, tax: dict[str, dict[int, str]]) -> dict | None:
    m = p.get("property_meta") or {}
    if not isinstance(m, dict):
        m = {}
    if _meta(m, "fave_property_sold") == "true" or _meta(m, "fave_property_rented") == "true":
        return None
    nombres = lambda t: [tax.get(t, {}).get(i) for i in (p.get(t) or []) if tax.get(t, {}).get(i)]
    estados, tipos = nombres("property_status"), nombres("property_type")
    areas, ciudades, deptos = nombres("property_area"), nombres("property_city"), nombres("property_state")
    titulo = H.unescape(re.sub(r"<[^>]+>", "", (p.get("title") or {}).get("rendered", ""))).strip() or None

    prefijo, sufijo = _meta(m, "fave_property_price_prefix"), _meta(m, "fave_property_price_postfix")
    crudo = _meta(m, "fave_property_price")
    if crudo is None and _meta(m, "fave_property_sec_price"):
        crudo, prefijo = _meta(m, "fave_property_sec_price"), sufijo or prefijo
    precio, moneda = None, None
    if crudo is not None:
        precio, moneda = N.parse_precio(str(crudo))
        if precio is None:
            mm = re.search(r"\d[\d.,]*", str(crudo))
            precio = N.numero(mm.group(0)) if mm else None
    if precio is not None and precio < 100:
        precio = None
    op = N.operacion(prefijo) or N.operacion(*estados) or N.operacion(titulo)
    moneda = (_moneda_txt(_meta(m, "fave_currency")) or moneda
              or _moneda_txt(prefijo, sufijo, str(crudo or "")))
    if precio is not None and moneda is None:
        # sin moneda cargada: Houzez muestra la moneda por defecto del sitio (USD en Uruguay); un alquiler
        # de 10.000 o más casi seguro está en pesos.
        moneda = "UYU" if op in ("alquiler", "alquiler_temporal") and precio >= 10000 else "USD"
    if precio is None:
        moneda = None

    lat, lon = N.numero(_meta(m, "houzez_geolocation_lat")), N.numero(_meta(m, "houzez_geolocation_long"))
    if not (lat and lon and -35.5 < lat < -29.5 and -59 < lon < -53):
        lat = lon = None
    depto = _depto(*deptos) or next((DEPTOS[N.simple(d)] for d in ciudades if N.simple(d) in DEPTOS), None)
    direccion = _meta(m, "fave_property_address") or _meta(m, "fave_property_map_address")
    partes = areas + ciudades + ([direccion] if direccion and not (areas or ciudades) else []) + deptos
    img = None
    for fm in (p.get("_embedded") or {}).get("wp:featuredmedia") or []:
        if isinstance(fm, dict) and fm.get("source_url"):
            img = fm["source_url"]
            break
    tipo_txt = " ".join(tipos)
    terreno = N.tipo_propiedad(tipo_txt, titulo) in ("terreno", "chacra_campo")
    edif, total = _m2(_meta(m, "fave_property_size")), _m2(_meta(m, "fave_property_land"))
    tipo = N.tipo_propiedad(tipo_txt) or N.tipo_propiedad(titulo)
    dorm = _int(_meta(m, "fave_property_bedrooms"))
    desc = _texto((p.get("excerpt") or {}).get("rendered", ""))[:600] or None
    return {
        "url": p.get("link"),
        "id_externo": str(_meta(m, "fave_property_id") or p.get("id")),
        "titulo": titulo,
        "precio": precio,
        "moneda": moneda,
        "operacion": op,
        "tipo": tipo,
        "ubicacion_txt": " , ".join(dict.fromkeys(x.strip() for x in partes if x and x.strip()))[:300] or None,
        "departamento": depto,
        "dormitorios": dorm if dorm or tipo == "apartamento" else None,  # 0 = "no aplica", salvo monoambientes
        "banos": _int(_meta(m, "fave_property_bathrooms")) or None,
        "m2_edificados": None if terreno else edif,
        "m2_totales": total or (edif if terreno else None),
        "gastos_comunes": N.numero(re.sub(r"[^\d.,]", "", str(_meta(m, "fave_expenses") or ""))) or None,
        "lat": lat,
        "lon": lon,
        "imagenes": [img] if img else [],
        "descripcion": desc,
        "breadcrumb": " > ".join(x for x in [" / ".join(estados), tipo_txt] + list(reversed(deptos + ciudades + areas))
                                  if x) or None,
    }


async def _taxonomias(cli, base: str, cuenta: list) -> dict[str, dict[int, str]]:
    tax: dict[str, dict[int, str]] = {}
    for t in TAXONOMIAS:
        tax[t] = {}
        for n in range(1, 30):
            r = await cli.get(f"{base}/wp-json/wp/v2/{t}?per_page={POR_PAGINA}&page={n}&_fields=id,name")
            cuenta[0] += 1
            datos = _json(r.texto) if r.status == 200 else None
            if not isinstance(datos, list):
                break
            for x in datos:
                if isinstance(x, dict) and "id" in x:
                    tax[t][x["id"]] = H.unescape(x.get("name") or "")
            if len(datos) < POR_PAGINA:
                break
    return tax


async def _listar_api(cli, base, primera, limite_ts, max_paginas, cuenta) -> tuple[dict[str, dict], bool]:
    tax = await _taxonomias(cli, base, cuenta)
    registros: dict[str, dict] = {}
    datos, n = primera, 1
    while True:
        for p in datos:
            if isinstance(p, dict) and p.get("link"):
                d = aviso_api(p, tax)
                if d:
                    registros[d["url"]] = d
        if len(datos) < POR_PAGINA:
            return registros, True
        if time.monotonic() > limite_ts or (max_paginas and cuenta[0] >= max_paginas):
            return registros, False
        n += 1
        r = await cli.get(_url_api(base, n))
        cuenta[0] += 1
        if r.status == 400 and "rest_post_invalid_page_number" in r.texto:
            return registros, True
        datos = _json(r.texto) if r.status == 200 else None
        if not isinstance(datos, list):
            return registros, False


def _url_api(base: str, n: int) -> str:
    return (f"{base}/wp-json/wp/v2/properties?per_page={POR_PAGINA}&page={n}&orderby=id&order=asc"
            f"&_embed=wp:featuredmedia&_fields={CAMPOS}")


# ------------------------------------------------------------------------------------- modo sitemap + fichas
def _ld(html: str) -> list[dict]:
    out = []
    for x in re.findall(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        d = _json(x.strip())
        pila = [d]
        while pila:
            e = pila.pop()
            if isinstance(e, list):
                pila.extend(e)
            elif isinstance(e, dict):
                if "@graph" in e:
                    pila.append(e["@graph"])
                t = e.get("@type")
                t = " ".join(t) if isinstance(t, list) else str(t or "")
                if not re.search(r"WebPage|WebSite|Organization|BreadcrumbList|ImageObject|Person|ReadAction", t):
                    out.append(e)
    return out


def _meta_html(html: str, prop: str) -> str | None:
    m = re.search(rf"""<meta[^>]+(?:property|name)=["']{prop}["'][^>]+content=["']([^"']*)""", html, re.I)
    return H.unescape(m.group(1)).strip() if m else None


def ficha(html: str, url: str) -> dict:
    """Datos de una ficha de aviso (cualquier tema de WordPress)."""
    limpio = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    i = limpio.find("<h1")
    cuerpo = limpio[i:] if i >= 0 else limpio
    pares: dict[str, str] = {}
    for m in PAR.finditer(cuerpo):
        k, v = N.simple(m.group(1)), H.unescape(m.group(2)).strip()
        if k and v and k not in pares and not re.match(r"(?i)consultar", k):
            pares[k] = v
    texto = _texto(cuerpo)
    f = FIN_FICHA.search(texto)
    if f:
        texto = texto[:f.start()]
    cabeza = texto[:3000]
    ld = _ld(html)
    par = lambda *rx: next((v for k, v in pares.items() if any(re.fullmatch(r, k) for r in rx)), None)

    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    titulo = _texto(h1.group(1)) if h1 else None
    if not titulo:
        titulo = re.split(r"\s+[-|–]\s+(?=[^-|–]*$)", _meta_html(html, "og:title") or "")[0] or None

    # precio: "Precio: USD 537.000" > primer precio de la cabecera > "Precio venta: 620.000" + "Moneda: U$S"
    precio = moneda = None
    ctx = ""
    m = PRECIO_ETQ.search(texto)
    if m and m.group(1):
        precio, moneda, ctx = N.numero(m.group(2)), N.moneda_norm(m.group(3) or m.group(1)), texto[max(0, m.start() - 40):m.end() + 25]
    if precio is None:
        for m in PRECIO_TXT.finditer(cabeza):
            v = N.numero(m.group(2))
            if v and v >= 100:
                precio, moneda = v, N.moneda_norm(m.group(3) or m.group(1))
                ctx = cabeza[max(0, m.start() - 40):m.end() + 25]
                break
    if precio is None:
        m = PRECIO_ETQ.search(texto)
        if m:
            precio = N.numero(m.group(2))
            moneda = N.moneda_norm(m.group(3) or "") or _moneda_txt(par(r"moneda.*"))
            ctx = texto[max(0, m.start() - 40):m.end() + 25]
    if precio is None:
        for e in ld:
            of = e.get("offers")
            of = of[0] if isinstance(of, list) and of else of
            if isinstance(of, dict) and of.get("price"):
                precio, moneda = N.numero(str(of["price"])), N.moneda_norm(of.get("priceCurrency"))
                break
    if precio is not None and precio < 100:
        precio = None
    if precio is None:
        moneda = None

    bc = re.search(r"""<(ol|ul|nav|div)[^>]+class=["'][^"']*breadcrumb[^"']*["'][^>]*>(.*?)</\1>""", html, re.S | re.I)
    bc = _texto(bc.group(2))[:200] if bc else None
    m = PRECIO_TXT.search(cabeza)
    ctx1 = cabeza[max(0, m.start() - 40):m.end() + 25] if m else ""
    # "con renta", "Renta $17.000" = inversión alquilada, no es un aviso de alquiler
    sin_renta = lambda t: re.sub(r"(?i)\brentas?\b", " ", t or "")
    op = next(filter(None, (N.operacion(sin_renta(t)) for t in (
        par(r"(tipo de )?operacion", r"estado( de la propiedad)?", r"status"), bc, ctx, ctx1, titulo,
        urlsplit(url).path.replace("-", " "), cabeza[:500]))), None)
    depa = lambda t: re.sub(r"(?i)\bdepartamentos?\b(?! de )", "apartamento", t or "")  # "departamento" = apto
    tipo = N.tipo_propiedad(depa(par(r"tipo( de propiedad| de inmueble)?", r"categoria"))) or N.tipo_propiedad(depa(titulo)) \
        or N.tipo_propiedad(depa(urlsplit(url).path.replace("-", " ")))

    ubic = [par(r"zona"), par(r"barrio"), par(r"ciudad o barrio"), par(r"localidad"), par(r"ciudad"),
            par(r"departamento")]
    for e in ld:
        a = e.get("address")
        if isinstance(a, dict):
            ubic += [a.get("addressLocality"), a.get("addressRegion")]
        elif isinstance(a, str):
            ubic.append(a)
    ubic = [H.unescape(str(x)).strip() for x in ubic if x and len(str(x)) < 80 and not re.search(r"(?i)google|mapa", str(x))]
    depto = _depto(par(r"departamento"), par(r"provincia"), par(r"estado")) \
        or next((DEPTOS[N.simple(x)] for x in ubic if N.simple(x) in DEPTOS), None)

    ldv = lambda k: next((e.get(k) for e in ld if e.get(k) not in (None, "")), None)
    d = next((m for m in (rx.search(cabeza) for rx in DORM) if m), None)
    b = next((m for m in (rx.search(cabeza) for rx in BANO) if m), None)
    dorm = _int(par(r"dormitorios?", r"nro dormitorios", r"habitaciones", r"cuartos")) \
        if par(r"dormitorios?", r"nro dormitorios", r"habitaciones", r"cuartos") else _int(ldv("numberOfBedrooms"))
    if dorm is None and d:
        dorm = int(d.group(1))
    banos = _int(par(r"banos?")) if par(r"banos?") else _int(ldv("numberOfBathroomsTotal"))
    if banos is None and b:
        banos = int(b.group(1))
    if dorm == 0 and tipo != "apartamento":
        dorm = None
    banos = banos or None
    terreno = tipo in ("terreno", "chacra_campo")
    edif = _m2(par(r"(area|superficie|metros)( edificada| construida| cubierta| interna| util)?", r"m2", r"m",
                   r"tamano( de la edificacion)?", r"total construido", r"area construida"))
    total = _m2(par(r"(area|superficie)( del| de)? (terreno|total|lote)", r"terreno", r"superficie terreno"))
    if edif is None and total is None:
        m2s = [v for v in (N.numero(x) for x in M2.findall(cabeza[:1500])) if v and v >= 10]
        if m2s:
            edif, total = (None, max(m2s)) if terreno else (min(m2s), max(m2s) if len(m2s) > 1 else None)
    if terreno and edif and not total:
        edif, total = None, edif

    imgs = []
    og = _meta_html(html, "og:image")
    if og:
        imgs.append(og)
    li = ldv("image")
    for x in (li if isinstance(li, list) else [li]):
        x = x.get("url") if isinstance(x, dict) else x
        if isinstance(x, str) and x.startswith("http") and x not in imgs:
            imgs.append(x)
    lat = re.search(r"""["'](?:lat|latitude|houzez_geolocation_lat)["']\s*:\s*["']?(-3[0-5]\.\d{3,})""", html)
    lon = re.search(r"""["'](?:lng|lon|long|longitude|houzez_geolocation_long)["']\s*:\s*["']?(-5[3-8]\.\d{3,})""", html)
    gc = par(r"gastos comunes", r"gastos")
    ide = par(r"id( de)? propiedad", r"codigo", r"referencia", r"ref")
    return {
        "url": url,
        "id_externo": (ide if ide and len(ide) < 30 else None) or urlsplit(url).path.strip("/").split("/")[-1][:80],
        "titulo": titulo,
        "precio": precio,
        "moneda": moneda,
        "operacion": op,
        "tipo": tipo,
        "ubicacion_txt": " , ".join(dict.fromkeys(ubic))[:300] or None,
        "departamento": depto,
        "dormitorios": dorm,
        "banos": banos,
        "m2_edificados": None if terreno else edif,
        "m2_totales": total,
        "gastos_comunes": (N.parse_precio(gc)[0] or N.numero(re.sub(r"[^\d.,]", "", gc)) or None) if gc else None,
        "lat": float(lat.group(1)) if lat and lon else None,
        "lon": float(lon.group(1)) if lat and lon else None,
        "imagenes": imgs[:3],
        "breadcrumb": bc,
        "descripcion": (_meta_html(html, "og:description") or _meta_html(html, "description") or texto[:600])[:600],
    }


async def _urls_sitemap(cli, base: str, cuenta: list) -> tuple[list[str], bool]:
    """URLs de avisos según los sitemaps del custom post type. Devuelve (urls, todo_ok)."""
    indices = list(dict.fromkeys(await cli.sitemaps_de_robots(base)
                                 + [base + "/wp-sitemap.xml", base + "/sitemap_index.xml"]))
    hijos: list[str] = []
    for ix in indices:
        if SITEMAP_CPT.search(urlsplit(ix).path):
            hijos.append(ix)
            continue
        r = await cli.get(ix)
        cuenta[0] += 1
        if r.status != 200:
            continue
        hijos += [u for u in LOC.findall(r.texto) if SITEMAP_CPT.search(urlsplit(H.unescape(u)).path)]
        if hijos:
            break
    urls, ok = [], bool(hijos)
    for sm in dict.fromkeys(H.unescape(h) for h in hijos):
        r = await cli.get(sm)
        cuenta[0] += 1
        if r.status != 200:
            ok = False
            continue
        urls += [H.unescape(u) for u in LOC.findall(r.texto) if es_aviso(H.unescape(u))]
    return list(dict.fromkeys(urls)), ok


async def _listar_fichas(cli, base, limite_ts, max_paginas, cuenta) -> tuple[dict[str, dict], bool]:
    urls, completo = await _urls_sitemap(cli, base, cuenta)
    if not urls:
        raise RuntimeError(f"{base}: no hay API REST de Houzez ni sitemap de avisos")
    registros: dict[str, dict] = {}
    for u in urls:
        if time.monotonic() > limite_ts or (max_paginas and cuenta[0] >= max_paginas):
            return registros, False
        r = await cli.get(u)
        cuenta[0] += 1
        if r.status in (404, 410):
            continue
        if r.status != 200:
            completo = False
            continue
        registros[u] = ficha(r.texto, u)
    return registros, completo


# --------------------------------------------------------------------------------------------------- entrada
async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    cuenta = [0]
    r = await cli.get(_url_api(base, 1))
    cuenta[0] += 1
    if r.status == 200:
        base = _base(r.url_final or base)
    datos = _json(r.texto) if r.status == 200 else None
    if isinstance(datos, list):
        return await _listar_api(cli, base, datos, limite_ts, max_paginas, cuenta)
    if r.status == 0 and r.error != "bloqueado_por_robots":
        raise RuntimeError(f"no abre {base}: {r.error}")
    return await _listar_fichas(cli, base, limite_ts, max_paginas, cuenta)
