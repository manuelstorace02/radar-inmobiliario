"""Cliente HTTP "educado": respeta robots.txt, una request a la vez por dominio con pausa,
reintentos con backoff, y se identifica con un User-Agent honesto."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = ("Mozilla/5.0 (compatible; RadarInmobiliarioUY/1.0; "
              "monitoreo de precios publicados; respeta robots.txt)")


@dataclass
class Respuesta:
    url: str            # URL pedida
    url_final: str      # URL luego de redirecciones
    status: int         # 0 = error de red
    texto: str
    error: str | None = None
    contenido: bytes = b""


class Cliente:
    def __init__(self, delay_seg: float = 1.5, timeout: float = 30, respetar_robots: bool = True):
        self.delay = delay_seg
        self.respetar_robots = respetar_robots
        self.http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-UY,es;q=0.9",
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            timeout=timeout, follow_redirects=True, http2=False)
        self._locks: dict[str, asyncio.Lock] = {}
        self._ultimo: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def cerrar(self):
        await self.http.aclose()

    async def _robots_ok(self, url: str) -> bool:
        if not self.respetar_robots:
            return True
        p = urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                r = await self.http.get(base + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[base] = rp
            except httpx.HTTPError:
                self._robots[base] = None
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    async def sitemaps_de_robots(self, base: str) -> list[str]:
        try:
            r = await self.http.get(base.rstrip("/") + "/robots.txt")
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        return [l.split(":", 1)[1].strip() for l in r.text.splitlines() if l.lower().startswith("sitemap:")]

    async def get(self, url: str, intentos: int = 3) -> Respuesta:
        if not await self._robots_ok(url):
            return Respuesta(url, url, 0, "", error="bloqueado_por_robots")
        dom = urlsplit(url).netloc
        lock = self._locks.setdefault(dom, asyncio.Lock())
        ultimo_error = None
        for i in range(intentos):
            async with lock:
                espera = self.delay - (time.monotonic() - self._ultimo.get(dom, 0))
                if espera > 0:
                    await asyncio.sleep(espera + random.uniform(0, 0.3))
                try:
                    r = await self.http.get(url)
                    self._ultimo[dom] = time.monotonic()
                except httpx.HTTPError as e:
                    self._ultimo[dom] = time.monotonic()
                    ultimo_error = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(2 ** i)
                    continue
            if r.status_code in (429, 500, 502, 503, 504) and i < intentos - 1:
                await asyncio.sleep(5 * (2 ** i))
                continue
            return Respuesta(url, str(r.url), r.status_code, r.text, contenido=r.content)
        return Respuesta(url, url, 0, "", error=ultimo_error)
