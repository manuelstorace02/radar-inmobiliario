"""Extracción genérica de datos de un aviso a partir del HTML.

Orden de confianza: JSON-LD (schema.org) > meta tags (OpenGraph) > elementos con clase 'price/precio'
> texto del título/descripcion > texto general. Cada sitio puede sobrescribir esto con un adapter
(radar/adapters/<fuente_id>.py), ver docs/NUEVA_FUENTE.md."""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from . import normalize as N

M2_RE = re.compile(r"(\d[\d\.,]*)\s*(m2|m²|mts2|mt2|metros cuadrados|mts\b)", re.I)
DORM_RE = re.compile(r"(\d+)\s*(dormitorios?|dorm\b\.?|habitaciones|cuartos)", re.I)
BANO_RE = re.compile(r"(\d+)\s*(baños?|banos?)", re.I)
GC_RE = re.compile(r"gastos?\s+comunes?[^\d$U]{0,20}" + N._MONEDA + r"?\s*([\d][\d\.,]*)", re.I)
NO_DISPONIBLE_RE = re.compile(
    r"(aviso (ya )?no (est[aá] )?disponible|publicaci[oó]n (pausada|finalizada|no disponible)|"
    r"propiedad (vendida|alquilada|no disponible)|ya fue (vendid|alquilad)|esta propiedad ya no)", re.I)

TIPOS_LD = {"product", "offer", "realestatelisting", "residence", "apartment", "house", "singlefamilyresidence",
            "accommodation", "place", "individualproduct", "apartmentcomplex", "landform", "lodgingbusiness"}


def _ld_objetos(soup: BeautifulSoup) -> list[dict]:
    out = []
    for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pila = [data]
        while pila:
            d = pila.pop()
            if isinstance(d, list):
                pila.extend(d)
            elif isinstance(d, dict):
                out.append(d)
                for k in ("@graph", "mainEntity", "itemOffered", "offers", "about"):
                    if k in d:
                        pila.append(d[k])
    return out


def _tipo_ld(d: dict) -> set[str]:
    t = d.get("@type", [])
    return {str(x).lower() for x in (t if isinstance(t, list) else [t])}


def _val(x: Any) -> Any:
    if isinstance(x, dict):
        return x.get("value") or x.get("@value") or x.get("name")
    if isinstance(x, list) and x:
        return _val(x[0])
    return x


