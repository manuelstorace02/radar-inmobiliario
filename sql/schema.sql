-- Radar Inmobiliario — esquema de base (Supabase / Postgres)
-- Se corre UNA vez en Supabase > SQL Editor. Es idempotente: se puede volver a correr.
--
-- Idea general:
--   * Lo que cambia seguido (fuentes, alias de barrios, parámetros) vive acá,
--     editable desde el "Table Editor" de Supabase como si fuera una planilla.
--   * El código vive en el repo de GitHub.
--   * No guardamos una fila por aviso por día (llenaría el plan gratis en semanas):
--     guardamos el estado actual de cada aviso + un registro de EVENTOS
--     (alta, baja, cambio de precio, etc.) + estadísticas diarias por barrio.
--     La "foto" completa de cada scrapeo se guarda comprimida en Storage.

-- ---------------------------------------------------------------- inmobiliarias
create table if not exists inmobiliarias (
  id          text primary key,           -- slug: 'sotheby-uy', 'gonzalo-rodriguez'...
  nombre      text not null,
  web         text,
  alias       text[] default '{}',        -- cómo aparece escrita en portales ("Inmob. X", "X Propiedades")
  notas       text,
  creado_en   timestamptz default now()
);

-- ---------------------------------------------------------------- fuentes
-- Una fuente = un sitio que se scrapea. Una inmobiliaria puede tener su web propia
-- y además aparecer en portales (InfoCasas, Gallito, Mercado Libre...).
create table if not exists fuentes (
  id              text primary key,       -- 'infocasas', 'gallito', 'web-xyz'
  nombre          text not null,
  tipo            text not null check (tipo in ('portal','inmobiliaria')),
  inmobiliaria_id text references inmobiliarias(id),
  url_base        text not null,
  modo            text not null default 'sitemap' check (modo in ('sitemap','listado','adapter')),
  patron_aviso    text,                   -- regex que identifica una URL de aviso
  listado_urls    text,                   -- URLs de listados separadas por ' | ', con {page} para paginar
  sitemap_completo boolean default false, -- el sitemap lista TODOS los avisos activos: si uno falta, es señal de baja
  max_urls        int  default 3000,      -- tope de URLs a revisar por corrida
  delay_seg       real default 1.5,       -- pausa entre requests al mismo dominio
  activa          boolean default true,
  estado          text default 'nueva',   -- nueva | ok | rota | bloqueada | pausada
  notas           text,
  creado_en       timestamptz default now()
);

-- ---------------------------------------------------------------- barrios
-- Diccionario: cómo lo escriben -> barrio normalizado.
create table if not exists barrios_alias (
  alias        text not null,             -- normalizado: minúsculas, sin tildes ("pocitos nuevo")
  barrio       text not null,             -- barrio canónico ("Pocitos")
  departamento text not null,             -- "Montevideo", "Maldonado"...
  tipo         text default 'barrio',     -- barrio | zona | localidad | departamento
  primary key (alias, departamento)
);

create table if not exists barrios_sin_mapear (
  texto        text not null,
  departamento text not null default '',
  veces        int  default 1,
  ejemplo_url  text,
  ultima_vez   date default current_date,
  primary key (texto, departamento)
);

-- ---------------------------------------------------------------- parámetros
create table if not exists parametros (
  clave         text primary key,
  valor         text not null,
  actualizado_en timestamptz default now()
);
insert into parametros (clave, valor) values ('tipo_cambio_usd_uyu', '40') on conflict do nothing;

-- ---------------------------------------------------------------- avisos
-- Una fila por URL de aviso, con su estado ACTUAL.
create table if not exists avisos (
  id                 text primary key,    -- '<fuente_id>:<id externo o hash de url>'
  fuente_id          text not null references fuentes(id),
  url                text not null,
  inmobiliaria_id    text references inmobiliarias(id),
  anunciante         text,                -- texto crudo del anunciante (en portales)
  operacion          text,                -- venta | alquiler | alquiler_temporal
  tipo               text,                -- apartamento | casa | terreno | local | oficina | ...
  titulo             text,
  departamento       text,
  barrio             text,
  barrio_raw         text,
  direccion          text,
  precio             numeric,
  moneda             text,                -- USD | UYU
  precio_usd         numeric,
  gastos_comunes     numeric,
  m2_totales         numeric,
  m2_edificados      numeric,
  dormitorios        int,
  banos              int,
  garaje             boolean,
  lat                double precision,
  lon                double precision,
  imagenes           jsonb default '[]',
  estado             text default 'activo' check (estado in ('activo','baja')),
  primera_vez        date not null,
  ultima_vez         date not null,       -- último día en que se vio activo
  revisado_en        date,                -- último día en que se pidió la URL
  fallos_consecutivos int default 0,
  propiedad_id       text,                -- misma propiedad en distintas fuentes
  actualizado_en     timestamptz default now()
);
create index if not exists avisos_fuente_idx    on avisos (fuente_id, estado);
create index if not exists avisos_barrio_idx    on avisos (departamento, barrio, operacion, tipo);
create index if not exists avisos_propiedad_idx on avisos (propiedad_id);

