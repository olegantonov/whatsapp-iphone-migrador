"""Confere se um backup do iPhone está completo e se o WhatsApp está dentro dele.

Nada aqui altera o backup. O que precisa ser decifrado (índice e banco de conversas) vai
para uma pasta temporária ao lado do backup, no mesmo disco, e é apagado no fim. A leitura
e a decifragem são feitas em blocos, para backups de centenas de GB não esgotarem a memória.

O que a verificação garante:
- todos os arquivos listados no índice existem e têm o tamanho esperado;
- o banco de conversas abre, passa no teste de integridade do SQLite e tem mensagens;
- cada anexo citado nas conversas foi procurado no backup (os ausentes são contados);
- uma amostra de mídias é decifrada por inteiro e tem o formato que a extensão indica;
- um inventário (tamanho e data de modificação de cada arquivo) é guardado; antes da
  restauração ele é refeito, e qualquer arquivo alterado, apagado ou novo bloqueia.
"""
import gzip
import hashlib
import logging
import os
import plistlib
import random
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from migrador.modelos import WHATSAPP_BUNDLES, RelatorioBackup, ResultadoWhatsApp, nome_whatsapp

log = logging.getLogger(__name__)

# Versão das regras de conferência. Um relatório de versão anterior exige verificar de novo.
# 3 (1.1.1): cada WhatsApp escolhido (comum e/ou Business) é conferido separadamente.
VERSAO_CONFERENCIA = 3

ARQUIVOS_METADADOS = ("Info.plist", "Manifest.plist", "Manifest.db", "Status.plist")
ARQUIVOS_IMPRESSAO = ARQUIVOS_METADADOS
BLOCO = 1024 * 1024
AMOSTRA_MIDIAS = 40
AMOSTRA_MAX_ARQUIVO = 50 * 1024**2
AMOSTRA_MAX_TOTAL = 600 * 1024**2

# Primeiros bytes esperados por extensão (o que a extensão promete, o conteúdo tem de cumprir).
ASSINATURAS = {
    ".jpg": [(0, b"\xff\xd8\xff")], ".jpeg": [(0, b"\xff\xd8\xff")],
    ".png": [(0, b"\x89PNG")], ".gif": [(0, b"GIF8")], ".webp": [(0, b"RIFF")],
    ".mp4": [(4, b"ftyp")], ".mov": [(4, b"ftyp"), (4, b"moov"), (4, b"wide"), (4, b"mdat")],
    ".m4a": [(4, b"ftyp")], ".heic": [(4, b"ftyp")], ".3gp": [(4, b"ftyp")],
    ".opus": [(0, b"OggS")], ".ogg": [(0, b"OggS")], ".pdf": [(0, b"%PDF")],
}


class SenhaIncorreta(Exception):
    pass


class FalhaDecifrar(Exception):
    pass


def dominio_grupo(bundle: str) -> str:
    return f"AppDomainGroup-group.{bundle}.shared"


def dominio_do_app(dominio: str, bundle: str) -> bool:
    """O domínio pertence a este app? Compara o identificador inteiro: "net.whatsapp.WhatsApp"
    não pode casar com "net.whatsapp.WhatsAppSMB" (o Business)."""
    return re.search(rf"(^|[-.]){re.escape(bundle)}($|\.)", dominio) is not None


# --- impressão digital e inventário --------------------------------------------------------

def impressao_digital(pasta: Path) -> str:
    """Hash dos arquivos de controle do backup (índice, manifesto, estado e Info.plist)."""
    h = hashlib.sha256()
    for nome in ARQUIVOS_IMPRESSAO:
        with open(pasta / nome, "rb") as fd:
            for bloco in iter(lambda: fd.read(BLOCO), b""):
                h.update(bloco)
    return h.hexdigest()


def _linhas_inventario(pasta: Path) -> Iterator[str]:
    """Uma linha por arquivo: caminho, tamanho e data de modificação (ns). Ordem estável."""
    for nome in ARQUIVOS_METADADOS:
        st = (pasta / nome).stat()
        yield f"{nome}\t{st.st_size}\t{st.st_mtime_ns}"
    subpastas = sorted((e for e in os.scandir(pasta) if e.is_dir() and len(e.name) == 2), key=lambda e: e.name)
    for sub in subpastas:
        for arq in sorted((e for e in os.scandir(sub.path) if e.is_file()), key=lambda e: e.name):
            st = arq.stat()
            yield f"{sub.name}/{arq.name}\t{st.st_size}\t{st.st_mtime_ns}"


