"""Conversa com iPhones de verdade pelo USB, usando o pymobiledevice3.

Precisa do serviço de dispositivos móveis da Apple instalado no Windows
(vem com o app "Apple Devices" da Microsoft Store ou com o iTunes).
"""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import (
    ConnectionFailedToUsbmuxdError,
    ConnectionTerminatedError,
    DeviceNotFoundError,
    NoDeviceConnectedError,
    NotEnoughDiskSpaceError,
    PairingDialogResponsePendingError,
    PasscodeRequiredError,
    PasswordRequiredError,
    PyMobileDevice3Exception,
    UserDeniedPairingError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service

from migrador.dispositivos.base import (
    PRECISA_CONFIAR,
    PRECISA_DESBLOQUEAR,
    Aviso,
    Cancelado,
    ErroAparelho,
    Progresso,
)
from migrador.modelos import WHATSAPP_BUNDLES, Aparelho, InfoWhatsApp, formatar_bytes

log = logging.getLogger(__name__)

APPLE_DEVICES = (
    "Instale o app \"Apple Devices\" pela Microsoft Store (ou o iTunes) e abra-o uma vez. "
    "Depois clique em \"Procurar iPhones\" de novo."
)
RECONECTAR = (
    "Confira o cabo, desbloqueie o iPhone e tente de novo."
)


def traduzir(e: BaseException) -> ErroAparelho:
    if isinstance(e, ErroAparelho):
        return e
    if isinstance(e, ConnectionFailedToUsbmuxdError):
        return ErroAparelho("O serviço da Apple para iPhone não está rodando neste PC.", APPLE_DEVICES)
    if isinstance(e, PairingDialogResponsePendingError):
        return ErroAparelho("O iPhone está perguntando se deve confiar neste computador.",
                            "Desbloqueie o iPhone, toque em \"Confiar\" e digite o código.")
    if isinstance(e, UserDeniedPairingError):
        return ErroAparelho("O iPhone recusou a conexão (foi tocado \"Não Confiar\").",
                            "Desconecte e conecte o cabo de novo e toque em \"Confiar\".")
    if isinstance(e, (PasswordRequiredError, PasscodeRequiredError)):
        return ErroAparelho("O iPhone está bloqueado.", "Desbloqueie o iPhone e tente de novo.")
    if isinstance(e, NotEnoughDiskSpaceError):
        return ErroAparelho("Falta espaço para concluir a operação.", str(e))
    if isinstance(e, (ConnectionTerminatedError, NoDeviceConnectedError, DeviceNotFoundError,
                      ConnectionError, asyncio.IncompleteReadError, EOFError)):
        return ErroAparelho("A conexão com o iPhone caiu.", RECONECTAR)
    texto = str(e)
    if isinstance(e, PyMobileDevice3Exception) and "Device link error" in texto:
        baixo = texto.lower()
        if "find my" in baixo or "findmy" in baixo or "fmip" in baixo:
            return ErroAparelho("O \"Buscar iPhone\" está ativado no iPhone novo.",
                                "No iPhone novo: Ajustes > [seu nome] > Buscar > Buscar iPhone > desativar.")
        if "no space left on device" in baixo or "code=28" in baixo:
            # O PC nunca repassa ao iPhone um "sem espaço" ao gravar (isso viraria erro aqui no
            # programa): este código 28 vem de dentro do próprio iPhone, enquanto ele prepara a cópia.
            return ErroAparelho(
                "O iPhone antigo avisou que ficou sem espaço enquanto preparava o backup.",
                "Reinicie o iPhone antigo (desligar e ligar de novo) e confira em Ajustes > Geral > "
                "Armazenamento do iPhone se há espaço livre. Depois tente de novo. Se o erro voltar, "
                "clique em \"Enviar diagnóstico ao suporte\".")
        if "password" in baixo or "passcode" in baixo:
            return ErroAparelho("A senha do backup não foi aceita pelo iPhone.",
                                "Confira a senha do backup e tente de novo.")
        return ErroAparelho("O iPhone recusou a operação.", texto)
    return ErroAparelho(f"Erro inesperado: {texto or type(e).__name__}",
                        "Tente de novo. Se persistir, envie o arquivo de registro (log) para o suporte.")


def _rodar(coro):
    try:
        return asyncio.run(coro)
    except Cancelado:
        raise
    except Exception as e:
        log.exception("falha na comunicação com o iPhone")
        raise traduzir(e) from e


async def _conectar(udid: str, pair_timeout: float | None = 60):
    return await create_using_usbmux(serial=udid, autopair=True, pair_timeout=pair_timeout)


class BackendReal:
    simulado = False
    # Resumo do último backup (mensagens e bytes que chegaram do iPhone), para o evento de falha.
    ultima_atividade: dict | None = None

    def servico_apple_ok(self) -> bool:
        try:
            asyncio.run(usbmux.list_devices())
            return True
        except Exception:
            return False

    def listar(self) -> list[Aparelho]:
        return _rodar(self._listar())

    async def _listar(self) -> list[Aparelho]:
        vistos, aparelhos = set(), []
        for d in await usbmux.list_devices():
            if not d.is_usb or d.serial in vistos:
                continue
            vistos.add(d.serial)
            aparelhos.append(await self._ler(d.serial))
        return aparelhos

    async def _ler(self, udid: str) -> Aparelho:
        ap = Aparelho(udid=udid)
        try:
            ld = await _conectar(udid, pair_timeout=5)
        except PairingDialogResponsePendingError:
            ap.erro = PRECISA_CONFIAR
            return ap
        except (PasswordRequiredError, PasscodeRequiredError):
            ap.erro = PRECISA_DESBLOQUEAR
            return ap
        except Exception as e:
            log.exception("falha lendo %s", udid)
            ap.erro = traduzir(e).mensagem
            return ap
        try:
            v = ld.all_values
            ap.nome = v.get("DeviceName", "")
            ap.tipo = v.get("ProductType", "")
            ap.ios = v.get("ProductVersion", "")
            disco = await _valor(ld, "com.apple.disk_usage")
            if isinstance(disco, dict):
                # Registro completo: no iOS 27 o "TotalDataAvailable" já veio muito acima do real.
                log.info("armazenamento de %s: %s", udid,
                         {k: v for k, v in disco.items() if isinstance(v, (int, float, bool))})
                ap.capacidade = disco.get("TotalDataCapacity")
                ap.livre = disco.get("TotalDataAvailable")
            ap.bateria = await _valor(ld, "com.apple.mobile.battery", "BatteryCurrentCapacity")
            buscar = await _valor(ld, "com.apple.fmip", "IsAssociated")
            ap.buscar_ativo = bool(buscar) if buscar is not None else None
        finally:
            await ld.close()
        log.info("aparelho %s: %s iOS %s, capacidade %s, livre %s, bateria %s%%, Buscar %s",
                 udid, ap.tipo, ap.ios, ap.capacidade, ap.livre, ap.bateria, ap.buscar_ativo)
        return ap

    def whatsapps(self, udid: str) -> list[InfoWhatsApp]:
        """Todos os WhatsApps instalados (comum e Business podem estar juntos)."""
        return _rodar(self._whatsapps(udid))

    async def _whatsapps(self, udid: str) -> list[InfoWhatsApp]:
        ld = await _conectar(udid)
        try:
            apps = await InstallationProxyService(lockdown=ld).get_apps(
                application_type="User", calculate_sizes=True, bundle_identifiers=list(WHATSAPP_BUNDLES)
            )
        finally:
            await ld.close()
        encontrados = []
        for bundle in WHATSAPP_BUNDLES:
            if bundle in apps:
                info = apps[bundle]
                tamanho = (info.get("StaticDiskUsage") or 0) + (info.get("DynamicDiskUsage") or 0)
                encontrados.append(InfoWhatsApp(bundle=bundle, tamanho=tamanho or None))
        log.info("WhatsApps em %s: %s", udid, [(w.bundle, w.tamanho) for w in encontrados])
        return encontrados

    def whatsapps_no_destino(self, udid: str) -> dict[str, dict]:
        """Estado de cada WhatsApp no iPhone novo. Depois da restauração o iPhone reinstala os apps
        pela App Store: enquanto baixa, o app aparece só como "placeholder" (ícone esperando)."""
        async def f():
            ld = await _conectar(udid)
            try:
                ip = InstallationProxyService(lockdown=ld)
                instalados = await ip.get_apps(application_type="User", calculate_sizes=True,
                                               bundle_identifiers=list(WHATSAPP_BUNDLES))
                todos = await ip.get_apps(application_type="Any", calculate_sizes=False,
                                          bundle_identifiers=list(WHATSAPP_BUNDLES), show_placeholders=True)
            finally:
                await ld.close()
            r = {}
            for bundle in WHATSAPP_BUNDLES:
                if bundle in instalados:
                    info = instalados[bundle]
                    # Mesma medida usada no iPhone antigo (app + dados), para comparar igual com igual.
                    dados = (info.get("StaticDiskUsage") or 0) + (info.get("DynamicDiskUsage") or 0)
                    r[bundle] = {"estado": "instalado", "versao": info.get("CFBundleShortVersionString"),
                                 "dados": dados or None}
                elif bundle in todos:
                    r[bundle] = {"estado": "instalando"}
            return r
        return _rodar(f())

    def ler_sistema(self):
        from migrador import sistema
        return sistema.ler(self.servico_apple_ok())

    def criptografia_ativa(self, udid: str) -> bool:
        async def f():
            ld = await _conectar(udid)
            try:
                async with Mobilebackup2Service(ld) as mb:
                    return await mb.get_will_encrypt()
            finally:
                await ld.close()
        return _rodar(f())

    def ativar_criptografia(self, udid: str, senha: str) -> None:
        async def f():
            ld = await _conectar(udid)
            try:
                async with Mobilebackup2Service(ld) as mb:
                    await mb.change_password(new=senha)
            finally:
                await ld.close()
        _rodar(f())

    def validar_senha(self, udid: str, senha: str) -> None:
        # Trocar a senha por ela mesma: o iPhone só aceita se a senha atual estiver certa.
        async def f():
            ld = await _conectar(udid)
            try:
                async with Mobilebackup2Service(ld) as mb:
                    await mb.change_password(old=senha, new=senha)
            finally:
                await ld.close()
        _rodar(f())

    def fazer_backup(self, udid: str, pasta: Path, progresso: Progresso, cancelar: threading.Event,
                     aviso: Aviso | None = None) -> None:
        async def f():
            ld = await _conectar(udid)
            try:
                async with Mobilebackup2Service(ld) as mb:
                    atividade = _Atividade()
                    _repassar_avisos(mb, atividade)
                    _acompanhar_link(mb, atividade)
                    # full=False: se já houver um backup parcial/anterior na pasta, é incremental
                    # e aproveita o que já foi copiado.
                    tarefa = asyncio.create_task(
                        mb.backup(full=False, backup_directory=pasta, progress_callback=progresso)
                    )
                    texto, proximo_registro = None, time.monotonic() + REGISTRO_BACKUP
                    try:
                        while not tarefa.done():
                            if cancelar.is_set():
                                tarefa.cancel()
                                try:
                                    await tarefa
                                except (asyncio.CancelledError, Exception):
                                    pass
                                raise Cancelado()
                            if aviso and atividade.texto() != texto:
                                texto = atividade.texto()
                                try:
                                    aviso(texto)
                                except Exception:
                                    log.exception("falha ao mostrar aviso")
                            if time.monotonic() >= proximo_registro:
                                proximo_registro += REGISTRO_BACKUP
                                atividade.registrar()
                            await asyncio.sleep(0.3)
                        tarefa.result()
                    finally:
                        atividade.registrar()
                        BackendReal.ultima_atividade = atividade.resumo()
            finally:
                await ld.close()
        pasta.mkdir(parents=True, exist_ok=True)
        _rodar(f())

    def restaurar(self, udid_destino: str, pasta: Path, udid_origem: str, senha: str, progresso: Progresso) -> None:
        async def f():
            ld = await _conectar(udid_destino)
            try:
                async with Mobilebackup2Service(ld) as mb:
                    await mb.restore(
                        backup_directory=pasta,
                        source=udid_origem,
                        system=True,
                        reboot=True,
                        # O iPhone pediria uma cópia integral do backup no PC (+200 GB). A integridade
                        # já é garantida pela impressão digital conferida antes da restauração.
                        copy=False,
                        # No pymobiledevice3 este parâmetro é "RestorePreserveSettings": False faz
                        # restaurar os ajustes que vieram do aparelho antigo.
                        settings=False,
                        remove=True,
                        password=senha,
                        progress_callback=progresso,
                    )
            finally:
                await ld.close()
        _rodar(f())


# Avisos que o iPhone manda durante o backup e que pedem ação da pessoa.
AVISOS_BACKUP = {
    "com.apple.LocalAuthentication.ui.presented":
        "O iPhone antigo está pedindo o código de desbloqueio: digite-o no iPhone para o backup continuar.",
    "com.apple.LocalAuthentication.ui.dismissed": "",
    "com.apple.itunes-client.syncCancelRequest": "O backup foi cancelado no próprio iPhone.",
}
# De quanto em quanto tempo o log anota se o iPhone está mandando dados (segundos).
REGISTRO_BACKUP = 120
# Sem nenhuma mensagem do iPhone por este tempo, a tela pede para conferir o aparelho (segundos).
SILENCIO_LONGO = 10 * 60
# Depois de receber os arquivos o iPhone organiza a pasta no PC (mover, apagar, listar). Num backup
# de 200 GB isso levou mais de 3 horas, com a porcentagem parada e nenhum byte novo chegando.
COMANDOS_FINAIS = {"DLMessageMoveItems", "DLMessageMoveFiles", "DLMessageRemoveItems", "DLMessageRemoveFiles",
                   "DLContentsOfDirectory", "DLMessageCopyItem"}
# Sem bytes novos por este tempo e com um comando final, a tela explica a etapa final (segundos).
SEM_BYTES_FINAL = 60


class _Atividade:
    """O que o iPhone já mandou durante o backup. Antes do primeiro arquivo ele passa um tempo
    preparando os dados sem mandar porcentagem nenhuma: a tela fica em 0% e, sem isto, não dá
    para saber (nem na tela nem no log) se a conexão está viva ou parada."""

    def __init__(self):
        self.inicio = self.ultima = self.ultimo_byte = time.monotonic()
        self.comando = None
        self.mensagens = 0
        self.bytes = 0
        self.aviso_iphone = ""
        self._vistos: set[str] = set()

    def mensagem(self, comando) -> None:
        self.ultima = time.monotonic()
        self.mensagens += 1
        self.comando = comando
        if comando not in self._vistos:
            self._vistos.add(comando)
            log.info("backup: primeira mensagem %s do iPhone após %.0f s", comando, self.ultima - self.inicio)

    def recebido(self, n: int) -> None:
        self.ultima = self.ultimo_byte = time.monotonic()
        self.bytes += n

    def notificacao(self, nome) -> None:
        self.ultima = time.monotonic()
        if nome in AVISOS_BACKUP:
            self.aviso_iphone = AVISOS_BACKUP[nome]

    def texto(self) -> str:
        if self.aviso_iphone:
            return self.aviso_iphone
        silencio = time.monotonic() - self.ultima
        if silencio >= SILENCIO_LONGO:
            return (f"O iPhone antigo não manda nada há {silencio // 60:.0f} minutos. Confira se ele continua "
                    "conectado e desbloqueie a tela para ver se apareceu algum pedido.")
        if self.finalizando():
            return (f"Etapa final: os {formatar_bytes(self.bytes)} já chegaram e agora o iPhone antigo organiza "
                    "os arquivos no PC. Em backups grandes isso leva horas, mesmo com a porcentagem parada ou "
                    "em 100%. Não desconecte o cabo nem feche o programa até aparecer \"Backup concluído\".")
        if not self.bytes:
            return ("O iPhone antigo está preparando o backup. Antes de enviar o primeiro arquivo ele organiza "
                    "os dados, e isso pode levar vários minutos com a porcentagem parada em 0%.")
        return f"Copiando o conteúdo do iPhone antigo… {formatar_bytes(self.bytes)} recebidos."

    def finalizando(self) -> bool:
        return (bool(self.bytes) and self.comando in COMANDOS_FINAIS
                and time.monotonic() - self.ultimo_byte >= SEM_BYTES_FINAL)

    def resumo(self) -> dict:
        agora = time.monotonic()
        return {"mensagens": self.mensagens, "ultimo_comando": self.comando, "bytes": self.bytes,
                "silencio_s": round(agora - self.ultima), "comandos": sorted(self._vistos, key=str)}

    def registrar(self) -> None:
        agora = time.monotonic()
        log.info("backup: %.0f min, %d mensagens do iPhone, última (%s) há %.0f s, %s recebidos",
                 (agora - self.inicio) / 60, self.mensagens, self.comando, agora - self.ultima,
                 formatar_bytes(self.bytes))


def _repassar_avisos(mb: Mobilebackup2Service, atividade: _Atividade) -> None:
    """O pymobiledevice3 só registra estes avisos no log; aqui eles também vão para a tela."""
    original = getattr(mb, "_log_backup_notification", None)
    if original is None:
        log.warning("esta versão do pymobiledevice3 não expõe os avisos do backup")
        return

    def repassar(evento):
        original(evento)
        nome = evento.get("Name") if isinstance(evento, dict) else None
        log.info("aviso do iPhone durante o backup: %s", nome)
        atividade.notificacao(nome)

    mb._log_backup_notification = repassar


def _acompanhar_link(mb: Mobilebackup2Service, atividade: _Atividade) -> None:
    """Conta as mensagens e os bytes que chegam pelo canal do backup. O pymobiledevice3 só chama o
    progresso quando o iPhone já está transferindo arquivos."""
    original = getattr(mb, "device_link", None)
    if original is None:
        log.warning("esta versão do pymobiledevice3 não expõe o canal do backup")
        return

    @asynccontextmanager
    async def device_link(*args, **kwargs):
        async with original(*args, **kwargs) as dl:
            receber, ler = getattr(dl, "receive_message", None), getattr(dl, "_recvall", None)
            if receber is None or ler is None:
                log.warning("esta versão do pymobiledevice3 não expõe as mensagens do backup")
                yield dl
                return

            async def receive_message():
                mensagem = await receber()
                atividade.mensagem(mensagem[0] if mensagem else None)
                return mensagem

            async def _recvall(tamanho):
                dados = await ler(tamanho)
                atividade.recebido(len(dados))
                return dados

            dl.receive_message, dl._recvall = receive_message, _recvall
            handlers = getattr(dl, "_dl_handlers", None)
            if isinstance(handlers, dict) and "DLContentsOfDirectory" in handlers:
                handlers["DLContentsOfDirectory"] = _listar_pasta_rapido(dl)
            else:
                log.warning("esta versão do pymobiledevice3 não expõe a listagem de pastas do backup")
            yield dl

    mb.device_link = device_link


def _listar_pasta_rapido(dl):
    """Mesma resposta do pymobiledevice3 para DLContentsOfDirectory, com os.scandir. O original
    consulta cada arquivo quatro vezes; no Windows o scandir já traz tipo, tamanho e data, e numa
    pasta de backup com milhares de arquivos cada listagem levava ~25 s, enquanto o iPhone espera
    a resposta para continuar a etapa final."""
    from pymobiledevice3.safe_paths import resolve_device_path

    async def contents_of_directory(message) -> None:
        dados = {}
        with os.scandir(resolve_device_path(dl.root_path, message[1])) as entradas:
            for ent in entradas:
                st = ent.stat()
                tipo = ("DLFileTypeDirectory" if ent.is_dir() else
                        "DLFileTypeRegular" if ent.is_file() else "DLFileTypeUnknown")
                # O plistlib espera datas UTC sem fuso.
                data = datetime.fromtimestamp(st.st_mtime, timezone.utc).replace(tzinfo=None)
                dados[ent.name] = {"DLFileType": tipo, "DLFileSize": st.st_size, "DLFileModificationDate": data}
        await dl.status_response(0, status_dict=dados)

    return contents_of_directory


async def _valor(ld, dominio: str, chave: str | None = None):
    try:
        return await ld.get_value(dominio, chave)
    except Exception:
        return None
