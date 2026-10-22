"""Orquestra a migração. A interface só chama estes métodos; cada um confere
pré-condições e salva o progresso antes e depois de agir."""
import logging
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from migrador import checagens, disco, estado as est, sistema, telemetria, versoes_ios
from migrador.dispositivos import criar
from migrador.dispositivos.base import Aviso, Backend, Cancelado, ErroAparelho
from migrador.modelos import (
    Aparelho, Checagem, InfoWhatsApp, RelatorioBackup, Status, nome_whatsapp, tem_bloqueio,
)
from migrador.config import pasta_dados
from migrador.verificacao import (
    SenhaIncorreta, backup_completo_existe, conferir_antes_de_restaurar, verificar_backup,
)

log = logging.getLogger(__name__)

GB = 1024**3


class Migracao:
    def __init__(self, estado: est.Estado, backend: Backend | None = None):
        self.estado = estado
        self.backend = backend or criar(estado.simulacao, estado.cenario)
        self.senha: str | None = estado.senha_guardada()
        self.aparelhos: list[Aparelho] = []

    @classmethod
    def nova(cls, simulacao: bool, cenario: str = "normal") -> "Migracao":
        est.arquivar()
        e = est.Estado(simulacao=simulacao, cenario=cenario)
        e.anotar("Nova migração iniciada" + (f" (simulação: {cenario})" if simulacao else ""))
        m = cls(e)
        m.salvar()
        return m

    @classmethod
    def retomar(cls) -> "Migracao | None":
        e = est.carregar()
        return cls(e) if e else None

    def salvar(self) -> None:
        est.salvar(self.estado)

    def _evento(self, tipo: str, **dados) -> None:
        e = self.estado
        telemetria.registrar(tipo, etapa=e.etapa, sessao=e.sessao, simulacao=e.simulacao, **dados)

    def _evento_falha(self, tipo: str, erro: BaseException, acomp: "_Acompanhamento", **extra) -> None:
        if isinstance(erro, Cancelado):
            tipo = tipo.replace("_falha", "_cancelado")
        self._evento(tipo, **_descrever_erro(erro), minutos=acomp.minutos(), porcentagem=acomp.ultimo, **extra)

    def _espaco_na_falha(self) -> dict:
        """Onde faltou espaço, se faltou: PC (pasta do backup) e iPhone antigo, lidos logo depois da falha."""
        e, d = self.estado, {}
        try:
            d["pc_livre_gb"] = _gb(shutil.disk_usage(e.pasta_backup).free)
            d["pasta_gb"] = _gb(_tamanho_pasta(e.pasta_aparelho))
        except Exception:
            log.exception("falha lendo o espaço do PC")
        try:
            origem = next((a for a in self.backend.listar() if a.udid == e.origem["udid"]), None)
            if origem:
                d["iphone_livre_gb"] = _gb(origem.livre)
        except Exception:
            log.exception("falha lendo o espaço do iPhone antigo")
        atividade = getattr(self.backend, "ultima_atividade", None)
        if atividade:
            d["atividade"] = atividade
        log.info("backup interrompido: %s", d)
        return d

    # --- versões ----------------------------------------------------------------------

    def ultimas_versoes(self) -> dict[str, str] | None:
        if hasattr(self.backend, "ultimas_versoes"):
            return self.backend.ultimas_versoes()
        return versoes_ios.buscar_ultimas()

    # --- sistema do PC -----------------------------------------------------------------

    def verificar_sistema(self, momento: str = "preparacao") -> list[Checagem]:
        """Verificações do PC (app e serviço da Apple, confiança, energia, tampa, concorrentes)."""
        s = self.backend.ler_sistema()
        self._sistema = s
        c = checagens.checar_sistema(s, simulado=self.backend.simulado, momento=momento)
        if self.backend.simulado:
            _rebaixar_na_simulacao(c)
        _registrar_checagens(f"sistema ({momento})", c)
        self._evento("sistema", momento=momento, liberada=not tem_bloqueio(c), pendencias=_pendencias(c),
                     **{k: v for k, v in s.para_log().items() if k not in ("usbmux_ok",)})
        return c

    def _exigir_pc_pronto(self, momento: str) -> None:
        """Logo antes de backup/restauração: energia, serviço e concorrentes podem ter mudado."""
        bloqueios = [ch for ch in self.verificar_sistema(momento) if ch.status == Status.BLOQUEIO]
        if bloqueios:
            ch = bloqueios[0]
            raise ErroAparelho(f"{ch.titulo}: {ch.detalhe}", ch.como_resolver)

    # --- conexão ----------------------------------------------------------------------

    def procurar(self) -> list[Aparelho]:
        self.aparelhos = self.backend.listar()
        return self.aparelhos

    def conferir(self, origem: Aparelho, destino: Aparelho | None, pasta: Path,
                 escolhidos: list[str] | None = None) -> list[Checagem]:
        """`escolhidos`: WhatsApps a migrar e conferir (comum, Business ou os dois). None = todos
        os instalados no iPhone antigo. `destino` None: só o iPhone antigo está conectado agora; o
        novo é conectado e conferido depois do backup."""
        c = checagens.checar_conexao(self.aparelhos)
        if tem_bloqueio(c):
            return c
        whatsapps: list[InfoWhatsApp] = []
        if destino is None or origem.udid != destino.udid:
            whatsapps = self.backend.whatsapps(origem.udid)
        self.whatsapps_encontrados = whatsapps
        if escolhidos is None:
            escolhidos = [w.bundle for w in whatsapps]
        c += checagens.checar_aparelhos(origem, destino, whatsapps, self.ultimas_versoes(), escolhidos)
        necessario = checagens.tamanho_estimado_backup(origem, whatsapps)
        # Um backup anterior na mesma pasta é reaproveitado (incremental) e não conta como espaço a ocupar.
        ja_ocupado = _tamanho_pasta(pasta / origem.udid)
        pc = checagens.checar_confianca([origem] + ([destino] if destino else []))
        pc += self.verificar_sistema("conexao")
        pc += checagens.checar_pc(None, pasta, disco.espaco_livre(pasta) + ja_ocupado,
                                  disco.sistema_arquivos(pasta), necessario)
        pc += checagens.checar_pasta(pasta, sistema.pasta_sincronizada(pasta), sistema.pasta_gravavel(pasta),
                                     sistema.disco_removivel(pasta))
        if self.backend.simulado:
            # O backup simulado ocupa poucos MB; mostra a regra real sem travar o treino.
            _rebaixar_na_simulacao(pc)
        c += pc
        _registrar_checagens("conferência", c)
        self._evento("conferencia", liberada=not tem_bloqueio(c), pendencias=_pendencias(c),
                     origem=_resumo_aparelho(origem), destino=_resumo_aparelho(destino),
                     whatsapps={w.bundle: _gb(w.tamanho) for w in whatsapps}, escolhidos=escolhidos,
                     estimado_gb=_gb(necessario))
        if not tem_bloqueio(c):
            e = self.estado
            e.origem, e.destino = origem.para_dict(), destino.para_dict() if destino else None
            e.destino_depois = destino is None
            if e.relatorio and set(escolhidos) != set(e.bundles_escolhidos):
                # A verificação anterior conferiu outros WhatsApps: não vale para a escolha nova.
                e.relatorio = None
                e.anotar("Escolha de WhatsApp mudou; o backup precisa ser verificado de novo")
            e.whatsapps = [{"bundle": w.bundle, "tamanho": w.tamanho} for w in whatsapps]
            e.escolhidos = list(escolhidos)
            e.whatsapp = None
            e.pasta_backup = str(pasta)
            nomes = ", ".join(nome_whatsapp(b) for b in escolhidos)
            novo = destino.rotulo() if destino else "iPhone novo a conectar depois do backup"
            e.anotar(f"Conferência aprovada: {origem.rotulo()} → {novo}; backup em {pasta}; "
                     f"migrar e conferir: {nomes}")
            e.avancar_para("senha")
            self.salvar()
        return c

    # --- senha ------------------------------------------------------------------------

    def criptografia_ativa(self) -> bool:
        ativa = self.backend.criptografia_ativa(self.estado.origem["udid"])
        self.estado.criptografia_ja_ativa = ativa
        self.salvar()
        return ativa

    def definir_senha(self, senha: str, guardar: bool) -> None:
        if len(senha) < 4:
            raise ErroAparelho("A senha precisa ter pelo menos 4 caracteres.")
        udid = self.estado.origem["udid"]
        ja_ativa = self.estado.criptografia_ja_ativa
        try:
            if ja_ativa:
                self.backend.validar_senha(udid, senha)
                self.estado.anotar("Senha de backup existente conferida")
            else:
                self.backend.ativar_criptografia(udid, senha)
                self.estado.criptografia_ja_ativa = True
                self.estado.anotar("Backup com senha ativado no iPhone antigo")
        except Exception as erro:
            self._evento("senha_falha", ja_ativa=ja_ativa, **_descrever_erro(erro))
            raise
        self._evento("senha_ok", ja_ativa=ja_ativa)
        self.senha = senha
        if guardar and self.estado.guardar_senha(senha):
            self.estado.anotar("Senha guardada protegida pelo Windows")
        self.estado.avancar_para("backup")
        self.salvar()

    # --- backup -----------------------------------------------------------------------

    def backup_anterior_completo(self) -> bool:
        return backup_completo_existe(self.estado.pasta_aparelho)

    def fazer_backup(self, progresso: Callable[[float], None], cancelar: threading.Event,
                     aviso: Aviso | None = None) -> None:
        e = self.estado
        if not e.origem or not e.pasta_backup:
            raise ErroAparelho("Faça a conferência dos aparelhos antes do backup.")
        presentes = {a.udid for a in self.backend.listar() if not a.erro}
        if e.origem["udid"] not in presentes:
            raise ErroAparelho("O iPhone antigo não está conectado.", "Conecte e desbloqueie o iPhone antigo.")
        self._exigir_pc_pronto("backup")
        e.anotar("Backup iniciado")
        e.relatorio = None
        self.salvar()
        incremental = self.backup_anterior_completo()
        self._evento("backup_inicio", incremental=incremental)
        acomp = _Acompanhamento(progresso, "backup")
        try:
            self.backend.fazer_backup(e.origem["udid"], Path(e.pasta_backup), acomp, cancelar, aviso)
        except BaseException as erro:
            extra = {} if isinstance(erro, Cancelado) else self._espaco_na_falha()
            self._evento_falha("backup_falha", erro, acomp, **extra)
            raise
        self._evento("backup_ok", incremental=incremental, minutos=acomp.minutos(),
                     pasta_gb=_gb(_tamanho_pasta(e.pasta_aparelho)))
        e.backup_concluido_em = datetime.now().isoformat(timespec="seconds")
        e.anotar("Backup concluído")
        e.avancar_para("verificacao")
        self.salvar()

    # --- verificação ------------------------------------------------------------------

    def verificar(self, progresso: Callable[[float, str], None]) -> RelatorioBackup:
        e = self.estado
        pasta = e.pasta_aparelho
        if not pasta or not pasta.exists():
            raise ErroAparelho("Não encontrei a pasta do backup.", f"Esperada em: {pasta}")
        try:
            rel = verificar_backup(pasta, self.senha or "", progresso,
                                   exigir_criptografia=e.criptografia_ja_ativa,
                                   inventario_destino=self._arquivo_inventario(),
                                   bundles=e.bundles_escolhidos or None)
        except SenhaIncorreta:
            self._evento("verificacao_falha", erro="SenhaIncorreta")
            raise ErroAparelho("A senha não abre este backup.", "Digite a senha que foi usada no backup.")
        except Exception as erro:
            self._evento("verificacao_falha", **_descrever_erro(erro))
            raise
        e.relatorio = rel.para_dict()
        e.anotar(f"Verificação: {'APROVADO' if rel.ok else 'REPROVADO'} — {rel.mensagens} mensagens, "
                 f"{rel.midias} mídias, {rel.arquivos} arquivos")
        self._evento("verificacao", aprovado=rel.ok, problemas=rel.problemas[:10], avisos=rel.avisos[:10],
                     criptografado=rel.criptografado, ios_backup=rel.ios_backup, arquivos=rel.arquivos,
                     backup_gb=_gb(rel.bytes_total), midias=rel.midias, whatsapp=rel.whatsapp_bundle,
                     banco_integro=rel.banco_integro, mensagens=rel.mensagens, conversas=rel.conversas,
                     anexos_referenciados=rel.anexos_referenciados, anexos_presentes=rel.anexos_presentes,
                     amostra=rel.amostra_conferida, amostra_falhas=rel.amostra_falhas,
                     whatsapp_gb=_gb(rel.bytes_whatsapp), escolhidos=e.bundles_escolhidos,
                     no_backup=rel.whatsapps_no_backup,
                     por_app={w["bundle"]: {k: w[k] for k in ("mensagens", "midias", "anexos_referenciados",
                                                              "anexos_presentes", "amostra_falhas", "banco_integro")}
                              for w in rel.whatsapps})
        if rel.ok:
            e.avancar_para("destino")
        self.salvar()
        return rel

    def _arquivo_inventario(self) -> Path:
        pasta = pasta_dados() / "inventarios"
        pasta.mkdir(exist_ok=True)
        return pasta / f"{self.estado.sessao}.tsv.gz"

    @property
    def relatorio(self) -> RelatorioBackup | None:
        return RelatorioBackup.de_dict(self.estado.relatorio) if self.estado.relatorio else None

    # --- destino e restauração --------------------------------------------------------

    def conferir_destino(self) -> tuple[Aparelho | None, list[Checagem]]:
        e, rel = self.estado, self.relatorio
        if not rel or not rel.ok:
            raise ErroAparelho("O backup ainda não foi verificado com sucesso.")
        aparelhos = self.procurar()
        c = checagens.checar_conexao(aparelhos)
        prontos = [a for a in aparelhos if not a.erro]
        if tem_bloqueio(c):
            return None, c
        if not prontos:
            return None, [checagens.Checagem("iPhone novo", checagens.Status.BLOQUEIO, "Nenhum iPhone conectado.",
                                             "Conecte o iPhone novo ao PC pelo cabo e desbloqueie.")]
        if e.destino_depois:
            # Conferência inicial só com o iPhone antigo: o novo é o único conectado que não é o antigo.
            # Com mais de um candidato, nenhum é escolhido sozinho, porque a restauração apaga o aparelho.
            outros = [a for a in prontos if a.udid != e.origem["udid"]]
            if len(outros) > 1:
                c.append(checagens.Checagem(
                    "iPhone novo", checagens.Status.BLOQUEIO,
                    f"Há {len(outros)} iPhones conectados além do antigo, e não sei qual deles é o novo.",
                    "Deixe conectado só o iPhone novo e clique em \"Conferir iPhone novo\" de novo."))
                _registrar_checagens("conferência do iPhone novo", c)
                return None, c
            destino = outros[0] if outros else None
        else:
            # Só o iPhone escolhido como novo na conferência inicial pode receber o backup: a restauração
            # apaga o aparelho, então outro iPhone conectado por engano nunca é escolhido sozinho.
            escolhido = (e.destino or {}).get("udid")
            destino = next((a for a in prontos if a.udid == escolhido), None)
        if destino is None:
            antigo = next((a for a in prontos if a.udid == e.origem["udid"]), None)
            if antigo and len(prontos) == 1:
                c += checagens.checar_destino_para_restaurar(antigo, e.origem["udid"], rel.ios_backup,
                                                             rel.bytes_total, self.ultimas_versoes())
                return antigo, c
            rotulo = Aparelho.de_dict(e.destino).rotulo() if e.destino else "o iPhone novo"
            c.append(checagens.Checagem(
                "iPhone novo", checagens.Status.BLOQUEIO,
                f"O iPhone escolhido como novo na conferência ({rotulo}) não está conectado. "
                + ("Há outro iPhone conectado, mas ele não foi o escolhido." if prontos else ""),
                "Conecte o iPhone novo escolhido antes. Para usar outro aparelho como novo, volte à etapa "
                "\"Conectar e conferir\" e faça a conferência de novo."))
            _registrar_checagens("conferência do iPhone novo", c)
            return None, c
        c += checagens.checar_destino_para_restaurar(destino, e.origem["udid"], rel.ios_backup, rel.bytes_total,
                                                     self.ultimas_versoes())
        problemas = conferir_antes_de_restaurar(e.pasta_aparelho, rel, self._arquivo_inventario())
        if problemas:
            c += [checagens.Checagem("Integridade do backup", checagens.Status.BLOQUEIO, p,
                                     "Volte à etapa de verificação.") for p in problemas]
        else:
            c.append(checagens.Checagem(
                "Integridade do backup", checagens.Status.OK,
                f"Nenhum dos {rel.inventario_arquivos:,} arquivos mudou desde a verificação".replace(",", ".")
                + " (tamanho e data de cada um conferidos)."))
        c += self.verificar_sistema("restauracao")
        _registrar_checagens("conferência do iPhone novo", c)
        self._evento("conferencia_destino", liberada=not tem_bloqueio(c), pendencias=_pendencias(c),
                     destino=_resumo_aparelho(destino))
        if not tem_bloqueio(c):
            e.destino = destino.para_dict()
            e.destino_conferido = destino.udid
            self.salvar()
        return destino, c

    def restaurar(self, progresso: Callable[[float], None]) -> None:
        e, rel = self.estado, self.relatorio
        if not rel or not rel.ok or not e.destino:
            raise ErroAparelho("Faça a verificação do backup e do iPhone novo antes de restaurar.")
        if e.destino["udid"] == e.origem["udid"]:
            raise ErroAparelho("O aparelho escolhido para restaurar é o iPhone antigo. Operação cancelada.")
        if e.destino_conferido != e.destino["udid"]:
            raise ErroAparelho("O iPhone novo ainda não foi conferido nesta etapa.", "Clique em \"Conferir o iPhone "
                               "novo\" antes de restaurar.")
        if rel.criptografado and not self.senha:
            raise ErroAparelho("Informe a senha do backup antes de restaurar.")
        presentes = {a.udid for a in self.backend.listar() if not a.erro}
        if e.destino["udid"] not in presentes:
            raise ErroAparelho("O iPhone novo conferido não está mais conectado. Nada foi alterado.",
                               "Conecte e desbloqueie o iPhone novo e confira de novo.")
        problemas = conferir_antes_de_restaurar(e.pasta_aparelho, rel, self._arquivo_inventario())
        if problemas:
            raise ErroAparelho(problemas[0], "Volte à etapa de verificação.")
        self._exigir_pc_pronto("restauracao")
        e.anotar(f"Restauração iniciada em {e.destino.get('nome') or e.destino['udid']}")
        e.avancar_para("restauracao")
        self.salvar()
        self._evento("restauracao_inicio", destino=_resumo_aparelho(Aparelho.de_dict(e.destino)))
        acomp = _Acompanhamento(progresso, "restauração")
        try:
            self.backend.restaurar(e.destino["udid"], Path(e.pasta_backup), e.origem["udid"],
                                   self.senha or "", acomp)
        except BaseException as erro:
            self._evento_falha("restauracao_falha", erro, acomp)
            raise
        self._evento("restauracao_ok", minutos=acomp.minutos())
        e.restaurado_em = datetime.now().isoformat(timespec="seconds")
        e.anotar("Restauração concluída; o iPhone novo vai reiniciar")
        e.avancar_para("concluido")
        self.salvar()

    # --- conferência no iPhone novo ------------------------------------------------------

    def conferir_resultado(self) -> dict[str, dict]:
        """Depois que o iPhone novo reinicia: cada WhatsApp escolhido voltou e trouxe os dados?
        Devolve {bundle: {estado, versao, dados, esperado, proporcao}}."""
        e, rel = self.estado, self.relatorio
        if not e.restaurado_em or not e.destino:
            raise ErroAparelho("A restauração ainda não foi concluída.")
        presentes = {a.udid: a for a in self.backend.listar()}
        ap = presentes.get(e.destino["udid"])
        if ap is None:
            raise ErroAparelho("O iPhone novo não está conectado.",
                               "Depois que ele reiniciar, desbloqueie, conecte pelo cabo e tente de novo.")
        if ap.erro:
            raise ErroAparelho("O iPhone novo ainda não liberou a conexão.",
                               "Desbloqueie o iPhone novo, toque em \"Confiar\" se aparecer e tente de novo.")
        no_destino = self.backend.whatsapps_no_destino(e.destino["udid"])
        por_bundle = {w["bundle"]: w for w in (rel.whatsapps if rel else [])}
        resultado = {}
        for bundle in e.bundles_escolhidos:
            r = dict(no_destino.get(bundle) or {"estado": "ausente"})
            # Compara com o que o iPhone antigo informava (mesma medida: app + dados).
            esperado = e.tamanho_whatsapp(bundle) or por_bundle.get(bundle, {}).get("bytes_whatsapp") or 0
            r["esperado"] = esperado
            if r.get("estado") == "instalado" and esperado and r.get("dados") is not None:
                r["proporcao"] = round(r["dados"] / esperado, 2)
            resultado[bundle] = r
        e.resultado = resultado
        resumo = "; ".join(f"{nome_whatsapp(b)} {r.get('estado')}"
                           + (f" ({r['proporcao']:.0%} dos dados)" if "proporcao" in r else "")
                           for b, r in resultado.items())
        e.anotar(f"Conferência no iPhone novo: {resumo}")
        self.salvar()
        self._evento("resultado", por_app={b: {"estado": r.get("estado"), "dados_gb": _gb(r.get("dados")),
                                               "esperado_gb": _gb(r.get("esperado")), "proporcao": r.get("proporcao"),
                                               "versao": r.get("versao")} for b, r in resultado.items()})
        return resultado

    def confirmar_resultado(self, conversas_ok: bool, midias_ok: bool, observacao: str = "") -> None:
        """A pessoa conferiu no iPhone novo (todos os WhatsApps escolhidos)."""
        e = self.estado
        e.confirmado_em = datetime.now().isoformat(timespec="seconds")
        e.anotar(f"Pessoa conferiu no iPhone novo: conversas {'OK' if conversas_ok else 'COM PROBLEMA'}, "
                 f"mídias {'OK' if midias_ok else 'COM PROBLEMA'}")
        self.salvar()
        from migrador.diagnostico import anonimizar
        self._evento("resultado_confirmado", conversas_ok=conversas_ok, midias_ok=midias_ok,
                     escolhidos=e.bundles_escolhidos,
                     observacao=anonimizar(observacao)[:500] or None)


