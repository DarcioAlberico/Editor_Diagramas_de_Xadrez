import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
from PySide6 import QtWidgets

from chess_pdf_editor.app import MainWindow, StudyPanel


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


def test_study_panel_export_uses_pgn_provider():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()
    panel.set_pgn_provider(lambda: "PGN com comentarios")

    try:
        assert panel._export_pgn_text() == "PGN com comentarios"
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_panel_marks_commented_moves():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()

    try:
        panel.set_commented_plies({2})
        panel._on_line_changed(["e4", "e5"], 2)

        assert panel.moves_table.item(0, 1).text() == "e4"
        assert panel.moves_table.item(0, 2).text() == "e5 *"
        assert panel.moves_table.item(0, 2).toolTip() == "Este lance tem comentario."
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_panel_signals_before_move_navigation():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()
    calls: list[bool] = []
    panel.about_to_change_line.connect(lambda: calls.append(True))

    try:
        panel._on_line_changed(["e4", "e5"], 2)
        panel._on_san_cell_clicked(0, 1)

        assert calls == [True]
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_move_reference_names_selected_ply():
    assert MainWindow._study_move_reference(0, ["e4", "e5"], "w", 1) == "posicao inicial"
    assert MainWindow._study_move_reference(1, ["e4", "e5"], "w", 1) == "1. e4"
    assert MainWindow._study_move_reference(2, ["e4", "e5"], "w", 1) == "1... e5"


def test_study_move_reference_handles_black_to_move_start():
    assert MainWindow._study_move_reference(1, ["g3", "Rf1"], "b", 12) == "12... g3"
    assert MainWindow._study_move_reference(2, ["g3", "Rf1"], "b", 12) == "13. Rf1"


def test_starting_study_position_uses_initial_board():
    pos = MainWindow._make_starting_study_position(4)

    assert pos.page_num == 4
    assert pos.rect_pdf == (0.0, 0.0, 0.0, 0.0)
    assert pos.fen == chess.STARTING_BOARD_FEN
    assert pos.side_to_move == "w"
    assert pos.fullmove_number == 1
