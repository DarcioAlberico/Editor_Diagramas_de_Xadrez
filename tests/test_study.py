import chess

from chess_pdf_editor.study import StudyGame


def test_study_push_undo_redo():
    game = StudyGame()
    san = game.push_move(chess.Move.from_uci("e2e4"))
    assert san == "e4"
    assert game.board.fen().startswith("rnbqkbnr/pppppppp/8/8/4P3")

    assert game.undo() is True
    assert game.board.fen().split()[0] == chess.STARTING_BOARD_FEN

    assert game.redo() is True
    assert game.board.fen().startswith("rnbqkbnr/pppppppp/8/8/4P3")


def test_study_illegal_move():
    game = StudyGame()
    try:
        game.push_move(chess.Move.from_uci("e2e5"))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_study_truncate_redo_after_new_move():
    game = StudyGame()
    game.push_move(chess.Move.from_uci("e2e4"))
    game.push_move(chess.Move.from_uci("e7e5"))
    assert game.undo() is True
    game.push_move(chess.Move.from_uci("c7c5"))
    assert game.redo() is False
    assert game.board.fen().startswith("rnbqkbnr/pp1ppppp/8/2p5/4P3")


def test_study_custom_start_fen_has_setup_tags():
    game = StudyGame("8/8/8/3k4/8/8/4K3/8 w - - 0 1")
    pgn = game.to_pgn()
    assert '[SetUp "1"]' in pgn
    assert '[FEN "8/8/8/3k4/8/8/4K3/8 w - - 0 1"]' in pgn


def test_study_custom_start_fen_black_to_move_accepts_black_first_move():
    game = StudyGame("4k3/8/8/8/8/8/8/4K3 b - - 0 1")
    san = game.push_move(chess.Move.from_uci("e8d8"))

    assert san == "Kd8"
    assert game.board.turn == chess.WHITE


def test_study_san_and_goto_ply():
    game = StudyGame()
    game.push_move(chess.Move.from_uci("e2e4"))
    game.push_move(chess.Move.from_uci("c7c5"))
    game.push_move(chess.Move.from_uci("g1f3"))

    assert game.san_line() == ["e4", "c5", "Nf3"]
    assert game.cursor == 3
    assert game.goto_ply(1) is True
    assert game.cursor == 1
    assert game.last_move() == chess.Move.from_uci("e2e4")
    assert game.goto_ply(999) is True
    assert game.cursor == 3


def test_study_load_pgn():
    game = StudyGame()
    pgn_text = """[Event "Test"]
[Site "Local"]
[Date "2026.02.23"]
[Round "-"]
[White "W"]
[Black "B"]
[Result "*"]

1. e4 c5 2. Nf3 d6 *
"""
    game.load_pgn(pgn_text)
    assert game.san_line() == ["e4", "c5", "Nf3", "d6"]
    assert game.cursor == 4
    assert game.board.fen().startswith("rnbqkbnr/pp2pppp/3p4/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R")


def test_study_pgn_comments_before_and_after():
    game = StudyGame()
    game.push_move(chess.Move.from_uci("e2e4"))
    pgn = game.to_pgn(comment_before="Plano inicial", comment_after="Depois de e4")

    assert "{ Plano inicial }" in pgn
    assert "e4 { Depois de e4 }" in pgn


def test_study_pgn_comments_can_target_current_ply():
    game = StudyGame()
    game.push_move(chess.Move.from_uci("e2e4"))
    game.push_move(chess.Move.from_uci("e7e5"))
    pgn = game.to_pgn(
        comment_before="Antes de e5",
        comment_after="Depois de e5",
        comment_ply=2,
    )

    assert "e4 { Antes de e5 }" in pgn
    assert "e5 { Depois de e5 }" in pgn


def test_study_pgn_keeps_comments_for_multiple_moves():
    game = StudyGame()
    game.push_move(chess.Move.from_uci("e2e4"))
    game.push_move(chess.Move.from_uci("e7e5"))
    game.push_move(chess.Move.from_uci("g1f3"))
    game.goto_ply(1)

    pgn = game.to_pgn(
        move_comments={
            1: {"before": "Antes de e4", "after": "Depois de e4"},
            3: {"before": "Antes de Nf3", "after": "Depois de Nf3"},
        },
        include_all=True,
    )

    assert "{ Antes de e4 }" in pgn
    assert "e4 { Depois de e4 }" in pgn
    assert "{ Antes de Nf3 }" in pgn
    assert "Nf3 { Depois de Nf3 }" in pgn
