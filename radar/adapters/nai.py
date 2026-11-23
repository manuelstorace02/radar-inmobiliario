"""nai (CRM inmobiliario de Sodio, uruguayo) — adapter de PLATAFORMA para las webs de inmobiliarias hechas con nai
(Kosak, Equilibrio, Monymar, Vargas, Pointer, Casa Victorica, ...; casi todas de Montevideo). Ver docs/INMOBILIARIAS.md.

Se usa con fuentes cuyo id empieza con "nai-" (ej. nai-kosak) y modo "adapter".

Cómo son estos sitios (fotos en publico.nai.com.uy/<id_inmobiliaria>/...):
  - buscador en /propiedades/<Operación>/U$S/Sin_precio_mínimo/Sin_precio_máximo/Todos_los_tipos/Todas_las_zonas/
    Todos_los_tipos_agrupadores/Todos_los_estados[/orden][/Página-N]. buscador.js solo arma esa URL: el listado
    viene en el HTML, 12 tarjetas por página (precio, barrio, dormitorios, baños, m²), aviso en /propiedad/<ID>
    (en Vargas el ID lleva letra: /propiedad/P9342). La moneda "U$S" de la URL no filtra (solo es la del rango).
  - Algunas plantillas (Equilibrio, Monymar, Vonhaus) meten además en la página una llamada
    mapa_listado([12 de la página], [fotos], [TODAS las propiedades de la búsqueda], [fotos], [proyectos], ...)
    con el registro completo de cada propiedad (precio, moneda, zona, coordenadas, m², gastos comunes...).
    Si está, con UN pedido a "Venta-Alquiler" se tiene todo el stock y no hace falta paginar.
  - Otras plantillas: Yasmine Coccaro (plantilla vieja, mismo buscador), Pointer (/propiedades?page=N, ignora el
    buscador) y Casa Victorica (rediseño propio: /propiedades/pagina/N, aviso /propiedad/ID/operacion/venta).
    El parser de tarjetas no depende de la plantilla: corta el HTML en tarjetas y busca precio, ubicación,
    dormitorios, baños y m² con rótulos (title="Habitaciones", íconos fa-bed, "3 D.", "2 dormitorios"...).
  - Monymar, Paulo del Río y Yasmine Coccaro piden en robots.txt "Crawl-delay: 30": se respeta (pausa extra).
"""
from __future__ import annotations

import asyncio
import html as H
import json
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from radar import normalize as N

PLATAFORMA = True  # lo usan todas las fuentes "nai-..."

BUSCADOR = "{prefijo}/{op}/U$S/Sin_precio_mínimo/Sin_precio_máximo/Todos_los_tipos/Todas_las_zonas/Todos_los_tipos_agrupadores"
LINK_BUSCADOR = re.compile(r"""href=["']\s*(?:https?://[^"'/]+)?(/(?:proyectos-)?propiedades)/(Venta-Alquiler|Venta|Alquiler_temporal|Alquiler)/"""
                           r"""U\$S/[^"']*["']""")
MAX_PAGINAS = 300
CRAWL_DELAY_MAX = 30.0  # se respeta el Crawl-delay de robots.txt hasta este tope

ID_RX = r"[A-Z]?\d{1,7}"
AVISO = re.compile(rf"""href=["']\s*((?:https?://[^"'/]+)?/propiedad/({ID_RX}))(?:-[^"'/]*)?(?:/operacion/(venta|alquiler)\w*)?/?["']""",
                   re.I)
PAGINA = re.compile(r"""href=["']([^"']*?(?:P%C3%A1gina-|Página-|[?&]page=|/pagina/))(\d+)([^"']*)["']""", re.I)
INICIO_TARJETA = re.compile(r"""<(?:div|article)\b[^>]*\bclass=["'](?:card|thumbnail|propiedad_cont|property-card)(?=[\s"'])""",
                            re.I)
