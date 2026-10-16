"""Lê o estado do PC que importa para a migração: app e serviço da Apple, iPhones que já
confiaram neste PC, energia, ação da tampa do notebook e programas que disputam o iPhone.

Só coleta fatos; quem decide se é OK, aviso ou bloqueio é checagens.checar_sistema().
Tudo tem fallback: se uma leitura falhar, o valor fica None e a checagem vira aviso.
"""
import ctypes
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW

# Processos que, abertos, podem iniciar backup ou sincronização sozinhos ao ver o iPhone
# e disputar a conexão com o programa.
CONCORRENTES = {
    "itunes.exe": "iTunes",
    "appledevices.exe": "Apple Devices (janela do app)",
    "3utools.exe": "3uTools",
    "imazing.exe": "iMazing",
    "imazing hd.exe": "iMazing",
    "copytrans.exe": "CopyTrans",
    "copytransshelbee.exe": "CopyTrans Shelbee",
    "drfonetoolkit.exe": "Dr.Fone",
    "wondershare dr.fone.exe": "Dr.Fone",
    "itools.exe": "iTools",
    "anytrans.exe": "AnyTrans",
    "tenorshare icarefone.exe": "Tenorshare iCareFone",
    "icarefone.exe": "Tenorshare iCareFone",
    "imyfone.exe": "iMyFone",
}
# Processo do serviço que conversa com iPhones (iTunes clássico ou app Apple Devices).
SERVICOS_APPLE = ("applemobiledeviceservice.exe", "applemobiledeviceprocess.exe")

ACOES_TAMPA = {0: "não fazer nada", 1: "suspender", 2: "hibernar", 3: "desligar"}


@dataclass
class SistemaPC:
    windows: str = ""
    build: int | None = None
    apple_devices: str | None = None   # versão do app Apple Devices, se instalado
    itunes: bool = False
    servico_rodando: bool | None = None
    usbmux_ok: bool = False
    confiaveis: list[str] = field(default_factory=list)  # UDIDs com registro de confiança
    na_tomada: bool | None = None
    tem_bateria: bool | None = None
    bateria_pct: int | None = None
    tampa_ca: int | None = None
    tampa_bateria: int | None = None
    concorrentes: list[str] = field(default_factory=list)

    def para_log(self) -> dict:
        d = asdict(self)
        d["confiaveis"] = len(self.confiaveis)
        return d


def ler(usbmux_ok: bool) -> SistemaPC:
    s = SistemaPC(usbmux_ok=usbmux_ok)
    s.windows, s.build = _windows()
    for nome, f in (("apple_devices", _apple_devices), ("itunes", _itunes), ("confiaveis", _confiaveis)):
        try:
            setattr(s, nome, f())
        except Exception:
            log.exception("falha lendo %s", nome)
    try:
        processos = _processos()
        s.servico_rodando = any(p in processos for p in SERVICOS_APPLE)
        s.concorrentes = sorted({rotulo for exe, rotulo in CONCORRENTES.items() if exe in processos})
    except Exception:
        log.exception("falha lendo processos")
    try:
        s.na_tomada, s.tem_bateria, s.bateria_pct = _energia()
    except Exception:
        log.exception("falha lendo energia")
    if s.tem_bateria:
        try:
            s.tampa_ca, s.tampa_bateria = _acao_tampa()
        except Exception:
            log.exception("falha lendo ação da tampa")
    log.info("sistema: %s", s.para_log())
    return s


def _windows() -> tuple[str, int | None]:
    if sys.platform != "win32":
        return platform.platform(), None
    v = sys.getwindowsversion()
    nome = "Windows 11" if v.build >= 22000 else f"Windows {platform.release()}"
    return f"{nome} (build {v.build})", v.build


