"""Comparação "cortina": original de um lado da linha, resultado do outro (§35).

A prévia cheia (§21) troca a página inteira de uma vez, e é justamente por isso
que ela não responde "o que mudou": o olho não acha a diferença entre dois
bitmaps que nunca estão na tela juntos. A cortina põe os dois lá.

A cobertura de pixel está em dois níveis, porque são duas afirmações diferentes:

* no widget, com bitmaps sintéticos, que a composição respeita a linha — nada de
  selecção nem PDF no caminho;
* na janela, que o bitmap base é o PDF original e o da cortina é o da prévia.

Juntas cobrem o caminho inteiro. Amostrar o widget *pintado* na janela não
serviria: o retângulo vermelho da seleção fica exatamente sobre o diagrama.
"""
from __future__ import annotations

import pytest

from conftest import DIAGRAM_RECT, make_pdf as _make_pdf

fitz = pytest.importorskip("fitz")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.widgets import SelectablePageWidget  # noqa: E402

FEN = "8/8/8/4k3/8/8/4K3/8"
FEN_EDITED = "8/8/8/3qk3/8/8/4K3/8"

#: O diagrama do PDF de teste, em pixels do render (zoom 2.0): x 200..520,
#: y 600..920. Com a linha no meio de uma página de 840 px (x = 420), ela cai
#: dentro do diagrama — que é o caso interessante, porque é ali que os dois
#: bitmaps diferem (conferido: diferem em toda essa faixa).
LEFT_X = 300
RIGHT_X = 470
SAMPLE_YS = tuple(range(620, 900, 20))


def _open_with_diagram(window, tmp_path, fen: str = FEN) -> None:
    window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = window.pdf_service.pdf_rect_to_image_rect(
        window.current_page,
        DIAGRAM_RECT,
        window.current_render.matrix,
    )
    window.page_widget.set_selection_rect(rect_img)
    window.board_editor.set_piece_placement(fen)


def _force_preview(window) -> None:
    window._preview_timer.stop()
    window._refresh_result_preview()


def _enable_curtain(window) -> None:
    window.act_toggle_curtain.setChecked(True)
    _force_preview(window)


def _image_from_png(png_bytes: bytes) -> QtGui.QImage:
    image = QtGui.QImage()
    assert image.loadFromData(png_bytes, "PNG")
    return image


def _rendered(widget) -> QtGui.QImage:
    image = QtGui.QImage(widget.size(), QtGui.QImage.Format_RGB32)
    image.fill(QtCore.Qt.white)
    widget.render(image)
    return image


def _column(image: QtGui.QImage, x: int) -> list[int]:
    return [image.pixelColor(x, y).rgb() for y in SAMPLE_YS]


def _base_column(widget, x: int) -> list[int]:
    """Coluna do bitmap base — o lado "antes" da cortina."""
    return _column(widget.pixmap().toImage(), x)


def _curtain_column(widget, x: int) -> list[int]:
    assert widget.has_curtain(), "a cortina está desligada"
    return _column(widget._curtain_pixmap.toImage(), x)


def _mouse_event(kind, x: float, y: float) -> QtGui.QMouseEvent:
    point = QtCore.QPointF(x, y)
    return QtGui.QMouseEvent(
        kind,
        point,
        point,
        point,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        QtGui.QPointingDevice.primaryPointingDevice(),
    )


def _drag(widget, start: tuple[float, float], end: tuple[float, float]) -> None:
    widget.mousePressEvent(_mouse_event(QtCore.QEvent.MouseButtonPress, *start))
    widget.mouseMoveEvent(_mouse_event(QtCore.QEvent.MouseMove, *end))
    widget.mouseReleaseEvent(_mouse_event(QtCore.QEvent.MouseButtonRelease, *end))


# ---------------------------------------------------------------------------
# O que a página mostra
# ---------------------------------------------------------------------------


def test_curtain_puts_original_and_result_on_the_same_page(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)

    assert main_window._curtain_active is True
    assert main_window.page_widget.has_curtain() is True
    assert main_window.current_preview_render is not None


