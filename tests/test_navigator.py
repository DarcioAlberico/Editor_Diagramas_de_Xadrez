"""Navegador de diagramas (§54).

A janela existe para conferir **as etiquetas** de um diagrama de cada vez: o
número do lance e a vez de jogar, que o livro imprime em volta do tabuleiro e que
a galeria não tem tamanho para mostrar.

O que estes testes protegem:

* andar na fila na ordem do livro, e voltar a um número sem 100 cliques;
* os campos editarem o **objeto vivo** — o mesmo que a janela principal guarda;
* a rajada de um spinbox virar **um** passo de desfazer, e não trinta;
* o retorno que as etiquetas dão (FEN final, link, legalidade), já que nenhuma
  delas muda um pixel da imagem;
* a promessa do Sprint 5.1: nenhuma QThread sobrevive ao fechamento.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf, process_until

pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.gallery import KIND_CANDIDATE, KIND_OPERATION  # noqa: E402
from chess_pdf_editor.navigator import DiagramNavigatorDialog  # noqa: E402
from chess_pdf_editor.pdf_service import operation_full_fen  # noqa: E402
from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
OTHER_FEN = "8/8/8/3qk3/8/8/4K3/8"
#: Rei preto em xeque com as brancas a jogar: legal só com as pretas a jogar.
#: É o caso que a auditoria sabe apontar como "lado a jogar trocado" (§37).
SIDE_SWAPPED_FEN = "4k3/8/8/8/8/8/8/K3R3"


def _op(page: int, rect=DIAGRAM_RECT, fen: str = FEN, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=rect, fen=fen, **kwargs)


def _dialog(pdf: Path, operations, candidates=(), **kwargs) -> DiagramNavigatorDialog:
    return DiagramNavigatorDialog(str(pdf), operations, candidates=candidates, **kwargs)


def _settle(qapp, seconds: float = 0.6) -> None:
    """Roda o loop de eventos por um tempo, sem esperar nada em especial.

    Usado para provar uma **ausência**: que nenhum segundo sinal chegou depois do
    primeiro. Um `processEvents` só não daria tempo do timer de 300 ms disparar de
    novo, e o teste passaria mesmo com o defeito presente.
    """
    process_until(qapp, lambda: False, timeout_sec=seconds)


# ---------------------------------------------------------------------------
# Navegação
# ---------------------------------------------------------------------------


def test_it_opens_on_the_first_diagram_of_the_book(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(2), _op(0)])
    try:
        assert dialog._current_key() == (KIND_OPERATION, 1), "ordem de página, não de inserção"
        assert dialog.position_spin.value() == 1
        assert dialog.count_label.text() == "de 2"
        assert not dialog.btn_prev.isEnabled()
        assert dialog.btn_next.isEnabled()
    finally:
        dialog.close()


def test_next_and_previous_walk_the_book(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(0), _op(1), _op(2)])
    try:
        dialog.btn_next.click()
        assert dialog._current_key() == (KIND_OPERATION, 1)
        dialog.btn_next.click()
        assert dialog._current_key() == (KIND_OPERATION, 2)
        assert not dialog.btn_next.isEnabled(), "não há para onde ir depois do último"
        dialog.btn_prev.click()
        assert dialog._current_key() == (KIND_OPERATION, 1)
    finally:
        dialog.close()


def test_the_position_spin_jumps_straight_to_a_diagram(qapp, tmp_path) -> None:
    """Sem ele, voltar ao 3º depois de chegar ao 5º seria clicar `Anterior` duas
    vezes — e num livro de verdade, cem."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=5)
    dialog = _dialog(pdf, [_op(page) for page in range(5)])
    try:
        dialog.position_spin.setValue(4)
        assert dialog._current_key() == (KIND_OPERATION, 3)
        assert dialog.header_label.text().startswith("Substituição 004")
        assert "página 4" in dialog.header_label.text()
    finally:
        dialog.close()


