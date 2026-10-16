"""Interface visual: um assistente passo a passo."""
import logging
import os
import queue
import secrets
import shutil
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from migrador import atualizador, checagens, diagnostico, disco, energia, registro, telemetria
from migrador.config import (
    APP_NOME, EMPRESA, REPOSITORIO, URL_CONTRIBUIR, URL_PROBLEMAS, URL_REPOSITORIO, documento_legal, recurso,
)
from migrador.dispositivos.base import Cancelado, ErroAparelho
from migrador.dispositivos.simulado import CENARIOS
from migrador.estado import CHAVES_ETAPAS, ETAPAS
from migrador.modelos import (
    Aparelho, Checagem, RelatorioBackup, Status, formatar_bytes, formatar_duracao, tem_bloqueio,
)
from migrador.motor import Migracao
from migrador.versao import __version__

log = logging.getLogger(__name__)

VERDE = ("#1a8f4c", "#25b061")
VERDE_ESCURO = ("#147a3f", "#1c9150")
LARANJA = ("#c26a00", "#f0a030")
VERMELHO = ("#c0392b", "#ff6b5b")
AZUL = ("#2563a8", "#6aa8f0")
CINZA = ("#6b7280", "#9ca3af")
CARTAO = ("#f3f4f6", "#23262b")
LATERAL = ("#e8f3ec", "#17221b")
DESABILITADO = ("#b9c4bd", "#3a403c")

ICONES = {
    Status.OK: ("✔", VERDE),
    Status.AVISO: ("⚠", LARANJA),
    Status.BLOQUEIO: ("✖", VERMELHO),
    Status.INFO: ("ℹ", AZUL),
}
LARGURA_TEXTO = 640
INTERVALO_ATUALIZACAO_MS = 6 * 3600 * 1000


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _checagem_anexos(rel: RelatorioBackup) -> Checagem:
    titulo = "Anexos citados nas conversas"
    if rel.anexos_referenciados < 0:
        return Checagem(titulo, Status.AVISO, "Não consegui cruzar (formato do banco diferente).")
    if not rel.anexos_referenciados:
        return Checagem(titulo, Status.INFO, "As conversas não citam anexos.")
    ausentes = rel.anexos_referenciados - rel.anexos_presentes
    texto = (f"{rel.anexos_presentes:,} de {rel.anexos_referenciados:,} estão no backup".replace(",", ".")
             + (f" ({ausentes:,} ausentes)".replace(",", ".") if ausentes else ""))
    status = Status.OK if not ausentes else (Status.BLOQUEIO if not rel.anexos_presentes else Status.AVISO)
    return Checagem(titulo, status, texto)


def _gerar_senha() -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(3))


