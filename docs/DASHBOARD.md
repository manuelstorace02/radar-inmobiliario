# Dashboard en Looker Studio

Un tablero que se actualiza solo con lo que el radar guarda cada día en Supabase. Gratis, con la cuenta de
Google de Manuel, y se puede abrir desde el celular.

## 1. Preparar la base (una vez, 2 minutos)

1. Abrí `sql/dashboard.sql` y copiá todo.
2. Supabase → **SQL Editor** → **New query** → pegá.
3. **Antes de correrlo**, reemplazá las DOS apariciones de `CAMBIAR_ESTA_CLAVE` por una contraseña larga
   (letras y números, sin comillas ni espacios). Guardala en las notas de Manuel: es la del dashboard.
4. **Run**. Tiene que decir "Success".

Esto crea 5 vistas (`dash_avisos_activos`, `dash_evolucion_barrio`, `dash_eventos`, `dash_misma_propiedad`,
`dash_corridas`) y un usuario `looker_lector` que **solo puede leer esas vistas**: no ve las tablas crudas ni
puede modificar nada.

## 2. Datos de conexión

Supabase → botón **Connect** (arriba) → pestaña **Direct / Connection string** → elegí **Session pooler**
(Looker necesita IPv4, y la conexión directa de Supabase es solo IPv6). De ahí sacás:

| Campo en Looker | Valor |
|---|---|
| Host / Nombre de host | el host del pooler, algo como `aws-0-sa-east-1.pooler.supabase.com` |
| Puerto | `5432` |
| Base de datos | `postgres` |
| Usuario | `looker_lector.pedzyaajcsqnjcpidhhl` (el usuario + punto + el id del proyecto) |
| Contraseña | la que pusiste en el paso 1 |

## 3. Conectar Looker Studio

1. Entrá a **lookerstudio.google.com** con la cuenta de Google de Manuel.
2. **Crear → Fuente de datos** → conector **PostgreSQL**.
3. Completá con la tabla del paso 2. Marcá **Habilitar SSL** (si pide certificado, probá primero sin él).
4. **Autenticar** → elegí la vista **`dash_avisos_activos`** → **Conectar** → **Crear informe**.
5. Repetí "Agregar datos" para las otras vistas: `dash_evolucion_barrio`, `dash_eventos`,
   `dash_misma_propiedad` y `dash_corridas`.

## 4. Las páginas del tablero

| Página | Vista | Gráficos |
|---|---|---|
| **Resumen** | `dash_eventos` | Tarjetas: "Publicado", "Desapareció", "Subió precio", "Bajó precio" de los últimos 7 días (filtro de fecha). Barras por `semana` y `evento`. |
| **Precios por barrio** | `dash_evolucion_barrio` | Serie temporal de `mediana_usd_m2` por `fecha`, desglosada por `barrio`. Filtros: `operacion` = venta, `tipo`, `departamento`. Tabla con barrio, avisos activos, mediana USD y USD/m². |
| **Mapa** | `dash_avisos_activos` | Mapa de burbujas con el campo `ubicacion` (en Looker: cambiar su tipo a *Geo → Latitud, longitud*), color por `usd_m2`. Filtros: operación, tipo, barrio, inmobiliaria, dormitorios. |
| **Inmobiliarias** | `dash_avisos_activos` + `dash_eventos` | Tabla: inmobiliaria, cantidad de avisos, mediana `precio_usd`, mediana `dias_publicado`. Barras: eventos por inmobiliaria. |
| **Misma propiedad** | `dash_misma_propiedad` | Tabla agrupada por `propiedad_id`: inmobiliaria, fuente, precio, mínimo y máximo, link. |
| **Salud** | `dash_corridas` | Tabla por fecha y fuente: estado, revisadas, OK, errores y notas. |

Tip: en la configuración de la fuente de datos, poné **Actualización de datos = cada 12 horas** (o 1 hora).

## 5. Compartir

**Compartir** (arriba a la derecha) → agregá el mail de Manuel o generá un link "cualquiera con el enlace puede
ver" si querés abrirlo sin loguearte. Desde el celular se abre en el navegador.
