"""Corrida de scraping: descubrir -> revisitar -> extraer -> comparar -> guardar eventos."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, unquote

from . import adapters, discover, extract
from . import normalize as N
from .http import Cliente, Respuesta
from .store import Store

CAMPOS_DATO = ["dormitorios", "m2_edificados", "operacion", "tipo", "barrio"]
MAX_HTML_FALLIDOS = 10


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def id_externo(url: str) -> str:
    path = urlsplit(url).path
    nums = re.findall(r"(\d{5,})", path)
    if nums:
        return nums[-1]
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _primero(fn, *textos):
    for t in textos:
        if t:
            v = fn(t)
            if v:
                return v
    return None


class Contexto:
    """Lo que se carga una vez por corrida y comparten todas las fuentes."""

    def __init__(self, store: Store, hoy: date):
        self.store = store
        self.hoy = hoy.isoformat()
        self.barrios = N.Barrios(store.select("barrios_alias"))
        self.tc = float(store.param("tipo_cambio_usd_uyu", "40") or 40)
        self.inmobiliarias = store.select("inmobiliarias")
        self._inm_alias = []
        for i in self.inmobiliarias:
            for a in [i["nombre"], *(i.get("alias") or [])]:
                if a and len(N.simple(a)) >= 4:
                    self._inm_alias.append((N.simple(a), i["id"]))
        self._inm_alias.sort(key=lambda x: -len(x[0]))
        self.sin_mapear: Counter = Counter()
        self.sin_mapear_url: dict = {}

    def inmobiliaria_de(self, anunciante: str | None) -> str | None:
        a = N.simple(anunciante)
        if not a:
            return None
        for alias, iid in self._inm_alias:
            if alias == a or re.search(rf"\b{re.escape(alias)}\b", a):
                return iid
        return None


def armar_aviso(datos: dict, fuente: dict, ctx: Contexto) -> dict:
    url = datos["url"]
    url_txt = unquote(urlsplit(url).path).replace("-", " ").replace("/", " ").replace("_", " ")
    titulo = datos.get("titulo")
    bc = datos.get("breadcrumb")
    desc = (datos.get("descripcion") or "")[:400]
    texto = (datos.get("_texto") or "")[:1500]

    operacion = datos.get("operacion") or _primero(N.operacion, url_txt, titulo, bc, desc, texto)
    tipo = datos.get("tipo") or _primero(N.tipo_propiedad, url_txt, titulo, bc, desc)

    barrio, depto = datos.get("barrio"), datos.get("departamento")
    if not barrio:
        barrio, depto2 = ctx.barrios.buscar(datos.get("ubicacion_txt"), bc, titulo, url_txt, desc,
                                            depto_hint=depto)
        depto = depto or depto2
    barrio_raw = datos.get("ubicacion_txt") or bc
    if not barrio:
        clave = (N.simple(barrio_raw or titulo or "")[:80], depto or "")
        ctx.sin_mapear[clave] += 1
        ctx.sin_mapear_url.setdefault(clave, url)

    if fuente["tipo"] == "inmobiliaria":
        inm = fuente.get("inmobiliaria_id")
    else:
        inm = ctx.inmobiliaria_de(datos.get("anunciante"))

    # sanidad: valores imposibles se descartan (los portales a veces cargan "1 m²" o 0 dormitorios en terrenos)
    for c, minimo, maximo in (("m2_edificados", 10, 100000), ("m2_totales", 10, 10_000_000), ("dormitorios", 0, 30),
                              ("banos", 0, 30)):
        v = datos.get(c)
        if v is not None and not (minimo <= float(v) <= maximo):
            datos[c] = None
    if tipo in ("terreno", "chacra_campo", "garaje") and datos.get("dormitorios") == 0:
        datos["dormitorios"] = None

    precio, moneda = datos.get("precio"), datos.get("moneda")
    if precio is not None and moneda is None:
        moneda = "USD" if (operacion == "venta" or precio > 30000 and operacion != "alquiler") else "UYU"

    return {
        "id": f"{fuente['id']}:{datos.get('id_externo') or id_externo(url)}",
        "fuente_id": fuente["id"], "url": url, "inmobiliaria_id": inm,
        "anunciante": datos.get("anunciante"), "operacion": operacion, "tipo": tipo,
        "titulo": (titulo or "")[:300] or None, "departamento": depto, "barrio": barrio,
        "barrio_raw": (barrio_raw or "")[:200] or None, "direccion": datos.get("direccion"),
        "precio": precio, "moneda": moneda, "precio_usd": N.a_usd(precio, moneda, ctx.tc),
        "gastos_comunes": datos.get("gastos_comunes"), "m2_totales": datos.get("m2_totales"),
        "m2_edificados": datos.get("m2_edificados"), "dormitorios": datos.get("dormitorios"),
        "banos": datos.get("banos"), "garaje": datos.get("garaje"),
        "lat": datos.get("lat"), "lon": datos.get("lon"),
        "imagenes": [i for i in (datos.get("imagenes") or []) if isinstance(i, str)][:5],
    }


def clasificar(resp: Respuesta, datos: dict | None, es_aviso) -> str:
    """'ok' | 'baja_segura' | 'baja_posible' | 'error'"""
    if resp.error == "bloqueado_por_robots":
        return "error"
    if resp.status in (410,):
        return "baja_segura"
    if resp.status == 404:
        return "baja_posible"
    if resp.status != 200:
        return "error"
    if datos and datos.get("no_disponible"):
        return "baja_segura"
    if resp.url_final.rstrip("/") != resp.url.rstrip("/") and not es_aviso(resp.url_final):
        return "baja_posible"  # redirigió a la home o a un listado
    if datos and (datos.get("precio") is not None or datos.get("titulo")):
        return "ok"
    return "error"


async def correr_fuente(fuente: dict, ctx: Contexto, minutos: float, max_urls: int | None = None,
                        solo_descubrir: bool = False) -> dict:
    t0 = time.monotonic()
    store, hoy = ctx.store, ctx.hoy
    ad = adapters.cargar(fuente["id"])
    patron = re.compile(fuente["patron_aviso"]) if fuente.get("patron_aviso") else None

    def es_aviso(u: str) -> bool:
        if ad and hasattr(ad, "es_aviso"):
            return ad.es_aviso(u)
        return bool(patron.search(u)) if patron else True

    corrida = {"fecha": hoy, "fuente_id": fuente["id"], "inicio": ahora(), "urls_descubiertas": 0,
               "urls_revisadas": 0, "avisos_ok": 0, "nuevos": 0, "bajas": 0, "cambios_precio": 0,
               "errores": 0, "estado": "ok", "notas": ""}
    cli = Cliente(delay_seg=float(fuente.get("delay_seg") or 1.5))
    try:
        # 1) descubrir
        try:
            if ad and hasattr(ad, "descubrir"):
                descubiertas = await ad.descubrir(cli, fuente)
            elif fuente.get("modo") == "listado":
                descubiertas = await discover.por_listado(cli, fuente.get("listado_urls"), patron)
            else:
                descubiertas = await discover.por_sitemap(cli, fuente["url_base"], patron)
                if not descubiertas and fuente.get("listado_urls"):
                    descubiertas = await discover.por_listado(cli, fuente.get("listado_urls"), patron)
        except Exception as e:  # noqa: BLE001
            descubiertas = {}
            corrida["notas"] += f"descubrimiento falló: {type(e).__name__}: {e}. "
        if not isinstance(descubiertas, dict):  # adapters/listados devuelven set: sin lastmod
            descubiertas = {u: None for u in descubiertas}
        descubiertas = {u: lm for u, lm in descubiertas.items() if es_aviso(u)}
        corrida["urls_descubiertas"] = len(descubiertas)
        if solo_descubrir:
            corrida["muestra"] = sorted(descubiertas)[:20]
            return corrida

        # 2) qué revisar hoy: nuevas primero, después las activas que hace más que no se revisan
        conocidos = {a["url"]: a for a in store.select("avisos", eq={"fuente_id": fuente["id"]})}
        nuevas = [u for u in descubiertas if u not in conocidos or conocidos[u]["estado"] == "baja"]
        activas = sorted((a for a in conocidos.values() if a["estado"] == "activo"),
                         key=lambda a: a.get("revisado_en") or "")
        # Si el sitemap lista todo el stock, los activos que faltan en él son candidatos a baja: se revisan primero.
        faltantes: set[str] = set()
        sitemap_sano = len(descubiertas) >= 0.7 * len(activas)
        if fuente.get("sitemap_completo") and sitemap_sano:
            faltantes = {a["url"] for a in activas if a["url"] not in descubiertas}
        elif fuente.get("sitemap_completo") and activas:
            corrida["notas"] += (f"sitemap con {len(descubiertas)} URLs vs {len(activas)} activos: "
                                 "no se usa como señal de baja hoy. ")
        # activos cuyo lastmod del sitemap es posterior a nuestra última revisión: probablemente cambiaron
        modificados = [a["url"] for a in activas if descubiertas.get(a["url"]) and
                       (not a.get("revisado_en") or descubiertas[a["url"]] > str(a["revisado_en"])[:10])]
        # Orden de la cola: 1) faltantes del sitemap y modificados (alta probabilidad de baja/cambio),
        # 2) nuevas hasta el 70% del cupo restante, 3) activos que hace más que no se revisan, 4) resto de nuevas.
        # Así, aun durante la carga inicial, todos los días se re-chequea una parte del stock conocido.
        tope = max_urls or int(fuente.get("max_urls") or 3000)
        prioridad = list(dict.fromkeys(sorted(faltantes) + modificados))[:tope]
        resto = tope - len(prioridad)
        cupo_nuevas = int(resto * 0.7) if len(activas) else resto
        viejas = [a["url"] for a in activas]
        cola = list(dict.fromkeys(prioridad + nuevas[:cupo_nuevas] + viejas + nuevas[cupo_nuevas:]))[:tope]
        # Carga inicial: mientras conozcamos menos del 90% de lo que publica la fuente, lo "nuevo" no es noticia
        # (es stock viejo que todavía no habíamos cargado): no se generan eventos de alta.
        conocidos_url = set(conocidos)
        cobertura = (len(conocidos_url & set(descubiertas)) / len(descubiertas)) if descubiertas else 1.0
        carga_inicial = cobertura < 0.9
        corrida["cobertura"] = round(cobertura, 3)
        if carga_inicial:
            corrida["notas"] += (f"carga inicial: {len(conocidos_url & set(descubiertas))} de {len(descubiertas)} "
                                 f"avisos cargados ({cobertura:.0%}); todavía no se reportan altas. ")
        if not descubiertas and conocidos:
            corrida["notas"] += "no se descubrió ninguna URL (¿cambió el sitemap/listado?). "

        # 3) pedir y extraer (secuencial por dominio; el Cliente ya mete la pausa)
        resultados: list[tuple[str, Respuesta, dict | None]] = []
        limite = t0 + minutos * 60
        for url in cola:
            if time.monotonic() > limite:
                corrida["notas"] += f"se cortó por tiempo tras {len(resultados)} URLs. "
                break
            resp = await cli.get(url)
            datos = None
            if resp.status == 200 and resp.texto:
                try:
                    datos = extract.extraer(resp.texto, url)
                    if ad and hasattr(ad, "extraer"):
                        datos = ad.extraer(resp.texto, url, datos)
                except Exception as e:  # noqa: BLE001
                    corrida["notas"] += f"extractor falló en {url}: {e}. "
            resultados.append((url, resp, datos))
    finally:
        await cli.cerrar()

    # 4) salud de la corrida
    clases = [(u, r, d, clasificar(r, d, es_aviso)) for u, r, d in resultados]
    # faltantes del sitemap que no llegamos a pedir hoy: cuentan como "baja posible" (necesitan 2 días seguidos)
    pedidas = {u for u, _, _ in resultados}
    for u in sorted(faltantes - pedidas):
        clases.append((u, Respuesta(u, u, -1, "", error="falta_en_sitemap"), None, "baja_posible"))
    pedidas_c = [c for c in clases if c[1].status != -1]
    n = len(pedidas_c) or 1
    errores = sum(1 for c in pedidas_c if c[3] == "error")
    oks = sum(1 for c in pedidas_c if c[3] == "ok")
    corrida["urls_revisadas"], corrida["errores"], corrida["avisos_ok"] = len(pedidas_c), errores, oks
    posibles_bajas = sum(1 for c in clases if c[3].startswith("baja"))
    sospechosa = (errores / n > 0.3) or (len(pedidas_c) >= 20 and oks == 0) or \
                 (len(conocidos) >= 20 and posibles_bajas > 0.3 * len(conocidos)) or \
                 (not descubiertas and bool(conocidos))
    if sospechosa:
        corrida["estado"] = "sospechosa"
        corrida["notas"] += "corrida sospechosa: NO se marcaron bajas. "
    if not clases and not descubiertas:
        corrida["estado"] = "fallida"

    # 5) comparar con lo que había y armar eventos
    filas, eventos, foto, fallidos = [], [], [], []
    for url, resp, datos, clase in clases:
        prev = conocidos.get(url)
        if clase == "ok":
            a = armar_aviso(datos, fuente, ctx)
            foto.append({**a, "status": resp.status, "precio_dudoso": bool(datos.get("_precio_dudoso"))})
            if prev and prev["id"] != a["id"]:
                a["id"] = prev["id"]
            a.update(estado="activo", ultima_vez=hoy, revisado_en=hoy, fallos_consecutivos=0,
                     primera_vez=(prev or {}).get("primera_vez") or hoy,
                     propiedad_id=(prev or {}).get("propiedad_id"))
            base_ev = {"fecha": hoy, "aviso_id": a["id"], "fuente_id": fuente["id"]}
            if not prev:
                corrida["nuevos"] += 1
                if not carga_inicial:
                    eventos.append({**base_ev, "tipo": "alta", "valor_nuevo": _fmt(a), "precio_usd_nuevo": a["precio_usd"]})
            else:
                if prev["estado"] == "baja":
                    eventos.append({**base_ev, "tipo": "reaparece", "valor_nuevo": _fmt(a),
                                    "precio_usd_nuevo": a["precio_usd"]})
                if a["precio"] is not None and prev.get("precio") is not None and \
                        (abs(float(a["precio"]) - float(prev["precio"])) > 0.5 or a["moneda"] != prev.get("moneda")):
                    eventos.append({**base_ev, "tipo": "precio", "campo": "precio",
                                    "valor_anterior": f"{prev.get('moneda')} {prev['precio']}",
                                    "valor_nuevo": f"{a['moneda']} {a['precio']}",
                                    "precio_usd_anterior": prev.get("precio_usd"), "precio_usd_nuevo": a["precio_usd"]})
                    corrida["cambios_precio"] += 1
                for c in CAMPOS_DATO:
                    if prev.get(c) not in (None, "") and a.get(c) not in (None, "") and str(prev[c]) != str(a[c]):
                        eventos.append({**base_ev, "tipo": "dato", "campo": c,
                                        "valor_anterior": str(prev[c]), "valor_nuevo": str(a[c])})
                for c in a:  # no pisar datos buenos con vacíos
                    if a[c] in (None, "", []) and prev.get(c) not in (None, "", []):
                        a[c] = prev[c]
            filas.append(a)
            if datos.get("precio") is None and len(fallidos) < MAX_HTML_FALLIDOS:
                fallidos.append((url, resp.texto))
        elif clase.startswith("baja") and prev and prev["estado"] == "activo":
            fallos = int(prev.get("fallos_consecutivos") or 0) + 1
            a = {**prev, "revisado_en": hoy, "fallos_consecutivos": fallos}
            if not sospechosa and (clase == "baja_segura" or fallos >= 2):
                a["estado"] = "baja"
                eventos.append({"fecha": hoy, "aviso_id": prev["id"], "fuente_id": fuente["id"], "tipo": "baja",
                                "valor_anterior": _fmt(prev), "precio_usd_anterior": prev.get("precio_usd")})
                corrida["bajas"] += 1
            filas.append(a)
        elif clase == "error" and resp.status == 200 and len(fallidos) < MAX_HTML_FALLIDOS:
            fallidos.append((url, resp.texto))

    # 6) guardar
    if filas:
        store.upsert("avisos", filas)
    if eventos:
        store.insert("eventos", eventos)
    try:
        if foto:
            blob = gzip.compress("\n".join(json.dumps(f, ensure_ascii=False, default=str) for f in foto).encode())
            store.subir_archivo(f"scrapeos/{hoy}/{fuente['id']}.jsonl.gz", blob)
        for i, (u, html) in enumerate(fallidos):
            store.subir_archivo(f"html_fallidos/{hoy}/{fuente['id']}/{i:02d}.html",
                                f"<!-- {u} -->\n{html}".encode(), "text/plain")
    except Exception as e:  # noqa: BLE001
        corrida["notas"] += f"no se pudo subir a Storage: {e}. "
    corrida["fin"] = ahora()
    corrida["notas"] = corrida["notas"].strip()
    store.insert("corridas", [corrida])
    return corrida


def _fmt(a: dict) -> str:
    partes = [a.get("operacion"), a.get("tipo"), a.get("barrio"),
              f"{a.get('dormitorios')}d" if a.get("dormitorios") is not None else None,
              f"{a.get('m2_edificados')}m2" if a.get("m2_edificados") else None,
              f"{a.get('moneda')} {a.get('precio')}" if a.get("precio") is not None else None]
    return " · ".join(str(p) for p in partes if p)


async def correr(store: Store, hoy: date, fuentes_ids: list[str] | None = None, minutos: float = 45,
                 max_urls: int | None = None) -> list[dict]:
    ctx = Contexto(store, hoy)
    fuentes = [f for f in store.select("fuentes") if f.get("activa") and f.get("estado") != "pausada"]
    if fuentes_ids:
        fuentes = [f for f in fuentes if f["id"] in fuentes_ids]
    # Las fuentes van en paralelo (son dominios distintos); dentro de cada una, de a una request.
    sem = asyncio.Semaphore(25)

    async def una(f):
        async with sem:
            try:
                return await correr_fuente(f, ctx, minutos, max_urls)
            except Exception as e:  # noqa: BLE001
                c = {"fecha": ctx.hoy, "fuente_id": f["id"], "inicio": ahora(), "fin": ahora(),
                     "estado": "fallida", "notas": f"{type(e).__name__}: {e}"}
                store.insert("corridas", [c])
                return c

    res = await asyncio.gather(*(una(f) for f in fuentes))
    if ctx.sin_mapear:
        previos = {(r["texto"], r["departamento"]): r for r in store.select("barrios_sin_mapear")}
        store.upsert("barrios_sin_mapear", [
            {"texto": t, "departamento": d, "veces": n + int((previos.get((t, d)) or {}).get("veces") or 0),
             "ejemplo_url": ctx.sin_mapear_url[(t, d)], "ultima_vez": ctx.hoy}
            for (t, d), n in ctx.sin_mapear.items() if t])
    return res