class App(ctk.CTk):
    def __init__(self, simulacao_inicial: bool = False):
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("green")
        self.title(f"{APP_NOME} — v{__version__}")
        self.geometry("1060x740")
        self.minsize(900, 640)
        try:
            self.iconbitmap(str(recurso("icone.ico")))
        except Exception:
            pass

        self.fila: queue.Queue = queue.Queue()
        self.ocupado = False
        self.migracao: Migracao | None = Migracao.retomar()
        self.simulacao_inicial = simulacao_inicial
        self.tela_atual = "inicio"
        self.lancamento: atualizador.Lancamento | None = None
        self._progresso = None  # (porcentagem, mensagem) escrito pela thread de trabalho
        self._alvo_progresso = None
        self._inicio_progresso = 0.0

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._montar_lateral()
        self._montar_principal()

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.after(100, self._processar_fila)
        self.after(200, self._atualizar_progresso)
        self.after(1500, self._checar_atualizacao)
        if telemetria.consentimento() is None:
            self.after(900, self._perguntar_telemetria)

        self.mostrar("inicio")

    # ------------------------------------------------------------------ estrutura

    def _montar_lateral(self):
        lat = ctk.CTkFrame(self, width=250, corner_radius=0, fg_color=LATERAL)
        lat.grid(row=0, column=0, sticky="nsw")
        lat.grid_propagate(False)
        ctk.CTkLabel(lat, text="Migrador de\nWhatsApp", font=ctk.CTkFont(size=22, weight="bold"),
                     justify="left", text_color=VERDE).pack(anchor="w", padx=22, pady=(26, 2))
        ctk.CTkLabel(lat, text="iPhone → iPhone", font=ctk.CTkFont(size=13), text_color=CINZA).pack(
            anchor="w", padx=22, pady=(0, 18))
        self.botoes_etapas = {}
        for i, (chave, nome) in enumerate(ETAPAS):
            b = ctk.CTkButton(lat, text=f"  {i + 1}.  {nome}", anchor="w", height=36, corner_radius=8,
                              fg_color="transparent", text_color=CINZA, hover_color=CARTAO,
                              command=lambda c=chave: self._clicar_etapa(c))
            b.pack(fill="x", padx=12, pady=2)
            self.botoes_etapas[chave] = b
        rodape = ctk.CTkFrame(lat, fg_color="transparent")
        rodape.pack(side="bottom", fill="x", padx=16, pady=14)
        ctk.CTkButton(rodape, text="ℹ  Saiba mais: sobre e licenças", height=30, fg_color="transparent",
                      border_width=1, border_color=VERDE, text_color=VERDE, hover_color=CARTAO,
                      command=self._saiba_mais).pack(anchor="w", fill="x", pady=(0, 8))
        ctk.CTkLabel(rodape, text=f"Versão {__version__} · {EMPRESA}", text_color=CINZA,
                     font=ctk.CTkFont(size=12)).pack(anchor="w")
        ctk.CTkLabel(rodape, text="Software livre (GPL-3.0)", text_color=CINZA,
                     font=ctk.CTkFont(size=12)).pack(anchor="w")
        ctk.CTkButton(rodape, text="Abrir registros (logs)", height=26, fg_color="transparent",
                      text_color=AZUL, hover_color=CARTAO, anchor="w",
                      command=lambda: os.startfile(registro.pasta_logs())).pack(anchor="w")
        ctk.CTkButton(rodape, text="Enviar diagnóstico ao suporte", height=26, fg_color="transparent",
                      text_color=AZUL, hover_color=CARTAO, anchor="w",
                      command=self._dialogo_diagnostico).pack(anchor="w")
        ctk.CTkButton(rodape, text="Procurar atualização", height=26, fg_color="transparent",
                      text_color=AZUL, hover_color=CARTAO, anchor="w",
                      command=lambda: self._checar_atualizacao(manual=True)).pack(anchor="w")

    def _montar_principal(self):
        p = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        p.grid(row=0, column=1, sticky="nsew")
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(3, weight=1)

        self.faixa_atualizacao = ctk.CTkFrame(p, fg_color=VERDE, corner_radius=0)
        self.lbl_atualizacao = ctk.CTkLabel(self.faixa_atualizacao, text="", text_color="white")
        self.lbl_atualizacao.pack(side="left", padx=16, pady=8)
        self.btn_atualizacao = ctk.CTkButton(self.faixa_atualizacao, text="Atualizar agora", width=140,
                                             fg_color="white", text_color="#14532d", hover_color="#dcfce7",
                                             command=self._atualizar_agora)
        self.btn_atualizacao.pack(side="right", padx=16, pady=6)

        self.faixa_simulacao = ctk.CTkLabel(
            p, text="MODO SIMULAÇÃO — nenhum iPhone real é usado; tudo aqui é treino",
            fg_color=LARANJA, text_color="white", corner_radius=0, height=30,
            font=ctk.CTkFont(size=13, weight="bold"))

        cab = ctk.CTkFrame(p, fg_color="transparent")
        cab.grid(row=2, column=0, sticky="ew", padx=32, pady=(22, 6))
        self.lbl_titulo = ctk.CTkLabel(cab, text="", font=ctk.CTkFont(size=26, weight="bold"), anchor="w")
        self.lbl_titulo.pack(anchor="w")
        self.lbl_subtitulo = ctk.CTkLabel(cab, text="", font=ctk.CTkFont(size=14), text_color=CINZA,
                                          anchor="w", justify="left", wraplength=720)
        self.lbl_subtitulo.pack(anchor="w", pady=(4, 0))

        self.corpo = ctk.CTkScrollableFrame(p, fg_color="transparent")
        self.corpo.grid(row=3, column=0, sticky="nsew", padx=20, pady=4)
        self.corpo.grid_columnconfigure(0, weight=1)

        nav = ctk.CTkFrame(p, fg_color="transparent")
        nav.grid(row=4, column=0, sticky="ew", padx=32, pady=(6, 18))
        self.btn_voltar = ctk.CTkButton(nav, text="← Voltar", width=120, fg_color="transparent",
                                        border_width=1, text_color=("gray20", "gray85"), command=self._voltar)
        self.btn_voltar.pack(side="left")
        self.lbl_status = ctk.CTkLabel(nav, text="", text_color=CINZA)
        self.lbl_status.pack(side="left", padx=16)
        self.btn_continuar = ctk.CTkButton(nav, text="Continuar →", width=200, height=40,
                                           font=ctk.CTkFont(size=15, weight="bold"))
        self.btn_continuar.pack(side="right")

    # ------------------------------------------------------------------ infraestrutura

    def report_callback_exception(self, tipo, valor, tb):
        # Erros em cliques e callbacks do Tk: no .exe sem console iriam para lugar nenhum.
        log.error("erro na interface", exc_info=(tipo, valor, tb))
        telemetria.registrar_excecao(tipo, valor, tb, onde="interface")
        try:
            self._mostrar_erro(valor)
        except Exception:
            pass

    def _processar_fila(self):
        while True:
            try:
                tarefa = self.fila.get_nowait()
            except queue.Empty:
                break
            try:
                tarefa()
            except Exception as e:
                log.exception("erro na interface")
                self._mostrar_erro(e)
        self.after(100, self._processar_fila)

    def em_segundo_plano(self, trabalho, ao_terminar=None, ao_falhar=None, status="Trabalhando…",
                         manter_acordado=False):
        self._definir_ocupado(True, status)
        if manter_acordado:
            energia.manter_acordado(True)

        def rodar():
            try:
                resultado = trabalho()
            except BaseException as e:  # noqa: BLE001 - tudo precisa voltar para a tela
                log.error("falha em segundo plano: %s\n%s", e, traceback.format_exc())
                self.fila.put(lambda e=e: self._fim_trabalho(ao_falhar, e, erro=True))
            else:
                self.fila.put(lambda r=resultado: self._fim_trabalho(ao_terminar, r))

        threading.Thread(target=rodar, daemon=True).start()

    def _fim_trabalho(self, callback, valor, erro=False):
        energia.manter_acordado(False)
        self._definir_ocupado(False)
        if callback:
            callback(valor)
        elif erro:
            self._mostrar_erro(valor)

    def _definir_ocupado(self, ocupado: bool, status: str = ""):
        self.ocupado = ocupado
        self.lbl_status.configure(text=status if ocupado else "")
        self._atualizar_lateral()
        if ocupado:
            self.btn_voltar.configure(state="disabled")
            self.btn_continuar.configure(state="disabled", fg_color=DESABILITADO)
        else:
            self.btn_voltar.configure(state="normal" if self.tela_atual != "inicio" else "disabled")

    def _mostrar_erro(self, e: BaseException):
        if isinstance(e, ErroAparelho):
            texto = e.mensagem + (f"\n\nO que fazer: {e.como_resolver}" if e.como_resolver else "")
        else:
            texto = f"{type(e).__name__}: {e}\n\nDetalhes no registro (logs)."
        texto += ("\n\nSe o problema continuar, clique em \"Enviar diagnóstico ao suporte\" (no canto "
                  "inferior esquerdo).")
        messagebox.showerror("Não deu certo", texto, parent=self)

    def _perguntar_telemetria(self):
        aceito = messagebox.askyesno(
            "Ajude a melhorar o programa",
            "Podemos enviar dados anônimos de funcionamento para o projeto? Eles mostram se a migração deu "
            "certo e onde costuma falhar, para corrigirmos os problemas.\n\n"
            "Vai: em que etapa a migração está, erros, quanto tempo levou, modelo, versão do iOS e espaço "
            "dos iPhones, versão do programa e do Windows.\n\n"
            "Nunca vai: conversas, fotos, contatos, senhas, nomes dos aparelhos, números de série ou seu "
            "nome.\n\nVocê pode mudar isso depois em \"Saiba mais\".",
            parent=self)
        telemetria.definir_consentimento(aceito)
        if aceito:
            telemetria.registrar("programa_aberto", primeira_vez=True)

    def _dialogo_diagnostico(self):
        jan = ctk.CTkToplevel(self)
        jan.title("Enviar diagnóstico ao suporte")
        jan.geometry("560x400")
        jan.transient(self)
        jan.after(50, jan.grab_set)
        ctk.CTkLabel(jan, text="Enviar diagnóstico ao suporte", font=ctk.CTkFont(size=17, weight="bold")).pack(
            anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(jan, wraplength=510, justify="left", text=(
            "O pacote leva os registros do programa e o andamento da migração. Antes de sair do PC, os "
            "números de série e os nomes dos iPhones e o seu nome de usuário do Windows são trocados por "
            "marcadores. Não vão senhas, conversas, fotos nem contatos.")).pack(anchor="w", padx=20)
        ctk.CTkLabel(jan, text="O que aconteceu? (opcional)").pack(anchor="w", padx=20, pady=(12, 2))
        caixa = ctk.CTkTextbox(jan, height=110)
        caixa.pack(fill="x", padx=20)
        status = ctk.CTkLabel(jan, text="", text_color=CINZA, wraplength=510, justify="left")
        status.pack(anchor="w", padx=20, pady=(8, 0))
        botoes = ctk.CTkFrame(jan, fg_color="transparent")
        botoes.pack(fill="x", padx=20, pady=14, side="bottom")

        def descricao():
            return caixa.get("1.0", "end").strip()

        def enviar():
            btn_enviar.configure(state="disabled")
            btn_salvar.configure(state="disabled")
            status.configure(text="Enviando…", text_color=CINZA)
            texto = descricao()

            def trabalho():
                pasta = Path(tempfile.mkdtemp(prefix="migrador-diag-"))
                zip_path = diagnostico.gerar_pacote(pasta / "diagnostico.zip", texto)
                try:
                    return telemetria.enviar_diagnostico(zip_path, texto)
                finally:
                    shutil.rmtree(pasta, ignore_errors=True)

            def ok(protocolo):
                status.configure(text=f"Enviado. Protocolo: {protocolo}\nInforme este protocolo ao suporte.",
                                 text_color=VERDE)
                btn_enviar.configure(text="Fechar", state="normal", command=jan.destroy)

            def falhou(e):
                log.warning("envio do diagnóstico falhou: %s", e)
                status.configure(text="Não consegui enviar (sem internet?). Use \"Salvar arquivo\" e mande o "
                                      "arquivo ao suporte de outro jeito.", text_color=VERMELHO)
                btn_enviar.configure(state="normal")
                btn_salvar.configure(state="normal")

            self.em_segundo_plano(trabalho, ok, falhou, status="Enviando diagnóstico…")

        def salvar():
            sugestao = f"diagnostico-migrador-{time.strftime('%Y%m%d-%H%M')}.zip"
            destino = filedialog.asksaveasfilename(
                parent=jan, title="Salvar diagnóstico", defaultextension=".zip", initialfile=sugestao,
                initialdir=str(Path.home() / "Desktop"), filetypes=[("Arquivo zip", "*.zip")])
            if not destino:
                return
            try:
                diagnostico.gerar_pacote(Path(destino), descricao())
            except Exception as e:
                log.exception("falha ao gerar diagnóstico")
                status.configure(text=f"Não consegui gerar o arquivo: {e}", text_color=VERMELHO)
                return
            status.configure(text=f"Arquivo salvo em {destino}", text_color=VERDE)
            try:
                os.startfile(Path(destino).parent)
            except Exception:
                pass

        btn_enviar = ctk.CTkButton(botoes, text="Enviar agora", width=150, command=enviar)
        btn_enviar.pack(side="right")
        btn_salvar = ctk.CTkButton(botoes, text="Salvar arquivo", width=130, fg_color="transparent",
                                   border_width=1, text_color=("gray20", "gray85"), command=salvar)
        btn_salvar.pack(side="right", padx=8)

    def _atualizar_progresso(self):
        alvo, dado = self._alvo_progresso, self._progresso
        if alvo and dado:
            pct, msg = dado
            try:
                alvo["barra"].set(max(0.0, min(pct / 100, 1.0)))
                decorrido = time.time() - self._inicio_progresso
                restante = decorrido * (100 - pct) / pct if pct >= 1 else None
                alvo["pct"].configure(text=f"{pct:.1f}%".replace(".", ","))
                detalhes = f"Decorrido: {formatar_duracao(decorrido)}   ·   Restante: {formatar_duracao(restante)}"
                alvo["tempo"].configure(text=detalhes)
                if msg:
                    alvo["msg"].configure(text=msg)
            except Exception:
                pass
        self.after(250, self._atualizar_progresso)

    def _iniciar_progresso(self, alvo):
        self._progresso = (0, "")
        self._alvo_progresso = alvo
        self._inicio_progresso = time.time()

    def _ao_fechar(self):
        if self.ocupado:
            if not messagebox.askyesno(
                "Operação em andamento",
                "Há uma operação em andamento com o iPhone. Fechar agora interrompe o processo.\n\n"
                "O backup pode ser retomado depois, mas uma restauração interrompida precisa ser refeita.\n\n"
                "Fechar mesmo assim?", icon="warning", parent=self):
                return
        energia.manter_acordado(False)
        self.destroy()

    # ------------------------------------------------------------------ navegação

    @property
    def etapa_liberada(self) -> str:
        return self.migracao.estado.etapa if self.migracao else "inicio"

    def _atualizar_lateral(self):
        liberada = CHAVES_ETAPAS.index(self.etapa_liberada)
        for i, chave in enumerate(CHAVES_ETAPAS):
            b = self.botoes_etapas[chave]
            nome = ETAPAS[i][1]
            if chave == self.tela_atual:
                estilo = dict(fg_color=VERDE, hover_color=VERDE, text_color="white", text=f"  {i + 1}.  {nome}")
            elif i < liberada:
                estilo = dict(fg_color="transparent", hover_color=CARTAO, text_color=VERDE, text=f"  ✔   {nome}")
            elif i == liberada:
                estilo = dict(fg_color="transparent", hover_color=CARTAO, text_color=("gray15", "gray90"),
                              text=f"  {i + 1}.  {nome}")
            else:
                estilo = dict(fg_color="transparent", hover_color=CARTAO, text_color=CINZA, text=f"  {i + 1}.  {nome}")
            # Desabilitado mantém a mesma cor: só deixa de responder ao clique.
            b.configure(text_color_disabled=estilo["text_color"], **estilo)
            b.configure(state="disabled" if (self.ocupado or i > liberada or chave == self.tela_atual) else "normal")

    def _clicar_etapa(self, chave):
        if not self.ocupado:
            self.mostrar(chave)

    def _voltar(self):
        i = CHAVES_ETAPAS.index(self.tela_atual)
        if i > 0:
            self.mostrar(CHAVES_ETAPAS[i - 1])

    def mostrar(self, chave: str):
        self.tela_atual = chave
        self._alvo_progresso = None
        for w in self.corpo.winfo_children():
            w.destroy()
        self.corpo._parent_canvas.yview_moveto(0)
        sim = bool(self.migracao and self.migracao.estado.simulacao)
        if sim:
            self.faixa_simulacao.grid(row=1, column=0, sticky="ew")
        else:
            self.faixa_simulacao.grid_forget()
        self.btn_voltar.configure(state="normal" if chave != "inicio" else "disabled")
        getattr(self, f"_tela_{chave}")()
        self._atualizar_lateral()

    def _cabecalho(self, titulo, subtitulo=""):
        self.lbl_titulo.configure(text=titulo)
        self.lbl_subtitulo.configure(text=subtitulo)

    def _botao_continuar(self, texto="Continuar →", comando=None, habilitado=True, cor=None):
        ativo = habilitado and not self.ocupado
        self.btn_continuar.configure(text=texto, command=comando or (lambda: None),
                                     state="normal" if ativo else "disabled",
                                     fg_color=(cor or VERDE) if ativo else DESABILITADO,
                                     hover_color=VERDE_ESCURO if not cor else cor)

    # ------------------------------------------------------------------ componentes

    def _cartao(self, titulo=None, cor_borda=None):
        c = ctk.CTkFrame(self.corpo, fg_color=CARTAO, corner_radius=12,
                         border_width=2 if cor_borda else 0, border_color=cor_borda or CARTAO)
        c.pack(fill="x", padx=12, pady=8)
        if titulo:
            ctk.CTkLabel(c, text=titulo, font=ctk.CTkFont(size=16, weight="bold"), anchor="w").pack(
                anchor="w", padx=18, pady=(14, 4))
        return c

    def _texto(self, pai, texto, cor=None, tamanho=14, negrito=False, pady=(2, 2)):
        lbl = ctk.CTkLabel(pai, text=texto, anchor="w", justify="left", wraplength=LARGURA_TEXTO,
                           text_color=cor, font=ctk.CTkFont(size=tamanho, weight="bold" if negrito else "normal"))
        lbl.pack(anchor="w", padx=18, pady=pady)
        return lbl

    def _numerada(self, pai, itens):
        for i, item in enumerate(itens, 1):
            linha = ctk.CTkFrame(pai, fg_color="transparent")
            linha.pack(fill="x", padx=18, pady=3)
            ctk.CTkLabel(linha, text=f"{i}", width=26, height=26, corner_radius=13, fg_color=VERDE,
                         text_color="white", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", anchor="n")
            ctk.CTkLabel(linha, text=item, anchor="w", justify="left", wraplength=LARGURA_TEXTO - 40,
                         font=ctk.CTkFont(size=14)).pack(side="left", padx=10, anchor="w")
        ctk.CTkFrame(pai, height=8, fg_color="transparent").pack()

    def _lista_checagens(self, pai, checagens: list[Checagem]):
        for ch in checagens:
            icone, cor = ICONES[ch.status]
            linha = ctk.CTkFrame(pai, fg_color="transparent")
            linha.pack(fill="x", padx=18, pady=4)
            ctk.CTkLabel(linha, text=icone, text_color=cor, width=26,
                         font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", anchor="n")
            bloco = ctk.CTkFrame(linha, fg_color="transparent")
            bloco.pack(side="left", fill="x", expand=True, padx=6)
            ctk.CTkLabel(bloco, text=ch.titulo, anchor="w", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            if ch.detalhe:
                ctk.CTkLabel(bloco, text=ch.detalhe, anchor="w", justify="left", wraplength=LARGURA_TEXTO - 60,
                             text_color=CINZA).pack(anchor="w")
            if ch.como_resolver and ch.status in (Status.BLOQUEIO, Status.AVISO):
                ctk.CTkLabel(bloco, text="→ " + ch.como_resolver, anchor="w", justify="left",
                             wraplength=LARGURA_TEXTO - 60, text_color=cor).pack(anchor="w")
        ctk.CTkFrame(pai, height=8, fg_color="transparent").pack()

    def _painel_progresso(self, pai, mensagem_inicial=""):
        barra = ctk.CTkProgressBar(pai, height=18, corner_radius=9)
        barra.set(0)
        barra.pack(fill="x", padx=18, pady=(12, 6))
        linha = ctk.CTkFrame(pai, fg_color="transparent")
        linha.pack(fill="x", padx=18)
        pct = ctk.CTkLabel(linha, text="0%", font=ctk.CTkFont(size=20, weight="bold"))
        pct.pack(side="left")
        tempo = ctk.CTkLabel(linha, text="", text_color=CINZA)
        tempo.pack(side="right")
        msg = self._texto(pai, mensagem_inicial, cor=CINZA, pady=(4, 14))
        return {"barra": barra, "pct": pct, "tempo": tempo, "msg": msg}

    # ------------------------------------------------------------------ telas

    def _tela_inicio(self):
        self._cabecalho("Transferir o WhatsApp de um iPhone para outro",
                        "Sem nuvem e sem depender da transferência do próprio WhatsApp: o PC faz um backup "
                        "completo do iPhone antigo, confere se as conversas estão lá e restaura no iPhone novo.")
        m = self.migracao
        if m and m.estado.etapa not in ("inicio", "concluido"):
            nome = dict(ETAPAS)[m.estado.etapa]
            c = self._cartao("Existe uma migração em andamento", cor_borda=VERDE)
            modo = "simulação" if m.estado.simulacao else "iPhones reais"
            self._texto(c, f"Parou na etapa \"{nome}\" ({modo}). Nada do que já foi feito se perdeu.")
            linha = ctk.CTkFrame(c, fg_color="transparent")
            linha.pack(anchor="w", padx=18, pady=(6, 14))
            ctk.CTkButton(linha, text="Retomar na etapa em que parou", command=lambda: self.mostrar(m.estado.etapa)).pack(
                side="left")
            ctk.CTkButton(linha, text="Começar do zero", fg_color="transparent", border_width=1,
                          text_color=("gray20", "gray85"), command=self._confirmar_recomecar).pack(side="left", padx=10)

        c = self._cartao("Como funciona")
        self._numerada(c, [
            "Você conecta os dois iPhones ao PC. O programa confere modelo, versão do iOS, espaço e "
            "WhatsApp, e diz exatamente o que ajustar antes de começar.",
            "O PC faz um backup completo e protegido por senha do iPhone antigo. O iPhone antigo não é "
            "apagado nem alterado.",
            "O programa abre o backup e confere que as conversas, mensagens e mídias do WhatsApp estão "
            "lá, íntegras. Só então libera o próximo passo.",
            "O backup é restaurado no iPhone novo, que fica igual ao antigo, com o WhatsApp e todas as conversas.",
        ])
        c = self._cartao("Antes de começar, saiba que")
        self._texto(c, "• Com um WhatsApp de ~200 GB, cada etapa de cópia (backup e restauração) leva de 2 a 5 "
                       "horas. Reserve um período em que o PC e os iPhones possam ficar parados e na tomada.")
        self._texto(c, "• O PC precisa de espaço livre do tamanho do conteúdo do iPhone antigo, mais uma folga.")
        self._texto(c, "• O iPhone novo será apagado e ficará com o conteúdo do antigo.", pady=(2, 4))
        ctk.CTkButton(c, text=f"Software livre desenvolvido pela {EMPRESA} · Saiba mais", anchor="w",
                      fg_color="transparent", text_color=AZUL, hover_color=CARTAO, height=26,
                      command=self._saiba_mais).pack(anchor="w", padx=10, pady=(0, 10))

        c = self._cartao("Modo de uso")
        modo_inicial = "Simulação (treino, sem iPhones)" if (
            self.simulacao_inicial or (m and m.estado.simulacao)) else "iPhones reais"
        self.var_modo = ctk.StringVar(value=modo_inicial)
        ctk.CTkSegmentedButton(c, values=["iPhones reais", "Simulação (treino, sem iPhones)"],
                               variable=self.var_modo, command=lambda _: self._alternar_cenario()).pack(
            anchor="w", padx=18, pady=(4, 8))
        self.quadro_cenario = ctk.CTkFrame(c, fg_color="transparent")
        ctk.CTkLabel(self.quadro_cenario, text="Situação simulada:").pack(side="left")
        self.var_cenario = ctk.StringVar(value=CENARIOS["normal"])
        ctk.CTkOptionMenu(self.quadro_cenario, values=list(CENARIOS.values()), variable=self.var_cenario,
                          width=380).pack(side="left", padx=10)
        self._alternar_cenario()
        ctk.CTkFrame(c, height=10, fg_color="transparent").pack()

        self._botao_continuar("Começar nova migração →", self._comecar)

    def _alternar_cenario(self):
        if self.var_modo.get().startswith("Simula"):
            self.quadro_cenario.pack(anchor="w", padx=18, pady=(0, 6))
        else:
            self.quadro_cenario.pack_forget()

    def _confirmar_recomecar(self):
        if messagebox.askyesno("Começar do zero",
                               "A migração em andamento será deixada de lado (o backup já feito continua "
                               "na pasta, não é apagado). Continuar?", parent=self):
            self._comecar()

    def _comecar(self):
        m = self.migracao
        if m and m.estado.etapa not in ("inicio", "concluido") and not messagebox.askyesno(
                "Nova migração", "Já existe uma migração em andamento. Começar outra do zero?", parent=self):
            return
        sim = self.var_modo.get().startswith("Simula")
        cenario = next(k for k, v in CENARIOS.items() if v == self.var_cenario.get())
        self.migracao = Migracao.nova(sim, cenario)
        self.migracao.estado.avancar_para("preparacao")
        self.migracao.salvar()
        self.mostrar("preparacao")

    def _tela_preparacao(self):
        self._cabecalho("Preparação", "O programa confere o PC sozinho. Depois, marque os itens que só você pode "
                                      "conferir. Isso evita a maioria dos problemas.")
        c = self._cartao("1. Verificação automática do PC")
        self._texto(c, "App e serviço da Apple, iPhones que já confiam neste PC, energia, tampa do notebook e "
                       "programas que podem disputar o iPhone.", cor=CINZA)
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(4, 4))
        ctk.CTkButton(linha, text="↻  Verificar de novo", width=170, command=self._verificar_pc).pack(side="left")
        self.btn_loja = ctk.CTkButton(linha, text="Abrir a Microsoft Store", width=190, fg_color="transparent",
                                      border_width=1, text_color=("gray20", "gray85"),
                                      command=lambda: webbrowser.open(checagens.LOJA_APPLE_DEVICES))
        self.quadro_pc = ctk.CTkFrame(c, fg_color="transparent")
        self.quadro_pc.pack(fill="x", pady=(0, 10))
        self.pc_ok = False

        itens = [
            "Tenho cabos USB que transmitem dados (os que vieram com o iPhone servem). Vou ligar os iPhones direto "
            "no PC, sem hub.",
            "O iPhone novo está ligado e configurado (ou na tela de configuração inicial). Ele será apagado: "
            "salvei o que havia de importante nele.",
            "No iPhone antigo, atualizei o WhatsApp pela App Store.",
            "Os dois iPhones estão carregados ou vão ficar na tomada durante o processo.",
            "Sei a senha do meu Apple ID: o iPhone novo vai pedir para baixar os apps de novo.",
            "O PC vai ficar ligado durante horas, sem ser desligado nem reiniciado.",
        ]
        c = self._cartao("2. Confira você mesmo")
        self.vars_prep = []
        for item in itens:
            v = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(c, text=item, variable=v, command=self._conferir_prep,
                            font=ctk.CTkFont(size=14)).pack(anchor="w", padx=18, pady=6)
            self.vars_prep.append(v)
        ctk.CTkFrame(c, height=8, fg_color="transparent").pack()
        c = self._cartao("Por que o WhatsApp oficial não resolveu?")
        self._texto(c, "O backup do iCloud tem limite de espaço e a transferência direta entre iPhones costuma "
                       "falhar com conversas muito grandes. Aqui a cópia vai por cabo para o disco do PC, sem "
                       "limite de nuvem, e cada etapa é conferida antes da próxima.", pady=(2, 14))
        self._botao_continuar("Continuar →", self._ir_conexao, habilitado=False)
        self._verificar_pc()

    def _verificar_pc(self):
        for w in self.quadro_pc.winfo_children():
            w.destroy()
        self._texto(self.quadro_pc, "Verificando o PC…", cor=CINZA)
        self.em_segundo_plano(lambda: self.migracao.verificar_sistema("preparacao"), self._mostrar_pc,
                              status="Verificando o PC…")

    def _mostrar_pc(self, c):
        if self.tela_atual != "preparacao":
            return
        for w in self.quadro_pc.winfo_children():
            w.destroy()
        self.pc_ok = not tem_bloqueio(c)
        self._lista_checagens(self.quadro_pc, c)
        if any(ch.titulo.startswith("App Apple Devices") and ch.status != Status.OK for ch in c):
            self.btn_loja.pack(side="left", padx=10)
        else:
            self.btn_loja.pack_forget()
        self._conferir_prep()

    def _conferir_prep(self):
        pronto = all(v.get() for v in self.vars_prep) and getattr(self, "pc_ok", False)
        self._botao_continuar("Continuar →", self._ir_conexao, habilitado=pronto)

    def _ir_conexao(self):
        self.migracao.estado.avancar_para("conexao")
        self.migracao.salvar()
        self.mostrar("conexao")

    # --- conexão

    def _tela_conexao(self):
        self._cabecalho("Conectar e conferir",
                        "Conecte OS DOIS iPhones ao PC pelo cabo, desbloqueie e toque em \"Confiar\" se aparecer.")
        e = self.migracao.estado
        c = self._cartao("1. Onde guardar o backup")
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(fill="x", padx=18, pady=(2, 4))
        self.var_pasta = ctk.StringVar(value=e.pasta_backup or str(disco.sugerir_pasta()))
        ctk.CTkEntry(linha, textvariable=self.var_pasta, width=480).pack(side="left")
        ctk.CTkButton(linha, text="Escolher…", width=100, command=self._escolher_pasta).pack(side="left", padx=8)
        self.lbl_espaco = self._texto(c, "", cor=CINZA, pady=(0, 12))
        self._mostrar_espaco()

        c = self._cartao("2. iPhones conectados")
        ctk.CTkButton(c, text="🔍  Procurar iPhones", command=self._procurar, width=200).pack(
            anchor="w", padx=18, pady=(4, 8))
        self.quadro_aparelhos = ctk.CTkFrame(c, fg_color="transparent")
        self.quadro_aparelhos.pack(fill="x", padx=0, pady=(0, 10))
        self.cartao_checagens = None
        self._botao_continuar("Continuar →", lambda: self.mostrar("senha"), habilitado=False)
        if self.migracao.aparelhos:
            self._desenhar_aparelhos(self.migracao.aparelhos)
        else:
            self._procurar()

    def _escolher_pasta(self):
        pasta = filedialog.askdirectory(parent=self, title="Pasta para guardar o backup")
        if pasta:
            self.var_pasta.set(str(Path(pasta) / "MigradorWhatsApp-Backup")
                               if not pasta.rstrip("/\\").endswith("MigradorWhatsApp-Backup") else pasta)
            self._mostrar_espaco()

    def _mostrar_espaco(self):
        try:
            p = Path(self.var_pasta.get())
            fs = disco.sistema_arquivos(p) or ""
            self.lbl_espaco.configure(text=f"Espaço livre nesse disco: {formatar_bytes(disco.espaco_livre(p))}"
                                           f"{'  ·  ' + fs if fs else ''}")
        except Exception:
            self.lbl_espaco.configure(text="")

    def _procurar(self):
        for w in self.quadro_aparelhos.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.quadro_aparelhos, text="Procurando…", text_color=CINZA).pack(anchor="w", padx=18)
        self.em_segundo_plano(self.migracao.procurar, self._desenhar_aparelhos, status="Procurando iPhones…")

    def _desenhar_aparelhos(self, aparelhos: list[Aparelho]):
        if self.tela_atual != "conexao":
            return
        for w in self.quadro_aparelhos.winfo_children():
            w.destroy()
        prontos = [a for a in aparelhos if not a.erro]
        problemas = [a for a in aparelhos if a.erro]
        if not aparelhos:
            self._texto(self.quadro_aparelhos, "Nenhum iPhone encontrado. Confira os cabos, desbloqueie os "
                                               "iPhones e clique em Procurar.", cor=LARANJA)
            if not self.migracao.backend.servico_apple_ok():
                self._texto(self.quadro_aparelhos, "O serviço da Apple não está rodando neste PC. Instale o app "
                                                   "\"Apple Devices\" pela Microsoft Store e abra-o uma vez.",
                            cor=VERMELHO)
            return
        for a in prontos:
            info = (f"📱  {a.rotulo()}   ·   {formatar_bytes(a.capacidade)} "
                    f"(usado {formatar_bytes(a.usado)})" + (f"   ·   bateria {a.bateria}%" if a.bateria else ""))
            self._texto(self.quadro_aparelhos, info)
        if problemas:
            self._lista_checagens(self.quadro_aparelhos,
                                  checagens.checar_conexao(problemas))
        if len(prontos) < 2:
            self._texto(self.quadro_aparelhos, "É preciso ter os dois iPhones conectados e liberados. Depois de "
                                               "resolver, clique em Procurar de novo.", cor=LARANJA)
            return
        rotulos = {a.rotulo() + f"  [{a.udid[-6:]}]": a for a in prontos}
        self._mapa_aparelhos = rotulos
        nomes = list(rotulos)
        e = self.migracao.estado
        ant = next((n for n, a in rotulos.items() if e.origem and a.udid == e.origem["udid"]), nomes[0])
        novo = next((n for n in nomes if n != ant), nomes[-1])
        grade = ctk.CTkFrame(self.quadro_aparelhos, fg_color="transparent")
        grade.pack(fill="x", padx=18, pady=(10, 4))
        ctk.CTkLabel(grade, text="iPhone ANTIGO (tem o WhatsApp):", width=240, anchor="w").grid(row=0, column=0, pady=4)
        self.var_origem = ctk.StringVar(value=ant)
        ctk.CTkOptionMenu(grade, values=nomes, variable=self.var_origem, width=420).grid(row=0, column=1, pady=4)
        ctk.CTkLabel(grade, text="iPhone NOVO (será apagado):", width=240, anchor="w").grid(row=1, column=0, pady=4)
        self.var_destino = ctk.StringVar(value=novo)
        ctk.CTkOptionMenu(grade, values=nomes, variable=self.var_destino, width=420).grid(row=1, column=1, pady=4)
        ctk.CTkButton(grade, text="⇅ Trocar", width=90, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=self._trocar).grid(row=0, column=2, rowspan=2, padx=10)
        ctk.CTkButton(self.quadro_aparelhos, text="Conferir tudo", width=200, command=self._conferir).pack(
            anchor="w", padx=18, pady=(10, 4))

    def _trocar(self):
        a, b = self.var_origem.get(), self.var_destino.get()
        self.var_origem.set(b)
        self.var_destino.set(a)

    def _conferir(self):
        origem = self._mapa_aparelhos[self.var_origem.get()]
        destino = self._mapa_aparelhos[self.var_destino.get()]
        pasta = Path(self.var_pasta.get().strip())
        if not pasta.is_absolute():
            messagebox.showwarning("Pasta", "Informe o caminho completo da pasta do backup.", parent=self)
            return
        self.em_segundo_plano(lambda: self.migracao.conferir(origem, destino, pasta), self._mostrar_conferencia,
                              status="Conferindo aparelhos, versões e espaço…")

    def _mostrar_conferencia(self, checagens: list[Checagem]):
        if self.tela_atual != "conexao":
            return
        if self.cartao_checagens:
            self.cartao_checagens.destroy()
        ok = not tem_bloqueio(checagens)
        self.cartao_checagens = self._cartao("3. Resultado da conferência", cor_borda=VERDE if ok else VERMELHO)
        if ok:
            self._texto(self.cartao_checagens, "Tudo certo para começar.", cor=VERDE, negrito=True)
        else:
            self._texto(self.cartao_checagens, "Resolva os itens em vermelho e clique em Conferir tudo de novo.",
                        cor=VERMELHO, negrito=True)
        self._lista_checagens(self.cartao_checagens, checagens)
        self._botao_continuar("Continuar →", lambda: self.mostrar("senha"), habilitado=ok)
        self.after(100, lambda: self.corpo._parent_canvas.yview_moveto(1.0))

    # --- senha

    def _tela_senha(self):
        self._cabecalho("Senha do backup",
                        "O backup com senha é criptografado e é o único que leva também as senhas salvas, os dados "
                        "de Saúde e as chaves de acesso dos apps.")
        self.quadro_senha = self._cartao()
        self._texto(self.quadro_senha, "Consultando o iPhone antigo…", cor=CINZA, pady=(14, 14))
        self._botao_continuar("Confirmar senha →", habilitado=False)
        self.em_segundo_plano(self.migracao.criptografia_ativa, self._desenhar_senha, status="Consultando o iPhone…")

    def _desenhar_senha(self, ativa: bool):
        if self.tela_atual != "senha":
            return
        for w in self.quadro_senha.winfo_children():
            w.destroy()
        c = self.quadro_senha
        self.var_senha = ctk.StringVar(value="" if ativa else _gerar_senha())
        self.var_anotei = ctk.BooleanVar(value=ativa)
        self.var_guardar = ctk.BooleanVar(value=True)
        if ativa:
            ctk.CTkLabel(c, text="O iPhone antigo já faz backup com senha", font=ctk.CTkFont(size=16, weight="bold")
                         ).pack(anchor="w", padx=18, pady=(14, 4))
            self._texto(c, "Digite a senha de backup que foi criada antes (não é o código de desbloqueio da tela, "
                           "é a senha de backup do iTunes/Finder/Apple Devices).")
            linha = ctk.CTkFrame(c, fg_color="transparent")
            linha.pack(anchor="w", padx=18, pady=8)
            ent = ctk.CTkEntry(linha, textvariable=self.var_senha, show="•", width=300, height=38,
                               font=ctk.CTkFont(size=16))
            ent.pack(side="left")
            ctk.CTkCheckBox(linha, text="mostrar", command=lambda: ent.configure(
                show="" if ent.cget("show") else "•")).pack(side="left", padx=12)
            self._texto(c, "Esqueceu a senha? No iPhone antigo: Ajustes > Geral > Transferir ou Redefinir o iPhone > "
                           "Redefinir > Redefinir Todos os Ajustes. Isso NÃO apaga fotos nem conversas; remove a senha "
                           "do backup (e também Wi-Fi e preferências). Depois volte a esta etapa.", cor=CINZA)
        else:
            ctk.CTkLabel(c, text="Crie a senha do backup", font=ctk.CTkFont(size=16, weight="bold")).pack(
                anchor="w", padx=18, pady=(14, 4))
            self._texto(c, "Sugerimos a senha abaixo. Você pode trocar por outra. ANOTE: sem ela o backup não "
                           "pode ser restaurado.", cor=LARANJA)
            linha = ctk.CTkFrame(c, fg_color="transparent")
            linha.pack(anchor="w", padx=18, pady=8)
            ctk.CTkEntry(linha, textvariable=self.var_senha, width=300, height=42,
                         font=ctk.CTkFont(family="Consolas", size=20, weight="bold")).pack(side="left")
            ctk.CTkButton(linha, text="Gerar outra", width=110, fg_color="transparent", border_width=1,
                          text_color=("gray20", "gray85"),
                          command=lambda: self.var_senha.set(_gerar_senha())).pack(side="left", padx=8)
            ctk.CTkButton(linha, text="Copiar", width=80, command=lambda: (
                self.clipboard_clear(), self.clipboard_append(self.var_senha.get()))).pack(side="left")
            ctk.CTkCheckBox(c, text="Anotei a senha em lugar seguro", variable=self.var_anotei,
                            command=self._habilitar_senha).pack(anchor="w", padx=18, pady=6)
            self._texto(c, "Ao confirmar, o iPhone antigo pode pedir o código de desbloqueio: digite-o no iPhone.",
                        cor=CINZA)
        ctk.CTkCheckBox(c, text="Guardar a senha também neste PC, protegida pela sua conta do Windows "
                                "(permite retomar sem digitar de novo)", variable=self.var_guardar).pack(
            anchor="w", padx=18, pady=(6, 14))
        self.var_senha.trace_add("write", lambda *_: self._habilitar_senha())
        self._habilitar_senha()

    def _habilitar_senha(self):
        ok = len(self.var_senha.get().strip()) >= 4 and self.var_anotei.get()
        self._botao_continuar("Confirmar senha →", self._confirmar_senha, habilitado=ok)

    def _confirmar_senha(self):
        senha, guardar = self.var_senha.get().strip(), self.var_guardar.get()
        self.em_segundo_plano(lambda: self.migracao.definir_senha(senha, guardar),
                              lambda _: self.mostrar("backup"),
                              status="Configurando a senha no iPhone (confira se ele pede o código)…")

    # --- backup

    def _tela_backup(self):
        e = self.migracao.estado
        self._cabecalho("Backup do iPhone antigo",
                        "O PC copia todo o conteúdo do iPhone antigo, inclusive o WhatsApp. O iPhone antigo não é "
                        "alterado.")
        tamanho = (e.whatsapp or {}).get("tamanho")
        origem = Aparelho.de_dict(e.origem) if e.origem else None
        estimado = max(x for x in (tamanho or 0, (origem.usado if origem else 0) or 0))
        anterior = self.migracao.backup_anterior_completo()
        if anterior:
            sobre_interromper = ("Já existe um backup completo nesta pasta: uma nova cópia só atualiza o que mudou. "
                                 "Se ela for interrompida, basta atualizar de novo.")
        else:
            sobre_interromper = ("Não use o iPhone antigo, não desconecte o cabo e não deixe o PC desligar. Se esta "
                                 "primeira cópia for interrompida (cabo, energia ou botão Parar), ela RECOMEÇA DO "
                                 "ZERO na próxima tentativa. É uma limitação do backup da Apple, e o iTunes faz o "
                                 "mesmo. Depois que uma cópia termina, as seguintes só atualizam o que mudou.")
        c = self._cartao("Durante o backup")
        self._numerada(c, [
            "Mantenha o iPhone antigo conectado pelo cabo e na tomada. Pode deixar a tela apagar.",
            sobre_interromper,
            "Se o iPhone pedir o código de desbloqueio, digite-o.",
            f"Tamanho estimado: {formatar_bytes(estimado)}. Tempo típico: "
            f"{formatar_duracao(estimado / (35 * 1024**2))} a {formatar_duracao(estimado / (15 * 1024**2))}.",
            f"Destino: {e.pasta_backup}",
        ])
        c = self._cartao("Progresso")
        self.painel = self._painel_progresso(c, "Pronto para começar.")
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(0, 14))
        texto = "↻  Atualizar backup (copia só o que mudou)" if anterior else "▶  Iniciar backup"
        self.btn_backup = ctk.CTkButton(linha, text=texto, width=240, height=40, command=self._iniciar_backup)
        self.btn_backup.pack(side="left")
        self.btn_cancelar = ctk.CTkButton(linha, text="Parar backup", width=160, fg_color="transparent",
                                          border_width=1, text_color=VERMELHO, state="disabled",
                                          command=self._cancelar_backup)
        self.btn_cancelar.pack(side="left", padx=10)
        if e.backup_concluido_em:
            self.painel["barra"].set(1)
            self.painel["pct"].configure(text="100%")
            self.painel["msg"].configure(text=f"Backup concluído em {e.backup_concluido_em.replace('T', ' às ')}.")
            self._botao_continuar("Verificar o backup →", lambda: self.mostrar("verificacao"))
        else:
            self._botao_continuar("Verificar o backup →", habilitado=False)

    def _iniciar_backup(self):
        self.cancelar = threading.Event()
        self._iniciar_progresso(self.painel)
        self.btn_backup.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")

        aviso_atual = [""]

        def prog(p):
            self._progresso = (float(p), aviso_atual[0] or "Copiando o conteúdo do iPhone antigo…")

        def aviso(texto):
            aviso_atual[0] = texto
            pct = self._progresso[0] if self._progresso else 0
            self._progresso = (pct, texto or "Copiando o conteúdo do iPhone antigo…")

        self.em_segundo_plano(lambda: self.migracao.fazer_backup(prog, self.cancelar, aviso), self._backup_ok,
                              self._backup_falhou, status="Backup em andamento…", manter_acordado=True)
        self.btn_cancelar.configure(state="normal")

    def _cancelar_backup(self):
        if self.migracao.backup_anterior_completo():
            texto = ("Parar a atualização do backup agora?\n\nComo já existe um backup completo, na próxima "
                     "tentativa ele copia só o que mudou.")
        else:
            texto = ("A primeira cópia ainda não terminou. Se parar agora, ela RECOMEÇA DO ZERO na próxima "
                     "tentativa. Com um WhatsApp grande, isso significa horas de cópia de novo.\n\n"
                     "Parar mesmo assim?")
        if messagebox.askyesno("Parar backup", texto, icon="warning", parent=self):
            self.cancelar.set()

    def _backup_ok(self, _):
        self._alvo_progresso = None
        if self.tela_atual == "backup":
            self.painel["barra"].set(1)
            self.painel["pct"].configure(text="100%")
        messagebox.showinfo("Backup concluído", "Backup concluído. Agora o programa vai conferir se está tudo lá.",
                            parent=self)
        self.mostrar("verificacao")

    def _backup_falhou(self, erro):
        self._alvo_progresso = None
        if self.tela_atual == "backup":
            anterior = self.migracao.backup_anterior_completo()
            self.btn_backup.configure(state="normal", text="↻  Atualizar backup (copia só o que mudou)" if anterior
                                      else "↻  Recomeçar o backup (do zero)")
            self.btn_cancelar.configure(state="disabled")
            parou = "Backup interrompido." if isinstance(erro, Cancelado) else "O backup parou antes de terminar."
            complemento = ("O backup completo anterior continua na pasta." if anterior
                           else "Esta primeira cópia não terminou; a próxima tentativa começa do zero.")
            self.painel["msg"].configure(text=f"{parou} {complemento}")
        if not isinstance(erro, Cancelado):
            self._mostrar_erro(erro)

    # --- verificação

    def _tela_verificacao(self):
        self._cabecalho("Verificar o backup",
                        "O programa abre o backup e confere, arquivo por arquivo, que está completo e que as conversas "
                        "do WhatsApp estão lá. Nada é alterado.")
        m = self.migracao
        self._botao_continuar("Preparar o iPhone novo →", habilitado=False)
        rel = m.relatorio
        if rel:
            self._desenhar_relatorio(rel)
            return
        self._preparar_verificacao()

    def _preparar_verificacao(self):
        m = self.migracao
        c = self._cartao()
        precisa_senha = m.estado.criptografia_ja_ativa and not m.senha and not m.estado.simulacao
        if precisa_senha:
            self._texto(c, "Digite a senha do backup para que ele possa ser aberto:", pady=(14, 4))
            var = ctk.StringVar()
            linha = ctk.CTkFrame(c, fg_color="transparent")
            linha.pack(anchor="w", padx=18, pady=(4, 14))
            ctk.CTkEntry(linha, textvariable=var, show="•", width=280).pack(side="left")

            def ok():
                m.senha = var.get().strip()
                for w in self.corpo.winfo_children():
                    w.destroy()
                self._executar_verificacao()

            ctk.CTkButton(linha, text="Verificar", command=ok).pack(side="left", padx=8)
            return
        c.destroy()
        self._executar_verificacao()

    def _executar_verificacao(self):
        c = self._cartao("Verificando…")
        painel = self._painel_progresso(c, "Abrindo o backup…")
        self._iniciar_progresso(painel)

        def prog(p, msg):
            self._progresso = (float(p), msg)

        self.em_segundo_plano(lambda: self.migracao.verificar(prog), self._verificacao_ok, self._verificacao_falhou,
                              status="Verificando o backup…", manter_acordado=True)

    def _verificacao_ok(self, rel: RelatorioBackup):
        self._alvo_progresso = None
        if self.tela_atual == "verificacao":
            for w in self.corpo.winfo_children():
                w.destroy()
            self._desenhar_relatorio(rel)

    def _verificacao_falhou(self, erro):
        self._alvo_progresso = None
        self._mostrar_erro(erro)
        if self.tela_atual == "verificacao":
            for w in self.corpo.winfo_children():
                w.destroy()
            if isinstance(erro, ErroAparelho) and "senha" in erro.mensagem.lower():
                self.migracao.senha = None
                self.migracao.estado.criptografia_ja_ativa = True
            self._preparar_verificacao()

    def _desenhar_relatorio(self, rel: RelatorioBackup):
        c = self._cartao("Backup aprovado" if rel.ok else "Backup reprovado", cor_borda=VERDE if rel.ok else VERMELHO)
        if rel.ok:
            self._texto(c, "O backup está completo e as conversas do WhatsApp estão íntegras.", cor=VERDE, negrito=True)
        itens = [
            Checagem("Conversas do WhatsApp", Status.OK if rel.conversas else Status.AVISO, f"{rel.conversas:,}".replace(",", ".")),
            Checagem("Mensagens", Status.OK if rel.mensagens else Status.BLOQUEIO, f"{rel.mensagens:,}".replace(",", ".")),
            Checagem("Fotos, vídeos, áudios e documentos do WhatsApp", Status.OK if rel.midias else Status.AVISO,
                     f"{rel.midias:,} arquivos · {formatar_bytes(rel.bytes_midia)}".replace(",", ".")),
            Checagem("Banco de conversas", Status.OK if rel.banco_integro else Status.BLOQUEIO,
                     "Íntegro (teste de integridade do SQLite)" if rel.banco_integro else "Com problema"),
            _checagem_anexos(rel),
            Checagem("Teste de mídias", Status.OK if rel.amostra_conferida and not rel.amostra_falhas else
                     (Status.BLOQUEIO if rel.amostra_falhas else Status.AVISO),
                     f"{rel.amostra_conferida} mídias sorteadas foram decifradas por inteiro e conferidas"
                     + (f"; {rel.amostra_falhas} falharam" if rel.amostra_falhas else "")),
            Checagem("Arquivos no backup", Status.OK if not rel.arquivos_faltando else Status.BLOQUEIO,
                     f"{rel.arquivos:,} arquivos · {formatar_bytes(rel.bytes_total)}".replace(",", ".")
                     + (f" · {rel.arquivos_faltando} faltando" if rel.arquivos_faltando else "")),
            Checagem("Criptografia", Status.OK if rel.criptografado else Status.BLOQUEIO,
                     "Backup protegido por senha" if rel.criptografado else "Backup sem senha"),
            Checagem("Inventário", Status.INFO,
                     f"Tamanho e data de {_milhar(rel.inventario_arquivos)} arquivos registrados. Antes de "
                     "restaurar, o programa confere de novo que nenhum mudou."),
            Checagem("iOS do backup", Status.INFO, rel.ios_backup),
        ]
        itens += [Checagem(p, Status.BLOQUEIO) for p in rel.problemas]
        itens += [Checagem(a, Status.AVISO) for a in rel.avisos]
        self._lista_checagens(c, itens)
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(0, 14))
        ctk.CTkButton(linha, text="Verificar de novo", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray85"), command=self._reverificar).pack(side="left")
        if not rel.ok:
            ctk.CTkButton(linha, text="Refazer o backup", command=lambda: self.mostrar("backup")).pack(
                side="left", padx=10)
        self._botao_continuar("Preparar o iPhone novo →", lambda: self.mostrar("destino"), habilitado=rel.ok)

    def _reverificar(self):
        for w in self.corpo.winfo_children():
            w.destroy()
        self._preparar_verificacao()

    # --- destino

    def _tela_destino(self):
        self._cabecalho("Preparar o iPhone novo",
                        "O backup está conferido e guardado no PC. Agora o iPhone novo pode ser apagado com segurança.")
        c = self._cartao("Faça no iPhone novo")
        self._numerada(c, [
            "Desconecte o iPhone antigo do PC. Não apague nada nele: ele é sua garantia até o fim.",
            "Apague o iPhone novo: Ajustes > Geral > Transferir ou Redefinir o iPhone > Apagar Conteúdo e Ajustes.",
            "Quando ele reiniciar, siga a configuração inicial (idioma, Wi-Fi…). Na tela \"Transferir Apps e "
            "Dados\", escolha \"Do Mac ou PC\" (ou \"Não transferir nada\" e depois conecte).",
            "Conecte o iPhone novo a este PC pelo cabo e toque em \"Confiar\" se aparecer.",
            "Clique em \"Conferir iPhone novo\" abaixo.",
        ])
        d = self.migracao.estado.destino
        if d:
            self._texto(c, f"Por segurança, só o iPhone escolhido como novo na conferência pode receber o backup: "
                           f"{Aparelho.de_dict(d).rotulo()}. Apagar o iPhone não muda essa identificação.",
                        cor=AZUL, pady=(0, 6))
        self._texto(c, "Alternativa sem apagar: deixe o iPhone novo configurado e desligue o \"Buscar iPhone\" nele. "
                       "A restauração substitui todo o conteúdo do mesmo jeito.", cor=CINZA, pady=(0, 14))
        c = self._cartao("Conferência do iPhone novo")
        ctk.CTkButton(c, text="🔍  Conferir iPhone novo", width=220, command=self._conferir_destino).pack(
            anchor="w", padx=18, pady=(4, 8))
        self.quadro_destino = ctk.CTkFrame(c, fg_color="transparent")
        self.quadro_destino.pack(fill="x", pady=(0, 10))
        self.var_ciente = ctk.BooleanVar(value=False)
        self.destino_ok = False
        ctk.CTkCheckBox(c, text="Entendo que todo o conteúdo atual do iPhone novo será substituído pelo do antigo.",
                        variable=self.var_ciente, command=self._habilitar_restaurar).pack(
            anchor="w", padx=18, pady=(0, 16))
        self._habilitar_restaurar()

    def _conferir_destino(self):
        for w in self.quadro_destino.winfo_children():
            w.destroy()
        self.em_segundo_plano(self.migracao.conferir_destino, self._mostrar_destino,
                              status="Conferindo o iPhone novo e o backup…")

    def _mostrar_destino(self, resultado):
        if self.tela_atual != "destino":
            return
        _, checagens = resultado
        self.destino_ok = not tem_bloqueio(checagens)
        self._lista_checagens(self.quadro_destino, checagens)
        self._habilitar_restaurar()

    def _habilitar_restaurar(self):
        self._botao_continuar("Restaurar agora →", self._confirmar_restauracao,
                              habilitado=self.destino_ok and self.var_ciente.get())

    def _confirmar_restauracao(self):
        d = self.migracao.estado.destino or {}
        nome = Aparelho.de_dict(d).rotulo() if d else "iPhone novo"
        if not messagebox.askyesno("Confirmar restauração",
                                   f"Restaurar o backup em:\n\n{nome}\n\nTodo o conteúdo atual desse iPhone será "
                                   "substituído. Continuar?", icon="warning", parent=self):
            return
        self.mostrar("restauracao")
        self._iniciar_restauracao()

    # --- restauração

    def _tela_restauracao(self):
        self._cabecalho("Restaurar no iPhone novo",
                        "O PC está enviando o backup para o iPhone novo. Ao final ele reinicia sozinho.")
        c = self._cartao("Durante a restauração")
        self._numerada(c, [
            "NÃO desconecte o iPhone novo nem use o PC para tarefas pesadas.",
            "A tela do iPhone vai mostrar \"Restaurando…\". É normal ele ficar assim por horas.",
            "Se o iPhone pedir o código de desbloqueio ou a senha do backup, digite-os.",
        ])
        c = self._cartao("Progresso")
        self.painel = self._painel_progresso(c, "Aguardando início.")
        self.btn_restaurar = ctk.CTkButton(c, text="↻  Tentar de novo", width=200, command=self._iniciar_restauracao)
        self._botao_continuar("Concluir →", habilitado=False)
        if self.migracao.estado.restaurado_em:
            self.painel["barra"].set(1)
            self.painel["pct"].configure(text="100%")
            self.painel["msg"].configure(text="Restauração concluída.")
            self._botao_continuar("Concluir →", lambda: self.mostrar("concluido"))
        elif not self.ocupado:
            self.btn_restaurar.configure(text="▶  Iniciar restauração")
            self.btn_restaurar.pack(anchor="w", padx=18, pady=(0, 14))

    def _iniciar_restauracao(self):
        if self.tela_atual != "restauracao":
            self.mostrar("restauracao")
        self.btn_restaurar.pack_forget()
        self._iniciar_progresso(self.painel)

        def prog(p):
            self._progresso = (float(p), "Enviando o backup para o iPhone novo…")

        self.em_segundo_plano(lambda: self.migracao.restaurar(prog), self._restauracao_ok, self._restauracao_falhou,
                              status="Restauração em andamento — não desconecte o iPhone…", manter_acordado=True)

    def _restauracao_ok(self, _):
        self._alvo_progresso = None
        messagebox.showinfo("Restauração concluída",
                            "Restauração concluída! O iPhone novo vai reiniciar. Veja os próximos passos.", parent=self)
        self.mostrar("concluido")

    def _restauracao_falhou(self, erro):
        self._alvo_progresso = None
        self._mostrar_erro(erro)
        if self.tela_atual == "restauracao":
            self.painel["msg"].configure(text="A restauração não terminou. O backup continua intacto no PC; "
                                              "resolva o problema e tente de novo.")
            self.btn_restaurar.configure(text="↻  Tentar de novo")
            self.btn_restaurar.pack(anchor="w", padx=18, pady=(0, 14))

    # --- concluído

    def _tela_concluido(self):
        e = self.migracao.estado
        self._cabecalho("Pronto! Falta pouco no iPhone novo",
                        "O conteúdo foi restaurado. Siga estes passos no iPhone novo para o WhatsApp aparecer com "
                        "todas as conversas.")
        c = self._cartao("No iPhone novo", cor_borda=VERDE)
        self._numerada(c, [
            "Espere ele reiniciar e conclua a configuração. Entre com o seu Apple ID quando pedir.",
            "Deixe-o no Wi-Fi e na tomada. Os apps, incluindo o WhatsApp, serão baixados da App Store "
            "automaticamente. Isso pode levar um tempo.",
            "Espere o ícone do WhatsApp terminar de baixar. NÃO apague nem reinstale o WhatsApp.",
            "Abra o WhatsApp e confirme o seu número por SMS. Depois confira as conversas e as mídias (abaixo).",
            "Quando o WhatsApp do novo for ativado, o do antigo é desconectado. Isso é normal.",
        ])
        c = self._cartao("Conferir o WhatsApp no iPhone novo", cor_borda=AZUL)
        self._texto(c, "Quando o iPhone novo terminar de reiniciar, desbloqueie-o, conecte pelo cabo e clique abaixo. "
                       "O programa confere se o WhatsApp voltou e se trouxe os dados do backup.", cor=CINZA)
        ctk.CTkButton(c, text="🔍  Conferir o WhatsApp no iPhone novo", width=300,
                      command=self._conferir_resultado).pack(anchor="w", padx=18, pady=(6, 6))
        self.quadro_resultado = ctk.CTkFrame(c, fg_color="transparent")
        self.quadro_resultado.pack(fill="x")
        if e.resultado:
            self._desenhar_resultado(e.resultado)
        self._texto(c, "Depois de abrir o WhatsApp e confirmar o número, confira e marque:", negrito=True,
                    pady=(10, 2))
        self.var_conversas = ctk.BooleanVar(value=bool(e.confirmado_em))
        self.var_midias = ctk.BooleanVar(value=bool(e.confirmado_em))
        ctk.CTkCheckBox(c, text="As conversas antigas aparecem (role até as mais antigas de uma conversa longa).",
                        variable=self.var_conversas).pack(anchor="w", padx=18, pady=4)
        ctk.CTkCheckBox(c, text="As fotos, vídeos e áudios antigos abrem.",
                        variable=self.var_midias).pack(anchor="w", padx=18, pady=4)
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(4, 14))
        ctk.CTkButton(linha, text="Registrar conferência", command=self._confirmar_resultado).pack(side="left")
        ctk.CTkButton(linha, text="Algo não veio: pedir ajuda", fg_color="transparent", border_width=1,
                      text_color=VERMELHO, command=self._dialogo_diagnostico).pack(side="left", padx=10)

        c = self._cartao("Depois de conferir")
        self._texto(c, "Só apague o backup do PC depois de conferir as conversas e mídias no iPhone novo. O backup "
                       "contém todo o conteúdo do iPhone antigo: não o deixe em computadores de terceiros.")
        linha = ctk.CTkFrame(c, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(8, 14))
        if e.pasta_backup:
            ctk.CTkButton(linha, text="Abrir pasta do backup",
                          command=lambda: os.startfile(e.pasta_backup) if Path(e.pasta_backup).exists() else None
                          ).pack(side="left")
        ctk.CTkButton(linha, text="Salvar relatório…", command=self._salvar_relatorio).pack(side="left", padx=10)
        self._botao_continuar("Nova migração", lambda: self.mostrar("inicio"))

    def _conferir_resultado(self):
        for w in self.quadro_resultado.winfo_children():
            w.destroy()
        self.em_segundo_plano(self.migracao.conferir_resultado, self._desenhar_resultado,
                              status="Conferindo o WhatsApp no iPhone novo…")

    def _desenhar_resultado(self, r: dict):
        if self.tela_atual != "concluido":
            return
        for w in self.quadro_resultado.winfo_children():
            w.destroy()
        estado = r.get("estado")
        if estado == "instalado":
            itens = [Checagem("WhatsApp no iPhone novo", Status.OK,
                              "Instalado" + (f" (versão {r['versao']})" if r.get("versao") else "") + ".")]
            prop = r.get("proporcao")
            if prop is not None:
                texto = (f"{formatar_bytes(r.get('dados'))} no iPhone novo; no iPhone antigo eram "
                         f"{formatar_bytes(r.get('esperado'))} (app + dados).")
                if prop >= 0.8:
                    itens.append(Checagem("Dados do WhatsApp", Status.OK, texto))
                else:
                    itens.append(Checagem("Dados do WhatsApp", Status.AVISO, texto,
                                          "Os dados podem ainda estar sendo organizados pelo iPhone. Abra o "
                                          "WhatsApp, espere alguns minutos e confira de novo. Se continuar "
                                          "bem menor, peça ajuda antes de apagar o backup."))
        elif estado == "instalando":
            itens = [Checagem("WhatsApp no iPhone novo", Status.AVISO, "Ainda está sendo baixado da App Store.",
                              "Deixe o iPhone no Wi-Fi e na tomada, espere o ícone terminar de baixar e confira de "
                              "novo. Não apague nem reinstale o WhatsApp.")]
        else:
            itens = [Checagem("WhatsApp no iPhone novo", Status.BLOQUEIO, "Não encontrei o WhatsApp.",
                              "Confira se você entrou com o Apple ID no iPhone novo (Ajustes > [seu nome]) e se "
                              "ele está no Wi-Fi. Os apps são baixados de novo depois da restauração. Se o "
                              "WhatsApp não aparecer, peça ajuda; o backup e o iPhone antigo continuam intactos.")]
        self._lista_checagens(self.quadro_resultado, itens)

    def _confirmar_resultado(self):
        conversas, midias = self.var_conversas.get(), self.var_midias.get()
        self.migracao.confirmar_resultado(conversas, midias)
        if conversas and midias:
            messagebox.showinfo("Conferência registrada", "Ótimo! A migração está confirmada. Agora você pode "
                                "apagar o backup do PC quando quiser.", parent=self)
        else:
            messagebox.showwarning("Conferência registrada", "Não apague o backup nem o iPhone antigo. Clique em "
                                   "\"Algo não veio: pedir ajuda\" para enviar o diagnóstico ao suporte.",
                                   parent=self)

    def _salvar_relatorio(self):
        e = self.migracao.estado
        destino = filedialog.asksaveasfilename(parent=self, defaultextension=".txt",
                                               initialfile="relatorio-migracao-whatsapp.txt",
                                               filetypes=[("Texto", "*.txt")])
        if not destino:
            return
        linhas = [APP_NOME, f"Versão {__version__}", ""]
        if e.origem:
            linhas.append(f"iPhone antigo: {Aparelho.de_dict(e.origem).rotulo()}")
        if e.destino:
            linhas.append(f"iPhone novo:   {Aparelho.de_dict(e.destino).rotulo()}")
        linhas.append(f"Pasta do backup: {e.pasta_backup}")
        rel = self.migracao.relatorio
        if rel:
            linhas += ["", "Verificação do backup:", f"  Conversas: {rel.conversas}", f"  Mensagens: {rel.mensagens}",
                       f"  Mídias: {rel.midias} ({formatar_bytes(rel.bytes_midia)})",
                       f"  Arquivos: {rel.arquivos} ({formatar_bytes(rel.bytes_total)})",
                       f"  Impressão digital: {rel.impressao_digital}"]
        linhas += ["", "Histórico:"] + [f"  {h}" for h in e.historico]
        Path(destino).write_text("\n".join(linhas), encoding="utf-8")

    # ------------------------------------------------------------------ saiba mais

    def _saiba_mais(self):
        if getattr(self, "janela_sobre", None) and self.janela_sobre.winfo_exists():
            self.janela_sobre.focus()
            return
        j = self.janela_sobre = ctk.CTkToplevel(self)
        j.title("Saiba mais — sobre e licenças")
        j.geometry("760x680")
        j.transient(self)
        corpo = ctk.CTkScrollableFrame(j, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=12, pady=12)

        def secao(titulo):
            c = ctk.CTkFrame(corpo, fg_color=CARTAO, corner_radius=12)
            c.pack(fill="x", padx=6, pady=6)
            ctk.CTkLabel(c, text=titulo, font=ctk.CTkFont(size=16, weight="bold"), anchor="w").pack(
                anchor="w", padx=18, pady=(14, 4))
            return c

        def texto(pai, t, cor=None, pady=(2, 2)):
            ctk.CTkLabel(pai, text=t, anchor="w", justify="left", wraplength=660, text_color=cor).pack(
                anchor="w", padx=18, pady=pady)

        def link(pai, rotulo, acao):
            ctk.CTkButton(pai, text=rotulo, anchor="w", fg_color="transparent", text_color=AZUL, hover_color=CARTAO,
                          height=26, command=acao).pack(anchor="w", padx=10)

        topo = ctk.CTkFrame(corpo, fg_color="transparent")
        topo.pack(fill="x", padx=6, pady=(4, 8))
        ctk.CTkLabel(topo, text=APP_NOME, font=ctk.CTkFont(size=22, weight="bold"), text_color=VERDE,
                     anchor="w").pack(anchor="w", padx=12)
        ctk.CTkLabel(topo, text=f"Versão {__version__}  ·  © 2026 {EMPRESA}", text_color=CINZA,
                     anchor="w").pack(anchor="w", padx=12)

        c = secao("Desenvolvimento")
        texto(c, f"Desenvolvido pela {EMPRESA}.")
        texto(c, "É software livre e de código aberto, aberto à comunidade: qualquer pessoa pode usar de graça, "
                 "estudar como funciona, sugerir melhorias, corrigir problemas e redistribuir, nos termos da licença "
                 "GPL-3.0.", pady=(2, 6))
        link(c, "→  Código-fonte no GitHub", lambda: webbrowser.open(URL_REPOSITORIO))
        link(c, "→  Relatar um problema ou sugerir uma melhoria", lambda: webbrowser.open(URL_PROBLEMAS))
        link(c, "→  Como contribuir com o projeto", lambda: webbrowser.open(URL_CONTRIBUIR))
        ctk.CTkFrame(c, height=10, fg_color="transparent").pack()

        c = secao("Licença")
        texto(c, "Este programa é distribuído sob a Licença Pública Geral GNU (GPL), versão 3 ou qualquer versão "
                 "posterior (GPL-3.0-or-later). Você pode redistribuí-lo e modificá-lo nesses termos. Quem distribuir "
                 "uma versão modificada precisa manter a mesma licença e disponibilizar o código-fonte.")
        texto(c, "O programa é oferecido SEM NENHUMA GARANTIA, nem mesmo a garantia implícita de "
                 "COMERCIALIZAÇÃO ou de ADEQUAÇÃO A UM PROPÓSITO ESPECÍFICO. Consulte a licença para mais "
                 "detalhes.", cor=CINZA, pady=(2, 6))
        link(c, "→  Ler a licença completa (GPL-3.0)",
             lambda: self._mostrar_documento("LICENCA.txt", "Licença GPL-3.0"))
        ctk.CTkFrame(c, height=10, fg_color="transparent").pack()

        c = secao("Componentes de terceiros")
        texto(c, "O programa só existe graças a estes projetos de código aberto:")
        for nome, lic, papel in [
            ("pymobiledevice3", "GPL-3.0-or-later", "comunicação com o iPhone (backup e restauração)"),
            ("pyiosbackup", "GPL-3.0-or-later", "leitura dos backups do iPhone"),
            ("CustomTkinter", "MIT", "interface visual"),
            ("cryptography", "Apache-2.0 / BSD-3-Clause", "criptografia do backup"),
            ("Python e Tcl/Tk", "PSF / estilo BSD", "linguagem e base gráfica"),
        ]:
            texto(c, f"•  {nome} ({lic}): {papel}")
        texto(c, "A lista completa, com versões e o texto de cada licença, acompanha o programa.", cor=CINZA,
              pady=(6, 6))
        link(c, "→  Ver todas as licenças de terceiros",
             lambda: self._mostrar_documento("TERCEIROS.txt", "Licenças de terceiros"))
        ctk.CTkFrame(c, height=10, fg_color="transparent").pack()

        c = secao("Privacidade")
        texto(c, "O programa não envia suas conversas, fotos, contatos ou qualquer dado dos iPhones para lugar "
                 "nenhum: o backup fica só no disco que você escolher neste PC.")
        texto(c, "Ele acessa a internet para: (1) consultar no GitHub se há versão nova do programa, (2) "
                 "consultar na Apple qual é a versão mais recente do iOS para cada modelo de iPhone e (3) se você "
                 "permitir, enviar dados anônimos de funcionamento ao servidor do projeto.")
        texto(c, "Os dados anônimos dizem em que etapa a migração está, os erros, o tempo gasto, o modelo, a versão "
                 "do iOS e o espaço dos iPhones. Nunca levam conversas, fotos, contatos, senhas, nomes dos "
                 "aparelhos ou números de série. Ficam guardados por 180 dias.")
        var_tel = ctk.BooleanVar(value=telemetria.consentimento() is True)
        ctk.CTkCheckBox(c, text="Enviar dados anônimos de funcionamento", variable=var_tel,
                        command=lambda: telemetria.definir_consentimento(var_tel.get())).pack(
            anchor="w", padx=18, pady=(4, 2))
        texto(c, "O diagnóstico com os registros só é enviado quando você clica em \"Enviar diagnóstico ao "
                 "suporte\".", cor=CINZA, pady=(2, 14))

        c = secao("Marcas")
        texto(c, "WhatsApp é marca da Meta Platforms, Inc. iPhone, iOS, iTunes e iCloud são marcas da Apple Inc. "
                 "Este projeto é independente e não tem vínculo com a Meta nem com a Apple.", pady=(2, 14))

    def _mostrar_documento(self, nome: str, titulo: str):
        arquivo = documento_legal(nome)
        if not arquivo:
            if messagebox.askyesno(titulo, "Este arquivo acompanha a versão instalada do programa. Abrir a página "
                                          "do projeto no GitHub?", parent=self.janela_sobre):
                webbrowser.open(URL_REPOSITORIO)
            return
        j = ctk.CTkToplevel(self.janela_sobre)
        j.title(titulo)
        j.geometry("820x640")
        j.transient(self.janela_sobre)
        caixa = ctk.CTkTextbox(j, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        caixa.pack(fill="both", expand=True, padx=10, pady=10)
        caixa.insert("1.0", arquivo.read_text(encoding="utf-8", errors="replace"))
        caixa.configure(state="disabled")

    # ------------------------------------------------------------------ atualização

    def _checar_atualizacao(self, manual=False):
        def pronto(lanc):
            if lanc:
                self.lancamento = lanc
                self._mostrar_faixa_atualizacao()
            elif manual:
                messagebox.showinfo("Atualização", f"Você já está na versão mais recente ({__version__}).", parent=self)

        def buscar():
            lanc = atualizador.buscar()
            self.fila.put(lambda: pronto(lanc))

        threading.Thread(target=buscar, daemon=True).start()
        if not manual:
            self.after(INTERVALO_ATUALIZACAO_MS, self._checar_atualizacao)

    def _mostrar_faixa_atualizacao(self):
        v = self.lancamento.versao
        if atualizador.pode_atualizar_sozinho():
            self.lbl_atualizacao.configure(text=f"Nova versão {v} disponível.")
            self.btn_atualizacao.configure(text="Atualizar agora", command=self._atualizar_agora)
        else:
            self.lbl_atualizacao.configure(text=f"Nova versão {v} disponível no GitHub.")
            self.btn_atualizacao.configure(text="Abrir página", command=lambda: webbrowser.open(
                f"https://github.com/{REPOSITORIO}/releases/latest"))
        self.faixa_atualizacao.grid(row=0, column=0, sticky="ew")

    def _atualizar_agora(self):
        if self.ocupado:
            messagebox.showwarning("Atualização", "Espere a operação atual terminar. A atualização não interrompe "
                                                  "backup nem restauração.", parent=self)
            return
        if not messagebox.askyesno("Atualizar", f"Baixar e instalar a versão {self.lancamento.versao}? O programa "
                                                "vai fechar e abrir de novo. O progresso da migração é mantido.",
                                   parent=self):
            return
        self.btn_atualizacao.configure(state="disabled", text="Baixando…")

        def prog(f):
            self.fila.put(lambda: self.btn_atualizacao.configure(text=f"Baixando {f * 100:.0f}%"))

        def fim(zip_path):
            if self.ocupado:
                return
            atualizador.aplicar_e_reiniciar(zip_path)

        def falha(e):
            self.btn_atualizacao.configure(state="normal", text="Atualizar agora")
            self._mostrar_erro(e)

        self.em_segundo_plano(lambda: atualizador.baixar(self.lancamento, prog), fim, falha,
                              status="Baixando atualização…")
