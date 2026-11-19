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


def cargar(fuente_id: str) -> ModuleType | None:
    nombre = fuente_id.replace("-", "_").replace(".", "_")
    try:
        return importlib.import_module(f"radar.adapters.{nombre}")
    except ModuleNotFoundError as e:
        if e.name == f"radar.adapters.{nombre}":
            return None
        raise
