"""Xintel (xintel.com.ar / xintelweb.com) — adapter de PLATAFORMA para las inmobiliarias cuya web está hecha con
el CRM argentino Xintel (Beba Páez Vilaró, TreeHouse, Sures, Etxe, Mooving...). Ver docs/INMOBILIARIAS.md.

Se usa con fuentes cuyo id empieza con "xintel-" (ej. xintel-treehouse) y modo "adapter".

Cómo son estos sitios:
  - los listados (/propiedades?ope=V) se arman con JavaScript: el HTML trae vacío el contenedor y un $.ajax a la
    API pública de Xintel (https://xintelapi.com.ar/ o https://xintel.com.ar/api/) con 'json':'resultados.fichas',
    el código de la inmobiliaria ('inm': 'TBR') y una clave pública ('apiK') escrita en el propio HTML;
  - esa API devuelve JSON con todos los datos de cada ficha (precio, operación, barrio, localidad, coordenadas,
    dormitorios, baños, superficies, fotos) y el total ('datos.cantidadFichas', 'datos.paginas'); pagina con
    page=0,1,2... (desde 0) y entrega como mucho 20 fichas por página (rppagina);
  - la ficha pública es <web>/<amigable> (ej. /casa-en-venta-en-el-chorro-ficha-bpv11179).
El adapter lee el código y la clave de la home (o de /propiedades) y recorre la API sin filtros: trae venta,
alquiler y alquiler temporario de una vez. La API también devuelve datos de los vendedores (nombre, teléfono,
mail): NO se copian.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlencode, urlsplit

from radar import normalize as N

PLATAFORMA = True  # lo usan todas las fuentes "xintel-..."

API_DEFECTO = "https://xintelapi.com.ar/"
_API_LOCK = asyncio.Lock()
POR_PAGINA = 20  # máximo que acepta la API
MAX_PAGINAS = 500
INM = re.compile(r"""['"]inm['"]\s*:\s*['"]([A-Za-z0-9]{2,6})['"]""")
APIK = re.compile(r"""['"]apiK['"]\s*:\s*['"]([A-Za-z0-9]{10,40})['"]""")
API_URL = re.compile(r"""url\s*:\s*['"](https?://(?:xintelapi\.com\.ar|(?:www\.)?xintel\.com\.ar/api)/?)['"]""")
OPERACION = {"V": "venta", "A": "alquiler", "T": "alquiler_temporal", "M": "venta"}
FICHA = re.compile(r"-ficha-[a-z]{2,5}\d+/?$|ficha\.php\?ficha=[A-Za-z]{2,5}\d+", re.I)


def es_aviso(url: str) -> bool:
    p = urlsplit(url)
    return bool(FICHA.search(p.path + ("?" + p.query if p.query else "")))


def _base(url: str) -> str:
    """Raíz del sitio con su carpeta (Sures vive en /sitio/): termina en '/'."""
    p = urlsplit(url)
    path = p.path if p.path.endswith("/") else p.path.rsplit("/", 1)[0] + "/"
    return f"{p.scheme}://{p.netloc}{path or '/'}"


def credenciales(html: str) -> tuple[str, str, str] | None:
    """(url_api, inm, apiK) del bloque $.ajax que pide 'resultados.fichas' (o, si no hay, del primero que haya)."""
    bloques = [m.start() for m in re.finditer(r"""['"]json['"]\s*:\s*['"]resultados\.fichas['"]""", html)]
    for pos in bloques + [None]:
        trozo = html[max(0, pos - 600):pos + 600] if pos is not None else html
        trozo_sin_coment = re.sub(r"//[^\n]*", "", trozo)  # ignora 'inm' comentados (//'inm':'SRS',)
        inm, key = INM.search(trozo_sin_coment), APIK.search(trozo_sin_coment)
        if inm and key:
            url = API_URL.search(html[max(0, (pos or 0) - 600):(pos or 0) + 600] if pos is not None else html)
            return (url.group(1) if url else API_DEFECTO), inm.group(1).upper(), key.group(1)
    return None


def _num(v) -> float | None:
    n = N.numero(str(v)) if v not in (None, "") else None
    return n if n and n > 0 else None


def _int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _limpio(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def ficha(f: dict, base: str, imgs: list | None = None) -> dict | None:
    amigable = (f.get("amigable") or "").strip().lstrip("/")
    if not amigable:
        return None
    url = base + amigable
    ope = (f.get("in_ope") or "").upper()
    operacion = OPERACION.get(ope) or N.operacion(f.get("operacion"))
    precio, moneda = None, None
    venta, alq = _num(f.get("venta_precio") or f.get("in_val")), _num(f.get("alquiler_precio") or f.get("in_vaa"))
    if operacion == "venta" and venta:
        precio, moneda = venta, N.moneda_norm(f.get("venta_moneda"))
    elif operacion in ("alquiler", "alquiler_temporal") and alq:
        precio, moneda = alq, N.moneda_norm(f.get("alquiler_moneda"))
    elif ope == "M" and alq:  # "venta y alquiler" sin precio de venta
        operacion, precio, moneda = "alquiler", alq, N.moneda_norm(f.get("alquiler_moneda"))
    tipo = N.tipo_propiedad(f.get("tipo"), f.get("in_tpr")) or N.tipo_propiedad(f.get("titulo"))
    # ubicación: barrio, localidad y la ruta de Xintel ("Otros países, Uruguay, Maldonado, Punta del Este, Lugano")
    ruta = [x.strip() for x in (f.get("in_iub_desc") or "").split(",")]
    ruta = [x for x in ruta if x and N.simple(x) not in ("otros paises", "uruguay")]
    partes = [_limpio(f.get("in_bar")).title(), _limpio(f.get("in_loc")).title()] + ruta[::-1]
    vistos, ubic = set(), []
    for p in partes:
        if p and N.simple(p) not in vistos:
            vistos.add(N.simple(p))
            ubic.append(p)
    departamento = next((d for d in N.DEPARTAMENTOS if any(N.simple(d) == N.simple(x) for x in ubic)), None)
    lat = lon = None
    coo = re.match(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", f.get("in_coo") or "")
    if coo:
        lat, lon = float(coo.group(1)), float(coo.group(2))
        if not (-36 < lat < -29 and -59 < lon < -52):  # fuera de Uruguay: coordenada por defecto o errónea
            lat = lon = None
    # superficies: in_cub = cubierta, in_sto = total edificada (con semicubierta), in_sut = terreno
    cub, sto, sut = _num(f.get("in_cub")), _num(f.get("in_sto")), _num(f.get("in_sut"))
    campo = tipo == "chacra_campo"
    if tipo in ("terreno", "chacra_campo"):
        m2_ed, m2_tot = None, sut or sto
        if campo and m2_tot and m2_tot < 2000:  # en chacras/campos Xintel suele cargar hectáreas
            m2_tot = None
    else:
        m2_ed = cub or sto
        m2_tot = sut or (sto if sto and m2_ed and sto > m2_ed else None)
    dorm = _int(f.get("cantidad_dormitorios"))
    banos = _int(f.get("in_bao")) or None
    fotos = [u for u in (imgs or []) if isinstance(u, str) and u.startswith("http")]
    if not fotos and f.get("img_princ"):
        fotos = [f["img_princ"]]
    gc = _num(f.get("in_exp")) if operacion != "venta" or tipo == "apartamento" else None
    return {
        "url": url,
        "id_externo": (f.get("codsuc") or f.get("in_suc") or "") + str(f.get("in_num") or ""),
        "titulo": _limpio(f.get("titulo")) or None,
        "precio": precio,
        "moneda": moneda if precio else None,
        "operacion": operacion,
        "tipo": tipo,
        "ubicacion_txt": ", ".join(ubic)[:300] or None,
        "departamento": departamento,
        "dormitorios": dorm,
        "banos": banos,
        "m2_edificados": m2_ed,
        "m2_totales": m2_tot,
        "gastos_comunes": gc,
        "lat": lat,
        "lon": lon,
        "imagenes": fotos[:3],
        "descripcion": _limpio(f.get("in_obs"))[:300] or None,
        "breadcrumb": f"{operacion or ''} > {f.get('tipo') or ''}",
    }


async def listar(cli, fuente: dict, limite_ts: float, max_paginas: int | None = None) -> tuple[dict[str, dict], bool]:
    base = _base(fuente["url_base"])
    registros: dict[str, dict] = {}
    pedidos = 0
    cred = None
    for pagina in ("", "propiedades?ope=V", "propiedades"):
        r = await cli.get(base + pagina)
        pedidos += 1
        if r.status == 200 and r.texto:
            if not pagina:
                base = _base(r.url_final or base)  # www. / https
            cred = credenciales(r.texto)
            if cred:
                break
        elif not pagina:
            raise RuntimeError(f"no abre la home de {base}: {r.status or r.error}")
    if not cred:
        raise RuntimeError(f"{base}: no encontré el código/clave de la API de Xintel en el HTML")
    api, inm, key = cred
    n, paginas, fin, total = 0, 0, False, None
    # la API numera las páginas desde 0; 'paginas' es el índice de la última
    while n <= min(paginas, MAX_PAGINAS):
        if time.monotonic() > limite_ts or (max_paginas and pedidos >= max_paginas):
            return registros, False
        q = {"json": "resultados.fichas", "inm": inm, "apiK": key, "page": n, "rppagina": POR_PAGINA,
             "ordenar": "numficha"}
        async with _API_LOCK:  # todas las inmobiliarias Xintel usan la misma API: de a un pedido, con pausa
            r = await cli.get(api + "?" + urlencode(q))
            await asyncio.sleep(1.0)
        pedidos += 1
        if r.status != 200:
            return registros, False
        try:
            res = json.loads(r.texto)["resultado"]
            fichas = res.get("fichas") or []
            datos = res.get("datos") or {}
        except (ValueError, KeyError, TypeError, AttributeError):
            return registros, False
        imgs = res.get("img") if isinstance(res.get("img"), list) and len(res.get("img")) == len(fichas) else None
        for i, f in enumerate(fichas):
            d = ficha(f, base, imgs[i] if imgs else None)
            if d:
                registros.setdefault(d["url"], d)
        paginas = _int(datos.get("paginas")) or paginas
        total = _int(datos.get("cantidadFichas")) or total
        if not fichas:  # 'paginas' a veces es una de más (total múltiplo de 20): página vacía = fin
            fin = True
            break
        n += 1
    # completo si se recorrieron todas las páginas y se juntó (casi) todo lo que la API dice tener; tolera unas
    # poquitas fichas perdidas (menos de una página) por altas/bajas mientras se paginaba
    completo = (fin or n > paginas) and paginas <= MAX_PAGINAS and bool(total) \
        and len(registros) >= total - max(3, total * 0.005)
    return registros, completo
