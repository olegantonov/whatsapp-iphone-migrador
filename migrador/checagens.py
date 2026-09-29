"""Regras que decidem se é seguro seguir. Um BLOQUEIO impede o botão Continuar."""
from pathlib import Path

from migrador.config import BATERIA_MINIMA, FOLGA_DESTINO_BYTES, FOLGA_PC_BYTES
from migrador.dispositivos.base import PRECISA_CONFIAR, PRECISA_DESBLOQUEAR
from migrador.modelos import Aparelho, Checagem, InfoWhatsApp, Status, formatar_bytes
from migrador.versoes_ios import comparar

ATUALIZAR = "No iPhone: Ajustes > Geral > Atualização de Software > Baixar e Instalar."
DESLIGAR_BUSCAR = (
    "No iPhone novo: Ajustes > [seu nome] > Buscar > Buscar iPhone > desativar "
    "(pede a senha do Apple ID). Se você já apagou o iPhone novo e ele está na tela de "
    "configuração, isso não é necessário."
)


def tamanho_estimado_backup(origem: Aparelho, whatsapp: InfoWhatsApp | None) -> int:
    """O backup nunca passa do espaço usado no iPhone; o WhatsApp sozinho é o piso."""
    candidatos = [v for v in (origem.usado, whatsapp.tamanho if whatsapp else None) if v]
    return max(candidatos) if candidatos else 0


def checar_pc(servico_ok: bool, pasta: Path, livre: int, sistema_arquivos: str | None,
              necessario: int) -> list[Checagem]:
    c: list[Checagem] = []
    if servico_ok:
        c.append(Checagem("Serviço da Apple no PC", Status.OK, "Encontrado."))
    else:
        c.append(Checagem("Serviço da Apple no PC", Status.BLOQUEIO,
                          "O Windows não tem o serviço que conversa com iPhones.",
                          "Instale o app \"Apple Devices\" pela Microsoft Store (ou o iTunes) e abra-o uma vez."))
    if sistema_arquivos and sistema_arquivos.upper().startswith("FAT"):
        c.append(Checagem("Formato do disco do backup", Status.BLOQUEIO,
                          f"O disco de {pasta.anchor} está em {sistema_arquivos}, que não aceita arquivos grandes.",
                          "Escolha uma pasta em um disco NTFS (o disco interno do PC é NTFS)."))
    total = necessario + FOLGA_PC_BYTES
    if livre >= total:
        c.append(Checagem("Espaço no PC", Status.OK,
                          f"{formatar_bytes(livre)} livres em {pasta.anchor}; o backup deve ocupar até "
                          f"{formatar_bytes(necessario)}."))
    else:
        c.append(Checagem("Espaço no PC", Status.BLOQUEIO,
                          f"Livre: {formatar_bytes(livre)}. Necessário: {formatar_bytes(total)} "
                          f"(backup de até {formatar_bytes(necessario)} + folga de segurança).",
                          "Escolha outra pasta, em um disco com mais espaço (pode ser um HD externo em NTFS)."))
    return c


def checar_conexao(aparelhos: list[Aparelho]) -> list[Checagem]:
    c = []
    for ap in aparelhos:
        if ap.erro == PRECISA_CONFIAR:
            c.append(Checagem("iPhone aguardando confirmação", Status.BLOQUEIO,
                              "Um iPhone está perguntando se deve confiar neste computador.",
                              "Desbloqueie o iPhone, toque em \"Confiar\", digite o código e clique em Procurar de novo."))
        elif ap.erro == PRECISA_DESBLOQUEAR:
            c.append(Checagem("iPhone bloqueado", Status.BLOQUEIO, "Um iPhone está com a tela bloqueada.",
                              "Desbloqueie o iPhone e clique em Procurar de novo."))
        elif ap.erro:
            c.append(Checagem("iPhone com problema de conexão", Status.BLOQUEIO, ap.erro,
                              "Desconecte e conecte o cabo de novo e clique em Procurar."))
    return c


