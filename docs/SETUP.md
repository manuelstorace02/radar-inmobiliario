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

## 3. Poner la URL en el repo

En GitHub, editá `config.json` (lápiz ✏️) y poné la Project URL:

```json
{ "supabase_url": "https://abcdxyz.supabase.co" }
```

Commit a `main`.

## 4. Configurar el entorno de Codex

En chatgpt.com/codex → entorno `radar-inmobiliario` → **Editar entorno**:

1. **Script de instalación** (✏️): `pip install -r requirements.txt`
2. **Acceso a internet**: activado (ya lo está).
3. **La clave de Supabase** — una de estas dos, según lo que ofrezca la pantalla:
   - Si hay **"Variables de entorno" / "Secretos"**: agregá `SUPABASE_SERVICE_KEY` = la clave service_role.
   - Si solo hay **"Secretos HTTP"**: creá uno para el dominio de tu proyecto (`abcdxyz.supabase.co`) que agregue
     los encabezados `apikey: <clave>` y `Authorization: Bearer <clave>`. El código funciona sin ver la clave.
4. **Guardar**.

## 5. Primera corrida (manual, supervisada)

En Codex, con el entorno `radar-inmobiliario`, mandá:

> Leé AGENTS.md. Hacé la puesta en marcha: corré `python -m radar seed`, después
> `python -m radar probar-fuente gallito -n 5` y mostrame el resultado. Si todo se ve bien, corré la rutina
> diaria completa (sección 1 de AGENTS.md) y dame el mensaje final.

Revisá que los 5 avisos de ejemplo tengan precio, barrio y operación correctos.

## 6. La tarea programada diaria

En Codex → **Tareas programadas** → **Nueva**:

- **Título:** `Radar Inmobiliario diario`
- **Qué debe hacer:**
  > Usá el entorno radar-inmobiliario. Leé AGENTS.md y ejecutá la "Rutina diaria" (sección 1) completa.
  > Tu respuesta final es el mensaje para Manuel con el formato de la sección 3.
- **Repetir:** Cada día — **Hora:** 7:00 — **Finalizar repetición:** Nunca.
- **Crear**.

Si la tarea programada no deja elegir el entorno y no puede ver el repo, avisá: la alternativa es disparar la
corrida con una GitHub Action (ver ROADMAP, riesgos).

## 7. Ver los resultados

- **Todos los días**: el resultado de la tarea en ChatGPT (con notificación) es el resumen, con el link al
  reporte visual completo (válido 7 días).
- **Los datos crudos**: Supabase → **Table Editor** (`avisos`, `eventos`, `estadisticas_diarias`...). Se puede
  filtrar y exportar a CSV como una planilla.
- **Sumar una inmobiliaria**: pedíselo a Codex en el chat ("agregá la inmobiliaria X, web Y").
