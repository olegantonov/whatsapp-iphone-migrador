# Instala (ou atualiza) o Migrador de WhatsApp para iPhone a partir da última versão publicada.
# Uso, no PowerShell:
#   irm https://raw.githubusercontent.com/olegantonov/whatsapp-iphone-migrador/main/instalar.ps1 | iex
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repo = 'olegantonov/whatsapp-iphone-migrador'
$pacote = 'MigradorWhatsApp-win64.zip'
$destino = Join-Path $env:LOCALAPPDATA 'Programs\MigradorWhatsApp'

Write-Host "Consultando a última versão..." -ForegroundColor Cyan
$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'instalador-migrador' }
$zipUrl = ($rel.assets | Where-Object name -eq $pacote).browser_download_url
$shaUrl = ($rel.assets | Where-Object name -eq "$pacote.sha256").browser_download_url
if (-not $zipUrl -or -not $shaUrl) { throw "A versão $($rel.tag_name) não tem o pacote para Windows." }

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("migrador-instalar-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
$zip = Join-Path $tmp $pacote
Write-Host "Baixando $($rel.tag_name)..." -ForegroundColor Cyan
Invoke-WebRequest $zipUrl -OutFile $zip -UseBasicParsing
# O GitHub entrega o .sha256 como binário; baixar para arquivo e ler como texto.
$shaArq = "$zip.sha256"
Invoke-WebRequest $shaUrl -OutFile $shaArq -UseBasicParsing
$esperado = ((Get-Content $shaArq -Raw).Trim() -split '\s+')[0].ToLower()
$obtido = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($esperado -ne $obtido) { throw "O arquivo baixado não confere com o publicado (SHA-256). Instalação cancelada." }

if (Get-Process -Name 'MigradorWhatsApp' -ErrorAction SilentlyContinue) {
    throw "Feche o Migrador de WhatsApp antes de instalar."
}

Expand-Archive $zip -DestinationPath $tmp -Force
$origem = Join-Path $tmp 'MigradorWhatsApp'
New-Item -ItemType Directory $destino -Force | Out-Null
$interno = Join-Path $destino '_internal'
if (Test-Path $interno) { Remove-Item $interno -Recurse -Force }
robocopy $origem $destino /E /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Falha ao copiar os arquivos ($LASTEXITCODE)." }
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$exe = Join-Path $destino 'MigradorWhatsApp.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($pasta in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {
    $atalho = $shell.CreateShortcut((Join-Path $pasta 'Migrador de WhatsApp.lnk'))
    $atalho.TargetPath = $exe
    $atalho.WorkingDirectory = $destino
    $atalho.IconLocation = "$exe,0"
    $atalho.Description = 'Transferir o WhatsApp de um iPhone para outro'
    $atalho.Save()
}

Write-Host "Instalado em $destino ($($rel.tag_name))." -ForegroundColor Green
Write-Host "Atalho criado na Área de Trabalho e no menu Iniciar. Abrindo..." -ForegroundColor Green
Start-Process $exe

$global:LASTEXITCODE = 0
