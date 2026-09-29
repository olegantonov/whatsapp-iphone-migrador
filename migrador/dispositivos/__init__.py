from migrador.dispositivos.base import Backend


def criar(simulacao: bool, cenario: str = "normal") -> Backend:
    if simulacao:
        from migrador.dispositivos.simulado import BackendSimulado

        return BackendSimulado(cenario)
    from migrador.dispositivos.real import BackendReal

    return BackendReal()
