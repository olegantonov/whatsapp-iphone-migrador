import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrador de WhatsApp para iPhone")
    parser.add_argument("--simulacao", action="store_true", help="abre já no modo simulação")
    parser.add_argument("--autoteste", action="store_true", help="roda a migração simulada sem interface")
    parser.add_argument("--saida", type=Path, help="arquivo de resultado do autoteste")
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--sistema", action="store_true", help="mostra a verificação do PC e sai")
    parser.add_argument("--atualizar", action="store_true",
                        help="baixa e instala a última versão sem abrir a interface")
    args = parser.parse_args()

    if args.versao:
        from migrador.versao import __version__
        print(__version__)
        return 0
    if args.sistema:
        return _verificar_sistema(args.saida)
    if args.atualizar:
        return _atualizar_sem_interface(args.saida)
    if args.autoteste:
        from migrador.autoteste import rodar
        return 0 if rodar(args.saida) else 1

    from migrador import registro
    arquivo = registro.iniciar()
    import logging

    from migrador.versao import __version__
    logging.getLogger(__name__).info("iniciando versão %s; log em %s", __version__, arquivo)
    from migrador import diagnostico, telemetria
    diagnostico.registrar_ambiente()
    telemetria.registrar("programa_aberto", simulacao=args.simulacao)

    from migrador.interface import App
    App(simulacao_inicial=args.simulacao).mainloop()
    return 0


def _verificar_sistema(saida: Path | None) -> int:
    from migrador import checagens, sistema
    from migrador.dispositivos.real import BackendReal
    s = sistema.ler(BackendReal().servico_apple_ok())
    linhas = [f"{ch.status.value.upper():9} {ch.titulo}: {ch.detalhe}" for ch in checagens.checar_sistema(s)]
    texto = "\n".join(linhas)
    if saida:
        saida.write_text(texto, encoding="utf-8")
    else:
        print(texto)
    return 0


def _atualizar_sem_interface(saida: Path | None) -> int:
    from migrador import atualizador, registro
    registro.iniciar()
    lanc = atualizador.buscar()
    texto = f"instalada: {atualizador.__version__}; disponível: {lanc.versao if lanc else 'nenhuma mais nova'}"
    if saida:
        saida.write_text(texto, encoding="utf-8")
    if not lanc:
        return 0
    if not atualizador.pode_atualizar_sozinho():
        return 2
    atualizador.aplicar_e_reiniciar(atualizador.baixar(lanc), reabrir=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
