from chess_pdf_editor.fen import (
    EMPTY_FEN,
    board_to_matrix,
    extract_piece_placement,
    matrix_to_piece_placement,
    normalize_piece_placement,
    validate_piece_placement,
)


def test_extract_piece_placement():
    assert extract_piece_placement("8/8/8/8/8/8/8/8 w - - 0 1") == EMPTY_FEN


def test_roundtrip_matrix_piece_placement():
    fen = "8/3k4/8/8/8/8/4K3/8"
    matrix = board_to_matrix(fen)
    out = matrix_to_piece_placement(matrix)
    assert out == fen


def test_validate_warnings_kings():
    warnings = validate_piece_placement("8/8/8/8/8/8/8/8")
    assert any("rei branco" in w for w in warnings)


def test_normalize():
    fen = "8/8/8/3k4/8/8/4K3/8"
    assert normalize_piece_placement(fen) == fen

