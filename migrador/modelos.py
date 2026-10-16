from dataclasses import asdict, dataclass, field
from enum import Enum

WHATSAPP_BUNDLES = {
    "net.whatsapp.WhatsApp": "WhatsApp",
    "net.whatsapp.WhatsAppSMB": "WhatsApp Business",
}

NOMES_MODELOS = {
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X",
    "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max", "iPhone11,6": "iPhone XS Max",
    "iPhone11,8": "iPhone XR",
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2ª geração)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,6": "iPhone SE (3ª geração)",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
}


def nome_whatsapp(bundle: str) -> str:
    return WHATSAPP_BUNDLES.get(bundle, bundle)


def nome_modelo(tipo: str) -> str:
    return NOMES_MODELOS.get(tipo, f"iPhone ({tipo})" if tipo else "iPhone")


def formatar_bytes(n: float | int | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unidade == "TB":
            if unidade in ("B", "KB"):
                return f"{n:.0f} {unidade}"
            return f"{n:.1f} {unidade}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB"


def formatar_duracao(segundos: float | None) -> str:
    if segundos is None or segundos < 0:
        return "calculando…"
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


@dataclass
class Aparelho:
    udid: str
    nome: str = ""
    tipo: str = ""
    ios: str = ""
    capacidade: int | None = None
    livre: int | None = None
    bateria: int | None = None
    buscar_ativo: bool | None = None
    # Quando o aparelho aparece no USB mas não deu para conversar com ele.
    erro: str | None = None

    @property
    def modelo(self) -> str:
        return nome_modelo(self.tipo)

    @property
    def usado(self) -> int | None:
        if self.capacidade is None or self.livre is None:
            return None
        return max(self.capacidade - self.livre, 0)

    def rotulo(self) -> str:
        partes = [self.nome or self.modelo]
        if self.nome and self.nome != self.modelo:
            partes.append(f"({self.modelo})")
        if self.ios:
            partes.append(f"— iOS {self.ios}")
        return " ".join(partes)

    def para_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def de_dict(d: dict) -> "Aparelho":
        return Aparelho(**{k: d.get(k) for k in Aparelho.__dataclass_fields__})


@dataclass
class InfoWhatsApp:
    bundle: str
    tamanho: int | None = None

    @property
    def nome(self) -> str:
        return WHATSAPP_BUNDLES.get(self.bundle, self.bundle)


@dataclass
class ResultadoWhatsApp:
    """Conferência de um WhatsApp (comum ou Business) dentro do backup."""
    bundle: str
    nome: str = ""
    conversas: int = 0
    mensagens: int = 0
    midias: int = 0
    bytes_midia: int = 0
    bytes_whatsapp: int = 0
    banco_integro: bool = False
    anexos_referenciados: int = 0
    anexos_presentes: int = 0
    amostra_conferida: int = 0
    amostra_falhas: int = 0
    problemas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict:
        return asdict(self)


class Status(str, Enum):
    OK = "ok"
    AVISO = "aviso"
    BLOQUEIO = "bloqueio"
    INFO = "info"


@dataclass
class Checagem:
    titulo: str
    status: Status
    detalhe: str = ""
    como_resolver: str = ""


def tem_bloqueio(checagens: list[Checagem]) -> bool:
    return any(c.status == Status.BLOQUEIO for c in checagens)


@dataclass
class RelatorioBackup:
    ok: bool
    problemas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    criptografado: bool = False
    ios_backup: str = ""
    arquivos: int = 0
    bytes_total: int = 0
    arquivos_faltando: int = 0
    whatsapp_bundle: str = ""
    conversas: int = 0
    mensagens: int = 0
    midias: int = 0
    bytes_midia: int = 0
    banco_integro: bool = False
    impressao_digital: str = ""
    # Versão 2 em diante (1.1.0): inventário de todos os arquivos, anexos cruzados e amostra decifrada.
    versao_conferencia: int = 1
    inventario: str = ""
    inventario_arquivos: int = 0
    anexos_referenciados: int = 0
    anexos_presentes: int = 0
    amostra_conferida: int = 0
    amostra_falhas: int = 0
    bytes_whatsapp: int = 0
    # Versão 3 em diante (1.1.1): um resultado por WhatsApp conferido (comum e/ou Business).
    whatsapps: list[dict] = field(default_factory=list)
    whatsapps_no_backup: list[str] = field(default_factory=list)

    def para_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def de_dict(d: dict) -> "RelatorioBackup":
        return RelatorioBackup(**{k: d[k] for k in RelatorioBackup.__dataclass_fields__ if k in d})