def _rebaixar_na_simulacao(c: list[Checagem]) -> None:
    """No treino, mostra as regras reais do PC sem impedir de seguir."""
    for ch in c:
        if ch.status == Status.BLOQUEIO:
            ch.status = Status.AVISO
            ch.detalhe = "[Simulação: numa migração real, isto impediria continuar.] " + ch.detalhe


def _registrar_checagens(onde: str, c: list[Checagem]) -> None:
    for ch in c:
        if ch.status != Status.OK:
            log.info("%s: [%s] %s — %s", onde, ch.status.name, ch.titulo, ch.detalhe)
    log.info("%s: %d itens, %s", onde, len(c), "com bloqueio" if tem_bloqueio(c) else "liberada")


class _Acompanhamento:
    """Repassa o progresso para a tela e anota no log cada 5% e o tempo: mostra onde um
    backup travou ou ficou lento. Guarda a última porcentagem para o evento de falha."""

    def __init__(self, progresso: Callable[[float], None], nome: str):
        self.progresso, self.nome = progresso, nome
        self.inicio = time.monotonic()
        self.ultimo: float | None = None
        self._anotado = -5.0

    def minutos(self) -> float:
        return round((time.monotonic() - self.inicio) / 60, 1)

    def __call__(self, p) -> None:
        try:
            valor = float(p)
        except (TypeError, ValueError):
            valor = None
        if valor is not None:
            self.ultimo = round(valor, 1)
            if valor >= self._anotado + 5:
                self._anotado = valor - valor % 5
                log.info("%s: %.0f%% em %.0f min", self.nome, valor, self.minutos())
        self.progresso(p)


