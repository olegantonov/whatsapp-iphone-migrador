"""Gera o arquivo de versão do PyInstaller (Propriedades > Detalhes do .exe no Windows)."""
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from migrador.config import APP_NOME, EMPRESA  # noqa: E402
from migrador.versao import __version__  # noqa: E402

numeros = [int(n) for n in re.findall(r"\d+", __version__)][:4]
numeros += [0] * (4 - len(numeros))
tupla = tuple(numeros)

MODELO = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tupla}, prodvers={tupla}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', '{EMPRESA}'),
      StringStruct('FileDescription', '{APP_NOME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'MigradorWhatsApp'),
      StringStruct('LegalCopyright', '© 2026 {EMPRESA}. Software livre sob a GPL-3.0-or-later.'),
      StringStruct('OriginalFilename', 'MigradorWhatsApp.exe'),
      StringStruct('ProductName', '{APP_NOME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""

saida = Path(sys.argv[1])
saida.parent.mkdir(parents=True, exist_ok=True)
saida.write_text(MODELO, encoding="utf-8")
print(saida)
