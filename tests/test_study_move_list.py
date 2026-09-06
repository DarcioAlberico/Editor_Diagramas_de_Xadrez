import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
from PySide6 import QtCore, QtWidgets

from chess_pdf_editor.app import MainWindow, StudyPanel
from chess_pdf_editor.types import StudyPosition


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
        panel.study_board._game.push_move(chess.Move.from_uci("e2e4"))
        panel.study_board._game.push_move(chess.Move.from_uci("e7e5"))
        panel.set_commented_plies({"e2e4|e7e5"})
        panel._on_line_changed(["e4", "e5"], 2)

        first = panel.moves_tree.topLevelItem(0)
        second = panel.moves_tree.topLevelItem(1)
        assert first.text(1) == "e4"
        assert second.text(1) == "e5 *"
        assert second.toolTip(1) == "Este lance tem comentário."
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_panel_signals_before_move_navigation():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()
    calls: list[bool] = []
    panel.about_to_change_line.connect(lambda: calls.append(True))

    try:
        panel.study_board._game.push_move(chess.Move.from_uci("e2e4"))
        panel.study_board._game.push_move(chess.Move.from_uci("e7e5"))
        panel._on_line_changed(["e4", "e5"], 2)
        panel._on_san_tree_item_clicked(panel.moves_tree.topLevelItem(0), 0)

        assert calls == [True]
        assert panel.study_board.current_path_key() == "e2e4"
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_panel_shows_variations_in_san_tree():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()

    try:
        panel.study_board._game.push_move(chess.Move.from_uci("e2e4"))
        panel.study_board._game.push_move(chess.Move.from_uci("e7e5"))
        panel.study_board._game.undo()
        panel.study_board._game.push_move(chess.Move.from_uci("c7c5"))
        panel._on_line_changed(["e4", "c5"], 2)

        first = panel.moves_tree.topLevelItem(0)
        assert first.text(1) == "e4"
        assert panel.moves_tree.topLevelItem(1).text(1) == "e5"
        assert first.childCount() == 1
        assert first.child(0).text(1) == "c5"
    finally:
        panel.deleteLater()
        app.processEvents()


def test_study_move_reference_names_selected_ply():
    assert MainWindow._study_move_reference(0, ["e4", "e5"], "w", 1) == "posição inicial"
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


def test_study_position_click_loads_board_and_preserves_previous_comment():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow.__new__(MainWindow)
    window._syncing_study_positions = False
    window.pdf_service = None
    window.current_render = None
    window.study_panel = StudyPanel()
    window.study_positions_list = QtWidgets.QListWidget()
    window.study_comment_target_label = QtWidgets.QLabel()
    window.study_comment_before_edit = QtWidgets.QPlainTextEdit()
    window.study_comment_after_edit = QtWidgets.QPlainTextEdit()
    window.study_positions = [
        StudyPosition(page_num=0, rect_pdf=(0, 0, 1, 1), fen=chess.STARTING_BOARD_FEN),
        StudyPosition(page_num=1, rect_pdf=(0, 0, 1, 1), fen="8/8/8/8/8/8/8/4K3", side_to_move="w"),
    ]

    previous = QtWidgets.QListWidgetItem("001")
    previous.setData(QtCore.Qt.UserRole, 0)
    current = QtWidgets.QListWidgetItem("002")
    current.setData(QtCore.Qt.UserRole, 1)
    window.study_positions_list.addItem(previous)
    window.study_positions_list.addItem(current)
    window.study_positions_list.setCurrentRow(0)
    window.study_comment_before_edit.setPlainText("comentario anterior")

    try:
        window._on_study_position_selected(current, previous)

        assert window.study_positions[0].move_comments["0"]["before"] == "comentario anterior"
        assert window.study_panel.study_board.current_fen().split()[0] == "8/8/8/8/8/8/8/4K3"
    finally:
        window.study_panel.deleteLater()
        app.processEvents()


# ---------------------------------------------------------------------------
# `Vez de jogar` do painel de estudo (§59.9)
# ---------------------------------------------------------------------------


def _panel():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = StudyPanel()
    panel.load_piece_placement("8/8/8/4k3/8/8/4K3/8", side_to_move="w")
    return panel


def test_the_side_combo_changes_the_starting_position():
    """O combo era lido e escrito, mas nao tinha `connect`: mexer nele nao fazia nada."""
    panel = _panel()
    antes = panel.study_board.start_fen()

    panel.side_combo.setCurrentIndex(1)  # pretas

    depois = panel.study_board.start_fen()
    assert depois != antes, "trocar a vez de jogar nao mudou a posicao inicial"
    assert depois.split()[1] == "b"
    assert panel.study_board.start_turn() == "b"


def test_loading_a_position_does_not_fire_the_side_change():
    """Preencher o painel nao pode disparar a troca que so o usuario deve fazer."""
    panel = _panel()
    panel.load_piece_placement("8/8/8/4k3/8/8/4K3/8", side_to_move="b")
    assert panel.study_board.start_fen().split()[1] == "b"

    panel.load_piece_placement("8/8/8/4k3/8/8/4K3/8", side_to_move="w")
    assert panel.study_board.start_fen().split()[1] == "w"


def test_the_side_combo_refuses_to_throw_away_a_line():
    """Trocar o lado da posicao inicial invalidaria os lances ja jogados.

    Descarta-los em silencio seria trocar um controle inerte por um destrutivo, que e
    pior: o combo volta ao valor anterior e a barra de estado diz o que fazer.
    """
    panel = _panel()
    panel.study_board._on_square_clicked(*panel.study_board._square_to_display(chess.E2))
    panel.study_board._on_square_clicked(*panel.study_board._square_to_display(chess.E3))
    assert panel.study_board.san_line(), "o lance nao entrou; o teste nao exercita nada"
    antes = panel.study_board.start_fen()

    panel.side_combo.setCurrentIndex(1)  # pretas

    assert panel.study_board.start_fen() == antes, "a linha foi jogada fora"
    assert panel.study_board.san_line(), "os lances sumiram"
    assert panel.side_combo.currentData() == "w", "o combo ficou mentindo sobre o estado"
    assert "reinicie a linha" in panel.status_label.text()
