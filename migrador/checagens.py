"""Regras que decidem se é seguro seguir. Um BLOQUEIO impede o botão Continuar."""
from pathlib import Path

from migrador.config import BATERIA_MINIMA, FOLGA_DESTINO_BYTES, FOLGA_PC_BYTES
from migrador.dispositivos.base import PRECISA_CONFIAR, PRECISA_DESBLOQUEAR
from migrador.modelos import WHATSAPP_BUNDLES, Aparelho, Checagem, InfoWhatsApp, Status, formatar_bytes
from migrador.versoes_ios import comparar

ATUALIZAR = "No iPhone: Ajustes > Geral > Atualização de Software > Baixar e Instalar."
DESLIGAR_BUSCAR = (
    "No iPhone novo: Ajustes > [seu nome] > Buscar > Buscar iPhone > desativar "
    "(pede a senha do Apple ID). Se você já apagou o iPhone novo e ele está na tela de "
    "configuração, isso não é necessário."
)


def tamanho_estimado_backup(origem: Aparelho, whatsapps: list[InfoWhatsApp]) -> int:
    """O backup nunca passa do espaço usado no iPhone; os WhatsApps somados são o piso (o backup
    é do iPhone inteiro, então todos vão, escolhidos ou não)."""
    soma = sum(w.tamanho or 0 for w in whatsapps)
    candidatos = [v for v in (origem.usado, soma) if v]
    return max(candidatos) if candidatos else 0


def checar_whatsapps(encontrados: list[InfoWhatsApp], escolhidos: list[str]) -> list[Checagem]:
    """Quais WhatsApps estão no iPhone antigo e quais a pessoa escolheu migrar e conferir."""
    if not encontrados:
        return [Checagem("WhatsApp no iPhone antigo", Status.BLOQUEIO,
                         "Nem o WhatsApp nem o WhatsApp Business foram encontrados no aparelho escolhido como antigo.",
                         "Confira se os aparelhos não estão trocados em \"iPhone antigo\" e \"iPhone novo\".")]
    c = []
    nomes = {w.bundle: w.nome for w in encontrados}
    for w in encontrados:
        tam = f" ({formatar_bytes(w.tamanho)})" if w.tamanho else ""
        if w.bundle in escolhidos:
            c.append(Checagem(f"{w.nome} no iPhone antigo", Status.OK,
                              f"Instalado{tam}. Será conferido no backup e no iPhone novo."))
        else:
            c.append(Checagem(f"{w.nome} no iPhone antigo", Status.INFO,
                              f"Instalado{tam}, mas não foi escolhido. O backup é do iPhone inteiro, então ele "
                              "também vai para o iPhone novo; só não é conferido. Se não quiser ele no novo, "
                              "apague o app no iPhone novo depois de conferir o escolhido."))
    for b in escolhidos:
        if b not in nomes:
            c.append(Checagem(f"{WHATSAPP_BUNDLES.get(b, b)} no iPhone antigo", Status.BLOQUEIO,
                              "Foi escolhido, mas não está instalado no iPhone antigo.",
                              "Escolha só os WhatsApps que aparecem como instalados."))
    if not escolhidos:
        c.append(Checagem("WhatsApp a migrar", Status.BLOQUEIO, "Nenhum WhatsApp foi escolhido.",
                          "Escolha o WhatsApp, o WhatsApp Business ou os dois."))
    return c


def checar_pc(servico_ok: bool | None, pasta: Path, livre: int, sistema_arquivos: str | None,
              necessario: int) -> list[Checagem]:
    """servico_ok=None: o serviço já foi conferido em checar_sistema()."""
    c: list[Checagem] = []
    if servico_ok is None:
        pass
    elif servico_ok:
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


def checar_aparelhos(origem: Aparelho, destino: Aparelho | None, whatsapps: list[InfoWhatsApp],
                     ultimas: dict[str, str] | None, escolhidos: list[str] | None = None) -> list[Checagem]:
    """`destino` None: conferência só com o iPhone antigo; o novo é conferido depois do backup."""
    c: list[Checagem] = []
    if destino is None:
        return _checar_so_antigo(origem, whatsapps, ultimas, escolhidos)
    if origem.udid == destino.udid:
        return [Checagem("Aparelhos", Status.BLOQUEIO, "O iPhone antigo e o novo são o mesmo aparelho.",
                         "Escolha aparelhos diferentes em \"iPhone antigo\" e \"iPhone novo\".")]

    escolhidos = [w.bundle for w in whatsapps] if escolhidos is None else escolhidos
    c += checar_whatsapps(whatsapps, escolhidos)

    c += _checar_versoes(origem, destino, ultimas)

    necessario = tamanho_estimado_backup(origem, whatsapps) + FOLGA_DESTINO_BYTES
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


