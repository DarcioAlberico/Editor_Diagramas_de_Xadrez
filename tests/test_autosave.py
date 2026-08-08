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

    def _boom(path, state):
        Path(path).write_text('{"truncado', encoding="utf-8")
        raise OSError("disco cheio")

    monkeypatch.setattr(autosave_module, "save_project_state", _boom)
    with pytest.raises(OSError):
        write_project_atomically(str(target), _state("outro.pdf"))

    assert target.read_bytes() == good_bytes
    assert json.loads(target.read_text(encoding="utf-8"))["source_pdf"] == "livro.pdf"
