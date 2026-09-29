"""Progresso da migração salvo em disco, para retomar depois de fechar o programa,
queda de energia ou atualização automática."""
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from migrador import protecao
from migrador.config import pasta_dados

log = logging.getLogger(__name__)

ETAPAS = [
    ("inicio", "Início"),
    ("preparacao", "Preparação"),
    ("conexao", "Conectar e conferir"),
    ("senha", "Senha do backup"),
    ("backup", "Backup do iPhone antigo"),
    ("verificacao", "Verificar o backup"),
    ("destino", "Preparar o iPhone novo"),
    ("restauracao", "Restaurar no iPhone novo"),
    ("concluido", "Concluído"),
]
CHAVES_ETAPAS = [c for c, _ in ETAPAS]


@dataclass
class Estado:
    etapa: str = "inicio"
    simulacao: bool = False
    cenario: str = "normal"
    origem: dict | None = None
    destino: dict | None = None
    whatsapp: dict | None = None
    pasta_backup: str = ""
    criptografia_ja_ativa: bool = False
    senha_protegida: str | None = None
    backup_concluido_em: str | None = None
    relatorio: dict | None = None
    restaurado_em: str | None = None
    historico: list[str] = field(default_factory=list)

    def anotar(self, texto: str) -> None:
        self.historico.append(f"{datetime.now():%d/%m/%Y %H:%M:%S} — {texto}")
        log.info(texto)

    def avancar_para(self, etapa: str) -> None:
        # Só avança; voltar de tela não desfaz o que já foi feito.
        if CHAVES_ETAPAS.index(etapa) > CHAVES_ETAPAS.index(self.etapa):
            self.etapa = etapa

    def guardar_senha(self, senha: str) -> bool:
        if not protecao.disponivel():
            return False
        self.senha_protegida = protecao.proteger(senha)
        return True

    def senha_guardada(self) -> str | None:
        if not self.senha_protegida:
            return None
        try:
            return protecao.revelar(self.senha_protegida)
        except Exception:
            log.warning("senha guardada não pôde ser aberta nesta conta")
            return None

    @property
    def pasta_aparelho(self) -> Path | None:
        if not self.pasta_backup or not self.origem:
            return None
        return Path(self.pasta_backup) / self.origem["udid"]


def arquivo() -> Path:
    return pasta_dados() / "estado.json"


def carregar() -> Estado | None:
    caminho = arquivo()
    if not caminho.exists():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return Estado(**{k: v for k, v in dados.items() if k in Estado.__dataclass_fields__})
    except Exception:
        log.exception("estado ilegível; ignorando")
        return None


def salvar(estado: Estado) -> None:
    caminho = arquivo()
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(estado), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, caminho)


def arquivar() -> None:
    """Tira o estado de uso (ao começar outra migração), mas guarda uma cópia."""
    caminho = arquivo()
    if caminho.exists():
        os.replace(caminho, caminho.with_name(f"estado-{datetime.now():%Y%m%d-%H%M%S}.json"))
