"""O editor de tabuleiro e os seus comandos: a auditoria da §41.2, continuada.

Três mudanças, e as duas primeiras atacam o mesmo recurso escasso — a altura do
bloco de cima do painel, que a §41.2 mediu **presa em 538 px** e que decide quanto
sobra para as abas:

- a paleta de peças saiu de cima do tabuleiro para o lado dele (70 px);
- os quatro comandos do tabuleiro saíram de três linhas para uma (46 px);
- `Detectar no PDF` passou a ser a ação principal enquanto não há nada selecionado.

O que estes testes prendem não é o número: é a estrutura de onde ele sai. O número
volta a subir sozinho se alguém empilhar de novo.
"""
from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

#: Altura do bloco de cima depois das duas mudanças de layout. Era 538 px.
TOP_BLOCK_HEIGHT = 440

#: O azul do `PRIMARY_BUTTON_STYLE`. Comparado por substring porque o que importa
#: é o botão estar com a folha de destaque, não a folha inteira ser igual.
PRIMARY_INK = "background-color: #1f6feb"


def _board_commands(window) -> list[QtWidgets.QPushButton]:
    return [window.btn_auto_orient, window.btn_rotate, window.btn_flip, window.btn_clear_board]


def _laid_out(window, qapp):
    """Janela com o layout já calculado.

    A fixture entrega a janela sem `show()`, e sem ele todo `geometry()` responde a
    origem — os testes de posição passariam ou falhariam por acidente.
    """
    window.resize(1500, 900)
    window.show()
    qapp.processEvents()
    return window


# ---------------------------------------------------------------------------
# A paleta ao lado do tabuleiro
# ---------------------------------------------------------------------------


def test_the_palette_sits_beside_the_board_and_not_above_it(main_window, qapp) -> None:
    """Geometria, não contagem de widgets: tem de falhar se alguém devolver a paleta
    para cima do tabuleiro, e continuar passando se ela mudar de número de colunas."""
    editor = _laid_out(main_window, qapp).board_editor
    board = editor._board_container.geometry()
    palette = editor._palette_frame.geometry()

    assert palette.left() >= board.right(), (
        f"a paleta voltou para cima do tabuleiro "
        f"(paleta em x={palette.left()}, tabuleiro até x={board.right()})"
    )
    # Ao lado de verdade: as duas faixas verticais se cruzam.
    assert palette.top() < board.bottom() and board.top() < palette.bottom()


def test_the_palette_keeps_white_and_black_in_separate_columns(main_window, qapp) -> None:
    """O agrupamento é o que torna a rotação barata.

    Era linha de cima (vazia + brancas) e linha de baixo (pretas); virou coluna da
    esquerda e da direita. Se as cores se misturarem, a paleta ficou mais difícil de
    ler do que era antes de mexer — e aí o ganho de 70 px foi pago com usabilidade.
    """
    editor = _laid_out(main_window, qapp).board_editor
    colunas: dict[int, set[bool]] = {}
    for piece, button in editor._palette_buttons.items():
        if piece == ".":
            continue
        colunas.setdefault(button.geometry().left(), set()).add(piece.isupper())

    assert len(colunas) == 2, f"esperava duas colunas de peças, achei {len(colunas)}"
    for cores in colunas.values():
        assert len(cores) == 1, "brancas e pretas na mesma coluna"


def test_the_board_editor_no_longer_drives_the_panel_height(main_window, qapp) -> None:
    """Prende os 116 px devolvidos às abas."""
    main_window.resize(1500, 900)
    main_window.show()
    qapp.processEvents()

    topo = main_window.right_vertical_splitter.widget(0)
    assert topo.minimumSizeHint().height() <= TOP_BLOCK_HEIGHT, (
        f"o bloco de cima voltou a pedir {topo.minimumSizeHint().height()} px"
    )


def test_the_panel_minimum_width_follows_the_board_editor(main_window) -> None:
    """Era um 380 escrito à mão, que a paleta ao lado deixaria pequeno demais.

    Um número mantido à mão dos dois lados é a forma de defeito da §45. Abaixo do
    que o editor pede, ele sai cortado, e nada avisa.
    """
    assert (
        main_window.side_stack.minimumWidth()
        >= main_window.board_editor.minimumSizeHint().width()
    )


# ---------------------------------------------------------------------------
# Os quatro comandos numa linha
# ---------------------------------------------------------------------------


