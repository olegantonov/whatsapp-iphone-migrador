import plistlib
import sqlite3

from migrador.dispositivos.simulado import gerar_backup_falso
from migrador.verificacao import conferir_antes_de_restaurar, verificar_backup


def _backup(tmp_path, **kw):
    pasta = tmp_path / "UDID"
    gerar_backup_falso(pasta, **kw)
    return pasta


def test_backup_integro_e_aprovado(tmp_path):
    rel = verificar_backup(_backup(tmp_path))
    assert rel.ok, rel.problemas
    assert rel.whatsapp_bundle == "net.whatsapp.WhatsApp"
    assert (rel.conversas, rel.mensagens, rel.midias) == (12, 480, 40)
    assert rel.banco_integro
    assert conferir_antes_de_restaurar(tmp_path / "UDID", rel) == []


def test_whatsapp_business_e_reconhecido(tmp_path):
    rel = verificar_backup(_backup(tmp_path, bundle="net.whatsapp.WhatsAppSMB"))
    assert rel.ok
    assert rel.whatsapp_bundle == "net.whatsapp.WhatsAppSMB"


def test_sem_whatsapp_reprova(tmp_path):
    rel = verificar_backup(_backup(tmp_path, bundle=None))
    assert not rel.ok
    assert any("WhatsApp não está" in p for p in rel.problemas)


def test_arquivo_faltando_reprova(tmp_path):
    pasta = _backup(tmp_path)
    sub = next(p for p in sorted(pasta.iterdir()) if p.is_dir())
    next(sub.iterdir()).unlink()
    rel = verificar_backup(pasta)
    assert not rel.ok
    assert rel.arquivos_faltando == 1


def test_arquivo_truncado_reprova(tmp_path):
    pasta = _backup(tmp_path)
    sub = next(p for p in sorted(pasta.iterdir()) if p.is_dir())
    arq = next(sub.iterdir())
    arq.write_bytes(arq.read_bytes()[:-1])
    rel = verificar_backup(pasta)
    assert not rel.ok
    assert any("tamanho diferente" in p for p in rel.problemas)


def test_backup_inacabado_reprova(tmp_path):
    pasta = _backup(tmp_path)
    status = plistlib.loads((pasta / "Status.plist").read_bytes())
    status["SnapshotState"] = "uploading"
    (pasta / "Status.plist").write_bytes(plistlib.dumps(status))
    rel = verificar_backup(pasta)
    assert not rel.ok
    assert any("não chegou ao fim" in p for p in rel.problemas)


def test_banco_corrompido_reprova(tmp_path):
    pasta = _backup(tmp_path)
    con = sqlite3.connect(pasta / "Manifest.db")
    file_id = con.execute("SELECT fileID FROM Files WHERE relativePath='ChatStorage.sqlite'").fetchone()[0]
    con.close()
    arq = pasta / file_id[:2] / file_id
    tamanho = arq.stat().st_size
    arq.write_bytes(b"lixo" * (tamanho // 4) + b"x" * (tamanho % 4))
    rel = verificar_backup(pasta)
    assert not rel.ok
    assert not rel.banco_integro


def test_alteracao_depois_da_verificacao_e_detectada(tmp_path):
    pasta = _backup(tmp_path)
    rel = verificar_backup(pasta)
    (pasta / "Status.plist").write_bytes((pasta / "Status.plist").read_bytes() + b" ")
    assert conferir_antes_de_restaurar(pasta, rel)


def test_metadado_ausente_reprova(tmp_path):
    pasta = _backup(tmp_path)
    (pasta / "Manifest.db").unlink()
    rel = verificar_backup(pasta)
    assert not rel.ok


def test_backup_completo_existe(tmp_path):
    from migrador.verificacao import backup_completo_existe
    assert not backup_completo_existe(None)
    assert not backup_completo_existe(tmp_path / "nada")
    pasta = _backup(tmp_path)
    assert backup_completo_existe(pasta)
    # primeira cópia interrompida: o pymobiledevice3 deixa o Manifest.plist vazio
    (pasta / "Manifest.plist").write_bytes(b"")
    assert not backup_completo_existe(pasta)
