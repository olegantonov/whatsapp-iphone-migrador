# Gera dist\MigradorWhatsApp-win64.zip (+ .sha256). Usado localmente e no GitHub Actions.
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Documentos legais que vão dentro do executável e metadados do .exe (empresa, versão).
New-Item -ItemType Directory assets\legal -Force | Out-Null
Copy-Item LICENSE assets\legal\LICENCA.txt -Force
& $Python tools\gerar_terceiros.py assets\legal\TERCEIROS.txt
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar a lista de licenças de terceiros" }
& $Python tools\versao_windows.py build\versao_windows.txt
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o arquivo de versão" }

& $Python -m PyInstaller --noconfirm --clean --windowed `
    --name MigradorWhatsApp `
    --icon assets\icone.ico `
    --version-file build\versao_windows.txt `
    --add-data "assets;assets" `
    --collect-all customtkinter `
    --collect-all pymobiledevice3 `
    --collect-all pyiosbackup `
    --collect-submodules bpylist2 `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

# Autoteste do executável gerado: a migração simulada completa precisa passar.
$resultado = Join-Path $env:TEMP "migrador-autoteste.txt"
Remove-Item $resultado -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "dist\MigradorWhatsApp\MigradorWhatsApp.exe" -ArgumentList "--autoteste","--saida",$resultado -Wait -PassThru
Get-Content $resultado
if ($p.ExitCode -ne 0) { throw "Autoteste do executável falhou" }

$zip = "dist\MigradorWhatsApp-win64.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\MigradorWhatsApp" -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zip.sha256" -Value "$hash  MigradorWhatsApp-win64.zip" -Encoding ascii -NoNewline
Write-Host "Pacote: $zip ($hash)"
