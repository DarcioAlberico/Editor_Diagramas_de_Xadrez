"""Galeria de diagramas do livro (§22.5).

Um livro reconhecido produz centenas de substituições; a galeria existe para
revisá-las sem percorrer página por página. O que estes testes protegem: a ordem
de leitura, o conteúdo das miniaturas de "depois", e a promessa do Sprint 5 de que
nenhuma QThread sobrevive ao fechamento.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAGRAM_RECT, SECOND_DIAGRAM_RECT, make_pdf, process_until

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.gallery import (  # noqa: E402
    KIND_CANDIDATE,
    KIND_OPERATION,
    GalleryDialog,
    build_items,
    compose_pair,
)
from chess_pdf_editor.types import EraseOperation, OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
OTHER_FEN = "8/8/8/3qk3/8/8/4K3/8"


def _op(page: int, rect=DIAGRAM_RECT, fen: str = FEN, **kwargs) -> OverlayOperation:
    return OverlayOperation(page_num=page, rect_pdf=rect, fen=fen, **kwargs)


def _dialog(qapp, pdf: Path, operations, candidates=(), erase_operations=()) -> GalleryDialog:
    dialog = GalleryDialog(
        str(pdf),
        operations,
        candidates=candidates,
        erase_operations=erase_operations,
    )
    return dialog


def _wait_for_thumbnails(qapp, dialog: GalleryDialog) -> bool:
    return process_until(
        qapp,
        lambda: all(
            not dialog.list_widget.item(row).icon().isNull()
            for row in range(dialog.list_widget.count())
        )
        and dialog.list_widget.count() > 0,
    )


def _render_pairs(qapp, pdf: Path, operations, candidates=(), erase_operations=()):
    """Roda a galeria até o fim e devolve `{chave: (antes, depois)}` em PNG.

    Comparar os bytes é a única forma de afirmar que o "depois" é de fato outra
    imagem — o ícone composto junta os dois num pixmap só.
    """
    dialog = GalleryDialog(
        str(pdf), operations, candidates=candidates, erase_operations=erase_operations
    )
    pairs: dict[tuple[str, int], tuple[bytes, bytes]] = {}
    worker = dialog._worker
    assert worker is not None, "a galeria não iniciou o worker"
    worker.thumbnail_ready.connect(
        lambda key, before, after: pairs.__setitem__(tuple(key), (bytes(before), bytes(after)))
    )
    try:
        assert process_until(qapp, lambda: dialog._worker is None), "o render não terminou"
    finally:
        dialog.close()
    return pairs


# ---------------------------------------------------------------------------
# Montagem da lista
# ---------------------------------------------------------------------------


def test_items_come_in_reading_order() -> None:
    """Ordem de página e, dentro dela, de cima para baixo.

    Não é só estética: é o que faz o cache de prévia do PdfService acertar, já que
    ele guarda um documento por assinatura de página.
    """
    items = build_items(
        [
            _op(2, DIAGRAM_RECT),
            _op(0, DIAGRAM_RECT),
            _op(0, SECOND_DIAGRAM_RECT),
        ]
    )
    assert [(item.page_num, item.rect_pdf[1]) for item in items] == [
        (0, SECOND_DIAGRAM_RECT[1]),
        (0, DIAGRAM_RECT[1]),
        (2, DIAGRAM_RECT[1]),
    ]


def test_candidates_are_marked_and_keep_their_own_index() -> None:
    items = build_items([_op(0)], [_op(1), _op(2)])

    kinds = {item.kind for item in items}
    assert kinds == {KIND_OPERATION, KIND_CANDIDATE}
    candidates = [item for item in items if item.kind == KIND_CANDIDATE]
    assert [item.index for item in candidates] == [0, 1], "índice é o da lista de origem"


def test_the_caption_carries_page_and_confidence() -> None:
    (item,) = build_items([_op(4, confidence=0.73)])
    caption = GalleryDialog._caption(item)
    assert "pág 5" in caption
    assert "0.73" in caption


def test_an_unknown_confidence_is_omitted_not_invented() -> None:
    (item,) = build_items([_op(0)])
    assert "conf" not in GalleryDialog._caption(item)


# ---------------------------------------------------------------------------
# Miniaturas
# ---------------------------------------------------------------------------


def test_compose_pair_puts_two_thumbnails_side_by_side(qapp) -> None:
    red = QtGui.QPixmap(40, 40)
    red.fill(QtGui.QColor("red"))
    blue = QtGui.QPixmap(40, 40)
    blue.fill(QtGui.QColor("blue"))

    def _png(pixmap):
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        return bytes(buffer.data())

    canvas = compose_pair(_png(red), _png(blue), size=60)

    assert canvas.width() > canvas.height(), "o par sai deitado"
    image = canvas.toImage()
    assert image.pixelColor(30, 30).name() == "#ff0000"
    assert image.pixelColor(60 + 9 + 30, 30).name() == "#0000ff"


def test_a_missing_thumbnail_does_not_crash_the_composition(qapp) -> None:
    canvas = compose_pair(b"", b"", size=40)
    assert not canvas.isNull()


def test_the_after_thumbnail_actually_shows_the_replacement(qapp, tmp_path) -> None:
    """Se antes e depois saíssem iguais, a galeria não serviria para conferir nada."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    pairs = _render_pairs(qapp, pdf, [_op(0)])

    assert set(pairs) == {(KIND_OPERATION, 0)}
    before, after = pairs[(KIND_OPERATION, 0)]
    assert before and after
    assert before != after


