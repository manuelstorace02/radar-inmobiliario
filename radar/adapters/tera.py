"""TeraCRM (Sierra / tera.com.uy / ri.com.uy) — adapter de PLATAFORMA: sirve para todas las inmobiliarias cuya
web está hecha con Tera (~40 en Uruguay, sobre todo en Punta del Este). Ver docs/INMOBILIARIAS.md.

Se usa con fuentes cuyo id empieza con "tera-" (ej. tera-nicolasdemodena) y modo "adapter".

Cómo son estos sitios:
  - categorías en /<tipo>/en-venta/ y /<tipo>/en-alquiler/ (apartamentos, casas, terrenos, chacras, locales...),
    enlazadas desde el menú de la home;
  - cada listado trae ~30 avisos por página, armados en el HTML (sin JS), con link a /<Tipo>/<ID>, barrio,
    precio ("U$S 250,000"), dormitorios, baños y m²; se pagina con ?pagina=N;
  - casi nunca tienen robots.txt ni sitemap. El Cliente igual respeta robots si existe (Luxury Punta prohíbe
    ?pagina=: en ese caso solo se lee la primera página de cada categoría y la corrida queda "incompleta").

El parser no depende del diseño (hay varias plantillas): toma el bloque de HTML entre el primer link a un aviso
y el primer link al aviso siguiente, y busca ahí el precio, los dormitorios, los baños y los m².
"""
from __future__ import annotations

import html as H
import json
import re
import time
from urllib.parse import urljoin, urlsplit

from radar import normalize as N

AVISO = re.compile(r"""(?:href=|open\()\s*["']((?:https?://[^"'/]+)?/?([A-Z][A-Za-z]+)/(\d{2,8}))(?:/[^"'\s]*)?["']""")
CATEG = re.compile(r"""href=["']((?:https?://[^"'/]+)?/([a-z-]+)/en-(venta|alquiler)/?)["']""", re.I)
PAG = re.compile(r"[?&]pagina=(\d+)")
PRECIO = re.compile(r"(U\$S|US\$|USD|U\$D|\$U|\$)\s*([\d.,]{3,})")
NUM = r"(?<![\d.,])(\d{1,2})(?![\d.,])"
DORM = re.compile(rf"{NUM}\s*(?:Dormitorio|Dorms?\b|Habitaci)|(?:Dormitorios?|Dorms?)\.?\s*:?\s*{NUM}", re.I)
BANO = re.compile(rf"{NUM}\s*Baños?|Baños?\s*:?\s*{NUM}", re.I)
M2 = re.compile(r"(?<![\d.,])(\d[\d.,]*)\s*m\s*(?:2|²)(?![\d])", re.I)
CATEGORIAS_DEFECTO = ["apartamentos/en-venta", "casas/en-venta", "terrenos/en-venta", "chacras/en-venta",
                      "locales/en-venta", "apartamentos/en-alquiler", "casas/en-alquiler"]
MAX_PAGINAS = 400
ZONA = re.compile(r"(?:Zona|Barrio|Ubicaci[oó]n)(?:\s*/\s*Barrio)?\s*:?\s*([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚáéíóúñÑ .]{2,40}?)(?=\s+(?:Dorm|Ba[ñn]|Sup|Cod|Precio|U\$S|USD|\$|\d)|$)")
GENERICO = re.compile(r"(?i)^(ver|más|mas|detalles?|consultar|contacto|ver detalles|ver más|ampliar|\+)\b.{0,15}$")
PLATAFORMA = True  # lo usan todas las fuentes "tera-..."