def test_the_four_board_commands_share_one_row(main_window, qapp) -> None:
    """Comparado por faixa, não por `y` igual: `Limpar` é mais alto que os outros por
    causa do estilo achatado, então ficaria centrado numa coordenada diferente sem
    estar em outra linha.

    O `_laid_out` não é decoração. Sem ele a mutação "voltar para três linhas" passa
    por este teste: os quatro botões respondem `y=0` enquanto o layout não correu, e
    aí compartilham a linha por acidente. Foi o que aconteceu na primeira versão.
    """
    _laid_out(main_window, qapp)
    faixas = [
        (b.mapTo(main_window, QtCore.QPoint(0, 0)).y(), b.height())
        for b in _board_commands(main_window)
    ]
    topo_mais_baixo = max(y for y, _ in faixas)
    base_mais_alta = min(y + h for y, h in faixas)

    assert topo_mais_baixo < base_mais_alta, (
        f"os comandos do tabuleiro voltaram a ocupar mais de uma linha: {faixas}"
    )


def test_the_row_of_commands_fits_the_narrowest_panel(main_window) -> None:
    """Com os quatro rótulos inteiros a linha pedia 794 px, e o painel mínimo tem ~450.

    Sem este teste, devolver o texto a um dos botões passa despercebido até alguém
    estreitar o painel e ver o Qt cortar as palavras — o defeito da §24.1.
    """
    largura = sum(b.sizeHint().width() for b in _board_commands(main_window)) + 3 * 6

    assert largura <= main_window.side_stack.minimumWidth(), (
        f"a linha pede {largura} px e o painel mínimo tem {main_window.side_stack.minimumWidth()}"
    )


def test_board_commands_without_a_label_still_explain_themselves(main_window) -> None:
    """A regra que o `test_toolbar` cobra da barra, aplicada ao painel."""
    for button in _board_commands(main_window):
        if button.text():
            continue
        assert not button.icon().isNull(), "botão sem rótulo e sem ícone"
        assert button.toolTip().strip(), "botão sem rótulo e sem dica"


def test_the_command_that_decides_alone_keeps_its_label(main_window) -> None:
    """Girar e espelhar o usuário confere olhando o tabuleiro; auto-orientar é o único
    que decide sozinho, e por isso o único que ninguém adivinha por um desenho."""
    assert main_window.btn_auto_orient.text().strip(), (
        "`Auto-orientar` perdeu o rótulo — é o comando que menos se adivinha"
    )


def test_every_unlabelled_board_command_has_a_shortcut(main_window) -> None:
    """Os três existiam **só** como botão: sem ação, sem menu e sem atalho. Perder o
    rótulo sem ganhar teclado deixaria o comando alcançável só por quem adivinhasse
    o desenho."""
    for nome in ("act_rotate_board", "act_flip_board", "act_clear_board"):
        action = getattr(main_window, nome)
        assert action.shortcuts(), f"{nome} não tem atalho"
        assert action.toolTip().strip(), f"{nome} não tem dica"


def test_the_new_shortcuts_do_not_collide_with_the_old_ones(main_window) -> None:
    """Colisão é a mesma tecla em **ações diferentes**, e é só isso que se cobra aqui.

    A primeira versão comparava a lista inteira e reprovava um caso que não é defeito:
    `act_redo` declara `QKeySequence.Redo` e `Ctrl+Y`, que no Windows são a mesma
    tecla — repetida dentro de uma ação só, onde não há ambiguidade sobre quem
    responde.
    """
    dono: dict[str, list[str]] = {}
    for action in main_window.findChildren(QtGui.QAction):
        nome = action.text() or action.toolTip() or "<sem nome>"
        for atalho in {s.toString() for s in action.shortcuts() if s.toString()}:
            dono.setdefault(atalho, []).append(nome)

    disputados = {tecla: acoes for tecla, acoes in dono.items() if len(acoes) > 1}

    assert not disputados, f"a mesma tecla em ações diferentes: {disputados}"


def test_the_shortcuts_do_what_the_buttons_do(main_window) -> None:
    """A ação e o botão têm de chamar o mesmo método — são dois caminhos escritos
    lado a lado, exatamente o par que a §45 documenta como fonte de defeito."""
    main_window.board_editor.set_piece_placement("8/8/8/8/8/8/8/K7")

    main_window.act_rotate_board.trigger()
    girado = main_window.board_editor.piece_placement()
    main_window.board_editor.set_piece_placement("8/8/8/8/8/8/8/K7")
    main_window.btn_rotate.click()
    assert main_window.board_editor.piece_placement() == girado

    main_window.act_clear_board.trigger()
    assert main_window.board_editor.piece_placement() == "8/8/8/8/8/8/8/8"


def test_clearing_the_board_weighs_less_than_the_commands_beside_it(main_window) -> None:
    """Nas três linhas o peso de `Limpar Tabuleiro` se lia da posição — ele tinha uma
    linha inteira para si. Lado a lado com girar e espelhar, ele precisa do estilo
    achatado para não virar o quarto botão de uma série (§41.4)."""
    assert main_window.btn_clear_board in main_window.destructive_buttons
    assert "transparent" in main_window.btn_clear_board.styleSheet()


