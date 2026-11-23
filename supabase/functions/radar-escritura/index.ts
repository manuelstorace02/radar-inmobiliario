// Radar Inmobiliario — conector de ESCRITURA para ChatGPT (servidor MCP chiquito en una Edge Function de Supabase).
//
// El conector oficial de Supabase en ChatGPT es de solo lectura (a propósito). Este agrega lo mínimo para que el
// usuario arme sus cosas desde el chat, sin tocar código:
//   - secciones del panel web (tabla panel_secciones)        - reportes del mensaje diario (reportes_guardados)
//   - pausar / reactivar cualquiera de los dos               - preferencias (parametros con prefijo pref_)
//   - anotar pedidos que necesitan código (tabla pedidos)    - republicar el panel ya (si hay GITHUB_TOKEN)
//
// Seguridad: NO ejecuta SQL de escritura. Las consultas se prueban con radar_consulta por GET (transacción de solo
// lectura) y solo se escriben filas en esas 4 tablas. Para usarlo hay que saber la clave (RADAR_MCP_CLAVE), que va
// en la URL del conector: https://<proyecto>.supabase.co/functions/v1/radar-escritura?k=<clave>
//
// Variables (Supabase > Edge Functions > Secrets):
//   RADAR_MCP_CLAVE   obligatoria. Una contraseña larga.
//   GITHUB_TOKEN      opcional. Token de GitHub con permiso "Actions: write" sobre el repo, para "publicar ya".
//   GITHUB_REPO       opcional. Por defecto manuelstorace02/radar-inmobiliario.
//   SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY las pone Supabase solo. RADAR_SERVICE_KEY las reemplaza si hace falta.

const URL_DB = Deno.env.get("SUPABASE_URL")!;
const KEY = Deno.env.get("RADAR_SERVICE_KEY") || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const CLAVE = Deno.env.get("RADAR_MCP_CLAVE") || "";
const GH_TOKEN = Deno.env.get("GITHUB_TOKEN") || "";
const GH_REPO = Deno.env.get("GITHUB_REPO") || "manuelstorace02/radar-inmobiliario";

const H: Record<string, string> = { "apikey": KEY, "Content-Type": "application/json" };
if (KEY.startsWith("eyJ")) H["Authorization"] = `Bearer ${KEY}`;

const SLUG = /^[a-z0-9][a-z0-9-]{2,60}$/;
const GRAFICOS = ["numero", "barras", "lineas", "tabla"];

async function consulta(sql: string): Promise<unknown[]> {
  const r = await fetch(`${URL_DB}/rest/v1/rpc/radar_consulta?q=${encodeURIComponent(sql)}`, { headers: H });
  const t = await r.text();
  if (!r.ok) throw new Error(`la consulta falló: ${t.slice(0, 400)}`);
  return JSON.parse(t) || [];
}

