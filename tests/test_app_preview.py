"""Testes de GUI da previa ao vivo (Qt offscreen).

As fixtures `qapp` e `main_window` vivem no `conftest.py`, junto com o
isolamento de preferencias, autosave e logs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, SECOND_DIAGRAM_RECT, make_pdf as _make_pdf

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

FEN = "8/8/8/4k3/8/8/4K3/8"
FEN_EDITED = "8/8/8/3qk3/8/8/4K3/8"


def _select_rect(window, rect_pdf) -> None:
    rect_img = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page,
        rect_pdf,
        window.current_render.matrix,
    )
    window.page_widget.set_selection_rect(rect_img)


def _select_diagram(window, fen: str = FEN, rect_pdf=DIAGRAM_RECT) -> None:
    _select_rect(window, rect_pdf)
    window.board_editor.set_piece_placement(fen)


def _force_preview(window) -> None:
    window._preview_timer.stop()
    window._refresh_result_preview()


def test_window_starts_without_restored_session(main_window) -> None:
    assert main_window.pdf_service is None
    assert main_window.current_render is None


def test_draft_operation_from_selection_and_fen(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    assert main_window._draft_operation() is None, "sem selecao nao ha rascunho"

    _select_diagram(main_window)
    draft = main_window._draft_operation()
    assert draft is not None
    assert draft.fen == FEN
    assert draft.page_num == 0


def test_live_preview_shows_result_and_restores_original(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    original_png = main_window.current_render.image_png
    _select_diagram(main_window)

    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)

    assert main_window._showing_preview is True
    assert main_window.current_preview_render is not None
    assert main_window.current_preview_render.image_png != original_png
    # A selecao do usuario sobrevive a troca de bitmap.
    assert main_window.page_widget.selection_rect() is not None
    # Na previa as marcacoes de trabalho saem da frente do resultado.
    assert main_window.page_widget._operation_rects == []

    main_window.act_toggle_preview.setChecked(False)
    _force_preview(main_window)
    assert main_window._showing_preview is False


def test_preview_follows_board_edits(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window)
    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)
    before_edit = main_window.current_preview_render.image_png

    main_window.board_editor.set_piece_placement(FEN_EDITED)
    _force_preview(main_window)

    assert main_window.current_preview_render.image_png != before_edit


def test_draft_preview_matches_committed_operation(main_window, tmp_path) -> None:
    """O que o usuario ve antes de confirmar e o que ele obtem ao confirmar."""
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window)
    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)
    draft_png = main_window.current_preview_render.image_png

    main_window._add_operation()
    _force_preview(main_window)

    assert len(main_window.operations) == 1
    assert main_window.current_preview_render.image_png == draft_png


def test_before_after_thumbnails(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window)
    _force_preview(main_window)

    assert main_window.before_after._before_png
    assert main_window.before_after._after_png
    assert main_window.before_after._before_png != main_window.before_after._after_png


def test_new_selection_does_not_reuse_previous_position(main_window, tmp_path) -> None:
    """Selecionar outro diagrama nao pode colar a posicao do anterior."""
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window, FEN, DIAGRAM_RECT)
    assert main_window._draft_operation() is not None

    # Mesma pagina, outra area: a FEN ainda no editor pertence ao 1o diagrama.
    _select_rect(main_window, SECOND_DIAGRAM_RECT)
    assert main_window._draft_operation() is None
    _force_preview(main_window)
    assert main_window.before_after._after_png is None

    # Assim que o usuario monta a posicao da area nova, a previa volta.
    main_window.board_editor.set_piece_placement(FEN_EDITED)
    draft = main_window._draft_operation()
    assert draft is not None
    assert draft.fen == FEN_EDITED


def test_selection_can_be_nudged_without_losing_draft(main_window, tmp_path) -> None:
    """Ajustar a mesma bbox mantem o rascunho (IoU alto)."""
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window)

    x0, y0, x1, y1 = DIAGRAM_RECT
    _select_rect(main_window, (x0 + 3.0, y0 + 3.0, x1 + 3.0, y1 + 3.0))
    assert main_window._draft_operation() is not None


def test_preview_survives_page_change(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _select_diagram(main_window)
    main_window._add_operation()
    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)

    main_window.current_page = 1
    main_window._render_current_page()
    _force_preview(main_window)

    assert main_window.current_render.page_num == 1
    # Pagina 2 nao tem alteracoes: nada de previa para mostrar.
    assert main_window._showing_preview is False


# ---------------------------------------------------------------------------
# Fila de candidatos
# ---------------------------------------------------------------------------


def _install_fake_ocr(monkeypatch, window, fen: str = FEN, rect_pdf=DIAGRAM_RECT):
    """Faz o cliente de OCR devolver uma deteccao fixa sobre `rect_pdf`."""
    from chess_pdf_editor import app as app_module
    from chess_pdf_editor.types import OcrBoardResult, OcrPrediction

    render = window.current_render
    x0, y0, x1, y1 = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page, rect_pdf, render.matrix
    )
    result = OcrBoardResult(
        fen=fen,
        xc=((x0 + x1) / 2.0) / render.width_px,
        yc=((y0 + y1) / 2.0) / render.height_px,
        width=(x1 - x0) / render.width_px,
        height=(y1 - y0) / render.height_px,
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def predict(self, image_bytes, filename="board.png"):
            return OcrPrediction(request_id="fake", status=200, message=None, results=[result])

    monkeypatch.setattr(app_module, "OcrApiClient", _FakeClient)


def test_recognition_queues_candidates_when_auto_apply_is_off(main_window, tmp_path, monkeypatch) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)

    main_window._recognize_current_page()

    assert main_window.operations == [], "nada pode ser aplicado sem conferencia"
    assert len(main_window.candidates) == 1
    assert main_window.candidates[0].fen == FEN


def test_recognition_applies_directly_when_auto_apply_is_on(main_window, tmp_path, monkeypatch) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(True)
    _install_fake_ocr(monkeypatch, main_window)

    main_window._recognize_current_page()

    assert len(main_window.operations) == 1
    assert main_window.candidates == []


def test_applying_candidate_moves_it_to_operations(main_window, tmp_path, monkeypatch) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)
    main_window._recognize_current_page()

    main_window.candidates_list.setCurrentRow(0)
    main_window._apply_selected_candidate()

    assert len(main_window.operations) == 1
    assert main_window.candidates == []
    assert main_window.operations[0].fen == FEN


def test_candidate_correction_is_what_gets_applied(main_window, tmp_path, monkeypatch) -> None:
    """Corrigir a posicao durante a conferencia aplica a versao corrigida."""
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)
    main_window._recognize_current_page()

    main_window._focus_candidate(0)
    main_window.board_editor.set_piece_placement(FEN_EDITED)
    main_window._apply_selected_candidate()

    assert len(main_window.operations) == 1
    assert main_window.operations[0].fen == FEN_EDITED


def test_applying_candidate_ignores_unrelated_draft(main_window, tmp_path, monkeypatch) -> None:
    """Sair para outra area nao pode sequestrar o Aplicar do candidato."""
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)
    main_window._recognize_current_page()
    main_window._focus_candidate(0)

    # Usuario passa a mexer em outro diagrama da mesma pagina.
    _select_diagram(main_window, FEN_EDITED, SECOND_DIAGRAM_RECT)
    assert main_window._draft_operation() is not None

    main_window.candidates_list.setCurrentRow(0)
    main_window._apply_selected_candidate()

    assert len(main_window.operations) == 1
    applied = main_window.operations[0]
    assert applied.fen == FEN, "aplicou a posicao da outra area"
    assert applied.rect_pdf == pytest.approx(DIAGRAM_RECT, abs=0.5)


def test_discarding_candidate_leaves_no_operation(main_window, tmp_path, monkeypatch) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)
    main_window._recognize_current_page()

    main_window.candidates_list.setCurrentRow(0)
    main_window._discard_selected_candidate()

    assert main_window.candidates == []
    assert main_window.operations == []


def test_candidate_preview_shows_result_before_applying(main_window, tmp_path, monkeypatch) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.auto_apply_check.setChecked(False)
    _install_fake_ocr(monkeypatch, main_window)
    main_window._recognize_current_page()

    main_window._focus_candidate(0)
    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)

    assert main_window.operations == [], "conferir nao aplica"
    assert main_window._showing_preview is True
    assert main_window.current_preview_render.image_png != main_window.current_render.image_png
