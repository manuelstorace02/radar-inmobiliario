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
                        e.get("valor_anterior") or "", e.get("valor_nuevo") or "", _pct(pct), a["url"]])
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
        if d.h not in serie:
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
    secciones.append(("Precio mediano por m² (USD) por barrio", ["Barrio", "Op.", "Tipo", "USD/m² hoy", "vs 7 d", "vs 30 d"],
                      filas_m[:60], "Solo avisos activos con m² informados."))

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

    return {"fecha": d.h, "resumen": resumen, "salud": filas, "secciones": secciones, "pendientes": pend}


def a_markdown(rep: dict, max_filas: int = 10) -> str:
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
            out.append(f"\n…y {len(filas) - max_filas} más en el reporte completo.")
        out.append("")
    return "\n".join(out)


def a_html(rep: dict) -> str:
    e = html.escape
    partes = []
    for titulo, cols, filas, nota in rep["secciones"]:
        cuerpo = "".join(
            "<tr>" + "".join(
                f'<td><a href="{e(v)}" target="_blank">ver</a></td>' if c == "Link" else f"<td>{e(str(v))}</td>"
                for c, v in zip(cols, f)) + "</tr>" for f in filas) or \
            f'<tr><td colspan="{len(cols)}" class="vacio">Sin movimientos.</td></tr>'
        partes.append(f"<section><h2>{e(titulo)}</h2>" + (f'<p class="nota">{e(nota)}</p>' if nota else "") +
                      f'<div class="tabla"><table><thead><tr>{"".join(f"<th>{e(c)}</th>" for c in cols)}</tr></thead>'
                      f"<tbody>{cuerpo}</tbody></table></div></section>")
    salud = "".join(f"<tr>{''.join(f'<td>{e(str(v))}</td>' for v in f)}</tr>" for f in rep["salud"])
    resumen = "".join(f"<li>{_md_bold(e(x))}</li>" for x in rep["resumen"])
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Radar Inmobiliario {rep['fecha']}</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e3de;--acc:#1f5f8b;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--mut:#a09f99;--line:#2e2e2b;--acc:#7fb6dd;--card:#1d1d1b}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}h1{{font-size:24px;margin:0 0 4px}}
h2{{font-size:17px;margin:32px 0 6px}}.nota{{color:var(--mut);font-size:13px;margin:0 0 8px}}
ul.res{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 14px 14px 32px}}
.tabla{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{color:var(--mut);font-weight:600}}a{{color:var(--acc)}}.vacio{{color:var(--mut)}}details{{margin-top:32px;color:var(--mut)}}
</style></head><body><main>
<h1>Radar Inmobiliario</h1><div class="nota">{rep['fecha']}</div><ul class="res">{resumen}</ul>
{''.join(partes)}
<details><summary>Estado de las fuentes hoy</summary><div class="tabla"><table><thead><tr>
<th>Fuente</th><th>Estado</th><th>Revisadas</th><th>OK</th><th>Nuevos</th><th>Bajas</th><th>Precio</th><th>Notas</th>
</tr></thead><tbody>{salud}</tbody></table></div></details>
</main></body></html>"""


def _md_bold(s: str) -> str:
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
