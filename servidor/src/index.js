// Recebe os eventos anônimos e os pacotes de diagnóstico do Migrador de WhatsApp para iPhone
// e guarda no D1. As rotas /admin/* exigem o token ADMIN_TOKEN (wrangler secret).

const MAX_EVENTOS_POR_ENVIO = 50;
const MAX_BYTES_EVENTOS = 64 * 1024;
const MAX_BYTES_DADOS = 4 * 1024;
const MAX_BYTES_DIAGNOSTICO = 1_800_000; // o D1 aceita até 2 MB por linha
const LIMITE_POR_HORA = { eventos: 120, diagnosticos: 6 };
const RETENCAO_DIAS = 180;
const TIPO_VALIDO = /^[a-z_]{2,40}$/;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/v1/eventos") return await receberEventos(req, env);
      if (req.method === "POST" && url.pathname === "/v1/diagnosticos") return await receberDiagnostico(req, env);
      if (url.pathname.startsWith("/admin/")) return await admin(req, env, url);
      if (url.pathname === "/") return json({ servico: "migrador-whatsapp-logs", ok: true });
      return json({ erro: "não encontrado" }, 404);
    } catch (e) {
      console.error(e);
      return json({ erro: "falha interna" }, 500);
    }
  },

  async scheduled(_evento, env) {
    const corte = `-${RETENCAO_DIAS} days`;
    await env.DB.batch([
      env.DB.prepare("DELETE FROM eventos WHERE recebido_em < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)").bind(corte),
      env.DB.prepare("DELETE FROM diagnosticos WHERE recebido_em < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)").bind(corte),
      env.DB.prepare("DELETE FROM limites WHERE chave < ?").bind(`${horaAtual(-2)}`),
    ]);
  },
};