def gerar_inventario(pasta: Path, destino: Path | None = None) -> tuple[str, int]:
    """Calcula o inventário; se `destino` for dado, grava a lista (gzip) para comparar depois."""
    h, total = hashlib.sha256(), 0
    saida = gzip.open(destino, "wt", encoding="utf-8") if destino else None
    try:
        for linha in _linhas_inventario(pasta):
            h.update(linha.encode() + b"\n")
            total += 1
            if saida:
                saida.write(linha + "\n")
    finally:
        if saida:
            saida.close()
    return h.hexdigest(), total


def _diferencas(pasta: Path, inventario_salvo: Path) -> tuple[int, int, int]:
    """(alterados, apagados, novos) entre o inventário salvo e a pasta agora."""
    with gzip.open(inventario_salvo, "rt", encoding="utf-8") as fd:
        antes = dict(linha.rstrip("\n").split("\t", 1) for linha in fd if linha.strip())
    agora = dict(linha.split("\t", 1) for linha in _linhas_inventario(pasta))
    alterados = sum(1 for k, v in antes.items() if k in agora and agora[k] != v)
    apagados = sum(1 for k in antes if k not in agora)
    novos = sum(1 for k in agora if k not in antes)
    return alterados, apagados, novos


def contar_arquivos_fisicos(pasta: Path) -> int:
    total = 0
    for sub in os.scandir(pasta):
        if sub.is_dir() and len(sub.name) == 2:
            total += sum(1 for f in os.scandir(sub.path) if f.is_file())
    return total


def conferir_antes_de_restaurar(pasta: Path, relatorio: RelatorioBackup,
                                inventario_salvo: Path | None = None) -> list[str]:
    """Confirma, sem senha, que nenhum arquivo do backup mudou desde a verificação:
    refaz o inventário de tamanho e data de todos os arquivos e compara."""
    if relatorio.versao_conferencia < VERSAO_CONFERENCIA:
        return ["Esta verificação foi feita por uma versão anterior do programa, que conferia menos coisas. "
                "Verifique o backup de novo."]
    problemas = [f"O arquivo {nome} sumiu da pasta do backup." for nome in ARQUIVOS_METADADOS
                 if not (pasta / nome).is_file()]
    if problemas:
        return problemas
    if impressao_digital(pasta) != relatorio.impressao_digital:
        problemas.append("Os arquivos de controle do backup mudaram depois da verificação. Verifique de novo.")
    digest, total = gerar_inventario(pasta)
    if digest != relatorio.inventario:
        detalhe = ""
        if inventario_salvo and inventario_salvo.exists():
            alterados, apagados, novos = _diferencas(pasta, inventario_salvo)
            detalhe = f" ({alterados} alterado(s), {apagados} apagado(s), {novos} novo(s))"
        else:
            detalhe = f" ({total} arquivos agora, {relatorio.inventario_arquivos} na verificação)"
        problemas.append(f"Arquivos do backup mudaram depois da verificação{detalhe}. Verifique de novo.")
    return problemas


def backup_completo_existe(pasta: Path | None) -> bool:
    """Existe um backup que terminou? Só a partir dele uma nova cópia é incremental; uma
    primeira cópia interrompida recomeça do zero (limitação do protocolo da Apple)."""
    if not pasta:
        return False
    try:
        if (pasta / "Manifest.plist").stat().st_size == 0 or not (pasta / "Manifest.db").is_file():
            return False
        status = plistlib.loads((pasta / "Status.plist").read_bytes())
        return status.get("SnapshotState") == "finished"
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False


