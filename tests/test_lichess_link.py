"""Posicionamento do rótulo Lichess no PDF exportado (§22.4).

O que estes testes protegem: o arquivo entregue ao usuário. A versão anterior
escrevia `Lichess` logo abaixo do diagrama sem olhar o que havia ali — e é
justamente onde o livro põe a legenda ("Diagrama 12", "as brancas jogam").
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from chess_pdf_editor.pdf_service import (  # noqa: E402
    LINK_TEXT,
    apply_operations_to_pdf,
)
from chess_pdf_editor.types import OverlayOperation  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
PAGE_W, PAGE_H = 420.0, 595.0
DIAGRAM = (100.0, 200.0, 260.0, 360.0)


def _make_pdf(path: Path, captions: dict[tuple[float, float], str] | None = None) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(fitz.Rect(*DIAGRAM), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    for (x, y), text in (captions or {}).items():
        page.insert_text(fitz.Point(x, y), text, fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


def _export(tmp_path: Path, source: Path, whiteout: bool = True) -> fitz.Document:
    out = tmp_path / "saida.pdf"
    apply_operations_to_pdf(
        str(source),
        str(out),
        [OverlayOperation(page_num=0, rect_pdf=DIAGRAM, fen=FEN)],
        whiteout=whiteout,
        include_lichess_link=True,
    )
    return fitz.open(str(out))


def _link_rects(page) -> list[fitz.Rect]:
    return [fitz.Rect(link["from"]) for link in page.get_links() if link.get("uri")]


def _label_count(page) -> int:
    return len(page.search_for(LINK_TEXT))


# ---------------------------------------------------------------------------
# Caminho normal
# ---------------------------------------------------------------------------


def test_the_label_goes_below_a_diagram_with_room(tmp_path: Path) -> None:
    doc = _export(tmp_path, _make_pdf(tmp_path / "livre.pdf"))
    try:
        page = doc[0]
        assert _label_count(page) == 1
        (rect,) = _link_rects(page)
        assert rect.y0 >= DIAGRAM[3], "o rótulo deveria ficar abaixo do diagrama"
    finally:
        doc.close()


def test_the_link_points_at_the_position(tmp_path: Path) -> None:
    doc = _export(tmp_path, _make_pdf(tmp_path / "livre.pdf"))
    try:
        uris = [link["uri"] for link in doc[0].get_links() if link.get("uri")]
        assert len(uris) == 1
        assert uris[0].startswith("https://lichess.org/analysis/")
        assert "4k3" in uris[0]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Colisão com o texto do livro
# ---------------------------------------------------------------------------


# O rótulo sai centralizado sob o diagrama (x ~158..202). Uma legenda curta e
# alinhada à esquerda não encosta nele — e o teste que usasse uma passaria sem
# exercitar colisão nenhuma. Estas são largas o bastante para cruzar essa faixa.
CAPTION_BELOW = "Diagrama 12 - as brancas jogam e ganham"
CAPTION_ABOVE = "Capitulo 3 - finais de torre e o problema da oposicao"


def test_a_caption_below_pushes_the_label_above(tmp_path: Path) -> None:
    """O caso real: legenda logo abaixo do diagrama."""
    source = _make_pdf(tmp_path / "legenda.pdf", {(105.0, DIAGRAM[3] + 12.0): CAPTION_BELOW})
    doc = _export(tmp_path, source, whiteout=False)
    try:
        page = doc[0]
        assert _label_count(page) == 1, "o rótulo sumiu ou saiu duplicado"
        (rect,) = _link_rects(page)
        assert rect.y1 <= DIAGRAM[1], "o rótulo deveria ter subido para cima do diagrama"
        assert "brancas jogam" in page.get_text("text"), "a legenda do livro foi destruída"
    finally:
        doc.close()


def test_with_no_free_space_the_diagram_itself_becomes_the_link(tmp_path: Path) -> None:
    """Nada visível é escrito, mas o link continua existindo."""
    source = _make_pdf(
        tmp_path / "cercado.pdf",
        {
            (105.0, DIAGRAM[3] + 12.0): CAPTION_BELOW,
            (105.0, DIAGRAM[1] - 6.0): CAPTION_ABOVE,
        },
    )
    doc = _export(tmp_path, source, whiteout=False)
    try:
        page = doc[0]
        assert _label_count(page) == 0, "não podia ter escrito rótulo nenhum"
        (rect,) = _link_rects(page)
        assert rect.x0 == pytest.approx(DIAGRAM[0], abs=1.0)
        assert rect.y1 == pytest.approx(DIAGRAM[3], abs=1.0)
        texto = page.get_text("text")
        assert "brancas jogam" in texto
        assert "finais de torre" in texto
    finally:
        doc.close()


def test_text_erased_by_the_whiteout_does_not_block_the_label(tmp_path: Path) -> None:
    """A checagem roda depois das redações: o que o whiteout apagou não conta."""
    # Legenda dentro da margem que o whiteout limpa (padding padrão de 0,5 pt não
    # alcança, então usa-se um padding grande na própria operação).
    source = _make_pdf(tmp_path / "apagada.pdf", {(105.0, DIAGRAM[3] + 6.0): "legenda"})
    out = tmp_path / "saida.pdf"
    apply_operations_to_pdf(
        str(source),
        str(out),
        [
            OverlayOperation(
                page_num=0,
                rect_pdf=DIAGRAM,
                fen=FEN,
                whiteout_padding_bottom_pt=14.0,
            )
        ],
        whiteout=True,
        include_lichess_link=True,
    )
    doc = fitz.open(str(out))
    try:
        page = doc[0]
        assert "legenda" not in page.get_text("text"), "o whiteout deveria ter apagado"
        assert _label_count(page) == 1
        (rect,) = _link_rects(page)
        assert rect.y0 >= DIAGRAM[3], "com a área limpa, o rótulo volta para baixo"
    finally:
        doc.close()


def test_two_diagrams_on_one_page_do_not_stack_their_labels(tmp_path: Path) -> None:
    """O rótulo de uma operação conta como texto para a seguinte."""
    second = (100.0, 380.0, 260.0, 540.0)
    doc_in = fitz.open()
    page_in = doc_in.new_page(width=PAGE_W, height=PAGE_H)
    for rect in (DIAGRAM, second):
        page_in.draw_rect(fitz.Rect(*rect), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    source = tmp_path / "dois.pdf"
    doc_in.save(str(source))
    doc_in.close()

    out = tmp_path / "saida.pdf"
    apply_operations_to_pdf(
        str(source),
        str(out),
        [
            OverlayOperation(page_num=0, rect_pdf=DIAGRAM, fen=FEN),
            OverlayOperation(page_num=0, rect_pdf=second, fen=FEN),
        ],
        whiteout=True,
        include_lichess_link=True,
    )
    doc = fitz.open(str(out))
    try:
        page = doc[0]
        rects = _link_rects(page)
        assert len(rects) == 2, "cada diagrama tem o seu link"
        assert not (rects[0] & rects[1]).get_area(), "os dois rótulos se sobrepuseram"
    finally:
        doc.close()


def test_disabling_the_link_writes_nothing(tmp_path: Path) -> None:
    source = _make_pdf(tmp_path / "livre.pdf")
    out = tmp_path / "saida.pdf"
    apply_operations_to_pdf(
        str(source),
        str(out),
        [OverlayOperation(page_num=0, rect_pdf=DIAGRAM, fen=FEN)],
        include_lichess_link=False,
    )
    doc = fitz.open(str(out))
    try:
        assert _link_rects(doc[0]) == []
        assert _label_count(doc[0]) == 0
    finally:
        doc.close()
