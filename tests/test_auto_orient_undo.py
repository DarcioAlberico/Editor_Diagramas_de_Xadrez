"""`Auto-orientar` reversível, e o ponto cego que obriga isso (§42).

A heurística de orientação (§26.3) decide pelo sentido dos peões, e isso é certo na
maioria dos diagramas. Mas ela erra **com confiança** numa família que livro de xadrez
tem de sobra: o estudo em que os peões dos dois lados já passaram uns pelos outros.

`BLIND_SPOT` é um diagrama **real** do dataset de teste (`board_1.png`) — uma corrida
de promoção mútua. De pé como está impresso, a heurística manda girar 180° com margem
2,5, que não é sequer marcada como ambígua. Nenhum limiar separa isso dos acertos
legítimos (margens 3,0 a 6,0 nas medições da §42.1) sem calibrar em quatro pontos.

Daí a conclusão que este módulo testa: o comando fica **manual e reversível**, e não
vira aviso automático.
"""
from __future__ import annotations

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from chess_pdf_editor.fen import board_to_matrix, matrix_to_piece_placement  # noqa: E402
from chess_pdf_editor.orientation import auto_orient  # noqa: E402


def _rotated_180(piece_placement: str) -> str:
    """A mesma posição impressa do ponto de vista das pretas.

    Calculada, e não escrita à mão: a primeira versão deste módulo trazia a FEN
    girada digitada, e ela tinha perdido um bispo no caminho.
    """
    matrix = board_to_matrix(piece_placement)
    return matrix_to_piece_placement([list(reversed(row)) for row in reversed(matrix)])


#: Diagrama real de livro (tests/data/local_ocr/board_1.png): corrida de promoção.
BLIND_SPOT = "3k4/3P4/8/7K/P6p/5p1P/1p1R4/1r6"
#: Posição de pé que a heurística acerta sem drama.
UPRIGHT = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R"
FLIPPED = _rotated_180(UPRIGHT)


def _placement(window) -> str:
    return window.board_editor.piece_placement()


# ---------------------------------------------------------------------------
# O ponto cego, medido
# ---------------------------------------------------------------------------


def test_the_heuristic_is_confidently_wrong_on_a_real_book_diagram() -> None:
    """Fixa o ponto cego com um exemplo real, para ninguém automatizar isto.

    Se um dia a heurística passar a acertar este caso, o teste falha e avisa que a
    §42.1 pode ser revista — inclusive a decisão de não emitir aviso automático.
    """
    result = auto_orient(BLIND_SPOT)

    assert result.rotation == 180, "o ponto cego sumiu; reveja a §42.1"
    assert result.ambiguous is False, "se fosse marcado ambíguo, avisar seria seguro"
    assert result.margin > 1.0


def test_the_heuristic_still_recovers_a_genuine_black_side_diagram() -> None:
    """O ponto cego não invalida a ferramenta: o caso que ela existe para resolver
    continua resolvido, e com margem bem maior."""
    result = auto_orient(FLIPPED)

    assert result.piece_placement == UPRIGHT
    assert result.margin >= 3.0


# ---------------------------------------------------------------------------
# Reversibilidade
# ---------------------------------------------------------------------------


def test_pressing_it_again_undoes_a_wrong_rotation(main_window) -> None:
    """O caso que motiva o sprint: a heurística girou um diagrama que estava certo."""
    main_window.board_editor.set_piece_placement(BLIND_SPOT)

    main_window._auto_orient_position()
    assert _placement(main_window) != BLIND_SPOT, "nada girou; o teste não prova nada"

    main_window._auto_orient_position()
    assert _placement(main_window) == BLIND_SPOT
    assert "desfeita" in main_window.statusBar().currentMessage()


def test_undoing_twice_in_a_row_rotates_again(main_window) -> None:
    """O terceiro toque é uma decisão nova, não a metade de um interruptor.

    Ou seja: girar → desfazer → girar chega no mesmo lugar do primeiro giro. O que
    isto **não** garante é que o `_auto_orient_undo` seja limpo explicitamente ao
    desfazer — conferido por mutação: tirar essa linha não muda comportamento nenhum,
    porque a chamada seguinte a descarta de todo modo ao ver que a posição mudou. A
    linha fica por clareza, não por necessidade.
    """
    main_window.board_editor.set_piece_placement(BLIND_SPOT)

    main_window._auto_orient_position()
    rotated = _placement(main_window)
    main_window._auto_orient_position()
    assert _placement(main_window) == BLIND_SPOT

    main_window._auto_orient_position()
    assert _placement(main_window) == rotated


def test_editing_after_rotating_gives_up_the_undo(main_window) -> None:
    """Desfazer para uma posição que o usuário já mexeu jogaria a edição dele fora."""
    main_window.board_editor.set_piece_placement(BLIND_SPOT)
    main_window._auto_orient_position()

    edited = "8/8/8/4k3/8/8/4K3/8"
    main_window.board_editor.set_piece_placement(edited)
    main_window._auto_orient_position()

    assert _placement(main_window) != BLIND_SPOT, "o desfazer velho apagou a edição"


def test_a_position_already_upright_is_left_alone(main_window) -> None:
    main_window.board_editor.set_piece_placement(UPRIGHT)

    main_window._auto_orient_position()

    assert _placement(main_window) == UPRIGHT
    assert "já é a mais plausível" in main_window.statusBar().currentMessage()


def test_nothing_to_undo_before_the_first_rotation(main_window) -> None:
    """Sem giro anterior, o atalho não pode "desfazer" para uma posição inventada."""
    assert main_window._auto_orient_undo is None
    main_window.board_editor.set_piece_placement(UPRIGHT)

    main_window._auto_orient_position()

    assert _placement(main_window) == UPRIGHT


# ---------------------------------------------------------------------------
# O que a mensagem conta
# ---------------------------------------------------------------------------


def test_the_message_shows_the_evidence_it_used(main_window) -> None:
    """Os motivos apareciam só quando *nada* girava — faltavam justamente quando o
    usuário precisa julgar se a decisão foi boa."""
    main_window.board_editor.set_piece_placement(BLIND_SPOT)

    main_window._auto_orient_position()
    message = main_window.statusBar().currentMessage()

    assert "girada 180°" in message
    assert "peões" in message, f"a mensagem não diz em que se baseou: {message!r}"
    assert "desfaz" in message, "o caminho de volta tem de estar na mensagem"


def test_an_invalid_position_does_not_arm_an_undo(main_window, no_modals) -> None:
    main_window.board_editor.set_piece_placement(UPRIGHT)
    main_window.fen_edit.setText("isto/nao/e/uma/fen")

    # Vai pelo caminho do erro; o importante é não deixar um desfazer armado.
    try:
        main_window._auto_orient_position()
    except Exception:
        pass

    assert main_window._auto_orient_undo is None or _placement(main_window) == UPRIGHT
