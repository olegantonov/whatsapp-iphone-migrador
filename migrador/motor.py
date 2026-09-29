"""Orquestra a migração. A interface só chama estes métodos; cada um confere
pré-condições e salva o progresso antes e depois de agir."""
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from migrador import checagens, disco, estado as est, versoes_ios
from migrador.dispositivos import criar
from migrador.dispositivos.base import Backend, ErroAparelho
from migrador.modelos import Aparelho, Checagem, InfoWhatsApp, RelatorioBackup, Status, tem_bloqueio
from migrador.verificacao import (
    SenhaIncorreta, backup_completo_existe, conferir_antes_de_restaurar, verificar_backup,
)

log = logging.getLogger(__name__)


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

    # --- versões ----------------------------------------------------------------------

    def ultimas_versoes(self) -> dict[str, str] | None:
        if hasattr(self.backend, "ultimas_versoes"):
            return self.backend.ultimas_versoes()
        return versoes_ios.buscar_ultimas()

    # --- conexão ----------------------------------------------------------------------

    def procurar(self) -> list[Aparelho]:
        self.aparelhos = self.backend.listar()
        return self.aparelhos

    def conferir(self, origem: Aparelho, destino: Aparelho, pasta: Path) -> list[Checagem]:
        c = checagens.checar_conexao(self.aparelhos)
        if tem_bloqueio(c):
            return c
        whatsapp: InfoWhatsApp | None = None
        if origem.udid != destino.udid:
            whatsapp = self.backend.whatsapp(origem.udid)
        c += checagens.checar_aparelhos(origem, destino, whatsapp, self.ultimas_versoes())
        necessario = checagens.tamanho_estimado_backup(origem, whatsapp)
        # Um backup anterior na mesma pasta é reaproveitado (incremental) e não conta como espaço a ocupar.
        ja_ocupado = _tamanho_pasta(pasta / origem.udid)
        pc = checagens.checar_pc(
            self.backend.servico_apple_ok(), pasta, disco.espaco_livre(pasta) + ja_ocupado,
            disco.sistema_arquivos(pasta), necessario,
        )
        if self.backend.simulado:
            # O backup simulado ocupa poucos MB; mostra a regra real sem travar o treino.
            for ch in pc:
                if ch.status == Status.BLOQUEIO and ch.titulo == "Espaço no PC":
                    ch.status = Status.AVISO
                    ch.detalhe = "[Simulação: numa migração real, isto impediria continuar.] " + ch.detalhe
        c += pc
        if not tem_bloqueio(c):
            e = self.estado
            e.origem, e.destino = origem.para_dict(), destino.para_dict()
            e.whatsapp = {"bundle": whatsapp.bundle, "tamanho": whatsapp.tamanho} if whatsapp else None
            e.pasta_backup = str(pasta)
            e.anotar(f"Conferência aprovada: {origem.rotulo()} → {destino.rotulo()}; backup em {pasta}")
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
        if self.estado.criptografia_ja_ativa:
            self.backend.validar_senha(udid, senha)
            self.estado.anotar("Senha de backup existente conferida")
        else:
            self.backend.ativar_criptografia(udid, senha)
            self.estado.criptografia_ja_ativa = True
            self.estado.anotar("Backup com senha ativado no iPhone antigo")
        self.senha = senha
        if guardar and self.estado.guardar_senha(senha):
            self.estado.anotar("Senha guardada protegida pelo Windows")
        self.estado.avancar_para("backup")
        self.salvar()

    # --- backup -----------------------------------------------------------------------

    def backup_anterior_completo(self) -> bool:
        return backup_completo_existe(self.estado.pasta_aparelho)

    def fazer_backup(self, progresso: Callable[[float], None], cancelar: threading.Event) -> None:
        e = self.estado
        if not e.origem or not e.pasta_backup:
            raise ErroAparelho("Faça a conferência dos aparelhos antes do backup.")
        presentes = {a.udid for a in self.backend.listar() if not a.erro}
        if e.origem["udid"] not in presentes:
            raise ErroAparelho("O iPhone antigo não está conectado.", "Conecte e desbloqueie o iPhone antigo.")
        e.anotar("Backup iniciado")
        e.relatorio = None
        self.salvar()
        self.backend.fazer_backup(e.origem["udid"], Path(e.pasta_backup), progresso, cancelar)
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
            rel = verificar_backup(pasta, self.senha or "", progresso)
        except SenhaIncorreta:
            raise ErroAparelho("A senha não abre este backup.", "Digite a senha que foi usada no backup.")
        if rel.criptografado is False and e.criptografia_ja_ativa and not e.simulacao:
            rel.avisos.append("O backup não saiu criptografado; senhas salvas e dados de Saúde não serão transferidos.")
        e.relatorio = rel.para_dict()
        e.anotar(f"Verificação: {'APROVADO' if rel.ok else 'REPROVADO'} — {rel.mensagens} mensagens, "
                 f"{rel.midias} mídias, {rel.arquivos} arquivos")
        if rel.ok:
            e.avancar_para("destino")
        self.salvar()
        return rel

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
        # Com os dois ligados, escolhe o que não é o antigo.
        destino = next((a for a in prontos if a.udid != e.origem["udid"]), prontos[0])
        c += checagens.checar_destino_para_restaurar(destino, e.origem["udid"], rel.ios_backup, rel.bytes_total,
                                                     self.ultimas_versoes())
        problemas = conferir_antes_de_restaurar(e.pasta_aparelho, rel)
        if problemas:
            c += [checagens.Checagem("Integridade do backup", checagens.Status.BLOQUEIO, p,
                                     "Volte à etapa de verificação.") for p in problemas]
        else:
            c.append(checagens.Checagem("Integridade do backup", checagens.Status.OK,
                                        "O backup está idêntico ao que foi verificado."))
        if not tem_bloqueio(c):
            e.destino = destino.para_dict()
            self.salvar()
        return destino, c

    def restaurar(self, progresso: Callable[[float], None]) -> None:
        e, rel = self.estado, self.relatorio
        if not rel or not rel.ok or not e.destino:
            raise ErroAparelho("Faça a verificação do backup e do iPhone novo antes de restaurar.")
        if e.destino["udid"] == e.origem["udid"]:
            raise ErroAparelho("O aparelho escolhido para restaurar é o iPhone antigo. Operação cancelada.")
        if rel.criptografado and not self.senha:
            raise ErroAparelho("Informe a senha do backup antes de restaurar.")
        problemas = conferir_antes_de_restaurar(e.pasta_aparelho, rel)
        if problemas:
            raise ErroAparelho(problemas[0], "Volte à etapa de verificação.")
        e.anotar(f"Restauração iniciada em {e.destino.get('nome') or e.destino['udid']}")
        e.avancar_para("restauracao")
        self.salvar()
        self.backend.restaurar(e.destino["udid"], Path(e.pasta_backup), e.origem["udid"],
                               self.senha or "", progresso)
        e.restaurado_em = datetime.now().isoformat(timespec="seconds")
        e.anotar("Restauração concluída; o iPhone novo vai reiniciar")
        e.avancar_para("concluido")
        self.salvar()


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
