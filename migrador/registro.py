import logging
import sys
import threading
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
    _capturar_excecoes()
    return _arquivo


def _capturar_excecoes() -> None:
    """No .exe sem console, uma exceção não tratada sumiria sem deixar rastro."""
    log = logging.getLogger("migrador.excecao")

    def no_programa(tipo, valor, tb):
        log.critical("exceção não tratada", exc_info=(tipo, valor, tb))
        _telemetria(tipo, valor, tb, "programa")

    def na_thread(args):
        log.critical("exceção não tratada na thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        _telemetria(args.exc_type, args.exc_value, args.exc_traceback, "thread")

    sys.excepthook = no_programa
    threading.excepthook = na_thread


def _telemetria(tipo, valor, tb, onde: str) -> None:
    try:
        from migrador import telemetria
        telemetria.registrar_excecao(tipo, valor, tb, onde)
    except Exception:
        pass


def arquivo_atual() -> Path | None:
    return _arquivo


def pasta_logs() -> Path:
    return pasta_dados() / "logs"
