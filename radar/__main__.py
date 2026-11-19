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
  consulta "SELECT ..."     Prueba una consulta de solo lectura sobre las vistas dash_*
  reporte-guardar ID --nombre ... --sql ...   Guarda un reporte personalizado (diario o --semanal)
  reporte-pausar ID [--activar]   Pausa (o reactiva) un reporte personalizado
  reportes                  Lista los reportes personalizados
  reportes-correr           Corre los reportes personalizados de hoy
  tipo-cambio               Actualiza el dólar desde una API pública (si falla, deja el anterior)
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

from . import adapters, dedupe, discover, extract, personalizados, report, scrape
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
    # Se archivan en Storage (Supabase no los muestra como página: es solo respaldo/descarga).
    try:
        store.subir_archivo(f"reportes/{rep['fecha']}.html", ht.encode(), "text/html; charset=utf-8")
        store.subir_archivo(f"reportes/{rep['fecha']}.md", md.encode(), "text/markdown; charset=utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"(no se pudo subir el reporte a Storage: {e})", file=sys.stderr)
    print(md)
    return md


def _salud_md(store) -> str:
    corr = store.select("corridas", eq={"fecha": hoy().isoformat()})
    lineas = [f"- {c['fuente_id']}: {c.get('estado')} — revisadas {c.get('urls_revisadas') or 0}, "
              f"ok {c.get('avisos_ok') or 0}, errores {c.get('errores') or 0}. {c.get('notas') or ''}".strip()
              for c in sorted(corr, key=lambda c: c["fuente_id"])]
    sm = sorted(store.select("barrios_sin_mapear"), key=lambda r: -int(r.get("veces") or 0))[:10]
    if sm:
        lineas.append("- Barrios sin mapear: " + "; ".join(f"{r['texto']} (×{r['veces']})" for r in sm))
    lineas.append(f"- Tipo de cambio usado: {store.param('tipo_cambio_usd_uyu', '?')} UYU por USD")
    return "\n".join(lineas) or "- (sin corridas hoy)"


def cmd_diaria(store, a):
    print("== 1/5 scraping"); cmd_correr(store, a)
    print("\n== 2/5 misma propiedad entre fuentes"); cmd_dedupe(store, a)
    print("\n== 3/5 estadísticas"); cmd_estadisticas(store, a)
    print("\n== 4/5 reporte\n"); md = cmd_reporte(store, a)
    print("\n== 5/5 reportes personalizados\n"); pers = cmd_reportes_correr(store, a)
    # Queda guardado en la base: la tarea de ChatGPT lo lee de acá con el conector de Supabase.
    salud = _salud_md(store)
    store.upsert("reportes_diarios", [{"fecha": hoy().isoformat(), "reporte_md": md, "personalizados_md": pers or "",
                                       "salud_md": salud, "generado_en": datetime.now(TZ).isoformat()}])
    print("\n== salud\n" + salud)
    print("\nReporte guardado en la tabla reportes_diarios.")


def cmd_tipo_cambio(store, a):
    """Actualiza el dólar (USD->UYU) desde una API pública. Si falla, deja el valor anterior."""
    import httpx
    try:
        r = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=20).json()
        tc = float(r["rates"]["UYU"])
        if not 20 < tc < 80:
            raise ValueError(f"valor raro: {tc}")
        store.set_param("tipo_cambio_usd_uyu", f"{tc:.3f}")
        print(f"tipo de cambio: {tc:.3f} UYU por USD")
    except Exception as e:  # noqa: BLE001
        print(f"(no se pudo actualizar el tipo de cambio, queda {store.param('tipo_cambio_usd_uyu')}: {e})")


def cmd_consulta(store, a):
    filas = personalizados.probar(store, a.sql)
    print(f"{len(filas)} fila(s)\n")
    print(personalizados.tabla_md(filas, max_filas=a.n))


def cmd_reporte_guardar(store, a):
    n = personalizados.guardar(store, a.id, a.nombre, a.descripcion or "", a.sql,
                               "semanal" if a.semanal else "diaria", not a.siempre)
    print(f"Guardado '{a.id}' ({'semanal, los lunes' if a.semanal else 'diario'}). Hoy da {n} fila(s).")


def cmd_reporte_pausar(store, a):
    r = [x for x in store.select("reportes_guardados") if x["id"] == a.id]
    if not r:
        sys.exit(f"No existe el reporte {a.id}")
    store.upsert("reportes_guardados", [{**r[0], "activo": a.activar}])
    print(f"{a.id}: {'activo' if a.activar else 'pausado'}")


def cmd_reportes_listar(store, a):
    for r in sorted(store.select("reportes_guardados"), key=lambda r: r["id"]):
        print(f"{'✔' if r.get('activo') else '⏸'} {r['id']:<32} {r.get('frecuencia'):<8} filas={r.get('ultimas_filas')} "
              f"{'ERROR: ' + r['ultimo_error'] if r.get('ultimo_error') else ''}\n    {r['nombre']} — {r.get('descripcion') or ''}")


def cmd_reportes_correr(store, a):
    try:
        md = personalizados.correr_todos(store, hoy())
    except Exception as e:  # noqa: BLE001
        md = ""
        print(f"(no se pudieron correr los reportes personalizados: {e})")
    print(md or "(no hay reportes personalizados guardados)")
    return md


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
    s = sub.add_parser("consulta"); s.add_argument("sql"); s.add_argument("-n", type=int, default=30)
    s = sub.add_parser("reporte-guardar"); s.add_argument("id"); s.add_argument("--nombre", required=True)
    s.add_argument("--descripcion"); s.add_argument("--sql", required=True)
    s.add_argument("--semanal", action="store_true", help="correr solo los lunes")
    s.add_argument("--siempre", action="store_true", help="mostrarlo aunque dé 0 filas")
    s = sub.add_parser("reporte-pausar"); s.add_argument("id"); s.add_argument("--activar", action="store_true")
    sub.add_parser("reportes"); sub.add_parser("reportes-correr"); sub.add_parser("tipo-cambio")
    a = p.parse_args(argv)
    store = abrir()
    {"seed": cmd_seed, "correr": cmd_correr, "diaria": cmd_diaria, "dedupe": cmd_dedupe,
     "estadisticas": cmd_estadisticas, "reporte": cmd_reporte, "probar-url": cmd_probar_url,
     "probar-fuente": cmd_probar_fuente, "param": cmd_param, "alias": cmd_alias,
     "consulta": cmd_consulta, "reporte-guardar": cmd_reporte_guardar, "reporte-pausar": cmd_reporte_pausar,
     "reportes": cmd_reportes_listar, "reportes-correr": cmd_reportes_correr, "tipo-cambio": cmd_tipo_cambio, "salud": cmd_salud}[a.cmd](store, a)


if __name__ == "__main__":
    main()