PRECIO = re.compile(r"(U\$S|US\$|USD|U\$D|\$U|UYU|\$)\s*([\d][\d.,]*)")
NUM = r"(?<![\d.,])(\d{1,2})(?![\d.,])"
DORM = re.compile(rf"\[dorm\]\s*{NUM}|{NUM}\s*(?:D\.|Dormitorios?|Dorms?\b|Habitaci)", re.I)
BANO = re.compile(rf"\[bano\]\s*{NUM}|{NUM}\s*(?:B\.|Baños?\b)", re.I)
M2 = r"(\d[\d.,]*)\s*(m\s*(?:2|²)|ha\b)"
M2_ED = re.compile(r"\[m2ed\]\s*" + M2, re.I)
M2_TE = re.compile(r"\[m2te\]\s*" + M2, re.I)
M2_ANY = re.compile(r"(?<![\d.,])" + M2, re.I)
# rótulos de las plantillas -> marcas en el texto
ROTULOS = [
    (re.compile(r"""<[^>]+(?:title|alt)=["'](?:Habitaciones|Dormitorios)["'][^>]*>|<i[^>]+fa-bed[^>]*>""", re.I), " [dorm] "),
    (re.compile(r"""<[^>]+(?:title|alt)=["']Baños["'][^>]*>|<i[^>]+fa-bath[^>]*>""", re.I), " [bano] "),
    (re.compile(r"""<[^>]+title=["']Superficie (?:Construida|edificada)["'][^>]*>|<i[^>]+fa-home[^>]*>""", re.I), " [m2ed] "),
    (re.compile(r"""<[^>]+title=["']Superficie (?:Terreno|total)["'][^>]*>""", re.I), " [m2te] "),
    (re.compile(r"""<[^>]+title=["']Superficie["'][^>]*>""", re.I), " [m2] "),
    (re.compile(r"""<[^>]+title=["']Superficie (?:terraza|balc[oó]n\w*)["'][^>]*>""", re.I), " [m2terraza] "),
    (re.compile(r"""<[^>]+(?:title|alt)=["']Garag?e["'][^>]*>|<i[^>]+fa-car[^>]*>""", re.I), " [garaje] "),
]
UBICACION = [
    re.compile(r"""(?:la|fa)-map-marker[^>]*>\s*</i>\s*([^<]{2,120})<""", re.I),              # plantilla clásica
    re.compile(r"""ubicacion-propiedad.*?<span class=["']lh-1["']>\s*([^<]{2,120})<""", re.I | re.S),  # Pointer
    re.compile(r"""class=["']ubicacion[^"']*["']>\s*([^<]{2,120})<""", re.I),                    # Yasmine
    re.compile(r"""<p class=["']eyebrow[^"']*["']>\s*([^<]{2,120})</p>""", re.I),                 # Casa Victorica
]
TITULO = re.compile(r"""<(h[2-5])\b[^>]*>(.*?)</\1>""", re.I | re.S)
TIPO_BADGE = re.compile(r"""tipo-propiedad.*?<span class=["']lh-1["']>\s*([^<]{2,60})<""", re.I | re.S)


def es_aviso(url: str) -> bool:
    return bool(re.search(rf"/propiedad/{ID_RX}(?:-[^/]*)?(?:/operacion/[a-z_]+)?/?$", urlsplit(url).path, re.I))


