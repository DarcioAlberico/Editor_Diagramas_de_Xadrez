"""Apagamento das coordenadas do diagrama original.

O diagrama do livro traz `a`-`h` embaixo e `1`-`8` na lateral. O whiteout cobre o
tabuleiro e um padding pequeno; as coordenadas ficam fora dele e emolduram o
diagrama novo com as letrinhas do antigo.

A detecção é de propósito conservadora: apagar texto do livro por engano é muito
pior que deixar uma letrinha. Metade destes testes existe para provar o que ela
**não** apaga.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from chess_pdf_editor.pdf_service import (  # noqa: E402
    apply_operations_to_pdf,
    find_coordinate_labels,
)
from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
PAGE_W, PAGE_H = 420.0, 595.0
BOARD = (120.0, 200.0, 280.0, 360.0)
CELL = (BOARD[2] - BOARD[0]) / 8.0


def _page_with_coordinates(extra: dict[tuple[float, float], str] | None = None):
    """Página com o diagrama e as coordenadas em volta, como num livro."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(fitz.Rect(*BOARD), color=(0, 0, 0), fill=(0.75, 0.75, 0.75))
    for index, letter in enumerate("abcdefgh"):
        x = BOARD[0] + CELL * (index + 0.5) - 2.0
        page.insert_text(fitz.Point(x, BOARD[3] + 8.0), letter, fontsize=7)
    for index, digit in enumerate("87654321"):
        y = BOARD[1] + CELL * (index + 0.5) + 2.0
        page.insert_text(fitz.Point(BOARD[0] - 8.0, y), digit, fontsize=7)
    for (x, y), text in (extra or {}).items():
        page.insert_text(fitz.Point(x, y), text, fontsize=9)
    return doc, page


def _labels(extra=None) -> list[str]:
    doc, page = _page_with_coordinates(extra)
    try:
        rects = find_coordinate_labels(page, BOARD)
        found = []
        for rect in rects:
            found.extend(
                word[4]
                for word in page.get_text("words")
                if not (fitz.Rect(word[0], word[1], word[2], word[3]) & rect).is_empty
            )
        return sorted(set(found))
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# O que ela acha
# ---------------------------------------------------------------------------


def test_all_sixteen_coordinates_are_found() -> None:
    assert _labels() == sorted(set("abcdefgh") | set("12345678"))


def test_the_boxes_stay_outside_the_board() -> None:
    doc, page = _page_with_coordinates()
    try:
        board = fitz.Rect(*BOARD)
        for rect in find_coordinate_labels(page, BOARD):
            assert (rect & board).get_area() == pytest.approx(0.0, abs=2.0)
    finally:
        doc.close()


def test_nothing_is_found_around_a_bare_diagram() -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(fitz.Rect(*BOARD), color=(0, 0, 0), fill=(0.75, 0.75, 0.75))
    try:
        assert find_coordinate_labels(page, BOARD) == []
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# O que ela NÃO pode apagar
# ---------------------------------------------------------------------------


def test_a_caption_is_left_alone() -> None:
    """`Diagrama 12` tem um `1` e um `2`, mas não como palavras de um caractere."""
    achados = _labels({(125.0, BOARD[3] + 22.0): "Diagrama 12 - brancas jogam"})
    assert "Diagrama" not in achados
    assert "12" not in achados


def test_a_page_number_far_below_is_left_alone() -> None:
    """O `8` do rodapé está alinhado com o tabuleiro em x, mas longe demais."""
    achados = _labels({(200.0, PAGE_H - 30.0): "8"})
    # Os oito dígitos das fileiras continuam sendo achados; o do rodapé não pode
    # acrescentar um nono retângulo.
    doc, page = _page_with_coordinates({(200.0, PAGE_H - 30.0): "8"})
    try:
        assert len(find_coordinate_labels(page, BOARD)) == 16
    finally:
        doc.close()
    assert achados


def test_a_move_number_beside_the_board_is_left_alone() -> None:
    """Um dígito solto na faixa lateral, mas fora do alinhamento vertical."""
    doc, page = _page_with_coordinates({(BOARD[0] - 9.0, BOARD[1] - 40.0): "5"})
    try:
        assert len(find_coordinate_labels(page, BOARD)) == 16
    finally:
        doc.close()


