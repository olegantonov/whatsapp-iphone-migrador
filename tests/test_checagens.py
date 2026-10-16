from pathlib import Path

from migrador import checagens
from migrador.modelos import Aparelho, InfoWhatsApp, Status, tem_bloqueio

GB = 1024**3
ULTIMAS = {"iPhone13,2": "27.0.1", "iPhone17,1": "27.0.1"}


def _par(**destino):
    origem = Aparelho(udid="A", tipo="iPhone13,2", ios="27.0.1", capacidade=256 * GB, livre=26 * GB,
                      buscar_ativo=True, bateria=80)
    d = dict(udid="B", tipo="iPhone17,1", ios="27.0.1", capacidade=512 * GB, livre=500 * GB,
             buscar_ativo=False, bateria=80)
    d.update(destino)
    return origem, Aparelho(**d)


WA = InfoWhatsApp("net.whatsapp.WhatsApp", 200 * GB)


def _titulos_bloqueio(c):
    return [x.titulo for x in c if x.status == Status.BLOQUEIO]


def test_caso_feliz_sem_bloqueio():
    o, d = _par()
    assert not tem_bloqueio(checagens.checar_aparelhos(o, d, [WA], ULTIMAS))


def test_destino_com_ios_mais_antigo_bloqueia():
    o, d = _par(ios="26.4")
    c = checagens.checar_aparelhos(o, d, [WA], ULTIMAS)
    assert "Versão do iOS" in _titulos_bloqueio(c)


def test_destino_desatualizado_mesmo_acima_da_origem_bloqueia():
    o, d = _par(ios="27.0")
    o.ios = "26.6"
    c = checagens.checar_aparelhos(o, d, [WA], ULTIMAS)
    assert "iOS do iPhone novo" in _titulos_bloqueio(c)


def test_origem_desatualizada_so_avisa():
    o, d = _par()
    o.ios = "26.6"
    c = checagens.checar_aparelhos(o, d, [WA], ULTIMAS)
    assert not tem_bloqueio(c)
    assert any(x.titulo == "iOS do iPhone antigo" and x.status == Status.AVISO for x in c)


def test_sem_consulta_a_apple_avisa_mas_nao_bloqueia():
    o, d = _par()
    c = checagens.checar_aparelhos(o, d, [WA], None)
    assert not tem_bloqueio(c)
    assert any(x.titulo == "Atualizações do iOS" for x in c)


def test_destino_pequeno_bloqueia():
    o, d = _par(capacidade=128 * GB)
    assert "Espaço no iPhone novo" in _titulos_bloqueio(checagens.checar_aparelhos(o, d, [WA], ULTIMAS))


def test_buscar_ligado_no_destino_bloqueia():
    o, d = _par(buscar_ativo=True)
    assert any("Buscar" in t for t in _titulos_bloqueio(checagens.checar_aparelhos(o, d, [WA], ULTIMAS)))


def test_sem_whatsapp_bloqueia():
    o, d = _par()
    assert any("WhatsApp" in t for t in _titulos_bloqueio(checagens.checar_aparelhos(o, d, [], ULTIMAS)))


def test_mesmo_aparelho_bloqueia():
    o, _ = _par()
    assert tem_bloqueio(checagens.checar_aparelhos(o, o, [WA], ULTIMAS))


def test_espaco_no_pc():
    ok = checagens.checar_pc(True, Path("E:/x"), 400 * GB, "NTFS", 230 * GB)
    assert not tem_bloqueio(ok)
    pouco = checagens.checar_pc(True, Path("C:/x"), 120 * GB, "NTFS", 230 * GB)
    assert "Espaço no PC" in _titulos_bloqueio(pouco)


def test_fat32_bloqueia():
    c = checagens.checar_pc(True, Path("F:/x"), 900 * GB, "FAT32", 10 * GB)
    assert "Formato do disco do backup" in _titulos_bloqueio(c)


def test_sem_servico_apple_bloqueia():
    c = checagens.checar_pc(False, Path("E:/x"), 900 * GB, "NTFS", 10 * GB)
    assert "Serviço da Apple no PC" in _titulos_bloqueio(c)


def test_restaurar_no_aparelho_antigo_e_impedido():
    o, _ = _par()
    c = checagens.checar_destino_para_restaurar(o, "A", "27.0.1", 200 * GB, ULTIMAS)
    assert tem_bloqueio(c)
    assert "ANTIGO" in c[0].detalhe


def test_restaurar_com_ios_menor_que_backup_bloqueia():
    _, d = _par(ios="26.0")
    c = checagens.checar_destino_para_restaurar(d, "A", "26.6", 200 * GB, ULTIMAS)
    assert "Versão do iOS" in _titulos_bloqueio(c)
