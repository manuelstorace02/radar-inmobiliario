"""Panel web: un sitio estático con la evolución del mercado y todos los reportes diarios.

Se arma cada madrugada desde Supabase (reportes_diarios + estadisticas_diarias + corridas) y se publica en
GitHub Pages. Si hay contraseña (PANEL_CLAVE), cada página se publica ENCRIPTADA (AES-GCM, clave derivada con
PBKDF2): el navegador pide la contraseña y la desencripta. Sin la contraseña, el contenido es ilegible aunque el
sitio sea público.
"""
from __future__ import annotations

import base64
import html
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

from .store import Store

PBKDF2_ITER = 150_000
COLORES = ["#1f5f8b", "#d9963a", "#3d8b5f", "#b4432f", "#7a5ea8", "#2a9d9b", "#c0577f", "#6b7a2f"]
BARRIOS_CLAVE = ["Pocitos", "Punta Carretas", "Centro", "Cordón", "Malvín", "Carrasco", "Buceo",
                 "Parque Rodó", "Punta del Este", "Aguada"]

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--fg:#1c1c1a;--mut:#6d6c66;--line:#e7e5de;--acc:#1f5f8b;--pos:#2e7d4f;--neg:#b4432f}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--card:#1c1c1a;--fg:#eceae4;--mut:#a3a19a;--line:#2d2c29;--acc:#86b8de;--pos:#6cc191;--neg:#ec8b78}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 16px 60px}h1{font-size:28px;margin:0;letter-spacing:-.02em}.sub{color:var(--mut)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}.kv{font-size:28px;font-weight:700}.kl{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin:16px 0}.card h2{font-size:17px;margin:0 0 4px}
.nota{color:var(--mut);font-size:13px;margin:0 0 10px}svg{width:100%;height:auto;display:block}
.ley{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mut);margin-top:6px}.ley i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.lista{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.lista a{display:block;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;color:var(--fg);text-decoration:none}
.lista a:hover{border-color:var(--acc)}.lista b{display:block}.lista span{color:var(--mut);font-size:12px}
.btn{display:inline-block;background:var(--acc);color:#fff;border-radius:10px;padding:10px 16px;text-decoration:none;font-weight:600;margin-top:12px}
footer{color:var(--mut);font-size:12px;margin-top:28px}
"""


# --------------------------------------------------------------------------- gráficos SVG (sin librerías)
def _fmt(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def grafico_lineas(series: dict[str, list[tuple[str, float]]], alto: int = 220, unidad: str = "") -> str:
    puntos = [(f, v) for s in series.values() for f, v in s]
    if not puntos:
        return '<p class="nota">Todavía no hay datos suficientes.</p>'
    fechas = sorted({f for f, _ in puntos})
    vmin, vmax = min(v for _, v in puntos), max(v for _, v in puntos)
    if vmin == vmax:
        vmin, vmax = vmin * 0.9, vmax * 1.1 or 1
    W, H, ml, mr, mt, mb = 720, alto, 56, 12, 10, 26
    def x(f):
        i = fechas.index(f)
        return ml + (W - ml - mr) * (i / max(1, len(fechas) - 1))
    def y(v):
        return mt + (H - mt - mb) * (1 - (v - vmin) / (vmax - vmin))
    g = []
    for k in range(5):  # grilla y eje Y
        v = vmin + (vmax - vmin) * k / 4
        g.append(f'<line x1="{ml}" x2="{W-mr}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/>'
                 f'<text x="{ml-6}" y="{y(v)+4:.1f}" text-anchor="end" font-size="11" fill="var(--mut)">{_fmt(v)}{unidad}</text>')
    paso = max(1, len(fechas) // 6)
    for i, f in enumerate(fechas):
        if i % paso == 0 or i == len(fechas) - 1:
            g.append(f'<text x="{x(f):.1f}" y="{H-6}" text-anchor="middle" font-size="11" fill="var(--mut)">{f[8:10]}/{f[5:7]}</text>')
    ley = []
    for n, (nombre, s) in enumerate(series.items()):
        c = COLORES[n % len(COLORES)]
        s = sorted(s)
        d = " ".join(f"{'M' if i == 0 else 'L'}{x(f):.1f},{y(v):.1f}" for i, (f, v) in enumerate(s))
        g.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2.2" stroke-linejoin="round"/>')
        if len(s) == 1:
            g.append(f'<circle cx="{x(s[0][0]):.1f}" cy="{y(s[0][1]):.1f}" r="3.5" fill="{c}"/>')
        ley.append(f'<span><i style="background:{c}"></i>{html.escape(nombre)}</span>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img">{"".join(g)}</svg>'
            + (f'<div class="ley">{"".join(ley)}</div>' if len(series) > 1 else ""))


def grafico_barras(series: dict[str, list[tuple[str, float]]], alto: int = 200) -> str:
    """Barras agrupadas por fecha (ej. publicados vs desaparecidos por día)."""
    fechas = sorted({f for s in series.values() for f, _ in s})[-30:]
    if not fechas:
        return '<p class="nota">Todavía no hay datos suficientes.</p>'
    vmax = max([v for s in series.values() for f, v in s if f in fechas] + [1])
    W, H, ml, mb, mt = 720, alto, 44, 26, 10
    ancho = (W - ml - 8) / len(fechas)
    nb = len(series)
    g = [f'<text x="{ml-6}" y="{mt+8}" text-anchor="end" font-size="11" fill="var(--mut)">{_fmt(vmax)}</text>',
         f'<line x1="{ml}" x2="{W-8}" y1="{H-mb}" y2="{H-mb}" stroke="var(--line)"/>']
    for n, (nombre, s) in enumerate(series.items()):
        c = COLORES[(n * 3) % len(COLORES)]
        dic = dict(s)
        for i, f in enumerate(fechas):
            v = dic.get(f, 0)
            h = (H - mb - mt) * v / vmax
            bx = ml + i * ancho + 2 + n * (ancho - 4) / nb
            g.append(f'<rect x="{bx:.1f}" y="{H-mb-h:.1f}" width="{max(1, (ancho-4)/nb-1):.1f}" height="{h:.1f}" rx="2" fill="{c}">'
                     f'<title>{f} · {html.escape(nombre)}: {_fmt(v)}</title></rect>')
    paso = max(1, len(fechas) // 6)
    for i, f in enumerate(fechas):
        if i % paso == 0 or i == len(fechas) - 1:
            g.append(f'<text x="{ml + i*ancho + ancho/2:.1f}" y="{H-6}" text-anchor="middle" font-size="11" fill="var(--mut)">{f[8:10]}/{f[5:7]}</text>')
    ley = "".join(f'<span><i style="background:{COLORES[(n*3) % len(COLORES)]}"></i>{html.escape(k)}</span>'
                  for n, k in enumerate(series))
    return f'<svg viewBox="0 0 {W} {H}" role="img">{"".join(g)}</svg><div class="ley">{ley}</div>'


# --------------------------------------------------------------------------- encriptado
def encriptar_pagina(contenido: str, clave: str, titulo: str) -> str:
    """Devuelve una página que pide la contraseña y desencripta el contenido en el navegador (WebCrypto)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    sal, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal, iterations=PBKDF2_ITER).derive(clave.encode())
    ct = AESGCM(key).encrypt(iv, contenido.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    datos = json.dumps({"s": b64(sal), "i": b64(iv), "c": b64(ct), "n": PBKDF2_ITER})
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{html.escape(titulo)}</title><style>{CSS}
.login{{max-width:360px;margin:18vh auto 0;padding:24px}}.login input{{width:100%;font-size:16px;padding:12px;border-radius:10px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);margin:14px 0 10px}}.login button{{width:100%;font-size:16px;
padding:12px;border:0;border-radius:10px;background:var(--acc);color:#fff;font-weight:600}}#err{{color:var(--neg);font-size:13px;min-height:18px}}</style></head>
<body><main><form class="card login" id="f"><h1>Radar Inmobiliario</h1><div class="sub">Ingresá la contraseña</div>
<input id="p" type="password" autocomplete="current-password" autofocus><button>Entrar</button><div id="err"></div></form></main>
<script>
const D={datos};
const u=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function abrir(pw){{
  const km=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveKey"]);
  const k=await crypto.subtle.deriveKey({{name:"PBKDF2",salt:u(D.s),iterations:D.n,hash:"SHA-256"}},km,{{name:"AES-GCM",length:256}},false,["decrypt"]);
  const t=await crypto.subtle.decrypt({{name:"AES-GCM",iv:u(D.i)}},k,u(D.c));
  try{{localStorage.setItem("radar_clave",pw)}}catch(e){{}}
  document.open();document.write(new TextDecoder().decode(t));document.close();
}}
document.getElementById("f").onsubmit=async ev=>{{ev.preventDefault();
  try{{await abrir(document.getElementById("p").value)}}catch(e){{document.getElementById("err").textContent="Contraseña incorrecta"}}}};
(async()=>{{let g=null;try{{g=localStorage.getItem("radar_clave")}}catch(e){{}}
  if(g){{try{{await abrir(g)}}catch(e){{try{{localStorage.removeItem("radar_clave")}}catch(_){{}}}}}}}})();
</script></body></html>"""


# --------------------------------------------------------------------------- panel
def armar_panel(reportes: list[dict], stats: list[dict], corridas: list[dict]) -> str:
    e = html.escape
    # evolución diaria del total (suma de avisos activos de estadisticas_diarias)
    activos = defaultdict(int)
    altas, bajas, subas, bajasp = (defaultdict(int) for _ in range(4))
    m2 = defaultdict(dict)
    for s in stats:
        f = str(s["fecha"])[:10]
        activos[f] += int(s.get("avisos_activos") or 0)
        altas[f] += int(s.get("altas") or 0)
        bajas[f] += int(s.get("bajas") or 0)
        subas[f] += int(s.get("subas_precio") or 0)
        bajasp[f] += int(s.get("bajas_precio") or 0)
        if s.get("operacion") == "venta" and s.get("tipo") == "apartamento" and s.get("mediana_usd_m2") \
                and s.get("barrio") in BARRIOS_CLAVE and int(s.get("avisos_activos") or 0) >= 5:
            m2[s["barrio"]][f] = float(s["mediana_usd_m2"])
    ultimo = max(activos) if activos else None
    kpi = lambda v, l: f'<div class="kpi"><div class="kv">{e(str(v))}</div><div class="kl">{e(l)}</div></div>'  # noqa: E731
    kpis = ""
    if ultimo:
        sem = sorted(activos)[-7:]
        kpis = (kpi(_fmt(activos[ultimo]), "avisos activos hoy")
                + kpi(_fmt(sum(altas[f] for f in sem)), "publicados (7 días)")
                + kpi(_fmt(sum(bajas[f] for f in sem)), "desaparecidos (7 días)")
                + kpi(_fmt(sum(subas[f] for f in sem)), "subas de precio (7 días)")
                + kpi(_fmt(sum(bajasp[f] for f in sem)), "bajas de precio (7 días)"))
    # cobertura por fuente
    fuentes = defaultdict(dict)
    for c in corridas:
        fuentes[c["fuente_id"]][str(c["fecha"])[:10]] = int(c.get("avisos_ok") or 0)

    lista = ""
    for r in sorted(reportes, key=lambda r: str(r["fecha"]), reverse=True):
        f = str(r["fecha"])[:10]
        lista += f'<a href="reportes/{f}.html"><b>{f[8:10]}/{f[5:7]}/{f[:4]}</b><span>reporte del día</span></a>'
    hoy = str(max(r["fecha"] for r in reportes))[:10] if reportes else None

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Radar Inmobiliario · Panel</title><style>{CSS}</style></head><body><main>
<h1>Radar Inmobiliario</h1><div class="sub">Panel · actualizado {e(ultimo or "—")}</div>
{f'<a class="btn" href="reportes/{hoy}.html">Ver el reporte de hoy →</a>' if hoy else ''}
<div class="kpis">{kpis}</div>
<section class="card"><h2>Avisos activos monitoreados</h2><p class="nota">Total por día, todas las fuentes.</p>
{grafico_lineas({"Avisos activos": sorted(activos.items())})}</section>
<section class="card"><h2>Movimiento diario</h2><p class="nota">Últimos 30 días. Durante la carga inicial de cada fuente no se cuentan publicados nuevos.</p>
{grafico_barras({"Publicados": sorted(altas.items()), "Desaparecidos": sorted(bajas.items()),
                 "Subas de precio": sorted(subas.items()), "Bajas de precio": sorted(bajasp.items())})}</section>
<section class="card"><h2>Precio por m² — apartamentos en venta</h2><p class="nota">Mediana en USD por barrio (barrios con 5+ avisos con m²).</p>
{grafico_lineas({b: sorted(v.items()) for b, v in sorted(m2.items())}, alto=260)}</section>
<section class="card"><h2>Avisos revisados por fuente</h2>
{grafico_lineas({k: sorted(v.items()) for k, v in sorted(fuentes.items())}, alto=180)}</section>
<section class="card"><h2>Reportes anteriores</h2><div class="lista">{lista or '<p class="nota">Todavía no hay reportes.</p>'}</div></section>
<footer>"Desapareció" no significa vendido ni alquilado: el aviso dejó de estar publicado.</footer>
</main></body></html>"""


def generar_sitio(store: Store, salida: Path, clave: str | None) -> dict:
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "reportes").mkdir(exist_ok=True)
    reportes = [r for r in store.select("reportes_diarios", columnas="fecha,reporte_html") if r.get("reporte_html")]
    stats = store.select("estadisticas_diarias")
    corridas = store.select("corridas", columnas="fecha,fuente_id,avisos_ok")
    escribir = (lambda contenido, titulo: encriptar_pagina(contenido, clave, titulo)) if clave \
        else (lambda contenido, titulo: contenido)
    volver = ('<div style="max-width:1080px;margin:16px auto 0;padding:0 16px"><a href="../index.html" '
              'style="color:inherit;opacity:.7;text-decoration:none">← Panel</a></div>')
    for r in reportes:
        f = str(r["fecha"])[:10]
        pagina = r["reporte_html"].replace("<main>", volver + "<main>", 1)
        (salida / "reportes" / f"{f}.html").write_text(escribir(pagina, f"Radar · {f}"), encoding="utf-8")
    (salida / "index.html").write_text(escribir(armar_panel(reportes, stats, corridas), "Radar Inmobiliario"),
                                       encoding="utf-8")
    (salida / ".nojekyll").write_text("")
    (salida / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    return {"reportes": len(reportes), "encriptado": bool(clave)}
