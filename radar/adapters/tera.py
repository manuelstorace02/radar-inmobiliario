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
import re
import time
from urllib.parse import urljoin, urlsplit

from radar import normalize as N

AVISO = re.compile(r"""(?:href=|open\()\s*["']((?:https?://[^"'/]+)?/?([A-Z][A-Za-z]+)/(\d{2,7}))(?:/[^"'\s]*)?["']""")
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


def es_aviso(url: str) -> bool:
    return bool(re.search(r"/[A-Z][A-Za-z]+/\d{2,7}/?$", urlsplit(url).path))


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
            "ubicacion_txt": " , ".join(dict.fromkeys(([zona.group(1).strip()] if zona else []) + anclas + pistas[:2]))[:300]
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


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    registros: dict[str, dict] = {}
    completo = True
    r = await cli.get(base + "/")
    if r.status != 200:
        raise RuntimeError(f"no abre la home de {base}: {r.status or r.error}")
    base = _base(r.url_final or base)  # www. / https
    categorias = {}
    for m in CATEG.finditer(r.texto):
        path = urlsplit(urljoin(base + "/", m.group(1))).path.strip("/")
        categorias[path.lower()] = "venta" if m.group(3).lower() == "venta" else "alquiler"
    if not categorias:
        categorias = {c: ("venta" if c.endswith("venta") else "alquiler") for c in CATEGORIAS_DEFECTO}
    paginas = 0
    for cat, op in categorias.items():
        n, ultima = 1, 1
        while n <= min(ultima, MAX_PAGINAS):
            if time.monotonic() > limite_ts or (max_paginas and paginas >= max_paginas):
                return registros, False
            url = f"{base}/{cat}/" + (f"?pagina={n}" if n > 1 else "")
            r = await cli.get(url)
            paginas += 1
            if r.status != 200:
                if r.error == "bloqueado_por_robots" or n > 1:
                    completo = False
                break
            nuevos = tarjetas(r.texto, base, op)
            antes = len(registros)
            for u, d in nuevos.items():
                registros.setdefault(u, d)
            ultima = max([ultima] + [int(x) for x in PAG.findall(r.texto)])
            if not nuevos or (n > 1 and len(registros) == antes):  # página vacía o repetida: fin
                break
            n += 1
    return registros, completo
