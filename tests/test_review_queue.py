"""Fila de revisão por confiança (Sprint 9.1).

Reconhecer um livro de 898 páginas passou a levar 8,5 min; conferir os candidatos
um a um é que virou o gargalo. Estes testes protegem o atalho — e, principalmente,
a segurança dele: com filtro ligado, uma ação em massa não pode tocar no que está
escondido.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf

fitz = pytest.importorskip("fitz")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _open(window, tmp_path: Path, pages: int = 4):
    window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=pages)), clear_ops=True)
    return window


def _candidate(page: int, confidence) -> OverlayOperation:
    return OverlayOperation(
        page_num=page,
        rect_pdf=DIAGRAM_RECT,
        fen=FEN,
        source="local-candidato",
        confidence=confidence,
    )


def _fill(window, *confidences) -> None:
    window.candidates = [_candidate(page, conf) for page, conf in enumerate(confidences)]
    window._refresh_candidates_list()


def _visible_confidences(window) -> list[object]:
    return [
        window.candidates[window.candidates_list.item(row).data(0x0100)].confidence
        for row in range(window.candidates_list.count())
    ]


# ---------------------------------------------------------------------------
# Filtro
# ---------------------------------------------------------------------------


def test_without_the_filter_every_candidate_is_listed(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, None)

    assert main_window.candidates_list.count() == 3


def test_the_filter_keeps_only_the_uncertain_ones(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window.candidates_threshold_spin.setValue(0.80)
    main_window.candidates_only_uncertain.setChecked(True)

    assert _visible_confidences(main_window) == [0.42]


def test_an_unknown_confidence_counts_as_uncertain(main_window, tmp_path) -> None:
    """Não saber não é o mesmo que estar confiante — a mesma regra do híbrido."""
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, None)
    main_window.candidates_only_uncertain.setChecked(True)

    assert _visible_confidences(main_window) == [None]


def test_the_threshold_moves_the_line(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.85, 0.42)
    main_window.candidates_only_uncertain.setChecked(True)

    main_window.candidates_threshold_spin.setValue(0.80)
    assert _visible_confidences(main_window) == [0.42]

    main_window.candidates_threshold_spin.setValue(0.90)
    assert _visible_confidences(main_window) == [0.85, 0.42]


def test_the_label_shows_how_many_were_hidden(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window.candidates_only_uncertain.setChecked(True)

    assert "1 incertos de 3" in main_window.candidates_label.text()


def test_the_section_stays_visible_when_the_filter_empties_the_list(main_window, tmp_path) -> None:
    """Se a seção sumisse, não haveria como desligar o filtro."""
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.97)
    main_window.candidates_only_uncertain.setChecked(True)

    assert main_window.candidates_list.count() == 0
    assert main_window.candidates_section.isVisibleTo(main_window)


# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------


def test_worst_first_puts_the_least_confident_on_top(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.71)
    main_window.candidates_worst_first.setChecked(True)

    assert _visible_confidences(main_window) == [0.42, 0.71, 0.99]


def test_unknown_confidence_sorts_first(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.42, None, 0.99)
    main_window.candidates_worst_first.setChecked(True)

    assert _visible_confidences(main_window) == [None, 0.42, 0.99]


def test_page_order_is_the_default(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.42, 0.99, 0.71)

    assert _visible_confidences(main_window) == [0.42, 0.99, 0.71]


# ---------------------------------------------------------------------------
# Segurança das ações em massa
# ---------------------------------------------------------------------------


def test_applying_all_with_a_filter_only_touches_what_is_visible(
    main_window, tmp_path, no_modals
) -> None:
    """O ponto da fila de conferência é não aplicar às cegas."""
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window.candidates_only_uncertain.setChecked(True)

    main_window._apply_all_candidates()

    assert [op.confidence for op in main_window.operations] == [0.42]
    assert [c.confidence for c in main_window.candidates] == [0.99, 0.95]


def test_discarding_all_with_a_filter_keeps_the_hidden_ones(
    main_window, tmp_path, no_modals
) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window.candidates_only_uncertain.setChecked(True)

    main_window._discard_all_candidates()

    assert [c.confidence for c in main_window.candidates] == [0.99, 0.95]
    assert main_window.operations == []


def test_the_confirmation_says_how_many_stay_behind(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window.candidates_only_uncertain.setChecked(True)

    main_window._apply_all_candidates()

    texts = [text for _title, text in no_modals]
    assert any("2 candidato(s) fora do filtro ficam na fila" in text for text in texts)


def test_the_buttons_say_visible_when_filtering(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42)

    assert main_window.btn_apply_all_candidates.text() == "Aplicar todos"

    main_window.candidates_only_uncertain.setChecked(True)
    assert main_window.btn_apply_all_candidates.text() == "Aplicar visíveis"
    assert main_window.btn_discard_all_candidates.text() == "Descartar visíveis"


def test_without_hidden_candidates_the_buttons_say_all(main_window, tmp_path) -> None:
    """Filtro ligado que não esconde nada não pode mudar o rótulo."""
    _open(main_window, tmp_path)
    _fill(main_window, 0.42, 0.30)
    main_window.candidates_only_uncertain.setChecked(True)

    assert main_window.btn_apply_all_candidates.text() == "Aplicar todos"


def test_mass_actions_are_disabled_when_the_filter_empties_the_list(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.97)
    main_window.candidates_only_uncertain.setChecked(True)

    assert main_window.btn_apply_all_candidates.isEnabled() is False
    assert main_window.btn_discard_all_candidates.isEnabled() is False


# ---------------------------------------------------------------------------
# Continuidade
# ---------------------------------------------------------------------------


def test_the_same_candidate_stays_selected_when_the_order_changes(main_window, tmp_path) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.71)
    main_window.candidates_list.setCurrentRow(1)  # o de 0,42
    assert main_window._selected_candidate_index() == 1

    main_window.candidates_worst_first.setChecked(True)

    assert main_window._selected_candidate_index() == 1, "a seleção seguiu a linha, não o item"
    assert main_window.candidates_list.currentRow() == 0


def test_the_filter_choice_survives_the_session(main_window) -> None:
    from chess_pdf_editor import app as app_module

    main_window.candidates_only_uncertain.setChecked(True)
    main_window.candidates_threshold_spin.setValue(0.65)
    main_window.candidates_worst_first.setChecked(True)
    settings = main_window.settings
    main_window.close()

    reopened = app_module.MainWindow(settings=settings)
    try:
        assert reopened.candidates_only_uncertain.isChecked()
        assert reopened.candidates_worst_first.isChecked()
        assert reopened.candidates_threshold_spin.value() == pytest.approx(0.65)
    finally:
        reopened.close()


def test_undo_brings_back_a_filtered_mass_discard(main_window, tmp_path, no_modals) -> None:
    _open(main_window, tmp_path)
    _fill(main_window, 0.99, 0.42, 0.95)
    main_window._commit_history("preparar")
    main_window.candidates_only_uncertain.setChecked(True)

    main_window._discard_all_candidates()
    assert len(main_window.candidates) == 2

    main_window._undo_change()
    assert [c.confidence for c in main_window.candidates] == [0.99, 0.42, 0.95]
