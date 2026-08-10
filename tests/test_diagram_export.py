"""Exportação dos diagramas isolados (§39).

O PDF de saída serve para ler o livro corrigido; não serve para reaproveitar um
diagrama num slide. Aqui cada substituição vira um arquivo.

Dois pontos merecem teste próprio e são o oposto um do outro: o cancelamento
**mantém** o que já gravou (ao contrário do PDF, §33, onde meio arquivo é pior que
nenhum), e a falha de um diagrama **não** aborta os outros.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, SECOND_DIAGRAM_RECT

from chess_pdf_editor.diagram_export import (
    DEFAULT_FORMAT,
    FORMATS,
    INDEX_NAME,
    DiagramExportResult,
    diagram_filename,
    export_diagrams,
    normalize_format,
)
from chess_pdf_editor.types import OverlayOperation

FEN = "8/8/8/4k3/8/8/4K3/8"
OTHER_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


def _op(page: int = 0, fen: str = FEN, rect=DIAGRAM_RECT) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=rect, fen=fen, source="ocr-selecao")


# ---------------------------------------------------------------------------
# Formatos e nomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_each_format_writes_a_real_file(tmp_path, fmt: str) -> None:
    result = export_diagrams([_op()], tmp_path, fmt=fmt, size_px=128)

    assert result.total_written == 1
    assert result.failed == []
    written = result.written[0]
    assert written.suffix == f".{fmt}"
    assert written.stat().st_size > 0


def test_the_png_is_a_png_and_the_pdf_is_a_pdf(tmp_path) -> None:
    """Extensão certa com conteúdo de outro formato seria pior que falhar."""
    png = export_diagrams([_op()], tmp_path / "png", fmt="png", size_px=128).written[0]
    pdf = export_diagrams([_op()], tmp_path / "pdf", fmt="pdf", size_px=128).written[0]
    svg = export_diagrams([_op()], tmp_path / "svg", fmt="svg", size_px=128).written[0]

    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert pdf.read_bytes()[:5] == b"%PDF-"
    assert "<svg" in svg.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("given", "expected"),
    [("PNG", "png"), (".svg", "svg"), ("pdf", "pdf"), ("", DEFAULT_FORMAT), ("jpeg", DEFAULT_FORMAT), (None, DEFAULT_FORMAT)],
)
def test_the_format_is_normalized(given, expected: str) -> None:
    assert normalize_format(given) == expected


def test_the_filename_sorts_by_page_in_a_file_manager() -> None:
    """`pag10` antes de `pag2` é exatamente o que não se quer."""
    names = [diagram_filename(_op(page=page), 1, "png") for page in (1, 9, 11, 99)]
    assert names == sorted(names)
    assert names[0] == "diagrama-pag0002-01.png"


def test_two_diagrams_on_the_same_page_get_different_names(tmp_path) -> None:
    ops = [_op(rect=SECOND_DIAGRAM_RECT), _op(rect=DIAGRAM_RECT)]
    result = export_diagrams(ops, tmp_path, size_px=128)

    assert result.total_written == 2
    assert len({path.name for path in result.written}) == 2


def test_the_numbering_follows_the_reading_order_of_the_page(tmp_path) -> None:
    """Mesma ordem da galeria: o `-01` é o de cima, qualquer que seja a ordem da lista."""
    top, bottom = SECOND_DIAGRAM_RECT, DIAGRAM_RECT
    assert top[1] < bottom[1], "a fixture pressupõe que SECOND fica acima"

    # Lista fora de ordem de propósito, e FENs distintas para saber quem é quem.
    export_diagrams(
        [_op(rect=bottom, fen=OTHER_FEN), _op(rect=top, fen=FEN)], tmp_path, size_px=128
    )

    by_name = {row["arquivo"]: row["fen"] for row in _read_index(tmp_path)}
    assert by_name["diagrama-pag0001-01.png"] == FEN, "o -01 não é o diagrama de cima"
    assert by_name["diagrama-pag0001-02.png"] == OTHER_FEN


# ---------------------------------------------------------------------------
# O índice
# ---------------------------------------------------------------------------


def _read_index(directory: Path) -> list[dict[str, str]]:
    with (directory / INDEX_NAME).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_index_says_what_is_in_each_file(tmp_path) -> None:
    """Uma pasta com 300 PNGs sem índice obriga a abrir um por um."""
    result = export_diagrams(
        [_op(page=0, fen=FEN), _op(page=4, fen=OTHER_FEN)], tmp_path, size_px=128
    )

    assert result.index_path is not None
    rows = _read_index(tmp_path)
    assert [row["pagina"] for row in rows] == ["1", "5"]
    assert [row["fen"] for row in rows] == [FEN, OTHER_FEN]
    assert {row["arquivo"] for row in rows} == {path.name for path in result.written}


def test_the_index_can_be_turned_off(tmp_path) -> None:
    result = export_diagrams([_op()], tmp_path, size_px=128, write_index=False)

    assert result.index_path is None
    assert not (tmp_path / INDEX_NAME).exists()


def test_nothing_to_export_writes_no_index(tmp_path) -> None:
    result = export_diagrams([], tmp_path, size_px=128)

    assert result == DiagramExportResult()
    assert not (tmp_path / INDEX_NAME).exists()


def test_the_folder_is_created_if_missing(tmp_path) -> None:
    target = tmp_path / "nova" / "pasta"
    export_diagrams([_op()], target, size_px=128)

    assert target.is_dir()


# ---------------------------------------------------------------------------
# Cancelar e falhar
# ---------------------------------------------------------------------------


def test_cancelling_keeps_what_was_already_written(tmp_path) -> None:
    """O oposto do PDF (§33): aqui são N arquivos independentes e os prontos servem."""
    ops = [_op(page=page) for page in range(5)]
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        # Deixa dois passarem e para no terceiro.
        return calls["n"] > 2

    result = export_diagrams(ops, tmp_path, size_px=128, should_cancel=should_cancel)

    assert result.canceled is True
    assert result.total_written == 2
    assert result.skipped == 3
    assert len(list(tmp_path.glob("*.png"))) == 2, "os gravados foram apagados"
    # O índice cobre só o que existe de fato.
    assert len(_read_index(tmp_path)) == 2


def test_cancelling_before_the_first_one_writes_nothing(tmp_path) -> None:
    result = export_diagrams([_op()], tmp_path, size_px=128, should_cancel=lambda: True)

    assert result.canceled is True
    assert result.total_written == 0
    assert result.skipped == 1


def test_one_broken_diagram_does_not_lose_the_others(tmp_path, monkeypatch) -> None:
    """Um livro perdido por causa de uma FEN estragada seria pior que um aviso."""
    from chess_pdf_editor import diagram_export

    real = diagram_export.render_board_png

    def explode(piece_placement: str, size_px: int = 512):
        if piece_placement == OTHER_FEN:
            raise RuntimeError("render explodiu de propósito")
        return real(piece_placement, size_px=size_px)

    monkeypatch.setattr(diagram_export, "render_board_png", explode)

    ops = [_op(page=0), _op(page=1, fen=OTHER_FEN), _op(page=2)]
    result = export_diagrams(ops, tmp_path, size_px=128)

    assert result.total_written == 2
    assert len(result.failed) == 1
    assert "explodiu" in result.failed[0][1]
    assert result.canceled is False
    # O que falhou não entra no índice, senão ele apontaria para arquivo inexistente.
    assert len(_read_index(tmp_path)) == 2


def test_progress_counts_every_diagram(tmp_path) -> None:
    seen: list[tuple[int, int]] = []
    ops = [_op(page=page) for page in range(3)]

    export_diagrams(ops, tmp_path, size_px=128, on_progress=lambda d, t: seen.append((d, t)))

    assert seen == [(1, 3), (2, 3), (3, 3)]


# ---------------------------------------------------------------------------
# Na janela
# ---------------------------------------------------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_the_command_needs_a_substitution(main_window) -> None:
    assert main_window.act_export_diagrams.isEnabled() is False

    main_window.operations = [_op()]
    main_window._update_edit_context_state()

    assert main_window.act_export_diagrams.isEnabled() is True


def test_asking_without_substitutions_explains_instead_of_opening(main_window, no_modals) -> None:
    main_window._export_diagrams_dialog()

    assert no_modals, "devia explicar que não há o que exportar"
    assert main_window._diagram_export_worker is None


def test_the_chosen_format_and_size_are_remembered(main_window, monkeypatch) -> None:
    main_window.operations = [_op()]
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)

    fmt, size, accepted = main_window._ask_diagram_export_options()

    assert accepted is True
    assert main_window.settings.value("diagram_export_format", "", str) == fmt
    assert int(main_window.settings.value("diagram_export_size", 0, int)) == size


def test_cancelling_the_options_dialog_exports_nothing(main_window, monkeypatch) -> None:
    main_window.operations = [_op()]
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self: QtWidgets.QDialog.Rejected)

    main_window._export_diagrams_dialog()

    assert main_window._diagram_export_worker is None


def test_the_worker_writes_the_files_it_was_given(main_window, qapp, tmp_path) -> None:
    """Integração de verdade: worker de ponta a ponta, como o botão dispara."""
    from conftest import process_until
    from chess_pdf_editor.workers import DiagramExportWorker

    ops = [_op(page=0), _op(page=1, fen=OTHER_FEN)]
    finished: list[tuple[int, int, str]] = []
    worker = DiagramExportWorker(ops, str(tmp_path), fmt="svg", size_px=128, parent=main_window)
    worker.done.connect(lambda w, f, i: finished.append((w, f, i)))
    worker.start()
    try:
        assert process_until(qapp, lambda: bool(finished), timeout_sec=40)
    finally:
        worker.cancel()
        worker.wait(5000)

    written, failures, index_path = finished[0]
    assert (written, failures) == (2, 0)
    assert Path(index_path).name == INDEX_NAME
    assert len(list(tmp_path.glob("*.svg"))) == 2