def checar_aparelhos(origem: Aparelho, destino: Aparelho, whatsapp: InfoWhatsApp | None,
                     ultimas: dict[str, str] | None) -> list[Checagem]:
    c: list[Checagem] = []
    if origem.udid == destino.udid:
        return [Checagem("Aparelhos", Status.BLOQUEIO, "O iPhone antigo e o novo são o mesmo aparelho.",
                         "Escolha aparelhos diferentes em \"iPhone antigo\" e \"iPhone novo\".")]

    if whatsapp:
        tam = f" ({formatar_bytes(whatsapp.tamanho)})" if whatsapp.tamanho else ""
        c.append(Checagem(f"{whatsapp.nome} no iPhone antigo", Status.OK, f"Instalado{tam}."))
    else:
        c.append(Checagem("WhatsApp no iPhone antigo", Status.BLOQUEIO,
                          "O WhatsApp não foi encontrado no aparelho escolhido como antigo.",
                          "Confira se os aparelhos não estão trocados em \"iPhone antigo\" e \"iPhone novo\"."))

    c += _checar_versoes(origem, destino, ultimas)

    necessario = tamanho_estimado_backup(origem, whatsapp) + FOLGA_DESTINO_BYTES
    if destino.capacidade is None:
        c.append(Checagem("Espaço no iPhone novo", Status.AVISO, "Não consegui ler a capacidade do iPhone novo.",
                          "Confira em Ajustes > Geral > Armazenamento que ele tem mais espaço que o antigo usa."))
    elif destino.capacidade >= necessario:
        c.append(Checagem("Espaço no iPhone novo", Status.OK,
                          f"Capacidade de {formatar_bytes(destino.capacidade)}; o iPhone antigo usa "
                          f"{formatar_bytes(origem.usado)}."))
    else:
        c.append(Checagem("Espaço no iPhone novo", Status.BLOQUEIO,
                          f"O iPhone novo tem {formatar_bytes(destino.capacidade)} e o conteúdo do antigo "
                          f"precisa de cerca de {formatar_bytes(necessario)}.",
                          "Libere espaço no iPhone antigo (fora do WhatsApp) ou use um iPhone novo com mais capacidade."))

    c += _checar_buscar(destino)
    for ap, papel in ((origem, "antigo"), (destino, "novo")):
        if ap.bateria is not None and ap.bateria < BATERIA_MINIMA:
            c.append(Checagem(f"Bateria do iPhone {papel}", Status.AVISO, f"Bateria em {ap.bateria}%.",
                              "Deixe o iPhone na tomada durante todo o processo."))
    return c


def _checar_versoes(origem: Aparelho, destino: Aparelho, ultimas: dict[str, str] | None) -> list[Checagem]:
    c = []
    if comparar(destino.ios, origem.ios) < 0:
        c.append(Checagem("Versão do iOS", Status.BLOQUEIO,
                          f"O iPhone novo está no iOS {destino.ios} e o antigo no iOS {origem.ios}. "
                          "Um backup não pode ser restaurado num iOS mais antigo.",
                          f"Atualize o iPhone novo. {ATUALIZAR}"))
    ultima_destino = (ultimas or {}).get(destino.tipo)
    ultima_origem = (ultimas or {}).get(origem.tipo)
    if ultimas is None:
        c.append(Checagem("Atualizações do iOS", Status.AVISO,
                          "Não consegui consultar a Apple para saber a versão mais recente do iOS.",
                          f"Confira manualmente nos dois iPhones. {ATUALIZAR}"))
    else:
        if ultima_destino and comparar(destino.ios, ultima_destino) < 0:
            c.append(Checagem("iOS do iPhone novo", Status.BLOQUEIO,
                              f"Está no iOS {destino.ios}; a versão mais recente para ele é a {ultima_destino}.",
                              f"Atualize o iPhone novo antes de continuar. {ATUALIZAR}"))
        elif ultima_destino:
            c.append(Checagem("iOS do iPhone novo", Status.OK, f"iOS {destino.ios}, a versão mais recente."))
        if ultima_origem and comparar(origem.ios, ultima_origem) < 0:
            c.append(Checagem("iOS do iPhone antigo", Status.AVISO,
                              f"Está no iOS {origem.ios}; existe o {ultima_origem}. Não é obrigatório atualizar "
                              "o antigo, porque o novo já está numa versão igual ou mais nova.",
                              "Se quiser, atualize o antigo também antes do backup."))
        elif ultima_origem:
            c.append(Checagem("iOS do iPhone antigo", Status.OK, f"iOS {origem.ios}, a versão mais recente."))
    if not any(x.status == Status.BLOQUEIO for x in c) and comparar(destino.ios, origem.ios) >= 0:
        c.append(Checagem("Compatibilidade do iOS", Status.OK,
                          f"iOS {origem.ios} → iOS {destino.ios}: compatível."))
    return c


