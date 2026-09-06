"""Painel lateral: os critérios de aceite da §20.5, medidos (§41).

O `test_toolbar` congelou a auditoria da barra de ferramentas (§34). Este faz o
mesmo pelo painel — a metade que nunca tinha sido medida.

O teste mais valioso daqui é `test_no_widget_ships_a_stylesheet_qt_cannot_parse`:
uma folha de estilo com erro de sintaxe é **descartada inteira e em silêncio** pelo
Qt, então a proteção que ela continha desaparece sem ninguém notar. Foi o que
aconteceu com a paleta de peças, e nenhum teste pegava.
"""
from __future__ import annotations

import sys
import time

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

#: Altura de janela a partir da qual o fluxo básico cabe sem rolar, medida em
#: 1500 px de largura e com a prévia expandida.
#:
#: A queda, em dois sprints e por dois caminhos diferentes:
#:
#: | | expandida | recolhida |
#: |---|---|---|
#: | §41.2, com o topo preso em 538 px | 1.100 | 1.050 |
#: | 9.22: paleta ao lado + comandos numa linha (§50) | 980 | 900 |
#: | 9.23: o que não é etapa fora da aba do fluxo (§51) | 880 | **790** |
#: | 9.29: a mesma janela, medida **depois** da prévia entrar (§57) | **892** | **790** |
#:
#: A última linha não é uma regressão: é a correção de uma medida. As três
#: anteriores foram tiradas ~25 ms depois de abrir o PDF, antes de o
#: `_preview_timer` (140 ms) trazer a prévia ao vivo e empurrar o painel em 14 px
#: — ver `_settle_layout`. A janela que o usuário abre já nasce com a prévia
#: dentro, então 892 é o número dele.
#:
#: O critério da §20.5 — caber em 1500x900 com a prévia expandida — **continua
#: cumprido**, agora com 8 px de folga em vez dos 22 que se acreditava ter.
#:
#: Encolher o editor de tabuleiro continua reprovado (§34.3). O que resolveu foi
#: mexer na divisão do painel — o caminho que a própria §41.2 apontava.
FLOW_FITS_FROM_HEIGHT = 892

#: Com a prévia recolhida.
FLOW_FITS_COLLAPSED_FROM_HEIGHT = 790

#: A janela padrão. O critério da §20.5 é sobre ela, e passou a ser cumprido.
DEFAULT_WINDOW_HEIGHT = 900


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
    """Os grupos de configuração, declarados pela janela.

    Era uma busca por "vanç" no título, o que prendia o critério da §20.5 ao texto
    do rótulo: quando os grupos foram renomeados no Sprint 9.23 o teste passou a
    achar zero grupos — e teria passado em silêncio se o `assert groups` não
    estivesse lá. Agora a janela diz quais são.
    """
    return list(window.settings_groups)


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


def _assert_flow_fits(bottom: int, viewport: int, mensagem: str) -> None:
    """Cobra o critério da §20.5 no Windows; fora dele, **mede e reporta** (§56).

    O critério é sobre pixels na tela do usuário, e a mesma janela mede diferente
    em cada plataforma: no Ubuntu do CI os mesmos widgets pedem ~20 px a mais.
    Afirmar lá que o fluxo cabe seria o teste mentindo, e afrouxar o limite até o
    número de lá faria o número deixar de significar "cabe na tela" para significar
    "cabe na pior métrica que eu conheço" — um critério sobre nada. Windows é a
    plataforma prioritária do produto (o README diz, e o instalador do §49 só
    existe para ela), então é lá que ele é cobrado.

    O que **não** se faz é pular calado. A razão do skip leva a medição daquela
    máquina, e o CI roda com `-rs` para imprimi-la. O que o Ubuntu publica hoje
    (§58.1):

    | | Windows | Ubuntu (CI) |
    |---|---|---|
    | 892 px, prévia expandida | 344 / 344 | 350 / 338 |
    | 900 px (o critério da §20.5) | 344 / 352 | 350 / 346 |
    | 790 px, prévia recolhida | 242 / 242 | 256 / 236 |

    Ou seja: falta **4 px** para o Linux cumprir o critério que interessa, e não
    os 24 que a §56 supunha. Um skip que só dissesse "não é Windows" teria
    deixado essa conta como trabalho de campo.
    """
    if sys.platform != "win32":
        pytest.skip(f"{mensagem} — critério medido em métricas do Windows (§56)")
    assert bottom <= viewport, mensagem


