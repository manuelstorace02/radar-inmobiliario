"""Línea de comandos. Uso: python -m radar <comando> [opciones]

  seed                      Carga/actualiza fuentes, inmobiliarias y barrios desde data/seed/*.csv
  diaria                    Rutina completa: correr + dedupe + estadisticas + reporte
  correr [--fuente ID ...]  Scrapea (todas las fuentes activas o las indicadas)
  dedupe                    Agrupa la misma propiedad entre fuentes
  estadisticas              Calcula estadísticas del día
  reporte                   Genera reportes/AAAA-MM-DD.md y .html (y los sube a Storage)
  probar-url URL [--fuente ID]   Muestra qué extrae de un aviso (no guarda nada)
  probar-fuente ID          Descubre URLs y extrae 5 avisos de muestra (no guarda nada)
  param CLAVE VALOR         Cambia un parámetro (ej.: tipo_cambio_usd_uyu 40.2)
  alias TEXTO BARRIO DEPTO  Enseña un nombre de barrio nuevo (ej.: alias "Pocitos Nuevo" Pocitos Montevideo)
  salud                     Últimas corridas por fuente y barrios sin mapear
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import adapters, dedupe, discover, extract, report, scrape
from .http import Cliente
from .store import ROOT, abrir

TZ = ZoneInfo("America/Montevideo")


def hoy() -> date:
    if os.environ.get("RADAR_FECHA"):
        return date.fromisoformat(os.environ["RADAR_FECHA"])
    return datetime.now(TZ).date()


def _csv(nombre: str) -> list[dict]:
    p = ROOT / "data" / "seed" / nombre
    with p.open(encoding="utf-8") as f:
        return [{k: (v.strip() if isinstance(v, str) else v) or None for k, v in r.items()}
                for r in csv.DictReader(l for l in f if not l.startswith("#"))]


def cmd_seed(store, a):
    inm = _csv("inmobiliarias.csv")
    for i in inm:
        i["alias"] = [x.strip() for x in (i.get("alias") or "").split(";") if x.strip()]
    if inm:
        store.upsert("inmobiliarias", inm)
    fu = _csv("fuentes.csv")
    for f in fu:
        f["activa"] = (f.get("activa") or "si").lower() in ("si", "sí", "true", "1", "yes")
        f["sitemap_completo"] = (f.get("sitemap_completo") or "no").lower() in ("si", "sí", "true", "1", "yes")
        f["max_urls"] = int(f.get("max_urls") or 3000)
        f["delay_seg"] = float(f.get("delay_seg") or 1.5)
        f["modo"] = f.get("modo") or "sitemap"
        f["estado"] = f.get("estado") or "nueva"
    # no pisar el estado que el agente ya haya puesto en la base
    previos = {f["id"]: f for f in store.select("fuentes")}
    for f in fu:
        if f["id"] in previos and previos[f["id"]].get("estado"):
            f["estado"] = previos[f["id"]]["estado"]
    store.upsert("fuentes", fu)
    ba = _csv("barrios.csv")
    from .normalize import simple
    filas = {}
    for b in ba:
        for alias in [b["barrio"], *(x for x in (b.get("alias") or "").split(";"))]:
            if alias and alias.strip():
                filas[(simple(alias), b["departamento"])] = {"alias": simple(alias), "barrio": b["barrio"],
                                                             "departamento": b["departamento"], "tipo": b.get("tipo") or "barrio"}
    store.upsert("barrios_alias", list(filas.values()))
    print(f"seed: {len(inm)} inmobiliarias, {len(fu)} fuentes, {len(filas)} alias de barrios")


def cmd_correr(store, a):
    res = asyncio.run(scrape.correr(store, hoy(), a.fuente or None, minutos=a.minutos, max_urls=a.max_urls))
    for c in res:
        print(f"{c['fuente_id']:<28} {c.get('estado') or '':<11} desc={c.get('urls_descubiertas', 0):<6} "
              f"rev={c.get('urls_revisadas', 0):<6} ok={c.get('avisos_ok', 0):<6} nuevos={c.get('nuevos', 0):<5} "
              f"bajas={c.get('bajas', 0):<4} precio={c.get('cambios_precio', 0):<4} err={c.get('errores', 0)}  {c.get('notas') or ''}")


def cmd_dedupe(store, a):
    r = dedupe.agrupar(store)
    print(json.dumps({k: v for k, v in r.items() if k != "dudosos"}, ensure_ascii=False))
    if r["dudosos"]:
        print("Pares dudosos (revisar a mano, score / url A / url B):")
        for d in r["dudosos"][:20]:
            print("  ", *d)


def cmd_estadisticas(store, a):
    print(f"estadísticas: {report.calcular_estadisticas(store, hoy())} grupos")


def cmd_reporte(store, a):
    rep = report.armar(store, hoy())
    md, ht = report.a_markdown(rep), report.a_html(rep)
    out = ROOT / "reportes"
    out.mkdir(exist_ok=True)
    (out / f"{rep['fecha']}.md").write_text(md, encoding="utf-8")
    (out / f"{rep['fecha']}.html").write_text(ht, encoding="utf-8")
    link = None
    try:
        store.subir_archivo(f"reportes/{rep['fecha']}.html", ht.encode(), "text/html")
        store.subir_archivo("reportes/ultimo.html", ht.encode(), "text/html")
        store.subir_archivo(f"reportes/{rep['fecha']}.md", md.encode(), "text/markdown")
        link = store.link_firmado(f"reportes/{rep['fecha']}.html", dias=7)
    except Exception as e:  # noqa: BLE001
        print(f"(no se pudo subir el reporte a Storage: {e})", file=sys.stderr)
    print(md)
    if link:
        print(f"\nReporte visual completo (link válido 7 días): {link}")


def cmd_diaria(store, a):
    print("== 1/4 scraping"); cmd_correr(store, a)
    print("\n== 2/4 misma propiedad entre fuentes"); cmd_dedupe(store, a)
    print("\n== 3/4 estadísticas"); cmd_estadisticas(store, a)
    print("\n== 4/4 reporte\n"); cmd_reporte(store, a)


def cmd_probar_url(store, a):
    async def go():
        cli = Cliente(delay_seg=0)
        r = await cli.get(a.url)
        await cli.cerrar()
        return r
    r = asyncio.run(go())
    print(f"status={r.status} final={r.url_final} error={r.error}")
    if r.status != 200:
        return
    d = extract.extraer(r.texto, a.url)
    if a.fuente:
        ad = adapters.cargar(a.fuente)
        if ad and hasattr(ad, "extraer"):
            d = ad.extraer(r.texto, a.url, d)
    fuente = next((f for f in store.select("fuentes") if f["id"] == a.fuente), None) or \
             {"id": a.fuente or "prueba", "tipo": "inmobiliaria", "inmobiliaria_id": None}
    ctx = scrape.Contexto(store, hoy())
    aviso = scrape.armar_aviso(d, fuente, ctx)
    print(json.dumps({"extraido": {k: v for k, v in d.items() if k != "_texto"}, "aviso_final": aviso},
                     ensure_ascii=False, indent=2, default=str))


def cmd_probar_fuente(store, a):
    fuente = next((f for f in store.select("fuentes") if f["id"] == a.id), None)
    if not fuente:
        sys.exit(f"No existe la fuente {a.id} (¿corriste seed?)")
    ctx = scrape.Contexto(store, hoy())

    async def go():
        c = await scrape.correr_fuente(fuente, ctx, minutos=10, solo_descubrir=True)
        print(f"URLs de avisos descubiertas: {c['urls_descubiertas']}  {c.get('notas') or ''}")
        for u in c.get("muestra", [])[:10]:
            print("  ", u)
        cli = Cliente(delay_seg=float(fuente.get("delay_seg") or 1.5))
        ad = adapters.cargar(fuente["id"])
        for u in c.get("muestra", [])[:a.n]:
            r = await cli.get(u)
            if r.status != 200:
                print(f"\n{u}\n  status={r.status} {r.error or ''}")
                continue
            d = extract.extraer(r.texto, u)
            if ad and hasattr(ad, "extraer"):
                d = ad.extraer(r.texto, u, d)
            av = scrape.armar_aviso(d, fuente, ctx)
            print(f"\n{u}\n  " + " | ".join(f"{k}={av.get(k)}" for k in
                  ("operacion", "tipo", "departamento", "barrio", "precio", "moneda", "m2_edificados",
                   "dormitorios", "banos", "anunciante")) + f"\n  titulo={av.get('titulo')}\n  ubicacion_raw={av.get('barrio_raw')}")
        await cli.cerrar()
    asyncio.run(go())


def cmd_alias(store, a):
    from .normalize import simple
    store.upsert("barrios_alias", [{"alias": simple(a.texto), "barrio": a.barrio, "departamento": a.departamento,
                                    "tipo": a.tipo}])
    print(f"alias '{simple(a.texto)}' -> {a.barrio} ({a.departamento}). "
          "Acordate de agregarlo también a data/seed/barrios.csv para que quede en el repo.")


def cmd_param(store, a):
    store.set_param(a.clave, a.valor)
    print(f"{a.clave} = {a.valor}")


def cmd_salud(store, a):
    corr = store.select("corridas", gte={"fecha": a.desde or hoy().isoformat()}, orden="fecha.desc")
    for c in corr[:200]:
        print(f"{c['fecha']} {c['fuente_id']:<28} {c.get('estado') or '':<11} rev={c.get('urls_revisadas')} "
              f"ok={c.get('avisos_ok')} err={c.get('errores')} {c.get('notas') or ''}")
    sm = sorted(store.select("barrios_sin_mapear"), key=lambda r: -int(r.get("veces") or 0))[:30]
    if sm:
        print("\nBarrios sin mapear (agregar a data/seed/barrios.csv o a la tabla barrios_alias):")
        for r in sm:
            print(f"  ×{r['veces']:<4} [{r.get('departamento') or '?'}] {r['texto']}   ej: {r.get('ejemplo_url')}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="radar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    for n in ("correr", "diaria"):
        s = sub.add_parser(n)
        s.add_argument("--fuente", action="append", help="id de fuente (se puede repetir)")
        s.add_argument("--minutos", type=float, default=float(os.environ.get("RADAR_MINUTOS", 40)),
                       help="tiempo máximo por fuente (default 40)")
        s.add_argument("--max-urls", type=int, default=None)
    sub.add_parser("dedupe"); sub.add_parser("estadisticas"); sub.add_parser("reporte")
    s = sub.add_parser("probar-url"); s.add_argument("url"); s.add_argument("--fuente")
    s = sub.add_parser("probar-fuente"); s.add_argument("id"); s.add_argument("-n", type=int, default=5)
    s = sub.add_parser("param"); s.add_argument("clave"); s.add_argument("valor")
    s = sub.add_parser("alias"); s.add_argument("texto"); s.add_argument("barrio"); s.add_argument("departamento")
    s.add_argument("--tipo", default="zona")
    s = sub.add_parser("salud"); s.add_argument("--desde")
    a = p.parse_args(argv)
    store = abrir()
    {"seed": cmd_seed, "correr": cmd_correr, "diaria": cmd_diaria, "dedupe": cmd_dedupe,
     "estadisticas": cmd_estadisticas, "reporte": cmd_reporte, "probar-url": cmd_probar_url,
     "probar-fuente": cmd_probar_fuente, "param": cmd_param, "alias": cmd_alias, "salud": cmd_salud}[a.cmd](store, a)


if __name__ == "__main__":
    main()
