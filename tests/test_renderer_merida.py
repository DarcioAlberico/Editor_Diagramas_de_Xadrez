from chess_pdf_editor.renderer import _merida_rows


def test_merida_rows_start_position():
    rows = _merida_rows("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
    assert rows == [
        "tMvWlVmT",
        "OoOoOoOo",
        " + + + +",
        "+ + + + ",
        " + + + +",
        "+ + + + ",
        "pPpPpPpP",
        "RnBqKbNr",
    ]


def test_merida_rows_empty_board():
    rows = _merida_rows("8/8/8/8/8/8/8/8")
    assert rows == [
        " + + + +",
        "+ + + + ",
        " + + + +",
        "+ + + + ",
        " + + + +",
        "+ + + + ",
        " + + + +",
        "+ + + + ",
    ]
