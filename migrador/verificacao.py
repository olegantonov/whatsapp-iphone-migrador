"""Confere se um backup do iPhone está completo e se o WhatsApp está dentro dele.

Nada aqui altera o backup: tudo é lido e, quando preciso, decifrado numa pasta temporária.
"""
import hashlib
import logging
import os
import plistlib
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

from migrador.modelos import WHATSAPP_BUNDLES, RelatorioBackup

log = logging.getLogger(__name__)

ARQUIVOS_METADADOS = ("Info.plist", "Manifest.plist", "Manifest.db", "Status.plist")
ARQUIVOS_IMPRESSAO = ("Manifest.db", "Manifest.plist", "Status.plist")


class SenhaIncorreta(Exception):
    pass


def dominio_grupo(bundle: str) -> str:
    return f"AppDomainGroup-group.{bundle}.shared"


def impressao_digital(pasta: Path) -> str:
    """Hash dos arquivos-índice do backup. Qualquer backup novo ou alteração muda o valor."""
    h = hashlib.sha256()
    for nome in ARQUIVOS_IMPRESSAO:
        with open(pasta / nome, "rb") as fd:
            for bloco in iter(lambda: fd.read(1024 * 1024), b""):
                h.update(bloco)
    return h.hexdigest()


def contar_arquivos_fisicos(pasta: Path) -> int:
    total = 0
    for sub in os.scandir(pasta):
        if sub.is_dir() and len(sub.name) == 2:
            total += sum(1 for f in os.scandir(sub.path) if f.is_file())
    return total


def conferir_antes_de_restaurar(pasta: Path, relatorio: RelatorioBackup) -> list[str]:
    """Checagem rápida (sem senha) de que o backup verificado não mudou desde a verificação."""
    problemas = []
    for nome in ARQUIVOS_METADADOS:
        if not (pasta / nome).is_file():
            problemas.append(f"O arquivo {nome} sumiu da pasta do backup.")
    if problemas:
        return problemas
    if impressao_digital(pasta) != relatorio.impressao_digital:
        problemas.append("O backup foi alterado depois da verificação. Refaça a verificação.")
    fisicos = contar_arquivos_fisicos(pasta)
    if fisicos < relatorio.arquivos:
        problemas.append(
            f"Faltam arquivos na pasta do backup ({fisicos} encontrados, {relatorio.arquivos} esperados)."
        )
    return problemas


def ler_manifesto(pasta: Path) -> dict:
    return plistlib.loads((pasta / "Manifest.plist").read_bytes())


def verificar_backup(
    pasta: Path,
    senha: str = "",
    progresso: Callable[[float, str], None] | None = None,
) -> RelatorioBackup:
    progresso = progresso or (lambda p, m: None)
    rel = RelatorioBackup(ok=False)

    for nome in ARQUIVOS_METADADOS:
        arq = pasta / nome
        if not arq.is_file() or arq.stat().st_size == 0:
            rel.problemas.append(f"O backup está incompleto: falta {nome}.")
    if rel.problemas:
        return rel

    try:
        status = plistlib.loads((pasta / "Status.plist").read_bytes())
        manifesto = ler_manifesto(pasta)
    except Exception as e:  # plist corrompido
        rel.problemas.append(f"Os arquivos de controle do backup estão corrompidos ({e}).")
        return rel

    if status.get("SnapshotState") != "finished":
        rel.problemas.append("O backup não chegou ao fim. Faça o backup de novo.")
        return rel

    rel.criptografado = bool(manifesto.get("IsEncrypted"))
    rel.ios_backup = manifesto.get("Lockdown", {}).get("ProductVersion", "")
    if rel.criptografado and not senha:
        rel.problemas.append("O backup tem senha e ela não foi informada.")
        return rel

    progresso(0, "Abrindo o índice do backup…")
    from pyiosbackup import Backup

    try:
        backup = Backup.from_path(pasta, senha if rel.criptografado else "")
    except Exception as e:
        log.exception("falha ao abrir backup")
        if rel.criptografado:
            raise SenhaIncorreta("Senha do backup incorreta.") from e
        rel.problemas.append(f"Não foi possível ler o índice do backup ({e}).")
        return rel

    try:
        _percorrer(backup, rel, progresso)
    finally:
        _fechar(backup)

    progresso(97, "Calculando a impressão digital do backup…")
    rel.impressao_digital = impressao_digital(pasta)
    rel.ok = not rel.problemas
    progresso(100, "Verificação concluída.")
    log.info("verificação: %s", {k: v for k, v in rel.para_dict().items() if k != "impressao_digital"})
    return rel


