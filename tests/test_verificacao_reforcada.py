"""Verificação reforçada (1.1.0): backup criptografado de verdade, amostra decifrada,
anexos cruzados com as conversas e inventário de todos os arquivos."""
import os
import sqlite3

import pytest

from migrador.dispositivos.simulado import gerar_backup_falso
from migrador.verificacao import (
    SenhaIncorreta, conferir_antes_de_restaurar, gerar_inventario, verificar_backup,
)

SENHA = "SENHA-DE-TESTE"


def _cifrado(tmp_path, **kw):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta, senha=SENHA, **kw)
    return pasta


def _arquivo_da_midia(pasta, indice=0):
    """Arquivo físico de uma mídia. O Manifest.db cifrado não abre sem senha, então o ID é
    recalculado como o gerador faz (sha1 de domínio-caminho)."""
    import hashlib
    rel = f"Message/Media/5511900000000@s.whatsapp.net/a/b/foto{indice}.jpg"
    file_id = hashlib.sha1(f"AppDomainGroup-group.net.whatsapp.WhatsApp.shared-{rel}".encode()).hexdigest()
    return pasta / file_id[:2] / file_id


def test_backup_criptografado_real_e_aprovado(tmp_path):
    pasta = _cifrado(tmp_path)
    rel = verificar_backup(pasta, SENHA, exigir_criptografia=True)
    assert rel.ok, rel.problemas
    assert rel.criptografado
    assert (rel.conversas, rel.mensagens, rel.midias) == (12, 480, 40)
    assert rel.anexos_referenciados == 40 and rel.anexos_presentes == 40
    assert rel.amostra_conferida == 40 and rel.amostra_falhas == 0
    assert rel.inventario and rel.inventario_arquivos > 40
    # nenhuma sobra de temporários ao lado do backup
    assert [p.name for p in pasta.parent.iterdir()] == ["UDID"]


def test_senha_errada_e_detectada(tmp_path):
    pasta = _cifrado(tmp_path)
    with pytest.raises(SenhaIncorreta):
        verificar_backup(pasta, "outra-senha")


def test_midia_cifrada_danificada_reprova(tmp_path):
    pasta = _cifrado(tmp_path)
    arq = _arquivo_da_midia(pasta, 3)
    dados = bytearray(arq.read_bytes())
    dados[-1] ^= 0xFF  # mesmo tamanho, último bloco estragado
    arq.write_bytes(bytes(dados))
    rel = verificar_backup(pasta, SENHA)
    assert not rel.ok
    assert rel.amostra_falhas == 1
    assert any("não puderam ser decifradas" in p for p in rel.problemas)


def test_backup_sem_criptografia_quando_exigida_reprova(tmp_path):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta)
    rel = verificar_backup(pasta, exigir_criptografia=True)
    assert not rel.ok
    assert any("SEM criptografia" in p for p in rel.problemas)


def _remover_midias(pasta, quantas):
    con = sqlite3.connect(pasta / "Manifest.db")
    linhas = con.execute("SELECT fileID FROM Files WHERE relativePath LIKE 'Message/Media/%' "
                         "ORDER BY relativePath LIMIT ?", (quantas,)).fetchall()
    for (file_id,) in linhas:
        con.execute("DELETE FROM Files WHERE fileID=?", (file_id,))
        (pasta / file_id[:2] / file_id).unlink()
    con.commit()
    con.close()


def test_anexos_ausentes_geram_aviso_com_contagem(tmp_path):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta)
    _remover_midias(pasta, 3)
    rel = verificar_backup(pasta)
    assert rel.ok
    assert (rel.anexos_referenciados, rel.anexos_presentes) == (40, 37)
    assert any("3 de 40 anexos" in a for a in rel.avisos)


def test_nenhum_anexo_no_backup_reprova(tmp_path):
    pasta = tmp_path / "bk" / "UDID"
    gerar_backup_falso(pasta)
    _remover_midias(pasta, 40)
    rel = verificar_backup(pasta)
    assert not rel.ok
    assert any("nenhum deles está no backup" in p for p in rel.problemas)


def test_midia_alterada_com_mesmo_tamanho_bloqueia_restauracao(tmp_path):
    pasta = _cifrado(tmp_path)
    inventario = tmp_path / "inv.tsv.gz"
    rel = verificar_backup(pasta, SENHA, inventario_destino=inventario)
    assert conferir_antes_de_restaurar(pasta, rel, inventario) == []
    arq = _arquivo_da_midia(pasta, 5)
    tamanho = arq.stat().st_size
    arq.write_bytes(os.urandom(tamanho))  # mesmo tamanho, índices intactos
    st = arq.stat()
    os.utime(arq, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    problemas = conferir_antes_de_restaurar(pasta, rel, inventario)
    assert problemas and "1 alterado(s)" in problemas[0]


def test_info_plist_entra_na_impressao_digital(tmp_path):
    pasta = _cifrado(tmp_path)
    rel = verificar_backup(pasta, SENHA)
    (pasta / "Info.plist").write_bytes((pasta / "Info.plist").read_bytes() + b"\n")
    assert conferir_antes_de_restaurar(pasta, rel)


def test_relatorio_antigo_exige_nova_verificacao(tmp_path):
    pasta = _cifrado(tmp_path)
    rel = verificar_backup(pasta, SENHA)
    rel.versao_conferencia = 1
    assert "versão anterior" in conferir_antes_de_restaurar(pasta, rel)[0]


def test_inventario_estavel(tmp_path):
    pasta = _cifrado(tmp_path)
    assert gerar_inventario(pasta) == gerar_inventario(pasta)
