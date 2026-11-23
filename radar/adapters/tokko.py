"""Tokko Broker (CRM argentino) — adapter de PLATAFORMA para las inmobiliarias uruguayas cuya web sale de Tokko
(~12; ver docs/INMOBILIARIAS.md). Se usa con fuentes cuyo id empieza con "tokko-" (ej. tokko-arquibrokers).

Hay dos familias de sitios:

1. La web "oficial" de Tokko (Tokko Web Framework, "tfw"): Arquibrokers, Katz, Casagrande, Abate, Alvicor.
   Se reconoce por `tfwListingUrl` / `static.tokkobroker.com/tfw` en el HTML. Los listados son /Venta, /Alquiler y
   /Alquiler-temporario; la primera página dice "N Resultados" y el resto se pide como el scroll infinito del
   sitio: /Venta?o=2,2&p=N (20 avisos por página, "--NoMoreProperties--" al final). Las tarjetas YA traen precio,
   tipo, barrio (en el alt de la foto: "Foto Casa en Venta en Malvín, Montevideo <dirección>"), dormitorios y m²,
   así que no hace falta abrir las fichas. Avisos en /p/<ID>-<slug> (/p/<ID> solo da 404: la URL lleva el slug).

2. Webs hechas a medida que solo toman los datos de Tokko (fotos en static.tokkobroker.com). Cada una tiene su
   perfil abajo (PERFILES, por dominio):
   - terramar.com.uy: busqueda.php?page=N (18 por página), avisos propiedad.php?ref=TM123.
   - afiliapropiedades.uy (WordPress): /propiedades/?pagina=N (12 por página, "N resultados"), avisos /p/<ID>-slug/.
   - emilianopedrozo.com: /ventas/de/<tipo>/pagina/N y /alquileres/de/<tipo>/pagina/N, avisos /<slug>.<ID>.<op>.
   - scrollinipropiedades.com: /propiedades/<operación>?page=N, avisos /propiedad/<op>/<ID>-1.
   - redhubinmobiliario.com: el listado solo muestra destacadas; el sitemap trae todas las fichas
     (/propiedad/TKV123/slug) y cada ficha tiene JSON-LD con precio y breadcrumb (operación/tipo/depto/barrio).
   NO soportados (ver informe): kwuruguay.com (paginación por POST de ASP.NET) y perezrobaudo.com (el listado se
   arma en el navegador llamando a la API de Tokko con la clave de la inmobiliaria).
"""
from __future__ import annotations

import asyncio
import html as H
import json
import re
import time
from urllib.parse import urljoin, urlsplit

from radar import normalize as N

PLATAFORMA = True  # lo usan todas las fuentes "tokko-..."

MAX_PAGINAS = 400  # por categoría/operación

# ----------------------------------------------------------------------------------------------- utilidades
PRECIO = re.compile(r"(U\$S|US\$|USD|U\$D|UYU|\$U|\$)\s*"
                    r"(\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+|\d{1,3}(?:[  ]\d{3})+(?![\d.,])|\d+)")
_NUM = r"(?<![\d.,])(\d{1,2})(?![\d.,])"
DORM = re.compile(rf"{_NUM}\s*(?:Dormitorio|Dorms?\b|Dor\.|Habitaci)|(?:Dormitorios?|Dorms?)\.?\s*:?\s*{_NUM}", re.I)
BANO = re.compile(rf"{_NUM}\s*Baños?|Baños?\s*:?\s*{_NUM}", re.I)
M2 = re.compile(r"(?<![\d.,])(\d[\d.,]*)\s*m\s*(?:2|²)(?![\d])", re.I)
M2_ETIQ = re.compile(r"([A-Za-zÁÉÍÓÚáéíóúñ ]{4,40}):\s*(\d[\d.,]*)\s*m\s*(?:2|²)", re.I)
GASTOS = re.compile(r"(?:Gastos comunes|Expensas)\s*:?\s*(U\$S|USD|UYU|\$U|\$)?\s*([\d.,]+)", re.I)
IMG = re.compile(r"""(https?://static\.tokkobroker\.com/[^"'\s)]+\.(?:jpe?g|png|webp))""", re.I)
DEPTOS = {N.simple(d): d for d in N.DEPARTAMENTOS}


