"""Percorre as telas no modo simulação e salva capturas (para documentação/revisão)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["MIGRADOR_SIM_RAPIDO"] = "1"
os.environ["MIGRADOR_DADOS"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageGrab  # noqa: E402

from migrador.interface import App  # noqa: E402

saida = Path(sys.argv[1])
saida.mkdir(parents=True, exist_ok=True)
app = App(simulacao_inicial=True)
app.geometry("1060x800+20+20")
app.attributes("-topmost", True)
passos = []


def foto(nome):
    def f():
        app.lift()
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(saida / f"{nome}.png")
    return f


def foto_janela(nome):
    def f():
        j = app.janela_sobre
        j.attributes("-topmost", True)
        j.lift()
        j.update()
        x, y = j.winfo_rootx(), j.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + j.winfo_width(), y + j.winfo_height()), all_screens=True).save(
            saida / f"{nome}.png")
    return f


def seq(*acoes):
    t = 800
    for a, espera in acoes:
        app.after(t, a)
        t += espera


def comecar():
    app._comecar()


def prep():
    for v in app.vars_prep:
        v.set(True)
    app._conferir_prep()
    app.btn_continuar.invoke()


pasta = tempfile.mkdtemp()
seq(
    (foto("01-inicio"), 800),
    (comecar, 800),
    (foto("02-preparacao"), 500),
    (prep, 2500),
    (lambda: app.var_pasta.set(pasta), 300),
    (lambda: app._conferir(), 2500),
    (foto("03-conexao"), 500),
    (lambda: app.corpo._parent_canvas.yview_moveto(1.0), 500),
    (foto("03b-conexao-resultado"), 500),
    (lambda: app.btn_continuar.invoke(), 1500),
    (foto("04-senha"), 500),
    (lambda: (app.var_anotei.set(True), app._habilitar_senha(), app.btn_continuar.invoke()), 2500),
    (foto("05-backup"), 500),
    (lambda: app._iniciar_backup(), 4000),
    (foto("06-verificacao"), 500),
    (lambda: app.btn_continuar.invoke(), 1500),
    (lambda: app._conferir_destino(), 2500),
    (lambda: app.corpo._parent_canvas.yview_moveto(1.0), 500),
    (foto("07-destino"), 500),
    (lambda: app.mostrar("concluido"), 1000),
    (foto("09-concluido"), 500),
    (lambda: app._saiba_mais(), 1500),
    (foto_janela("10-saiba-mais"), 500),
    (app.destroy, 500),
)
# fecha as caixas de mensagem automaticamente
import migrador.interface as itf  # noqa: E402
itf.messagebox.showinfo = lambda *a, **k: None
app.mainloop()
