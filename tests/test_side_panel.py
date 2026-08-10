"""Painel lateral: os critérios de aceite da §20.5, medidos (§41).

O `test_toolbar` congelou a auditoria da barra de ferramentas (§34). Este faz o
mesmo pelo painel — a metade que nunca tinha sido medida.

O teste mais valioso daqui é `test_no_widget_ships_a_stylesheet_qt_cannot_parse`:
uma folha de estilo com erro de sintaxe é **descartada inteira e em silêncio** pelo
Qt, então a proteção que ela continha desaparece sem ninguém notar. Foi o que
aconteceu com a paleta de peças, e nenhum teste pegava.
"""
from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

#: Altura de janela a partir da qual o fluxo básico cabe sem rolar, medida em
#: 1500 px de largura. Em 900 (a altura padrão) ele **não** cabe: falta 191 px. A
#: causa é estrutural e está registrada na §41.3 — o painel de cima fica preso no
#: seu mínimo de 538 px, e encolher o editor de tabuleiro já foi tentado e
#: revertido na §34.3.
FLOW_FITS_FROM_HEIGHT = 1100


def _stylesheet_warnings(build) -> list[str]:
    """Constrói algo com um handler de mensagens do Qt instalado e devolve o que
    ele reclamou de folha de estilo."""
    seen: list[str] = []

    def handler(mode, context, message):
        if "stylesheet" in str(message).lower():
            seen.append(str(message))

    previous = QtCore.qInstallMessageHandler(handler)
    try:
        build()
    finally:
        QtCore.qInstallMessageHandler(previous)
    return seen


def _advanced_groups(window) -> list[QtWidgets.QGroupBox]:
    return [
        group
        for group in window.findChildren(QtWidgets.QGroupBox)
        if group.isCheckable() and "vanç" in group.title()
    ]


# ---------------------------------------------------------------------------
# Folhas de estilo
# ---------------------------------------------------------------------------


def test_no_widget_ships_a_stylesheet_qt_cannot_parse(qapp, tmp_path) -> None:
    """Qt descarta a folha inteira ao primeiro erro de sintaxe, sem exceção.

    O caso real: `}}` sobrando numa linha que não era f-string. Ia embora com a
    folha a cor fixa do texto da paleta — a proteção que existia justamente para o
    tema escuro não estava em vigor, e o app só imprimia um aviso no log.

    A janela é construída **dentro** do escopo do handler, e não pela fixture: a
    maior parte das folhas é aplicada no `__init__`, então instalar o handler depois
    faz o teste passar sem olhar nada. Foi o que a primeira versão deste teste fez.
    """
    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)
    built: list = []

    def build() -> None:
        window = app_module.MainWindow(settings=settings)
        built.append(window)
        window.resize(1500, 900)
        window.show()
        qapp.processEvents()

    try:
        warnings = _stylesheet_warnings(build)
    finally:
        for window in built:
            window.close()

    assert warnings == [], f"{len(warnings)} folha(s) recusada(s) pelo Qt: {warnings[:2]}"


def test_every_applied_stylesheet_is_balanced(main_window) -> None:
    """Rede mais rasa que a de cima, mas que aponta o culpado.

    Contar chaves acha o erro sem depender de o Qt avisar — e o aviso do Qt não diz
    *qual* widget, o que torna o defeito caro de achar.
    """
    for widget in main_window.findChildren(QtWidgets.QWidget):
        sheet = widget.styleSheet()
        if not sheet:
            continue
        assert sheet.count("{") == sheet.count("}"), f"chaves desbalanceadas: {sheet!r}"
        assert "}}" not in sheet, f"chave dupla (f-string esquecida?): {sheet!r}"
        assert "{{" not in sheet, f"chave dupla (f-string esquecida?): {sheet!r}"


# ---------------------------------------------------------------------------
# §20.5: configurações avançadas escondidas
# ---------------------------------------------------------------------------


def test_advanced_settings_do_not_show_up_by_default(main_window) -> None:
    groups = _advanced_groups(main_window)

    assert groups, "nenhum grupo 'Avançado' encontrado — a §20.2 pede que exista"
    for group in groups:
        assert group.isChecked() is False, f"{group.title()} abre expandido"


# ---------------------------------------------------------------------------
# §20.5: comandos destrutivos com menos destaque
# ---------------------------------------------------------------------------


def test_destructive_commands_weigh_less_than_the_main_action(main_window) -> None:
    assert main_window.destructive_buttons, "a lista de destrutivos está vazia"
    for button in main_window.destructive_buttons:
        sheet = button.styleSheet()
        assert sheet, f"{button.text()!r} ficou com o peso visual de qualquer botão"
        # Achatado: sem preenchimento e sem negrito. Vermelho seria *mais* destaque.
        assert "transparent" in sheet, f"{button.text()!r} tem preenchimento"
        assert "font-weight: 600" not in sheet, f"{button.text()!r} está em negrito"


def test_the_main_action_is_the_one_that_stands_out(main_window, tmp_path) -> None:
    """O contraste é o ponto: destaque em tudo não é destaque."""
    from conftest import DIAGRAM_RECT, make_pdf

    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(rect_img)
    main_window.board_editor.set_piece_placement("8/8/8/4k3/8/8/4K3/8")

    # Com seleção e posição prontas, `Adicionar substituição` é a ação principal.
    assert "background-color: #1f6feb" in main_window.btn_add.styleSheet()
    assert "transparent" in main_window.btn_clear.styleSheet()


