"""Normalización: texto, precios, operación, tipo de propiedad y barrios."""
from __future__ import annotations

import re
import unicodedata

DEPARTAMENTOS = ["Montevideo", "Canelones", "Maldonado", "Rocha", "Colonia", "San José", "Florida",
                 "Lavalleja", "Treinta y Tres", "Cerro Largo", "Rivera", "Tacuarembó", "Durazno",
                 "Flores", "Soriano", "Río Negro", "Paysandú", "Salto", "Artigas"]


def simple(s: str | None) -> str:
    """minúsculas, sin tildes, sin signos, espacios simples."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------- números y precios
def numero(s: str | None) -> float | None:
    """'250.000' -> 250000 ; '1.250,50' -> 1250.5 ; '85,5' -> 85.5 ; '1,200' -> 1200"""
    if s is None:
        return None
    s = str(s).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        ent, _, dec = s.rpartition(",")
        s = s.replace(",", "") if len(dec) == 3 else ent.replace(",", "") + "." + dec
    elif s.count(".") >= 1:
        ent, _, dec = s.rpartition(".")
        if len(dec) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


_MONEDA = r"(U\$S|US\$|U\$D|USD|U\.S\.\$|\$U|UYU|\$)"
PRECIO_RE = re.compile(_MONEDA + r"\s*([\d][\d\.\,\s]*\d|\d)", re.I)


def moneda_norm(m: str | None) -> str | None:
    if not m:
        return None
    m = m.upper().replace(" ", "")
    if m in ("U$S", "US$", "U$D", "USD", "U.S.$", "DOLARES", "DÓLARES"):
        return "USD"
    if m in ("$U", "UYU", "$", "PESOS", "UY$"):
        return "UYU"
    return None


def parse_precio(texto: str | None) -> tuple[float | None, str | None]:
    if not texto:
        return None, None
    m = PRECIO_RE.search(texto)
    if not m:
        return None, None
    return numero(m.group(2)), moneda_norm(m.group(1))


def a_usd(precio: float | None, moneda: str | None, tc: float) -> float | None:
    if precio is None:
        return None
    if moneda == "UYU":
        return round(precio / tc, 2) if tc else None
    return precio


# ----------------------------------------------------------------- operación y tipo
def operacion(*textos: str | None) -> str | None:
    t = simple(" ".join(x for x in textos if x))
    if re.search(r"\b(temporada|temporario|alquiler temporal|por dia|por noche|vacacion)", t):
        return "alquiler_temporal"
    if re.search(r"\b(alquiler|alquila|alquilar|arriendo|renta|rent)\b", t):
        return "alquiler"
    if re.search(r"\b(venta|vende|vender|comprar|sale)\b", t):
        return "venta"
    return None


_TIPOS = [
    ("apartamento", r"\b(apartamento|apartamentos|apto|aptos|monoambiente|penthouse|duplex|loft)\b"),
    ("casa", r"\b(casa|casas|chalet|vivienda)\b"),
    ("terreno", r"\b(terreno|terrenos|solar|solares|lote|lotes)\b"),
    ("local", r"\b(local|locales)( comercial)?\b"),
    ("oficina", r"\b(oficina|oficinas)\b"),
    ("chacra_campo", r"\b(chacra|chacras|campo|campos|estancia)\b"),
    ("garaje", r"\b(garaje|garajes|cochera|cocheras|estacionamiento)\b"),
    ("deposito", r"\b(deposito|depositos|galpon|galpones|nave industrial)\b"),
    ("edificio", r"\b(edificio|edificios)\b"),
]


def tipo_propiedad(*textos: str | None) -> str | None:
    t = simple(" ".join(x for x in textos if x))
    for nombre, rx in _TIPOS:
        if re.search(rx, t):
            return nombre
    return None


# ----------------------------------------------------------------- barrios
class Barrios:
    """Busca barrios en texto usando la tabla barrios_alias.

    Prioridad: coincidencia más larga; si el texto menciona un departamento, se prefieren
    alias de ese departamento (desambigua 'La Paloma' Montevideo vs. Rocha)."""

    def __init__(self, filas: list[dict]):
        self.por_alias: dict[str, list[dict]] = {}
        for f in filas:
            self.por_alias.setdefault(simple(f["alias"]), []).append(f)
        aliases = sorted(self.por_alias, key=len, reverse=True)
        self.rx = re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases if a) + r")\b") if aliases else None
        self.deptos = {simple(d): d for d in DEPARTAMENTOS}

    def departamento_en(self, texto: str) -> str | None:
        t = simple(texto)
        for k, v in self.deptos.items():
            if re.search(rf"\b{k}\b", t):
                return v
        return None

    def buscar(self, *textos: str | None, depto_hint: str | None = None,
               estructurados: int = 2) -> tuple[str | None, str | None]:
        """Devuelve (barrio, departamento). Recorre los textos en orden de confianza.

        Los primeros `estructurados` textos son campos de ubicación (ubicación, breadcrumb): ahí se aceptan
        nombres de departamento. En el resto (título, URL, descripción) NO, porque casi todos los
        departamentos son también calles de Montevideo ("Av. Rivera", "Bv. Artigas", "Canelones 1234")."""
        if not self.rx:
            return None, depto_hint
        depto_visto = depto_hint
        for i, texto in enumerate(textos):
            if not texto:
                continue
            t = simple(texto)
            estructurado = i < estructurados
            if estructurado and not depto_visto:
                depto_visto = self.departamento_en(texto)
            depto = depto_visto
            candidatos = []
            for m in self.rx.finditer(t):
                for f in self.por_alias[m.group(1)]:
                    if f["tipo"] == "departamento" and not estructurado:
                        continue
                    candidatos.append((len(m.group(1)), f))
            if not candidatos:
                continue
            # primero barrios/zonas/localidades (más largos primero); el departamento solo si no hay otra cosa
            candidatos.sort(key=lambda c: (c[1]["tipo"] == "departamento", -c[0]))
            if depto:
                del_depto = [c for c in candidatos if c[1]["departamento"] == depto]
                if del_depto:
                    candidatos = del_depto
            f = candidatos[0][1]
            if f["tipo"] == "departamento":
                depto_visto = depto_visto or f["departamento"]
                continue  # seguir buscando el barrio en los otros textos
            return f["barrio"], f["departamento"]
        return None, depto_visto
