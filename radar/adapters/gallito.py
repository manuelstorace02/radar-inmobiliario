"""Gallito — modo HÍBRIDO (listado + sitemap).

Las páginas de búsqueda (https://www.gallito.com.uy/inmuebles/venta, .../apartamentos/venta/montevideo/pocitos,
.../2-dormitorios) son una app Next.js (React Server Components) que trae en el HTML las "cards" de 39 avisos por
página: precio (número y moneda), ubicación, dormitorios, baños, m², coordenadas y foto. NO trae la inmobiliaria.

Límite: cada búsqueda llega hasta la página 50 (~1.950 avisos); después redirige a la página 1. Por eso se parte
la búsqueda en cortes cada vez más finos (operación → tipo → departamento → barrio → dormitorios) hasta que cada
corte entra en 50 páginas. Lo que no entra en ningún corte (pocos casos: monoambientes de un barrio enorme, etc.)
lo cubre el sitemap.

Cómo lo usa el scraper (HIBRIDO = True, ver radar/scrape.py):
  1. listar(): precio y datos de casi todo el stock, todos los días (~2.050 páginas, ~60 min; ~97% del stock).
  2. el sitemap completo sigue siendo la referencia para saber qué existe (señal de baja) y la fecha de cambio.
  3. con el tiempo que queda se abren avisos uno por uno: primero los que no están en el listado y los que
     todavía no tienen inmobiliaria (el dato que el listado no trae).

robots.txt: permite todo a crawlers genéricos (bloquea bots de IA por nombre; el nuestro no es uno).
No se guardan teléfonos ni contactos.
"""
from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from pathlib import Path

from radar import normalize as N

BASE = "https://www.gallito.com.uy"
HIBRIDO = True
ANUNCIANTE_FALSO = "gallito"  # avisos viejos guardados con el nombre del sitio como anunciante: se re-abren
MINUTOS = 165           # listado (~60 min) + avisos sueltos con el tiempo que sobre. Tope del workflow: 5,5 h.
POR_PAGINA = 39
MAX_PAGINAS = 50        # después de la 50 el sitio redirige a la 1
OPERACIONES = ["venta", "alquiler", "alquiler-temporario"]
TIPOS = ["apartamentos", "casas", "terrenos", "comercios", "campos", "otro"]
DEPTOS = ["montevideo", "canelones", "maldonado", "rocha", "colonia", "san-jose", "florida", "lavalleja",
          "paysandu", "salto", "soriano", "rio-negro", "tacuarembo", "rivera", "durazno", "flores", "artigas",
          "cerro-largo", "treinta-y-tres"]
DORMITORIOS = ["1-dormitorio", "2-dormitorios", "3-dormitorios", "4-dormitorios"]
TIPO_DE = {"apartamentos": "apartamento", "casas": "casa", "terrenos": "terreno", "comercios": "local",
           "campos": "chacra_campo"}
OP_DE = {"venta": "venta", "alquiler": "alquiler", "alquiler-temporario": "alquiler_temporal"}

NEXT_F = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)
TOTAL = re.compile(r'"total":(\d+)')
_PATRON = re.compile(r"gallito\.com\.uy/[^/?#]+-inmuebles-\d+$")


def es_aviso(url: str) -> bool:
    return bool(_PATRON.search(url))


def slug(t: str) -> str:
    """Como arma Gallito los barrios en la URL: ñ -> nn, sin tildes, espacios -> '-'."""
    t = (t or "").lower().replace("ñ", "nn")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _payload(html: str) -> str:
    return "".join(json.loads(p) for p in NEXT_F.findall(html))


def cards(html: str) -> tuple[list[dict], int | None]:
    s = _payload(html)
    i = s.find('"cards":[{')
    if i < 0:
        return [], None
    arr, _ = json.JSONDecoder().raw_decode(s[i + 8:])
    t = TOTAL.search(s)
    return arr, int(t.group(1)) if t else None


_EMPRESA = re.compile(r"inmobiliari|propiedad|negocio|bienes|raices|real ?estate|inmueble|estudio|grupo|"
                      r"construc|desarroll|broker|asesor|gestion|s\.?a\.?$|srl|ltda|&", re.I)


