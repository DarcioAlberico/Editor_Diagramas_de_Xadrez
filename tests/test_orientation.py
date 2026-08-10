"""Auto-orientação da posição (Sprint 6.3)."""
from __future__ import annotations

import pytest

from chess_pdf_editor.fen import board_to_matrix, matrix_to_piece_placement
from chess_pdf_editor.orientation import auto_orient, plausibility, rank_orientations

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
ENDGAME = "8/5p2/4k3/8/8/3K4/5P2/8"


def _rotate(piece_placement: str, times: int) -> str:
    matrix = board_to_matrix(piece_placement)
    for _ in range(times % 4):
        matrix = [list(row) for row in zip(*matrix[::-1])]
    return matrix_to_piece_placement(matrix)


def test_an_upright_position_is_left_alone() -> None:
    result = auto_orient(START)
    assert result.rotation == 0
    assert result.piece_placement == START
    assert not result.changed


def test_an_upside_down_position_is_turned_back() -> None:
    upside_down = _rotate(START, 2)
    assert upside_down != START

    result = auto_orient(upside_down)
    assert result.rotation == 180
    assert result.piece_placement == START


@pytest.mark.parametrize("times", [1, 2, 3])
def test_every_rotation_of_a_real_position_is_recovered(times: int) -> None:
    result = auto_orient(_rotate(ENDGAME, times))
    assert result.piece_placement == ENDGAME


def test_sideways_positions_lose_because_pawns_land_on_the_back_rank() -> None:
    """É o critério que de fato elimina 90° e 270° num diagrama de livro."""
    sideways = _rotate(START, 1)
    upright_score, _ = plausibility(START)
    sideways_score, reasons = plausibility(sideways)

    assert sideways_score < upright_score
    assert any("1ª/8ª fila" in reason for reason in reasons)


def test_pawn_direction_is_what_separates_0_from_180() -> None:
    """Girar 180° preserva reis e contagens — só o sentido dos peões muda de sinal."""
    upright, _ = plausibility(ENDGAME)
    flipped, reasons = plausibility(_rotate(ENDGAME, 2))

    assert upright > flipped
    assert any("sentido oposto" in reason for reason in reasons)


def test_a_position_without_pawns_is_reported_as_ambiguous() -> None:
    """Sem peões dos dois lados o sinal mais forte não tem o que dizer."""
    kings_only = "4k3/8/8/8/8/8/8/4K3"
    result = auto_orient(kings_only)

    assert result.ambiguous
    assert any("sem peões" in reason for reason in result.best.reasons)


def test_ties_prefer_not_rotating() -> None:
    """Empate tem de manter o que está na tela: girar à toa assusta."""
    symmetric = "4k3/8/8/8/8/8/8/4K3"
    assert auto_orient(symmetric).rotation == 0


def test_all_four_rotations_are_scored() -> None:
    candidates = rank_orientations(ENDGAME)
    assert len(candidates) == 4
    assert {candidate.rotation for candidate in candidates} == {0, 90, 180, 270}
    scores = [candidate.score for candidate in candidates]
    assert scores == sorted(scores, reverse=True), "a lista sai da melhor para a pior"


def test_missing_kings_are_penalized() -> None:
    no_kings, _ = plausibility("8/8/8/8/8/8/8/8")
    both_kings, _ = plausibility("4k3/8/8/8/8/8/8/4K3")
    assert no_kings < both_kings


def test_an_invalid_position_raises() -> None:
    with pytest.raises(ValueError):
        auto_orient("nao-e-uma-fen")
