"""Detecção de diagrama por clique único (§38).

As fixtures são os diagramas **reais** de `tests/data/local_ocr/`, e não um tabuleiro
desenhado por nós, pelo mesmo motivo que o `test_local_ocr` já registra: o renderer do
app desenha as casas escuras hachuradas e sem moldura, um estilo que não existe em
livro nenhum — o detector por contorno não acha nada nele. Conferido: numa página com
dois tabuleiros do nosso renderer, `detect_board_rects` devolve zero.

Tudo aqui pula sem o detector local (OpenCV) instalado, que é a instalação em que o
clique único simplesmente não faz nada.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chess_pdf_editor import local_ocr

pytestmark = pytest.mark.skipif(
    not local_ocr.dependencies_available(),
    reason=local_ocr.unavailable_reason() or "detector local indisponível",
)

fitz = pytest.importorskip("fitz")

from chess_pdf_editor.local_ocr.engine import (  # noqa: E402
    board_rect_at,
    detect_board_rects,
)

DATA_DIR = Path(__file__).parent / "data" / "local_ocr"
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
#: Onde cada diagrama é colado, em pontos PDF.
FIRST_BOARD = (70.0, 260.0, 270.0, 460.0)
SECOND_BOARD = (330.0, 560.0, 510.0, 740.0)
ZOOM = 2.0
#: Quanto o retângulo devolvido pode diferir do lugar onde o diagrama foi colado.
#: A borda detectada é a do desenho, que não é exatamente a caixa da imagem.
TOLERANCE_PX = 12.0


def _make_page(path: Path, boards=(FIRST_BOARD, SECOND_BOARD), pages: int = 1) -> Path:
    """PDF de livro: texto em volta e diagramas reais colados."""
    names = ("board_1.png", "board_2.png")
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        for line in range(8):
            page.insert_text(
                fitz.Point(60, 60 + line * 16), "texto do livro " * 4, fontsize=10
            )
        for index, rect in enumerate(boards):
            page.insert_image(
                fitz.Rect(*rect),
                stream=(DATA_DIR / names[index % len(names)]).read_bytes(),
                keep_proportion=False,
            )
    doc.save(str(path))
    doc.close()
    return path


def _page_png(path: Path) -> bytes:
    doc = fitz.open(str(path))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB)
    png = pix.tobytes("png")
    doc.close()
    return png


def _expected(rect_pdf) -> tuple[float, float, float, float]:
    return tuple(value * ZOOM for value in rect_pdf)


def _center(rect_pdf) -> tuple[float, float]:
    x0, y0, x1, y1 = _expected(rect_pdf)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _close(got, expected, tolerance: float = TOLERANCE_PX) -> bool:
    return all(abs(float(got[i]) - float(expected[i])) <= tolerance for i in range(4))


@pytest.fixture(scope="module")
def book_png(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("clickdetect") / "book.pdf"
    return _page_png(_make_page(path))


# ---------------------------------------------------------------------------
# O detector, sozinho
# ---------------------------------------------------------------------------


def test_both_diagrams_of_the_page_are_found(book_png) -> None:
    rects = detect_board_rects(book_png)

    assert len(rects) == 2, f"esperava dois tabuleiros, veio {rects}"
    ordered = sorted(rects, key=lambda rect: rect[1])
    assert _close(ordered[0], _expected(FIRST_BOARD))
    assert _close(ordered[1], _expected(SECOND_BOARD))


def test_a_page_of_only_text_has_no_boards(tmp_path) -> None:
    png = _page_png(_make_page(tmp_path / "so-texto.pdf", boards=()))
    assert detect_board_rects(png) == []


@pytest.mark.parametrize("board", [FIRST_BOARD, SECOND_BOARD])
def test_a_click_inside_a_diagram_finds_that_diagram(book_png, board) -> None:
    found = board_rect_at(book_png, _center(board))

    assert found is not None
    assert _close(found, _expected(board))


def test_a_click_on_the_text_finds_nothing(book_png) -> None:
    """O clique perdido não pode arrastar a seleção para um diagrama distante."""
    assert board_rect_at(book_png, (200.0, 130.0)) is None


def test_a_click_that_grazed_the_border_still_counts(book_png) -> None:
    """Exigir acerto dentro de uma borda de 2 px seria exigir mão de cirurgião."""
    x0, y0, _x1, _y1 = _expected(FIRST_BOARD)
    found = board_rect_at(book_png, (x0 - 6.0, y0 + 40.0))

    assert found is not None
    assert _close(found, _expected(FIRST_BOARD))


def test_a_click_far_outside_does_not_snap_to_a_distant_diagram(book_png) -> None:
    x0, y0, _x1, _y1 = _expected(FIRST_BOARD)
    assert board_rect_at(book_png, (x0 - 200.0, y0 - 200.0)) is None


def test_the_tighter_rectangle_wins_when_two_contain_the_point(monkeypatch) -> None:
    """Moldura dentro de moldura: a borda justa do tabuleiro é a menor."""
    from chess_pdf_editor.local_ocr import engine

    outer = (0.0, 0.0, 400.0, 400.0)
    inner = (100.0, 100.0, 300.0, 300.0)
    monkeypatch.setattr(engine, "detect_board_rects", lambda *a, **k: [outer, inner])

    assert engine.board_rect_at(b"nao importa", (200.0, 200.0)) == inner


# ---------------------------------------------------------------------------
# Na janela
# ---------------------------------------------------------------------------


def _click_at(window, rect_pdf) -> None:
    x0, y0, x1, y1 = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page, rect_pdf, window.current_render.matrix
    )
    window._on_page_clicked(((x0 + x1) / 2.0, (y0 + y1) / 2.0))


def _open_book(window, tmp_path) -> None:
    window._open_pdf(str(_make_page(tmp_path / "book.pdf")), clear_ops=True)


def _selection_matches(window, rect_pdf, tolerance: float = TOLERANCE_PX) -> bool:
    selection = window.page_widget.selection_rect()
    assert selection is not None, "nenhuma seleção foi criada"
    expected = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page, rect_pdf, window.current_render.matrix
    )
    return _close(selection, expected, tolerance)


def test_clicking_a_diagram_selects_its_borders(main_window, tmp_path) -> None:
    _open_book(main_window, tmp_path)
    assert main_window.page_widget.selection_rect() is None

    _click_at(main_window, FIRST_BOARD)

    assert _selection_matches(main_window, FIRST_BOARD)
    assert "detectado" in main_window.statusBar().currentMessage()


def test_clicking_the_other_diagram_selects_that_one(main_window, tmp_path) -> None:
    _open_book(main_window, tmp_path)

    _click_at(main_window, FIRST_BOARD)
    _click_at(main_window, SECOND_BOARD)

    assert _selection_matches(main_window, SECOND_BOARD)


def test_clicking_the_text_creates_no_selection(main_window, tmp_path) -> None:
    _open_book(main_window, tmp_path)

    main_window._on_page_clicked((200.0, 130.0))

    assert main_window.page_widget.selection_rect() is None


def test_the_preference_turns_the_detection_off(main_window, tmp_path) -> None:
    _open_book(main_window, tmp_path)
    main_window.click_detects_check.setChecked(False)

    _click_at(main_window, FIRST_BOARD)

    assert main_window.page_widget.selection_rect() is None
    assert main_window.settings.value("click_detects_diagram", True, bool) is False


def test_the_preference_is_on_by_default(main_window) -> None:
    assert main_window.click_detects_diagram is True
    assert main_window.click_detects_check.isChecked() is True


def test_clicking_a_second_diagram_does_not_reuse_the_first_position(
    main_window, tmp_path
) -> None:
    """A garantia da §21.5, agora pelo caminho do clique.

    Detectar a área não faz a posição carregada no editor pertencer a ela; se
    fizesse, a prévia desenharia o diagrama anterior sobre este.
    """
    _open_book(main_window, tmp_path)
    _click_at(main_window, FIRST_BOARD)
    # Uma posição passa a pertencer ao primeiro diagrama.
    main_window.board_editor.set_piece_placement("8/8/8/4k3/8/8/4K3/8")
    assert main_window._draft_operation() is not None

    _click_at(main_window, SECOND_BOARD)

    assert _selection_matches(main_window, SECOND_BOARD)
    assert main_window._draft_operation() is None, "a FEN do primeiro vazou para o segundo"
