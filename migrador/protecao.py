"""Guarda a senha do backup cifrada com a proteção de dados do Windows (DPAPI).

Só a mesma conta de usuário, neste mesmo PC, consegue abrir. Serve para que um
travamento ou reinício no meio da migração não deixe o backup inacessível.
"""
import base64
import ctypes
import sys
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def disponivel() -> bool:
    return sys.platform == "win32"


def _para_blob(dados: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(dados, len(dados))
    return _Blob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _de_blob(blob: _Blob) -> bytes:
    dados = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return dados


def proteger(texto: str) -> str:
    if not disponivel():
        raise RuntimeError("proteção de senha só existe no Windows")
    entrada = _para_blob(texto.encode("utf-8"))
    saida = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(entrada), "MigradorWhatsApp", None, None, None, 0x1, ctypes.byref(saida)
    ):
        raise ctypes.WinError()
    return base64.b64encode(_de_blob(saida)).decode("ascii")


def revelar(protegido: str) -> str:
    if not disponivel():
        raise RuntimeError("proteção de senha só existe no Windows")
    entrada = _para_blob(base64.b64decode(protegido))
    saida = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, None, None, None, 0x1, ctypes.byref(saida)
    ):
        raise ctypes.WinError()
    return _de_blob(saida).decode("utf-8")