-- ---------------------------------------------------------------- eventos
create table if not exists eventos (
  id                bigserial primary key,
  fecha             date not null,
  aviso_id          text not null references avisos(id),
  fuente_id         text not null,
  tipo              text not null check (tipo in ('alta','baja','reaparece','precio','dato')),
  campo             text,
  valor_anterior    text,
  valor_nuevo       text,
  precio_usd_anterior numeric,
  precio_usd_nuevo    numeric,
  creado_en         timestamptz default now()
);
create index if not exists eventos_fecha_idx on eventos (fecha, tipo);
create index if not exists eventos_aviso_idx on eventos (aviso_id);

-- ---------------------------------------------------------------- corridas
create table if not exists corridas (
  id               bigserial primary key,
  fecha            date not null,
  fuente_id        text not null,
  inicio           timestamptz,
  fin              timestamptz,
  urls_descubiertas int default 0,
  urls_revisadas   int default 0,
  avisos_ok        int default 0,
  nuevos           int default 0,
  bajas            int default 0,
  cambios_precio   int default 0,
  errores          int default 0,
  estado           text,                  -- ok | sospechosa | fallida
  notas            text
);
create index if not exists corridas_fecha_idx on corridas (fecha, fuente_id);

-- ---------------------------------------------------------------- estadísticas diarias
create table if not exists estadisticas_diarias (
  fecha          date not null,
  departamento   text not null,
  barrio         text not null,
  operacion      text not null,
  tipo           text not null,
  avisos_activos int,
  mediana_usd    numeric,
  mediana_usd_m2 numeric,
  altas          int,
  bajas          int,
  subas_precio   int,
  bajas_precio   int,
  primary key (fecha, departamento, barrio, operacion, tipo)
);

-- ---------------------------------------------------------------- vistas (para Looker Studio / consultas)
create or replace view v_eventos_detalle as
select e.fecha, e.tipo as evento, e.campo, e.valor_anterior, e.valor_nuevo,
       e.precio_usd_anterior, e.precio_usd_nuevo,
       case when e.precio_usd_anterior > 0
            then round(100.0 * (e.precio_usd_nuevo - e.precio_usd_anterior) / e.precio_usd_anterior, 1) end as variacion_pct,
       a.fuente_id, f.tipo as tipo_fuente,
       coalesce(i.nombre, a.anunciante, f.nombre) as inmobiliaria,
       a.operacion, a.tipo, a.departamento, a.barrio, a.dormitorios, a.m2_edificados,
       a.precio_usd, a.titulo, a.url, a.propiedad_id
from eventos e
join avisos a on a.id = e.aviso_id
join fuentes f on f.id = a.fuente_id
left join inmobiliarias i on i.id = a.inmobiliaria_id;

-- Misma propiedad publicada en varias fuentes, con diferencia de precio.
create or replace view v_propiedades_multifuente as
select propiedad_id,
       count(*)                          as avisos,
       count(distinct fuente_id)         as fuentes,
       count(*) filter (where estado = 'activo') as activos,
       count(*) filter (where estado = 'baja')   as dados_de_baja,
       min(precio_usd) filter (where estado = 'activo') as precio_min_usd,
       max(precio_usd) filter (where estado = 'activo') as precio_max_usd,
       round(100.0 * (max(precio_usd) filter (where estado='activo') - min(precio_usd) filter (where estado='activo'))
             / nullif(min(precio_usd) filter (where estado='activo'), 0), 1) as diferencia_pct,
       min(departamento) as departamento, min(barrio) as barrio, min(operacion) as operacion, min(tipo) as tipo,
       string_agg(fuente_id || ' ' || coalesce(precio::text,'?') || ' ' || coalesce(moneda,''), ' | ' order by fuente_id) as detalle
from avisos
where propiedad_id is not null
group by propiedad_id
having count(*) > 1;

-- Stock activo por inmobiliaria y barrio.
create or replace view v_stock_inmobiliaria as
select coalesce(i.nombre, a.anunciante, f.nombre) as inmobiliaria, a.fuente_id,
       a.operacion, a.tipo, a.departamento, a.barrio,
       count(*) as avisos_activos,
       percentile_cont(0.5) within group (order by a.precio_usd) as mediana_usd
from avisos a
join fuentes f on f.id = a.fuente_id
left join inmobiliarias i on i.id = a.inmobiliaria_id
where a.estado = 'activo'
group by 1,2,3,4,5,6;

-- Seguridad: con RLS activado y sin políticas, solo la service_role key (la que usa el agente)
-- puede leer/escribir. Nadie más con la anon key.
alter table inmobiliarias        enable row level security;
alter table fuentes              enable row level security;
alter table barrios_alias        enable row level security;
alter table barrios_sin_mapear   enable row level security;
alter table parametros           enable row level security;
alter table avisos               enable row level security;
alter table eventos              enable row level security;
alter table corridas             enable row level security;
alter table estadisticas_diarias enable row level security;

-- migraciones (si la tabla ya existía)
alter table fuentes add column if not exists sitemap_completo boolean default false;