# --- plantilla nueva de Tera (Parolin, Antonini Sánchez, Vigga): links en minúscula /<tipo>/<ID>/<slug>, todo el
# stock en /propiedades/en-venta/?pagina=N, tarjetas <article> o "card-property-wrap" con la foto y el precio ANTES
# del link (por eso el bloque de cada aviso se corta en el contenedor de la tarjeta, no en el link).
TIPOS_NUEVA = "apartamento|casa|terreno|chacra|campo|local|oficina|edificio|galpon|deposito|cochera|garaje|hotel"
AVISO_NUEVA = re.compile(rf"""(?:href|data-link)=["']((?:https?://[^"'/]+)?/({TIPOS_NUEVA})/(\d{{1,8}})/[^"'\s#?]+)["']""")
TARJETA = re.compile(r"""<article\b|<div[^>]*\bclass=["'](?:card-property-wrap|cs_card|item)\b""")
ZONA_CLASE = re.compile(r"""class=["'][^"']*(?:zona|subtitle|location)[^"']*["'][^>]*>(.*?)</(?:p|div|span|li)>""", re.S)
# Lupa (detail.php?api_id=N&operacion=venta, listado properties.php?page=N)
AVISO_LUPA = re.compile(r"""href=["']((?:https?://[^"']+/)?detail\.php\?api_id=(\d+)&(?:amp;)?operacion=(\w+))["']""")
# Punta Ballena Inmobiliaria: categorías "casas-venta/?periodo=0", "apartamentos-alquiler/?periodo=16"
CATEG_GUION = re.compile(r"""href=["']((?:https?://[^"'/]+)?/?[a-z]+-(venta|alquiler)/?\?periodo=\d+)["']""")


def es_aviso(url: str) -> bool:
    p = urlsplit(url)
    return bool(re.search(r"/[A-Z][A-Za-z]+/(?:\d{2,8}|[0-9a-f-]{36})/?$", p.path)       # clásica / Market del Este
                or re.search(rf"^/(?:{TIPOS_NUEVA})/\d{{1,8}}/[^/]+/?$", p.path)         # plantilla nueva
                or re.search(r"/ficha\.php$", p.path) and re.search(r"(?:^|&)id=\d+", p.query)          # Gary
                or re.search(r"/detail\.php$", p.path) and re.search(r"(?:^|&)api_id=\d+", p.query))    # Lupa