def _texto(fragmento: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragmento, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _precio(txt: str) -> tuple[float | None, str | None]:
    p = PRECIO.search(txt)
    if not p:
        return None, None
    v = N.numero(p.group(2).replace(" ", " "))
    if v is None or v < 100:  # "$ 0", restos
        return None, None
    return v, N.moneda_norm(p.group(1))


def _tipo(*textos: str | None) -> str | None:
    """N.tipo_propiedad + los nombres que usa Tokko y que normalize no conoce ("Departamento", "PH")."""
    t = N.tipo_propiedad(*textos)
    if t:
        return t
    s = N.simple(" ".join(x for x in textos if x))
    if re.search(r"\b(departamento|departamentos|ph|dpto|depto)\b", s):
        return "apartamento"
    return None


def _depto(*textos: str | None) -> str | None:
    for t in textos:
        s = N.simple(t)
        for k, v in DEPTOS.items():
            if re.search(rf"\b{k}\b", s):
                return v
    return None


def _medidas(txt: str, tipo: str | None) -> dict:
    """dormitorios, baños y m² a partir del texto de una tarjeta."""
    d = re.search(rf"Dormitorios?\s*:\s*{_NUM}", txt, re.I) or DORM.search(txt)
    b = re.search(rf"Baños?\s*:\s*{_NUM}", txt, re.I) or BANO.search(txt)
    edif = tot = None
    for etiq, val in M2_ETIQ.findall(txt):
        v, e = N.numero(val), N.simple(etiq)
        if not v or v < 10:
            continue
        if re.search(r"cubiert|constru|edific|propia|interior", e):
            edif = edif or v
        elif re.search(r"total|terreno|lote|solar", e):
            tot = tot or v
    if edif is None and tot is None:
        m2s = [v for v in (N.numero(x) for x in M2.findall(txt)) if v and v >= 10]
        if tipo in ("terreno", "chacra_campo"):
            tot = max(m2s) if m2s else None
        elif m2s:
            edif, tot = min(m2s), (max(m2s) if len(m2s) > 1 and max(m2s) != min(m2s) else None)
    g = GASTOS.search(txt)
    return {
        "dormitorios": int(d.group(1) or d.group(2)) if d else None,
        "banos": int(b.group(1) or b.group(2)) if b else None,
        "m2_edificados": edif,
        "m2_totales": tot,
        "gastos_comunes": N.numero(g.group(2)) if g else None,
    }


def _imagenes(bloque: str) -> list[str]:
    return list(dict.fromkeys(IMG.findall(bloque)))[:3]


def _tarjetas(html: str, inicio: re.Pattern, link: re.Pattern) -> list[tuple[str, re.Match]]:
    """Corta el HTML en tarjetas (desde cada `inicio` hasta el siguiente) y devuelve las que tienen link a aviso."""
    pos = [m.start() for m in inicio.finditer(html)]
    out, vistos = [], set()
    for i, p in enumerate(pos):
        bloque = html[p:pos[i + 1] if i + 1 < len(pos) else min(len(html), p + 15000)]
        m = link.search(bloque)
        if m and m.group(1) not in vistos:
            vistos.add(m.group(1))
            out.append((bloque, m))
    return out


def _total(html: str, rx: str) -> int | None:
    m = re.search(rx, html, re.I)
    return int(re.sub(r"\D", "", m.group(1))) if m else None


# Las webs tfw son dominios distintos pero el mismo servidor de Tokko: con varias en paralelo (el scraper corre
# muchas fuentes a la vez) responde 429 enseguida. Entre TODAS las fuentes tfw del proceso se hace un pedido a la
# vez, con TFW_PAUSA segundos entre pedidos, y ante un 429 se espera y se reintenta.
TFW_PAUSA = 1.0
TFW_ESPERAS_429 = (20, 60, 120)
_tfw_lock: dict[int, asyncio.Lock] = {}  # uno por event loop
_tfw_ultimo = [0.0]


class _Ctx:
    """Cuenta pedidos y corta por tiempo / max_paginas."""

    def __init__(self, cli, limite_ts: float, max_paginas: int | None):
        self.cli, self.limite, self.maxp, self.paginas = cli, limite_ts, max_paginas, 0
        self.registros: dict[str, dict] = {}
        self.cortes: list[str] = []  # páginas que fallaron (para diagnóstico)
        self.tfw = False  # True: pedidos coordinados con las demás fuentes tfw

    def agotado(self) -> bool:
        return time.monotonic() > self.limite or bool(self.maxp and self.paginas >= self.maxp)

    async def get(self, url: str):
        self.paginas += 1
        if not self.tfw:
            return await self.cli.get(url)
        lock = _tfw_lock.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
        for espera in (*TFW_ESPERAS_429, None):
            async with lock:
                falta = TFW_PAUSA - (time.monotonic() - _tfw_ultimo[0])
                if falta > 0:
                    await asyncio.sleep(falta)
                r = await self.cli.get(url)
                _tfw_ultimo[0] = time.monotonic()
                if r.status == 429 and espera is not None and time.monotonic() + espera < self.limite:
                    await asyncio.sleep(espera)  # con el lock tomado: frena a todas las fuentes tfw
                    continue
            return r
        return r


async def _paginar(ctx: _Ctx, url_pagina, parse, fin_rx: str | None = None,
                   cortar_repetidas: bool = True) -> tuple[set[str], bool]:
    """Recorre páginas 1..N mientras aparezcan avisos nuevos. Devuelve (ids vistos, terminó bien)."""
    ids: set[str] = set()
    for n in range(1, MAX_PAGINAS + 1):
        if ctx.agotado():
            return ids, False
        r = await ctx.get(url_pagina(n))
        if r.status != 200:
            ctx.cortes.append(f"{url_pagina(n)}: {r.status or r.error}")
            return ids, (r.status == 404 and n > 1)
        if fin_rx and re.search(fin_rx, r.texto):
            return ids, True
        nuevos = parse(r.texto, r.url_final or url_pagina(n))
        antes = len(ids)
        for d in nuevos:
            ids.add(d["id_externo"])
            ctx.registros.setdefault(d["url"], d)
        if not nuevos or (cortar_repetidas and len(ids) == antes):  # página vacía o repetida: fin
            if n > 1 and len(r.texto) > 1000 and not nuevos:
                ctx.cortes.append(f"{url_pagina(n)}: página sin avisos ({len(r.texto)} bytes)")
            return ids, True
    return ids, False


# ------------------------------------------------------------------------------- 1. Tokko Web Framework (tfw)
TFW_CARD = re.compile(r"""<[a-z]+[^>]*\sprop-id=["']\d+["']""", re.I)
TFW_LINK = re.compile(r"""href=["'](/p/(\d+)-[^"']*)["']""")
TFW_ALT = re.compile(r"""alt=["']Foto ([^"']+)["']""")
TFW_ALT_PARTES = re.compile(r"^(.*?) en ((?:Venta|Alquiler(?: temporario)?)(?:\s*(?:y|,|/)\s*(?:Venta|Alquiler(?: temporario)?))*)"
                            r"\s+en (.+)$", re.I)
# localidades de varias palabras que Tokko usa como "ciudad" (padre del barrio)
CIUDADES = ["Punta del Este", "Ciudad de la Costa", "Costa de Oro", "Colonia del Sacramento", "José Ignacio",
            "San Carlos", "La Barra", "Las Piedras", "Ciudad de Plata", "Countries/B.Cerrados"]
TFW_OPS = [("Venta", "venta"), ("Alquiler", "alquiler"), ("Alquiler-temporario", "alquiler_temporal")]


def _tfw_ubicacion(alt_loc: str, slug: str, barrio_hint: str | None = None) -> tuple[str, str | None]:
    """'Malvín, Montevideo Atlántico y Rubens' + slug '...-en-Malvín-Atlántico-y-Rubens' -> ('Malvín, Montevideo',
    'Montevideo'). El alt junta la ciudad con la dirección; la dirección se reconoce porque también está en el slug."""
    barrio, _, resto = alt_loc.partition(",")
    barrio, resto = barrio.strip(), resto.strip()
    ciudad = resto
    if resto:
        m = re.search(r"-en-(?:Venta|Alquiler)(?:-temporario)?(?:-y-(?:Venta|Alquiler)(?:-temporario)?)*-en-", slug, re.I)
        cola = slug[m.end():] if m else slug.split("-en-")[-1]
        pal_b = barrio.split()
        pal = [p for p in cola.split("-") if p]
        if N.simple(" ".join(pal[:len(pal_b)])) == N.simple(barrio):
            pal = pal[len(pal_b):]
        cw = resto.split()
        # la dirección es la cola del alt y también la cola del slug
        if pal and len(pal) < len(cw) and N.simple(" ".join(cw[-len(pal):])) == N.simple(" ".join(pal)):
            ciudad = " ".join(cw[:-len(pal)])
        if ciudad == resto:  # no se pudo separar: "(Depto)" o un departamento al principio, o las 2 primeras palabras
            dep = re.match(r"^([^()]{0,40}\))", resto)  # "Countries/B.Cerrados (Canelones) Dirección"
            pref = next((d for d in CIUDADES + N.DEPARTAMENTOS if N.simple(resto).startswith(N.simple(d) + " ")), None)
            ciudad = dep.group(1) if dep else (pref or " ".join(cw[:2]))
    ubic = ", ".join(x for x in (barrio, ciudad.strip(" ,")) if x)
    return ubic, _depto(ciudad)


def _tfw_parse(html: str, base: str, operacion: str) -> list[dict]:
    out = []
    for bloque, m in _tarjetas(html, TFW_CARD, TFW_LINK):
        path, id_ = m.group(1), m.group(2)
        url = base + path
        txt = _texto(bloque)
        alt = TFW_ALT.search(bloque)
        alt = H.unescape(alt.group(1)) if alt else ""
        partes = TFW_ALT_PARTES.match(alt)
        tipo_txt = partes.group(1) if partes else alt.split(" en ")[0]
        ubic, depto = _tfw_ubicacion(partes.group(3), path.split("/")[-1]) if partes else (None, None)
        h = re.search(r"<h[2-5][^>]*>(.*?)</h[2-5]>", bloque, re.S)
        titulo = _texto(h.group(1)) if h else None
        if not titulo:
            dir_ = re.search(r'class="prop-desc-dir"[^>]*>(.*?)</div>', bloque, re.S)
            titulo = alt or (_texto(dir_.group(1)) if dir_ else None)
        # el precio va en su propio elemento; buscarlo fuera del alt/title
        precio, moneda = _precio(txt)
        tipo = _tipo(tipo_txt) or _tipo(titulo)
        desc = re.search(r'class="[^"]*description[^"]*"[^>]*>(.*?)</p>', bloque, re.S)
        d = {
            "url": url, "id_externo": id_, "titulo": titulo, "precio": precio, "moneda": moneda,
            "operacion": operacion, "tipo": tipo, "ubicacion_txt": ubic, "departamento": depto,
            "imagenes": _imagenes(bloque), "descripcion": (_texto(desc.group(1)) if desc else txt)[:400],
            "breadcrumb": f"{operacion} > {tipo_txt}",
        }
        d.update(_medidas(txt, tipo))
        out.append(d)
    return out


async def _listar_tfw(ctx: _Ctx, base: str, home: str) -> bool:
    # Venta y Alquiler siempre (algunas home no enlazan /Venta); temporario solo si aparece en el menú
    ops = TFW_OPS[:2] + [(p, o) for p, o in TFW_OPS[2:] if re.search(rf"""href=["'](?:{re.escape(base)})?/{p}["']""", home)]
    completo = True
    for path, op in ops:
        total: list[int | None] = [None]

        def parse(texto, url, op=op, total=total):
            if total[0] is None:
                total[0] = _total(texto, r"([\d.]+)\s+Resultados")
            return _tfw_parse(texto, base, op)

        # al final el sitio responde "--NoMoreProperties--" (página sin avisos)
        ids, ok = await _paginar(ctx, lambda n, p=path: f"{base}/{p}?o=2,2&p={n}", parse)
        if ok and total[0] is not None and total[0] - 3 * len(ids) // 100 - 5 <= len(ids) < total[0]:
            # Faltan unos pocos: el orden "última actualización" se mueve si editan un aviso durante la corrida
            # (y hay empates). Una segunda pasada ordenada por precio suele completar el conjunto.
            ids2, ok = await _paginar(ctx, lambda n, p=path: f"{base}/{p}?o=1,1&p={n}", parse, cortar_repetidas=False)
            ids |= ids2
        # Sin el total o con menos avisos que los anunciados, no se pueden dar bajas por buenas. Tolerancia mínima:
        # algunos sitios anuncian 1-3 avisos más de los que muestran en cualquier orden (Abate: 875 vs 874 tras
        # las dos pasadas), y esos nunca se vieron, así que no generan bajas falsas.
        if not ok or total[0] is None or len(ids) < total[0] - max(2, total[0] * 3 // 1000):
            completo = False
    return completo


# ------------------------------------------------------------------------------- 2. perfiles a medida
TERRAMAR_OPS = [(1, "venta"), (2, "alquiler"), (3, "alquiler_temporal")]


async def _listar_terramar(ctx: _Ctx, base: str, home: str) -> bool:
    card = re.compile(r'<div class="property-item">')
    link = re.compile(r"""href=["'](?:https?://[^"']+/)?propiedad\.php\?ref=([A-Z]{1,4}\d{2,9})["']""")

    def parse(texto, url, op_lista):
        out = []
        for bloque, m in _tarjetas(texto, card, link):
            ref = m.group(1)
            txt = _texto(bloque)
            tit = re.search(r'property--title"[^>]*>(.*?)</h5>', bloque, re.S)
            loc = re.search(r'property--location"[^>]*>(.*?)</p>', bloque, re.S)
            pr = re.search(r'property--price"[^>]*>(.*?)</p>', bloque, re.S)
            tit, loc = (_texto(tit.group(1)) if tit else ""), (_texto(loc.group(1)) if loc else "")
            precio, moneda = _precio(_texto(pr.group(1)) if pr else "")
            tipo = _tipo(tit) or _tipo(loc)
            feats = {N.simple(k): _texto(v) for k, v in re.findall(
                r'class="feature">(.*?)</span>\s*<span class="feature-num">(.*?)</span>', bloque, re.S)}
            ent = lambda k: int(feats[k]) if feats.get(k, "").isdigit() else None  # noqa: E731
            sup = N.numero(re.sub(r"[^\d.,]", "", feats.get("sup", "").split("m")[0])) if feats.get("sup") else None
            # "DEPARTAMENTO EN ALQUILER - DELAMAR 209 - La Barra": la zona es el último tramo
            zona = [p.strip() for p in re.split(r"\s+-\s+", loc) if p.strip()]
            d = {"url": f"{base}/propiedad.php?ref={ref}", "id_externo": ref, "titulo": loc or tit,
                 "precio": precio, "moneda": moneda, "operacion": op_lista, "tipo": tipo,
                 "ubicacion_txt": (zona[-1] if zona else None), "departamento": _depto(loc),
                 "imagenes": _imagenes(bloque), "descripcion": txt[:400], "breadcrumb": f"{op_lista} > {tit}",
                 "dormitorios": ent("hab"), "banos": ent("banos"),
                 "m2_edificados": None if tipo in ("terreno", "chacra_campo") else (sup if sup and sup >= 10 else None),
                 "m2_totales": (sup if sup and sup >= 10 else None) if tipo in ("terreno", "chacra_campo") else None,
                 "gastos_comunes": None}
            out.append(d)
        return out

    completo = True
    for cod, op in TERRAMAR_OPS:
        _, ok = await _paginar(ctx, lambda n, c=cod: f"{base}/busqueda.php?operation_types={c}&page={n}",
                               lambda t, u, op=op: parse(t, u, op))
        completo = completo and ok
    return completo


async def _listar_afilia(ctx: _Ctx, base: str, home: str) -> bool:
    card = re.compile(r'<article class="af-card')
    link = re.compile(r"""href=["'](?:https?://[^"'/]+)?/p/(\d+)-[^"']*["']""")
    total: list[int | None] = [None]

    def parse(texto, url):
        if total[0] is None:
            total[0] = _total(texto, r"([\d.]+)\s+resultados")
        out = []
        for bloque, m in _tarjetas(texto, card, link):
            id_ = m.group(1)
            href = re.search(r"""href=["']([^"']*/p/%s-[^"']*)["']""" % id_, bloque).group(1)
            txt = _texto(bloque)
            tag = re.search(r'class="af-tag">(.*?)</div>', bloque, re.S)
            h3 = re.search(r"<h3>(.*?)</h3>", bloque, re.S)
            strong = re.search(r"<strong>(.*?)</strong>", bloque, re.S)
            strong = _texto(strong.group(1)) if strong else ""
            titulo = _texto(h3.group(1)) if h3 else None
            precio, moneda = _precio(strong)
            sub = re.search(r"</h3>\s*<p>(.*?)</p>", bloque, re.S)
            tipo = _tipo(_texto(sub.group(1)) if sub else "") or _tipo(titulo)
            op = N.operacion(strong.split("·")[0]) or N.operacion(titulo)
            ubic = _texto(tag.group(1)) if tag else None
            d = {"url": urljoin(base + "/", href), "id_externo": id_, "titulo": titulo, "precio": precio,
                 "moneda": moneda, "operacion": op, "tipo": tipo, "ubicacion_txt": ubic,
                 "departamento": _depto(ubic), "imagenes": _imagenes(bloque), "descripcion": txt[:400],
                 "breadcrumb": f"{op or ''} > {tipo or ''}"}
            d.update(_medidas(txt, tipo))
            out.append(d)
        return out

    ids, ok = await _paginar(ctx, lambda n: f"{base}/propiedades/" + (f"?pagina={n}" if n > 1 else ""), parse)
    return ok and total[0] is not None and len(ids) >= total[0]


PEDROZO_TIPOS = ["casas", "apartamentos", "chacras", "terrenos", "locales", "campos", "hoteles", "ph"]


async def _listar_pedrozo(ctx: _Ctx, base: str, home: str) -> bool:
    card = re.compile(r'<div class="col-xs-12 col-sm-12 col-md-4">')
    link = re.compile(r"""href=["'](/[^"'/]*\.(\d{2,9})\.(\d))["']""")
    completo = True
    for seccion, op in (("ventas", "venta"), ("alquileres", "alquiler_temporal")):
        for t in PEDROZO_TIPOS:
            def parse(texto, url, op=op, t=t):
                out = []
                for bloque, m in _tarjetas(texto, card, link):
                    path, id_ = m.group(1), m.group(2)
                    txt = _texto(bloque)
                    tit = re.search(r'class="titulo">(.*?)</div>', bloque, re.S)
                    tit = _texto(tit.group(1)) if tit else None
                    pr = re.search(r'class="precio">(.*?)</div>', bloque, re.S)
                    precio, moneda = _precio(_texto(pr.group(1)) if pr else "")
                    tipo = _tipo(t)
                    edif = re.search(r'title="M2 (?:edificado|propio)".*?dato">(.*?)</div>', bloque, re.S)
                    terr = re.search(r'title="M2 terreno".*?dato">(.*?)</div>', bloque, re.S)
                    dorm = re.search(r'title="Dormitorios".*?dato">(.*?)</div>', bloque, re.S)
                    desc = re.search(r'class="text">(.*?)</div>', bloque, re.S)
                    def num(x):
                        v = re.search(r"\d[\d.,]*", _texto(x.group(1))) if x else None
                        return N.numero(v.group(0)) if v else None
                    dn = num(dorm)
                    d = {"url": base + path, "id_externo": id_, "titulo": tit, "precio": precio, "moneda": moneda,
                         "operacion": op, "tipo": tipo, "ubicacion_txt": tit, "departamento": None,
                         "imagenes": _imagenes(bloque), "descripcion": (_texto(desc.group(1)) if desc else txt)[:400],
                         "breadcrumb": f"{seccion} > {t}"}
                    d.update(_medidas(txt, tipo))
                    d.update({"dormitorios": int(dn) if dn else None, "m2_edificados": num(edif) or None,
                              "m2_totales": num(terr) or None})
                    if tipo in ("terreno", "chacra_campo"):
                        d["m2_edificados"] = None
                    out.append(d)
                return out

            ids, ok = await _paginar(
                ctx, lambda n, s=seccion, t=t: f"{base}/{s}/de/{t}" + (f"/pagina/{n}" if n > 1 else ""), parse)
            completo = completo and ok
            if ctx.agotado():
                return False
    return completo


SCROLLINI_OPS = [("venta", "venta"), ("alquiler", "alquiler"), ("alquiler-temporario", "alquiler_temporal")]
# códigos de referencia de Tokko: <sucursal><tipo><id>, ej. HAP3525917 = apartamento
TOKKO_COD_TIPO = {"AP": "apartamento", "HO": "casa", "LA": "terreno", "OF": "oficina", "LO": "local",
                  "GA": "garaje", "PH": "apartamento"}


async def _listar_scrollini(ctx: _Ctx, base: str, home: str) -> bool:
    card = re.compile(r'<div class="card shadow-sm')
    link = re.compile(r"""href=["'](?:https?://[^"'/]+)?/propiedad/([a-z-]+/(\d+)-\d+)["']""")
    completo = True
    for path, op in SCROLLINI_OPS:
        def parse(texto, url, op=op):
            out = []
            for bloque, m in _tarjetas(texto, card, link):
                rel, id_ = m.group(1), m.group(2)
                txt = _texto(bloque)
                loc = re.search(r'fi-map-pin[^>]*></i>(.*?)</div>', bloque, re.S)
                tit = re.search(r"<h3[^>]*>(.*?)</h3>", bloque, re.S)
                pr = re.search(r'<p class="lead[^"]*">(.*?)</p>', bloque, re.S)
                cod = re.search(r"COD\.\s*([A-Z0-9 ]+)<", bloque)
                tipo = None
                if cod:
                    c = re.search(rf"\b[A-Z]?([A-Z]{{2}}){id_}\b", cod.group(1))
                    tipo = TOKKO_COD_TIPO.get(c.group(1)) if c else None
                titulo = _texto(tit.group(1)) if tit else None
                tipo = tipo or _tipo(titulo)
                precio, moneda = _precio(_texto(pr.group(1)) if pr else "")
                ubic = _texto(loc.group(1)) if loc else None
                d = {"url": f"{base}/propiedad/{rel}", "id_externo": id_, "titulo": titulo, "precio": precio,
                     "moneda": moneda, "operacion": op, "tipo": tipo, "ubicacion_txt": ubic,
                     "departamento": _depto(ubic), "imagenes": _imagenes(bloque), "descripcion": txt[:400],
                     "breadcrumb": f"{op} > {tipo or ''}"}
                d.update(_medidas(txt, tipo))
                out.append(d)
            return out

        ids, ok = await _paginar(ctx, lambda n, p=path: f"{base}/propiedades/{p}" + (f"?page={n}" if n > 1 else ""),
                                 parse)
        completo = completo and ok
    return completo


async def _listar_redhub(ctx: _Ctx, base: str, home: str) -> bool:
    """El listado solo muestra destacadas: se usa el sitemap y se abre cada ficha (JSON-LD)."""
    r = await ctx.get(base + "/sitemap.xml")
    if r.status != 200:
        raise RuntimeError(f"sin sitemap en {base}: {r.status or r.error}")
    urls = list(dict.fromkeys(H.unescape(u) for u in re.findall(r"<loc>\s*([^<]*/propiedad/[^<]+?)\s*</loc>", r.texto)))
    if not urls:
        return False
    completo = True
    for u in urls:
        if ctx.agotado():
            return False
        f = await ctx.get(u)
        if f.status != 200:
            completo = False  # puede ser una baja reciente que el sitemap todavía lista: no arriesgar
            continue
        d = _ficha_jsonld(f.texto, u)
        if d:
            ctx.registros.setdefault(d["url"], d)
    return completo


def _ficha_jsonld(texto: str, url: str) -> dict | None:
    datos, migas = None, []
    for bloque in re.findall(r'<script type="application/ld\+json">(.*?)</script>', texto, re.S):
        try:
            j = json.loads(bloque)
        except ValueError:
            continue
        if j.get("@type") == "BreadcrumbList":
            migas = [e.get("name", "") for e in j.get("itemListElement", [])]
        elif j.get("@type") in ("RealEstateListing", "Product", "Offer", "Residence", "House", "Apartment"):
            datos = j
    if not datos:
        return None
    m = re.search(r"/propiedad/([A-Z]*\d+)", url)
    oferta = datos.get("offers") or {}
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else {}
    precio = oferta.get("price")
    try:
        precio = float(precio) if precio not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        precio = None
    migas = [x for x in migas if x and x != "Inicio" and not x.startswith("Ref.")]
    nombre = H.unescape(datos.get("name") or "")
    tipo = _tipo(migas[1] if len(migas) > 1 else "") or _tipo(nombre)
    desc = H.unescape(datos.get("description") or "")
    ubic = ", ".join(reversed(migas[2:])) if len(migas) > 2 else None  # "Punta Gorda, Montevideo"
    img = datos.get("image")
    d = {"url": url, "id_externo": m.group(1) if m else None, "titulo": nombre or None, "precio": precio,
         "moneda": N.moneda_norm(oferta.get("priceCurrency")) if precio else None,
         "operacion": N.operacion(migas[0] if migas else "") or N.operacion(nombre),
         "tipo": tipo, "ubicacion_txt": ubic, "departamento": _depto(migas[2] if len(migas) > 2 else ""),
         "imagenes": ([img] if isinstance(img, str) else list(img or []))[:3], "descripcion": desc[:400],
         "breadcrumb": " > ".join(migas)}
    d.update(_medidas(nombre + " " + desc, tipo))
    return d


PERFILES = {
    "terramar.com.uy": _listar_terramar,
    "afiliapropiedades.uy": _listar_afilia,
    "emilianopedrozo.com": _listar_pedrozo,
    "scrollinipropiedades.com": _listar_scrollini,
    "redhubinmobiliario.com": _listar_redhub,
}
NO_SOPORTADOS = {
    "kwuruguay.com": "la paginación del listado es por POST (ASP.NET __doPostBack)",
    "perezrobaudo.com": "el listado se arma en el navegador con la API de Tokko (clave de la inmobiliaria)",
}

# ------------------------------------------------------------------------------- interfaz del adapter
AVISO_RX = re.compile(r"/p/\d+-|propiedad\.php\?ref=[A-Z]+\d+|/propiedad/(?:[a-z-]+/\d+-\d+|[A-Z]*\d+/)|"
                      r"/[^/]+\.\d{2,9}\.\d$|detalle\.aspx\?id=TK[A-Z]\d+")


def es_aviso(url: str) -> bool:
    p = urlsplit(url)
    return bool(AVISO_RX.search(p.path + ("?" + p.query if p.query else "")))


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    host = _host(base)
    if host in NO_SOPORTADOS:
        raise RuntimeError(f"{host}: no soportado ({NO_SOPORTADOS[host]})")
    ctx = _Ctx(cli, limite_ts, max_paginas)
    r = await ctx.get(base + "/")
    if r.status != 200:
        raise RuntimeError(f"no abre la home de {base}: {r.status or r.error}")
    base = _base(r.url_final or base)  # www. / https
    if host in PERFILES:
        completo = await PERFILES[host](ctx, base, r.texto)
    elif "tfwListingUrl" in r.texto or "static.tokkobroker.com/tfw" in r.texto:
        ctx.tfw = True
        completo = await _listar_tfw(ctx, base, r.texto)
    else:
        raise RuntimeError(f"{host}: no se reconoce la plantilla Tokko (ni tfw ni perfil propio)")
    return ctx.registros, completo and not ctx.agotado()