function json(dados, status = 200) {
  return new Response(JSON.stringify(dados, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function texto(v, max = 200) {
  if (v === undefined || v === null) return null;
  return String(v).slice(0, max);
}

function horaAtual(deslocamento = 0) {
  const d = new Date(Date.now() + deslocamento * 3600_000);
  return d.toISOString().slice(0, 13); // 2026-10-16T14
}

async function hash(valor, bytes = 8) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(valor));
  return [...new Uint8Array(d)].slice(0, bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

// A chave começa pela hora, para o cron apagar as antigas com um "<".
async function dentroDoLimite(req, env, tipo) {
  const ip = req.headers.get("cf-connecting-ip") || "?";
  const chave = `${horaAtual()}|${tipo}|${await hash(ip)}`;
  const linha = await env.DB.prepare(
    "INSERT INTO limites (chave, contagem) VALUES (?, 1) " +
      "ON CONFLICT(chave) DO UPDATE SET contagem = contagem + 1 RETURNING contagem",
  )
    .bind(chave)
    .first();
  return linha.contagem <= LIMITE_POR_HORA[tipo];
}

async function receberEventos(req, env) {
  const corpo = await req.text();
  if (corpo.length > MAX_BYTES_EVENTOS) return json({ erro: "envio grande demais" }, 413);
  let dados;
  try {
    dados = JSON.parse(corpo);
  } catch {
    return json({ erro: "JSON inválido" }, 400);
  }
  const instalacao = texto(dados.instalacao, 64);
  const eventos = Array.isArray(dados.eventos) ? dados.eventos.slice(0, MAX_EVENTOS_POR_ENVIO) : [];
  if (!instalacao || !eventos.length) return json({ erro: "faltam instalacao ou eventos" }, 400);
  if (!(await dentroDoLimite(req, env, "eventos"))) return json({ erro: "limite de envios atingido" }, 429);

  const insercoes = [];
  for (const ev of eventos) {
    const tipo = texto(ev.tipo, 40);
    if (!tipo || !TIPO_VALIDO.test(tipo)) continue;
    let extra = ev.dados === undefined ? null : JSON.stringify(ev.dados);
    if (extra && extra.length > MAX_BYTES_DADOS) extra = JSON.stringify({ cortado: extra.slice(0, MAX_BYTES_DADOS) });
    insercoes.push(
      env.DB.prepare(
        "INSERT INTO eventos (ocorrido_em, instalacao, sessao, versao, windows, tipo, etapa, simulacao, dados) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(
        texto(ev.quando, 40),
        instalacao,
        texto(ev.sessao, 64),
        texto(dados.versao, 40),
        texto(dados.windows, 120),
        tipo,
        texto(ev.etapa, 40),
        ev.simulacao ? 1 : 0,
        extra,
      ),
    );
  }
  if (insercoes.length) await env.DB.batch(insercoes);
  return json({ ok: true, gravados: insercoes.length });
}

async function receberDiagnostico(req, env) {
  const tamanhoDeclarado = Number(req.headers.get("content-length") || 0);
  if (tamanhoDeclarado > MAX_BYTES_DIAGNOSTICO) return json({ erro: "arquivo grande demais" }, 413);
  const conteudo = new Uint8Array(await req.arrayBuffer());
  if (!conteudo.length || conteudo.length > MAX_BYTES_DIAGNOSTICO) return json({ erro: "tamanho inválido" }, 413);
  // Todo .zip começa com "PK\x03\x04".
  if (conteudo[0] !== 0x50 || conteudo[1] !== 0x4b) return json({ erro: "esperado um .zip" }, 415);
  if (!(await dentroDoLimite(req, env, "diagnosticos"))) return json({ erro: "limite de envios atingido" }, 429);

  const id = protocolo();
  let descricao = req.headers.get("x-descricao") || "";
  try {
    descricao = decodeURIComponent(descricao);
  } catch {}
  await env.DB.prepare(
    "INSERT INTO diagnosticos (id, instalacao, versao, descricao, tamanho, conteudo) VALUES (?, ?, ?, ?, ?, ?)",
  )
    .bind(
      id,
      texto(req.headers.get("x-instalacao"), 64),
      texto(req.headers.get("x-versao"), 40),
      texto(descricao, 2000),
      conteudo.length,
      conteudo,
    )
    .run();
  return json({ ok: true, protocolo: id });
}

// Curto o bastante para a pessoa ditar ao suporte; sem 0/O e 1/I para não confundir.
function protocolo() {
  const alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const s = [...bytes].map((b) => alfabeto[b % alfabeto.length]).join("");
  return `${s.slice(0, 4)}-${s.slice(4)}`;
}

async function admin(req, env, url) {
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!env.ADMIN_TOKEN || !(await iguais(token, env.ADMIN_TOKEN))) return json({ erro: "não autorizado" }, 401);
  const p = url.searchParams;
  const dias = Math.min(Number(p.get("dias") || 30), 365);
  const desde = `-${dias} days`;

  if (url.pathname === "/admin/resumo") {
    const porTipo = await env.DB.prepare(
      "SELECT tipo, simulacao, COUNT(*) total, COUNT(DISTINCT instalacao) pcs FROM eventos " +
        "WHERE recebido_em >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) GROUP BY tipo, simulacao ORDER BY total DESC",
    )
      .bind(desde)
      .all();
    const porVersao = await env.DB.prepare(
      "SELECT versao, COUNT(DISTINCT instalacao) pcs, MAX(recebido_em) ultimo FROM eventos " +
        "WHERE recebido_em >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) GROUP BY versao ORDER BY versao DESC",
    )
      .bind(desde)
      .all();
    const falhas = await env.DB.prepare(
      "SELECT recebido_em, instalacao, sessao, versao, tipo, etapa, dados FROM eventos " +
        "WHERE simulacao = 0 AND (tipo LIKE '%falha%' OR tipo = 'excecao') " +
        "AND recebido_em >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) ORDER BY id DESC LIMIT 50",
    )
      .bind(desde)
      .all();
    const diags = await env.DB.prepare(
      "SELECT id, recebido_em, versao, descricao, tamanho FROM diagnosticos ORDER BY recebido_em DESC LIMIT 20",
    ).all();
    return json({
      dias,
      por_tipo: porTipo.results,
      por_versao: porVersao.results,
      falhas_recentes: falhas.results.map(comDados),
      diagnosticos_recentes: diags.results,
    });
  }

  if (url.pathname === "/admin/eventos") {
    const filtros = ["recebido_em >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)"];
    const valores = [desde];
    for (const campo of ["tipo", "instalacao", "sessao", "versao"]) {
      if (p.get(campo)) {
        filtros.push(`${campo} = ?`);
        valores.push(p.get(campo));
      }
    }
    if (p.get("simulacao") !== "1") filtros.push("simulacao = 0");
    const limite = Math.min(Number(p.get("limite") || 200), 1000);
    const r = await env.DB.prepare(
      `SELECT * FROM eventos WHERE ${filtros.join(" AND ")} ORDER BY id DESC LIMIT ${limite}`,
    )
      .bind(...valores)
      .all();
    return json(r.results.map(comDados));
  }

  if (url.pathname === "/admin/diagnosticos") {
    const r = await env.DB.prepare(
      "SELECT id, recebido_em, instalacao, versao, descricao, tamanho FROM diagnosticos ORDER BY recebido_em DESC LIMIT 200",
    ).all();
    return json(r.results);
  }

  const m = url.pathname.match(/^\/admin\/diagnosticos\/([A-Z0-9-]{9})$/);
  if (m) {
    const linha = await env.DB.prepare("SELECT conteudo FROM diagnosticos WHERE id = ?").bind(m[1]).first();
    if (!linha) return json({ erro: "protocolo não encontrado" }, 404);
    return new Response(new Uint8Array(linha.conteudo), {
      headers: {
        "content-type": "application/zip",
        "content-disposition": `attachment; filename="diagnostico-${m[1]}.zip"`,
      },
    });
  }
  return json({ erro: "não encontrado" }, 404);
}

function comDados(linha) {
  if (linha.dados) {
    try {
      linha.dados = JSON.parse(linha.dados);
    } catch {}
  }
  return linha;
}

async function iguais(a, b) {
  // Compara os hashes (tamanho fixo) em vez dos textos, para não vazar o token pelo tempo de resposta.
  const [ha, hb] = await Promise.all([hash(a, 32), hash(b, 32)]);
  return ha === hb;
}
