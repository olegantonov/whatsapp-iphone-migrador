"""Verificações do PC, destino fixo e conferência do WhatsApp depois da restauração."""
import threading
from pathlib import Path

from migrador import checagens, sistema
from migrador.dispositivos.simulado import UDID_DESTINO, UDID_ORIGEM
from migrador.modelos import Aparelho, Status, tem_bloqueio
from migrador.motor import Migracao


def _pc(**kw):
    base = dict(windows="Windows 11 (build 26200)", build=26200, apple_devices="1.0", servico_rodando=True,
                usbmux_ok=True, confiaveis=["A"], na_tomada=True, tem_bateria=False)
    base.update(kw)
    return sistema.SistemaPC(**base)


def _status(c, titulo):
    return next(ch.status for ch in c if ch.titulo.startswith(titulo))


def test_pc_ideal_sem_bloqueio():
    assert not tem_bloqueio(checagens.checar_sistema(_pc(), momento="backup"))


def test_sem_app_da_apple_bloqueia():
    c = checagens.checar_sistema(_pc(apple_devices=None, itunes=False, usbmux_ok=False))
    assert _status(c, "App Apple Devices ou iTunes") == Status.BLOQUEIO


def test_itunes_tambem_serve():
    c = checagens.checar_sistema(_pc(apple_devices=None, itunes=True))
    assert _status(c, "iTunes") == Status.OK


def test_servico_parado_bloqueia():
    c = checagens.checar_sistema(_pc(usbmux_ok=False, servico_rodando=False))
    assert _status(c, "Serviço da Apple") == Status.BLOQUEIO


def test_primeira_conexao_ainda_nao_feita_avisa():
    c = checagens.checar_sistema(_pc(confiaveis=[]))
    assert _status(c, "Primeira conexão") == Status.AVISO


def test_notebook_na_bateria_bloqueia_e_na_simulacao_so_avisa():
    pc = _pc(tem_bateria=True, na_tomada=False, bateria_pct=40, tampa_ca=0)
    assert _status(checagens.checar_sistema(pc), "Energia do PC") == Status.BLOQUEIO
    assert _status(checagens.checar_sistema(pc, simulado=True), "Energia do PC") == Status.AVISO


def test_tampa_que_suspende_avisa():
    c = checagens.checar_sistema(_pc(tem_bateria=True, na_tomada=True, bateria_pct=90, tampa_ca=1))
    assert _status(c, "Tampa do notebook") == Status.AVISO


def test_programa_concorrente_avisa_na_preparacao_e_bloqueia_no_backup():
    pc = _pc(concorrentes=["iTunes"])
    assert _status(checagens.checar_sistema(pc, momento="preparacao"), "Outros programas") == Status.AVISO
    assert _status(checagens.checar_sistema(pc, momento="backup"), "Outros programas") == Status.BLOQUEIO


def test_pasta_no_onedrive_bloqueia(monkeypatch, tmp_path):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    pasta = tmp_path / "OneDrive" / "Backup"
    assert sistema.pasta_sincronizada(pasta) == "OneDrive"
    c = checagens.checar_pasta(pasta, "OneDrive", True, False)
    assert tem_bloqueio(c)
    assert sistema.pasta_sincronizada(tmp_path / "outra") is None


def _ate_destino(tmp_path):
    m = Migracao.nova(True, "normal")
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup")
    m.criptografia_ativa()
    m.definir_senha("SENHA-1", guardar=False)
    m.fazer_backup(lambda p: None, threading.Event())
    assert m.verificar(lambda p, s: None).ok
    return m


def test_destino_e_o_escolhido_na_conferencia(tmp_path):
    m = _ate_destino(tmp_path)
    destino, c = m.conferir_destino()
    assert destino.udid == UDID_DESTINO and not tem_bloqueio(c)


def test_outro_iphone_nunca_e_escolhido_sozinho(tmp_path):
    m = _ate_destino(tmp_path)
    m.estado.destino = Aparelho(udid="OUTRO-IPHONE", nome="iPhone escolhido").para_dict()
    destino, c = m.conferir_destino()
    assert destino is None
    assert tem_bloqueio(c)
    assert any("não está conectado" in ch.detalhe for ch in c)


def test_restaurar_sem_conferir_destino_e_recusado(tmp_path):
    import pytest

    from migrador.dispositivos.base import ErroAparelho
    m = _ate_destino(tmp_path)
    with pytest.raises(ErroAparelho):
        m.restaurar(lambda p: None)


def test_conferencia_do_whatsapp_no_iphone_novo(tmp_path):
    m = _ate_destino(tmp_path)
    m.conferir_destino()
    m.restaurar(lambda p: None)
    assert m.conferir_resultado()["net.whatsapp.WhatsApp"]["estado"] == "instalando"
    r = m.conferir_resultado()["net.whatsapp.WhatsApp"]
    assert r["estado"] == "instalado" and r["proporcao"] > 0.9
    m.confirmar_resultado(True, True)
    assert m.estado.confirmado_em


def test_inventario_fica_fora_da_pasta_do_backup(tmp_path):
    m = _ate_destino(tmp_path)
    assert m._arquivo_inventario().exists()
    assert not str(m._arquivo_inventario()).startswith(str(Path(m.estado.pasta_backup)))
    assert UDID_ORIGEM in [p.name for p in Path(m.estado.pasta_backup).iterdir()]
