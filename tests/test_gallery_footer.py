"""O rodapé de edição da galeria, e o link Lichess por diagrama (§52).

A galeria mostrava o livro inteiro e só servia para **achar** um diagrama: ajustar
qualquer coisa nele exigia fechar, voltar ao painel e reencontrá-lo lá. O rodapé
edita o que tem valor por substituição — lado a jogar, número do lance, link
Lichess e borda — sem sair da grade.

O link Lichess era **global**, e virar per-diagrama é uma mudança de modelo, não de
tela. O que estes testes protegem, nessa ordem de importância:

1. que `None` continua significando "segue a global" — é o que faz um projeto
   antigo exportar exatamente o mesmo PDF de antes;
2. que a escolha por diagrama chega ao PDF exportado;
3. que preencher o rodapé não escreve de volta no que acabou de ler.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, make_pdf

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.gallery import GalleryDialog, build_items  # noqa: E402
from chess_pdf_editor.pdf_service import (  # noqa: E402
    apply_operations_to_pdf,
    wants_lichess_link,
)
from chess_pdf_editor.project_state import load_project_state  # noqa: E402
from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"


def _op(page: int = 0, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=DIAGRAM_RECT, fen=FEN, **kwargs)


def _dialog(operations, candidates=(), *, pdf: Path, **kwargs) -> GalleryDialog:
    return GalleryDialog(str(pdf), operations, candidates=candidates, **kwargs)


# ---------------------------------------------------------------------------
# A regra dos três estados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("escolha", "global_", "esperado"),
    [
        (None, True, True),
        (None, False, False),
        (True, False, True),
        (False, True, False),
        (True, True, True),
        (False, False, False),
    ],
)
def test_the_per_diagram_choice_beats_the_global_one(escolha, global_, esperado) -> None:
    assert wants_lichess_link(_op(include_lichess_link=escolha), global_) is esperado


def test_an_operation_without_the_field_follows_the_global(tmp_path) -> None:
    """Compatibilidade com projeto antigo, dita como teste.

    Um `OverlayOperation` de antes do §52 não tem o atributo; o `getattr` com
    padrão é o que faz o projeto de schema 9 exportar o mesmo PDF de sempre.
    """

    class OperacaoAntiga:
        pass

    assert wants_lichess_link(OperacaoAntiga(), True) is True
    assert wants_lichess_link(OperacaoAntiga(), False) is False


def test_a_project_saved_before_the_field_existed_loads_as_no_opinion(tmp_path) -> None:
    """O teste mais importante deste arquivo, e o que quase não foi escrito.

    A rede da §45 confere que um campo **preenchido** sobrevive ao round-trip. Ela
    não olha o campo **ausente**, e é justamente aí que mora o estrago: se a leitura
    colapsasse `None` em `False` — um `bool(item.get(...))` distraído basta —, todo
    projeto de schema 9 reabriria com cada diagrama recusando o link **de propósito**
    e imune à opção global. Nada quebraria, nada avisaria, e o PDF exportado sairia
    diferente do que o usuário já tinha conferido.

    Foi uma mutação que achou o buraco: trocar `_optional_bool` por `bool(...)`
    passava por toda a suíte.
    """
    payload = {
        "schema_version": 9,
        "source_pdf": "livro.pdf",
        "source_pdf_fingerprint": {"sha256": "abc"},
        "operations": [
            {"page_num": 0, "rect_pdf": [1.0, 2.0, 3.0, 4.0], "fen": FEN}
        ],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    carregado = load_project_state(str(path))

    assert carregado.operations[0].include_lichess_link is None, (
        "ausente virou uma escolha explícita"
    )
    # E a consequência que interessa: continua obedecendo à global, nos dois sentidos.
    assert wants_lichess_link(carregado.operations[0], True) is True
    assert wants_lichess_link(carregado.operations[0], False) is False


def test_an_explicit_false_is_not_confused_with_absence(tmp_path) -> None:
    """A outra metade da mesma regra: `False` gravado é uma decisão, e tem de voltar
    como decisão."""
    payload = {
        "schema_version": 10,
        "source_pdf": "livro.pdf",
        "source_pdf_fingerprint": {"sha256": "abc"},
        "operations": [
            {
                "page_num": 0,
                "rect_pdf": [1.0, 2.0, 3.0, 4.0],
                "fen": FEN,
                "include_lichess_link": False,
            }
        ],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    operacao = load_project_state(str(path)).operations[0]

    assert operacao.include_lichess_link is False
    assert wants_lichess_link(operacao, True) is False, "a global não pode vencer a decisão"


# ---------------------------------------------------------------------------
# A escolha chega ao PDF
# ---------------------------------------------------------------------------


def _links_na_pagina(path: Path, page_num: int = 0) -> list[str]:
    doc = fitz.open(str(path))
    try:
        return [link.get("uri", "") for link in doc[page_num].get_links() if link.get("uri")]
    finally:
        doc.close()


def test_a_diagram_that_refuses_the_link_has_none_in_the_exported_pdf(tmp_path) -> None:
    origem = make_pdf(tmp_path / "livro.pdf")
    destino = tmp_path / "saida.pdf"

    apply_operations_to_pdf(
        str(origem),
        str(destino),
        [_op(include_lichess_link=False)],
        include_lichess_link=True,  # global ligada, e ainda assim sem link
    )

    assert _links_na_pagina(destino) == []


def test_a_diagram_that_asks_for_the_link_gets_it_against_the_global(tmp_path) -> None:
    origem = make_pdf(tmp_path / "livro.pdf")
    destino = tmp_path / "saida.pdf"

    apply_operations_to_pdf(
        str(origem),
        str(destino),
        [_op(include_lichess_link=True)],
        include_lichess_link=False,  # global desligada
    )

    assert any("lichess.org" in uri for uri in _links_na_pagina(destino))


def test_two_diagrams_on_the_same_page_can_disagree(tmp_path) -> None:
    """O caso que uma opção global nunca conseguiu atender, e a razão do campo."""
    origem = make_pdf(tmp_path / "livro.pdf")
    destino = tmp_path / "saida.pdf"
    outro = OverlayOperation(
        page_num=0, rect_pdf=(100.0, 90.0, 260.0, 250.0), fen=FEN, include_lichess_link=False
    )

    apply_operations_to_pdf(
        str(origem),
        str(destino),
        [_op(include_lichess_link=True), outro],
        include_lichess_link=True,
    )

    assert len([uri for uri in _links_na_pagina(destino) if "lichess.org" in uri]) == 1


# ---------------------------------------------------------------------------
# O rodapé
# ---------------------------------------------------------------------------


def test_the_footer_is_off_until_something_is_selected(qapp, tmp_path) -> None:
    dialog = _dialog([_op()], pdf=make_pdf(tmp_path / "livro.pdf"))
    try:
        assert dialog.footer_box.isEnabled() is False
        assert "Nenhum" in dialog.footer_label.text()
    finally:
        dialog.close()


def test_selecting_a_diagram_fills_the_footer_with_its_values(qapp, tmp_path) -> None:
    op = _op(side_to_move="b", fullmove_number=17, border_width_pt=1.5, include_lichess_link=False)
    dialog = _dialog([op], pdf=make_pdf(tmp_path / "livro.pdf"))
    try:
        dialog.list_widget.setCurrentRow(0)

        assert dialog.footer_box.isEnabled()
        assert dialog.side_combo.currentData() == "b"
        assert dialog.move_spin.value() == 17
        assert dialog.border_spin.value() == pytest.approx(1.5)
        assert dialog.lichess_combo.currentData() is False
        assert "001" in dialog.footer_label.text()
    finally:
        dialog.close()


def test_filling_the_footer_does_not_write_back(qapp, tmp_path) -> None:
    """A guarda `_loading_footer`, dita como teste.

    Sem ela o preenchimento dispara `valueChanged`, que grava de volta no objeto
    que acabou de ser lido e emite `entry_edited` — e a janela principal marcaria o
    projeto como alterado só porque alguém clicou numa miniatura.

    **Todos os valores abaixo diferem do padrão do widget**, e isso é o teste. A
    primeira versão usava uma operação recém-criada: `setCurrentIndex(0)` num combo
    que já está em 0 não emite sinal nenhum, então ela passava com ou sem a guarda.
    Foi uma mutação que mostrou isso.
    """
    op = _op(side_to_move="b", fullmove_number=42, border_width_pt=3.0, include_lichess_link=True)
    dialog = _dialog([op], pdf=make_pdf(tmp_path / "livro.pdf"))
    editados: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: editados.append((kind, index)))
    try:
        dialog.list_widget.setCurrentRow(0)

        assert editados == [], "selecionar não é editar"
    finally:
        dialog.close()


def test_editing_the_footer_reaches_the_real_operation(qapp, tmp_path) -> None:
    """O rodapé edita o **mesmo objeto** que a janela principal guarda — é isso que
    dispensa qualquer reconciliação depois."""
    op = _op()
    dialog = _dialog([op], pdf=make_pdf(tmp_path / "livro.pdf"))
    try:
        dialog.list_widget.setCurrentRow(0)
        dialog.side_combo.setCurrentIndex(1)
        dialog.move_spin.setValue(23)
        dialog.border_spin.setValue(2.0)
        dialog.lichess_combo.setCurrentIndex(2)  # sem link

        assert op.side_to_move == "b"
        assert op.fullmove_number == 23
        assert op.border_width_pt == pytest.approx(2.0)
        assert op.include_lichess_link is False
    finally:
        dialog.close()


def test_editing_announces_which_diagram_changed(qapp, tmp_path) -> None:
    """A janela principal precisa saber para atualizar prévia, listas e histórico."""
    dialog = _dialog([_op(), _op(1)], pdf=make_pdf(tmp_path / "livro.pdf", pages=2))
    editados: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: editados.append((kind, index)))
    try:
        dialog.list_widget.setCurrentRow(1)
        dialog.move_spin.setValue(9)

        assert editados == [("operation", 1)]
    finally:
        dialog.close()


def test_the_caption_marks_only_the_diagrams_that_disagree(qapp, tmp_path) -> None:
    """Marcar os dois casos não marcaria nada: o que se procura na grade é a
    exceção."""
    (padrao,) = build_items([_op()])
    assert "link" not in GalleryDialog._caption(padrao, _op())
    assert "sem link" in GalleryDialog._caption(padrao, _op(include_lichess_link=False))
    assert "com link" in GalleryDialog._caption(padrao, _op(include_lichess_link=True))


def test_editing_updates_the_caption_in_the_grid(qapp, tmp_path) -> None:
    op = _op()
    dialog = _dialog([op], pdf=make_pdf(tmp_path / "livro.pdf"))
    try:
        dialog.list_widget.setCurrentRow(0)
        dialog.lichess_combo.setCurrentIndex(2)  # sem link

        assert "sem link" in dialog.list_widget.item(0).text()
    finally:
        dialog.close()


def test_a_candidate_can_be_edited_too(qapp, tmp_path) -> None:
    """Candidato é a mesma classe, e o ajuste feito nele sobrevive à aplicação."""
    candidato = _op()
    dialog = _dialog([], candidates=[candidato], pdf=make_pdf(tmp_path / "livro.pdf"))
    editados: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: editados.append((kind, index)))
    try:
        dialog.list_widget.setCurrentRow(0)
        dialog.move_spin.setValue(4)

        assert candidato.fullmove_number == 4
        assert editados == [("candidate", 0)]
    finally:
        dialog.close()


def test_an_edit_during_the_initial_render_waits_its_turn(qapp, tmp_path) -> None:
    """Nunca há dois workers ao mesmo tempo.

    A justificativa que eu tinha escrito era ordem de entrega: dois workers sobre a
    mesma chave entregariam fora de ordem e o mais velho poderia vencer. A mutação
    mostrou que é pior — removendo a guarda, o **processo de teste morre** antes de
    chegar aqui. Rodado isolado, este teste falha limpo, com a mensagem certa.
    """
    dialog = _dialog([_op()], pdf=make_pdf(tmp_path / "livro.pdf"))
    try:
        durante = dialog._worker
        assert durante is not None, "o render inicial não começou"

        dialog.list_widget.setCurrentRow(0)
        dialog.move_spin.setValue(5)

        assert dialog._worker is durante, "um segundo worker começou por cima do primeiro"
        assert ("operation", 0) in dialog._dirty_keys, "a edição não ficou na fila"
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Ponta a ponta, pela janela principal
# ---------------------------------------------------------------------------


def test_a_gallery_edit_can_be_undone(main_window, tmp_path, qapp) -> None:
    """Sem o commit no histórico, um Ctrl+Z depois da edição desfaria a ação
    **anterior** e deixaria esta de pé — o pior desfazer possível."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(rect_img)
    main_window.board_editor.set_piece_placement(FEN)
    main_window._add_operation()

    main_window._open_gallery()
    dialog = main_window.gallery_dialog
    assert dialog is not None, "a galeria não abriu"
    try:
        dialog.list_widget.setCurrentRow(0)
        dialog.lichess_combo.setCurrentIndex(2)  # sem link
        qapp.processEvents()

        assert main_window.operations[0].include_lichess_link is False

        main_window._undo_change()

        assert main_window.operations[0].include_lichess_link is None, (
            "desfazer não devolveu a escolha anterior"
        )
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Aplicação em lote (§52.5)
#
# O que estes testes protegem é sobretudo o que o lote **não** faz. Um livro tem
# centenas de diagramas, e a diferença entre uma ferramenta útil e um acidente
# irrecuperável está em quantos gestos explícitos separam o usuário de carimbar
# "lance 5" em trezentas posições.
# ---------------------------------------------------------------------------