def _objeto(s: str, clave: str):
    i = s.find(f'"{clave}":')
    if i < 0:
        return None
    try:
        v, _ = json.JSONDecoder().raw_decode(s[i + len(clave) + 3:])
        return v
    except ValueError:
        return None


def extraer(html: str, url: str, generico: dict) -> dict:
    """La página del aviso: el JSON-LD dice que el autor es "Gallito Luis IA" (el sitio). La inmobiliaria real
    está en publisherAgency; si no hay, publisher (que puede ser una inmobiliaria o una persona: de las personas
    no se guarda el nombre, queda "Trato directo")."""
    d = dict(generico)
    if (d.get("anunciante") or "").lower().startswith("gallito"):
        d["anunciante"] = None
    try:
        s = _payload(html)
    except ValueError:
        return d
    agencia = _objeto(s, "publisherAgency")
    pub = _objeto(s, "publisher")
    if isinstance(agencia, dict) and agencia.get("name"):
        d["anunciante"] = agencia["name"].strip()
    elif isinstance(pub, dict) and pub.get("name") and _EMPRESA.search(N.simple(pub["name"])):
        d["anunciante"] = pub["name"].strip()
    elif agencia is None and "publisherAgency" in s:
        d["anunciante"] = "Trato directo"  # lo publica una persona: no se guarda su nombre (y no se vuelve a abrir)
    trail = _objeto(s, "seoTrail")
    if isinstance(trail, list):
        d["breadcrumb"] = " > ".join(t.get("label", "") for t in trail if isinstance(t, dict))
    return d


def _barrios_semilla() -> dict[str, set[str]]:
    """Barrios del seed (data/seed/barrios.csv) como slugs, por departamento."""
    out: dict[str, set[str]] = {}
    p = Path(__file__).resolve().parents[2] / "data" / "seed" / "barrios.csv"
    if not p.exists():
        return out
    filas = csv.DictReader(l for l in p.open(encoding="utf-8") if not l.startswith("#"))
    for f in filas:
        if f.get("tipo") in ("barrio", "zona", "localidad") and f.get("departamento"):
            out.setdefault(slug(f["departamento"]), set()).add(slug(f["barrio"]))
    return out


