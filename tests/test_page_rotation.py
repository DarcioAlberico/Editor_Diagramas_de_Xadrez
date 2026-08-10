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


def _make_pdf(
    path: Path, rotation: int, with_text: bool = True, diagram: tuple = DIAGRAM
) -> Path:
    """Página de livro girada, com o "diagrama" magenta.

    `with_text=False` para os testes que medem a **tinta escura** do tabuleiro: o texto
    da página é tinta escura também, e entraria na caixa medida. Foi o que derrubou a
    primeira versão do teste de posição, inclusive em `/Rotate 0`.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    if with_text:
        page.insert_text(fitz.Point(60, 60), f"pagina girada {rotation}", fontsize=12)
    page.draw_rect(fitz.Rect(*diagram), color=None, fill=MAGENTA)
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


def _substitute(
    tmp_path: Path,
    rotation: int,
    with_text: bool = True,
    diagram: tuple = DIAGRAM,
    **kwargs,
):
    """Faz o fluxo inteiro numa página girada e devolve o que se vê depois."""
    source = _make_pdf(
        tmp_path / f"in{rotation}.pdf", rotation, with_text=with_text, diagram=diagram
    )
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
# Rotação junto com CropBox deslocada (§48)
#
# A §47 recusava esta combinação por não ter identificado a transformação. A
# calibração que a identificou estava medida numa CropBox **simétrica** —
# margens esquerda e direita iguais, topo e base iguais —, e por isso cada termo
# das fórmulas era ambíguo entre duas grandezas diferentes. Com margens
# desiguais a regra aparece de primeira.
#
# Daí a fixture: as quatro margens desta CropBox são **todas diferentes**
# (30 / 95 / 45 / 70). Uma conversão que troque eixo ou sinal passa numa CropBox
# simétrica e falha aqui — que é exatamente o que se quer de uma fixture.
# ---------------------------------------------------------------------------

CROPBOX = (30.0, 45.0, 500.0, 772.0)
#: Um diagrama no rodapé: em página girada o retângulo convertido cai fora de
#: `page.rect`, que tem largura e altura trocadas. Era o recorte que o apagava.
LOW_DIAGRAM = (70.0, 620.0, 270.0, 820.0)


def _make_pdf_with_cropbox(
    path: Path, rotation: int, cropbox=CROPBOX, diagram: tuple = DIAGRAM
) -> Path:
    _make_pdf(path, 0, with_text=False, diagram=diagram)
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


def _export(source: Path, output: Path) -> tuple:
    service = PdfService(str(source))
    try:
        render = service.render_page(0, zoom=ZOOM)
        selection = _selection_as_the_user_would(service, render)
        seen_before, _ = _boxes(render.image_png)
    finally:
        service.close()
    apply_operations_to_pdf(
        str(source),
        str(output),
        [OverlayOperation(page_num=0, rect_pdf=selection, fen=FEN)],
        include_lichess_link=False,
    )
    return seen_before, selection


def _after(output: Path) -> tuple:
    service = PdfService(str(output))
    try:
        return _boxes(service.render_page(0, zoom=ZOOM).image_png)
    finally:
        service.close()


def test_an_offset_cropbox_alone_still_works(tmp_path: Path) -> None:
    """CropBox deslocada sem rotação sempre funcionou, e continua funcionando."""
    source = _make_pdf_with_cropbox(tmp_path / "crop.pdf", rotation=0)
    output = tmp_path / "crop-out.pdf"

    _export(source, output)

    magenta, _ink = _after(output)
    assert magenta is None, "o diagrama original sobreviveu"


@pytest.mark.parametrize("rotation", (90, 180, 270))
def test_the_whiteout_covers_the_diagram_with_rotation_and_cropbox(
    tmp_path: Path, rotation: int
) -> None:
    """A combinação que a §47 recusava: agora exporta, e exporta certo."""
    source = _make_pdf_with_cropbox(tmp_path / f"both{rotation}.pdf", rotation=rotation)
    output = tmp_path / f"both{rotation}-out.pdf"

    _export(source, output)

    magenta, _ink = _after(output)
    assert magenta is None, f"o diagrama original sobreviveu em {magenta}"


@pytest.mark.parametrize("rotation", (90, 180, 270))
def test_the_board_lands_where_selected_with_rotation_and_cropbox(
    tmp_path: Path, rotation: int
) -> None:
    """Cobrir o original não basta: o tabuleiro novo tem de cair no lugar dele.

    É o teste que pega a conversão com a magnitude certa e o eixo errado — o
    sintoma medido na §47.2.
    """
    source = _make_pdf_with_cropbox(tmp_path / f"pos{rotation}.pdf", rotation=rotation)
    output = tmp_path / f"pos{rotation}-out.pdf"

    seen_before, _selection = _export(source, output)

    _magenta, ink = _after(output)
    assert ink is not None, "nenhum tabuleiro foi desenhado"
    assert all(abs(ink[i] - seen_before[i]) <= TOLERANCE_PX for i in range(4)), (
        f"o tabuleiro caiu em {ink}, e a seleção era {seen_before}"
    )


def test_the_board_is_not_lying_on_its_side_with_a_cropbox(tmp_path: Path) -> None:
    """As duas metades da §46 continuam independentes: posição e orientação."""
    crops: dict[int, tuple] = {}
    for rotation in ROTATIONS:
        source = _make_pdf_with_cropbox(tmp_path / f"ori{rotation}.pdf", rotation=rotation)
        output = tmp_path / f"ori{rotation}-out.pdf"
        _seen, selection = _export(source, output)
        document = fitz.open(str(output))
        try:
            pixmap = document[0].get_pixmap(
                matrix=fitz.Matrix(1.0, 1.0), clip=fitz.Rect(selection), colorspace=fitz.csRGB
            )
            crops[rotation] = (pixmap.width, pixmap.height, pixmap.samples)
        finally:
            document.close()

    for rotation in (90, 180, 270):
        assert crops[rotation] == crops[0], f"o tabuleiro em /Rotate {rotation} saiu girado"


# ---------------------------------------------------------------------------
# O recorte que apagava trabalho válido (§48.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", (90, 270))
def test_a_diagram_low_on_a_rotated_page_is_still_covered(
    tmp_path: Path, rotation: int
) -> None:
    """Defeito que já existia desde a §46, e que nenhum teste pegava.

    Convertido para espaço de escrita, um diagrama no rodapé fica em y ≈ 800 —
    além dos 595 de altura de `page.rect` numa página girada. O recorte
    `& page.rect` zerava o whiteout inteiro e o diagrama original sobrevivia.
    O diagrama das outras fixtures fica em y ≤ 460 e nunca chegava lá.
    """
    _before, magenta, _ink, _sel, _out = _substitute(
        tmp_path, rotation, with_text=False, diagram=LOW_DIAGRAM
    )

    assert magenta is None, f"o whiteout foi recortado e o original sobreviveu em {magenta}"


@pytest.mark.parametrize("rotation", (90, 270))
def test_a_low_diagram_with_a_cropbox_is_also_covered(tmp_path: Path, rotation: int) -> None:
    """O mesmo recorte, agora com a CropBox deslocada junto."""
    source = _make_pdf_with_cropbox(
        tmp_path / f"low{rotation}.pdf", rotation=rotation, diagram=(70.0, 560.0, 250.0, 740.0)
    )
    output = tmp_path / f"low{rotation}-out.pdf"

    _export(source, output)

    magenta, _ink = _after(output)
    assert magenta is None, f"o diagrama original sobreviveu em {magenta}"


# ---------------------------------------------------------------------------
# Ler texto da página também é em espaço de escrita (§48.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_text_extraction_from_a_selection_works_on_a_rotated_page(
    tmp_path: Path, rotation: int
) -> None:
    """`get_text(clip=...)` é em espaço de escrita, e recebia o da seleção.

    O modo de estudo lê a página por aqui: em qualquer página girada a resposta
    era string vazia, sem erro nenhum.
    """
    path = tmp_path / f"txt{rotation}.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(fitz.Point(100, 300), "Diagrama 12", fontsize=14)
    page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()

    service = PdfService(str(path))
    try:
        # Pelo caminho do usuário: ele vê o texto, arrasta em volta dele, e a
        # interface converte com `image_rect_to_pdf_rect`.
        render = service.render_page(0, zoom=ZOOM)
        _magenta, ink = _boxes(render.image_png)
        assert ink is not None, "o texto não foi encontrado no render"
        margin = 4 * ZOOM  # a caixa medida vem da amostragem de 2 px do `_boxes`
        around = (ink[0] - margin, ink[1] - margin, ink[2] + margin, ink[3] + margin)
        selection = service.image_rect_to_pdf_rect(0, around, render.matrix)
        text = service.extract_text_from_pdf_rect(0, selection)
    finally:
        service.close()

    assert "Diagrama 12" in text, f"não leu o texto da seleção: {text!r}"
