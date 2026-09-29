import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from migrador.config import pasta_dados

_arquivo: Path | None = None


def iniciar() -> Path:
    """Liga o log em arquivo. Tudo que o programa faz com os aparelhos fica registrado
    para diagnóstico, sem nunca gravar senhas."""
    global _arquivo
    if _arquivo:
        return _arquivo
    pasta = pasta_dados() / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    _arquivo = pasta / f"migrador-{datetime.now():%Y%m%d-%H%M%S}.log"
    handler = RotatingFileHandler(_arquivo, maxBytes=20 * 1024**2, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(handler)
    return _arquivo


def arquivo_atual() -> Path | None:
    return _arquivo


def pasta_logs() -> Path:
    return pasta_dados() / "logs"
