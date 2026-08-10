"""Páginas com `/Rotate`: livro escaneado de lado (§46).

Um PDF pode declarar rotação na página. O que o usuário vê é a página girada, e é
nesse espaço que ele desenha a seleção — mas escrever no conteúdo da página é em
espaço **não-rotacionado**. Sem converter entre os dois, numa página com `/Rotate 90`:

* o whiteout não cobria o diagrama original — ele sobrevivia inteiro no PDF exportado;
* o tabuleiro novo caía em outro lugar da página, **deitado**.

Nenhum teste cobria rotação antes disto, e `pdf_service` não mencionava a palavra.

A sonda é um retângulo **magenta**: nenhum antialiasing de texto preto produz essa
cor, então achá-lo é inequívoco. A primeira versão desta sonda usava cinza e acusou
divergência até em `/Rotate 0`, porque pegava as bordas do texto.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from chess_pdf_editor.pdf_service import (  # noqa: E402
    PdfService,
    apply_operations_to_pdf,
)
from chess_pdf_editor.types import EraseOperation, OverlayOperation  # noqa: E402

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
#: Onde o "diagrama" é desenhado, em espaço não-rotacionado.
DIAGRAM = (70.0, 260.0, 270.0, 460.0)
MAGENTA = (1.0, 0.0, 1.0)
FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R"
ZOOM = 2.0
ROTATIONS = (0, 90, 180, 270)
#: A borda detectada difere da caixa pedida por antialiasing.
TOLERANCE_PX = 6


def _make_pdf(path: Path, rotation: int, with_text: bool = True) -> Path:
    """Página de livro girada, com o "diagrama" magenta.

    `with_text=False` para os testes que medem a **tinta escura** do tabuleiro: o texto
    da página é tinta escura também, e entraria na caixa medida. Foi o que derrubou a
    primeira versão do teste de posição, inclusive em `/Rotate 0`.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    if with_text:
        page.insert_text(fitz.Point(60, 60), f"pagina girada {rotation}", fontsize=12)
    page.draw_rect(fitz.Rect(*DIAGRAM), color=None, fill=MAGENTA)
    page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def _boxes(png: bytes) -> tuple[tuple | None, tuple | None]:
    """Caixa do magenta e caixa da tinta escura na imagem renderizada."""
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.Pixmap(png))
    magenta: list[tuple[int, int]] = []
    ink: list[tuple[int, int]] = []
    for y in range(0, pixmap.height, 2):
        for x in range(0, pixmap.width, 2):
            red, green, blue = pixmap.pixel(x, y)
            if red > 200 and green < 60 and blue > 200:
                magenta.append((x, y))
            elif red + green + blue < 200:
                ink.append((x, y))

    def box(points):
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    return box(magenta), box(ink)


def _selection_as_the_user_would(service: PdfService, render) -> tuple:
    """O usuário seleciona o que vê; o app converte com `image_rect_to_pdf_rect`."""
    seen, _ink = _boxes(render.image_png)
    assert seen is not None, "o diagrama magenta não foi encontrado no render"
    return service.image_rect_to_pdf_rect(0, seen, render.matrix)


def _substitute(tmp_path: Path, rotation: int, with_text: bool = True, **kwargs):
    """Faz o fluxo inteiro numa página girada e devolve o que se vê depois."""
    source = _make_pdf(tmp_path / f"in{rotation}.pdf", rotation, with_text=with_text)
    service = PdfService(str(source))
    try:
        render = service.render_page(0, zoom=ZOOM)
        seen_before, _ = _boxes(render.image_png)
        selection = _selection_as_the_user_would(service, render)
    finally:
        service.close()

    output = tmp_path / f"out{rotation}.pdf"
    apply_operations_to_pdf(
        str(source),
        str(output),
        [OverlayOperation(page_num=0, rect_pdf=selection, fen=FEN)],
        include_lichess_link=False,
        **kwargs,
    )
    after = PdfService(str(output))
    try:
        magenta, ink = _boxes(after.render_page(0, zoom=ZOOM).image_png)
    finally:
        after.close()
    return seen_before, magenta, ink, selection, output


