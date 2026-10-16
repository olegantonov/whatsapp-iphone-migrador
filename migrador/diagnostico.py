"""Pacote de diagnóstico: os registros (logs) e o andamento da migração, sem dados pessoais.

Serve para investigar falhas em PCs de usuários. Antes de sair do PC, o conteúdo passa
por anonimizar(): números de série dos iPhones, nomes dos aparelhos e o nome do usuário
do Windows são trocados por marcadores. Senhas nunca vão para o log nem para o pacote.
"""
import getpass
import hashlib
import json
import logging
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from migrador import estado as est, registro
from migrador.config import empacotado, pasta_programa
from migrador.versao import __version__

log = logging.getLogger(__name__)

# UDID de iPhone: 8-16 hex com hífen (modelos novos) ou 40 hex (antigos).
_UDID = re.compile(r"\b(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})\b")
MAX_LOGS = 8
MAX_BYTES_LOG = 5 * 1024**2


def _versao_pacote(nome: str) -> str:
    try:
        from importlib.metadata import version
        return version(nome)
    except Exception:
        return "?"


def ambiente() -> dict:
    """O que costuma explicar uma falha: versões e onde o programa roda."""
    return {
        "programa": __version__,
        "windows": platform.platform(),
        "python": sys.version.split()[0],
        "pymobiledevice3": _versao_pacote("pymobiledevice3"),
        "pyiosbackup": _versao_pacote("pyiosbackup"),
        "empacotado": empacotado(),
        "pasta_programa": str(pasta_programa()),
    }


def registrar_ambiente() -> None:
    log.info("ambiente: %s", ambiente())


def _apelido(valor: str, prefixo: str) -> str:
    # Mesmo aparelho, mesmo apelido em todos os arquivos: dá para seguir o fio sem expor o número.
    return f"<{prefixo}-{hashlib.sha256(valor.encode()).hexdigest()[:8]}>"


def anonimizar(texto: str, nomes: list[str] | None = None, udids: list[str] | None = None) -> str:
    for udid in {u for u in (udids or []) if u}:
        texto = re.sub(re.escape(udid), _apelido(udid.upper(), "iphone"), texto, flags=re.IGNORECASE)
    texto = _UDID.sub(lambda m: _apelido(m.group(0).upper(), "iphone"), texto)
    for nome in sorted({n for n in (nomes or []) if n and len(n) >= 3}, key=len, reverse=True):
        texto = texto.replace(nome, "<nome-do-aparelho>")
    usuario = _usuario_windows()
    if usuario and len(usuario) >= 3:
        texto = re.sub(rf"(\\Users\\|/Users/){re.escape(usuario)}", r"\1<usuario>", texto, flags=re.IGNORECASE)
        texto = texto.replace(usuario, "<usuario>")
    return texto


def _usuario_windows() -> str:
    try:
        return os.environ.get("USERNAME") or getpass.getuser()
    except Exception:
        return ""


def _nomes_aparelhos(e: est.Estado | None) -> list[str]:
    if not e:
        return []
    return [d.get("nome", "") for d in (e.origem, e.destino) if d]


def _udids(e: est.Estado | None) -> list[str]:
    if not e:
        return []
    return [d.get("udid", "") for d in (e.origem, e.destino) if d]


def estado_sem_segredos(e: est.Estado | None) -> dict | None:
    if not e:
        return None
    from dataclasses import asdict
    dados = asdict(e)
    dados.pop("senha_protegida", None)
    for papel in ("origem", "destino"):
        if dados.get(papel):
            dados[papel] = {k: v for k, v in dados[papel].items() if k != "nome"}
    return dados


def logs_recentes(limite: int = MAX_LOGS) -> list[Path]:
    pasta = registro.pasta_logs()
    if not pasta.exists():
        return []
    # Inclui as continuações do log rotativo (migrador-….log.1, .2…) e o atualizacao.log.
    arquivos = [p for p in pasta.iterdir() if p.is_file() and ".log" in p.name]
    return sorted(arquivos, key=lambda p: p.stat().st_mtime, reverse=True)[:limite]


def _ler_final(caminho: Path, limite: int = MAX_BYTES_LOG) -> str:
    """Logs grandes: guarda o final, que é onde a falha costuma estar."""
    dados = caminho.read_bytes()
    if len(dados) > limite:
        dados = b"[... inicio cortado ...]\n" + dados[-limite:]
    return dados.decode("utf-8", errors="replace")


def gerar_pacote(destino: Path, descricao: str = "") -> Path:
    """Gera um .zip com os logs recentes e o andamento, tudo anonimizado."""
    e = est.carregar()
    nomes, udids = _nomes_aparelhos(e), _udids(e)

    def limpar(texto: str) -> str:
        return anonimizar(texto, nomes, udids)

    resumo = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "descricao_do_usuario": limpar(descricao),
        "ambiente": ambiente(),
        "etapa": e.etapa if e else None,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("resumo.json", limpar(json.dumps(resumo, ensure_ascii=False, indent=2)))
        estado = estado_sem_segredos(e)
        if estado:
            z.writestr("estado.json", limpar(json.dumps(estado, ensure_ascii=False, indent=2)))
        for arq in logs_recentes():
            try:
                z.writestr(f"logs/{arq.name}", limpar(_ler_final(arq)))
            except OSError:
                log.warning("não consegui ler %s para o diagnóstico", arq.name)
    log.info("pacote de diagnóstico gerado: %s", destino)
    return destino
