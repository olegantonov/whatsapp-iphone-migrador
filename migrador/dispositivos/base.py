import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from migrador.modelos import Aparelho, InfoWhatsApp

# Recebe a porcentagem (0 a 100).
Progresso = Callable[[float], None]


class ErroAparelho(Exception):
    """Erro já traduzido para o usuário, com a orientação do que fazer."""

    def __init__(self, mensagem: str, como_resolver: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.como_resolver = como_resolver


class Cancelado(Exception):
    pass


# Valores de Aparelho.erro
PRECISA_CONFIAR = "confiar"
PRECISA_DESBLOQUEAR = "desbloquear"


class Backend(Protocol):
    simulado: bool

    def servico_apple_ok(self) -> bool: ...

    def listar(self) -> list[Aparelho]: ...

    def whatsapp(self, udid: str) -> InfoWhatsApp | None: ...

    def criptografia_ativa(self, udid: str) -> bool: ...

    def ativar_criptografia(self, udid: str, senha: str) -> None: ...

    def validar_senha(self, udid: str, senha: str) -> None: ...

    def fazer_backup(self, udid: str, pasta: Path, progresso: Progresso, cancelar: threading.Event) -> None: ...

    def restaurar(self, udid_destino: str, pasta: Path, udid_origem: str, senha: str, progresso: Progresso) -> None: ...