def _checar_buscar(destino: Aparelho) -> list[Checagem]:
    if destino.buscar_ativo:
        return [Checagem("\"Buscar iPhone\" no iPhone novo", Status.BLOQUEIO,
                         "Está ligado, e com ele ligado o iPhone recusa a restauração.", DESLIGAR_BUSCAR)]
    if destino.buscar_ativo is None:
        return [Checagem("\"Buscar iPhone\" no iPhone novo", Status.AVISO,
                         "Não consegui confirmar se está desligado.", DESLIGAR_BUSCAR)]
    return [Checagem("\"Buscar iPhone\" no iPhone novo", Status.OK, "Desligado.")]


def checar_destino_para_restaurar(destino: Aparelho, udid_origem: str, ios_backup: str, bytes_backup: int,
                                  ultimas: dict[str, str] | None) -> list[Checagem]:
    if destino.udid == udid_origem:
        return [Checagem("Aparelho conectado", Status.BLOQUEIO,
                         "Este é o iPhone ANTIGO. A restauração só pode ser feita no iPhone novo.",
                         "Desconecte o iPhone antigo e conecte o novo.")]
    c = [Checagem("Aparelho conectado", Status.OK, destino.rotulo())]
    if comparar(destino.ios, ios_backup) < 0:
        c.append(Checagem("Versão do iOS", Status.BLOQUEIO,
                          f"O backup é do iOS {ios_backup} e o iPhone novo está no iOS {destino.ios}.",
                          f"Atualize o iPhone novo. {ATUALIZAR}"))
    else:
        ultima = (ultimas or {}).get(destino.tipo)
        if ultima and comparar(destino.ios, ultima) < 0:
            c.append(Checagem("Versão do iOS", Status.BLOQUEIO,
                              f"O iPhone novo está no iOS {destino.ios}; a versão mais recente é a {ultima}.",
                              f"Atualize antes de restaurar. {ATUALIZAR}"))
        else:
            c.append(Checagem("Versão do iOS", Status.OK, f"iOS {destino.ios} (backup do iOS {ios_backup})."))
    necessario = bytes_backup + FOLGA_DESTINO_BYTES
    if destino.capacidade is not None and destino.capacidade < necessario:
        c.append(Checagem("Espaço no iPhone novo", Status.BLOQUEIO,
                          f"O backup tem {formatar_bytes(bytes_backup)} e o iPhone novo comporta "
                          f"{formatar_bytes(destino.capacidade)}.", "Use um iPhone com mais capacidade."))
    else:
        c.append(Checagem("Espaço no iPhone novo", Status.OK,
                          f"Backup de {formatar_bytes(bytes_backup)}; capacidade {formatar_bytes(destino.capacidade)}."))
    c += _checar_buscar(destino)
    if destino.bateria is not None and destino.bateria < BATERIA_MINIMA:
        c.append(Checagem("Bateria do iPhone novo", Status.AVISO, f"Bateria em {destino.bateria}%.",
                          "Deixe o iPhone na tomada durante a restauração."))
    return c