def test_a_letter_far_from_the_board_is_left_alone() -> None:
    doc, page = _page_with_coordinates({(40.0, 100.0): "e"})
    try:
        assert len(find_coordinate_labels(page, BOARD)) == 16
    finally:
        doc.close()


def test_letters_outside_the_chess_range_are_left_alone() -> None:
    """`i`, `z`, `9` e `0` não são coordenadas de tabuleiro."""
    doc, page = _page_with_coordinates()
    try:
        base = len(find_coordinate_labels(page, BOARD))
    finally:
        doc.close()

    doc, page = _page_with_coordinates(
        {
            (BOARD[0] + 20.0, BOARD[3] + 18.0): "i",
            (BOARD[0] + 40.0, BOARD[3] + 18.0): "z",
            (BOARD[0] - 8.0, BOARD[1] + 20.0): "9",
            (BOARD[0] - 8.0, BOARD[1] + 40.0): "0",
        }
    )
    try:
        assert len(find_coordinate_labels(page, BOARD)) == base
    finally:
        doc.close()


def test_an_empty_rect_finds_nothing() -> None:
    doc, page = _page_with_coordinates()
    try:
        assert find_coordinate_labels(page, (10.0, 10.0, 10.0, 10.0)) == []
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# No PDF exportado
# ---------------------------------------------------------------------------


def _export(tmp_path: Path, erase_coordinates: bool, extra=None) -> fitz.Document:
    doc, _page = _page_with_coordinates(extra)
    source = tmp_path / "livro.pdf"
    doc.save(str(source))
    doc.close()

    out = tmp_path / f"saida_{erase_coordinates}.pdf"
    apply_operations_to_pdf(
        str(source),
        str(out),
        [OverlayOperation(page_num=0, rect_pdf=BOARD, fen=FEN)],
        whiteout=True,
        include_lichess_link=False,
        erase_coordinates=erase_coordinates,
    )
    return fitz.open(str(out))


def test_by_default_the_coordinates_survive(tmp_path: Path) -> None:
    """Sem pedir, nada muda: quem tem projeto antigo não pode ver o PDF mudar sozinho."""
    doc = _export(tmp_path, erase_coordinates=False)
    try:
        texto = doc[0].get_text("text")
        assert "a" in texto and "8" in texto
    finally:
        doc.close()


def _labels_around_the_board(page) -> list[str]:
    """Palavras de um caractere que sobraram FORA do tabuleiro.

    A checagem não pode ser "a página ficou sem texto": o diagrama que inserimos
    é vetorial com a fonte Merida, que mapeia as peças em letras ASCII (§22.2), e
    portanto contribui com `+`, `L`, `k`... para a extração de texto. O que
    importa é o que sobrou em volta.
    """
    board = fitz.Rect(*BOARD)
    return sorted(
        {
            str(word[4]).strip()
            for word in page.get_text("words")
            if len(str(word[4]).strip()) == 1
            and (fitz.Rect(word[0], word[1], word[2], word[3]) & board).is_empty
        }
    )


def test_with_the_option_on_they_are_gone(tmp_path: Path) -> None:
    doc = _export(tmp_path, erase_coordinates=True)
    try:
        assert _labels_around_the_board(doc[0]) == []
    finally:
        doc.close()


def test_the_same_page_without_the_option_keeps_them(tmp_path: Path) -> None:
    """Contraprova do teste acima: sem a opção, as 16 continuam lá."""
    doc = _export(tmp_path, erase_coordinates=False)
    try:
        assert set(_labels_around_the_board(doc[0])) >= set("abcdefgh")
    finally:
        doc.close()


def test_the_book_text_survives_the_erasure(tmp_path: Path) -> None:
    """A promessa: só as coordenadas somem."""
    doc = _export(
        tmp_path,
        erase_coordinates=True,
        extra={(125.0, BOARD[3] + 24.0): "Diagrama 12 - brancas jogam e ganham"},
    )
    try:
        texto = doc[0].get_text("text")
        assert "Diagrama 12" in texto
        assert "brancas jogam e ganham" in texto
    finally:
        doc.close()
