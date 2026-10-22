from migrador import motor


def test_soma_os_arquivos_de_todas_as_subpastas(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "um").write_bytes(b"x" * 100)
    (tmp_path / "a" / "b" / "dois").write_bytes(b"x" * 50)
    assert motor._tamanho_pasta(tmp_path) == 150
    assert motor._tamanho_pasta(tmp_path / "nao-existe") == 0


def test_desiste_quando_passa_do_tempo(tmp_path):
    # Pasta enorme depois de uma falha: melhor não saber o tamanho do que segurar a tela.
    for i in range(3):
        (tmp_path / str(i)).mkdir()
        (tmp_path / str(i) / "f").write_bytes(b"x")
    assert motor._tamanho_pasta(tmp_path, limite_s=-1) is None
    assert motor._tamanho_pasta(tmp_path, limite_s=60) == 3
