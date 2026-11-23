-- Radar Inmobiliario — reportes y búsquedas personalizadas del usuario.
-- Se corre en Supabase > SQL Editor DESPUÉS de schema.sql y dashboard.sql. Es idempotente.
--
-- El usuario le pide al agente "quiero que todos los días me muestres X". El agente escribe la consulta SQL,
-- la prueba, se la muestra al usuario y, si le sirve, la guarda acá. La rutina diaria corre todas las
-- activas y las agrega al mensaje de la mañana.

create table if not exists reportes_guardados (
  id                  text primary key,            -- slug: 'aptos-2d-pocitos-bajan'
  nombre              text not null,               -- título que ve el usuario
  descripcion         text,                        -- qué busca, en palabras del usuario
  sql                 text not null,               -- un SELECT sobre las vistas dash_* (ver AGENTS.md)
  frecuencia          text not null default 'diaria' check (frecuencia in ('diaria','semanal')),
  solo_si_hay_resultados boolean default true,     -- si da 0 filas, no se muestra
  activo              boolean default true,
  pedido_por          text default 'usuario',
  creado_en           timestamptz default now(),
  ultima_ejecucion    timestamptz,
  ultimas_filas       int,
  ultimo_error        text
);
alter table reportes_guardados enable row level security;
grant all on reportes_guardados to service_role;

-- Ejecuta UNA consulta SELECT y devuelve las filas como JSON (máx. 500).
-- Seguridad:
--   * El código la llama por GET, y la API de Supabase corre los GET en transacciones de SOLO LECTURA:
--     cualquier intento de modificar datos falla.
--   * La consulta va envuelta como subconsulta: no se pueden encadenar varias sentencias con ';'.
--   * Solo la puede ejecutar la service_role (la clave del agente).
create or replace function radar_consulta(q text)
returns jsonb
language plpgsql
stable
set statement_timeout = '30s'
as $$
declare
  r jsonb;
begin
  if q ~* '^\s*(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\y' then
    raise exception 'radar_consulta solo acepta SELECT/WITH';
  end if;
  execute format('select coalesce(jsonb_agg(t), ''[]''::jsonb) from (select * from (%s) s limit 500) t',
                 regexp_replace(q, ';\s*$', ''))
    into r;
  return r;
end;
$$;

revoke all on function radar_consulta(text) from public, anon, authenticated;
grant execute on function radar_consulta(text) to service_role;

-- Reporte de cada día: lo escribe la corrida automática (GitHub Actions) y lo lee la tarea de ChatGPT
-- con el conector de Supabase para armar el mensaje del usuario.
create table if not exists reportes_diarios (
  fecha             date primary key,
  reporte_md        text,          -- reporte general (tablas)
  personalizados_md text,          -- "## Tus reportes"
  salud_md          text,          -- estado de las fuentes, barrios sin mapear, tipo de cambio
  generado_en       timestamptz default now()
);
alter table reportes_diarios enable row level security;
grant all on reportes_diarios to service_role;

-- Reporte visual autocontenido (HTML) que la tarea de ChatGPT le entrega al usuario como archivo.
alter table reportes_diarios add column if not exists reporte_html text;