def _fechar(backup) -> None:
    """O pyiosbackup deixa uma cópia decifrada do índice na pasta temporária; apagamos."""
    db = getattr(backup, "_manifest_db", None)
    try:
        db._conn.close()
    except Exception:
        pass
    caminho = getattr(db, "path", None)
    if caminho:
        Path(caminho).unlink(missing_ok=True)


def _percorrer(backup, rel: RelatorioBackup, progresso) -> None:
    total = _contar_entradas(backup)
    bundles_encontrados: set[str] = set()
    grupos = {dominio_grupo(b): b for b in WHATSAPP_BUNDLES}
    entradas_chat = {}
    tamanho_errado = 0

    for i, entrada in enumerate(backup.iter_entries()):
        if i % 2000 == 0:
            frac = (i / total) if total else 0
            progresso(frac * 80, f"Conferindo arquivos… {i:,} de {total:,}".replace(",", "."))
        if not entrada.is_file():
            continue
        rel.arquivos += 1
        rel.bytes_total += entrada.size
        caminho = entrada.real_path
        try:
            tamanho_disco = caminho.stat().st_size
        except FileNotFoundError:
            rel.arquivos_faltando += 1
            continue
        # Backups cifrados guardam o arquivo com preenchimento de até 16 bytes.
        if (rel.criptografado and tamanho_disco < entrada.size) or (
            not rel.criptografado and tamanho_disco != entrada.size
        ):
            tamanho_errado += 1

        bundle = grupos.get(entrada.domain)
        if bundle:
            bundles_encontrados.add(bundle)
            if entrada.relative_path.startswith("Message/Media/"):
                rel.midias += 1
                rel.bytes_midia += entrada.size
            elif entrada.relative_path.startswith("ChatStorage.sqlite"):
                entradas_chat.setdefault(bundle, {})[entrada.relative_path] = entrada

    if rel.arquivos_faltando:
        rel.problemas.append(f"{rel.arquivos_faltando} arquivo(s) listados no índice não estão na pasta do backup.")
    if tamanho_errado:
        rel.problemas.append(f"{tamanho_errado} arquivo(s) do backup estão com tamanho diferente do esperado.")

    bundle = next((b for b in WHATSAPP_BUNDLES if "ChatStorage.sqlite" in entradas_chat.get(b, {})), None)
    if not bundle:
        if bundles_encontrados:
            rel.problemas.append("O WhatsApp aparece no backup, mas o banco de conversas (ChatStorage.sqlite) não.")
        else:
            rel.problemas.append("O WhatsApp não está neste backup.")
    else:
        rel.whatsapp_bundle = bundle
        progresso(85, "Abrindo o banco de conversas do WhatsApp…")
        _conferir_banco(entradas_chat[bundle], rel)


def _contar_entradas(backup) -> int:
    try:
        return backup._manifest_db._conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0]
    except Exception:
        return 0


def _conferir_banco(entradas: dict, rel: RelatorioBackup) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="migrador-chat-"))
    try:
        for nome, entrada in entradas.items():
            (tmp / nome).write_bytes(entrada.read_bytes())
        con = sqlite3.connect(tmp / "ChatStorage.sqlite")
        try:
            resultado = con.execute("PRAGMA quick_check").fetchone()[0]
            rel.banco_integro = resultado == "ok"
            if not rel.banco_integro:
                rel.problemas.append(f"O banco de conversas do WhatsApp está corrompido ({resultado}).")
                return
            rel.conversas = con.execute("SELECT COUNT(*) FROM ZWACHATSESSION").fetchone()[0]
            rel.mensagens = con.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        rel.banco_integro = False
        rel.problemas.append(f"Não foi possível ler o banco de conversas do WhatsApp ({e}).")
        return
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if rel.mensagens == 0:
        rel.problemas.append("O banco do WhatsApp foi encontrado, mas não tem nenhuma mensagem.")
    if rel.midias == 0:
        rel.avisos.append("Nenhuma foto, vídeo ou áudio do WhatsApp foi encontrado no backup.")