def test_the_two_bitmaps_are_the_original_and_the_result(main_window, tmp_path) -> None:
    """O que impede a composição invertida: base = antes, cortina = depois."""
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)

    before = _image_from_png(main_window.current_render.image_png)
    after = _image_from_png(main_window.current_preview_render.image_png)
    # Se os dois bitmaps fossem iguais nestas colunas, o resto não provaria nada.
    assert _column(before, LEFT_X) != _column(after, LEFT_X)
    assert _column(before, RIGHT_X) != _column(after, RIGHT_X)

    widget = main_window.page_widget
    assert _base_column(widget, LEFT_X) == _column(before, LEFT_X), "a base não é o original"
    assert _curtain_column(widget, RIGHT_X) == _column(after, RIGHT_X), "a cortina não é o resultado"


def test_the_work_rectangles_stay_out_of_the_comparison(main_window, tmp_path) -> None:
    """Mesma regra da prévia (§21.4): as marcações não competem com o resultado."""
    _open_with_diagram(main_window, tmp_path)
    main_window._add_operation()
    _enable_curtain(main_window)

    assert main_window.operations, "o teste precisa de uma substituição salva"
    assert main_window.page_widget._operation_rects == []


def test_the_title_says_the_page_is_a_comparison(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)
    assert "[comparação: antes | depois]" in main_window.windowTitle()

    main_window.act_toggle_curtain.setChecked(False)
    _force_preview(main_window)
    assert "comparação" not in main_window.windowTitle()


def test_a_page_without_changes_has_nothing_to_compare(main_window, tmp_path) -> None:
    main_window._open_pdf(str(_make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    _enable_curtain(main_window)

    assert main_window._curtain_active is False
    assert main_window.page_widget.has_curtain() is False
    assert main_window._showing_preview is False


def test_turning_the_curtain_off_restores_the_original(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)

    main_window.act_toggle_curtain.setChecked(False)
    _force_preview(main_window)

    assert main_window._curtain_active is False
    assert main_window.page_widget.has_curtain() is False
    assert main_window._showing_preview is False
    before = _image_from_png(main_window.current_render.image_png)
    assert _base_column(main_window.page_widget, RIGHT_X) == _column(before, RIGHT_X)


def test_the_result_never_outlives_its_page(main_window, tmp_path) -> None:
    """Cortina viva numa página nova mostraria o resultado de outra página."""
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)
    assert main_window.page_widget.has_curtain() is True

    main_window._next_page()

    assert main_window.page_widget.has_curtain() is False
    assert main_window._curtain_active is False


# ---------------------------------------------------------------------------
# Cortina x prévia cheia
# ---------------------------------------------------------------------------


def test_curtain_and_full_preview_do_not_share_the_page(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)

    main_window.act_toggle_preview.setChecked(True)
    main_window.act_toggle_curtain.setChecked(True)
    assert main_window.act_toggle_preview.isChecked() is False
    assert main_window.preview_result_enabled is False

    main_window.act_toggle_preview.setChecked(True)
    assert main_window.act_toggle_curtain.isChecked() is False
    assert main_window.compare_curtain_enabled is False


def test_full_preview_still_replaces_the_whole_page(main_window, tmp_path) -> None:
    """A cortina não pode ter mudado o comportamento da prévia de §21."""
    _open_with_diagram(main_window, tmp_path)
    main_window.act_toggle_preview.setChecked(True)
    _force_preview(main_window)

    after = _image_from_png(main_window.current_preview_render.image_png)
    widget = main_window.page_widget
    assert _base_column(widget, LEFT_X) == _column(after, LEFT_X)
    assert _base_column(widget, RIGHT_X) == _column(after, RIGHT_X)
    assert widget.has_curtain() is False


# ---------------------------------------------------------------------------
# Arrastar a linha
# ---------------------------------------------------------------------------


def test_dragging_the_line_does_not_touch_the_selection(main_window, tmp_path) -> None:
    """A linha cruza a seleção: sem prioridade, arrastar viraria mover seleção."""
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)
    widget = main_window.page_widget
    split = widget.curtain_split_x()
    assert 200 < split < 520, "a linha precisa cair dentro do diagrama"
    selection_before = widget.selection_rect()
    assert selection_before is not None

    _drag(widget, (split, 750.0), (600.0, 750.0))

    assert widget.selection_rect() == selection_before
    assert widget.curtain_fraction() == pytest.approx(600.0 / 840.0, abs=0.01)