# ---------------------------------------------------------------------------
# O que o usuário vê e o que o app calcula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_the_selection_round_trips_through_pdf_space(tmp_path: Path, rotation: int) -> None:
    """Imagem → PDF → imagem tem de fechar, em qualquer rotação."""
    service = PdfService(str(_make_pdf(tmp_path / "p.pdf", rotation)))
    try:
        render = service.render_page(0, zoom=ZOOM)
        seen, _ = _boxes(render.image_png)
        as_pdf = service.image_rect_to_pdf_rect(0, seen, render.matrix)
        back = service.pdf_rect_to_image_rect(0, as_pdf, render.matrix)
    finally:
        service.close()

    assert all(abs(back[i] - seen[i]) <= 2 for i in range(4)), f"{back} != {seen}"


@pytest.mark.parametrize("rotation", (90, 270))
def test_a_rotated_page_renders_landscape(tmp_path: Path, rotation: int) -> None:
    """Garante que a fixture está de fato girada — sem isto o resto não prova nada."""
    service = PdfService(str(_make_pdf(tmp_path / "p.pdf", rotation)))
    try:
        render = service.render_page(0, zoom=ZOOM)
        pixmap = fitz.Pixmap(render.image_png)
        assert pixmap.width > pixmap.height
    finally:
        service.close()


# ---------------------------------------------------------------------------
# A substituição, ponta a ponta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_the_whiteout_covers_the_original_diagram(tmp_path: Path, rotation: int) -> None:
    """Era o sintoma mais grave: em página girada o diagrama antigo sobrevivia."""
    _before, magenta, _ink, _sel, _out = _substitute(tmp_path, rotation)

    assert magenta is None, f"o diagrama original sobreviveu em {magenta}"


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_the_new_board_lands_where_the_user_selected(tmp_path: Path, rotation: int) -> None:
    # Sem texto na página: aqui a medição é a tinta do tabuleiro, e o texto é tinta.
    seen_before, _magenta, ink, _sel, _out = _substitute(tmp_path, rotation, with_text=False)

    assert ink is not None, "nenhum tabuleiro foi desenhado"
    assert all(abs(ink[i] - seen_before[i]) <= TOLERANCE_PX for i in range(4)), (
        f"o tabuleiro caiu em {ink}, e a seleção era {seen_before}"
    )


def test_the_board_is_not_lying_on_its_side(tmp_path: Path) -> None:
    """O segundo sintoma: a posição certa mas as peças deitadas.

    Medido comparando o recorte do tabuleiro com o da mesma substituição numa página
    sem rotação — tem de sair **idêntico**, não só do mesmo tamanho.
    """
    crops: dict[int, tuple] = {}
    for rotation in ROTATIONS:
        _before, _magenta, _ink, selection, output = _substitute(
            tmp_path, rotation, with_text=False
        )
        document = fitz.open(str(output))
        try:
            page = document[0]
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(1.0, 1.0), clip=fitz.Rect(selection), colorspace=fitz.csRGB
            )
            crops[rotation] = (pixmap.width, pixmap.height, pixmap.samples)
        finally:
            document.close()

    for rotation in (90, 180, 270):
        assert crops[rotation] == crops[0], f"o tabuleiro em /Rotate {rotation} saiu girado"


@pytest.mark.parametrize("rotation", (90, 270))
def test_an_erasure_also_lands_right_on_a_rotated_page(tmp_path: Path, rotation: int) -> None:
    """Apagamento passa pela mesma conversão; sem ela apagava o lugar errado."""
    source = _make_pdf(tmp_path / f"in{rotation}.pdf", rotation)
    service = PdfService(str(source))
    try:
        render = service.render_page(0, zoom=ZOOM)
        selection = _selection_as_the_user_would(service, render)
    finally:
        service.close()

    output = tmp_path / f"erased{rotation}.pdf"
    apply_operations_to_pdf(
        str(source),
        str(output),
        [],
        erase_operations=[EraseOperation(page_num=0, rect_pdf=selection)],
        include_lichess_link=False,
    )
    after = PdfService(str(output))
    try:
        magenta, _ink = _boxes(after.render_page(0, zoom=ZOOM).image_png)
    finally:
        after.close()

    assert magenta is None, f"o apagamento não pegou o diagrama; sobrou {magenta}"


