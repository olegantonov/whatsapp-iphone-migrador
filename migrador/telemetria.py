"""Envio de dados de funcionamento para o servidor do projeto (Cloudflare Worker + D1).

Duas coisas, ambas opcionais:
- eventos curtos e anônimos (etapa concluída, falha e tipo de erro, modelo e iOS dos iPhones),
  enviados só se a pessoa aceitou na primeira vez que abriu o programa;
- o pacote de diagnóstico (logs anonimizados), enviado só quando a pessoa clica para enviar.

Nunca vão conversas, fotos, contatos, senhas, nomes de aparelho ou números de série.
Sem internet, os eventos esperam numa fila local e vão na próxima vez; nada aqui pode
travar ou derrubar a migração.
"""
import json
import logging
import platform
import threading
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from migrador.config import URL_SERVIDOR_LOGS, pasta_dados
from migrador.versao import __version__

log = logging.getLogger(__name__)

MAX_FILA = 500
LOTE = 50
_trava = threading.Lock()
_enviando = threading.Event()


def _arquivo_preferencias() -> Path:
    return pasta_dados() / "preferencias.json"


def _fila() -> Path:
    return pasta_dados() / "fila-eventos.jsonl"


def _ler_preferencias() -> dict:
    try:
        return json.loads(_arquivo_preferencias().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar_preferencias(dados: dict) -> None:
    caminho = _arquivo_preferencias()
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


def instalacao() -> str:
    """Número aleatório deste PC: junta os eventos de uma mesma instalação sem identificar ninguém."""
    with _trava:
        p = _ler_preferencias()
        if not p.get("instalacao"):
            p["instalacao"] = uuid.uuid4().hex
            _gravar_preferencias(p)
        return p["instalacao"]


def consentimento() -> bool | None:
    """True/False conforme a escolha; None se a pessoa ainda não respondeu."""
    valor = _ler_preferencias().get("telemetria")
    return valor if isinstance(valor, bool) else None


def definir_consentimento(aceito: bool) -> None:
    with _trava:
        p = _ler_preferencias()
        p["telemetria"] = bool(aceito)
        p.setdefault("instalacao", uuid.uuid4().hex)
        _gravar_preferencias(p)
    log.info("envio de dados anônimos %s", "autorizado" if aceito else "recusado")
    if not aceito:
        _fila().unlink(missing_ok=True)


def registrar(tipo: str, etapa: str | None = None, sessao: str | None = None, simulacao: bool = False,
              **dados) -> None:
    if consentimento() is not True:
        return
    try:
        evento = {"tipo": tipo, "quando": datetime.now().astimezone().isoformat(timespec="seconds"),
                  "etapa": etapa, "sessao": sessao, "simulacao": simulacao, "dados": dados or None}
        with _trava:
            with open(_fila(), "a", encoding="utf-8") as fd:
                fd.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
        enviar_em_segundo_plano()
    except Exception:
        log.exception("falha ao registrar evento %s", tipo)


def enviar_em_segundo_plano() -> None:
    if consentimento() is not True or _enviando.is_set():
        return
    _enviando.set()

    def rodar():
        try:
            enviar_pendentes()
        finally:
            _enviando.clear()

    threading.Thread(target=rodar, name="telemetria", daemon=True).start()


def enviar_pendentes(timeout: float = 15) -> int:
    """Envia a fila em lotes. Devolve quantos eventos saíram; falhas ficam para a próxima vez.

    Só uma thread envia por vez e quem registra só acrescenta no fim do arquivo, então as
    primeiras linhas lidas continuam sendo as primeiras na hora de tirá-las da fila."""
    enviados = 0
    while True:
        with _trava:
            linhas = _ler_fila()
            if len(linhas) > MAX_FILA:  # muito tempo sem internet: descarta os mais antigos
                linhas = linhas[-MAX_FILA:]
                _gravar_fila(linhas)
        if not linhas:
            return enviados
        bloco = linhas[:LOTE]
        lote = []
        for x in bloco:
            try:
                lote.append(json.loads(x))
            except ValueError:
                pass
        if lote:
            try:
                _post_json("/v1/eventos", {"instalacao": instalacao(), "versao": __version__,
                                           "windows": platform.platform(), "eventos": lote}, timeout)
            except Exception as e:
                log.info("eventos não enviados agora (%s); ficam na fila", e)
                return enviados
        with _trava:
            _gravar_fila(_ler_fila()[len(bloco):])
        enviados += len(lote)


def _ler_fila() -> list[str]:
    try:
        return [x for x in _fila().read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return []


def _gravar_fila(linhas: list[str]) -> None:
    if linhas:
        _fila().write_text("\n".join(linhas) + "\n", encoding="utf-8")
    else:
        _fila().unlink(missing_ok=True)


def _post_json(caminho: str, dados: dict, timeout: float) -> dict:
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL_SERVIDOR_LOGS + caminho, data=corpo, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"MigradorWhatsApp/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def enviar_diagnostico(zip_path: Path, descricao: str = "", timeout: float = 60) -> str:
    """Envia o pacote (já anonimizado) e devolve o protocolo para a pessoa informar ao suporte."""
    from urllib.parse import quote
    req = urllib.request.Request(
        URL_SERVIDOR_LOGS + "/v1/diagnosticos", data=zip_path.read_bytes(), method="POST", headers={
            "Content-Type": "application/zip", "User-Agent": f"MigradorWhatsApp/{__version__}",
            "X-Instalacao": instalacao(), "X-Versao": __version__, "X-Descricao": quote(descricao[:2000])})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resposta = json.loads(r.read())
    protocolo = resposta.get("protocolo")
    if not protocolo:
        raise RuntimeError(f"resposta inesperada do servidor: {resposta}")
    log.info("diagnóstico enviado; protocolo %s", protocolo)
    return protocolo


def registrar_excecao(tipo, valor, tb, onde: str) -> None:
    """Erro não tratado: tipo, mensagem anonimizada e onde no código aconteceu."""
    try:
        import traceback

        from migrador.diagnostico import anonimizar
        quadros = traceback.extract_tb(tb)[-4:] if tb else []
        local = [f"{Path(q.filename).name}:{q.lineno} {q.name}" for q in quadros]
        registrar("excecao", onde=onde, erro=getattr(tipo, "__name__", str(tipo)),
                  mensagem=anonimizar(str(valor))[:300], local=local)
    except Exception:
        log.exception("falha ao registrar exceção")
