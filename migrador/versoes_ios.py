"""Consulta na Apple qual é a versão mais recente do iOS para cada modelo de iPhone.

Usa o catálogo público gdmf.apple.com, o mesmo que os aparelhos consultam para
oferecer atualizações.
"""
import json
import logging
import ssl
import urllib.request

from packaging.version import InvalidVersion, Version

log = logging.getLogger(__name__)

URL = "https://gdmf.apple.com/v2/pmv"
_cache: dict[str, str] | None = None


def comparar(a: str, b: str) -> int:
    """-1 se a < b, 0 se iguais, 1 se a > b."""
    try:
        va, vb = Version(a), Version(b)
    except InvalidVersion:
        return 0
    return (va > vb) - (va < vb)


def buscar_ultimas(timeout: float = 15) -> dict[str, str] | None:
    global _cache
    if _cache is not None:
        return _cache
    # O certificado do gdmf é emitido pela raiz própria da Apple, que não vem no Windows.
    # A informação é pública e só serve para recomendar atualização (quem atualiza é o
    # próprio iPhone, pelos servidores da Apple), então a verificação do certificado é dispensável.
    contexto = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(URL, context=contexto, timeout=timeout) as r:
            dados = json.load(r)
    except Exception as e:
        log.warning("não consegui consultar as versões do iOS: %s", e)
        return None
    _cache = interpretar(dados)
    return _cache


def interpretar(dados: dict) -> dict[str, str]:
    ultimas: dict[str, str] = {}
    for conjunto in dados.get("PublicAssetSets", {}).get("iOS", []):
        versao = conjunto.get("ProductVersion", "")
        for modelo in conjunto.get("SupportedDevices", []):
            if not modelo.startswith("iPhone"):
                continue
            if modelo not in ultimas or comparar(versao, ultimas[modelo]) > 0:
                ultimas[modelo] = versao
    return ultimas
