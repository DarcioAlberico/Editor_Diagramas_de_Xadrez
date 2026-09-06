from __future__ import annotations

import json
from pathlib import Path

import pytest

from chess_pdf_editor.autosave import (
    autosave_path_for_pdf,
    is_autosave_path,
    write_project_atomically,
)
from chess_pdf_editor.project_state import ProjectState, load_project_state
from chess_pdf_editor.types import OverlayOperation

FEN = "8/8/8/4k3/8/8/4K3/8"


def _state(pdf_path: str) -> ProjectState:
    return ProjectState(
        source_pdf=pdf_path,
        source_pdf_fingerprint={"sha256": "abc"},
        operations=[OverlayOperation(page_num=0, rect_pdf=(1.0, 2.0, 3.0, 4.0), fen=FEN)],
    )


def test_path_is_stable_for_the_same_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "livro.pdf"
    first = autosave_path_for_pdf(str(pdf), base_dir=tmp_path)
    second = autosave_path_for_pdf(str(pdf), base_dir=tmp_path)
    assert first == second
    assert is_autosave_path(str(first))


def test_same_name_in_different_folders_does_not_collide(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = autosave_path_for_pdf(str(tmp_path / "a" / "livro.pdf"), base_dir=tmp_path)
    second = autosave_path_for_pdf(str(tmp_path / "b" / "livro.pdf"), base_dir=tmp_path)
    assert first != second


def test_name_survives_characters_the_filesystem_rejects(tmp_path: Path) -> None:
    path = autosave_path_for_pdf(str(tmp_path / "a b: c?.pdf"), base_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")  # precisa ser gravavel de fato
    assert path.exists()


def test_write_is_atomic_and_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "projeto.json"
    write_project_atomically(str(target), _state("livro.pdf"))

    assert target.exists()
    assert not (target.parent / (target.name + ".tmp")).exists(), "temporario ficou para tras"
    loaded = load_project_state(str(target))
    assert loaded.operations[0].fen == FEN


def test_failed_write_does_not_destroy_the_previous_file(tmp_path: Path, monkeypatch) -> None:
    """O ponto do os.replace: um autosave que falha nao deixa JSON truncado no lugar do bom."""
    target = tmp_path / "projeto.json"
    write_project_atomically(str(target), _state("livro.pdf"))
    good_bytes = target.read_bytes()

    import chess_pdf_editor.autosave as autosave_module

    def _boom(path, state, extra=None):
        Path(path).write_text('{"truncado', encoding="utf-8")
        raise OSError("disco cheio")

    monkeypatch.setattr(autosave_module, "save_project_state", _boom)
    with pytest.raises(OSError):
        write_project_atomically(str(target), _state("outro.pdf"))

    assert target.read_bytes() == good_bytes
    assert json.loads(target.read_text(encoding="utf-8"))["source_pdf"] == "livro.pdf"


# ---------------------------------------------------------------------------
# Gravação durável (§43)
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_no_temporary_behind(tmp_path: Path, monkeypatch) -> None:
    """Antes, cada falha deixava um `.json.tmp` truncado ao lado do projeto."""
    from chess_pdf_editor import autosave as autosave_module

    target = tmp_path / "projeto.json"
    write_project_atomically(str(target), _state("livro.pdf"))
    good = target.read_bytes()

    def explode(path, state, extra=None):
        # Escreve pela metade e falha, como um disco que enche no meio.
        Path(path).write_text('{"parcial": ', encoding="utf-8")
        raise OSError("disco cheio")

    monkeypatch.setattr(autosave_module, "save_project_state", explode)
    with pytest.raises(OSError):
        write_project_atomically(str(target), _state("livro.pdf"))

    assert target.read_bytes() == good, "a falha estragou o projeto bom"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "projeto.json"]
    assert leftovers == [], f"sobrou lixo: {leftovers}"


def test_an_interrupt_also_cleans_up(tmp_path: Path, monkeypatch) -> None:
    """`KeyboardInterrupt` não é `Exception`, e deixaria o mesmo lixo."""
    from chess_pdf_editor import autosave as autosave_module

    target = tmp_path / "projeto.json"

    def interrupt(path, state, extra=None):
        Path(path).write_text("{", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(autosave_module, "save_project_state", interrupt)
    with pytest.raises(KeyboardInterrupt):
        write_project_atomically(str(target), _state("livro.pdf"))

    assert list(tmp_path.iterdir()) == []


def test_the_bytes_are_pushed_to_disk_before_the_rename(tmp_path: Path, monkeypatch) -> None:
    """`os.replace` ordena a troca de nome, não a gravação do conteúdo.

    Sem `fsync`, uma queda de energia pode deixar o nome novo apontando para blocos
    que nunca foram escritos — e é justamente queda de energia que o cabeçalho do
    módulo promete cobrir.
    """
    from chess_pdf_editor import autosave as autosave_module

    synced: list[int] = []
    real_fsync = autosave_module.os.fsync
    real_replace = autosave_module.os.replace
    order: list[str] = []

    def spy_fsync(fd):
        synced.append(fd)
        order.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst):
        order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(autosave_module.os, "fsync", spy_fsync)
    monkeypatch.setattr(autosave_module.os, "replace", spy_replace)

    write_project_atomically(str(tmp_path / "projeto.json"), _state("livro.pdf"))

    assert synced, "nada foi sincronizado com o disco"
    assert order.index("fsync") < order.index("replace"), f"ordem errada: {order}"


def test_the_written_project_reloads(tmp_path: Path) -> None:
    """A durabilidade não vale nada se o que ficou no disco não abrir."""
    target = tmp_path / "projeto.json"
    write_project_atomically(str(target), _state("livro.pdf"))

    reloaded = load_project_state(str(target))

    assert len(reloaded.operations) == 1
    assert reloaded.operations[0].fen == FEN
    assert json.loads(target.read_text(encoding="utf-8"))["source_pdf"] == "livro.pdf"


def test_writing_twice_in_a_row_keeps_the_folder_clean(tmp_path: Path) -> None:
    target = tmp_path / "projeto.json"
    write_project_atomically(str(target), _state("a.pdf"))
    write_project_atomically(str(target), _state("b.pdf"))

    assert [p.name for p in tmp_path.iterdir()] == ["projeto.json"]
    assert load_project_state(str(target)).source_pdf == "b.pdf"
