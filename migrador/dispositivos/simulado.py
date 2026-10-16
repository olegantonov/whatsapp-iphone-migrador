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

from migrador.dispositivos.base import PRECISA_CONFIAR, Aviso, Cancelado, ErroAparelho, Progresso
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
    "dois_whatsapps": "WhatsApp e WhatsApp Business no iPhone antigo",
    "so_business": "Só o WhatsApp Business no iPhone antigo",
    "buscar_ativo": "\"Buscar iPhone\" ligado no iPhone novo",
    "queda_backup": "Cabo desconecta no meio do backup",
    "backup_corrompido": "Backup sai incompleto (arquivo faltando)",
    "ja_criptografado": "iPhone antigo já tem senha de backup (senha: 1234)",
}

COMUM, BUSINESS = "net.whatsapp.WhatsApp", "net.whatsapp.WhatsAppSMB"

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
        self._restaurado = False
        self._consultas_destino = 0

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

    def _bundles(self) -> list[str]:
        if self.cenario == "sem_whatsapp":
            return []
        if self.cenario == "so_business":
            return [BUSINESS]
        if self.cenario == "dois_whatsapps":
            return [COMUM, BUSINESS]
        return [COMUM]

    def whatsapps(self, udid: str) -> list[InfoWhatsApp]:
        if udid != UDID_ORIGEM:
            return []
        tamanhos = {COMUM: 200 * GB, BUSINESS: 30 * GB}
        return [InfoWhatsApp(bundle=b, tamanho=tamanhos[b]) for b in self._bundles()]

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

    def fazer_backup(self, udid: str, pasta: Path, progresso: Progresso, cancelar: threading.Event,
                     aviso: Aviso | None = None) -> None:
        passos = 60
        for i in range(passos + 1):
            if cancelar.is_set():
                raise Cancelado()
            # Como no iPhone real: o backup com senha pode pedir o código de desbloqueio no começo.
            if aviso and i == 1:
                aviso("O iPhone antigo está pedindo o código de desbloqueio: digite-o no iPhone para o backup "
                      "continuar.")
            elif aviso and i == 4:
                aviso("")
            if self.cenario == "queda_backup" and self._quedas == 0 and i == passos * 45 // 100:
                self._quedas += 1
                raise ErroAparelho("A conexão com o iPhone caiu.",
                                   "Confira o cabo, desbloqueie o iPhone e tente de novo.")
            progresso(i * 100 / passos)
            self._espera(0.2)
        gerar_backup_falso(pasta / udid, ios="26.6", senha=self._senha, bundles=self._bundles())
        if self.cenario == "backup_corrompido":
            _apagar_um_arquivo(pasta / udid)

    def whatsapps_no_destino(self, udid: str) -> dict[str, dict]:
        if not self._restaurado:
            return {}
        self._consultas_destino += 1
        if self._consultas_destino == 1:
            return {b: {"estado": "instalando"} for b in self._bundles()}
        dados = {COMUM: 196 * GB, BUSINESS: 29 * GB}
        return {b: {"estado": "instalado", "versao": "26.20.1", "dados": dados[b]} for b in self._bundles()}

    def ler_sistema(self):
        """No treino, mostra o PC de verdade; nos testes (modo rápido), um PC ideal e previsível."""
        from migrador import sistema
        if _rapido():
            return sistema.SistemaPC(windows="Windows 11 (build 26200)", build=26200, apple_devices="1.0",
                                     servico_rodando=True, usbmux_ok=True, confiaveis=[UDID_ORIGEM],
                                     na_tomada=True, tem_bateria=False)
        return sistema.ler(True)

    def restaurar(self, udid_destino: str, pasta: Path, udid_origem: str, senha: str, progresso: Progresso) -> None:
        if not (pasta / udid_origem / "Manifest.plist").exists():
            raise ErroAparelho("Backup não encontrado na pasta indicada.")
        # Como o iPhone: só aceita a senha com que o backup foi feito.
        if self._senha and senha != self._senha:
            raise ErroAparelho("A senha do backup não foi aceita pelo iPhone.", "Confira a senha do backup.")
        if self.cenario == "buscar_ativo":
            raise ErroAparelho("O \"Buscar iPhone\" está ativado no iPhone novo.",
                               "No iPhone novo: Ajustes > [seu nome] > Buscar > Buscar iPhone > desativar.")
        for i in range(51):
            progresso(i * 2)
            self._espera(0.2)
        self._restaurado = True


