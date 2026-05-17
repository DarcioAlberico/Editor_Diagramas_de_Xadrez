from chess_pdf_editor.app import StudyPanel


def test_format_san_rows_starting_from_white_pairs_moves():
    rows = StudyPanel._format_san_rows(["e4", "c5", "Nf3"], "w", 1)

    assert rows == [
        ("1.", "e4", 1, "c5", 2),
        ("2.", "Nf3", 3, None, None),
    ]


def test_format_san_rows_starting_from_black_uses_ellipsis():
    rows = StudyPanel._format_san_rows(["g3", "Rf1", "Rh1+"], "b", 1)

    assert rows == [
        ("1...", None, None, "g3", 1),
        ("2.", "Rf1", 2, "Rh1+", 3),
    ]


def test_format_san_rows_respects_fullmove_number():
    rows = StudyPanel._format_san_rows(["g3", "Rf1"], "b", 12)

    assert rows == [
        ("12...", None, None, "g3", 1),
        ("13.", "Rf1", 2, None, None),
    ]
