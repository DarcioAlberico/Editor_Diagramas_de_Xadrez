"""Ajuste fino da seleção (Sprint 6.1).

O que estes testes protegem: antes só existia "arrastar do zero". Corrigir um recorte
2 pt torto exigia apagar e redesenhar — e com a prévia ao vivo isso ficou pior, porque
cada redesenho pisca o diagrama inteiro.
"""
from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.widgets import SelectablePageWidget  # noqa: E402

RECT = (100.0, 100.0, 300.0, 300.0)


@pytest.fixture
def page_widget(qapp):
    widget = SelectablePageWidget()
    pixmap = QtGui.QPixmap(400, 400)
    pixmap.fill(QtGui.QColor("white"))
    widget.set_page_pixmap(pixmap)
    # Zoom 2.0: 1 pt PDF = 2 px de tela, que é o padrão do visor.
    widget.set_points_scale(2.0)
    yield widget
    widget.deleteLater()


def _mouse_event(kind, x, y, button, buttons, modifiers=QtCore.Qt.NoModifier):
    # Assinatura completa (local, scene, global): as mais curtas estão marcadas como
    # deprecated no Qt 6.10 e enchem a saída da suíte de avisos.
    point = QtCore.QPointF(x, y)
    return QtGui.QMouseEvent(
        kind,
        point,
        point,
        point,
        button,
        buttons,
        modifiers,
        QtGui.QPointingDevice.primaryPointingDevice(),
    )


def _press(widget, x, y, modifiers=QtCore.Qt.NoModifier):
    widget.mousePressEvent(
        _mouse_event(
            QtCore.QEvent.MouseButtonPress,
            x,
            y,
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            modifiers,
        )
    )


def _move(widget, x, y):
    widget.mouseMoveEvent(
        _mouse_event(
            QtCore.QEvent.MouseMove, x, y, QtCore.Qt.NoButton, QtCore.Qt.LeftButton
        )
    )


def _release(widget, x, y):
    widget.mouseReleaseEvent(
        _mouse_event(
            QtCore.QEvent.MouseButtonRelease,
            x,
            y,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
    )


def _key(widget, key, modifiers=QtCore.Qt.NoModifier):
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, modifiers)
    widget.keyPressEvent(event)
    return event


# ---------------------------------------------------------------------------
# Alças
# ---------------------------------------------------------------------------


def test_dragging_a_corner_handle_resizes_instead_of_starting_over(page_widget) -> None:
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 300.0, 300.0)  # alça inferior-direita
    _move(page_widget, 340.0, 330.0)
    _release(page_widget, 340.0, 330.0)

    x0, y0, x1, y1 = page_widget.selection_rect()
    assert (x0, y0) == pytest.approx((100.0, 100.0)), "o canto oposto tem de ficar parado"
    assert (x1, y1) == pytest.approx((340.0, 330.0))


def test_dragging_an_edge_handle_moves_only_that_side(page_widget) -> None:
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 100.0, 200.0)  # alça da borda esquerda
    _move(page_widget, 70.0, 240.0)
    _release(page_widget, 70.0, 240.0)

    x0, y0, x1, y1 = page_widget.selection_rect()
    assert x0 == pytest.approx(70.0)
    assert (y0, x1, y1) == pytest.approx((100.0, 300.0, 300.0)), "só a esquerda podia mudar"


def test_dragging_the_middle_moves_the_selection_whole(page_widget) -> None:
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 200.0, 200.0)
    _move(page_widget, 230.0, 180.0)
    _release(page_widget, 230.0, 180.0)

    x0, y0, x1, y1 = page_widget.selection_rect()
    assert (x0, y0, x1, y1) == pytest.approx((130.0, 80.0, 330.0, 280.0))


def test_moving_against_the_edge_keeps_the_size(page_widget) -> None:
    """Encostar na margem não pode encolher a seleção — o diagrama tem tamanho fixo."""
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 200.0, 200.0)
    _move(page_widget, 0.0, 0.0)
    _release(page_widget, 0.0, 0.0)

    x0, y0, x1, y1 = page_widget.selection_rect()
    assert (x1 - x0, y1 - y0) == pytest.approx((200.0, 200.0))
    assert (x0, y0) == pytest.approx((0.0, 0.0))


