"""Acceso a la base.

Dos implementaciones con la misma interfaz mínima:
  * SupabaseStore: la de producción. Habla con Supabase por HTTPS (PostgREST + Storage),
    así funciona desde el entorno en la nube de Codex sin drivers de Postgres.
  * LocalStore: SQLite en data/local.db, para probar en una compu sin Supabase.

Se elige con la variable RADAR_DB: 'supabase' (por defecto si existe SUPABASE_URL) o 'local'.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parent.parent

# Columnas por tabla (orden y nombres comunes a ambas implementaciones).
TABLAS: dict[str, list[str]] = {
    "inmobiliarias": ["id", "nombre", "web", "alias", "notas"],
    "fuentes": ["id", "nombre", "tipo", "inmobiliaria_id", "url_base", "modo", "patron_aviso",
                "listado_urls", "sitemap_completo", "max_urls", "delay_seg", "activa", "estado", "notas"],
    "barrios_alias": ["alias", "barrio", "departamento", "tipo"],
    "barrios_sin_mapear": ["texto", "departamento", "veces", "ejemplo_url", "ultima_vez"],
    "parametros": ["clave", "valor"],
    "avisos": ["id", "fuente_id", "url", "inmobiliaria_id", "anunciante", "operacion", "tipo", "titulo",
               "departamento", "barrio", "barrio_raw", "direccion", "precio", "moneda", "precio_usd",
               "gastos_comunes", "m2_totales", "m2_edificados", "dormitorios", "banos", "garaje",
               "lat", "lon", "imagenes", "estado", "primera_vez", "ultima_vez", "revisado_en",
               "fallos_consecutivos", "propiedad_id"],
    "eventos": ["fecha", "aviso_id", "fuente_id", "tipo", "campo", "valor_anterior", "valor_nuevo",
                "precio_usd_anterior", "precio_usd_nuevo"],
    "corridas": ["fecha", "fuente_id", "inicio", "fin", "urls_descubiertas", "urls_revisadas", "avisos_ok",
                 "nuevos", "bajas", "cambios_precio", "errores", "estado", "notas"],
    "estadisticas_diarias": ["fecha", "departamento", "barrio", "operacion", "tipo", "avisos_activos",
                             "mediana_usd", "mediana_usd_m2", "altas", "bajas", "subas_precio", "bajas_precio"],
}
CLAVES: dict[str, list[str]] = {
    "inmobiliarias": ["id"], "fuentes": ["id"], "barrios_alias": ["alias", "departamento"],
    "barrios_sin_mapear": ["texto", "departamento"], "parametros": ["clave"], "avisos": ["id"],
    "estadisticas_diarias": ["fecha", "departamento", "barrio", "operacion", "tipo"],
}
JSON_COLS = {"avisos": {"imagenes"}, "inmobiliarias": {"alias"}}


def _norm_row(tabla: str, row: dict) -> dict:
    """Deja la fila con exactamente las columnas de la tabla (PostgREST lo exige en upserts masivos)."""
    return {c: row.get(c) for c in TABLAS[tabla]}


class Store:
    def select(self, tabla: str, eq: dict | None = None, columnas: str = "*",
               gte: dict | None = None, orden: str | None = None) -> list[dict]: ...
    def upsert(self, tabla: str, filas: Iterable[dict]) -> None: ...
    def insert(self, tabla: str, filas: Iterable[dict]) -> None: ...
    def subir_archivo(self, ruta: str, contenido: bytes, content_type: str = "application/gzip") -> None: ...
    def link_firmado(self, ruta: str, dias: int = 7) -> str | None:
        return None

    # helpers comunes
    def param(self, clave: str, default: str | None = None) -> str | None:
        r = self.select("parametros", eq={"clave": clave})
        return r[0]["valor"] if r else default

    def set_param(self, clave: str, valor: str) -> None:
        self.upsert("parametros", [{"clave": clave, "valor": valor}])


# --------------------------------------------------------------------------- Supabase
class SupabaseStore(Store):
    LOTE = 500

    def __init__(self, url: str, key: str | None):
        self.url = url.rstrip("/")
        h = {"Content-Type": "application/json"}
        if key:  # si el entorno inyecta la clave por proxy (secreto HTTP), puede venir vacía
            h["apikey"] = key
            if key.startswith("eyJ"):  # claves "legacy" (JWT) van también como Bearer; las sb_secret_ solo en apikey
                h["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(headers=h, timeout=60)

    def _rest(self, tabla: str) -> str:
        return f"{self.url}/rest/v1/{tabla}"

    def select(self, tabla, eq=None, columnas="*", gte=None, orden=None):
        params: dict[str, str] = {"select": columnas}
        for k, v in (eq or {}).items():
            params[k] = f"eq.{_pg(v)}"
        for k, v in (gte or {}).items():
            params[k] = f"gte.{_pg(v)}"
        if orden:
            params["order"] = orden
        out, desde, paso = [], 0, 1000
        while True:
            r = self.http.get(self._rest(tabla), params=params,
                              headers={"Range-Unit": "items", "Range": f"{desde}-{desde + paso - 1}"})
            _check(r)
            filas = r.json()
            out.extend(filas)
            if len(filas) < paso:
                return out
            desde += paso

    def upsert(self, tabla, filas):
        filas = [_norm_row(tabla, f) for f in filas]
        on_conflict = ",".join(CLAVES[tabla])
        for i in range(0, len(filas), self.LOTE):
            r = self.http.post(self._rest(tabla), params={"on_conflict": on_conflict},
                               content=json.dumps(filas[i:i + self.LOTE], default=str),
                               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            _check(r)

    def insert(self, tabla, filas):
        filas = [_norm_row(tabla, f) for f in filas]
        for i in range(0, len(filas), self.LOTE):
            r = self.http.post(self._rest(tabla), content=json.dumps(filas[i:i + self.LOTE], default=str),
                               headers={"Prefer": "return=minimal"})
            _check(r)

    def subir_archivo(self, ruta, contenido, content_type="application/gzip"):
        bucket = os.environ.get("SUPABASE_BUCKET", "scrapeos")
        r = self.http.post(f"{self.url}/storage/v1/object/{bucket}/{ruta}", content=contenido,
                           headers={"Content-Type": content_type, "x-upsert": "true"})
        _check(r)


    def link_firmado(self, ruta, dias=7):
        bucket = os.environ.get("SUPABASE_BUCKET", "scrapeos")
        r = self.http.post(f"{self.url}/storage/v1/object/sign/{bucket}/{ruta}",
                           content=json.dumps({"expiresIn": dias * 86400}))
        _check(r)
        rel = r.json().get("signedURL") or r.json().get("signedUrl")
        return f"{self.url}/storage/v1{rel}" if rel else None


def _pg(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _check(r: httpx.Response) -> None:
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase {r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:500]}")


# --------------------------------------------------------------------------- SQLite local
class LocalStore(Store):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        for t, cols in TABLAS.items():
            pk = f", primary key ({', '.join(CLAVES[t])})" if t in CLAVES else ""
            self.db.execute(f"create table if not exists {t} ({', '.join(cols)}{pk})")
        self.db.commit()
        self.archivos = ROOT / "data" / "storage"

    def select(self, tabla, eq=None, columnas="*", gte=None, orden=None):
        where, vals = [], []
        for k, v in (eq or {}).items():
            where.append(f"{k} = ?"); vals.append(_sq(v))
        for k, v in (gte or {}).items():
            where.append(f"{k} >= ?"); vals.append(_sq(v))
        sql = f"select {columnas} from {tabla}" + (f" where {' and '.join(where)}" if where else "")
        if orden:
            col, _, d = orden.partition(".")
            sql += f" order by {col} {'desc' if d == 'desc' else 'asc'}"
        out = []
        for row in self.db.execute(sql, vals):
            d = dict(row)
            for c in JSON_COLS.get(tabla, set()) & d.keys():
                if isinstance(d[c], str):
                    d[c] = json.loads(d[c])
            for c in ("activa", "garaje", "sitemap_completo"):
                if c in d and d[c] is not None:
                    d[c] = bool(d[c])
            out.append(d)
        return out

    def upsert(self, tabla, filas):
        cols = TABLAS[tabla]
        sql = (f"insert into {tabla} ({', '.join(cols)}) values ({', '.join('?' * len(cols))}) "
               f"on conflict ({', '.join(CLAVES[tabla])}) do update set "
               + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in CLAVES[tabla]))
        self.db.executemany(sql, [[_sq(f.get(c)) for c in cols] for f in filas])
        self.db.commit()

    def insert(self, tabla, filas):
        cols = TABLAS[tabla]
        self.db.executemany(f"insert into {tabla} ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
                            [[_sq(f.get(c)) for c in cols] for f in filas])
        self.db.commit()

    def link_firmado(self, ruta, dias=7):
        return (self.archivos / ruta).as_uri()

    def subir_archivo(self, ruta, contenido, content_type="application/gzip"):
        p = self.archivos / ruta
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contenido)


def _sq(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if v is not None and not isinstance(v, (int, float, str, bytes)):
        return str(v)
    return v


def _config() -> dict:
    """config.json en la raíz del repo (no secreto: solo la URL del proyecto de Supabase)."""
    p = ROOT / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}


def abrir() -> Store:
    hay_supabase = os.environ.get("SUPABASE_URL") or _config().get("supabase_url")
    modo = os.environ.get("RADAR_DB") or ("supabase" if hay_supabase else "local")
    if modo == "supabase":
        url = os.environ.get("SUPABASE_URL") or _config().get("supabase_url")
        if not url:
            raise SystemExit("Falta la variable SUPABASE_URL (ver docs/SETUP.md).")
        return SupabaseStore(url, os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY"))
    return LocalStore(ROOT / "data" / "local.db")
