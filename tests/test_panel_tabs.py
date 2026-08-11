"""A repartição do painel em abas (Sprint 9.23, §51).

A aba `OCR` carregava tudo: cinco etapas numeradas, a fila de conferência, e um
grupo `Avançado` com aparência e configuração do motor. Pedia 745 px num visor de
222. As outras duas abas somavam 476 px de conteúdo — a `Aparência` pedia 59.

O que estes testes prendem é o critério de repartição, não o desenho: **na aba do
fluxo fica o que é etapa do fluxo**. É a regra que faz a conta da §41.2 fechar, e é
a que se perde primeiro quando alguém acrescenta "só mais um controle aqui".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _tab_titles(window) -> list[str]:
    return [window.edit_tabs.tabText(i) for i in range(window.edit_tabs.count())]


def _fill_candidates(window, *confidences) -> None:
    window.candidates = [
        OverlayOperation(
            page_num=page,
            rect_pdf=DIAGRAM_RECT,
            fen=FEN,
            source="local-candidato",
            confidence=conf,
        )
        for page, conf in enumerate(confidences)
    ]
    window._refresh_candidates_list()


# ---------------------------------------------------------------------------
# A repartição
# ---------------------------------------------------------------------------


def test_the_flow_tab_holds_only_the_flow(main_window) -> None:
    """O critério, escrito como teste.

    Cada widget abaixo saiu da aba do fluxo por não ser etapa dele: dois são
    preferência, um é um parágrafo de estado, os outros são configuração do motor
    e da aparência do que se grava. Juntos pesavam 87 px **acima** do último passo
    — e é só o que está acima do último passo que decide se o fluxo rola.
    """
    flow_tab = main_window.edit_tabs.widget(0)
    fora_do_fluxo = (
        main_window.auto_apply_check,
        main_window.engine_status_label,
        main_window.engine_combo,
        main_window.endpoint_edit,
        main_window.local_model_edit,
        main_window.merida_font_edit,
        main_window.whiteout_check,
        main_window.click_detects_check,
        main_window.candidates_list,
    )

    for widget in fora_do_fluxo:
        assert not flow_tab.isAncestorOf(widget), (
            f"{widget.objectName() or type(widget).__name__} voltou para a aba do fluxo"
        )


def test_the_flow_tab_still_holds_every_step(main_window) -> None:
    """A contrapartida do teste acima: despoluir não pode virar esvaziar."""
    flow_tab = main_window.edit_tabs.widget(0)
    etapas = (
        main_window.btn_snap,
        main_window.btn_ocr,
        main_window.btn_ocr_page,
        main_window.btn_ocr_full,
        main_window.compare_group,
        main_window.btn_add,
        main_window.btn_add_eraser,
        main_window.changes_list,
    )

    for widget in etapas:
        assert flow_tab.isAncestorOf(widget), "uma etapa do fluxo saiu da aba do fluxo"


def test_the_tabs_are_named_for_what_they_hold(main_window) -> None:
    assert _tab_titles(main_window) == ["Diagrama", "Conferir", "FEN", "Ajustes"]


def test_the_tab_bar_fits_the_narrowest_panel(main_window, qapp) -> None:
    """Quatro abas em vez de três, num painel que não ficou mais largo. Se a barra
    não couber, o Qt põe setas de rolagem e as abas do fim ficam inalcançáveis sem
    um clique extra — o oposto de despoluir."""
    main_window.resize(1500, 900)
    main_window.show()
    qapp.processEvents()
    barra = main_window.edit_tabs.tabBar().sizeHint().width()

    assert barra <= main_window.side_stack.minimumWidth(), (
        f"a barra de abas pede {barra} px e o painel mínimo tem "
        f"{main_window.side_stack.minimumWidth()}"
    )


def test_no_tab_needs_horizontal_scrolling(main_window, qapp) -> None:
    """Rolagem horizontal é pior que um botão apertado.

    Apareceu de verdade ao mudar a fonte Merida de aba: campo de caminho mais
    `Selecionar Fonte...` mais `Limpar` numa linha só estouram a largura, e o Qt
    não corta o último botão — ele põe uma barra horizontal na aba inteira, que
    esconde metade dos controles atrás de um arrasto. Só se viu olhando a tela.

    A medição é na largura **padrão** do painel, não na mínima. A mínima só
    acontece para quem arrasta o divisor até o fim, e exigi-la forçaria rótulos
    telegráficos em tudo — os textos aqui já são medidos numa fonte offscreen mais
    larga que a real, então a régra ficaria mais dura que a realidade duas vezes.
    """
    main_window.resize(1500, 900)
    main_window.show()
    qapp.processEvents()

    largos: list[str] = []
    for i in range(main_window.edit_tabs.count()):
        main_window.edit_tabs.setTabVisible(i, True)
        main_window.edit_tabs.setCurrentIndex(i)
        # Os grupos recolhíveis escondem o conteúdo: abertos é como o usuário os vê
        # quando precisa deles, e é a largura de então que tem de caber.
        for group in main_window.settings_groups:
            group.setChecked(True)
        qapp.processEvents()
        page = main_window.edit_tabs.widget(i)
        pedido = page.widget().minimumSizeHint().width()
        if pedido > page.viewport().width():
            largos.append(
                f"{main_window.edit_tabs.tabText(i)} pede {pedido} px "
                f"e o visor dá {page.viewport().width()}"
            )

    assert not largos, "aba(s) exigindo rolagem horizontal: " + "; ".join(largos)


def test_the_settings_groups_start_collapsed(main_window) -> None:
    """§20.5. Agora estão numa aba própria **e** recolhidos — mas a aba própria não
    substitui o recolhimento: abrir `Ajustes` mostraria tudo de uma vez."""
    for group in main_window.settings_groups:
        assert group.isCheckable()
        assert group.isChecked() is False, f"{group.title()} abre expandido"


# ---------------------------------------------------------------------------
# A aba de conferência
# ---------------------------------------------------------------------------


def test_the_review_tab_is_hidden_until_there_is_something_to_review(main_window) -> None:
    """Uma aba permanentemente vazia é poluição com outro nome."""
    assert main_window.edit_tabs.isTabVisible(main_window._candidates_tab_index) is False


def test_a_batch_makes_the_review_tab_appear_and_takes_you_there(main_window, tmp_path) -> None:
    """O fim do lote é o momento em que a conferência passa a ser o trabalho."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    assert main_window.edit_tabs.currentIndex() == 0

    _fill_candidates(main_window, 0.9, 0.4, 0.7)

    index = main_window._candidates_tab_index
    assert main_window.edit_tabs.isTabVisible(index)
    assert main_window.edit_tabs.currentIndex() == index, (
        "o lote terminou e a fila não foi mostrada"
    )
    assert main_window.edit_tabs.tabText(index) == "Conferir (3)"