def test_a_click_inside_the_selection_is_still_a_click(page_widget) -> None:
    """`point_clicked` é o que foca a substituição existente naquele ponto."""
    clicks: list[tuple[float, float]] = []
    page_widget.point_clicked.connect(lambda point: clicks.append(point))
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 200.0, 200.0)
    _release(page_widget, 202.0, 201.0)

    assert clicks == [(202.0, 201.0)]
    assert page_widget.selection_rect() is None


def test_dragging_outside_the_selection_starts_a_new_one(page_widget) -> None:
    page_widget.set_selection_rect(RECT)

    _press(page_widget, 20.0, 20.0)
    _move(page_widget, 80.0, 90.0)
    _release(page_widget, 80.0, 90.0)

    assert page_widget.selection_rect() == pytest.approx((20.0, 20.0, 80.0, 90.0))


def test_tiny_selections_only_get_corner_handles(page_widget) -> None:
    """Numa seleção minúscula as alças de borda cobririam as de canto."""
    page_widget.set_selection_rect((10.0, 10.0, 30.0, 30.0))
    assert set(page_widget._handle_centers()) == {"nw", "ne", "se", "sw"}

    page_widget.set_selection_rect(RECT)
    assert len(page_widget._handle_centers()) == 8


# ---------------------------------------------------------------------------
# Teclado
# ---------------------------------------------------------------------------


def test_arrow_moves_one_point(page_widget) -> None:
    page_widget.set_selection_rect(RECT)
    _key(page_widget, QtCore.Qt.Key_Right)
    # 1 pt PDF com zoom 2.0 = 2 px de tela.
    assert page_widget.selection_rect() == pytest.approx((102.0, 100.0, 302.0, 300.0))


def test_shift_arrow_moves_a_quarter_point(page_widget) -> None:
    page_widget.set_selection_rect(RECT)
    _key(page_widget, QtCore.Qt.Key_Down, QtCore.Qt.ShiftModifier)
    assert page_widget.selection_rect() == pytest.approx((100.0, 100.5, 300.0, 300.5))


def test_the_keyboard_step_follows_the_zoom(page_widget) -> None:
    """O passo é em pontos PDF: em zoom 4 o mesmo 1 pt são 4 px."""
    page_widget.set_points_scale(4.0)
    page_widget.set_selection_rect(RECT)
    _key(page_widget, QtCore.Qt.Key_Right)
    assert page_widget.selection_rect()[0] == pytest.approx(104.0)


def test_ctrl_arrow_resizes(page_widget) -> None:
    page_widget.set_selection_rect(RECT)
    _key(page_widget, QtCore.Qt.Key_Right, QtCore.Qt.ControlModifier)
    x0, y0, x1, y1 = page_widget.selection_rect()
    assert (x0, y0) == pytest.approx((100.0, 100.0)), "redimensionar não desloca o canto"
    assert (x1, y1) == pytest.approx((302.0, 300.0))


def test_arrows_emit_selection_changed(page_widget) -> None:
    """Sem o sinal a prévia ao vivo não acompanharia o ajuste fino."""
    seen: list[object] = []
    page_widget.set_selection_rect(RECT)
    page_widget.selection_changed.connect(lambda rect: seen.append(rect))
    _key(page_widget, QtCore.Qt.Key_Left)
    assert len(seen) == 1


def test_arrows_are_ignored_without_a_selection(page_widget) -> None:
    """Sem seleção as setas continuam sendo da navegação de página."""
    event = _key(page_widget, QtCore.Qt.Key_Right)
    assert not event.isAccepted()
    assert page_widget.selection_rect() is None


def test_shortcut_override_is_claimed_only_when_there_is_a_selection(page_widget) -> None:
    """`←`/`→` são atalhos de janela; sem aceitar o override eles nunca chegariam aqui.

    O Qt manda o `ShortcutOverride` já *ignorado* e decide pelo `isAccepted()` na volta,
    não pelo retorno de `event()` — é por isso que o teste ignora antes de enviar.
    """

    def _send():
        event = QtGui.QKeyEvent(
            QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_Right, QtCore.Qt.NoModifier
        )
        event.ignore()
        page_widget.event(event)
        return event

    assert not _send().isAccepted(), "sem seleção, a página é que navega"

    page_widget.set_selection_rect(RECT)
    assert _send().isAccepted(), "com seleção, as setas são do ajuste fino"