def _selecionar(dialog, *rows: int) -> None:
    dialog.list_widget.clearSelection()
    for row in rows:
        dialog.list_widget.item(row).setSelected(True)
    dialog.list_widget.setCurrentRow(rows[0])
    # `setCurrentRow` limpa a seleção múltipla; refazê-la depois é o que deixa o
    # item corrente (de onde saem os valores) dentro do lote.
    for row in rows:
        dialog.list_widget.item(row).setSelected(True)


def _tres(tmp_path):
    return [_op(0), _op(1), _op(2)], make_pdf(tmp_path / "livro.pdf", pages=3)


def test_the_batch_row_hides_until_there_is_a_batch(qapp, tmp_path) -> None:
    """Com um selecionado ela seria um segundo caminho para o que os campos de cima
    já fazem na hora."""
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        assert dialog.batch_row.isVisible() is False

        dialog.list_widget.setCurrentRow(0)
        assert dialog.batch_row.isVisibleTo(dialog.footer_box) is False, "um só não é lote"

        _selecionar(dialog, 0, 1)
        assert dialog.batch_row.isVisibleTo(dialog.footer_box) is True
    finally:
        dialog.close()


def test_the_batch_needs_a_field_ticked_before_it_can_run(qapp, tmp_path) -> None:
    """Dois gestos explícitos: escolher os diagramas e escolher os campos. Sem o
    segundo, o botão não age."""
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        _selecionar(dialog, 0, 1, 2)

        assert dialog.btn_apply_batch.isEnabled() is False
        dialog.batch_checks["lichess"].setChecked(True)
        assert dialog.btn_apply_batch.isEnabled() is True
    finally:
        dialog.close()