def backup_pode_continuar(pasta: Path | None) -> bool:
    """Uma cópia que não terminou, mas deixou na pasta o índice (Manifest.plist, Manifest.db e
    Status.plist). Com eles o pymobiledevice3 pede ao iPhone um backup incremental, e o iPhone
    aproveita o que já está no PC. Sem eles, a próxima tentativa recomeça do zero."""
    if not pasta or backup_completo_existe(pasta):
        return False
    try:
        return all((pasta / nome).stat().st_size > 0 for nome in ("Manifest.plist", "Manifest.db", "Status.plist"))
    except OSError:
        return False


def situacao_da_pasta(pasta: Path | None) -> dict:
    """O que já existe na pasta antes de um backup, para saber pelos registros se o iPhone pôde
    continuar a cópia anterior: sem Manifest.plist, Manifest.db e Status.plist o pymobiledevice3
    pede um backup completo. Nada é apagado; os arquivos já copiados continuam na pasta."""
    if not pasta or not pasta.exists():
        return {"existe": False}
    d = {"existe": True}
    for nome in ("Manifest.plist", "Manifest.db", "Status.plist", "Info.plist"):
        try:
            d[nome] = (pasta / nome).stat().st_size
        except OSError:
            d[nome] = None
    try:
        status = plistlib.loads((pasta / "Status.plist").read_bytes())
        d["snapshot"] = status.get("SnapshotState")
        d["backup_state"] = status.get("BackupState")
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    d["snapshot_pendente"] = (pasta / "Snapshot").is_dir()
    return d


def ler_manifesto(pasta: Path) -> dict:
    return plistlib.loads((pasta / "Manifest.plist").read_bytes())


# --- decifragem em blocos ------------------------------------------------------------------

def _chave_do_arquivo(keybag, chave_embrulhada: bytes) -> bytes:
    from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
    if not chave_embrulhada or len(chave_embrulhada) < 28:
        raise FalhaDecifrar("arquivo sem chave de criptografia no índice")
    classe = int.from_bytes(chave_embrulhada[:4], "little")
    try:
        return aes_key_unwrap(keybag.get_key(classe), chave_embrulhada[4:])
    except Exception as e:
        raise FalhaDecifrar(f"chave do arquivo não abre ({type(e).__name__})") from e