def test_it_can_open_on_the_diagram_the_window_already_had(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(0), _op(1), _op(2)], start_key=(KIND_OPERATION, 2))
    try:
        assert dialog._current_key() == (KIND_OPERATION, 2)
        assert dialog.position_spin.value() == 3
    finally:
        dialog.close()


def test_candidates_walk_in_the_same_queue_and_say_so(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(pdf, [_op(0)], candidates=[_op(1, confidence=0.42)])
    try:
        dialog.btn_next.click()
        assert dialog._current_key() == (KIND_CANDIDATE, 0)
        texto = dialog.header_label.text()
        assert texto.startswith("Candidato 001")
        assert "ainda não aplicado" in texto
        assert "0.42" in texto
    finally:
        dialog.close()


def test_enter_in_the_position_field_does_not_press_a_button(qapp, tmp_path) -> None:
    """Todo `QPushButton` de um diálogo nasce `autoDefault`, e o foco inicial é o
    campo da posição: Enter depois de digitar "3" acionaria `Anterior` ou
    `Fechar` em vez de confirmar o número."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(page) for page in range(3)])
    try:
        botoes = dialog.findChildren(QtWidgets.QPushButton)
        assert botoes, "a janela tem botões"
        assert not any(botao.autoDefault() for botao in botoes)
        assert not any(botao.isDefault() for botao in botoes)
    finally:
        dialog.close()


def test_an_empty_navigator_says_so_instead_of_showing_nothing(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [])
    try:
        assert dialog.header_label.text() == "Nenhum diagrama"
        assert not dialog.tags_box.isEnabled()
        assert not dialog.btn_next.isEnabled()
        assert not dialog.btn_prev.isEnabled()
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# As etiquetas
# ---------------------------------------------------------------------------


def test_the_fields_edit_the_live_operation(qapp, tmp_path) -> None:
    """Por referência, como a galeria: o objeto aqui é o da janela principal."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    op = _op(0)
    dialog = _dialog(pdf, [op])
    try:
        dialog.move_spin.setValue(17)
        dialog.side_combo.setCurrentIndex(1)
        dialog.lichess_combo.setCurrentIndex(2)
        dialog.border_spin.setValue(1.5)

        assert op.fullmove_number == 17
        assert op.side_to_move == "b"
        assert op.include_lichess_link is False
        assert op.border_width_pt == pytest.approx(1.5)
    finally:
        dialog.close()


def test_navigating_loads_the_fields_without_writing_them_back(qapp, tmp_path) -> None:
    """Sem a guarda `_loading`, preencher os campos dispararia `valueChanged` e
    gravaria de volta — marcando o projeto como alterado só por navegar."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    first = _op(0, fullmove_number=4, side_to_move="b")
    second = _op(1, fullmove_number=9)
    dialog = _dialog(pdf, [first, second])
    edited: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: edited.append((kind, index)))
    try:
        dialog.btn_next.click()
        assert dialog.move_spin.value() == 9
        assert dialog.side_combo.currentData() == "w"
        dialog.btn_prev.click()
        assert dialog.move_spin.value() == 4
        assert dialog.side_combo.currentData() == "b"

        _settle(qapp)
        assert edited == [], "navegar não é editar"
        assert (first.fullmove_number, first.side_to_move) == (4, "b")
        assert (second.fullmove_number, second.side_to_move) == (9, "w")
    finally:
        dialog.close()


def test_a_burst_on_the_spinbox_becomes_one_undo_step(qapp, tmp_path) -> None:
    """Arrastar o spin de 1 a 5 é **uma** decisão do usuário.

    Um sinal por passo empilharia quatro passos de desfazer para desfazer um
    gesto, e o histórico guarda 60 — algumas mexidas apagariam o resto da sessão.
    """
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    op = _op(0)
    dialog = _dialog(pdf, [op])
    edited: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: edited.append((kind, index)))
    try:
        for value in (2, 3, 4, 5):
            dialog.move_spin.setValue(value)
        assert edited == [], "o sinal não sai antes do usuário parar de mexer"

        assert process_until(qapp, lambda: bool(edited)), "o sinal nunca saiu"
        _settle(qapp)
        assert edited == [(KIND_OPERATION, 0)]
        assert op.fullmove_number == 5
    finally:
        dialog.close()


def test_walking_away_delivers_the_pending_edit(qapp, tmp_path) -> None:
    """A edição já está no objeto; sem o sinal a janela principal nunca a comita."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(pdf, [_op(0), _op(1)])
    edited: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: edited.append((kind, index)))
    try:
        dialog.move_spin.setValue(12)
        dialog.btn_next.click()
        assert edited == [(KIND_OPERATION, 0)], "o sinal é do diagrama que saiu"
    finally:
        dialog.close()