def _window_with_pdf(qapp, tmp_path, altura: int, previa: bool):
    """Janela medível: aberta num PDF, na altura pedida, com a prévia como se quer.

    O PDF importa e não é cerimônia. Sem ele o rótulo de contexto mostra "Abra um
    PDF para iniciar", que é mais longo, quebra em duas linhas e empurra o fluxo
    10 px para baixo — mediria uma condição em que o fluxo nem pode ser executado.
    A §41.2 mediu com o PDF aberto; estes números só são comparáveis com os dela
    na mesma condição.
    """
    from conftest import make_pdf

    from chess_pdf_editor import app as app_module

    settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    settings.setValue("remote_privacy_ack", True)
    window = app_module.MainWindow(settings=settings)
    window.resize(1500, altura)
    window.show()
    window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    window.compare_group.setChecked(previa)
    _settle_layout(qapp, window)
    return window


def _settle_layout(
    qapp,
    window,
    minimo_ms: int = 600,
    limite_ms: int = 3000,
    estaveis_exigidas: int = 3,
) -> None:
    """Roda o loop de eventos até a medição assentar, e por tempo suficiente (§56.7).

    Qt faz layout em resposta a eventos, e **um** `processEvents()` não garante que
    a passada terminou. Medido nesta máquina, na janela de 880 px:

    | t | pede | o que aconteceu |
    |---|---|---|
    | ~25 ms | 330 | primeira passada de layout |
    | ~135 ms | **344** | a prévia ao vivo entrou e empurrou o resto |

    Os 14 px são o `_preview_timer`: `_open_pdf` agenda a prévia num `QTimer` de
    140 ms, e quando ela chega o painel de comparação cresce. Medir antes disso lê
    uma janela que o usuário nunca vê — a dele já nasce com a prévia dentro.

    Este não é um detalhe de teste: era o defeito. As três medições da §50/§51
    foram tiradas assim, aos ~25 ms, e por isso davam 880 px como altura mínima.
    A altura mínima de verdade é 892 (§57).

    Duas condições, e as duas são necessárias:

    * **estabilidade** — três leituras iguais seguidas. Uma só não distingue
      "assentou" de "ainda nem começou a se mexer";
    * **tempo mínimo** — 600 ms de eventos processados de fato. Estabilidade
      sozinha para cedo demais: aos 25 ms a leitura já está estável, e fica
      estável por mais 110 ms — tempo de sobra para três rodadas concordarem
      sobre o número errado.

    O teto de 3 s existe para o teste falhar pela medição, e não por timeout, se
    algum dia a janela nunca assentar.
    """
    inicio = time.monotonic()
    anterior = None
    estaveis = 0
    while True:
        qapp.processEvents(QtCore.QEventLoop.AllEvents, 20)
        atual = _flow_bottom_and_viewport(window)
        estaveis = estaveis + 1 if atual == anterior else 0
        anterior = atual
        decorrido_ms = (time.monotonic() - inicio) * 1000.0
        if decorrido_ms >= minimo_ms and estaveis >= estaveis_exigidas:
            return
        if decorrido_ms >= limite_ms:  # pragma: no cover - janela que nunca assenta
            return


