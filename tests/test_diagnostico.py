import json
import logging
import threading
import zipfile

from migrador import diagnostico, registro
from migrador.dispositivos.simulado import UDID_ORIGEM
from migrador.motor import Migracao


def test_anonimizar_troca_udid_nome_e_usuario(monkeypatch):
    monkeypatch.setenv("USERNAME", "Fulano Silva")
    texto = ("aparelho 00008101-001A2B3C4D5E6F70: iPhone de Maria em C:\\Users\\Fulano Silva\\Desktop; "
             "antigo a1b2c3d4e5f60718293a4b5c6d7e8f9012345678")
    saida = diagnostico.anonimizar(texto, ["iPhone de Maria"])
    assert "00008101-001A2B3C4D5E6F70" not in saida
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678" not in saida
    assert "Maria" not in saida and "Fulano" not in saida
    assert "<iphone-" in saida and "<nome-do-aparelho>" in saida and "\\Users\\<usuario>\\" in saida


def test_mesmo_udid_mesmo_apelido():
    a = diagnostico.anonimizar("00008101-001A2B3C4D5E6F70")
    b = diagnostico.anonimizar("x 00008101-001a2b3c4d5e6f70 y")
    assert a in b


def test_pacote_sem_senha_nem_nomes(tmp_path):
    registro._arquivo = None
    registro.iniciar()
    m = Migracao.nova(True, "normal")
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup")
    m.criptografia_ativa()
    m.definir_senha("SENHA-SECRETA-99", guardar=True)
    m.fazer_backup(lambda p: None, threading.Event())
    logging.getLogger("teste").info("aparelho %s pronto", UDID_ORIGEM)

    zip_path = diagnostico.gerar_pacote(tmp_path / "diag.zip", "travou no backup do iPhone de Maria")
    with zipfile.ZipFile(zip_path) as z:
        nomes = z.namelist()
        tudo = "\n".join(z.read(n).decode("utf-8") for n in nomes)
        estado = json.loads(z.read("estado.json"))
    assert "resumo.json" in nomes and any(n.startswith("logs/") for n in nomes)
    assert "SENHA-SECRETA-99" not in tudo
    assert "senha_protegida" not in estado
    assert "iPhone de Maria" not in tudo
    assert UDID_ORIGEM not in tudo
    assert "backup: 100% em" in tudo  # o progresso ficou no log
    for h in list(logging.getLogger().handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            logging.getLogger().removeHandler(h)
            h.close()
    registro._arquivo = None


def test_aviso_de_codigo_chega_na_tela(tmp_path):
    m = Migracao.nova(True, "normal")
    ap = m.procurar()
    m.conferir(ap[0], ap[1], tmp_path / "backup")
    m.criptografia_ativa()
    m.definir_senha("SENHA-1", guardar=False)
    avisos = []
    m.fazer_backup(lambda p: None, threading.Event(), avisos.append)
    assert any("código" in a for a in avisos) and avisos[-1] == ""
