"""Atualização automática pelas Releases do GitHub.

Fluxo: consulta a última release → compara com a versão instalada → baixa o .zip →
confere o SHA-256 publicado junto → um script PowerShell espera o programa fechar,
troca os arquivos e abre a versão nova. Os dados da migração ficam em
%LOCALAPPDATA%\\MigradorWhatsApp e não são tocados.
"""
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from migrador.config import NOME_EXECUTAVEL, NOME_PACOTE_RELEASE, REPOSITORIO, empacotado, pasta_programa
from migrador.versao import __version__

log = logging.getLogger(__name__)

API = f"https://api.github.com/repos/{REPOSITORIO}/releases/latest"


@dataclass
class Lancamento:
    versao: str
    notas: str
    url_zip: str
    url_sha256: str | None


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"MigradorWhatsApp/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def interpretar(dados: dict) -> Lancamento | None:
    tag = str(dados.get("tag_name", "")).lstrip("vV")
    if not tag or dados.get("draft") or dados.get("prerelease"):
        return None
    ativos = {a.get("name"): a.get("browser_download_url") for a in dados.get("assets", [])}
    if NOME_PACOTE_RELEASE not in ativos:
        return None
    return Lancamento(versao=tag, notas=dados.get("body") or "", url_zip=ativos[NOME_PACOTE_RELEASE],
                      url_sha256=ativos.get(NOME_PACOTE_RELEASE + ".sha256"))


def mais_nova(candidata: str, atual: str = __version__) -> bool:
    try:
        return Version(candidata) > Version(atual)
    except InvalidVersion:
        return False


def buscar() -> Lancamento | None:
    """Última release, só se for mais nova que a instalada."""
    try:
        lanc = interpretar(json.loads(_get(API)))
    except Exception as e:
        log.info("sem informação de atualização: %s", e)
        return None
    if lanc and mais_nova(lanc.versao):
        return lanc
    return None


def pode_atualizar_sozinho() -> bool:
    if not empacotado():
        return False
    try:
        teste = pasta_programa() / ".teste-escrita"
        teste.write_text("ok")
        teste.unlink()
        return True
    except OSError:
        return False


def baixar(lanc: Lancamento, progresso=lambda f: None) -> Path:
    destino = Path(tempfile.mkdtemp(prefix="migrador-atualizacao-")) / NOME_PACOTE_RELEASE
    req = urllib.request.Request(lanc.url_zip, headers={"User-Agent": f"MigradorWhatsApp/{__version__}"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(destino, "wb") as fd:
        total = int(r.headers.get("Content-Length") or 0)
        lido = 0
        while bloco := r.read(1024 * 256):
            fd.write(bloco)
            h.update(bloco)
            lido += len(bloco)
            if total:
                progresso(lido / total)
    if lanc.url_sha256:
        esperado = _get(lanc.url_sha256).decode().split()[0].strip().lower()
        if h.hexdigest() != esperado:
            destino.unlink(missing_ok=True)
            raise RuntimeError("O arquivo baixado não confere com o publicado (SHA-256). Atualização cancelada.")
    else:
        raise RuntimeError("A versão publicada não tem o arquivo de conferência SHA-256. Atualização cancelada.")
    if not zipfile.is_zipfile(destino):
        raise RuntimeError("Pacote de atualização inválido.")
    return destino


SCRIPT = r"""
param([int]$Pid_, [string]$Zip, [string]$Destino, [string]$Exe, [int]$Reabrir = 1)
$ErrorActionPreference = 'Stop'
$log = Join-Path $env:LOCALAPPDATA 'MigradorWhatsApp\logs\atualizacao.log'
function Log($m) { Add-Content -Path $log -Value "$(Get-Date -Format s) $m" -Encoding UTF8 }
try {
  Log "aguardando o programa (PID $Pid_) fechar"
  try { Wait-Process -Id $Pid_ -Timeout 120 -ErrorAction SilentlyContinue } catch {}
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("migrador-novo-" + [guid]::NewGuid())
  New-Item -ItemType Directory $tmp | Out-Null
  # tar.exe (nativo desde o Windows 10 1803) é muito mais rápido que Expand-Archive.
  tar.exe -xf $Zip -C $tmp
  if ($LASTEXITCODE -ne 0) { Expand-Archive -Path $Zip -DestinationPath $tmp -Force }
  $origem = Get-ChildItem $tmp -Recurse -Filter 'MigradorWhatsApp.exe' | Select-Object -First 1
  if (-not $origem) { throw 'pacote sem MigradorWhatsApp.exe' }
  $origemDir = $origem.Directory.FullName
  # Guarda a versão atual ao lado; se a cópia falhar, ela volta. Só mexe nos arquivos
  # do próprio programa, nunca no resto da pasta onde ele está.
  $interno = Join-Path $Destino '_internal'
  $velho = Join-Path $Destino '_internal.anterior'
  $exeAtual = Join-Path $Destino 'MigradorWhatsApp.exe'
  if (Test-Path $velho) { Remove-Item $velho -Recurse -Force }
  if (Test-Path $interno) { Rename-Item $interno $velho }
  if (Test-Path $exeAtual) { Copy-Item $exeAtual "$exeAtual.anterior" -Force }
  robocopy $origemDir $Destino /E /R:5 /W:2 /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) {
    Log "cópia falhou ($LASTEXITCODE); voltando à versão anterior"
    if (Test-Path $interno) { Remove-Item $interno -Recurse -Force }
    if (Test-Path $velho) { Rename-Item $velho $interno }
    if (Test-Path "$exeAtual.anterior") { Copy-Item "$exeAtual.anterior" $exeAtual -Force }
    throw "robocopy falhou"
  }
  Remove-Item $velho -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item "$exeAtual.anterior" -Force -ErrorAction SilentlyContinue
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Log "atualização aplicada"
} catch {
  Log "ERRO: $_"
}
if ($Reabrir -eq 1) { Start-Process -FilePath $Exe }
"""


def aplicar_e_reiniciar(zip_path: Path, reabrir: bool = True) -> None:
    """Dispara o script de troca e encerra o programa atual."""
    script = zip_path.parent / "aplicar-atualizacao.ps1"
    script.write_text(SCRIPT, encoding="utf-8-sig")
    destino = pasta_programa()
    exe = destino / NOME_EXECUTAVEL
    comando = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-Pid_", str(os.getpid()), "-Zip", str(zip_path), "-Destino", str(destino), "-Exe", str(exe),
               "-Reabrir", "1" if reabrir else "0"]
    # Sem DETACHED_PROCESS: com ela o PowerShell encerra sem executar o script.
    # BREAKAWAY_FROM_JOB evita que o script morra junto com o programa quando este roda
    # dentro de um "job" do Windows; se o job não permitir, tenta sem.
    sem_janela = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(comando, creationflags=sem_janela | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(comando, creationflags=sem_janela, close_fds=True)
    log.info("atualização disparada; encerrando")
    os._exit(0)


def instalado_em() -> str:
    return str(pasta_programa()) if empacotado() else f"código-fonte ({sys.executable})"