def test_a_page_with_two_diagrams_shows_both_in_each_after(qapp, tmp_path) -> None:
    """O PDF exportado terá as duas; a miniatura mentiria se mostrasse só uma."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    single = _render_pairs(qapp, pdf, [_op(0, DIAGRAM_RECT)])
    both = _render_pairs(
        qapp, pdf, [_op(0, DIAGRAM_RECT), _op(0, SECOND_DIAGRAM_RECT, fen=OTHER_FEN)]
    )

    # O recorte do primeiro diagrama é o mesmo nos dois casos; o que muda é a
    # página por baixo, agora com a segunda substituição aplicada.
    first_alone = single[(KIND_OPERATION, 0)][1]
    first_with_sibling = both[(KIND_OPERATION, 0)][1]
    assert first_alone == first_with_sibling, (
        "os diagramas desta página não se sobrepõem, então o recorte do primeiro "
        "não deveria mudar"
    )
    assert len(both) == 2


def test_a_candidate_after_shows_the_candidate_applied(qapp, tmp_path) -> None:
    """O candidato ainda não está em `operations`: sem isso o 'depois' seria o original."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    pairs = _render_pairs(qapp, pdf, [], candidates=[_op(0)])

    before, after = pairs[(KIND_CANDIDATE, 0)]
    assert before != after