def _gb(n: int | None) -> float | None:
    return round(n / GB, 1) if n else None


def _resumo_aparelho(ap: Aparelho | None) -> dict | None:
    """Só o que ajuda a investigar: modelo, iOS, espaço e bateria. Sem nome nem número de série."""
    if not ap:
        return None
    return {"modelo": ap.tipo, "ios": ap.ios, "capacidade_gb": _gb(ap.capacidade), "livre_gb": _gb(ap.livre),
            "bateria": ap.bateria, "buscar": ap.buscar_ativo, "erro": ap.erro}


def _pendencias(c: list[Checagem]) -> list[str]:
    return [f"{ch.status.value}: {ch.titulo}" for ch in c if ch.status in (Status.BLOQUEIO, Status.AVISO)]


def _descrever_erro(erro: BaseException) -> dict:
    from migrador.diagnostico import anonimizar
    d = {"erro": type(erro).__name__, "mensagem": anonimizar(str(erro))[:300]}
    if isinstance(erro, ErroAparelho) and erro.como_resolver:
        d["orientacao"] = anonimizar(erro.como_resolver)[:300]
    causa = erro.__cause__ or erro.__context__
    if causa is not None and causa is not erro:
        d["causa"] = type(causa).__name__
        d["causa_mensagem"] = anonimizar(str(causa))[:300]
    return d


def _tamanho_pasta(pasta: Path) -> int:
    if not pasta.exists():
        return 0
    total = 0
    for arq in pasta.rglob("*"):
        try:
            if arq.is_file():
                total += arq.stat().st_size
        except OSError:
            pass
    return total
