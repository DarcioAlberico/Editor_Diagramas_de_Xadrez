from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Iterable, Optional

import chess
import chess.pgn


@dataclass
class StudyState:
    start_fen: str
    current_fen: str
    move_count: int
    can_undo: bool
    can_redo: bool


class StudyGame:
    def __init__(self, start_fen: str = chess.STARTING_FEN) -> None:
        self._start_fen = ""
        self._board = chess.Board()
        self._history: list[chess.Move] = []
        self._cursor = 0
        self.set_start_fen(start_fen)

    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def start_fen(self) -> str:
        return self._start_fen

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def history_length(self) -> int:
        return len(self._history)

    def set_start_fen(self, fen: str) -> None:
        board = chess.Board(fen)
        self._start_fen = board.fen()
        self._history = []
        self._cursor = 0
        self._board = board.copy(stack=False)

    def state(self) -> StudyState:
        return StudyState(
            start_fen=self._start_fen,
            current_fen=self._board.fen(),
            move_count=self._cursor,
            can_undo=self._cursor > 0,
            can_redo=self._cursor < len(self._history),
        )

    def legal_moves_from(self, square: int) -> list[chess.Move]:
        return [mv for mv in self._board.legal_moves if mv.from_square == square]

    def push_move(self, move: chess.Move) -> str:
        if move not in self._board.legal_moves:
            raise ValueError(f"Movimento ilegal: {move.uci()}")
        san = self._board.san(move)
        if self._cursor < len(self._history):
            self._history = self._history[: self._cursor]
        self._history.append(move)
        self._cursor += 1
        self._board.push(move)
        return san

    def undo(self) -> bool:
        if self._cursor <= 0:
            return False
        self._cursor -= 1
        self._rebuild()
        return True

    def redo(self) -> bool:
        if self._cursor >= len(self._history):
            return False
        self._cursor += 1
        self._rebuild()
        return True

    def clear_moves(self) -> None:
        self._history = []
        self._cursor = 0
        self._rebuild()

    def goto_ply(self, ply: int) -> bool:
        target = max(0, min(int(ply), len(self._history)))
        if target == self._cursor:
            return False
        self._cursor = target
        self._rebuild()
        return True

    def last_move(self) -> Optional[chess.Move]:
        if self._cursor <= 0:
            return None
        return self._history[self._cursor - 1]

    def moves(self) -> list[chess.Move]:
        return list(self._history[: self._cursor])

    def all_moves(self) -> list[chess.Move]:
        return list(self._history)

    def san_line(self) -> list[str]:
        board = chess.Board(self._start_fen)
        out: list[str] = []
        for move in self._history:
            out.append(board.san(move))
            board.push(move)
        return out

    def load_pgn(self, pgn_text: str) -> None:
        stream = StringIO(pgn_text or "")
        game = chess.pgn.read_game(stream)
        if game is None:
            raise ValueError("PGN invalido ou vazio.")
        start_board = game.board()
        self.set_start_fen(start_board.fen())
        for move in game.mainline_moves():
            self.push_move(move)

    def to_pgn(
        self,
        event: str = "Study Session",
        site: str = "Chess PDF Editor",
        white: str = "White",
        black: str = "Black",
        date: Optional[datetime] = None,
        comment_before: str = "",
        comment_after: str = "",
        comment_ply: Optional[int] = None,
    ) -> str:
        game = chess.pgn.Game()
        now = date or datetime.now()
        game.headers["Event"] = event
        game.headers["Site"] = site
        game.headers["Date"] = now.strftime("%Y.%m.%d")
        game.headers["Round"] = "-"
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["Result"] = self._board.result(claim_draw=True) if self._board.is_game_over(claim_draw=True) else "*"

        if self._start_fen != chess.STARTING_FEN:
            game.setup(chess.Board(self._start_fen))
            game.headers["SetUp"] = "1"
            game.headers["FEN"] = self._start_fen

        node = game
        target_ply = self._cursor if comment_ply is None else max(0, min(int(comment_ply), self._cursor))
        before_text = comment_before.strip()
        after_text = comment_after.strip()
        if before_text and target_ply == 0:
            game.comment = before_text

        for ply_idx, move in enumerate(self._history[: self._cursor], start=1):
            if before_text and ply_idx == target_ply:
                node.comment = before_text
            node = node.add_variation(move)
            if after_text and ply_idx == target_ply:
                node.comment = after_text
        if after_text and target_ply == 0:
            game.comment = after_text if not game.comment else f"{game.comment} {after_text}"
        return str(game)

    def load_moves(self, moves: Iterable[chess.Move]) -> None:
        self._history = []
        self._cursor = 0
        self._rebuild()
        for move in moves:
            self.push_move(move)

    def _rebuild(self) -> None:
        board = chess.Board(self._start_fen)
        for move in self._history[: self._cursor]:
            board.push(move)
        self._board = board