def test_closing_delivers_the_pending_edit(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)])
    edited: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: edited.append((kind, index)))

    dialog.move_spin.setValue(12)
    dialog.close()

    assert edited == [(KIND_OPERATION, 0)]


# ---------------------------------------------------------------------------
# O retorno que as etiquetas dão
# ---------------------------------------------------------------------------


def test_the_final_fen_is_the_one_the_pdf_will_use(qapp, tmp_path) -> None:
    """As etiquetas não mudam um pixel: a FEN é o único retorno imediato delas.

    E é a **mesma** função da exportação, não uma segunda cópia da regra.
    """
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    op = _op(0)
    dialog = _dialog(pdf, [op])
    try:
        dialog.move_spin.setValue(23)
        dialog.side_combo.setCurrentIndex(1)
        assert dialog.fen_value.text() == operation_full_fen(op)
        assert dialog.fen_value.text() == f"{FEN} b - - 0 23"
    finally:
        dialog.close()


def test_the_link_line_says_whether_the_pdf_will_carry_one(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)], include_lichess_link=True)
    try:
        assert "O PDF vai levar" in dialog.link_label.text()
        dialog.lichess_combo.setCurrentIndex(2)  # sem link neste diagrama
        assert "Sem link no PDF" in dialog.link_label.text()
        assert "lichess.org/analysis" in dialog.link_label.text(), (
            "mesmo sem link no PDF dá para conferir a posição"
        )
    finally:
        dialog.close()


def test_a_diagram_that_follows_the_global_choice_follows_it(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)], include_lichess_link=False)
    try:
        assert dialog.lichess_combo.currentData() is None, "nasce em `Padrão`"
        assert "Sem link no PDF" in dialog.link_label.text()
    finally:
        dialog.close()


def test_the_audit_calls_out_a_swapped_side_to_move(qapp, tmp_path) -> None:
    """O único juiz automático do campo `vez de jogar` (§37)."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0, fen=SIDE_SWAPPED_FEN)])
    try:
        assert "lado a jogar" in dialog.legality_label.text()

        dialog.side_combo.setCurrentIndex(1)  # pretas
        assert "nada a apontar" in dialog.legality_label.text().lower()
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_the_pair_arrives_and_the_two_sides_differ(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)])
    pares: list[tuple[bytes, bytes]] = []
    worker = dialog._worker
    assert worker is not None, "o navegador não iniciou o render"
    worker.thumbnail_ready.connect(
        lambda key, before, after: pares.append((bytes(before), bytes(after)))
    )
    try:
        assert process_until(qapp, lambda: bool(pares)), "o render não terminou"
        before, after = pares[0]
        assert before and after
        assert before != after, "o 'depois' tem de mostrar a substituição"
        # `isHidden`, e não `isVisible`: o diálogo do teste nunca é mostrado, e
        # filho de janela escondida é invisível por herança mesmo tendo sido
        # mostrado. O que se quer saber aqui é se a mensagem deu lugar ao par.
        assert not dialog.before_after.thumbs.isHidden()
    finally:
        dialog.close()


def test_only_one_item_is_rendered_at_a_time(qapp, tmp_path) -> None:
    """Um livro inteiro por diagrama visitado seria minutos de espera por clique."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=4)
    dialog = _dialog(pdf, [_op(page) for page in range(4)])
    try:
        worker = dialog._worker
        assert worker is not None
        assert len(worker._items) == 1
    finally:
        dialog.close()