def test_where_the_user_left_the_line_is_remembered(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)
    widget = main_window.page_widget

    _drag(widget, (widget.curtain_split_x(), 750.0), (630.0, 750.0))
    moved = widget.curtain_fraction()
    assert main_window.curtain_fraction == pytest.approx(moved)
    assert main_window.settings.value("compare_curtain_fraction", 0.5, float) == pytest.approx(moved)

    # Um novo render da cortina reaproveita a posição, em vez de voltar ao meio.
    main_window.board_editor.set_piece_placement(FEN_EDITED)
    _force_preview(main_window)
    assert widget.curtain_fraction() == pytest.approx(moved)


def test_dragging_elsewhere_still_selects(main_window, tmp_path) -> None:
    """A prioridade da linha vale perto dela, não na página inteira."""
    _open_with_diagram(main_window, tmp_path)
    _enable_curtain(main_window)
    widget = main_window.page_widget
    fraction_before = widget.curtain_fraction()

    _drag(widget, (60.0, 120.0), (170.0, 260.0))

    assert widget.curtain_fraction() == pytest.approx(fraction_before)
    selection = widget.selection_rect()
    assert selection is not None
    assert selection[0] == pytest.approx(60.0, abs=1.0)


# ---------------------------------------------------------------------------
# A composição, no widget e sem PDF no caminho
# ---------------------------------------------------------------------------

WIDGET_W = 400
WIDGET_H = 200
#: Longe da linha (x = 200 com fração 0,5), da alça e dos rótulos do topo.
PROBE_Y = 150
BEFORE_X = 40
AFTER_X = 360


def _solid(color) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(WIDGET_W, WIDGET_H)
    pixmap.fill(QtGui.QColor(color))
    return pixmap


#: Verde e azul, não vermelho: o véu da seleção *é* vermelho, e vermelho sobre
#: vermelho não muda pixel nenhum — o teste do véu não provaria nada.
BEFORE_COLOR = QtCore.Qt.green
AFTER_COLOR = QtCore.Qt.blue
GREEN = QtGui.QColor(BEFORE_COLOR).name()
BLUE = QtGui.QColor(AFTER_COLOR).name()


def _curtain_widget(qapp) -> SelectablePageWidget:
    widget = SelectablePageWidget()
    # Nesta ordem: `set_page_pixmap` derruba a cortina de propósito.
    widget.set_page_pixmap(_solid(BEFORE_COLOR))
    widget.set_curtain_pixmap(_solid(AFTER_COLOR))
    return widget


def _color_at(widget, x: int) -> str:
    return _rendered(widget).pixelColor(x, PROBE_Y).name()


def test_the_line_decides_which_bitmap_each_pixel_comes_from(qapp) -> None:
    widget = _curtain_widget(qapp)
    widget.set_curtain_fraction(0.5)

    assert _color_at(widget, BEFORE_X) == GREEN, "à esquerda tem de sobrar o bitmap base"
    assert _color_at(widget, AFTER_X) == BLUE, "à direita tem de aparecer a cortina"


@pytest.mark.parametrize(
    ("fraction", "esperado_esquerda", "esperado_direita"),
    [(0.0, BLUE, BLUE), (1.0, GREEN, GREEN)],
)
def test_the_line_at_the_edges_shows_one_bitmap_only(
    qapp, fraction: float, esperado_esquerda: str, esperado_direita: str
) -> None:
    """Arrastar até a borda é um limpa-vidros: a página inteira de um lado só."""
    widget = _curtain_widget(qapp)
    widget.set_curtain_fraction(fraction)

    assert _color_at(widget, BEFORE_X) == esperado_esquerda
    assert _color_at(widget, AFTER_X) == esperado_direita


def test_without_a_curtain_the_page_is_painted_whole(qapp) -> None:
    widget = _curtain_widget(qapp)
    widget.set_curtain_pixmap(None)

    assert widget.has_curtain() is False
    assert _color_at(widget, BEFORE_X) == GREEN
    assert _color_at(widget, AFTER_X) == GREEN


def test_a_new_page_bitmap_drops_the_curtain(qapp) -> None:
    widget = _curtain_widget(qapp)
    assert widget.has_curtain() is True

    widget.set_page_pixmap(_solid(QtCore.Qt.magenta))

    assert widget.has_curtain() is False


