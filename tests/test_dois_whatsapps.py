"""WhatsApp e WhatsApp Business no mesmo iPhone: a pessoa escolhe um, outro ou os dois,
e cada escolhido é conferido separadamente."""
import hashlib
import threading

import pytest

from migrador import checagens
from migrador.dispositivos.base import ErroAparelho
from migrador.dispositivos.simulado import BUSINESS, COMUM, gerar_backup_falso
from migrador.estado import Estado
from migrador.modelos import InfoWhatsApp, Status, tem_bloqueio
from migrador.motor import Migracao
from migrador.verificacao import dominio_do_app, verificar_backup

SENHA = "SENHA-DOIS"


def _backup_dois(tmp_path, senha=SENHA):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta, senha=senha, bundles=[COMUM, BUSINESS])
    return pasta


def _arquivo(pasta, bundle, relativo):
    file_id = hashlib.sha1(f"AppDomainGroup-group.{bundle}.shared-{relativo}".encode()).hexdigest()
    return pasta / file_id[:2] / file_id


def _estragar(arq):
    dados = bytearray(arq.read_bytes())
    dados[-1] ^= 0xFF
    arq.write_bytes(bytes(dados))


def _por_app(rel):
    return {w["bundle"]: w for w in rel.whatsapps}


# --- identificação dos domínios -------------------------------------------------------------

def test_dominio_do_comum_nao_casa_com_o_business():
    assert dominio_do_app("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", COMUM)
    assert dominio_do_app("AppDomain-net.whatsapp.WhatsApp", COMUM)
    assert dominio_do_app("AppDomainPlugin-net.whatsapp.WhatsApp.ShareExtension", COMUM)
    assert not dominio_do_app("AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared", COMUM)
    assert not dominio_do_app("AppDomain-net.whatsapp.WhatsAppSMB", COMUM)
    assert dominio_do_app("AppDomain-net.whatsapp.WhatsAppSMB", BUSINESS)


# --- verificação ----------------------------------------------------------------------------

def test_os_dois_sao_conferidos_separadamente(tmp_path):
    rel = verificar_backup(_backup_dois(tmp_path), SENHA)
    assert rel.ok, rel.problemas
    apps = _por_app(rel)
    assert set(apps) == {COMUM, BUSINESS}
    assert (apps[COMUM]["conversas"], apps[COMUM]["mensagens"], apps[COMUM]["midias"]) == (12, 480, 40)
    assert (apps[BUSINESS]["conversas"], apps[BUSINESS]["mensagens"], apps[BUSINESS]["midias"]) == (6, 240, 20)
    for w in apps.values():
        assert w["banco_integro"] and w["anexos_presentes"] == w["anexos_referenciados"] == w["midias"]
        assert w["amostra_conferida"] == w["midias"] and not w["amostra_falhas"]
    # totais somados, e os bytes de um app não entram no outro
    assert rel.mensagens == 720 and rel.midias == 60
    assert apps[COMUM]["bytes_whatsapp"] > apps[BUSINESS]["bytes_whatsapp"] > 0
    assert rel.bytes_whatsapp == apps[COMUM]["bytes_whatsapp"] + apps[BUSINESS]["bytes_whatsapp"]


def test_business_danificado_reprova_quando_escolhido(tmp_path):
    pasta = _backup_dois(tmp_path)
    _estragar(_arquivo(pasta, BUSINESS, "ChatStorage.sqlite"))
    rel = verificar_backup(pasta, SENHA, bundles=[COMUM, BUSINESS])
    assert not rel.ok
    assert any(p.startswith("WhatsApp Business:") and "decifrado" in p for p in rel.problemas)
    assert _por_app(rel)[COMUM]["banco_integro"]
    assert not _por_app(rel)[BUSINESS]["banco_integro"]


def test_midia_do_business_danificada_reprova(tmp_path):
    pasta = _backup_dois(tmp_path)
    _estragar(_arquivo(pasta, BUSINESS, "Message/Media/5511900000000@s.whatsapp.net/a/b/foto7.jpg"))
    rel = verificar_backup(pasta, SENHA)
    assert not rel.ok
    assert _por_app(rel)[BUSINESS]["amostra_falhas"] == 1
    assert _por_app(rel)[COMUM]["amostra_falhas"] == 0


def test_so_o_comum_escolhido_nao_reprova_pelo_business(tmp_path):
    pasta = _backup_dois(tmp_path)
    _estragar(_arquivo(pasta, BUSINESS, "ChatStorage.sqlite"))
    rel = verificar_backup(pasta, SENHA, bundles=[COMUM])
    assert rel.ok, rel.problemas
    assert list(_por_app(rel)) == [COMUM]
    assert any("WhatsApp Business também está no backup" in a and "não foi conferido" in a for a in rel.avisos)


def test_so_o_business_escolhido(tmp_path):
    rel = verificar_backup(_backup_dois(tmp_path), SENHA, bundles=[BUSINESS])
    assert rel.ok, rel.problemas
    assert list(_por_app(rel)) == [BUSINESS]
    assert rel.mensagens == 240
    assert any("WhatsApp também está no backup" in a for a in rel.avisos)


