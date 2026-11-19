# Radar Inmobiliario Uruguay

Todos los días revisa los avisos publicados por inmobiliarias y portales de Uruguay, guarda todo, detecta
qué cambió (publicaciones nuevas, avisos que desaparecen, cambios de precio, la misma propiedad publicada en
varios lugares con precios distintos) y manda un resumen con las tendencias por barrio e inmobiliaria.

- **Cómo instalarlo:** [docs/SETUP.md](docs/SETUP.md)
- **Qué hace el agente cada día:** [AGENTS.md](AGENTS.md)
- **Sumar una inmobiliaria o portal:** [docs/NUEVA_FUENTE.md](docs/NUEVA_FUENTE.md)
- **Qué falta construir:** [ROADMAP.md](ROADMAP.md)

## Cómo está armado

```
Codex (tarea programada, 7:00)            Supabase
  └─ python -m radar diaria   ───────────►  avisos        estado actual de cada aviso
       1. descubrir (sitemaps/listados)     eventos       alta · baja · precio · reaparece
       2. revisar avisos (educado)          corridas      salud de cada fuente por día
       3. comparar y registrar eventos      estadisticas_diarias  mediana USD y USD/m² por barrio
       4. misma propiedad entre fuentes     Storage       foto diaria completa (.jsonl.gz) y reportes
       5. estadísticas + reporte
  └─ mensaje para Manuel
```

## Probar en una compu (opcional)

```bash
pip install -r requirements.txt
RADAR_DB=local python -m radar seed
RADAR_DB=local python -m radar probar-fuente gallito -n 3
RADAR_DB=local python -m radar diaria --fuente gallito --max-urls 30
```

`RADAR_DB=local` usa una base SQLite en `data/local.db` en vez de Supabase.