def _checar_so_antigo(origem: Aparelho, whatsapps: list[InfoWhatsApp], ultimas: dict[str, str] | None,
                      escolhidos: list[str] | None) -> list[Checagem]:
    escolhidos = [w.bundle for w in whatsapps] if escolhidos is None else escolhidos
    c = checar_whatsapps(whatsapps, escolhidos)
    ultima_origem = (ultimas or {}).get(origem.tipo)
    if ultimas is None:
        c.append(Checagem("Atualizações do iOS", Status.AVISO,
                          "Não consegui consultar a Apple para saber a versão mais recente do iOS.",
                          f"Confira manualmente no iPhone antigo. {ATUALIZAR}"))
    elif ultima_origem and comparar(origem.ios, ultima_origem) < 0:
        c.append(Checagem("iOS do iPhone antigo", Status.AVISO,
                          f"Está no iOS {origem.ios}; existe o {ultima_origem}. Não é obrigatório atualizar o antigo.",
                          "Se quiser, atualize o antigo antes do backup."))
    elif ultima_origem:
        c.append(Checagem("iOS do iPhone antigo", Status.OK, f"iOS {origem.ios}, a versão mais recente."))
    necessario = tamanho_estimado_backup(origem, whatsapps) + FOLGA_DESTINO_BYTES
    c.append(Checagem("iPhone novo", Status.INFO,
                      "Será conectado e conferido depois do backup, sozinho no PC. Ele vai precisar do iOS "
                      f"{origem.ios} ou mais novo (de preferência o mais recente) e de pelo menos "
                      f"{formatar_bytes(necessario)} de capacidade.",
                      "Se quiser saber agora se ele serve, conecte os dois iPhones e clique em Procurar de novo."))
    if origem.bateria is not None and origem.bateria < BATERIA_MINIMA:
        c.append(Checagem("Bateria do iPhone antigo", Status.AVISO, f"Bateria em {origem.bateria}%.",
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


# --- sistema do PC -------------------------------------------------------------------------

LOJA_APPLE_DEVICES = "ms-windows-store://search/?query=Apple%20Devices"
INSTALAR_APPLE = (
    "Instale o app \"Apple Devices\" pela Microsoft Store (botão \"Abrir a Microsoft Store\"), abra-o uma "
    "vez e clique em \"Verificar de novo\". Se o PC não tiver a Microsoft Store, instale o iTunes pelo site "
    "da Apple."
)


def checar_sistema(s, simulado: bool = False, momento: str = "preparacao") -> list[Checagem]:
    """Verificações do PC. `momento`: preparacao (antes de conectar), backup ou restauracao.
    Na preparação, programas concorrentes e falta de confiança são só avisos; na hora da cópia, bloqueiam."""
    from migrador.sistema import ACOES_TAMPA
    c: list[Checagem] = []

    if s.build is not None and s.build < 19041:
        c.append(Checagem("Windows", Status.AVISO, f"{s.windows}: versão antiga do Windows 10.",
                          "Atualize o Windows (Configurações > Windows Update). O app Apple Devices pede o "
                          "Windows 10 atualizado ou o Windows 11; se não der, use o iTunes."))
    else:
        c.append(Checagem("Windows", Status.OK, s.windows or "Versão não identificada."))

    if s.apple_devices:
        c.append(Checagem("App Apple Devices", Status.OK, f"Instalado (versão {s.apple_devices})."))
    elif s.itunes:
        c.append(Checagem("iTunes", Status.OK, "Instalado. Ele traz o serviço que conversa com iPhones."))
    else:
        c.append(Checagem("App Apple Devices ou iTunes", Status.BLOQUEIO,
                          "Nenhum dos dois está instalado neste PC. Sem eles o Windows não reconhece iPhones.",
                          INSTALAR_APPLE))

    if s.usbmux_ok:
        c.append(Checagem("Serviço da Apple no PC", Status.OK, "Rodando e respondendo."))
    elif s.apple_devices or s.itunes:
        parado = " (o processo do serviço nem está aberto)" if s.servico_rodando is False else ""
        c.append(Checagem("Serviço da Apple no PC", Status.BLOQUEIO,
                          f"O app está instalado, mas o serviço que conversa com iPhones não respondeu{parado}.",
                          "Abra o app Apple Devices (ou o iTunes) uma vez e feche. Se continuar, reinicie o PC."))

    if s.confiaveis:
        n = len(s.confiaveis)
        texto = f"{n} iPhones já confiam neste PC." if n > 1 else "1 iPhone já confia neste PC."
        c.append(Checagem("Primeira conexão com este PC", Status.OK,
                          texto + " Os que ainda não confiam vão pedir \"Confiar\" ao conectar."))
    else:
        c.append(Checagem("Primeira conexão com este PC", Status.AVISO,
                          "Nenhum iPhone foi conectado e autorizado neste PC ainda.",
                          "Na próxima etapa, conecte cada iPhone pelo cabo, desbloqueie, toque em \"Confiar\" e "
                          "digite o código do iPhone. Isso só é pedido na primeira vez."))

    if s.tem_bateria is False:
        c.append(Checagem("Energia do PC", Status.OK, "PC de mesa (sem bateria). Mantenha-o ligado na tomada."))
    elif s.na_tomada is True:
        pct = f" Bateria em {s.bateria_pct}%." if s.bateria_pct is not None else ""
        c.append(Checagem("Energia do PC", Status.OK, f"Notebook ligado na tomada.{pct}"))
    elif s.na_tomada is False:
        pct = f" (bateria em {s.bateria_pct}%)" if s.bateria_pct is not None else ""
        c.append(Checagem("Energia do PC", Status.AVISO if simulado else Status.BLOQUEIO,
                          f"O notebook está funcionando na bateria{pct}. Backup e restauração levam horas; se a "
                          "bateria acabar, a cópia é interrompida.",
                          "Ligue o carregador do notebook na tomada e clique em \"Verificar de novo\"."))
    else:
        c.append(Checagem("Energia do PC", Status.AVISO, "Não consegui ler se o PC está na tomada.",
                          "Confira se o carregador está ligado."))

    if s.tem_bateria:
        acao = s.tampa_ca
        if acao is None:
            c.append(Checagem("Tampa do notebook", Status.AVISO, "Não consegui ler o que acontece ao fechar a tampa.",
                              "Não feche a tampa do notebook durante o processo."))
        elif acao != 0:
            c.append(Checagem("Tampa do notebook", Status.AVISO,
                              f"Fechar a tampa faz o notebook {ACOES_TAMPA.get(acao, 'parar')}, e isso interrompe "
                              "a cópia. O programa impede a suspensão por inatividade, mas não a da tampa.",
                              "Deixe a tampa aberta até o fim. Ou mude em Painel de Controle > Opções de Energia > "
                              "\"Escolher a função do fechamento da tampa\" > Na tomada: \"Nada a fazer\"."))
        else:
            c.append(Checagem("Tampa do notebook", Status.OK, "Fechar a tampa na tomada não suspende o notebook."))

    c.append(Checagem("Suspensão do Windows", Status.INFO,
                      "Durante backup, verificação e restauração o programa impede que o PC entre em suspensão "
                      "por inatividade. Desligar ou reiniciar o PC continua interrompendo a cópia."))

    if s.concorrentes:
        status = Status.AVISO if (simulado or momento == "preparacao") else Status.BLOQUEIO
        c.append(Checagem("Outros programas de iPhone abertos", status,
                          f"Aberto agora: {', '.join(s.concorrentes)}. Eles podem começar um backup ou uma "
                          "sincronização sozinhos ao ver o iPhone e disputar a conexão.",
                          "Feche a janela desses programas (o serviço da Apple continua funcionando em segundo "
                          "plano) e clique em \"Verificar de novo\"."))
    else:
        c.append(Checagem("Outros programas de iPhone", Status.OK,
                          "Nenhum aberto (iTunes, 3uTools, iMazing, CopyTrans…)."))
    return c


def checar_confianca(aparelhos: list[Aparelho]) -> list[Checagem]:
    """Por aparelho: a primeira conexão (o toque em "Confiar") já foi feita?"""
    return [Checagem(f"Confiança: {ap.rotulo()}", Status.OK, "Conectado pelo cabo e autorizado neste PC.")
            for ap in aparelhos if not ap.erro]


def checar_pasta(pasta: Path, sincronizada: str | None, gravavel: bool, removivel: bool) -> list[Checagem]:
    c = []
    if gravavel:
        c.append(Checagem("Permissão na pasta do backup", Status.OK, "O programa consegue gravar nela."))
    else:
        c.append(Checagem("Permissão na pasta do backup", Status.BLOQUEIO,
                          f"O programa não consegue gravar em {pasta}.",
                          "Escolha outra pasta, por exemplo na raiz de outro disco."))
    if sincronizada:
        c.append(Checagem("Pasta sincronizada com a nuvem", Status.BLOQUEIO,
                          f"A pasta está dentro do {sincronizada}. Ele tentaria enviar o backup inteiro para a "
                          "nuvem e pode deixar arquivos guardados só na nuvem, o que estraga a restauração.",
                          f"Escolha uma pasta fora do {sincronizada}, por exemplo C:\\MigradorWhatsApp-Backup."))
    if removivel:
        c.append(Checagem("Disco removível", Status.AVISO,
                          f"{pasta.anchor} é um disco removível (pendrive ou HD externo).",
                          "Não desconecte o disco até o fim da restauração e ligue-o direto no PC, sem hub USB."))
    return c
