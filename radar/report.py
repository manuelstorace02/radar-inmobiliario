"""Estadísticas diarias + reporte del día (Markdown para el mensaje y HTML visual)."""
from __future__ import annotations

import html
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from .store import Store

OPS = {"venta": "Venta", "alquiler": "Alquiler", "alquiler_temporal": "Alq. temporal"}


def _usd(x) -> str:
    if x is None:
        return "—"
    return f"US$ {float(x):,.0f}".replace(",", ".")


def _pct(x) -> str:
    return "—" if x is None else f"{x:+.1f}%"


class Datos:
    def __init__(self, store: Store, hoy: date):
        self.hoy = hoy
        self.h = hoy.isoformat()
        self.avisos = {a["id"]: a for a in store.select("avisos")}
        self.fuentes = {f["id"]: f for f in store.select("fuentes")}
        self.inm = {i["id"]: i["nombre"] for i in store.select("inmobiliarias")}
        self.eventos = store.select("eventos", gte={"fecha": (hoy - timedelta(days=30)).isoformat()})
        self.corridas = [c for c in store.select("corridas", eq={"fecha": self.h})]
        self.stats_prev = store.select("estadisticas_diarias", gte={"fecha": (hoy - timedelta(days=31)).isoformat()})
        self.tc = store.param("tipo_cambio_usd_uyu")
        self.sin_mapear = sorted(store.select("barrios_sin_mapear"), key=lambda r: -int(r.get("veces") or 0))

    def inmobiliaria(self, a: dict) -> str:
        if a.get("inmobiliaria_id") and a["inmobiliaria_id"] in self.inm:
            return self.inm[a["inmobiliaria_id"]]
        f = self.fuentes.get(a["fuente_id"], {})
        if f.get("tipo") == "portal":
            return (a.get("anunciante") or "¿?") + f" ({f.get('nombre')})"
        return f.get("nombre") or a["fuente_id"]

    def ev(self, desde_dias: int = 0, tipo: str | None = None) -> list[dict]:
        lim = (self.hoy - timedelta(days=desde_dias)).isoformat()
        return [e for e in self.eventos if str(e["fecha"])[:10] >= lim and (tipo is None or e["tipo"] == tipo)
                and e["aviso_id"] in self.avisos]


# --------------------------------------------------------------------------- estadísticas
def calcular_estadisticas(store: Store, hoy: date) -> int:
    d = Datos(store, hoy)
    grupos = defaultdict(list)
    for a in d.avisos.values():
        if a["estado"] == "activo" and a.get("operacion") and a.get("tipo"):
            grupos[(a.get("departamento") or "¿?", a.get("barrio") or "¿?", a["operacion"], a["tipo"])].append(a)
    cont = defaultdict(Counter)
    for e in d.ev(0):
        a = d.avisos[e["aviso_id"]]
        k = (a.get("departamento") or "¿?", a.get("barrio") or "¿?", a.get("operacion"), a.get("tipo"))
        if e["tipo"] == "precio" and e.get("precio_usd_anterior") and e.get("precio_usd_nuevo"):
            cont[k]["subas_precio" if float(e["precio_usd_nuevo"]) > float(e["precio_usd_anterior"]) else "bajas_precio"] += 1
        elif e["tipo"] in ("alta", "baja"):
            cont[k][e["tipo"] + "s"] += 1
    filas = []
    for k in set(grupos) | {k for k in cont if k[2] and k[3]}:
        av = grupos.get(k, [])
        precios = [float(a["precio_usd"]) for a in av if a.get("precio_usd")]
        m2 = [float(a["precio_usd"]) / float(a["m2_edificados"]) for a in av
              if a.get("precio_usd") and a.get("m2_edificados") and float(a["m2_edificados"]) >= 15]
        filas.append({"fecha": d.h, "departamento": k[0], "barrio": k[1], "operacion": k[2], "tipo": k[3],
                      "avisos_activos": len(av),
                      "mediana_usd": round(statistics.median(precios), 0) if precios else None,
                      "mediana_usd_m2": round(statistics.median(m2), 1) if m2 else None,
                      "altas": cont[k]["altas"], "bajas": cont[k]["bajas"],
                      "subas_precio": cont[k]["subas_precio"], "bajas_precio": cont[k]["bajas_precio"]})
    if filas:
        store.upsert("estadisticas_diarias", filas)
    return len(filas)


