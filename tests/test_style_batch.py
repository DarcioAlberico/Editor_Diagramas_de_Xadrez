"""Estilo em lote com prévia (§36).

O que estava sem rede antes deste sprint: `Aplicar em todas as substituições`
reescrevia o estilo de todo o livro a cada passo de spinbox, e **não havia um
único teste** cobrindo esse caminho. Aqui entram os dois lados — a proposta que
não toca em nada até ser aceita, e a aplicação que toca em tudo de uma vez.
"""
from __future__ import annotations

import pytest

from conftest import DIAGRAM_RECT, SECOND_DIAGRAM_RECT, make_pdf as _make_pdf, process_until

from chess_pdf_editor.gallery import GalleryItem, KIND_OPERATION
from chess_pdf_editor.style_batch import (
    DEFAULT_SAMPLE,
    StyleBatchDialog,
    StyleProposal,
    count_affected,
    restyle,
    sample_items,
)
from chess_pdf_editor.types import OverlayOperation

FEN = "8/8/8/4k3/8/8/4K3/8"


def _op(page_num: int = 0, padding: float = 0.5, border: float = 0.0) -> OverlayOperation:
    return OverlayOperation(
        page_num=page_num,
        rect_pdf=DIAGRAM_RECT,
        fen=FEN,
        whiteout_padding_pt=padding,
        whiteout_padding_left_pt=padding,
        whiteout_padding_top_pt=padding,
        whiteout_padding_right_pt=padding,
        whiteout_padding_bottom_pt=padding,
        border_width_pt=border,
    )


PROPOSAL = StyleProposal(
    padding_left_pt=3.0,
    padding_top_pt=2.0,
    padding_right_pt=3.0,
    padding_bottom_pt=4.0,
    border_width_pt=0.75,
)


# ---------------------------------------------------------------------------
# A proposta
# ---------------------------------------------------------------------------


def test_a_proposal_read_from_an_operation_matches_it() -> None:
    op = _op(padding=2.5, border=1.0)
    assert StyleProposal.from_operation(op).matches(op) is True


def test_the_legacy_mean_field_is_the_mean_of_the_four_sides() -> None:
    """`whiteout_padding_pt` é anterior ao padding por lado e sobrevive no
    formato do projeto; deixá-lo desalinhado seria um projeto salvo inconsistente."""
    assert PROPOSAL.padding_mean_pt == pytest.approx((3.0 + 2.0 + 3.0 + 4.0) / 4.0)

    op = _op()
    PROPOSAL.apply_in_place(op)
    assert op.whiteout_padding_pt == pytest.approx(3.0)


def test_applying_to_a_copy_leaves_the_original_alone() -> None:
    """É o caminho da prévia: propor não pode mudar o que está salvo."""
    op = _op(padding=0.5, border=0.0)
    clone = PROPOSAL.applied_to(op)

    assert clone.whiteout_padding_left_pt == pytest.approx(3.0)
    assert op.whiteout_padding_left_pt == pytest.approx(0.5), "a original foi mutada"
    assert op.border_width_pt == pytest.approx(0.0)
    # O resto da operação atravessa intacto.
    assert clone.fen == op.fen
    assert clone.rect_pdf == op.rect_pdf
    assert clone.page_num == op.page_num


def test_applying_in_place_keeps_the_same_object() -> None:
    """É o caminho do commit: outros painéis guardam a mesma referência."""
    op = _op()
    PROPOSAL.apply_in_place(op)

    assert op.whiteout_padding_bottom_pt == pytest.approx(4.0)
    assert op.border_width_pt == pytest.approx(0.75)


def test_restyle_returns_one_copy_per_operation() -> None:
    ops = [_op(page_num=0), _op(page_num=1)]
    styled = restyle(ops, PROPOSAL)

    assert len(styled) == 2
    assert all(PROPOSAL.matches(op) for op in styled)
    assert not any(PROPOSAL.matches(op) for op in ops)


def test_only_what_would_really_change_is_counted() -> None:
    """Aplicar o estilo que já está lá não é mudança, e o botão não deve mentir."""
    already = _op(padding=3.0)
    PROPOSAL.apply_in_place(already)
    ops = [already, _op(padding=0.5), _op(padding=0.5)]

    assert count_affected(ops, PROPOSAL) == 2
    assert count_affected([already], PROPOSAL) == 0
    assert count_affected([], PROPOSAL) == 0


# ---------------------------------------------------------------------------
# A amostra
# ---------------------------------------------------------------------------