def test_the_batch_only_touches_the_ticked_fields(qapp, tmp_path) -> None:
    """O teste central. Marcar `Link` não pode carimbar o lance junto — é
    exatamente o acidente que as caixas existem para impedir."""
    ops, pdf = _tres(tmp_path)
    ops[1].fullmove_number = 40
    ops[2].fullmove_number = 41
    dialog = _dialog(ops, pdf=pdf)
    try:
        _selecionar(dialog, 0, 1, 2)
        dialog.move_spin.setValue(7)  # posto no rodapé, mas não marcado: não sai daqui
        dialog.lichess_combo.setCurrentIndex(2)  # sem link
        dialog.batch_checks["lichess"].setChecked(True)

        dialog.btn_apply_batch.click()

        assert [op.include_lichess_link for op in ops] == [False, False, False]
        assert [op.fullmove_number for op in ops] == [1, 40, 41], (
            "o lote mexeu num campo que não foi marcado"
        )
    finally:
        dialog.close()


def test_the_batch_leaves_unselected_diagrams_alone(qapp, tmp_path) -> None:
    """A regra da §23 aplicada aqui: ação em massa não toca no que está fora do
    alcance declarado."""
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        _selecionar(dialog, 0, 1)
        dialog.lichess_combo.setCurrentIndex(1)  # com link
        dialog.batch_checks["lichess"].setChecked(True)

        dialog.btn_apply_batch.click()

        assert [op.include_lichess_link for op in ops] == [True, True, None]
    finally:
        dialog.close()


