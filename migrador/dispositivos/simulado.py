"""iPhones de mentira para treinar o passo a passo e testar o programa sem aparelhos.

O backup gerado tem a mesma estrutura de um backup real do iPhone (Manifest.db,
pastas por hash, banco ChatStorage.sqlite do WhatsApp), então a verificação roda
exatamente o mesmo código usado com aparelhos reais.
"""
import hashlib
import logging
import os
import plistlib
import random
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from migrador.dispositivos.base import PRECISA_CONFIAR, Cancelado, ErroAparelho, Progresso
from migrador.modelos import Aparelho, InfoWhatsApp

log = logging.getLogger(__name__)

GB = 1024**3
UDID_ORIGEM = "00008101-SIMULADO0000ORIG"
UDID_DESTINO = "00008140-SIMULADO0000DEST"

CENARIOS = {
    "normal": "Tudo certo do começo ao fim",
    "confiar": "iPhone novo ainda não confiou no PC",
    "destino_desatualizado": "iPhone novo com iOS mais antigo que o velho",
    "sem_espaco_destino": "iPhone novo sem espaço para o backup",
    "sem_whatsapp": "iPhone antigo sem WhatsApp",
    "buscar_ativo": "\"Buscar iPhone\" ligado no iPhone novo",
    "queda_backup": "Cabo desconecta no meio do backup",
    "backup_corrompido": "Backup sai incompleto (arquivo faltando)",
    "ja_criptografado": "iPhone antigo já tem senha de backup (senha: 1234)",
}

ULTIMAS_VERSOES = {"iPhone13,2": "27.0.1", "iPhone17,1": "27.0.1"}


def _rapido() -> bool:
    return os.environ.get("MIGRADOR_SIM_RAPIDO") == "1"


class BackendSimulado:
    simulado = True

    def __init__(self, cenario: str = "normal"):
        self.cenario = cenario if cenario in CENARIOS else "normal"
        self._senha = "1234" if self.cenario == "ja_criptografado" else None
        self._listagens = 0
        self._quedas = 0

    def ultimas_versoes(self) -> dict[str, str]:
        return dict(ULTIMAS_VERSOES)

    def _espera(self, segundos: float) -> None:
        if not _rapido():
            time.sleep(segundos)

    def servico_apple_ok(self) -> bool:
        return True

    def listar(self) -> list[Aparelho]:
        self._espera(0.8)
        self._listagens += 1
        origem = Aparelho(
            udid=UDID_ORIGEM, nome="iPhone de Maria", tipo="iPhone13,2", ios="26.6",
            capacidade=256 * GB, livre=25 * GB, bateria=78, buscar_ativo=True,
        )
        destino = Aparelho(
            udid=UDID_DESTINO, nome="iPhone", tipo="iPhone17,1", ios="27.0.1",
            capacidade=512 * GB, livre=498 * GB, bateria=64, buscar_ativo=False,
        )
        if self.cenario == "destino_desatualizado":
            destino.ios = "26.2"
        elif self.cenario == "sem_espaco_destino":
            destino.capacidade, destino.livre = 128 * GB, 110 * GB
        elif self.cenario == "buscar_ativo":
            destino.buscar_ativo = True
        elif self.cenario == "confiar" and self._listagens == 1:
            destino = Aparelho(udid=UDID_DESTINO, erro=PRECISA_CONFIAR)
        return [origem, destino]

    def whatsapp(self, udid: str) -> InfoWhatsApp | None:
        if udid != UDID_ORIGEM or self.cenario == "sem_whatsapp":
            return None
        return InfoWhatsApp(bundle="net.whatsapp.WhatsApp", tamanho=200 * GB)

    def criptografia_ativa(self, udid: str) -> bool:
        return self._senha is not None

    def ativar_criptografia(self, udid: str, senha: str) -> None:
        self._espera(1)
        self._senha = senha

    def validar_senha(self, udid: str, senha: str) -> None:
        self._espera(1)
        if senha != self._senha:
            raise ErroAparelho("A senha do backup não foi aceita pelo iPhone.",
                               "Confira a senha do backup e tente de novo.")

    def fazer_backup(self, udid: str, pasta: Path, progresso: Progresso, cancelar: threading.Event) -> None:
        passos = 60
        for i in range(passos + 1):
            if cancelar.is_set():
                raise Cancelado()
            if self.cenario == "queda_backup" and self._quedas == 0 and i == passos * 45 // 100:
                self._quedas += 1
                raise ErroAparelho("A conexão com o iPhone caiu.",
                                   "Confira o cabo, desbloqueie o iPhone e tente de novo.")
            progresso(i * 100 / passos)
            self._espera(0.2)
        gerar_backup_falso(pasta / udid, ios="26.6")
        if self.cenario == "backup_corrompido":
            _apagar_um_arquivo(pasta / udid)

    def restaurar(self, udid_destino: str, pasta: Path, udid_origem: str, senha: str, progresso: Progresso) -> None:
        if not (pasta / udid_origem / "Manifest.plist").exists():
            raise ErroAparelho("Backup não encontrado na pasta indicada.")
        if self.cenario == "buscar_ativo":
            raise ErroAparelho("O \"Buscar iPhone\" está ativado no iPhone novo.",
                               "No iPhone novo: Ajustes > [seu nome] > Buscar > Buscar iPhone > desativar.")
        for i in range(51):
            progresso(i * 2)
            self._espera(0.2)