def _items(total: int) -> list[GalleryItem]:
    return [
        GalleryItem(kind=KIND_OPERATION, index=i, page_num=i, rect_pdf=DIAGRAM_RECT, fen=FEN)
        for i in range(total)
    ]


def test_a_short_book_is_shown_whole() -> None:
    items = _items(5)
    assert sample_items(items, 24) == items


def test_a_long_book_is_sampled_across_not_from_the_front() -> None:
    """Os primeiros diagramas de um livro são todos do mesmo capítulo."""
    picked = sample_items(_items(300), 24)

    assert len(picked) == 24
    pages = [item.page_num for item in picked]
    assert pages == sorted(pages)
    assert pages[0] == 0, "a primeira tem de entrar"
    assert pages[-1] == 299, "a última também"
    # Espalhada: a amostra alcança o fim do livro, não as 24 primeiras páginas.
    assert max(pages) > 200


def test_the_sample_never_repeats_an_entry() -> None:
    picked = sample_items(_items(30), 24)
    keys = [item.key for item in picked]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("limit", [0, -3])
def test_a_non_positive_limit_shows_nothing(limit: int) -> None:
    assert sample_items(_items(10), limit) == []


def test_a_limit_of_one_shows_the_first() -> None:
    picked = sample_items(_items(10), 1)
    assert [item.page_num for item in picked] == [0]


def test_an_empty_book_samples_to_nothing() -> None:
    assert sample_items([], DEFAULT_SAMPLE) == []


# ---------------------------------------------------------------------------
# A janela: aplicar e cancelar
# ---------------------------------------------------------------------------

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def _open_with_two_operations(window, tmp_path) -> None:
    window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    for rect in (DIAGRAM_RECT, SECOND_DIAGRAM_RECT):
        rect_img = window.pdf_service.pdf_rect_to_image_rect(
            window.current_page, rect, window.current_render.matrix
        )
        window.page_widget.set_selection_rect(rect_img)
        window.board_editor.set_piece_placement(FEN)
        window._add_operation()
    assert len(window.operations) == 2


def test_applying_writes_the_style_on_every_operation(main_window, tmp_path) -> None:
    _open_with_two_operations(main_window, tmp_path)

    main_window._apply_style_to_all(PROPOSAL)

    assert all(PROPOSAL.matches(op) for op in main_window.operations)
    # Os spinboxes do painel passam a mostrar o que foi aplicado.
    assert main_window.pad_left_spin.value() == pytest.approx(3.0)
    assert main_window.pad_bottom_spin.value() == pytest.approx(4.0)
    assert main_window.op_border_spin.value() == pytest.approx(0.75)


def test_applying_is_one_undo_not_one_per_diagram(main_window, tmp_path) -> None:
    """Restilizar 300 diagramas não pode custar 300 Ctrl+Z."""
    _open_with_two_operations(main_window, tmp_path)
    depth_before = len(main_window.history)

    main_window._apply_style_to_all(PROPOSAL)

    assert len(main_window.history) == depth_before + 1
    assert main_window.history.undo_label == "Estilo de todas as substituições"

    main_window._undo_change()
    assert not any(PROPOSAL.matches(op) for op in main_window.operations)


def test_applying_the_style_already_there_does_nothing(main_window, tmp_path) -> None:
    _open_with_two_operations(main_window, tmp_path)
    main_window._apply_style_to_all(PROPOSAL)
    depth_after_first = len(main_window.history)

    main_window._apply_style_to_all(PROPOSAL)

    assert len(main_window.history) == depth_after_first, "commit sem mudança"


def test_the_dialog_needs_a_pdf_and_a_substitution(main_window, tmp_path, no_modals) -> None:
    main_window._open_style_batch_dialog()
    assert no_modals, "sem PDF o comando tem de explicar, não abrir a grade"

    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    no_modals.clear()
    main_window._open_style_batch_dialog()
    assert no_modals, "sem substituição não há estilo para comparar"


def test_the_command_is_off_until_there_is_something_to_restyle(main_window, tmp_path) -> None:
    assert main_window.btn_style_batch.isEnabled() is False
    assert main_window.act_style_batch.isEnabled() is False

    _open_with_two_operations(main_window, tmp_path)

    assert main_window.btn_style_batch.isEnabled() is True
    assert main_window.act_style_batch.isEnabled() is True


