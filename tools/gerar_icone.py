"""Gera assets/icone.ico (rodar só quando quiser mudar o ícone)."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([16, 16, S - 16, S - 16], radius=110, fill=(26, 143, 76, 255))


def telefone(x, y, cor):
    d.rounded_rectangle([x, y, x + 130, y + 240], radius=26, outline=(255, 255, 255, 255), width=18, fill=cor)
    d.rounded_rectangle([x + 45, y + 22, x + 85, y + 32], radius=5, fill=(255, 255, 255, 255))


telefone(70, 150, (26, 143, 76, 255))
telefone(312, 150, (255, 255, 255, 60))
# seta
d.rectangle([215, 258, 282, 282], fill=(255, 255, 255, 255))
d.polygon([(275, 230), (312, 270), (275, 310)], fill=(255, 255, 255, 255))
# balão de conversa no telefone de destino
d.ellipse([340, 225, 415, 290], fill=(255, 255, 255, 255))
d.polygon([(352, 280), (345, 305), (372, 287)], fill=(255, 255, 255, 255))

saida = Path(__file__).resolve().parent.parent / "assets" / "icone.ico"
img.save(saida, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(saida.with_suffix(".png"))
print(saida)
