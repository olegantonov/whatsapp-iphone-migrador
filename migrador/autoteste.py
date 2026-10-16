"""Roda a migração completa em modo simulação, sem interface.

Usado no GitHub Actions para garantir que o executável gerado funciona antes de
publicar uma atualização, e útil para diagnóstico em qualquer PC:
    MigradorWhatsApp.exe --autoteste --saida resultado.txt
"""
import os
import tempfile
import threading
import traceback
from pathlib import Path


def rodar(saida: Path | None = None) -> bool:
    from migrador.versao import __version__
    linhas: list[str] = [f"versão: {__version__}"]
    ok = False
    try:
        os.environ["MIGRADOR_SIM_RAPIDO"] = "1"
        os.environ["MIGRADOR_DADOS"] = tempfile.mkdtemp(prefix="migrador-autoteste-dados-")
        # Garante que o motor real (pymobiledevice3, pyiosbackup) está no pacote.
        import migrador.dispositivos.real  # noqa: F401
        import customtkinter  # noqa: F401

        from migrador.modelos import tem_bloqueio
        from migrador.motor import Migracao

        m = Migracao.nova(simulacao=True, cenario="normal")
        aparelhos = m.procurar()
        origem, destino = aparelhos[0], aparelhos[1]
        pasta = Path(tempfile.mkdtemp(prefix="migrador-autoteste-backup-"))
        c = m.conferir(origem, destino, pasta)
        assert not tem_bloqueio(c), [x.titulo for x in c if x.status.value == "bloqueio"]
        linhas.append("conferência: ok")
        m.criptografia_ativa()
        m.definir_senha("SENHA-TESTE", guardar=True)
        linhas.append("senha: ok")
        m.fazer_backup(lambda p: None, threading.Event())
        linhas.append("backup: ok")
        rel = m.verificar(lambda p, msg: None)
        assert rel.ok, rel.problemas
        # O backup simulado é criptografado de verdade: isto prova que a decifragem está no pacote.
        assert rel.criptografado and rel.amostra_conferida and not rel.amostra_falhas
        assert rel.anexos_referenciados == rel.anexos_presentes > 0
        linhas.append(f"verificação: ok ({rel.mensagens} mensagens, {rel.midias} mídias, criptografado, "
                      f"{rel.amostra_conferida} mídias decifradas, {rel.anexos_presentes} anexos cruzados)")
        _, c = m.conferir_destino()
        assert not tem_bloqueio(c), [x.detalhe for x in c if x.status.value == "bloqueio"]
        m.restaurar(lambda p: None)
        assert m.estado.etapa == "concluido"
        linhas.append("restauração: ok")
        m.conferir_resultado()
        assert all(r["estado"] == "instalado" for r in m.conferir_resultado().values())
        linhas.append("conferência no iPhone novo: ok")

        # WhatsApp e WhatsApp Business no mesmo iPhone: cada um conferido separadamente.
        m = Migracao.nova(simulacao=True, cenario="dois_whatsapps")
        aparelhos = m.procurar()
        pasta = Path(tempfile.mkdtemp(prefix="migrador-autoteste-backup-"))
        assert not tem_bloqueio(m.conferir(aparelhos[0], aparelhos[1], pasta))
        m.criptografia_ativa()
        m.definir_senha("SENHA-TESTE", guardar=False)
        m.fazer_backup(lambda p: None, threading.Event())
        rel = m.verificar(lambda p, msg: None)
        assert rel.ok, rel.problemas
        assert [w["bundle"] for w in rel.whatsapps] == ["net.whatsapp.WhatsApp", "net.whatsapp.WhatsAppSMB"]
        assert all(w["banco_integro"] and w["amostra_conferida"] and not w["amostra_falhas"] for w in rel.whatsapps)
        _, c = m.conferir_destino()
        assert not tem_bloqueio(c)
        m.restaurar(lambda p: None)
        m.conferir_resultado()
        assert len(m.conferir_resultado()) == 2
        linhas.append("dois WhatsApps (comum + Business): ok")
        ok = True
    except Exception:
        linhas.append(traceback.format_exc())
    linhas.append("RESULTADO: " + ("SUCESSO" if ok else "FALHA"))
    texto = "\n".join(linhas)
    if saida:
        saida.write_text(texto, encoding="utf-8")
    else:
        print(texto)
    return ok