def test_a_late_result_from_another_diagram_is_ignored(qapp, tmp_path) -> None:
    """Sair correndo com Alt+→ não pode pintar o diagrama anterior por cima."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(pdf, [_op(0), _op(1)])
    try:
        dialog._on_thumbnail_ready((KIND_OPERATION, 1), b"antes", b"depois")
        assert dialog.before_after._before_png != b"antes"

        dialog._on_thumbnail_ready(dialog._current_key(), b"antes", b"depois")
        assert dialog.before_after._before_png == b"antes"
    finally:
        dialog.close()


def test_closing_leaves_no_worker_running(qapp, tmp_path) -> None:
    """A lição do Sprint 5.1: nenhuma QThread sobrevive ao fechamento."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(page) for page in range(3)])
    worker = dialog._worker
    assert worker is not None

    dialog.close()

    assert dialog._worker is None
    assert worker.isFinished()


def test_stopping_twice_is_harmless(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)])
    dialog.stop_worker()
    dialog.stop_worker()
    dialog.close()


# ---------------------------------------------------------------------------
# Listas trocadas por baixo
# ---------------------------------------------------------------------------


def test_rebind_points_the_fields_at_the_new_lists(qapp, tmp_path) -> None:
    """Desfazer substitui as listas; sem `rebind` a edição iria para uma órfã."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    antigas = [_op(0), _op(1)]
    dialog = _dialog(pdf, antigas)
    try:
        dialog.btn_next.click()
        novas = [_op(0), _op(1)]
        dialog.rebind(novas)

        assert dialog._current_key() == (KIND_OPERATION, 1), "a posição é da mesma chave"
        dialog.move_spin.setValue(31)
        assert novas[1].fullmove_number == 31
        assert antigas[1].fullmove_number == 1, "a lista órfã não recebe mais nada"
    finally:
        dialog.close()


def test_rebind_keeps_the_diagram_and_not_the_number(qapp, tmp_path) -> None:
    """O diagrama 3 pode virar o 2 quando um anterior some — ficar no "terceiro"
    seria ficar noutro diagrama."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=3)
    dialog = _dialog(pdf, [_op(0), _op(1), _op(2, fen=OTHER_FEN)])
    try:
        dialog.position_spin.setValue(3)
        chave = dialog._current_key()

        # A operação da página 1 saiu: quem era índice 2 passa a ser 1.
        dialog.rebind([_op(0), _op(2, fen=OTHER_FEN)])

        assert dialog.position_spin.value() == 2
        assert dialog._current_key() != chave
        assert dialog.current_entry().fen == OTHER_FEN
    finally:
        dialog.close()


