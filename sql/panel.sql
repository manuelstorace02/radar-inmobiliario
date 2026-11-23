-- Radar Inmobiliario — secciones del panel que arma el usuario desde el chat.
-- Se corre UNA vez en Supabase > SQL Editor (después de reportes.sql). Es idempotente.
--
-- Cada fila es un bloque del panel web: un título, una consulta SELECT sobre las vistas dash_* y cómo
-- dibujarla. El panel se rearma cada madrugada (o al pedir "publicar ahora") y muestra las activas en orden.
--
--   grafico = 'numero'  -> la primera fila, cada columna una tarjeta grande (ej. total, mediana)
--             'barras'  -> columnas (etiqueta, valor): barras horizontales ordenadas
--             'lineas'  -> columnas (fecha, valor) o (fecha, serie, valor): evolución en el tiempo
--             'tabla'   -> cualquier consulta; si trae la columna url, el primer dato es un link

create table if not exists panel_secciones (
  id            text primary key,              -- slug: 'm2-malvin-norte-vs-sur'
  titulo        text not null,
  descripcion   text,                          -- qué muestra, en palabras del usuario
  sql           text not null,                 -- SELECT sobre las vistas dash_*
  grafico       text not null default 'tabla' check (grafico in ('numero','barras','lineas','tabla')),
  orden         int default 100,               -- menor = más arriba
  activo        boolean default true,
  pedido_por    text default 'usuario',
  creado_en     timestamptz default now(),
  actualizado_en timestamptz default now(),
  ultimas_filas int,
  ultimo_error  text
);
alter table panel_secciones enable row level security;
grant all on panel_secciones to service_role;
drop policy if exists lectura on panel_secciones;
create policy lectura on panel_secciones for select using (true);
revoke all on panel_secciones from anon, authenticated;

-- Pedidos sueltos del usuario que necesitan código (una fuente nueva, un cambio de diseño...): quedan anotados
-- para el próximo que trabaje en el repo (Codex o quien sea). El agente del chat no toca código.
create table if not exists pedidos (
  id          bigint generated always as identity primary key,
  creado_en   timestamptz default now(),
  texto       text not null,
  estado      text default 'pendiente' check (estado in ('pendiente','hecho','descartado')),
  respuesta   text
);
alter table pedidos enable row level security;
grant all on pedidos to service_role;
drop policy if exists lectura on pedidos;
create policy lectura on pedidos for select using (true);
revoke all on pedidos from anon, authenticated;
