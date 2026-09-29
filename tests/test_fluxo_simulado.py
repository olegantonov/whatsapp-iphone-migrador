import threading

import pytest

from migrador import estado
from migrador.dispositivos.base import Cancelado, ErroAparelho
from migrador.modelos import Status, tem_bloqueio
from migrador.motor import Migracao


def _conferir(m, tmp_path):
    ap = m.procurar()
    return m.conferir(ap[0], ap[1], tmp_path / "backup")


def _ate_backup(cenario, tmp_path, senha="SENHA-1"):
    m = Migracao.nova(True, cenario)
    assert not tem_bloqueio(_conferir(m, tmp_path))
    m.criptografia_ativa()
    m.definir_senha(senha, guardar=False)
    return m


def test_fluxo_completo(tmp_path):
    m = _ate_backup("normal", tmp_path)
    m.fazer_backup(lambda p: None, threading.Event())
    assert m.verificar(lambda p, s: None).ok
    _, c = m.conferir_destino()
    assert not tem_bloqueio(c)
    m.restaurar(lambda p: None)
    assert m.estado.etapa == "concluido"
    # o progresso fica salvo em disco
    assert estado.carregar().etapa == "concluido"


def test_retomar_depois_de_fechar(tmp_path):
    m = _ate_backup("normal", tmp_path)
    m.fazer_backup(lambda p: None, threading.Event())
    retomada = Migracao.retomar()
    assert retomada.estado.etapa == "verificacao"
    assert retomada.estado.origem["udid"] == m.estado.origem["udid"]


@pytest.mark.parametrize("cenario,titulo", [
    ("destino_desatualizado", "Versão do iOS"),
    ("sem_espaco_destino", "Espaço no iPhone novo"),
    ("sem_whatsapp", "WhatsApp no iPhone antigo"),
    ("buscar_ativo", "\"Buscar iPhone\" no iPhone novo"),
])
def test_cenarios_que_bloqueiam_a_conferencia(cenario, titulo, tmp_path):
    m = Migracao.nova(True, cenario)
    c = _conferir(m, tmp_path)
    assert any(x.titulo == titulo and x.status == Status.BLOQUEIO for x in c)
    assert m.estado.etapa != "senha"


def test_confiar_pede_acao_e_depois_libera(tmp_path):
    m = Migracao.nova(True, "confiar")
    c = _conferir(m, tmp_path)
    assert any("confirmação" in x.titulo for x in c)
    assert not tem_bloqueio(_conferir(m, tmp_path))


def test_queda_no_backup_e_retomada(tmp_path):
    m = _ate_backup("queda_backup", tmp_path)
    with pytest.raises(ErroAparelho):
        m.fazer_backup(lambda p: None, threading.Event())
    assert m.estado.etapa == "backup"
    m.fazer_backup(lambda p: None, threading.Event())
    assert m.verificar(lambda p, s: None).ok


def test_cancelar_backup(tmp_path):
    m = _ate_backup("normal", tmp_path)
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelado):
        m.fazer_backup(lambda p: None, ev)
    assert m.estado.etapa == "backup"


def test_backup_corrompido_nao_libera_restauracao(tmp_path):
    m = _ate_backup("backup_corrompido", tmp_path)
    m.fazer_backup(lambda p: None, threading.Event())
    rel = m.verificar(lambda p, s: None)
    assert not rel.ok
    assert m.estado.etapa == "verificacao"
    with pytest.raises(ErroAparelho):
        m.conferir_destino()
    with pytest.raises(ErroAparelho):
        m.restaurar(lambda p: None)


def test_senha_existente_errada_e_recusada(tmp_path):
    m = Migracao.nova(True, "ja_criptografado")
    _conferir(m, tmp_path)
    assert m.criptografia_ativa()
    with pytest.raises(ErroAparelho):
        m.definir_senha("errada", guardar=False)
    m.definir_senha("1234", guardar=False)
    assert m.estado.etapa == "backup"


def test_backup_alterado_depois_da_verificacao_bloqueia_restauracao(tmp_path):
    m = _ate_backup("normal", tmp_path)
    m.fazer_backup(lambda p: None, threading.Event())
    m.verificar(lambda p, s: None)
    status = m.estado.pasta_aparelho / "Status.plist"
    status.write_bytes(status.read_bytes() + b" ")
    _, c = m.conferir_destino()
    assert any(x.titulo == "Integridade do backup" and x.status == Status.BLOQUEIO for x in c)
    with pytest.raises(ErroAparelho):
        m.restaurar(lambda p: None)


def test_senha_guardada_protegida(tmp_path):
    from migrador import protecao
    if not protecao.disponivel():
        pytest.skip("DPAPI só no Windows")
    m = Migracao.nova(True, "normal")
    _conferir(m, tmp_path)
    m.criptografia_ativa()
    m.definir_senha("MINHA-SENHA", guardar=True)
    bruto = estado.arquivo().read_text(encoding="utf-8")
    assert "MINHA-SENHA" not in bruto
    assert Migracao.retomar().senha == "MINHA-SENHA"


def test_backup_anterior_completo_so_depois_de_terminar(tmp_path):
    m = _ate_backup("queda_backup", tmp_path)
    assert not m.backup_anterior_completo()
    with pytest.raises(ErroAparelho):
        m.fazer_backup(lambda p: None, threading.Event())
    assert not m.backup_anterior_completo()
    m.fazer_backup(lambda p: None, threading.Event())
    assert m.backup_anterior_completo()