def test_comparing_drops_the_red_veil_over_the_selection(qapp) -> None:
    """O véu da seleção cai sobre os dois lados e tinge o que se quer comparar."""
    widget = _curtain_widget(qapp)
    widget.set_curtain_fraction(0.5)
    # Seleção cobrindo a página inteira: é o caso real, porque a área do
    # diagrama é justamente o que está selecionado.
    widget.set_selection_rect((10.0, 10.0, float(WIDGET_W - 10), float(WIDGET_H - 10)))

    assert _color_at(widget, BEFORE_X) == GREEN, "o lado antes saiu tingido"
    assert _color_at(widget, AFTER_X) == BLUE, "o lado depois saiu tingido"

    # Sem cortina o véu volta: ele é útil para achar a seleção na página.
    widget.set_curtain_pixmap(None)
    assert _color_at(widget, BEFORE_X) != GREEN


def test_the_grip_follows_what_is_on_screen(qapp) -> None:
    """Ancorada no bitmap, a alça passaria a vida fora da tela: a página tem
    1.190 px de altura e o visor uns 800."""
    widget = _curtain_widget(qapp)
    # Fora de um visor a faixa é a página toda — é o caminho que `render()` usa.
    assert widget.curtain_band() == QtCore.QRectF(0.0, 0.0, WIDGET_W, WIDGET_H)

    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setWidget(widget)
    scroll.resize(200, 100)
    scroll.show()
    qapp.processEvents()
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    qapp.processEvents()

    band = widget.curtain_band()
    if band.height() >= WIDGET_H:
        pytest.skip("esta plataforma não calcula visibleRegion offscreen")
    assert band.top() > 0.0, "a faixa ignorou a rolagem"
    assert band.bottom() <= WIDGET_H
    scroll.close()


def test_the_fraction_never_leaves_the_page(qapp) -> None:
    widget = _curtain_widget(qapp)

    widget.set_curtain_fraction(-3.0)
    assert widget.curtain_fraction() == pytest.approx(0.0)

    widget.set_curtain_fraction(9.0)
    assert widget.curtain_fraction() == pytest.approx(1.0)

    widget.set_curtain_fraction("nao é número")
    assert widget.curtain_fraction() == pytest.approx(1.0), "valor inválido não muda nada"


# ---------------------------------------------------------------------------
# Descoberta pela interface
# ---------------------------------------------------------------------------


def test_the_command_is_reachable_and_explains_itself(main_window) -> None:
    action = main_window.act_toggle_curtain
    assert action.isCheckable()
    assert action.shortcuts(), "sem atalho e sem rótulo na barra, ninguém acha"
    assert action.toolTip().strip()
    assert not action.icon().isNull()


def test_the_panel_button_mirrors_the_action(main_window, tmp_path) -> None:
    _open_with_diagram(main_window, tmp_path)
    main_window.btn_toggle_curtain.setChecked(True)
    assert main_window.act_toggle_curtain.isChecked() is True

    _force_preview(main_window)
    assert main_window.btn_toggle_curtain.text() == "Voltar ao PDF original"

    main_window.act_toggle_curtain.setChecked(False)
    _force_preview(main_window)
    assert main_window.btn_toggle_curtain.isChecked() is False
    assert main_window.btn_toggle_curtain.text() == "Comparar com cortina"


def test_the_curtain_needs_a_pdf(main_window) -> None:
    assert main_window.act_toggle_curtain.isEnabled() is False
    assert main_window.btn_toggle_curtain.isEnabled() is False


def test_the_curtain_comes_back_ligada_no_proximo_uso(qapp, tmp_path) -> None:
    """Fechar o app com a cortina ligada e reabrir tem de reabrir comparando."""
    from chess_pdf_editor import app as app_module
    from chess_pdf_editor.pdf_service import clear_board_render_cache

    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)
    settings.setValue("compare_curtain_enabled", True)
    settings.setValue("compare_curtain_fraction", 0.72)
    clear_board_render_cache()

    window = app_module.MainWindow(settings=settings)
    try:
        assert window.act_toggle_curtain.isChecked() is True
        assert window.btn_toggle_curtain.isChecked() is True
        assert window.curtain_fraction == pytest.approx(0.72)

        _open_with_diagram(window, tmp_path)
        _force_preview(window)

        # Sem ninguém tocar no botão: a preferência sozinha já compara.
        assert window._curtain_active is True
        assert window.page_widget.curtain_fraction() == pytest.approx(0.72)
    finally:
        window.close()
        clear_board_render_cache()
