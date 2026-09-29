import os
import sys
from pathlib import Path

APP_NOME = "Migrador de WhatsApp para iPhone"
REPOSITORIO = "olegantonov/whatsapp-iphone-migrador"
NOME_EXECUTAVEL = "MigradorWhatsApp.exe"
NOME_PACOTE_RELEASE = "MigradorWhatsApp-win64.zip"
NOME_PASTA_BACKUP = "MigradorWhatsApp-Backup"

# Folga exigida além do tamanho estimado do backup, no PC e no iPhone de destino.
FOLGA_PC_BYTES = 10 * 1024**3
FOLGA_DESTINO_BYTES = 5 * 1024**3
BATERIA_MINIMA = 30


def pasta_dados() -> Path:
    """Pasta com estado, logs e senha protegida. Fica fora da pasta do programa
    para sobreviver às atualizações automáticas."""
    base = os.environ.get("MIGRADOR_DADOS")
    if base:
        caminho = Path(base)
    else:
        caminho = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MigradorWhatsApp"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def empacotado() -> bool:
    return bool(getattr(sys, "frozen", False))


def pasta_programa() -> Path:
    if empacotado():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def recurso(nome: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", pasta_programa()))
    return base / "assets" / nome
