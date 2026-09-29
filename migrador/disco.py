import ctypes
import shutil
import string
import sys
from pathlib import Path

from migrador.config import NOME_PASTA_BACKUP


def _existente(caminho: Path) -> Path:
    caminho = caminho.resolve()
    while not caminho.exists() and caminho.parent != caminho:
        caminho = caminho.parent
    return caminho


def espaco_livre(caminho: Path) -> int:
    return shutil.disk_usage(_existente(caminho)).free


def sistema_arquivos(caminho: Path) -> str | None:
    """NTFS, exFAT, FAT32… FAT32 não serve: não aceita arquivos acima de 4 GB."""
    if sys.platform != "win32":
        return None
    raiz = _existente(caminho).anchor
    nome = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(raiz), None, 0, None, None, None, nome, len(nome)
    )
    return nome.value if ok else None


def unidades_fixas() -> list[Path]:
    if sys.platform != "win32":
        return [Path.home()]
    unidades = []
    mascara = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letra in enumerate(string.ascii_uppercase):
        if mascara & (1 << i):
            raiz = f"{letra}:\\"
            # 3 = disco fixo (interno ou externo reconhecido como fixo)
            if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(raiz)) == 3:
                unidades.append(Path(raiz))
    return unidades


def sugerir_pasta() -> Path:
    """A unidade fixa com mais espaço livre, que costuma ser o disco de dados."""
    melhor, livre = Path.home(), -1
    for unidade in unidades_fixas():
        try:
            f = shutil.disk_usage(unidade).free
        except OSError:
            continue
        if f > livre:
            melhor, livre = unidade, f
    return melhor / NOME_PASTA_BACKUP