# ---------------------------------------------------------------------------
# Os ícones desenhados
#
# Foram desenhados, e não escritos como glifo Unicode, porque a cobertura de fonte
# não dá para medir aqui: sob a plataforma offscreen o `inFont()` responde `False`
# até para o `×` que a paleta usa e que aparece na tela. Estes testes são o troco
# dessa escolha — o que se ganhou ao desenhar foi poder verificar.
# ---------------------------------------------------------------------------


def _tinta(icon: QtGui.QIcon, lado: int = 64) -> int:
    """Quantos pixels do ícone não são transparentes."""
    imagem = icon.pixmap(lado, lado).toImage()
    return sum(
        1
        for y in range(imagem.height())
        for x in range(imagem.width())
        if QtGui.QColor(imagem.pixel(x, y)).alpha() > 0 and imagem.pixelColor(x, y).alpha() > 0
    )


@pytest.mark.parametrize("nome", ["rotate", "flip", "clear"])
def test_every_drawn_icon_actually_has_ink(qapp, nome: str) -> None:
    """Um ícone vazio é o modo de falha que a escolha de desenhar existe para evitar.
    Sem este teste ele seria invisível: o botão fica lá, do tamanho certo, em branco."""
    from chess_pdf_editor.widgets import board_transform_icon

    icon = board_transform_icon(nome)
    assert not icon.isNull()
    assert _tinta(icon) > 0, f"o ícone {nome!r} saiu em branco"


def test_the_three_icons_are_different_drawings(qapp) -> None:
    """Copiar e colar o painter é o defeito provável aqui, e ele passaria pelo teste
    de tinta acima sem piscar."""
    from chess_pdf_editor.widgets import board_transform_icon

    desenhos = {
        nome: board_transform_icon(nome).pixmap(64, 64).toImage()
        for nome in ("rotate", "flip", "clear")
    }

    assert desenhos["rotate"] != desenhos["flip"]
    assert desenhos["flip"] != desenhos["clear"]
    assert desenhos["rotate"] != desenhos["clear"]


def test_an_unknown_icon_name_is_refused(qapp) -> None:
    """Recusar em vez de devolver um ícone vazio: a mesma escolha da §33 e da §47,
    aplicada a um caso pequeno. Um ícone em branco só aparece na tela do usuário."""
    from chess_pdf_editor.widgets import board_transform_icon

    with pytest.raises(ValueError):
        board_transform_icon("espelhar-horizontal")


# ---------------------------------------------------------------------------
# A ação principal quando ainda não há nada selecionado
# ---------------------------------------------------------------------------


def test_the_batch_button_leads_when_there_is_nothing_selected(main_window, tmp_path) -> None:
    """O estado "PDF aberto e nada feito" era o único do fluxo sem ação principal.

    `_set_primary_button` já listava os dois botões de lote entre os candidatos desde
    que foi escrito; o que faltava era um estado elegê-los. É o momento em que o lote
    responde melhor que a mão — um livro de 898 páginas não se seleciona a dedo.
    """
    from conftest import make_pdf

    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)

    assert PRIMARY_INK in main_window.btn_ocr_full.styleSheet(), (
        "`Detectar no PDF` não está em destaque com o PDF recém-aberto"
    )
    # Um de cada vez é a regra da §20.5.
    assert PRIMARY_INK not in main_window.btn_ocr.styleSheet()


def test_selecting_a_diagram_hands_the_lead_back(main_window, tmp_path) -> None:
    """O destaque do lote é do estado, não permanente: assim que há seleção, quem
    lidera é o reconhecimento dela."""
    from conftest import DIAGRAM_RECT, make_pdf

    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(rect_img)

    assert PRIMARY_INK in main_window.btn_ocr.styleSheet()
    assert PRIMARY_INK not in main_window.btn_ocr_full.styleSheet()


def test_no_button_stands_out_before_a_pdf_is_open(main_window) -> None:
    """Destaque em tudo não é destaque, e destaque no que está desabilitado é pior:
    aponta para uma porta trancada."""
    for nome in ("btn_ocr", "btn_ocr_page", "btn_ocr_full", "btn_add"):
        assert PRIMARY_INK not in getattr(main_window, nome).styleSheet()


def test_the_context_label_stays_on_one_line(main_window, tmp_path, qapp) -> None:
    """A primeira versão deste texto explicava o lote por extenso, quebrava em duas
    linhas e custava 28 px — num painel onde a conta da §41.2 é de dezenas. O botão
    em destaque já diz o que ele faz."""
    from conftest import make_pdf

    main_window.resize(1500, 900)
    main_window.show()
    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    qapp.processEvents()

    uma_linha = QtGui.QFontMetrics(main_window.edit_context_label.font()).height()
    # A folha do `CONTEXT_STYLE` põe 8 px de padding em cima e embaixo.
    texto = main_window.edit_context_label.height() - 16

    assert texto <= uma_linha * 1.5, (
        f"o rótulo de contexto ocupa {texto} px, mais de uma linha de {uma_linha}"
    )