def _texto(fragmento: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<!--.*?-->", " ", fragmento, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _int(m) -> int | None:
    if not m:
        return None
    v = next((g for g in m.groups() if g), None)
    return int(v) if v else None


def _sup(num: str, unidad: str) -> float | None:
    v = N.numero(num)
    if v and unidad.lower().startswith("ha"):
        v *= 10000
    return v if v and v >= 10 else None


def _m2(rx, txt) -> float | None:
    m = rx.search(txt)
    return _sup(m.group(1), m.group(2)) if m else None


def _m2s(rx, txt) -> list[float]:
    return [v for v in (_sup(a, b) for a, b in rx.findall(txt)) if v]


def _precio(txt: str) -> tuple[float | None, str | None]:
    p = PRECIO.search(txt)
    if not p:
        return None, None
    v = N.numero(p.group(2))
    if v is None or v < 100:  # "U$S 1", "$ 0": precio a consultar
        return None, None
    return v, N.moneda_norm(p.group(1))


# ------------------------------------------------------------------ tarjetas del listado (HTML)
def _bloques(html: str) -> list[tuple[str, str, str | None]]:
    """[(id, bloque_html, operacion_de_la_url)] de una página de listado."""
    inicios = [m.start() for m in INICIO_TARJETA.finditer(html)]
    out, vistos = [], set()
    if inicios:
        inicios.append(len(html))
        for a, b in zip(inicios, inicios[1:]):
            bloque = html[a:min(b, a + 20000)]
            m = AVISO.search(bloque)
            if m and m.group(2) not in vistos:
                vistos.add(m.group(2))
                out.append((m.group(2), bloque, m.group(3)))
        if out:
            return out
    # plantilla desconocida: del primer link de un aviso al primer link del siguiente (como tera.py)
    hits = []
    for m in AVISO.finditer(html):
        if m.group(2) not in vistos:
            vistos.add(m.group(2))
            hits.append((m.start(), m.group(2), m.group(3)))
    for n, (pos, id_, op) in enumerate(hits):
        fin = hits[n + 1][0] if n + 1 < len(hits) else min(len(html), pos + 8000)
        out.append((id_, html[html.rfind("<", 0, pos):fin], op))
    return out


def tarjetas(html: str, base: str, operacion: str | None) -> dict[str, dict]:
    out = {}
    for id_, bloque, op_url in _bloques(html):
        marcado = bloque
        for rx, marca in ROTULOS:
            marcado = rx.sub(marca, marcado)
        txt = _texto(marcado)
        precio, moneda = _precio(txt)
        ubic = None
        for rx in UBICACION:
            m = rx.search(bloque)
            if m and _texto(m.group(1)):
                ubic = _texto(m.group(1))
                break
        titulo = None
        for _, t in TITULO.findall(bloque):
            t = _texto(t)
            if t and not PRECIO.search(t):
                titulo = t[:200]
                break
        if not titulo:
            alt = re.search(r"""<img[^>]+alt=["']([^"']{4,200})["']""", bloque)
            titulo = H.unescape(alt.group(1)).strip() if alt else ubic
        tb = TIPO_BADGE.search(bloque)
        tipo = N.tipo_propiedad(tb.group(1)) if tb else None
        ub2 = re.search(r"""class=["']ubicacion[^"']*["']>\s*([^<]{2,120})<""", bloque)
        tipo = tipo or N.tipo_propiedad(ubic, titulo, ub2.group(1) if ub2 else None) or ("garaje" if re.search(r"(?i)\bgarage", titulo or "") else None)
        op = operacion or N.operacion(op_url, txt[:300]) or None
        if precio is None and re.search(r"(?i)consultar\s+alquiler", txt) and not operacion:
            op = "alquiler"
        terreno = tipo in ("terreno", "chacra_campo")
        m2ed, m2te = _m2(M2_ED, txt), _m2(M2_TE, txt)
        otros = _m2s(re.compile(r"\[m2\]\s*" + M2, re.I), txt)
        if m2ed is None and m2te is None:  # sin rótulos: "120 m²  300 m²"
            todos = _m2s(M2_ANY, re.sub(r"\[m2terraza\]\s*" + M2, " ", txt, flags=re.I))
            if todos:
                if terreno:
                    m2te = max(todos)
                else:
                    m2ed = min(todos)
                    m2te = max(todos) if len(todos) > 1 else None
        elif m2te is None and otros and not terreno:
            m2te = max(otros) if max(otros) != m2ed else None
        if terreno and m2te is None:
            m2te, m2ed = m2ed or (max(otros) if otros else None), None
        img = re.search(r"""(?:<img[^>]+src=|url\()\s*["']?(https?://[^"')\s]+\.(?:jpe?g|webp|png))""", bloque, re.I)
        url = f"{base}/propiedad/{id_}"
        out[url] = {
            "url": url,
            "id_externo": id_,
            "titulo": titulo,
            "precio": precio,
            "moneda": moneda,
            "operacion": op,
            "tipo": tipo,
            "ubicacion_txt": ubic,
            "dormitorios": 0 if re.search(r"(?i)\[dorm\]\s*(?:MA|mono)", txt) else _int(DORM.search(txt)),
            "banos": _int(BANO.search(txt)),
            "m2_edificados": None if terreno else m2ed,
            "m2_totales": m2te,
            "imagenes": [img.group(1)] if img else [],
            "descripcion": txt[:400],
            "breadcrumb": " > ".join(x for x in (op, tipo, ubic) if x) or None,
        }
    return out


# ------------------------------------------------------------------ mapa_listado(...) (registro completo en JSON)
def _args_mapa(html: str) -> list | None:
    i = html.find("mapa_listado([")
    while i > 0 and html[max(0, i - 2):i].strip().endswith("//"):  # la copia comentada
        i = html.find("mapa_listado([", i + 1)
    if i < 0:
        return None
    pos = i + len("mapa_listado(")
    dec, args = json.JSONDecoder(), []
    while len(args) < 6:
        try:
            v, fin = dec.raw_decode(html, pos)
        except ValueError:
            break
        args.append(v)
        m = re.compile(r"\s*,\s*").match(html, fin)
        if not m:
            break
        pos = m.end()
    return args if len(args) >= 3 and isinstance(args[2], list) else None


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def registros_json(args: list, base: str) -> dict[str, dict]:
    props, fotos = args[2], (args[3] if len(args) > 3 and isinstance(args[3], list) else [])
    out = {}
    for n, p in enumerate(props):
        if not isinstance(p, dict) or not p.get("id_propiedad") or str(p.get("inactiva")) == "1":
            continue
        id_ = str(p["id_propiedad"])
        venta, alq = str(p.get("venta")) == "1", str(p.get("alquiler")) == "1"
        if venta:
            op, precio, moneda = "venta", _f(p.get("precio_venta")), p.get("moneda_venta")
            if str(p.get("ocultar_venta")) == "1":
                precio = None
            titulo = p.get("titulo_venta") or p.get("titulo") or p.get("titulo_alquiler")
        elif alq:
            op, precio, moneda = "alquiler", _f(p.get("precio_alquiler")), p.get("moneda_alquiler")
            if str(p.get("ocultar_alquiler")) == "1":
                precio = None
            titulo = p.get("titulo_alquiler") or p.get("titulo") or p.get("titulo_venta")
        else:
            op, precio, moneda, titulo = None, None, None, p.get("titulo")
        if precio is not None and precio < 100:
            precio = None
        moneda = N.moneda_norm(moneda) if precio is not None else None
        tipo_txt = p.get("tipo") or ""
        tipo = N.tipo_propiedad(tipo_txt) or N.tipo_propiedad(titulo)
        terreno = tipo in ("terreno", "chacra_campo")
        sup, sup_c, sup_t = _f(p.get("superficie")), _f(p.get("c_superficie_construida")), _f(p.get("c_superficie_terreno"))
        lat = lon = None
        c = re.match(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", p.get("coordenadas") or "")
        if c and -36 < float(c.group(1)) < -29 and -59 < float(c.group(2)) < -52:
            lat, lon = float(c.group(1)), float(c.group(2))
        estado = (p.get("estado") or "").strip()
        ubic = ", ".join(dict.fromkeys(x.strip() for x in (p.get("zona"), p.get("ciudad"), estado) if x and x.strip()))
        desc = re.sub(r"\s+", " ", (p.get("descripcion_venta") if venta else p.get("descripcion_alquiler"))
                      or p.get("descripcion") or "").strip()
        gc = _f(p.get("c_gastos_comunes"))
        url = f"{base}/propiedad/{id_}"
        out[url] = {
            "url": url,
            "id_externo": id_,
            "titulo": (titulo or p.get("nombre_agrupacion") or "").strip()[:200] or None,
            "precio": precio,
            "moneda": moneda,
            "operacion": op,
            "tipo": tipo,
            "ubicacion_txt": ubic or None,
            "departamento": estado if estado in N.DEPARTAMENTOS else None,
            "dormitorios": int(_f(p.get("dormitorios")) or 0) or None,
            "banos": int(_f(p.get("banios")) or 0) or None,
            "m2_edificados": None if terreno else (sup_c or sup),
            "m2_totales": sup_t or (sup if terreno else None),
            "gastos_comunes": gc,
            "lat": lat,
            "lon": lon,
            "imagenes": [fotos[n]] if n < len(fotos) and isinstance(fotos[n], str) else [],
            "descripcion": desc[:400] or None,
            "breadcrumb": " > ".join(x for x in (op, tipo_txt, ubic) if x) or None,
        }
    return out


# ------------------------------------------------------------------ recorrido
def _paginas(html: str, base: str) -> tuple[str | None, int]:
    """(plantilla de URL de página con {n}, número de página más alto enlazado)."""
    plantilla, ultima = None, 1
    for m in PAGINA.finditer(html):
        n = int(m.group(2))
        ultima = max(ultima, n)
        if plantilla is None or n == 2:
            plantilla = urljoin(base + "/", H.unescape(m.group(1))) + "{n}" + H.unescape(m.group(3))
    return plantilla, ultima


def _url(base: str, path: str) -> str:
    return base + quote(path, safe="/$-_")


async def _crawl_delay(cli, base: str) -> float:
    try:
        r = await cli.get(base + "/robots.txt")
    except Exception:
        return 0.0
    if r.status != 200 or "<html" in r.texto[:500].lower():
        return 0.0
    m = re.search(r"(?im)^\s*crawl-delay\s*:\s*([\d.]+)", r.texto)
    return min(float(m.group(1)), CRAWL_DELAY_MAX) if m else 0.0


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    registros: dict[str, dict] = {}
    r = await cli.get(base + "/")
    if r.status != 200:
        raise RuntimeError(f"no abre la home de {base}: {r.status or r.error}")
    base = _base(r.url_final or base)
    pausa = max(0.0, await _crawl_delay(cli, base) - getattr(cli, "delay", 1.0))
    pedidos = 0

    async def pedir(url):
        nonlocal pedidos
        if pedidos and pausa:
            await asyncio.sleep(pausa)
        pedidos += 1
        return await cli.get(url)

    def cortar() -> bool:
        return time.monotonic() > limite_ts or bool(max_paginas and pedidos >= max_paginas)

    # el buscador: /propiedades/<Operación>/U$S/... (Kaplan usa /proyectos-propiedades/...; la mayoría agrega
    # /Todos_los_estados al final). Se toma de los links de la home.
    links = [(m.group(1), m.group(2), m.group(0)) for m in LINK_BUSCADOR.finditer(r.texto)]
    prefijo = "/propiedades" if not links or any(x[0] == "/propiedades" for x in links) else links[0][0]
    estados = not links or any("Todos_los_estados" in x[2] for x in links)
    hay_temporal = any(x[1] == "Alquiler_temporal" for x in links)

    def buscador(op: str) -> str:
        return _url(base, BUSCADOR.format(prefijo=prefijo, op=op) + ("/Todos_los_estados" if estados else ""))

    # 1) buscador con venta y alquiler juntos: si la página trae mapa_listado(...) con todo, listo
    r = await pedir(buscador("Venta-Alquiler"))
    if r.status == 200:
        args = _args_mapa(r.texto)
        if args is not None and args[2]:
            registros.update(registros_json(args, base))
            if hay_temporal and not cortar():
                r = await pedir(buscador("Alquiler_temporal"))
                args = _args_mapa(r.texto) if r.status == 200 else None
                if args is None:
                    return registros, False
                for u, d in registros_json(args, base).items():
                    if u not in registros:
                        d["operacion"] = "alquiler_temporal"
                    registros.setdefault(u, d)
            return registros, True
    buscador_ok = r.status == 200 and bool(_bloques(r.texto)) and "gina-" in (_paginas(r.texto, base)[0] or "gina-")

    # 2) recorrer páginas de tarjetas: por operación con el buscador, o el listado /propiedades (Pointer, Casa Victorica)
    if buscador_ok:
        recorridos = [(buscador("Venta"), "venta"), (buscador("Alquiler"), "alquiler")]
        if hay_temporal:
            recorridos.append((buscador("Alquiler_temporal"), "alquiler_temporal"))
    else:
        recorridos = [(base + "/propiedades", None)]
    completo = True
    for inicio, op in recorridos:
        if cortar():
            return registros, False
        r = await pedir(inicio)
        if r.status != 200:
            completo = False
            if op is None and not registros:
                raise RuntimeError(f"no abre el listado de {base}: {r.status or r.error}")
            continue
        plantilla, ultima = _paginas(r.texto, base)
        nuevos = tarjetas(r.texto, base, op)
        if not nuevos and op is None:
            raise RuntimeError(f"no se reconocen avisos en {inicio}")
        vistos = set(nuevos)
        for u, d in nuevos.items():
            registros.setdefault(u, d)
        n = 2
        while plantilla and n <= min(ultima, MAX_PAGINAS):
            if cortar():
                return registros, False
            r = await pedir(plantilla.format(n=n))
            if r.status != 200:
                completo = False
                break
            nuevos = tarjetas(r.texto, base, op)
            if not set(nuevos) - vistos:  # página vacía o repetida: fin
                break
            vistos |= set(nuevos)
            for u, d in nuevos.items():
                registros.setdefault(u, d)
            ultima = max(ultima, _paginas(r.texto, base)[1])
            n += 1
        if plantilla and n > MAX_PAGINAS:
            completo = False
    return registros, completo