def test_rebind_drops_a_pending_edit_instead_of_announcing_it(qapp, tmp_path) -> None:
    """O objeto que ela tocou saiu do projeto; anunciá-la mandaria a janela
    principal comitar um índice que agora aponta para outro diagrama."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)])
    edited: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: edited.append((kind, index)))
    try:
        dialog.move_spin.setValue(8)
        dialog.rebind([_op(0)])
        _settle(qapp)
        assert edited == []
    finally:
        dialog.close()


def test_a_diagram_that_left_the_list_says_so(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(pdf, [_op(0)])
    try:
        dialog.rebind([])
        assert dialog.header_label.text() == "Nenhum diagrama"
        assert not dialog.tags_box.isEnabled()
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Integração com a janela principal
# ---------------------------------------------------------------------------


def test_the_window_refuses_without_a_pdf(main_window, no_modals) -> None:
    main_window._open_navigator()
    assert main_window.navigator_dialog is None
    assert any(title == "Sem PDF" for title, _ in no_modals)


def test_the_window_refuses_to_open_it_empty(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window._open_navigator()

    assert main_window.navigator_dialog is None
    assert any(title == "Navegador de diagramas" for title, _ in no_modals)


def test_it_opens_on_the_operation_selected_in_the_window(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    main_window.operations.extend([_op(0), _op(1), _op(2)])
    main_window._refresh_operations_list()
    main_window._set_current_operation(2)

    main_window._open_navigator()
    dialog = main_window.navigator_dialog
    try:
        assert dialog is not None
        assert dialog._current_key() == (KIND_OPERATION, 2)
    finally:
        dialog.close()


def test_editing_in_the_navigator_reaches_the_history(main_window, tmp_path, no_modals) -> None:
    """Sem o commit, um `Ctrl+Z` depois da edição desfaria a *ação anterior*."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=2)), clear_ops=True)
    main_window.operations.append(_op(0))
    main_window._refresh_operations_list()
    main_window._reset_history("base")

    main_window._open_navigator()
    dialog = main_window.navigator_dialog
    assert dialog is not None
    try:
        dialog.move_spin.setValue(14)
        dialog._flush_edit(render=False)
    finally:
        dialog.close()

    assert main_window.operations[0].fullmove_number == 14
    assert main_window.history.can_undo
    assert main_window.history.undo_label == "editar etiquetas no navegador"

    main_window._undo_change()
    assert main_window.operations[0].fullmove_number == 1


def test_undo_rebinds_the_open_navigator(main_window, tmp_path, no_modals) -> None:
    """Desfazer troca as listas: continuar editando as antigas seria editar o nada."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=2)), clear_ops=True)
    main_window.operations.append(_op(0))
    main_window._refresh_operations_list()
    main_window._reset_history("base")

    main_window._open_navigator()
    dialog = main_window.navigator_dialog
    assert dialog is not None
    try:
        dialog.move_spin.setValue(14)
        dialog._flush_edit(render=False)
        main_window._undo_change()

        assert main_window.operations[0].fullmove_number == 1
        assert dialog.move_spin.value() == 1, "os campos seguiram a lista restaurada"

        dialog.move_spin.setValue(21)
        assert main_window.operations[0].fullmove_number == 21
    finally:
        dialog.close()


def test_going_to_a_diagram_takes_the_window_to_its_page(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    main_window.operations.extend([_op(0), _op(2, fen=OTHER_FEN)])
    main_window._refresh_operations_list()

    main_window._open_navigator()
    dialog = main_window.navigator_dialog
    assert dialog is not None
    try:
        dialog.position_spin.setValue(2)
        dialog.btn_focus.click()

        assert main_window.current_page == 2
        assert main_window.board_editor.piece_placement() == OTHER_FEN
    finally:
        dialog.close()


def test_opening_another_pdf_closes_the_navigator(main_window, tmp_path, no_modals) -> None:
    """Outro livro, outras páginas: ele ficaria renderizando o caminho antigo."""
    main_window._open_pdf(str(make_pdf(tmp_path / "a.pdf", pages=2)), clear_ops=True)
    main_window.operations.append(_op(0))
    main_window._refresh_operations_list()
    main_window._open_navigator()
    assert main_window.navigator_dialog is not None

    main_window._open_pdf(str(make_pdf(tmp_path / "b.pdf", pages=2)), clear_ops=True)

    assert main_window.navigator_dialog is None


def test_closing_the_window_closes_the_navigator(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    main_window.operations.extend([_op(page) for page in range(3)])
    main_window._refresh_operations_list()
    main_window._open_navigator()

    dialog = main_window.navigator_dialog
    assert dialog is not None
    worker = dialog._worker

    main_window.close()

    assert main_window.navigator_dialog is None
    if worker is not None:
        assert worker.isFinished()


def test_the_second_opening_replaces_the_first(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=2)), clear_ops=True)
    main_window.operations.append(_op(0))
    main_window._refresh_operations_list()

    main_window._open_navigator()
    first = main_window.navigator_dialog
    main_window._open_navigator()
    second = main_window.navigator_dialog
    try:
        assert first is not second
        assert second is not None
    finally:
        if second is not None:
            second.close()