# --------------------------------------------------------------------------- reporte
def armar(store: Store, hoy: date) -> dict:
    d = Datos(store, hoy)
    secciones: list[tuple[str, list[str], list[list[str]], str]] = []  # (titulo, columnas, filas, nota)

    # Salud
    filas = []
    for c in sorted(d.corridas, key=lambda c: c["fuente_id"]):
        filas.append([d.fuentes.get(c["fuente_id"], {}).get("nombre", c["fuente_id"]), c.get("estado") or "",
                      str(c.get("urls_revisadas") or 0), str(c.get("avisos_ok") or 0), str(c.get("nuevos") or 0),
                      str(c.get("bajas") or 0), str(c.get("cambios_precio") or 0), (c.get("notas") or "")[:120]])
    malas = [c for c in d.corridas if c.get("estado") != "ok"]

    hoy_ev = d.ev(0)
    altas = [e for e in hoy_ev if e["tipo"] == "alta"]
    bajas = [e for e in hoy_ev if e["tipo"] == "baja"]
    precios = [e for e in hoy_ev if e["tipo"] == "precio" and e.get("precio_usd_anterior") and e.get("precio_usd_nuevo")]
    subas = [e for e in precios if float(e["precio_usd_nuevo"]) > float(e["precio_usd_anterior"])]
    activos = sum(1 for a in d.avisos.values() if a["estado"] == "activo")

    resumen = [
        f"**{activos:,} avisos activos** monitoreados en "
        f"{sum(1 for f in d.fuentes.values() if f.get('activa') and f.get('estado') != 'pausada')} fuente(s).".replace(",", "."),
        f"Hoy: **{len(altas)} publicados nuevos**, **{len(bajas)} dados de baja**, "
        f"**{len(precios)} cambios de precio** ({len(subas)} subas, {len(precios) - len(subas)} bajas).",
    ]
    if malas:
        resumen.append(f"⚠️ {len(malas)} fuente(s) con problemas hoy: " +
                       ", ".join(f"{c['fuente_id']} ({c.get('estado')})" for c in malas) + ".")

    # Por inmobiliaria
    por_inm = defaultdict(Counter)
    for e in hoy_ev:
        a = d.avisos[e["aviso_id"]]
        k = d.inmobiliaria(a)
        if e["tipo"] == "precio" and e in precios:
            por_inm[k]["sube" if e in subas else "baja_precio"] += 1
        else:
            por_inm[k][e["tipo"]] += 1
    filas_inm = sorted(([k, str(v["alta"]), str(v["baja"]), str(v["sube"]), str(v["baja_precio"])]
                        for k, v in por_inm.items()), key=lambda r: -sum(int(x) for x in r[1:]))[:20]
    secciones.append(("Movimiento por inmobiliaria (hoy)",
                      ["Inmobiliaria", "Nuevos", "Bajas", "Suben precio", "Bajan precio"], filas_inm, ""))

    # Bajas, con chequeo multifuente
    filas_b = []
    for e in bajas[:40]:
        a = d.avisos[e["aviso_id"]]
        otros = [o for o in d.avisos.values() if a.get("propiedad_id") and o.get("propiedad_id") == a["propiedad_id"]
                 and o["id"] != a["id"]]
        sigue = [d.inmobiliaria(o) for o in otros if o["estado"] == "activo"]
        dias = (hoy - date.fromisoformat(str(a["primera_vez"])[:10])).days if a.get("primera_vez") else None
        estado_multi = ("sigue publicada en: " + ", ".join(sigue)) if sigue else \
                       ("desapareció de TODAS las fuentes" if otros else "solo estaba en esta fuente")
        filas_b.append([d.inmobiliaria(a), OPS.get(a.get("operacion"), a.get("operacion") or ""), a.get("tipo") or "",
                        a.get("barrio") or "¿?", _usd(a.get("precio_usd")), f"{dias} d" if dias is not None else "—",
                        estado_multi, a["url"]])
    secciones.append(("Avisos que desaparecieron hoy", ["Inmobiliaria", "Op.", "Tipo", "Barrio", "Último precio",
                      "Publicado", "Otras fuentes", "Link"], filas_b,
                      "Desaparecer no es lo mismo que vender/alquilar: puede haber vencido o cambiado de inmobiliaria."))

    # Cambios de precio
    filas_p = []
    for e in sorted(precios, key=lambda e: -abs(float(e["precio_usd_nuevo"]) / float(e["precio_usd_anterior"]) - 1))[:30]:
        a = d.avisos[e["aviso_id"]]
        pct = 100 * (float(e["precio_usd_nuevo"]) / float(e["precio_usd_anterior"]) - 1)
        filas_p.append([d.inmobiliaria(a), OPS.get(a.get("operacion"), ""), a.get("tipo") or "", a.get("barrio") or "¿?",
                        _usd(e.get("precio_usd_anterior")), _usd(e.get("precio_usd_nuevo")), _pct(pct), a["url"]])
    secciones.append(("Cambios de precio de hoy", ["Inmobiliaria", "Op.", "Tipo", "Barrio", "Antes", "Ahora", "Var.", "Link"],
                      filas_p, ""))

    # Tendencias por barrio (7 y 30 días) — cuántas inmobiliarias distintas mueven precios en la misma zona
    filas_t = []
    for ventana in (7, 30):
        g = defaultdict(lambda: {"subas": 0, "bajas": 0, "pcts": [], "inm_suben": set(), "inm_bajan": set()})
        for e in d.ev(ventana, "precio"):
            if not (e.get("precio_usd_anterior") and e.get("precio_usd_nuevo")):
                continue
            a = d.avisos[e["aviso_id"]]
            k = (a.get("barrio") or "¿?", OPS.get(a.get("operacion"), ""), a.get("tipo") or "")
            pct = 100 * (float(e["precio_usd_nuevo"]) / float(e["precio_usd_anterior"]) - 1)
            g[k]["pcts"].append(pct)
            if pct > 0:
                g[k]["subas"] += 1; g[k]["inm_suben"].add(d.inmobiliaria(a))
            else:
                g[k]["bajas"] += 1; g[k]["inm_bajan"].add(d.inmobiliaria(a))
        for k, v in g.items():
            n_inm = max(len(v["inm_suben"]), len(v["inm_bajan"]))
            if n_inm >= 2 or v["subas"] + v["bajas"] >= 4:
                señal = "↑ suben" if len(v["inm_suben"]) > len(v["inm_bajan"]) else "↓ bajan"
                filas_t.append([f"{ventana} días", *k, str(v["subas"]), str(v["bajas"]),
                                _pct(statistics.mean(v["pcts"])),
                                f"{señal}: {len(v['inm_suben']) if señal.startswith('↑') else len(v['inm_bajan'])} inmobiliarias"])
    filas_t.sort(key=lambda r: (r[0], -int(r[7].split(": ")[1].split()[0])))
    secciones.append(("Tendencias de precio por zona", ["Ventana", "Barrio", "Op.", "Tipo", "Subas", "Bajas",
                      "Var. prom.", "Señal"], filas_t[:30],
                      "Aparece una zona cuando 2+ inmobiliarias distintas movieron precios en la misma dirección, "
                      "o hubo 4+ cambios."))

    # Mediana USD/m2 por barrio vs hace 7 y 30 días
    hist = defaultdict(dict)
    for s in d.stats_prev:
        if s.get("mediana_usd_m2"):
            hist[(s["departamento"], s["barrio"], s["operacion"], s["tipo"])][str(s["fecha"])[:10]] = float(s["mediana_usd_m2"])
    filas_m = []
    for k, serie in hist.items():
        if d.h not in serie or k[2] != "venta" or k[1] == "¿?":
            continue
        actual = serie[d.h]
        def hace(n):
            f = (hoy - timedelta(days=n)).isoformat()
            ant = [v for fecha, v in serie.items() if fecha <= f]
            return ant[-1] if ant else None
        v7, v30 = hace(7), hace(30)
        filas_m.append([k[1], OPS.get(k[2], k[2]), k[3], f"{actual:,.0f}".replace(",", "."),
                        _pct(100 * (actual / v7 - 1)) if v7 else "—", _pct(100 * (actual / v30 - 1)) if v30 else "—"])
    filas_m.sort(key=lambda r: r[0])
    secciones.append(("Precio mediano por m² en venta (USD) por barrio", ["Barrio", "Op.", "Tipo", "USD/m² hoy", "vs 7 d", "vs 30 d"],
                      filas_m[:60], "Solo avisos de venta activos con m² informados."))

    # Misma propiedad, distinto precio
    props = defaultdict(list)
    for a in d.avisos.values():
        if a.get("propiedad_id") and a["estado"] == "activo" and a.get("precio_usd"):
            props[a["propiedad_id"]].append(a)
    filas_d = []
    for pid, av in props.items():
        if len(av) < 2:
            continue
        mn, mx = min(av, key=lambda a: float(a["precio_usd"])), max(av, key=lambda a: float(a["precio_usd"]))
        dif = 100 * (float(mx["precio_usd"]) / float(mn["precio_usd"]) - 1)
        if dif >= 3:
            filas_d.append([mn.get("barrio") or "¿?", mn.get("tipo") or "", str(len(av)),
                            f"{d.inmobiliaria(mn)}: {_usd(mn['precio_usd'])}", f"{d.inmobiliaria(mx)}: {_usd(mx['precio_usd'])}",
                            f"{dif:.1f}%"])
    filas_d.sort(key=lambda r: -float(r[5][:-1]))
    multi = sum(1 for v in props.values() if len(v) > 1)
    secciones.append(("Misma propiedad, distinto precio según dónde se publica",
                      ["Barrio", "Tipo", "Avisos", "Más barata", "Más cara", "Diferencia"], filas_d[:30],
                      f"{multi} propiedades activas están publicadas en más de un lugar."))

    # Pendientes del agente
    pend = [f"{r['texto']} ({r.get('departamento') or 's/depto'}) ×{r['veces']}" for r in d.sin_mapear[:15]]

    # KPIs y datos para gráficos (reporte visual)
    import re as _re
    cargas = []
    for c in d.corridas:
        m = _re.search(r"carga inicial: (\d+) de (\d+)", c.get("notas") or "")
        if m:
            cargas.append((d.fuentes.get(c["fuente_id"], {}).get("nombre", c["fuente_id"]), int(m.group(1)), int(m.group(2))))
    m2_barrio = defaultdict(list)
    for a in d.avisos.values():
        if (a["estado"] == "activo" and a.get("operacion") == "venta" and a.get("tipo") == "apartamento"
                and a.get("barrio") and a.get("precio_usd") and a.get("m2_edificados") and float(a["m2_edificados"]) >= 15):
            m2_barrio[a["barrio"]].append(float(a["precio_usd"]) / float(a["m2_edificados"]))
    graf_m2 = sorted(((b, statistics.median(v), len(v)) for b, v in m2_barrio.items() if len(v) >= 5),
                     key=lambda x: -x[1])[:15]
    graf_inm = sorted(((k, v["alta"], v["baja"], v["sube"], v["baja_precio"]) for k, v in por_inm.items()),
                      key=lambda x: -sum(x[1:]))[:10]
    kpis = {"activos": activos, "altas": len(altas), "bajas": len(bajas), "subas": len(subas),
            "bajas_precio": len(precios) - len(subas), "multifuente": multi,
            "fuentes": sum(1 for f in d.fuentes.values() if f.get("activa") and f.get("estado") != "pausada"),
            "tc": d.tc}
    return {"fecha": d.h, "resumen": resumen, "salud": filas, "secciones": secciones, "pendientes": pend,
            "kpis": kpis, "cargas": cargas, "graf_m2": graf_m2, "graf_inm": graf_inm, "malas": [c["fuente_id"] for c in malas]}