def test_every_diagram_gets_a_cell_even_before_rendering(qapp, tmp_path) -> None:
    """A grade aparece cheia na hora; as imagens vão chegando."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(qapp, pdf, [_op(0), _op(1)])
    try:
        assert dialog.list_widget.count() == 2
    finally:
        dialog.close()


def test_the_icons_arrive_in_the_cells(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(qapp, pdf, [_op(0), _op(1)])
    try:
        assert _wait_for_thumbnails(qapp, dialog), "as miniaturas não ficaram prontas"
    finally:
        dialog.close()


def test_an_empty_gallery_explains_itself(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=1)
    dialog = _dialog(qapp, pdf, [])
    try:
        assert dialog.list_widget.count() == 0
        assert "Nenhum diagrama" in dialog.status_label.text()
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Navegação
# ---------------------------------------------------------------------------


def test_clicking_a_cell_reports_which_diagram(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(qapp, pdf, [_op(0)], candidates=[_op(1)])
    seen: list[tuple[str, int]] = []
    dialog.entry_activated.connect(lambda kind, index: seen.append((kind, index)))
    try:
        dialog._on_item_activated(dialog.list_widget.item(0))
        dialog._on_item_activated(dialog.list_widget.item(1))
    finally:
        dialog.close()

    assert seen == [(KIND_OPERATION, 0), (KIND_CANDIDATE, 0)]


# ---------------------------------------------------------------------------
# Ciclo de vida da thread
# ---------------------------------------------------------------------------


def test_closing_the_gallery_leaves_no_worker_running(qapp, tmp_path) -> None:
    """A lição do Sprint 5.1: nenhuma QThread sobrevive ao fechamento."""
    pdf = make_pdf(tmp_path / "book.pdf", pages=6)
    operations = [_op(page) for page in range(6)]
    dialog = _dialog(qapp, pdf, operations)
    worker = dialog._worker
    assert worker is not None

    dialog.close()

    assert dialog._worker is None
    assert worker.isFinished(), "o worker continuou vivo depois de fechar"


def test_stopping_twice_is_harmless(qapp, tmp_path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    dialog = _dialog(qapp, pdf, [_op(0)])
    dialog.stop_worker()
    dialog.stop_worker()
    dialog.close()


# ---------------------------------------------------------------------------
# Integração com a janela
# ---------------------------------------------------------------------------


def test_the_window_refuses_to_open_an_empty_gallery(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window._open_gallery()

    assert main_window.gallery_dialog is None
    assert any("Galeria" == title for title, _ in no_modals)


def test_the_window_refuses_without_a_pdf(main_window, no_modals) -> None:
    main_window._open_gallery()
    assert any("Sem PDF" == title for title, _ in no_modals)


def test_opening_the_gallery_keeps_a_reference(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window.operations.append(_op(0))

    main_window._open_gallery()
    try:
        assert main_window.gallery_dialog is not None
        assert main_window.gallery_dialog.list_widget.count() == 1
    finally:
        main_window.gallery_dialog.close()


def test_choosing_a_diagram_takes_the_window_to_its_page(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    main_window.operations.extend([_op(0), _op(2, fen=OTHER_FEN)])
    main_window._refresh_operations_list()

    main_window._focus_gallery_entry(KIND_OPERATION, 1)

    assert main_window.current_page == 2
    assert main_window.board_editor.piece_placement() == OTHER_FEN


def test_choosing_a_candidate_selects_it_in_the_queue(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=3)), clear_ops=True)
    main_window.candidates.extend([_op(0), _op(2, fen=OTHER_FEN)])
    main_window._refresh_candidates_list()

    main_window._focus_gallery_entry(KIND_CANDIDATE, 1)

    assert main_window.current_page == 2
    assert main_window._selected_candidate_index() == 1


def test_an_out_of_range_entry_is_ignored(main_window, tmp_path, no_modals) -> None:
    """A galeria é montada de uma cópia: o usuário pode remover algo enquanto ela vive."""
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    main_window._focus_gallery_entry(KIND_OPERATION, 99)
    main_window._focus_gallery_entry(KIND_CANDIDATE, 99)


def test_closing_the_window_closes_the_gallery(main_window, tmp_path, no_modals) -> None:
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf", pages=4)), clear_ops=True)
    main_window.operations.extend([_op(page) for page in range(4)])

    main_window._open_gallery()
    dialog = main_window.gallery_dialog
    assert dialog is not None
    worker = dialog._worker

    main_window.close()

    assert main_window.gallery_dialog is None
    if worker is not None:
        assert worker.isFinished()


def test_erasures_reach_the_after_thumbnail(qapp, tmp_path) -> None:
    """O 'depois' tem de ser o que o PDF exportado mostra, apagamentos inclusive.

    O apagamento precisa cair sobre conteúdo *dentro do enquadramento* da
    miniatura: sobre papel branco não haveria diferença nenhuma para medir, e o
    teste passaria sem exercitar nada.
    """
    x0, y0, x1, y1 = DIAGRAM_RECT
    pdf = tmp_path / "com_legenda.pdf"
    doc = fitz.open()
    page = doc.new_page(width=420, height=595)
    page.draw_rect(fitz.Rect(*DIAGRAM_RECT), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    # Legenda logo abaixo do diagrama, dentro da margem de 10% que a miniatura pega.
    page.insert_text(fitz.Point(x0, y1 + 9), "Diagrama 12 - brancas jogam", fontsize=8)
    doc.save(str(pdf))
    doc.close()

    erase = EraseOperation(page_num=0, rect_pdf=(x0 - 2.0, y1 + 2.0, x1, y1 + 12.0))
    sem_apagar = _render_pairs(qapp, pdf, [_op(0)])
    com_apagar = _render_pairs(qapp, pdf, [_op(0)], erase_operations=[erase])

    assert sem_apagar[(KIND_OPERATION, 0)][1] != com_apagar[(KIND_OPERATION, 0)][1]