# ---------------------------------------------------------------------------
# Gerador de backup falso (também usado pelos testes)
# ---------------------------------------------------------------------------

def _mbfile(relative_path: str, tamanho: int) -> bytes:
    """Metadado de arquivo no formato NSKeyedArchiver usado pelo Manifest.db."""
    agora = int(time.time())
    return plistlib.dumps(
        {
            "$version": 100000,
            "$archiver": "NSKeyedArchiver",
            "$top": {"root": plistlib.UID(1)},
            "$objects": [
                "$null",
                {
                    "$class": plistlib.UID(3),
                    "RelativePath": plistlib.UID(2),
                    "LastModified": agora, "LastStatusChange": agora, "Birth": agora,
                    "Size": tamanho, "Mode": 0o100644, "GroupID": 501, "UserID": 501,
                    "InodeNumber": 1, "Flags": 0, "ProtectionClass": 3,
                },
                relative_path,
                {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
            ],
        },
        fmt=plistlib.FMT_BINARY,
    )


def _banco_whatsapp(conversas: int, mensagens: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "ChatStorage.sqlite"
        con = sqlite3.connect(caminho)
        con.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME TEXT, ZCONTACTJID TEXT)")
        con.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZTEXT TEXT, "
                    "ZMESSAGEDATE REAL)")
        rnd = random.Random(42)
        for c in range(1, conversas + 1):
            con.execute("INSERT INTO ZWACHATSESSION VALUES (?,?,?)", (c, f"Contato {c}", f"55119{c:08d}@s.whatsapp.net"))
        for m in range(1, mensagens + 1):
            con.execute("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?)",
                        (m, rnd.randint(1, conversas), f"mensagem {m}", 700000000 + m))
        con.commit()
        con.close()
        return caminho.read_bytes()


def gerar_backup_falso(
    pasta: Path,
    ios: str = "26.6",
    bundle: str | None = "net.whatsapp.WhatsApp",
    conversas: int = 12,
    mensagens: int = 480,
    midias: int = 40,
) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos: list[tuple[str, str, bytes]] = [
        ("HomeDomain", "Library/Preferences/com.apple.springboard.plist", b"x" * 300),
        ("HomeDomain", "Library/SMS/sms.db", os.urandom(2048)),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.HEIC", os.urandom(5000)),
    ]
    if bundle:
        grupo = f"AppDomainGroup-group.{bundle}.shared"
        arquivos.append((grupo, "ChatStorage.sqlite", _banco_whatsapp(conversas, mensagens)))
        for i in range(midias):
            arquivos.append((grupo, f"Message/Media/5511900000000@s.whatsapp.net/a/b/foto{i}.jpg",
                             os.urandom(1000 + i)))
        arquivos.append((f"AppDomain-{bundle}", "Library/Preferences/net.whatsapp.WhatsApp.plist", b"p" * 120))

    db_path = pasta / "Manifest.db"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    con.execute("CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB)")
    for dominio, relativo, conteudo in arquivos:
        file_id = hashlib.sha1(f"{dominio}-{relativo}".encode()).hexdigest()
        destino = pasta / file_id[:2] / file_id
        destino.parent.mkdir(exist_ok=True)
        destino.write_bytes(conteudo)
        con.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                    (file_id, dominio, relativo, 1, _mbfile(relativo, len(conteudo))))
    con.commit()
    con.close()

    agora = datetime.now()
    (pasta / "Info.plist").write_bytes(plistlib.dumps({
        "Device Name": "iPhone de Maria", "Product Type": "iPhone13,2", "Product Version": ios,
        "Last Backup Date": agora, "Unique Identifier": pasta.name,
    }))
    (pasta / "Manifest.plist").write_bytes(plistlib.dumps({
        "IsEncrypted": False, "Version": "10.0", "Date": agora, "WasPasscodeSet": True,
        "Lockdown": {"ProductVersion": ios, "ProductType": "iPhone13,2", "DeviceName": "iPhone de Maria",
                     "UniqueDeviceID": pasta.name},
    }))
    (pasta / "Status.plist").write_bytes(plistlib.dumps({
        "BackupState": "new", "SnapshotState": "finished", "IsFullBackup": True, "Version": "3.3",
        "Date": agora, "UUID": "SIMULADO",
    }, fmt=plistlib.FMT_BINARY))


def _apagar_um_arquivo(pasta: Path) -> None:
    for sub in sorted(pasta.iterdir()):
        if sub.is_dir() and len(sub.name) == 2:
            next(sub.iterdir()).unlink()
            return
