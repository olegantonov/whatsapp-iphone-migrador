import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["MIGRADOR_SIM_RAPIDO"] = "1"


@pytest.fixture(autouse=True)
def dados_isolados(tmp_path, monkeypatch):
    monkeypatch.setenv("MIGRADOR_DADOS", str(tmp_path / "dados"))
    yield