def _rodar(comando: list[str], timeout: float = 20) -> str:
    # stdin explícito: no .exe sem console, herdar um stdin inexistente faz o subprocess falhar.
    r = subprocess.run(comando, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                       creationflags=SEM_JANELA)
    for cod in ("utf-8", "cp850", "cp1252"):
        try:
            return r.stdout.decode(cod)
        except UnicodeDecodeError:
            continue
    return r.stdout.decode("utf-8", errors="replace")


def _apple_devices() -> str | None:
    if sys.platform != "win32":
        return None
    saida = _rodar(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-AppxPackage -Name AppleInc.AppleDevices).Version"]).strip()
    return saida.splitlines()[0].strip() if saida else None


def _itunes() -> bool:
    if sys.platform != "win32":
        return False
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (Path(base) / "iTunes" / "iTunes.exe").exists():
            return True
    # iTunes da Microsoft Store
    saida = _rodar(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-AppxPackage -Name AppleInc.iTunes).Version"]).strip()
    return bool(saida)


def pasta_confianca() -> Path:
    return Path(os.environ.get("ALLUSERSPROFILE", r"C:\ProgramData")) / "Apple" / "Lockdown"


def _confiaveis() -> list[str]:
    """Cada iPhone que já tocou em "Confiar" para este PC deixa um <UDID>.plist aqui."""
    pasta = pasta_confianca()
    if not pasta.is_dir():
        return []
    return sorted(p.stem for p in pasta.glob("*.plist") if p.stem != "SystemConfiguration")


def _processos() -> set[str]:
    if sys.platform != "win32":
        return set()
    saida = _rodar(["tasklist.exe", "/FO", "CSV", "/NH"])
    return {linha.split('","')[0].strip('"').lower() for linha in saida.splitlines() if linha.startswith('"')}


class _StatusEnergia(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _energia() -> tuple[bool | None, bool | None, int | None]:
    if sys.platform != "win32":
        return None, None, None
    st = _StatusEnergia()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None, None, None
    na_tomada = {0: False, 1: True}.get(st.ACLineStatus)
    tem_bateria = None if st.BatteryFlag == 255 else not (st.BatteryFlag & 128)
    pct = st.BatteryLifePercent if st.BatteryLifePercent <= 100 else None
    return na_tomada, tem_bateria, pct


def _acao_tampa() -> tuple[int | None, int | None]:
    """powercfg mostra dois índices no fim da saída: na tomada (CA) e na bateria (CC)."""
    saida = _rodar(["powercfg.exe", "/q", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION"])
    valores = re.findall(r":\s*0x([0-9a-fA-F]{8})\s*$", saida, flags=re.MULTILINE)
    if len(valores) < 2:
        return None, None
    return int(valores[-2], 16), int(valores[-1], 16)


def pasta_sincronizada(pasta: Path) -> str | None:
    """Nome do serviço de nuvem se a pasta estiver dentro de uma pasta sincronizada."""
    alvo = str(pasta.resolve()).lower()
    candidatos = {
        "OneDrive": [os.environ.get(v) for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")],
        "Dropbox": [str(Path.home() / "Dropbox")],
        "Google Drive": [str(Path.home() / "Google Drive"), str(Path.home() / "My Drive")],
        "iCloud Drive": [str(Path.home() / "iCloudDrive")],
    }
    for servico, caminhos in candidatos.items():
        for c in caminhos:
            if c and (alvo == c.lower() or alvo.startswith(c.lower().rstrip("\\/") + os.sep)):
                return servico
    if "onedrive" in alvo.split(os.sep)[1:3] or any(p.startswith("onedrive") for p in alvo.split(os.sep)[:4]):
        return "OneDrive"
    return None


def pasta_gravavel(pasta: Path) -> bool:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        teste = pasta / ".teste-escrita-migrador"
        teste.write_bytes(b"ok")
        teste.unlink()
        return True
    except OSError:
        return False


def disco_removivel(pasta: Path) -> bool:
    if sys.platform != "win32":
        return False
    raiz = Path(pasta).resolve().anchor
    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(raiz)) == 2  # DRIVE_REMOVABLE
