"""InfoCasas — modo "listado completo".

Las páginas de búsqueda (https://www.infocasas.com.uy/venta/pagina2, ...) son una app Next.js que trae en
<script id="__NEXT_DATA__"> los datos completos de ~22 avisos por página (precio en USD, inmobiliaria, barrio,
coordenadas, m², dormitorios, baños...). Recorriendo venta + alquiler + alquiler temporal se obtiene TODO el
stock (~110.000 avisos, ~5.000 páginas) sin abrir cada aviso.

robots.txt: prohíbe las búsquedas combinadas con "-y-" (/venta/*-y-*); estas URLs simples están permitidas
(el Cliente igual chequea robots.txt en cada pedido).

No se guardan teléfonos ni WhatsApp de las inmobiliarias (vienen en los datos, se descartan).
"""
from __future__ import annotations

import json
import re
import time

from radar import normalize as N

BASE = "https://www.infocasas.com.uy"
SECCIONES = ["venta", "alquiler", "alquiler-temporal"]
MINUTOS = 170  # ~5.000 páginas a ~1,8 s c/u. El workflow tiene tope de 4 h.
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
POINT_RE = re.compile(r"POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def es_aviso(url: str) -> bool:
    return bool(re.search(r"infocasas\.com\.uy/[^/]+/\d{6,}$", url))


def _busqueda(html: str) -> tuple[list[dict], int | None]:
    m = NEXT_RE.search(html)
    if not m:
        return [], None
    d = json.loads(m.group(1))
    fr = (((d.get("props") or {}).get("pageProps") or {}).get("fetchResult") or {})
    sf = fr.get("searchFast") or {}
    pag = sf.get("paginatorInfo") or {}
    return sf.get("data") or [], pag.get("lastPage")


def _nombres(lista) -> str | None:
    if isinstance(lista, list) and lista:
        return lista[0].get("name")
    if isinstance(lista, dict):
        return lista.get("name")
    return None


def convertir(x: dict, seccion: str) -> dict | None:
    link = x.get("link")
    if not link:
        return None
    url = BASE + link
    precio = x.get("price") or {}
    moneda = N.moneda_norm(((precio.get("currency") or {}).get("name") or "").replace(" ", ""))
    monto = precio.get("amount")
    if precio.get("hidePrice") or not monto:
        monto, moneda = None, None
    loc = x.get("locations") or {}
    barrio = _nombres(loc.get("neighbourhood")) or _nombres(loc.get("zone")) or _nombres(loc.get("locality"))
    ciudad = _nombres(loc.get("city"))
    depto = _nombres(loc.get("state"))
    lat, lon = x.get("latitude"), x.get("longitude")
    if not (lat and lon):
        p = POINT_RE.search((loc.get("location_point") or ""))
        if p:
            lon, lat = float(p.group(1)), float(p.group(2))
    owner = x.get("owner") or {}
    op_nombre = (x.get("operation_type") or {}).get("name") or seccion.replace("-", " ")
    tipo_nombre = (x.get("property_type") or {}).get("name") or ""
    gc = x.get("commonExpenses")
    imgs = [i.get("image") or i.get("url") if isinstance(i, dict) else i for i in (x.get("images") or [])][:5] \
        or ([x["img"]] if x.get("img") else [])
    return {
        "url": url,
        "id_externo": str(x.get("id") or link.rsplit("/", 1)[-1]),
        "titulo": x.get("title"),
        "precio": float(monto) if monto else None,
        "moneda": moneda,
        "operacion": N.operacion(op_nombre) or N.operacion(seccion.replace("-", " ")),
        "tipo": N.tipo_propiedad(tipo_nombre) or (N.simple(tipo_nombre) or None),
        "ubicacion_txt": ", ".join(v for v in (barrio, ciudad, depto) if v) or None,
        "direccion": x.get("address") if x.get("showAddress") else None,
        "m2_edificados": x.get("m2Built") or x.get("m2apto") or None,
        "m2_totales": x.get("m2Terrain") or x.get("m2") or None,
        "dormitorios": x.get("bedrooms"),
        "banos": x.get("bathrooms"),
        "garaje": bool(x.get("garage") or x.get("hasGarage")) or None,
        "gastos_comunes": gc if isinstance(gc, (int, float)) and gc > 0 else None,
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "imagenes": [i for i in imgs if isinstance(i, str)],
        "anunciante": None if owner.get("particular") else owner.get("name"),
        "breadcrumb": f"{op_nombre} > {tipo_nombre} > {depto or ''} > {barrio or ''}",
    }


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    """Devuelve ({url: datos}, completo). completo=False si se cortó por tiempo, tope o error."""
    registros: dict[str, dict] = {}
    completo = True
    for seccion in SECCIONES:
        pagina, ultima, errores = 1, None, 0
        while True:
            if time.monotonic() > limite_ts or (max_paginas and pagina > max_paginas):
                return registros, False
            url = f"{BASE}/{seccion}" + (f"/pagina{pagina}" if pagina > 1 else "")
            r = await cli.get(url)
            if r.status != 200:
                errores += 1
                if errores >= 3:
                    completo = False
                    break
                continue
            try:
                avisos, ultima = _busqueda(r.texto)
            except (ValueError, KeyError) as e:  # cambió el formato de la página
                raise RuntimeError(f"InfoCasas cambió el formato de {url}: {e}") from e
            for x in avisos:
                d = convertir(x, seccion)
                if d:
                    registros.setdefault(d["url"], d)
            if not avisos or (ultima and pagina >= ultima):
                break
            pagina += 1
    return registros, completo
