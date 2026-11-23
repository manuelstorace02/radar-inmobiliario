"""Adapters por sitio.

Si existe radar/adapters/<fuente_id con '-' cambiado por '_'>.py, el scraper lo usa.
Un adapter puede definir cualquiera de estas funciones (todas opcionales):

    async def descubrir(cli, fuente) -> set[str]
        Devuelve las URLs de avisos del sitio (reemplaza sitemap/listado).

    def es_aviso(url: str) -> bool
        True si la URL es de un aviso (reemplaza 'patron_aviso').

    def extraer(html: str, url: str, generico: dict) -> dict
        Recibe lo que sacó el extractor genérico y devuelve el dict corregido/completado.
        Claves útiles: precio, moneda, operacion, tipo, titulo, ubicacion_txt, barrio, departamento,
        m2_totales, m2_edificados, dormitorios, banos, gastos_comunes, garaje, lat, lon,
        imagenes, anunciante, id_externo, no_disponible.

Ver docs/NUEVA_FUENTE.md y el ejemplo _ejemplo.py.
"""
from __future__ import annotations

import importlib
from types import ModuleType


def _importar(nombre: str) -> ModuleType | None:
    try:
        return importlib.import_module(f"radar.adapters.{nombre}")
    except ModuleNotFoundError as e:
        if e.name == f"radar.adapters.{nombre}":
            return None
        raise


def cargar(fuente_id: str) -> ModuleType | None:
    """Adapter propio de la fuente (radar/adapters/<id>.py) o, si no hay, el de su PLATAFORMA: el prefijo del id
    antes del primer '-' (ej. 'tera-nicolasdemodena' usa radar/adapters/tera.py)."""
    ad = _importar(fuente_id.replace("-", "_").replace(".", "_"))
    if ad is None and "-" in fuente_id:
        pref = fuente_id.split("-", 1)[0]
        ad = _importar(pref) if pref not in ("_ejemplo",) else None
        if ad is not None and not getattr(ad, "PLATAFORMA", False):
            return None
    return ad
