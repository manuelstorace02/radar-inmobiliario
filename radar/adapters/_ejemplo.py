"""Ejemplo de adapter. Copiar como <fuente_id>.py (guiones -> guiones bajos) y ajustar.

No se carga solo: el nombre empieza con '_'."""
import re

from bs4 import BeautifulSoup

from radar import normalize as N


def es_aviso(url: str) -> bool:
    # Ej.: https://www.inmobiliaria-x.com.uy/propiedad/12345-apartamento-pocitos
    return bool(re.search(r"/propiedad/\d+", url))


def extraer(html: str, url: str, generico: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    d = dict(generico)
    precio = soup.select_one(".property-price")
    if precio:
        d["precio"], d["moneda"] = N.parse_precio(precio.get_text(" ", strip=True))
    ubic = soup.select_one(".property-location")
    if ubic:
        d["ubicacion_txt"] = ubic.get_text(" ", strip=True)
    m = re.search(r"/propiedad/(\d+)", url)
    if m:
        d["id_externo"] = m.group(1)
    return d