def test_the_basic_flow_fits_without_scrolling_from_the_measured_height(
    qapp, tmp_path
) -> None:
    """Prende a altura a partir da qual o fluxo cabe.

    Afirma que não piora: quem acrescentar à aba do fluxo 100 px acima de
    `Adicionar substituição` quebra este teste.
    """
    window = _window_with_pdf(qapp, tmp_path, FLOW_FITS_FROM_HEIGHT, previa=True)
    try:
        bottom, viewport = _flow_bottom_and_viewport(window)
        _assert_flow_fits(
            bottom,
            viewport,
            f"em {FLOW_FITS_FROM_HEIGHT} px o fluxo pede {bottom} px e o visor dá {viewport}",
        )
    finally:
        window.close()


def test_the_flow_fits_the_default_window_with_the_preview_open(qapp, tmp_path) -> None:
    """O critério da §20.5, que a §15.1 dava como *decidido não forçar*.

    Este é o teste que a §41.2 dizia não ser possível cumprir: fluxo básico sem
    rolagem em 1500×900, com a prévia **expandida** — sem pedir ao usuário que
    esconda justamente o que o app faz de melhor. Faltavam 191 px; dois sprints
    depois sobram 22.

    Se ele voltar a falhar, o que quebrou não é layout: é alguém tendo posto na aba
    do fluxo algo que não é etapa dele.
    """
    window = _window_with_pdf(qapp, tmp_path, DEFAULT_WINDOW_HEIGHT, previa=True)
    try:
        bottom, viewport = _flow_bottom_and_viewport(window)
        _assert_flow_fits(
            bottom,
            viewport,
            f"em {DEFAULT_WINDOW_HEIGHT} px o fluxo pede {bottom} px e o visor dá {viewport}",
        )
    finally:
        window.close()


def test_the_flow_fits_an_even_shorter_window_with_the_preview_collapsed(qapp, tmp_path) -> None:
    window = _window_with_pdf(qapp, tmp_path, FLOW_FITS_COLLAPSED_FROM_HEIGHT, previa=False)
    try:
        bottom, viewport = _flow_bottom_and_viewport(window)
        _assert_flow_fits(
            bottom,
            viewport,
            f"em {FLOW_FITS_COLLAPSED_FROM_HEIGHT} px com a prévia recolhida o fluxo "
            f"pede {bottom} px e o visor dá {viewport}",
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
    """As etapas de 1 a 4 têm de continuar numeradas depois de o painel atualizar.

    O último passo perdia o número: `Alterações (N)` sobrescrevia `4 · Alterações`
    no primeiro refresh da lista, que acontece já no arranque. O fluxo ficava 1, 2,
    3 e um rótulo solto.

    Eram cinco passos até o Sprint 9.23. A conferência dos candidatos saiu para a
    sua própria aba (§51.2) e o fluxo renumerou de 1–5 para 1–4 — manter os números
    antigos deixaria um buraco no 2, e uma sequência furada não diz quantas etapas
    faltam, que é para o que o número serve.
    """
    from conftest import DIAGRAM_RECT, make_pdf

    main_window._open_pdf(str(make_pdf(tmp_path / "book.pdf")), clear_ops=True)
    rect_img = main_window.pdf_service.pdf_rect_to_image_rect(
        0, DIAGRAM_RECT, main_window.current_render.matrix
    )
    main_window.page_widget.set_selection_rect(rect_img)
    main_window.board_editor.set_piece_placement("8/8/8/4k3/8/8/4K3/8")
    main_window._add_operation()

    # Só prefixos numéricos: outros rótulos usam o mesmo separador sem serem etapa.
    numbered = {
        text.split(" · ")[0]
        for text in (
            [label.text() for label in main_window.findChildren(QtWidgets.QLabel)]
            + [group.title() for group in main_window.findChildren(QtWidgets.QGroupBox)]
        )
        if " · " in text and text.split(" · ")[0].isdigit()
    }

    assert numbered == {"1", "2", "3", "4"}, f"etapas encontradas: {sorted(numbered)}"
    # E a contagem continua visível junto do número.
    assert main_window.changes_label.text() == "4 · Alterações (1)"