def test_an_unrotated_page_is_untouched_by_the_fix(tmp_path: Path) -> None:
    """A conversão só entra quando há rotação, então o caso comum não muda.

    Vale byte a byte: `/Rotate 0` é a esmagadora maioria dos livros, e a §21.2 trava
    prévia e exportação no mesmo caminho de código.
    """
    source = _make_pdf(tmp_path / "plain.pdf", 0)
    service = PdfService(str(source))
    try:
        render = service.render_page(0, zoom=ZOOM)
        selection = _selection_as_the_user_would(service, render)
    finally:
        service.close()

    outputs = []
    for index in (1, 2):
        output = tmp_path / f"plain{index}.pdf"
        apply_operations_to_pdf(
            str(source),
            str(output),
            [OverlayOperation(page_num=0, rect_pdf=selection, fen=FEN)],
            include_lichess_link=False,
        )
        after = PdfService(str(output))
        try:
            outputs.append(after.render_page(0, zoom=ZOOM).image_png)
        finally:
            after.close()

    assert outputs[0] == outputs[1], "a exportação deixou de ser determinística"


# ---------------------------------------------------------------------------
# Rotação junto com CropBox deslocada: recusa explícita (§47)
# ---------------------------------------------------------------------------

CROPBOX = (40.0, 60.0, 555.0, 782.0)


def _make_pdf_with_cropbox(path: Path, rotation: int, cropbox=CROPBOX) -> Path:
    _make_pdf(path, 0, with_text=False)
    document = fitz.open(str(path))
    try:
        page = document[0]
        if cropbox:
            page.set_cropbox(fitz.Rect(*cropbox))
        if rotation:
            page.set_rotation(rotation)
        document.save(str(path), incremental=True, encryption=0)
    finally:
        document.close()
    return path


def _export(source: Path, output: Path) -> None:
    service = PdfService(str(source))
    try:
        render = service.render_page(0, zoom=ZOOM)
        selection = _selection_as_the_user_would(service, render)
    finally:
        service.close()
    apply_operations_to_pdf(
        str(source),
        str(output),
        [OverlayOperation(page_num=0, rect_pdf=selection, fen=FEN)],
        include_lichess_link=False,
    )


def test_an_offset_cropbox_alone_still_works(tmp_path: Path) -> None:
    """CropBox deslocada sem rotação sempre funcionou, e continua funcionando."""
    source = _make_pdf_with_cropbox(tmp_path / "crop.pdf", rotation=0)
    output = tmp_path / "crop-out.pdf"

    _export(source, output)

    after = PdfService(str(output))
    try:
        magenta, _ink = _boxes(after.render_page(0, zoom=ZOOM).image_png)
    finally:
        after.close()
    assert magenta is None, "o diagrama original sobreviveu"


@pytest.mark.parametrize("rotation", (90, 180, 270))
def test_rotation_with_an_offset_cropbox_is_refused_loudly(tmp_path: Path, rotation: int) -> None:
    """Combinação não resolvida (§47): o app **recusa** em vez de exportar errado.

    O sintoma que isto substitui era pior que um erro: PDF com o diagrama antigo
    intacto e o novo atravessado noutro lugar, que ninguém percebe num livro de 900
    páginas até distribuí-lo.
    """
    from chess_pdf_editor.pdf_service import UnsupportedPageGeometry

    source = _make_pdf_with_cropbox(tmp_path / f"both{rotation}.pdf", rotation=rotation)
    output = tmp_path / f"both{rotation}-out.pdf"

    with pytest.raises(UnsupportedPageGeometry) as raised:
        _export(source, output)

    message = str(raised.value)
    assert "CropBox" in message
    assert str(rotation) in message
    assert not output.exists(), "recusou, mas deixou um PDF pela metade"
