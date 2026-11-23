# Instalación paso a paso

Todo en la nube: GitHub (código), Supabase (base de datos) y Codex en la cuenta de ChatGPT del usuario
(agente diario). No hace falta ninguna compu prendida.

## 1. Subir este repo a GitHub

1. Entrá a `github.com/manuelstorace02/radar-inmobiliario`.
2. **Add file → Upload files**.
3. Arrastrá **todo el contenido** de la carpeta `radar-inmobiliario` (no la carpeta en sí: lo de adentro:
   `AGENTS.md`, `radar/`, `data/`, `docs/`, `sql/`, etc.).
4. Abajo: **Commit changes** directo a `main`.

## 2. Crear la base en Supabase (gratis)

1. Entrá a **supabase.com** y creá una cuenta (podés entrar con el GitHub del usuario).
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

## 6. Panel web con contraseña (GitHub Pages)

Cada corrida arma un sitio con el panel (evolución, gráficos) y todos los reportes diarios, **encriptado**:
sin la contraseña no se puede leer nada aunque el sitio sea público.

1. Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**: `PANEL_CLAVE` = la
   contraseña del panel (si falta, la corrida NO publica nada).
3. Dirección: **https://manuelstorace02.github.io/radar-inmobiliario/** (la contraseña se pide una vez y queda
   recordada en ese navegador).

## 7. La tarea programada de ChatGPT (el mensaje del día)

Necesita el conector **Supabase Radar** (solo lectura) y, para poder pedir cambios desde ese mismo chat, el
conector **Radar escritura** (paso 9). **Repetir:** cada día, **7:30**. **Qué debe hacer:**

```
Usá el conector Supabase Radar (solo lectura). Leé primero la tabla parametros (filas con clave que empieza
con pref_): son preferencias del usuario sobre el mensaje (barrios foco, largo, qué no mostrar); respetalas.
Buscá en la tabla reportes_diarios la fila de hoy
(fecha = fecha de hoy en Uruguay). Si no existe, usá la más reciente y avisá arriba de todo que la corrida
de hoy no llegó (hay que mirar la pestaña Actions del repo manuelstorace02/radar-inmobiliario).

Escribí "Lo más importante hoy": 3 a 6 bullets interpretando reporte_md y personalizados_md, con números
concretos (qué inmobiliarias se movieron, zonas donde varias subieron o bajaron precios, propiedades que
desaparecieron de todos lados, misma propiedad con precios muy distintos según dónde se publica).
Después una línea con lo importante de salud_md, y al final este link:
Reporte completo y panel: https://manuelstorace02.github.io/radar-inmobiliario/reportes/AAAA-MM-DD.html
(con la fecha de la fila que usaste).

Reglas: usá solo números que estén en esa fila o que consultes en la base; nunca digas "vendido" ni
"alquilado", decí "desapareció"; si no hubo movimientos relevantes, decilo en una línea. Si una misma
propiedad aparece con precios muy distintos, aclaralo como "a revisar": puede ser un error del agrupado.

Si el usuario te responde en este chat pidiendo cambios (una sección nueva en el panel, un reporte diario,
una alerta, preferencias), usá el conector Radar escritura siguiendo la sección 2.2 de AGENTS.md del repo
manuelstorace02/radar-inmobiliario: probá la consulta, mostrale qué va a ver y guardá solo con su OK.
```

## 8. Ver los resultados

- **Todos los días**: el resultado de la tarea en ChatGPT (con notificación) es el resumen.
- **Los datos crudos**: Supabase → **Table Editor** (`avisos`, `eventos`, `estadisticas_diarias`...). Se puede
  filtrar y exportar a CSV como una planilla.
- **Sumar una inmobiliaria**: pedíselo a Codex en el chat ("agregá la inmobiliaria X, web Y").

## 9. Conector de escritura (pedir cambios del panel desde el chat)

Con esto el usuario le dice a ChatGPT "agregame al panel la evolución del m² en Malvín Norte vs Malvín" y
queda hecho, sin Codex. Es una función chiquita en Supabase que solo puede guardar secciones del panel,
reportes, preferencias y pedidos (no puede borrar ni cambiar datos del scraping).

1. **Tablas**: Supabase → **SQL Editor** → pegar `sql/panel.sql` → **Run**.
2. **La función**: Supabase → **Edge Functions** → **Deploy a new function** → **Via Editor**. Nombre:
   `radar-escritura`. Borrar el código de ejemplo, pegar todo `supabase/functions/radar-escritura/index.ts`
   → **Deploy function**.
3. En la función → **Details** → apagar **Verify JWT with legacy secret** (la protege su propia clave) → **Save**.
4. **Edge Functions → Secrets → Add new secret**: `RADAR_MCP_CLAVE` = una contraseña larga (solo letras y
   números). Opcional, para "publicar ya": `GITHUB_TOKEN` = un token de GitHub *fine-grained* sobre el repo
   `radar-inmobiliario` con permiso **Actions: Read and write** (github.com → Settings → Developer settings →
   Fine-grained tokens).
5. **ChatGPT** → Configuración → **Aplicaciones y conectores** → **Crear** (modo desarrollador). Nombre
   `Radar escritura`; URL `https://pedzyaajcsqnjcpidhhl.supabase.co/functions/v1/radar-escritura?k=LA_CLAVE`;
   autenticación: **Sin autenticación**. Activarlo en la tarea de la mañana y en los chats donde se use.
6. Probar en un chat: *"Con Radar escritura, listá mis cosas"*.

