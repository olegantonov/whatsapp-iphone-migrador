-- Eventos curtos e anônimos de funcionamento (só de quem aceitou enviar).
CREATE TABLE eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recebido_em TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  ocorrido_em TEXT,
  instalacao TEXT NOT NULL,   -- número aleatório criado no PC; não identifica a pessoa
  sessao TEXT,                -- número aleatório de cada migração
  versao TEXT,
  windows TEXT,
  tipo TEXT NOT NULL,
  etapa TEXT,
  simulacao INTEGER NOT NULL DEFAULT 0,
  dados TEXT                  -- JSON pequeno
);
CREATE INDEX eventos_recebido ON eventos (recebido_em);
CREATE INDEX eventos_tipo ON eventos (tipo, recebido_em);
CREATE INDEX eventos_instalacao ON eventos (instalacao, recebido_em);

-- Pacotes de diagnóstico enviados pelo botão (logs anonimizados, .zip).
CREATE TABLE diagnosticos (
  id TEXT PRIMARY KEY,        -- protocolo mostrado para a pessoa
  recebido_em TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  instalacao TEXT,
  versao TEXT,
  descricao TEXT,
  tamanho INTEGER NOT NULL,
  conteudo BLOB NOT NULL
);
CREATE INDEX diagnosticos_recebido ON diagnosticos (recebido_em);

-- Limite de envios por origem e hora, contra abuso do endereço público.
CREATE TABLE limites (
  chave TEXT PRIMARY KEY,
  contagem INTEGER NOT NULL
);