def test_the_batch_announces_itself_once(qapp, tmp_path) -> None:
    """Um sinal, não N: do outro lado isto vira **um** passo de desfazer."""
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    lotes: list[int] = []
    singulares: list[tuple[str, int]] = []
    dialog.batch_edited.connect(lotes.append)
    dialog.entry_edited.connect(lambda kind, index: singulares.append((kind, index)))
    try:
        _selecionar(dialog, 0, 1, 2)
        dialog.batch_checks["border"].setChecked(True)
        dialog.btn_apply_batch.click()

        assert lotes == [3]
        assert singulares == [], "o lote não pode emitir o sinal de edição singular"
    finally:
        dialog.close()


def test_the_batch_updates_every_caption_it_touched(qapp, tmp_path) -> None:
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        _selecionar(dialog, 0, 1, 2)
        dialog.lichess_combo.setCurrentIndex(2)
        dialog.batch_checks["lichess"].setChecked(True)
        dialog.btn_apply_batch.click()

        textos = [dialog.list_widget.item(i).text() for i in range(3)]
        assert all("sem link" in texto for texto in textos)
    finally:
        dialog.close()


def test_building_a_selection_does_not_drag_the_main_window_along(
    qapp, tmp_path, monkeypatch
) -> None:
    """Ctrl e Shift são gestos de seleção, não de navegação.

    Sem a guarda, montar uma seleção de 20 diagramas levaria a janela principal a
    20 páginas pelo caminho — 20 renders para chegar onde nem se queria ir.

    O Qt não deixa **definir** os modificadores (só lê os reais), então o que se
    troca aqui é o leitor.
    """
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    navegou: list[tuple[str, int]] = []
    dialog.entry_activated.connect(lambda kind, index: navegou.append((kind, index)))
    try:
        monkeypatch.setattr(
            QtWidgets.QApplication,
            "keyboardModifiers",
            staticmethod(lambda: QtCore.Qt.ControlModifier),
        )
        dialog._on_item_activated(dialog.list_widget.item(1))
        assert navegou == [], "Ctrl+clique navegou"

        monkeypatch.setattr(
            QtWidgets.QApplication,
            "keyboardModifiers",
            staticmethod(lambda: QtCore.Qt.NoModifier),
        )
        dialog._on_item_activated(dialog.list_widget.item(1))
        assert navegou == [("operation", 1)], "clique simples deixou de navegar"
    finally:
        dialog.close()


