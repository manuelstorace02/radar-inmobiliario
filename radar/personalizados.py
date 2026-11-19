"""Reportes y búsquedas personalizadas: consultas SQL guardadas en `reportes_guardados` que se corren
cada día (o cada lunes) y se agregan al mensaje de la mañana."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .store import Store


def _celda(v, max_len: int = 60) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        v = int(v) if v.is_integer() else round(v, 2)
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}".replace(",", ".")
    s = str(v).replace("|", "/").replace("\n", " ")
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def tabla_md(filas: list[dict], max_filas: int = 20) -> str:
    if not filas:
        return "_Sin resultados._"
    cols = [c for c in filas[0].keys() if c != "url"]
    tiene_url = "url" in filas[0]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for f in filas[:max_filas]:
        celdas = [_celda(f.get(c)) for c in cols]
        if tiene_url and f.get("url"):
            celdas[0] = f"[{celdas[0]}]({f['url']})"
        out.append("| " + " | ".join(celdas) + " |")
    if len(filas) > max_filas:
        out.append(f"\n…y {len(filas) - max_filas} más.")
    return "\n".join(out)


def probar(store: Store, sql: str) -> list[dict]:
    return store.consulta(sql)


def guardar(store: Store, id_: str, nombre: str, descripcion: str, sql: str, frecuencia: str = "diaria",
            solo_si_hay_resultados: bool = True, pedido_por: str = "Manuel") -> int:
    filas = store.consulta(sql)  # si la consulta falla, no se guarda
    store.upsert("reportes_guardados", [{
        "id": id_, "nombre": nombre, "descripcion": descripcion, "sql": sql, "frecuencia": frecuencia,
        "solo_si_hay_resultados": solo_si_hay_resultados, "activo": True, "pedido_por": pedido_por,
        "ultima_ejecucion": datetime.now(timezone.utc).isoformat(), "ultimas_filas": len(filas), "ultimo_error": None}])
    return len(filas)


def correr_todos(store: Store, hoy: date) -> str:
    reportes = [r for r in store.select("reportes_guardados") if r.get("activo")]
    if not reportes:
        return ""
    secciones, actualizados = [], []
    for r in sorted(reportes, key=lambda r: r["id"]):
        if r.get("frecuencia") == "semanal" and hoy.weekday() != 0:  # semanales: los lunes
            continue
        r = dict(r)
        r["ultima_ejecucion"] = datetime.now(timezone.utc).isoformat()
        try:
            filas = store.consulta(r["sql"])
            r["ultimas_filas"], r["ultimo_error"] = len(filas), None
            if filas or not r.get("solo_si_hay_resultados", True):
                cab = f"### {r['nombre']}"
                if r.get("descripcion"):
                    cab += f"\n_{r['descripcion']}_"
                secciones.append(f"{cab}\n\n{tabla_md(filas)}")
        except Exception as e:  # noqa: BLE001
            r["ultimo_error"] = str(e)[:500]
            secciones.append(f"### {r['nombre']}\n\n⚠️ No se pudo correr hoy: {str(e)[:200]}")
        actualizados.append(r)
    if actualizados:
        store.upsert("reportes_guardados", actualizados)
    if not secciones:
        return "## Tus reportes\n\n_Ninguno de tus reportes tuvo resultados hoy._"
    return "## Tus reportes\n\n" + "\n\n".join(secciones)