def a_markdown(rep: dict, max_filas: int = 20) -> str:
    out = [f"# Radar Inmobiliario — {rep['fecha']}", "", *[f"- {x}" for x in rep["resumen"]], ""]
    for titulo, cols, filas, nota in rep["secciones"]:
        if not filas:
            continue
        out += [f"## {titulo}", ""]
        if nota:
            out += [f"_{nota}_", ""]
        cols_md = [c for c in cols if c != "Link"]
        out.append("| " + " | ".join(cols_md) + " |")
        out.append("|" + "---|" * len(cols_md))
        for f in filas[:max_filas]:
            celdas = [v for c, v in zip(cols, f) if c != "Link"]
            if "Link" in cols:
                link = f[cols.index("Link")]
                celdas[0] = f"[{celdas[0]}]({link})"
            out.append("| " + " | ".join(str(c).replace("|", "/") for c in celdas) + " |")
        if len(filas) > max_filas:
            out.append(f"\n…y {len(filas) - max_filas} más (pedíselas a Codex o miralas en el dashboard).")
        out.append("")
    return "\n".join(out)


def _n(x) -> str:
    return f"{int(round(float(x))):,}".replace(",", ".")


def a_html(rep: dict, personalizados: list | None = None) -> str:
    """Reporte visual autocontenido (sin internet, sin librerías): se abre con doble clic en cualquier navegador.
    Trae un hueco <!--RESUMEN--> para que la tarea de ChatGPT escriba ahí "Lo más importante hoy"."""
    e = html.escape
    k = rep.get("kpis", {})
    fecha = rep["fecha"]

    def tarjeta(valor, etiqueta, clase=""):
        return f'<div class="kpi {clase}"><div class="kv">{e(str(valor))}</div><div class="kl">{e(etiqueta)}</div></div>'

    kpis = "".join([
        tarjeta(_n(k.get("activos", 0)), "avisos activos"),
        tarjeta(k.get("altas", 0), "publicados hoy", "pos"),
        tarjeta(k.get("bajas", 0), "desaparecieron", "neg"),
        tarjeta(k.get("subas", 0), "subieron de precio", "neg"),
        tarjeta(k.get("bajas_precio", 0), "bajaron de precio", "pos"),
        tarjeta(k.get("multifuente", 0), "propiedades en +1 lugar"),
    ])

    chips = []
    for nombre, cargados, total in rep.get("cargas", []):
        pct = 100 * cargados / total if total else 0
        chips.append(f'<span class="chip">{e(nombre)}: carga inicial {pct:.0f}% '
                     f'<span class="mut">({_n(cargados)} de {_n(total)})</span></span>')
    for f in rep.get("malas", []):
        chips.append(f'<span class="chip alerta">⚠ {e(f)} con problemas hoy</span>')

    def barras(items, fmt, titulo, nota=""):
        if not items:
            return ""
        mx = max(v for _, v, *_ in items) or 1
        filas = "".join(
            f'<div class="bar"><div class="bl">{e(str(n))}</div><div class="bt"><div class="bf" style="width:{100*v/mx:.1f}%"></div></div>'
            f'<div class="bv">{e(fmt(v, *resto))}</div></div>' for n, v, *resto in items)
        return f'<section class="card"><h2>{e(titulo)}</h2>{f"<p class=nota>{e(nota)}</p>" if nota else ""}{filas}</section>'

    graf_m2 = barras([(b, v, c) for b, v, c in rep.get("graf_m2", [])],
                     lambda v, c: f"US$ {_n(v)}/m²  ·  {c} avisos",
                     "Precio por m² — apartamentos en venta", "Mediana por barrio, barrios con 5+ avisos con m² informado.")
    inm = rep.get("graf_inm", [])
    graf_inm = ""
    if inm:
        mx = max(sum(x[1:]) for x in inm) or 1
        filas = ""
        for n, alta, baja, sube, bajap in inm:
            segs = "".join(f'<div class="seg {c}" style="width:{100*v/mx:.1f}%" title="{t}: {v}"></div>'
                           for v, c, t in ((alta, "s-alta", "Nuevos"), (baja, "s-baja", "Bajas"),
                                           (sube, "s-sube", "Suben"), (bajap, "s-bajap", "Bajan")) if v)
            filas += (f'<div class="bar"><div class="bl">{e(n)}</div><div class="bt stack">{segs}</div>'
                      f'<div class="bv">{alta + baja + sube + bajap}</div></div>')
        graf_inm = ('<section class="card"><h2>Movimiento por inmobiliaria hoy</h2><div class="ley">'
                    '<span><i class="s-alta"></i>Nuevos</span><span><i class="s-baja"></i>Desaparecieron</span>'
                    '<span><i class="s-sube"></i>Suben precio</span><span><i class="s-bajap"></i>Bajan precio</span>'
                    f'</div>{filas}</section>')

    def tabla(cols, filas):
        cab = "".join(f"<th>{e(c)}</th>" for c in cols if c != "Link")
        cuerpo = ""
        for f in filas:
            celdas = []
            link = f[cols.index("Link")] if "Link" in cols else None
            for c, v in zip(cols, f):
                if c == "Link":
                    continue
                txt = e(str(v))
                if link and not celdas:
                    txt = f'<a href="{e(link)}" target="_blank" rel="noopener">{txt}</a>'
                cls = ' class="up"' if str(v).startswith("+") else (' class="down"' if str(v).startswith("-") and "%" in str(v) else "")
                celdas.append(f"<td{cls}>{txt}</td>")
            cuerpo += "<tr>" + "".join(celdas) + "</tr>"
        return f'<div class="tw"><table><thead><tr>{cab}</tr></thead><tbody>{cuerpo}</tbody></table></div>'

    secciones = ""
    for titulo, cols, filas, nota in rep["secciones"]:
        if not filas or (titulo.startswith("Movimiento por inmobiliaria") and graf_inm):
            continue  # ese ya está como gráfico
        secciones += (f'<section class="card"><h2>{e(titulo)} <span class="cnt">{len(filas)}</span></h2>'
                      + (f'<p class="nota">{e(nota)}</p>' if nota else "") + tabla(cols, filas) + "</section>")

    tus = ""
    for p in personalizados or []:
        if p.get("error"):
            cuerpo = f'<p class="nota">⚠ No se pudo correr hoy: {e(p["error"][:200])}</p>'
        elif p.get("filas"):
            from .personalizados import _celda
            cols = [c for c in p["filas"][0].keys() if c != "url"]
            def fmt(c, v):
                if v is None:
                    return "—"
                if isinstance(v, (int, float)) and ("usd" in c or "precio" in c):
                    return _usd(v)
                return _celda(v, 80)
            filas = [[*(fmt(c, r.get(c)) for c in cols), r.get("url") or ""] for r in p["filas"][:50]]
            bonitas = [c.replace("_usd", " (USD)").replace("_", " ").capitalize() for c in cols]
            cuerpo = tabla(bonitas + ["Link"], filas) if "url" in p["filas"][0] else tabla(bonitas, [f[:-1] for f in filas])
        else:
            cuerpo = '<p class="nota">Sin resultados hoy.</p>'
        tus += (f'<div class="pers"><h3>{e(p["nombre"])}</h3>'
                + (f'<p class="nota">{e(p["descripcion"])}</p>' if p.get("descripcion") else "") + cuerpo + "</div>")
    if tus:
        tus = f'<section class="card destacado"><h2>Tus reportes</h2>{tus}</section>'

    resumen_def = "".join(f"<li>{_md_bold(e(x))}</li>" for x in rep["resumen"])
    salud = "".join("<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in f) + "</tr>" for f in rep["salud"])

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar Inmobiliario · {fecha}</title>
<style>
:root{{--bg:#f6f5f1;--card:#fff;--fg:#1c1c1a;--mut:#6d6c66;--line:#e7e5de;--acc:#1f5f8b;--pos:#2e7d4f;--neg:#b4432f;
--alta:#3d8b5f;--baja:#c4553f;--sube:#d9963a;--bajap:#4a86b5;--bar:#1f5f8b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121211;--card:#1c1c1a;--fg:#eceae4;--mut:#a3a19a;--line:#2d2c29;--acc:#86b8de;
--pos:#6cc191;--neg:#ec8b78;--bar:#6aa6d4}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:28px 16px 60px}}
header h1{{font-size:28px;margin:0;letter-spacing:-.02em}}header .sub{{color:var(--mut);margin-top:2px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px}}.chip{{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px}}
.chip.alerta{{border-color:var(--neg);color:var(--neg)}}.mut{{color:var(--mut)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
.kv{{font-size:28px;font-weight:700;letter-spacing:-.02em}}.kl{{color:var(--mut);font-size:13px}}
.kpi.pos .kv{{color:var(--pos)}}.kpi.neg .kv{{color:var(--neg)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 18px 14px;margin:16px 0}}
.card h2{{font-size:17px;margin:0 0 10px}}.card h3{{font-size:15px;margin:14px 0 4px}}.cnt{{color:var(--mut);font-weight:500;font-size:13px}}
.destacado{{border:2px solid var(--acc)}}.resumen ul{{margin:0;padding-left:20px}}.resumen li{{margin:6px 0}}
.nota{{color:var(--mut);font-size:13px;margin:0 0 10px}}
.bar{{display:grid;grid-template-columns:minmax(110px,30%) 1fr auto;gap:10px;align-items:center;margin:7px 0;font-size:13px}}
.bl{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bt{{background:var(--line);border-radius:6px;height:12px;overflow:hidden;display:flex}}
.bf{{background:var(--bar);height:100%;border-radius:6px}}.bv{{color:var(--mut);white-space:nowrap}}
.seg{{height:100%}}.s-alta{{background:var(--alta)}}.s-baja{{background:var(--baja)}}.s-sube{{background:var(--sube)}}.s-bajap{{background:var(--bajap)}}
.ley{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--mut);margin-bottom:6px}}.ley i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--mut);font-weight:600}}
td.up{{color:var(--neg)}}td.down{{color:var(--pos)}}a{{color:var(--acc);text-decoration:none}}a:hover{{text-decoration:underline}}
details{{margin-top:22px;color:var(--mut)}}summary{{cursor:pointer}}footer{{color:var(--mut);font-size:12px;margin-top:28px}}
@media (max-width:560px){{.kv{{font-size:23px}}.bar{{grid-template-columns:96px 1fr auto}}}}
</style></head><body><main>
<header><h1>Radar Inmobiliario</h1><div class="sub">{fecha} · {k.get("fuentes", 1)} fuente(s) monitoreada(s)</div>
<div class="chips">{"".join(chips)}</div></header>
<div class="kpis">{kpis}</div>
<section class="card destacado resumen"><h2>Lo más importante hoy</h2><ul><!--RESUMEN-->{resumen_def}<!--/RESUMEN--></ul></section>
{tus}{graf_inm}{graf_m2}{secciones}
<details><summary>Estado de las fuentes</summary><div class="tw"><table><thead><tr><th>Fuente</th><th>Estado</th>
<th>Revisadas</th><th>OK</th><th>Nuevos</th><th>Bajas</th><th>Precio</th><th>Notas</th></tr></thead><tbody>{salud}</tbody></table></div></details>
<footer>"Desapareció" no significa vendido ni alquilado: el aviso dejó de estar publicado. Precios en USD
(pesos convertidos al tipo de cambio del día{f": {k['tc']}" if k.get("tc") else ""}).</footer>
</main></body></html>"""


def _md_bold(s: str) -> str:
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
