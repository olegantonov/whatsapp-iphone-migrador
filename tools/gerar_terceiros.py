"""Gera o arquivo de licenças de terceiros que acompanha o executável.

Percorre as dependências de requirements.txt (e as dependências delas) instaladas no
ambiente de empacotamento e junta nome, versão, licença, site e o texto da licença
de cada pacote. Uso: python tools/gerar_terceiros.py <arquivo de saída>
"""
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

RAIZ = Path(__file__).resolve().parent.parent

EXTRAS_FIXOS = [
    ("Python", "Python Software Foundation License (PSF-2.0)", "https://www.python.org/",
     "O interpretador Python e sua biblioteca padrão acompanham o executável."),
    ("Tcl/Tk", "Licença estilo BSD (Tcl/Tk License)", "https://www.tcl.tk/software/tcltk/license.html",
     "Base gráfica usada pelo tkinter/CustomTkinter."),
    ("PyInstaller (bootloader)", "GPL-2.0-or-later com exceção para o bootloader",
     "https://pyinstaller.org/en/stable/license.html",
     "Usado para empacotar o executável; a exceção permite distribuir o bootloader junto ao programa."),
]


def _nome_normalizado(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def dependencias() -> list[metadata.Distribution]:
    ambiente = default_environment()
    ambiente["extra"] = ""
    pendentes = []
    for linha in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip()
        if linha:
            pendentes.append(Requirement(linha).name)
    vistos: dict[str, metadata.Distribution] = {}
    while pendentes:
        nome = _nome_normalizado(pendentes.pop())
        if nome in vistos:
            continue
        try:
            dist = metadata.distribution(nome)
        except metadata.PackageNotFoundError:
            continue
        vistos[nome] = dist
        for req in dist.requires or []:
            r = Requirement(req)
            if r.marker and not r.marker.evaluate(ambiente):
                continue
            pendentes.append(r.name)
    return sorted(vistos.values(), key=lambda d: d.metadata["Name"].lower())


def licenca(dist: metadata.Distribution) -> str:
    md = dist.metadata
    if md.get("License-Expression"):
        return md["License-Expression"]
    classificadores = [c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    if classificadores:
        return "; ".join(classificadores)
    texto = (md.get("License") or "").strip().splitlines()
    return texto[0][:100] if texto else "não informada"


def site(dist: metadata.Distribution) -> str:
    md = dist.metadata
    if md.get("Home-page"):
        return md["Home-page"]
    for url in md.get_all("Project-URL") or []:
        return url.split(",", 1)[-1].strip()
    return ""


def textos_licenca(dist: metadata.Distribution) -> list[tuple[str, str]]:
    textos = []
    for arq in dist.files or []:
        nome = arq.name.upper()
        if any(nome.startswith(p) for p in ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
            try:
                textos.append((arq.name, Path(arq.locate()).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return textos


def main(saida: Path) -> None:
    deps = dependencias()
    linhas = [
        "MIGRADOR DE WHATSAPP PARA IPHONE — COMPONENTES DE TERCEIROS",
        "=" * 64,
        "",
        "Este programa é distribuído sob a GPL-3.0-or-later e inclui os componentes abaixo,",
        "cada um sob a sua própria licença. Os textos completos das licenças estão ao final.",
        "",
    ]
    for nome, lic, url, obs in EXTRAS_FIXOS:
        linhas += [f"* {nome}", f"    Licença: {lic}", f"    Site:    {url}", f"    {obs}", ""]
    for d in deps:
        linhas += [f"* {d.metadata['Name']} {d.version}", f"    Licença: {licenca(d)}"]
        if site(d):
            linhas.append(f"    Site:    {site(d)}")
        linhas.append("")
    linhas += ["", "TEXTOS DAS LICENÇAS", "=" * 64]
    for d in deps:
        for arquivo, texto in textos_licenca(d):
            linhas += ["", "-" * 64, f"{d.metadata['Name']} {d.version} — {arquivo}", "-" * 64, texto.strip()]
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"{len(deps)} pacotes -> {saida}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
