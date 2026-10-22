"""Acompanhamento do backup real sem iPhone: um canal falso no lugar do pymobiledevice3."""
import asyncio
import threading
from contextlib import asynccontextmanager

import pytest

from migrador.dispositivos import real

CODIGO = "com.apple.LocalAuthentication.ui.presented"


class CanalFalso:
    def __init__(self, mensagens, dados):
        self.mensagens, self.dados = list(mensagens), dados

    async def receive_message(self):
        return self.mensagens.pop(0)

    async def _recvall(self, tamanho):
        return self.dados[:tamanho]


class BackupFalso:
    """Imita o Mobilebackup2Service: pede o código, demora "preparando" e depois manda arquivos."""

    def __init__(self, preparar: threading.Event):
        self.preparar = preparar
        self.notificacoes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def _log_backup_notification(self, evento):
        self.notificacoes.append(evento)

    @asynccontextmanager
    async def device_link(self, *_args, **_kwargs):
        yield CanalFalso([["DLMessageUploadFiles", {}, 0.0], ["DLMessageProcessMessage", {"ErrorCode": 0}]],
                         b"x" * 4096)

    async def backup(self, full, backup_directory, progress_callback):
        self._log_backup_notification({"Name": CODIGO})
        self._log_backup_notification({"Name": "com.apple.LocalAuthentication.ui.dismissed"})
        while not self.preparar.is_set():
            await asyncio.sleep(0.05)
        async with self.device_link(backup_directory) as dl:
            mensagem = await dl.receive_message()
            progress_callback(mensagem[2])
            await dl._recvall(4096)
            await dl.receive_message()


class LockdownFalso:
    async def close(self):
        pass


@pytest.fixture
def backup_falso(monkeypatch):
    preparar = threading.Event()
    falso = BackupFalso(preparar)

    async def conectar(_udid, pair_timeout=60):
        return LockdownFalso()

    monkeypatch.setattr(real, "_conectar", conectar)
    monkeypatch.setattr(real, "Mobilebackup2Service", lambda _ld: falso)
    return falso


def test_tela_mostra_preparacao_e_depois_o_que_chegou(backup_falso, tmp_path):
    avisos, progresso = [], []

    def aviso(texto):
        avisos.append(texto)
        if "preparando" in texto:
            backup_falso.preparar.set()

    real.BackendReal().fazer_backup("udid", tmp_path, progresso.append, threading.Event(), aviso)

    assert progresso == [0.0]
    assert any("preparando o backup" in a for a in avisos)
    assert backup_falso.notificacoes[0]["Name"] == CODIGO
    resumo = real.BackendReal.ultima_atividade
    assert resumo["bytes"] == 4096 and resumo["ultimo_comando"] == "DLMessageProcessMessage"


def test_aviso_do_codigo_tem_prioridade():
    atividade = real._Atividade()
    atividade.notificacao(CODIGO)
    assert "código de desbloqueio" in atividade.texto()
    atividade.notificacao("com.apple.LocalAuthentication.ui.dismissed")
    assert "preparando" in atividade.texto()


def test_silencio_longo_pede_para_conferir_o_iphone(monkeypatch):
    atividade = real._Atividade()
    atividade.recebido(10)
    atividade.ultima -= real.SILENCIO_LONGO + 60
    assert "não manda nada há 11 minutos" in atividade.texto()


def test_erro_28_do_iphone_vira_orientacao_em_portugues():
    from pymobiledevice3.exceptions import PyMobileDevice3Exception
    bruto = PyMobileDevice3Exception(
        "Device link error: {'ErrorCode': 102, 'ErrorDescription': 'Output stream write error: Error "
        "Domain=NSPOSIXErrorDomain Code=28 \"No space left on device\" (MBErrorDomain/102)'}")
    erro = real.traduzir(bruto)
    assert "sem espaço" in erro.mensagem and "Reinicie o iPhone antigo" in erro.como_resolver
    assert "Device link" not in erro.como_resolver
