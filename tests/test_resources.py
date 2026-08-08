"""Localização de assets, dentro e fora do executável (Sprint 8.1).

O modo de falha que estes testes protegem: caminhos que funcionam rodando do
repositório param de funcionar congelados, e o sintoma aparece só quando alguém
abre o `.exe` numa máquina limpa.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chess_pdf_editor import resources
from chess_pdf_editor.local_ocr import BUNDLED_MODEL_NAME, bundled_model_path


@pytest.fixture
def frozen(monkeypatch, tmp_path: Path):
    """Simula o app congelado, com o bundle extraído em `tmp_path/bundle`."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    fake_exe = tmp_path / "app" / "ChessPdfEditor.exe"
    fake_exe.parent.mkdir()
    fake_exe.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    return bundle


def test_outside_a_bundle_there_is_no_bundle_root() -> None:
    assert resources.is_frozen() is False
    assert resources.bundle_root() is None


def test_the_repo_root_holds_the_model_and_the_sources() -> None:
    root = resources.repo_root()
    assert (root / "src" / "chess_pdf_editor" / "app.py").is_file()
    assert (root / "models" / BUNDLED_MODEL_NAME).is_file()


def test_the_bundle_comes_first_when_frozen(frozen: Path) -> None:
    """Dentro do executável o conteúdo empacotado tem de vencer o disco."""
    assert resources.is_frozen() is True
    assert resources.bundle_root() == frozen
    assert list(resources.asset_roots())[0] == frozen.resolve()


def test_the_executable_folder_is_searched_when_frozen(frozen: Path) -> None:
    """É como o usuário de um build acrescenta uma fonte sem reempacotar."""
    roots = list(resources.asset_roots())
    assert resources.executable_dir() in roots
    assert roots.index(resources.executable_dir()) < roots.index(Path.cwd().resolve())


def test_the_executable_folder_is_not_searched_from_source() -> None:
    """Fora do bundle, `sys.executable` é o python — a pasta dele não é nossa."""
    assert resources.executable_dir() not in list(resources.asset_roots())


def test_roots_have_no_duplicates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(resources.repo_root())
    roots = list(resources.asset_roots())
    assert len(roots) == len(set(roots))


def test_find_asset_returns_none_when_nothing_matches() -> None:
    assert resources.find_asset("nao", "existe", "mesmo.bin") is None


def test_find_asset_locates_the_model() -> None:
    found = resources.find_asset("models", BUNDLED_MODEL_NAME)
    assert found is not None and found.is_file()


def test_the_model_is_found_inside_the_bundle(frozen: Path) -> None:
    """O caso que o empacotamento quebra: modelo dentro do bundle, não no repo."""
    (frozen / "models").mkdir()
    packaged = frozen / "models" / BUNDLED_MODEL_NAME
    packaged.write_bytes(b"pesos falsos")

    assert bundled_model_path() == packaged


def test_a_bundle_without_the_model_still_falls_back_to_disk(frozen: Path) -> None:
    """Bundle incompleto não é fatal se o modelo estiver ao alcance em disco."""
    assert bundled_model_path() == resources.repo_root() / "models" / BUNDLED_MODEL_NAME


def test_a_missing_model_still_yields_a_path_to_show(frozen: Path, monkeypatch) -> None:
    """A mensagem de erro precisa dizer *onde* o modelo era esperado.

    Aqui o bundle é a única raiz, para o teste medir o caminho do "não achei em
    lugar nenhum" — com o repositório na lista, o modelo do código-fonte apareceria.
    """
    monkeypatch.setattr(resources, "asset_roots", lambda: iter([frozen]))

    path = bundled_model_path()
    assert path == frozen / "models" / BUNDLED_MODEL_NAME
    assert not path.exists()


def test_asset_candidates_covers_every_root(frozen: Path) -> None:
    candidates = resources.asset_candidates("assets", "fonts")
    assert len(candidates) == len(list(resources.asset_roots()))
    assert all(candidate.parts[-2:] == ("assets", "fonts") for candidate in candidates)
