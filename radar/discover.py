"""Descubrimiento de URLs de avisos: sitemaps y páginas de listado."""
from __future__ import annotations

import gzip
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .http import Cliente

LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
URL_RE = re.compile(r"<url>(.*?)</url>", re.I | re.S)
LASTMOD_RE = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.I | re.S)


def _locs(xml: str) -> list[str]:
    return [l.replace("&amp;", "&") for l in LOC_RE.findall(xml)]


async def por_sitemap(cli: Cliente, base: str, patron: re.Pattern | None,
                      max_sitemaps: int = 300) -> dict[str, str | None]:
    """Lee robots.txt -> Sitemap:, o prueba /sitemap.xml y /sitemap_index.xml. Recorre índices anidados.
    Devuelve {url: lastmod (AAAA-MM-DD) o None}."""
    pendientes = await cli.sitemaps_de_robots(base) or [
        urljoin(base, "/sitemap.xml"), urljoin(base, "/sitemap_index.xml"), urljoin(base, "/wp-sitemap.xml")]
    vistos: set[str] = set()
    urls: dict[str, str | None] = {}
    while pendientes and len(vistos) < max_sitemaps:
        sm = pendientes.pop(0)
        if sm in vistos:
            continue
        vistos.add(sm)
        r = await cli.get(sm)
        if r.status != 200:
            continue
        xml = r.texto
        if r.contenido[:2] == b"\x1f\x8b":
            try:
                xml = gzip.decompress(r.contenido).decode("utf-8", "ignore")
            except OSError:
                continue
        if "<sitemapindex" in xml.lower():
            locs = _locs(xml)
            # priorizar sitemaps que suenan a propiedades
            locs.sort(key=lambda u: 0 if re.search(r"(propiedad|property|inmueble|aviso|listing|venta|alquiler)", u, re.I) else 1)
            pendientes.extend(locs)
        else:
            for bloque in URL_RE.findall(xml):
                m = LOC_RE.search(bloque)
                if not m:
                    continue
                u = m.group(1).replace("&amp;", "&")
                if patron is None or patron.search(u):
                    lm = LASTMOD_RE.search(bloque)
                    urls[u] = lm.group(1)[:10] if lm else None
    return urls


async def por_listado(cli: Cliente, listado_urls: str, patron: re.Pattern | None,
                      max_paginas: int = 200) -> set[str]:
    """Recorre páginas de listado. Cada URL puede tener {page} para paginar (1, 2, 3...).
    Corta cuando una página no trae avisos nuevos."""
    urls: set[str] = set()
    for plantilla in [u.strip() for u in (listado_urls or "").split("|") if u.strip()]:
        paginas = range(1, max_paginas + 1) if "{page}" in plantilla else [1]
        for n in paginas:
            url = plantilla.replace("{page}", str(n))
            r = await cli.get(url)
            if r.status != 200:
                break
            nuevos = links_de_avisos(r.texto, r.url_final, patron) - urls
            if not nuevos:
                break
            urls |= nuevos
    return urls


def links_de_avisos(html: str, base_url: str, patron: re.Pattern | None) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    dom = urlsplit(base_url).netloc
    out = set()
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"]).split("#")[0]
        if urlsplit(u).netloc != dom:
            continue
        if patron is None or patron.search(u):
            out.add(u)
    return out