def decifrar_fluxo(origem: Path, keybag, chave_embrulhada: bytes, destino=None,
                   tamanho_esperado: int | None = None, exigir_preenchimento: bool = True) -> bytes:
    """Decifra AES-256-CBC (IV zero) em blocos de 1 MB e confere o preenchimento PKCS7 no fim.
    Grava em `destino` (arquivo aberto) se dado. Devolve os primeiros 16 bytes decifrados."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    chave = _chave_do_arquivo(keybag, chave_embrulhada)
    decifrador = Cipher(algorithms.AES(chave), modes.CBC(b"\x00" * 16)).decryptor()
    inicio, pendente, escritos = b"", b"", 0
    with open(origem, "rb") as fd:
        tamanho_cifrado = os.fstat(fd.fileno()).st_size
        if tamanho_cifrado % 16:
            raise FalhaDecifrar("tamanho do arquivo cifrado não é múltiplo de 16 bytes")
        for bloco in iter(lambda: fd.read(BLOCO), b""):
            claro = pendente + decifrador.update(bloco)
            # Guarda o último bloco de 16 bytes: é nele que fica o preenchimento.
            pendente, pronto = claro[-16:], claro[:-16]
            if pronto:
                if not inicio:
                    inicio = pronto[:16]
                if destino:
                    destino.write(pronto)
                escritos += len(pronto)
    pendente += decifrador.finalize()
    if pendente:
        n = pendente[-1]
        if 1 <= n <= 16 and pendente.endswith(bytes([n]) * n):
            pendente = pendente[:-n]
        elif exigir_preenchimento:
            raise FalhaDecifrar("preenchimento inválido no fim do arquivo (dados corrompidos ou chave errada)")
    if not inicio:
        inicio = pendente[:16]
    if destino:
        destino.write(pendente)
    escritos += len(pendente)
    if tamanho_esperado is not None and escritos != tamanho_esperado:
        raise FalhaDecifrar(f"tamanho decifrado {escritos} difere do esperado {tamanho_esperado}")
    return inicio


def _extrair(entrada, destino: Path, keybag) -> None:
    with open(destino, "wb") as saida:
        if keybag is None:
            with open(entrada.real_path, "rb") as fd:
                shutil.copyfileobj(fd, saida, BLOCO)
        else:
            decifrar_fluxo(entrada.real_path, keybag, entrada.encryption_key, saida, entrada.size)


class _BackupAberto:
    """O mínimo do pyiosbackup de que precisamos, sem carregar o índice inteiro na memória."""

    def __init__(self, pasta: Path, senha: str, tmp: Path):
        from packaging.version import Version
        from pyiosbackup.backup import Backup
        from pyiosbackup.keybag import Keybag
        from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
        from pyiosbackup.manifest_plist import ManifestPlist

        manifesto = ManifestPlist.from_path(pasta / ManifestPlist.NAME)
        if manifesto.product_version <= Version("10.2"):
            raise ValueError("backup de iOS 10.2 ou anterior não é suportado")
        self.keybag = None
        if manifesto.is_encrypted:
            try:
                self.keybag = Keybag.from_manifest(manifesto, senha)
            except Exception as e:
                raise SenhaIncorreta("Senha do backup incorreta.") from e
        indice = tmp / "Manifest-decifrado.db"
        if self.keybag:
            with open(indice, "wb") as saida:
                try:
                    decifrar_fluxo(pasta / "Manifest.db", self.keybag, manifesto.manifest_key, saida,
                                   exigir_preenchimento=False)
                except FalhaDecifrar as e:
                    raise SenhaIncorreta("Senha do backup incorreta.") from e
        else:
            shutil.copyfile(pasta / "Manifest.db", indice)
        self.db = ManifestDbSqlite3(indice)
        try:
            self.db._conn.execute("SELECT COUNT(*) FROM Files").fetchone()
        except sqlite3.DatabaseError as e:
            self.fechar()
            if self.keybag:
                raise SenhaIncorreta("Senha do backup incorreta.") from e
            raise
        info = plistlib.loads((pasta / "Info.plist").read_bytes())
        status = plistlib.loads((pasta / "Status.plist").read_bytes())
        self.backup = Backup(pasta, self.db, manifesto, status, info, self.keybag)

    def total(self) -> int:
        return self.db._conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0]

    def entradas(self):
        from pyiosbackup.entry import Entry
        # Cursor percorrido aos poucos (o pyiosbackup faria fetchall de tudo).
        for linha in self.db._conn.execute("SELECT * FROM Files"):
            yield Entry(self.backup, **self.db._load_entry(linha))

    def fechar(self) -> None:
        try:
            self.db._conn.close()
        except Exception:
            pass


# --- verificação ---------------------------------------------------------------------------

def verificar_backup(
    pasta: Path,
    senha: str = "",
    progresso: Callable[[float, str], None] | None = None,
    exigir_criptografia: bool = False,
    inventario_destino: Path | None = None,
    bundles: list[str] | None = None,
) -> RelatorioBackup:
    """`bundles`: WhatsApps que precisam estar no backup e ser conferidos (comum, Business ou os
    dois). None = todos os que estiverem no backup."""
    progresso = progresso or (lambda p, m: None)
    rel = RelatorioBackup(ok=False, versao_conferencia=VERSAO_CONFERENCIA)

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
    if exigir_criptografia and not rel.criptografado:
        rel.problemas.append("O backup saiu SEM criptografia, embora o backup com senha estivesse ativado. Sem "
                             "criptografia as senhas salvas e os dados de Saúde não vão, e o resultado não é o "
                             "esperado. Faça o backup de novo.")
        return rel
    if rel.criptografado and not senha:
        rel.problemas.append("O backup tem senha e ela não foi informada.")
        return rel

    progresso(0, "Abrindo o índice do backup…")
    # Temporários no mesmo disco do backup (o C: pode não ter espaço para o banco de conversas).
    tmp = Path(tempfile.mkdtemp(prefix=".verificacao-", dir=pasta.parent))
    try:
        try:
            aberto = _BackupAberto(pasta, senha if rel.criptografado else "", tmp)
        except SenhaIncorreta:
            raise
        except Exception as e:
            log.exception("falha ao abrir backup")
            rel.problemas.append(f"Não foi possível ler o índice do backup ({e}).")
            return rel
        try:
            _percorrer(aberto, rel, progresso, tmp, bundles)
        finally:
            aberto.fechar()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    progresso(96, "Registrando o inventário de todos os arquivos…")
    rel.impressao_digital = impressao_digital(pasta)
    rel.inventario, rel.inventario_arquivos = gerar_inventario(pasta, inventario_destino)
    rel.ok = not rel.problemas
    progresso(100, "Verificação concluída.")
    log.info("verificação: %s", {k: v for k, v in rel.para_dict().items()
                                 if k not in ("impressao_digital", "inventario")})
    return rel


def _percorrer(aberto: _BackupAberto, rel: RelatorioBackup, progresso, tmp: Path,
               escolhidos: list[str] | None) -> None:
    total = aberto.total()
    grupos = {dominio_grupo(b): b for b in WHATSAPP_BUNDLES}
    bundles_encontrados: set[str] = set()
    entradas_chat: dict[str, dict] = {}
    midias_por_bundle: dict[str, set[str]] = {}
    amostra: dict[str, list] = {}
    vistos_amostra: dict[str, int] = {}
    bytes_por_bundle: dict[str, int] = {}
    contagem_midias: dict[str, tuple[int, int]] = {}
    tamanho_errado = 0
    rnd = random.Random()

    for i, entrada in enumerate(aberto.entradas()):
        if i % 2000 == 0:
            frac = (i / total) if total else 0
            progresso(frac * 70, f"Conferindo arquivos… {i:,} de {total:,}".replace(",", "."))
        if not entrada.is_file():
            continue
        rel.arquivos += 1
        rel.bytes_total += entrada.size
        try:
            tamanho_disco = entrada.real_path.stat().st_size
        except FileNotFoundError:
            rel.arquivos_faltando += 1
            continue
        # Backups cifrados guardam o arquivo com preenchimento de 1 a 16 bytes.
        if rel.criptografado:
            errado = tamanho_disco < entrada.size
        else:
            errado = tamanho_disco != entrada.size
        if errado:
            tamanho_errado += 1

        for b in WHATSAPP_BUNDLES:
            if dominio_do_app(entrada.domain, b):
                bytes_por_bundle[b] = bytes_por_bundle.get(b, 0) + entrada.size
        bundle = grupos.get(entrada.domain)
        if bundle:
            bundles_encontrados.add(bundle)
            caminho = entrada.relative_path
            if caminho.startswith("Message/Media/"):
                n_midias, b_midias = contagem_midias.get(bundle, (0, 0))
                contagem_midias[bundle] = (n_midias + 1, b_midias + entrada.size)
                midias_por_bundle.setdefault(bundle, set()).add(caminho)
                if 0 < entrada.size <= AMOSTRA_MAX_ARQUIVO and not errado:
                    # Amostragem uniforme sem guardar todas as entradas (reservoir sampling).
                    n = vistos_amostra.get(bundle, 0) + 1
                    vistos_amostra[bundle] = n
                    lista = amostra.setdefault(bundle, [])
                    if len(lista) < AMOSTRA_MIDIAS:
                        lista.append(entrada)
                    else:
                        j = rnd.randrange(n)
                        if j < AMOSTRA_MIDIAS:
                            lista[j] = entrada
            elif caminho.startswith("ChatStorage.sqlite"):
                entradas_chat.setdefault(bundle, {})[caminho] = entrada

    if rel.arquivos_faltando:
        rel.problemas.append(f"{rel.arquivos_faltando} arquivo(s) listados no índice não estão na pasta do backup.")
    if tamanho_errado:
        rel.problemas.append(f"{tamanho_errado} arquivo(s) do backup estão com tamanho diferente do esperado.")

    com_banco = [b for b in WHATSAPP_BUNDLES if "ChatStorage.sqlite" in entradas_chat.get(b, {})]
    rel.whatsapps_no_backup = [b for b in WHATSAPP_BUNDLES if b in bundles_encontrados]
    alvo = list(escolhidos) if escolhidos else com_banco
    if not alvo:
        if bundles_encontrados:
            rel.problemas.append("O WhatsApp aparece no backup, mas o banco de conversas (ChatStorage.sqlite) não.")
        else:
            rel.problemas.append("O WhatsApp não está neste backup.")
        return

    for b in rel.whatsapps_no_backup:
        if b not in alvo:
            rel.avisos.append(f"O {nome_whatsapp(b)} também está no backup e vai para o iPhone novo, mas não foi "
                              "conferido porque não foi escolhido.")
    # Mensagens com o nome do app quando houver mais de um no backup, para não haver dúvida de qual falhou.
    com_nome = len(alvo) > 1 or len(rel.whatsapps_no_backup) > 1
    passo = 28 / len(alvo)
    for k, bundle in enumerate(alvo):
        r = ResultadoWhatsApp(bundle=bundle, nome=nome_whatsapp(bundle))
        r.midias, r.bytes_midia = contagem_midias.get(bundle, (0, 0))
        r.bytes_whatsapp = bytes_por_bundle.get(bundle, 0)
        base = 70 + k * passo
        if bundle not in bundles_encontrados:
            r.problemas.append(f"O {r.nome} foi escolhido, mas não está no backup.")
        elif bundle not in com_banco:
            r.problemas.append(f"O {r.nome} aparece no backup, mas o banco de conversas (ChatStorage.sqlite) não.")
        else:
            progresso(base, f"{r.nome}: testando uma amostra de fotos, vídeos e áudios…")
            _testar_amostra(amostra.get(bundle, []), aberto.keybag, r)
            progresso(base + passo / 3, f"{r.nome}: abrindo o banco de conversas…")
            _conferir_banco(entradas_chat[bundle], r, aberto.keybag, tmp / bundle,
                            midias_por_bundle.get(bundle, set()), progresso, base + passo / 2)
        _juntar(rel, r, com_nome)


def _juntar(rel: RelatorioBackup, r: ResultadoWhatsApp, com_nome: bool) -> None:
    prefixo = f"{r.nome}: " if com_nome else ""
    rel.problemas += [prefixo + p for p in r.problemas]
    rel.avisos += [prefixo + a for a in r.avisos]
    rel.whatsapps.append(r.para_dict())
    if not rel.whatsapp_bundle:
        rel.whatsapp_bundle = r.bundle
    # Totais (somados) para telas e eventos que mostram um número só.
    rel.conversas += r.conversas
    rel.mensagens += r.mensagens
    rel.midias += r.midias
    rel.bytes_midia += r.bytes_midia
    rel.bytes_whatsapp += r.bytes_whatsapp
    rel.anexos_referenciados += max(r.anexos_referenciados, 0)
    rel.anexos_presentes += r.anexos_presentes
    rel.amostra_conferida += r.amostra_conferida
    rel.amostra_falhas += r.amostra_falhas
    rel.banco_integro = all(w["banco_integro"] for w in rel.whatsapps)


def _testar_amostra(entradas: list, keybag, r: ResultadoWhatsApp) -> None:
    """Decifra cada mídia da amostra por inteiro (prova que a chave e os dados estão bons) e
    confere se o começo do arquivo combina com a extensão."""
    falhas = formato = total_bytes = 0
    for entrada in entradas:
        if total_bytes + entrada.size > AMOSTRA_MAX_TOTAL:
            break
        total_bytes += entrada.size
        try:
            if keybag is None:
                with open(entrada.real_path, "rb") as fd:
                    inicio = fd.read(16)
            else:
                inicio = decifrar_fluxo(entrada.real_path, keybag, entrada.encryption_key,
                                        tamanho_esperado=entrada.size)
        except (FalhaDecifrar, OSError) as e:
            falhas += 1
            log.warning("mídia da amostra não decifrou (%s): %s", r.nome, e)
            continue
        r.amostra_conferida += 1
        esperadas = ASSINATURAS.get(Path(entrada.relative_path).suffix.lower())
        if esperadas and not any(inicio[o:o + len(m)] == m for o, m in esperadas):
            formato += 1
    r.amostra_falhas = falhas
    if falhas:
        r.problemas.append(f"{falhas} de {falhas + r.amostra_conferida} mídias testadas não puderam ser "
                           "decifradas: o backup está danificado. Faça o backup de novo.")
    if formato:
        r.avisos.append(f"{formato} de {r.amostra_conferida} mídias testadas têm conteúdo diferente do que a "
                        "extensão indica. Isso pode ser normal no WhatsApp, mas vale conferir as mídias depois.")


def _normalizar_anexo(caminho: str) -> str:
    """No banco o caminho é relativo à pasta Message/ ("Media/…"); no backup ele começa em "Message/"."""
    caminho = caminho.replace("\\", "/").lstrip("/")
    i = caminho.find("Message/")
    if i >= 0 and (i == 0 or caminho[i - 1] == "/"):
        return caminho[i:]
    return "Message/" + caminho


def _conferir_banco(entradas: dict, r: ResultadoWhatsApp, keybag, tmp: Path, midias: set[str], progresso,
                    pct: float) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for nome, entrada in entradas.items():
            _extrair(entrada, tmp / nome, keybag)
    except FalhaDecifrar as e:
        r.banco_integro = False
        r.problemas.append(f"O banco de conversas não pôde ser decifrado ({e}).")
        return
    except OSError as e:
        r.problemas.append(f"Não consegui copiar o banco de conversas para conferir ({e}). Confira o espaço "
                           "livre no disco do backup.")
        return
    con = None
    try:
        con = sqlite3.connect(tmp / "ChatStorage.sqlite")
        progresso(pct, f"{r.nome}: testando a integridade do banco de conversas…")
        resultado = con.execute("PRAGMA quick_check").fetchone()[0]
        r.banco_integro = resultado == "ok"
        if not r.banco_integro:
            r.problemas.append(f"O banco de conversas está corrompido ({resultado}).")
            return
        r.conversas = con.execute("SELECT COUNT(*) FROM ZWACHATSESSION").fetchone()[0]
        r.mensagens = con.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0]
        _cruzar_anexos(con, midias, r)
    except sqlite3.DatabaseError as e:
        r.banco_integro = False
        r.problemas.append(f"Não foi possível ler o banco de conversas ({e}).")
        return
    finally:
        if con:
            con.close()
        # Libera o espaço logo (o banco de cada app pode ter GB).
        shutil.rmtree(tmp, ignore_errors=True)

    if r.mensagens == 0:
        r.problemas.append("O banco de conversas foi encontrado, mas não tem nenhuma mensagem.")
    if r.midias == 0:
        r.avisos.append("Nenhuma foto, vídeo ou áudio foi encontrado no backup.")


def _cruzar_anexos(con: sqlite3.Connection, midias: set[str], r: ResultadoWhatsApp) -> None:
    """Cada anexo que as conversas citam está no backup? Só contagens: os caminhos têm números
    de telefone dos contatos e não vão para o log."""
    try:
        cursor = con.execute("SELECT ZMEDIALOCALPATH FROM ZWAMEDIAITEM "
                             "WHERE ZMEDIALOCALPATH IS NOT NULL AND ZMEDIALOCALPATH != ''")
    except sqlite3.DatabaseError:
        r.avisos.append("Não consegui cruzar os anexos com as conversas (o banco tem outro formato).")
        r.anexos_referenciados = -1
        return
    for (caminho,) in cursor:
        r.anexos_referenciados += 1
        if _normalizar_anexo(caminho) in midias:
            r.anexos_presentes += 1
    ausentes = r.anexos_referenciados - r.anexos_presentes
    if r.anexos_referenciados and not r.anexos_presentes:
        r.problemas.append(f"As conversas citam {r.anexos_referenciados} anexos e nenhum deles está no backup. "
                           "As mídias não foram copiadas; faça o backup de novo.")
    elif ausentes:
        pct = ausentes * 100 / r.anexos_referenciados
        r.avisos.append(
            f"{ausentes} de {r.anexos_referenciados} anexos citados nas conversas ({pct:.1f}%) não estão no "
            "backup. Normalmente são mídias que já não estão no iPhone antigo (apagadas para liberar espaço ou "
            "nunca baixadas). Antes de continuar, abra no iPhone antigo uma conversa antiga com fotos e confira "
            "se elas aparecem.")