def _meta(soup, *nombres) -> str | None:
    for n in nombres:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
              or soup.find("meta", attrs={"itemprop": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def extraer(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    r: dict[str, Any] = {"url": url}

    # ---------- JSON-LD
    for d in _ld_objetos(soup):
        tipos = _tipo_ld(d)
        if not tipos & TIPOS_LD:
            continue
        if "offer" in tipos or "price" in d:
            if r.get("precio") is None and d.get("price") not in (None, ""):
                r["precio"] = N.numero(str(d.get("price")))
                r["moneda"] = N.moneda_norm(str(d.get("priceCurrency") or "")) or r.get("moneda")
            spec = d.get("priceSpecification")
            if r.get("precio") is None and isinstance(spec, dict) and spec.get("price"):
                r["precio"] = N.numero(str(spec["price"]))
                r["moneda"] = N.moneda_norm(str(spec.get("priceCurrency") or ""))
        for k in ("seller", "offeredBy", "provider", "broker"):
            s = d.get(k)
            if isinstance(s, dict) and s.get("name"):
                r.setdefault("anunciante", str(s["name"]).strip())
        r.setdefault("titulo", d.get("name"))
        addr = d.get("address")
        if not addr and isinstance(d.get("itemOffered"), dict):
            addr = d["itemOffered"].get("address")
        if isinstance(addr, dict):
            r.setdefault("direccion", addr.get("streetAddress"))
            partes = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("streetAddress")]
            r.setdefault("ubicacion_txt", " , ".join(str(p) for p in partes if p))
        elif isinstance(addr, str):
            r.setdefault("ubicacion_txt", addr)
        geo = d.get("geo")
        if isinstance(geo, dict):
            r.setdefault("lat", N.numero(str(geo.get("latitude"))) if geo.get("latitude") else None)
            r.setdefault("lon", N.numero(str(geo.get("longitude"))) if geo.get("longitude") else None)
        if d.get("floorSize"):
            r.setdefault("m2_edificados", N.numero(str(_val(d["floorSize"]))))
        for k in ("numberOfBedrooms", "numberOfRooms"):
            if d.get(k) not in (None, ""):
                v = N.numero(str(_val(d[k])))
                if v is not None:
                    r.setdefault("dormitorios", int(v))
        for k in ("numberOfBathroomsTotal", "numberOfFullBathrooms"):
            if d.get(k) not in (None, ""):
                v = N.numero(str(_val(d[k])))
                if v is not None:
                    r.setdefault("banos", int(v))
        img = d.get("image")
        if img:
            imgs = img if isinstance(img, list) else [img]
            r.setdefault("imagenes", [i.get("url") if isinstance(i, dict) else i for i in imgs][:5])
        if d.get("description"):
            r.setdefault("descripcion", str(d["description"])[:2000])

    # ---------- meta tags
    titulo_meta = _meta(soup, "og:title", "twitter:title")
    h1 = soup.find("h1")
    r["titulo"] = r.get("titulo") or titulo_meta or (h1.get_text(" ", strip=True) if h1 else None) \
                  or (soup.title.get_text(strip=True) if soup.title else None)
    r.setdefault("descripcion", _meta(soup, "og:description", "description"))
    if r.get("precio") is None:
        p = _meta(soup, "product:price:amount", "og:price:amount", "price")
        if p:
            r["precio"] = N.numero(p)
            r["moneda"] = N.moneda_norm(_meta(soup, "product:price:currency", "og:price:currency", "priceCurrency"))
    if not r.get("imagenes"):
        og = _meta(soup, "og:image")
        r["imagenes"] = [og] if og else []

    # ---------- elementos de precio
    if r.get("precio") is None:
        for el in soup.find_all(attrs={"class": re.compile(r"(price|precio)", re.I)}, limit=30):
            p, m = N.parse_precio(el.get_text(" ", strip=True))
            if p and p > 100:
                r["precio"], r["moneda"] = p, m
                break
    if r.get("precio") is None:
        for t in (r.get("titulo"), h1.get_text(" ", strip=True) if h1 else None, r.get("descripcion")):
            p, m = N.parse_precio(t)
            if p and p > 100:
                r["precio"], r["moneda"] = p, m
                break

    # breadcrumb: suele traer "Venta > Apartamentos > Montevideo > Pocitos"
    bc = soup.find(attrs={"class": re.compile("breadcrumb", re.I)})
    r["breadcrumb"] = bc.get_text(" > ", strip=True) if bc else None

    # ---------- texto general (solo lo visible principal)
    for s in soup(["script", "style", "noscript", "footer", "nav"]):
        s.decompose()
    cuerpo = soup.find("main") or soup.body or soup
    texto = cuerpo.get_text(" ", strip=True)[:20000] if cuerpo else ""
    r["_texto"] = texto  # se usa para operación/tipo/barrio; no se guarda

    if r.get("precio") is None:
        p, m = N.parse_precio(texto[:3000])
        if p and p > 100:
            r["precio"], r["moneda"] = p, m
            r["_precio_dudoso"] = True

    base = " ".join(str(x) for x in (r.get("titulo"), r.get("descripcion")) if x)
    if r.get("m2_edificados") is None:
        m = M2_RE.search(base) or M2_RE.search(texto)
        if m:
            r["m2_edificados"] = N.numero(m.group(1))
    if r.get("dormitorios") is None:
        m = DORM_RE.search(base) or DORM_RE.search(texto)
        if m:
            r["dormitorios"] = int(m.group(1))
        elif re.search(r"monoambiente", base + texto[:3000], re.I):
            r["dormitorios"] = 0
    if r.get("banos") is None:
        m = BANO_RE.search(base) or BANO_RE.search(texto)
        if m:
            r["banos"] = int(m.group(1))
    m = GC_RE.search(texto)
    if m:
        r["gastos_comunes"] = N.numero(m.group(2))
    r["garaje"] = bool(re.search(r"\b(garaje|cochera|parking)\b", base, re.I)) or None

    r["no_disponible"] = bool(NO_DISPONIBLE_RE.search(texto[:5000]))
    return r
