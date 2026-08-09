"""Barra de ferramentas: tem de caber na tela (§24.9).

A auditoria da §24 mediu a interface com widgets reais, offscreen. Estes testes
congelam o resultado dessa medição — sem eles, o próximo comando acrescentado à
barra a devolve ao estado de transbordar sem que ninguém perceba.
"""
from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

#: Antes desta versão: 2.223 px, ou seja, transbordava até em 1920.
#: A folga até 1280 é para o próximo comando não estourar de imediato.
MAX_TOOLBAR_WIDTH = 1280


def _toolbar(window) -> QtWidgets.QToolBar:
    toolbar = window.findChild(QtWidgets.QToolBar)
    assert toolbar is not None, "a janela não tem barra de ferramentas"
    return toolbar


def test_the_toolbar_fits_on_a_normal_screen(main_window) -> None:
    largura = _toolbar(main_window).sizeHint().width()
    assert largura <= MAX_TOOLBAR_WIDTH, (
        f"a barra pede {largura} px e vai transbordar para o menu »"
    )


def test_the_anchors_of_the_workflow_keep_their_text(main_window) -> None:
    """Abrir e exportar são o começo e o fim do fluxo; ícone sozinho não basta."""
    toolbar = _toolbar(main_window)
    for action in (main_window.act_open_pdf, main_window.act_save_pdf):
        button = toolbar.widgetForAction(action)
        assert button is not None
        assert button.toolButtonStyle() != QtCore.Qt.ToolButtonIconOnly
        assert action.text()


@pytest.mark.parametrize(
    "nome",
    ["act_save_project", "act_load_project", "act_undo", "act_redo", "act_prev", "act_next"],
)
def test_icon_only_buttons_still_explain_themselves(main_window, nome: str) -> None:
    """Sem texto, o que resta é a dica — e ela não pode faltar."""
    action = getattr(main_window, nome)
    button = _toolbar(main_window).widgetForAction(action)
    assert button is not None
    assert button.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly
    assert action.toolTip().strip(), f"{nome} ficou sem dica"
    assert not action.icon().isNull(), f"{nome} ficou sem ícone"


def test_every_icon_only_button_has_a_shortcut(main_window) -> None:
    """Quem perdeu o rótulo tem de continuar alcançável pelo teclado."""
    for nome in ("act_save_project", "act_load_project", "act_undo", "act_redo", "act_prev", "act_next"):
        action = getattr(main_window, nome)
        assert action.shortcuts(), f"{nome} não tem atalho"


# ---------------------------------------------------------------------------
# O seletor de modo
# ---------------------------------------------------------------------------


def test_the_mode_button_shows_the_current_mode(main_window) -> None:
    """Antes eram três botões e o modo se lia por qual estava afundado."""
    main_window._set_mode("edit")
    assert main_window.mode_button.text() == "Modo: Edição"

    main_window._set_mode("study")
    assert main_window.mode_button.text() == "Modo: Estudo"

    main_window._set_mode("read")
    assert main_window.mode_button.text() == "Modo: Leitura"


def test_the_three_modes_are_still_reachable(main_window) -> None:
    menu = main_window.mode_button.menu()
    assert menu is not None
    textos = [action.text() for action in menu.actions()]
    assert textos == ["Leitura", "Estudo", "Edição"]


def test_choosing_a_mode_from_the_menu_switches_the_panel(main_window) -> None:
    main_window.act_mode_study.trigger()
    assert main_window.side_stack.currentIndex() == 1
    assert main_window.mode_button.text() == "Modo: Estudo"

    main_window.act_mode_edit.trigger()
    assert main_window.side_stack.currentIndex() == 0


# ---------------------------------------------------------------------------
# Alturas mínimas
# ---------------------------------------------------------------------------


def test_the_study_panel_fits_in_a_normal_window(main_window) -> None:
    """Pedia 1.136 px de altura numa janela padrão de 900: nunca cabia."""
    assert main_window.study_workspace.minimumHeight() <= 620


def test_the_side_panel_tabs_can_shrink(main_window) -> None:
    """A §24.1 resolveu isto com QScrollArea; o teste impede a regressão."""
    aba = main_window.edit_tabs.widget(0)
    assert aba.minimumSizeHint().height() <= 120