async def _barrios_sitemap(cli) -> dict[str, set[str]]:
    """Barrios que Gallito publica en sitemaps/searches.xml, por departamento."""
    out: dict[str, set[str]] = {}
    r = await cli.get(f"{BASE}/sitemaps/searches.xml")
    for m in re.finditer(r"<loc>\s*https?://[^/]+/inmuebles/[^/]+/[^/]+/([^/<]+)/([^/<\s]+)\s*</loc>", r.texto or ""):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def convertir(c: dict, op: str, tipo_slug: str | None, depto: str | None) -> dict | None:
    href = c.get("href")
    if not href:
        return None
    pin = c.get("pin") or {}
    precio, moneda = pin.get("price"), N.moneda_norm(pin.get("currency") or "")
    if not precio:
        precio, moneda = N.parse_precio(c.get("price") or "")
    stats = {s.get("icon"): s.get("text") for s in (c.get("stats") or []) if isinstance(s, dict)}
    m2 = N.numero(re.sub(r"m²|m2", "", stats.get("square") or ""))
    tipo = TIPO_DE.get(tipo_slug or "") or N.tipo_propiedad(pin.get("type") or "") or None
    at = pin.get("at") or [None, None]
    depto_nom = (depto or "").replace("-", " ").title() or None
    ubic = ", ".join(v for v in (c.get("location"), depto_nom) if v) or None
    foto = (c.get("photo") or {}).get("src")
    return {
        "url": BASE + href,
        "titulo": c.get("title"),
        "precio": float(precio) if precio else None,
        "moneda": moneda if precio else None,
        "operacion": OP_DE.get(op),
        "tipo": tipo,
        "ubicacion_txt": ubic,
        "m2_edificados": m2 if tipo not in ("terreno", "chacra_campo") else None,
        "m2_totales": m2 if tipo in ("terreno", "chacra_campo") else None,
        "dormitorios": int(N.numero(stats["bed"])) if N.numero(stats.get("bed") or "") is not None else None,
        "banos": int(N.numero(stats["bath"])) if N.numero(stats.get("bath") or "") is not None else None,
        "lon": float(at[0]) if at and at[0] else None,
        "lat": float(at[1]) if at and len(at) > 1 and at[1] else None,
        "imagenes": [foto] if foto else [],
        "anunciante": None,  # el listado no lo trae: lo completa la visita al aviso (y no se pisa el que ya hay)
        "breadcrumb": f"{op} > {tipo_slug or ''} > {depto_nom or ''} > {c.get('location') or ''}",
    }


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    """Devuelve ({url: datos}, completo). completo=True solo si cada corte se recorrió entero y los cortes cubren
    el total publicado. En modo híbrido igual no se usa para bajas (para eso está el sitemap)."""
    registros: dict[str, dict] = {}
    estado = {"completo": True, "cortado": False, "paginas": 0}
    barrios = _barrios_semilla()
    for d, s in (await _barrios_sitemap(cli)).items():
        barrios.setdefault(d, set()).update(s)

    async def pagina(path: str, n: int) -> tuple[list[dict], int | None] | None:
        if time.monotonic() > limite_ts or (max_paginas and estado["paginas"] >= max_paginas):
            estado["cortado"] = True
            return None
        estado["paginas"] += 1
        r = await cli.get(BASE + path + (f"?pag={n}" if n > 1 else ""))
        if r.status != 200:  # 301/404: corte que no existe (barrio sin avisos) o página fuera de rango
            return [], None
        pedida = BASE + path + (f"?pag={n}" if n > 1 else "")
        if r.url_final and r.url_final.rstrip("/") != pedida:
            return [], None  # redirigió (barrio inexistente o página fuera de rango): no cuenta
        try:
            return cards(r.texto)
        except (ValueError, KeyError) as e:
            raise RuntimeError(f"Gallito cambió el formato de {path}: {e}") from e

    def guardar(lista, op, tipo_slug, depto):
        for c in lista:
            d = convertir(c, op, tipo_slug, depto)
            if d:
                previo = registros.get(d["url"])
                # el corte más específico sabe el tipo/departamento: no pisar con uno más genérico
                if not previo or (not previo.get("tipo") and d.get("tipo")):
                    registros[d["url"]] = d

    async def nodo(path: str, op: str, tipo_slug: str | None, depto: str | None, niveles: list[str]) -> int:
        """Recorre un corte. Devuelve el total que dice el sitio (0 si no existe)."""
        res = await pagina(path, 1)
        if res is None:
            return 0
        lista, total = res
        if total is None:
            return 0
        guardar(lista, op, tipo_slug, depto)
        if total <= POR_PAGINA * MAX_PAGINAS or not niveles:
            ultima = min(MAX_PAGINAS, -(-total // POR_PAGINA))
            if total > POR_PAGINA * MAX_PAGINAS:
                estado["completo"] = False
            for n in range(2, ultima + 1):
                res = await pagina(path, n)
                if res is None:
                    return total
                if not res[0]:
                    break
                guardar(res[0], op, tipo_slug, depto)
            return total
        nivel, resto = niveles[0], niveles[1:]
        if nivel == "tipo":
            hijos = [(f"{path.replace('/inmuebles/', '/inmuebles/' + t + '/', 1)}", t, depto) for t in TIPOS]
        elif nivel == "depto":
            hijos = [(f"{path}/{d}", tipo_slug, d) for d in DEPTOS]
        elif nivel == "barrio":
            hijos = [(f"{path}/{b}", tipo_slug, depto) for b in sorted(barrios.get(depto or "", ()))]
        else:  # dormitorios
            hijos = [(f"{path}/{d}", tipo_slug, depto) for d in DORMITORIOS]
        if not hijos:  # departamento sin barrios conocidos: se salta al nivel siguiente
            return await nodo(path, op, tipo_slug, depto, resto)
        suma = 0
        for p, t, d in hijos:
            suma += await nodo(p, op, t, d, resto)
            if estado["cortado"]:
                return total
        # los barrios se solapan (Pocitos / Pocitos Nuevo), así que la suma puede pasarse; si no llega, faltan avisos
        if suma < total:
            estado["completo"] = False
        return total

    for op in OPERACIONES:
        await nodo(f"/inmuebles/{op}", op, None, None, ["tipo", "depto", "barrio", "dormitorios"])
        if estado["cortado"]:
            break
    return registros, estado["completo"] and not estado["cortado"]