def test_escolhido_ausente_do_backup_reprova(tmp_path):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta, senha=SENHA, bundles=[COMUM])
    rel = verificar_backup(pasta, SENHA, bundles=[COMUM, BUSINESS])
    assert not rel.ok
    assert any("WhatsApp Business foi escolhido, mas não está no backup" in p for p in rel.problemas)


def test_so_business_instalado_e_reconhecido_sem_escolha(tmp_path):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta, senha=SENHA, bundles=[BUSINESS])
    rel = verificar_backup(pasta, SENHA)
    assert rel.ok and list(_por_app(rel)) == [BUSINESS]


# --- conferência ----------------------------------------------------------------------------

WA, WB = InfoWhatsApp(COMUM, 100), InfoWhatsApp(BUSINESS, 50)


def test_checagem_mostra_nao_escolhido_como_informacao():
    c = checagens.checar_whatsapps([WA, WB], [BUSINESS])
    st = {ch.titulo: ch.status for ch in c}
    assert st["WhatsApp Business no iPhone antigo"] == Status.OK
    assert st["WhatsApp no iPhone antigo"] == Status.INFO
    assert not tem_bloqueio(c)


def test_escolha_vazia_ou_de_app_nao_instalado_bloqueia():
    assert tem_bloqueio(checagens.checar_whatsapps([WA, WB], []))
    assert tem_bloqueio(checagens.checar_whatsapps([WA], [COMUM, BUSINESS]))
    assert tem_bloqueio(checagens.checar_whatsapps([], []))


def test_estimativa_soma_os_dois():
    from migrador.modelos import Aparelho
    o = Aparelho(udid="A", capacidade=0, livre=0)
    assert checagens.tamanho_estimado_backup(o, [WA, WB]) == 150


# --- fluxo completo -------------------------------------------------------------------------

def _fluxo(tmp_path, cenario, escolhidos=None):
    m = Migracao.nova(True, cenario)
    ap = m.procurar()
    c = m.conferir(ap[0], ap[1], tmp_path / "backup", escolhidos)
    assert not tem_bloqueio(c), [x.detalhe for x in c if x.status == Status.BLOQUEIO]
    m.criptografia_ativa()
    m.definir_senha("SENHA-1", guardar=False)
    m.fazer_backup(lambda p: None, threading.Event())
    rel = m.verificar(lambda p, s: None)
    assert rel.ok, rel.problemas
    _, c = m.conferir_destino()
    assert not tem_bloqueio(c)
    m.restaurar(lambda p: None)
    m.conferir_resultado()  # 1ª consulta: ainda baixando da App Store
    return m, rel, m.conferir_resultado()


@pytest.mark.parametrize("escolhidos, esperados", [
    (None, [COMUM, BUSINESS]),
    ([COMUM, BUSINESS], [COMUM, BUSINESS]),
    ([COMUM], [COMUM]),
    ([BUSINESS], [BUSINESS]),
])
def test_fluxo_com_os_dois_instalados(tmp_path, escolhidos, esperados):
    m, rel, resultado = _fluxo(tmp_path, "dois_whatsapps", escolhidos)
    assert m.estado.bundles_escolhidos == esperados
    assert [w["bundle"] for w in rel.whatsapps] == esperados
    assert list(resultado) == esperados
    for r in resultado.values():
        assert r["estado"] == "instalado" and r["proporcao"] > 0.9


def test_fluxo_so_business_instalado(tmp_path):
    m, rel, resultado = _fluxo(tmp_path, "so_business")
    assert m.estado.bundles_escolhidos == [BUSINESS]
    assert list(resultado) == [BUSINESS]


def test_escolher_app_que_nao_esta_instalado_bloqueia_conferencia(tmp_path):
    m = Migracao.nova(True, "normal")
    ap = m.procurar()
    c = m.conferir(ap[0], ap[1], tmp_path / "backup", [BUSINESS])
    assert tem_bloqueio(c)


def test_mudar_a_escolha_exige_nova_verificacao(tmp_path):
    m, rel, _ = _fluxo(tmp_path, "dois_whatsapps")
    m.estado.etapa = "conexao"
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup", [COMUM])
    assert m.estado.relatorio is None
    with pytest.raises(ErroAparelho):
        m.conferir_destino()


def test_passar_de_um_para_os_dois_exige_nova_verificacao(tmp_path):
    m, rel, _ = _fluxo(tmp_path, "dois_whatsapps", [COMUM])
    m.estado.etapa = "conexao"
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup", [COMUM, BUSINESS])
    assert m.estado.relatorio is None
    rel = m.verificar(lambda p, s: None)
    assert [w["bundle"] for w in rel.whatsapps] == [COMUM, BUSINESS]


def test_mesma_escolha_mantem_a_verificacao(tmp_path):
    m, rel, _ = _fluxo(tmp_path, "dois_whatsapps")
    m.estado.etapa = "conexao"
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup", [BUSINESS, COMUM])
    assert m.estado.relatorio is not None


def test_estado_salvo_pela_versao_anterior_continua_valendo():
    e = Estado(whatsapp={"bundle": BUSINESS, "tamanho": 10})
    assert e.bundles_escolhidos == [BUSINESS]
    assert e.tamanho_whatsapp(BUSINESS) == 10
