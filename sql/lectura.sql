-- Radar Inmobiliario — permitir LECTURA de las tablas a roles de solo lectura (ej. el conector de
-- Supabase en ChatGPT, que usa un usuario de solo lectura).
--
-- Las tablas tienen RLS activado. Sin políticas, cualquier rol que no sea service_role ve 0 filas:
-- por eso el conector decía "reportes_diarios sin registros" aunque la corrida la había guardado.
-- Esta política permite SELECT a quien YA tenga permiso de lectura sobre la tabla. anon y authenticated
-- (la API pública) no tienen ese permiso, así que siguen sin ver nada. Nadie gana permiso de escritura.
-- Es idempotente: se puede volver a correr cuando se agreguen tablas nuevas.

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('drop policy if exists lectura on public.%I', t);
    execute format('create policy lectura on public.%I for select using (true)', t);
  end loop;
end $$;

-- Asegurar que la API pública (anon/authenticated) NO pueda leer nada.
revoke all on all tables in schema public from anon, authenticated;
