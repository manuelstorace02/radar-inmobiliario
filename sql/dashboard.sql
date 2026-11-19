-- Radar Inmobiliario — vistas para el dashboard (Looker Studio) + usuario de solo lectura.
-- Se corre en Supabase > SQL Editor DESPUÉS de schema.sql. Es idempotente.
--
-- ANTES DE CORRER: cambiá CAMBIAR_ESTA_CLAVE (abajo) por una contraseña larga. Es la que se pone en Looker Studio.
-- Ese usuario solo puede LEER estas vistas: no ve las tablas crudas ni puede modificar nada.

-- ---------------------------------------------------------------- avisos activos (mapa, tablas, filtros)
create or replace view dash_avisos_activos as
select a.id,
       f.nombre                                         as fuente,
       f.tipo                                           as tipo_fuente,
       coalesce(i.nombre, a.anunciante, f.nombre)       as inmobiliaria,
       a.operacion, a.tipo, a.departamento,
       coalesce(a.barrio, 'Sin barrio')                 as barrio,
       a.dormitorios, a.banos, a.m2_edificados,
       a.precio, a.moneda, a.precio_usd,
       case when a.m2_edificados >= 15 and a.precio_usd > 0
            then round(a.precio_usd / a.m2_edificados, 0) end as usd_m2,
       a.lat, a.lon,
       case when a.lat is not null and a.lon is not null
            then a.lat::text || ',' || a.lon::text end  as ubicacion,      -- formato que entiende el mapa de Looker
       a.primera_vez,
       (current_date - a.primera_vez)                   as dias_publicado,
       (a.propiedad_id is not null)                     as publicada_en_varios_lugares,
       a.titulo, a.url
from avisos a
join fuentes f on f.id = a.fuente_id
left join inmobiliarias i on i.id = a.inmobiliaria_id
where a.estado = 'activo';

-- ---------------------------------------------------------------- evolución diaria por barrio
create or replace view dash_evolucion_barrio as
select fecha, departamento, barrio, operacion, tipo,
       avisos_activos, mediana_usd, mediana_usd_m2,
       altas, bajas, subas_precio, bajas_precio
from estadisticas_diarias
where barrio <> '¿?';

-- ---------------------------------------------------------------- eventos (altas, bajas, cambios de precio)
create or replace view dash_eventos as
select e.fecha,
       date_trunc('week', e.fecha)::date                as semana,
       case e.tipo when 'alta' then 'Publicado' when 'baja' then 'Desapareció'
                   when 'precio' then case when e.precio_usd_nuevo > e.precio_usd_anterior
                                           then 'Subió precio' else 'Bajó precio' end
                   when 'reaparece' then 'Reapareció' else 'Cambio de datos' end as evento,
       e.precio_usd_anterior, e.precio_usd_nuevo,
       case when e.precio_usd_anterior > 0 and e.precio_usd_nuevo > 0
            then round(100.0 * (e.precio_usd_nuevo - e.precio_usd_anterior) / e.precio_usd_anterior, 1) end
                                                        as variacion_pct,
       f.nombre                                         as fuente,
       coalesce(i.nombre, a.anunciante, f.nombre)       as inmobiliaria,
       a.operacion, a.tipo, a.departamento,
       coalesce(a.barrio, 'Sin barrio')                 as barrio,
       a.dormitorios, a.m2_edificados, a.precio_usd, a.titulo, a.url
from eventos e
join avisos a   on a.id = e.aviso_id
join fuentes f  on f.id = a.fuente_id
left join inmobiliarias i on i.id = a.inmobiliaria_id
where e.tipo <> 'dato';

-- ---------------------------------------------------------------- misma propiedad, distinto precio
create or replace view dash_misma_propiedad as
select a.propiedad_id,
       coalesce(i.nombre, a.anunciante, f.nombre)       as inmobiliaria,
       f.nombre                                         as fuente,
       a.operacion, a.tipo, a.departamento, a.barrio, a.dormitorios, a.m2_edificados,
       a.precio_usd, a.estado,
       min(a.precio_usd) over (partition by a.propiedad_id) as precio_min_usd,
       max(a.precio_usd) over (partition by a.propiedad_id) as precio_max_usd,
       a.url
from avisos a
join fuentes f on f.id = a.fuente_id
left join inmobiliarias i on i.id = a.inmobiliaria_id
where a.propiedad_id is not null;

-- ---------------------------------------------------------------- salud de las fuentes
create or replace view dash_corridas as
select c.fecha, f.nombre as fuente, c.estado, c.urls_descubiertas, c.urls_revisadas, c.avisos_ok,
       c.nuevos, c.bajas, c.cambios_precio, c.errores, c.notas
from corridas c join fuentes f on f.id = c.fuente_id;

-- ---------------------------------------------------------------- usuario de solo lectura para Looker
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'looker_lector') then
    create role looker_lector login password 'CAMBIAR_ESTA_CLAVE';
  else
    alter role looker_lector with login password 'CAMBIAR_ESTA_CLAVE';
  end if;
end $$;

grant usage on schema public to looker_lector;
grant select on dash_avisos_activos, dash_evolucion_barrio, dash_eventos, dash_misma_propiedad, dash_corridas
  to looker_lector;