def test_destructive_buttons_stay_legible_on_hover(main_window) -> None:
    """Discreto não pode virar ilegível quando a mão chega no botão."""
    for button in main_window.destructive_buttons:
        assert "QPushButton:hover" in button.styleSheet()


# ---------------------------------------------------------------------------
# §20.5: menos cliques que o combo
# ---------------------------------------------------------------------------


def test_the_piece_palette_replaced_the_combo(main_window) -> None:
    """Paleta: 1 clique escolhe a peça, 1 clique põe na casa. O combo pedia 3
    (abrir, escolher, clicar na casa)."""
    palette = main_window.board_editor._palette_buttons

    assert len(palette) == 13, "12 peças e a casa vazia"
    assert main_window.board_editor.findChild(QtWidgets.QComboBox) is None, (
        "o combo de peça ativa voltou"
    )


def test_choosing_a_piece_then_a_square_is_two_clicks(main_window) -> None:
    board = main_window.board_editor
    board.set_piece_placement("8/8/8/8/8/8/8/8")

    board._palette_buttons["q"].click()          # clique 1: escolhe a peça
    board._set_cell_from_palette(0, 0)           # clique 2: põe na casa

    assert board.piece_placement().split("/")[0].startswith("q")


# ---------------------------------------------------------------------------
# §20.5: rolagem do fluxo básico
# ---------------------------------------------------------------------------


def _flow_bottom_and_viewport(window) -> tuple[int, int]:
    """Onde termina `Adicionar substituição` e até onde o visor mostra."""
    tab = window.edit_tabs.widget(0)
    top = window.btn_add.mapTo(tab.widget(), QtCore.QPoint(0, 0)).y()
    return (top + window.btn_add.height(), tab.viewport().height())


def test_the_basic_flow_fits_without_scrolling_from_the_measured_height(
    qapp, tmp_path
) -> None:
    """Prende a altura a partir da qual o fluxo cabe.

    Não afirma que cabe em 900 — não cabe, e a §41.3 diz por quê. Afirma que não
    piora: quem acrescentar 200 px de painel acima de `Adicionar substituição`
    quebra este teste.
    """
    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)
    window = app_module.MainWindow(settings=settings)
    try:
        window.resize(1500, FLOW_FITS_FROM_HEIGHT)
        window.show()
        qapp.processEvents()
        bottom, viewport = _flow_bottom_and_viewport(window)
        assert bottom <= viewport, (
            f"em {FLOW_FITS_FROM_HEIGHT} px o fluxo pede {bottom} px e o visor dá {viewport}"
        )
    finally:
        window.close()


def test_collapsing_a_group_survives_reopening(qapp, tmp_path) -> None:
    """Quem recolhe a prévia para caber não pode ter de refazer o clique a cada
    abertura — era o que acontecia antes da §41.4."""
    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)

    first = app_module.MainWindow(settings=settings)
    try:
        assert first.compare_group.isChecked() is True, "a prévia devia abrir expandida"
        first.compare_group.setChecked(False)
    finally:
        first.close()

    second = app_module.MainWindow(settings=settings)
    try:
        assert second.compare_group.isChecked() is False, "o recolhimento não sobreviveu"
    finally:
        second.close()


def test_the_advanced_group_also_remembers_being_opened(qapp, tmp_path) -> None:
    """A persistência vale nos dois sentidos, senão só serviria para esconder."""
    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)

    first = app_module.MainWindow(settings=settings)
    try:
        groups = _advanced_groups(first)
        groups[0].setChecked(True)
        title = groups[0].title()
    finally:
        first.close()

    second = app_module.MainWindow(settings=settings)
    try:
        reopened = next(g for g in _advanced_groups(second) if g.title() == title)
        assert reopened.isChecked() is True
    finally:
        second.close()


# ---------------------------------------------------------------------------
# §20.2: o fluxo numerado
# ---------------------------------------------------------------------------


def test_the_numbered_flow_keeps_all_its_steps(main_window, tmp_path) -> None:
    """As etapas de 1 a 5 têm de continuar numeradas depois de o painel atualizar.

    O passo 5 perdia o número: `Alterações (N)` sobrescrevia `5 · Alterações` no
    primeiro refresh da lista, que acontece já no arranque. O fluxo ficava 1, 2, 3, 4
    e um rótulo solto.
    """
    from conftest import DIAGRAM_RECT, make_pdf

    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(rect_img)
    main_window.board_editor.set_piece_placement("8/8/8/4k3/8/8/4K3/8")
    main_window._add_operation()

    numbered = {
        text.split(" · ")[0]
        for text in (
            [label.text() for label in main_window.findChildren(QtWidgets.QLabel)]
            + [group.title() for group in main_window.findChildren(QtWidgets.QGroupBox)]
        )
        if " · " in text
    }

    assert {"1", "2", "3", "4", "5"} <= numbered, f"etapas encontradas: {sorted(numbered)}"
    # E a contagem continua visível junto do número.
    assert main_window.changes_label.text() == "5 · Alterações (1)"