def test_working_the_queue_does_not_yank_you_between_tabs(main_window, tmp_path) -> None:
    """A ida é só na transição de vazia para cheia.

    O filtro mora dentro desta aba, então um `setCurrentIndex` a cada refresh
    arrancaria o usuário do lugar no meio do próprio trabalho dele. E se ele foi
    ver a lista de alterações, mexer na fila não pode trazê-lo de volta à força.
    """
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    _fill_candidates(main_window, 0.9, 0.4, 0.7)

    main_window.edit_tabs.setCurrentIndex(0)
    main_window._refresh_candidates_list()

    assert main_window.edit_tabs.currentIndex() == 0, "o refresh trocou a aba de volta"


def test_emptying_the_queue_takes_the_tab_away(main_window, tmp_path) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    _fill_candidates(main_window, 0.9, 0.4)
    assert main_window.edit_tabs.isTabVisible(main_window._candidates_tab_index)

    _fill_candidates(main_window)

    assert main_window.edit_tabs.isTabVisible(main_window._candidates_tab_index) is False


def test_the_count_in_the_tab_follows_the_queue(main_window, tmp_path) -> None:
    """O número na aba é o que diz se vale ir lá, já que a aba pode não estar à
    vista."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    _fill_candidates(main_window, 0.9, 0.4)

    assert main_window.edit_tabs.tabText(main_window._candidates_tab_index) == "Conferir (2)"


# ---------------------------------------------------------------------------
# A lista única (§20.4, a metade que faltava)
# ---------------------------------------------------------------------------


def _add_operation(window, tmp_path: Path):
    window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, window.current_render.matrix
    )
    window.page_widget.set_selection_rect(rect_img)
    window.board_editor.set_piece_placement(FEN)
    window._add_operation()
    return window


def test_there_is_only_one_list_of_changes(main_window) -> None:
    """A §20.4 pedia "lista única" e só metade tinha sido feita: substituições e
    apagamentos foram unificados entre si, e a `fen_ops_list` continuou ao lado, na
    aba FEN, com a sua própria seleção, o seu botão de remover e o seu atalho de
    apagar — três caminhos paralelos para o mesmo objeto."""
    assert not hasattr(main_window, "fen_ops_list"), "a segunda lista voltou"
    assert not hasattr(main_window, "btn_remove_fen")


def test_the_fen_fields_follow_the_single_list(main_window, tmp_path) -> None:
    """Sem lista própria, os campos da aba FEN têm de seguir quem está selecionado
    na lista única — senão editá-los mexeria numa substituição invisível."""
    _add_operation(main_window, tmp_path)
    main_window.operations[0].side_to_move = "b"
    main_window.operations[0].fullmove_number = 7
    # Sai da substituição e volta: adicionar já a deixa selecionada, então
    # selecioná-la de novo não é troca nenhuma e não sincronizaria nada — a
    # primeira versão deste teste passava por isso sem exercitar o caminho.
    main_window._add_eraser_from_selection()
    main_window._select_change("eraser", 0)

    main_window._select_change("operation", 0)

    assert main_window.fen_side_combo.currentData() == "b"
    assert main_window.fen_move_spin.value() == 7
    assert "001" in main_window.fen_meta_label.text()


def test_the_fen_fields_say_when_they_have_no_owner(main_window, tmp_path) -> None:
    """Um apagamento não tem FEN. Deixar os campos mostrando os da substituição
    anterior seria oferecer a edição de algo que não está selecionado."""
    _add_operation(main_window, tmp_path)
    main_window._add_eraser_from_selection()
    main_window._select_change("eraser", 0)

    assert main_window.fen_side_combo.isEnabled() is False
    assert main_window.fen_move_spin.isEnabled() is False
    assert "Nenhuma" in main_window.fen_meta_label.text()


def test_editing_the_metadata_reaches_the_selected_operation(main_window, tmp_path) -> None:
    """O caminho todo, ponta a ponta: é o que o desvio removido fazia por dentro."""
    _add_operation(main_window, tmp_path)
    main_window._select_change("operation", 0)

    main_window.fen_side_combo.setCurrentIndex(1)  # pretas
    main_window.fen_move_spin.setValue(12)

    assert main_window.operations[0].side_to_move == "b"
    assert main_window.operations[0].fullmove_number == 12