def test_cancelling_the_dialog_changes_nothing(main_window, tmp_path, monkeypatch) -> None:
    _open_with_two_operations(main_window, tmp_path)
    before = [StyleProposal.from_operation(op) for op in main_window.operations]

    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda self: QtWidgets.QDialog.Rejected
    )
    main_window._open_style_batch_dialog()

    assert [StyleProposal.from_operation(op) for op in main_window.operations] == before


def _captured_pairs(dialog) -> list[tuple[bytes, bytes]]:
    pairs: list[tuple[bytes, bytes]] = []
    dialog._worker.thumbnail_ready.connect(
        lambda key, before, after: pairs.append((bytes(before), bytes(after)))
    )
    return pairs


def _style_dialog(window, proposal) -> StyleBatchDialog:
    return StyleBatchDialog(
        window.current_pdf_path,
        window.operations,
        erase_operations=window.erase_operations,
        whiteout=window.whiteout_check.isChecked(),
        include_lichess_link=window.include_lichess_link_check.isChecked(),
        erase_coordinates=window.erase_coordinates_check.isChecked(),
        proposal=proposal,
        parent=window,
    )


def test_the_grid_really_shows_two_different_renders(main_window, qapp, tmp_path) -> None:
    """O ponto da grade: o lado esquerdo é o estilo salvo, o direito o proposto.
    Se os dois saíssem iguais, a grade seria decoração."""
    _open_with_two_operations(main_window, tmp_path)
    fat = StyleProposal(
        padding_left_pt=12.0,
        padding_top_pt=12.0,
        padding_right_pt=12.0,
        padding_bottom_pt=12.0,
        border_width_pt=2.0,
    )
    assert count_affected(main_window.operations, fat) == 2

    dialog = _style_dialog(main_window, fat)
    try:
        pairs = _captured_pairs(dialog)
        assert process_until(qapp, lambda: len(pairs) >= 2, timeout_sec=40)
        for before_png, after_png in pairs:
            assert before_png and after_png
            assert before_png != after_png, "a grade mostrou duas vezes a mesma coisa"
    finally:
        dialog.stop_worker()
        dialog.close()


def test_proposing_the_style_already_there_shows_two_equal_sides(
    main_window, qapp, tmp_path
) -> None:
    """Prova que a diferença vem do estilo, e não de o "antes" ser outro render."""
    _open_with_two_operations(main_window, tmp_path)
    same = StyleProposal.from_operation(main_window.operations[0])
    assert count_affected(main_window.operations, same) == 0

    dialog = _style_dialog(main_window, same)
    try:
        pairs = _captured_pairs(dialog)
        assert process_until(qapp, lambda: len(pairs) >= 2, timeout_sec=40)
        for before_png, after_png in pairs:
            assert before_png == after_png
    finally:
        dialog.stop_worker()
        dialog.close()


def test_the_grid_says_how_many_of_how_many(main_window, tmp_path) -> None:
    """Recorte silencioso se lê como "conferi tudo"."""
    _open_with_two_operations(main_window, tmp_path)

    dialog = _style_dialog(main_window, PROPOSAL)
    try:
        assert "2" in dialog.sample_label.text()
        assert dialog.apply_button.text() == "Aplicar em 2 de 2"

        # Proposta igual à atual: o botão não pode prometer mudança.
        same = StyleProposal.from_operation(main_window.operations[0])
        dialog._spins["padding_left_pt"].setValue(same.padding_left_pt)
        dialog._spins["padding_top_pt"].setValue(same.padding_top_pt)
        dialog._spins["padding_right_pt"].setValue(same.padding_right_pt)
        dialog._spins["padding_bottom_pt"].setValue(same.padding_bottom_pt)
        dialog._spins["border_width_pt"].setValue(same.border_width_pt)
        assert dialog.apply_button.text() == "Aplicar (nada muda)"
    finally:
        dialog.stop_worker()
        dialog.close()


def test_accepting_the_dialog_applies_what_it_proposed(main_window, tmp_path, monkeypatch) -> None:
    _open_with_two_operations(main_window, tmp_path)

    def _accept(dialog):
        # O usuário mexe nos spinboxes da grade e aceita.
        dialog._spins["padding_left_pt"].setValue(3.0)
        dialog._spins["padding_top_pt"].setValue(2.0)
        dialog._spins["padding_right_pt"].setValue(3.0)
        dialog._spins["padding_bottom_pt"].setValue(4.0)
        dialog._spins["border_width_pt"].setValue(0.75)
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec", _accept)
    main_window._open_style_batch_dialog()

    assert all(PROPOSAL.matches(op) for op in main_window.operations)
