from migrador import atualizador, versoes_ios
from migrador.config import NOME_PACOTE_RELEASE


def _release(tag="v1.2.0", **kw):
    dados = {
        "tag_name": tag, "draft": False, "prerelease": False, "body": "novidades",
        "assets": [
            {"name": NOME_PACOTE_RELEASE, "browser_download_url": "https://x/zip"},
            {"name": NOME_PACOTE_RELEASE + ".sha256", "browser_download_url": "https://x/sha"},
        ],
    }
    dados.update(kw)
    return dados


def test_interpreta_release():
    lanc = atualizador.interpretar(_release())
    assert lanc.versao == "1.2.0"
    assert lanc.url_sha256 == "https://x/sha"


def test_ignora_rascunho_e_prerelease():
    assert atualizador.interpretar(_release(draft=True)) is None
    assert atualizador.interpretar(_release(prerelease=True)) is None


def test_ignora_release_sem_pacote():
    assert atualizador.interpretar(_release(assets=[])) is None


def test_comparacao_de_versoes():
    assert atualizador.mais_nova("1.10.0", "1.9.3")
    assert not atualizador.mais_nova("1.0.0", "1.0.0")
    assert not atualizador.mais_nova("0.9", "1.0.0")
    assert not atualizador.mais_nova("lixo", "1.0.0")


def test_ultimas_versoes_ios():
    dados = {"PublicAssetSets": {"iOS": [
        {"ProductVersion": "26.6", "SupportedDevices": ["iPhone13,2", "iPad8,1"]},
        {"ProductVersion": "27.0.1", "SupportedDevices": ["iPhone13,2", "iPhone17,1"]},
        {"ProductVersion": "16.7.12", "SupportedDevices": ["iPhone10,1"]},
    ]}}
    r = versoes_ios.interpretar(dados)
    assert r == {"iPhone13,2": "27.0.1", "iPhone17,1": "27.0.1", "iPhone10,1": "16.7.12"}
    assert versoes_ios.comparar("26.10", "26.9") == 1