# ---------------------------------------------------------------------------
# Gerador de backup falso (também usado pelos testes)
# ---------------------------------------------------------------------------

def _mbfile(relative_path: str, tamanho: int, chave: bytes | None = None) -> bytes:
    """Metadado de arquivo no formato NSKeyedArchiver usado pelo Manifest.db."""
    agora = int(time.time())
    objetos = [
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
    ]
    if chave is not None:
        objetos[1]["EncryptionKey"] = plistlib.UID(4)
        objetos.append({"NS.data": chave, "$class": plistlib.UID(5)})
        objetos.append({"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]})
    return plistlib.dumps(
        {"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": plistlib.UID(1)}, "$objects": objetos},
        fmt=plistlib.FMT_BINARY,
    )


class _Cifra:
    """Cifra como o iPhone: chave derivada da senha, chaves de classe embrulhadas no keybag e
    uma chave AES por arquivo (AES-256-CBC, IV zero, preenchimento PKCS7). Iterações baixas para
    os testes serem rápidos; o formato é o mesmo de um backup real."""

    CLASSE = 3

    def __init__(self, senha: str):
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap
        self._wrap = aes_key_wrap
        self.salt, self.dpsl = os.urandom(20), os.urandom(20)
        self.iter, self.dpic = 1, 1
        derivada = hashlib.pbkdf2_hmac("sha256", senha.encode(), self.dpsl, self.dpic, 32)
        self.chave_senha = hashlib.pbkdf2_hmac("sha1", derivada, self.salt, self.iter, 32)
        self.chave_classe = os.urandom(32)

    def keybag(self) -> bytes:
        import struct

        def tlv(tag: bytes, valor) -> bytes:
            dados = struct.pack(">I", valor) if isinstance(valor, int) else valor
            return tag + struct.pack(">I", len(dados)) + dados

        raiz = [tlv(b"VERS", 4), tlv(b"TYPE", 1), tlv(b"UUID", os.urandom(16)), tlv(b"HMCK", os.urandom(40)),
                tlv(b"WRAP", 0), tlv(b"SALT", self.salt), tlv(b"ITER", self.iter), tlv(b"DPWT", 1),
                tlv(b"DPIC", self.dpic), tlv(b"DPSL", self.dpsl)]
        classe = [tlv(b"UUID", os.urandom(16)), tlv(b"CLAS", self.CLASSE), tlv(b"WRAP", 3), tlv(b"KTYP", 0),
                  tlv(b"WPKY", self._wrap(self.chave_senha, self.chave_classe))]
        return b"".join(raiz + classe)

    def chave_embrulhada(self, chave: bytes) -> bytes:
        return self.CLASSE.to_bytes(4, "little") + self._wrap(self.chave_classe, chave)

    @staticmethod
    def cifrar(chave: bytes, dados: bytes) -> bytes:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        preenchedor = padding.PKCS7(128).padder()
        dados = preenchedor.update(dados) + preenchedor.finalize()
        c = Cipher(algorithms.AES(chave), modes.CBC(b"\x00" * 16)).encryptor()
        return c.update(dados) + c.finalize()


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"


def _banco_whatsapp(conversas: int, mensagens: int, midias: list[str]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "ChatStorage.sqlite"
        con = sqlite3.connect(caminho)
        con.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME TEXT, ZCONTACTJID TEXT)")
        con.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZTEXT TEXT, "
                    "ZMESSAGEDATE REAL, ZMEDIAITEM INTEGER)")
        # Como no WhatsApp: o caminho do anexo é relativo à pasta Message/ do grupo de apps.
        con.execute("CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH TEXT)")
        rnd = random.Random(42)
        for c in range(1, conversas + 1):
            con.execute("INSERT INTO ZWACHATSESSION VALUES (?,?,?)", (c, f"Contato {c}", f"55119{c:08d}@s.whatsapp.net"))
        for m in range(1, mensagens + 1):
            midia = m if m <= len(midias) else None
            con.execute("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?)",
                        (m, rnd.randint(1, conversas), f"mensagem {m}", 700000000 + m, midia))
        for i, relativo in enumerate(midias, start=1):
            con.execute("INSERT INTO ZWAMEDIAITEM VALUES (?,?,?)", (i, i, relativo.removeprefix("Message/")))
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
    senha: str | None = None,
    bundles: list[str] | None = None,
) -> None:
    """Backup no formato da Apple. Com `senha`, sai criptografado de verdade (como no iPhone).
    `bundles` põe mais de um WhatsApp (comum e Business); sem ele vale `bundle`."""
    if bundles is None:
        bundles = [bundle] if bundle else []
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos: list[tuple[str, str, bytes]] = [
        ("HomeDomain", "Library/Preferences/com.apple.springboard.plist", b"x" * 300),
        ("HomeDomain", "Library/SMS/sms.db", os.urandom(2048)),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.HEIC", os.urandom(5000)),
    ]
    for n, bundle in enumerate(bundles):
        # O Business simulado é menor, para os números dos dois não se confundirem nos testes.
        fator = 1 if n == 0 else 2
        grupo = f"AppDomainGroup-group.{bundle}.shared"
        caminhos = [f"Message/Media/5511900000000@s.whatsapp.net/a/b/foto{i}.jpg" for i in range(midias // fator)]
        arquivos.append((grupo, "ChatStorage.sqlite",
                         _banco_whatsapp(conversas // fator, mensagens // fator, caminhos)))
        for i, relativo in enumerate(caminhos):
            arquivos.append((grupo, relativo, JPEG + os.urandom(1000 + i)))
        arquivos.append((f"AppDomain-{bundle}", f"Library/Preferences/{bundle}.plist", b"p" * 120))

    cifra = _Cifra(senha) if senha else None
    db_path = pasta / "Manifest.db"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    con.execute("CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB)")
    for dominio, relativo, conteudo in arquivos:
        file_id = hashlib.sha1(f"{dominio}-{relativo}".encode()).hexdigest()
        destino = pasta / file_id[:2] / file_id
        destino.parent.mkdir(exist_ok=True)
        chave = None
        if cifra:
            chave_arquivo = os.urandom(32)
            chave = cifra.chave_embrulhada(chave_arquivo)
            destino.write_bytes(cifra.cifrar(chave_arquivo, conteudo))
        else:
            destino.write_bytes(conteudo)
        con.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                    (file_id, dominio, relativo, 1, _mbfile(relativo, len(conteudo), chave)))
    con.commit()
    con.close()

    manifesto = {
        "IsEncrypted": bool(cifra), "Version": "10.0", "Date": datetime.now(), "WasPasscodeSet": True,
        "Lockdown": {"ProductVersion": ios, "ProductType": "iPhone13,2", "DeviceName": "iPhone de Maria",
                     "UniqueDeviceID": pasta.name},
    }
    if cifra:
        chave_manifesto = os.urandom(32)
        db_path.write_bytes(cifra.cifrar(chave_manifesto, db_path.read_bytes()))
        manifesto["BackupKeyBag"] = cifra.keybag()
        manifesto["ManifestKey"] = cifra.chave_embrulhada(chave_manifesto)

    agora = datetime.now()
    (pasta / "Info.plist").write_bytes(plistlib.dumps({
        "Device Name": "iPhone de Maria", "Product Type": "iPhone13,2", "Product Version": ios,
        "Last Backup Date": agora, "Unique Identifier": pasta.name,
    }))
    (pasta / "Manifest.plist").write_bytes(plistlib.dumps(manifesto))
    (pasta / "Status.plist").write_bytes(plistlib.dumps({
        "BackupState": "new", "SnapshotState": "finished", "IsFullBackup": True, "Version": "3.3",
        "Date": agora, "UUID": "SIMULADO",
    }, fmt=plistlib.FMT_BINARY))


def _apagar_um_arquivo(pasta: Path) -> None:
    for sub in sorted(pasta.iterdir()):
        if sub.is_dir() and len(sub.name) == 2:
            next(sub.iterdir()).unlink()
            return