async function upsert(tabla: string, filas: Record<string, unknown>[], conflicto: string) {
  const r = await fetch(`${URL_DB}/rest/v1/${tabla}?on_conflict=${conflicto}`, {
    method: "POST", headers: { ...H, "Prefer": "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify(filas),
  });
  if (!r.ok) throw new Error(`no se pudo guardar en ${tabla}: ${(await r.text()).slice(0, 400)}`);
}

async function select(tabla: string, columnas: string) {
  const r = await fetch(`${URL_DB}/rest/v1/${tabla}?select=${columnas}`, { headers: H });
  if (!r.ok) throw new Error(`no se pudo leer ${tabla}: ${(await r.text()).slice(0, 300)}`);
  return await r.json();
}

function exigir(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const TOOLS = [
  {
    name: "probar_consulta",
    description: "Corre un SELECT de solo lectura sobre las vistas dash_* (ver AGENTS.md del repo) y devuelve hasta " +
      "30 filas. Usalo SIEMPRE antes de guardar una sección o un reporte, para mostrarle al usuario qué va a ver.",
    inputSchema: { type: "object", properties: { sql: { type: "string", description: "SELECT ... o WITH ..." } }, required: ["sql"] },
    annotations: { readOnlyHint: true },
  },
  {
    name: "guardar_seccion_panel",
    description: "Crea o cambia (mismo id) una sección del panel web. grafico: 'numero' (1 fila, cada columna una " +
      "tarjeta), 'barras' (columnas etiqueta, valor), 'lineas' (fecha, valor) o (fecha, serie, valor), 'tabla' " +
      "(cualquier cosa; si trae columna url, la primera columna es link). orden: menor = más arriba (por defecto 100; " +
      "las fijas del panel van debajo de las del usuario). Se ve en el panel después de publicar_panel o mañana.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "slug corto en minúsculas con guiones, ej. m2-malvin-norte-vs-sur" },
        titulo: { type: "string" }, descripcion: { type: "string" }, sql: { type: "string" },
        grafico: { type: "string", enum: GRAFICOS }, orden: { type: "integer" },
      },
      required: ["id", "titulo", "sql", "grafico"],
    },
  },
  {
    name: "guardar_reporte",
    description: "Crea o cambia (mismo id) un reporte que se agrega al mensaje de la mañana y al reporte del día. " +
      "frecuencia 'diaria' o 'semanal' (lunes). solo_si_hay_resultados: si da 0 filas no se muestra (alertas).",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string" }, nombre: { type: "string" }, descripcion: { type: "string" }, sql: { type: "string" },
        frecuencia: { type: "string", enum: ["diaria", "semanal"] }, solo_si_hay_resultados: { type: "boolean" },
      },
      required: ["id", "nombre", "sql"],
    },
  },
  {
    name: "pausar",
    description: "Pausa (activo=false) o reactiva (activo=true) una sección del panel o un reporte. Nunca se borra nada.",
    inputSchema: {
      type: "object",
      properties: { que: { type: "string", enum: ["seccion", "reporte"] }, id: { type: "string" }, activo: { type: "boolean" } },
      required: ["que", "id", "activo"],
    },
  },
  {
    name: "listar_mis_cosas",
    description: "Lista las secciones del panel, los reportes guardados, las preferencias y los pedidos pendientes.",
    inputSchema: { type: "object", properties: {} },
    annotations: { readOnlyHint: true },
  },
  {
    name: "guardar_preferencia",
    description: "Guarda una preferencia del usuario para el mensaje diario (ej. clave 'barrios_foco' valor " +
      "'Malvín Norte, Buceo'; 'tono' 'corto y al grano'). La tarea de la mañana las lee de parametros (pref_*).",
    inputSchema: {
      type: "object",
      properties: { clave: { type: "string" }, valor: { type: "string" } }, required: ["clave", "valor"],
    },
  },
  {
    name: "anotar_pedido",
    description: "Anota un pedido que necesita cambiar código (sumar una página o inmobiliaria, cambiar el diseño, " +
      "una función nueva). Queda en la tabla pedidos para el próximo que trabaje en el repo.",
    inputSchema: { type: "object", properties: { texto: { type: "string" } }, required: ["texto"] },
  },
  {
    name: "publicar_panel",
    description: "Rearma y publica el panel web ahora (tarda ~2 minutos) para ver los cambios sin esperar a mañana.",
    inputSchema: { type: "object", properties: {} },
  },
];

