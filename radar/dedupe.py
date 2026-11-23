"""Agrupa avisos que son la MISMA propiedad publicada en distintas fuentes (web propia, portales,
otras inmobiliarias). Asigna avisos.propiedad_id.

Heurística (todas deben cumplirse, salvo que coincida una foto o la ubicación exacta):
  * mismo departamento, barrio, operación y tipo
  * mismos dormitorios (si ambos lo tienen)
  * m² edificados a menos de 4% (si ambos lo tienen)
  * si ambos tienen coordenadas, a menos de 250 m
  * precio en USD a menos de 12%
  * distinta fuente, o mismo portal pero distinto anunciante (dos inmobiliarias publicando lo mismo)
Atajos fuertes: misma URL de imagen (nombre de archivo) o coordenadas a < 30 m con mismos dormitorios.
Los casos dudosos (score intermedio) se listan para que el agente los revise."""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict

from .store import Store


def _img_keys(a: dict) -> set[str]:
    out = set()
    for u in a.get("imagenes") or []:
        nombre = re.sub(r"\?.*$", "", str(u)).rsplit("/", 1)[-1].lower()
        nombre = re.sub(r"(_|-)(\d{2,4}x\d{2,4}|thumb|small|medium|large)", "", nombre)
        if len(nombre) >= 8:
            out.add(nombre)
    return out


def _dist_m(a, b) -> float | None:
    if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return None
    la1, lo1, la2, lo2 = map(math.radians, (float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def _cerca(x, y, tol) -> bool | None:
    if x in (None, 0) or y in (None, 0):
        return None
    x, y = float(x), float(y)
    return abs(x - y) / max(x, y) <= tol


def score(a: dict, b: dict) -> float:
    if a["fuente_id"] == b["fuente_id"]:
        # dentro de un portal, la misma propiedad puede estar publicada por dos inmobiliarias distintas
        ia = a.get("inmobiliaria_id") or a.get("anunciante")
        ib = b.get("inmobiliaria_id") or b.get("anunciante")
        if not ia or not ib or ia == ib:
            return 0
    ia_, ib_ = a.get("_img"), b.get("_img")
    if (ia_ if ia_ is not None else _img_keys(a)) & (ib_ if ib_ is not None else _img_keys(b)):
        return 1.0
    d = _dist_m(a, b)
    if d is not None and d < 30 and a.get("dormitorios") == b.get("dormitorios"):
        return 0.95
    if d is not None and d > 250:  # coordenadas lejanas: otra propiedad
        return 0
    # Sin foto en común ni ubicación exacta: hace falta que coincida todo lo demás, y ajustado.
    if a.get("dormitorios") is None or b.get("dormitorios") is None or a["dormitorios"] != b["dormitorios"]:
        return 0
    if not _cerca(a.get("m2_edificados"), b.get("m2_edificados"), 0.03):
        return 0
    if not _cerca(a.get("precio_usd"), b.get("precio_usd"), 0.05):
        return 0
    if a.get("banos") is not None and b.get("banos") is not None and a["banos"] != b["banos"]:
        return 0
    s = 0.85
    if d is not None:  # cerca (< 250 m)
        s += 0.1
    elif not _cerca(a.get("precio_usd"), b.get("precio_usd"), 0.02):
        s = 0.75  # sin coordenadas y precio no casi idéntico: dudoso, no se agrupa
    return s


MAX_GRUPO = 8  # una misma propiedad rara vez está en más de 8 avisos


def agrupar(store: Store, umbral: float = 0.85) -> dict:
    avisos = [a for a in store.select("avisos") if a.get("barrio") and a.get("operacion") and a.get("tipo")]
    bloques = defaultdict(list)
    for a in avisos:
        bloques[(a["departamento"], a["barrio"], a["operacion"], a["tipo"])].append(a)

    padre = {a["id"]: a["id"] for a in avisos}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    dudosos = []

    def comparar(x, y):
        sc = score(x, y)
        if sc >= umbral:
            padre[raiz(x["id"])] = raiz(y["id"])
        elif sc >= 0.6:
            dudosos.append((round(sc, 2), x["url"], y["url"]))

    # Candidatos (no todos contra todos: con 100k+ avisos sería lentísimo):
    # 1) avisos que comparten una foto (nombre de archivo), dentro de la misma operación y tipo
    for a in avisos:
        a["_img"] = _img_keys(a)
    por_img = defaultdict(list)
    for a in avisos:
        for k in a["_img"]:
            por_img[(a["operacion"], a["tipo"], k)].append(a)
    for grupo in por_img.values():
        if 1 < len(grupo) <= 20:  # una "foto" compartida por 20+ avisos es un logo o un render genérico
            for i in range(len(grupo)):
                for j in range(i + 1, len(grupo)):
                    comparar(grupo[i], grupo[j])
    # 2) dentro de cada bloque (depto, barrio, operación, tipo), solo pares con precio a menos de 12%
    for grupo in bloques.values():
        por_dorm = defaultdict(list)
        for a in grupo:
            if a.get("precio_usd") and a.get("dormitorios") is not None:
                por_dorm[a["dormitorios"]].append(a)
        for sub in por_dorm.values():
            sub.sort(key=lambda a: float(a["precio_usd"]))
            for i, x in enumerate(sub):
                px = float(x["precio_usd"])
                for y in sub[i + 1:]:
                    if float(y["precio_usd"]) > px * 1.05:
                        break
                    comparar(x, y)

    miembros = defaultdict(list)
    for a in avisos:
        miembros[raiz(a["id"])].append(a)
    cambios = []
    grandes = 0
    for ids in miembros.values():
        pid = None
        if len(ids) > MAX_GRUPO:  # cadena de parecidos (ej. unidades iguales de un edificio): no es UNA propiedad
            grandes += 1
            ids_ok = False
        else:
            ids_ok = True
        if len(ids) > 1 and ids_ok:
            # id estable: el de menor id de aviso, hasheado
            pid = "p_" + hashlib.sha1(min(x["id"] for x in ids).encode()).hexdigest()[:12]
        for a in ids:
            if a.get("propiedad_id") != pid:
                a["propiedad_id"] = pid
                cambios.append(a)
    for a in avisos:
        a.pop("_img", None)
    if cambios:
        store.upsert("avisos", cambios)
    return {"avisos": len(avisos), "grupos_multifuente": sum(1 for v in miembros.values() if len(v) > 1),
            "actualizados": len(cambios), "grupos_descartados_por_grandes": grandes, "dudosos": sorted(dudosos, reverse=True)[:50]}