def test_the_footer_stops_writing_live_when_several_are_selected(qapp, tmp_path) -> None:
    """O defeito que este teste prende foi meu, e apareceu montando o lote.

    Com vários selecionados, mexer no rodapé para preparar os valores editava de
    passagem o item corrente. O usuário ficava com **dois** passos de desfazer para
    o que fez como um — e o primeiro Ctrl+Z desfazia o lote deixando um diagrama
    alterado no meio da seleção.
    """
    ops, pdf = _tres(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    editados: list[tuple[str, int]] = []
    dialog.entry_edited.connect(lambda kind, index: editados.append((kind, index)))
    try:
        _selecionar(dialog, 0, 1, 2)
        dialog.lichess_combo.setCurrentIndex(2)
        dialog.move_spin.setValue(31)

        assert [op.include_lichess_link for op in ops] == [None, None, None]
        assert [op.fullmove_number for op in ops] == [1, 1, 1]
        assert editados == [], "preparar o lote não é editar"
    finally:
        dialog.close()


def test_a_batch_is_one_undo_step(main_window, tmp_path, qapp) -> None:
    """Ponta a ponta. N commits fariam o usuário apertar Ctrl+Z trezentas vezes
    para voltar de uma decisão que ele tomou com um clique."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    for page in range(3):
        main_window.current_page = page
        main_window.page_widget.set_selection_rect(rect_img)
        main_window.board_editor.set_piece_placement(FEN)
        main_window._add_operation()
    assert len(main_window.operations) == 3

    main_window._open_gallery()
    dialog = main_window.gallery_dialog
    assert dialog is not None
    try:
        _selecionar(dialog, 0, 1, 2)
        dialog.lichess_combo.setCurrentIndex(2)
        dialog.batch_checks["lichess"].setChecked(True)
        dialog.btn_apply_batch.click()
        qapp.processEvents()

        assert all(op.include_lichess_link is False for op in main_window.operations)

        main_window._undo_change()

        assert all(op.include_lichess_link is None for op in main_window.operations), (
            "um Ctrl+Z tem de desfazer o lote inteiro"
        )
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# O filtro (§52.6)
#
# O teste que mais importa aqui não é nenhum dos que conferem o recorte: é o
# `test_the_batch_cannot_touch_what_the_filter_hid`. Esconder no Qt **não**
# deseleciona, então sem cuidado explícito um item fora do filtro continuaria
# selecionado e entraria no lote sem aparecer na tela — o acidente que a §23
# proibiu, com o agravante de aqui ele mexer no PDF que vai ser exportado.
# ---------------------------------------------------------------------------


def _visiveis(dialog) -> list[int]:
    """Páginas (1-based) das células à vista."""
    return [
        dialog._items[row].page_num + 1
        for row in range(dialog.list_widget.count())
        if not dialog.list_widget.item(row).isHidden()
    ]


def _seis(tmp_path):
    ops = [_op(p) for p in range(6)]
    return ops, make_pdf(tmp_path / "livro.pdf", pages=6)


def test_the_page_range_cuts_the_grid(qapp, tmp_path) -> None:
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.filter_page_from.setValue(3)
        dialog.filter_page_to.setValue(5)

        assert _visiveis(dialog) == [3, 4, 5]
    finally:
        dialog.close()


def test_a_backwards_page_range_is_read_as_the_user_meant_it(qapp, tmp_path) -> None:
    """Quem digita "5 a 3" quis 3 a 5. Recusar seria transformar um engano de
    digitação numa grade vazia."""
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.filter_page_to.setValue(3)
        dialog.filter_page_from.setValue(5)

        assert _visiveis(dialog) == [3, 4, 5]
    finally:
        dialog.close()


def test_the_kind_filter_separates_applied_from_pending(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "livro.pdf", pages=4)
    dialog = _dialog([_op(0), _op(1)], candidates=[_op(2), _op(3)], pdf=pdf)
    try:
        dialog.filter_kind.setCurrentIndex(2)  # candidatos
        assert _visiveis(dialog) == [3, 4]

        dialog.filter_kind.setCurrentIndex(1)  # substituições
        assert _visiveis(dialog) == [1, 2]
    finally:
        dialog.close()


def test_the_link_filter_finds_the_exceptions(qapp, tmp_path) -> None:
    """Num livro de centenas, achar os que discordam do padrão é agulha em palheiro."""
    ops = [_op(0), _op(1, include_lichess_link=False), _op(2, include_lichess_link=True)]
    dialog = _dialog(ops, pdf=make_pdf(tmp_path / "livro.pdf", pages=3))
    try:
        dialog.filter_lichess.setCurrentIndex(1)  # segue o padrão
        assert _visiveis(dialog) == [1]

        dialog.filter_lichess.setCurrentIndex(3)  # sem link
        assert _visiveis(dialog) == [2]

        dialog.filter_lichess.setCurrentIndex(2)  # com link
        assert _visiveis(dialog) == [3]
    finally:
        dialog.close()


def test_hiding_a_diagram_also_deselects_it(qapp, tmp_path) -> None:
    """A regra, na origem: no Qt, esconder não deseleciona."""
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.list_widget.selectAll()
        assert len(dialog._selected_keys()) == 6

        dialog.filter_page_to.setValue(2)

        assert len(dialog._selected_keys()) == 2
        assert dialog.list_widget.item(5).isSelected() is False
    finally:
        dialog.close()


def test_the_batch_cannot_touch_what_the_filter_hid(qapp, tmp_path) -> None:
    """O teste mais importante do filtro, e o motivo de ele existir com cuidado.

    Sem isto, filtrar depois de selecionar deixaria itens invisíveis dentro do lote:
    o usuário veria "aplicado em 2" e teria mexido em 6 — e só descobriria no PDF
    exportado.
    """
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.list_widget.selectAll()
        dialog.filter_page_to.setValue(2)  # esconde as páginas 3 a 6
        dialog.lichess_combo.setCurrentIndex(2)  # sem link
        dialog.batch_checks["lichess"].setChecked(True)
        dialog.btn_apply_batch.click()

        assert [op.include_lichess_link for op in ops] == [False, False, None, None, None, None]
    finally:
        dialog.close()


def test_the_batch_says_how_many_it_left_out(qapp, tmp_path) -> None:
    """A outra metade da regra da §23: declarar o que ficou fora do alcance."""
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.filter_page_to.setValue(2)
        dialog.list_widget.selectAll()
        dialog.batch_checks["border"].setChecked(True)
        dialog.btn_apply_batch.click()

        assert "4 fora do filtro" in dialog.status_label.text()
    finally:
        dialog.close()


def test_the_status_says_how_much_is_hidden(qapp, tmp_path) -> None:
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        assert "6 diagrama(s)" in dialog.status_label.text()

        dialog.filter_page_to.setValue(4)

        assert "4 de 6" in dialog.status_label.text()
        assert "2 fora do filtro" in dialog.status_label.text()
    finally:
        dialog.close()


def test_show_everything_puts_the_grid_back(qapp, tmp_path) -> None:
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.filter_kind.setCurrentIndex(2)
        dialog.filter_page_from.setValue(4)
        assert _visiveis(dialog) == []

        dialog.btn_clear_filter.click()

        assert _visiveis(dialog) == [1, 2, 3, 4, 5, 6]
    finally:
        dialog.close()


def test_filtering_away_the_current_diagram_clears_the_footer(qapp, tmp_path) -> None:
    """Os campos não podem continuar oferecendo a edição de um diagrama que saiu da
    tela."""
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.list_widget.setCurrentRow(5)
        assert dialog.footer_box.isEnabled()

        dialog.filter_page_to.setValue(2)

        assert dialog.footer_box.isEnabled() is False
    finally:
        dialog.close()


def test_an_edit_does_not_make_the_diagram_vanish_under_your_hands(qapp, tmp_path) -> None:
    """Decisão registrada: o filtro **não** se reaplica sozinho depois de uma edição.

    Filtrar por "sem link" e então marcar "padrão" faria a seleção inteira sumir no
    instante do clique. A legenda se atualiza no lugar, que mostra o que aconteceu
    sem tirar da tela o que o usuário está olhando. Quem quiser o recorte novo mexe
    no filtro.
    """
    ops = [_op(0, include_lichess_link=False), _op(1, include_lichess_link=False)]
    dialog = _dialog(ops, pdf=make_pdf(tmp_path / "livro.pdf", pages=2))
    try:
        dialog.filter_lichess.setCurrentIndex(3)  # sem link
        assert _visiveis(dialog) == [1, 2]

        dialog.list_widget.setCurrentRow(0)
        dialog.lichess_combo.setCurrentIndex(0)  # padrão

        assert _visiveis(dialog) == [1, 2], "o diagrama sumiu no meio da edição"
        assert "link" not in dialog.list_widget.item(0).text(), "a legenda não acompanhou"
    finally:
        dialog.close()


def test_select_all_leaves_the_batch_usable(qapp, tmp_path) -> None:
    """`Ctrl+A` é o caminho mais natural para um lote, e era o que não funcionava.

    O grupo do rodapé tinha a habilitação presa ao item **corrente**, e `selectAll`
    seleciona sem definir um corrente: o grupo inteiro nascia desabilitado por
    herança, e com ele o botão do lote que mora lá dentro. A linha aparecia e não
    se deixava clicar.
    """
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.list_widget.selectAll()
        dialog.batch_checks["lichess"].setChecked(True)
        dialog.lichess_combo.setCurrentIndex(2)

        assert dialog.footer_box.isEnabled(), "o rodapé ficou desabilitado com tudo selecionado"
        assert dialog.btn_apply_batch.isEnabled()

        dialog.btn_apply_batch.click()
        assert all(op.include_lichess_link is False for op in ops)
    finally:
        dialog.close()


def test_a_hidden_item_stays_out_of_the_batch_even_if_still_selected(qapp, tmp_path) -> None:
    """A segunda rede, testada sozinha.

    São dois mecanismos para a mesma regra: `_apply_filter` deseleciona o que
    esconde, e `_selected_keys` ignora o que está escondido. Cada um sozinho já
    bastaria — e é por isso que uma mutação em qualquer um deles passa pelo teste do
    outro. Aqui a segunda é exercitada direto, escondendo pela API do Qt sem
    deselecionar, que é o que um caminho futuro faria por engano.
    """
    ops, pdf = _seis(tmp_path)
    dialog = _dialog(ops, pdf=pdf)
    try:
        dialog.list_widget.selectAll()
        # Escondido **e** ainda selecionado: o estado que não pode chegar ao lote.
        dialog.list_widget.item(4).setHidden(True)
        dialog.list_widget.item(5).setHidden(True)
        assert dialog.list_widget.item(5).isSelected(), "o Qt mudou: esconder deselecionou"

        assert dialog._selected_keys() == [("operation", i) for i in range(4)]

        dialog.batch_checks["lichess"].setChecked(True)
        dialog.lichess_combo.setCurrentIndex(2)
        dialog.btn_apply_batch.click()

        assert [op.include_lichess_link for op in ops] == [False] * 4 + [None, None]
    finally:
        dialog.close()
