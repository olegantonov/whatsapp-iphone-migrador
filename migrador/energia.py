"""Impede o Windows de suspender durante backup e restauração (horas de cópia)."""
import ctypes
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def manter_acordado(ligar: bool) -> None:
    if sys.platform != "win32":
        return
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)