async function llamar(nombre: string, a: Record<string, any>): Promise<unknown> {
  const ahora = new Date().toISOString();
  switch (nombre) {
    case "probar_consulta": {
      const filas = await consulta(String(a.sql || ""));
      return { filas: filas.length, muestra: filas.slice(0, 30) };
    }
    case "guardar_seccion_panel": {
      exigir(SLUG.test(a.id || ""), "id inválido: usar minúsculas, números y guiones (3 a 60 caracteres)");
      exigir(GRAFICOS.includes(a.grafico), `grafico debe ser uno de ${GRAFICOS.join(", ")}`);
      const filas = await consulta(a.sql);
      await upsert("panel_secciones", [{
        id: a.id, titulo: a.titulo, descripcion: a.descripcion ?? null, sql: a.sql, grafico: a.grafico,
        orden: Number.isInteger(a.orden) ? a.orden : 100, activo: true, pedido_por: "usuario (chat)",
        actualizado_en: ahora, ultimas_filas: filas.length, ultimo_error: null,
      }], "id");
      return { ok: true, filas_hoy: filas.length, muestra: filas.slice(0, 5),
        nota: "Guardada. Se ve en el panel después de publicar_panel o en la corrida de mañana." };
    }
    case "guardar_reporte": {
      exigir(SLUG.test(a.id || ""), "id inválido: usar minúsculas, números y guiones (3 a 60 caracteres)");
      const filas = await consulta(a.sql);
      await upsert("reportes_guardados", [{
        id: a.id, nombre: a.nombre, descripcion: a.descripcion ?? null, sql: a.sql,
        frecuencia: a.frecuencia === "semanal" ? "semanal" : "diaria",
        solo_si_hay_resultados: a.solo_si_hay_resultados ?? true, activo: true, pedido_por: "usuario (chat)",
        ultima_ejecucion: ahora, ultimas_filas: filas.length, ultimo_error: null,
      }], "id");
      return { ok: true, filas_hoy: filas.length, muestra: filas.slice(0, 5), nota: "Guardado. Sale desde mañana." };
    }
    case "pausar": {
      const tabla = a.que === "seccion" ? "panel_secciones" : "reportes_guardados";
      const r = await fetch(`${URL_DB}/rest/v1/${tabla}?id=eq.${encodeURIComponent(a.id)}`, {
        method: "PATCH", headers: { ...H, "Prefer": "return=representation" },
        body: JSON.stringify({ activo: !!a.activo }),
      });
      const t = r.ok ? await r.json() : [];
      exigir(Array.isArray(t) && t.length, `no existe ${a.que} con id ${a.id}`);
      return { ok: true, id: a.id, activo: !!a.activo };
    }
    case "listar_mis_cosas": {
      return {
        secciones_panel: await select("panel_secciones", "id,titulo,grafico,orden,activo,ultimas_filas,ultimo_error"),
        reportes: await select("reportes_guardados", "id,nombre,frecuencia,activo,ultimas_filas,ultimo_error"),
        preferencias: (await select("parametros", "clave,valor")).filter((p: any) => String(p.clave).startsWith("pref_")),
        pedidos: (await select("pedidos", "id,creado_en,texto,estado,respuesta")).filter((p: any) => p.estado === "pendiente"),
      };
    }
    case "guardar_preferencia": {
      const clave = "pref_" + String(a.clave || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^pref_/, "");
      exigir(clave.length > 5 && clave.length < 60, "clave inválida");
      await upsert("parametros", [{ clave, valor: String(a.valor ?? "").slice(0, 2000) }], "clave");
      return { ok: true, clave };
    }
    case "anotar_pedido": {
      exigir(String(a.texto || "").trim().length > 3, "el pedido está vacío");
      const r = await fetch(`${URL_DB}/rest/v1/pedidos`, {
        method: "POST", headers: { ...H, "Prefer": "return=representation" },
        body: JSON.stringify([{ texto: String(a.texto).slice(0, 4000) }]),
      });
      const t = await r.text();
      exigir(r.ok, `no se pudo anotar: ${t.slice(0, 300)}`);
      return { ok: true, pedido: JSON.parse(t)[0] };
    }
    case "publicar_panel": {
      if (!GH_TOKEN) return { ok: false, nota: "No está configurado GITHUB_TOKEN: el panel se actualiza en la corrida de la madrugada." };
      const r = await fetch(`https://api.github.com/repos/${GH_REPO}/actions/workflows/radar-diario.yml/dispatches`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${GH_TOKEN}`, "Accept": "application/vnd.github+json", "User-Agent": "radar-escritura" },
        body: JSON.stringify({ ref: "main", inputs: { solo_panel: "true" } }),
      });
      if (r.status !== 204) throw new Error(`GitHub respondió ${r.status}: ${(await r.text()).slice(0, 300)}`);
      return { ok: true, nota: "Publicando: en ~2 minutos está en el panel (recargá la página)." };
    }
  }
  throw new Error(`herramienta desconocida: ${nombre}`);
}

async function rpc(m: any): Promise<any | null> {
  const id = m?.id ?? null;
  const res = (result: unknown) => ({ jsonrpc: "2.0", id, result });
  if (typeof m?.method !== "string") return { jsonrpc: "2.0", id, error: { code: -32600, message: "pedido inválido" } };
  if (m.method.startsWith("notifications/")) return null;
  switch (m.method) {
    case "initialize":
      return res({
        protocolVersion: m.params?.protocolVersion || "2025-06-18",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "radar-escritura", version: "1.0.0" },
        instructions: "Escritura acotada del Radar Inmobiliario: secciones del panel, reportes guardados, " +
          "preferencias y pedidos. Probá cada consulta con probar_consulta y mostrásela al usuario antes de guardar.",
      });
    case "ping":
      return res({});
    case "tools/list":
      return res({ tools: TOOLS });
    case "tools/call": {
      try {
        const out = await llamar(m.params?.name, m.params?.arguments || {});
        return res({ content: [{ type: "text", text: JSON.stringify(out, null, 1) }] });
      } catch (e) {
        return res({ content: [{ type: "text", text: `Error: ${(e as Error).message}` }], isError: true });
      }
    }
    default:
      return { jsonrpc: "2.0", id, error: { code: -32601, message: `método no soportado: ${m.method}` } };
  }
}

Deno.serve(async (req) => {
  const u = new URL(req.url);
  const k = u.searchParams.get("k") || u.pathname.split("/").filter(Boolean).pop() || "";
  if (!CLAVE || k !== CLAVE) return new Response("no autorizado", { status: 401 });
  if (req.method === "GET") return new Response("radar-escritura: usar POST (MCP)", { status: 405, headers: { "Allow": "POST" } });
  if (req.method === "DELETE") return new Response(null, { status: 204 });
  if (req.method !== "POST") return new Response(null, { status: 405 });
  let body: any;
  try {
    body = await req.json();
  } catch {
    return Response.json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "JSON inválido" } }, { status: 400 });
  }
  const lote = Array.isArray(body);
  const resps = (await Promise.all((lote ? body : [body]).map(rpc))).filter((r) => r !== null);
  if (!resps.length) return new Response(null, { status: 202 });
  return Response.json(lote ? resps : resps[0]);
});
