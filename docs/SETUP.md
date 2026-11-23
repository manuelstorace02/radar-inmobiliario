# Instalación paso a paso

Todo en la nube: GitHub (código), Supabase (base de datos) y Codex en la cuenta de ChatGPT de Manuel
(agente diario). No hace falta ninguna compu prendida.

## 1. Subir este repo a GitHub

1. Entrá a `github.com/manuelstorace02/radar-inmobiliario`.
2. **Add file → Upload files**.
3. Arrastrá **todo el contenido** de la carpeta `radar-inmobiliario` (no la carpeta en sí: lo de adentro:
   `AGENTS.md`, `radar/`, `data/`, `docs/`, `sql/`, etc.).
4. Abajo: **Commit changes** directo a `main`.

## 2. Crear la base en Supabase (gratis)

1. Entrá a **supabase.com** y creá una cuenta (podés entrar con el GitHub de Manuel).
2. **New project**: nombre `radar-inmobiliario`, región **South America (São Paulo)**, generá una contraseña y
   guardala en un lugar seguro.
3. Cuando termine de crearse: menú izquierdo → **SQL Editor** → **New query** → pegá todo el contenido de
   `sql/schema.sql` → **Run**. Tiene que decir "Success".
4. Menú izquierdo → **Storage** → **New bucket** → nombre `scrapeos`, **privado** (Public desactivado) → Create.
5. Menú izquierdo → **Project Settings → API** (o **Data API**). Copiá:
   - **Project URL** (algo como `https://abcdxyz.supabase.co`).
   - La clave **service_role** (secreta; empieza con `eyJ...` o `sb_secret_...`). **No la compartas ni la
     pegues en el repo.**

6. Después, de la misma forma, corré `sql/dashboard.sql` (cambiando la contraseña adentro, ver
   `docs/DASHBOARD.md`), `sql/reportes.sql` (reportes personalizados) y `sql/lectura.sql` (lectura para el
   conector de ChatGPT).

## 3. Poner la URL en el repo

En GitHub, editá `config.json` (lápiz ✏️) y poné la Project URL:

```json
{ "supabase_url": "https://abcdxyz.supabase.co" }
```

Commit a `main`.

## 4. Configurar el entorno de Codex

En chatgpt.com/codex → entorno `radar-inmobiliario` → **Editar entorno**:

1. **Script de instalación** (✏️): `python3 -m venv /tmp/radar-venv && /tmp/radar-venv/bin/pip install -q -r requirements.txt`
2. **Acceso a internet**: activado (ya lo está).
3. **La clave de Supabase** — una de estas dos, según lo que ofrezca la pantalla:
   - Si hay **"Variables de entorno" / "Secretos"**: agregá `SUPABASE_SERVICE_KEY` = la clave service_role.
   - Si solo hay **"Secretos HTTP"**: creá uno para el dominio de tu proyecto (`abcdxyz.supabase.co`) que agregue
     el encabezado `apikey: <clave>` (con una clave legacy `eyJ...`, también `Authorization: Bearer <clave>`).
     El código funciona sin ver la clave.
4. **Guardar**.

## 5. La corrida automática (GitHub Actions)

El scraping corre en la nube de GitHub todos los días a las 06:00 de Uruguay
(`.github/workflows/radar-diario.yml`). Las tareas programadas de ChatGPT no pueden ejecutar código, por
eso el trabajo pesado va acá.

1. En el repo: **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `SUPABASE_SERVICE_KEY`
   - Secret: la clave secreta de Supabase (`sb_secret_...`).
2. Probarlo a mano: pestaña **Actions → Radar diario → Run workflow** (podés poner `max_urls` = 200 para
   una prueba corta). Tarda ~5 min la prueba corta y ~55 min la completa. Verde = salió bien; el resumen
   del reporte queda en la página de esa corrida.

Plan gratis de GitHub para repos privados: 2.000 minutos por mes; la corrida diaria usa ~55.

## 6. La tarea programada de ChatGPT (el mensaje para Manuel)

Necesita el conector **Supabase Radar** (solo lectura) conectado en ChatGPT. En **Tareas programadas →
Nueva** (o editar la que ya existe):

- **Repetir:** Cada día — **Hora:** 7:30 (después de que termine la corrida de las 6:00).
- **Qué debe hacer:**

```
Usá el conector Supabase Radar (solo lectura). Buscá en la tabla reportes_diarios la fila de hoy
(fecha = fecha de hoy en Uruguay). Si no existe, usá la más reciente y avisá arriba de todo que la corrida
de hoy no llegó (hay que mirar la pestaña Actions del repo manuelstorace02/radar-inmobiliario).

1) Escribí "Lo más importante hoy": 3 a 6 bullets interpretando reporte_md y personalizados_md, con números
   concretos (qué inmobiliarias se movieron, zonas donde varias subieron o bajaron precios, propiedades que
   desaparecieron de todos lados, misma propiedad con precios muy distintos según dónde se publica).
2) Tomá la columna reporte_html. Reemplazá TODO lo que está entre <!--RESUMEN--> y <!--/RESUMEN--> por tus
   bullets en HTML (<li>...</li>, podés usar <b>). No cambies nada más del HTML.
3) Entregámelo como archivo descargable llamado radar-AAAA-MM-DD.html (creálo con la herramienta de Python /
   archivos). Si no podés crear archivos, mostralo en Canvas.
4) En el mensaje del chat poné solo: los bullets, una línea con las notas técnicas importantes de salud_md, y
   el archivo.

Reglas: usá solo números que estén en esa fila o que consultes en la base; nunca digas "vendido" ni
"alquilado", decí "desapareció"; si no hubo movimientos relevantes, decilo en una línea.
```

## 7. Ver los resultados

- **Todos los días**: el resultado de la tarea en ChatGPT (con notificación) es el resumen.
- **Los datos crudos**: Supabase → **Table Editor** (`avisos`, `eventos`, `estadisticas_diarias`...). Se puede
  filtrar y exportar a CSV como una planilla.
- **Sumar una inmobiliaria**: pedíselo a Codex en el chat ("agregá la inmobiliaria X, web Y").