def _texto(fragmento: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragmento, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def tarjetas(html: str, base: str, operacion: str | None) -> dict[str, dict]:
    """Avisos de una página de listado: {url: datos}."""
    hits = [(m.start(), m.group(2), m.group(3)) for m in AVISO.finditer(html)]
    primeros: list[tuple[int, str, str]] = []
    vistos = set()
    for pos, tipo, id_ in hits:
        if id_ not in vistos:
            vistos.add(id_)
            primeros.append((pos, tipo, id_))
    out = {}
    for n, (pos, tipo, id_) in enumerate(primeros):
        fin = primeros[n + 1][0] if n + 1 < len(primeros) else min(len(html), pos + 20000)
        bloque = html[html.rfind("<", 0, pos):fin]
        txt = _texto(bloque)
        # textos de los links a este aviso (suele ser el barrio o el título)
        anclas = [_texto(a) for a in re.findall(rf"""/{tipo}/{id_}(?:/[^"'\s]*)?["'][^>]*>(.*?)</a>""", bloque, re.S)]
        anclas = [re.sub(rf"^\s*#?{id_}\b\s*", "", a) for a in anclas]
        anclas = [a for a in anclas if a and len(a) < 120 and not PRECIO.search(a) and not GENERICO.match(a)]
        # pistas de ubicación que no dependen de la plantilla
        pistas = [H.unescape(x).strip() for x in re.findall(r"""(?:title|alt)=["']([^"']{4,160})["']""", bloque)]
        pistas = [re.split(r"\s[-–]\s|\.\s", x)[0][:90] for x in pistas if not GENERICO.match(x)]
        zona = ZONA.search(txt)
        # "<span>La Jacinta</span><a href=/Casa/ID>" (Karen Colombo y similares)
        previo = re.search(rf"""<span[^>]*>\s*([^<\d]{{3,60}}?)\s*</span>\s*<a[^>]+/?{tipo}/{id_}\b""", bloque)
        if previo and not GENERICO.match(previo.group(1)) and not PRECIO.search(previo.group(1)):
            zona_previa = H.unescape(previo.group(1)).strip()
        else:
            zona_previa = None
        p = PRECIO.search(txt)
        precio, moneda = (N.numero(p.group(2)), N.moneda_norm(p.group(1))) if p else (None, None)
        if precio is not None and precio < 100:  # "$ 0", restos de números sueltos
            precio, moneda = None, None
        d, b = DORM.search(txt), BANO.search(txt)
        m2s = [v for v in (N.numero(x) for x in M2.findall(txt)) if v and v >= 10]
        terreno = N.tipo_propiedad(tipo) in ("terreno", "chacra_campo")
        img = re.search(r"""<img[^>]+src=["']([^"']+\.(?:jpe?g|webp|png))""", html[max(0, pos - 3000):fin], re.I)
        url = f"{base}/{tipo}/{id_}"
        out[url] = {
            "url": url,
            "id_externo": id_,
            "titulo": (anclas[0] if anclas else None),
            "precio": precio,
            "moneda": moneda,
            "operacion": operacion,
            "tipo": N.tipo_propiedad(tipo) or None,
            # barrio: "Zona/Barrio: X", el texto de los links y los title/alt ("Apartamento en Mansa - ...")
            "ubicacion_txt": " , ".join(dict.fromkeys(([zona.group(1).strip()] if zona else []) + ([zona_previa] if zona_previa else [])
                                                      + anclas + pistas[:2]))[:300]
                             or None,
            "descripcion": txt[:600],
            "dormitorios": int(d.group(1) or d.group(2)) if d else None,
            "banos": int(b.group(1) or b.group(2)) if b else None,
            # con dos superficies (edificada y de terreno), la menor es la edificada
            "m2_edificados": None if terreno or not m2s else min(m2s),
            "m2_totales": (max(m2s) if m2s else None) if terreno else (max(m2s) if len(m2s) > 1 else None),
            "imagenes": [urljoin(base + "/", img.group(1))] if img else [],
            "breadcrumb": f"{operacion or ''} > {tipo}",
        }
    return out


def _attr(bloque: str, nombre: str) -> str | None:
    m = re.search(rf"""data-{nombre}=["']([^"']*)["']""", bloque)
    return H.unescape(m.group(1)).strip() or None if m else None


def _tarjetas_por_bloque(html: str, base: str, hits: list[tuple[int, str, str, str, str | None]]) -> dict[str, dict]:
    """Plantillas nuevas: hits = [(pos, tipo, id, url, operacion)]. El bloque de cada aviso va desde el contenedor de
    su tarjeta (<article>, card-property-wrap...) hasta el contenedor de la siguiente."""
    primeros, vistos = [], set()
    for h in hits:
        if h[2] not in vistos:
            vistos.add(h[2])
            primeros.append(h)
    aperturas = [m.start() for m in TARJETA.finditer(html)]
    inicios, previo = [], -1
    for pos, *_ in primeros:
        cand = [a for a in aperturas if previo < a <= pos]
        inicios.append(cand[-1] if cand else html.rfind("<", 0, pos))
        previo = pos
    out = {}
    for n, (pos, tipo, id_, url, operacion) in enumerate(primeros):
        pie = html.find("<footer", pos)
        fin = inicios[n + 1] if n + 1 < len(primeros) else min(len(html), pos + 8000, pie if pie > 0 else len(html))
        bloque = html[inicios[n]:fin]
        txt = _texto(bloque)
        titulo = _attr(bloque, "titulo")
        if not titulo:
            t = re.search(r"<h[2-4][^>]*>(.*?)</h[2-4]>", bloque, re.S)
            titulo = _texto(t.group(1)) if t else None
        zona = _attr(bloque, "zona")
        if not zona:
            z = ZONA_CLASE.search(bloque)
            zona = _texto(z.group(1)) if z else None
        p = PRECIO.search(_attr(bloque, "precio") or "") or PRECIO.search(txt)
        precio, moneda = (N.numero(p.group(2)), N.moneda_norm(p.group(1))) if p else (None, None)
        if precio is not None and precio < 100:
            precio, moneda = None, None
        d, b = DORM.search(txt), BANO.search(txt)
        dorm = _attr(bloque, "dorm") or (d and (d.group(1) or d.group(2)))
        banos = _attr(bloque, "banos") or (b and (b.group(1) or b.group(2)))
        m2s = [v for v in (N.numero(x) for x in M2.findall(txt)) if v and v >= 10]
        sup = N.numero(_attr(bloque, "sup"))
        if sup and sup >= 10:
            m2s = m2s or [sup]
        tp = N.tipo_propiedad(tipo) or N.tipo_propiedad(titulo or "")
        terreno = tp in ("terreno", "chacra_campo")
        img = re.search(r"""(?:<img[^>]+src=|data-foto=|url\()["']([^"']+\.(?:jpe?g|webp|png))""", bloque, re.I)
        out[url] = {
            "url": url,
            "id_externo": id_,
            "titulo": titulo,
            "precio": precio,
            "moneda": moneda,
            "operacion": operacion,
            "tipo": tp,
            "ubicacion_txt": " , ".join(x for x in dict.fromkeys([zona, titulo]) if x)[:300] or None,
            "descripcion": txt[:600],
            "dormitorios": int(dorm) if dorm and str(dorm).isdigit() else None,
            "banos": int(banos) if banos and str(banos).isdigit() else None,
            "m2_edificados": None if terreno or not m2s else min(m2s),
            "m2_totales": (max(m2s) if m2s else None) if terreno else (max(m2s) if len(m2s) > 1 else None),
            "imagenes": [urljoin(base + "/", img.group(1))] if img else [],
            "breadcrumb": f"{operacion or ''} > {tipo}",
        }
    return out


def tarjetas_nueva(html: str, base: str, operacion: str | None) -> dict[str, dict]:
    """Plantilla nueva (links /casa/123/slug): la URL canónica es la del link, con el slug."""
    hits = [(m.start(), m.group(2), m.group(3), urljoin(base + "/", m.group(1)), operacion)
            for m in AVISO_NUEVA.finditer(html)]
    return _tarjetas_por_bloque(html, base, hits)


def tarjetas_guion(html: str, base: str, operacion: str | None) -> dict[str, dict]:
    """Punta Ballena Inmobiliaria: links clásicos /Casa/ID (relativos), tarjeta en <div class="item">."""
    hits = [(m.start(), m.group(2), m.group(3), f"{base}/{m.group(2)}/{m.group(3)}", operacion)
            for m in AVISO.finditer(html)]
    return _tarjetas_por_bloque(html, base, hits)


def tarjetas_lupa(html: str, base: str) -> dict[str, dict]:
    hits = [(m.start(), "", m.group(2), f"{base}/detail.php?api_id={m.group(2)}&operacion={m.group(3)}",
             N.operacion(m.group(3)) or m.group(3)) for m in AVISO_LUPA.finditer(html)]
    out = _tarjetas_por_bloque(html, base, hits)
    for d in out.values():
        d["breadcrumb"] = f"{d['operacion'] or ''} > {d['tipo'] or ''}"
    return out


def _json_en(texto: str, marca: str):
    i = texto.find(marca)
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(texto[i + len(marca):].lstrip())[0]
    except ValueError:
        return None


async def _listar_gary(cli, base: str, limite_ts: float, max_paginas: int | None) -> tuple[dict[str, dict], bool]:
    """Gary+ (gary.uy): catalogo.php y rentals.php traen todo el stock en un array JS "var PROPS=[...]" con los
    campos de Tera. Las fichas son ficha.php?id=N. Alquiler de temporada pide elegir período: no se lee."""
    registros, completo = {}, True
    paginas = [("catalogo.php", "venta"), ("rentals.php?m=anual", "alquiler"),
               ("rentals.php?m=invernal", "alquiler_temporal")]
    for n, (pag, op) in enumerate(paginas):
        if time.monotonic() > limite_ts or (max_paginas and n >= max_paginas):
            return registros, False
        r = await cli.get(f"{base}/{pag}")
        props = _json_en(r.texto, "var PROPS=") if r.status == 200 else None
        if props is None:
            completo = False
            continue
        for p in props:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            url = f"{base}/ficha.php?id={p['id']}"
            precio = p.get("precio") if isinstance(p.get("precio"), (int, float)) and p.get("precio") > 0 else None
            tp = N.tipo_propiedad(p.get("tipo") or "")
            sup = p.get("supE") if isinstance(p.get("supE"), (int, float)) and p.get("supE") > 0 else None
            registros.setdefault(url, {
                "url": url, "id_externo": str(p["id"]), "titulo": p.get("titulo"),
                "precio": float(precio) if precio else None, "moneda": "USD" if precio else None,
                "operacion": op, "tipo": tp,
                "ubicacion_txt": " , ".join(x for x in dict.fromkeys([p.get("barrio"), p.get("ciudad")]) if x) or None,
                "departamento": p.get("depto") or None,
                "dormitorios": p.get("dorm") if isinstance(p.get("dorm"), int) and (p["dorm"] or tp == "apartamento") else None,
                "banos": p.get("banos") if isinstance(p.get("banos"), int) and p.get("banos") > 0 else None,
                "m2_edificados": None if tp in ("terreno", "chacra_campo") else sup,
                "m2_totales": sup if tp in ("terreno", "chacra_campo") else None,
                "imagenes": [p["img"]] if p.get("img") else [],
                "descripcion": (p.get("titulo") or "")[:600],
                "breadcrumb": f"{op} > {p.get('tipo') or ''}",
            })
    return registros, completo


async def _listar_market(cli, base: str, limite_ts: float, max_paginas: int | None) -> tuple[dict[str, dict], bool]:
    """Market del Este (Next.js sobre Tera): /listings?transaction=sale|rent&page=N; los datos vienen en el payload
    de React ("self.__next_f.push") como objetos {"id", "teraId", "category", "price", "location", ...}."""
    registros, completo, paginas = {}, True, 0
    for trans, op in (("sale", "venta"), ("rent", "alquiler")):
        n, ultima = 1, 1
        while n <= min(ultima, MAX_PAGINAS):
            if time.monotonic() > limite_ts or (max_paginas and paginas >= max_paginas):
                return registros, False
            r = await cli.get(f"{base}/listings?transaction={trans}&page={n}")
            paginas += 1
            if r.status != 200:
                completo = False
                break
            flujo = "".join(json.loads(c) for c in
                            re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', r.texto))
            antes, dec = len(registros), json.JSONDecoder()
            for m in re.finditer(r'\{"id":"[^"]+","teraId"', flujo):
                try:
                    o = dec.raw_decode(flujo[m.start():])[0]
                except ValueError:
                    continue
                cat = re.sub(r"[^A-Za-z]", "", o.get("category") or "") or "Propiedad"
                ide = str(o.get("teraId") or o["id"])
                url = f"{base}/{cat}/{ide}"
                precio = o.get("price") if o.get("showPrice", True) and isinstance(o.get("price"), (int, float)) else None
                if precio is not None and not (100 <= precio < 100_000_000):  # 111111111 = "consultar"
                    precio = None
                tp = N.tipo_propiedad(cat) or N.tipo_propiedad(o.get("title") or "")
                op_t = N.operacion(o.get("title"))  # en "sale" aparece algún "LOCAL EN ALQUILER"
                terreno = tp in ("terreno", "chacra_campo")
                m2e = o.get("areaM2") if isinstance(o.get("areaM2"), (int, float)) and o["areaM2"] >= 10 else None
                m2t = o.get("areaTerreno") if isinstance(o.get("areaTerreno"), (int, float)) and o["areaTerreno"] >= 100 else None
                registros.setdefault(url, {
                    "url": url, "id_externo": ide, "titulo": (o.get("title") or "").strip() or None,
                    "precio": float(precio) if precio else None,
                    "moneda": N.moneda_norm(o.get("currency")) if precio else None,
                    "operacion": op_t if op == "venta" and op_t in ("alquiler", "alquiler_temporal") else op,
                    "tipo": tp, "ubicacion_txt": o.get("location") or None,
                    "dormitorios": o.get("bedrooms") if isinstance(o.get("bedrooms"), int) else None,
                    "banos": o.get("bathrooms") if isinstance(o.get("bathrooms"), int) else None,
                    "m2_edificados": None if terreno else m2e,
                    "m2_totales": m2t if not terreno else (m2t or m2e),
                    "imagenes": [urljoin(base + "/", o["image"])] if o.get("image") else [],
                    "descripcion": (o.get("description") or o.get("title") or "")[:600],
                    "breadcrumb": f"{op} > {cat}",
                })
            ultima = max([ultima] + [int(x) for x in re.findall(rf"transaction={trans}&(?:amp;)?page=(\d+)", r.texto)])
            if len(registros) == antes:
                break
            n += 1
    return registros, completo


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    registros: dict[str, dict] = {}
    completo = True
    r = await cli.get(base + "/")
    if r.status != 200:
        raise RuntimeError(f"no abre la home de {base}: {r.status or r.error}")
    base = _base(r.url_final or base)  # www. / https
    home = r.texto
    # variantes que no son la web clásica de Tera
    if re.search(r"""href=["']catalogo\.php""", home) and "ficha.php" in home:
        return await _listar_gary(cli, base, limite_ts, max_paginas)
    if "/_next/" in home and re.search(r"""href=["']/listings""", home):
        return await _listar_market(cli, base, limite_ts, max_paginas)
    lupa = bool(AVISO_LUPA.search(home) or re.search(r"""href=["']properties\.php["']""", home))
    # plantilla nueva: links /casa/123/slug y ninguno de la clásica (/Casa/123)
    nueva = not lupa and not AVISO.search(home) and bool(AVISO_NUEVA.search(home))
    categorias = {}
    if lupa:
        categorias = {"properties.php": None}
    else:
        for m in CATEG.finditer(home):
            path = urlsplit(urljoin(base + "/", m.group(1))).path.strip("/")
            categorias[path.lower()] = "venta" if m.group(3).lower() == "venta" else "alquiler"
    if nueva:
        # /propiedades/en-venta/ ya trae todos los tipos; en alquiler algunas (Parolin) solo filtran por tipo
        venta = {c: o for c, o in categorias.items() if o == "venta"}
        if "propiedades/en-venta" in venta:
            venta = {"propiedades/en-venta": "venta"}
        alq = {c: o for c, o in categorias.items() if o == "alquiler"}
        alq = dict(sorted(alq.items(), key=lambda x: x[0] != "propiedades/en-alquiler"))
        for c in ("apartamentos/en-alquiler", "casas/en-alquiler", "chacras/en-alquiler"):
            alq.setdefault(c, "alquiler")
        categorias = {**venta, **alq}
    guion = False
    if not categorias:
        # Punta Ballena Inmobiliaria: "ventas" / "alquileres" enlazan a "casas-venta/?periodo=0", etc.
        paginas_menu = [home]
        for m in re.finditer(r"""href=["']((?:https?://[^"'/]+)?/?(?:ventas|alquileres))["']""", home):
            rr = await cli.get(urljoin(base + "/", m.group(1)))
            if rr.status == 200:
                paginas_menu.append(rr.texto)
        for t in paginas_menu:
            for m in CATEG_GUION.finditer(t):
                categorias.setdefault(urljoin(base + "/", m.group(1))[len(base) + 1:], m.group(2))
        guion = bool(categorias)
    if not categorias:
        categorias = {c: ("venta" if c.endswith("venta") else "alquiler") for c in CATEGORIAS_DEFECTO}
    paginas = 0
    for cat, op in categorias.items():
        n, ultima = 1, 1
        while n <= min(ultima, MAX_PAGINAS):
            if time.monotonic() > limite_ts or (max_paginas and paginas >= max_paginas):
                return registros, False
            if lupa:
                url = f"{base}/{cat}" + (f"?page={n}" if n > 1 else "")
            elif "?" in cat:
                url = f"{base}/{cat}" + (f"&pagina={n}" if n > 1 else "")
            else:
                url = f"{base}/{cat}/" + (f"?pagina={n}" if n > 1 else "")
            r = await cli.get(url)
            paginas += 1
            if r.status != 200:
                if r.error == "bloqueado_por_robots" or n > 1:
                    completo = False
                break
            if lupa:
                nuevos = tarjetas_lupa(r.texto, base)
            elif nueva:
                nuevos = tarjetas_nueva(r.texto, base, op)
            elif guion:
                nuevos = tarjetas_guion(r.texto, base, op)
            else:
                nuevos = tarjetas(r.texto, base, op)
            antes = len(registros)
            for u, d in nuevos.items():
                registros.setdefault(u, d)
            ultima = max([ultima] + [int(x) for x in (re.findall(r"[?&]page=(\d+)", r.texto) if lupa
                                                      else PAG.findall(r.texto))])
            if not nuevos or (n > 1 and len(registros) == antes):  # página vacía o repetida: fin
                break
            if nueva and len(registros) == antes:  # categoría que repite otra (Parolin /propiedades/en-alquiler/)
                break
            n += 1
    return registros, completo
