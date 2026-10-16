"""Conversa com iPhones de verdade pelo USB, usando o pymobiledevice3.

Precisa do serviço de dispositivos móveis da Apple instalado no Windows
(vem com o app "Apple Devices" da Microsoft Store ou com o iTunes).
"""
import asyncio
import logging
import threading
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
from migrador.modelos import WHATSAPP_BUNDLES, Aparelho, InfoWhatsApp

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
                    _repassar_avisos(mb, aviso)
                    # full=False: se já houver um backup parcial/anterior na pasta, é incremental
                    # e aproveita o que já foi copiado.
                    tarefa = asyncio.create_task(
                        mb.backup(full=False, backup_directory=pasta, progress_callback=progresso)
                    )
                    while not tarefa.done():
                        if cancelar.is_set():
                            tarefa.cancel()
                            try:
                                await tarefa
                            except (asyncio.CancelledError, Exception):
                                pass
                            raise Cancelado()
                        await asyncio.sleep(0.3)
                    tarefa.result()
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


def _repassar_avisos(mb: Mobilebackup2Service, aviso: Aviso | None) -> None:
    """O pymobiledevice3 só registra estes avisos no log; aqui eles também vão para a tela."""
    original = getattr(mb, "_log_backup_notification", None)
    if original is None:
        log.warning("esta versão do pymobiledevice3 não expõe os avisos do backup")
        return

    def repassar(evento):
        original(evento)
        nome = evento.get("Name") if isinstance(evento, dict) else None
        log.info("aviso do iPhone durante o backup: %s", nome)
        if aviso and nome in AVISOS_BACKUP:
            try:
                aviso(AVISOS_BACKUP[nome])
            except Exception:
                log.exception("falha ao mostrar aviso")

    mb._log_backup_notification = repassar


async def _valor(ld, dominio: str, chave: str | None = None):
    try:
        return await ld.get_value(dominio, chave)
    except Exception:
        return None
